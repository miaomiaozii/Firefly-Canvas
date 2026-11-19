import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import agent_ops_store as ops
from firefly_opsdb import OpsDb
from tests.test_agent_ops_store import record


class RetentionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'p').mkdir()
        self.db = OpsDb(self.root)
        self.cap = patch.object(ops, 'MAX_OPERATIONS', 3)
        self.cap.start()
        self.addCleanup(self.cap.stop)

    def begin(self, turn, owner='session', external=True):
        return ops.begin_turn(self.db, 'p', turn, owner, external=external)

    def add(self, op, turn=None, parent=None):
        item = record(op, turn)
        if parent:
            item['parent_op_id'] = parent
        ops.append_operation(self.db, 'p', item)

    def ids(self):
        return [r['op_id'] for r in ops.list_operations(self.db, 'p', limit=100)]

    def test_multiple_ended_turns_pruned_whole_and_active_preserved(self):
        self.begin('a')
        self.add('a1', 'a')
        self.add('a2', 'a')
        self.begin('b')
        self.add('b1', 'b')
        self.begin('c')
        self.add('c1', 'c')
        self.assertEqual(self.ids(), ['c1', 'b1'])
        self.add('c2', 'c')
        self.add('c3', 'c')
        self.assertEqual(self.ids(), ['c3', 'c2', 'c1'])

    def test_oversized_turn_warns_once_and_is_pruned_whole_only_after_end(self):
        self.begin('a')
        with self.assertLogs(ops.logger, level='WARNING') as logs:
            for i in range(5):
                self.add(str(i), 'a')
            self.assertEqual(self.ids(), ['4', '3', '2', '1', '0'])
            ops.mark_turn_lookup_failed(self.db, 'p', 'a')
            self.begin('b')
            self.add('new', 'b')
        self.assertEqual(self.ids(), ['new'])
        self.assertEqual(len(logs.records), 1)
        self.assertIn('exceeds retention cap', logs.records[0].getMessage())
        self.assertIsNone(ops.undo_failure(self.db, 'p', {'op_id': None, 'turn_id': 'a'}))

    def test_active_family_and_pending_turn_do_not_block_other_writes(self):
        self.begin('a')
        self.add('root', 'a')
        ops.set_active_operation(self.db, 'p', 'root', 'task', True)
        self.addCleanup(ops.set_active_operation, self.db, 'p', 'root', 'task', False)
        self.begin('b')
        self.add('b1', 'b')
        self.add('b2', 'b')
        self.begin('c')
        self.add('c1', 'c')
        self.assertEqual(self.ids(), ['c1', 'root'])
        self.add('child', 'a', parent='root')
        self.assertEqual(self.ids(), ['child', 'c1', 'root'])

    def test_external_timeout_and_hosted_no_timeout(self):
        with patch.object(ops.time, 'time', return_value=10000):
            self.begin('a')
            self.add('a1', 'a')
            self.begin('h', owner='host', external=False)
            self.add('h1', 'h')
        with patch.object(ops.time, 'time', return_value=10000 + ops.EXTERNAL_TURN_INACTIVITY_SECONDS):
            self.begin('b', owner='other-session')
            self.add('b1', 'b')
            self.add('b2', 'b')
        self.assertIsNone(ops.get_operation(self.db, 'p', 'a1'))
        self.assertIsNotNone(ops.get_operation(self.db, 'p', 'h1'))

    def test_inflight_lease_prevents_prune(self):
        self.assertIsNone(ops.begin_turn(self.db, 'p', 'a', 'session', external=True, reserve=True))
        self.add('a1', 'a')
        self.begin('b')
        self.add('b1', 'b')
        self.add('b2', 'b')
        self.add('b3', 'b')
        self.assertIn('a1', self.ids())
        ops.release_turn(self.db, 'p', 'a')
        self.add('b4', 'b')
        self.assertNotIn('a1', self.ids())


class HostedTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_begin_failure_keeps_hosted_prompt_running_but_disables_turn_undo(self):
        import main
        from app.services.agent_host import AgentSession, agent_host_manager
        from app.services.agent_ops_undo import undo_ops
        from firefly_projects import ProjectStore
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            session = AgentSession(project_id='p', node_id='host')
            send = AsyncMock(return_value={'sent': True})
            with patch.object(main, 'FIREFLY_PROJECT_STORE', store), \
                 patch.object(agent_host_manager, '_send_prompt_now', new=send), \
                 patch.object(ops, 'begin_turn', side_effect=OSError('sidecar unavailable')):
                self.assertEqual(await agent_host_manager._start_logical_turn(session, 'first'), {'sent': True})
            send.assert_awaited_once_with(session, 'first')
            outcome = undo_ops(store, 'p', turn_id=session._current_turn_id)
            self.assertEqual(outcome['status'], 'conflict')
            self.assertIn('sidecar turn lookup failed', outcome['error'])

    async def test_next_prompt_ends_previous_turn_even_after_response_end(self):
        import main
        from app.services.agent_host import AgentSession, agent_host_manager
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'p').mkdir()
            db = OpsDb(root)
            session = AgentSession(project_id='p', node_id='host')
            with patch.object(main, 'FIREFLY_PROJECT_STORE', SimpleNamespace(root=Path(root))), \
                 patch.object(agent_host_manager, '_send_prompt_now', new=AsyncMock(return_value={})), \
                 patch.object(ops, 'MAX_OPERATIONS', 1):
                await agent_host_manager._start_logical_turn(session, 'first')
                first = session._current_turn_id
                ops.append_operation(db, 'p', record('first', first))
                session._current_turn_id = None
                await agent_host_manager._start_logical_turn(session, 'second')
                second = session._current_turn_id
                self.assertNotEqual(first, second)
                ops.append_operation(db, 'p', record('second', second))
                self.assertIsNone(ops.get_operation(db, 'p', 'first'))
                self.assertIsNotNone(ops.get_operation(db, 'p', 'second'))
