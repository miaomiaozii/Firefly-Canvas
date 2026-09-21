# Agent 节点 v2：Pi 终端（TUI）形态设计稿

> 状态：设计稿 v2（可行性已实测验证，待评审）
> 核心命题：一个 Agent 节点 = 一个嵌入画布的持久 Pi 会话（终端形态），用户持续自然语言对话，Pi 作为底座自主操作画布完成任务。
> v2 变更（2026-09-21）：agent 宿主从"自定义 Node sidecar（Pi SDK）/ 每轮 `pi -p --resume`"改为 **pi CLI `--mode rpc` 长驻子进程 + 仓库内 canvas-tools 扩展**。全部实测验证，见 §3.1 与 §10。

## 1. 设计目标

- **一个节点就是一个 TUI**：节点面板是终端式对话窗口，流式输出，持续多轮。
- **自然语言进，结果出**：用户不编排任务列表，只说目标；规划、工具调用、执行全部由 Agent 自主完成。
- **Pi 是底座**：Agent 推理循环、工具调用协议、会话持久化全部复用 Pi（@earendil-works/pi-coding-agent），不自研 agent loop。
- **画布自动化**：Agent 通过一组"画布工具"操作画布（建节点、连线、触发生成、聚焦视口），所有动作在画布上实时可见。
- **项目作用域**：所有会话与操作归属当前项目（ADR 0013），随项目保存/导出/回收。

## 2. 交互模型（示例会话）

```
┌─ Agent 节点 · 在线 · gpt-x ────────────────┐
│                                            │
│ > 给这个咖啡杯做一组产品三视图，参考这张图   │
│                                            │
│ ⚙ canvas.list_nodes()                      │
│ ⚙ media.upload(参考图.png) → #m03          │
│                                            │
│ 计划：① 建提示词节点（三视图指令）          │
│       ② 参考图连入                          │
│       ③ 触发生成                            │
│ 执行？                                     │
│                                            │
│ > 执行                                      │
│                                            │
│ ⚙ canvas.create_node(prompt) → #n15        │
│ ⚙ canvas.connect(#n15, #m03)               │
│ ⚙ generate.trigger(#n15) → task#t88        │
│ ⏳ 生成中…                                  │
│ ✅ 完成：三视图已生成（#n15），视口已定位    │
│                                            │
│ ┌──────────────────────────────────────┐   │
│ │ 输入…（Enter 发送 / Shift+Enter 换行）│   │
│ └──────────────────────────────────────┘   │
└────────────────────────────────────────────┘
```

关键交互原则：

1. **小任务直接执行**，大任务先给计划再执行——由 Agent 自主判断，不是 UI 强制流程。
2. **工具调用以活动行展示**（`⚙ tool → 结果`），可折叠，不喧宾夺主。
3. **画布实时可见**：Agent 建节点/连线/生成时，画布同步呈现（用户"看着"Agent 干活）。
4. **破坏性操作内联确认**：删除节点/删除媒体等，Agent 在对话中先问，用户确认后才执行（工具层强制，不依赖提示词自觉）。
5. **视口跟随**：Agent 完成工作后可聚焦视口到产物（`viewport.focus`），用户不用自己找。

## 3. 架构

```
┌─ 前端：Agent 节点 TUI 面板 ─────────────────┐
│  终端式渲染：流式 markdown + 工具活动行       │
└──────────────┬─────────────────────────────┘
               ↕ WebSocket（流式事件：text / tool / state / confirm）
┌─ 后端：FastAPI ─────────────────────────────┐
│  /api/projects/{pid}/agent-nodes/{nid}/…    │
│  Agent 宿主管理器：spawn/kill/respawn 子进程  │
│  事件转发、确认门、项目作用域                 │
│  工具回调端点（供扩展经 localhost HTTP 调用） │
└──────────────┬─────────────────────────────┘
               ↕ stdio JSONL（pi RPC 协议，长驻子进程）
┌─ pi CLI 子进程（每个 Agent 节点一个）────────┐
│  pi --mode rpc --no-extensions              │
│     -e <repo>/canvas-tools.ts               │
│     --no-builtin-tools --tools canvas.*,…   │
│     --session-dir <项目>/agent_nodes/<nid>/  │
│  canvas-tools.ts（仓库内扩展）：注册画布工具，│
│  每个 handler 经 localhost HTTP 回调 FastAPI │
└────────────────────────────────────────────┘
```

