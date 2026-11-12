# Agent TUI 根因调查报告（findings）

- 日期：2026-11-14（会话 C 接续调查）
- 方法：代码直读（行号均实际核读）+ 采用 `.qa/agent-tui-workstate.md` 中已实测事实（节点 `agent_5u7u41u6ee7o`，框 806x650 CSS，.node-body 780px，.agent-tui-card 451px）。截图佐证：`.qa/shots/03_agent_node_gap_full.png`（本会话已查看：卡片铺满全高、仅占左侧约 58% 宽，右侧为框的 `--panel` 底色；顶部条是卡片自己的 `.agent-tui-header`，并非 `.node-head`——后者在 agent 节点上被 CSS 隐藏，见 D 节）。
- 范围文件：`static/js/canvas-agent-tui.js`（1341 行）、`static/css/smart-canvas.css`（4342 行）、`static/js/smart-canvas.js`（16357 行）、`static/js/canvas-node-ops.js`、`app/routers/api_agent_nodes.py`。
- 既有测试资产（修复时须跑）：`tests/test_agent_tui.py`、`tests/agent_tui_polish_browser.py`、`tests/canvas_module_units/test-canvas-agent-tui.mjs`。

---

## A) Bug1：卡片宽度不跟随节点框（框大卡小、右侧空白）

**已确认：`.agent-tui-card` 没有任何 width 规则，这是根因。**

证据链（每一环都直读核实）：

1. 节点框尺寸 = 持久化的 `node.w/h`。
   - 创建默认 `w:382, h:360`：`static/js/canvas-node-ops.js:91-104`（`createAgentNode`）。
   - 布局取 `node.w/h`，默认 382x360，注释明言「尺寸由节点自身 w/h 决定（可拖拽拉伸）」：`static/js/smart-canvas.js:1615-1629`（`imageLayout` agent 分支）。
   - resize 手柄写 `node.w/h`：mousedown 起手势 `smart-canvas.js:11011-11037`；window mousemove 通用分支写 `node.w = Math.max(minW, ...)` / `node.h`：`smart-canvas.js:14764-14765`（agent 最小 330x270：`smart-canvas.js:14705-14707`）；mouseup 提交并 `render()`：`smart-canvas.js:14938-14945`。用户实测节点 806x650 即持久化后的 `node.w/h`（画布随 `scheduleSave` 保存整个 canvas JSON：`smart-canvas.js:6734-6742`）。
2. 渲染时框尺寸写进 `.image-node` 内联 style：`width:${layout.width}px;height:${layout.height}px`：`smart-canvas.js:9226`。拖拽中只改 `el.style.width/height`，且只特判 loading 占位，不碰 TUI 卡片：`static/js/canvas-node-ops.js:173-190`（`updateNodeElementDuringResize`）。
3. CSS 链条：
   - `.image-node.agent-smart-node { padding:12px; ... }`：`static/css/smart-canvas.css:195`
   - `.image-node.agent-smart-node .node-body { width:100%; height:100%; ... align-items:stretch; }`：`smart-canvas.css:198`（`.node-body` 基类 `display:flex`：`smart-canvas.css:486`）
   - `.agent-tui-card { display:flex; flex-direction:column; height:100%; ... }`——**只有 height:100%，无 width / flex 主轴规则**：`smart-canvas.css:3708-3721`
   - 结果：`.node-body` 是 flex row，`align-items:stretch` 只撑交叉轴（高→铺满），主轴（宽）取 max-content → 实测 451px（由 header 行两个 130px 上限的 select + 按钮行内容决定），右侧 780-451≈329px 空白。与截图完全一致。
4. 全代码没有任何「按 node.w/h 重设卡片内容尺寸」的逻辑：卡片是纯 CSS 容器，唯一消费 `node.w/h` 的地方是 `imageLayout`（框尺寸）。即：**框可以被拖大，卡片内容永远不会跟着变宽**。
5. 对照组：兄弟卡片都有 `width:100%; height:100%`——`.prompt-node-card`（`smart-canvas.css:170`）、`.conversation-node-card`（`smart-canvas.css:171`）、`.agent-task-card`（`smart-canvas.css:247` 附近，`width:100%; height:100%`）。agent 卡片缺 width 是孤例。
6. 死样式层 `.agent-node-card` / `.agent-node-heading` 等（`smart-canvas.css:199-244`）：JS 零引用（grep `agent-node-card|agent-node-heading|agent-node-goal|agent-node-controls|agent-node-draft` 无匹配），是第一代卡片遗留，**与本次宽度 bug 无因果**，属清理项（见 D 节）。

