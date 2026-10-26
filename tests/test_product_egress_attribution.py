"""#232：产品自己的外连必须能在门证据里归属到发起进程。

#230 的进程树归属此前只在门 fixture（Python 子进程主动连外网被拒）里成立，
真实门跑里 by_method 全是 domain-fallback：产品真正的出口走浏览器和 node，
那些进程没有钩子。这条探针走产品自己的取远端素材函数（app.core.fetch_remote_media_bytes
里的 requests.get），跑在装了守卫钩子的进程里，于是这次外连必须留下带 pid 的
证据。它同时钉住两个方向：连不上（边界仍生效）+ 连不上之后证据里查得到是谁
（归属生效）。

域名用 RFC 2606 保留的 .invalid：脱离门单独跑这个测试也不会打到真实主机。
"""

from __future__ import annotations

import json
import os
import unittest

try:
    from app import core
except ModuleNotFoundError:  # pragma: no cover - 门 fixture 的最小仓库骨架没有 app/
    core = None

PROBE_HOST = "egress-attribution-probe.invalid"
PROBE_URL = f"https://{PROBE_HOST}/media.png"
_NO_APP = unittest.skipIf(core is None, "这个仓库骨架里没有 app/，没有产品路径可跑")


def _guard_event_file() -> str:
    return os.environ.get("FIREFLY_QUALITY_GATE_GUARD_EVENTS", "")


def _event_snapshot(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _events_after(path: str, offset: int) -> list[dict]:
    with open(path, "r", encoding="utf-8") as stream:
        stream.seek(offset)
        tail = stream.read()
    events = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


class ProductEgressAttributionProbe(unittest.TestCase):
    @_NO_APP
    def test_product_remote_media_fetch_is_denied(self):
        # 这条脱离门也要成立：产品拿 .invalid 取素材必须失败，不能静默成功。
        with self.assertRaises(Exception):
            core.fetch_remote_media_bytes(PROBE_URL, timeout=5.0)

    @_NO_APP
    @unittest.skipUnless(
        os.environ.get("FIREFLY_QUALITY_GATE") == "1",
        "守卫事件只在门跑里存在",
    )
    def test_denied_product_egress_carries_process_identity(self):
        path = _guard_event_file()
        self.assertTrue(path, "门跑里没有守卫事件文件")
        offset = _event_snapshot(path)

        with self.assertRaises(Exception):
            core.fetch_remote_media_bytes(PROBE_URL, timeout=5.0)

        events = _events_after(path, offset)
        hits = [event for event in events if PROBE_HOST in str(event.get("target", ""))]
        self.assertTrue(
            hits,
            f"产品外连没在守卫证据里留下目标记录：{events[:20]}",
        )
        # deny proxy 也会把同一次尝试记一份，但代理看不到进程，那份记录按
        # 构造就没有 pid（#230 当初要 pid 就是因为这个）。所以这里要的是
        # 「至少一条带完整进程身份」，而不是「每条都有」。
        identified = [
            event
            for event in hits
            if event.get("pid") and event.get("ppid") and event.get("exe")
        ]
        self.assertTrue(
            identified,
            f"产品外连没留下带进程身份（pid/ppid/exe）的记录：{hits}",
        )


if __name__ == "__main__":
    unittest.main()
