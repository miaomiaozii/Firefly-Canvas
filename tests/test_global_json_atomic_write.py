"""#345：全局配置 JSON 的原子写 + 损坏隔离。

事故口径（issue #345 / #145）：全局配置（API Key、资源库、提示词库、存储目录、共享文件夹）
以前都是 ``open(path, "w")`` + ``json.dump``。``"w"`` 在打开那一刻就把原文件截成 0 字节，
dump 到一半断电/崩溃就什么都不剩；读侧再 ``except Exception: return 默认值``，下一次保存
就把默认值写回去——用户整套数据从此消失，而且坏文件也被顶掉，没法定因。

钉住四条验收：
1. 写入中途被中断 → 磁盘上的原文件一字不变，也不留垃圾临时文件；
2. 内容不可序列化 → 保存报错，磁盘保持上次内容；
3. 文件解析失败 → 先改名 ``.corrupt-<ts>`` 保住现场 + 记一条诊断给界面提示，默认值不会覆盖它；
4. 项目画布读不回 → 坏 ``canvas.json`` 先改名保住，再用备份继续；
5. 行为守卫：真的保存点不许再用“先截断再写”的裸写法（直接拦截对目标文件的 ``open(..., "w")``）。
"""
import builtins
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import firefly_json_io
import main
from firefly_projects import ProjectStore

import app.services.canvas as _services_canvas
import app.services.projects as _services_projects
import app.services.shared as _services_shared
import app.routers.api_canvases as _router_canvases
import app.routers.api_prompt_libraries as _router_prompt_libraries
import app.routers.api_shared_folders as _router_shared_folders
import app.routers.api_storage_settings as _router_storage_settings


class TempDirCaseMixin:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        firefly_json_io.clear_data_diagnostics()

    def tearDown(self):
        firefly_json_io.clear_data_diagnostics()
        self.temp.cleanup()

    def path_for(self, name):
        return self.root / name


class AtomicWriteTests(TempDirCaseMixin, unittest.TestCase):
    """验收①②：写一半死掉 / 序列化不了，都不许碰原文件。"""

    def setUp(self):
        TempDirCaseMixin.setUp(self)
        self.target = self.path_for("asset_library.json")
        self.original = json.dumps({"libraries": [{"id": "mine", "items": [{"id": "a"}]}]}, ensure_ascii=False)
        self.target.write_text(self.original, encoding="utf-8")

    def leftovers(self):
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith("."))

    def test_interrupted_dump_leaves_original_file_intact(self):
        """最真实的事故：json.dump 写到一半进程死了，换名那步根本没执行。"""

        def half_dump(value, handle, **_kwargs):
            handle.write('{"libraries": [')
            handle.flush()
            raise OSError("写入中途被中断")

        with patch.object(firefly_json_io.json, "dump", half_dump):
            with self.assertRaises(OSError):
                firefly_json_io.write_json_atomic(self.target, {"libraries": [{"id": "new"}]})
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)
        self.assertEqual(self.leftovers(), [], "临时文件必须清干净")

    def test_replace_failure_leaves_original_file_intact(self):
        with patch.object(firefly_json_io.os, "replace", side_effect=OSError("换名失败")):
            with self.assertRaises(OSError):
                firefly_json_io.write_json_atomic(self.target, {"libraries": []})
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)
        self.assertEqual(self.leftovers(), [])

    def test_unserializable_value_raises_without_touching_original(self):
        with self.assertRaises(TypeError):
            firefly_json_io.write_json_atomic(self.target, {"broken": object()})
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original)
        self.assertEqual(self.leftovers(), [])

    def test_successful_write_leaves_no_temp_files_and_is_readable(self):
        firefly_json_io.write_json_atomic(self.target, {"libraries": [{"id": "new"}]})
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8"))["libraries"][0]["id"], "new")
        self.assertEqual(self.leftovers(), [])

    def test_keep_backup_rotates_previous_contents_newest_first(self):
        firefly_json_io.write_json_atomic(self.target, {"v": 2}, keep_backup=True, backup_count=2)
        firefly_json_io.write_json_atomic(self.target, {"v": 3}, keep_backup=True, backup_count=2)
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), {"v": 3})
        self.assertEqual(json.loads((self.root / "asset_library.json.bak").read_text(encoding="utf-8")), {"v": 2})
        self.assertEqual(json.loads((self.root / "asset_library.json.bak-2").read_text(encoding="utf-8")),
                         json.loads(self.original))
        self.assertFalse([p for p in self.root.iterdir() if p.name.startswith(".")])


