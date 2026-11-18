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

## #421 / #424：节点设置与原生续接（实施门 2）

### 原子设置和运行时边界

- `POST /agent-nodes/{node_id}/settings` 将宿主、模型和思考档位作为一个节点事务；旧 `/model` 委托同一事务。能力验证、运行时变更和画布 CAS 失败不能留下半套设置；CAS 重试只重建字段，不重复执行原生命令。几何/所有权并发变化可保留，设置竞争、节点删除则明确失败，绝不复活节点。此失败补偿合同取代 #332「宿主副作用不回滚」的旧合同；旧 `/model` 也遵循新语义，无例外。提交成功后的广播或回包失败不触发补偿：例如 fork 已落盘后项目才被移入回收站，原样返回 `PROJECT_NOT_FOUND`，不切回原生绑定，因为事务已提交，后续删除不是 CAS 失败。
- 节点锁只覆盖设置/切换/分支事务及 prompt 接收，不覆盖整轮推理。原生流、压缩、排队、确认、重试和未完成 stop 均拒绝设置，错误码 `agent_busy`；活着但空闲的 Pi 进程不是 busy。steer、follow-up、abort 和确认回复不因设置等待而被锁死。
- 切换先停止旧空闲宿主并等待 reader 结束，再持久化/选择目标宿主；保留同一 `AgentSession` 和监听器容器。恢复使用目标宿主自己的 ID/分支，不把 Pi ID 当作 Codex thread，也不把“选择宿主”当作已经启动了该宿主。
- Pi 设置使用请求 ID 精确匹配的 ACK 和原生 `get_state` 回读；模型、思考任一失败/钳位则回滚两者，回滚不能确认时显式报错。Codex 每轮（包括 resume 和失败重建）固定本轮 model/effort 快照；`submitted` 只在真正提交 argv 后更新，`effective` 仍为 null，来源明确为未确认。
- 覆盖值切回“跟随宿主默认”时，文件快照探针不足以证明生产环境默认。本门选择明确拒绝：`agent_default_unverified`，旧设置和画布保持不变；不静默清空覆盖值，也不把目录默认冒充实际生效值。
- 事件带宿主和运行时 generation；旧 reader 的迟到帧不得写入新宿主或覆盖绑定。历史读口和降级重放只选所需宿主，旧无 host 日志归 Pi；seq 始终是节点级游标，切宿主不重编号。读取历史不启动宿主。

### 绑定属于服务器，不属于画布 JSON

- 精确绑定字段清单：`agentHostSessions`、`extSessionId`、`branches`、`current_branch`、`mcp_session`。普通 Agent upsert 的客户端值一律不具备写权限。已有节点保留服务器原字段的存在性和值；不因一次普通画布保存迁移/修补旧字段。`agentCli`、`agentHostSettings`、`model` 同样保留，防止绕过空闲检查和设置事务。
- 新 ID、已不存在的 ID 均按无绑定节点建立。保留消息及各宿主 model/effort 意图，重建空的各宿主绑定，并移除外部 MCP 绑定；即使客户端再次提交带旧绑定的副本，也不能覆盖服务器的空绑定/新绑定。非 Agent 改成 Agent 也走新建保护，普通保存不得把已有 Agent 改成非 Agent 来绕过保护。
- 只有后端内部 `ProjectStore.apply_agent_host_mutations` 可提交受信绑定写入；普通 `apply_mutations` 默认不信任。权限作为服务端调用上下文传递，不来自客户端 JSON、mutation 字段或 actor 字符串。宿主事务、原生 ID 回填以及 MCP 既有合法新建/重绑使用此入口；`api_agent_tools` 只透传内部权限，MCP 的仲裁、确认、所有权和重绑语义不变。
- Store 的变更只在 Agent upsert 分支，不修改 generation、ownership、outbox 或 pendingTasks 的逻辑。绑定和旧投影在同一锁/CAS 下落盘；客户端缓存不是绑定真源。
- Pi 分支 CAS 只接受活跃绑定不变时的纯追加，或目标分支记录和原生绑定完全吻合的已满足切换（不增加 revision/touch）；保留非活跃宿主的最新独立进展。若切换的目标分支记录在已确认原生切换后被删除，则保留 #332 的 `404 NOT_FOUND`：不保存已删除的选择，也不重复切换。节点删除或重试耗尽仍补偿原生操作，无法确认时报错。成功回包的分支字段与 canvas 使用广播后的同一份快照。

### 已知局限：普通删除撤销不恢复原生会话

- 没有可信 clone/恢复标识时，服务器不能区分普通新建、克隆和同 ID 删除后的画布撤销恢复。本门不增加受信恢复事务：**撤销恢复保留 Agent 消息和设置，但不续接原生会话；下次发送新开会话。** 该策略由 review 明确裁定，不是克隆规则的隐式副作用。
- 规范映射中的空绑定会清空旧内存 ID/文件引用；Pi 使用一个新的显式 `--session-id`，即使旧目录仍有 JSONL 也不走 `--continue` 或最新文件启发式。旧的无映射节点仍保留原来的兼容发现路径。阴性测试覆盖同 ID 删除/恢复、残留旧 Pi 文件、消息/设置保留及新 ID 回填。
- 截至门 2，只接后端合同和旧 UI 冒烟，未实现 r3 工具条、480px 默认宽度或前端设置 undo；门 3 当前实现见下节。真 CLI 验证和截图是实施证据，不替代 review 验收。

## #421 / #424：设置前置条件与人工设置历史（实施门 3）

