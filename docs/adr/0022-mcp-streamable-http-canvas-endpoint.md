---
status: accepted
date: 2026-10-28
---

# 画布对外暴露 MCP 端点（streamable-HTTP）：外部 agent 会话具现为 Agent 节点

2026-10-28 拷问会话方向经用户签署（spec #233 决议 Q3/Q12，确认 a）：整画布以一个
MCP 端点对外暴露，外部会话具现为画布上的 Agent 节点。关联：#233（拷问决议与拆票）、
#240（同源守卫，MCP 开放前置）、#243（实现票，Blocked by #240/#242）、#244（工具面
17→~30）、`docs/adr/0012-agent-is-a-separate-node.md`、
`docs/adr/0016-canvas-deletion-unified-consequences-model.md`、
`docs/adr/0019-pi-sidecar-runtime-deep-integration.md`。

## 决策

- **整画布 MCP 端点（streamable-HTTP，纯 JSON、无 SSE）**：现有 17 个 agent 工具
  已是服务端 project-scoped HTTP API（五个路由声明 `app/routers/api_agent_tools.py:20-28`，
  工具语义与参数在 `app/agent_extensions/canvas-tools.ts`），直接封装为 MCP
  streamable-HTTP 端点对外，外部 agent（Claude Code / Codex / Cursor / VS Code 等）
  直接操作画布；封装≈免费，本 ADR 不引入新画布能力。
- **外部 MCP 会话具现为画布上的 Agent 节点（决议 Q12-a）**：外部会话绑定到画布上的
  Agent 节点，复用节点绑定的确认门（`request_confirmation` 强制 node_id，
  `app/routers/api_agent_tools.py:510-511`，必填校验 `:532-533`）与画布可观察性
  （外部会话的工具调用在画布上投影为可见任务/结果，画布 TUI 可观察外部会话）。
  ADR 0012、ADR 0019 不修订，只引用：外部会话与应用内 pi 会话共享同一套会话管理与
  权限层，不建第二套。
- **安全边界（v1）**：仅 loopback 绑定 + Host 头校验。服务现仅绑定 127.0.0.1
  （`main.py:1318`，uvicorn `host="127.0.0.1"` port=3000），MCP 端点沿用该绑定，
  外部方只能是同机进程；token 鉴权留接口、v1 不实现。同源守卫为前置票 #240
  （#233 审计：agent-tools 端点缺同源守卫，任意本机进程可驱动项目）。
- **确认门阻塞语义**：烧钱/破坏性工具的确认门为「阻塞等待」——MCP 工具调用阻塞
  直到用户在画布上点击确认/拒绝（`request_confirmation` 带超时阻塞等待用户决策，
  `app/routers/api_agent_tools.py:579-580`，默认 60 秒 `:530`）；不设 fire-and-forget
  变体（仅 `generate.request_batch` 的 #400 窄例外见下）。因外部会话具现为 Agent 节点，「向谁要确认」永远是画布上确定的 tab。
- **会话→项目绑定**：现 spawn-time env 绑定（`FIREFLY_AGENT_API_BASE` /
  `FIREFLY_AGENT_PROJECT_ID` / `FIREFLY_AGENT_NODE_ID`，spawn 时注入 pi 子进程，
  `app/services/agent_host.py:164-166`）只适用于应用内 pi 会话，不适用于外部连接。
  外部会话用 `?project=` URL 参数 + `client_id` 注册表（client_id→project，前端
  WS 连上后上报）+ 三级仲裁 + 命令定向下发单 tab：
  1. `?project=` 显式指定项目 → 下发给该项目的 tab；该项目无打开的 tab → 报错提示先打开项目；
  2. 未指定且多个项目的 tab 打开 → 报错提示指定 `?project=`；
  3. 未指定且仅单个项目 tab 打开 → 下发给该 tab。

  命令单 client 定向下发（不广播）；超时文案区分「tab 已关」与「可能仍在跑，
  先取画布状态再重试」。仲裁形态抄 ComfyArt（参照仓库）`api/mcp_commands.py` 的
  `_target(project)` 三级仲裁，经 #233 拷问签署。
- **超时契约（写进文档）**：wait 类工具可阻塞数分钟——`generate.wait` 默认 120 秒、
  上限 600 秒（`app/routers/api_agent_tools.py:357`，`timeout: int = Query(default=120, ge=1, le=600)`）；
  外部 MCP 客户端的请求超时须设为 600 秒（签署的「600s 超时契约」），交付文档含
  Claude Code / Codex / Cursor / VS Code 客户端配置示例。

## 已知缺口（#243 顺带修，审计见 #233）

- 假工具 `canvas.get_selection` 恒空（`app/routers/api_agent_tools.py:119-124`）：摘除或实装。
- viewport 三件套读写无人消费的空 dict（`app/routers/api_agent_tools.py:31` `VIEWPORT_STATE`，
  端点 `:455-505`）。
- `_apply_mutations`（`app/routers/api_agent_tools.py:45`）不调 `broadcast_canvas_updated`：
  外部会话改动不实时反映到其他标签页。
- `generate.wait` 的 timeout 参数发在 GET body 被忽略（`app/agent_extensions/canvas-tools.ts:254-258`
  vs Query 参数 `app/routers/api_agent_tools.py:357`）。

## Consequences

- 新依赖：`requirements.txt` 现无 mcp 包，streamable-HTTP MCP server 需引入（#243 依赖注意）。
- 外部会话与应用内 Agent 节点共用确认门、权限层与生成历史（ADR 0012）；破坏性操作
  仍走 ADR 0016 的删除后果矩阵与确认流程，不另设外部专用路径。
- 多 tab 场景（同机多项目/多页）依赖 client_id 注册表与三级仲裁保证命令路由与
  确认门投影无歧义；实现前先跑多 tab 契约测试确认边界（#243）。
