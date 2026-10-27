# 任务卡 batch-5：Wave 1（ComfyArt 对照落地 P0 波）并发派发

> 总控：主会话（HYBRID STARK-JARVIS）。2026-10-27 派发。执行协议：/implement（spec→TDD→单测→全量→自评 review→commit 分支）。核验与合并由总控负责；worker 禁止 push/关票/合并。

## 泳道与派发（worktree + 分支隔离）

| 泳道 | 票 | 难度/预估 | worktree | 分支 | runId | 状态 |
|---|---|---|---|---|---|---|
| A | #234 取回三层断裂修复 | 大 / 120m | D:/ff-wt/wt-234 | issue/234 | del_mvqyj7a7_55p5 → 120m超时零commit(仍纯探索) → resume del_mvr2tzwi_8re0(100m,硬时箱:T+15m红测试commit/T+60m逐层绿/T+85m全量) | 续跑中(关键路径) |
| B | #239 agent_host 进程树必杀 | 中 | (已删) | (已删) | 3 轮 worker 超时 → **总控接管收尾** commit 94e2e4a | ✅ 合并 cbac05c，票已关，review PASS（全量 1313 passed + domain 门 passed） |
| C | #240 agent 端点同源守卫 | 小 / 45m | D:/ff-wt/wt-240(已删) | issue/240(已删) | del_mvqyj7a8_173t → 超时 → resume del_mvr05qi1_knyh exit0 | ✅ 已合并 ff32d35，票已关，review PASS（总控复跑 7/7+41/41） |
| D | #242 ADR 0022 MCP + #245 ADR 0006 补行 | 文档 / 45m | D:/ff-wt/wt-242(已删) | issue/242(已删) | del_mvqyj7a8_110l → 超时 → resume del_mvr05qi2_au0n exit0 | ✅ 已合并 2221329，两票已关，review PASS |
| E | #250 ADR 0021 ops.db sidecar | 文档 / 45m | D:/ff-wt/wt-250(已删) | issue/250(已删) | del_mvqyj7a8_m7xy → 超时 → resume del_mvr05qi2_ar4l exit0 | ✅ 已合并 3d3fca4，票已关，review PASS；ADR status=提议待用户签署 |

⚠️ resume 运行 cwd 回落到主仓 D:\Firefly-Canvas（未带 cwd 参数）——总控监控 worker 是否误写主仓主工作树；必要时 cancel + 带 cwd 重派。

## 教训（Wave 1b 派发前适用）

- worker 普遍「探索耗尽预算」：4 个超时票全部无 commit。Wave 1b 起：总控先自己做定向 grep 把 file:line 事实写进任务卡，并在票面写明「探索预算≤N 次工具调用，X 分钟内必须有首个 commit」。
- 新规则：issue-NNN-result.md 必须随分支 commit（#240 的 result 漏 commit，总控从 worktree 抢救）。

## 关键决策记录

- ADR 编号预分配：#250 → 0021；#242 → 0022（避免两文档票抢同一编号）。
- #251/#257/#258 隐性前置 = #250（ops.db schema），已加急派发；票面 Blocked by 待补边。
- smart-canvas.js 六票（234/237/238/251/252/253）串行队列序：234 → 237 → 238 → 252 → 253 → 251。

## Wave 1b 已派发（#240/#242 已合并解锁）

| 泳道 | 票 | 难度/预估 | worktree | 分支 |
|---|---|---|---|---|
| G | #241 TUI confirm fallback 404 | 小 / 40m | D:/ff-wt/wt-241 | issue/241 | del_mvr2ozan_if2l(60m) exit0 | ✅ 已合并，票已关，review PASS（总控复跑 50 passed） |
| H | #243 MCP streamable-HTTP 端点实现 | 中大 / 120m | D:/ff-wt/wt-243 | issue/243 | del_mvr2ozao_b3ja(150m) | 进行中 |

合并序纪律：#241 先于 #243（可能同触 confirm 端点区）；#243 开工先读 docs/adr/0022（含已知缺口清单）。

## Wave 1b 待解锁队列（前置合并后派发）

- #241（待 #240）；#235（待 #234）→ #236（待 234,235）；#243（待 240；#242 已合入✅）；
- #237、#238（smart-canvas 队列，待 #234 合并）；#254（待 #236）。
- 暂缓：#249（可能与 smart-canvas/测试文件重叠，待 #234 合并后评估）、#246、#261（#261 与 #249 同抢 tests/test_workflows.py，排后）。
- 人工闸口：#233（ready-for-human）、#259（产品决策）；#247/#248 为 wayfinder 地图票。
