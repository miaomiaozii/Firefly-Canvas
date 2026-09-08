# 生成（生图/生视频）流程审计报告

日期：2026-08-30
状态：只读审计结论，修复项已建 issue（#79-#83）。与 `docs/deletion-mechanism-review.md`（删除机制审计）互为补充。
**第 1 期已完成（2026-08-31）**：#83（历史 outputs 累加合并）与 #79（孤儿进回收区 + 通用恢复端点）已落地，单测 792 OK + e2e 44/44 无回归。#80/#81/#82-a 待第 2 期。

审计范围：任务生命周期（提交→运行→失败/取消/超时/重启恢复）在前后端的完整链路。

## 1. 现状矩阵（已核实行号，app/routers/api_project_library.py 记为 api）

| 环节 | 图片任务 | 视频任务 |
|---|---|---|
| 提交 | POST /image-generation-tasks（api:1931），task_id 前缀 `project_img_`（:1979） | POST 视频任务路由 |
| 查询 | GET /{task_id}（api:1687） | 同 |
| 取消 | POST /{task_id}/cancel（api:1721） | **无路由** |
| 重试 | POST /{task_id}/retry（api:1865） | 前端走重新提交 |
| 任务级硬超时 | 有：`asyncio.wait_for`（api:1266），默认 1800s（`image_generation_task_timeout_seconds()`，app/core.py:95-104，env `FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS`），超时 status=`timeout`（api:1346/:1430） | **无**（`run_project_video_task` api:1529+ 直接 await，可挂到服务商侧任意久） |
| 失败记录 | 历史带已归档图的 outputs（「取回兜底」，api:1431 附近）+ per-candidate 部分提交（partial_canvas） | 同（但已下载部分无 outputs 回写） |
| 取消记录 | **`'outputs': []`（api:1767）**→ 已下载图进孤儿清理被静默硬删（#79） | 无取消 |
| 重启恢复 | durable pending → fail，错误「服务重启后无法确认任务是否已调用在线模型；为避免重复计费，任务已终止，请显式重试」（api:1098-1100）；provider 任务 ID 未持久化，无法免费重查（#82） | 同 |
| 前端取消/重试按钮 | **仅历史抽屉**：smart-canvas.js:9979-9980（`data-log-cancel-task` / `data-log-retry-task`），handler :10309-10317 → `controlProjectImageTask`；渲染条件 `projectTask = String(taskId).startsWith('project_img_')`（:9882） | **无按钮**（前缀判定排除） |
| 取回兜底 | `retryFailedGeneration`（:21102）：先 GET 任务取回已归档图（toast「已取回 n 张」），失败才用新幂等键 `regen-${Date.now()}-...` 重提交；抽屉失败条目带 salvage 缩略图（:9946-9953） | 无 |

## 2. 问题清单

### G0（P0，最高优先）多张并发生成历史只留 1 张 —— issue #83 ✅ 已修复（第 1 期）
- 现象：4 张生 3 张卡 1 张 → 历史条目永远只有 1 张（节点上有 3 张）；取消后 1 张也没了。用户原话：「生图历史里面永远只有第一张，不管你取消任务或者别的都是这样」。
- 根因（两处叠加）：api:1312 partial 记录 `outputs` 只带当前刚归档的一张；`firefly_projects.py:2388-2392` store 历史合并是**纯替换**（删同 id + 追加），outputs 不累加——而同函数的节点 images 是累加的（:2317-2345），只有历史记录漏了。
- 修复：`complete_media_generation` 同 history_id 记录改为 outputs 累加合并（按 asset_id 去重、完成顺序追加、可变字段以新记录为准）。一个修复同时覆盖 #79 的记录层丢图（历史引用 asset → 不算孤儿 → 不被静默硬删）。
- 验证：单测 n=4 三张 partial 后卡住 → outputs=3；cancel → outputs 仍=3 且文件未删；e2e 节点图数=历史 outputs 数。

