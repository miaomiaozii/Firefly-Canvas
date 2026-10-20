import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.complete_generation_lifecycle_fixture import write_complete_generation_lifecycle_runner
from tests.composer_flow_fixture import write_composer_flow_runner
from tests.edge_compatibility_fixture import write_edge_compatibility_runner
from tests.generation_lifecycle_fixture import write_generation_lifecycle_runner
from tests.long_content_fixture import write_long_content_runner
from tests.project_linked_generation_fixture import write_project_linked_generation_runner
from tests.ui_integrity_fixture import write_ui_integrity_runner, write_valid_ui_integrity_artifact
from tools.native_input_evidence import double_click_evidence, selector_owner_click_evidence
from tools.quality_gate import (
    BROWSER_EVIDENCE_STEP_NAMES,
    MODE_STEPS,
    STEP_DEFINITIONS,
    UNIT_CONTRACT_STEP_NAMES,
    compute_unit_contract_shards,
    discover_unit_contract_modules,
    expected_native_focus,
    expand_unit_contract_steps,
    failed_native_browser_evidence,
    stop_leaked_children,
    trusted_node_executable,
    unit_contract_modules_for_shards,
    unit_contract_slot_for_shard,
    wait_for_process_exit,
)
from tools.ui_integrity_evidence import accept_baseline


ROOT = Path(__file__).resolve().parents[1]
QUALITY_GATE = ROOT / "tools" / "quality_gate.py"