- v1 无 token 鉴权：安全边界是「仅同机 loopback 进程可连」+ 同源守卫（#240）；
  将来若需非 loopback 开放，必须先实现 token 鉴权并修订本 ADR。
- 超时契约（wait 类工具可阻塞数分钟、客户端 600s）成为对外交付承诺，客户端配置
  示例是文档交付物的一部分。

## 修订（#357，2026-11-16）：确认门移到服务端统一执行入口

- 确认门不再由 `canvas-tools.ts` 客户端预确认（原「先 POST /agent/confirm 再执行」）；
  改挂在 pi REST 与 MCP 共用的服务端执行入口：`api_agent_tools._server_confirm_gate`
  （复用 `request_confirmation_core`），pi 写操作 URL 带 `?agent_node_id=` 指明归属节点。
- 覆盖面：`DELETE /canvas/nodes`、`DELETE /canvas/edges`（新增）、edit_crop/rotate/flip/
  outpaint/erase、`DELETE /media`——这些 Agent 工具写操作在服务端确认。MCP 侧
  `canvas.disconnect` 同步入门。此修订原先允许同一 REST URL 的无头同源直写绕过确认，
  **该兼容规则已由 #456 取代**；其余确认规则仍有效。
- 确认等待超时 60s → 90s（`AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS` /
  `api_mcp.CONFIRM_TIMEOUT_SECONDS`）：写操作（含门等待）必须大于旧 60s 门槛。
- 本 ADR 上文引用的行号（`:579-580`/`:530`）为撰写时快照，已随 #357 重构漂移。

## 修订（#456）：Agent 工具写入 REST URL 专用

- Agent 写入工具 URL 必须携带非空 `X-Agent-Project`；缺头或空白返回标准 Agent 错误
  `400 INVALID_ARGUMENT`，不能绕过服务端确认、归属、记账或生成限额。该头是调用上下文，
  不是鉴权凭据；同源和 Host 检查不变。
- 覆盖节点、边、布局、参考图、媒体上传/删除、视口变更、生成触发、批量编辑和操作撤销。
  批量提示词工具只在应用差异时要求该头，预览仍只读。
- 浏览器的用户写入走 `/api/project-library/`，宿主控制走 `/agent-nodes/`；这些路径、
  只读工具和 MCP 不改。`/agent/confirm` 是确认请求接口，不执行画布工具写入。

## 修订（#400 / A10）：仅新生成批次异步确认

- `generate.request_batch` 立即返回 `batch_id` 和 `awaiting_confirmation`（已获批的重复请求返回 `approved`）。同一预检只登记一次；`generate.batch_status` 可等待下一次状态变化（上限 600 秒），`generate.stop_batch` 不取消已开始项。
- 复用现有 Agent 浏览器整批确认（90 秒），展示总张数、备选模型与重试次数；批准前不写画布、历史、用量或会话确认日志，也不调用服务商。外部 MCP 用同一有界 `Mcp-Session-Id`，确认选择已存在的 Agent 节点；不为预检或待决批次创建、重绑节点。
- 批准时核对预检快照与配置，合计张数原子计入会话额度。逐个启动已批准项，每次只运行一个既有持久化生图任务，本版本不重试或切换模型。批次自身仅驻内存，不提供重启续跑、长期待决或逐项确认。A12 / D6 另交付。
- `canvas.batch_edit` 仍一次同步整批确认，保持原响应与原子改词行为，不迁入新工具。其他确认档位与默认值不变。

## 修订（#402 / A12）：确认收据与同轮系统续接

- 花钱调用统一立即返回 `CONFIRM_PENDING` / `awaiting_confirmation` 及 `confirm_id`；原执行在服务端等待，不由 Agent 重复提交。确认事件携带准备好的提示词、参考图、模型、尺寸和张数，确认及处理结果进入现有回放日志。
- 非花钱门保留 90 秒同步响应窗口；窗口结束只返回待确认收据，不撤卡、不拒绝，也不删除确认处理器。待决操作仅在用户决定、停止、宿主/会话/用户轮次失效时结束。仅保留当前进程内待决，不承诺重启恢复执行。
- `standard` 是新会话默认值：批次一次批准；`cautious` 对每项花钱调用确认；`autonomous` 免花钱门，但删除非本轮新建节点、删除非 Agent 新建连线和删除媒体仍须确认。免门归属只采信服务端操作日志，不采信调用参数或节点自报字段。
- 批准前不写画布、生成历史或用量，也不调用服务商。批准后重新核对宿主、generation、原用户轮、输入及服务商配置，写入时继续执行 CAS；旧批准不能授权变化后的输入。
- 已准入的图片任务在独立于待决确认的上下文中归档和结算，保留原操作/轮次归属；后续宿主、用户轮或服务商设置变化不能使已花钱任务永久 `cleanup_pending`。尚未准入的新调用仍检查原确认有效性，不把结算许可扩成新的执行许可。
- 操作结束由原宿主接收系统续接，沿用原 `turn_id` 和操作撤销归属；忙碌时按现有待发队列顺序投递，不伪造用户输入、不创建新用户轮。外部 MCP 不向承载节点的宿主注入系统续接。
- 本修订覆盖上文 #357 的超时失败约定和 #400 的临时 90 秒过期、批准前不记确认日志及缺少长期待决约定。`canvas.batch_edit` 仍保持一次整批确认与原子改词，不迁入新批次工具；D6 的逐项选择界面另交付。

## 修订（#457）：前端事件关联与项目对话读口

### 确认目标与逻辑轮

