"""#224 存量回填：给改动前写入的 history outputs 补上可读 name。

老记录只有 {'asset_id', 'kind'}，而名字在 store 的媒体记录里本来就有
（firefly_projects._new_media_record 写 name=Path(filename).name）。
这里验证一次性回填能把那个名字补回历史，并且幂等、可 dry-run、
不会把 asset_id 本身当名字写回去。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from tools.backfill_history_output_names import main as cli_main

BARE_ID = "0f02737013cc4b4c9af179c5b8459b84"


def _read_history(root: Path, project_id: str):
    return json.loads((root / project_id / "history.json").read_text(encoding="utf-8"))["records"]


def _write_history(root: Path, project_id: str, records) -> None:
    (root / project_id / "history.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8"
    )


def _legacy_record(outputs, *, history_id="generation-legacy-1"):
    return {"id": history_id, "type": "online-image", "status": "succeeded", "outputs": outputs}


class _BackfillFixture(unittest.TestCase):
    """共用搭场景：临时项目库 + 项目 + 一张导入图 + 一条老历史记录。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _import(self, project_id: str, filename: str) -> str:
        media = self.store.import_media(
            project_id, b"png-bytes", filename, "image/png",
            {"type": "upload", "name": filename},
        )
        return media["asset_id"]

    def _reopen(self) -> ProjectStore:
        self.store.close()
        self.store = ProjectStore(self.root, snapshot_delay=0)
        return self.store

    def _project(self, *, name="海报原图.png", outputs=None):
        """建项目 + 导入一张图；outputs 里的 {asset_id} 会替换成真实 ID。"""
        project = self.store.create_project("回填项目")
        asset_id = self._import(project["id"], name)
        if outputs is None:
            outputs = [{"asset_id": "{asset_id}", "kind": "image"}]
        rendered = json.loads(json.dumps(outputs).replace("{asset_id}", asset_id))
        _write_history(self.root, project["id"], [_legacy_record(rendered)])
        return project["id"], asset_id

