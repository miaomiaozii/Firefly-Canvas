"""#224: 素材抽屉的名字格必须复用星标面板那套去歧义逻辑。

现象:#224 存量回填(tools/backfill_history_output_names.py)之后,素材名全是
生成器默认名 generated-N.png/.jpg —— 实测 1053 个输出项只有 14 个不同值,
generated-1.jpg 一种占 479 条。「已标记」面板已经改成带生成时间日期去歧义,
但素材抽屉的名字格还是直接拼 item.name,于是一排重复名字:同一个缺陷的第二个现场。

这里钉住方向(源码级契约,因为素材网格由 hub 直接 innerHTML,没法走单元 harness):
名字格必须调用 canvas-media-history.js 的 markedItemDisplayName,
不许在 hub 里另写一套显示名逻辑。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HUB = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
MODULE = (ROOT / "static" / "js" / "canvas-media-history.js").read_text(encoding="utf-8")


class AssetDisplayNameContractTests(unittest.TestCase):
    def test_module_owns_the_shared_display_name_logic(self):
        # 去歧义逻辑只有一个实现处：模块里的 markedItemDisplayName + 默认名模式。
        self.assertRegex(MODULE, r"function markedItemDisplayName\(")
        self.assertIn("GENERATOR_DEFAULT_NAME_PATTERN", MODULE)
        self.assertEqual(
            1,
            sum(1 for source in (HUB, MODULE) if "GENERATOR_DEFAULT_NAME_PATTERN =" in source),
            "生成器默认名的判定只能有一份实现",
        )

    def test_asset_grid_name_cell_delegates_to_shared_helper(self):
        lines = [line for line in HUB.splitlines() if 'class="asset-name"' in line]
        self.assertTrue(lines, "没找到素材抽屉的名字格，改动前先看渲染点还在不在")
        for line in lines:
            self.assertIn(
                "markedItemDisplayName(",
                line,
                "素材抽屉的名字格必须调用 markedItemDisplayName，别再直接拼 item.name",
            )

    def test_asset_grid_title_matches_visible_text(self):
        # title 与可见文本必须同源，否则悬停看到的和列表里的不一样，
        # 去歧义后缀（时间日期）在悬停里就丢了。
        for line in HUB.splitlines():
            if 'class="asset-name"' not in line:
                continue
            title = line.split('class="asset-name"', 1)[1]
            self.assertEqual(
                title.count("markedItemDisplayName("), 2,
                f"title 和文本要用同一个函数各一次：{title[:200]}",
            )

    def test_asset_display_name_passes_the_asset_id(self):
        # 没有时间信息时会退回 asset_id 短前缀，所以调用时必须把 id 传进去。
        # 素材抽屉的条目用 item.id（不是 item.asset_id）。
        for line in HUB.splitlines():
            if 'class="asset-name"' not in line:
                continue
            self.assertRegex(
                line,
                r"markedItemDisplayName\(\{[^}]*asset_id:\s*(item\.(?:id|asset_id))",
                "调用处必须带上 asset_id，否则同分钟的同名素材分不开",
            )
            break


if __name__ == "__main__":
    unittest.main()
