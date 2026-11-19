import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore
from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from app.services.agent_ops_undo import undo_ops


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('integrity')['id']
        self.db = OpsDb(self.store.root)
        self.journal = AgentOpsJournal(self.store, self.db)
        self.addCleanup(self.journal.close)

    def canvas(self):
        return self.store.open_project(self.pid)['canvas']

    def upsert(self, node):
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': node}],
                                   base_revision=self.canvas()['revision'])

    def agent(self, op_id, node, turn=None, parent=None, flush=True):
        with self.journal.mutation(self.pid) as operation:
            before = self.canvas()
            self.upsert(node)
            delta = agent_ops_delta.build_delta(before, self.canvas())
            old, new = record_sides(delta)
            rec = {'op_id': op_id, 'turn_id': turn, 'agent_node_id': 'agent', 'tool': 'test',
                   'args': {}, 'before': old, 'after': new,
                   'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                   'created_at': '2026-01-01', 'irreversible_effects': [], 'undone': False}
            if parent:
                rec['parent_op_id'] = parent
            operation.register(rec, delta)
        if flush:
            self.store.flush(self.pid)
            self.assertEqual(operation.result()['status'], 'settled')
        return operation

    def test_pending_turn_and_op_are_not_success_or_not_found(self):
        self.agent('first', {'id': 'a', 'type': 'text'}, 'turn')
        pending = self.agent('second', {'id': 'b', 'type': 'text'}, 'turn', flush=False)
        before = self.canvas()
        for kwargs in ({'op_id': 'second'}, {'turn_id': 'turn'}):
            result = undo_ops(self.store, self.pid, **kwargs)
            self.assertEqual(result['status'], 'pending')
            self.assertIn('second', str(result))
            self.assertEqual(self.canvas(), before)
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'first')['undone'])
        self.store.flush(self.pid)
        self.assertEqual(pending.result()['status'], 'settled')
        result = undo_ops(self.store, self.pid, turn_id='turn')
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(result['reverted'], ['second', 'first'])
        self.assertEqual(self.canvas()['nodes'], [])

    def test_pending_descendant_blocks_root_undo(self):
        self.agent('parent', {'id': 'a', 'type': 'text'}, parent=None)
        self.agent('child', {'id': 'b', 'type': 'text'}, parent='parent', flush=False)
        result = undo_ops(self.store, self.pid, op_id='parent')
        self.assertEqual(result['status'], 'pending')
        self.assertIn('child', str(result))
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'parent')['undone'])

    def test_closed_journal_keeps_unsettled_operation_visible(self):
        self.agent('first', {'id': 'a', 'type': 'text'}, 'turn')
        self.agent('second', {'id': 'b', 'type': 'text'}, 'turn', flush=False)
        self.journal.close()
        for kwargs in ({'op_id': 'second'}, {'turn_id': 'turn'}):
            result = undo_ops(self.store, self.pid, **kwargs)
            self.assertEqual(result['status'], 'pending')
            self.assertIn('second', str(result))
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'first')['undone'])

    def test_conflicted_original_blocks_turn_and_keeps_id(self):
        self.agent('first', {'id': 'a', 'type': 'text'}, 'turn')
        operation = self.agent('second', {'id': 'b', 'type': 'text', 'v': 1}, 'turn', flush=False)
        self.upsert({'id': 'b', 'type': 'text', 'v': 2})
        self.store.flush(self.pid)
        self.assertEqual(operation.result()['status'], 'conflict')
        for kwargs in ({'op_id': 'second'}, {'turn_id': 'turn'}):
            result = undo_ops(self.store, self.pid, **kwargs)
            self.assertEqual(result['status'], 'conflict')
            self.assertIn('second', str(result))
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'first')['undone'])

    def test_remaining_node_references_reject_inverse(self):
        for field, value in [('inputNodeIds', ['created']), ('historyFor', 'created')]:
            with self.subTest(field=field):
                op = 'create-' + field
                self.agent(op, {'id': 'created', 'type': 'text'})
                self.upsert({'id': 'ref', 'type': 'text', field: value})
                before = self.canvas()
                result = undo_ops(self.store, self.pid, op_id=op)
                self.assertEqual(result['status'], 'conflict')
                self.assertIn(field, str(result['conflicts']))
                self.assertEqual(self.canvas(), before)
                self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, op)['undone'])
                self.store.apply_mutations(self.pid, [{'op': 'remove_node', 'node_id': 'ref'}],
                                           base_revision=self.canvas()['revision'])
                self.assertEqual(undo_ops(self.store, self.pid, op_id=op)['status'], 'reverted')

    def test_reference_removed_by_inverse_is_safe(self):
        self.agent('create', {'id': 'created', 'type': 'text'}, 'turn')
        self.agent('reference', {'id': 'ref', 'type': 'text',
                                 'inputNodeIds': ['created'], 'historyFor': 'created'}, 'turn')
        result = undo_ops(self.store, self.pid, turn_id='turn')
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(self.canvas()['nodes'], [])

    def test_retention_does_not_prune_parent_with_pending_child(self):
        with patch.object(agent_ops_store, 'MAX_OPERATIONS', 2):
            self.agent('parent', {'id': 'a', 'type': 'text'})
            self.agent('other', {'id': 'b', 'type': 'text'})
            child = self.agent('child', {'id': 'c', 'type': 'text'}, parent='parent', flush=False)
            # A separate direct writer must not evict the pending child's ancestry.
            item = agent_ops_store.get_operation(self.db, self.pid, 'other')
            item['op_id'] = 'extra'
            agent_ops_store.append_operation(OpsDb(self.store.root), self.pid, item)
            self.assertIsNotNone(agent_ops_store.get_operation(self.db, self.pid, 'parent'))
            self.store.flush(self.pid)
            self.assertEqual(child.result()['status'], 'settled')
            self.assertIsNotNone(agent_ops_store.get_operation(self.db, self.pid, 'parent'))
            self.assertIsNotNone(agent_ops_store.get_operation(self.db, self.pid, 'child'))

    def test_pending_turn_protected_during_retention(self):
        with patch.object(agent_ops_store, 'MAX_OPERATIONS', 2):
            self.agent('parent', {'id': 'a', 'type': 'text'}, 'turn')
            self.agent('other', {'id': 'b', 'type': 'text'}, 'other')
            child = self.agent('child', {'id': 'c', 'type': 'text'}, 'turn', parent='parent', flush=False)
            item = agent_ops_store.get_operation(self.db, self.pid, 'other')
            item['op_id'] = 'extra'
            agent_ops_store.append_operation(OpsDb(self.store.root), self.pid, item)
            self.store.flush(self.pid)
            self.assertEqual(child.result()['status'], 'settled')
            self.assertEqual({r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid, turn_id='turn')},
                             {'parent', 'child'})

    def test_direct_retention_writer_does_not_deadlock_registration(self):
        with patch.object(agent_ops_store, 'MAX_OPERATIONS', 1):
            self.agent('first', {'id': 'a', 'type': 'text'})
            item = agent_ops_store.get_operation(self.db, self.pid, 'first')
            item['op_id'] = 'other'
            entered, release, started = threading.Event(), threading.Event(), threading.Event()
            errors = []
            original = agent_ops_store._reserved

            def pause(*args):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('retention release timed out')
                return original(*args)

            def append():
                try:
                    agent_ops_store.append_operation(OpsDb(self.store.root), self.pid, item)
                except Exception as exc:
                    errors.append(exc)

            def register():
                try:
                    started.set()
                    self.agent('pending', {'id': 'b', 'type': 'text'}, flush=False)
                except Exception as exc:
                    errors.append(exc)

            with patch.object(agent_ops_store, '_reserved', side_effect=pause):
                writer = threading.Thread(target=append)
                writer.start()
                self.assertTrue(entered.wait(5))
                registrar = threading.Thread(target=register)
                registrar.start()
                self.assertTrue(started.wait(5))
                release.set()
                writer.join(5)
                registrar.join(5)
            self.assertFalse(writer.is_alive())
            self.assertFalse(registrar.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(undo_ops(self.store, self.pid, op_id='pending')['status'], 'pending')

    def test_registration_rejects_parent_already_pruned(self):
        with patch.object(agent_ops_store, 'MAX_OPERATIONS', 1):
            self.agent('parent', {'id': 'a', 'type': 'text'})
            self.agent('other', {'id': 'b', 'type': 'text'})
            self.assertIsNone(agent_ops_store.get_operation(self.db, self.pid, 'parent'))
            with self.assertRaisesRegex(ValueError, 'parent.*pruned'):
                self.agent('child', {'id': 'c', 'type': 'text'}, parent='parent', flush=False)


if __name__ == '__main__':
    unittest.main()