- `confirm` 精确新增顶层 `target_node_ids: string[]`，原 `confirm_id/tool/params/message/host/generation/turn_id/seq` 不变；`confirm_resolved` 同样携带该数组。live 与保存/回放是同一 payload。按准备输入或真实参数的顺序稳定去重，不用承载确认卡的 Agent ID 代替目标。
- `generate.request_batch` 从已准备的 `preflight.items[].node_id` 取目标，cautious 逐项门只含当前项；`generate.trigger/retry` 与旧 `canvas.batch_edit` 从准备输入取目标。`canvas.batch_prompt_edit` 只含 diff 中确有变化的节点，不含 unchanged 项。布局门含本次准备变更中已存在的节点（含受影响框），不含尚未创建的框或被跳过节点；单节点编辑/删除用实际 `node_id`，断边用两个端点。媒体删除不改画布引用，因此为 `[]`，不猜引用节点。目标字段仅供呈现，不授予批准、归属或 CAS 权限。
- 新 `agent_end` 一律含 `turn_id: string | null`：在分发入口、任何异步持久化/订阅者和 FIFO 投递之前捕获该响应的逻辑轮。值就是 #393 操作日志的 turn ID，不是原生 thread/session/generation、MCP request ID 或下一条排队输入的轮。`user_prompt` 同时保存其真实用户轮的 `turn_id`。
- 一条真实用户消息开始一轮；排队用户消息实际投递时才开下一轮。系统确认续接不产生 `user_prompt`，它的 `agent_end` 仍指原轮；仅排队的续接不截断当前助手消息，实际投递才构成消息边界。没有可证明的活动用户轮时返回 null，不借用上一轮，也不为旧日志伪造/回填 ID；既有无此字段的日志原样保留。重试、停止、批准有效性、FIFO、#402 续接与 #442 回收语义不变。

### `chat.history` / `chat.search`（REST、MCP、Pi 共用）

REST 为 `GET /api/projects/{project_id}/chat/history` 和 `/chat/search`；参数用 query string。MCP/Pi 工具名为 `chat.history` / `chat.search`，参数同名 JSON 对象。全部只读，不创建/重绑 Agent，不启动宿主，不恢复待发队列，不修复/迁移画布，不读用户全局宿主目录。旧 `/agent-nodes/{node_id}/events` 和 `/events/page` 合同不变。

- 两工具接受 `scope: "node" | "canvas"`、`node_id?: string`、`host?: "pi" | "codex"`、`limit?: integer`（默认 50，1..200）。history 默认 node，search 默认 canvas。node 的缺省目标是发起工具的 Agent；REST 可用 `agent_node_id` 指定此缺省值。显式 `node_id` 优先，仍限定本项目。
- node 省略 host 时读当前 Agent 的活跃宿主；已删除/缺失/非 Agent 目标必须显式 host，不能猜历史宿主。显式 host 的缺失历史安全返回空数组。canvas 禁止 node_id，只查当前项目所有保留日志，含已删除 Agent；host 省略读两宿主。
- `chat.history` 另接受仅 node 可用的 `before_seq?: positive integer`（严格小于）；返回最近 limit 条消息，**页内 seq 升序**，`next_before_seq` 供向前翻页。canvas 返回对话摘要而非事件原文。
- `chat.search` 必填 `query: string`（裁剪空白后非空，原长最多 200），按 Unicode casefold 字面量匹配，不是正则。结果每条代表一条消息，不把 delta 拆成命中；默认跨本画布内各 Agent，而不跨项目。未知参数、错误范围/类型或越界限制为标准 `400 INVALID_ARGUMENT`。
- 项目是读取边界，不把 MCP session 当独立私有租户：同项目 canvas 显式共享该项目 Agent 历史；node 范围不会混入其他节点/宿主。REST 的项目路径、MCP 既有项目仲裁为权威；若提供 `X-Agent-Project`，必须与之相同，否则 `403 PROJECT_MISMATCH`。MCP JSON 参数不能覆盖 project/caller/session。沿用同源/Host 守卫，无新增跨项目搜索权限。
- 会话身份为 `{node_id}:{host}`，表示节点该宿主的**保留日志流**，不是某个原生 thread、分支或进程；换原生绑定仍是同一日志流。`retired` 表示当前画布已无该 Agent。消息 ID 为 `{node_id}:{host}:{seq}`；seq 是节点事件游标，不跨节点比较。助手消息用首个 text delta 的 seq（无 delta 时用 message_end 的 seq），合并流式 delta 与最终正文，避免重复；历史旧无 host 记录归 Pi。
- 仅投影服务端 `user_prompt`、用户 steer 及助手可见正文；未投递 follow-up、系统确认续接、工具参数/输出、思考、raw、附件/引用上下文、用量与原生配置/凭据均不搜索或返回。用户/助手正文自己写出的内容仍是正文，不承诺对正文做秘密识别/删改。旧轮 ID 不明时 message.turn_id 为 null。

### 精确响应形状与有界读取

所有成功响应共有：

```typescript
{ project_id: string, scope: "node" | "canvas", node_id: string | null,
  host: "pi" | "codex" | null }
```

node history 额外字段：

```typescript
{ messages: Message[], has_more: boolean, next_before_seq: number | null, truncated: boolean }
Message = { message_id: string, node_id: string, host: "pi" | "codex", seq: number,
  timestamp: number, turn_id: string | null, role: "user" | "assistant",
  text: string, text_truncated: boolean }
```

canvas history 额外字段：

```typescript
{ conversations: [{ conversation_id: string, node_id: string, host: "pi" | "codex",
  retired: boolean, title: string, last_message_id: string, last_seq: number,
  timestamp: number, snippet: string, message_count: number }], has_more: boolean, truncated: boolean }
```

search 额外字段：

```typescript
{ query: string, hits: [(Omit<Message, "text"> & {snippet: string})], has_more: boolean, truncated: boolean }
```

