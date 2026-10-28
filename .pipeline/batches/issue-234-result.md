# issue-234-result.md：项目图片任务手动取回三层修复

## 改动清单（commits a72e058 红测试 / a29f61a 实现 / d191006 术语合规）

1. **层3 upstream_task_id 持久化**（app/routers/api_project_library.py:878-883,891,934）：
   `run_project_image_task` 失败分支计算 `getattr(exc,'upstream_task_id','') or extract_task_id_from_text(detail)`，写入 failure_record（进 canvas history，重启不丢）+ CANVAS_TASKS 内存注册表。
2. **durable 透传**（:428-431）：`durable_project_image_task` terminal 分支返回 `upstream_task_id`。
3. **层2 GET 主动取回**（:1183+）：新增 `_maybe_recover_project_image_task`——status ∈ {failed,timeout} 且有 upstream id → `query_upstream_image_task(provider, upstream, record_history=False)`（复用现有引擎）→ succeeded 时 `archive_project_generated_image` 归档项目媒体 + `complete_image_generation(require_pending=False)` 写 succeeded 终态覆盖（record 带 `recovered: True`）→ 更新 CANVAS_TASKS + broadcast_canvas_updated；未出图/出错 → 原快照 + `recover: {status,error}` 诊断。GET 加 `recover: Optional[int]`（默认开，recover=0 关）。
4. **层1 前端**（static/js/canvas-generation-tasks.js:641-664）：取回按钮改走 GET ?recover=1（项目任务），非项目任务保留旧 fetchImageTaskQuery 路径；`recover.status==='running'` 映射为「仍在生成」提示。

## 测试证据

- tests/test_project_image_recover.py：6 passed（层3 持久化 / 层2 stub 首查失败二查成功落画布 / recover=0 不触发 / 重启后 durable 取回）。
- 全量：1306 passed, 1 skipped, 618 subtests（1 术语违例已修 d191006 后 guard+recover 11 passed 复跑确认）。
- node --check canvas-generation-tasks.js：OK。

## 票面验收对照

| 验收 | 结果 |
|---|---|
| stub provider 首查失败二查有成品→取回落画布 | PASS（测试 a） |
| 重启后（记录含 upstream id）取回可用 | PASS（测试 c：纯 store 直写不经内存） |
| quality_gate domain | 总控合并前在 main 复跑 |
| 浏览器取回证据（trial 层） | **遗留**：见风险 |

## 遗留风险

- 浏览器 trial 证据未产出（需真实 provider 或 stub 服务器 + 门框架接线）→ 建议独立小票补门证据。
- provider 已删除时按记录原平台盲试（无 base_url 自然失败），误判面窄但存在。
- RunningHub 取回路径 `save_ai_image_to_output` 落全局 output 再由 `archive_project_generated_image(url)` 归档——两跳，极端情况下第一跳文件可被清理竞态吞掉（低概率，记为 UI 卫生候选）。
