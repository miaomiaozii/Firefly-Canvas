"""#258: codex CLI 用量采集测试（run_codex_cli --json + codex_chat_text → ops.db）。

- run_codex_cli(capture_usage=True)：加 --json，解析 JSONL 最后一个 turn.completed.usage，
  文本兜底用 agent_message（不兜 JSONL 原文）
- 默认模式行为不变（无 --json，纯文本）
- codex_chat_text：turn 用量写 ops.db（provider=codex，别名归一 cached_input_tokens→cache_read）；
  无 project_id 跳过记录且不影响对话
"""
import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services import conversation

STUB = Path(__file__).resolve().parent / "stubs" / "fake_codex.cmd"


def _with_fake_codex(fn):
    old = main.codex_cli_executable
    main.codex_cli_executable = lambda: str(STUB)
    try:
        return fn()
    finally:
        main.codex_cli_executable = old


class RunCodexCliUsageTests(unittest.TestCase):
    def test_capture_usage_parses_turn_completed_jsonl(self):
        def fn():
            return asyncio.run(conversation.run_codex_cli('hello', model='gpt-5', capture_usage=True))
        raw = _with_fake_codex(fn)
        self.assertEqual(raw['text'], 'FAKE LAST')
        self.assertEqual(raw['_usage']['input_tokens'], 42)
        self.assertEqual(raw['_usage']['output_tokens'], 7)
        self.assertEqual(raw['_usage']['cached_input_tokens'], 3)

    def test_default_mode_unchanged(self):
        def fn():
            return asyncio.run(
                conversation.run_codex_cli('hello', model='gpt-5', output_last_message=False)
            )
        raw = _with_fake_codex(fn)
        self.assertEqual(raw['text'], 'FAKE PLAIN')  # 无 --json（默认行为不变）
        self.assertIsNone(raw['_usage'])

    def test_jsonl_message_fallback_without_last_file(self):
        def fn():
            return asyncio.run(
                conversation.run_codex_cli('hello', model='gpt-5', output_last_message=False, capture_usage=True)
            )
        raw = _with_fake_codex(fn)
        self.assertEqual(raw['text'], 'FAKE LAST')  # 兜 agent_message 而非 JSONL 原文


class CodexChatTextUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)
        self.project_id = self.store.create_project("Codex用量")["id"]
        # project_store_call 走全局单例：临时换成临时 store（SQLite 隔离，对照 test_project_versions）
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store

    def tearDown(self):
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.store.close()
        self.temp_dir.cleanup()

    def _payload(self, project_id):
        return types.SimpleNamespace(message='hi', model='gpt-5', project_id=project_id, system_prompt='')

    def test_codex_chat_text_records_usage_turn(self):
        def fn():
            return asyncio.run(conversation.codex_chat_text(self._payload(self.project_id), []))
        text, raw = _with_fake_codex(fn)
        self.assertEqual(text, 'FAKE LAST')
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary['turns'], 1)
        total = summary['total']
        self.assertEqual(total['input_tokens'], 42)
        self.assertEqual(total['output_tokens'], 7)
        self.assertEqual(total['cache_read_input_tokens'], 3)  # 别名归一
        model_row = summary['by_model'][0]
        self.assertEqual(model_row['provider'], 'codex')
        self.assertEqual(model_row['model'], 'gpt-5')
        self.assertIsNotNone(total['avg_duration_seconds'])

    def test_no_project_id_skips_record(self):
        def fn():
            return asyncio.run(conversation.codex_chat_text(self._payload(''), []))
        text, raw = _with_fake_codex(fn)
        self.assertEqual(text, 'FAKE LAST')  # 对话本身不受遥测影响


if __name__ == "__main__":
    unittest.main()