- timestamp 为事件时间的 epoch **秒**；无有效值为 0。搜索命中和会话摘要按最近消息的 `(timestamp, node_id, host, seq)` **降序**，同值顺序确定。message_count 仅为本次保留扫描内的消息数，不是假称终生总数。title 最多 200 字，snippet 最多 240 字；search 摘要围绕第一次匹配，history 摘要取开头。
- 每请求最多检查目录前 500 个条目（顺序取文件系统枚举，超限不承诺全局最新；选中的文件按名字排序），每文件最多读取尾部 2 MiB / 2000 行，全请求最多 16 MiB；丢弃被裁断的首行和未完成的最后一行。消息正文最多 16000 字，超出标 `text_truncated`，超出的正文不参与匹配。路径越界/链接文件不跟随，记录内 project/node 不符则丢弃，不读取相邻项目。
- `has_more` 仅指扫描内还有超过 limit 的结果（node 可用 next_before_seq 继续；canvas/search 可缩小范围或提高 limit），不是无限游标或完整性证明。`truncated` 表示扫描预算、目录上限或个别文件读取失败，可能还有未扫描历史；保留策略已经删除的历史不能找回。不建全局索引，不保证全库精确总数。空命中正常返回空数组、has_more=false；项目缺失为 `404 PROJECT_NOT_FOUND`，画布/历史目录不安全或不可读为 `409 CHAT_HISTORY_UNAVAILABLE`，均使用现有 Agent 错误工厂。

## 修订（#406 / C1）：只呈现的结构化事件与人工答题回执

`agent.present` 是 manifest 的共享能力，REST 为 `POST /api/projects/{project_id}/agent/present?agent_node_id={agent_id}`，MCP/Pi 同名。REST、MCP 的项目仲裁和现有同源守卫仍是权限边界；可选 `X-Agent-Project` 不得与真实项目不同。必须指向已有 Agent，呈现不建节点、不重绑宿主、不批准、不执行、不扩文件权限。外部 MCP 可以建立仅用于事件记录的内存会话，不启动原生进程。

### #411 接入合同

输入为以下互斥对象；各层未知字段均拒绝，身份/轮次/执行状态不接受客户端声明：

```typescript
{ action: "show", kind: "plan", title: string, steps: {id: string, text: string}[] }
{ action: "show", kind: "plan", title: string, plan_id: string, generation: number } // #482，正整数；与 steps 互斥
{ action: "show", kind: "question", title: string, questions:
  {id: string, text: string, options: {id: string, text: string}[], recommended: string}[] }
{ action: "show", kind: "diff", title: string, op_id: string } // 与 diff_id 二选一
{ action: "show", kind: "diff", title: string, diff_id: string }
{ action: "get", presentation_id: string }
```

- show/get 成功均返回 `{presentation: P}`。P 精确为 `{schema: 1, id: string, kind: "plan"|"question"|"diff", title: string, state: "presented"|"pending"|"answered"|"expired", data: object, answers?: Record<string,string>}`。ID 由服务端生成；plan/diff 为 presented，question 初始 pending，答题后 answered，失效后 expired；answers 仅为实际人工选择的题 ID → 选项 ID。
- 旧 steps 形态 plan 的 data 是 `{steps}`，仅是提案，不改执行 todo；#482 的真实预览引用形态见下文。question 的 data 是 `{questions}`；推荐值必须存在于该题选项，题 ID/题内选项 ID 必须唯一，多题同卡。C1 仅显示选项/推荐与“按推荐开始”，不做 #411 的编辑、勾选等完整交互。
- diff 的 data 从本调用者有权读取的真实引用解析，不接受助手自述“已应用”：operation 为 `{source:"operation", op_id, turn_id, tool, before, after, undone}`，preview 为 `{source:"preview", diff_id, items, applied:false}`。读取 preview 不消费原确认/CAS 对象；没有真实操作或预览回执不能伪造差异卡。
- live 事件为 `{type: P.kind, presentation: P, turn_id, host, generation, seq, ...既有事件元数据}`，同一 payload 进入已有 /events、/events/page 和 JSONL；项目、节点、host/generation、原 turn 和 seq 均来自服务端，不猜原生协议、不回填旧轮次。展示、人工决策和投递失败更新沿用同一 presentation ID，TUI 原位更新，历史回放只读。普通助手正文不解析成这些事件。
- 宿主投递失败时，question 事件可另带顶层 `delivery_error`（标准 Agent 错误对象），并保存/回放；它不是 P 内的答案字段。“answered”只证明人工选择已记录，不证明原生宿主已收到、执行或确认。UI 必须显示真实投递错误；真实 HTTP 拒绝也不能被已记录的答案遮掉。

### 人工回答不是执行确认

`GET /api/projects/{project_id}/agent-nodes/{node_id}/questions/{id}` 返回 `{presentation:P, answerable:boolean, delivery_error?:AgentError}`，不创建/恢复会话。前端先查当前回执，再向同路径 `/answers` POST `{answers:Record<string,string>, request_id?:string}`，必须为所有题各提交一个合法选项。成功为 `{presentation:P, input:既有输入准入回执|null}`。

- hosted 的回答经真实用户输入入口和 FIFO；忙时排为 follow-up，实际投递才产生新用户轮，不借 #402 确认门、不自动批准、不造同轮系统续接。队列准入成功不等于原生投递成功。相同答案重发只读原回执，不重复写管道；不同答案冲突。已记录的管道错误也原样重回，不因另一 POST 重试不确定副作用。
- 任何新输入、停止、host/generation/输入 epoch/原轮或节点身份变化均重新校验；陈旧按钮不能跨宿主提交。UI 失焦、宿主/项目切换后的迟到响应不能更新新视图。
- external MCP 的答案只存储；必须用原项目、节点和显式 Mcp-Session-Id 的 get 读取，不能向应用内另一宿主注入、自动开跑或冒充 hosted。show/get 不承诺服务重启后恢复执行；只读历史事件不等于可回答的活动对象。
- 每个 ProjectStore 最多 256 个内存回执，30 分钟有效，最旧项会被淘汰；失效读取/回答为 409 CONFLICT。参数/解析差异最多 65536 UTF-8 字节；标题最多 200 字，ID 最多 128 字，题目/步骤/选项文本最多 2000 字；步骤 1..50、题 1..10、每题选项 2..10。内存状态不代替持久执行或无限历史查询。

