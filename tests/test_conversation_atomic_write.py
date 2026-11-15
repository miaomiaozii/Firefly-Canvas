"""#383：per-user 对话文件也必须原子写，坏文件不许被静默吞掉。

写侧原来是 `open(path,'w') + json.dump`（`app/services/conversation.py:144`），打开那一刻
就把旧文件清空；读侧 `load_conversation`（`:7`）对半份 JSON 直接抛 `JSONDecodeError`（到
HTTP 层就是 500），`list_conversations`（`:162`）则 `except Exception: continue` 一声不吭。

读侧语义按频道里 reviewer 定的做法（与 #345、#374 同一套，不另起）：读不出的文件先交给
`firefly_json_io.read_json` 改名 `.corrupt-<UTC>` 保住字节 + 记 ERROR/诊断，再按「文件不
存在」继续；不抛异常、不弹 5xx。

**核过的事实（grep 全仓）**：`load_conversation`、`list_conversations`、`new_conversation`
在 `app/` 和 `static/js` 里都没有调用者，只在 `app/services/_base.py:64 _DYN_NAMES` 里注册
进动态表——这条路径目前是死代码。本票仍按票面改掉（它挂在公共表上，随时可能被接回去），
「要不要删掉这套死代码」单独报 reviewer，不在这里判断。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io
from app.services import conversation as s_conversation

import main


class ConversationDirMixin:
    """把 CONVERSATION_DIR 指到临时目录，别碰用户真实的对话文件。"""

    USER_ID = "u-383"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        firefly_json_io.clear_data_diagnostics()
        self._dir_patch = patch.object(main, "CONVERSATION_DIR", str(self.root))
        self._dir_patch.start()
        self.path = Path(s_conversation.conversation_path(self.USER_ID, "conv-1"))

    def tearDown(self):
        self._dir_patch.stop()
        firefly_json_io.clear_data_diagnostics()
        self.temp.cleanup()

    def diagnostics(self):
        return firefly_json_io.recent_data_diagnostics(limit=20)

    def corrupt_backups(self):
        return sorted(p.name for p in self.path.parent.iterdir() if ".corrupt-" in p.name)

    def staging_leftovers(self):
        return sorted(p.name for p in self.path.parent.iterdir() if p.name.startswith("."))


class ConversationWriteTests(ConversationDirMixin, unittest.TestCase):
    """验收①：写到一半被中断，对话文件保持原样，不留临时文件。"""

    def setUp(self):
        super().setUp()
        self.original = json.dumps(
            {"id": "conv-1", "title": "旧标题", "messages": [{"role": "user", "content": "旧内容"}]},
            ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.original, encoding="utf-8")

    def test_interrupted_write_keeps_previous_conversation(self):
        def half_dump(value, handle, **_kwargs):
            handle.write('{"id": "conv-1", "title": "半份')
            handle.flush()
            raise OSError("写入中途被中断")

        with patch.object(firefly_json_io.json, "dump", half_dump):
            with self.assertRaises(OSError):
                s_conversation.save_conversation(
                    self.USER_ID, {"id": "conv-1", "title": "新标题", "messages": []})
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original,
                         "写入中断时不该改动已存的对话文件")
        self.assertEqual(self.staging_leftovers(), [], "临时文件必须清干净")

    def test_successful_write_round_trips(self):
        conversation = {"id": "conv-1", "title": "新标题", "messages": [{"role": "user", "content": "新内容"}]}
        s_conversation.save_conversation(self.USER_ID, conversation)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved.get("title"), "新标题", f"对话应原样存下，实际：{saved}")
        self.assertEqual(self.staging_leftovers(), [], "成功写入后不该留临时文件")

    def test_writer_does_not_open_the_target_file_truncating(self):
        """票面验收③的写法守卫：`save_conversation` 里不许再出现截断式打开。

        注抵里引的旧写法不算（#374 踩过的坑：正则会把注抵里的代码片段当成写法），
        所以先剔掉整行注抵再扫。
        """
        source = Path(s_conversation.__file__).read_text(encoding="utf-8")
        block = source.split("def save_conversation(", 1)[1].split("\ndef ", 1)[0]
        code = "\n".join(line for line in block.splitlines() if not line.strip().startswith("#"))
        self.assertNotIn("open(", code, f"save_conversation 不该自己 open 目标文件：{code}")
        self.assertIn("write_json_atomic", code, "save_conversation 应走 firefly_json_io.write_json_atomic")


class ConversationReadTests(ConversationDirMixin, unittest.TestCase):
    """验收②：读不出的对话文件先隔离保住字节，再按「不存在」继续，不抛异常。"""

    def test_corrupt_conversation_is_quarantined_not_raised(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        half = '{"id": "conv-1", "title": "半份'
        self.path.write_text(half, encoding="utf-8")

        loaded = s_conversation.load_conversation(self.USER_ID, "conv-1")

        self.assertEqual(len(self.corrupt_backups()), 1, f"应恰好改名保住一份，实际：{self.corrupt_backups()}")
        preserved = (self.path.parent / self.corrupt_backups()[0]).read_text(encoding="utf-8")
        self.assertEqual(preserved, half, "隔离文件必须一字不差保住原字节")
        self.assertFalse(self.path.exists(), "原路径不该再留着坏文件")
        self.assertIsInstance(loaded, dict, f"读不出时应按「不存在」返回会话骨架，实际：{loaded!r}")
        self.assertEqual(loaded.get("messages"), [], f"骨架应是空消息，实际：{loaded}")
        self.assertEqual(loaded.get("id"), "conv-1", f"骨架应带请求的对话 ID，实际：{loaded}")
        kinds = [item.get("kind") for item in self.diagnostics()]
        self.assertIn(firefly_json_io.DIAGNOSTIC_KIND_CORRUPT, kinds,
                      f"坏文件必须进诊断（不许静默），实际诊断：{self.diagnostics()}")

    def test_missing_conversation_still_reports_404(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            s_conversation.load_conversation(self.USER_ID, "nope")
        self.assertEqual(caught.exception.status_code, 404,
                         "文件不存在仍应是 404，不该被这次的改动改成 200")

    def test_good_conversation_still_loads(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"id": "conv-1", "title": "标题", "messages": []}, ensure_ascii=False),
                             encoding="utf-8")
        loaded = s_conversation.load_conversation(self.USER_ID, "conv-1")
        self.assertEqual(loaded.get("title"), "标题")
        self.assertEqual(self.corrupt_backups(), [], "好文件不该被隔离")

    def test_list_conversations_reports_the_corrupt_one_instead_of_skipping_silently(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        good = self.path.parent / "good.json"
        good.write_text(json.dumps({"id": "good", "title": "好对话", "updated_at": 5, "messages": []},
                                   ensure_ascii=False), encoding="utf-8")
        broken = self.path.parent / "broken.json"
        broken.write_text("{", encoding="utf-8")

        records = s_conversation.list_conversations(self.USER_ID)

        self.assertEqual([item["id"] for item in records], ["good"],
                         f"坏文件不该进列表，但好对话必须照常列出，实际：{records}")
        self.assertFalse(broken.exists(), "坏文件应被改名隔离，不该留在原地")
        quarantined = [p.name for p in self.path.parent.iterdir() if "broken.json.corrupt-" in p.name]
        self.assertEqual(len(quarantined), 1, f"应保住坏文件字节，实际：{sorted(p.name for p in self.path.parent.iterdir())}")
        self.assertEqual((self.path.parent / quarantined[0]).read_text(encoding="utf-8"), "{",
                         "隔离副本必须原样保住字节")
        paths = [item.get("file", "") for item in self.diagnostics()]
        self.assertTrue(any("broken.json" in str(path) for path in paths),
                        f"跳过坏文件必须留下诊断记录，实际诊断：{self.diagnostics()}")


class EvidenceWriterAtomicityTests(unittest.TestCase):
    """票面点名的第二处：`tools/ui_integrity_evidence.py` 的证据 JSON 也是截断式写。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name) / "evidence.json"
        self.original = json.dumps({"status": "passed", "old": True}, ensure_ascii=False, indent=2) + "\n"
        self.target.write_text(self.original, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_interrupted_write_keeps_previous_evidence(self):
        from tools import ui_integrity_evidence as t_evidence

        def half_dump(value, handle, **_kwargs):
            handle.write('{"status": "半份')
            handle.flush()
            raise OSError("写入中途被中断")

        with patch.object(firefly_json_io.json, "dump", half_dump):
            with self.assertRaises(OSError):
                t_evidence.write_json(self.target, {"status": "failed", "new": True})
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.original,
                         "证据文件不该在半份写入时被改坏")

    def test_successful_write_round_trips(self):
        from tools import ui_integrity_evidence as t_evidence
        t_evidence.write_json(self.target, {"status": "passed", "n": 3})
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")).get("n"), 3)


if __name__ == "__main__":
    unittest.main()