### 3.1 为什么是 pi CLI `--mode rpc` 子进程（2026-09-21 实测）

- Pi SDK 是 TypeScript/Node 生态，Python 后端无法进程内嵌入 → 必须外部 Node 进程（v1 结论不变）。
- **`pi --mode rpc` 原生提供完整 JSONL 协议**，无需自研 sidecar 包 Pi SDK：
  - stdin 命令：`prompt` / `steer` / `follow_up` / `abort` / `new_session` / `get_state` / `set_model` / `compact`（可选 `id` 字段做请求/响应关联）。
  - stdout 事件流：`message_update`（文本 delta）、`tool_execution_start/update/end`、`turn/agent` 生命周期、`auto_retry_start/end`、`agent_settled`。
- **steer 原生支持**（`steer` 命令：当前轮工具执行完后、下次 LLM 调用前投递）——v1 认为 CLI 形态要牺牲 steer，实测不成立。
- **无每轮启动延迟**：进程长驻，一个节点一个进程；FastAPI 宿主管理器负责 spawn/kill/respawn。
- **工具隔离（安全）**：`--no-builtin-tools --tools canvas.*,generate.*,media.*,viewport.*` → agent 只能用画布工具，碰不到用户机器的 bash/read/write。`--no-extensions -e canvas-tools.ts` → 隔离用户全局扩展（实测：不隔离时全局扩展的 `extension_ui_request` 事件会混入 RPC 流）。
- **扩展放仓库内**：canvas-tools.ts 放 Firefly-Canvas 仓库（实测模块解析通过，`pi-stable`/`typebox` 导入由 pi 加载器解析）；产品不附带 Node 运行时，pi CLI 是用户本机前提（与既有 codex/gemini CLI 适配器同前提）。
- 与仓库既有形态一致：CONTEXT.md 的"CLI 适配器"概念（api_codex.py、api_gemini_cli.py 先例），agent 宿主是该家族的 Pi 专用成员。
- **崩溃恢复**：宿主管理器检测进程死亡 → 带 `--continue`（或 `--session <id>`）respawn → Pi 按 JSONL 原生恢复会话（实测：新进程记得之前对话）。

### 3.2 会话与节点的关系

- 一个 Agent 节点 ↔ 一个 Pi 会话（1:1，持久绑定）。
- 会话文件：`<项目目录>/agent_nodes/<node_id>/sessions/<pi 生成文件名>.jsonl`（Pi 原生 JSONL；文件名由 pi 生成为 `timestamp_uuid.jsonl`，启动后 `get_state` 返回 `sessionId`/`sessionFile`，节点属性存 sessionId 引用）。
- 节点属性只存：sessionId 引用、模型、自治级别。会话本体在项目目录内 → 项目导出/回收/恢复天然覆盖（ADR 0013）。
- 多 Agent 节点并存：各自独立子进程 + 独立 session-dir + 独立上下文，互不干扰。
- 多轮：同一进程内连续 `prompt`；进程重启后 `--continue` 续接。

## 4. 画布工具集（Agent 的手）

按职责分组，全部项目作用域，返回紧凑 JSON：