### 提示词与验证边界

Pi/Codex 的首次与恢复提交共用一份默认策略；三档 suffix 仅保留空兼容值，确认档位只由服务端执行。策略要求先一次性提问、真实计划/差异回执、保护人工内容、附件和参考图角色按 §13、按真实错误与现有两次预算恢复、pending 后结束当前响应而非 abort、一句结果加真实差异。Pi 的工具 allowlist 包含共享扩展命名空间；不开放内置文件、shell 或网络工具。

#406 的本地桩只证明文本、真实提交边界、工具/确认管线和 TUI，不证明模型行为；#405 保持 OPEN，真实 B2 指标 NOT_RUN/null。原 Agent `plan.apply` 超出同步窗口仍返回 PLAN_NOT_CONFIRMED，通用 resumable 缺口归 #475；下文 #484 仅接通人工应用，不修复此缺口。

## 修订（#482 / #484）：真实方案引用、人工预览编辑与统一人工应用

#484 取代 #482「卡片应用向原 Agent 发送真实用户输入」的约定：所有 hosted Pi/Codex、external MCP、REST 导入的预览统一返回 `apply_via:"human_apply"`，浏览器直接走人工应用接口。旧 steps 卡、question/diff/get、原 Agent 工具及精确 scope 保留；不增加权限档位、持久方案或自动执行。

### 引用不等于任务快照或执行权限

`agent.present` 新增与 steps 互斥的 `{action:"show",kind:"plan",title,plan_id,generation}`。`generation` 必须为正整数；缺失、布尔值、浮点值或未知字段为 `400 INVALID_ARGUMENT`。服务端在原调用者的精确方案 scope 中解析真实预览，REST、hosted MCP 和 external MCP 的 binding 由各适配器传入，不从参数推断。换项目、节点、MCP 会话、宿主/输入分支/epoch 或方案 generation 后不能借呈现穿透原 scope。

成功仍为 `{presentation:P}`，其中 `P.kind="plan"`、`P.state="presented"`、`P.data={source:"plan_preview",plan_id,generation}`；不把 tasks、warnings 等完整工作簿内容重复塞入既有 64 KiB 呈现预算。`source` 来自服务端，调用者不得提供。live、`/events`、`/events/page` 与持久 JSONL 共用原事件管线；顶层事件 generation 仍为宿主运行代次，`P.data.generation` 才是方案修订代次，两者不能互换。历史引用记录当时代次，不自动更新或恢复为活动执行对象。

### 人工接口与完整预览

- `GET /api/projects/{project_id}/plan/previews/{plan_id}?agent_node_id={actor}`：读取当前完整预览；可加唯一的正整数 `generation` 作陈旧检查。
- `POST /api/projects/{project_id}/plan/previews/{plan_id}/edits?agent_node_id={actor}`：JSON 必须且只能为 `{generation:正整数,ops:[编辑对象]}`。每项必须有真实 task_id，并且只选 `select:boolean`、`rename:string`、`merge_into:task_id`、`set_section:string` 之一；不支持 split、set_refs 或新增动作。
- `POST /api/projects/{project_id}/plan/previews/{plan_id}/apply?agent_node_id={actor}`：JSON 必须且只能为 `{"generation":正整数}`。actor 是导入预览的原 Agent 节点；只采用当前 selected，不接受 task_ids、layout、approved、turn_id 或来源/绑定字段。
- 三者均要求严格同源的 Origin 或 Referer，且 `X-Agent-Project` 必填并匹配路径项目；依赖现有 loopback/Host/项目守卫。来源由专用 HTTP handler 固定，不接收 caller/source/binding/apply_via；未知字段、重复查询键以及 POST 查询中的 generation 均拒绝。正文 generation 不接受布尔值或浮点值。服务端验证实际 Store、预览所属项目/节点和现存 Agent 节点；不创建或重绑宿主会话。
- 这是现有本地同源信任边界，**不是密码认证**；不承诺抵抗同源恶意代码或能伪造 HTTP 头的本机进程。

### 原 scope、CAS 与失效

人工读写有效预览只需该 Store/project/actor，不要求浏览器冒充导入时的 MCP SID 或宿主；原 Agent 通道仍要求完整 scope 精确相等，外部 MCP 必须保留原 SID，没有新增 MCP human 模式。编辑复用原验证、深拷贝和缓存对象身份 CAS，必须带当前 generation；整批成功只加一，并发同代次只有一方成功。旧代次、错 scope、缺失/淘汰/过期预览为 `409 PLAN_STALE`；请求格式/参数非法为 `400 INVALID_ARGUMENT`，找不到任务沿用 `404 TASK_NOT_FOUND`。编辑失败不部分回写或递增代次；成功编辑不改画布/媒体，不批准应用，旧 presentation 的引用代次也不随之回填。

人工仍可查看、编辑原 Agent scope 已失效但尚在 RAM 有效期内的预览，**应用不恢复失效 scope**。服务端从原 Preview 取得 binding，检查原会话、输入 epoch、host、branch、附件及摘要；会话失效/消失、原 epoch/host/branch 缺失或不再匹配、输入被 abort、节点身份改变、原附件缺失或摘要变化均为 `409 PLAN_STALE`，不唤起、重绑或复活会话。原会话可以不活跃但必须仍有效，不要求外部 Agent 正在运行。#411 必须将此失效显示为「方案已过期，请让 Agent 重新导入」；本票不实现卡片 UI。

### 应用状态与原通道查询

人工 GET、edits、已完成的人工 apply，以及原通道幂等 `plan.import_excel` 均返回完整预览：`{plan_id,generation,agent_node_id,apply_via:"human_apply",preview,application}`。agent_node_id 来自服务端原 actor；preview 保留 tasks（各自含 selected）、sections、warnings、excluded、suggested_refs 与 source。application 为服务端状态：