class CorruptReadTests(TempDirCaseMixin, unittest.TestCase):
    """验收③：坏文件先保住，再走默认值；默认值永远盖不到现场。"""

    def test_corrupt_file_renamed_and_bytes_preserved(self):
        corrupt = '{"libraries": [TRUNC'
        target = self.path_for("api_providers.json")
        target.write_text(corrupt, encoding="utf-8")
        self.assertEqual(firefly_json_io.read_json(target, default=[]), [])
        self.assertFalse(target.exists(), "坏文件必须被挪开，否则下一次保存就把它盖掉")
        quarantined = sorted(p for p in self.root.iterdir() if p.name.startswith("api_providers.json.corrupt-"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)

    def test_corrupt_read_records_diagnostic_for_the_ui(self):
        target = self.path_for("prompt_libraries.json")
        target.write_text("not json at all", encoding="utf-8")
        firefly_json_io.read_json(target, default=None)
        entries = firefly_json_io.recent_data_diagnostics()
        self.assertEqual(len(entries), 1)
        self.assertIn("prompt_libraries.json", entries[0]["file"])
        self.assertTrue(entries[0]["at"], "界面提示需要时间戳")

    def test_missing_file_is_silent_and_default_returned(self):
        self.assertEqual(firefly_json_io.read_json(self.path_for("absent.json"), default=["default"]), ["default"])
        self.assertEqual(firefly_json_io.recent_data_diagnostics(), [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [])

    def test_empty_file_counts_as_corrupt(self):
        target = self.path_for("storage_settings.json")
        target.write_text("", encoding="utf-8")
        self.assertEqual(firefly_json_io.read_json(target, default={}), {})
        self.assertTrue([p for p in self.root.iterdir() if ".corrupt-" in p.name])

    def test_raise_on_corrupt_exposes_quarantine_location(self):
        target = self.path_for("global_settings.json")
        target.write_text("{", encoding="utf-8")
        with self.assertRaises(firefly_json_io.CorruptJSONFile) as caught:
            firefly_json_io.read_json(target, default={}, raise_on_corrupt=True)
        self.assertIsNotNone(caught.exception.quarantine)
        self.assertIn(".corrupt-", caught.exception.quarantine.name)


class GlobalConfigLivePathTests(TempDirCaseMixin, unittest.TestCase):
    """验收①②③落在真的保存点/加载点上，不是只测工具函数。"""

    def test_asset_library_save_interrupted_keeps_previous_library(self):
        target = self.path_for("asset_library.json")
        previous = {"version": 1, "libraries": [{"id": "mine", "name": "我的库", "items": [{"id": "asset-1", "name": "图一"}]}]}
        target.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        with patch.object(main, "ASSET_LIBRARY_PATH", str(target)), \
                patch.object(firefly_json_io.os, "replace", side_effect=OSError("断电")):
            with self.assertRaises(OSError):
                main.save_asset_library({"version": 1, "libraries": [{"id": "other", "name": "别的库", "items": []}]})
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        with patch.object(main, "ASSET_LIBRARY_PATH", str(target)):
            loaded = main.load_asset_library()
        self.assertEqual([item["id"] for lib in loaded["libraries"] for item in lib.get("items", [])], ["asset-1"])

    def test_corrupt_asset_library_is_quarantined_not_emptied(self):
        target = self.path_for("asset_library.json")
        corrupt = '{"version": 1, "libraries": [{"id": "mine"'
        target.write_text(corrupt, encoding="utf-8")
        with patch.object(main, "ASSET_LIBRARY_PATH", str(target)):
            loaded = main.load_asset_library()
        # 默认库照常可用（产品行为不变）……
        self.assertIn("default", [lib.get("id") for lib in loaded.get("libraries", [])])
        # ……但现场还在：它被改名保在 .corrupt-<ts>，不会被新写回的默认库盖掉。
        quarantined = sorted(p for p in self.root.iterdir() if p.name.startswith("asset_library.json.corrupt-"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)

    def test_corrupt_prompt_libraries_scene_survives_the_default_rewrite(self):
        target = self.path_for("prompt_libraries.json")
        corrupt = '{"active_library_id": "mine", "libraries": [OOPS'
        target.write_text(corrupt, encoding="utf-8")
        with patch.object(main, "PROMPT_LIBRARY_PATH", str(target)), \
                patch.object(_router_prompt_libraries, "PROMPT_LIBRARY_PATH", str(target)):
            loaded = main.load_prompt_libraries()
        # 默认库照常可用（产品行为不变）……
        self.assertTrue(loaded.get("libraries"))
        # ……但现场还在，且新写出的默认库文件与现场是两个文件。
        quarantined = sorted(p for p in self.root.iterdir() if p.name.startswith("prompt_libraries.json.corrupt-"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)
        self.assertTrue(target.exists())
        self.assertNotEqual(target.read_text(encoding="utf-8"), corrupt)
        self.assertTrue(firefly_json_io.recent_data_diagnostics())

    def test_corrupt_api_providers_keeps_encrypted_keys_and_reports(self):
        target = self.path_for("api_providers.json")
        corrupt = '[{"id": "gpt", "api_key": "enc:XXXXXXXX"'
        target.write_text(corrupt, encoding="utf-8")
        with patch.object(main, "API_PROVIDERS_FILE", str(target)):
            providers = main.load_api_providers()
        self.assertTrue(providers, "读坏了也要能用（内置默认平台）")
        quarantined = sorted(p for p in self.root.iterdir() if p.name.startswith("api_providers.json.corrupt-"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)
        entries = firefly_json_io.recent_data_diagnostics()
        self.assertTrue(any("api_providers.json" in entry["file"] for entry in entries))

    def test_corrupt_storage_settings_reports_instead_of_silently_resetting(self):
        target = self.path_for("storage_settings.json")
        corrupt = '{"output": "D:/我的资源'
        target.write_text(corrupt, encoding="utf-8")
        with patch.object(main, "STORAGE_SETTINGS_FILE", str(target)), \
                patch.object(_router_storage_settings, "STORAGE_SETTINGS_FILE", str(target)):
            settings = main.load_storage_settings()
        self.assertIn("dirs", settings)
        quarantined = sorted(p for p in self.root.iterdir() if p.name.startswith("storage_settings.json.corrupt-"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)

    def test_corrupt_shared_folders_reports_instead_of_silently_emptying(self):
        target = self.path_for("shared_folders.json")
        corrupt = '{"folders": [{"path": "D:/素材"'
        target.write_text(corrupt, encoding="utf-8")
        with patch.object(_router_shared_folders, "SHARED_FOLDERS_FILE", str(target)):
            loaded = _router_shared_folders.shared_folders_load()
        self.assertEqual(loaded, {"folders": []})
        quarantined = sorted(p for p in self.root.iterdir() if p.name.startswith("shared_folders.json.corrupt-"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)
        # 另一份同名实现（services 侧）也必须走同一条路，否则改一处漏一处。
        target.write_text(corrupt, encoding="utf-8")
        with patch.object(_services_shared._svc, "_main", lambda name: str(target) if name == "SHARED_FOLDERS_FILE" else getattr(main, name)):
            self.assertEqual(_services_shared.shared_folders_load(), {"folders": []})


class GlobalJsonSaverBehaviorTests(TempDirCaseMixin, unittest.TestCase):
    """验收④的行为守卫：真的保存点绝不再直接以写模式打开目标文件。

    只测工具函数不够——事故恰恰出在“每个保存点各自抄一份写法”。这里把配置常量指向临时
    路径，再拦截 ``open(target, "w"/"a")``：任何一处回归成裸截断写都会当场炸。
    """

    def _guard(self, targets):
        resolved = {str(Path(str(p)).resolve()) for p in targets}
        real_open = builtins.open

        def guarded_open(file, mode="r", *args, **kwargs):
            name = str(file)
            if isinstance(mode, str) and ("w" in mode or "a" in mode) and str(Path(name).resolve()) in resolved:
                raise AssertionError(f"裸写全局配置（先截断再写）: {name} mode={mode}")
            return real_open(file, mode, *args, **kwargs)

        return patch.object(builtins, "open", guarded_open)

    def test_every_global_json_saver_writes_through_a_temp_file(self):
        asset_library = self.path_for("asset_library.json")
        prompt_libraries = self.path_for("prompt_libraries.json")
        shared_folders = self.path_for("shared_folders.json")
        storage_settings = self.path_for("storage_settings.json")
        projects_path = self.path_for("projects.json")
        history_file = self.path_for("history.json")
        legacy_canvas = self.path_for("canvas-legacy.json")
        for path in (asset_library, prompt_libraries, shared_folders, storage_settings, projects_path, history_file, legacy_canvas):
            path.write_text(json.dumps({"libraries": [], "folders": [], "dirs": {}, "projects": [], "items": []}), encoding="utf-8")
        canvas_dir = self.path_for("canvas")
        canvas_dir.mkdir()

        with self._guard([asset_library, prompt_libraries, shared_folders, storage_settings, projects_path, history_file, legacy_canvas]):
            with patch.object(main, "ASSET_LIBRARY_PATH", str(asset_library)):
                main.save_asset_library({"version": 1, "libraries": []})
            with patch.object(_router_prompt_libraries, "PROMPT_LIBRARY_PATH", str(prompt_libraries)):
                _router_prompt_libraries.save_prompt_libraries({"active_library_id": "system", "libraries": []})
            with patch.object(_router_shared_folders, "SHARED_FOLDERS_FILE", str(shared_folders)):
                _router_shared_folders.shared_folders_save({"folders": []})
            with patch.object(_router_storage_settings, "STORAGE_SETTINGS_FILE", str(storage_settings)), \
                    patch.object(_router_storage_settings, "DEFAULT_STORAGE_DIRS", {}), \
                    patch.object(_router_storage_settings, "apply_storage_settings", lambda _dirs: None):
                _router_storage_settings.save_storage_settings({})
            with patch.object(_services_projects._svc, "_main", lambda name: str(projects_path) if name == "PROJECTS_PATH" else getattr(main, name)):
                _services_projects.save_projects([])
            with patch.object(_services_canvas._svc, "_main", lambda name: str(history_file) if name == "HISTORY_FILE" else getattr(main, name)):
                _services_canvas.save_to_history({"id": "one"})
            with patch.object(_router_canvases._app_main, "CANVAS_DIR", str(canvas_dir), create=True), \
                    patch.object(_router_canvases, "canvas_path", lambda _canvas_id: str(legacy_canvas)):
                _router_canvases.save_canvas({"id": "legacy", "kind": "classic", "nodes": [], "connections": []})

        for path in (asset_library, prompt_libraries, shared_folders, storage_settings, projects_path, history_file, legacy_canvas):
            json.loads(path.read_text(encoding="utf-8"))


class ProjectCanvasSceneTests(TempDirCaseMixin, unittest.TestCase):
    """验收④：坏 canvas.json 的现场必须在“用备份继续”之后还在。"""

    def setUp(self):
        TempDirCaseMixin.setUp(self)
        self.projects_root = self.root / "projects"
        self.store = ProjectStore(self.projects_root, snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        TempDirCaseMixin.tearDown(self)

    def apply_mutations(self, project_id, mutations):
        revision = self.store.open_project(project_id)["canvas"]["revision"]
        return self.store.apply_mutations(project_id, mutations, base_revision=revision)

    def test_corrupt_canvas_is_quarantined_before_backup_restore(self):
        project = self.store.create_project("现场保住")
        project_id = project["id"]
        self.apply_mutations(project_id, [{"op": "upsert_node", "node": {"id": "kept", "x": 1, "y": 1}}])
        self.store.flush(project_id)
        # 再一笔 + flush：轮到 canvas.json.bak 里存下 kept 那一版，回退才有东西可接。
        self.apply_mutations(project_id, [{"op": "upsert_node", "node": {"id": "lost", "x": 9, "y": 9}}])
        self.store.flush(project_id)
        self.store.close()
        project_dir = self.projects_root / project_id
        broken = '{"id": "x", "project_id": "x", "nodes": [{"id": "lost"}, OOPS'
        canvas_path = project_dir / "canvas.json"
        canvas_path.write_text(broken, encoding="utf-8")

        # 模拟“重启后再打开”：内存里没有缓存，才真的从盘上读。
        reopened_store = ProjectStore(self.projects_root, snapshot_delay=0)
        try:
            opened = reopened_store.open_project(project_id)
            canvas = opened["canvas"]
            self.assertEqual([node["id"] for node in canvas.get("nodes", [])], ["kept"], "备份要继续能用（产品行为不变）")
            quarantined = sorted(p.name for p in project_dir.iterdir() if p.name.startswith("canvas.json.corrupt-"))
            self.assertEqual(len(quarantined), 1, "现场必须在，且只有一份")
            self.assertEqual((project_dir / quarantined[0]).read_text(encoding="utf-8"), broken)
            self.assertIn("lost", (project_dir / quarantined[0]).read_text(encoding="utf-8"), "坏掉那一笔的现场要还能人肉取回")
            # 前端（#94 恢复向导）读的就是 canvas._recovery：这里要能说清现场改名成了什么。
            mark = canvas.get("_recovery") or {}
            self.assertEqual(mark.get("restored_from"), "canvas.json.bak")
            self.assertEqual(mark.get("corrupt_saved"), quarantined[0], "界面提示要能说出现场改名成了什么")
        finally:
            reopened_store.close()


class DataDiagnosticsEndpointTests(TempDirCaseMixin, unittest.TestCase):
    """界面提示的后端接口：读坏了要能在 /api/config/data-diagnostics 里查到。"""

    def setUp(self):
        TempDirCaseMixin.setUp(self)
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

    def test_clean_state_reports_nothing(self):
        response = self.client.get("/api/config/data-diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": []})

    def test_corrupt_config_shows_up_with_the_quarantine_name(self):
        target = self.path_for("asset_library.json")
        target.write_text('{"libraries": [CUT', encoding="utf-8")
        with patch.object(main, "ASSET_LIBRARY_PATH", str(target)):
            main.load_asset_library()
        items = self.client.get("/api/config/data-diagnostics").json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], firefly_json_io.DIAGNOSTIC_KIND_CORRUPT)
        self.assertTrue(items[0]["file"].endswith("asset_library.json"))
        self.assertIn(".corrupt-", items[0]["detail"], "得把改名后的文件名交给界面")
        self.assertTrue(items[0]["at"])

    def test_diagnostics_never_leak_file_contents(self):
        target = self.path_for("api_providers.json")
        target.write_text('[{"api_key": "enc:SUPERSECRET", "id": ', encoding="utf-8")
        with patch.object(main, "API_PROVIDERS_FILE", str(target)):
            main.load_api_providers()
        body = self.client.get("/api/config/data-diagnostics").content.decode("utf-8")
        self.assertNotIn("SUPERSECRET", body)


class FrontendWiringTests(unittest.TestCase):
    """设置页必须把诊断说出来——静默回到默认值就是 #345 的用户可见后果。"""

    def test_settings_page_reads_the_diagnostics_endpoint(self):
        script = (ROOT / "static" / "js" / "global-settings.js").read_text(encoding="utf-8")
        self.assertIn("/api/config/data-diagnostics", script)
        self.assertIn("corrupt", script, "提示里要告诉用户坏文件保在 .corrupt-… 里")

    def test_settings_page_assets_carry_a_fresh_cache_buster(self):
        # #224 同类坑：restart-firefly.ps1 的 Bump-AssetVersions 只刷 smart-canvas.html，
        # 改了 global-settings.js 不跡戳，用户 Ctrl+F5 也拿不到新代码。
        html = (ROOT / "static" / "global-settings.html").read_text(encoding="utf-8")
        stamps = {value for _, value in re.findall(r'"(/static/[^"]*)\?v=([0-9][0-9.]*)"', html)}
        self.assertEqual(len(stamps), 1, "设置页所有静态资产必须共用一个 ?v=")
        stamp = stamps.pop()
        self.assertGreater(".".join(stamp.split(".")[:2]), "20261114.174656", "本票改过 JS，戳记必须比 #346 那次新")


if __name__ == "__main__":
    unittest.main()
