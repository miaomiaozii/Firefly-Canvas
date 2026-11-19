import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import AgentOpsJournal, before_append_operation, record_sides
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class SidecarFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('sidecar')['id']
        self.db = OpsDb(self.store.root)
        self.journal = AgentOpsJournal(self.store, self.db)
        self.addCleanup(self.journal.close)

    def agent(self, op_id, node_id):
        with self.journal.mutation(self.pid) as operation:
            before = self.store.open_project(self.pid)['canvas']
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
                'id': node_id, 'type': 'text', 'value': 1}}], base_revision=before['revision'])
            delta = agent_ops_delta.build_delta(before, self.store.open_project(self.pid)['canvas'])
            old, new = record_sides(delta)
            operation.register({'op_id': op_id, 'turn_id': 'turn', 'agent_node_id': 'agent',
                                'tool': 'test', 'args': {}, 'before': old, 'after': new,
                                'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                                'created_at': '2026-01-01', 'irreversible_effects': [],
                                'undone': False}, delta)
        return operation

    def test_failed_later_op_blocks_undo_of_earlier_op_in_turn(self):
        self.agent('earlier', 'a')
        self.store.flush(self.pid)
        self.agent('failed', 'b')
        with patch.object(agent_ops_store, 'append_operation', side_effect=OSError('sidecar unavailable')):
            self.store.flush(self.pid)
        before = self.store.open_project(self.pid)['canvas']
        result = undo_ops(self.store, self.pid, op_id='earlier')
        self.assertEqual(result['status'], 'conflict', result)
        self.assertIn('sidecar append failed', result['error'])
        self.assertEqual(self.store.open_project(self.pid)['canvas'], before)

    def test_failed_turn_stays_irreversible_after_flush_and_user_edit(self):
        self.agent('earlier', 'a')
        self.store.flush(self.pid)
        self.agent('failed', 'b')
        with patch.object(agent_ops_store, 'append_operation', side_effect=OSError('sidecar unavailable')):
            self.store.flush(self.pid)
        self.store.flush(self.pid)
        canvas = self.store.open_project(self.pid)['canvas']
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
            'id': 'a', 'type': 'text', 'value': 2}}], base_revision=canvas['revision'])
        self.store.flush(self.pid)
        before = self.store.open_project(self.pid)['canvas']
        with (self.store.root / self.pid / 'canvas.json').open(encoding='utf-8') as source:
            disk_before = json.load(source)
        for target in ({'op_id': 'earlier'}, {'op_id': 'failed'}, {'turn_id': 'turn'}):
            result = undo_ops(self.store, self.pid, **target)
            self.assertEqual(result['status'], 'conflict', result)
            self.assertIn('sidecar append failed', result['error'])
            self.assertEqual(self.store.open_project(self.pid)['canvas'], before)
            with (self.store.root / self.pid / 'canvas.json').open(encoding='utf-8') as source:
                self.assertEqual(json.load(source), disk_before)
        self.assertTrue(all(not r['undone'] for r in agent_ops_store.list_operations(self.db, self.pid)))

    def test_hook_does_not_backfill_direct_effect_after_database_outage(self):
        self.agent('earlier', 'a')
        with patch.object(agent_ops_store, 'append_operation', side_effect=OSError('offline')):
            self.store.flush(self.pid)
        effect = {'op_id': 'later', 'turn_id': 'other', 'agent_node_id': 'agent',
                  'tool': 'media.delete', 'args': {}, 'before': [], 'after': [],
                  'touched_node_ids': [], 'created_at': '2026-01-01',
                  'irreversible_effects': ['file deleted'], 'undone': False}
        with patch.object(agent_ops_store, 'append_operation', side_effect=OSError('offline')):
            self.assertTrue(before_append_operation(self.db, self.pid, effect))
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.assertIn('sidecar append failed', undo_ops(self.store, self.pid, op_id='later')['error'])
        self.store.flush(self.pid)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.assertIn('sidecar append failed', undo_ops(self.store, self.pid, turn_id='other')['error'])

    def test_hook_writes_direct_effect_without_retrying_failed_canvas(self):
        operation = self.agent('earlier', 'a')
        with patch.object(agent_ops_store, 'append_operation', side_effect=OSError('offline')):
            self.store.flush(self.pid)
        effect = {'op_id': 'later', 'turn_id': 'turn', 'agent_node_id': 'agent',
                  'tool': 'media.delete', 'args': {}, 'before': [], 'after': [],
                  'touched_node_ids': [], 'created_at': '2026-01-01',
                  'irreversible_effects': ['file deleted'], 'undone': False}
        self.assertTrue(before_append_operation(self.db, self.pid, effect))
        self.assertEqual(operation.result()['status'], 'append_failed')
        self.assertEqual([r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid)],
                         ['later'])
        canvas = self.store.open_project(self.pid)['canvas']
        self.assertEqual(next(n for n in canvas['nodes'] if n['id'] == 'a')['value'], 1)
        self.assertIn('sidecar append failed', undo_ops(self.store, self.pid, op_id='earlier')['error'])

    def test_direct_effect_post_commit_exception_does_not_duplicate_the_record(self):
        effect = {'op_id': 'later', 'turn_id': 'turn', 'parent_op_id': None,
                  'agent_node_id': 'agent', 'tool': 'media.delete', 'args': {},
                  'before': [], 'after': [], 'touched_node_ids': [], 'created_at': '2026-01-01',
                  'irreversible_effects': ['file deleted'], 'undone': False}
        original = agent_ops_store.append_operation
        def committed_then_failed(*args):
            original(*args)
            raise OSError('after commit')
        with patch.object(agent_ops_store, 'append_operation', side_effect=committed_then_failed):
            self.assertTrue(before_append_operation(self.db, self.pid, effect))
        self.store.flush(self.pid)
        self.assertEqual([r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid)], ['later'])
        self.assertIn('sidecar append failed', undo_ops(self.store, self.pid, op_id='later')['error'])

    def test_store_hook_does_not_block_later_direct_effect(self):
        original = agent_ops_store.append_operation
        def integrated(opsdb, project_id, record):
            agent_ops_store._project_id(project_id)
            agent_ops_store._validate_record(record)
            if not before_append_operation(opsdb, project_id, record):
                return original(opsdb, project_id, record)
        with patch.object(agent_ops_store, 'append_operation', side_effect=integrated):
            self.test_direct_effect_continues_after_failed_canvas_append_without_backfill()

    def test_direct_effect_continues_after_failed_canvas_append_without_backfill(self):
        self.agent('earlier', 'a')
        with patch.object(agent_ops_store, 'append_operation', side_effect=OSError('sidecar unavailable')):
            self.store.flush(self.pid)
        disk = self.store.root / self.pid / 'canvas.json'
        self.assertEqual(json.loads(disk.read_text(encoding='utf-8'))['nodes'][0]['value'], 1)
        canvas = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
            'id': 'a', 'type': 'text', 'value': 2}}], base_revision=canvas['revision'])
        self.store.flush(self.pid)
        agent_ops_store.append_operation(self.db, self.pid, {
            'op_id': 'later', 'turn_id': 'turn', 'agent_node_id': 'agent', 'tool': 'media.delete',
            'args': {}, 'before': [], 'after': [], 'touched_node_ids': [], 'created_at': '2026-01-01',
            'irreversible_effects': ['file deleted'], 'undone': False})
        self.store.flush(self.pid)
        self.assertEqual([row['op_id'] for row in agent_ops_store.list_operations(self.db, self.pid)],
                         ['later'])
        for target in ({'turn_id': 'turn'}, {'op_id': 'later'}):
            outcome = undo_ops(self.store, self.pid, **target)
            self.assertEqual(outcome['status'], 'conflict', outcome)
            self.assertIn('sidecar append failed', outcome['error'])
        self.assertEqual(json.loads(disk.read_text(encoding='utf-8'))['nodes'][0]['value'], 2)