class HistoryOutputNameBackfillTests(_BackfillFixture):
    def test_legacy_output_gets_readable_name_from_media_record(self):
        project_id, _ = self._project()
        summary = self.store.backfill_history_output_names()
        self.assertEqual(1, summary["outputs_named"])
        self.assertEqual("海报原图.png", _read_history(self.root, project_id)[0]["outputs"][0]["name"])
        # 重开一次：证明写盘了，不是只改了内存缓存
        self._reopen()
        self.assertEqual("海报原图.png", _read_history(self.root, project_id)[0]["outputs"][0]["name"])

    def test_name_that_is_just_the_asset_id_is_not_written_back(self):
        # 生成原件的媒体 name 就是 <asset_id>.png，写回去等于把裸 ID 又塞回列表。
        project_id, _ = self._project(name=f"{BARE_ID}.png")
        summary = self.store.backfill_history_output_names(project_id=project_id)
        self.assertEqual(0, summary["outputs_named"])
        self.assertEqual(1, summary["outputs_unresolved"])
        self.assertNotIn("name", _read_history(self.root, project_id)[0]["outputs"][0])

    def test_canvas_node_alias_is_used_when_media_name_is_bare_id(self):
        project_id, asset_id = self._project(name=f"{BARE_ID}.png")
        canvas = self.store.open_project(project_id)["canvas"]
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {"id": "n1", "type": "image", "alias": "主视觉-星标", "images": [{"asset_id": asset_id}]},
        }], base_revision=int(canvas["revision"]))
        summary = self.store.backfill_history_output_names(project_id=project_id)
        self.assertEqual(1, summary["outputs_named"])
        self.assertEqual("主视觉-星标", _read_history(self.root, project_id)[0]["outputs"][0]["name"])

    def test_existing_name_is_never_overwritten(self):
        project_id, _ = self._project(outputs=[{"asset_id": "{asset_id}", "kind": "image", "name": "原来就有的名字.png"}])
        self.store.backfill_history_output_names()
        self.assertEqual(
            "原来就有的名字.png",
            _read_history(self.root, project_id)[0]["outputs"][0]["name"],
        )

    def test_dry_run_touches_nothing_on_disk(self):
        project_id, _ = self._project()
        path = self.root / project_id / "history.json"
        before = path.read_text(encoding="utf-8")
        summary = self.store.backfill_history_output_names(dry_run=True)
        self.assertEqual(1, summary["outputs_named"])
        self.assertTrue(summary["dry_run"])
        self.assertEqual(0, summary["projects_changed"])
        # 预演也得给出会改哪些项目，运维脚本才能先备份再写
        self.assertEqual([project_id], summary["changed_projects"])
        self.assertEqual(before, path.read_text(encoding="utf-8"))

    def test_second_run_is_a_no_op(self):
        self._project()
        self.store.backfill_history_output_names()
        self._reopen()
        second = self.store.backfill_history_output_names()
        self.assertEqual(0, second["outputs_named"])
        self.assertEqual(0, second["projects_changed"])

    def test_project_filter_leaves_other_projects_untouched(self):
        target_id, _ = self._project()
        other = self.store.create_project("别的项目")
        other_asset = self._import(other["id"], "别人的图.png")
        _write_history(self.root, other["id"], [_legacy_record(
            [{"asset_id": other_asset, "kind": "image"}], history_id="generation-other")])
        summary = self.store.backfill_history_output_names(project_id=target_id)
        self.assertEqual(1, summary["projects_scanned"])
        self.assertEqual(1, summary["outputs_named"])
        self.assertNotIn("name", _read_history(self.root, other["id"])[0]["outputs"][0])

    def test_malformed_entries_are_skipped_without_crashing(self):
        project_id, _ = self._project(outputs=[
            None,
            "不是对象",
            {"kind": "image"},
            {"asset_id": "素材库里根本没有的 ID", "kind": "image"},
            {"asset_id": "{asset_id}", "kind": "image"},
        ])
        summary = self.store.backfill_history_output_names(project_id=project_id)
        self.assertEqual(1, summary["outputs_named"])
        outputs = _read_history(self.root, project_id)[0]["outputs"]
        self.assertIsNone(outputs[0])
        self.assertEqual("不是对象", outputs[1])
        self.assertNotIn("name", outputs[2])
        self.assertNotIn("name", outputs[3])
        self.assertEqual("海报原图.png", outputs[4]["name"])

    def test_summary_counts_all_projects_when_unfiltered(self):
        self._project()
        second = self.store.create_project("第二个项目")
        second_asset = self._import(second["id"], "第二张.png")
        _write_history(self.root, second["id"], [_legacy_record(
            [{"asset_id": second_asset, "kind": "image"}], history_id="generation-two")])
        summary = self.store.backfill_history_output_names()
        self.assertEqual(2, summary["projects_scanned"])
        self.assertEqual(2, summary["outputs_named"])
        self.assertEqual(2, summary["projects_changed"])
        self.assertEqual(2, summary["records_scanned"])


class BackfillCommandLineTests(_BackfillFixture):
    """运维入口：默认只预演，--apply 才写盘，并且写前备份。"""

    def _cli(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = cli_main(argv)
        return rc, buffer.getvalue()

    def test_cli_defaults_to_dry_run(self):
        project_id, _ = self._project()
        path = self.root / project_id / "history.json"
        before = path.read_text(encoding="utf-8")
        rc, output = self._cli(["--projects-root", str(self.root)])
        self.assertEqual(0, rc)
        self.assertIn("待补名字：1", output)
        self.assertEqual(before, path.read_text(encoding="utf-8"))

    def test_cli_apply_writes_names_and_backs_up_original(self):
        project_id, _ = self._project()
        backup_root = Path(self.temp_dir.name) / "backups"
        rc, output = self._cli([
            "--projects-root", str(self.root), "--apply", "--backup-dir", str(backup_root),
        ])
        self.assertEqual(0, rc)
        self.assertEqual("海报原图.png", _read_history(self.root, project_id)[0]["outputs"][0]["name"])
        # 备份里必须是写盘前的那份（没有 name），否则出事没法退
        backups = sorted(backup_root.iterdir())
        self.assertEqual(1, len(backups))
        backed_up = (backups[0] / project_id / "history.json").read_text(encoding="utf-8")
        self.assertNotIn("海报原图.png", backed_up)

    def test_cli_apply_is_silent_when_nothing_to_do(self):
        self._project()
        self.store.backfill_history_output_names()
        rc, output = self._cli(["--projects-root", str(self.root), "--apply"])
        self.assertEqual(0, rc)
        self.assertIn("没有需要补的名字", output)

    def test_cli_reports_missing_project_root(self):
        rc, output = self._cli(["--projects-root", str(Path(self.temp_dir.name) / "不存在")])
        self.assertEqual(1, rc)
        self.assertIn("项目库不存在", output)


if __name__ == "__main__":
    unittest.main()