**最小修复候选：**
- A1（推荐，一行）：给 `.agent-tui-card`（`smart-canvas.css:3708`）加 `width:100%`——与 `prompt/conversation/task` 卡片同一模式。
- A2：`flex:1 1 auto; min-width:0`（单一 flex 子项下等效；`min-width:0` 防内容顶宽）。
- 不要动 `align-items`/`align-self`：那是交叉轴，高度已铺满，问题在主轴。
- 修复后 `.agent-tui-input`（`flex:1; min-width:0`，`smart-canvas.css:4318`）会自动撑满输入行，无需额外改动。

---

## B) Bug3：草稿被吞 /「文字要取消选择才显示」

**已确认：没有任何草稿持久化；任何一次 body 重建（选择、取消选择、每个 WS 事件）都会 innerHTML 全量重造 textarea，草稿+焦点（+IME 合成会话）一并丢失。**

选择/取消选择链路（逐行核实）：

- **点节点选中**：`.image-node` 的 `el.onclick`：`smart-canvas.js:10715-10731`。
  - 已选中 → `alreadySelected` → 只 `syncSelectionUi()` + `updateComposer()` 后 return（`smart-canvas.js:10722-10727`）。`syncSelectionUi` 仅 classList 切换、无 DOM 重建（`smart-canvas.js:1012-1039`）→ **草稿保留**（与 workstate 实测「点标题保持 value」一致）。
  - 未选中 → 设 `selectedId` 后调 `render()`（`smart-canvas.js:10728-10730`）→ 全量重建（见下）→ 草稿丢。
- **点空白取消选择**：`shell.onclick`：`smart-canvas.js:14430-14437` → `clearSelection()`（`smart-canvas.js:998-1003`；其中 `savePromptDraftForCurrent()` 只保存全局 composer 的 contenteditable，与 TUI 输入框无关）→ `render()`。
- **`render()` 全量重建**：所有节点经 template 重新生成（`smart-canvas.js:9190-9277`），`.node-body` 内是新的 `nodeBodyHtml`（agent → `agentNodeBodyHtml`：`smart-canvas.js:8841`；`static/js/canvas-agent-tui.js:770`）→ **新的空 textarea**（`canvas-agent-tui.js:923`）。
- **`renderAgentNode()` 局部重建**：`canvas-agent-tui.js:1199-1233`——`bodyContainer.innerHTML = body`（`:1218`）→ `bindAgentNodeControls`（`:1219`）→ 滚动（`:1222-1225`）→ 更新 `.node-head-meta`（`:1229-1231`）。
  调用点共 19 处（grep 核实）：`:72, :81, :108`（连接/重连/模型池异步拉取后）、`:338`（**任意 WS 事件**：`handleAgentEvent` 尾部 `:337-338`；其中 `session` ack `:188-200`、流式 `text_delta/thinking_delta` `:241-263`——agent 运行中每 ~100ms 一次）、`:381`（发送 prompt）、`:444`（附件）、`:460-474`（steer/follow_up/abort 等命令）、`:516/:530`（model/autonomy 变更）、`:552/:576`（fork/切分支）、`:639`（confirm）、`:1005`（重试连接）、`:1019`（工具展开）、`:1056`（thinking 开关）、`:1187`（移除待发附件）。

草稿持久化缺失（核实）：

- 每节点状态 `agentTuiState`（Map，`canvas-agent-tui.js:17`；`getAgentTuiState` `:19-39`）字段为 socket/messages/status/model/autonomy/toolActivities/thinkingVisible/pendingImages/reconnect 系列——**没有 draft/inputDraft 字段**。
- 全 JS 无任何把 `inputEl.value` 读回/恢复的代码（`data-agent-tui-input` 仅 4 处：渲染 `:923`、绑定 `:1025`、steer 读取 `:1067`、followup 读取 `:1082`）。
- `savePromptDraftForCurrent`（`smart-canvas.js:11642-11651`）只处理全局 composer 的 `promptInput`（contenteditable），不覆盖 `.agent-tui-input`。
- 连带伤害：innerHTML 重建会**替换掉聚焦中的 textarea 元素 → 焦点丢失**；运行中流式事件高频触发时用户实际上无法在输入框连续打字（而 placeholder 明确引导「运行中 Enter 排队为后续指令」——`canvas-agent-tui.js:923`，即官方预期的用法与现状直接冲突）；IME 合成会话同样被 DOM 销毁（Enter 有 `isComposing && keyCode!==229` 守卫防误发：`canvas-agent-tui.js:1029-1030`，#252；但对「重建打断合成」没有任何防护——对比 `render()` 对 composer 有 `promptHadFocus` 防护：`smart-canvas.js:9181-9183` 与 `:9280-9282`，`renderAgentNode` 无对应逻辑）。

