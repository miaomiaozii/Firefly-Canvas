"""#217 时序守卫：孤儿媒体归档不得跑在「删除已落盘」之前。

旧实现里归档由 canvas-media-history.js 自己的 ARCHIVE_DELAY_MS=2500 计时器触发，
比 smart-canvas.js 的 DEBOUNCED_SAVE_MS=3000 保存防抖早 500ms。归档请求到达时
服务端 canvas 仍引用着被删资产 → 409 → cascadeArchiveDeletedMedia 的 catch 吞掉
→ 回收区永远没有条目，且没有任何地方报错。

这类 bug 的形状是「两个文件里的魔法数互相赛跑」，改一个常量就破，而且没有行为
测试会变红。所以这里钉三件事：

1. ARCHIVE_DELAY_MS 的数值下界（有人把它调回 2500 就红）。
2. 产品代码里没有任何计时器使用 ARCHIVE_DELAY_MS（有人恢复计时器式归档就红——
   这才是真正的回归形状，光钉数值挡不住）。
3. 归档触发点留在保存成功路径上（markAutosaveSaved → flushPendingMediaArchives）。
"""

import re
import unittest

from tests.canvas_frontend_sources import ROOT


HISTORY_JS = ROOT / "static" / "js" / "canvas-media-history.js"
SMART_JS = ROOT / "static" / "js" / "smart-canvas.js"


def _code_only(source: str) -> str:
    """去掉整行 // 注释，避免注释里提到的常量名被当成代码引用。"""
    return "\n".join(line.split("//", 1)[0] for line in source.splitlines())


def _constant(source: str, name: str) -> int:
    match = re.search(rf"\bconst {name} = (\d+);", source)
    if match is None:
        raise AssertionError(f"未找到常量 {name}——守卫假设的常量被改名或删掉了")
    return int(match.group(1))


def _function_body(source: str, name: str) -> str:
    start = source.find(f"function {name}(")
    if start == -1:
        raise AssertionError(f"未找到函数 {name}()")
    nxt = source.find("\nfunction ", start + 1)
    return source[start:] if nxt == -1 else source[start:nxt]


class OrphanMediaArchiveGuard(unittest.TestCase):
    def test_archive_delay_is_not_below_save_debounce(self):
        history = _code_only(HISTORY_JS.read_text(encoding="utf-8"))
        smart = _code_only(SMART_JS.read_text(encoding="utf-8"))
        archive_delay = _constant(history, "ARCHIVE_DELAY_MS")
        save_debounce = _constant(smart, "DEBOUNCED_SAVE_MS")
        self.assertGreaterEqual(
            archive_delay,
            save_debounce,
            f"ARCHIVE_DELAY_MS={archive_delay} 低于 DEBOUNCED_SAVE_MS={save_debounce}："
            "归档会比保存早发出，服务端 canvas 仍引用被删资产 → 409 → 回收区无条目（#217）",
        )

    def test_no_timer_uses_archive_delay(self):
        # 钉数值挡不住「换个更大的计时器继续抢跑」。真正要禁的是计时器本身。
        history = _code_only(HISTORY_JS.read_text(encoding="utf-8"))
        smart = _code_only(SMART_JS.read_text(encoding="utf-8"))
        for label, source in (("canvas-media-history.js", history), ("smart-canvas.js", smart)):
            uses = [
                line.strip()
                for line in source.splitlines()
                if "ARCHIVE_DELAY_MS" in line and not line.strip().startswith("const")
            ]
            self.assertEqual(
                uses,
                [],
                f"{label} 里有用到 ARCHIVE_DELAY_MS 的代码：{uses}——"
                "归档必须只在保存成功后触发，任何用它做计时器的写法都会重现 #217",
            )

    def test_schedule_only_registers_and_does_not_arm_a_timer(self):
        body = _code_only(_function_body(HISTORY_JS.read_text(encoding="utf-8"), "scheduleMediaArchive"))
        self.assertNotIn(
            "setTimeout",
            body,
            "scheduleMediaArchive 里出现 setTimeout：归档又回到自己计时器上（#217）",
        )
        self.assertIn("pendingMediaArchives.push", body)

    def test_archive_flush_hangs_off_save_success(self):
        smart = SMART_JS.read_text(encoding="utf-8")
        body = _code_only(_function_body(smart, "markAutosaveSaved"))
        self.assertIn(
            "flushPendingMediaArchives()",
            body,
            "markAutosaveSaved 不再触发 flushPendingMediaArchives：归档失去唯一的安全触发点，"
            "会退回到抢跑式计时器（#217）",
        )


if __name__ == "__main__":
    unittest.main()
