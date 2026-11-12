# 【Agent TUI】输入框固定 30-36px 无法增长，长输入被吞进滚动条

验证人：QA（Tabbit 实操实测，2026-11-13）。严重度 S6。

## 发生了什么
在 Agent 节点输入框打字超过 1-2 行，框高度不变（实测固定 30-36px），内容进入内部滚动（输入 6 行：内容高 113px / 可视 29px）。

## 我期望看到什么
输入框随内容自动增高（成熟 web 聊天输入框的标准行为），到合理上限再出滚动条。

## 复现步骤
1. 选中 Agent 节点
2. 在底部输入框粘贴多行长文本（≥6 行）
3. 实际结果：输入框高度不变，只能滚动查看
QA 截图：`.qa/shots/04_agent_input_overflow.png`（输入 6 行后框仍固定高度、内容滚动至 line5/6）、`.qa/shots/02_textarea_no_grow.png`（均已随 commit 3c38bb6 入库，push 后可直接访问）

## 补充信息
- 代码无任何自动增高逻辑：输入框只绑了 mousedown/keydown（static/js/canvas-agent-tui.js:1025-1035）。
- 样式层隐患：同名规则两条互相覆盖——static/css/smart-canvas.css:4095-4104（死规则）与 :4317-4330（生效：`resize:none; max-height:80px; overflow-y:auto`）。修 auto-grow 必须同时去重两条并处理 80px 上限语义，否则增高到 80px 卡死。
- 与 #301（「Agent 卡片按 UI 规范重设计」，closed 2026-11-10）同症状家族：输入区在重渲染/样式覆盖下丢失内容的问题反复出现；#301 教训：无实拍不关票，本票已附实拍。
