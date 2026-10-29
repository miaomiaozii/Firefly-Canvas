"""LLM 用量/成本遥测（issue #258；ADR 0021 ops.db 租户「usage」）。

移植自 ComfyArt ``bot/_cli_common.py`` 的 ``normalize_usage`` / ``merge_usage``
（MIT，见 NOTICE.md 登记）：
- ``USAGE_KEY_ALIASES``：多 provider 字段别名归一（OpenAI 风格 prompt_tokens、
  ACP camelCase inputTokens、pi SDK Usage 短键 input/cacheRead/…）；
- ``merge_usage``：跨 turn 累加。

采集点（两路径）：
- pi sidecar：``app/services/agent_host.py`` 的 agent_start/agent_end 事件
  （turn 生命周期；``collect_pi_turn_usage`` 汇总 agent_end 消息中的 usage）；
- codex CLI：``app/services/conversation.py`` 的 ``run_codex_cli --json``
  （``parse_codex_jsonl_usage`` 解析 JSONL 里最后一个 turn.completed.usage）。

存储：每 turn 一行 ``usage_turns``（ops.db 租户表，前缀 usage_*，ADR 0021 决策 5），
additive 迁移 ``usage_001`` 走 ``ops_migrations``。租户隔离 = 每项目独立 ops.db
文件 + project_id 过滤（ADR 0021）。

失败模型：遥测 best-effort——``record_usage_turn`` 出错仅记日志并返回 None，
绝不影响 agent/对话主流程；查询 API 原样暴露 ops.db 错误（与版本树一致）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

USAGE_CANONICAL_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_tokens",
)

# 别名表（对照 ComfyArt bot/_cli_common.py _USAGE_KEY_ALIASES，MIT 见 NOTICE.md）。
# 三段来源：
# 1) ComfyArt 原表（OpenAI 风格）：input_tokens/prompt_tokens/output_tokens/
#    completion_tokens/cache_read_input_tokens/cached_input_tokens/cache_creation_input_tokens
# 2) ACP camelCase（ComfyArt §85.1）：inputTokens/outputTokens/cachedReadTokens/cachedWriteTokens
# 3) pi SDK Usage 短键（packages/ai/src/types.ts）：input/output/cacheRead/cacheWrite/cacheWrite1h/totalTokens
USAGE_KEY_ALIASES = {
    # —— OpenAI 风格（ComfyArt 原表）
    "input_tokens": "input_tokens",
    "prompt_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "completion_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_input_tokens",
    "cached_input_tokens": "cache_read_input_tokens",
    "cache_creation_input_tokens": "cache_creation_input_tokens",
    # —— ACP camelCase
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cachedReadTokens": "cache_read_input_tokens",
    "cachedWriteTokens": "cache_creation_input_tokens",
    # —— pi SDK Usage 短键
    "input": "input_tokens",
    "output": "output_tokens",
    "cacheRead": "cache_read_input_tokens",
    "cacheWrite": "cache_creation_input_tokens",
    "cacheWrite1h": "cache_creation_input_tokens",
    "totalTokens": "total_tokens",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _zero_usage() -> Dict[str, Any]:
    result = {key: 0 for key in USAGE_CANONICAL_KEYS}
    result["cost_usd"] = None
    return result


def normalize_usage(raw: Optional[Dict[str, Any]], cost_usd: Optional[float] = None) -> Dict[str, Any]:
    """多 provider 字段别名归一（移植自 ComfyArt normalize_usage）。

    - 只收 int/float（bool 不算数字），未知键忽略；
    - ``total_tokens`` 缺失时默认 = input + output（cache_read 是 input 的子集，不加回）；
    - ``cost_usd`` 仅当显式传入数字时写入，round 到 6 位。
    """
    result = _zero_usage()
    if isinstance(raw, dict):
        for key, value in raw.items():
            canon = USAGE_KEY_ALIASES.get(str(key))
            if canon is None or not _is_number(value):
                continue
            result[canon] = int(value)
    if result["total_tokens"] == 0 and (result["input_tokens"] or result["output_tokens"]):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    if _is_number(cost_usd):
        result["cost_usd"] = round(float(cost_usd), 6)
    return result


def merge_usage(
    total: Optional[Dict[str, Any]],
    raw: Optional[Dict[str, Any]],
    cost_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """跨 turn 累加（移植自 ComfyArt merge_usage）：total + normalize(raw)。"""
    base = _zero_usage()
    if isinstance(total, dict):
        for key in USAGE_CANONICAL_KEYS:
            value = total.get(key)
            if _is_number(value):
                base[key] = int(value)
        cost = total.get("cost_usd")
        if _is_number(cost):
            base["cost_usd"] = float(cost)
    usage = normalize_usage(raw, cost_usd)
    for key in USAGE_CANONICAL_KEYS:
        base[key] += usage[key]
    if usage["cost_usd"] is not None:
        base["cost_usd"] = round((base["cost_usd"] or 0) + usage["cost_usd"], 6)
    return base


def usage_has_data(usage: Optional[Dict[str, Any]]) -> bool:
    """是否值得记一行：任一 token 字段非 0 或有 cost。"""
    if not isinstance(usage, dict):
        return False
    if any(usage.get(key) for key in USAGE_CANONICAL_KEYS):
        return True
    return _is_number(usage.get("cost_usd"))


def parse_codex_jsonl_usage(stdout_text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """解析 codex CLI ``--json`` JSONL 输出。

    返回 (usage, last_agent_message)：
    - usage = 最后一个 ``turn.completed`` 事件的 ``usage`` dict（原始别名，交给 normalize）；
    - last_agent_message = 最后一个 item.type==agent_message 的文本（--output-last-message 文件
      缺失时的文本兜底）。非 JSON 行忽略，解析失败不抛。
    """
    usage: Optional[Dict[str, Any]] = None
    last_message = ""
    for line in (stdout_text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "turn.completed":
            candidate = event.get("usage")
            if isinstance(candidate, dict):
                usage = candidate
        elif event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text:
                    last_message = text
    return (usage, last_message)


def collect_pi_turn_usage(messages: Any) -> Dict[str, Any]:
    """汇总 pi sidecar agent_end 事件 messages 中 assistant 消息的 usage（一个 turn 可能多次 LLM 调用）。"""
    total: Optional[Dict[str, Any]] = None
    if not isinstance(messages, list):
        return _zero_usage()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        cost = usage.get("cost")
        cost_total = cost.get("total") if isinstance(cost, dict) else None
        total = merge_usage(total, usage, cost_total)
    return total if total is not None else _zero_usage()


class ProjectUsageMixin:
    """ops.db usage 租户：每 turn 一行记录 + 聚合查询（issue #258）。

    依赖 ProjectStore 提供 root，ProjectVersionsMixin 提供 _opsdb / _require_project_id
    （同库同锁，跨租户写序串行化，ADR 0021 每项目锁不变量）。
    """

    def record_usage_turn(
        self,
        project_id: str,
        *,
        provider: str,
        model: str = "",
        node_id: str = "",
        usage: Optional[Dict[str, Any]] = None,
        duration_seconds: Optional[float] = None,
    ) -> Optional[int]:
        """记一行 turn 用量（best-effort：出错仅记日志返回 None；全 0 用量不记）。"""
        try:
            project_id = self._require_project_id(project_id)
            normalized = normalize_usage(usage)
            if isinstance(usage, dict) and _is_number(usage.get("cost_usd")):
                normalized["cost_usd"] = round(float(usage["cost_usd"]), 6)  # 已归一 dict 的 cost 透传
            if not usage_has_data(normalized):
                return None
            if _is_number(duration_seconds):
                duration_seconds = round(max(0.0, float(duration_seconds)), 3)
            else:
                duration_seconds = None
            db = self._opsdb()
            with db.project_lock(project_id), db.connect(project_id) as conn:
                cursor = conn.execute(
                    "INSERT INTO usage_turns "
                    "(project_id, provider, model, node_id, input_tokens, output_tokens, "
                    "cache_read_input_tokens, cache_creation_input_tokens, total_tokens, "
                    "cost_usd, duration_seconds, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        str(provider or ""),
                        str(model or ""),
                        str(node_id or ""),
                        normalized["input_tokens"],
                        normalized["output_tokens"],
                        normalized["cache_read_input_tokens"],
                        normalized["cache_creation_input_tokens"],
                        normalized["total_tokens"],
                        normalized["cost_usd"],
                        duration_seconds,
                        _now_iso(),
                    ),
                )
                conn.commit()
                return cursor.lastrowid
        except Exception:
            print(
                f"[firefly-usage] 项目 {project_id} 用量记录失败（遥测 best-effort，不影响主流程）",
                file=sys.stderr,
            )
            return None

    def list_usage_summary(self, project_id: str, days: Optional[int] = None) -> Dict[str, Any]:
        """聚合查询（租户隔离：本项目的 ops.db 文件 + project_id 过滤）。

        days：只统计最近 N 天（UTC）。无 ops.db 文件 = 空状态（ADR 0021）。
        """
        project_id = self._require_project_id(project_id)
        db = self._opsdb()
        if not db.has_db(project_id):
            return {"project_id": project_id, "turns": 0, "total": _zero_usage(), "by_day": [], "by_model": []}
        sql = (
            "SELECT provider, model, input_tokens, output_tokens, cache_read_input_tokens, "
            "cache_creation_input_tokens, total_tokens, cost_usd, duration_seconds, created_at "
            "FROM usage_turns WHERE project_id = ?"
        )
        params: List[Any] = [project_id]
        if _is_number(days) and int(days) > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
            sql += " AND created_at >= ?"
            params.append(cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
        with db.project_lock(project_id), db.connect(project_id) as conn:
            rows = conn.execute(sql, params).fetchall()

        total = _zero_usage()
        durations: List[float] = []
        by_day: Dict[str, Dict[str, Any]] = {}
        by_model: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            row_usage = {key: int(row[key]) for key in USAGE_CANONICAL_KEYS}
            row_cost = row["cost_usd"]
            if row_cost is None:
                row_cost = 0.0
            row_cost = float(row_cost)
            row_duration = row["duration_seconds"]
            for key in USAGE_CANONICAL_KEYS:
                total[key] += row_usage[key]
            total["cost_usd"] = (total["cost_usd"] or 0) + row_cost
            if _is_number(row_duration):
                durations.append(float(row_duration))

            day = str(row["created_at"])[:10]
            day_bucket = by_day.setdefault(
                day, {"day": day, "turns": 0, **{key: 0 for key in USAGE_CANONICAL_KEYS}, "cost_usd": 0.0}
            )
            day_bucket["turns"] += 1
            for key in USAGE_CANONICAL_KEYS:
                day_bucket[key] += row_usage[key]
            day_bucket["cost_usd"] += row_cost

            model_key = (str(row["provider"] or ""), str(row["model"] or ""))
            model_bucket = by_model.setdefault(
                model_key,
                {"provider": model_key[0], "model": model_key[1], "turns": 0,
                 **{key: 0 for key in USAGE_CANONICAL_KEYS}, "cost_usd": 0.0},
            )
            model_bucket["turns"] += 1
            for key in USAGE_CANONICAL_KEYS:
                model_bucket[key] += row_usage[key]
            model_bucket["cost_usd"] += row_cost

        if total["cost_usd"] in (0.0, None):
            total["cost_usd"] = 0.0
        total["avg_duration_seconds"] = round(sum(durations) / len(durations), 3) if durations else None
        for bucket in list(by_day.values()) + list(by_model.values()):
            if bucket["cost_usd"] == 0.0:
                bucket["cost_usd"] = 0.0
        return {
            "project_id": project_id,
            "turns": len(rows),
            "total": total,
            "by_day": [by_day[day] for day in sorted(by_day)],
            "by_model": [by_model[key] for key in sorted(by_model)],
        }

    def list_usage_by_node(self, project_id: str) -> Dict[str, Dict[str, Any]]:
        """usage 租户只读聚合：按 node_id 摊平成本/ token（issue #262 全局任务总控台用）。

        与 list_usage_summary 同租户同锁：每项目独立 ops.db + project_id 过滤（ADR 0021）；
        无 ops.db 文件 = 空状态。返回 {node_id: {'turns','cost_usd','input_tokens',
        'output_tokens','total_tokens'}}；node_id 为空的行归到 "" 桶。
        """
        project_id = self._require_project_id(project_id)
        db = self._opsdb()
        if not db.has_db(project_id):
            return {}
        with db.project_lock(project_id), db.connect(project_id) as conn:
            rows = conn.execute(
                "SELECT node_id, input_tokens, output_tokens, total_tokens, cost_usd "
                "FROM usage_turns WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        buckets: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row["node_id"] or "")
            bucket = buckets.setdefault(
                key, {'turns': 0, 'cost_usd': 0.0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
            )
            bucket['turns'] += 1
            bucket['cost_usd'] += float(row["cost_usd"] or 0.0)
            for source, target in (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'), ('total_tokens', 'total_tokens')):
                bucket[target] += int(row[source] or 0)
        for bucket in buckets.values():
            bucket['cost_usd'] = round(bucket['cost_usd'], 6)
        return buckets
