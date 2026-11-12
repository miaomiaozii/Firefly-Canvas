# 【Agent TUI】其他审计发现汇总：刷新丢会话历史、渲染自动拉起后端会话、附件无进度、fork 用原生弹窗、无障碍缺失

验证人：QA（代码审计，全部 file:line 已验证；无独立实拍的标注为「代码级」）。严重度 S7-S10。可按条目再拆。

## 发生了什么 / 复现 / 期望（逐条）

### 1. 刷新页面后 TUI 会话历史全丢（严重度：中，代码级）
- 现象：前端会话状态是纯内存（static/js/canvas-agent-tui.js:17），刷新即丢；后端 stream 连接只发 session 确认不回放历史（app/routers/api_agent_nodes.py:453-497），且无「拉历史消息」端点（该文件端点清单：/prompt /steer /follow_up /abort /confirm /fork-messages /fork /switch-branch /model /autonomy /status /chat-models /stream）。
- 复现：节点对话若干轮 → 刷新页面 → 对话区空白，但后端 pi 会话可能仍在运行 → 状态错配。
- 期望：刷新后回放历史，或明确提示「新会话」。

### 2. 画布每次渲染都可能自动拉起后端会话（严重度：中，代码级）
- 渲染 → 绑定控件 → 连 WebSocket（static/js/canvas-agent-tui.js:995）→ 后端对每个连接 get_or_create_session 且 not running 就 spawn（app/routers/api_agent_nodes.py:462-468）。
- 后果：只是打开画布浏览，服务器端 pi 进程可能被意外启动（资源/费用/语义问题）。
- 期望：仅在用户明确交互（发送/继续）时创建会话。

### 3. 附件处理无进度、图片无大小上限（严重度：中/低，代码级）
- 图片整体读 dataURL 无上限（static/js/canvas-agent-tui.js:347-355、:398-404）；视频/音频转换 fetch 无进度提示（:357-375），用户长时间无反馈后才出现缩略图或失败消息。

### 4. fork 选择器用原生 prompt()（严重度：低，代码级）
- static/js/canvas-agent-tui.js:560-580 两次 `prompt()` 弹窗，与画布自有 modal 体系不一致（体验突兀）。

### 5. 无障碍缺失（严重度：低，代码级）
- 消息区无 aria-live（流式输出读屏不可闻）；思考开关无 aria-pressed（且激活态样式已死，见⑤视觉票）；状态徽标无 role="status"（对照：agent-task 节点有，static/js/canvas-agent-tui.js:958）。

### 6. 从消息区无法起手平移画布（严重度：低，体验取舍）
- 消息区 mousedown 阻止冒泡防拖拽（static/js/canvas-agent-tui.js:1193-1196），副作用是从卡片内无法 pan，只能抓框边。

### 7. 死代码清理（非用户可见）
- static/js/smart-canvas.js:9279 `return;` 之后整段旧版渲染逻辑（至 ~:9338）永不可达。
