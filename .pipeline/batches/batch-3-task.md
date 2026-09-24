# batch-3 任务卡（#117 TUI 规格细节补齐）：活动行折叠 + 卡片状态灯/预览 + 流式 markdown + 多行输入

## 背景
#117 Agent 节点 v2（Pi 终端 TUI）。TUI 主体已上线（`static/js/smart-canvas.js` 约 11331-11700 行：agentTuiState、connectAgentWebSocket、handleAgentEvent、agentNodeBodyHtml、renderAgentNode）。主会话对照 spec（GitHub issue #117 的 Implementation Decisions："深色底、等宽字体、流式 markdown、工具活动行（⚙/✅/⏳/❌ 前缀可折叠）、思考默认隐藏可开关、底部终端式输入行（Enter 发送 / Shift+Enter 换行）。节点卡片（画布上）显示名称+状态灯（空闲/运行中/等待确认）+最后一行预览"）逐条核对，发现 4 处未实现。本批次补齐（纯前端 + 浏览器测试）。

## 现状（已核实）
1. 活动行：`agentNodeBodyHtml` 内 `state.toolActivities` 渲染为平铺 div（`.agent-tui-tool`：icon + name + 80 字符 args 预览），无折叠交互（约 11645-11658 行）
2. 画布节点卡片：smart-agent 卡片只有 node-head（标题）+ node-body（空/通用），无状态灯无最后行预览；状态灯 class（agent-tui-status-idle/busy/connecting/error/disconnected）只存在于 TUI 面板内（约 11602-11626、11676 行）
3. 消息渲染：纯文本 escapeHtml，无 markdown
4. 输入行：`<input type="text" class="agent-tui-input">`（约 11687 行），Enter 发送逻辑在约 13717 行（`event.key === 'Enter' && !event.shiftKey`）

## 验收标准
1. **活动行可折叠**：工具活动行默认收起（只显示 icon + 工具名 + 状态），点击展开显示完整参数/结果；展开态再点收起。实现方式自定（行内 toggle 或整组折叠均可），需有 CSS 与事件绑定
2. **节点卡片状态灯 + 最后行预览**：画布上 smart-agent 节点卡片显示状态灯（空闲/运行中/等待确认/错误，复用 agent-tui-status-* class 体系）+ 最后一行消息预览（从 agentTuiState 取最后一条消息/活动截断显示）；TUI 状态变化时卡片同步更新（renderAgentNode 已存在，接上即可）
3. **流式 markdown**：TUI 消息文本用 markdown 渲染（流式更新时重渲染）。仓库内如无现成 markdown 渲染器，用一个轻量自实现（支持代码块/行内代码/粗体/斜体/列表/链接即可，不引第三方库）；必须保持 XSS 安全（先 escapeHtml 再渲染标记）
4. **多行输入**：输入行改 `<textarea>`（1-3 行自适应或固定小高度），Enter 发送、Shift+Enter 换行（placeholder 文案与行为一致）；发送后清空
5. **浏览器测试**：在既有 `tests/agent_ui_browser.py`（或新增 `tests/agent_tui_polish_browser.py`）加断言——活动行点击展开/收起、卡片状态灯存在、markdown 渲染（如 **粗体** 出 `<strong>`）、textarea Shift+Enter 不换行发送行为（至少断言元素类型与关键 class）
6. 全量回归 `python -m pytest tests/ -q --ignore=tests/excel_import_browser.py` 全绿（单测+浏览器，10-15 分钟，bash timeout 1200s+，只报 passed/failed 数）
7. 写 `.pipeline/batches/batch-3-result.md`（覆盖旧文件）：4 项改动清单+验收 1-6 逐条+测试输出+遗留风险
8. 不要提交 commit

## 约束
- 只动 `static/js/smart-canvas.js`、`static/css/`（TUI 相关样式）、浏览器测试文件；不碰后端、canvas-tools.ts、旧引擎保留区
- 不破坏既有 TUI 行为（消息流/steer/follow_up/abort/确认卡/思考开关）——既有浏览器测试必须原样通过
- 状态灯"等待确认"态：state.confirm 存在时显示（确认卡渲染中即为等待确认）
- 一次跑不完就在干净边界停下，result.md 写清进度