```typescript
{
  state: "ready" | "awaiting_confirmation" | "applied" | "unknown",
  generation: number, // 当前方案代次
  receipt: null | {
    generation: number, // 真实已提交代次，不随编辑改写
    applied_by: "human" | "agent",
    result: {groups, tasks, already_applied, op_id, touched_node_ids} // 真实创建回执
  },
  confirm_id?: string, // 当前真实待确认 ID
  error?: {code, message, hint, retryable, details?} // 最近一次实际应用错误
}
```

ready 表示当前代次尚未提交且无待决应用；awaiting_confirmation 不是成功；applied 必须有当前代次提交证据；unknown 表示证据不足，须核对而非自动重试。只保留最近一次真实成功回执；编辑到新代次可以回到 ready，同时 receipt 保留实际上一已提交 generation，不能称新代次已应用。更早操作按既有 ops 合同查询。回执给出真实框、prompt_node/gen_node、已有项和操作 ID，不伪造生成 task_id 或「已生成」状态；空槽通过返回节点及画布读口查看，真实生成后才用既有 generate 查询。

原 Agent 后续用同附件、摘要、sheet 和原 binding/scope 再次 `plan.import_excel`，读取同一 plan_id 的最新人工编辑与应用回执；正常同分支下一轮不因运行代次变化失去查询资格，新 SID/宿主/分支或已 abort 上下文不获此权限。该查询仍须原附件存在且有效。预览及回执仅 RAM，每 Store 最多 64 项、1800 秒，读、编辑、应用均不刷新 TTL；淘汰、过期或重启后不承诺恢复，也不承诺跨重启 exactly-once。已知 op_id 可按既有 ops 合同查询，不新增 `plan.get`、共享工具或 manifest 改动，不提供附件消失后的按 plan_id Agent 查询。

### 人工执行、确认与代次消费（迁移 #399）

人工应用直接复用准备变更、确切任务确认、输入/附件/画布复核、最终 CAS、operation journal 与 undo，不调用 `/prompt`，不向任何 Agent 注入输入或系统续接，不启动/恢复原生宿主。审计归属仍为原 Agent 节点与 `plan.apply`，人工来源由服务端记录，`turn_id=null`，不借旧 Agent 用户轮或制造新用户轮。只创建所选任务的非空提示词、input 边、空生图槽和板块框；不生图、不导入媒体、不连接产品图片，不覆盖幸存任务或扩张人工改过的框。原 Agent `plan.apply` 仍可用；省略 task_ids 才采用 selected，显式 task_ids 合同保留，但同受以下消费约束。

仅人工应用接入 resumable，使用 `confirmation_transport(hosted=False)`，即使预览由 hosted 导入也不投递 Agent continuation。90 秒只是同步窗口；超窗返回真实 `CONFIRM_PENDING` / awaiting_confirmation 与 confirm_id，原执行继续等待人工决定，由 GET 查终态，不重发 POST 或 Agent apply 来回放，不把 pending 当成功。批准前不写画布/媒体；批准后仍复核原输入、附件、画布 revision、当时运行代次和确认归属，再执行最终 CAS。编辑、停止、节点/宿主/分支/输入 epoch/运行代次、画布或附件变化使旧确认失效；拒绝或已证明未提交的失败释放自身预留，不递增方案代次，也不能清理或覆盖新代次的状态。原 Agent 超窗缺口仍归 #475，不在本票扩展。

**#484 显式取代 #399「同 generation 重复成功、再补另一批任务」的增量合同**：在 Store 锁内预留当前代次，任一来源（human 或 agent）的首次真实非空提交消费该 generation。当前代次已消费后，任一来源再 apply 均为 `409 PLAN_ALREADY_APPLIED`，details 含 plan_id/generation、真实来源和回执；待决竞争请求为 `409 CONFLICT`，不新增确认卡或第二次执行。追加任务须先编辑形成新 generation，再重新批准。undo 或人工删除节点不复活已消费代次；旧代次仍由 CAS 拒绝，最新 receipt 保留真实提交代次。人工零勾选为 `400 INVALID_ARGUMENT`；原 Agent 空操作保留；全部所选项已存在时仅返回真实 no_changes/已有项信息，均不消费代次、不伪造 applied 或无操作日志。

消费只按既有 journal/persisted proof 与操作回执结算，批准、内存变化、start 广播或单独拿到 op_id 均不是提交证据。已证明落盘前失败不标 applied；已证明落盘后，即使 end 广播/HTTP 报错或 sidecar append_failed，仍消费代次并保留真实 receipt，同时如实保留错误与日志限制。公开的 `application.error` 保留真实错误 code/message，并将已证明提交的错误标为不可重试、提示勿重复提交；同项目后续 flush 不能抹去该错误。append_failed 的响应为错误而非无警告的成功，details 中说明日志状态与撤销不可用；与流错误同时发生时两个线索都保留。无法判定有无提交则为 unknown，不解锁自动重做。撤销仍走真实 `ops.undo`；append_failed 可能意味着撤销不可用，不能声称所有报错都可逆。

这些是后端接缝，不等于真实模型、原生 SDK 或 #411 浏览器视觉验收；本地同源信任也不扩成 OS 沙盒。

## 修订（#487）：生成批次子集批准与 batch-only 审批快照

本节仅修订 `generate.preflight/request_batch/batch_status/stop_batch` 的新生成批次，不迁移普通非批次确认或 `canvas.batch_edit`。**显式取代上文 #402 中批次的 `autonomous` 免花钱门例外**：三档批次均先返回真实 `awaiting_confirmation`，不可自动批准。`standard/autonomous` 一次批准当前所选批次；`cautious` 先选择子集（不扣额度、不建任务），再为所选项逐项等待原有真实花钱门。未选择项为 `skipped`；逐项门只批准该项，不能借选择门执行尚未批准的付费调用。

### 项身份、快照与一次决策