**「文字要取消节点选择才会显示」的解释**（基于已验证路径的最佳推断，标注为推断）：
选中态下，`renderAgentNode` 会被 WS 事件（流式 delta、`session` ack `:188-200`）、首次模型池异步拉取（`:78-83`）、以及任何按钮/选择器操作反复触发，进行中的输入被反复重置；而用户文字唯一「稳定可见」的形态，是从 `state.messages` 重新渲染出的消息历史——这正是一次全量 `render()` 的产物，而**取消选择恰好是触发全量 render 的动作**（`smart-canvas.js:14430-14437`）。因此用户感知为「文字要取消选择才（稳定地）显示」。workstate 实测确认了取消选择即 wipe、重复选中保留 value 两个方向，与此模型一致。

**修复方向（草稿应持久化在哪）：**
- 在 `agentTuiState`（`canvas-agent-tui.js:19-39`）加 `inputDraft`（或独立 `Map<nodeId, draft>`）；
- 在 `bindAgentNodeControls` 里给 `inputEl` 加 `input`/`blur` 监听写 draft（`canvas-agent-tui.js:1025-1035`）；
- 绑定末尾（该函数同时被 `render()` 与 `renderAgentNode()` 调用：`smart-canvas.js:10700`、`canvas-agent-tui.js:1219`）恢复 `inputEl.value`，并在「重建前该 textarea 正聚焦且未处于 IME 合成中」时恢复焦点与光标位置；
- 更稳的变体：`renderAgentNode` 在节点输入框聚焦时跳过整卡重建，只对 `.agent-tui-messages` 做差异更新。

---

## C) Bug2：输入框不随文字变大（无 auto-grow）

**已确认：全文件不存在任何 auto-grow 代码。**

- `inputEl` 上只有两个监听器：`mousedown` stopPropagation（`canvas-agent-tui.js:1026`）、`keydown`（stopPropagation + Enter 发送，`:1027-1035`）。**没有 `input` 事件监听、没有 `scrollHeight` 测量、没有写 `style.height` 的任何代码**（全文件唯一的 scrollTop 在 `:1224`）。
- 两条互相覆盖的 `.agent-tui-input` CSS 规则（均核实）：
  1. `smart-canvas.css:4095-4104`：`flex:1; background:transparent; border:none; outline:none; padding:2px 0;`（#301 拆分输入区时写的）；
  2. `smart-canvas.css:4317-4330`：`flex:1; min-width:0; background:#1a1b26; border:1px solid #3b4261; border-radius:6px; padding:6px 10px; resize:none(:4327); overflow-y:auto(:4328); max-height:80px(:4329); line-height:1.4;`（级联在后 → **实际生效**，与实测 29px 高、禁 resize、上限 80px 一致）。
  - `::placeholder` 规则也重复了两遍：`smart-canvas.css:4106-4108` 与 `:4336-4338`。
- `max-height:80px` 语义：多行草稿的可见上限 ≈4 行（12px 字 + line-height 1.4），超出后在 80px 框内内部滚动；叠加无 auto-grow → 初始 1 行（`rows="1"`，`canvas-agent-tui.js:923`）+ 固定 ~29px 可见窗，用户打字超过一行后文字被内部滚动藏住，「输入框太小、不随文字变大」成立。

**修复方向：** 删除前一条规则（`css:4095-4108` 段）与重复 placeholder；保留 `css:4317-4330` 并加 auto-grow（`input` 事件中 `style.height='auto'; style.height=Math.min(scrollHeight, cap)+'px'`），同时重定义 `max-height`（删除或提高到与内容上限一致），避免「grow 到 80px 卡死」。

---

## D) Bug4：图标/视觉语言 + 重复/死亡样式层

**原始 emoji/字形清单**（全部在 `static/js/canvas-agent-tui.js`，均非 lucide 图标，逐行核实）：

