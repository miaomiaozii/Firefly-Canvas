import json
import unittest
from unittest.mock import AsyncMock

from main import ConnectionManager


class CanvasUpdatedBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = ConnectionManager()
        self.socket = AsyncMock()
        self.manager.active_connections.append(self.socket)

    async def test_legacy_wire_format_and_source(self):
        for client_id, source in (("", ""), ("tab-一", "agent"), (None, None)):
            with self.subTest(client_id=client_id, source=source):
                self.socket.send_text.reset_mock()
                await self.manager.broadcast_canvas_updated("画布", 123, client_id, source)
                raw = self.socket.send_text.call_args.args[0]
                old_payload = {
                    "type": "canvas_updated",
                    "canvas_id": "画布",
                    "updated_at": 123,
                    "client_id": client_id or "",
                    "source": source or "",
                }
                self.assertEqual(raw, json.dumps(old_payload))
                self.assertTrue({"op_id", "touched_node_ids", "phase"}.isdisjoint(json.loads(raw)))

    async def test_optional_fields_round_trip(self):
        for phase in ("start", "end", "undo"):
            with self.subTest(phase=phase):
                self.socket.send_text.reset_mock()
                await self.manager.broadcast_canvas_updated(
                    "canvas", 456, source="agent", op_id="op-一",
                    touched_node_ids=["node-一", "node-2"], phase=phase,
                )
                self.assertEqual(json.loads(self.socket.send_text.call_args.args[0]), {
                    "type": "canvas_updated", "canvas_id": "canvas", "updated_at": 456,
                    "client_id": "", "source": "agent", "op_id": "op-一",
                    "touched_node_ids": ["node-一", "node-2"], "phase": phase,
                })

    async def test_empty_optional_values_are_not_omitted(self):
        await self.manager.broadcast_canvas_updated("canvas", 1, op_id="", touched_node_ids=[])
        payload = json.loads(self.socket.send_text.call_args.args[0])
        self.assertEqual(payload["op_id"], "")
        self.assertEqual(payload["touched_node_ids"], [])
        self.assertNotIn("phase", payload)

    async def test_invalid_phase_rejected_without_sending(self):
        for phase in ("", "START", "other", 0):
            with self.subTest(phase=phase):
                with self.assertRaises(ValueError):
                    await self.manager.broadcast_canvas_updated("canvas", 1, phase=phase)
                self.socket.send_text.assert_not_called()

    async def test_new_fields_are_keyword_only(self):
        with self.assertRaises(TypeError):
            await self.manager.broadcast_canvas_updated("canvas", 1, "", "", "op")
        self.socket.send_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
