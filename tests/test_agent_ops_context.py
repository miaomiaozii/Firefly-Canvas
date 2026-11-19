import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import unittest
from uuid import UUID

from app.services.agent_ops_context import (
    agent_ops_scope,
    create_agent_ops_task,
    current_agent_ops_context,
)


class AgentOpsContextTests(unittest.TestCase):
    def test_immutable_snapshot_and_server_generated_ids(self):
        args = {"node": {"tags": ["original", {"value": 2}]}}
        with agent_ops_scope(project_id="project", agent_node_id="agent", turn_id="turn",
                             tool="canvas.update_node", args=args) as operation:
            self.assertIs(current_agent_ops_context(), operation)
            self.assertEqual(UUID(operation.op_id).version, 4)
            args["node"]["tags"][1]["value"] = 3
            exposed = operation.args
            exposed["node"]["tags"].append("mutated")
            self.assertEqual(operation.args, {"node": {"tags": ["original", {"value": 2}]}})
            with self.assertRaises(FrozenInstanceError):
                operation.tool = "other"
            with agent_ops_scope(project_id="project", tool="canvas.update_node", args={}) as second:
                self.assertNotEqual(operation.op_id, second.op_id)
                self.assertIsNone(second.agent_node_id)
                self.assertIsNone(second.turn_id)
            self.assertIs(current_agent_ops_context(), operation)
        self.assertIsNone(current_agent_ops_context())

    def test_nested_exception_restores_original_and_invalid_scope_does_not_replace_it(self):
        with agent_ops_scope(project_id="outer", tool="outer", args={}) as outer:
            with self.assertRaisesRegex(RuntimeError, "abort"):
                with agent_ops_scope(project_id="inner", tool="inner", args={}) as inner:
                    self.assertIs(current_agent_ops_context(), inner)
                    raise RuntimeError("abort")
            self.assertIs(current_agent_ops_context(), outer)
            for invalid in ({"x": float("nan")}, {1: "non-string"}, {"x": object()}, []):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    with agent_ops_scope(project_id="p", tool="t", args=invalid):
                        pass
                self.assertIs(current_agent_ops_context(), outer)
            for field, value in (("project_id", ""), ("tool", " "),
                                 ("agent_node_id", ""), ("turn_id", "")):
                values = {"project_id": "p", "tool": "t", "args": {}}
                values[field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    with agent_ops_scope(**values):
                        pass
                self.assertIs(current_agent_ops_context(), outer)
        self.assertIsNone(current_agent_ops_context())


class AgentOpsAsyncContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_tasks_copy_and_isolate_scopes(self):
        ready = asyncio.Event()
        started = 0

        async def worker(index, parent):
            nonlocal started
            self.assertIs(current_agent_ops_context(), parent)
            with agent_ops_scope(project_id=f"p{index}", agent_node_id=f"a{index}",
                                 turn_id=f"turn{index}", tool="canvas.update_node",
                                 args={"index": index}) as operation:
                started += 1
                if started == 2:
                    ready.set()
                await ready.wait()
                await asyncio.sleep(0)
                self.assertIs(current_agent_ops_context(), operation)
                self.assertEqual(operation.args, {"index": index})
                self.assertEqual(operation.project_id, f"p{index}")
            self.assertIs(current_agent_ops_context(), parent)
            return operation

        with agent_ops_scope(project_id="parent", tool="parent", args={}) as parent:
            tasks = [create_agent_ops_task(worker(i, parent)) for i in range(2)]
            operations = await asyncio.gather(*tasks)
            self.assertIs(current_agent_ops_context(), parent)
            self.assertNotEqual(operations[0].op_id, operations[1].op_id)
        self.assertIsNone(current_agent_ops_context())

    async def test_inherited_task_can_outlive_scope_and_detached_task_is_empty(self):
        gate = asyncio.Event()

        async def read_after_gate():
            await gate.wait()
            return current_agent_ops_context()

        with agent_ops_scope(project_id="p", tool="t", args={}) as operation:
            inherited = create_agent_ops_task(read_after_gate())
            detached = contextvars.Context().run(asyncio.create_task, read_after_gate())
        self.assertIsNone(current_agent_ops_context())
        gate.set()
        self.assertIs(await inherited, operation)
        self.assertIsNone(await detached)

    async def test_thread_propagation_and_worker_restoration(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_running_loop()
            with agent_ops_scope(project_id="p", tool="t", args={}) as operation:
                self.assertIs(await asyncio.to_thread(current_agent_ops_context), operation)
                self.assertIsNone(await loop.run_in_executor(executor, current_agent_ops_context))
                copied = contextvars.copy_context()
                self.assertIs(await loop.run_in_executor(executor, copied.run, current_agent_ops_context), operation)
                self.assertIsNone(await loop.run_in_executor(executor, current_agent_ops_context))
                self.assertIs(current_agent_ops_context(), operation)
            self.assertIsNone(current_agent_ops_context())

    async def test_cancelled_task_restores_its_parent_context(self):
        entered = asyncio.Event()
        restored = []

        async def worker(parent):
            try:
                with agent_ops_scope(project_id="child", tool="t", args={}):
                    entered.set()
                    await asyncio.Event().wait()
            finally:
                restored.append(current_agent_ops_context())

        with agent_ops_scope(project_id="parent", tool="t", args={}) as parent:
            task = create_agent_ops_task(worker(parent))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(restored, [parent])
            self.assertIs(current_agent_ops_context(), parent)
        self.assertIsNone(current_agent_ops_context())