class QualityGateTests(unittest.TestCase):
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
        self.assertIn("intentional unit shard failure", shard["stderr_tail"])

    def test_unit_contract_shard_timeout_blocks_trial(self):
        (self.fixture_root / "tests" / "test_quality_gate.py").write_text(
            "import time, unittest\n"
            "class SlowQualityGateContract(unittest.TestCase):\n"
            "    def test_slow(self): time.sleep(20)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()
        # 预算从 1 秒提到 3 秒后，trial 会跑到最后的 browser-tracer；
        # fixture 必须提供 conversation_browser.py 桩，否则它会以“文件不存在”的 exit 2 先把门弄红。
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

        # #167 后 javascript-syntax 是一个真的 unittest 步骤（约 1 秒），
        # 预算必须给到 3 秒，否则超时的是语法步骤而不是分片。
        shards = compute_unit_contract_shards(self.fixture_root)
        shard_name = next(
            name
            for name, targets in shards.items()
            if any("test_slow" in target for target in targets)
        )

        result = self.run_gate(
            mode="trial",
            FIREFLY_TEST_MODE="1",
            FIREFLY_QUALITY_GATE_TEST_BUDGET_SECONDS="3",
        )

        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], shard_name)
        self.assertEqual(report["first_failure"]["status"], "timed-out")

    def test_javascript_syntax_step_covers_every_canvas_module(self):
        # #167：javascript-syntax 以前只 `node --check static/js/smart-canvas.js`，
        # 拆出去的 canvas-*.js 语法错误在 fast/domain 完全漏网。
        command = STEP_DEFINITIONS["javascript-syntax"](sys.executable)
        joined = " ".join(command)
        self.assertIn("tests.test_canvas_frontend_sources", joined)
        self.assertNotIn("static/js/smart-canvas.js", joined)
        for mode in ("fast", "domain", "trial", "release"):
            self.assertIn("javascript-syntax", MODE_STEPS[mode])

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

    def test_wired_canvas_browser_runners_resolve_their_repo_root(self):
        # 门在临时 workspace 里执行步骤；runner 里硬编码的仓库根会读到工作区外的源码，
        # 在非本机（例如 CI）上直接带不动。
        repo_root = Path(__file__).resolve().parents[1]
        for name in (
            "canvas-boundaries-evidence",
            "video-failure-evidence",
            "node-flow-cascade-evidence",
            "fly-fixes-evidence",
        ):
            command = STEP_DEFINITIONS[name](sys.executable)
            runner = repo_root / command[1]
            self.assertTrue(runner.is_file(), f"missing {command[1]}")
            self.assertNotIn("Firefly-Canvas", runner.read_text(encoding="utf-8"))
            self.assertIn("Path(__file__).resolve().parents[1]", runner.read_text(encoding="utf-8"))

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

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-quality-gate-test-")
        self.fixture_root = Path(self.temp_dir.name) / "repo"
        self.evidence_dir = Path(self.temp_dir.name) / "evidence"
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
        # #167：新接入的两个画布浏览器 runner 在 fixture 里用最小桩，
        # 和 excel_import_browser.py 的桩同一套路（门只验接线，不验产品行为）。
        # #185：node_flow 级联与 fly_fixes 两个 runner 同一套路接入。
        for runner_name in (
            "canvas_boundaries_browser.py",
            "video_failure_browser.py",
            "node_flow_browser.py",
            "fly_fixes_browser.py",
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
        write_generation_lifecycle_runner(self.fixture_root)
        write_complete_generation_lifecycle_runner(self.fixture_root)
        write_composer_flow_runner(self.fixture_root)
        write_long_content_runner(self.fixture_root)
        write_project_linked_generation_runner(self.fixture_root)
        write_ui_integrity_runner(self.fixture_root)
        write_edge_compatibility_runner(self.fixture_root)
        self.ui_integrity_baselines = Path(self.temp_dir.name) / "ui-integrity-baselines"
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.fixture_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

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
                 "    for name in ('01-loaded.png','02-restored.png','03-organized.png','04-ungrouped.png'):\n"
                "        image = Image.new('RGB', (width, height), (15, 23, 42))\n"
                "        ImageDraw.Draw(image).rectangle((10, 10, 100, 100), fill=(37, 99, 235))\n"
                "        image.save(folder/name, format='PNG')\n"
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

    def test_fast_mode_runs_isolated_syntax_and_contract_steps_and_reports_dirty_tree(self):
        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["mode"], "fast")
        self.assertEqual(report["budget_seconds"], 30)
        self.assertEqual(report["verdict"], "passed")
        self.assertEqual([step["name"] for step in report["steps"]], ["javascript-syntax", "excel-api-smoke", "surface-contract"])
        self.assertTrue(all(step["status"] == "passed" for step in report["steps"]))
        self.assertTrue(report["dirty_worktree"]["before"]["dirty"])
        self.assertFalse(report["dirty_worktree"]["changed_during_run"])
        self.assertEqual(Path(report["evidence_dir"]), self.evidence_dir.resolve())
        self.assertIn("final_exit_code", report)

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

    def test_domain_rejects_invalid_generation_lifecycle_evidence(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()
        for variant in (
            "bad-retention",
            "bad-hash",
            "bad-output-identity",
            "short-phase-chain",
            "missing-dialog",
            "destructive-api",
        ):
            with self.subTest(variant=variant):
                self.evidence_dir = Path(self.temp_dir.name) / f"evidence-{variant}"
                write_generation_lifecycle_runner(self.fixture_root, variant=variant)
                result = self.run_gate("domain")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                report = self.report()
                self.assertEqual(report["first_failure"]["name"], "generation-lifecycle-evidence")
                step = next(
                    item
                    for item in report["steps"]
                    if item["name"] == "generation-lifecycle-evidence"
                )
                self.assertEqual(step["status"], "failed")

    def test_domain_rejects_native_evidence_without_project_roundtrip(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-roundtrip")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("project roundtrip", browser_step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_without_full_project_scope(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="incomplete-project-scope")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("project roundtrip", browser_step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_without_native_duplicate_operation(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-native-duplicate")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("native project journey", browser_step["stderr_tail"])

    def test_domain_rejects_project_roundtrip_without_augmented_browser_save(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-augmented-save")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("native project journey", browser_step["stderr_tail"])

    def test_domain_rejects_context_menu_evidence_from_the_wrong_mouse_button(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="wrong-context-button")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("mouse button", browser_step["stderr_tail"])

    def test_domain_rejects_click_evidence_without_a_browser_semantic_event(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-semantic-click")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("click evidence", browser_step["stderr_tail"])

    def test_domain_rejects_drag_evidence_released_outside_the_selected_element(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="drag-outside-selector")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("selected element", browser_step["stderr_tail"])

    def test_domain_accepts_drag_evidence_from_a_child_to_its_selected_parent(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="drag-child-to-parent")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_domain_rejects_project_roundtrip_with_tampered_export_media(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="tampered-export-media")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        browser_step = next(step for step in report["steps"] if step["name"] == "native-browser-evidence")
        self.assertEqual(browser_step["status"], "failed")
        self.assertIn("export package", browser_step["stderr_tail"])

    def test_domain_rejects_native_evidence_without_image_organization(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-image-organization")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("image organization", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_changed_original_media(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="tampered-image-media")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("original media", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_without_native_group_shortcut(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-native-group")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("native image organization", browser_step["stderr_tail"])

    def test_domain_rejects_image_upload_without_focus_on_the_file_input(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="wrong-upload-focus")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("upload focus", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_a_missing_group_ratio(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="missing-group-ratio")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("group ratios", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_a_duplicate_ungroup_ratio(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="duplicate-ungroup-ratio")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("ungroup ratio identities", browser_step["stderr_tail"])

    def test_domain_rejects_image_organization_with_a_tampered_reopened_hash(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture(variant="tampered-reopened-hash")

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        browser_step = next(
            step for step in self.report()["steps"] if step["name"] == "native-browser-evidence"
        )
        self.assertIn("reopened media hashes", browser_step["stderr_tail"])

    def test_domain_hard_denies_non_loopback_access_from_the_provider_contract(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_provider_contract_template.py").write_text(
            "import socket, unittest\n\n"
            "class ProviderContract(unittest.TestCase):\n"
            "    def test_provider_contract_cannot_reach_the_network(self):\n"
            "        socket.create_connection(('provider.invalid', 443), timeout=0.1)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "provider-contract")
        network_events = [event for event in report["guard_events"] if event["kind"] == "network"]
        self.assertTrue(network_events)
        self.assertEqual(network_events[0]["target"], "provider.invalid")

    def test_domain_runs_the_provider_registry_contract(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_provider_contract_registry.py").write_text(
            "import unittest\n\n"
            "class ProviderRegistryContract(unittest.TestCase):\n"
            "    def test_registry_failure_reaches_the_gate(self): self.fail('registry contract sentinel')\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "provider-contract")
        provider_step = next(step for step in report["steps"] if step["name"] == "provider-contract")
        self.assertIn("registry contract sentinel", provider_step["stderr_tail"])

    def test_domain_runs_the_provider_family_contracts(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_provider_contract_families.py").write_text(
            "import unittest\n\n"
            "class ProviderFamilyContract(unittest.TestCase):\n"
            "    def test_family_failure_reaches_the_gate(self): self.fail('family contract sentinel')\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate("domain")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "provider-contract")
        provider_step = next(step for step in report["steps"] if step["name"] == "provider-contract")
        self.assertIn("family contract sentinel", provider_step["stderr_tail"])

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
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
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

                result = self.run_gate("domain")

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                report = self.report()
                self.assertEqual(report["first_failure"]["name"], "native-browser-evidence")

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

    def test_caught_browser_proxy_attempt_is_reported_and_fails_the_gate(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import socket\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class BrowserProxyContract(unittest.TestCase):\n"
            "    def test_caught_browser_request(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "        try:\n"
            "            connection.sendall(\n"
            "                b'CONNECT browser-escape.example:443 HTTP/1.1\\r\\n'\n"
            "                b'Host: browser-escape.example:443\\r\\n\\r\\n'\n"
            "            )\n"
            "            connection.recv(1024)\n"
            "        finally:\n"
            "            connection.close()\n"
            "        method_connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "        try:\n"
            "            method_connection.sendall(\n"
            "                b'PROPFIND http://browser-method.example/resource HTTP/1.1\\r\\n'\n"
            "                b'Host: browser-method.example\\r\\n\\r\\n'\n"
            "            )\n"
            "            method_connection.recv(1024)\n"
            "        finally:\n"
            "            method_connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        network_targets = {
            event["target"]
            for event in self.report()["guard_events"]
            if event["kind"] == "network"
        }
        self.assertIn("browser-escape.example", network_targets)
        self.assertIn("browser-method.example", network_targets)

    def test_blocked_browser_control_plane_noise_is_reported_without_failing_product_gate(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import socket\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class BrowserControlPlaneContract(unittest.TestCase):\n"
            "    def test_blocked_chrome_probe(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "        try:\n"
            "            connection.sendall(\n"
            "                b'CONNECT clients2.google.com:443 HTTP/1.1\\r\\n'\n"
            "                b'Host: clients2.google.com:443\\r\\n\\r\\n'\n"
            "            )\n"
            "            connection.recv(1024)\n"
            "        finally:\n"
            "            connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "browser-background-network", "target": "clients2.google.com"},
            self.report()["guard_events"],
        )

    def test_microsoft_and_bing_control_plane_noise_is_a_background_observation(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import socket\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class EdgeControlPlaneContract(unittest.TestCase):\n"
            "    def test_blocked_edge_probes(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        for target in ('edge.microsoft.com', 'cn.bing.com', 'www.bing.com'):\n"
            "            connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "            try:\n"
            "                connection.sendall(\n"
            "                    f'CONNECT {target}:443 HTTP/1.1\\r\\n'\n"
            "                    f'Host: {target}:443\\r\\n\\r\\n'.encode()\n"
            "                )\n"
            "                connection.recv(1024)\n"
            "            finally:\n"
            "                connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        guard_events = self.report()["guard_events"]
        for target in ("edge.microsoft.com", "cn.bing.com", "www.bing.com"):
            self.assertIn(
                {"kind": "browser-background-network", "target": target},
                guard_events,
            )

    def test_chrome_update_control_plane_noise_is_a_background_observation(self):
        # Chrome 自身的更新/组件检查（update.googleapis.com）属于浏览器控制面，
        # 不是产品发起的网络调用；必须记为 S3 观察，不能当作 S0 隔离守卫违规。
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import socket\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class ChromeUpdateControlPlaneContract(unittest.TestCase):\n"
            "    def test_blocked_chrome_update_probe(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "        try:\n"
            "            connection.sendall(\n"
            "                b'CONNECT update.googleapis.com:443 HTTP/1.1\\r\\n'\n"
            "                b'Host: update.googleapis.com:443\\r\\n\\r\\n'\n"
            "            )\n"
            "            connection.recv(1024)\n"
            "        finally:\n"
            "            connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "browser-background-network", "target": "update.googleapis.com"},
            self.report()["guard_events"],
        )

    def test_google_update_look_alike_host_is_still_a_blocking_network_target(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import socket\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class GoogleUpdateLookAlikeContract(unittest.TestCase):\n"
            "    def test_blocked_look_alike_probe(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "        try:\n"
            "            connection.sendall(\n"
            "                b'CONNECT evil-update.googleapis.com:443 HTTP/1.1\\r\\n'\n"
            "                b'Host: evil-update.googleapis.com:443\\r\\n\\r\\n'\n"
            "            )\n"
            "            connection.recv(1024)\n"
            "        finally:\n"
            "            connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "network", "target": "evil-update.googleapis.com"},
            self.report()["guard_events"],
        )

    def test_microsoft_look_alike_host_is_still_a_blocking_network_target(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import socket\n"
            "import unittest\n"
            "from urllib.parse import urlsplit\n\n"
            "class EdgeLookAlikeContract(unittest.TestCase):\n"
            "    def test_blocked_look_alike_probe(self):\n"
            "        proxy = urlsplit(os.environ['FIREFLY_QUALITY_GATE_BROWSER_PROXY'])\n"
            "        connection = socket.create_connection((proxy.hostname, proxy.port))\n"
            "        try:\n"
            "            connection.sendall(\n"
            "                b'CONNECT evil-microsoft.com:443 HTTP/1.1\\r\\n'\n"
            "                b'Host: evil-microsoft.com:443\\r\\n\\r\\n'\n"
            "            )\n"
            "            connection.recv(1024)\n"
            "        finally:\n"
            "            connection.close()\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            {"kind": "network", "target": "evil-microsoft.com"},
            self.report()["guard_events"],
        )

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

    def test_node_options_cannot_preload_code_before_the_syntax_step(self):
        preload = Path(self.temp_dir.name) / "node-preload.js"
        marker = Path(self.temp_dir.name) / "node-preload-ran"
        preload.write_text(
            f"require('fs').writeFileSync({str(marker)!r}, 'ran');\n",
            encoding="utf-8",
        )

        result = self.run_gate(NODE_OPTIONS=f"--require={preload}")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())

    def test_node_child_process_is_blocked_by_the_mandatory_preload(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
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
            "        self.assertNotEqual(result.returncode, 0)\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(
            any(
                event.get("kind") == "subprocess-executable"
                and str(event.get("target") or "").startswith("node:spawnSync:pid=")
                for event in self.report()["guard_events"]
            )
        )

    def test_trusted_node_cannot_remove_the_guard_with_custom_child_environment(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
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
            "        self.assertNotEqual(result.returncode, 0)\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(
            any(
                event.get("kind") == "subprocess-executable"
                and str(event.get("target") or "").startswith("node:spawnSync:pid=")
                for event in self.report()["guard_events"]
            )
        )

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
        self.assertIn(
            {"kind": "subprocess-executable", "target": Path(sys.executable).name.lower()},
            report["guard_events"],
        )

    # issue #174：并行组里一个步骤产生的 S0 边界事件，绝不能把同组其它步骤改红。
    def write_parallel_boundary_fixture(self) -> dict:
        """fixture：只有 unit-contract-quality-gate 那一步会越界（SELF_TEST=1 才触发），
        越界后它自己仍然 exit 0，所以只有归因正确的步骤应该被改红。"""
        violation = {"kind": "subprocess-executable", "target": Path(sys.executable).name.lower()}
        (self.fixture_root / "tests" / "test_quality_gate.py").write_text(
            "import os\n"
            "import subprocess\n"
            "import sys\n"
            "import unittest\n\n"
            "class ParallelBoundaryContract(unittest.TestCase):\n"
            "    def test_denied_subprocess_is_attributed_to_its_own_step(self):\n"
            "        if os.environ.get('FIREFLY_QUALITY_GATE_SELF_TEST') != '1':\n"
            "            return\n"
            "        try:\n"
            "            subprocess.run([sys.executable, '-c', 'print(1)'], check=False)\n"
            "        except PermissionError:\n"
            "            pass\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()
        return violation

    def test_parallel_group_boundary_event_does_not_redden_sibling_steps(self):
        violation = self.write_parallel_boundary_fixture()
        shards = compute_unit_contract_shards(self.fixture_root)
        offender_name = next(
            name
            for name, targets in shards.items()
            if any(
                "test_denied_subprocess_is_attributed_to_its_own_step" in target
                for target in targets
            )
        )

        result = self.run_gate(mode="trial")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        steps = {step["name"]: step for step in report["steps"]}
        offender = steps[offender_name]
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
        # 执法不能放松：越界事件仍完整进入全局证据，整个门仍然是红的。
        self.assertIn(violation, report["guard_events"])

    def test_blocking_guard_event_attribution_is_visible_in_verdict_reasons(self):
        violation = self.write_parallel_boundary_fixture()
        shards = compute_unit_contract_shards(self.fixture_root)
        offender_name = next(
            name
            for name, targets in shards.items()
            if any(
                "test_denied_subprocess_is_attributed_to_its_own_step" in target
                for target in targets
            )
        )

        result = self.run_gate(mode="trial")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        reasons = "\n".join(report["verdict_reasons"])
        self.assertIn(violation["kind"], reasons)
        self.assertIn(violation["target"], reasons)
        self.assertIn(offender_name, reasons)

    def test_javascript_syntax_uses_trusted_node_instead_of_path(self):
        fake_bin = Path(self.temp_dir.name) / "fake-bin"
        fake_bin.mkdir()
        marker = Path(self.temp_dir.name) / "fake-node-ran"
        if os.name == "nt":
            fake_node = fake_bin / "node.exe"
            fake_node.write_bytes(Path(sys.executable).read_bytes())
        else:
            fake_node = fake_bin / "node"
            fake_node.write_text(f"#!/bin/sh\nprintf ran > {marker!s}\n", encoding="utf-8")
            fake_node.chmod(0o755)

        result = self.run_gate(PATH=str(fake_bin) + os.pathsep + os.environ["PATH"])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())

    def test_provider_configuration_paths_are_not_inherited(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import os\n"
            "import unittest\n\n"
            "class ProviderConfigurationContract(unittest.TestCase):\n"
            "    def test_real_provider_configuration_is_absent(self):\n"
            "        self.assertNotIn('CODEX_AUTH_FILE', os.environ)\n",
            encoding="utf-8",
        )

        result = self.run_gate(CODEX_AUTH_FILE=str(Path(self.temp_dir.name) / "real-auth.json"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_evidence_directory_inside_repo_is_rejected_without_mutating_it(self):
        evidence_dir = self.fixture_root / "gate-evidence"

        result = self.run_gate(evidence_dir=evidence_dir)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(evidence_dir.exists())

    def test_parent_secrets_are_redacted_from_process_output_evidence(self):
        secret = "s3cr3t"
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import json\n"
            "import os\n"
            "import unittest\n\n"
            "class SecretOutputContract(unittest.TestCase):\n"
            "    def test_output(self):\n"
            f"        print({secret!r})\n"
            "        try:\n"
            f"            os.system({secret!r})\n"
            "        except Exception:\n"
            "            pass\n"
            "        with open(os.environ['FIREFLY_QUALITY_GATE_RETRY_EVENTS'], 'a', encoding='utf-8') as events:\n"
            f"            events.write(json.dumps({{{secret!r}: 'must be redacted'}}) + '\\n')\n",
            encoding="utf-8",
        )

        result = self.run_gate(GITHUB_TOKEN=secret)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        serialized_report = json.dumps(self.report())
        self.assertNotIn(secret, serialized_report)
        self.assertIn("[REDACTED]", serialized_report)

    def test_retry_evidence_is_reported_as_failure_instead_of_green(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import json\n"
            "import os\n"
            "import unittest\n\n"
            "class RetryContract(unittest.TestCase):\n"
            "    def test_success_after_retry(self):\n"
            "        with open(os.environ['FIREFLY_QUALITY_GATE_RETRY_EVENTS'], 'a', encoding='utf-8') as events:\n"
            "            events.write(json.dumps({'attempt': 2, 'reason': 'fixture retry'}) + '\\n')\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(
            (self.evidence_dir / "report.json").is_file(),
            result.stdout + result.stderr,
        )
        report = self.report()
        self.assertEqual(report["first_failure"]["name"], "surface-contract")
        self.assertEqual(report["retry_events"], [{"attempt": 2, "reason": "fixture retry"}])

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
            {"kind": "subprocess-leak", "target": Path(sys.executable).name.lower()},
            report["guard_events"],
        )
        self.assertEqual(report["first_failure"]["name"], "surface-contract")

    def test_utf8_step_output_is_captured_without_crashing_the_gate(self):
        expected_output = "quality evidence: \U0001f525"
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import unittest\n\n"
            "class UnicodeOutputContract(unittest.TestCase):\n"
            "    def test_output(self):\n"
            f"        print({expected_output!r})\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        surface_step = next(
            step for step in self.report()["steps"] if step["name"] == "surface-contract"
        )
        self.assertIn(expected_output, surface_step["stdout_tail"])

    def test_steps_run_in_a_snapshot_and_cannot_modify_an_already_dirty_file(self):
        target = self.fixture_root / "static" / "js" / "smart-canvas.js"
        original_content = target.read_text(encoding="utf-8")
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class WorktreeContract(unittest.TestCase):\n"
            "    def test_mutates_existing_untracked_file(self):\n"
            "        target = Path(__file__).parents[1] / 'static' / 'js' / 'smart-canvas.js'\n"
            "        target.write_text('const mutatedDuringGate = true;\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), original_content)
        report = self.report()
        self.assertFalse(report["dirty_worktree"]["changed_during_run"])
        self.assertTrue(report["execution_workspace"]["isolated"])

    def test_skipped_contract_is_reported_as_failure_instead_of_green(self):
        (self.fixture_root / "tests" / "test_product_surface_behavior.py").write_text(
            "import unittest\n\n"
            "class SkippedContract(unittest.TestCase):\n"
            "    @unittest.skip('missing deterministic fixture')\n"
            "    def test_contract(self):\n"
            "        pass\n",
            encoding="utf-8",
        )

        result = self.run_gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        contract = next(step for step in report["steps"] if step["name"] == "surface-contract")
        self.assertEqual(contract["status"], "failed")
        self.assertEqual(contract["unittest"]["skipped"], 1)
        self.assertEqual(report["first_failure"]["name"], "surface-contract")

    def test_browser_tracer_reported_skip_is_failure_instead_of_green(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\n\n"
            "class ProjectContract(unittest.TestCase):\n"
            "    def test_project_contract(self):\n"
            "        pass\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "conversation_browser.py").write_text(
            "print('SKIPPED: browser unavailable')\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate(mode="trial")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        tracer = next(step for step in report["steps"] if step["name"] == "browser-tracer")
        self.assertTrue(tracer["reported_skip"])
        self.assertEqual(tracer["status"], "failed")

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
            "fast": (30, ("javascript-syntax", "excel-api-smoke", "surface-contract")),
            "domain": (180, ("javascript-syntax", "surface-contract", "project-contract", "provider-contract", "native-browser-evidence", "generation-lifecycle-evidence")),
            "trial": (300, ("javascript-syntax", "unit-contract-quality-gate", "unit-contract-remainder", "native-browser-evidence", "project-linked-generation-evidence", "generation-lifecycle-evidence", "complete-generation-lifecycle-evidence", "composer-flow-evidence", "long-content-evidence", "ui-integrity-evidence", "excel-import-evidence", "canvas-boundaries-evidence", "video-failure-evidence", "node-flow-cascade-evidence", "fly-fixes-evidence", "unit-contract-browser", "browser-tracer")),
            "release": (
                900,
                (
                    "javascript-syntax",
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
                    "video-failure-evidence",
                    "node-flow-cascade-evidence",
                    "fly-fixes-evidence",
                    "unit-contract-browser",
                    "browser-tracer",
                    "browser-harness-contract",
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

    def test_failed_non_critical_step_records_s2_finding_without_changing_verdict(self):
        (self.fixture_root / "static" / "js" / "smart-canvas.js").write_text(
            "const broken = ;\n",
            encoding="utf-8",
        )

        result = self.run_gate()

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
        (self.fixture_root / "static" / "js" / "smart-canvas.js").write_text(
            "const broken = ;\n",
            encoding="utf-8",
        )

        result = self.run_gate(FIREFLY_QUALITY_GATE_ACCEPT_S2="1")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = self.report()
        self.assertEqual(report["verdict"], "failed")
        self.assertIs(report["s2_accepted"], True)
        self.assertEqual(report["first_failure"]["name"], "javascript-syntax")
        self.assertIn("explicitly accepted", " ".join(report["verdict_reasons"]))

    def test_guard_and_failed_critical_journey_are_classified_s0_and_s1(self):
        (self.fixture_root / "tests" / "test_project_store.py").write_text(
            "import unittest\nclass ProjectContract(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.fixture_root / "tests" / "test_provider_contract_template.py").write_text(
            "import socket, unittest\n\n"
            "class ProviderContract(unittest.TestCase):\n"
            "    def test_provider_contract_cannot_reach_the_network(self):\n"
            "        socket.create_connection(('provider.invalid', 443), timeout=0.1)\n",
            encoding="utf-8",
        )
        self.write_native_browser_fixture()

        result = self.run_gate("domain")

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
        s1 = next(
            finding
            for finding in report["severity_frequency_findings"]
            if finding["severity"] == "S1"
        )
        self.assertEqual(s1["source"], "step")
        self.assertEqual(s1["step"], "provider-contract")


if __name__ == "__main__":
    unittest.main()
