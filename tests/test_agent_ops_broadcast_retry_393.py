import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import firefly_json_io
import main
from app.routers import api_agent_ops
from app.services import agent_ops_delta, agent_ops_store, agent_ops_undo
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class UndoBroadcastRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('broadcast')['id']
        self.store.open_project(self.pid)
        self.db = OpsDb(self.store.root)
        self.journal = AgentOpsJournal(self.store, self.db)
        self.addCleanup(self.journal.close)
        self.path = self.store.root / self.pid / 'canvas.json'
        store_patch = patch.object(main, 'FIREFLY_PROJECT_STORE', self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.broadcast = AsyncMock()
        broadcast_patch = patch.object(main.manager, 'broadcast_canvas_updated', self.broadcast)
        broadcast_patch.start()
        self.addCleanup(broadcast_patch.stop)

    def disk_nodes(self):
        return json.loads(self.path.read_text(encoding='utf-8'))['nodes']

    def add(self, op_id='add', node_id='n', turn='turn'):
        with self.journal.mutation(self.pid) as operation:
            before = self.store.canvas(self.pid)
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {'id': node_id, 'type': 'text'}}],
                                       base_revision=before['revision'])
            delta = agent_ops_delta.build_delta(before, self.store.canvas(self.pid))
            old, new = record_sides(delta)
            operation.register({'op_id': op_id, 'turn_id': turn, 'agent_node_id': 'agent', 'tool': 'test',
                                'args': {}, 'before': old, 'after': new,
                                'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                                'created_at': '2026-01-01', 'irreversible_effects': [], 'undone': False}, delta)
        self.store.flush(self.pid)
        self.assertEqual(operation.result()['status'], 'settled')

    def assert_broadcast(self, op_id, touched):
        self.broadcast.assert_awaited_once_with(
            self.pid, int(self.store.canvas(self.pid)['updated_at']), source='agent',
            op_id=op_id, touched_node_ids=touched, phase='undo')

    async def test_pre_replacement_failure_retry_notifies_once(self):
        self.add()
        original = firefly_json_io._replace_staged

        def fail_canvas(staging, target):
            if target == self.path:
                raise OSError('before replacement')
            return original(staging, target)

        with patch.object(firefly_json_io, '_replace_staged', side_effect=fail_canvas):
            with self.assertRaises(HTTPException) as error:
                await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(len(self.disk_nodes()), 1)
        self.broadcast.assert_not_awaited()
        result = await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(result['status'], 'already_reverted')
        self.assertEqual(result['reverted'], [])
        self.assertNotIn('broadcast_op_ids', result)
        self.assertEqual(self.disk_nodes(), [])
        self.assert_broadcast('add', ['n'])
        await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assert_broadcast('add', ['n'])

    async def test_sidecar_failure_notifies_after_persist_then_not_again(self):
        self.add()
        with patch.object(agent_ops_store, 'mark_undone', side_effect=OSError('sidecar')):
            with self.assertRaises(HTTPException) as error:
                await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(self.disk_nodes(), [])
        self.assert_broadcast('add', ['n'])
        result = await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(result['status'], 'already_reverted')
        self.assert_broadcast('add', ['n'])

    async def test_user_already_reverted_and_persisted_does_not_notify(self):
        self.add()
        canvas = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [{'op': 'remove_node', 'node_id': 'n'}],
                                   base_revision=canvas['revision'], force_empty=True)
        self.store.flush(self.pid)
        result = await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(result['status'], 'already_reverted')
        self.broadcast.assert_not_awaited()

    async def test_already_reverted_in_memory_but_not_on_disk_notifies(self):
        self.add()
        canvas = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [{'op': 'remove_node', 'node_id': 'n'}],
                                   base_revision=canvas['revision'], force_empty=True)
        self.assertEqual(len(self.disk_nodes()), 1)
        result = await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(result['status'], 'already_reverted')
        self.assertEqual(self.disk_nodes(), [])
        self.assert_broadcast('add', ['n'])

    async def test_turn_retry_uses_all_touched_nodes_and_none_op_id(self):
        self.add('a', 'z')
        self.add('b', 'a')
        original = firefly_json_io._replace_staged

        def fail_canvas(staging, target):
            if target == self.path:
                raise OSError('before replacement')
            return original(staging, target)

        with patch.object(firefly_json_io, '_replace_staged', side_effect=fail_canvas):
            with self.assertRaises(HTTPException):
                await api_agent_ops.undo_ops_core(self.pid, {'turn_id': 'turn'})
        self.broadcast.assert_not_awaited()
        result = await api_agent_ops.undo_ops_core(self.pid, {'turn_id': 'turn'})
        self.assertEqual(result['status'], 'already_reverted')
        self.assert_broadcast(None, ['a', 'z'])

    async def test_effect_only_preserves_existing_broadcast(self):
        agent_ops_store.append_operation(self.db, self.pid, {
            'op_id': 'effect', 'turn_id': 'turn', 'parent_op_id': None, 'agent_node_id': 'agent',
            'tool': 'media.delete', 'args': {}, 'before': [], 'after': [], 'touched_node_ids': [],
            'created_at': '2026-01-01', 'irreversible_effects': ['deleted'], 'undone': False})
        result = await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'effect'})
        self.assertEqual(result['status'], 'reverted')
        self.assert_broadcast('effect', [])
        await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'effect'})
        self.assert_broadcast('effect', [])

    async def test_normal_undo_broadcast_is_not_duplicated(self):
        self.add()
        result = await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(result['status'], 'reverted')
        self.assert_broadcast('add', ['n'])

    async def test_service_reports_only_inverses_newly_persisted(self):
        self.add('a', 'a')
        self.add('b', 'b')
        canvas = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [{'op': 'remove_node', 'node_id': 'a'}],
                                   base_revision=canvas['revision'])
        self.store.flush(self.pid)
        result = agent_ops_undo.undo_ops(self.store, self.pid, turn_id='turn')
        self.assertEqual(result['broadcast_op_ids'], ['b'])
        self.assertEqual(result['already_reverted'], ['a'])

    async def test_retry_replays_overlapping_inverses_in_order(self):
        self.add('a', 'n')
        with self.journal.mutation(self.pid) as operation:
            before = self.store.canvas(self.pid)
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {'id': 'n', 'type': 'text', 'v': 1}}],
                                       base_revision=before['revision'])
            delta = agent_ops_delta.build_delta(before, self.store.canvas(self.pid))
            old, new = record_sides(delta)
            operation.register({'op_id': 'b', 'turn_id': 'turn', 'agent_node_id': 'agent', 'tool': 'test',
                                'args': {}, 'before': old, 'after': new,
                                'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                                'created_at': '2026-01-01', 'irreversible_effects': [], 'undone': False}, delta)
        self.store.flush(self.pid)
        self.assertEqual(operation.result()['status'], 'settled')
        with patch.object(firefly_json_io, '_replace_staged', side_effect=OSError('before')):
            self.assertEqual(agent_ops_undo.undo_ops(self.store, self.pid, turn_id='turn')['status'], 'pending')
        with patch.object(agent_ops_store, 'mark_undone', side_effect=OSError('sidecar')):
            result = agent_ops_undo.undo_ops(self.store, self.pid, turn_id='turn')
        self.assertEqual(result['broadcast_op_ids'], ['b', 'a'])
        self.assertEqual(result['status'], 'error')
        result = agent_ops_undo.undo_ops(self.store, self.pid, turn_id='turn')
        self.assertEqual(result['status'], 'already_reverted')
        self.assertNotIn('broadcast_op_ids', result)

    async def test_net_zero_turn_has_no_newly_persisted_inverse_ids(self):
        self.add('initial', 'n', turn='initial')
        for op_id, node in [('set', {'id': 'n', 'type': 'text', 'v': 1}),
                            ('unset', {'id': 'n', 'type': 'text'})]:
            with self.journal.mutation(self.pid) as operation:
                before = self.store.canvas(self.pid)
                self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': node}],
                                           base_revision=before['revision'])
                delta = agent_ops_delta.build_delta(before, self.store.canvas(self.pid))
                old, new = record_sides(delta)
                operation.register({'op_id': op_id, 'turn_id': 'net-zero', 'agent_node_id': 'agent', 'tool': 'test',
                                    'args': {}, 'before': old, 'after': new,
                                    'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                                    'created_at': '2026-01-01', 'irreversible_effects': [], 'undone': False}, delta)
            self.store.flush(self.pid)
            self.assertEqual(operation.result()['status'], 'settled')
        before = self.disk_nodes()
        result = agent_ops_undo.undo_ops(self.store, self.pid, turn_id='net-zero')
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(self.disk_nodes(), before)
        self.assertNotIn('broadcast_op_ids', result)

    async def test_post_replacement_error_does_not_rebroadcast_on_sidecar_retry(self):
        self.add()
        original = firefly_json_io._replace_staged

        def fail_history(staging, target):
            if target.name == 'history.json':
                raise OSError('history')
            return original(staging, target)

        with patch.object(firefly_json_io, '_replace_staged', side_effect=fail_history), \
             patch.object(agent_ops_store, 'mark_undone', side_effect=OSError('sidecar')):
            result = agent_ops_undo.undo_ops(self.store, self.pid, op_id='add')
        self.assertEqual(result['broadcast_op_ids'], ['add'])
        self.assertEqual(result['status'], 'error')
        result = agent_ops_undo.undo_ops(self.store, self.pid, op_id='add')
        self.assertNotIn('broadcast_op_ids', result)

    async def test_contract_unions_ids_once_and_remains_internal(self):
        self.add('a', 'z')
        self.add('b', 'a')
        service_result = {'status': 'reverted', 'reverted': ['a'], 'already_reverted': ['b'],
                          'conflicts': [], 'irreversible_effects': [], 'error': None,
                          'broadcast_op_ids': ['a', 'b']}
        with patch.object(api_agent_ops.agent_ops_undo, 'undo_ops', return_value=service_result):
            result = await api_agent_ops.undo_ops_core(self.pid, {'turn_id': 'turn'})
        self.assertNotIn('broadcast_op_ids', result)
        self.assert_broadcast(None, ['a', 'z'])

    async def test_contract_broadcasts_before_reporting_error(self):
        self.add()
        service_result = {'status': 'error', 'reverted': [], 'already_reverted': [],
                          'conflicts': [], 'irreversible_effects': [], 'error': 'sidecar',
                          'broadcast_op_ids': ['add']}
        with patch.object(api_agent_ops.agent_ops_undo, 'undo_ops', return_value=service_result):
            with self.assertRaises(HTTPException) as error:
                await api_agent_ops.undo_ops_core(self.pid, {'op_id': 'add'})
        self.assertEqual(error.exception.status_code, 500)
        self.assertNotIn('broadcast_op_ids', error.exception.detail.get('details', {}))
        self.assert_broadcast('add', ['n'])


if __name__ == '__main__':
    unittest.main()
