"""孤儿排队记录被服务端清理后，已打开的浏览器要丢掉卡片并停止对它的轮询。

服务端清理写的是 failed 终态记录；canvas_updated 合并靠 history 终态把本地那份
pendingTasks 一起丢掉，轮询也要在本地不再挂着该任务时停下，而不是再跑 30 分钟。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.canvas_frontend_sources import ROOT
from tests.test_canvas_merge_position_guard import (
    GENERATION_TASKS_JS,
    SMART_CANVAS_JS,
    _merge_harness,
    _top_level_function,
)
from tools.quality_gate import trusted_node_executable

ORPHAN_ID = "project_img_de56806a1f1e4daab904b5f6f5987e9d"
NODE_ID = "smart_xrla9u1uojnr"


def _function(source: str, name: str) -> str:
    """_top_level_function only matches sync declarations; pollSmartCanvasTask is async."""
    if f"\nasync function {name}(" in source:
        return "async " + _top_level_function(source.replace(f"\nasync function {name}(", f"\nfunction {name}("), name)
    return _top_level_function(source, name)


def _orphan_node_executable() -> str:
    node = trusted_node_executable()
    if not Path(node).is_file():
        node = shutil.which("node")
    assert node and Path(node).is_file(), f"node 不可用（{node}）"
    return node


def _run_orphan_script(script: str) -> dict:
    result = subprocess.run([_orphan_node_executable(), "-e", script], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"node 执行失败：{result.stderr.strip()[:1500]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _terminal_record() -> dict:
    return {"id": "generation-" + "f" * 32, "task_id": ORPHAN_ID, "status": "failed",
            "termination_reason": "orphan_pending", "result_node_id": NODE_ID}


class OrphanPendingFrontendTests(unittest.TestCase):
    def test_canvas_updated_merge_drops_the_orphan_card(self):
        local = {"id": NODE_ID, "type": "smart-image", "images": [], "pending": 2, "running": False,
                 "pendingTasks": [{"taskId": ORPHAN_ID, "kind": "image", "count": 2}]}
        remote = {"id": NODE_ID, "type": "smart-image", "images": [], "pending": 0, "running": False}
        terminal = _top_level_function(SMART_CANVAS_JS.read_text(encoding="utf-8"), "terminalSmartGenerationTaskIds")
        script = "\n".join([
            _merge_harness(), terminal, "const NODE_TYPE_TASK = 'agent-task';",
            f"const merged = mergeSmartNode({json.dumps(local)}, {json.dumps(remote)},"
            f" terminalSmartGenerationTaskIds([{json.dumps(_terminal_record())}]), {{authoritativeMedia:true}});",
            "console.log(JSON.stringify(merged));",
        ])
        merged = _run_orphan_script(script)
        self.assertNotIn("pendingTasks", merged)
        self.assertEqual(merged["pending"], 0)
        self.assertFalse(merged["running"])

    def _poll(self, nodes: list, history: list) -> dict:
        source = GENERATION_TASKS_JS.read_text(encoding="utf-8")
        functions = [_function(source, name) for name in ("pollSmartCanvasTask", "smartPendingTasks")]
        if "function smartTaskSettledByServer(" in source:
            functions.append(_function(source, "smartTaskSettledByServer"))
        script = "\n".join([
            "globalThis.setTimeout = fn => { fn(); return 0; };",
            "const activeSmartTaskPolls = new Map();",
            f"const canvasState = {{nodes: {json.dumps(nodes)}}};",
            f"const canvas = {{format: 'firefly-canvas-v1', project_id: 'p', history: {json.dumps(history)}}};",
            "let fetches = 0;",
            "function projectImageTaskUrl(id){ return '/t/' + id; }",
            "function applySmartTaskProgress(){}",
            "function absorbProjectGenerationResult(r){ return r; }",
            "function failSmartPendingHistoryLog(){ return false; }",
            "function extractUpstreamTaskId(){ return ''; }",
            "function tr(k){ return k; }",
            "globalThis.fetch = async () => { fetches += 1; return {ok: true, json: async () => ({status: fetches < 3 ? 'download_failed' : 'succeeded', result: {}})}; };",
            *functions,
            f"pollSmartCanvasTask({json.dumps(ORPHAN_ID)}).then(",
            "  () => console.log(JSON.stringify({fetches, outcome: 'resolved'})),",
            "  e => console.log(JSON.stringify({fetches, outcome: 'rejected', settled: Boolean(e && e.taskSettledByServer)})));",
        ])
        return _run_orphan_script(script)

    def test_polling_stops_once_server_settled_and_card_is_gone(self):
        nodes = [{"id": NODE_ID, "type": "smart-image", "pending": 0}]
        outcome = self._poll(nodes, [_terminal_record()])
        self.assertEqual(outcome, {"fetches": 0, "outcome": "rejected", "settled": True})

    def test_polling_continues_while_card_still_pending_locally(self):
        nodes = [{"id": NODE_ID, "type": "smart-image", "pending": 2,
                  "pendingTasks": [{"taskId": ORPHAN_ID, "kind": "image", "count": 2}]}]
        outcome = self._poll(nodes, [_terminal_record()])
        self.assertEqual(outcome, {"fetches": 3, "outcome": "resolved"})


if __name__ == "__main__":
    unittest.main()
