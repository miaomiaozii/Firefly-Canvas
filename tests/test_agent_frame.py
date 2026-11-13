"""Refs #323 票 E：会话 frame 落位 + reference frame 素材注入（服务端）。

覆盖：compute_frame_slot/frame_capacity 网格数学与自动长高（监理钉②）、
create_node_core 的 chatFrameId 落位链（含显式 x/y 尊重、悬挂降级）、
_collect_reference_images 护栏（≤8 张/5MB/路径失败不炸整轮=监理钉③/视频）、
user_prompt refs 元数据（监理钉①）、insert_ref_images codex argv 插入（0.162 实测 -i 语法）。
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services import agent_frame
from app.services.agent_host import agent_host_manager
from app.services.agent_adapters.codex_adapter import CodexAdapter, insert_ref_images
from app.routers.api_agent_tools import create_node_core


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FrameSlotMath(unittest.TestCase):
    def test_slot_grid_and_grow(self):
        # w=800 → cols=(800-40+16)//256=3；h=360 → rows=(360-40-40+16)//156=1 → 容量 3
        frame = {"x": 100, "y": 100, "w": 800, "h": 360}
        dx, dy, new_h = agent_frame.compute_frame_slot(frame, 0)
        self.assertEqual((dx, dy), (20, 60))
        self.assertEqual(new_h, 360)  # 不缩小
        dx1, dy1, _ = agent_frame.compute_frame_slot(frame, 1)
        self.assertEqual((dx1, dy1), (20 + 256, 60))
        dx3, dy3, grown = agent_frame.compute_frame_slot(frame, 3)  # 第 4 个 → 第二行
        self.assertEqual((dx3, dy3), (20, 60 + 156))
        self.assertEqual(grown, 60 + 156 + 140 + 20)  # 按整行向下扩张（钉②）
        self.assertEqual(agent_frame.frame_capacity(frame), 3)

    def test_degenerate_frame_no_crash(self):
        dx, dy, h = agent_frame.compute_frame_slot({"w": 0, "h": 0}, 0)
        self.assertEqual((dx, dy), (20, 60))
        self.assertGreater(h, 0)
        self.assertEqual(agent_frame.frame_capacity({}), 0)


class CreateNodePlacement(unittest.TestCase):
    """create_node_core：agent.chatFrameId → frame 网格落位 + items 追加。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fly-frame-test-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.prev = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.pid = self.store.create_project("frame test")["id"]
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "agent-1", "type": "smart-agent",
                                           "x": 0, "y": 0, "chatFrameId": "group-1"}},
            {"op": "upsert_node", "node": {"id": "group-1", "type": "smart-group",
                                           "x": 100, "y": 100, "w": 800, "h": 360, "items": []}},
        ], base_revision=0)

    def tearDown(self):
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.prev
        self.temp.cleanup()

    def _nodes(self):
        return {n["id"]: n for n in self.store.canvas(self.pid)["nodes"]}

    def test_sequential_slots_and_items(self):
        r1 = run_async(create_node_core(self.pid, {"type": "smart-image"}, agent_node_id="agent-1"))
        r2 = run_async(create_node_core(self.pid, {"type": "smart-image"}, agent_node_id="agent-1"))
        nodes = self._nodes()
        self.assertEqual((nodes[r1["node_id"]]["x"], nodes[r1["node_id"]]["y"]), (120, 160))
        self.assertEqual((nodes[r2["node_id"]]["x"], nodes[r2["node_id"]]["y"]), (376, 160))
        self.assertEqual(self._nodes()["group-1"]["items"], [r1["node_id"], r2["node_id"]])

    def test_explicit_position_respected(self):
        r = run_async(create_node_core(self.pid, {"type": "t", "x": 5, "y": 7}, agent_node_id="agent-1"))
        n = self._nodes()[r["node_id"]]
        self.assertEqual((n["x"], n["y"]), (5, 7))
        self.assertEqual(self._nodes()["group-1"]["items"], [])  # 未进 frame

    def test_frame_row_grow(self):
        for _ in range(4):  # 容量 3 → 第 4 个把 h 从 360 长到 376
            run_async(create_node_core(self.pid, {"type": "t"}, agent_node_id="agent-1"))
        self.assertEqual(self._nodes()["group-1"]["h"], 376)

    def test_no_or_dangling_frame_defaults(self):
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "agent-2", "type": "smart-agent", "x": 0, "y": 0}},
            {"op": "upsert_node", "node": {"id": "agent-3", "type": "smart-agent", "x": 0, "y": 0,
                                           "chatFrameId": "ghost"}},
        ], base_revision=self.store.canvas(self.pid)["revision"])
        r1 = run_async(create_node_core(self.pid, {"type": "t"}, agent_node_id="agent-2"))
        r2 = run_async(create_node_core(self.pid, {"type": "t"}, agent_node_id="agent-3"))
        nodes = self._nodes()
        self.assertEqual((nodes[r1["node_id"]]["x"], nodes[r1["node_id"]]["y"]), (0, 0))
        self.assertEqual((nodes[r2["node_id"]]["x"], nodes[r2["node_id"]]["y"]), (0, 0))


