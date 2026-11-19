"""Cancellation-safe execution of blocking agent operations."""

import asyncio


async def run_blocking_to_completion(func, /, *args, **kwargs):
    """Run a blocking operation without abandoning it on caller cancellation.

    Cancellation wins over either the result or an exception from the operation,
    but is raised only after the operation has exited, including its cleanup.
    Without cancellation, the operation's result or exception is propagated.
    The first observed cancellation is retained if the caller cancels repeatedly;
    an operation exception is retrieved and discarded when cancellation wins.
    As with any coroutine, cancellation before it starts cannot launch the work.
    """
    return await await_to_completion(asyncio.to_thread(func, *args, **kwargs))


async def await_to_completion(coro):
    """Drain a coroutine before propagating caller cancellation."""
    task = asyncio.create_task(coro)
    cancellation = None
    # A previously handled cancellation must not be mistaken for a new one.
    initial_cancels = asyncio.current_task().cancelling()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if (cancellation is None and task.cancelled()
                    and asyncio.current_task().cancelling() == initial_cancels):
                return task.result()
            if cancellation is None:
                cancellation = exc
        except BaseException:
            if cancellation is None:
                raise
            break

    if cancellation is not None:
        # Retrieve failures even when cancellation takes priority over them.
        try:
            task.result()
        except BaseException:
            pass
        raise cancellation
    return task.result()
