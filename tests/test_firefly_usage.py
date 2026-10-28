"""LLM 用量/成本遥测单测（issue #258：normalize/merge 移植 + ops.db usage 租户）。

纯函数（firefly_usage）：
- normalize_usage：别名归一（OpenAI 风格 / ACP camelCase / pi SDK 短键）、非数字丢弃、
  total_tokens 默认 = input + output、cost round 6 位
- merge_usage：跨 turn 累加（cost 缺省不覆盖、有则累加）
- parse_codex_jsonl_usage：JSONL 扫描（噪声行忽略）、最后一个 turn.completed 生效、agent_message 兜底
- collect_pi_turn_usage：agent_end messages 中 assistant 消息 usage 求和（cost.total 提取）
store 层（ProjectUsageMixin）：
- record_usage_turn 写 ops.db usage_turns（additive 迁移 usage_001 走 ops_migrations）
- 租户隔离：每项目独立 ops.db 文件 + project_id 过滤；A 项目行不可见于 B 项目
- 全 0 用量不记；无项目 KeyError；list_usage_summary 聚合（total/by_day/by_model/days 过滤）
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore
from firefly_usage import (
    USAGE_CANONICAL_KEYS,
    collect_pi_turn_usage,
    merge_usage,
    normalize_usage,
    parse_codex_jsonl_usage,
    usage_has_data,
)


class NormalizeUsageTests(unittest.TestCase):
    def test_openai_style_aliases(self):
        """ComfyArt 原表：OpenAI 风格别名。"""
        usage = normalize_usage({
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cached_input_tokens": 3,
            "cache_creation_input_tokens": 2,
        })
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 5)
        self.assertEqual(usage["cache_read_input_tokens"], 3)
        self.assertEqual(usage["cache_creation_input_tokens"], 2)
        self.assertIsNone(usage["cost_usd"])

    def test_acp_camel_case_aliases(self):
        usage = normalize_usage({
            "inputTokens": 100,
            "outputTokens": 20,
            "cachedReadTokens": 40,
            "cachedWriteTokens": 15,
        })
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["cache_read_input_tokens"], 40)
        self.assertEqual(usage["cache_creation_input_tokens"], 15)

    def test_pi_sdk_short_keys(self):
        """pi SDK Usage：input/output/cacheRead/cacheWrite/totalTokens。"""
        usage = normalize_usage({
            "input": 7,
            "output": 3,
            "cacheRead": 6,
            "cacheWrite": 1,
            "totalTokens": 10,
            "cacheWrite1h": 2,
        })
        self.assertEqual(usage["input_tokens"], 7)
        self.assertEqual(usage["output_tokens"], 3)
        self.assertEqual(usage["cache_read_input_tokens"], 6)
        # cacheWrite 与 cacheWrite1h 都归一 cache_creation，后者覆盖前者（同值求和语义见 merge）
        self.assertEqual(usage["cache_creation_input_tokens"], 2)
        self.assertEqual(usage["total_tokens"], 10)

    def test_non_numbers_and_unknown_keys_dropped(self):
        usage = normalize_usage({
            "input_tokens": "many",
            "output_tokens": True,  # bool 不算数字
            "cache_read_input_tokens": None,
            "weird_key": 99,
            "prompt_tokens": 4,
        })
        self.assertEqual(usage["input_tokens"], 4)  # prompt_tokens=4 有效；"many"/bool/None 丢弃
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cache_read_input_tokens"], 0)

    def test_total_tokens_defaults_to_input_plus_output(self):
        usage = normalize_usage({"prompt_tokens": 12, "completion_tokens": 8})
        self.assertEqual(usage["total_tokens"], 20)

    def test_cost_usd_rounded_six(self):
        usage = normalize_usage({"input_tokens": 1}, cost_usd=0.123456789)
        self.assertEqual(usage["cost_usd"], 0.123457)

    def test_cost_usd_invalid_ignored(self):
        self.assertIsNone(normalize_usage({"input_tokens": 1}, cost_usd="free")["cost_usd"])
        self.assertIsNone(normalize_usage({"input_tokens": 1}, cost_usd=True)["cost_usd"])

    def test_empty_and_none(self):
        usage = normalize_usage(None)
        for key in USAGE_CANONICAL_KEYS:
            self.assertEqual(usage[key], 0)
        self.assertIsNone(usage["cost_usd"])
        self.assertFalse(usage_has_data(usage))


class MergeUsageTests(unittest.TestCase):
    def test_accumulates_across_turns(self):
        total = merge_usage(None, {"prompt_tokens": 10, "completion_tokens": 5})
        total = merge_usage(total, {"input_tokens": 2, "output_tokens": 3})
        self.assertEqual(total["input_tokens"], 12)
        self.assertEqual(total["output_tokens"], 8)
        self.assertEqual(total["total_tokens"], 20)

    def test_cost_accumulates_and_keeps_when_absent(self):
        total = merge_usage(None, {"input_tokens": 1}, 0.001)
        self.assertEqual(total["cost_usd"], 0.001)
        total = merge_usage(total, {"input_tokens": 1})  # 无 cost：保留旧值
        self.assertEqual(total["cost_usd"], 0.001)
        total = merge_usage(total, {"input_tokens": 1}, 0.002)
        self.assertEqual(total["cost_usd"], 0.003)

    def test_none_total_starts_fresh(self):
        total = merge_usage(None, None)
        self.assertEqual(total["input_tokens"], 0)


class ParseCodexJsonlTests(unittest.TestCase):
    def test_extracts_last_turn_completed_usage(self):
        lines = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "world"}}),
            json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 20, "output_tokens": 7, "cached_input_tokens": 1}}),
        ]
        usage, message = parse_codex_jsonl_usage("\n".join(lines))
        self.assertEqual(usage["input_tokens"], 20)  # 最后一个 turn.completed 生效
        self.assertEqual(usage["output_tokens"], 7)
        self.assertEqual(message, "world")

    def test_noise_lines_and_bad_json_ignored(self):
        text = "some banner text\n{not json}\n" + json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}
        ) + "\n\n"
        usage, message = parse_codex_jsonl_usage(text)
        self.assertEqual(usage["input_tokens"], 3)
        self.assertEqual(message, "")

    def test_no_turn_completed(self):
        usage, message = parse_codex_jsonl_usage(json.dumps({"type": "turn.failed", "error": "x"}))
        self.assertIsNone(usage)
        self.assertEqual(message, "")

    def test_turn_completed_without_usage_keeps_none(self):
        usage, _ = parse_codex_jsonl_usage(json.dumps({"type": "turn.completed"}))
        self.assertIsNone(usage)


class CollectPiTurnUsageTests(unittest.TestCase):
    def test_sums_assistant_messages_with_cost_total(self):
        messages = [
            {"role": "user", "content": "hi", "usage": {"input": 999}},
            {"role": "assistant", "content": "a", "usage": {
                "input": 10, "output": 4, "cacheRead": 5, "cacheWrite": 1, "totalTokens": 14,
                "cost": {"input": 0.0001, "output": 0.0002, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0003}}},
            {"role": "assistant", "content": "b", "usage": {
                "input": 2, "output": 1, "totalTokens": 3,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0.0001}}},
        ]
        total = collect_pi_turn_usage(messages)
        self.assertEqual(total["input_tokens"], 12)
        self.assertEqual(total["output_tokens"], 5)
        self.assertEqual(total["cache_read_input_tokens"], 5)
        self.assertEqual(total["cache_creation_input_tokens"], 1)
        self.assertEqual(total["cost_usd"], 0.0004)

    def test_ignores_non_assistant_and_missing_usage(self):
        total = collect_pi_turn_usage([{"role": "user"}, {"role": "assistant"}])
        self.assertEqual(total["input_tokens"], 0)
        self.assertFalse(usage_has_data(total))

    def test_non_list_input(self):
        self.assertEqual(collect_pi_turn_usage(None)["input_tokens"], 0)


def _usage_row(**overrides):
    row = {
        "id": 1,
        "project_id": "p1",
        "provider": "pi",
        "model": "test/model",
        "node_id": "n1",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 2,
        "cache_creation_input_tokens": 0,
        "total_tokens": 15,
        "cost_usd": None,
        "duration_seconds": 1.234,
        "created_at": "2026-07-15T08:00:00Z",
    }
    row.update(overrides)
    return row


class ProjectUsageStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=60.0)
        self.project_id = self.store.create_project("用量测试")["id"]

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_record_creates_ops_db_and_row(self):
        row_id = self.store.record_usage_turn(
            self.project_id,
            provider="pi",
            model="anthropic/claude-test",
            node_id="n1",
            usage={"input": 10, "output": 5, "cacheRead": 2, "totalTokens": 15},
            duration_seconds=1.234,
        )
        self.assertIsNotNone(row_id)
        db_path = self.root / self.project_id / "ops.db"
        self.assertTrue(db_path.is_file())
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            self.assertIn("usage_001", {r["key"] for r in conn.execute("SELECT key FROM ops_migrations")})
            row = conn.execute("SELECT * FROM usage_turns WHERE id = ?", (row_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["provider"], "pi")
            self.assertEqual(row["model"], "anthropic/claude-test")
            self.assertEqual(row["input_tokens"], 10)
            self.assertEqual(row["output_tokens"], 5)
            self.assertEqual(row["cache_read_input_tokens"], 2)
            self.assertEqual(row["total_tokens"], 15)
            self.assertEqual(row["duration_seconds"], 1.234)
        finally:
            conn.close()

    def test_alias_normalized_before_insert(self):
        self.store.record_usage_turn(
            self.project_id, provider="codex", model="gpt-5",
            usage={"prompt_tokens": 100, "completion_tokens": 30, "cached_input_tokens": 10},
        )
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["total"]["input_tokens"], 100)
        self.assertEqual(summary["total"]["output_tokens"], 30)
        self.assertEqual(summary["total"]["cache_read_input_tokens"], 10)
        self.assertEqual(summary["total"]["total_tokens"], 130)

    def test_cost_usd_persisted_and_aggregated(self):
        self.store.record_usage_turn(
            self.project_id, provider="pi", model="m",
            usage={"input": 1, "output": 1, "cost_usd": 0.000123456},
        )
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary["total"]["cost_usd"], 0.000123)

    def test_zero_usage_not_recorded(self):
        self.assertIsNone(self.store.record_usage_turn(self.project_id, provider="pi", model="m", usage={"input": 0}))
        self.assertIsNone(self.store.record_usage_turn(self.project_id, provider="pi", model="m"))
        self.assertEqual(self.store.list_usage_summary(self.project_id)["turns"], 0)

    def test_missing_project_keyerror_on_summary_and_skipped_on_record(self):
        with self.assertRaises(KeyError):
            self.store.list_usage_summary("no-such-project")
        # 记录 best-effort：项目不存在时跳过而非上抛
        self.assertIsNone(self.store.record_usage_turn("no-such-project", provider="pi", model="m", usage={"input": 5}))

    def test_tenant_isolation_separate_projects(self):
        other_id = self.store.create_project("另一个项目")["id"]
        self.store.record_usage_turn(self.project_id, provider="pi", model="a", usage={"input": 1, "output": 1})
        self.store.record_usage_turn(other_id, provider="codex", model="b", usage={"input": 9, "output": 9})
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["total"]["input_tokens"], 1)
        other = self.store.list_usage_summary(other_id)
        self.assertEqual(other["turns"], 1)
        self.assertEqual(other["total"]["input_tokens"], 9)
        # 两个项目 = 两个独立 ops.db 文件（ADR 0021 租户隔离）
        self.assertTrue((self.root / self.project_id / "ops.db").is_file())
        self.assertTrue((self.root / other_id / "ops.db").is_file())

    def test_summary_aggregation_by_day_and_model(self):
        self.store.record_usage_turn(
            self.project_id, provider="pi", model="anthropic/a",
            usage={"input": 10, "output": 5, "cacheRead": 2}, duration_seconds=2.0,
        )
        self.store.record_usage_turn(self.project_id, provider="codex", model="gpt-5",
                                     usage={"prompt_tokens": 4, "completion_tokens": 1}, duration_seconds=4.0)
        summary = self.store.list_usage_summary(self.project_id)
        self.assertEqual(summary["turns"], 2)
        self.assertEqual(summary["total"]["input_tokens"], 14)
        self.assertEqual(summary["total"]["output_tokens"], 6)
        self.assertEqual(len(summary["by_model"]), 2)
        pi_model = next(m for m in summary["by_model"] if m["provider"] == "pi")
        self.assertEqual(pi_model["turns"], 1)
        self.assertEqual(pi_model["input_tokens"], 10)
        self.assertEqual(len(summary["by_day"]), 1)
        self.assertEqual(summary["by_day"][0]["turns"], 2)
        self.assertEqual(summary["total"]["avg_duration_seconds"], 3.0)

    def test_summary_days_filter(self):
        self.store.record_usage_turn(self.project_id, provider="pi", model="a", usage={"input": 1})
        summary = self.store.list_usage_summary(self.project_id, days=1)
        self.assertEqual(summary["turns"], 1)

    def test_empty_project_no_usage_rows(self):
        # create_project 会经版本树租户自动建 ops.db（初始空快照），但 usage_turns 应为空
        new_id = self.store.create_project("空用量")["id"]
        summary = self.store.list_usage_summary(new_id)
        self.assertEqual(summary["turns"], 0)
        self.assertEqual(summary["by_day"], [])
        self.assertEqual(summary["total"]["input_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
