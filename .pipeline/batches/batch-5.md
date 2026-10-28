# 任务卡 batch-5：Wave 1（ComfyArt 对照落地 P0 波）并发派发

> 总控：主会话（HYBRID STARK-JARVIS）。2026-10-27 派发。执行协议：/implement（spec→TDD→单测→全量→自评 review→commit 分支）。核验与合并由总控负责；worker 禁止 push/关票/合并。

## 泳道与派发（worktree + 分支隔离）

| 泳道 | 票 | 难度/预估 | worktree | 分支 | runId | 状态 |
|---|---|---|---|---|---|---|
| A | #234 取回三层断裂修复 | 大 | (已删) | (已删) | 2 轮超时（地图含幻觉 API）→ 三派红测试 a72e058 → resume 75m 再超时 → **总控接管**：契约 6 绿+全量 1306 绿+domain 门 passed，a29f61a+d191006 | ✅ 合并 6feca94，票已关，review PASS；trial 证据转 #263 |
| B | #239 agent_host 进程树必杀 | 中 | (已删) | (已删) | 3 轮 worker 超时 → **总控接管收尾** commit 94e2e4a | ✅ 合并 cbac05c，票已关，review PASS（全量 1313 passed + domain 门 passed） |
| C | #240 agent 端点同源守卫 | 小 / 45m | D:/ff-wt/wt-240(已删) | issue/240(已删) | del_mvqyj7a8_173t → 超时 → resume del_mvr05qi1_knyh exit0 | ✅ 已合并 ff32d35，票已关，review PASS（总控复跑 7/7+41/41） |
| D | #242 ADR 0022 MCP + #245 ADR 0006 补行 | 文档 / 45m | D:/ff-wt/wt-242(已删) | issue/242(已删) | del_mvqyj7a8_110l → 超时 → resume del_mvr05qi2_au0n exit0 | ✅ 已合并 2221329，两票已关，review PASS |
| E | #250 ADR 0021 ops.db sidecar | 文档 / 45m | D:/ff-wt/wt-250(已删) | issue/250(已删) | del_mvqyj7a8_m7xy → 超时 → resume del_mvr05qi2_ar4l exit0 | ✅ 已合并 3d3fca4，票已关，review PASS；ADR status=提议待用户签署 |

⚠️ resume 运行 cwd 回落到主仓 D:\Firefly-Canvas（未带 cwd 参数）——总控监控 worker 是否误写主仓主工作树；必要时 cancel + 带 cwd 重派。

## 教训（Wave 1b 派发前适用）

- worker 普遍「探索耗尽预算」：4 个超时票全部无 commit。Wave 1b 起：总控先自己做定向 grep 把 file:line 事实写进任务卡，并在票面写明「探索预算≤N 次工具调用，X 分钟内必须有首个 commit」。
- 新规则：issue-NNN-result.md 必须随分支 commit（#240 的 result 漏 commit，总控从 worktree 抢救）。
- 新规则（Wave1c）：侦察摘要中的方法/路径必须带 grep 命中证据才可写进任务处方（#234 幻觉 API 教训）；resume 任务必含「T+30~35m 无条件先 commit 全部未提交改动」。

## 关键决策记录

- ADR 编号预分配：#250 → 0021；#242 → 0022（避免两文档票抢同一编号）。
- #251/#257/#258 隐性前置 = #250（ops.db schema），已加急派发；票面 Blocked by 待补边。
- smart-canvas.js 六票（234/237/238/251/252/253）串行队列序：234 → 237 → 238 → 252 → 253 → 251。

## Wave 1b 已派发（#240/#242 已合并解锁）

| 泳道 | 票 | 难度/预估 | worktree | 分支 |
|---|---|---|---|---|
| G | #241 TUI confirm fallback 404 | 小 / 40m | D:/ff-wt/wt-241 | issue/241 | del_mvr2ozan_if2l(60m) exit0 | ✅ 已合并，票已关，review PASS（总控复跑 50 passed） |
| H | #243 MCP streamable-HTTP 端点实现 | 中大 | (已删) | (已删) | 三轮 worker（150+70+80m）后总控接管：查明 harness 污染假失败根因+修隔离；全量 1360 passed | ✅ 已合 wave1-live，票已关，review PASS |
| I | #235 provider 未知终态化+RunningHub code+resume not-found | 中 | D:/ff-wt/wt-235 | issue/235 | del_mvrhaem0_73gb(90m，锚点已注入) | 进行中 |
| J | #238 缩放自适应缩略图+视频悬停 | 中小 | D:/ff-wt/wt-238 | issue/238 | del_mvrhaem0_4q7g(75m) | 进行中 |
| K | #263 #234 trial 门证据补挂 | 小 | D:/ff-wt/wt-263 | issue/263 | del_mvre0g3z_2h0k(55m，总控亲跑红测试+处方注入) | 进行中 |
| L | #237 停止语义精修 | 小 | 待派 | 待派 | — | 可派（#243 已合并），下一波 |

合并序纪律：#241 先于 #243（可能同触 confirm 端点区）；#243 开工先读 docs/adr/0022（含已知缺口清单）。

## Wave 1c/1d 状态（刷新 10:4x）

- ⚠️ 主线事实：本地工作线 = wave1-live（原 pipeline/canvas-modularization 改名；main 直系后代 + 8 票合并）；已推 origin 并开 **PR #264**（base main，待人工审阅）。新 worktree 均基于 wave1-live。
- 已关 8 票：#234 #239 #240 #241 #242 #243(刚合) #245 #250。全量在 wave1-live：1360 passed / 1 skipped。
- 在跑 5 条：#235 #238（旧基线）+ #263（del_mvri56q3_93si）+ #244（del_mvri31rv_jp7y）+ #249（del_mvri37ra_h6cb）。
- 待解锁：#237（再押后：与 #244 同触 agent_host/project_sessions）；#236→#254（待 #235）；#252/#253（待 #238）；#251/#257/#258（待 ADR 0021 签署）；#260（待 #259）。
- 人工闸口：**PR #264 审阅**、#233（ready-for-human）、#259（产品决策）、**ADR 0021 签署**；#247/#248 已标 needs-info 并评论索要证据/澄清。
- 排队：#246、#261（与 #249 同抢 tests/test_workflows.py，待 #249 合并）。
