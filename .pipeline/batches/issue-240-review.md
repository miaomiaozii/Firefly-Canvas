# 审核 issue-240-review.md：#240 agent 端点同源守卫

**判定：PASS**（总控复跑测试 + diff 审查，合并 ff32d35，票已关）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| 守卫模型=ensure_same_origin_request（Host 回环） | PASS：`ensure_agent_same_origin`（app/core.py，HTTPConnection 标注——FastAPI 0.141 对 WS 路由注入 WebSocket 而非 Request，router 级 dependency 才生效） |
| 差异点：无 Origin 回环 sidecar 放行 | PASS（pi spawn 的 canvas-tools.ts 走 http://127.0.0.1 + X-Agent-Project，无浏览器 Origin 头；带 Origin/Referer 则必须精确匹配） |
| 挂上 api_agent_tools.py + api_agent_nodes.py 全部端点（含 WS stream） | PASS（commit 87dd2d6 diff 审查） |
| 测试 | 总控复跑：新 tests/test_agent_endpoints_same_origin.py **7/7**；tests/test_agent_tui.py **41/41**（含 TestClient ws 改绝对回环 URL 适配）。全量套件 worker 报 1109 passed, 1 skipped（未由总控复跑，Wave 末全量门兜底） |
| 无新依赖 | PASS |

## 过程备注

- 首派 45m 超时；resume `del_mvr05qi1_knyh` 完成（2 红守卫测试先行→实现）。
- 事故：resume 期间曾对主仓 issue-240 文件误跑 stash，已 pop 恢复，主仓完好。
- issue-240-result.md 未随分支 commit，总控从 worktree 抢救回主仓（教训：票尾要求 result 文件必须 commit）。