| 组 | 工具 | 说明 |
|---|---|---|
| 查询 | `canvas.list_nodes` | 节点摘要（id/类型/名称/位置/连线/状态），供 Agent 感知画布 |
| 查询 | `canvas.get_node` / `canvas.get_selection` | 单节点详情 / 当前选中 |
| 结构 | `canvas.create_node` | 建节点（prompt/image/video/conversation…） |
| 结构 | `canvas.update_node` / `canvas.delete_node` | 修改 / 删除（删除走确认门） |
| 结构 | `canvas.connect` / `canvas.disconnect` | 连线 / 断线 |
| 生成 | `generate.trigger` | 在节点上触发生成 → 返回 task id |
| 生成 | `generate.status` / `generate.wait` | 查询 / 阻塞等待（带超时，超时返回进行中状态而非失败） |
| 媒体 | `media.upload` / `media.list` | 上传资产（如用户给的参考图）/ 列项目媒体 |
| 视口 | `viewport.focus` | 聚焦到指定节点（让结果"指给用户看"） |

工具设计原则：

- **走既有管线**：`generate.trigger` 复用现有生成管线（任务状态卡照常出现），Agent 不另起炉灶。
- **异步生成 = trigger + wait**：wait 超时不视为失败，Agent 可稍后再查（避免长阻塞）。
- **确认门在工具层（硬门，实现细节）**：破坏性工具的 handler（canvas-tools.ts 内）先调 FastAPI 确认端点 → 端点经 WebSocket 向前端发 confirm 事件 → handler 阻塞等待用户批准（带超时）→ 批准才执行。提示词只能"建议"，门是硬的。
- **上下文注入**：会话开始 + Agent 主动调用 `canvas.list_nodes` 获取画布快照；不每轮全量注入（省 token）。

## 5. 系统提示词（Agent 的脑）

要点（完整文案实施时定稿，经 `--system-prompt` 传入）：

- 角色：本项目的画布自动化 Agent；你是执行者，不是编排器——不要让用户填表/选任务。
- 风格：简洁、中文、边做边报（每个动作一行活动行 + 必要的解释）。
- 判断：小任务直接做；多步任务先给 3 行内计划，等用户确认或用户已明确"直接做"。
- 安全：破坏性操作必须先问；不确定用户意图时问，不要猜。
- 感知：动手前先 `canvas.list_nodes` 看现状，避免重复建节点。

## 6. 前端 TUI 规格

- **节点卡片（画布上）**：名称 + 状态灯（空闲/运行中/等待确认）+ 最后一行预览。
- **展开面板（终端形态）**：
  - 头部：状态、模型名、会话时长。
  - 主体（滚动区）：用户消息（左/无前缀）、Agent 文本（markdown 流式）、工具活动行（`⚙/✅/⏳/❌` 前缀，可折叠详情）、思考（默认隐藏，可开关）。
  - 底部：终端式输入行（等宽字体、`>` 前缀）。运行中可继续输入：排队（`follow_up`）或 steer 打断改向（`steer` RPC 命令，原生支持，当前轮工具执行完后投递）。
- **等待确认态**：内联确认按钮（批准/取消），对应后端 confirm 门。
- **参考图附件**：`prompt` 命令支持 `images`（base64）→ TUI 输入行可附参考图（实测协议支持）。
- **视觉**：深色终端底、等宽字体、细边框——与画布其他节点的表单形态明显区分（"这是个终端"一眼可辨）。

## 7. 与现有概念的关系

| 现有概念 | 处置 |
|---|---|
| 旧 Agent 节点（先计划后执行：任务列表/生成计划/确认并执行） | **整体移除**，由 TUI 形态取代；"计划"变成对话中的自然步骤 |
| 对话节点（ADR 0002） | 保留不动：纯消息存储，不承担动作。Agent 节点 = 对话 + 动作，二者并存（是否合并另议，见开放问题） |
| 任务状态卡 | 不变：Agent 触发的生成照常产生任务状态卡 |
| 生成历史（ADR 0013） | 不变：Agent 触发的生成照常进项目生成历史 |
| CLI 适配器（codex/gemini-cli 桥） | 并存：agent 宿主是 Pi 专用 CLI 桥（`pi --mode rpc`），同家族不替代通用 CLI 桥 |

