# Issue #240 结果报告

**标题**：Security(W1)：agent-tools/agent-nodes 端点补同源守卫（MCP 开放前置）
**分支**：`issue/240`（基于 `pipeline/canvas-modularization`）
**Commit**：`87dd2d63ea1b96fc91193d8c5553412dac57dd83` — `security(agent-endpoints): 补同源守卫 (#240)`

## 完成状态：✅ 完成，全部验收项通过

## 改动内容

| 文件 | 改动 |
|---|---|
| `app/core.py` | 新增 `ensure_agent_same_origin(request: HTTPConnection)`，紧随 `ensure_same_origin_request`，复用同一守卫模型（Host 白名单 `{localhost, 127.0.0.1, ::1, 0.0.0.0, ::}` + Origin/Referer 与应用来源精确匹配，403 detail 与现有 API 一致） |
| `app/routers/api_agent_tools.py` | 5 个 router（canvas / generate / media / viewport / agent-confirm）全部挂 `dependencies=[Depends(ensure_agent_same_origin)]` |
| `app/routers/api_agent_nodes.py` | router 级挂同一守卫，覆盖全部端点**含 WebSocket stream** |
| `tests/test_agent_endpoints_same_origin.py` | 新增（7 个测试，TDD 先红后绿） |
| `tests/test_agent_tui.py` | 5 处 `websocket_connect` 相对路径改为绝对 `ws://127.0.0.1/...`（适配守卫，见"测试情况"） |

## 守卫语义（与票面"本机回环+Host 校验"模型对齐）

- **Host 必须是回环**：非回环 Host 一律 403（无论是否带 Origin）。
- **带 Origin/Referer**（浏览器请求）：必须与应用来源（scheme+host）精确匹配，否则 403。
- **无 Origin/Referer 的回环调用**：放行 —— 票面处方第 2 条明确要求不破坏 pi sidecar 现有调用路径（服务端自己 spawn，`X-Agent-Project` header + `http://127.0.0.1`，无 Origin），否则会把自家 sidecar 锁在门外。
- **WebSocket 兼容**：守卫参数标注为 `starlette.requests.HTTPConnection`（FastAPI 0.141 对 WS 路由注入 `WebSocket` 实例，`Request` 标注不会被注入到 WS 依赖），scheme 映射 `ws→http`、`wss→https`（兼容 TestClient 的 `ws`/生产 ASGI 的 `websocket` 两种 scope scheme）。

## 验收对照

| 票面验收项 | 结果 |
|---|---|
| 跨源请求被拒的单测 | ✅ `tests/test_agent_endpoints_same_origin.py`：跨源 Origin 403、仅跨源 Referer 403、非回环 Host 无 Origin 403、跨源 WebSocket 被拒 |
| sidecar 端到端不回归 | ✅ 回环 + `X-Agent-Project` + 无 Origin 的 sidecar 形态请求 200 放行（`test_sidecar_loopback_without_origin_passes`）；同源 WS 连接收到 `session` 确认帧不回归（`test_same_origin_websocket_allowed`）；全量测试中既有 agent 流程（confirm、canvas 生成、多节点流）全绿 |
| `python tools/quality_gate.py domain` 通过 | ✅ `{"mode": "domain", "verdict": "passed", "final_exit_code": 0, "duration_seconds": 128.6}`（evidence: `C:\Users\Administrator\AppData\Local\Temp\firefly-quality-gate\20261028-031633-domain-68036`） |

## 测试情况

- 新增测试：7/7 通过（TDD：跨源被拒测试先写并确认红，实现后转绿）。
- 全量：`python -m pytest tests/ -x -q` → **1308 passed, 1 skipped, 618 subtests passed**（605s）。
- 唯一回归点及处置：`tests/test_agent_tui.py` 3 个多节点隔离测试用相对路径 `websocket_connect`，starlette 1.6 TestClient 会 join 到硬编码的 `ws://testserver`（Host 非回环 → 被守卫正确拒绝，`WebSocketDenialResponse`）。已改为绝对 `ws://127.0.0.1/...` URL（与其 TestClient `base_url="http://127.0.0.1"` 及默认 Origin 头一致），属测试适配而非守卫绕过。

## 自我 code-review 结论

- 守卫只加在 `api_agent_tools.py`（5 个 agent router）与 `api_agent_nodes.py`（1 个 router），与票面范围完全一致。
- 未触碰任何公开/通用端点：project CRUD、通用 canvas API、文件服务、静态资源等其他 router 零改动（diff 仅 5 个文件：core + 2 个 agent router + 2 个测试）。
- 无新增依赖；`quality_gate.py domain` 通过。

## 阻塞 / 偏离

- 无阻塞，无实现偏离。
- 未 push、未合并、未关闭 Issue（按工作规则由总控核验合并）。
- 备注（供总控参考）：守卫允许"回环 Host + 无 Origin"的编程式调用，这是票面处方第 2 条为 sidecar 明确要求的语义；未来 MCP 开放票若需收紧该分支，应在新票处理。
