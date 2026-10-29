# issue-258 结果报告（LLM 用量/成本遥测：normalize_usage/merge_usage 移植进 ops.db）

**状态：完成**（票面要求全部落地：纯函数移植 + ops.db usage 租户 + pi sidecar/codex CLI 两采集路径 + GET 聚合端点；`quality_gate fast` + `domain` 绿 + 全量 pytest 对照基线，见文末验证证据表）

分支 `issue/258`（基于 `origin/issue/257`，HEAD `c139505`），7 commits。

## Commits

| commit | 内容 |
|---|---|
| `c9f53cf` | feat(usage): LLM 用量遥测纯函数移植 + ops.db usage 租户（issue #258） |
| `db70096` | feat(usage): pi sidecar 采集 hook — agent_start/agent_end turn 用量写 ops.db（issue #258） |
| `4456aef` | feat(usage): codex CLI 采集路径 — run_codex_cli --json turn 用量写 ops.db（issue #258） |
| `c7083e5` | feat(usage): GET /api/project-library/{id}/usage 聚合端点（issue #258） |
| `55691de` | docs(usage): NOTICE.md 登记 ComfyArt MIT 移植（normalize_usage/merge_usage，issue #258） |
| `cc92ece` | test(usage): opsdb 迁移清单断言纳入 usage_001 租户（issue #258） |
| `7bb80ba` | test(usage): agent_host usage 测试遵循 loop 保存/恢复约定，不再击穿 test_agent_tui（issue #258） |

## 票面要求落地

### ① normalize_usage / merge_usage 移植（ComfyArt `bot/_cli_common.py:62-87`，MIT，NOTICE.md 已登记）

新模块 `firefly_usage.py`：

- `normalize_usage(raw, cost_usd=None)`：多 provider 字段别名归一——只收 int/float（bool 不算数字）、未知键忽略；`total_tokens` 缺失时默认 = input + output（cache_read 是 input 子集，不加回）；`cost_usd` 仅显式数字写入，round 6 位。
- `merge_usage(total, raw, cost_usd=None)`：跨 turn 累加；cost 缺省保留旧值、有则累加。

**别名表 `USAGE_KEY_ALIASES`（三段来源）**：

| 来源 | 别名 → 归一 |
|---|---|
| ComfyArt 原表（OpenAI 风格） | `input_tokens`→input_tokens、`prompt_tokens`→input_tokens、`output_tokens`→output_tokens、`completion_tokens`→output_tokens、`cache_read_input_tokens`→cache_read_input_tokens、`cached_input_tokens`→cache_read_input_tokens、`cache_creation_input_tokens`→cache_creation_input_tokens |
| ACP camelCase（ComfyArt §85.1） | `inputTokens`→input_tokens、`outputTokens`→output_tokens、`cachedReadTokens`→cache_read_input_tokens、`cachedWriteTokens`→cache_creation_input_tokens |
| pi SDK Usage 短键（`packages/ai/src/types.ts`） | `input`→input_tokens、`output`→output_tokens、`cacheRead`→cache_read_input_tokens、`cacheWrite`/`cacheWrite1h`→cache_creation_input_tokens、`totalTokens`→total_tokens |

### ② usage 租户入住 ops.db（ADR 0021 租户之四）

- 迁移 `usage_001`（`firefly_opsdb.py` OPS_MIGRATIONS 追加，additive-only，走既有 `ops_migrations` 机制，#257 已落地，未动共享表）：
  - `usage_turns`（前缀 `usage_*` 符合 ADR 0021 决策 5）：`id, project_id, provider, model, node_id, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, total_tokens, cost_usd REAL, duration_seconds REAL, created_at`
  - 索引 `idx_usage_turns_project_time (project_id, created_at)`
- `ProjectUsageMixin`（`firefly_usage.py`）混入 `ProjectStore`（`firefly_projects.py`，与 `ProjectVersionsMixin` 同库同锁）：
  - `record_usage_turn(project_id, *, provider, model, node_id, usage, duration_seconds)`：**best-effort**——出错仅记日志返回 None；全 0 用量不记行；项目不存在（KeyError）跳过。
  - `list_usage_summary(project_id, days=None)`：total + by_day + by_model 聚合；`days` 按 UTC 过滤；无 ops.db 文件 = 空状态（ADR 0021 失败模型）。

### ③ 采集点清单（两路径都接）

| 路径 | 采集点 | 文件:函数 | 语义 |
|---|---|---|---|
| pi sidecar | `agent_start` 事件 | `app/services/agent_host.py` `_dispatch_event` | 记 `AgentSession._turn_started_at`（turn 时长起点） |
| pi sidecar | `agent_end` 事件 | `app/services/agent_host.py` `_record_turn_usage`（新增） | `collect_pi_turn_usage(event['messages'])` 汇总 assistant 消息 usage（含 `cost.total`）→ `project_store_call('record_usage_turn', ...)`（`asyncio.to_thread`，best-effort 不影响事件流）；provider/model 取 `session.model` 斜杠拆分（`anthropic/x`→provider=anthropic；无斜杠 provider=pi） |
| codex CLI | `codex_chat_text` | `app/services/conversation.py` `run_codex_cli(capture_usage=True)` + `_record_codex_usage`（新增） | `--json` JSONL → `parse_codex_jsonl_usage` 取**最后一个** `turn.completed.usage`（`cached_input_tokens` 等原始键经 normalize 归一）→ provider=codex 写 ops.db；文本兜底用 JSONL `agent_message`（不兜 JSONL 原文）；`run_codex_cli` 默认模式（capture_usage=False）行为不变，其他调用方零影响 |