### G1（P0）取消丢图 —— issue #79 ✅ 已修复（第 1 期）
- 现象：付费生成 → 取消 → 已下载好的图被静默硬删，无任何痕迹。
- 根因链：取消路由历史记录 `'outputs': []`（api:1767）→ `run_project_image_task` 的 CancelledError 分支把已下载项注册为孤儿（api:1412-1415）→ `cleanup_project_generation_orphans`（api:1622-1634）→ `delete_media_if_unreferenced`（firefly_projects_cleanup.py:446）静默硬删。
- 不对称：失败路径反而把已归档图写进 outputs 可取回；取消路径直接抹掉。
- 修复：cancelled 历史记录的 outputs 与失败路径对齐（写已归档项）；孤儿清理改为写入媒体回收区（7 天可恢复）而非静默硬删。

### G2（P1）视频任务三无 —— issue #80
- 无取消路由、无硬超时、抽屉按钮前缀判定排除视频。
- 修复：视频补硬超时（与图片同机制）+ 取消路由（`cancel_requested` 标志同构）；前端按钮判定改为按任务类型而非 id 前缀。

### G3（P1）30 分钟干等 —— issue #81
- 超时默认 1800s 偏长；节点本体无取消按钮（埋在历史抽屉）；无「已等待时长」反馈。
- 修复：节点运行态加取消按钮（调用现有 cancel 路由）；运行态显示已用时长；评估默认超时下调（如 600s）或按服务商类型分档。

### G4（P1）重启丢付费任务 —— issue #82
- 服务端重启时 durable pending 任务一律 fail，错误文案要求「显式重试」（=重新付费）；provider 侧任务 ID 未持久化，无法免费重查结果。
- 方案（待拍板）：
  - a：提交成功即持久化 provider 任务 ID，重启后先按 ID 重轮询，取不到再标记 fail；
  - b：只把 provider 任务 ID 写进历史/日志，供人工向服务商查询，代码不做自动重查；
  - c：不改（wontfix），维持「重启即终止、重新重试」。

## 3. 对用户感知问题的定性

| 用户反馈 | 定性 |
|---|---|
| 「取消单独生图状态的按钮没有」 | 半属实：图片任务有取消按钮但只在历史抽屉（且文案入口隐蔽）；视频任务完全没有；节点本体无按钮 |
| 「重试或者图片链接恢复」 | 半属实：图片任务有重试（含已归档图取回），仅抽屉内；视频无 |
| 「生成历史不会同步」 | **属实（#83）**：用户指批量生图 4 张生 3 张卡 1 张时，历史条目永远只有 1 张（partial 写回 outputs 不累加，store 纯替换，api:1312 + firefly_projects.py:2388-2392） |
| 「一直卡着」 | 属实：图片最长干等 30 分钟且无节点级取消、无时长反馈；视频理论上可无限挂起 |

## 4. 决策与分期

- **D3 ✅（2026-08-30 确认）**：取消/失败记录 outputs 写回已归档项（#83 的 store 合并统一解决）+ 孤儿清理进媒体回收区而非静默硬删。
- **#82 → 方案 a（2026-08-30 拍板）**：提交成功即持久化 provider 任务 ID，重启后先按 ID 重轮询，取不到再 fail；落地时新增 ADR。

分期：
- 第 1 期（bug 修复，并入删除修复第 1 期）：**#83（历史 outputs 累加合并）** ✅、#79（取消丢图，记录层由 #83 覆盖，剩余=孤儿清理进回收区）✅——均已完成（2026-08-31）：`_merge_generation_outputs`/`_node_owns_generated_output` helper、`complete_media_generation` 同 id 合并 + T4 回填改按 asset_id、`_end_media_generation` 终态合并（replaced_prior_id）、`trash_unreferenced_generation_media` + `restore_trash_media` + `POST /trash/media/restore`；新增 7 单测。
- 第 2 期（功能补齐，未开始）：#80（视频取消/超时/按钮）、#81（节点取消按钮 + 时长反馈 + 超时评估）、#82-a（provider ID 持久化 + 重启重轮询 + ADR）。

## 5. 关联

- 删除侧：#71（P1-9 生成孤儿静默硬删）与 G1 同根因（`delete_media_if_unreferenced` 静默硬删），修复时应一起改。
- ADR：`docs/adr/0014`（项目级原件删除边界）、`0015`（单资产删除）适用；#82 若选 a 需新增 ADR 记录「重启重轮询」策略。
