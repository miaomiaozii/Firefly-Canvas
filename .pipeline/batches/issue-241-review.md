# 审核 issue-241-review.md：#241 TUI confirm fallback 404

**判定：PASS**（总控复跑测试 + diff 审查，已合并，票已关）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| fallback 打到真实存在的路由 | PASS：新增 `POST /{node_id}/confirm`（app/routers/api_agent_nodes.py，router 级 ensure_agent_same_origin 覆盖） |
| 与 WS 确认同一状态机 | PASS：复用 session.confirm_handlers 广播，confirm_id 不匹配各自 no-op |
| 失败不再静默 | PASS：前端非 2xx console.warn 带 status/confirm_id（canvas-agent-tui.js:417-427） |
| 回归测试 | PASS：tests/test_agent_tui.py +54 行；总控复跑 test_agent_tui + same_origin = 50 passed |

## 保留意见（不阻塞）

- handler 循环 `except Exception: pass`——与 WS 路径同构，但「失败可见」承诺只覆盖 HTTP 层；handler 内部异常仍静默。记为后续 UI 卫生票候选。
- worker 顺手改了总控 batch-5.md（陈旧副本）——合并自动无冲突，总控版为准。新纪律：worker 禁触 batch-5.md。