| 位置 | 字形 | 用途 |
|---|---|---|
| `:927` | 📎 | 附件按钮 |
| `:928` | 💭 | 思考开关按钮 |
| `:929` | ↪ | steer 按钮 |
| `:930` | ⏳ | follow-up 按钮 |
| `:931` | ⏹ | abort 按钮 |
| `:877` | 🌿 | fork 按钮（header 设置簇） |
| `:783` | 💭 | thinking 区块标签 |
| `:793` | `>` | 用户消息角色符 |
| `:794` | 📎 | 消息附件名行 |
| `:807-810` | ⚙ / ✅ / ❌ / ⏳ | 工具活动状态图标 |
| `:818` | ▾ / ▸ | 工具行展开/折叠 |
| `:834` | ⚠️ | confirm 卡片前缀 |
| `:922` | `>` | 输入行 prompt 符 |
| `:235, :329, :374, :377, :482, :491, :501` | ⚠️ | 系统警告文案前缀（模型错误/启动失败/未送达/steer/follow_up/停止） |
| `:481` | ↪ | 「steer:」系统消息 |
| `:490` | ⏳ | 「follow_up queued:」系统消息 |
| `:500` | ⏹ | 「aborted」系统消息 |
| `:637` | ✅ / ❌ | confirm 结果「已批准/已取消」 |

画布其余 UI 的图标语言是 **lucide SVG**（`data-lucide` + `refreshIcons()`：`smart-canvas.js:416`；如节点删除按钮 `<i data-lucide="trash-2">`：`smart-canvas.js:9219/9228`）。TUI 的裸 emoji 依赖平台字体渲染、尺寸/色彩不可控（28x28 按钮内 13px 字形，`smart-canvas.css:4116-4130`），与画布风格割裂——用户抱怨成立。header 的模型/自治/分支 select（`canvas-agent-tui.js:856-876`）是原生 `<select>` 深底样式（`smart-canvas.css:3841-3868`），也偏「散落的调试控件」。

**重复/死亡样式层（核实）：**
1. **`.agent-node-card` 整块死样式**：`smart-canvas.css:199-244`（`.agent-node-card/.agent-node-heading/.agent-node-subtitle/.agent-node-goal/.agent-node-controls/.agent-node-drafts/.agent-node-draft*/.agent-node-plan/.agent-plan-*/.agent-node-warning`）——JS 零引用（grep 确认），第一代卡片遗留；与活层的 `.agent-tui-card`（`css:3708+`）两代并存。
2. **`.agent-tui-input` 双规则**：`css:4095-4104`（死）vs `css:4317-4330`（生效）；`::placeholder` 双份（`css:4106` / `css:4336`）。
3. **`.agent-tui-tool` 双规则**：`css:3936-3944` 与 `css:4235-4238`；`.agent-tui-tool-args` 双规则：`css:3956-3961`（`white-space:nowrap; overflow:hidden; text-overflow:ellipsis`——与详情 `<pre>` 内容冲突）与 `css:4268-4270`。
4. **thinking 激活态样式失效**：选择器 `.agent-tui-btn-thinking.agent-tui-btn-active`（`smart-canvas.css:4141-4145`）是 **class 选择器**，但 `canvas-agent-tui.js:928` 输出的是**裸 HTML 属性** `agent-tui-btn-active`（`${state.thinkingVisible ? 'agent-tui-btn-active' : ''}`，无 `class=`）→ 选择器永远不匹配：思考开/关在按钮上**没有任何视觉变化**（且裸属性污染 DOM）。
5. **`.node-head-meta` 状态预览是死功能**：`agentCardStatusHtml`（`canvas-agent-tui.js:711-745`）被 `render()` 模板（`smart-canvas.js:9226`）与 `renderAgentNode`（`canvas-agent-tui.js:1229-1231`）渲染，但 `.node-head`/`.node-title` 在 `:not(.empty-node)` 节点上 `display:none`（`smart-canvas.css:451-452`），而 agent 节点永远不是 `.empty-node`（`isEmpty` 仅对 image 节点为真：`smart-canvas.js:9213`）→ 「节点头状态灯 + 末行预览」对 agent 节点**永远不可见**（截图顶部那条其实是卡片自己的 `.agent-tui-header`）。

---

## E) 其他用户可见缺陷（审计发现）

