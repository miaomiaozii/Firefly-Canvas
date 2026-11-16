---
status: accepted
---

# Agent 节点可停靠：同一实体、三种呈现

Agent 节点除浮动在画布上外，可停靠到画布左/右边缘（贴边、全高、不随画布缩放/平移）。停靠是**呈现位置**，不是新实体：会话、消息、草稿、工具状态仍全部属于该节点（见 [ADR 0012](0012-agent-is-a-separate-node.md)）。

## 决定

- 数据模型：`node.dock ∈ {'left','right'}`（无该字段=浮动）、`node.dockCollapsed ∈ {true,false}`（窄条收起态）。两个字段随画布按项目持久化（与 `node.w` 同路，`canvasForStorage` 无字段白名单）。
- DOM：停靠态的节点 DOM 挂在 `#shell` 下的停靠槽 `#agentDock`（与 `#world` 平级），不进 `#world`，因此不随 `#world` 的 translate+scale 变换。浮动↔停靠互切**不动视口、不改 `node.x/y`**：浮动态坐标原样保留，停靠→浮动原处落回。
- 三种形态同一实体：消息/流式更新走 TUI 的 `renderAgentNode` 就地路径（按 `[data-id]` 全文档查找，元素在停靠槽里同样工作）；全量 `render()` 跳过停靠中的节点，不触碰停靠槽里的现成元素——因此输入/IME 组合永不被全量重建打断（与 #314 草稿/IME 保护同一原因）；只有显式切换形态才搬 DOM，搬前抓焦点/选区、搬完恢复。IME 组合进行中的切换请求推迟到 `compositionend` 后执行（`pendingDock`，与 `pendingRerender` 同机制）。
- 交互：节点头按钮「停左 / 停右 / 收起 / 展开 / 取回」；停靠态拖节点头拖出画布落回浮动（落点=世界坐标）。不做浮动→停靠的边缘吸附拖拽（误触风险，spec 只要求拖出）。
- 停靠宽度复用 `node.w`（与画布内布局同公式：有效 `node.w` 否则 382，下限 330）；收起态固定 44px 窄条。
- 窄视口（<900px，或 面板宽+480px > 视口宽）自动收起为窄条；**只自动收起、不自动展开**，展开由用户手动；收起态持久化，刷新后按当前视口重新判定一次。
- 层级：停靠面板 `z-index:85`，与面板类浮层（提示词预设面板等）同档，低于历史抽屉（86）——符合 spec「排在面板类浮层和历史抽屉之间」。85 与 86 之间无整数可用，取 85 的依据是停靠面板与历史抽屉在**水平方向零重叠**（抽屉 `left/right` 加 `#shell` 上的 `--agent-dock-left/--agent-dock-right` 变量让开停靠面板，窄屏 media 查询同样处理），同档的 preset-panel 只出现在提示词节点附近。小地图、左上工具栏同样让开。
- 已知代价：停靠节点若有画布连线，连线端点仍画在 `node.x/y` 世界坐标（空位），不重路由到面板边缘。

## 范围

Refs #407（agent 节点可停靠 D1）。实现：`static/js/canvas-agent-tui.js`（停靠控制器 `syncAgentDock`/`agentDockSet`/拖出）、`static/js/smart-canvas.js`（`render()` 滤除停靠节点 + 尾部调 `syncAgentDock`）、`static/smart-canvas.html`（`#agentDock` 槽）、`static/css/smart-canvas.css`（停靠样式与边距变量）。