class _FakeStore:
    def __init__(self, canvas, records, fail_paths=()):
        self._canvas = canvas
        self._records = records
        self._fail = set(fail_paths)

    def canvas(self, pid):
        return self._canvas

    def media_record(self, pid, asset_id):
        if asset_id in self._fail:
            raise RuntimeError("项目媒体不存在")
        return {"path": self._records[asset_id]}


class RefCollectionGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fly-ref-test-")
        self.root = Path(self.temp.name)
        self.png = self.root / "a.png"
        self.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        self.big = self.root / "big.png"
        with open(self.big, "wb") as f:
            f.seek(agent_frame.REF_MAX_IMAGE_BYTES)
            f.write(b"1")
        members = []
        records = {"assetbig": str(self.big)}
        # 顺序有意：巨图/悬挂资产排在配额前，先触各自护栏再触配额
        members += [
            {"id": "big", "type": "smart-image", "title": "巨图",
             "images": [{"kind": "image", "asset_id": "assetbig"}]},
            {"id": "gone", "type": "smart-image", "title": "悬挂资产",
             "images": [{"kind": "image", "asset_id": "bad"}]},
        ]
        records["bad"] = str(self.root / "missing.png")
        for i in range(9):  # 9 张正常图 → 注入至上限 8 后最后 1 张超配额跳过
            p = self.root / f"m{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\nx")
            records[f"asset{i}"] = str(p)
            members.append({"id": f"img{i}", "type": "smart-image", "title": f"图{i}",
                            "images": [{"kind": "image", "asset_id": f"asset{i}"}]})
        members += [
            {"id": "vid", "type": "smart-video", "title": "视频A",
             "images": [{"kind": "video", "asset_id": "asset0"}]},
        ]
        canvas = {"nodes": [
            {"id": "ag", "type": "smart-agent", "refFrameId": "fr"},
            {"id": "fr", "type": "smart-group", "items": [m["id"] for m in members] + ["nope"]},
            *members,
        ]}
        self.prev = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = _FakeStore(canvas, records)
        self.session = SimpleNamespace(project_id="p", node_id="ag")

    def tearDown(self):
        main.FIREFLY_PROJECT_STORE = self.prev
        self.temp.cleanup()

    def test_guards(self):
        images, skipped = agent_host_manager._collect_reference_images(self.session)
        self.assertEqual(len(images), agent_frame.REF_MAX_IMAGES)  # 上限 8
        reasons = {s["reason"] for s in skipped}
        self.assertIn(f"超单轮 {agent_frame.REF_MAX_IMAGES} 张上限", reasons)
        self.assertIn("视频本轮不注入", reasons)
        self.assertIn(f"单图超 {agent_frame.REF_MAX_IMAGE_BYTES // 1_000_000}MB", reasons)
        self.assertIn("路径解析失败", reasons)
        self.assertIn("节点不存在", reasons)
        self.assertTrue(all(i["mime"] == "image/png" for i in images))
        payload = agent_host_manager._pi_image_payload(images[0])
        self.assertEqual(payload["type"], "image")
        self.assertTrue(payload["data"])

    def test_no_binding_empty(self):
        s = SimpleNamespace(project_id="p", node_id="ag")
        main.FIREFLY_PROJECT_STORE = _FakeStore(
            {"nodes": [{"id": "ag", "type": "smart-agent"}]}, {})
        images, skipped = agent_host_manager._collect_reference_images(s)
        self.assertEqual((images, skipped), ([], []))


class CodexArgvInjection(unittest.TestCase):
    def test_insert_before_stdin_dash(self):
        adapter = CodexAdapter()
        base = ["/bin/codex", "exec", "--json", "-"]
        out = insert_ref_images(base, ["C:/m/a.png", "C:/m/b.png"])
        self.assertEqual(out, ["/bin/codex", "exec", "--json",
                               "-i", "C:/m/a.png", "-i", "C:/m/b.png", "-"])
        self.assertEqual(insert_ref_images(base, []), base)
        self.assertEqual(insert_ref_images(["codex", "exec"], ["x"]), ["codex", "exec"])


if __name__ == "__main__":
    unittest.main()
