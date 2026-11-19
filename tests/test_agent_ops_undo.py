import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io
from firefly_opsdb import OpsDb
from firefly_projects import InvalidMutation, ProjectStore
from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from app.services.agent_ops_undo import list_ops, undo_ops


class UndoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('undo')['id']
        self.store.open_project(self.pid)
        self.db = OpsDb(self.store.root)
        self.journal = AgentOpsJournal(self.store, self.db)
        self.addCleanup(self.journal.close)
        self.path = self.store.root / self.pid / 'canvas.json'

    def canvas(self):
        return self.store.open_project(self.pid)['canvas']

    def mutate(self, *ops):
        self.store.apply_mutations(self.pid, list(ops), base_revision=self.canvas()['revision'])

    def upsert(self, node):
        self.mutate({'op': 'upsert_node', 'node': node})

    def agent(self, op_id, ops, turn=None, effects=None, parent=None):
        with self.journal.mutation(self.pid) as operation:
            before = self.canvas()
            self.mutate(*ops)
            delta = agent_ops_delta.build_delta(before, self.canvas())
            old, new = record_sides(delta)
            record = {'op_id': op_id, 'turn_id': turn, 'agent_node_id': 'agent', 'tool': 'test',
                      'args': {}, 'before': old, 'after': new,
                      'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                      'created_at': '2026-01-01', 'irreversible_effects': effects or [], 'undone': False}
            if parent is not None:
                record['parent_op_id'] = parent
            operation.register(record, delta)
        self.store.flush(self.pid)
        self.assertEqual(operation.result()['status'], 'settled')

    def disk(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_list_lazy_filter_validation_and_missing(self):
        (self.store.root / 'empty').mkdir()
        self.assertEqual(list_ops(self.store, 'empty'), [])
        self.assertFalse(self.db.has_db('empty'))
        self.assertEqual(list_ops(self.store, self.pid), [])
        self.agent('one', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1}}], 't')
        self.agent('two', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 2}}], 'other')
        self.assertEqual([r['op_id'] for r in list_ops(self.store, self.pid, limit=1)], ['two'])
        self.assertEqual([r['op_id'] for r in list_ops(self.store, self.pid, turn_id='t')], ['one'])
        self.assertEqual(undo_ops(self.store, self.pid, op_id='absent')['status'], 'not_found')
        for args in ({}, {'op_id': 'a', 'turn_id': 'b'}, {'op_id': ''}):
            with self.assertRaises(ValueError):
                undo_ops(self.store, self.pid, **args)
        with self.assertRaises(ValueError):
            list_ops(self.store, self.pid, limit=0)

    def test_sole_node_and_already_undone_effects(self):
        self.agent('add', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text'}}], effects=['paid'])
        result = undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(result['reverted'], ['add'])
        self.assertEqual(result['irreversible_effects'], [{'op_id': 'add', 'effect': 'paid'}])
        self.assertEqual(self.canvas()['nodes'], [])
        self.assertEqual(self.disk()['nodes'], [])
        self.assertTrue(agent_ops_store.get_operation(self.db, self.pid, 'add')['undone'])
        self.assertEqual(undo_ops(self.store, self.pid, op_id='add')['already_reverted'], ['add'])

    def test_user_already_reverted_flushes_before_mark(self):
        self.agent('add', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text'}}])
        self.store.apply_mutations(self.pid, [{'op': 'remove_node', 'node_id': 'n'}],
                                   base_revision=self.canvas()['revision'], force_empty=True)
        self.assertEqual(len(self.disk()['nodes']), 1)
        result = undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['status'], 'already_reverted')
        self.assertEqual(result['already_reverted'], ['add'])
        self.assertEqual(self.disk()['nodes'], [])
        self.assertTrue(agent_ops_store.get_operation(self.db, self.pid, 'add')['undone'])

    def test_reverse_turn_and_preserve_unrelated_fields(self):
        self.upsert({'id': 'n', 'type': 'text', 'v': 0, 'extra': 'old'})
        self.store.flush(self.pid)
        for i in (1, 2):
            self.agent(str(i), [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': i, 'extra': 'old'}}], 't')
        self.upsert({'id': 'n', 'type': 'text', 'v': 2, 'extra': 'user'})
        result = undo_ops(self.store, self.pid, turn_id='t')
        self.assertEqual(result['reverted'], ['2', '1'])
        self.assertEqual(self.canvas()['nodes'], [{'id': 'n', 'type': 'text', 'v': 0, 'extra': 'user'}])

    def test_tree_reverse_occurrence_multiple_roots_and_running(self):
        self.upsert({'id': 'n', 'type': 'text', 'v': 0})
        self.store.flush(self.pid)
        def change(op, value, parent=None):
            self.agent(op, [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': value}}], 't', parent=parent)
        change('root', 1)
        change('other-root', 2)
        change('child', 3, 'root')
        change('grandchild', 4, 'child')
        before, disk = self.canvas(), self.disk()
        result = undo_ops(self.store, self.pid, turn_id='t', is_running=lambda pid, op: op == 'child')
        self.assertEqual(result['status'], 'generating')
        self.assertEqual(result['running'], ['child'])
        self.assertEqual(self.canvas(), before)
        self.assertEqual(self.disk(), disk)
        self.assertFalse(any(r['undone'] for r in list_ops(self.store, self.pid)))
        result = undo_ops(self.store, self.pid, op_id='root', is_running=lambda pid, op: op == 'grandchild')
        self.assertEqual(result['status'], 'generating')
        self.assertEqual(result['running'], ['grandchild'])
        self.assertEqual(self.canvas(), before)
        result = undo_ops(self.store, self.pid, op_id='root', is_running=lambda pid, op: False)
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(self.canvas(), before)
        self.assertFalse(any(r['undone'] for r in list_ops(self.store, self.pid)))
        result = undo_ops(self.store, self.pid, turn_id='t', is_running=lambda pid, op: False)
        self.assertEqual(result['reverted'], ['grandchild', 'child', 'other-root', 'root'])
        self.assertEqual(self.disk()['nodes'][0]['v'], 0)

    def test_tree_root_includes_multilevel_descendants(self):
        for op, parent, value in [('root', None, 1), ('child', 'root', 2), ('grandchild', 'child', 3)]:
            self.agent(op, [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': value}}], 't', parent=parent)
        result = undo_ops(self.store, self.pid, op_id='root')
        self.assertEqual(result['reverted'], ['grandchild', 'child', 'root'])
        self.assertEqual(self.disk()['nodes'], [])

    def test_empty_delta_effect_report_and_cross_project(self):
        item = {'op_id': 'deleted', 'turn_id': 't', 'parent_op_id': None,
                'agent_node_id': 'agent', 'tool': 'media.delete', 'args': {}, 'before': [], 'after': [],
                'touched_node_ids': [], 'created_at': '2026-01-01',
                'irreversible_effects': ['file deleted'], 'undone': False}
        agent_ops_store.append_operation(self.db, self.pid, item)
        other = self.store.create_project('other')['id']
        self.assertEqual(undo_ops(self.store, other, op_id='deleted')['status'], 'not_found')
        disk = self.disk()
        result = undo_ops(self.store, self.pid, op_id='deleted')
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(result['irreversible_effects'], [{'op_id': 'deleted', 'effect': 'file deleted'}])
        self.assertTrue(agent_ops_store.get_operation(self.db, self.pid, 'deleted')['undone'])
        self.assertEqual(self.disk(), disk)

    def test_turn_conflict_atomic_even_after_newest_would_apply(self):
        self.upsert({'id': 'n', 'type': 'text', 'v': 0, 'w': 0})
        self.store.flush(self.pid)
        self.agent('one', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1, 'w': 0}}], 't')
        self.agent('two', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1, 'w': 1}}], 't')
        self.upsert({'id': 'n', 'type': 'text', 'v': 5, 'w': 1})
        before, disk = self.canvas(), self.disk()
        result = undo_ops(self.store, self.pid, turn_id='t')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['conflicts'][0]['op_id'], 'one')
        self.assertEqual(self.canvas(), before)
        self.assertEqual(self.disk(), disk)
        self.assertFalse(any(r['undone'] for r in list_ops(self.store, self.pid)))

    def test_turn_snapshot_exceeds_cap_and_undoes_every_record(self):
        self.upsert({'id': 'n', 'type': 'text', 'v': 0})
        self.store.flush(self.pid)
        for i in range(1, 5):
            self.agent(str(i), [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': i}}], 'large')
        with patch.object(agent_ops_store, 'MAX_OPERATIONS', 2):
            result = undo_ops(self.store, self.pid, turn_id='large')
        self.assertEqual(result['reverted'], ['4', '3', '2', '1'])
        self.assertEqual(self.disk()['nodes'][0]['v'], 0)
        self.assertTrue(all(record['undone'] for record in agent_ops_store.list_turn_snapshot(self.db, self.pid, 'large')))

    def test_turn_snapshot_exceeds_cap_conflict_keeps_every_record(self):
        self.upsert({'id': 'n', 'type': 'text', 'v': 0, 'w': 0})
        self.store.flush(self.pid)
        self.agent('old', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1, 'w': 0}}], 'large')
        for i in range(1, 4):
            self.agent(str(i), [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1, 'w': i}}], 'large')
        self.upsert({'id': 'n', 'type': 'text', 'v': 9, 'w': 3})
        before, disk = self.canvas(), self.disk()
        with patch.object(agent_ops_store, 'MAX_OPERATIONS', 2):
            result = undo_ops(self.store, self.pid, turn_id='large')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(result['conflicts'][0]['op_id'], 'old')
        self.assertEqual(self.canvas(), before)
        self.assertEqual(self.disk(), disk)
        self.assertFalse(any(record['undone'] for record in agent_ops_store.list_turn_snapshot(self.db, self.pid, 'large')))

    def test_incident_edge_and_group_membership_are_conflicts(self):
        for relation in ('edge', 'group'):
            with self.subTest(relation=relation):
                self.agent(relation, [{'op': 'upsert_node', 'node': {'id': relation, 'type': 'text'}}])
                if relation == 'edge':
                    self.mutate({'op': 'upsert_edge', 'edge': {'from': relation, 'to': 'other'}})
                else:
                    self.upsert({'id': 'group', 'type': 'smart-group', 'items': [relation]})
                result = undo_ops(self.store, self.pid, op_id=relation)
                self.assertEqual(result['status'], 'conflict')
                self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, relation)['undone'])

    def test_deleted_node_and_edge_restored_without_touching_other_nodes(self):
        self.upsert({'id': 'a', 'type': 'text'})
        self.upsert({'id': 'b', 'type': 'text'})
        self.mutate({'op': 'upsert_edge', 'edge': {'from': 'a', 'to': 'b', 'kind': 'input'}})
        self.store.flush(self.pid)
        self.agent('remove', [{'op': 'remove_node', 'node_id': 'a'}])
        self.upsert({'id': 'b', 'type': 'text', 'user': True})
        result = undo_ops(self.store, self.pid, op_id='remove')
        self.assertEqual(result['status'], 'reverted')
        canvas = self.disk()
        self.assertEqual({n['id']: n for n in canvas['nodes']}, {
            'a': {'id': 'a', 'type': 'text'}, 'b': {'id': 'b', 'type': 'text', 'user': True}})
        self.assertEqual(canvas['connections'], [{'from': 'a', 'to': 'b', 'kind': 'input'}])

    def test_store_rejecting_inverse_is_conflict_without_mutation(self):
        self.agent('add', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text'}}])
        before, disk = self.canvas(), self.disk()
        with patch.object(self.store, '_apply_mutation', side_effect=InvalidMutation('media unavailable')):
            result = undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['status'], 'conflict')
        self.assertIn('media unavailable', result['conflicts'][0]['reason'])
        self.assertEqual(self.canvas(), before)
        self.assertEqual(self.disk(), disk)
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'add')['undone'])

    def test_pre_replacement_failure_pending_retry_no_false_mark(self):
        self.agent('add', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text'}}])
        original = firefly_json_io._replace_staged
        def fail(staging, target):
            if target == self.path:
                raise OSError('before')
            return original(staging, target)
        with patch.object(firefly_json_io, '_replace_staged', side_effect=fail):
            result = undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['status'], 'pending')
        self.assertIn('before', result['error'])
        self.assertEqual(len(self.disk()['nodes']), 1)
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'add')['undone'])
        result = undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['status'], 'already_reverted')
        self.assertEqual(self.disk()['nodes'], [])
        self.assertTrue(agent_ops_store.get_operation(self.db, self.pid, 'add')['undone'])

    def test_pre_failure_then_user_overwrites_conflicts_without_false_mark(self):
        self.upsert({'id': 'n', 'type': 'text', 'v': 0})
        self.store.flush(self.pid)
        self.agent('change', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1}}])
        with patch.object(firefly_json_io, '_replace_staged', side_effect=OSError('before')):
            result = undo_ops(self.store, self.pid, op_id='change')
        self.assertEqual(result['status'], 'pending')
        self.upsert({'id': 'n', 'type': 'text', 'v': 9})
        result = undo_ops(self.store, self.pid, op_id='change')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(self.canvas()['nodes'][0]['v'], 9)
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'change')['undone'])

    def test_malformed_record_is_rejected_before_mutation(self):
        self.agent('add', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text'}}])
        record = agent_ops_store.get_operation(self.db, self.pid, 'add')
        record['op_id'] = 'bad'
        record['after'][0]['id'] = 'other'
        agent_ops_store.append_operation(self.db, self.pid, record)
        before, disk = self.canvas(), self.disk()
        with self.assertRaisesRegex(ValueError, 'sides disagree'):
            undo_ops(self.store, self.pid, op_id='bad')
        self.assertEqual(self.canvas(), before)
        self.assertEqual(self.disk(), disk)
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'bad')['undone'])

    def test_post_replacement_failures_mark_and_report(self):
        for failure in ('history', 'listener', 'cleanup'):
            with self.subTest(failure=failure):
                self.agent(failure, [{'op': 'upsert_node', 'node': {'id': failure, 'type': 'text'}}])
                original = firefly_json_io._replace_staged
                staging = self.path.parent / '.undo-test.tmp'
                original_staging, original_unlink = firefly_json_io._staging_path, Path.unlink
                def replace(source, target):
                    if failure == 'history' and target.name == 'history.json':
                        raise OSError(failure)
                    return original(source, target)
                def stage(target):
                    return staging if failure == 'cleanup' and target == self.path else original_staging(target)
                def unlink(path, *args, **kwargs):
                    if failure == 'cleanup' and path == staging:
                        raise OSError(failure)
                    return original_unlink(path, *args, **kwargs)
                def listener(pid, canvas):
                    raise OSError(failure)
                if failure == 'listener':
                    self.store.add_persisted_canvas_listener(listener)
                try:
                    with patch.object(firefly_json_io, '_replace_staged', side_effect=replace), \
                         patch.object(firefly_json_io, '_staging_path', side_effect=stage), \
                         patch.object(Path, 'unlink', unlink):
                        result = undo_ops(self.store, self.pid, op_id=failure)
                finally:
                    if failure == 'listener':
                        self.store.remove_persisted_canvas_listener(listener)
                self.assertEqual(result['status'], 'reverted')
                self.assertIn(failure, result['error'])
                self.assertNotIn(failure, [n['id'] for n in self.disk()['nodes']])
                self.assertTrue(agent_ops_store.get_operation(self.db, self.pid, failure)['undone'])

    def test_sidecar_failure_never_claims_mark_and_retry(self):
        self.agent('add', [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text'}}])
        with patch.object(agent_ops_store, 'mark_undone', side_effect=OSError('sidecar')):
            result = undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['reverted'], [])
        self.assertIn('sidecar', result['error'])
        self.assertEqual(self.disk()['nodes'], [])
        self.assertFalse(agent_ops_store.get_operation(self.db, self.pid, 'add')['undone'])
        self.assertEqual(undo_ops(self.store, self.pid, op_id='add')['status'], 'already_reverted')


if __name__ == '__main__':
    unittest.main()
