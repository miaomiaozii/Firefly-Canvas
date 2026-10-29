r"""#195：``_project_directory`` 的包含判断必须归一化 Windows 扩展长度前缀。

其余 6 个可达站点（``_validated_projects_root`` / ``_import_fly`` 媒体与
agent_nodes / ``media_record`` / ``delete_media`` / ``copy_library_to``）已在
606d045 切换到 ``path_is_within``；``_project_directory`` 当时以「两侧都是
``resolve(strict=True)``，不会残留前缀」为由未改。该论断只覆盖 #193 的并发
创建竞态：strict 模式下第一次 ``_getfinalpathname`` 失败会立即 raise。但
``ntpath.realpath`` 对已存在路径还会做第二次校验调用，该次调用以不同的
winerror 失败时（并发删除、瞬时 FS 错误），``\\?\`` 前缀同样残留（实测：
该序列下 ``Path(已存在).resolve(strict=True)`` 返回带前缀形式）。

前缀残留后 ``splitdrive`` 把 ``\\?\c:`` 与 ``c:`` 判为不同盘，
``os.path.commonpath`` 抛 ``ValueError: Paths don't have the same drive``，
健康的项目目录会被误报为「项目目录路径无效」。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ntpath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import firefly_projects
from firefly_projects import ProjectStore

EXT = "\\\\?\\"


def strip_prefix(path_text):
    text = str(path_text)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith(EXT):
        return text[len(EXT):]
    return text


def normkey(path_text):
    return os.path.normcase(strip_prefix(path_text))


class StrictResolvePrefixRaceTests(unittest.TestCase):
    """站点级回归：strict=True 的 resolve 在第二次调用失败时残留前缀。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_project_directory_survives_second_call_failure_race(self):
        project = self.store.create_project("strict 前缀竞态")
        lexical = self.root / project["id"]
        # 目标路径在 realpath 内部出现的两种形态：
        # 第一次调用的入参形态、第二次调用的 long-name 形态。
        expected = {normkey(lexical), normkey(lexical.resolve())}
        state = {"calls": 0}
        real = ntpath._getfinalpathname

        def side_effect(path):
            if normkey(path) not in expected:
                return real(path)
            state["calls"] += 1
            if state["calls"] == 1:
                return real(path)  # 第一次：路径存在，成功
            exc = OSError(2, "模拟并发删除窗口")
            exc.winerror = 2  # 第二次（校验）失败，winerror != 0
            raise exc

        with patch("ntpath._getfinalpathname", side_effect=side_effect):
            directory = self.store._project_directory(project["id"])

        # realpath 内部可能短名→长名展开，与 lexical 字面形态不同，统一用 resolve() 形态比较。
        lexical_resolved = lexical.resolve()
        self.assertEqual(state["calls"], 2, "前缀竞态未被触发")
        self.assertTrue(directory.is_dir())
        self.assertEqual(
            firefly_projects._canonical_drive_key(str(directory)),
            firefly_projects._canonical_drive_key(str(lexical_resolved)),
        )


class ProjectDirectoryPrefixUnitTests(unittest.TestCase):
    """直接单测：钉住失败机制与归一化修复。"""

    @unittest.skipUnless(os.name == "nt", "splitdrive 盘符语义仅 Windows")
    def test_bare_normcase_commonpath_raises_on_prefix_mismatch(self):
        # #195 修复的失败机制：一侧带 \\?\ 前缀、另一侧不带 → 不同盘 → ValueError。
        with self.assertRaises(ValueError):
            os.path.commonpath(
                [os.path.normcase(r"C:\Projects\p"), os.path.normcase("\\\\?\\C:\\Projects\\p\\m")]
            )

    def test_canonical_drive_key_comparison_succeeds(self):
        root_key = firefly_projects._canonical_drive_key(r"C:\Projects\p")
        candidate_key = firefly_projects._canonical_drive_key("\\\\?\\C:\\Projects\\p\\m")
        self.assertEqual(os.path.commonpath([root_key, candidate_key]), root_key)

    def test_canonical_drive_key_strips_unc_prefix(self):
        self.assertEqual(
            firefly_projects._canonical_drive_key("\\\\?\\UNC\\srv\\share\\x"),
            os.path.normcase("\\\\srv\\share\\x"),
        )


if __name__ == "__main__":
    unittest.main()
