# 审核 issue-243-review.md：#243 MCP streamable-HTTP 端点

**判定：PASS**（总控复跑全量 1360 passed / 1 skipped，655s；合并入 wave1-live，票已关）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| streamable-HTTP 端点（api_mcp.py, 525 行） | PASS：tests/test_mcp_endpoint.py 583 行覆盖 |
| 具现确认门（MCP approve ↔ WS confirm 状态机） | PASS：复用 session.confirm_handlers；三轮卡点根因=harness sys.modules 污染产生的假象，非门 bug |
| 三级仲裁 resolve_project | PASS：+ `_live_open_project_ids()` 过滤已删除项目的陈旧注册（防 409 劫持） |
| 假工具清理 + 广播补全 | PASS：canvas-tools.ts / api_agent_tools.py（+310 行）/ project_sessions.py(+31) |
| 测试隔离（顺带） | PASS：viewport 隔离断言移入子进程 runner（tests/_conversation_viewport_isolation_runner.py），修 MCP 全量假失败 |

## 保留意见（不阻塞）

- e37e02d 是「worker 实现 + 总控隔离修」的混合 commit（我 git add -A 误并），message 只写了 test(234)——内容完整，追溯性受损。教训：总控在 worker worktree 提交前先 status 点名。
- 三轮 worker 超时（150+70+80m）：MCP 协议语义+确认门调试环境成本高。教训已记：协议类大票拆「端点壳」与「门语义」两票。
