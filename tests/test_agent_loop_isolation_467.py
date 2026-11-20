"""#467：同步测试夹具拥有自己的 loop，不依赖或改写调用线程的 loop。"""
import asyncio
import unittest

import pytest

from tests import test_agent_event_dispatch as dispatch_tests
from tests import test_agent_host_kill as kill_tests
from tests import test_agent_host_usage as usage_tests


class RecordingLoopPolicy(asyncio.DefaultEventLoopPolicy):
    def __init__(self):
        super().__init__()
        self.assignments = []

    def set_event_loop(self, loop):
        self.assignments.append(loop)
        super().set_event_loop(loop)


def make_case(fixture):
    if fixture == "dispatch":
        return dispatch_tests.AgentEventDispatchTests("test_sync_and_async_handlers_both_receive_without_error")
    if fixture == "usage":
        return usage_tests.AgentHostUsageTests("test_agent_end_records_usage_turn")

    class KillFixtureCase(kill_tests.AgentHostKillBase):
        def runTest(self):
            self.assertEqual(self._run(asyncio.sleep(0, result="owned-loop")), "owned-loop")

    return KillFixtureCase()


@pytest.mark.parametrize("fixture", ["dispatch", "kill", "usage"])
@pytest.mark.parametrize("caller_state", ["fresh", "cleared", "after-run", "existing"])
def test_real_fixture_owns_loop_without_changing_callers_policy(fixture, caller_state):
    previous_policy = asyncio.get_event_loop_policy()
    policy = RecordingLoopPolicy()
    asyncio.set_event_loop_policy(policy)
    caller_loop = None
    case = None
    result = unittest.TestResult()
    try:
        if caller_state == "cleared":
            asyncio.set_event_loop(None)
        elif caller_state == "after-run":
            asyncio.run(asyncio.sleep(0))
        elif caller_state == "existing":
            caller_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(caller_loop)
        assignments_before = list(policy.assignments)
        case = make_case(fixture)
        case.run(result)
        assert result.testsRun == 1
        assert result.errors == []
        assert result.failures == []
        assert case.loop.is_closed()
        assert asyncio.get_event_loop_policy() is policy
        assert policy.assignments == assignments_before
        if caller_loop is not None:
            assert not caller_loop.is_closed()
            assert asyncio.get_event_loop() is caller_loop
        elif caller_state != "fresh":
            with pytest.raises(RuntimeError, match="There is no current event loop"):
                asyncio.get_event_loop()
    finally:
        # 失败的 setUp 不会调用 tearDown，红测也必须释放自己创建的存储。
        if case is not None and (result.errors or result.failures):
            store = getattr(case, "store", None)
            if store is not None:
                store.close()
            temporary = getattr(case, "temp_dir", None)
            if temporary is not None:
                temporary.cleanup()
        for loop in policy.assignments:
            if loop is not None and not loop.is_closed():
                loop.close()
        asyncio.set_event_loop_policy(previous_policy)


def test_kill_fixture_owns_and_restores_its_bound_app(monkeypatch):
    shadow_main = object()
    monkeypatch.setattr(kill_tests.agent_host, "_app_main", shadow_main)

    class KillStoreCase(kill_tests.AgentHostKillBase):
        def runTest(self):
            self.assertEqual(
                self.manager._get_project_dir(self.project_id),
                str(self.store.root / self.project_id),
            )

    result = unittest.TestResult()
    KillStoreCase().run(result)
    assert result.testsRun == 1
    assert result.errors == []
    assert result.failures == []
    assert kill_tests.agent_host._app_main is shadow_main