领地例外说明：仅动了 `app/services/agent_host.py`（+`AgentSession._turn_started_at` 字段与 1 个私有方法）；**未动 `app/agent_tools.py` 工具面**，**未动任何 fal 相关文件**；conversation.py 改动集中在 `run_codex_cli` 签名扩展 + `codex_chat_text` 两个函数内。

### ④ 查询 API（展示归 #262，本票不做展示）

- `GET /api/project-library/{project_id}/usage?days=N`（`app/routers/api_project_usage.py`，main.py 按字母序 idiom 注册）
- 返回 `{project_id, turns, total{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens, total_tokens, cost_usd, avg_duration_seconds}, by_day[], by_model[]}`
- 租户隔离：每项目独立 ops.db 文件 + `project_id` 过滤（ADR 0021）；项目不存在 → 404。

## 测试证据

### 新增测试（42 项，全部 SQLite 隔离 / 临时目录，TDD 先行）

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `tests/test_firefly_usage.py` | 27 | 别名归一（OpenAI/ACP/pi 三段 + 非数字丢弃 + total 默认 + cost round 6）；merge 累加（含 cost 缺省保留/累加）；`parse_codex_jsonl_usage`（噪声行/坏 JSON 忽略、最后一个 turn.completed 生效、agent_message 兜底）；`collect_pi_turn_usage`（assistant 求和、cost.total 提取、非 assistant 忽略）；store 层：记行/迁移 usage_001 入 ops_migrations/别名归一后入库/cost 持久化聚合/全 0 不记/项目不存在 KeyError+记录跳过/**租户隔离（A 项目行不可见于 B，独立 ops.db 文件）**/聚合 by_day/by_model/avg_duration/days 过滤/空项目空状态 |
| `tests/test_agent_host_usage.py` | 5 | pi 采集 hook：agent_end 记行（provider/model 斜杠拆分）/无 usage 不记/**遥测失败不炸事件流**/无斜杠 provider=pi/多 turn 多行（每 turn 一行不合并） |
| `tests/test_codex_usage.py` | 5 | 假 codex CLI 桩（`tests/stubs/fake_codex.cmd/.py`）：`capture_usage=True` 解析 JSONL turn.completed/默认模式行为不变（无 --json）/无 last-message 文件时文本兜底 agent_message/codex_chat_text 记行（provider=codex、`cached_input_tokens`→cache_read 归一）/无 project_id 跳过且不影响对话 |
| `tests/test_usage_api.py` | 5 | GET 端点：空状态 200/聚合正确/项目不存在 404/**API 层租户隔离**/days 参数 |

### 门与基线对照

| 项 | 结果 |
|---|---|
| `python tools/quality_gate.py fast` | **passed**（verdict=passed，final_exit_code=0，15.0s） |
| `python tools/quality_gate.py domain` | **passed**（verdict=passed，final_exit_code=0，142.0s，浏览器步一次通过，无需重试） |
| 收集集 diff（pytest --collect-only，wt-257 vs wt-258） | wt-258 = wt-257 **恰好 +42 个新增测试**，无删改、无遗漏 |
| 全量 pytest（issue/258 最终轮） | **1478 passed, 1 skipped, 0 failed, 628 subtests passed**（513s） |
| 全量 pytest（origin/issue/257 基线重跑） | 见下表 |

| 全量 pytest | passed | skipped | failed | subtests | 耗时 |
|---|---|---|---|---|---|
| origin/issue/257 基线（c139505 重跑） | 1436 | 1 | 0 | 628 | 8m13s |
| issue/258 最终轮 | 1478 | 1 | 0 | 628 | 8m33s |

新增 42 个测试，无存量测试回归。全量 pytest 共跑三轮：第一轮 12 failed（2 个为 usage_001 迁移导致的存量 opsdb 精确断言→cc92ece 修正；10 个 test_agent_tui 为本文件 asyncio.run 清掉主线程 loop 击穿既有顺序依赖→7bb80ba 修正），最终轮零失败。

## 验收标准核对

- ✅ 一次 agent 对话后 ops.db 出现 usage 行且别名归一——`tests/test_agent_host_usage.py::test_agent_end_records_usage_turn`（pi 路径）+ `tests/test_codex_usage.py::test_codex_chat_text_records_usage_turn`（codex 路径）：`usage_turns` 入库且 `cached_input_tokens`/`cacheRead`/`inputTokens` 等全部归一到 `input_tokens`/`output_tokens`/`cache_read_input_tokens`/`cache_creation_input_tokens`。
- ✅ `python tools/quality_gate.py domain` 通过。

## 已知边界（不在本票范围）

- `generation.py` 的 codex 生图路径也经 `codex_chat_text` 调用，天然带上采集（payload 带 project_id 时记行）；纯工具调用类无 LLM turn 的路径不记。
- 成本：pi 路径取 `usage.cost.total`；codex CLI 不报 cost（`turn.completed.usage` 无 cost 字段）→ `cost_usd` 为 NULL，聚合按 0 计。
- 展示/图表/全局任务中心用量视图：归 #262。
