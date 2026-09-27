# batch-3 结果（#117 TUI 规格细节补齐）

## 改动清单
1. **活动行可折叠**（US-05）：`static/js/smart-canvas.js` 工具活动行改 header 结构（▸/▾ toggle + icon + name），点击展开显示完整参数/结果；`activity.expanded` state 驱动，重渲染保留；CSS `.agent-tui-tool-*` 系列
2. **卡片状态灯 + 最后行预览**：新增 `agentCardStatusHtml(nodeId)`，画布 smart-agent 卡片显示状态灯（空闲/运行中/连接中/错误/已断开/等待确认，confirm 覆盖）+ 最后一条消息 40 字符预览（escapeHtml）
3. **流式 markdown**：新增 `renderMarkdown(text)`（smart-canvas.js:727）——先 escapeHtml 再渲染代码块/行内代码/粗体/斜体/链接/无序列表；**链接协议白名单**（https?/mailto/相对路径，javascript: 等消毒为 `#`）；不引第三方库
4. **多行输入**：输入行 `<input>` → `<textarea rows=1>`，Enter 发送 / Shift+Enter 换行，发送后清空，busy 时 disabled
5. **CSS**：`static/css/smart-canvas.css` +132 行（状态灯、预览、活动行折叠、markdown 元素、textarea）
6. **测试**：新增 `tests/agent_tui_polish_browser.py` + `tests/agent_tui_polish_browser_client.js`（17 断言）

## 验收逐条
1. 活动行可折叠 ✅（默认收起 display:none，点击展开 display:block，实测）
2. 卡片状态灯 + 最后行预览 ✅（class=agent-card-status agent-tui-status-*，preview 存在，实测）
3. 流式 markdown ✅（bold→strong、italic→em、inline code 实测；javascript: 链接消毒为 # 实测；https 链接保留实测）
4. 多行输入 ✅（tag=TEXTAREA 实测，Enter/Shift+Enter 逻辑代码亲核）
5. 浏览器测试 ✅（17 断言全过）
6. 全量回归 ✅（见下）
7. result.md ✅
8. 未提交（由主会话提交）✅

## 测试输出
- 新浏览器测试：`python tests/agent_tui_polish_browser.py` → `(1424, 900) PASS`，17 断言全过
- 全量回归：后台跑 `29 failed, 985 passed, 342 subtests`，29 个失败全部为 subprocess FileNotFoundError（后台 shell 无 node 的环境假失败）；交互 shell 重跑失败涉及的 4 个文件 `tests/test_project_canvas_mutation_client.py tests/test_quality_gate.py tests/test_outpaint_ratios.py tests/test_project_image_generation_ui.py` → **117 passed, 26 subtests, 0 failed**

## 遗留风险
- P2：`<pre>` 被包进 `<p>`（浏览器容错）；卡片状态灯未映射 retrying；代码块内行内标记可能被二次渲染（纯外观）
- 既有 M1 小竞态：connectAgentWebSocket 对 CONNECTING 态旧 socket 无守卫（非本批引入）
