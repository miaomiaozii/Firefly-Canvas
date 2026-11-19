import asyncio
import contextvars
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.services.agent_ops_async import run_blocking_to_completion


class RunBlockingToCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_arguments_and_single_to_thread_call(self):
        def operation(left, *, right):
            return left + right

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
            result = await run_blocking_to_completion(operation, 2, right=3)
        self.assertEqual(result, 5)
        to_thread.assert_called_once_with(operation, 2, right=3)

    async def test_operation_exception_is_propagated(self):
        error = ValueError("operation failed")

        def operation():
            raise error

        with self.assertRaises(ValueError) as raised:
            await run_blocking_to_completion(operation)
        self.assertIs(raised.exception, error)

    async def test_context_is_propagated_to_thread(self):
        variable = contextvars.ContextVar("agent_operation", default="missing")
        token = variable.set("operation-393")
        try:
            self.assertEqual(await run_blocking_to_completion(variable.get), "operation-393")
        finally:
            variable.reset(token)

    async def test_repeated_cancellation_waits_for_finally_and_wins_over_error(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        in_finally = asyncio.Event()
        release_work = threading.Event()
        release_finally = threading.Event()
        settled = threading.Event()

        def operation():
            try:
                loop.call_soon_threadsafe(started.set)
                if not release_work.wait(5):
                    raise TimeoutError("work was not released")
                raise ValueError("operation failed after cancellation")
            finally:
                loop.call_soon_threadsafe(in_finally.set)
                if not release_finally.wait(5):
                    raise TimeoutError("cleanup was not released")
                settled.set()

        caller = asyncio.create_task(run_blocking_to_completion(operation))
        try:
            await asyncio.wait_for(started.wait(), 2)
            caller.cancel("first")
            await asyncio.sleep(0)
            self.assertFalse(caller.done())
            caller.cancel("second")
            await asyncio.sleep(0)
            self.assertFalse(caller.done())
            release_work.set()
            await asyncio.wait_for(in_finally.wait(), 2)
            self.assertFalse(caller.done())
            self.assertFalse(settled.is_set())
            release_finally.set()
            with self.assertRaises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(caller, 2)
            self.assertEqual(raised.exception.args, ("first",))
            self.assertTrue(settled.is_set())
        finally:
            release_work.set()
            release_finally.set()
            if not caller.done():
                await asyncio.wait_for(asyncio.gather(caller, return_exceptions=True), 2)

    async def test_cancellation_wins_over_successful_result(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def operation():
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("work was not released")
            return "completed"

        caller = asyncio.create_task(run_blocking_to_completion(operation))
        try:
            await asyncio.wait_for(started.wait(), 2)
            caller.cancel("cancelled")
            await asyncio.sleep(0)
            self.assertFalse(caller.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(caller, 2)
            self.assertEqual(raised.exception.args, ("cancelled",))
        finally:
            release.set()
            if not caller.done():
                await asyncio.wait_for(asyncio.gather(caller, return_exceptions=True), 2)

    async def test_cancellation_while_queued_still_runs_operation(self):
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        occupied = threading.Event()
        release_slot = threading.Event()
        ran = threading.Event()

        def occupy_thread():
            occupied.set()
            if not release_slot.wait(5):
                raise TimeoutError("executor slot was not released")

        occupied_future = executor.submit(occupy_thread)
        caller = None
        try:
            self.assertTrue(occupied.wait(2))
            caller = asyncio.create_task(run_blocking_to_completion(ran.set))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(ran.is_set())
            caller.cancel()
            await asyncio.sleep(0)
            self.assertFalse(caller.done())
            release_slot.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(caller, 2)
            self.assertTrue(ran.is_set())
        finally:
            release_slot.set()
            if caller is not None and not caller.done():
                await asyncio.wait_for(asyncio.gather(caller, return_exceptions=True), 2)
            occupied_future.result(timeout=2)
            executor.shutdown(wait=True)

    async def test_previously_handled_cancellation_does_not_mask_worker_cancellation(self):
        current = asyncio.current_task()
        current.cancel("already handled")
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

        def operation():
            raise asyncio.CancelledError("from worker")

        with self.assertRaises(asyncio.CancelledError) as raised:
            await run_blocking_to_completion(operation)
        self.assertEqual(raised.exception.args, ("from worker",))

    async def test_worker_cancelled_error_does_not_loop(self):
        def operation():
            raise asyncio.CancelledError("from worker")

        with self.assertRaises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(run_blocking_to_completion(operation), 2)
        self.assertEqual(raised.exception.args, ("from worker",))
