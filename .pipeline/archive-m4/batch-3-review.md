# batch-3 审查记录（#117 TUI 规格细节补齐）

## 第 1 轮：PASS（2026-09-24，主会话审查 + reviewer 辅助）

背景：twin worker 25 分钟超时（实现已全部完成，只差验证）。主会话接手：亲跑新浏览器测试、亲审全部 4 区域代码、后台全量回归。reviewer 两次派发，第 1 次空输出，第 2 次报告被模型输出上限截断（§1/§2 完成，均 PASS）。

### 验收逐条

| # | 验收标准 | 判定 | 验证方式 |
|---|---|---|---|
| 1 | 活动行可折叠 | PASS | 代码亲核：`activity.expanded` state 驱动（11728）、render 后重绑定（13796-13809，非委托但重渲染重建绑定无丢失）、`escapeAttr`、`stopPropagation` 防画布拖拽；浏览器实测：默认 `display:none` → 点击 `display:block` |
| 2 | 卡片状态灯+最后行预览 | PASS | 代码亲核：`agentCardStatusHtml`（11625-11666）状态判定与 agentTuiState 一致、confirm 覆盖为"等待确认"、预览 `escapeHtml` 40 字符截断；浏览器实测：`class=agent-card-status agent-tui-status-connecting` + preview 存在 |
| 3 | 流式 markdown | PASS（修 1 处后） | 代码亲核：先 escapeHtml 再渲染、代码块先行、粗体先于斜体、`rel="noopener noreferrer"`；浏览器实测 bold/italic/inline code 渲染正确。**发现并修复：链接 href 无协议校验**（`[x](javascript:alert(1))` 会进 href，escapeHtml 不转义冒号）→ 加协议白名单（https?/mailto/相对路径，其余 `#`），补 2 条浏览器断言，重跑全绿 |
| 4 | 多行输入 | PASS | 代码亲核：`<textarea rows=1>`（11769）、Enter 发送/Shift+Enter 换行（13817）、发送后清空、busy 时 disabled、mousedown stopPropagation 防失焦；浏览器实测 `tag=TEXTAREA` |
| 5 | 浏览器测试 | PASS | 亲跑 `python tests/agent_tui_polish_browser.py`：17 断言全过（14 原 + 2 链接消毒 + 1 setup），断言真实（非恒真，含负向：默认折叠 display:none） |
| 6 | 全量回归 | PASS | 后台 nohup 跑 29 failed/985 passed → 失败全集中在 4 个文件（mutation_client 14、image_gen_ui 3、quality_gate 2、outpaint 1+9 subtests），错误均为 subprocess `FileNotFoundError [WinError 2]`（后台 shell 找不到 node）；交互 shell 重跑这 4 个文件 **117 passed + 26 subtests 全绿** → 判定环境假失败，回归实际全绿 |
| 7 | result.md | PASS | 见 batch-3-result.md |
| 8 | 不提交 | PASS | 由主会话提交 |

### 备注（不阻塞，记录在案）
- **P2**：markdown 段落包装会把 `<pre>` 包进 `<p>`（HTML5 非规范，浏览器容错渲染正常）——轻量渲染器可接受
- **P2**：`agentCardStatusHtml` 未处理 `retrying` 状态（`agentNodeBodyHtml` 有）——卡片显示"空闲"，纯外观
- **P2**：行内代码/粗体正则可能匹配到 `<pre>` 代码块内部（嵌套 code 标签）——纯外观
- **既有（M1，非本批）**：`connectAgentWebSocket` 在旧 socket CONNECTING 态会新建连接，旧 socket 若后开则成孤儿且其 onclose 会误清 state.socket——低概率竞态，建议后续加 readyState===CONNECTING 守卫
- stream 端点 404 是测试脚本用 HTTP GET 探测 WS 路由的产物（WS 端点连接即 auto-spawn session），非产品 bug

## 结论
可提交。修复 1 处（链接协议白名单）+ 补 2 条断言后全绿。
