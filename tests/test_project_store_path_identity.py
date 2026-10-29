r"""#195：Windows 扩展长度前缀必须在所有包含判断前归一化。

#193 的修复（``_canonical_drive_key``）只接进了 ``_safe_project_path`` 一处。
其余 ``os.path.commonpath`` 站点仍直接比较两侧字符串。``Path.resolve(strict=False)``
对同一目标做两次 ``_getfinalpathname`` 查询，仅当两次 winerror 一致才剥离
``\\?\`` 前缀；父目录被并发创建时 winerror 由 3 变为 2，前缀残留。
``splitdrive`` 把 ``\\?\c:`` 与 ``c:`` 判为不同盘，``commonpath`` 抛
``ValueError: Paths don't have the same drive``。

后果分两类，都实测过：
- 无 try 的站点（媒体读写）直接抛 ``ValueError``，本该是「不存在」的正常路径变 500。
- 有 ``except ValueError: pass`` 兜底的站点（仓库内检查、库嵌套检查）**静默跳过安全检查**。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import firefly_projects
from firefly_projects import ProjectNotFound, ProjectStore, ProjectStoreError


def racing_getfinalpathname(target_path, real_getfinalpathname, state):
    """重建 #193 实测到的 winerror 序列：成对出现 3（路径不存在）→ 2（文件不存在）。

    ``ntpath.realpath`` 与 ``pathlib._WindowsFlavour.resolve`` 都只在两次 winerror
    相同时才剥掉 ``\\?\`` 前缀，因此该序列让 ``resolve()`` 返回带前缀的形式。
    一次性开关：实测在媒体站点上足以稳定复现（pathlib 每次 resolve 的内部
    调用次数不止两次，按奇偶交替反而会被后续配对抹平）。
    """
    target_key = os.path.normcase(str(target_path))

    def side_effect(path):
        key = str(path)
        if key.startswith("\\\\?\\"):
            key = key[4:]
        if os.path.normcase(key) != target_key:
            return real_getfinalpathname(path)
        if not state["first"]:
            state["first"] = True
            exc = OSError(3, "模拟并发父目录状态")
            exc.winerror = 3
            raise exc
        if not state["second"]:
            state["second"] = True
            exc = OSError(2, "模拟并发父目录状态")
            exc.winerror = 2
            raise exc
        return real_getfinalpathname(path)

    return side_effect


class ExtendedPrefixContainmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def patch_prefix_race(self, target_path):
        import ntpath

        real = ntpath._getfinalpathname
        state = {"first": False, "second": False}
        return patch(
            "ntpath._getfinalpathname",
            side_effect=racing_getfinalpathname(target_path, real, state),
        ), state

    def assertRaceTriggered(self, state):
        self.assertTrue(state["first"] and state["second"], "前缀竞态未被触发")

    def test_projects_root_inside_repository_is_still_rejected_under_prefix_race(self):
        """``_validated_projects_root``：前缀竞态不得让「项目库不能位于代码仓库内」静默失效。"""
        repository = Path(firefly_projects.__file__).resolve().parent
        candidate = repository / "accidental-projects-library"
        patcher, state = self.patch_prefix_race(candidate)
        with patcher:
            with self.assertRaises(ProjectStoreError):
                firefly_projects._validated_projects_root(candidate)
        self.assertRaceTriggered(state)

    def test_media_record_reports_missing_media_under_prefix_race(self):
        """``media_record``：文件已缺失时应报「媒体不存在」，不得抛 ``ValueError``。"""
        project = self.store.create_project("前缀竞态媒体")
        media = self.store.import_media(project["id"], b"png-bytes", "out.png", "image/png")
        path = Path(self.store.media_record(project["id"], media["asset_id"])["path"])
        path.unlink()

        patcher, state = self.patch_prefix_race(path)
        with patcher:
            with self.assertRaises(ProjectNotFound):
                self.store.media_record(project["id"], media["asset_id"])
        self.assertRaceTriggered(state)

    def test_delete_media_survives_prefix_race(self):
        """``delete_media``：删除物理文件的包含判断不得因前缀残留抛 ``ValueError``。"""
        project = self.store.create_project("前缀竞态删除")
        media = self.store.import_media(project["id"], b"png-bytes", "gone.png", "image/png")
        path = Path(self.store.media_record(project["id"], media["asset_id"])["path"])
        path.unlink()

        patcher, state = self.patch_prefix_race(path)
        with patcher:
            result = self.store.delete_media(project["id"], media["asset_id"], delete_file=True)
        self.assertEqual(result["deleted"], media["asset_id"])
        self.assertRaceTriggered(state)

    def test_nested_library_is_still_rejected_under_prefix_race(self):
        """``copy_library_to``：前缀残留不得让「新旧项目库不能互相嵌套」静默跳过。

        该站点带前缀的一侧来自 ``_validated_projects_root()`` 的返回值；winerror
        竞态会被该函数内部的多次 resolve 消耗，所以直接在函数边界注入带前缀的
        返回值 —— 即 #193 实测到的那种 ``resolve()`` 结果形态。
        """
        nested = self.root / "nested-library"
        prefixed = "\\\\?\\" + str(nested.resolve())
        with patch.object(
            firefly_projects,
            "_validated_projects_root",
            return_value=Path(prefixed),
        ):
            with self.assertRaises(ProjectStoreError) as caught:
                self.store.copy_library_to(nested)
        self.assertIn("嵌套", str(caught.exception))


class PathIsWithinUnitTests(unittest.TestCase):
    """共享判定本身的直接单测（覆盖 ``_import_fly`` 等难以构造站点级竞态的路径）。"""

    def test_prefixed_and_plain_paths_on_the_same_drive_are_contained(self):
        self.assertTrue(firefly_projects.path_is_within(r"C:\Projects\p\media", "\\\\?\\C:\\Projects\\p\\media\\a.png"))

    def test_prefixed_root_still_contains_plain_candidate(self):
        self.assertTrue(firefly_projects.path_is_within("\\\\?\\C:\\Projects\\p", "C:\\Projects\\p\\media"))

    def test_lowercase_drive_letter_matches_uppercase_prefix(self):
        self.assertTrue(firefly_projects.path_is_within("\\\\?\\C:\\a", "c:\\a\\b"))

    def test_candidate_outside_root_is_not_contained(self):
        self.assertFalse(firefly_projects.path_is_within("C:\\Projects\\p", "C:\\Projects\\q\\a.png"))

    def test_different_drives_do_not_raise(self):
        self.assertFalse(firefly_projects.path_is_within("D:\\Projects\\p", "C:\\Projects\\p\\a.png"))

    def test_unc_prefix_is_normalized_on_both_sides(self):
        self.assertTrue(firefly_projects.path_is_within("\\\\?\\UNC\\srv\\share\\root", "\\\\srv\\share\\root\\sub"))


if __name__ == "__main__":
    unittest.main()