## 8. 里程碑建议

- **M1 骨架**：Agent 宿主管理器（spawn `pi --mode rpc`、事件转发、TUI 流式渲染）+ 查询/结构工具（list/create/connect/update）。验收：对话让 Agent 建出"提示词节点+连线"的最小画布。
- **M2 生成**：generate.trigger/wait + media.upload + viewport.focus + steer/follow_up 接线。验收：对话让 Agent 完成"参考图→三视图生成→视口定位"全链路。
- **M3 生产化**：会话持久化/重启恢复（`--continue` respawn）、确认门、多节点隔离、错误恢复（崩溃 respawn）。
- **M4 增强（可选）**：自治级别（每节点可调：直接执行/计划先确认）、PiX 式分支（从历史某轮分叉重做，RPC 协议有 `fork` 命令）、并行 Agent。

## 9. 已决断

1. **对话节点保留**：Agent 节点 = 对话+动作，对话节点（ADR 0002）= 纯消息存储，定位不同。本期不动，避免扩面。
2. **自治级别 = 直接执行 + 破坏性确认**：确认门做在工具层（硬门），不靠提示词。不恢复"计划先确认"的强制流程。
3. **部署 = pi CLI `--mode rpc` 长驻子进程**（2026-09-21，取代 v1 的"每轮 `pi -p --resume`"）：实测 RPC 模式原生提供 steer/follow_up/abort + 会话持久化 + 自定义工具扩展，且无每轮启动延迟；产品仍不附带 Node 运行时（pi CLI 是用户本机前提，与 codex/gemini 适配器同前提）。canvas-tools.ts 扩展放仓库内，`--no-extensions --no-builtin-tools` 双重隔离。FastAPI 与 agent 宿主之间就是 pi 原生 JSONL 协议——v1 预留的"JSON-RPC 接口"由 pi 直接提供，未来换宿主实现（如进程内 SDK）只换 spawn 方式不换协议。
4. **默认模型 = 跟随全局默认**（pi 全局 settings.json 的 defaultProvider/defaultModel），节点级覆盖放 M3+（`--provider/--model` 或运行中 `set_model`）。

## 10. 可行性验证（2026-09-21 实测）

测试件（可复跑）：`tmp/pi_rpc_smoke.py`（FastAPI 式 spawn + JSONL 双向）+ `tmp/test-rpc-ext.ts`（示例扩展，注册 `test_echo` 工具）。

PASS 项：

- `get_state` 返回 sessionId/sessionFile/model/messageCount。
- 模型正确调用自定义扩展工具（`test_echo`，args `{"text":"hello-world"}`，结果回流，`tool_execution_start/end` 事件齐全）。
- 流式文本 delta（`message_update`）+ 完整 turn/agent 生命周期事件。
- 多轮上下文：第二轮 prompt 引用第一轮结果正确。
- 会话 JSONL 落盘 `--session-dir`；**新进程 `--continue` 恢复同一会话，模型记得之前对话**（实测回答出 "hello-world"）。
- 扩展从 Firefly-Canvas 仓库目录加载（模块解析通过）。
- 内置自动重试（`auto_retry_start/end` 事件，模型 503 时自动退避重试）。

注意事项：

- 不隔离用户全局扩展时，`extension_ui_request` 事件会混入 RPC 流 → 必须 `--no-extensions`。
- RPC framing 是严格 JSONL，LF 为唯一分隔符（文档明示 Node `readline` 不合规——它会按 U+2028/U+2029 切分；Python 端按行读即可）。
- `prompt` 支持 `images`/`videos`（base64）→ TUI 附件可行。
- 本机模型可用性实测：`vllm-remote/qwen3.8-27b-w8a16-mtp2` 可用；`llama-cpp-local`（默认）服务器未启动时自动重试后空回复；`codex-pool` 无账号时 503。宿主管理器应把 `auto_retry_end success=false` 映射为 TUI 错误态。
