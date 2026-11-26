import builtins
import hashlib
import io
import json
import os
import re
import runpy
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import FunctionType, SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

from tests.complete_generation_lifecycle_fixture import write_complete_generation_lifecycle_runner
from tests.composer_flow_fixture import write_composer_flow_runner
from tests.edge_compatibility_fixture import write_edge_compatibility_runner
from tests.generation_lifecycle_fixture import write_generation_lifecycle_runner
from tests.long_content_fixture import write_long_content_runner
from tests.project_linked_generation_fixture import write_project_linked_generation_runner
from tests.ui_integrity_fixture import write_ui_integrity_runner, write_valid_ui_integrity_artifact
from tests.test_pre_commit_hook import git as hook_test_git
from tools import quality_gate
from tools.install_git_hooks import HOOKS_DIR, install as install_git_hooks
from tools.native_input_evidence import double_click_evidence, selector_owner_click_evidence
from tools.process_tree import kill_process_tree, process_alive
from tools.quality_gate import (
    _ACTIVE_STEP_PROCESSES,
    BROWSER_EVIDENCE_STEP_NAMES,
    EXIT_HOOKS_INSTALLED,
    GUARD_SOURCE,
    MODE_STEPS,
    STEP_DEFINITIONS,
    UNIT_CONTRACT_STEP_NAMES,
    blocking_guard_events,
    browser_step_spec,
    build_severity_findings,
    classify_guard_events,
    compute_unit_contract_shards,
    discover_unit_contract_modules,
    expected_native_focus,
    expand_unit_contract_steps,
    execution_groups,
    merge_gate_exit_code,
    failed_native_browser_evidence,
    step_exit_code,
    stop_leaked_children,
    terminate_active_step_processes,
    trusted_node_executable,
    unit_contract_modules_for_shards,
    unit_contract_slot_for_shard,
    wait_for_process_exit,
)
from tools.ui_integrity_evidence import accept_baseline
from tools.quality_gate import BrowserDenyProxy as _ProductionBrowserDenyProxy


ROOT = Path(__file__).resolve().parents[1]
QUALITY_GATE = ROOT / "tools" / "quality_gate.py"