- 每项发布服务端生成的不透明 `item_id` 和真实 `node_id`，不是列表下标或客户端声明的映射。编辑/移除后的幸存项保持 ID；移除的 ID 不复用。预检、确认事件的 `params.preflight` 和人工读口共用 prepared 项（含有效正文 `user_prompt`、`final_prompt`、refs、model/provider、aspect_ratio、size、count）。
- 快照发布正整数 `preview_revision`、小写 64 位 `snapshot_sha256`；与宿主 generation 无关。摘要绑定原 caller/project/actor、节点绑定、源选择/输入/服务商证明、顺序与项身份、真实 prepared 输入及路由、获准的 fallback_models/max_retries。request_batch 会把执行选项封入摘要，应以当前真实门的快照为准，不能沿用初次 preflight 的旧摘要。
- 批准集按当前门的项顺序规范化，另封 `approval_sha256 = SHA256(snapshot_sha256 + JSON(canonical approved_item_ids))`；此 JSON 为无额外空白的 UTF-8 数组。原门只可消费一次；执行只遍历封定选择，不重新放宽为完整可变 preview。
- 批次 HTTP 决策对象精确为 `{confirm_id:string, approved:boolean, approved_item_ids?:string[], snapshot_sha256?:string}`。`approved` 是严格 JSON boolean；未知字段、缺失/错误类型、非小写 64 位十六进制摘要、子集缺摘要、拒绝夹带子集均为 `400 INVALID_ARGUMENT`，不作真假值转换或吞字段后整批批准。内部已注册回调保留严格 bool 兼容，并支持同一结构化对象；不是可以伪造 Caller 的新入口。
- 合法旧 `{confirm_id,approved:true}` 仅批准这个仍待决 ID 所指的当前门全部项（cautious 逐项门即该一项）。合法格式但不匹配的摘要、空/重复/未知/他批/已移除项、旧门/重复消费为 `409 CONFIRM_STALE`；项数组本身不是字符串数组为 `400 INVALID_ARGUMENT`。拒绝可带当前摘要作陈旧检查，但不可带批准集。来源/服务商输入变化仍可能先返回 `409 PREFLIGHT_STALE`，不统一改名为 CONFIRM_STALE。
- `batch_status` 额外返回 `preview_revision/snapshot_sha256/approved_item_ids/approval_sha256`，各任务行带 item_id。额度按真正选择项的 count 求和；未选项不建任务、不扣额度、不调服务商。全量超过会话上限仅给 `BUDGET_EXCEEDED` warning 并保留清单，真正准入仍检查所选张数上限；其它 blocker 不放宽。cautious 只在每项付费批准后计该项一次，不在选择阶段重复扣总数。确认 HTTP 的接收成功不是额度准入、任务成功或执行完成，最终仍读 batch_status/task；后续额度失败可能表现为批次终态 reason，而非第二个确认 HTTP 错误。#401 的 replay_safe 与重试上限继续适用，无单价配置时不制造金额。

### 人工 GET、edit_item / remove_item 的精确 HTTP 合同

`edit_item/remove_item` 在本节表示单项操作，**实际路由后缀为 `/preview/edit`、`/preview/remove`，不是同名的新 URL**。共同前缀为 `/api/projects/{project_id}/agent-nodes/{actor}/batches/{batch_id}`：

| 操作 | 请求 | 成功（HTTP 200） |
|---|---|---|
| `GET /preview` | 无 query 参数 | `{batch_id,status,preflight,confirm_id,actionable,decision_token}`；无待决 handler 时后三项为 `null,false,null`。 |
| `POST /preview/edit` | 严格 `{preview_revision:positive integer,snapshot_sha256:string,item_id:string,patch:{prompt?:string,runSettings?:object}}` | 同上，返回安装后的真实门；有效 no-op 返回原快照/门。 |
| `POST /preview/remove` | 严格 `{preview_revision:positive integer,snapshot_sha256:string,item_id:string}` | 同上，移除后的快照或空批次终态。 |

- 两种 POST 顶层键必须齐全且无额外字段；revision 不接受 bool/浮点值，item_id 必须非空白字符串，摘要必须小写 64 位十六进制。edit 的 patch 非空且只允许 prompt/runSettings；prompt 非空白、最多 100000 字符，替换本批该项的**有效任务正文**，不是整个含参考说明的 final_prompt。runSettings 只接受既有 `model/provider_id/aspect_ratio/count/routingMode`：字符串最多 200 字，count 严格整数 1..8，routingMode 为 auto/manual，aspect_ratio 为可解析的正宽高比；实际模型/服务商可用性仍由真实预检判断。不得开放 size、refs、任意 payload 或权限/来源字段。空 runSettings 对象本身合法；有效输入未变仍是 no-op。
- 三个人工读写口及批次 `/agent-nodes/{actor}/confirm` 均要求严格同源 Origin 或 Referer，且恰一个与路径匹配的 `X-Agent-Project`；缺失/错误/重复项目头为 `403 PROJECT_MISMATCH`。任意 query 字段（含身份覆盖、重复键）为 `400 INVALID_ARGUMENT`；Origin/Host 仍走现有守卫。HTTP 不接收 decision_token 或 caller/source/SID/host/generation/turn 等身份字段，人工通道由专用 handler 固定，不把 body 当原 Agent Caller；审计保留服务端原批次的 rest/mcp 来源。不是密码认证，也不防同源恶意脚本或能伪造头的本机进程。
- 人工接口定位实际 Store/project/原 actor 下的 RAM 对象、现存 Agent 和原 Origin，不要求浏览器冒充 MCP SID，也不创建/恢复/重绑宿主。原 Agent/MCP 工具仍检查完整 caller key 和原 SID，不提供工具参数 human 模式。
- 只可在初始 `awaiting_confirmation`、尚未决定选择且真实门可操作时 edit/remove。旧 revision/hash、未知/移除项、选择已决定、运行态及对象/来源失效为 `409 CONFIRM_STALE`；请求格式/字段非法为 `400 INVALID_ARGUMENT`；真实重预检 blocker 为 `409 PREFLIGHT_BLOCKED`，原输入/服务商变化为 `409 PREFLIGHT_STALE`。失败不安装部分候选，不把所有错误冒称画布 CAS 冲突。
- 编辑深拷贝 batch-only overrides，经**同一 canonical resolver 的正文组合边界**重算提示词、参考编号/角色、模型路由、比例/count 和汇总，保留共享参考前缀，不让上游 prompt 吞掉正文覆盖。耗时 IO 不持安装锁，最终按 Store/批次锁序复核原对象身份、输入证明与快照 CAS。
- **不回写画布节点、连线、revision、runSettings 或人工编辑标记，也不预留回写层**；remove 只移出审批快照，不删除画布节点。有效 no-op 保留 revision/hash/confirm_id 和原期限，不造日志或延寿；真实 edit/remove 递增一次 revision、更新摘要、作废旧门并发布新真实门。原 waiter 以 replaced 结束后继续等新门，不把替换当整批拒绝、不重放旧执行或重复续接。最后一项移除产生空快照、`status:"stopped", reason:"empty_batch"`，confirm_id/token 为 null，不创建可批准的空门。运行后的任务取消仍用既有入口，不能编辑运行队列。

