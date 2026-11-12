## 侦察补充（规范出处与实现要点）

- **唯一成文规格**：`docs/design/agent-node-pi-tui.md` §6——节点卡片=名称+状态灯+最后一行预览；视觉定位「深色终端底、等宽字体、细边框——与画布其他节点的表单形态明显区分」。ADR 0012：Agent 必须是独立节点。
- **决策点（先定再画）**：项目存在两套并存视觉语言——`static/css/studio-foundation.css` design tokens（--accent #5879dc、圆角 12/8/6/999、间距 4-24、字号阶 10-20、Inter+雅黑）vs Agent TUI 自成的 Tokyo Night 硬编码色板（smart-canvas.css:3704+：底 #1a1b26、主 #7aa2f7、绿 #9ece6a、等宽 JetBrains Mono）。重设计需明确：保留 TUI 终端感（有规格背书）但吸收 tokens 的间距/圆角/命中尺寸体系，还是全面迁移。**建议前者**：终端感是 Agent 卡的身份，丑的根源是排版纪律而非色板。
- **截图缺陷定位（已核对源码）**：
  - 徽章竖排：`.agent-tui-status` 缺 `white-space:nowrap`（收起态 `.agent-card-status` 有）→一行修复；header 单行 flex 无 wrap 且无 `min-width:0`，title+徽章+2-3 下拉+🌿 窄卡互挤。
  - 占位截断：placeholder 含完整快捷键说明（「输入指令... (Enter 发送 / Shift+Enter 换行，运行中 Enter 排队)」），rows=1 textarea 必截断——快捷键说明应移入悬停提示/帮助面，placeholder 留短版。
  - 右下角小方块**不是折叠钮，是节点缩放手柄**（`.node-resize-handle`）——Agent 卡片根本没有展开/收起按钮（对话节点才有 data-conversation-collapse）。重设计应补折叠态（收起=标题+状态灯+最后预览，对齐 §6 规格）。
  - 图标按钮 28×28 有 title 无 aria-label；状态共 10 态（含 #280 三态 failed/reconnecting/reconnect_stopped）但 CSS 仅 5 个徽章 class，crashed 无独立样式。