1. **消息区自动滚动失效 + 每次重建滚动归零**（严重度：高）。真正可滚动的容器是 `.agent-tui-body`（`smart-canvas.css:3871-3876`，`overflow-y:auto`），但自动滚动写到了 `[data-agent-tui-messages]` 上（`canvas-agent-tui.js:1222-1225`），而 `.agent-tui-messages`（`smart-canvas.css:3878-3881`）**没有任何 overflow 规则、不可滚动** → `scrollTop=scrollHeight` 是空操作；同时 `.agent-tui-body` 每次 `renderAgentNode` 都被 innerHTML 整体替换（`canvas-agent-tui.js:1218`）→ 滚动位置每次事件归零。流式输出时每 ~100ms 一次 delta → 用户永远跟不上最新输出。
2. **轮子滚消息区 = 缩放画布**（严重度：高）。全局 wheel 先查 `canvasWheelIsReserved(e.target)`（`smart-canvas.js:15128-15140`），白名单含 `textarea/input` 等但**不含 `.agent-tui-body`**（`smart-canvas.js:15098-15126`）→ 在消息区滚轮会 `preventDefault` 并缩放视口。与缺陷 1 复合：长对话实际上**无法用滚轮翻阅**（只剩原生滚动条拖拽）。
3. **键盘快捷键泄漏（焦点在 TUI 按钮/select 上时）**（严重度：高）。全局快捷键统一用 `isEditableTarget(e.target)` 守卫（`smart-canvas.js:1042-1044`：只认 `input,textarea,select,option,[contenteditable],.prompt-node-control,.prompt-input`——**不含 button**）。TUI textarea 的 keydown 有 `stopPropagation`（`canvas-agent-tui.js:1027`）所以打字安全；但用户点过任何 TUI 按钮（📎/💭/发送/重试…）后焦点落到 `<button>` 上，此时：
   - Delete/Backspace → **删除整个选中节点**（`smart-canvas.js:15304-15315`）；
   - Tab → 弹出节点重命名编辑（`smart-canvas.js:15192-15200`）；
   - `z` → 缩放预览（`:15213-15217`）；方向键 → 移动节点（`:15225-15231`）；`a` → 打开资产库（`:15233-15236`）；F2 → 批量重命名（`:15240-15243`）。
4. **刷新页面后 TUI 会话历史丢失**（严重度：中）。`agentTuiState` 是纯内存 Map（`canvas-agent-tui.js:17`），messages/thinking/pendingImages 全丢；后端 stream 端点连上后只发 `session` 确认事件，**不回放历史**（`app/routers/api_agent_nodes.py:453-497`），也没有任何「拉历史消息」端点（该文件端点清单：`/prompt /steer /follow_up /abort /confirm /fork-messages /fork /switch-branch /model /autonomy /status /chat-models /stream`）→ 刷新后 TUI 是空白会话，但后端 pi 会话可能仍在运行 → 状态错配。
5. **渲染即触发后端 spawn 会话**（严重度：中）。`render()`/`renderAgentNode()` → `bindAgentNodeControls` → `connectAgentWebSocket`（`canvas-agent-tui.js:995`）；后端 stream 端点对每个连接 `get_or_create_session` 且「not running 就 spawn」（`api_agent_nodes.py:462-468`）→ 每次画布重渲染都可能自动拉起服务器端 pi 进程（资源与「意外启动」语义问题）。
6. **附件流**（严重度：中/低）。图片整体读成 dataURL 无大小上限（`canvas-agent-tui.js:347-355` `readAsDataURL`，`:398-404` 入队）；视频 contact-sheet / 音频波形卡的转换 `fetch` 无任何进度提示（`canvas-agent-tui.js:357-375` `convertAgentMediaAttachment`），用户只看到长时间无反馈后出现缩略图或失败系统消息。
7. **fork 选择器用原生 `prompt()`**（严重度：低）。`showForkPicker`（`canvas-agent-tui.js:560-580`）两次 `prompt()` 弹窗，与画布 modal 体系（`openAssetNameDialog` 等）不一致。
8. **无障碍**（严重度：低）。消息区无 `aria-live`（流式输出对读屏不可闻）；thinking 开关无 `aria-pressed`（且激活态样式已死，见 D-4）；状态徽标无 `role="status"`（对照 agent-task 节点有 `role="status"`：`canvas-agent-tui.js:958`）。
9. **pointer-events 取舍**（严重度：低）。消息区 `mousedown` stopPropagation（`canvas-agent-tui.js:1193-1196`）防住了节点拖拽，但也意味着从消息区无法起手 pan 画布（需抓框边/空白）。
10. **`render()` 尾部死代码**（非用户可见，清理项）：`return;`（`smart-canvas.js:9279`）之后的整段旧版渲染逻辑（至 `:9338` 附近）永不可达。