### 三通道、门级 token 与最薄浏览器迁移

- HTTP、WS 与当前注册的真实确认回调进入同一结构化决策/原子消费校验；不捕 TypeError 后降级批准，也不从客户端参数重造更宽 Caller。批次 HTTP 成功回 `{ok:true,confirm_id,approved}`；WS 成功回 `{type:"confirm_result",ok:true,confirm_id,approved}`，错误沿现有 error 帧携带 status/code/message。内部回调传播同一结构化错误，不虚构 HTTP 响应。
- 浏览器 WS 不能自设项目头。严格人工 HTTP `GET /preview` 返回当前门随机 `decision_token`，绑定原 Store/project/actor/batch/confirm/snapshot/Origin；GET 只读取，不轮换、延寿或恢复能力。批次 WS 帧为 `{type:"confirm",confirm_id,approved,approved_item_ids?,snapshot_sha256?,decision_token}`，包括旧整批 bool 决策也必须带当前 token，握手还需真实同源 Origin/Referer。缺失/错误/他门/替换或消费后 token 为 `409 CONFIRM_STALE`；JSON 中伪造 X-Agent-Project 不能代替 HTTP 头。此 token 仅桥接当前人工门，不是 MCP 登录、独立 SID 撤销或 v1 token 鉴权。
- 实际浏览器对 **`batch-confirm-*`** 的整批批准/拒绝，无论 WS 是否 OPEN，都只 POST 同源 `/api/projects/{project}/agent-nodes/{node}/confirm`，带匹配的 `X-Agent-Project` 和旧 `{confirm_id,approved:boolean}`，不发送 WS 批次决定。收到匹配的服务器接受回执前保留当前卡、不声称已批准；真实失败可见且当前门可重试，不自动重放不确定副作用。迟到响应不得清掉已替换的门、下一 cautious 花钱门或另一宿主/视图的卡；服务器 confirm_resolved 仍可按真实决定撤旧卡。多个独立批次的未决门按 ID 保留，处理当前门后继续展示其余未决门；替换/接受过的旧门不能再次出现。
- 非批次 WS/HTTP fallback 合同保留。本次不实现 #412 的勾选子集、编辑或移除 UI；后端能力和门级 token 不等于完整 #412 交互验收。

### 生命周期、journal 与不保证的持久性

- 停止、实际 carrier/session 销毁、节点 mcp_session 解绑/重绑、宿主/generation/原用户轮或输入失效使旧门/token 无效；批准和每项准入仍检查原输入与服务商。正常 turn_end 不等于 carrier 销毁。**没有独立 SID 撤销协议或新入口**；已准入付费任务继续沿原归属结算，不因门失效删除真实结果；external MCP 不唤起另一宿主。
- 审批沿真实 Agent 事件 JSONL：待决、旧新 revision/hash、单项 edit/remove、replaced、批准集/approval_sha256、拒绝、停止/失效均进入既有 live、`/events`、`/events/page` 管线，actor/来源/原轮由服务端绑定。实际生成仍用 agent_ops/task admission；审批快照编辑没有画布差异，不伪造 no-op canvas operation，也不把 undo 说成退费。
- 本票批次审计为严格门：待决、修订、批准/拒绝等必要事件 `_persist_event` 失败以 `500 JOURNAL_WRITE_FAILED` 拒绝（仍在等待的 HTTP 调用返回该错误；后台错误不伪造第二次 HTTP 回包），不以 UI 广播或内存变化冒充写成，不放行未经审计的授权、扣款或任务。停止遇审计失败仍撤能力，但错误可见；不为补日志恢复旧许可。该严格规则不全局改写普通工具的旧 bool 日志合同。
- 同一修订的旧门 resolution 与新门 pending 合为一次 JSONL append；在捕获到写入/flush 的 OSError 或短写时尝试 truncate 回原文件长度，再报告失败。**这是 handled-write rollback，不是 fsync、掉电安全、跨进程事务或 ACID**；rollback 本身也可能失败，seq 可有空洞。JSONL、RAM grant、额度和外部任务不构成跨介质 exactly-once；先审计后消费的进程崩溃可能留下未执行审计，不能据此自动重试未知副作用。
- 批次/快照/门/token/grant 仅在本进程 RAM；读取/编辑不延寿，不新增 90 秒业务过期。重启、淘汰或真实 scope 失效后不从 JSONL 回放复活批准或执行，不承诺 RAM grant 重启续跑；真正持久任务仍按既有证据查询/结算。上述合同不代表真实模型、原生 SDK、付费服务商或完整 #412 UI 已验证。
