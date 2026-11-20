"""#467：空项目夹具也必须隔离进程任务表，不能继承前序测试的任务。"""
import unittest

import pytest

from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from tests import test_global_tasks as console_tests


@pytest.mark.parametrize("victim_method", [
    "test_empty_console_has_no_tasks_and_no_projects",
    "test_live_inmemory_task_shown_and_cancel_reuses_project_endpoint",
])
def test_console_fixture_isolates_and_restores_predecessor_tasks(victim_method):
    task = {
        "id": "predecessor-task-467", "project_id": "predecessor-project-467",
        "type": "project-online-image", "status": "succeeded",
        "created_at": 1_700_000_000, "updated_at": 1_700_000_001,
    }
    with CANVAS_TASK_LOCK:
        previous = dict(CANVAS_TASKS)
        CANVAS_TASKS.clear()
        CANVAS_TASKS[task["id"]] = task
    try:
        # 必须走真实夹具；只 mock API 会漏掉 setUp/tearDown 的进程状态泄漏。
        victim = console_tests.GlobalTasksConsoleTests(victim_method)
        result = unittest.TestResult()
        victim.run(result)
        assert result.testsRun == 1
        assert result.errors == []
        assert result.failures == []
        with CANVAS_TASK_LOCK:
            assert CANVAS_TASKS == {task["id"]: task}
            assert CANVAS_TASKS[task["id"]] is task
    finally:
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.clear()
            CANVAS_TASKS.update(previous)