---

## F) 误删节点调查（点击右上角整节点删除、无确认）

**结论：「单击直接删整个节点、无确认对话框」是当前实现的既定行为（对所有节点类型一致，agent 非特例），且代码注释表明是 #149 B5 有意为之；唯一补救是 Ctrl+Z 撤销。**

元素与链路（逐行核实）：

- agent 节点上**可见**的删除入口只有一个：`.floating-node-actions` 里的 `.mini-x.node-delete`。
  - 模板：`smart-canvas.js:9228`（`!isEmpty && !isGroup` 时渲染；agent 节点 `isEmpty` 恒 false → 总是渲染）。
  - 定位：`position:absolute; right:7px; top:7px; z-index:8; opacity:0`，`.image-node:hover` 时 `opacity:1; pointer-events:auto`（`smart-canvas.css:460-461`）→ **正是节点框右上角、悬浮在卡片顶栏（🌿 fork 按钮附近）之上**——与事故现场「点右上角删了节点」完全吻合。
  - 另一个 `.node-head` 里的 `deleteBtn`（`smart-canvas.js:9226`）对 agent 节点被 `smart-canvas.css:451`（`:not(.empty-node) .node-head{display:none}`）隐藏，不参与事故。
- 处理链：`.node-delete` click 绑定（`smart-canvas.js:10751-10755`，`preventDefault + stopPropagation`）→ `deleteNodeFromButton(id)`（`static/js/canvas-node-ops.js:313-319`）→ `deleteNode(id)`（`canvas-node-ops.js:242-282`）。
  - 仅有的两道闸：生成中节点 toast 拒绝（`canvas-node-ops.js:314-315`；键盘路径同 `smart-canvas.js:15310-15313`）；`pushUndo()`（`canvas-node-ops.js:248`）——**没有确认对话框、没有两步式、没有延迟窗口**。
  - 注释（`canvas-node-ops.js:315-316`）：「#149 B5: 统一为一步直接删节点（与 Delete 键行为一致），不再对有图节点两步清图」→ 单步直删是有意设计。
  - `deleteNode` 内部对 agent 节点会 `cleanupAgentTuiState`（`canvas-node-ops.js:263-267`：关 WS、清退避定时器、删状态），随后 `render()` + `scheduleSave()` → 持久化删除。
- 键盘路径同样一步直删：Delete/Backspace（`smart-canvas.js:15304-15315`），有 `isEditableTarget` 守卫与 pushUndo。
- 恢复：`pushUndo` 深快照 → Ctrl+Z 可恢复（用户已实测恢复成功）。

**风险评估**：agent 节点携带运行中的会话、排队指令与输入草稿，删除代价远高于普通空节点；同时它的删除按钮只在 hover 时浮现且与卡片右上角功能键（🌿）视觉邻近，「误点」概率结构性偏高。建议方向：对 agent 节点（或所有携带运行态的节点）加两步确认/长按删除，且 hover 删除钮与卡片设置簇拉开距离。

---

## 排名缺陷清单（按严重度）