本节记录门 3 实现；本 ADR 原有“已接受”状态不代表门 3 已通过。扩围依据为 reviewer **01a1de6f** 在 review-batch-ui `MESSAGES.md` 的 2026-11-18 07:55:49 原文（2173–2180 行）：只加人工设置历史专用 `/settings` 分支及可选前置条件，复用既有事务，不改通用 delta/普通保存或服务器绑定保护。产品字段合同见 [CONTEXT.md](../../CONTEXT.md)。

### `/settings` 的可选 `expected` schema

完整路径为 `POST /api/projects/{project_id}/agent-nodes/{node_id}/settings`。顶层允许字段为 `host`、`model`、`effort`、`user_edit`、`expected`。`expected` 可省略，**省略时行为与门 2 一致**；一旦提供就必须是恰含以下三键的对象：

```typescript
{ host: "pi" | "codex", model: string, effort: string | null }
```

- 三键均必需，不接收额外键或 `expected:null`；host 精确匹配小写 pi/codex，不裁剪或强制转换。model 允许空字符串；effort 允许 null 或任意字符串（含空字符串）。期望值只比较、不应用、不做能力验证，不能把它的空 effort 规范成 null；目标 model/effort 的既有规范化与能力验证不变。格式错误返回 400 `invalid_agent_settings`。
- 在已有节点锁内重新读取当前节点，用 `read_host_fields` 的规则得到**当前活跃宿主**的 `{host,model,effort}`，而不是比较旧顶层投影、整个 host map、绑定或客户端快照。两张映射均缺失时沿用旧字段归原宿主的规则；有映射时以映射为准。
- 比较先于能力探测、原生设置/stop 等写入；不匹配返回 **409 `agent_settings_conflict`**，不写原生、不保存目标值。它不是客户端预检后再无条件 POST，也不是 trusted 写权限或新的 Store 入口。
- 匹配仍须通过原 busy 检查；原生流、压缩、排队、确认、重试、未完成 stop 仍返回 `agent_busy`。之后沿用既有节点锁、画布 CAS/重试、严格 Pi ACK/state 回读、失败补偿；不能由 expected 绕过生产默认未确认的 `agent_default_unverified`。CAS 重试不重复原生命令，设置竞争/删除仍明确失败。

### 人工宿主/模型/思考的专用撤销与重做

- `receiveAgentSettingsCanvas` 只在权威画布接收成功且相应人工设置仍被当前画布接受后记录历史。新项为 `{agentHostSettingsEdit:{nodeId,before,after}}`，before/after 均仅为 `{host,model,effort}`；不存全节点、原生绑定、非活跃宿主进展、消息或旧 `userTouchedAt`。只改变绑定/时间戳、不改变所选人工设置的回包不是该类历史步。
- undo 通过同一 `/settings` 提交 before、`expected:after`、`user_edit:true`；redo 提交 after、`expected:before`、`user_edit:true`。持久历史中的选择对也须严格校验，不能因损坏或缺失 expected 退化为无条件设置。host 切换仍选择目标宿主的服务器绑定，不重放历史原生 ID/分支。
- 只允许一个设置历史请求在途；不乐观改节点、不提前 pop/push。必须 HTTP 成功且 `success:true`、非 `persisted:false`、有权威 canvas，接收器返回 true，并在异步等待及接收/刷新前后确认项目、画布、历史 epoch/栈顶仍有效，才移动原历史项并持久化栈。
- HTTP/网络/解析/权威接收失败、busy、409 预期冲突、默认无法验证、节点消失或历史上下文失效时，显示失败并保留原栈。迟到回包若已在服务端成功，不用本地旧快照补偿或回滚原生绑定；不以移栈假称当前历史操作成功。
- 该分支不调用普通 `scheduleSave` 恢复设置，不改通用 `actionDelta` 或其它编辑的普通保存语义；`user_edit:true` 沿用服务器当前人工编辑时间，不倒拨旧时间。门 2 的普通删除撤销不恢复原生会话规则保持不变。

### 权威设置接收后的 TUI 同步

- 普通 host/model/effort 设置及其 undo/redo 先走权威画布接收器，历史恢复以 `recordUndo:false` 避免再记一条；随后共同调用 `applyAgentHostSettingsAuthority`：从当前节点同步活跃宿主/视图，采用响应 `effort_state`，并调用 `renderAgentNode`。停靠 DOM 不由全量 render 重建，也必须就地更新参数、只读提示及运行时证据，不能继续显示旧宿主。
- 活跃宿主与只读查看宿主分离；另一个宿主的草稿、附件/引用、焦点选区、阅读锚点及事件视图只在页面内按宿主暂存，不进节点。只读查看只拉带 host 的历史，不开 WebSocket/启动宿主，不开放发送或参数编辑；切回继续才经显式确认与设置事务。视图切换使旧 socket/回放回调失效，不能用迟到结果覆盖当前视图。
- 模型和思考共用选择器，打开、模型变更或显式重试才读取原生 capabilities，不在 render/控制绑定时探测。无能力就显示错误/不可用，不编造档位；ultra 不暴露。新模型不支持已存 effort 时须明确确认组合提交 `effort:null`，后端默认未确认仍拒绝，不静默降档。
- requested/submitted/effective 三层语义继续适用：只把权威 `effort_state` 当运行时证据；Codex 仍为 effective=null，UI 区分“已设置”“已提交”和“生效未确认”，不能拿配置或目录回显冒充生效。Pi 生效提示依赖严格 ACK/state 回读。停止请求送达不是宿主已停止，终态反馈/状态核验前参数保持锁定。
- 本门不修改 Store 绑定保护、generation、ownership、outbox、pendingTasks，不授予客户端 trusted 权限。r3、480px 回退、临时呈现状态与用户消息导航详见 [ADR 0028](0028-agent-node-dockable.md)；浏览器、完整 Store 证据及截图矩阵见门 3 交付记录，本文不替代验收。