def node_modules_snapshot(repo_root):
    entries = {}

    def visit(path, relative, ancestors):
        info = path.lstat()
        metadata = (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        is_link = stat.S_ISLNK(info.st_mode) or os.path.isjunction(path)
        if is_link:
            entries[relative] = (metadata, os.readlink(path))
            if not path.exists():
                return
            info = path.stat()
            metadata = (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").digest()
            entries[relative] = (entries.get(relative), metadata, digest)
        elif stat.S_ISDIR(info.st_mode):
            identity = (info.st_dev, info.st_ino)
            entries[relative] = (entries.get(relative), metadata, "directory")
            if identity not in ancestors:
                with os.scandir(path) as children:
                    for child in children:
                        visit(Path(child.path), relative / child.name, ancestors | {identity})
        elif not is_link:
            entries[relative] = (metadata, "special")

    root = repo_root / "node_modules"
    if os.path.lexists(root):
        visit(root, Path("."), set())
    return entries


@contextmanager
def assert_node_modules_unchanged(test, repo_root):
    before = node_modules_snapshot(repo_root)
    try:
        yield
    finally:
        test.assertEqual(before, node_modules_snapshot(repo_root), "步骤写到了工作仓库的 node_modules")


def unattributable_noise_source(targets, kind):
    """生成「归属不到任何进程」的出口噪声 fixture。

    #230：出口代理只看得到目标域名，看不到发起者。真实宿主工具（Antigravity、
    Gemini CLI 那类）不在门的进程树里，也不会被我们的进程内钩子看到，所以它们在
    证据里的形态就是「只有 kind + target、没有 pid」。这里直接往守卫事件文件里写
    这个形态，而不是让门的子进程去连代理——后者按 #230 的判据属于「可归属到门
    进程树」，必须判红；用它模拟宿主噪声会把语义搞反（#227 的容忍会被误伤）。
    """
    lines = [
        "import json",
        "import os",
        "import unittest",
        "",
        "class ControlPlaneNoiseContract(unittest.TestCase):",
        "    def test_control_plane_noise_is_observed(self):",
        "        sink = os.environ['FIREFLY_QUALITY_GATE_GUARD_EVENTS']",
        "        targets = [" + ", ".join(repr(str(target)) for target in targets) + "]",
        "        payload = " + repr(kind),
        "        for target in targets:",
        "            with open(sink, 'a', encoding='utf-8') as handle:",
        "                handle.write(json.dumps({'kind': payload, 'target': target}) + chr(10))",
        "",
    ]
    return "\n".join(lines) + "\n"


class CanvasCouplingGateWiringTests(unittest.TestCase):
    def test_fast_parallel_group_is_limited_to_independent_checks(self):
        self.assertEqual(execution_groups(MODE_STEPS['fast'], fast=True)[0],
                         ('parallel', ['javascript-syntax', 'javascript-types', 'canvas-coupling']))
        self.assertEqual(execution_groups(MODE_STEPS['fast'])[0],
                         ('sequential', ['javascript-syntax']))

    def test_fast_mode_runs_canvas_coupling_ratchet(self):
        step = "canvas-coupling"
        self.assertIn(step, MODE_STEPS["fast"])
        self.assertEqual(
            STEP_DEFINITIONS[step](sys.executable),
            [sys.executable, "-m", "unittest", "-b", "tests.test_canvas_core_layer", "tests.test_canvas_coupling_report"],
        )


class StudioColorGateWiringTests(unittest.TestCase):
    def test_fast_mode_runs_studio_color_guard(self):
        step = "studio-color-guard"
        self.assertIn(step, MODE_STEPS["fast"])
        self.assertEqual(
            STEP_DEFINITIONS[step](sys.executable),
            [sys.executable, "-m", "unittest", "-b", "tests.test_studio_color_guard"],
        )


class GateTempPaths(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-quality-gate-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.fixture_root = Path(self.temp_dir.name) / "repo"
        self.evidence_dir = Path(self.temp_dir.name) / "evidence"


class GateFixture(GateTempPaths):
    @staticmethod
    def guard_events(events):
        # #230：守卫事件现在带发起进程 pid（进程树归属证据），所以断言只比
        # kind + target，不再假定事件字典只有这两个键。
        return [
            {"kind": event.get("kind"), "target": event.get("target")}
            for event in events or []
        ]

    def setUp(self):
        super().setUp()
        (self.fixture_root / "static" / "js").mkdir(parents=True)
        (self.fixture_root / "tests").mkdir()
        (self.fixture_root / "static" / "js" / "smart-canvas.js").write_text(
            "const qualityGateFixture = true;\n",
            encoding="utf-8",
        )
        # #167：javascript-syntax 步骤现在跑 tests/test_canvas_frontend_sources.py，
        # 所以 fixture 里的桩必须真的对 static/js/*.js 跑 node --check：
        # 下面两个 S2 测试靠写坏 smart-canvas.js 来验证语法门会红。
        # node 路径用受信路径写进桩里，不能拿 PATH 上的 node（伪造 node 测试会误报）。
        (self.fixture_root / "tests" / "test_canvas_frontend_sources.py").write_text(
            "import subprocess\n"
            "import unittest\n"
            "from pathlib import Path\n\n"
            f"NODE = {trusted_node_executable()!r}\n"
            "ROOT = Path(__file__).resolve().parents[1]\n\n"
            "class CanvasFrontendSourceListTests(unittest.TestCase):\n"
            "    def test_every_canvas_module_passes_node_syntax_check(self):\n"
            "        for script in sorted((ROOT / 'static' / 'js').glob('*.js')):\n"
            "            result = subprocess.run(\n"
            "                [NODE, '--check', str(script)], capture_output=True, text=True,\n"
            "            )\n"
            "            self.assertEqual(result.returncode, 0, script.name + ': ' + result.stderr.strip())\n",
            encoding="utf-8",
        )
        # The minimal fixture exercises gate wiring, not the real analyses.
        for module_name in ("test_canvas_core_layer.py", "test_canvas_coupling_report.py", "test_studio_color_guard.py"):
            (self.fixture_root / "tests" / module_name).write_text(
                "import unittest\nclass CanvasFixture(unittest.TestCase):\n"
                "    def test_wiring(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        # #232：egress-attribution 步骤跑的是产品自己的取远端素材路径（app.core）。
        # 这里合成的是最小仓库骨架，没有 app/，所以给一个不碰网络的桩：这个
        # 套件验的是「门真的把这一步排进了每种模式」，探针行为由真门跑验。
        (self.fixture_root / "tests" / "test_product_egress_attribution.py").write_text(
            "import unittest\n\n"
            "class EgressAttributionProbeStub(unittest.TestCase):\n"
            "    def test_stub(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        # #356：surface-contract 步骤新增 tests.test_tool_surface_consistency.SourceSameOriginTests
        # （清单↔codegen freshness）。fixture 最小骨架里没有这个模块，同一套路给桩
        # （门只验接线；同源层真实断言由真门跑/全量 pytest 验）。
        (self.fixture_root / "tests" / "test_tool_surface_consistency.py").write_text(
            "import unittest\n\n"
            "class SourceSameOriginTests(unittest.TestCase):\n"
            "    def test_stub(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        # #261：doc-link-check（fast advisory）跑 tools/doc_link_check.py。fixture 是
        # 最小仓库骨架没有 docs/，给一个桩（门只验接线，报告行为由真门跑/单测验）。
        (self.fixture_root / "tools").mkdir(exist_ok=True)
        (self.fixture_root / "tools" / "doc_link_check.py").write_text(
            "import sys\n"
            "print('doc-link-check fixture stub')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        # #379：i18n-validate（fast）跑 static/js/i18n/validate-i18n.js。fixture 最小
        # 骨架没有 i18n 文件，给一个桩（门只验接线，校验器行为由真门跑验）。
        (self.fixture_root / "static" / "js" / "i18n").mkdir(parents=True, exist_ok=True)
        (self.fixture_root / "static" / "js" / "i18n" / "validate-i18n.js").write_text(
            "console.log('i18n ok: fixture stub');\n",
            encoding="utf-8",
        )
        # #167：新接入的两个画布浏览器 runner 在 fixture 里用最小桩，
        # 和 excel_import_browser.py 的桩同一套路（门只验接线，不验产品行为）。
        # #185：node_flow 级联与 fly_fixes 两个 runner 同一套路接入。
        # #269：图层面板浏览器 runner 同一套路接入（门只验接线，不验产品行为）。
        for runner_name in (
            "canvas_boundaries_browser.py",
            "video_failure_browser.py",
            "node_flow_browser.py",
            "fly_fixes_browser.py",
            "layers_panel_browser.py",
            # #295：tabbit 端到端入口同一套路（门只验接线；环境未就绪时真实入口
            # 也会 exit 0 快速跳过，桩与真实行为一致）。
            "tabbit_e2e_evidence.py",
            # #346：`hidden` 语义页面级证据 runner 同一套路（门只验接线不验产品行为）。
            "canvas_hidden_visibility_browser.py",
            # #377：项目被删后不再反复 PATCH 的页面级证据 runner，同一套路。
            "canvas_project_gone_browser.py",
            # #382：/ws/stats 断线后对已删项目停止重连的页面级证据 runner，同一套路。
            "ws_stats_reconnect_browser.py",
        ):
            (self.fixture_root / "tests" / runner_name).write_text(
                "import sys\n"
                f"print('{runner_name} fixture ok')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
        (self.fixture_root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import unittest\n\n"
            "class FixtureContract(unittest.TestCase):\n"
            "    def test_quality_gate_environment_is_isolated(self):\n"
            "        import os\n"
            "        from pathlib import Path\n"
            "        self.assertEqual(os.environ.get('FIREFLY_TEST_MODE'), '1')\n"
            "        self.assertTrue(Path(os.environ['FIREFLY_RUNTIME_ROOT']).is_dir())\n"
            "        self.assertTrue(Path(os.environ['FIREFLY_PROJECTS_ROOT']).is_dir())\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_history_deletion_isolation.py").write_text(
            "import unittest\n"
            "class HistoryDeletionIsolation(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        for module_name in ("test_excel_importer.py", "test_excel_export.py"):
            (self.fixture_root / "tests" / module_name).write_text(
                "import unittest\n\n"
                "class FixtureExcelContract(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        (self.fixture_root / "tests" / "excel_import_browser.py").write_text(
            "import sys\n"
            "print('excel-import-evidence fixture ok')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        for module_name in (
            "test_project_history.py",
            "test_project_video_generation.py",
            "test_project_image_generation.py",
            "test_issue40_count8_concurrency.py",
            "test_issue41_task_state_machine.py",
            "test_diagnostics.py",
        ):
            (self.fixture_root / "tests" / module_name).write_text(
                "import unittest\n\n"
                "class FixtureContract(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        (self.fixture_root / "tests" / "test_provider_contract_template.py").write_text(
            "import unittest\n\n"
            "class ProviderContract(unittest.TestCase):\n"
            "    def test_contract(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        for module_name in ("test_provider_contract_registry.py", "test_provider_contract_families.py"):
            (self.fixture_root / "tests" / module_name).write_text(
                "import unittest\n\n"
                "class ProviderContract(unittest.TestCase):\n"
                "    def test_contract(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        # #369：类型检查步骤——fixture 没有类型检查配置（无 package.json/jsconfig），
        # 真实步骤模块在这种情况下跑一个接线测试 exit 0；桩同一行为。
        # 门只验接线；红绿有效性由
        # test_javascript_types_step_installs_offline_and_rejects_bad_js 用完整配置仓库覆盖。
        (self.fixture_root / "tests" / "test_javascript_types.py").write_text(
            "import unittest\n\n"
            "class JavaScriptTypeCheckFixture(unittest.TestCase):\n"
            "    def test_noop_wiring(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        write_generation_lifecycle_runner(self.fixture_root)
        write_complete_generation_lifecycle_runner(self.fixture_root)
        write_composer_flow_runner(self.fixture_root)
        write_long_content_runner(self.fixture_root)
        write_project_linked_generation_runner(self.fixture_root)
        write_ui_integrity_runner(self.fixture_root)
        write_edge_compatibility_runner(self.fixture_root)
        self.ui_integrity_baselines = Path(self.temp_dir.name) / "ui-integrity-baselines"
        if self.uses_git:
            self.init_git()

    uses_git = True

    def init_git(self):
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.fixture_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def prepare_ui_integrity_baseline(self):
        if (self.ui_integrity_baselines / "manifest.json").is_file():
            return
        candidate = Path(self.temp_dir.name) / "ui-integrity-candidate"
        write_valid_ui_integrity_artifact(candidate)
        accept_baseline(
            candidate,
            self.ui_integrity_baselines,
            accepted_by="quality-gate-test",
            reason="deterministic quality gate fixture",
        )

    def run_gate(self, mode="fast", evidence_dir=None, **environment):
        env = os.environ.copy()
        env.update(environment)
        if mode in {"trial", "release"}:
            self.prepare_ui_integrity_baseline()
            env.setdefault("FIREFLY_UI_INTEGRITY_BASELINES", str(self.ui_integrity_baselines))
        return subprocess.run(
            [
                sys.executable,
                str(QUALITY_GATE),
                mode,
                "--repo-root",
                str(self.fixture_root),
                "--evidence-dir",
                str(evidence_dir or self.evidence_dir),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )

    def report(self):
        return json.loads((self.evidence_dir / "report.json").read_text(encoding="utf-8"))

    def write_native_browser_fixture(self, *, complete=True, variant=None):
        left_click_evidence = repr(selector_owner_click_evidence())
        right_click_evidence = repr(selector_owner_click_evidence(button="right"))
        double_click_evidence_value = repr(double_click_evidence())
        script = (
            "import argparse, hashlib, json, zipfile\n"
            "from pathlib import Path\n"
            "from PIL import Image, ImageDraw\n"
            f"CLICK_EVIDENCE_LEFT = {left_click_evidence}\n"
            f"CLICK_EVIDENCE_RIGHT = {right_click_evidence}\n"
            f"DOUBLE_CLICK_EVIDENCE = {double_click_evidence_value}\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--artifacts-dir', type=Path, required=True)\n"
            "root = parser.parse_args().artifacts_dir\n"
            "root.mkdir(parents=True)\n"
        )
        if complete:
            script += (
                "viewports = ((1920,1080),(1440,900),(1280,720),(1024,768),(800,600))\n"
                "attempts = []\n"
                "for width, height in viewports:\n"
                "    folder = root / f'viewport-{width}x{height}'\n"
                "    folder.mkdir()\n"
                "    operations = [\n"
                "        {'sequence':1,'action':'click','selector':'#showCreate','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button#showCreate','observed_target':'button#showCreate','observed_type':'mouseup','focus_after':'button#showCreate','trusted':True,'unobscured':True},\n"
                "        {'sequence':2,'action':'click','selector':'#projectName','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'input#projectName','observed_target':'input#projectName','observed_type':'mouseup','focus_after':'input#projectName','trusted':True,'unobscured':True},\n"
                "        {'sequence':3,'action':'keyboard','text_length':7,'observed_key':'e','observed_target':'input#projectName','observed_type':'keyup','focus_after':'input#projectName','trusted':True},\n"
                "        {'sequence':4,'action':'click','selector':'#createForm button[type=\"submit\"]','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.button.primary','observed_target':'button.button.primary','observed_type':'mouseup','focus_after':'button.button.primary','trusted':True,'unobscured':True},\n"
                "        {'sequence':5,'action':'click','selector':'#shell','button':'right','observed_button':2,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'div#shell','observed_target':'div#shell','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                "        {'sequence':6,'action':'click','selector':'#createMenu [data-create-type=\"prompt\"]','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.create-card','observed_target':'button.create-card','observed_type':'mouseup','focus_after':'button.create-card','trusted':True,'unobscured':True},\n"
                "        {'sequence':7,'action':'click','selector':'.prompt-node-text','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'textarea.prompt-node-text','observed_target':'textarea.prompt-node-text','observed_type':'mouseup','focus_after':'textarea.prompt-node-text','trusted':True,'unobscured':True},\n"
                "        {'sequence':8,'action':'keyboard','text_length':7,'observed_key':'e','observed_target':'textarea.prompt-node-text','observed_type':'keyup','focus_after':'textarea.prompt-node-text','trusted':True},\n"
                "        {'sequence':9,'action':'drag','selector':'.image-node','observed_button':0,'observed_within_selector':True,'start':{'x':10,'y':10},'end':{'x':20,'y':20},'observed_point':{'x':20,'y':20},'hit':'div.image-node','observed_target':'div.image-node','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                "        {'sequence':10,'action':'click','selector':'#smartSaveButton','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button#smartSaveButton','observed_target':'button#smartSaveButton','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                "        {'sequence':11,'action':'click','selector':'.smart-back','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.smart-back','observed_target':'button.smart-back','observed_type':'mouseup','focus_after':'button.smart-back','trusted':True,'unobscured':True},\n"
                "        {'sequence':12,'action':'click','selector':'#projectGrid .project-card:nth-child(1) .project-open','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'h2','observed_target':'h2','observed_type':'mouseup','focus_after':'button.project-open','trusted':True,'unobscured':True},\n"
                "        {'sequence':13,'action':'drag','selector':'.image-node[data-id=generated-node]','observed_button':0,'observed_within_selector':True,'start':{'x':10,'y':10},'end':{'x':20,'y':20},'observed_point':{'x':20,'y':20},'hit':'div.image-node','observed_target':'div.image-node','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                "        {'sequence':14,'action':'click','selector':'#smartSaveButton','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button#smartSaveButton','observed_target':'button#smartSaveButton','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                "        {'sequence':15,'action':'click','selector':'.smart-back','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.smart-back','observed_target':'button.smart-back','observed_type':'mouseup','focus_after':'button.smart-back','trusted':True,'unobscured':True},\n"
                "        {'sequence':16,'action':'click','selector':'#projectGrid .project-card:nth-child(1) .project-duplicate','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.project-action.project-duplicate','observed_target':'button.project-action.project-duplicate','observed_type':'mouseup','focus_after':'button.project-action.project-duplicate','trusted':True,'unobscured':True},\n"
                "        {'sequence':17,'action':'click','selector':'#projectGrid .project-card:nth-child(1) .project-open','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'h2','observed_target':'h2','observed_type':'mouseup','focus_after':'button.project-open','trusted':True,'unobscured':True},\n"
                 "    ]\n"
                 "    for item in operations:\n"
                 "        if item.get('action') == 'click': item.update(dict(CLICK_EVIDENCE_RIGHT if item.get('button') == 'right' else CLICK_EVIDENCE_LEFT))\n"
                 "    (folder/'operations.jsonl').write_text(''.join(json.dumps(item)+'\\n' for item in operations), encoding='utf-8')\n"
                 "    organization_payloads = {'landscape.png':b'landscape-source','portrait.png':b'portrait-source','square.png':b'square-source'}\n"
                 "    organization_files = [{'name':name,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()} for name,payload in organization_payloads.items()]\n"
                 "    organization_operations = [\n"
                 "        {'sequence':1,'action':'click','selector':'#canvasEmptyImport','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button#canvasEmptyImport','observed_target':'button#canvasEmptyImport','observed_type':'mouseup','focus_after':'button#canvasEmptyImport','trusted':True,'unobscured':True},\n"
                 "        {'sequence':2,'action':'file-upload','selector':'body > input[type=\"file\"]:not(#fileInput)','files':organization_files[:2],'observed_target':'input','focus_after':'input','observed_type':'change','observed_modifiers':[],'trusted':True},\n"
                 "        {'sequence':3,'action':'drag','selector':'.image-node[data-id=multi-image]','release_selector':'.image-node[data-id=multi-image]','modifiers':[],'observed_modifiers':[],'observed_button':0,'observed_within_selector':True,'start':{'x':20,'y':20},'end':{'x':80,'y':60},'observed_point':{'x':80,'y':60},'hit':'div.image-node','observed_target':'div.image-node','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':4,'action':'click','selector':'#shell','button':'right','observed_button':2,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'div#shell','observed_target':'div#shell','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':5,'action':'click','selector':'#createMenu [data-create-type=\"image\"]','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.create-card','observed_target':'button.create-card','observed_type':'mouseup','focus_after':'button.create-card','trusted':True,'unobscured':True},\n"
                 "        {'sequence':6,'action':'double_click','selector':'.image-node[data-id=square-image] .node-drop','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'div.node-drop','observed_target':'div.node-drop','observed_type':'dblclick','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':7,'action':'file-upload','selector':'body > input[type=\"file\"]:not(#fileInput)','files':organization_files[2:],'observed_target':'input','focus_after':'input','observed_type':'change','observed_modifiers':[],'trusted':True},\n"
                 "        {'sequence':8,'action':'drag','selector':'.image-node[data-id=square-image]','release_selector':'.image-node[data-id=square-image]','modifiers':[],'observed_modifiers':[],'observed_button':0,'observed_within_selector':True,'start':{'x':20,'y':20},'end':{'x':180,'y':100},'observed_point':{'x':180,'y':100},'hit':'div.image-node','observed_target':'div.image-node','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':9,'action':'drag','selector':'#shell','release_selector':'#shell','modifiers':['Control'],'observed_modifiers':['Control'],'observed_button':0,'observed_within_selector':True,'start':{'x':10,'y':10},'end':{'x':400,'y':300},'observed_point':{'x':400,'y':300},'hit':'div#shell','observed_target':'div#shell','observed_type':'mouseup','focus_after':'div#shell','trusted':True,'unobscured':True},\n"
                 "        {'sequence':10,'action':'keyboard','key':'g','modifiers':['Control'],'observed_modifiers':['Control'],'observed_key':'g','observed_target':'div#shell','observed_type':'keyup','focus_after':'div#shell','trusted':True},\n"
                 "        {'sequence':11,'action':'click','selector':'.image-node.smart-group-node[data-id=group-image]','observed_button':0,'point':{'x':300,'y':160},'observed_point':{'x':300,'y':160},'hit':'div.smart-group-node','observed_target':'div.smart-group-node','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':12,'action':'drag','selector':'.image-node.smart-group-node[data-id=group-image]','release_selector':'.image-node.smart-group-node[data-id=group-image]','modifiers':[],'observed_modifiers':[],'observed_button':0,'observed_within_selector':True,'start':{'x':300,'y':160},'end':{'x':300,'y':60},'observed_point':{'x':300,'y':60},'hit':'div.smart-group-node','observed_target':'div.smart-group-node','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':13,'action':'drag','selector':'.image-node.smart-group-node[data-id=group-image] .node-resize-handle','release_selector':'#shell','modifiers':[],'observed_modifiers':[],'observed_button':0,'observed_within_selector':True,'start':{'x':300,'y':200},'end':{'x':380,'y':260},'observed_point':{'x':380,'y':260},'hit':'div.node-resize-handle','observed_target':'div#shell','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':14,'action':'keyboard','key':'g','modifiers':['Control','Shift'],'observed_modifiers':['Control','Shift'],'observed_key':'g','observed_target':'body','observed_type':'keyup','focus_after':'body','trusted':True},\n"
                 "        {'sequence':15,'action':'keyboard','key':'Escape','modifiers':[],'observed_modifiers':[],'observed_key':'Escape','observed_target':'body','observed_type':'keyup','focus_after':'body','trusted':True},\n"
                 "        {'sequence':16,'action':'click','selector':'#smartSaveButton','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button#smartSaveButton','observed_target':'button#smartSaveButton','observed_type':'mouseup','focus_after':'body','trusted':True,'unobscured':True},\n"
                 "        {'sequence':17,'action':'click','selector':'.smart-back','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.smart-back','observed_target':'button.smart-back','observed_type':'mouseup','focus_after':'button.smart-back','trusted':True,'unobscured':True},\n"
                 "        {'sequence':18,'action':'click','selector':'.project-open','observed_button':0,'point':{'x':10,'y':10},'observed_point':{'x':10,'y':10},'hit':'button.project-open','observed_target':'button.project-open','observed_type':'mouseup','focus_after':'button.project-open','trusted':True,'unobscured':True},\n"
                 "    ]\n"
                 "    for item in organization_operations:\n"
                 "        if item.get('action') == 'click': item.update(dict(CLICK_EVIDENCE_RIGHT if item.get('button') == 'right' else CLICK_EVIDENCE_LEFT))\n"
                 "        elif item.get('action') == 'double_click': item.update(dict(DOUBLE_CLICK_EVIDENCE))\n"
                 "    (folder/'organization-operations.jsonl').write_text(''.join(json.dumps(item)+'\\n' for item in organization_operations), encoding='utf-8')\n"
                 "    (folder/'console-errors.json').write_text('[]\\n', encoding='utf-8')\n"
                 "    (folder/'requests.json').write_text('[]\\n', encoding='utf-8')\n"
                "    image = Image.new('RGB', (width, height), (15, 23, 42))\n"
                "    ImageDraw.Draw(image).rectangle((10, 10, 100, 100), fill=(37, 99, 235))\n"
                "    image.save(folder/'01-loaded.png', format='PNG', compress_level=1)\n"
                "    screenshot = (folder/'01-loaded.png').read_bytes()\n"
                "    for name in ('02-restored.png','03-organized.png','04-ungrouped.png'):\n"
                "        (folder/name).write_bytes(screenshot)\n"
                "    prefix = f'viewport-{width}x{height}'\n"
                "    asset_payloads = {'asset-generated':b'generated-fixture','asset-referenced':b'referenced-fixture','asset-unreferenced':b'unreferenced-fixture'}\n"
                "    media_items = {asset_id:{'asset_id':asset_id,'filename':asset_id+'.png','sha256':hashlib.sha256(payload).hexdigest(),'size':len(payload)} for asset_id,payload in asset_payloads.items()}\n"
                "    canvas = {'id':'project-fixture','project_id':'project-fixture','revision':1,'nodes':[{'id':'node-fixture'},{'id':'asset-node'},{'id':'generated-node'}],'connections':[{'from':'node-fixture','to':'generated-node'}]}\n"
                "    history = {'records':[{'id':'history-fixture','output_asset_id':'asset-generated'}]}\n"
                "    package_files = {'project.json':json.dumps({'id':'project-fixture'}).encode(),'canvas.json':json.dumps(canvas).encode(),'history.json':json.dumps(history).encode(),'media/index.json':json.dumps({'items':media_items}).encode(),**{'media/'+asset_id+'.png':payload for asset_id,payload in asset_payloads.items()}}\n"
                "    package_manifest = {'format':'firefly-package-v1','project_id':'project-fixture','files':[{'path':path,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()} for path,payload in sorted(package_files.items())]}\n"
                "    package_path = folder/'project-roundtrip.fly'\n"
                "    with zipfile.ZipFile(package_path,'w',zipfile.ZIP_DEFLATED) as archive:\n"
                "        archive.writestr('manifest.json',json.dumps(package_manifest))\n"
                "        for path,payload in package_files.items(): archive.writestr(path,payload)\n"
                 "    project_roundtrip = {'status':'passed','project_id':'project-fixture','canvas_id':'project-fixture','reopened_project_id':'project-fixture','node_id':'node-fixture','node_count':3,'position_before':{'x':120,'y':140},'position_after':{'x':220,'y':240},'revision_after_save':2,'revision_after_reopen':2,'save_status':'saved','browser_augmented_save':True,'generated_position_before':{'x':360,'y':180},'generated_position_after':{'x':391,'y':199},'connection_count':1,'media_asset_ids':['asset-generated','asset-referenced','asset-unreferenced'],'referenced_asset_id':'asset-referenced','generated_asset_id':'asset-generated','unreferenced_asset_id':'asset-unreferenced','history_output_asset_id':'asset-generated','duplicate_via_native_ui':True,'duplicate_project_id':'duplicate-fixture','duplicate_canvas_id':'duplicate-fixture','duplicate_node_count':3,'duplicate_connection_count':1,'duplicate_media_asset_ids':['asset-generated','asset-referenced','asset-unreferenced'],'duplicate_provenance_preserved':True,'duplicate_paths_independent':True,'export_project_id':'project-fixture','export_media_asset_ids':['asset-generated','asset-referenced','asset-unreferenced'],'export_package':prefix+'/project-roundtrip.fly','export_manifest_verified':True,'export_media_bytes_preserved':True,'export_history_preserved':True,'trashed_project_id':'duplicate-fixture','restored_project_id':'duplicate-fixture','source_project_preserved':True,'restored_duplicate_preserved':True,'reopened_canvas_preserved':True,'reopened_history_preserved':True,'reopened_media_preserved':True}\n"
                 "    organization_assets = [{'asset_id':'asset-landscape','name':'landscape.png','size':len(organization_payloads['landscape.png']),'sha256':hashlib.sha256(organization_payloads['landscape.png']).hexdigest(),'source_width':600,'source_height':400},{'asset_id':'asset-portrait','name':'portrait.png','size':len(organization_payloads['portrait.png']),'sha256':hashlib.sha256(organization_payloads['portrait.png']).hexdigest(),'source_width':400,'source_height':600},{'asset_id':'asset-square','name':'square.png','size':len(organization_payloads['square.png']),'sha256':hashlib.sha256(organization_payloads['square.png']).hexdigest(),'source_width':512,'source_height':512}]\n"
                 "    organization_hashes = {item['asset_id']:item['sha256'] for item in organization_assets}\n"
                 "    image_organization = {'status':'passed','project_id':'organization-fixture','reopened_project_id':'organization-fixture','operations':prefix+'/organization-operations.jsonl','assets':organization_assets,'media_before':dict(organization_hashes),'media_after':dict(organization_hashes),'reopened_media_hashes':dict(organization_hashes),'ordinary_ratios':[{'asset_id':'asset-landscape','display_ratio':1.5},{'asset_id':'asset-portrait','display_ratio':0.6666667}],'group_id':'group-fixture','group_ratios':[{'asset_id':'asset-landscape','display_ratio':1.5},{'asset_id':'asset-portrait','display_ratio':0.6666667},{'asset_id':'asset-square','display_ratio':1.0}],'ungroup_ratios':[{'asset_id':'asset-landscape','display_ratio':1.5},{'asset_id':'asset-portrait','display_ratio':0.6666667},{'asset_id':'asset-square','display_ratio':1.0}],'group_size_before':{'width':500,'height':320},'group_size_after':{'width':620,'height':410},'ungrouped':[{'asset_id':'asset-landscape','display_ratio':1.5,'rect':{'x':100,'y':120,'width':300,'height':200},'clipped':False},{'asset_id':'asset-portrait','display_ratio':0.6666667,'rect':{'x':424,'y':120,'width':160,'height':240},'clipped':False},{'asset_id':'asset-square','display_ratio':1.0,'rect':{'x':608,'y':120,'width':220,'height':220},'clipped':False}],'controls_reachable':True,'page_horizontal_overflow':False,'reopened_preserved':True,'organized_screenshot':prefix+'/03-organized.png','ungrouped_screenshot':prefix+'/04-ungrouped.png'}\n"
                 "    attempts.append({'viewport':[width,height],'observed':{'width':width,'height':height,'dpr':1,'color_scheme':'light','theme':'light','fonts':{'Inter':True,'JetBrains Mono':True,'Space Grotesk':True}},'status':'passed','operations':prefix+'/operations.jsonl','console_errors':prefix+'/console-errors.json','unexpected_console_errors':0,'requests':prefix+'/requests.json','screenshots':[prefix+'/01-loaded.png',prefix+'/02-restored.png',prefix+'/03-organized.png',prefix+'/04-ungrouped.png'],'project_roundtrip':project_roundtrip,'image_organization':image_organization})\n"
                "manifest = {'schema_version':1,'journey':'core-canvas','status':'passed','environment':{'os':'Windows','browser':{'name':'Google Chrome','channel':'stable','version':'Chrome/fixture'},'dpr':1,'color_scheme':'light','fonts':['Inter','JetBrains Mono','Space Grotesk']},'attempts':attempts,'first_failure':None}\n"
            )
        else:
            script += "manifest = {'schema_version':1,'journey':'core-canvas','status':'passed'}\n"
        variants = {
            None: "",
            "untrusted": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[0]['trusted'] = False\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "outside-root": "manifest['attempts'][0]['screenshots'][0] = '../escaped.png'\n",
            "bad-theme": "manifest['attempts'][0]['observed']['theme'] = 'dark'\n",
            "missing-hit": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[0].pop('hit')\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "wrong-focus": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[0]['focus_after'] = 'body'\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "wrong-context-button": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[4]['observed_button'] = 0\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "missing-semantic-click": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[0]['observed_type'] = 'mouseup'\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "drag-outside-selector": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[12]['observed_within_selector'] = False\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "drag-child-to-parent": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[12]['hit'] = 'img.node-img'\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "truncated-png": "(root/'viewport-1920x1080'/'01-loaded.png').write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture')\n",
            "blank-png": "Image.new('RGB', (1920, 1080), (15, 23, 42)).save(root/'viewport-1920x1080'/'01-loaded.png', format='PNG')\n",
            "missing-roundtrip": "manifest['attempts'][0].pop('project_roundtrip')\n",
            "incomplete-project-scope": "manifest['attempts'][0]['project_roundtrip'].pop('duplicate_project_id')\n",
            "missing-native-duplicate": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [item for item in (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()) if '.project-duplicate' not in str(item.get('selector') or '')]\n"
                "for sequence,item in enumerate(items,start=1): item['sequence'] = sequence\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "missing-augmented-save": (
                "path = root/'viewport-1920x1080'/'operations.jsonl'\n"
                "items = [item for item in (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()) if item.get('sequence') != 14]\n"
                "for sequence,item in enumerate(items,start=1): item['sequence'] = sequence\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "tampered-export-media": (
                "path = root/'viewport-1920x1080'/'project-roundtrip.fly'\n"
                "replacement = path.with_suffix('.tampered')\n"
                "with zipfile.ZipFile(path,'r') as source, zipfile.ZipFile(replacement,'w',zipfile.ZIP_DEFLATED) as target:\n"
                "    for name in source.namelist(): target.writestr(name,b'tampered' if name == 'media/asset-generated.png' else source.read(name))\n"
                "replacement.replace(path)\n"
            ),
            "missing-image-organization": "manifest['attempts'][0].pop('image_organization')\n",
            "tampered-image-media": "manifest['attempts'][0]['image_organization']['media_after']['asset-landscape'] = '0' * 64\n",
            "missing-native-group": (
                "path = root/'viewport-1920x1080'/'organization-operations.jsonl'\n"
                "items = [item for item in (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()) if not (item.get('action') == 'keyboard' and item.get('key') == 'g' and item.get('modifiers') == ['Control'])]\n"
                "for sequence,item in enumerate(items,start=1): item['sequence'] = sequence\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "wrong-upload-focus": (
                "path = root/'viewport-1920x1080'/'organization-operations.jsonl'\n"
                "items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]\n"
                "items[1]['focus_after'] = 'body'\n"
                "path.write_text(''.join(json.dumps(item)+'\\n' for item in items), encoding='utf-8')\n"
            ),
            "missing-group-ratio": "manifest['attempts'][0]['image_organization']['group_ratios'].pop()\n",
            "duplicate-ungroup-ratio": "manifest['attempts'][0]['image_organization']['ungroup_ratios'][2] = dict(manifest['attempts'][0]['image_organization']['ungroup_ratios'][0])\n",
            "tampered-reopened-hash": "manifest['attempts'][0]['image_organization']['reopened_media_hashes']['asset-landscape'] = '0' * 64\n",
        }
        if variant not in variants:
            raise ValueError(f"unknown native browser fixture variant: {variant}")
        script += variants[variant]
        script += "(root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')\n"
        (self.fixture_root / "tests" / "core_surface_browser.py").write_text(script, encoding="utf-8")


@dataclass
class StepResponse:
    returncode: int = 0
    stdout: str | bytes = ""
    stderr: str | bytes = ""
    timed_out: bool = False


class FixtureProcess:
    def __init__(self, command, response):
        self.command = command
        self.returncode = response.returncode
        self.response = response
        self.stopped = False

    def communicate(self, timeout):
        if self.response.timed_out and not self.stopped:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.response.stdout, self.response.stderr

    def stop(self):
        self.stopped = True


class InProcessGateFixture(GateFixture):
    """Run the real scheduler and report writer without launching test steps."""

    uses_git = False

    @staticmethod
    def copy_fixture_workspace(repo_root, workspace_root):
        shutil.copytree(repo_root, workspace_root)
        return sum(path.is_file() for path in workspace_root.rglob("*"))

    # These are test-only artifact generators written by GateFixture, not the
    # scripts from the execution workspace. Never execute a command from it.
    browser_fixtures = frozenset({
        "core_surface_browser.py",
        "project_linked_generation_native_browser.py",
        "generation_lifecycle_browser.py",
        "generation_task_native_browser.py",
        "composer_flow_native_browser.py",
        "long_content_native_browser.py",
        "ui_integrity_native_browser.py",
        "edge_compatibility_browser.py",
    })

    browser_fixture_lock = threading.Lock()

    def write_browser_artifacts(self, command):
        if len(command) < 4 or command[-2] != "--artifacts-dir":
            return
        fixture = self.fixture_root / "tests" / Path(command[1]).name
        if fixture.name not in self.browser_fixtures:
            raise AssertionError(f"No artifact fixture for {command!r}")
        if not fixture.is_file() and fixture.name == "core_surface_browser.py":
            self.write_native_browser_fixture()
        if fixture.name == "ui_integrity_native_browser.py":
            write_valid_ui_integrity_artifact(Path(command[-1]))
            return
        # Fake parallel browser steps share argv; only one fixture can use it at a time.
        with self.browser_fixture_lock, mock.patch.object(sys, "argv", [str(fixture), *command[2:]]):
            runpy.run_path(str(fixture), run_name="__main__")

    def run_gate(self, mode="fast", evidence_dir=None, *, spawn=None, verify_git=False,
                 proxy_factory=None, **environment):
        if mode in {"trial", "release"}:
            self.prepare_ui_integrity_baseline()
            environment.setdefault("FIREFLY_UI_INTEGRITY_BASELINES", str(self.ui_integrity_baselines))

        def spawn_step(command, repo_root, step_environment):
            response = spawn(command, repo_root, step_environment) if spawn else None
            if response is None:
                self.write_browser_artifacts(command)
                response = StepResponse()
            return FixtureProcess(command, response)

        argv = [mode, "--repo-root", str(self.fixture_root), "--evidence-dir", str(evidence_dir or self.evidence_dir)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            if verify_git:
                self.init_git()
            else:
                stack.enter_context(mock.patch.object(quality_gate, "git_state", return_value={
                    "available": True, "dirty": False, "entries": [], "fingerprints": {},
                }))
                stack.enter_context(mock.patch.object(
                    quality_gate, "create_execution_workspace", self.copy_fixture_workspace,
                ))
            stack.enter_context(mock.patch.dict(os.environ, environment))
            stack.enter_context(mock.patch.object(quality_gate, "_spawn_step", spawn_step))
            stack.enter_context(mock.patch.object(quality_gate, "stop_process_tree", FixtureProcess.stop))
            stack.enter_context(mock.patch.object(quality_gate, "gate_leftover_processes", return_value=[]))
            if proxy_factory is None:
                proxy_factory = lambda _: nullcontext(SimpleNamespace(url="http://127.0.0.1:41080"))
            stack.enter_context(mock.patch.object(quality_gate, "BrowserDenyProxy", side_effect=proxy_factory))
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            returncode = quality_gate.main(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout.getvalue(), stderr.getvalue())


class GateWiringTests(InProcessGateFixture):
    def test_trial_covers_every_discoverable_unit_contract_module_once(self):
        modules = discover_unit_contract_modules(ROOT)
        shards = compute_unit_contract_shards(ROOT)
        covered = list(unit_contract_modules_for_shards(shards))
        targets = [target for shard_targets in shards.values() for target in shard_targets]

        # 每个被发现的模块恰好落入一片、绝不跨片重复：新模块拿到默认
        # 估计自动进装箱，不靠人手维护列表。
        self.assertEqual(sorted(covered), list(modules))
        self.assertEqual(len(targets), len(set(targets)))
        gate_targets = [
            target
            for name, shard_targets in shards.items()
            if unit_contract_slot_for_shard(name) == "unit-contract-quality-gate"
            for target in shard_targets
        ]
        self.assertTrue(gate_targets)
        self.assertIn(
            "tests.test_generation_log_reuse_ui",
            shards["unit-contract-browser"],
        )
        # #204：三个 unit-contract 槽位不再直接进 STEP_DEFINITIONS——运行时由
        # expand_unit_contract_steps 展开成具体分片（resolve_step_command 负责命令）。
        # 这里钉死的是它们在 MODE_STEPS 里的存在与模式约束。
        for name in UNIT_CONTRACT_STEP_NAMES:
            self.assertNotIn(name, STEP_DEFINITIONS)
            self.assertNotIn(name, MODE_STEPS["fast"])
            self.assertNotIn(name, MODE_STEPS["domain"])
            self.assertIn(name, MODE_STEPS["trial"])
            self.assertIn(name, MODE_STEPS["release"])
        for duplicated_contract_step in (
            "surface-contract",
            "project-contract",
            "provider-contract",
        ):
            self.assertNotIn(duplicated_contract_step, MODE_STEPS["trial"])
            self.assertNotIn(duplicated_contract_step, MODE_STEPS["release"])

    def test_egress_attribution_probe_runs_in_every_mode(self):
        # #232：真实门跑里 by_method 全是 domain-fallback——#230 的进程树归属
        # 那一半从没在真实环境生效过（产品出口走浏览器/node，没钩子）。
        # 这一步跑产品自己的取远端素材函数（装在带钩子的进程里），让每次
        # 真门跑都必然留下一条带 pid 的产品外连证据。四个模式都要：
        # 只靠 fixture 证明机制存在不算，换机器跑也要能对上账。
        step = "egress-attribution"
        self.assertIn(step, STEP_DEFINITIONS)
        joined = " ".join(STEP_DEFINITIONS[step](sys.executable))
        self.assertIn("tests.test_product_egress_attribution", joined)
        for mode in ("fast", "domain", "trial", "release"):
            self.assertIn(step, MODE_STEPS[mode])

    def test_javascript_syntax_step_covers_every_canvas_module(self):
        # #167：javascript-syntax 以前只 `node --check static/js/smart-canvas.js`，
        # 拆出去的 canvas-*.js 语法错误在 fast/domain 完全漏网。
        command = STEP_DEFINITIONS["javascript-syntax"](sys.executable)
        joined = " ".join(command)
        self.assertIn("tests.test_canvas_frontend_sources", joined)
        self.assertNotIn("static/js/smart-canvas.js", joined)
        for mode in ("fast", "domain", "trial", "release"):
            self.assertIn("javascript-syntax", MODE_STEPS[mode])

    def test_javascript_types_step_registered_after_syntax_in_every_mode(self):
        # #369：类型检查步骤必须紧跟语法步——语法红时类型检查没有意义，
        # 语法绿才轮到它；四个模式都要：fast 是提交时门，类型错误要在这里免费拦住。
        step = "javascript-types"
        self.assertIn(step, STEP_DEFINITIONS)
        joined = " ".join(STEP_DEFINITIONS[step](sys.executable))
        self.assertIn("tests.test_javascript_types", joined)
        for mode in ("fast", "domain", "trial", "release"):
            steps = MODE_STEPS[mode]
            self.assertIn(step, steps)
            self.assertEqual(steps.index(step), steps.index("javascript-syntax") + 1)

    def test_canvas_boundaries_evidence_runs_only_in_trial_and_release(self):
        # #167：runner 长期游离在门禁之外，只能手动跑。
        step = "canvas-boundaries-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertEqual(
            STEP_DEFINITIONS[step](sys.executable),
            [sys.executable, "tests/canvas_boundaries_browser.py"],
        )
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])
        self.assertIn(step, BROWSER_EVIDENCE_STEP_NAMES)

    def test_video_failure_evidence_runs_only_in_trial_and_release(self):
        step = "video-failure-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertEqual(
            STEP_DEFINITIONS[step](sys.executable),
            [sys.executable, "tests/video_failure_browser.py"],
        )
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])
        self.assertIn(step, BROWSER_EVIDENCE_STEP_NAMES)

    def test_tabbit_e2e_evidence_runs_only_in_trial_and_release(self):
        # #295：tabbit 端到端套件接入交付门禁（#296 已落地失败自动重跑与截图归档，
        # 入口脚本只做编排）。按 #167/#185 先例接进 trial/release，不进并行浏览器
        # 组（它经 tabbit 自己的浏览器跑，不走门的 Chrome runner，预算另计）。
        step = "tabbit-e2e-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertEqual(
            STEP_DEFINITIONS[step](sys.executable),
            [sys.executable, "tests/tabbit_e2e_evidence.py"],
        )
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])
        self.assertNotIn(step, BROWSER_EVIDENCE_STEP_NAMES)

    def test_tabbit_e2e_suite_wires_mouse_scenario(self):
        # #310：#295 只接了三个套件，scenario_mouse.sh（#299 的鼠标维，m1–m6）
        # 不在 SCENARIO_SCRIPTS 里，任何档位都不会被跑到。这里锁套件清单：
        # #360：套件清单改为「tools/e2e/ 下所有 scenario_*.sh 自动纳入」（见
        # tests/tabbit_e2e_evidence.py 的 _scenario_scripts）；这里钉住当时那四套的
        # 硬编码清单已过期，改成断言：四套仍在 + 任何 scenario_*.sh 不在清单就是 bug。
        # 门内该步恒为环境跳过（合成临时 home 看不见 tabbit-cli，#310 承认的
        # 空步），真实留证走门外执行入口脚本，路径取舍见 #310 评论。
        from tests import tabbit_e2e_evidence
        root = Path(__file__).resolve().parents[1]
        on_disk = {f"tools/e2e/{p.name}" for p in (root / "tools" / "e2e").glob("scenario_*.sh")}
        self.assertEqual(set(tabbit_e2e_evidence.SCENARIO_SCRIPTS), on_disk,
                         "tools/e2e/ 下的 scenario_*.sh 必须全部接进门，新增套件不需要改入口")
        # #360：五个写型入口都要在门里跑；e2e_qa.sh 不叫 scenario_*.sh，单独钉。
        self.assertIn(tabbit_e2e_evidence.QA_SCRIPT, tabbit_e2e_evidence.ENTRY_SCRIPTS,
                      "e2e_qa.sh 也是写型入口（#360），不能从门里溜掉")
        self.assertEqual(set(tabbit_e2e_evidence.ENTRY_SCRIPTS),
                         set(tabbit_e2e_evidence.SCENARIO_TIMEOUTS),
                         "每个入口都要有自己那份超时预算（#360：预算按套件拆）")
        self.assertEqual(
            on_disk & {
                "tools/e2e/scenario_keys.sh",
                "tools/e2e/scenario_delete_undo.sh",
                "tools/e2e/scenario_ux_rules.sh",
                "tools/e2e/scenario_mouse.sh",
            },
            {
                "tools/e2e/scenario_keys.sh",
                "tools/e2e/scenario_delete_undo.sh",
                "tools/e2e/scenario_ux_rules.sh",
                "tools/e2e/scenario_mouse.sh",
            },
            "#310 那四套不能从清单里掉出去",
        )
        for script in tabbit_e2e_evidence.SCENARIO_SCRIPTS:
            self.assertTrue((root / script).is_file(), f"套件脚本缺失: {script}")

    def test_all_browser_runners_resolve_their_repo_root(self):
        # 门在临时 workspace 里执行步骤（create_execution_workspace），runner 里硬编码的
        # 仓库根会读到工作区外的源码：证据看起来绿，测的却是另一份代码。
        # 这个坑在 #172 里实测到：在 git worktree 里跑 canvas_interactions_browser.py
        # 仍然加载主仓 static/，导致「修复前」对照无效。
        offenders = []
        for runner in sorted((Path(__file__).resolve().parents[1] / "tests").glob("*browser*.py")):
            text = runner.read_text(encoding="utf-8")
            if "Firefly-Canvas" in text:
                offenders.append(runner.name)
        self.assertEqual([], offenders)

    def test_wired_browser_runners_accept_zero_arguments(self):
        # #377 首轮 trial 门：新接进去的 runner 写了 argparse required=True，
        # 门不带参数跑，步骤 argparse exit 2（0.5s 就红）。
        # 门的调用形式就是 `python tests/<runner>.py`，所以 runner 的目录参数必须有默认值。
        repo_root = Path(__file__).resolve().parents[1]
        offenders = []
        for name in sorted(BROWSER_EVIDENCE_STEP_NAMES):
            spec = browser_step_spec(name, sys.executable, Path(tempfile.mkdtemp(prefix="ff-gate-argv-")))
            command = spec[0] if spec else STEP_DEFINITIONS[name](sys.executable)
            if not command[1].startswith("tests/"):
                continue
            runner = repo_root / command[1]
            if not runner.is_file():
                continue
            text = runner.read_text(encoding="utf-8")
            for flag in re.findall(r'add_argument\(\s*"(--[a-z-]+)"[^)]*required=True', text, re.S):
                if flag not in command:
                    offenders.append(f"{name} -> {command[1]} 要 {flag}，但门的命令里没给")
        self.assertEqual([], offenders, "门的调用形式必须给齐 runner 标了 required=True 的参数")

    def test_wired_canvas_browser_runners_resolve_their_repo_root(self):
        # 门在临时 workspace 里执行步骤；runner 里硬编码的仓库根会读到工作区外的源码，
        # 在非本机（例如 CI）上直接带不动。
        repo_root = Path(__file__).resolve().parents[1]
        for name in (
            "canvas-boundaries-evidence",
            "video-failure-evidence",
            "node-flow-cascade-evidence",
            "fly-fixes-evidence",
            # #346：`hidden` 语义的页面级计算样式证据。
            "canvas-hidden-visibility-evidence",
            # #377：项目被删后的画布页保存链路证据。
            "canvas-project-gone-evidence",
            # #382：/ws/stats 断线后对已删项目停止重连的证据。
            "ws-stats-reconnect-evidence",
        ):
            command = STEP_DEFINITIONS[name](sys.executable)
            runner = repo_root / command[1]
            self.assertTrue(runner.is_file(), f"missing {command[1]}")
            self.assertNotIn("Firefly-Canvas", runner.read_text(encoding="utf-8"))
            self.assertIn("Path(__file__).resolve().parents[1]", runner.read_text(encoding="utf-8"))

    def test_every_browser_harness_that_spawns_kills_process_trees(self):
        # #361：chrome 带 ``--user-data-dir`` 会拉起 GPU / renderer / crashpad 一整套，
        # 只 `.terminate()` 直接子进程的话那些会留在后台钉着临时 profile
        # （2026-11-14 清出来的 26 个遗留里有 8 个 headless 浏览器）。
        offenders = []
        for runner in sorted((ROOT / "tests").glob("*browser*.py")):
            text = runner.read_text(encoding="utf-8")
            if runner.name == "test_browser_process.py":  # Contains synthetic launch examples.
                continue
            if ("Popen(" in text or "browser_popen(" in text) and "process_tree" not in text:
                offenders.append(runner.name)
        self.assertEqual([], offenders, "起子进程的 browser 夹具必须走 kill_process_tree")

    def test_canvas_interactions_evidence_stays_out_of_the_gate(self):
        # #172：canvas_interactions_browser.py 长期红（两条布局断言），
        # 接入只会让门常红并掩盖真实回归；那张票判定完再接。
        for name in ("canvas-interactions-evidence", "canvas-interactions"):
            self.assertNotIn(name, STEP_DEFINITIONS)
            self.assertNotIn(name, BROWSER_EVIDENCE_STEP_NAMES)
            for mode in MODE_STEPS.values():
                self.assertNotIn(name, mode)

    def test_ui_integrity_runs_only_in_trial_and_release(self):
        step = "ui-integrity-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])

    def test_excel_import_evidence_runs_only_in_trial_and_release(self):
        step = "excel-import-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])
        self.assertIn(step, BROWSER_EVIDENCE_STEP_NAMES)

    def test_excel_api_smoke_runs_in_fast_only(self):
        # trial/release 的 unit-contract 分片会发现全部单测，excel 模块已在其中；
        # fast 档只保留轻量 API 冒烟。
        step = "excel-api-smoke"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])

    def test_long_content_runs_only_in_trial_and_release(self):
        step = "long-content-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])

    def test_composer_flow_runs_only_in_trial_and_release(self):
        step = "composer-flow-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])

    def test_complete_generation_lifecycle_runs_only_in_trial_and_release(self):
        step = "complete-generation-lifecycle-evidence"
        self.assertIn(step, STEP_DEFINITIONS)
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])

    def test_native_focus_contract_recognizes_only_project_roundtrip_controls(self):
        accepted = [
            {"action": "click", "selector": "#showCreate", "focus_after": "button#showCreate.button.primary"},
            {"action": "click", "selector": "#projectName", "focus_after": "input#projectName"},
            {"action": "click", "selector": '#createForm button[type="submit"]', "focus_after": "button.button.primary"},
            {"action": "click", "selector": '#createMenu [data-create-type="prompt"]', "focus_after": "button.create-card"},
            {"action": "click", "selector": ".smart-back", "focus_after": "button.smart-back"},
            {"action": "click", "selector": "#projectGrid .project-card:nth-child(1) .project-open", "focus_after": "button.project-open"},
            {"action": "click", "selector": "#projectGrid .project-card:nth-child(1) .project-duplicate", "focus_after": "button.project-action.project-duplicate"},
            {"action": "click", "selector": "#projectGrid .project-card:nth-child(1) .project-cleanup-preview", "focus_after": "button.project-action.project-cleanup-preview"},
            {"action": "click", "selector": "#projectGrid .project-card:nth-of-type(1) .project-more", "focus_after": "button#project-more-seed.tool-button.tool-icon.project-more"},
            {"action": "click", "selector": "#closeCleanupPreview", "focus_after": "button#closeCleanupPreview.button.secondary"},
            {"action": "click", "selector": "#confirmCleanup", "focus_after": "button#confirmCleanup.button.danger"},
            {"action": "keyboard", "observed_target": "input#projectName", "focus_after": "input#projectName"},
        ]
        self.assertTrue(all(expected_native_focus(operation) for operation in accepted))
        self.assertFalse(
            expected_native_focus(
                {"action": "keyboard", "observed_target": "input#untrusted", "focus_after": "input#untrusted"}
            )
        )
        self.assertFalse(
            expected_native_focus(
                {
                    "action": "click",
                    "selector": "#projectGrid .project-card:nth-child(1) .project-duplicate",
                    "focus_after": "button.project-action.project-delete",
                }
            )
        )

    def test_domain_runs_the_provider_registry_contract(self):
        self.assert_provider_contract_failure(
            "tests.test_provider_contract_registry", "registry contract sentinel"
        )

    def test_domain_runs_the_provider_family_contracts(self):
        self.assert_provider_contract_failure(
            "tests.test_provider_contract_families", "family contract sentinel"
        )

    def assert_provider_contract_failure(self, module, sentinel):
        def spawn(command, _repo_root, _environment):
            if module in command:
                return StepResponse(returncode=1, stderr=f"AssertionError: {sentinel}\n")

        result = self.run_gate("domain", spawn=spawn)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "provider-contract")
        provider_step = next(step for step in report["steps"] if step["name"] == "provider-contract")
        self.assertIn(sentinel, provider_step["stderr_tail"])

    def test_all_modes_are_cumulative_and_keep_their_declared_budgets(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\n\n"
            "class ProjectContract(unittest.TestCase):\n"
            "    def test_project_contract(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "conversation_browser.py").write_text(
            "import socket\n"
            "server = socket.socket()\n"
            "server.bind(('127.0.0.1', 0))\n"
            "server.listen(1)\n"
            "client = socket.create_connection(('127.0.0.1', server.getsockname()[1]))\n"
            "connection, _ = server.accept()\n"
            "client.close()\n"
            "connection.close()\n"
            "server.close()\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_conversation_browser_harness.py").write_text(
            "import unittest\n\n"
            "class BrowserHarnessContract(unittest.TestCase):\n"
            "    def test_harness_contract(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()
        # #204：三个 unit-contract 槽位在运行时展开成具体分片步骤。这里钉死的是
        # (a) 各模式预算、(b) MODE_STEPS 的槽位顺序、(c) 真实步骤列表恰好是
        # 槽位按当前分片方案的确定性展开。
        expected = {
            "fast": (30, ("javascript-syntax", "javascript-types", "canvas-coupling", "excel-api-smoke", "surface-contract", "egress-attribution", "doc-link-check", "i18n-validate", "studio-color-guard")),
            "domain": (180, ("javascript-syntax", "javascript-types", "surface-contract", "project-contract", "provider-contract", "native-browser-evidence", "generation-lifecycle-evidence", "canvas-hidden-visibility-evidence", "egress-attribution")),
            "trial": (300, ("javascript-syntax", "javascript-types", "unit-contract-quality-gate", "unit-contract-remainder", "native-browser-evidence", "project-linked-generation-evidence", "generation-lifecycle-evidence", "complete-generation-lifecycle-evidence", "composer-flow-evidence", "long-content-evidence", "ui-integrity-evidence", "excel-import-evidence", "canvas-boundaries-evidence", "layers-panel-evidence", "canvas-hidden-visibility-evidence", "canvas-project-gone-evidence", "ws-stats-reconnect-evidence", "video-failure-evidence", "node-flow-cascade-evidence", "fly-fixes-evidence", "tabbit-e2e-evidence", "unit-contract-browser", "browser-tracer", "egress-attribution")),
            "release": (
                900,
                (
                    "javascript-syntax",
                    "javascript-types",
                    "unit-contract-quality-gate",
                    "unit-contract-remainder",
                    "native-browser-evidence",
                    "project-linked-generation-evidence",
                    "generation-lifecycle-evidence",
                    "complete-generation-lifecycle-evidence",
                    "composer-flow-evidence",
                    "long-content-evidence",
                    "ui-integrity-evidence",
                    "excel-import-evidence",
                    "edge-compatibility",
                    "canvas-boundaries-evidence",
                    "layers-panel-evidence",
                    "canvas-hidden-visibility-evidence",
                    "canvas-project-gone-evidence",
                    "ws-stats-reconnect-evidence",
                    "video-failure-evidence",
                    "node-flow-cascade-evidence",
                    "fly-fixes-evidence",
                    "tabbit-e2e-evidence",
                    "unit-contract-browser",
                    "browser-tracer",
                    "browser-harness-contract",
                    "egress-attribution",
                ),
            ),
        }
        shards = compute_unit_contract_shards(self.fixture_root)

        for mode, (budget, slots) in expected.items():
            with self.subTest(mode=mode):
                self.assertEqual(MODE_STEPS[mode], slots)
                self.evidence_dir = Path(self.temp_dir.name) / f"evidence-{mode}"
                result = self.run_gate(mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = self.report()
                self.assertEqual(report["budget_seconds"], budget)
                self.assertEqual(
                    [step["name"] for step in report["steps"]],
                    list(expand_unit_contract_steps(slots, shards)),
                )

    def test_edge_compatibility_step_is_release_only(self):
        step = "edge-compatibility"
        self.assertNotIn(step, MODE_STEPS["fast"])
        self.assertNotIn(step, MODE_STEPS["domain"])
        self.assertNotIn(step, MODE_STEPS["trial"])
        self.assertIn(step, MODE_STEPS["release"])


class GateShardIntegrationTests(GateFixture):
    def test_failing_dynamic_unit_contract_blocks_trial_and_preserves_first_failure(self):
        (self.fixture_root / "tests" / "test_dynamic_contract.py").write_text(
            "import unittest\n"
            "class DynamicContract(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('intentional unit shard failure')\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()
        shards = compute_unit_contract_shards(self.fixture_root)
        shard_name = next(
            name
            for name, targets in shards.items()
            if "tests.test_dynamic_contract" in targets
        )

        result = self.run_gate(mode="trial")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], shard_name)
        self.assertEqual(report["first_failure"]["status"], "failed")
        self.assertIn(
            "tests.test_dynamic_contract",
            report["unit_contract_inventory"]["modules"],
        )
        shard = next(step for step in report["steps"] if step["name"] == shard_name)
        self.assertIn("tests.test_dynamic_contract", shard["command"])
        if "failure_diagnostics" in shard:
            self.assertEqual(shard["stderr_tail"], "")
            self.assertIn("intentional unit shard failure", result.stdout + result.stderr)
            self.assertIn(
                "tests.test_dynamic_contract.DynamicContract.test_failure",
                [failure["test_id"] for failure in shard["failure_diagnostics"]["failures"]],
            )
        else:
            self.assertIn("intentional unit shard failure", shard["stderr_tail"])

    def test_self_test_mode_denies_unrelated_subprocesses(self):
        (self.fixture_root / "tests" / "test_quality_gate.py").write_text(
            "import subprocess\n"
            "import sys\n"
            "import unittest\n\n"
            "class SelfTestBoundaryContract(unittest.TestCase):\n"
            "    def test_unrelated_python_is_denied(self):\n"
            "        subprocess.run([sys.executable, '-c', 'print(1)'], check=False)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()
        shards = compute_unit_contract_shards(self.fixture_root)
        shard_name = next(
            name
            for name, targets in shards.items()
            if any("test_unrelated_python_is_denied" in target for target in targets)
        )

        result = self.run_gate(mode="trial")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], shard_name)
        subprocess_events = [
            event
            for event in report["guard_events"]
            if event.get("kind") == "subprocess-executable"
            and event.get("target") == "<redacted>"
        ]
        self.assertTrue(subprocess_events, report["guard_events"])
        # #230：证据要能回答「这条是谁发的」，所以守卫事件现在都带发起进程 pid。
        self.assertTrue(
            all(isinstance(event.get("pid"), int) and event["pid"] > 0 for event in subprocess_events),
            f'守卫事件没带 pid：{subprocess_events}',
        )


class GateOutcomeTests(InProcessGateFixture):
    @staticmethod
    def _append_outcome_event(event_path, event):
        with Path(event_path).open("a", encoding="utf-8") as events:
            events.write(json.dumps(event) + "\n")

    @staticmethod
    def _fail_javascript_syntax(command, _repo_root, _environment):
        if list(command) == STEP_DEFINITIONS["javascript-syntax"](sys.executable):
            return StepResponse(returncode=1, stderr="SyntaxError: Unexpected token ';'\n")

    def test_unit_contract_shard_timeout_blocks_trial(self):
        # Shard discovery reads this method's ID; only the fake child times out.
        (self.fixture_root / "tests" / "test_quality_gate.py").write_text(
            "import unittest\n"
            "class SlowQualityGateContract(unittest.TestCase):\n"
            "    def test_slow(self): pass\n",
            encoding="utf-8",
        )
        target = "tests.test_quality_gate.SlowQualityGateContract.test_slow"
        shards = compute_unit_contract_shards(self.fixture_root)
        shard_name = next(name for name, targets in shards.items() if target in targets)

        def spawn(command, _repo_root, _environment):
            if target in command:
                return StepResponse(timed_out=True)

        result = self.run_gate(
            mode="trial",
            spawn=spawn,
            FIREFLY_TEST_MODE="1",
            FIREFLY_QUALITY_GATE_TEST_BUDGET_SECONDS="8",
        )

        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["budget_seconds"], 300)
        self.assertEqual(report["effective_budget_seconds"], 8)
        self.assertEqual(report["first_failure"]["name"], shard_name)
        self.assertEqual(report["first_failure"]["status"], "timed-out")

    def test_fast_mode_runs_isolated_syntax_and_contract_steps_and_reports_dirty_tree(self):
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)

        def spawn(command, _repo_root, environment):
            if list(command) == surface_command:
                self.assertEqual(environment.get("FIREFLY_TEST_MODE"), "1")
                self.assertTrue(Path(environment["FIREFLY_RUNTIME_ROOT"]).is_dir())
                self.assertTrue(Path(environment["FIREFLY_PROJECTS_ROOT"]).is_dir())

        result = self.run_gate(spawn=spawn, verify_git=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["mode"], "fast")
        self.assertEqual(report["budget_seconds"], 30)
        self.assertEqual(report["verdict"], "passed")
        self.assertEqual(
            [step["name"] for step in report["steps"]],
            [
                "javascript-syntax", "javascript-types", "canvas-coupling", "excel-api-smoke",
                "surface-contract", "egress-attribution", "doc-link-check", "i18n-validate",
                "studio-color-guard",
            ],
        )
        self.assertTrue(all(step["status"] == "passed" for step in report["steps"]))
        self.assertTrue(report["dirty_worktree"]["before"]["dirty"])
        self.assertFalse(report["dirty_worktree"]["changed_during_run"])
        self.assertEqual(Path(report["evidence_dir"]), self.evidence_dir.resolve())
        self.assertIn("final_exit_code", report)

    def test_fast_mode_keeps_running_later_steps_after_an_early_failure(self):
        result = self.run_gate(mode="fast", spawn=self._fail_javascript_syntax)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(
            [step["name"] for step in report["steps"]], list(MODE_STEPS["fast"])
        )
        by_name = {step["name"]: step for step in report["steps"]}
        self.assertEqual(by_name["javascript-syntax"]["status"], "failed")
        self.assertEqual(report["first_failure"]["name"], "javascript-syntax")
        for name in MODE_STEPS["fast"][1:]:
            self.assertEqual(
                by_name[name]["status"],
                "passed",
                f"{name} 该照样跑完并绿：{by_name[name]['stderr_tail']}",
            )
        # Hooks need both outcomes in stderr without opening the report (#370).
        self.assertIn("[failed] javascript-syntax", result.stderr)
        self.assertIn("[passed] doc-link-check", result.stderr)

    def test_fast_mode_reports_every_failing_step_not_just_the_first(self):
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)

        def spawn(command, repo_root, environment):
            if list(command) == surface_command:
                return StepResponse(returncode=1, stderr="AssertionError: surface contract sentinel\n")
            return self._fail_javascript_syntax(command, repo_root, environment)

        result = self.run_gate(mode="fast", spawn=spawn)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        red = [step["name"] for step in report["steps"] if step["status"] != "passed"]
        self.assertEqual(red, ["javascript-syntax", "surface-contract"])
        self.assertEqual(report["first_failure"]["name"], "javascript-syntax")

    def test_parallel_group_boundary_event_does_not_redden_sibling_steps(self):
        violation = {"kind": "subprocess-executable", "target": "<redacted>"}
        (self.fixture_root / "tests" / "test_quality_gate.py").write_text(
            "import unittest\n"
            "class ParallelBoundaryContract(unittest.TestCase):\n"
            "    def test_denied_subprocess_is_attributed_to_its_own_step(self): pass\n",
            encoding="utf-8",
        )
        target = (
            "tests.test_quality_gate.ParallelBoundaryContract."
            "test_denied_subprocess_is_attributed_to_its_own_step"
        )
        shards = compute_unit_contract_shards(self.fixture_root)
        offender_name = next(name for name, targets in shards.items() if target in targets)

        def spawn(command, _repo_root, environment):
            if target in command and environment.get("FIREFLY_QUALITY_GATE_SELF_TEST") == "1":
                # A caught denial leaves a private guard record but the child exits 0 (#174).
                self._append_outcome_event(
                    environment["FIREFLY_QUALITY_GATE_GUARD_EVENTS"],
                    {**violation, "pid": 12345, "ppid": os.getpid(), "exe": "<redacted>"},
                )

        result = self.run_gate(mode="trial", spawn=spawn)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        with self.subTest(phase="step-isolation"):
            steps = {step["name"]: step for step in report["steps"]}
            offender = steps[offender_name]
            self.assertTrue(offender["parallel_group"])
            self.assertEqual(offender["status"], "failed", offender.get("stderr_tail") or "")
            self.assertEqual(offender["exit_code"], 1)
            self.assertIn(
                (violation["kind"], violation["target"]),
                {(event["kind"], event["target"]) for event in offender["guard_events"]},
            )
            for name in (
                *(shard_name for shard_name in shards if shard_name != offender_name),
                "excel-import-evidence",
                "canvas-boundaries-evidence",
            ):
                step = steps[name]
                self.assertEqual(
                    step["status"],
                    "passed",
                    f"{name} 被同组其它步骤的越界事件改红：{step.get('stderr_tail') or ''}",
                )
                self.assertEqual(step["exit_code"], 0, name)
                self.assertNotIn(
                    (violation["kind"], violation["target"]),
                    {(event["kind"], event["target"]) for event in step["guard_events"]},
                    f"{name} 的 guard_events 里出现了不属于它的边界事件",
                )
            self.assertIn(
                violation,
                [
                    {"kind": event.get("kind"), "target": event.get("target")}
                    for event in report["guard_events"]
                ],
            )
        with self.subTest(phase="verdict-reasons"):
            reasons = "\n".join(report["verdict_reasons"])
            self.assertIn(violation["kind"], reasons)
            self.assertIn(violation["target"], reasons)
            self.assertIn(offender_name, reasons)

    def test_evidence_directory_inside_repo_is_rejected_without_mutating_it(self):
        evidence_dir = self.fixture_root / "gate-evidence"

        result = self.run_gate(evidence_dir=evidence_dir)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(evidence_dir.exists())

    def test_parent_secrets_are_redacted_from_process_output_evidence(self):
        secret = "s3cr3t"
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)

        def spawn(command, _repo_root, environment):
            if list(command) == surface_command:
                self._append_outcome_event(
                    environment["FIREFLY_QUALITY_GATE_GUARD_EVENTS"],
                    {"kind": "subprocess-executable", "target": secret},
                )
                self._append_outcome_event(
                    environment["FIREFLY_QUALITY_GATE_RETRY_EVENTS"],
                    {secret: "must be redacted"},
                )
                return StepResponse(stdout=secret + "\n")

        result = self.run_gate(spawn=spawn, GITHUB_TOKEN=secret)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        serialized_report = json.dumps(self.report())
        self.assertNotIn(secret, serialized_report)
        self.assertIn("[REDACTED]", serialized_report)

    def test_retry_evidence_is_reported_as_failure_instead_of_green(self):
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)

        def spawn(command, _repo_root, environment):
            if list(command) == surface_command:
                self._append_outcome_event(
                    environment["FIREFLY_QUALITY_GATE_RETRY_EVENTS"],
                    {"attempt": 2, "reason": "fixture retry"},
                )

        result = self.run_gate(spawn=spawn)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(
            (self.evidence_dir / "report.json").is_file(),
            result.stdout + result.stderr,
        )
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "surface-contract")
        self.assertEqual(report["retry_events"], [{"attempt": 2, "reason": "fixture retry"}])

    def test_private_unit_shard_retry_evidence_fails_its_step(self):
        (self.fixture_root / "tests" / "test_private_retry.py").write_text(
            "import unittest\n"
            "class RetryContract(unittest.TestCase):\n"
            "    def test_retry(self): pass\n",
            encoding="utf-8",
        )
        target = "tests.test_private_retry"
        shard_name = next(
            name for name, targets in compute_unit_contract_shards(self.fixture_root).items()
            if target in targets
        )

        def spawn(command, _repo_root, environment):
            if target in command:
                self._append_outcome_event(
                    environment["FIREFLY_QUALITY_GATE_RETRY_EVENTS"], {"attempt": 2}
                )

        result = self.run_gate(mode="trial", spawn=spawn)
        report = self.report()
        step = next(step for step in report["steps"] if step["name"] == shard_name)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["retry_events"], [{"attempt": 2}])
        self.assertEqual(report["first_failure"]["name"], shard_name)

    def test_utf8_step_output_is_captured_without_crashing_the_gate(self):
        expected_output = "quality evidence: \U0001f525"
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)

        def spawn(command, _repo_root, _environment):
            if list(command) == surface_command:
                return StepResponse(stdout=(expected_output + "\n").encode("utf-8"))

        result = self.run_gate(spawn=spawn)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        surface_step = next(
            step for step in self.report()["steps"] if step["name"] == "surface-contract"
        )
        self.assertIn(expected_output, surface_step["stdout_tail"])

    def test_steps_run_in_a_snapshot_and_cannot_modify_an_already_dirty_file(self):
        relative_target = Path("static/js/smart-canvas.js")
        target = self.fixture_root / relative_target
        target.parent.mkdir(parents=True, exist_ok=True)
        original_content = b"const qualityGateFixture = true;\n"
        target.write_bytes(original_content)
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)
        mutated = False

        def spawn(command, repo_root, _environment):
            nonlocal mutated
            if list(command) == surface_command:
                snapshot_target = Path(repo_root) / relative_target
                self.assertEqual(snapshot_target.read_bytes(), original_content)
                snapshot_target.write_bytes(b"const mutatedDuringGate = true;\n")
                mutated = True

        result = self.run_gate(spawn=spawn, verify_git=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(mutated, "surface-contract did not make the snapshot write")
        self.assertEqual(target.read_bytes(), original_content)
        report = self.report()
        self.assertFalse(report["dirty_worktree"]["changed_during_run"])
        self.assertTrue(report["execution_workspace"]["isolated"])

    def test_skipped_contract_is_reported_as_failure_instead_of_green(self):
        surface_command = STEP_DEFINITIONS["surface-contract"](sys.executable)

        def spawn(command, _repo_root, _environment):
            if list(command) == surface_command:
                return StepResponse(stderr="s\n\nRan 1 test in 0.000s\n\nOK (skipped=1)\n")

        result = self.run_gate(spawn=spawn)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        contract = next(step for step in report["steps"] if step["name"] == "surface-contract")
        self.assertEqual(contract["status"], "failed")
        self.assertEqual(contract["unittest"]["skipped"], 1)
        self.assertEqual(report["first_failure"]["name"], "surface-contract")

    def test_browser_tracer_reported_skip_is_failure_instead_of_green(self):
        tracer_command = STEP_DEFINITIONS["browser-tracer"](sys.executable)

        def spawn(command, _repo_root, _environment):
            if list(command) == tracer_command:
                return StepResponse(stdout="SKIPPED: browser unavailable\n")

        result = self.run_gate(mode="trial", spawn=spawn)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        tracer = next(step for step in report["steps"] if step["name"] == "browser-tracer")
        self.assertTrue(tracer["reported_skip"])
        self.assertEqual(tracer["status"], "failed")

    def test_failed_non_critical_step_records_s2_finding_without_changing_verdict(self):
        result = self.run_gate(spawn=self._fail_javascript_syntax)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "javascript-syntax")
        s2_findings = [
            finding
            for finding in report["severity_frequency_findings"]
            if finding["severity"] == "S2"
        ]
        self.assertTrue(s2_findings)
        self.assertEqual(s2_findings[0]["step"], "javascript-syntax")
        self.assertEqual(s2_findings[0]["frequency"], "FU")
        self.assertEqual(s2_findings[0]["observations"], 1)
        self.assertFalse(report["s2_accepted"])

    def test_s2_accept_flag_is_recorded_but_never_converts_failure_into_success(self):
        result = self.run_gate(
            spawn=self._fail_javascript_syntax, FIREFLY_QUALITY_GATE_ACCEPT_S2="1"
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["verdict"], "failed")
        self.assertIs(report["s2_accepted"], True)
        self.assertEqual(report["first_failure"]["name"], "javascript-syntax")
        self.assertIn("explicitly accepted", " ".join(report["verdict_reasons"]))

    def test_guard_and_failed_critical_journey_are_classified_s0_and_s1(self):
        provider_command = STEP_DEFINITIONS["provider-contract"](sys.executable)

        def spawn(command, _repo_root, environment):
            if list(command) == provider_command:
                self._append_outcome_event(
                    environment["FIREFLY_QUALITY_GATE_GUARD_EVENTS"],
                    {
                        "kind": "network", "target": "provider.invalid",
                        "pid": 12345, "ppid": os.getpid(), "exe": command[0],
                    },
                )
                return StepResponse(
                    returncode=1,
                    stderr="PermissionError: quality gate denied non-localhost network target: provider.invalid\n",
                )

        result = self.run_gate("domain", spawn=spawn)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        severities = [finding["severity"] for finding in report["severity_frequency_findings"]]
        self.assertIn("S0", severities)
        self.assertIn("S1", severities)
        s0 = next(
            finding
            for finding in report["severity_frequency_findings"]
            if finding["severity"] == "S0"
        )
        self.assertEqual(s0["source"], "guard")
        # #230 requires the S0 message to distinguish process-tree attribution from fallback.
        self.assertIn("attributed to the gate process tree", s0["message"])
        s1 = next(
            finding
            for finding in report["severity_frequency_findings"]
            if finding["severity"] == "S1"
        )
        self.assertEqual(s1["source"], "step")
        self.assertEqual(s1["step"], "provider-contract")