| # | 严重度 | 一句话标题 | 用户可见症状 | 根因（一行） | 修复方向 |
|---|---|---|---|---|---|
| 1 | S1 严重 | 单击/键盘无确认删除整个 agent 节点 | 误点右上角 hover 删除钮（或点过按钮后按 Delete）节点即被删，无确认 | 删除链路一步直删、无确认（`canvas-node-ops.js:313-319`、`smart-canvas.js:15304`；#149 B5 有意为之） | agent 节点两步确认/删除保护 + hover 删除钮与卡片功能键视觉分离 |
| 2 | S2 高 | 任何重渲染静默吞掉进行中输入（含焦点与 IME） | 打字过程中选/取消节点、或 agent 流式输出时，输入内容凭空消失；输入框焦点被抢 | `renderAgentNode`/`render()` innerHTML 全量重建 textarea 且无草稿持久化（`canvas-agent-tui.js:1218`；状态无 draft 字段 `:17-39`） | `agentTuiState.inputDraft` + input/blur 捕获 + 绑定后恢复 value/焦点/光标（聚焦且非合成中时） |
| 3 | S3 高 | 长对话不可用：自动滚动空操作 + 滚动归零 + 滚轮变缩放 | 流式输出追不上最新消息；消息历史滚不动（滚轮在缩放画布） | 自动滚动写错元素（`:1222-1225` 写 `.agent-tui-messages`，实际滚动容器是 `.agent-tui-body` `css:3871`）；`canvasWheelIsReserved` 白名单缺 `.agent-tui-body`（`smart-canvas.js:15098-15126`） | 滚动改到 `.agent-tui-body`；把 `.agent-tui-body` 加入 wheel 白名单 |
| 4 | S4 高 | 卡片宽度不跟随节点框（Bug1） | 框被拖宽后右侧大片空白（实测 780px 内容区里卡片仅 451px） | `.agent-tui-card` 只有 `height:100%` 无 width（`smart-canvas.css:3708-3721`），flex row 主轴取 max-content | 加 `width:100%`（对齐 `css:170-171` 兄弟卡片模式）或 `flex:1; min-width:0` |
| 5 | S5 中 | 输入框不 auto-grow + 双 CSS 规则 + 80px 硬顶（Bug2） | 多行指令被藏进 29px 单行框内部滚动，必须手动滚才能看全 | 无 `input` 监听/无高度计算（`canvas-agent-tui.js:1025-1035`）；`max-height:80px; resize:none` 生效（`css:4327-4329`） | 去重 `css:4095-4108`；实现 auto-grow 并重定义 max-height |
| 6 | S6 中 | 裸 emoji 图标与画布 lucide 语言割裂；thinking 激活态样式失效（Bug4） | 按钮图标平台渲染不一致、显「简陋」；思考开关点亮后无视觉反馈 | 按钮用原始 emoji（`canvas-agent-tui.js:927-931, 877, 807-818`）；`:928` 输出裸属性而 CSS 用 class 选择器（`css:4141`） | 换 lucide `data-lucide` 图标；改 `class=`/`[attr]` 选择器 + 加 `aria-pressed` |
| 7 | S7 中 | 刷新后 TUI 会话历史全丢 | 刷新页面后历史消息/思考/待发附件全空，后端会话却可能仍在跑 | 状态仅内存 Map（`canvas-agent-tui.js:17`）；后端 stream 不回放历史、无历史端点（`api_agent_nodes.py:453-497`） | stream 连接时回放历史，或新增 GET history 端点 + 前端 hydrate |
| 8 | S8 低 | 死亡/重复样式层与死功能 | 不可见（维护性债务；node-head 状态灯永不显示） | `.agent-node-*` 死块 `css:199-244`；`.node-head` 被 `css:451-452` 隐藏但仍在渲染（`smart-canvas.js:9226`）；`render()` return 后死代码（`:9279+`） | 清理死样式/死渲染；若保留状态灯需放开 agent 节点的 `.node-head` 显示 |
| 9 | S9 低 | 附件无大小上限/无转换进度；fork 用原生 prompt() | 大文件拖入无限制、视频/音频转换期间无反馈；分叉选择体验突兀 | `readAsDataURL` 无限制（`canvas-agent-tui.js:347-355`）；`showForkPicker` 用 `prompt()`（`:560-580`） | 大小上限+toast 进度；fork 选择器 modal 化 |
| 10 | S10 低 | 每次画布渲染都可能自动 spawn 后端会话；a11y 缺口 | 意外拉起 pi 进程；读屏无法感知流式输出 | bind 即 `connectAgentWebSocket`（`canvas-agent-tui.js:995`）+ 后端连接即 spawn（`api_agent_nodes.py:462-468`）；无 `aria-live` | 懒连接（用户首次交互/选中时建连）；补 `aria-live`/`role` |

---

### 附：给拆票人的备注
- A/B/C/D 四票与 F 事故票可直接引用上文行号；修 Bug1 时若同时做 D-5（node-head 状态灯），注意 `css:451-452` 的全局隐藏规则不要误伤其他节点类型。
- 修 Bug3（草稿）与 Bug2（auto-grow）建议同一票：两者都动 `bindAgentNodeControls` 的 input 绑定与同一组 CSS 规则，分开修会互相踩。
- 回归面：`tests/canvas_module_units/test-canvas-agent-tui.mjs`（纯函数单测）、`tests/test_agent_tui.py`、`tests/agent_tui_polish_browser.py`（浏览器断言，#301 的验收脚本）。
