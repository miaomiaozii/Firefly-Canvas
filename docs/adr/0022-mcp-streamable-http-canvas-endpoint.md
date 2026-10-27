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
  变体。因外部会话具现为 Agent 节点，「向谁要确认」永远是画布上确定的 tab。
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