_GUARD_SINK = "FIREFLY_QUALITY_GATE_GUARD_EVENTS"


_GUARD_PROXY = "FIREFLY_QUALITY_GATE_BROWSER_PROXY"


_GUARD_FIXTURE_PID = 41001


def _isolated_python_guard(environment, *, pid=_GUARD_FIXTURE_PID):
    """Install the actual sitecustomize hooks on disposable module facades."""
    def unexpected_io(*args, **kwargs):
        raise AssertionError("The guard fixture attempted unexpected external I/O")

    def fixture_lookup(host, port, *args, **kwargs):
        if host != "127.0.0.1":
            unexpected_io()
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]

    def no_io(*args, **kwargs):
        pass

    def open_guard_sink(path, flags, mode):
        if path != environment[_GUARD_SINK]:
            unexpected_io()
        return os.open(path, flags, mode)

    class Socket:
        __init__ = settimeout = connect = close = no_io
        connect_ex = sendto = unexpected_io

    class HTTPConnection:
        __init__ = set_tunnel = no_io

    class BaseEventLoop:
        create_connection = create_datagram_endpoint = unexpected_io

    class Popen:
        __init__ = unexpected_io

    fake_socket = SimpleNamespace(
        socket=Socket,
        getaddrinfo=fixture_lookup,
        gethostbyname=unexpected_io,
        gethostbyname_ex=unexpected_io,
        gethostbyaddr=unexpected_io,
        getnameinfo=unexpected_io,
    )
    fake_http = SimpleNamespace(client=SimpleNamespace(HTTPConnection=HTTPConnection))
    facades = {
        "socket": fake_socket,
        "http.client": fake_http,
        "asyncio": SimpleNamespace(BaseEventLoop=BaseEventLoop),
        "subprocess": SimpleNamespace(Popen=Popen),
        "os": SimpleNamespace(
            environ=dict(environment), path=os.path,
            getpid=lambda: pid, getppid=lambda: 41000,
            open=open_guard_sink, write=os.write, close=os.close,
            O_APPEND=os.O_APPEND, O_CREAT=os.O_CREAT, O_WRONLY=os.O_WRONLY,
        ),
        "sys": SimpleNamespace(executable=sys.executable),
        "shutil": SimpleNamespace(which=unexpected_io),
    }

    def fixture_import(name, globals=None, locals=None, fromlist=(), level=0):
        if not level and name in facades:
            return facades[name]
        if not level and name in {"hashlib", "ipaddress", "json", "urllib.parse"}:
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise ImportError(f"Unexpected isolated guard import: {name}")

    namespace = {
        "__name__": "_quality_gate_guard_fixture",
        "__builtins__": {**vars(builtins), "__import__": fixture_import},
    }
    exec(compile(GUARD_SOURCE, "<quality_gate.GUARD_SOURCE>", "exec"), namespace)

    # create_connection is not hooked itself. Its normal resolver and socket
    # calls must hit the installed wrappers, not the live socket module.
    create_connection = socket.create_connection
    fake_socket.create_connection = FunctionType(
        create_connection.__code__,
        {
            "_GLOBAL_DEFAULT_TIMEOUT": socket._GLOBAL_DEFAULT_TIMEOUT,
            "SOCK_STREAM": socket.SOCK_STREAM,
            "error": OSError,
            "getaddrinfo": fake_socket.getaddrinfo,
            "socket": fake_socket.socket,
        },
        argdefs=create_connection.__defaults__,
        closure=create_connection.__closure__,
    )
    fake_socket.create_connection.__kwdefaults__ = create_connection.__kwdefaults__
    return SimpleNamespace(socket=fake_socket, http=fake_http)


