"""#212: 素材抽屉默认库命名收敛测试。

CONTEXT.md 规范术语（2026-09 词条）：
- 素材抽屉：管理项目素材与全局资源库的界面（面板）
- 全局资源库：可跨项目复用的素材或提示词集合
- 资产库：_Avoid_ 词（“资源库/素材抽屉/项目回收区”三处词条均登记），
  不应出现在用户可见文案和数据里

默认库（稳定身份 id='default'）是「全局资源库」的默认库，统一显示为
「默认全局资源库」（issue #212 预期的“明确标注为它的默认库”形态）：
真实数据、前端兜底文案、种子数据三处必须用同一个词。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main

CANONICAL_DEFAULT_NAME = "默认全局资源库"
LEGACY_DISPLAY_NAMES = ("默认资产库", "默认素材抽屉")

# 用户可见文案来源：i18n 条目 + 前端显示串 + 种子数据。
# 后端代码不做裸串扫描（app/services/misc.py 的迁移表 DEFAULT_LIBRARY_LEGACY_NAMES
# 按设计保留历史名用于识别存量数据），改由下面的行为断言覆盖。
USER_VISIBLE_SOURCES = [
    "static/js/i18n/smart-canvas.js",
    "static/js/i18n/global-settings.js",
    "static/js/global-canvas.js",
    "static/js/smart-canvas.js",
    "static/smart-canvas.html",
    "static/project-list.html",
    "static/global-settings.html",
    "data/asset_library.json",
]


class DefaultLibraryNamingTests(unittest.TestCase):
    def _lib(self, default_name, extra=None):
        libs = [{"id": "default", "name": default_name, "type": "asset", "categories": []}]
        if extra:
            libs.extend(extra)
        return {"active_library_id": "default", "libraries": libs, "categories": [], "updated_at": 0}

    def test_canonical_name_consistent_across_sources(self):
        """后端默认、前端兜底 i18n、种子数据三处用同一个规范名。"""
        # 后端默认库
        lib = main.default_asset_library()
        names = [l.get("name") for l in lib.get("libraries", []) if l.get("id") == "default"]
        self.assertEqual(names, [CANONICAL_DEFAULT_NAME], "后端默认库名必须是规范名")

        # 前端兜底文案（库数据为空时 smart-canvas.js 的 fallback 使用此 i18n）
        i18n = (ROOT / "static/js/i18n/smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn(
            '"smart.defaultAssetLibrary": { zh: "默认全局资源库"',
            i18n,
            "i18n 兜底文案必须与真实数据里的默认库名同一个词",
        )

        # 种子数据（全新安装的落盘默认值）
        seed = json.loads((ROOT / "data/asset_library.json").read_text(encoding="utf-8"))
        names = [l.get("name") for l in seed.get("libraries", []) if l.get("id") == "default"]
        self.assertEqual(names, [CANONICAL_DEFAULT_NAME], "种子数据默认库名必须是规范名")

    def test_no_legacy_display_names_in_user_visible_sources(self):
        """默认库的两个历史显示名不再出现在任何用户可见文案来源里。"""
        hits = []
        for rel in USER_VISIBLE_SOURCES:
            path = ROOT / rel
            if not path.exists():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for legacy in LEGACY_DISPLAY_NAMES:
                    if legacy in line:
                        hits.append(f"  {rel}:{lineno}: {legacy} | {line.strip()[:80]}")
        self.assertEqual(hits, [], "用户可见文案里仍有历史叫法：\n" + "\n".join(hits))

    def test_migrates_legacy_default_names(self):
        """存量默认库历史名在加载时被一次性迁移为规范名。"""
        for legacy in LEGACY_DISPLAY_NAMES:
            lib = main.normalize_asset_library(self._lib(legacy))
            default = next(l for l in lib["libraries"] if l["id"] == "default")
            self.assertEqual(default["name"], CANONICAL_DEFAULT_NAME, f"历史名「{legacy}」应被迁移为规范名")

    def test_preserves_user_renamed_default_library(self):
        """用户自己重命名的默认库不被迁移覆盖。"""
        lib = main.normalize_asset_library(self._lib("我的常用素材"))
        default = next(l for l in lib["libraries"] if l["id"] == "default")
        self.assertEqual(default["name"], "我的常用素材", "用户起的名字不得被覆盖")

    def test_does_not_touch_non_default_libraries(self):
        """迁移只认默认库的稳定身份 id='default'，非默认库即使同名也不动。"""
        lib = main.normalize_asset_library(
            self._lib("默认资产库", extra=[{"id": "lib_x", "name": "默认资产库", "type": "asset", "categories": []}])
        )
        by_id = {l["id"]: l for l in lib["libraries"]}
        self.assertEqual(by_id["default"]["name"], CANONICAL_DEFAULT_NAME)
        self.assertEqual(by_id["lib_x"]["name"], "默认资产库", "非默认库不受迁移影响")


if __name__ == "__main__":
    unittest.main()
