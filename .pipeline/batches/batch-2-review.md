# batch-2 审查记录（#117 US-27 + US-22）

## 第 1 轮：PASS（2026-09-24，主会话审查）

| # | 验收标准 | 判定 | 验证方式 |
|---|---|---|---|
| 1 | 项目永久删除 kill 会话 | PASS | diff 亲核：purge_trashed_project 成功后调 cleanup_project |
| 2 | 进回收站 kill 会话 | PASS | diff 亲核：trash_firefly_project 同上；无批量删除入口（grep 确认） |
| 3 | 恢复无需特殊处理 | PASS | 会话文件随目录回来 + spawn 自动 --continue（agent_host.py:123-128） |
| 4 | 导出含 agent_nodes/ | PASS | diff 亲核：export 收集+manifest 声明+ZIP_DEFLATED；导入验证放行前缀；提取有 zip-slip 防护（commonpath 检查） |
| 5 | cleanup_project 方法 | PASS | 代码亲核：key 前缀 `{project_id}:` 与 `_session_key` 格式一致；锁内移除记录防竞态、锁外 stop 进程；stop_session 容错（terminate→5s→kill） |
| 6 | fake host 单测 | PASS | 读测试：kill/多项目隔离/幂等/无 session 4 例，断言到位 |
| 7 | 全量回归 | PASS | 单测 1005（twin）+ 浏览器 57+22 subtests（主会话亲跑 73.72s）+ import main OK |
| 8 | result.md | PASS | 完整准确 |
| 9 | 不提交 | PASS | — |

US-22：设计满足（app 无全局文本模型设置，spawn 空 model 不传 --model，pi 用自身默认）——与主会话独立侦察结论一致。

备注（不阻塞）：
- 导出未对 agent_nodes/ 文件做大小限制（会话 JSONL 通常 <1MB，可接受）
- twin 的"全量回归"又是单测（316.30s）；浏览器测试由主会话补跑全绿（连续两个批次如此，收尾前已确认）

## 审查中额外发现（转入 batch-3）
主会话对照 spec Implementation Decisions 逐条核 TUI 形态，发现 4 处 M1 遗留缺口（前几轮会话实现 TUI 时未覆盖）：
1. US-05 活动行"可折叠"未实现——工具活动行是平的（icon+name+80 字符 args 预览），无展开/收起交互
2. 节点卡片（画布上）无状态灯（空闲/运行中/等待确认）+ 最后行预览——状态灯只在 TUI 面板内（11676 行）
3. 消息渲染无 markdown（纯文本 escapeHtml），spec 要求"流式 markdown"
4. 输入行是单行 `<input type="text">`，placeholder 宣称"Shift+Enter 换行"但单行 input 无法换行——spec 要求终端式输入行（Enter 发送/Shift+Enter 换行）