def _deny_synthetic_proxy_request(environment, method, target, host):
    """Dispatch an in-memory HTTP request through the real deny handler."""
    # Use the production constructor's handler closure without opening a listener
    # or patching http.server (the gate fixture may replace BrowserDenyProxy).
    initializer = _ProductionBrowserDenyProxy.__init__
    real_server = initializer.__globals__["http"].server

    def capture_handler(address, handler):
        return SimpleNamespace(RequestHandlerClass=handler, serve_forever=lambda: None)

    local_initializer = FunctionType(
        initializer.__code__,
        {
            **initializer.__globals__,
            "http": SimpleNamespace(server=SimpleNamespace(
                BaseHTTPRequestHandler=real_server.BaseHTTPRequestHandler,
                ThreadingHTTPServer=capture_handler,
            )),
        },
        argdefs=initializer.__defaults__,
        closure=initializer.__closure__,
    )
    proxy = _ProductionBrowserDenyProxy.__new__(_ProductionBrowserDenyProxy)
    local_initializer(proxy, Path(environment[_GUARD_SINK]))
    wire = f"{method} {target} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode("ascii")
    response = bytearray()
    request = SimpleNamespace(makefile=lambda *_: io.BytesIO(wire), sendall=response.extend)
    proxy.server.RequestHandlerClass(request, ("127.0.0.1", 41010), proxy.server)
    if response.split(b" ", 2)[1:2] != [b"403"]:
        raise AssertionError(f"Synthetic {method} was not denied: {bytes(response)!r}")


