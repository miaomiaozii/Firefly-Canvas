# ADR 0024：codex per-turn 宿主桥接 + 会话绑定与降级语义

状态：已接受（监理 2026-11-13 频道 is-impl 放行；含监理补充钉：resume 只用显式 id）
日期：2026-11-13
关联：ADR 0011（pi 常驻 RPC 桥）、ADR 0022（MCP 端点+三级仲裁+确认闸）、spec `.local/agent-work/spec/agent-fullnode-spec.md` §2

## 背景与决策

Agent 节点支持第二宿主 codex（本机 CLI 已装）。codex 无 pi 式常驻 RPC 模式；`codex exec` 是一次性进程，`resume <id>` 可续会话（会话文件在 `~/.codex/sessions/`，Firefly 与外部 CLI 同库），`exec --json` 输出 JSONL 事件流。

**决策：pi 维持常驻进程（ADR 0011 不动）；codex 采用 per-turn 进程模型**——每轮 prompt 起 `codex exec`（首轮）或 `codex exec resume <sessionId>`（续轮），进程随 turn 结束退出；sessionId 先落盘后 spawn。

## 1. 事件映射词汇表

内部词汇 = 前端 `static/js/canvas-agent-tui.js:196-328` 既有 switch 契约（session/agent_start/agent_end/turn_start/turn_end/message_update(ames)/tool_execution_start|update|end/auto_retry_start|end/process_exited/confirm/response/error）。codex 适配器有损映射，不新增前端词汇：

| codex exec --json | pi RPC（ADR 0011） | 内部事件 | 说明 |
|---|---|---|---|
| （spawn 成功） | （spawn） | session(busy) | 每轮 spawn 即上报 |
| task_started / turn_started | agent_start | agent_start + turn_start | |
| item/agent_message | message_update(response) | message_update | codex 无增量 delta，整条直传 |
| item/reasoning | thinking_delta(ames) | message_update(ames.type=thinking_delta) | 整条非流式 |
| item/command_execution | tool_execution_* | tool_execution_start→update→end | 聚合输出进 update，exit 进 end |
| item/mcp_tool_call | tool_execution_*(MCP) | tool_execution_* | 画布操作走 MCP 端点；confirm 事件由 api_mcp 发（确认闸），非适配器 |
| item/web_search | tool_execution_* | tool_execution_* | |
| item/error | error | error | codex exec 出错也可能 exit=0，以 item/error 为准（实测钉） |
| token_count | usage | usage（status 载荷） | codex total 为全程累计非增量——ops.db 记账差分（实测钉） |
| turn_completed | agent_end | turn_end + agent_end(running) | 之后进程退出 |
| turn_aborted | abort→agent_end | agent_end(stopReason=aborted) | abort 用 SIGINT |
| （退出码≠0/异常） | process_exited | process_exited | 与 pi 同义 |

## 2. per-turn 状态机与降级

```
stopped ─prompt─▶ spawning ─OK─▶ busy(turn) ─turn_completed/aborted─▶ idle_stored(sessionId)
   ▲                │spawn失败                                        │
   │                ▼                                                │
   └──abort/退出── idle_stored ◀── resume <sessionId>（下轮） ────────┘
busy ─崩溃─▶ auto-respawn 一次：resume <sessionId>
resume 失败（session 缺失/版本不兼容，实测报错退出）──▶
  降级：新会话（无 resume）+ events.jsonl（票C）最近 N 轮（默认 12）文本前置注入
  + error 事件「会话续接失败，已降级重建」——UI 可见，不静默。
```

不变量：
1. sessionId 落盘先于 spawn（幂等）；同节点 busy 时拒收并发 prompt；abort=SIGINT 单轮，不毁 sessionId。
2. **resume 一律显式 threadId**。`resume --last` 不可靠：监理实测 `resume --last --json` 报 `Error: no rollout found for thread id …`（thread id 错配）。
3. **适配器禁写 `~/.codex/config.toml`**（用户文件）。一切配置经 `-c` 命令行覆盖。codex 启动可能自写该文件（监理实测发现 mcp_servers 段残留后手工还原；成因待票 B 实测钉）。
4. 系统提示主方案 `-c 'developer_instructions="…"'`（监理实测模型逐字服从）；prompt 前缀块仅 fallback。

## 3. MCP `&node=` 绑定与 ADR 0022 仲裁语义

端点扩展 `/mcp?project=X&node=<agent_node_id>`：
- **仲裁语义零改动**：命令路由仍走 0022 三级仲裁（client_id 注册表 → project → 单实例兜底）；`&node=` 不参与路由。回归测试钉死：不带 node 参数时与 0022 全等。
- `&node=` 只改归属：node 指向已存在 smart-agent 节点 → 直接绑定（工具调用记录挂该节点外挂会话）；缺失/非法 → 按 0022 原路具现新节点。
- 确认闸、快照/重试、写类工具语义全部继承 0022。

