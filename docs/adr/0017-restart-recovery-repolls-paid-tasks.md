---
status: accepted
date: 2026-09-10
---

# 服务重启后重轮询已付费任务（不重复提交）

本 ADR 记录 issue #80（生图）/ #82（视频）落地时的决策：服务重启后，对**已提交到在线服务商但尚未完成**的任务，按持久化的 provider task ID **重新轮询原任务**取回结果，而不是终止任务或重新提交。

## 背景

修复前（2026-08-31 生图流程专项审计，`docs/generation-flow-review.md`）：

- 服务重启时，durable 状态为 pending 的任务被 `fail_image_generation` 终止，错误文案「服务重启后无法确认任务是否已调用在线模型；为避免重复计费，任务已终止，请显式重试」（`app/routers/api_project_library.py` 旧 :1098-1100）。
- 根因：**provider 任务 ID 未持久化**，重启后无法向 provider 查询原任务。
- 后果不对称：防重复计费的代价由用户承担——「付了钱，重启就没了」；显式重试 = 新的 provider 调用 = 再付一次钱。

#82 给出三个方案：**a** 持久化 provider task ID + 重启重轮询；**b** 维持现状仅加人工核对标注；**c** wontfix。最终选 **a**（#80 生图、#82 视频同构实现，均已合入 main 并关闭）。

## 决策

1. **提交成功即持久化**：provider 提交返回任务标识后，立即写入内存 registry **和** canvas `pendingTasks`（随快照落盘，durable）。入口：`app/routers/api_project_library.py:1526` 附近；存储辅助：`firefly_projects.py:2137`（「用于服务重启后按 provider task id 重新轮询（避免重复计费，#82）」）。
2. **重启恢复 = 重轮询，绝不重新提交**：pending 任务带 provider task ID → 后台向 provider 查询/轮询原任务（`_resume_project_video_generation`，`app/routers/api_project_library.py:2293`；图片侧同构）。成功 → 归档 + `complete_media_generation` 落账；provider 侧失败 / 任务不存在 / 轮询超时 → 终态 fail，**错误文案必须携带 provider task ID** 供人工查询。
3. **降级路径（都走终态 fail，不静默丢失）**：
   - provider 已从 API 设置移除 → fail，提示「请在 API 设置恢复该服务商后手动重试」；
   - 无 provider task ID（专属适配器，或提交前中断）→ fail，「无法重轮询，请手动重试」（`app/routers/api_project_library.py:2331`）。
4. **并发防双重恢复**：`resuming` 标志在 `CANVAS_TASK_LOCK` 锁内先置位，防并发 GET 双起重启恢复（`app/routers/api_project_library.py:2435` 注释）。
5. **崩溃窗口规则**：crash 发生在「两次快照写之间」时，以终态历史（durable commit 标记）为准——**已付费成功不得合成成重启失败**（`durable_project_video_task` 的 docstring 约定，图片/视频同约定）。
6. **registry 丢失重建**：内存 registry 与 canvas 不一致时，从 canvas 的 durable 记录重建任务状态（`app/routers/api_project_library.py:2371`，「与图片 recover 同构，#80/#82」）。

## 不做什么

- 不自动向 provider 重新提交任务（重复计费风险，用户显式重试才是重新提交）。
- 不自动退款 / 自动客服工单；人工兜底 = 错误文案里的 provider task ID + 手动重试。
- 不为「提交前中断」的任务做恢复（无 provider task ID，本来就没有付费事实）。

## 实现索引

| 位置 | 内容 |
|---|---|
| `app/routers/api_project_library.py:1526` | 提交成功即持久化 provider 任务标识（内存 + canvas pendingTasks） |
| `firefly_projects.py:2137` | durable 持久化辅助（重启后按 provider task id 重轮询） |
| `app/routers/api_project_library.py:2293` | `_resume_project_video_generation`：重启重轮询（视频） |
| `app/routers/api_project_library.py:2371` | registry 丢失后从 canvas 重建任务状态 |
| `app/routers/api_project_library.py:2435` | resuming 锁内标志，防并发双重恢复 |

关联：issue #80、#82；`docs/generation-flow-review.md`。
