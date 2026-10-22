"""前端「比例 ↔ 模型」契约：切换模型后不能残留该模型不支持的比例。

回归背景：节点 runSettings 里存着 banana 的 1:4（ratio: "portrait14"），切到
gpt-image 家族（长宽比上限 3:1）后，chip 因为 `|| ratio === current` 的逃生门仍然可选，
请求发出后在路由预检阶段被拒：
  没有手动固定的模型完整支持本次请求（不兼容参数：aspect_ratio）
用户只看到一句「不支持」，不知道该怎么改。

测试方式：从 static/js/smart-canvas.js 里按大括号配对切出相关函数，注入 stub 后用
node 真实执行（前端是浏览器脚本，没法直接 import）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "static" / "js" / "smart-canvas.js"

DOCUMENTED_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "9:21"]


def _node_executable() -> str:
    """与 tests/test_canvas_module_units.py 同口径：先取质量门受信 node，回退 PATH。

    裸 "node" 在门内会被 sitecustomize 守卫拒：
    PermissionError: quality gate denied node arguments（20261022-112044 trial）。
    """
    from tools.quality_gate import trusted_node_executable

    trusted = trusted_node_executable()
    if Path(trusted).is_file():
        return trusted
    found = shutil.which("node")
    return found if found else trusted
EXTREME_RATIOS = ["1:4", "4:1", "8:1", "1:8"]


def _slice_balanced(source: str, header: str) -> str:
    """从 header 起切到与之配对的大括号结束（含函数体内的 ${} 与注释，成对抵消）。"""
    start = source.index(header)
    cursor = source.index("{", start)
    depth = 0
    for index in range(cursor, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


class CanvasRatioModelAlignmentTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node not available")
        source = SOURCE.read_text(encoding="utf-8")
        presets = re.search(r"const IMAGE_RATIO_PRESETS = \[.*?\n\];", source, re.S)
        cls.snippets = {
            "IMAGE_RATIO_PRESETS": presets.group(0) if presets else None,
            "IMAGE_RATIO_PRESET_KEYS_BY_RATIO": re.search(
                r"const IMAGE_RATIO_PRESET_KEYS_BY_RATIO = .*?;\n", source, re.S).group(0),
            "parseRatioValue": _slice_balanced(source, "function parseRatioValue(value){"),
            "ratioLog": _slice_balanced(source, "function ratioLog(value){"),
            "apiImageAllowedAspectRatios": _slice_balanced(source, "function apiImageAllowedAspectRatios(sourceSettings=settings){"),
            "apiImageRatioPresets": _slice_balanced(source, "function apiImageRatioPresets(sourceSettings=settings){"),
            "alignRatioToModel": _slice_balanced(source, "function alignRatioToModel(sourceSettings=settings){"),
        }
        for name, snippet in cls.snippets.items():
            if not snippet:
                raise AssertionError(f"missing {name} in {SOURCE}")

    def _run(self, provider_capabilities: dict, model_specs: dict, settings: dict) -> dict:
        harness = f"""
{self.snippets['IMAGE_RATIO_PRESETS']}
{self.snippets['IMAGE_RATIO_PRESET_KEYS_BY_RATIO']}
{self.snippets['parseRatioValue']}
{self.snippets['ratioLog']}
{self.snippets['apiImageAllowedAspectRatios']}
{self.snippets['apiImageRatioPresets']}
{self.snippets['alignRatioToModel']}
const PROVIDER = {{id: "p", image_model_capabilities: {json.dumps(provider_capabilities)}}};
const modelSpecs = {json.dumps(model_specs)};
function apiProviderById(id) {{ return id === "p" ? PROVIDER : null; }}
const settings = {json.dumps(settings)};
const before = apiImageRatioPresets(settings).map(([, ratio]) => ratio);
const aligned = alignRatioToModel(settings);
console.log(JSON.stringify({{
  allowed: apiImageAllowedAspectRatios(settings),
  presetsBefore: before,
  aligned,
  ratioAfter: settings.ratio,
  ratioValueAfter: (IMAGE_RATIO_PRESETS.find(([value]) => value === settings.ratio) || [])[1],
}}));
"""
        # 走 `node -e`，不往 %TEMP% 落脚本：门对 node 的放行只有
        # 「恰 2 参数且 operation ∈ {-e, --check}」或「单个 tests/ 开头的 .mjs 路径」
        # （tools/quality_gate.py:938-946）。临时文件的绝对路径两个都不满足，在门里
        # 直接 PermissionError: quality gate denied node arguments（20261022-112044）。
        # harness 是纯脚本（无 import/export），-e 能跑。
        completed = subprocess.run(
            [_node_executable(), "-e", harness],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        return json.loads(completed.stdout)

    def test_gpt_model_chip_list_excludes_extreme_ratios_it_cannot_render(self):
        result = self._run(
            {"gpt-image-2.5-flare": {"aspect_ratios": DOCUMENTED_RATIOS}},
            {},
            {"model": "gpt-image-2.5-flare", "provider_id": "p", "ratio": "portrait14"},
        )
        self.assertEqual(result["presetsBefore"], DOCUMENTED_RATIOS)
        self.assertNotIn("1:4", result["presetsBefore"])

    def test_stale_extreme_ratio_is_snapped_to_the_nearest_supported_ratio_same_orientation(self):
        result = self._run(
            {"gpt-image-2.5-flare": {"aspect_ratios": DOCUMENTED_RATIOS}},
            {},
            {"model": "gpt-image-2.5-flare", "provider_id": "p", "ratio": "portrait14"},
        )
        self.assertEqual(result["aligned"], "1:4→9:21")
        self.assertEqual(result["ratioAfter"], "ultratall")
        self.assertEqual(result["ratioValueAfter"], "9:21")

    def test_supported_ratio_is_left_untouched(self):
        result = self._run(
            {"nano-banana-2": {"aspect_ratios": DOCUMENTED_RATIOS + EXTREME_RATIOS}},
            {},
            {"model": "nano-banana-2", "provider_id": "p", "ratio": "portrait14"},
        )
        self.assertEqual(result["aligned"], "")
        self.assertEqual(result["ratioAfter"], "portrait14")
        self.assertIn("1:4", result["presetsBefore"])

    def test_unknown_capability_table_does_not_touch_user_selection(self):
        result = self._run({}, {}, {"model": "some-new-model", "provider_id": "p", "ratio": "portrait14"})
        self.assertIsNone(result["allowed"])
        self.assertEqual(len(result["presetsBefore"]), 15)
        self.assertEqual(result["aligned"], "")
        self.assertEqual(result["ratioAfter"], "portrait14")

    def test_ratio_presets_no_longer_keep_unsupported_current_ratio(self):
        body = self.snippets["apiImageRatioPresets"]
        self.assertNotIn("ratio === current", body.replace(" ", ""))
        self.assertIn("allowed.includes(ratio)", body)

    def test_ratio_alignment_runs_on_model_switch_and_on_picker_render(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertRegex(source, r"if\(key === 'model'\)\s*alignRatioToModel\(settings\);")
        self.assertRegex(source, r"if\(!prefix\) alignRatioToModel\(settings\);")


if __name__ == "__main__":
    unittest.main()
