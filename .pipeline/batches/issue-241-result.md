# Issue #241 结果报告

**标题**：Bug(W1)：TUI 确认门 HTTP fallback 打到不存在的路由（必 404 被静默吞）
**分支**：`issue/241`（已含 #240/#242/#250 合并）
**Commit**：本分支唯一代码 commit（message: `fix(agent): 补 TUI 确认门 HTTP fallback 路由，失败不再静默吞 (#241)`，含本报告）

## 完成状态：✅ 完成，全部验收项通过

## 改动内容

| 文件 | 改动 |
|---|---|
| `app/routers/api_agent_nodes.py` | 新增 `POST /{node_id}/confirm` 端点（`post_agent_node_confirm`，置于 `/{node_id}/fork-messages` 之前）：取 `{confirm_id, approved}` 顺序派发全局 `agent_host_manager` 对应 session 的 `session.confirm_handlers`，与 WS 接收循环 `cmd_type == "confirm"` 分支（重构前同一状态机）完全一致；fastapi import 补 `Request` |
| `static/js/canvas-agent-tui.js` | `sendAgentConfirm` 的 HTTP fallback：fetch 链补 `.then(res => { if (!res.ok) console.warn(...) })`——HTTP 错误状态（404/500/403…）不再静默吞；网络级失败保留原 `.catch(console.error)`；注释标明目标端点位置 |
| `tests/test_agent_tui.py` | `TestAgentConfirmation` 新增 2 个回归测试（TDD 先红后绿） |
| `.pipeline/batches/issue-241-result.md` | 本报告 |

## 修复方案

采用票面处方第 1 条（补端点，而非删 fallback + 禁用按钮）：

- 新端点 URL 与 TUI fallback 原目标逐字一致：`/api/projects/{pid}/agent-nodes/{nid}/confirm`（router 前缀 + `/{node_id}/confirm`），TUI 侧 URL 无需改动，只补失败可见性。
- 状态机与 WS 路径一致：逐个 `await handler(confirm_id, approved)`，handler 自行校验 confirm_id（不匹配 no-op），异常 try/except 吞（与 WS 分支 441-449 行同构）。
- session 用 `get_or_create_session` 获取（与 `agent_event_stream` 相同）；注意该实现对非 running 会话会**重建**新 session——真实确认场景必然发生在 running 会话上，语义成立。
- 不引入项目校验 404：`ensure_agent_same_origin`（#240）已在 router 级覆盖同源/Host 守卫。
- 响应体 `{"ok": true, "confirm_id": ..., "approved": ...}`（fire-and-forget，无 handler 匹配时静默 no-op 仍 200——与 WS 路径同语义）。
- TUI「静默」根因：`fetch` 只对网络失败 reject，HTTP 错误状态（含 404）正常 resolve，原代码无任何 `res.ok` 检查。该 TUI 无 toast 组件，按要求采用最低可见渠道 `console.warn`（含 status/statusText/confirm_id 便于排查）。

## 验收对照

| 票面/任务验收项 | 结果 |
|---|---|
| fallback 打到真实存在的路由 | ✅ `POST /api/projects/{pid}/agent-nodes/{nid}/confirm` 已存在并接入既有 confirm 状态机 |
| 失败不再静默（非 404 至少 console.warn/toast） | ✅ `!res.ok → console.warn`（覆盖一切 HTTP 错误状态，含 404）；网络错误 `console.error` 保留 |
| 新增回归测试断言 fallback 目标路由存在且 2xx | ✅ `test_confirm_http_fallback_route_exists_and_2xx`：TestClient 用绝对回环 URL `base_url="http://127.0.0.1"` + 匹配 Origin（过 `ensure_agent_same_origin`），断言 200 + `ok` + handler 实际收到 `(confirm_id, approved)` |
| 票面：确认门在 WS 断开场景的行为有测试覆盖 | ✅ HTTP fallback 即 WS 断开路径，2 个新测试覆盖（含 `test_confirm_http_fallback_missing_session_still_2xx`：无活跃会话仍 2xx no-op） |
| `python tools/quality_gate.py domain` 通过 | ✅ `{"mode": "domain", "verdict": "passed", "final_exit_code": 0, "duration_seconds": 128.547}`（evidence: `C:\Users\Administrator\AppData\Local\Temp\firefly-quality-gate\20261028-034723-domain-62380`） |

## 测试情况

- 新增测试：2/2 通过（TDD：先写测试确认红——路由 404 AssertionError——补端点后转绿）。
  - 过程中踩到 `get_or_create_session` 的「非 running 会话被重建」行为：测试须以 `FakeAgentProcess` + `status="running"` 让会话呈 running，否则端点拿到新建的空 session（与既有 `test_confirm_endpoint_sends_event` 同一适配手法）。
- `tests/test_agent_tui.py`：**43 passed**（原 41 基线 + 2 新增）。
- `tests/test_agent_endpoints_same_origin.py`：**7 passed**（#240 守卫无回归）。
- 全量：`python -m pytest tests/ -q` → **1310 passed, 1 skipped, 618 subtests passed**（606.5s，较 #240 基线 1308 仅 +2 新增）。

## 自我 code-review 结论

- 端点仅复用既有状态机（`session.confirm_handlers` 派发），无新全局状态、无新依赖；JS 仅增 `.then` 分支，WS 主路径与网络 catch 不变。
- 路由注册无遮蔽：`/{node_id}/confirm` 为独立两段路径，与既有 `/{node_id}/model|autonomy|prompt|steer|follow_up|abort`、WS `/{node_id}/stream` 均不冲突。
- 未新建 conftest.py；无新增依赖。

## 阻塞 / 偏离

- 无阻塞，无实现偏离。
- 未 push、未合并、未关闭 Issue（按工作规则由总控核验合并）。
- 备注（供总控参考）：新端点为 fire-and-forget 语义，若 confirm_id 无 handler 匹配（已超时/会话已重建）则静默 no-op 且返回 200——与 WS 路径同语义；未来如需投递回执，可在响应体加 `matched` 字段而不破坏现有客户端。
