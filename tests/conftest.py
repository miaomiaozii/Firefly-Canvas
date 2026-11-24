"""Suite-wide pytest hooks."""
import pytest

# 集中登记而不是在各文件写 pytestmark：这些文件多数是 unittest，quality_gate 还会用
# `python -m unittest` 直接跑其中一部分，保持它们不依赖 pytest。
# 入选标准：启动真实浏览器/子进程/门自测，且基线单例实测 ≳1s（见 D:/ff-qa/test-slim/REPORT.md）。
# 键是相对 tests/ 的模块路径；值为 None 表示整个文件，否则只标这些类。
SLOW = {
    "test_quality_gate.py": {"QualityGateTests"},
    "test_quality_gate_issue435.py": {"ProjectContractDiagnosticsTests"},
    "test_comfyui_connection_472.py": None,
    "test_agent_host_kill.py": None,
    "test_generation_log_reuse_ui.py": {"GenerationHistoryDrawerUiTests"},
    "test_canvas_module_units.py": {"CanvasModuleUnitsTests"},
    "test_tool_surface_consistency.py": {"DualPathEquivalenceTests"},
    "test_agent_cli_boundary.py": {"TestAgentCliSpawnBoundary"},
    "test_refresh_cache_exit.py": None,
    "test_dependency_preflight.py": {"CheckDependenciesBehaviorTests", "InstallBatVerifyTests", "LauncherGuardsTests"},
    "test_chat_media_attachments.py": {"VideoContactSheetFfmpegTests"},
    "test_process_tree.py": {"OrphanedTreeTests", "SpawnOnceTests", "ProcessJobTests"},
    "test_conversation_browser_harness.py": {"ConversationBrowserHarnessTests"},
    "eval/trajectory/test_pipeline.py": None,
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        parts = item.nodeid.split("::")
        path = parts[0].replace("\\", "/")
        if not path.startswith("tests/"):
            continue
        key = path[len("tests/"):]
        if key not in SLOW:
            continue
        classes = SLOW[key]
        if classes is None or (len(parts) > 2 and parts[1] in classes):
            item.add_marker(pytest.mark.slow)
