# 审核记录更正：issue-244 / issue-249 / issue-263 / issue-246 状态纠偏

**本文件取代 issue-244-review.md 与 issue-249-263-246-review.md 的错误结论**（总控早期误判，触发点：票面与派工编号错位 + 压缩摘要失真）。

## 事实矩阵（以 git 对象与 gh 实况为准）

| 票 | 真实票面 | 实际交付 | 状态 |
|---|---|---|---|
| #244 | 工具面 17→~30（canvas.view/view_media/edit_*/batch_edit） | **未实现**（#243 只含 MCP 端点+假工具清理） | ✅ 已重开 |
| 派工「healthz」 | （无对应原票） | worker 真实交付：api_health.py+snapshot_sessions+4 测试 | ✅ 转移至 **#265** 并合并 3c18262，已关 |
| #249 | UI 卫生包（Escape 栈/204/删自造上限/JSON 锁测试） | worker **空产出**（wt-249 分支=基线 c6541df，无新 commit） | 开放，待重派 |
| #263 | #234 浏览器 trial 证据 | worker 空产出+连接错误中断 | 开放，待重派 |
| #246 | freeSpot 落点扫描（W3） | 未开始 | 开放，W3 排队 |

## 教训（已入纪律）

1. 派工前必须 `gh issue view` 核对票面与工单编号一致；worker 任务简报必须注入票面原文关键段。
2. worker 纪律新增：发现「派工指令与票面正文不符」→ 立即停工上报，不得自行选边实施。
3. 总控关票前必须验证：分支 merge-base + 交付文件确在目标分支（`git ls-tree`），不得只凭 result 文件存在。
4. 压缩摘要中的「已合并」类结论要用 git 实况复核后才能对外报告。

---

## 第二轮审计（2026-10-28，产物级验证）：全部已关票种真实

对 wave1-live 树逐票 ls-tree/grep 验证：

| 票 | 产物证据（wave1-live） |
|---|---|
| #240 | tests/test_agent_same_origin.py ✓ |
| #241 | app/routers/api_agent_nodes.py（confirm×21 处）+ tests/test_agent_tui.py ✓ |
| #242 | docs/adr/0022-mcp-streamable-http-canvas-endpoint.md ✓（票面是 ADR 文档票，先前"viewport-domains.js"为摘要记忆错误——该文件在任何分支都不存在） |
| #245 | tests/test_project_stats_endpoint.py ✓ |
| #243 | app/services/mcp_http.py + routers/api_mcp.py + tests/test_mcp_endpoint.py ✓ |
| #239 | agent_host.py `_kill_process_tree`/`stop_idle_watchdog`（8 处）✓ |
| #234 | 合并 6feca94 stat 完整：api_image_task_query.py+31 / api_project_library.py+172（recover 实现实际落点，非先前记忆的 generation.py）/ tests/test_project_image_recover.py+223 / canvas-generation-tasks.js+19 ✓ |
| #265(healthz) | e571927+bf0f212 在历史 ✓ |

结论：8 张已关票全部真实；错误仅存在于我的压缩摘要措辞（viewport-domains、"api_image_task_query recover" 定位），已在此更正。