## 4. 沙盒与审批策略

- 宿主进程一律 `-s read-only`（默认档）：codex 可写盘即绕过画布治理；一切写操作必须经 MCP 端点（三级仲裁 + 确认闸兜底）。
- 审批制实测项在票 B 编码期钉死（结论入 IMPL-NOTES，不改本 ADR 结构）：read-only 下 shell 写命令的 approval 行为、workspace-write+scratch cwd 放宽档的边界。默认不放宽。

## 后果

- 正面：codex 会话 Firefly/外部 CLI 双向接续（同 `~/.codex/sessions/` 库）；per-turn 天然无「刷新丢历史」；配合票C events.jsonl 完整回放。
- 代价：每轮 spawn（秒级）；reasoning 非流式；usage 记账需差分。

## 勘误（2026-11-13，WS E2E 实测）

- 正文 §1 中「message_update(ames)」为笔误：WS 线上 payload 键为 `assistantMessageEvent`，形如 `{type: text_delta|thinking_delta|thinking_start|…, delta}`（前端 `canvas-agent-tui.js:249-268` 读 `event.assistantMessageEvent.{type,delta}`；2026-11-13 定稿 main 实跑钉死，见 IMPL-NOTES #317 复验节）。映射语义不变，仅键名更正。

## 勘误（2026-11-13，票 B/C 实施实测）
1. §3 端点路径：MCP 端点实路径为 **/api/mcp**（api_mcp.py:603），正文 `/mcp` 系笔误；`&node=` 绑定语义不变。
2. §4 补充（审批策略）：codex 侧 MCP 工具默认审批模式与 exec 非交互语义组合会**自动拒绝** MCP 调用（实测 error: "requires approval, but approval policy is never"）——适配器必须注入 `-c 'mcp_servers.firefly.default_tools_approval_mode="approve"'`。这不削弱安全：画布写类工具的确认闸（ADR 0022）在 Firefly 端点侧执行，与宿主沙盒正交。正确键名是 `default_tools_approval_mode`（值 auto|prompt|writes|approve）；`default_tools_approval_policy` 为无效键（实测被 ignore）。
3. §1 映射表补充（实测）：`item{mcp_tool_call}` 终态结果在 `item.result.content[]`（非 aggregated_output），错误在 `item.error`；映射器已按此提取。

## #421 / #424：配置与能力合同（实施门 1）

- 对话执行仍是原有 `exec` / 显式 ID `resume`，不改为 app-server。`SpawnParams.reasoning_effort=None` 不覆盖宿主；显式值由 Codex adapter 经已有 TOML 编码器加入 `-c model_reasoning_effort=…`，位于 `resume` 之前。Pi adapter 对应 `--thinking`。本门只提供参数合同，尚未从节点设置接入运行时。
- `app/services/agent_node_host_settings.py` 提供纯函数 `read_host_fields` / `build_host_settings_fields`：`agentHostSettings` 保存各宿主 model/effort 意图，`agentHostSessions` 保存各宿主绑定/分支；旧顶层字段只是活跃宿主投影。仅无两张映射的旧节点可迁移，旧字段只归原 agentCli（缺省 Pi）。函数不写画布、不分配会话、不改变几何/所有权；事务接入留到下一门。
- `GET /api/projects/{project_id}/agent-nodes/capabilities` 必须有可信同源信号，在线程中进行有界原生元数据探测。Pi 读取 RPC state / models / available thinking levels；Codex 临时 app-server 只做 initialize / config/read / model/list，不创建 thread/turn。每模型档位来自原生能力，隐藏 ultra；无法确认就返回明确 unavailable，不编造枚举或默认值。
- 探针只复制白名单配置/认证文件到独立临时 home，不读会话、不回写源文件、不返回凭据/原始错误；子环境剥离凭据环境变量、代理、自定义 CA、会话及 Node 注入。因此来源明确标记 `file-only; sanitized-environment; account-callability-unverified`，不能宣称等于生产完整环境的默认或账号可调用性。配置依赖环境变量的场景须在设置事务接入前明确处理，不能拿此元数据冒充生产回读。
- 探针在所有出口回收自己的进程树、管道和临时目录；Windows 按实际创建句柄挂载匿名 Job、等待其 active_processes 为零，临时文件仅对共享冲突做有界清理重试。回收失败不报 available。
- requested（保存意图）、submitted（实际参数）、effective（权威宿主回读）不等价。Codex 配置回显、目录默认、argv、exit 0 都不构成 effective；没有可信回读时必须为 null / 未确认。Pi 实际设置仍须严格 ACK 和 state 回读，本门元数据探针不是该设置事务。
