"""Event loop policy isolation for tests that opt into this fixture."""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def isolated_event_loop_policy():
    # asyncio runners can clear the current loop; preserve it with its policy.
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)
