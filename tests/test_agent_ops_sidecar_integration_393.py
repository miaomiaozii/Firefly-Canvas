import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main  # Initialize the application module used by the router.
from app.routers import api_agent_tools as tools
from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import record_sides
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class SidecarIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('sidecar')['id']
        self.db = OpsDb(self.store.root)
        self.addCleanup(self.close_journal)

    def close_journal(self):
        with tools._JOURNALS_LOCK:
            journal = tools._JOURNALS.pop(self.store, None)
        if journal:
            journal.close()

    def context(self, op='effect', turn='effect-turn'):
        return SimpleNamespace(op_id=op, turn_id=turn, agent_node_id='agent',
                               tool='test', args={})

    def canvas_op(self):
        journal = tools._journal_for(self.store)
        with journal.mutation(self.pid) as op:
            before = self.store.canvas(self.pid)
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
                'id': 'n', 'type': 'text', 'value': 1}}], base_revision=before['revision'])
            delta = agent_ops_delta.build_delta(before, self.store.canvas(self.pid))
            old, new = record_sides(delta)
            op.register({'op_id': 'canvas', 'turn_id': 'canvas-turn', 'agent_node_id': 'agent',
                         'tool': 'test', 'args': {}, 'before': old, 'after': new,
                         'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                         'created_at': '2026-01-01', 'irreversible_effects': [], 'undone': False}, delta)
        return op

    def effect(self, op='direct'):
        return {'op_id': op, 'turn_id': 'direct-turn', 'agent_node_id': 'agent', 'tool': 'test',
                'args': {}, 'before': [], 'after': [], 'touched_node_ids': [],
                'created_at': '2026-01-01', 'irreversible_effects': ['effect'], 'undone': False}

    def test_effect_only_preflights_without_canvas_mutation(self):
        guarded = set()
        tools._media_root(self.store, self.pid, self.context(), 'attempted', guarded)
        self.assertEqual(guarded, {'effect'})
        self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, 'effect')['irreversible_effects'], ['attempted'])
        tools.set_agent_generation_running(self.store, self.pid, 'effect', 'effect', False)
        context = self.context('session')
        tools._delete_session_effect(self.store, self.pid, context, {'outcome': 'unknown'}, create=True)
        tools._delete_session_effect(self.store, self.pid, context, {'outcome': 'done'}, create=False)
        self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, 'session')['irreversible_effects'], [{'outcome': 'done'}])

    def test_pending_canvas_must_not_allow_media_or_session_preflight(self):
        op = self.canvas_op()
        with self.assertRaisesRegex(RuntimeError, 'journal pending'):
            tools._media_root(self.store, self.pid, self.context(), 'attempted', set())
        with self.assertRaisesRegex(RuntimeError, 'journal pending'):
            tools._delete_session_effect(self.store, self.pid, self.context('session'), {'outcome': 'unknown'}, create=True)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.store.flush(self.pid)
        self.assertEqual(op.result()['status'], 'settled')
        self.assertEqual([r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid)],
                         ['session', 'effect', 'canvas'])

    def test_database_unavailable_does_not_backfill_failed_records(self):
        op = self.canvas_op()
        def offline(db, *args, **kwargs):
            raise OSError('database offline')
        with patch.object(OpsDb, 'connect', offline):
            self.store.flush(self.pid)
            with self.assertRaisesRegex(RuntimeError, 'journal append_failed'):
                tools._media_root(self.store, self.pid, self.context(), 'attempted', set())
            agent_ops_store.append_operation(self.db, self.pid, self.effect())
            self.assertEqual(op.result()['status'], 'append_failed')
        self.store.flush(self.pid)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        for turn in ('canvas-turn', 'effect-turn', 'direct-turn'):
            self.assertEqual(undo_ops(self.store, self.pid, turn_id=turn)['status'], 'conflict')
        agent_ops_store.append_operation(self.db, self.pid, self.effect('after-outage'))
        self.assertEqual([r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid)],
                         ['after-outage'])

    def test_effect_only_unavailable_database_is_not_reported_as_success(self):
        def offline(db, *args, **kwargs):
            raise OSError('database offline')
        guarded = set()
        with patch.object(OpsDb, 'connect', offline):
            with self.assertRaisesRegex(RuntimeError, 'journal append_failed'):
                tools._media_root(self.store, self.pid, self.context(), 'attempted', guarded)
            with self.assertRaisesRegex(OSError, 'database offline'):
                tools._delete_session_effect(self.store, self.pid, self.context('session'),
                                             {'outcome': 'unknown'}, create=True)
        self.assertEqual(guarded, set())
        self.store.flush(self.pid)
        self.assertEqual(agent_ops_store.list_operations(self.db, self.pid), [])
        self.assertEqual(undo_ops(self.store, self.pid, turn_id='effect-turn')['status'], 'conflict')

    def test_postcommit_exception_does_not_block_later_direct_effect(self):
        op = self.canvas_op()
        original = agent_ops_store.append_operation
        def fail_after_commit(db, pid, record):
            if record['op_id'] == 'canvas':
                original(db, pid, record)
                raise OSError('postcommit')
            return original(db, pid, record)
        with patch.object(agent_ops_store, 'append_operation', side_effect=fail_after_commit):
            self.store.flush(self.pid)
        self.assertEqual(op.result()['status'], 'append_failed')
        agent_ops_store.append_operation(self.db, self.pid, self.effect())
        self.assertEqual(op.result()['status'], 'append_failed')
        self.assertEqual([r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid)],
                         ['direct', 'canvas'])
        self.assertEqual(undo_ops(self.store, self.pid, turn_id='canvas-turn')['status'], 'conflict')

    def test_cancelled_direct_effect_waits_for_worker_and_keeps_record(self):
        from app.services.agent_ops_async import run_blocking_to_completion

        async def scenario():
            loop = asyncio.get_running_loop()
            started = asyncio.Event()
            release = threading.Event()
            guarded = set()

            def worker():
                try:
                    tools._media_root(self.store, self.pid, self.context(), 'attempted', guarded)
                    loop.call_soon_threadsafe(started.set)
                    if not release.wait(5):
                        raise TimeoutError('worker not released')
                finally:
                    if 'effect' in guarded:
                        tools.set_agent_generation_running(self.store, self.pid, 'effect', 'effect', False)

            task = asyncio.create_task(run_blocking_to_completion(worker))
            try:
                await asyncio.wait_for(started.wait(), 3)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                self.assertEqual(agent_ops_store.get_operation(self.db, self.pid, 'effect')['irreversible_effects'],
                                 ['attempted'])
                self.assertFalse(tools.agent_generation_is_running(self.store, self.pid, 'effect'))
            finally:
                release.set()
                if not task.done():
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())

    def test_direct_effect_serializes_behind_inflight_canvas_mutation(self):
        journal = tools._journal_for(self.store)
        started, finished = threading.Event(), threading.Event()
        errors = []
        def append():
            started.set()
            try:
                agent_ops_store.append_operation(self.db, self.pid, self.effect())
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()
        with journal.mutation(self.pid) as op:
            before = self.store.canvas(self.pid)
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
                'id': 'n', 'type': 'text'}}], base_revision=before['revision'])
            thread = threading.Thread(target=append)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(.05))
            delta = agent_ops_delta.build_delta(before, self.store.canvas(self.pid))
            old, new = record_sides(delta)
            op.register({'op_id': 'canvas', 'turn_id': 'canvas-turn', 'agent_node_id': 'agent',
                         'tool': 'test', 'args': {}, 'before': old, 'after': new,
                         'touched_node_ids': agent_ops_delta.touched_node_ids(delta),
                         'created_at': '2026-01-01', 'irreversible_effects': [], 'undone': False}, delta)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.store.flush(self.pid)
        self.assertEqual([r['op_id'] for r in agent_ops_store.list_operations(self.db, self.pid)],
                         ['direct', 'canvas'])
