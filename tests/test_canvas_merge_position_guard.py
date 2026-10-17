"""#165 守卫：远端画布合并不得用旧快照覆盖本地已拖动、尚未落盘的节点位置。

`mergeSmartNode` 把远端画布快照逐节点合并进 `canvasState.nodes`。生成分组框被拖动
释放后，新位置只存在于本地，要等自动保存（DEBOUNCED_SAVE_MS=3000ms）才落盘；在这个
窗口里到达的任何远端快照都会把旧位置写回来（issue #165 的「约 0.2 秒后一帧弹回」）。

本测试把 `mergeSmartNode` 及其依赖从前端源码里抽出来，用质量门同款的受信 node 直接
执行 JS 语义，断言两件事：

1. 带更新 `positionUpdatedAt` 的本地节点，其 x/y 必须赢过远端副本；远端更新时仍取远端。
2. `mergeSmartNode` 原有意图不变：不带媒体/任务状态的节点必须保持远端的持久化形状，
   只读轮询不得凭空造 `images:[]` 与 busy 标志（源码 :5167-5170 的注释）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.canvas_frontend_sources import ROOT
from tools.quality_gate import trusted_node_executable


SMART_CANVAS_JS = ROOT / "static" / "js" / "smart-canvas.js"
CORE_JS = ROOT / "static" / "js" / "canvas-core.js"
GENERATION_TASKS_JS = ROOT / "static" / "js" / "canvas-generation-tasks.js"
AGENT_TUI_JS = ROOT / "static" / "js" / "canvas-agent-tui.js"

# mergeSmartNode 的依赖闭包（跨模块的两个函数单独列出）。
SMART_CANVAS_FUNCTIONS = (
    "mergeSmartNode",
    "applyLocalNodePosition",
    "smartNodeUsesGenerationMerge",
    "mergeSmartImageLists",
    "smartNodeHasCompletedResult",
    "smartNodeHasDisplayResult",
    "smartNodeInFlight",
    "mergeSmartPendingTaskLists",
    "mergeSmartSubmissionLists",
    "completeSmartNodeWithImages",
    "markSmartNodeComplete",
    "clearSmartNodeBusyState",
)


def _top_level_function(source: str, name: str) -> str:
    """Slice a top-level `function name(...){...}` declaration out of a JS source."""
    pattern = re.compile(rf"^function {re.escape(name)}\s*\(")
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if not pattern.match(line):
            continue
        end = index
        while end + 1 < len(lines) and lines[end + 1] != "}":
            end += 1
        if end + 1 >= len(lines):
            raise AssertionError(f"unterminated function {name}")
        return "\n".join(lines[index:end + 2])
    raise AssertionError(f"top-level function not found: {name}")


def _merge_harness() -> str:
    smart_canvas = SMART_CANVAS_JS.read_text(encoding="utf-8")
    functions = [_top_level_function(smart_canvas, name) for name in SMART_CANVAS_FUNCTIONS]
    functions.append(_top_level_function(GENERATION_TASKS_JS.read_text(encoding="utf-8"), "smartPendingTasks"))
    functions.append(_top_level_function(CORE_JS.read_text(encoding="utf-8"), "smartGenerationSubmissions"))
    functions.append(_top_level_function(AGENT_TUI_JS.read_text(encoding="utf-8"), "isSmartAgentTaskNode"))
    return "\n\n".join([
        "// mergeSmartNode 的运行时依赖（画布全局）在单元守卫里给最小替身。",
        "const smartNodeRunTokens = new Map();",
        "const smartRunTokens = new Map();",
        "const canvasState = {nodes: []};",
        "const canvas = {format: 'firefly-canvas-v1'};",
        "const smartGenerationNodeRevisions = new Map();",
        "function nowMs(){ return Date.now(); }",
        "function tr(key){ return key; }",
        "function liveSmartNode(node){ return node; }",
        "function requestSmartEdgeReveal(){}",
        "function animateSmartGenerationResult(){}",
        "function scheduleSave(){}",
        "function render(){}",
        *functions,
    ])


def _run_merge_cases(cases: dict) -> dict:
    node = trusted_node_executable()
    if not Path(node).is_file():
        node = shutil.which("node")
    assert node and Path(node).is_file(), f"node 不可用（{node}）：守卫必须真跑，不能静默跳过"
    script = "\n".join([
        _merge_harness(),
        f"const cases = {json.dumps(cases, ensure_ascii=False)};",
        "const out = {};",
        "for (const [name, input] of Object.entries(cases)) {",
        "  out[name] = mergeSmartNode(input.local, input.remote);",
        "}",
        "console.log(JSON.stringify(out));",
    ])
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"node 合并守卫执行失败：{result.stderr.strip()[:1200]}")
    return json.loads(result.stdout)


class SmartNodeMergePositionGuardTests(unittest.TestCase):
    def setUp(self):
        self.group_local = {
            "id": "layer-group", "type": "smart-group", "x": 580, "y": 370,
            "images": [{"asset_id": "a1", "kind": "image", "url": "/api/media/a1"}],
            "runFinishedAt": 1000, "running": False, "pending": 0, "queued": False,
        }
        self.group_remote = {
            "id": "layer-group", "type": "smart-group", "x": 780, "y": 230,
            "images": [{"asset_id": "a1", "kind": "image", "url": "/api/media/a1"}],
            "runFinishedAt": 1000, "running": False, "pending": 0, "queued": False,
        }

    def test_newer_local_position_beats_remote_copy(self):
        """#165：本地刚拖动过（positionUpdatedAt 更新），远端旧快照不得把位置弹回。"""
        local = dict(self.group_local, positionUpdatedAt=2000)
        remote = dict(self.group_remote, positionUpdatedAt=1000)
        merged = _run_merge_cases({"drag": {"local": local, "remote": remote}})["drag"]
        self.assertEqual((merged["x"], merged["y"]), (580, 370),
                         f"远端旧快照覆盖了本地位置：merged={merged}")

    def test_newer_local_position_beats_remote_copy_without_generation_shape(self):
        """不带媒体/任务字段的节点（生成分组框的裸形状）同样要保住本地位置。"""
        local = {"id": "plain", "type": "smart-group", "x": 10, "y": 20, "positionUpdatedAt": 2000}
        remote = {"id": "plain", "type": "smart-group", "x": 30, "y": 40}
        merged = _run_merge_cases({"plain": {"local": local, "remote": remote}})["plain"]
        self.assertEqual((merged["x"], merged["y"]), (10, 20),
                         f"远端旧快照覆盖了本地位置：merged={merged}")

    def test_newer_remote_position_still_wins(self):
        """位置裁决是 last-write-wins，不是一味偏向本地。"""
        local = dict(self.group_local, positionUpdatedAt=1000)
        remote = dict(self.group_remote, x=900, y=120, positionUpdatedAt=2000)
        merged = _run_merge_cases({"remote": {"local": local, "remote": remote}})["remote"]
        self.assertEqual((merged["x"], merged["y"]), (900, 120),
                         f"远端位置更新时却被本地覆盖：merged={merged}")

    def test_local_without_position_stamp_follows_remote(self):
        """本地没有拖动记录时，完全跟随远端副本。"""
        local = dict(self.group_local)
        remote = dict(self.group_remote, x=900, y=120, positionUpdatedAt=2000)
        merged = _run_merge_cases({"unstamped": {"local": local, "remote": remote}})["unstamped"]
        self.assertEqual((merged["x"], merged["y"]), (900, 120),
                         f"无本地拖动记录时不应偏向本地：merged={merged}")

    def test_read_only_poll_still_keeps_exact_remote_shape(self):
        """源码 :5167-5170 保护的意图：只读轮询不得给无媒体节点造 images:[] 与 busy 标志。"""
        local = {"id": "plain", "type": "smart-group", "x": 10, "y": 20}
        remote = {"id": "plain", "type": "smart-group", "x": 30, "y": 40, "created_at": 7}
        merged = _run_merge_cases({"poll": {"local": local, "remote": remote}})["poll"]
        self.assertEqual(merged, remote, f"只读轮询改写了远端持久化形状：merged={merged}")

    def test_read_only_poll_with_local_stamp_only_rewrites_position(self):
        """位置守卫不得顺手给无媒体节点造出媒体/busy 字段。"""
        local = {"id": "plain", "type": "smart-group", "x": 10, "y": 20, "positionUpdatedAt": 2000}
        remote = {"id": "plain", "type": "smart-group", "x": 30, "y": 40, "created_at": 7}
        merged = _run_merge_cases({"pollStamped": {"local": local, "remote": remote}})["pollStamped"]
        self.assertEqual(merged, dict(remote, x=10, y=20, positionUpdatedAt=2000),
                         f"位置守卫改写了远端持久化形状：merged={merged}")

    def test_repeated_merge_before_save_keeps_local_position(self):
        """保存落盘前的第二次合并（另一个远端广播）也不得把位置弹回。"""
        first = _run_merge_cases({"a": {
            "local": dict(self.group_local, positionUpdatedAt=2000),
            "remote": dict(self.group_remote, updated_at=1500),
        }})["a"]
        second = _run_merge_cases({"b": {
            "local": first,
            "remote": dict(self.group_remote, x=1000, y=1000, updated_at=1600),
        }})["b"]
        self.assertEqual((second["x"], second["y"]), (580, 370),
                         f"连续合并把本地位置弹回：first={first} second={second}")


if __name__ == "__main__":
    unittest.main()
