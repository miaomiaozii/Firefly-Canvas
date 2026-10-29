"""#260 批量重命名契约：画布多选节点 → prefix+pad+start 批量改名（对照 ComfyArt BatchRename）。

回归形状：
1. 弹窗（DOM）、入口（工具栏按钮 + F2）、逻辑（canvas-node-ops.js）、i18n 词条、快捷键说明
   五处只要少一处，功能就是「按钮点了没反应」或「按键打开空弹窗」——本测试钉住五处齐全。
2. 编号模板（pad 补零 / start 起点）是纯计算，容易被改成「直接拼数字」而丢掉补零对齐，
   这里用 Node 直接求值画布源码里的 batchRenameLabel/batchRenamePlan，钉住真实行为而不是文本。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from tests.canvas_frontend_sources import ROOT

NODE_OPS_JS = ROOT / "static" / "js" / "canvas-node-ops.js"
SMART_CANVAS_JS = ROOT / "static" / "js" / "smart-canvas.js"
SMART_CANVAS_HTML = ROOT / "static" / "smart-canvas.html"
I18N_SMART_JS = ROOT / "static" / "js" / "i18n" / "smart-canvas.js"

BATCH_KEYS = (
    "smart.batchRename",
    "smart.batchRenamePrefix",
    "smart.batchRenamePad",
    "smart.batchRenameStart",
    "smart.batchRenameCount",
    "smart.batchRenameNeedMulti",
    "smart.batchRenameDone",
    "smart.shortcutBatchRename",
)


class BatchRenameContract(unittest.TestCase):
    def test_dialog_markup_exists(self):
        html = SMART_CANVAS_HTML.read_text(encoding="utf-8")
        for element_id in (
            "smartBatchRenameBackdrop",
            "smartBatchRenamePrefix",
            "smartBatchRenamePad",
            "smartBatchRenameStart",
            "smartBatchRenameCount",
            "smartBatchRenamePreview",
            "smartBatchRenameCancel",
            "smartBatchRenameApply",
        ):
            self.assertIn(f'id="{element_id}"', html, f"批量重命名弹窗缺少 #{element_id}")

    def test_entries_are_wired(self):
        html = SMART_CANVAS_HTML.read_text(encoding="utf-8")
        smart = SMART_CANVAS_JS.read_text(encoding="utf-8")
        self.assertIn('id="smartBatchRenameBtn"', html, "缺少工具栏批量重命名按钮")
        self.assertIn('aria-keyshortcuts="F2"', html, "按钮必须声明 F2（可发现性）")
        self.assertIn('data-i18n="smart.shortcutBatchRename"', html, "快捷键说明必须列出 F2")
        self.assertIn("openBatchRenameDialog()", smart, "smart-canvas.js 未接批量重命名入口")
        self.assertIn("if(key === 'f2')", smart, "F2 快捷键分支缺失")
        # 少于 2 个选中时按钮必须禁用（两个选中态同步点都要）
        self.assertEqual(
            smart.count("smartBatchRenameBtn.disabled"),
            2,
            "批量重命名按钮的禁用状态必须在两个选中态同步点都同步",
        )

    def test_logic_lives_in_node_ops_module(self):
        source = NODE_OPS_JS.read_text(encoding="utf-8")
        for name in (
            "function batchRenameTargets",
            "function batchRenameLabel",
            "function batchRenamePlan",
            "function openBatchRenameDialog",
            "function applyBatchRename",
            "function closeBatchRenameDialog",
        ):
            self.assertIn(name, source, f"canvas-node-ops.js 缺少 {name}()")
        self.assertIn("'batch-rename'", source, "弹窗必须登记到 FireflyEscapeStack（#249 约定）")
        self.assertIn("pushUndo()", source, "批量改名必须压一次撤销快照")
        self.assertIn("canvasCore.scheduleSave()", source, "批量改名必须触发保存")

    def test_i18n_keys_defined(self):
        source = I18N_SMART_JS.read_text(encoding="utf-8")
        for key in BATCH_KEYS:
            self.assertIn(f'"{key}"', source, f"i18n 缺少 {key}")
            self.assertIn("zh:", source.split(f'"{key}"', 1)[1].splitlines()[0], f"{key} 缺少中文")
            self.assertIn("en:", source.split(f'"{key}"', 1)[1].splitlines()[0], f"{key} 缺少英文")

    @unittest.skipIf(shutil.which("node") is None, "需要 node 求值画布源码")
    def test_number_template_behavior(self):
        source = NODE_OPS_JS.read_text(encoding="utf-8")
        harness = (
            "const fs=require('fs');\n"
            f"const src=fs.readFileSync({json.dumps(str(NODE_OPS_JS))},'utf8');\n"
            "const canvasState={nodes:[{id:'a',title:'Cat'},{id:'b',title:'Dog'},{id:'c',title:'Outside'}]};\n"
            "const selectedNodeIds=()=>['a','b'];\n"
            "const api=new Function('canvasState','selectedNodeIds','escapeHtml','tr','trf','toast','pushUndo','canvasCore',src+\";return {batchRenameLabel,batchRenamePlan};\");\n"
            "const ops=api(canvasState,selectedNodeIds,()=>\"\",()=>\"\",()=>\"\",()=>{},()=>{},{});\n"
            "const label=(pad,start,i)=>ops.batchRenameLabel('Image_',pad,start,i);\n"
            "console.log(JSON.stringify({"
            "pad2:[label(2,1,0),label(2,1,9),label(2,1,10)],"
            "pad3:[label(3,7,0),label(3,7,2)],"
            "pad1:[label(1,0,0),label(1,0,10)],"
            "emptyPrefix:[ops.batchRenameLabel('',2,1,0)],"
            "plan:ops.batchRenamePlan('Shot_',2,5).map(e=>({from:e.from,to:e.to,conflict:e.conflict}))"
            "}));\n"
        )
        payload = json.loads(subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(payload["pad2"], ["Image_01", "Image_10", "Image_11"])
        self.assertEqual(payload["pad3"], ["Image_007", "Image_009"])
        self.assertEqual(payload["pad1"], ["Image_0", "Image_10"])
        self.assertEqual(payload["emptyPrefix"], ["01"])
        self.assertEqual(
            payload["plan"],
            [{"from": "Cat", "to": "Shot_05", "conflict": False}, {"from": "Dog", "to": "Shot_06", "conflict": False}],
            "计划必须按选择顺序编号，并只对照选择集外的节点标冲突",
        )


if __name__ == "__main__":
    unittest.main()
