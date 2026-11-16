---
status: accepted
---

# Agent 节点可停靠：同一实体、三种呈现

Agent 节点除浮动在画布上外，可停靠到画布左/右边缘（贴边、全高、不随画布缩放/平移）。停靠是**呈现位置**，不是新实体：会话、消息、草稿、工具状态仍全部属于该节点（见 [ADR 0012](0012-agent-is-a-separate-node.md)）。

## 决定

- 数据模型：`node.dock ∈ {'left','right'}`（无该字段或 null=浮动）与 `node.w` 随画布按项目持久化；收起态和手动展开标记分别只在页面内存 `agentDockCollapsed` / `agentDockUserExpanded` Set 中，不写节点、不触发保存。旧 `node.dockCollapsed` 在节点规范化时删除，`canvasForStorage` 再防御剥离。唯独加载差异基线使用 `preserveLegacyDockCollapsed:true` 在独立克隆中保留磁盘旧值，使未改动节点也会在下一次增量保存产生删除旧字段的 upsert；正常保存没有该选项。
- DOM：停靠态的节点 DOM 挂在 `#shell` 下的停靠槽 `#agentDock`（与 `#world` 平级），不进 `#world`，因此不随 `#world` 的 translate+scale 变换。按钮切换或被另一个 Agent 替换时**不动视口、不改 `node.x/y`**：浮动态坐标原样保留，取回原处落回。拖出则按落点换算世界坐标（`screenToWorld` 后 x/y 各减 20 并取整），不是原坐标取回。
- 三种形态同一实体：消息/流式更新走 TUI 的 `renderAgentNode` 就地路径（按 `[data-id]` 全文档查找，元素在停靠槽里同样工作）；全量 `render()` 跳过停靠中的节点，不触碰停靠槽里的现成元素——因此输入/IME 组合永不被全量重建打断（与 #314 草稿/IME 保护同一原因）；只有显式切换形态才搬 DOM，搬前抓焦点/选区、搬完恢复。目标或被替换者正在 IME 组合时，整次切换请求 `{nodeId,dockSide}` 挂在阻塞者的 `pendingDock`，等其 `compositionend` 后执行，不提前取回另一节点。
- 交互：节点头按钮「停左 / 停右 / 收起 / 展开 / 取回」；停靠态拖节点头拖出画布落回浮动（落点=世界坐标）。不做浮动→停靠的边缘吸附拖拽（误触风险，spec 只要求拖出）。
- 停靠宽度复用 `node.w`（与画布内布局同公式：有效 `node.w` 否则 382，下限 330）；收起态固定 44px 窄条。
- 窄视口（<900px，或 面板宽+480px > 视口宽）自动收起为窄条；**只自动收起、不自动展开**，展开由用户手动。手动展开抑制本次页面后续自动收起；刷新后两种内存标记均消失，只按当前视口重判。窄条边距按 12px 间距 + 44px 宽度让位，不沿用展开宽度。
- 唯一停靠：停靠第二个 Agent 时，先取回其它 Agent，再停靠目标（同侧、异侧均相同），原坐标及会话/草稿不动。旧数据同时有多个有效 dock 时，按 `canvasState.nodes` 数组顺序保留第一个 Agent 的有效 dock，其余置 null；非 Agent 的 dock 也清除。在 `render()` 的 world 过滤前规范化，`syncAgentDock()` 再兜底；兼容清理随下一次内容保存落盘，不因收起或同步触发保存。
- 验证视口偏离：批准的第四档截图是 900px；旧交付改用 880px，是对批准验证方案的偏离，不是代码修复。900px 对默认宽度 382px 不触发自动收起，880px 会触发；代码阈值仍为 <900px。
- 层级：停靠面板 `z-index:85`，与面板类浮层（提示词预设面板等）同档，低于历史抽屉（86）——符合 spec「排在面板类浮层和历史抽屉之间」。85 与 86 之间无整数可用，取 85 的依据是停靠面板与历史抽屉在**水平方向让位**（抽屉 `left/right` 加 `#shell` 上的 `--agent-dock-left/--agent-dock-right` 变量让开停靠面板，包含后置 Foundation history rail 规则和窄屏 media 查询；零重叠由交付截图及双方 DOMRect 验证），同档的 preset-panel 只出现在提示词节点附近。小地图、左上工具栏同样让开。
- 已知代价：停靠节点若有画布连线，连线端点仍画在 `node.x/y` 世界坐标（空位），不重路由到面板边缘。

## 范围

Refs #407（agent 节点可停靠 D1）。实现：`static/js/canvas-agent-tui.js`（停靠控制器 `syncAgentDock`/`agentDockSet`/拖出）、`static/js/smart-canvas.js`（`render()` 滤除停靠节点 + 尾部调 `syncAgentDock`）、`static/smart-canvas.html`（`#agentDock` 槽）、`static/css/smart-canvas.css`（停靠样式与边距变量）。