def _guarded_proxy_request(
    environment, method, target, host, *, pid=_GUARD_FIXTURE_PID, tunnel_host=None,
):
    guard = _isolated_python_guard(environment, pid=pid)
    proxy = urlsplit(environment[_GUARD_PROXY])
    if tunnel_host is not None:
        guard.http.client.HTTPConnection(proxy.hostname, proxy.port).set_tunnel(tunnel_host, 443)
    connection = guard.socket.create_connection((proxy.hostname, proxy.port), timeout=3)
    try:
        _deny_synthetic_proxy_request(environment, method, target, host)
    finally:
        connection.close()


class GuardRuleTests(InProcessGateFixture):
    # All old QualityGateTests IDs map to the same method name here, except the
    # three #499 look-alike IDs mapped to named subTests at their destination.
    @staticmethod
    def _kind_and_target(events):
        return [
            {"kind": event.get("kind"), "target": event.get("target")}
            for event in events or []
        ]

    def _run_guard_contract(
        self, exercise, *, mode="fast", module="tests.test_product_surface_behavior", **environment,
    ):
        seen = []

        def spawn(command, repo_root, step_environment):
            if module in command:
                seen.append(tuple(command))
                return exercise(step_environment)
            return None

        result = self.run_gate(mode=mode, spawn=spawn, **environment)
        # A missing or redirected contract must not make a green-case test pass.
        self.assertEqual(len(seen), 1, f"Expected one launch of {module}: {seen}")
        return result

    def _run_background_targets(self, *targets, recorded_kind=None):
        # An uninstrumented caller leaves only the proxy-side event, with no PID.
        def exercise(environment):
            for target in targets:
                if recorded_kind is not None:
                    quality_gate.append_event(Path(environment[_GUARD_SINK]), recorded_kind, target)
                else:
                    authority = f"{target}:443"
                    _deny_synthetic_proxy_request(environment, "CONNECT", authority, authority)

        return self._run_guard_contract(exercise)

    def _caught_network_denial(self, environment):
        guard = _isolated_python_guard(environment)
        with self.assertRaises(PermissionError):
            guard.socket.create_connection(("203.0.113.1", 443), timeout=0.01)

    def test_a_non_probe_proxy_hop_from_a_guarded_process_is_still_a_violation(self):
        def exercise(environment):
            # #232: a probe exemption in this event batch must stay scoped to
            # that PID, even though both clients use the same proxy endpoint.
            probe = "egress-attribution-probe.invalid"
            authority = f"{probe}:443"
            _guarded_proxy_request(
                environment, "CONNECT", authority, authority,
                pid=_GUARD_FIXTURE_PID + 1, tunnel_host=probe,
            )
            _guarded_proxy_request(
                environment, "GET", "http://not-the-probe.invalid/x", "not-the-probe.invalid",
            )

        result = self._run_guard_contract(exercise)
        self.assertNotEqual(result.returncode, 0, "非探针目标的外连却没判红，信封不隔离")
        blocking = (self.report().get("guard_attribution") or {}).get("blocking") or []
        self.assertTrue(
            any(
                event.get("kind") == "proxy-egress" and event.get("pid") == _GUARD_FIXTURE_PID
                for event in blocking
            ),
            f"非探针 pid 的 hop 被一起免掉了：{blocking}",
        )

    def test_domain_hard_denies_non_loopback_access_from_the_provider_contract(self):
        def exercise(environment):
            guard = _isolated_python_guard(environment)
            with self.assertRaises(PermissionError) as denied:
                guard.socket.create_connection(("provider.invalid", 443), timeout=0.1)
            # The old provider contract let the denial escape the test body.
            return StepResponse(returncode=1, stderr=str(denied.exception))

        result = self._run_guard_contract(
            exercise, mode="domain", module="tests.test_provider_contract_template",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "provider-contract")
        network_events = [event for event in report["guard_events"] if event["kind"] == "network"]
        self.assertTrue(network_events)
        self.assertEqual(network_events[0]["target"], "provider.invalid")

    def test_caught_browser_proxy_attempt_is_reported_and_fails_the_gate(self):
        def exercise(environment):
            _guarded_proxy_request(environment, "CONNECT", "browser-escape.example:443", "browser-escape.example:443")
            _guarded_proxy_request(environment, "PROPFIND", "http://browser-method.example/resource", "browser-method.example")

        result = self._run_guard_contract(exercise)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        network_targets = {
            event["target"]
            for event in self.report()["guard_events"]
            if event["kind"] == "network"
        }
        self.assertEqual(network_targets, {"<redacted>"})

    def _run_with_shared_proxy_denial_during_doc_check(self, step_response, *, step_event=False):
        proxy_environment = {}
        seen = []

        def proxy_factory(sink):
            proxy_environment[_GUARD_SINK] = str(sink)
            return nullcontext(SimpleNamespace(url="http://127.0.0.1:41080"))

        def spawn(command, repo_root, step_environment):
            if "tools/doc_link_check.py" not in command:
                return None
            seen.append("doc-link-check")
            authority = "anonymous-connect.invalid:443"
            _deny_synthetic_proxy_request(proxy_environment, "CONNECT", authority, authority)
            if step_event:
                quality_gate.append_event(Path(step_environment[_GUARD_SINK]), "network", "<redacted>")
            return step_response

        result = self.run_gate(spawn=spawn, proxy_factory=proxy_factory)
        self.assertEqual(seen, ["doc-link-check"])
        return result, self.report()

    def test_anonymous_proxy_denial_is_a_gate_failure_not_a_sequential_step_failure(self):
        result, report = self._run_with_shared_proxy_denial_during_doc_check(
            StepResponse(stdout="local links checked"),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        step = next(step for step in report["steps"] if step["name"] == "doc-link-check")
        self.assertEqual((step["status"], step["exit_code"]), ("passed", 0))
        self.assertIn("local links checked", step["stdout_tail"])
        self.assertEqual(step["guard_events"], [])
        self.assertEqual(report["first_failure"]["name"], "boundary-events")
        self.assertIn(
            {"kind": "network", "target": "<redacted>", "pid": None,
             "step": None, "attributed_by": "unattributed"},
            report["guard_attribution"]["blocking"],
        )
        self.assertTrue(any(
            finding["severity"] == "S0" and finding["step"] == "unattributed"
            for finding in report["severity_frequency_findings"]
        ))

    def test_shared_proxy_denial_keeps_its_own_attribution_next_to_identical_step_evidence(self):
        result, report = self._run_with_shared_proxy_denial_during_doc_check(
            StepResponse(returncode=7, stderr="local check failed"), step_event=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        failure = report["first_failure"]
        self.assertEqual((failure["name"], failure["exit_code"]), ("doc-link-check", 7))
        self.assertEqual(len(failure["guard_events"]), 1)
        self.assertCountEqual(
            [event["step"] for event in report["guard_attribution"]["blocking"]],
            ["doc-link-check", None],
        )
        self.assertCountEqual(
            [finding["step"] for finding in report["severity_frequency_findings"] if finding["severity"] == "S0"],
            ["doc-link-check", "unattributed"],
        )

    def test_blocked_browser_control_plane_noise_is_reported_without_failing_product_gate(self):
        result = self._run_background_targets("clients2.google.com")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "browser-background-network", "target": "clients2.google.com"},
            self._kind_and_target(self.report()["guard_events"]),
        )

    def test_microsoft_and_bing_control_plane_noise_is_a_background_observation(self):
        targets = ("edge.microsoft.com", "cn.bing.com", "www.bing.com")
        # Match the original unattributed event fixture; the live proxy redacts
        # these suffix-matched names before writing its own events.
        result = self._run_background_targets(*targets, recorded_kind="browser-background-network")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        attribution = report.get("guard_attribution") or {}
        self.assertEqual(
            attribution.get("unattributable_background"),
            (attribution.get("by_method") or {}).get("domain-fallback"),
            "背景噪声计数与 by_method 不自洽：" + json.dumps(attribution, ensure_ascii=False)[:800],
        )
        for target in targets:
            self.assertIn(
                {"kind": "browser-background-network", "target": target, "attributed_by": "domain-fallback"},
                report["guard_events"],
            )

    def test_chrome_update_control_plane_noise_is_a_background_observation(self):
        result = self._run_background_targets("update.googleapis.com")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "browser-background-network", "target": "update.googleapis.com"},
            self._kind_and_target(self.report()["guard_events"]),
        )

    def test_google_account_oauth_endpoint_is_browser_background_noise(self):
        result = self._run_background_targets("oauth2.googleapis.com")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "browser-background-network", "target": "oauth2.googleapis.com"},
            self._kind_and_target(self.report()["guard_events"]),
        )

    def test_host_ai_tool_control_plane_noise_is_a_background_observation(self):
        targets = (
            "antigravity-unleash.goog",
            "daily-cloudcode-pa.googleapis.com",
            "antigravity-cli-auto-updater-974169037036.us-central1.run.app",
        )
        result = self._run_background_targets(*targets)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        for target in targets:
            self.assertIn(
                {"kind": "host-tool-network", "target": target, "attributed_by": "domain-fallback"},
                report["guard_events"],
            )
        self.assertNotIn("antigravity-unleash.goog", json.dumps(report["verdict_reasons"], ensure_ascii=False))

    def test_host_noise_counts_are_reported_for_cross_machine_comparison(self):
        result = self._run_background_targets("antigravity-unleash.goog", "play.googleapis.com", "www.googleapis.com")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        counts = self.report()["guard_event_counts"]
        self.assertEqual(counts.get("host-tool-network"), 3, "宿主噪声计数缺失 = 换机器无法对比（#227 期望 3）")
        self.assertFalse(counts.get("network"), "没有真违规却出现 network 计数 = 分类把真违规洗掉了")

    def test_guard_events_carry_originating_process_identity(self):
        result = self._run_guard_contract(self._caught_network_denial)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        events = [
            event for event in report["guard_events"]
            if event.get("kind") == "network" and str(event.get("target")) == "203.0.113.1"
        ]
        self.assertTrue(events, f'没有 network 违规事件：{report["guard_events"]}')
        for event in events:
            self.assertIsInstance(event.get("pid"), int, f'缺 pid：{event}')
            self.assertIsInstance(event.get("ppid"), int, f'缺父进程 pid（#230 要求带父链）：{event}')
            self.assertTrue(str(event.get("exe") or "").strip(), f'缺可执行路径：{event}')

    def test_guard_attribution_counts_each_attribution_method(self):
        def exercise(environment):
            _deny_synthetic_proxy_request(environment, "CONNECT", "clients2.google.com:443", "clients2.google.com:443")
            self._caught_network_denial(environment)

        result = self._run_guard_contract(exercise)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        attribution = report.get("guard_attribution") or {}
        by_method = attribution.get("by_method") or {}
        self.assertTrue(str(attribution.get("by_method_scope") or "").strip(), "by_method 没标明统计口径")
        self.assertGreaterEqual(by_method.get("process-tree", 0), 1, f"门进程树自己发起的事件没按进程树归类：{by_method}")
        self.assertGreaterEqual(by_method.get("domain-fallback", 0), 1, f"归属不到的噪声没标明是域名兜底：{by_method}")
        labelled = [
            event for event in report["guard_events"]
            if str(event.get("kind")) in {"network", "browser-background-network"}
        ]
        self.assertTrue(all(event.get("attributed_by") for event in labelled), f"证据里看不出这条事件用的判据：{labelled}")

    def test_native_socket_connect_to_allowlisted_target_is_blocking(self):
        def exercise(environment):
            _guarded_proxy_request(environment, "CONNECT", "www.googleapis.com:443", "www.googleapis.com:443")

        result = self._run_guard_contract(exercise)
        report = self.report()
        self.assertEqual(
            result.returncode, 1,
            "原生连接连到域名清单里的目标被判成宿主噪声了（#230）：" + result.stdout[-2000:],
        )
        blocking = (report.get("guard_attribution") or {}).get("blocking") or []
        self.assertTrue(
            any(event.get("kind") == "proxy-egress" and event.get("attributed_by") == "process-tree" for event in blocking),
            f"出口代理 hop 没被按进程树归为违规：{blocking}",
        )

    def test_loopback_traffic_to_a_non_proxy_port_is_not_a_violation(self):
        def exercise(environment):
            proxy_port = urlsplit(environment[_GUARD_PROXY]).port
            # Pick an ordinary loopback port that cannot equal the proxy port.
            local_port = proxy_port % 65535 + 1
            guard = _isolated_python_guard(environment)
            guard.socket.create_connection(("127.0.0.1", local_port)).close()

        result = self._run_guard_contract(exercise)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertFalse(
            [event for event in self.report()["guard_events"] if event.get("kind") == "proxy-egress"],
            "连本地非代理端口被记成了出口代理 hop",
        )

    def test_google_account_oauth_connect_is_classified_as_browser_noise(self):
        def exercise(environment):
            _guarded_proxy_request(environment, "CONNECT", "oauth2.googleapis.com:443", "oauth2.googleapis.com:443")

        result = self._run_guard_contract(exercise)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertIn(
            {"kind": "browser-background-network", "target": "oauth2.googleapis.com"},
            self._kind_and_target(report["guard_events"]),
        )
        blocking = (report.get("guard_attribution") or {}).get("blocking") or []
        self.assertTrue(
            any(event.get("kind") == "proxy-egress" and event.get("attributed_by") == "process-tree" for event in blocking),
            f"代理 hop 没按进程树归为违规：{blocking}",
        )

    def test_look_alike_hosts_are_blocking(self):
        # QualityGateTests.test_host_ai_tool_look_alike_host_is_still_a_blocking_network_target -> host-ai-tool
        # QualityGateTests.test_google_update_look_alike_host_is_still_a_blocking_network_target -> google-update
        # QualityGateTests.test_microsoft_look_alike_host_is_still_a_blocking_network_target -> microsoft
        for case, target, exact_exit in (
            ("host-ai-tool", "antigravity-unleash.goog.evil.com", None),
            ("google-update", "evil-update.googleapis.com", None),
            ("microsoft", "evil-microsoft.com", 1),
        ):
            with self.subTest(case=case):
                def exercise(environment):
                    authority = f"{target}:443"
                    _guarded_proxy_request(environment, "CONNECT", authority, authority)

                result = self._run_guard_contract(exercise)
                if exact_exit is None:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, exact_exit, result.stdout + result.stderr)
                self.assertIn(
                    {"kind": "network", "target": "<redacted>"},
                    self._kind_and_target(self.report()["guard_events"]),
                )

    def test_provider_configuration_paths_are_not_inherited(self):
        def exercise(environment):
            self.assertNotIn("CODEX_AUTH_FILE", environment)

        # Keep the inherited value an absolute path without creating a config file.
        synthetic_path = Path(tempfile.gettempdir()) / "synthetic-provider" / "real-auth.json"
        result = self._run_guard_contract(exercise, CODEX_AUTH_FILE=str(synthetic_path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GateProxyIntegrationTests(GateFixture):
    def test_attribution_probe_attempt_is_evidence_not_a_violation(self):
        # #232：真实门跑里必须出现一类能按进程树归属的产品外连，否则 #230 的
        # 进程树判据只在 fixture 里成立。探针尝试的那次外连不能计 S0——但
        # 豁免不能变成“允许出网”：代理仍拒 CONNECT，字节出不去；豁免只把
        # 这一次尝试降级为归属证据，且只对写死的保留域名生效。
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import socket\n"
            "import unittest\n\n"
            "class ProbeAttemptContract(unittest.TestCase):\n"
            "    def test_probe_target_is_denied_but_recorded(self):\n"
            "        try:\n"
            "            socket.create_connection(('egress-attribution-probe.invalid', 443), timeout=3)\n"
            "        except OSError:\n"
            "            pass\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        report = self.report()
        probe_events = [
            event
            for event in report["guard_events"]
            if "egress-attribution-probe.invalid" in str(event.get("target", ""))
        ]
        self.assertTrue(probe_events, "探针尝试没进证据")
        self.assertTrue(
            all(event.get("pid") for event in probe_events),
            f"探针记录没带进程身份：{probe_events}",
        )
        attribution = report.get("guard_attribution") or {}
        self.assertGreaterEqual(
            (attribution.get("by_method") or {}).get("process-tree", 0),
            1,
            f"真实跑里进程树归属仍为 0：{attribution.get('by_method')}",
        )
        self.assertFalse(attribution.get("domain_fallback_only"), "有进程树归属却标成全靠域名兜底")
        self.assertEqual(attribution.get("blocking") or [], [])
        self.assertEqual(
            [reason for reason in report["verdict_reasons"] if "S0 boundary violation" in reason],
            [],
            "探针尝试仍被判 S0",
        )

    def test_gate_tree_process_reaching_a_host_tool_target_is_still_blocking(self):
        # #230：域名白名单回答的是「目标像不像宿主遥测」，不是「连接是谁发的」。
        # 产品自己的进程（在门进程树里）去连 play.googleapis.com 这种白名单目标，
        # 必须还是 S0——否则白名单就把真违规洗掉了（#227 期望 1 的反向版）。
        # 注意：必须覆写 fast 步骤实际会跑的那个文件，否则 fixture 根本不被执行，
        # 门当然绿——这是一个真实踩过的 vacuous 陷阱。
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import http.client\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class ProductTreeReachesHostTarget(unittest.TestCase):\n"
            "    def test_connect(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        # 用真实 HTTP 客户端（requests/httpx 也是这么 CONNECT 的），\n"
            "        # 目标主机走 set_tunnel，守卫才能记下产品请求的目标。\n"
            "        connection = http.client.HTTPConnection(proxy.hostname, proxy.port)\n"
            "        connection.set_tunnel('play.googleapis.com', 443)\n"
            "        try:\n"
            "            connection.request('GET', '/')\n"
            "        except Exception:\n"
            "            pass\n"
            "        finally:\n"
            "            connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        report = self.report()
        counts = report.get("guard_event_counts", {})
        self.assertEqual(
            result.returncode, 1,
            '门进程树自己的连接被判成宿主噪声放过了（#230）：' + result.stdout[-2000:],
        )
        self.assertGreaterEqual(
            counts.get("proxy-request", 0), 1,
            '证据里没有「这个进程请求了这个目标」的归属记录 = 无法按进程树归属（#230 期望 1）',
        )
        attributed = [
            event
            for step in report.get("steps", [])
            for event in (step.get("guard_events") or [])
            if event.get("kind") == "proxy-request"
        ]
        self.assertTrue(
            attributed and all(isinstance(event.get("pid"), int) and event["pid"] > 0 for event in attributed),
            f'归属记录没带发起进程 pid：{attributed}',
        )
        blocking = (report.get("guard_attribution") or {}).get("blocking") or []
        self.assertTrue(
            any(
                event.get("kind") == "proxy-request"
                and str(event.get("target", "")).lower() == "play.googleapis.com"
                and event.get("attributed_by") == "process-tree"
                and isinstance(event.get("pid"), int)
                for event in blocking
            ),
            f'产品自己的 CONNECT 记录（带 pid）没按进程树判红：{blocking}',
        )
        # 宿主噪声不能被改名成产品违规。判红靠的是带 pid 的那条 proxy-request，
        # 不需要给代理日志里那条噪声换归属；一换就会同现「产品连接数」虚高，
        # 而且 step 视图与全量视图会给出两个不同的归属。
        noise = [
            event
            for event in report["guard_events"]
            if event.get("kind") == "host-tool-network"
            and str(event.get("target", "")).lower() == "play.googleapis.com"
        ]
        self.assertTrue(noise, "代理日志里没有那条宿主噪声事件，fixture 没跑到")
        self.assertEqual(
            {event.get("attributed_by") for event in noise},
            {"domain-fallback"},
            f'宿主噪声被升级成产品归属：{noise}',
        )
        # 同一条事件在 step 视图和全量视图里必须同一个判据。
        step_view = {
            (event.get("kind"), str(event.get("target")), event.get("pid")): event.get("attributed_by")
            for step in report.get("steps", [])
            for event in (step.get("guard_events") or [])
        }
        merged_view = {
            (event.get("kind"), str(event.get("target")), event.get("pid")): event.get("attributed_by")
            for event in report["guard_events"]
        }
        shared = set(step_view) & set(merged_view)
        self.assertTrue(shared, "step 视图与全量视图没有共同事件，无法对账")
        self.assertEqual(
            {key: step_view[key] for key in shared},
            {key: merged_view[key] for key in shared},
            "同一条事件在两个视图里判据不同（#230 证据自相矛盾）",
        )
        attribution = report.get("guard_attribution") or {}
        self.assertEqual(
            attribution.get("unattributable_background"),
            (attribution.get("by_method") or {}).get("domain-fallback"),
            "背景噪声计数与 by_method 不自洽：" + json.dumps(attribution, ensure_ascii=False)[:800],
        )


class GateToolIntegrationTests(GateFixture):
    def test_javascript_types_step_installs_offline_and_rejects_bad_js(self):
        # #369：最小门禁 fixture 无类型检查配置，必须另用完整配置验证离线安装及类型检查红绿。
        scratch = Path(self.temp_dir.name) / "type-repo"
        (scratch / "static" / "js").mkdir(parents=True)
        (scratch / "tests").mkdir()
        (scratch / "packages" / "npm").mkdir(parents=True)
        shutil.copy2(ROOT / "package.json", scratch / "package.json")
        shutil.copy2(ROOT / "jsconfig.json", scratch / "jsconfig.json")
        shutil.copy2(ROOT / "package-lock.json", scratch / "package-lock.json")
        for tarball in sorted((ROOT / "packages" / "npm").glob("*.tgz")):
            shutil.copy2(tarball, scratch / "packages" / "npm" / tarball.name)
        shutil.copy2(
            ROOT / "tests" / "test_javascript_types.py",
            scratch / "tests" / "test_javascript_types.py",
        )
        (scratch / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (scratch / "static" / "js" / "typed.js").write_text(
            "// @ts-check\n/** @type {number} */\nconst value = 42;\nexport { value };\n",
            encoding="utf-8",
        )

        environment = os.environ.copy()
        environment["NPM_CONFIG_CACHE"] = str(Path(self.temp_dir.name) / "npm-cache-must-stay-empty")
        command = [sys.executable, "-m", "unittest", "-b", "tests.test_javascript_types"]
        with assert_node_modules_unchanged(self, ROOT):
            green = subprocess.run(
                command,
                cwd=scratch,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)
        self.assertTrue((scratch / "node_modules" / "typescript" / "lib" / "tsc.js").is_file())
        self.assertFalse(Path(environment["NPM_CONFIG_CACHE"]).exists(), "步骤跑了 npm / 写了 npm 缓存")

        (scratch / "static" / "js" / "bad.js").write_text(
            "// @ts-check\n/** @type {number} */\nconst value = \"not a number\";\nexport { value };\n",
            encoding="utf-8",
        )
        with assert_node_modules_unchanged(self, ROOT):
            red = subprocess.run(
                command,
                cwd=scratch,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        self.assertNotEqual(red.returncode, 0)
        self.assertIn("TS2322", red.stderr)

    def test_gate_helper_imports_exist_in_both_modes(self):
        # 门有两个 import 分支：当包导入走 ``from tools.process_tree import``，
        # 而门在沙箱 workspace 里把自己是当顶层脚本跑，走 ``from process_tree import``。
        # 只修一个分支时，测试里 ``import tools.quality_gate`` 是绿的，真跑门才 NameError。
        probe = (
            "import pathlib, sys\n"
            f"sys.path.insert(0, str(pathlib.Path(r'{ROOT}') / 'tools'))\n"
            "import quality_gate\n"
            "required = ['ProcessJob', 'gate_leftover_processes', 'job_object_supported', "
            "'kill_process_tree', 'select_reapable_orphans']\n"
            "missing = [n for n in required if not hasattr(quality_gate, n)]\n"
            "raise SystemExit(1 if missing else 0)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, encoding="utf-8", timeout=180
        )
        self.assertEqual(0, result.returncode, f"沙箱模式（顶层脚本）缺帮助函数：{result.stderr[-400:]}")


class GateCliIntegrationTests(GateFixture):
    def test_real_pre_commit_fast_gate_blocks_broken_canvas_and_runs_later_checks(self):
        # Git 实际调用仓库里的 hook；薄包装只把真门的证据指向夹具目录。
        hooks = self.fixture_root / HOOKS_DIR
        hooks.mkdir(parents=True)
        shutil.copy2(ROOT / HOOKS_DIR / "pre-commit", hooks / "pre-commit")
        (hooks.parent / "quality_gate.py").write_text(
            "import subprocess, sys\n"
            f"raise SystemExit(subprocess.call([sys.executable, {str(QUALITY_GATE)!r}, "
            f"*sys.argv[1:], '--repo-root', {str(self.fixture_root)!r}, "
            f"'--evidence-dir', {str(self.evidence_dir)!r}]))\n",
            encoding="utf-8",
        )
        for key, value in (("user.name", "gate test"), ("user.email", "gate@test.invalid")):
            result = hook_test_git(self.fixture_root, "config", key, value)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(install_git_hooks(self.fixture_root), 0)
        script = self.fixture_root / "static/js/smart-canvas.js"
        valid_source = script.read_text(encoding="utf-8")
        script.write_text("const broken = ;\n", encoding="utf-8")
        result = hook_test_git(self.fixture_root, "add", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)

        rejected = hook_test_git(self.fixture_root, "commit", "-m", "invalid canvas")
        self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
        self.assertNotEqual(hook_test_git(self.fixture_root, "rev-parse", "--verify", "HEAD").returncode, 0)
        report = self.report()
        self.assertEqual(report["final_exit_code"], 1)
        self.assertEqual([step["name"] for step in report["steps"]], list(MODE_STEPS["fast"]))
        self.assertEqual(
            [(step["name"], step["status"]) for step in report["steps"]],
            [(name, "failed" if name == "javascript-syntax" else "passed") for name in MODE_STEPS["fast"]],
        )
        self.assertIn("[failed] javascript-syntax", rejected.stderr)
        self.assertIn("[passed] studio-color-guard", rejected.stderr)

        script.write_text(valid_source, encoding="utf-8")
        result = hook_test_git(self.fixture_root, "add", "--", "static/js/smart-canvas.js")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.evidence_dir.rename(self.evidence_dir.with_name("red-evidence"))
        accepted = hook_test_git(self.fixture_root, "commit", "-m", "valid canvas")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.report()["verdict"], "passed")

    def test_release_reports_issue39_audit_fields_and_parallel_evidence(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "conversation_browser.py").write_text(
            "import socket\n"
            "server = socket.socket()\n"
            "server.bind(('127.0.0.1', 0))\n"
            "server.listen(1)\n"
            "client = socket.create_connection(('127.0.0.1', server.getsockname()[1]))\n"
            "connection, _ = server.accept()\n"
            "client.close()\n"
            "connection.close()\n"
            "server.close()\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_conversation_browser_harness.py").write_text(
            "import unittest\n\n"
            "class BrowserHarnessContract(unittest.TestCase):\n"
            "    def test_harness_contract(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate(mode="release")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["verdict"], "passed")
        self.assertIs(report["offline_dry_run"], True)
        self.assertEqual(
            report["support_matrix"]["windows"]["chrome"]["stable"]["modes"],
            ["fast", "domain", "trial", "release"],
        )
        self.assertEqual(
            report["support_matrix"]["windows"]["edge"]["stable"]["modes"],
            ["release"],
        )
        self.assertEqual(report["support_matrix"]["phone"], "out")
        self.assertEqual(report["support_matrix"]["firefox"], "out")
        self.assertEqual(report["severity_frequency_findings"], [])
        self.assertEqual(sorted(report["severity_policy"]), ["S0", "S1", "S2", "S3"])
        self.assertFalse(report["s2_accepted"])
        self.assertEqual(report["retry_policy"]["allowed_evidence_retries"], 0)
        self.assertIs(report["retry_policy"]["first_failure_preserved"], True)
        self.assertTrue(report["verdict_reasons"])
        summary = report["provider_contract_summary"]
        self.assertIn("fixture_files", summary)
        self.assertIn("contract_test_modules", summary)
        edge_step = next(step for step in report["steps"] if step["name"] == "edge-compatibility")
        self.assertEqual(edge_step["status"], "passed")
        self.assertIs(edge_step["parallel_group"], True)
        self.assertEqual(edge_step["browser_evidence"]["status"], "validated")
        native_step = next(
            step for step in report["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIs(native_step["parallel_group"], True)
        shards = report["unit_contract_inventory"]["shards"]
        self.assertTrue(shards)
        for name in sorted(shards):
            contract_step = next(step for step in report["steps"] if step["name"] == name)
            self.assertIs(contract_step["parallel_group"], True)
        self.assertTrue(report["unit_contract_inventory"]["covered_in_mode"])
        covered_modules = [
            module
            for shard_modules in shards.values()
            for module in shard_modules
        ]
        self.assertEqual(
            sorted(covered_modules),
            report["unit_contract_inventory"]["modules"],
        )


class GateEvidenceIntegrationTests(GateFixture):
    def test_domain_validates_and_reports_native_browser_artifacts(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "passed")
        evidence = browser_step["browser_evidence"]
        self.assertEqual(evidence["status"], "validated")
        self.assertEqual(evidence["viewport_count"], 5)
        self.assertTrue(Path(evidence["manifest"]).is_file())
        self.assertTrue(Path(evidence["root"]).is_relative_to(self.evidence_dir.resolve()))
        lifecycle_step = next(
            step for step in report["steps"] if step["name"] == "generation-lifecycle-evidence"
        )
        self.assertEqual(lifecycle_step["status"], "passed")
        self.assertEqual(lifecycle_step["browser_evidence"]["viewport_count"], 5)

    def test_domain_fails_closed_when_native_browser_manifest_is_incomplete(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(complete=False)

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "native-browser-evidence")
        self.assertIn("native browser evidence invalid", report["first_failure"]["stderr_tail"])


class GateEvidenceTests(InProcessGateFixture):
    def setUp(self):
        GateTempPaths.setUp(self)
        (self.fixture_root / "tests").mkdir(parents=True)

    def evaluate_evidence(self, name="native-browser-evidence"):
        spec = browser_step_spec(name, sys.executable, self.evidence_dir)
        self.write_browser_artifacts(spec[0])
        step = {"name": name, "status": "passed", "exit_code": 0, "stderr_tail": ""}
        quality_gate._finalize_step(step, spec, [], [], [], [])
        code, first_failure = quality_gate.final_failure_verdict(
            [step], step_exit_code(step), [], [], changed_during_run=False,
            mode="domain", run_budget=quality_gate.MODE_BUDGETS["domain"],
        )
        return code, step, first_failure

    def test_domain_rejects_invalid_generation_lifecycle_evidence(self):
        for variant in (
            "bad-retention",
            "bad-hash",
            "bad-output-identity",
            "short-phase-chain",
            "missing-dialog",
            "missing-cancel",
            "duplicate-record-request",
            "destructive-api",
        ):
            with self.subTest(variant=variant):
                self.evidence_dir = Path(self.temp_dir.name) / f"evidence-{variant}"
                write_generation_lifecycle_runner(self.fixture_root, variant=variant)
                code, step, first_failure = self.evaluate_evidence("generation-lifecycle-evidence")
                self.assertEqual(code, 1, step["stderr_tail"])
                self.assertEqual(first_failure["name"], "generation-lifecycle-evidence")
                self.assertEqual(step["status"], "failed")

    def test_domain_rejects_native_evidence_without_project_roundtrip(self):
        self.write_native_browser_fixture(variant="missing-roundtrip")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("project roundtrip", browser_step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_without_full_project_scope(self):
        self.write_native_browser_fixture(variant="incomplete-project-scope")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("project roundtrip", browser_step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_without_native_duplicate_operation(self):
        self.write_native_browser_fixture(variant="missing-native-duplicate")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("native project journey", browser_step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_without_augmented_browser_save(self):
        self.write_native_browser_fixture(variant="missing-augmented-save")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("native project journey", browser_step["stderr_tail"])

    def test_domain_rejects_context_menu_evidence_from_the_wrong_mouse_button(self):
        self.write_native_browser_fixture(variant="wrong-context-button")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("mouse button", browser_step["stderr_tail"])

    def test_domain_rejects_click_evidence_without_a_browser_semantic_event(self):
        self.write_native_browser_fixture(variant="missing-semantic-click")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("click evidence", browser_step["stderr_tail"])

    def test_domain_rejects_drag_evidence_released_outside_the_selected_element(self):
        self.write_native_browser_fixture(variant="drag-outside-selector")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("selected element", browser_step["stderr_tail"])

    def test_domain_accepts_drag_evidence_from_a_child_to_its_selected_parent(self):
        self.write_native_browser_fixture(variant="drag-child-to-parent")

        code, step, _ = self.evaluate_evidence()

        self.assertEqual(code, 0, step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_with_tampered_export_media(self):
        self.write_native_browser_fixture(variant="tampered-export-media")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("export package", browser_step["stderr_tail"])

    def test_domain_rejects_native_evidence_without_image_organization(self):
        self.write_native_browser_fixture(variant="missing-image-organization")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("image organization", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_changed_original_media(self):
        self.write_native_browser_fixture(variant="tampered-image-media")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("original media", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_without_native_group_shortcut(self):
        self.write_native_browser_fixture(variant="missing-native-group")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("native image organization", browser_step["stderr_tail"])

    def test_domain_rejects_image_upload_without_focus_on_the_file_input(self):
        self.write_native_browser_fixture(variant="wrong-upload-focus")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("upload focus", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_a_missing_group_ratio(self):
        self.write_native_browser_fixture(variant="missing-group-ratio")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("group ratios", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_a_duplicate_ungroup_ratio(self):
        self.write_native_browser_fixture(variant="duplicate-ungroup-ratio")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("ungroup ratio identities", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_a_tampered_reopened_hash(self):
        self.write_native_browser_fixture(variant="tampered-reopened-hash")

        code, browser_step, _ = self.evaluate_evidence()

        self.assertEqual(code, 1, browser_step["stderr_tail"])
        self.assertIn("reopened media hashes", browser_step["stderr_tail"])

    def test_failed_native_browser_evidence_validates_diagnostic_paths(self):
        root = Path(self.temp_dir.name) / "failed-browser"
        viewport = root / "viewport-800x600"
        viewport.mkdir(parents=True)
        (viewport / "operations.jsonl").write_text("", encoding="utf-8")
        (viewport / "console-errors.json").write_text("[]\n", encoding="utf-8")
        (viewport / "requests.json").write_text("[]\n", encoding="utf-8")
        failure = {
            "viewport": [800, 600],
            "reason": "browser failed",
            "failure_screenshot": None,
            "operations": "viewport-800x600/operations.jsonl",
            "console_errors": "viewport-800x600/console-errors.json",
            "requests": "viewport-800x600/requests.json",
        }
        manifest = {
            "schema_version": 1,
            "journey": "core-canvas",
            "status": "failed",
            "first_failure": failure,
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        captured = failed_native_browser_evidence(root)

        self.assertEqual(captured["status"], "captured")
        self.assertEqual(captured["first_failure"]["reason"], "browser failed")

        manifest["first_failure"]["operations"] = "../escaped.jsonl"
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        invalid = failed_native_browser_evidence(root)

        self.assertEqual(invalid["status"], "invalid")
        self.assertIn("outside", invalid["artifact_error"])

    def test_domain_rejects_untrusted_or_out_of_root_native_browser_evidence(self):
        variants = (
            "untrusted",
            "outside-root",
            "bad-theme",
            "missing-hit",
            "wrong-focus",
            "truncated-png",
            "blank-png",
        )
        for name in variants:
            with self.subTest(name=name):
                self.evidence_dir = Path(self.temp_dir.name) / f"evidence-{name}"
                self.write_native_browser_fixture(variant=name)

                code, step, first_failure = self.evaluate_evidence()

                self.assertEqual(code, 1, step["stderr_tail"])
                self.assertEqual(first_failure["name"], "native-browser-evidence")


class GatePythonGuardIntegrationTests(GateFixture):
    def test_caught_external_network_and_provider_attempts_still_fail_the_gate(self):
        provider_executable = Path(self.temp_dir.name) / "provider-fixture"
        disguised_browser = Path(self.temp_dir.name) / "chrome.exe"
        disguised_browser.write_text("not a trusted browser executable\n", encoding="utf-8")
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import asyncio\n"
            "import os\n"
            "import socket\n"
            "import subprocess\n"
            "import sys\n"
            "import unittest\n\n"
            "class GuardContract(unittest.TestCase):\n"
            "    def test_caught_attempts(self):\n"
            "        try:\n"
            "            socket.create_connection(('203.0.113.1', 443), timeout=0.01)\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            socket.gethostbyname('203.0.113.3')\n"
            "        except Exception:\n"
            "            pass\n"
            "        datagram = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "        try:\n"
            "            datagram.sendto(b'blocked', ('198.51.100.1', 9))\n"
            "        except Exception:\n"
            "            pass\n"
            "        finally:\n"
            "            datagram.close()\n"
            "        async def attempt_asyncio_connection():\n"
            "            loop = asyncio.get_running_loop()\n"
            "            async def resolve_without_network(*args, **kwargs):\n"
            "                return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('203.0.113.2', 9))]\n"
            "            async def stop_before_connect(*args, **kwargs):\n"
            "                raise OSError('safe pre-network stop')\n"
            "            loop._ensure_resolved = resolve_without_network\n"
            "            loop._connect_sock = stop_before_connect\n"
            "            try:\n"
            "                await asyncio.open_connection('203.0.113.2', 9)\n"
            "            except Exception:\n"
            "                pass\n"
            "        asyncio.run(attempt_asyncio_connection())\n"
            "        try:\n"
            f"            subprocess.run([{str(provider_executable)!r}, '--version'], check=False)\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            subprocess.run('echo shell-bypass', shell=True, check=False)\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            subprocess.run(['cmd.exe', '/c', 'exit', '0'], check=False)\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            subprocess.run([sys.executable, '-S', '-c', 'print(1)'], check=False)\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            subprocess.run([os.fsencode(sys.executable), b'-S', b'-c', b'print(1)'], check=False)\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            f"            subprocess.run([{str(disguised_browser)!r}], check=False)\n"
            "        except Exception:\n"
            "            pass\n",
            encoding="utf-8",
        )

        result = self.run_gate(
            CODEX_BIN=str(provider_executable),
            PATH=str(disguised_browser.parent) + os.pathsep + os.environ["PATH"],
        )

        report = self.report()
        self.assertEqual(
            result.returncode,
            1,
            result.stdout + result.stderr + json.dumps(report, ensure_ascii=False, indent=2),
        )
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(report["first_failure"]["name"], "surface-contract")
        self.assertEqual(
            {event["kind"] for event in report["guard_events"]},
            {"network", "provider-executable", "subprocess-executable"},
        )
        subprocess_event_targets = [
            event["target"]
            for event in report["guard_events"]
            if event["kind"] == "subprocess-executable"
        ]
        subprocess_targets = set(subprocess_event_targets)
        self.assertIn(Path(sys.executable).name.lower(), subprocess_targets)
        self.assertGreaterEqual(
            subprocess_event_targets.count(Path(sys.executable).name.lower()),
            2,
        )
        self.assertIn("chrome.exe", subprocess_targets)
        network_targets = {
            event["target"]
            for event in report["guard_events"]
            if event["kind"] == "network"
        }
        self.assertIn("198.51.100.1", network_targets)
        self.assertIn("203.0.113.2", network_targets)
        self.assertIn("203.0.113.3", network_targets)

    def test_python_child_cannot_remove_guard_with_a_custom_environment(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import subprocess\n"
            "import sys\n"
            "import unittest\n\n"
            "class ChildEnvironmentContract(unittest.TestCase):\n"
            "    def test_sitecustomize_is_reinjected(self):\n"
            "        child_environment = {\n"
            "            key: os.environ[key]\n"
            "            for key in ('PATH', 'SYSTEMROOT', 'WINDIR')\n"
            "            if key in os.environ\n"
            "        }\n"
            "        result = subprocess.run(\n"
            "            [sys.executable, '-c', \"import sys; raise SystemExit(7 if 'sitecustomize' in sys.modules else 0)\"],\n"
            "            env=child_environment,\n"
            "            check=False,\n"
            "        )\n"
            "        self.assertEqual(result.returncode, 7)\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GateNodeGuardIntegrationTests(GateFixture):
    def test_node_boundary_event_carries_process_identity(self):
        # #232：node 钩子的 record() 只写 kind/target，不带 pid——所以 node 触发的
        # 边界事件在证据里永远归属不到发起进程（node 是画布语法测试、Playwright
        # 驱动的宿主）。Python 钩子早就带 pid/ppid/exe，两边必须对称；不带
        # 目标地址也不行：只知道「node 试过 net.connect」对不了账。
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import subprocess\n"
            "import unittest\n\n"
            "class NodeBoundaryIdentityContract(unittest.TestCase):\n"
            "    def test_denied_socket_reports_process(self):\n"
            "        result = subprocess.run(\n"
            "            ['node', '-e',\n"
            "             \"require('node:net').connect({host: 'api.example.com', port: 443})\"],\n"
            "            capture_output=True,\n"
            "            text=True,\n"
            "            check=False,\n"
            "        )\n"
            "        self.assertNotEqual(result.returncode, 0)\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertNotEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        report = self.report()
        node_events = [
            event
            for event in report["guard_events"]
            if str(event.get("target", "")).startswith("node:")
        ]
        self.assertTrue(node_events, "fixture 没产生 node 边界事件，这条钉是空的")
        for event in node_events:
            self.assertTrue(event.get("pid"), f"node 边界事件没带 pid：{event}")
            self.assertTrue(event.get("ppid"), f"node 边界事件没带 ppid：{event}")
            self.assertTrue(event.get("exe"), f"node 边界事件没带可执行路径：{event}")
        self.assertTrue(
            any("api.example.com" in str(event.get("target", "")) for event in node_events),
            f"node 边界事件没记下请求的目标：{node_events}",
        )
        by_method = (report.get("guard_attribution") or {}).get("by_method") or {}
        self.assertGreaterEqual(
            by_method.get("process-tree", 0),
            1,
            f"node 边界事件没按进程树归属：{by_method}",
        )

    def test_node_process_spawn_is_blocked(self):
        cases = {
            'default_environment': (
                "import subprocess\n"
                "import unittest\n\n"
                "class NodeBoundaryContract(unittest.TestCase):\n"
                "    def test_child_process_is_denied(self):\n"
                "        result = subprocess.run(\n"
                "            ['node', '-e', \"require('node:child_process').spawnSync('cmd.exe')\"],\n"
                "            capture_output=True,\n"
                "            text=True,\n"
                "            check=False,\n"
                "        )\n"
                "        self.assertNotEqual(result.returncode, 0)\n"
            ),
            'custom_child_environment': (
                "import os\n"
                "import subprocess\n"
                "import unittest\n\n"
                "class NodeGuardEnvironmentContract(unittest.TestCase):\n"
                "    def test_custom_environment_cannot_remove_the_preload(self):\n"
                "        child_environment = {\n"
                "            key: os.environ[key]\n"
                "            for key in ('PATH', 'SYSTEMROOT', 'WINDIR')\n"
                "            if key in os.environ\n"
                "        }\n"
                "        child_environment['NODE_OPTIONS'] = ''\n"
                "        result = subprocess.run(\n"
                "            ['node', '-e', \"require('node:child_process').spawnSync('cmd.exe')\"],\n"
                "            env=child_environment,\n"
                "            capture_output=True,\n"
                "            text=True,\n"
                "            check=False,\n"
                "        )\n"
                "        self.assertNotEqual(result.returncode, 0)\n"
            ),
        }
        contract = self.fixture_root / 'tests' / 'test_product_surface_behavior.py'
        evidence_root = self.evidence_dir
        for case, fixture_source in cases.items():
            with self.subTest(case=case):
                self.evidence_dir = evidence_root / case
                contract.write_text(fixture_source, encoding="utf-8")
                result = self.run_gate()

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertTrue(
                    any(
                        event.get("kind") == "subprocess-executable"
                        and str(event.get("target") or "").startswith("node:spawnSync:pid=")
                        for event in self.report()["guard_events"]
                    )
                )


class GateNodeLaunchIntegrationTests(GateFixture):
    def test_trusted_node_launch_does_not_execute_inherited_overrides(self):
        preload = Path(self.temp_dir.name) / "node-preload.js"
        preload_marker = Path(self.temp_dir.name) / "node-preload-ran"
        preload.write_text(
            f"require('fs').writeFileSync({str(preload_marker)!r}, 'ran');\n",
            encoding="utf-8",
        )

        fake_bin = Path(self.temp_dir.name) / "fake-bin"
        fake_bin.mkdir()
        path_marker = Path(self.temp_dir.name) / "fake-node-ran"
        if os.name == "nt":
            fake_node = fake_bin / "node.exe"
            fake_node.write_bytes(Path(sys.executable).read_bytes())
        else:
            fake_node = fake_bin / "node"
            fake_node.write_text(f"#!/bin/sh\nprintf ran > {path_marker!s}\n", encoding="utf-8")
            fake_node.chmod(0o755)

        result = self.run_gate(
            NODE_OPTIONS=f'--require={preload}',
            PATH=str(fake_bin) + os.pathsep + os.environ['PATH'],
        )

        with self.subTest(case='inherited_node_options'):
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(preload_marker.exists())
        with self.subTest(case='inherited_path'):
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(path_marker.exists())


class GateProcessRuleTests(GateTempPaths):
    def test_transient_child_exit_during_grace_period_is_not_reported_as_a_leak(self):
        guard_events = self.evidence_dir / "guard-events.jsonl"
        guard_events.parent.mkdir(parents=True, exist_ok=True)
        with (
            mock.patch("tools.quality_gate.wait_for_process_exit", return_value=True),
            mock.patch("tools.quality_gate.stop_process_id") as stop_process,
        ):
            stop_leaked_children(
                [{"pid": 43210, "target": "chrome.exe"}],
                guard_events,
            )

        stop_process.assert_not_called()
        self.assertFalse(guard_events.exists())

    def test_reused_child_pid_with_a_different_image_is_not_reported_as_a_leak(self):
        guard_events = self.evidence_dir / "guard-events.jsonl"
        guard_events.parent.mkdir(parents=True, exist_ok=True)
        with (
            mock.patch("tools.quality_gate.wait_for_process_exit", return_value=False),
            mock.patch(
                "tools.quality_gate.process_matches_recorded_target",
                return_value=False,
            ),
            mock.patch("tools.quality_gate.stop_process_id") as stop_process,
        ):
            stop_leaked_children(
                [{"pid": 43210, "target": "git.exe"}],
                guard_events,
            )

        stop_process.assert_not_called()
        self.assertFalse(guard_events.exists())

    def test_persistent_child_after_grace_period_is_stopped_and_reported(self):
        guard_events = self.evidence_dir / "guard-events.jsonl"
        guard_events.parent.mkdir(parents=True, exist_ok=True)
        with (
            mock.patch("tools.quality_gate.wait_for_process_exit", return_value=False),
            mock.patch(
                "tools.quality_gate.process_matches_recorded_target",
                return_value=True,
            ),
            mock.patch("tools.quality_gate.stop_process_id") as stop_process,
        ):
            stop_leaked_children(
                [{"pid": 43210, "target": "chrome.exe"}],
                guard_events,
            )

        stop_process.assert_called_once_with(43210)
        self.assertEqual(
            json.loads(guard_events.read_text(encoding="utf-8").strip()),
            {"kind": "subprocess-leak", "target": "chrome.exe"},
        )

    def test_browser_children_get_a_longer_shutdown_grace_than_other_children(self):
        guard_events = self.evidence_dir / "guard-events.jsonl"
        guard_events.parent.mkdir(parents=True, exist_ok=True)
        with mock.patch("tools.quality_gate.wait_for_process_exit", return_value=True) as wait:
            stop_leaked_children(
                [
                    {"pid": 43210, "target": "chrome.exe"},
                    {"pid": 43211, "target": "msedge.exe"},
                    {"pid": 43212, "target": Path(sys.executable).name.lower()},
                ],
                guard_events,
            )

        wait.assert_has_calls(
            [
                mock.call(43210, timeout=8.0),
                mock.call(43211, timeout=8.0),
                mock.call(43212, timeout=1.0),
            ]
        )
        self.assertFalse(guard_events.exists())

    def test_node_children_get_the_same_long_shutdown_grace(self):
        # #226：node.exe 的退出尾巴（V8 卸载、stdout 冲刷）在并行负载下会超过 1s。
        # 1s 窗口内没退完就被判 S0 subprocess-leak，守卫还会 taskkill 一个正在
        # 正常退出的测试子进程，而红会落到当时正在跑的无关 shard 上。
        guard_events = self.evidence_dir / "guard-events.jsonl"
        guard_events.parent.mkdir(parents=True, exist_ok=True)
        with mock.patch("tools.quality_gate.wait_for_process_exit", return_value=True) as wait:
            stop_leaked_children(
                [
                    {"pid": 43220, "target": "node.exe"},
                    {"pid": 43221, "target": "node"},
                    {"pid": 43222, "target": "cmd.exe"},
                ],
                guard_events,
            )
        wait.assert_has_calls(
            [
                mock.call(43220, timeout=8.0),
                mock.call(43221, timeout=8.0),
                mock.call(43222, timeout=1.0),
            ]
        )
        self.assertFalse(guard_events.exists())

    def test_wait_for_process_exit_is_bounded_and_observes_natural_exit(self):
        with (
            mock.patch(
                "tools.quality_gate.process_is_running",
                side_effect=[True, True, False],
            ),
            mock.patch(
                "tools.quality_gate.time.monotonic",
                side_effect=[10.0, 10.1, 10.2],
            ),
            mock.patch("tools.quality_gate.time.sleep") as sleep,
        ):
            self.assertTrue(wait_for_process_exit(43210, timeout=1.0, poll_interval=0.05))
        self.assertEqual(sleep.call_count, 2)

        with (
            mock.patch("tools.quality_gate.process_is_running", return_value=True),
            mock.patch(
                "tools.quality_gate.time.monotonic",
                side_effect=[20.0, 20.1, 21.1],
            ),
            mock.patch("tools.quality_gate.time.sleep") as sleep,
        ):
            self.assertFalse(wait_for_process_exit(43210, timeout=1.0, poll_interval=0.05))
        self.assertEqual(sleep.call_count, 1)


class GateRetryIntegrationTests(GateFixture):
    def test_late_retry_evidence_cannot_arrive_after_a_green_step_decision(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import subprocess\n"
            "import sys\n"
            "import unittest\n\n"
            "class LateRetryContract(unittest.TestCase):\n"
            "    def test_spawns_delayed_retry_writer(self):\n"
            "        child_code = (\n"
            "            \"import os, time; time.sleep(2); \"\n"
            "            \"path = os.environ['FIREFLY_QUALITY_GATE_RETRY_EVENTS']; \"\n"
            "            \"open(path, 'a', encoding='utf-8').write(\"\n"
            "            \"'{\\\"attempt\\\": 2, \\\"reason\\\": \\\"late retry\\\"}\\\\n')\"\n"
            "        )\n"
            "        subprocess.Popen(\n"
            "            [sys.executable, '-c', child_code],\n"
            "            stdout=subprocess.DEVNULL,\n"
            "            stderr=subprocess.DEVNULL,\n"
            "            cwd=os.path.dirname(sys.executable),\n"
            "        )\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(
            (self.evidence_dir / "report.json").is_file(),
            result.stdout + result.stderr,
        )
        report = self.report()
        self.assertIn(
            {"kind": "subprocess-leak", "target": Path(sys.executable).name.lower(), "attributed_by": "unattributed"},
            report["guard_events"],
        )
        self.assertEqual(report["first_failure"]["name"], "surface-contract")


class GateProcessIntegrationTests(GateFixture):
    def test_mode_budget_timeout_is_red_and_uses_exit_code_124(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import time\n"
            "import unittest\n\n"
            "class SlowContract(unittest.TestCase):\n"
            "    def test_contract(self):\n"
            "        time.sleep(5)\n",
            encoding="utf-8",
        )

        result = self.run_gate(
            FIREFLY_TEST_MODE="1",
            FIREFLY_QUALITY_GATE_TEST_BUDGET_SECONDS="1",
        )

        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["budget_seconds"], 30)
        self.assertEqual(report["effective_budget_seconds"], 1)
        self.assertEqual(report["first_failure"]["status"], "timed-out")
        # 判据是「门在 1 秒预算处杀掉子进程」而不是「放它跑完 5 秒 sleep」。
        # 原先写死 <3 秒：trial/release 把步骤跑成有界并行组（tools/quality_gate.py:493、
        # :3275），fixture 子门要跟同组其他步骤抢 CPU，实测一次 3.015 秒就红了。
        # 所以阈值绑到夹具自身的 5 秒 sleep：跑完必然 >=5 秒，提前杀掉则远小于它。
        self.assertLess(report["duration_seconds"], 5)
        # #361：报告要说清楚本轮子进程到底挂进作业了没有。挂不上不是错（受限环境），
        # 但不能闭着不说——那样只有主动杀树在兜底，没人知道。
        lifecycle = report["process_lifecycle"]
        self.assertIn("Job Object", lifecycle["job_object"])
        self.assertIsInstance(lifecycle["scanned"], int)
        self.assertIsInstance(lifecycle["reaped"], list)
        # #361：临时目录删不动（还有进程钉着）要能在报告里看见，但不翻 verdict。
        self.assertIn("workspace_leftovers", lifecycle)
        self.assertIsInstance(lifecycle["workspace_leftovers"], list)

    def test_timed_out_step_leaves_no_living_grandchild(self):
        """#361 验收：步骤超时后，它生的孙进程也不能活着。

        以前只会杀步骤子进程本身（孙进程被丢回桌面会话）。孙进程用心跳文件证明生死：
        活着就每 0.2 秒追一个字节，门退出后再读两次，大小不再长就是死了。
        （不能用 pid：pid 会被回收，一个进过坟的 pid 也查得到）。
        """
        heartbeat = Path(self.temp_dir.name) / "leak-heartbeat"
        child_code = (
            "import time\n"
            "from pathlib import Path\n"
            f"path = Path({str(heartbeat)!r})\n"
            "for _ in range(100):\n"
            "    with path.open('a') as handle:\n"
            "        handle.write('x')\n"
            "    time.sleep(0.2)\n"
        )
        # 故意把漏网进程放在 fast 的**第一个** unittest 步骤（javascript-syntax 跑的就是
        # tests/test_canvas_frontend_sources.py，见 setUp 里的桩）：预算一到它一定已经在跑，
        # 不会出现「还没轮到这一步就超时」的飘。桩自己 20 秒后会退出，不会漏在后台。
        (self.fixture_root / "tests" / "test_canvas_frontend_sources.py").write_text(
            "import subprocess, sys, time\n"
            "import unittest\n\n"
            f"CHILD_CODE = {child_code!r}\n\n"
            "class LeakySlowContract(unittest.TestCase):\n"
            "    def test_spawn_then_sleep(self):\n"
            "        subprocess.Popen([sys.executable, '-c', CHILD_CODE])\n"
            "        time.sleep(20)\n",
            encoding="utf-8",
        )

        result = self.run_gate(
            FIREFLY_TEST_MODE="1",
            FIREFLY_QUALITY_GATE_TEST_BUDGET_SECONDS="3",
        )
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "javascript-syntax")
        self.assertEqual(report["first_failure"]["status"], "timed-out")

        time.sleep(1.0)
        self.assertTrue(heartbeat.exists(), "孙进程没起来，测试本身有问题")
        first = heartbeat.stat().st_size
        time.sleep(1.5)
        second = heartbeat.stat().st_size
        self.assertEqual(
            first,
            second,
            "门退出了，超时步骤的孙进程还在写心跳文件——进程树没杀干",
        )


class GuardAttributionConsistencyTests(unittest.TestCase):
    """#230：判据不能取决于「事件怎么分批读」。

    同一条事件在 step 视图与全量视图里只能有一个 attributed_by；宿主噪声不能
    因为「另一个 step 里产品请求过同名目标」就被改名成产品归属。
    """

    PRODUCT_REQUEST = {"kind": "proxy-request", "target": "play.googleapis.com", "pid": 7}
    HOST_NOISE = {"kind": "host-tool-network", "target": "play.googleapis.com"}

    def test_host_noise_stays_domain_fallback_even_if_the_tree_requested_the_target(self):
        merged = classify_guard_events([dict(self.PRODUCT_REQUEST), dict(self.HOST_NOISE)])
        labels = {event["kind"]: event["attributed_by"] for event in merged}
        self.assertEqual(
            labels.get("host-tool-network"),
            "domain-fallback",
            f'宿主噪声被升级成产品归属：{merged}',
        )
        self.assertEqual(labels.get("proxy-request"), "process-tree")
        blocking = [event["kind"] for event in blocking_guard_events([dict(self.PRODUCT_REQUEST), dict(self.HOST_NOISE)])]
        self.assertEqual(
            blocking,
            ["proxy-request"],
            "判红应该只靠带 pid 的那条，不该靠给噪声改名",
        )

    def test_classification_is_identical_per_step_or_merged(self):
        per_step = classify_guard_events([dict(self.PRODUCT_REQUEST)]) + classify_guard_events([dict(self.HOST_NOISE)])
        merged = classify_guard_events([dict(self.PRODUCT_REQUEST), dict(self.HOST_NOISE)])
        self.assertEqual(
            [event["attributed_by"] for event in per_step],
            [event["attributed_by"] for event in merged],
            "同一条事件因为分批不同而得到不同判据",
        )

    def test_findings_keep_the_step_that_actually_violated(self):
        steps = [
            {
                "name": "surface-contract",
                "status": "passed",
                "guard_events": [{"kind": "network", "target": "203.0.113.1", "pid": 11}],
            },
            {
                "name": "project-contract",
                "status": "passed",
                "guard_events": [{"kind": "network", "target": "203.0.113.1", "pid": 22}],
            },
        ]
        merged = [dict(event) for step in steps for event in step["guard_events"]]
        findings = [finding for finding in build_severity_findings(steps, merged, []) if finding["severity"] == "S0"]
        self.assertEqual(
            sorted(finding.get("step") for finding in findings),
            ["project-contract", "surface-contract"],
            f'两条同名目标的违规没能各自归到自己的 step：{findings}',
        )


class ProcessLifecycleHooksTests(unittest.TestCase):
    """#361：门被打断时，正在跑的步骤子进程连同它生的 node / chrome 也要一起走。

    Job Object 在受限环境里挂不上（GetLastError=5），那时只剩这条登记+杀树的路。
    """

    def test_exit_hooks_are_installed_on_import(self):
        self.assertTrue(EXIT_HOOKS_INSTALLED, "进模块就要把 atexit / 信号钩子装好")

    def test_terminate_active_step_processes_kills_whole_tree(self):
        if os.environ.get("FIREFLY_QUALITY_GATE_SELF_TEST") == "1":
            self.skipTest("门自测沙箱里守卫会 deny 无关子进程（sitecustomize 的 Popen 包装），"
                          "这条要验真实现场，只能在门外跑")
        with tempfile.TemporaryDirectory(prefix="firefly-lifecycle-hook-") as temporary:
            child = Path(temporary) / "child.py"
            child.write_text("import time\nwhile True:\n    time.sleep(0.2)\n", encoding="utf-8")
            parent = Path(temporary) / "parent.py"
            parent.write_text(
                "import subprocess, sys, time\n"
                "proc = subprocess.Popen([sys.executable, sys.argv[1]])\n"
                "print(proc.pid, flush=True)\n"
                "while True:\n    time.sleep(0.2)\n",
                encoding="utf-8",
            )
            tree_root = subprocess.Popen(
                [sys.executable, str(parent), str(child)],
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            _ACTIVE_STEP_PROCESSES.add(tree_root)
            try:
                grandchild_pid = int((tree_root.stdout.readline() or "").strip())
                self.assertGreater(grandchild_pid, 0)
                self.assertTrue(process_alive(grandchild_pid), "孙进程应该还在跑")

                terminate_active_step_processes()

                tree_root.wait(timeout=15)
                deadline = time.monotonic() + 15
                while process_alive(grandchild_pid) and time.monotonic() < deadline:
                    time.sleep(0.2)
                self.assertFalse(
                    process_alive(grandchild_pid),
                    f"门退出时没把孙进程（pid {grandchild_pid}）带走——就是 2026-11-14 那 26 个残留的成因",
                )
            finally:
                _ACTIVE_STEP_PROCESSES.discard(tree_root)
                if tree_root.poll() is None:
                    kill_process_tree(tree_root)


class ContinueOnFailureExitCodeTests(unittest.TestCase):
    """#370：门不再中途 break 后，多个红共存，退出码按「最严重」给。

    严重度：超时（124）> 前置条件不成立（2）> 步骤红（1）。否则后面的绿步会把
    前面的红盖掉，或一个普通失败遮掉超时这个最该优先看的事实。
    """

    def test_merge_never_downgrades(self):
        self.assertEqual(merge_gate_exit_code(0, 1), 1)
        self.assertEqual(merge_gate_exit_code(1, 0), 1)
        self.assertEqual(merge_gate_exit_code(2, 1), 2)
        self.assertEqual(merge_gate_exit_code(1, 2), 2)
        self.assertEqual(merge_gate_exit_code(2, 124), 124)
        self.assertEqual(merge_gate_exit_code(124, 2), 124)

    def test_unknown_codes_count_as_a_plain_failure(self):
        self.assertEqual(merge_gate_exit_code(0, 3), 3)
        self.assertEqual(merge_gate_exit_code(124, 3), 124)

    def test_step_exit_code_maps_status_to_the_right_severity(self):
        self.assertEqual(step_exit_code({"status": "passed"}), 0)
        self.assertEqual(step_exit_code({"status": "failed"}), 1)
        self.assertEqual(step_exit_code({"status": "timed-out"}), 124)
        self.assertEqual(step_exit_code({"status": "precondition-failed"}), 2)


if __name__ == "__main__":
    unittest.main()
