# 审核 issue-234-review.md：#234 取回三层断裂修复

**判定：PASS**（总控接管实现+自审，合并 6feca94，票已关。关键路径解锁）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| 层3 失败路径持久化 upstream_task_id | PASS（:878-883 计算 → failure_record + CANVAS_TASKS 双写；canvas history 重启不丢） |
| 层2 GET 主动取回 | PASS（_maybe_recover_project_image_task：query_upstream_image_task(record_history=False) → archive_project_generated_image → complete_image_generation(require_pending=False) 终态覆盖，recovered:True 标记） |
| 层1 前端语义 | PASS（取回按钮走 GET ?recover=1；recover.status=running→「仍在生成」） |
| stub 首查失败二查成功落画布 | PASS：tests/test_project_image_recover.py 6 passed |
| 重启后取回可用 | PASS（测试 c：纯 store 直写） |
| domain 门 | PASS（130.9s verdict=passed，总控实跑） |
| 全量回归 | 1306 passed, 1 skipped, 618 subtests（术语违例修复后 guard 绿） |
| 浏览器 trial 证据 | **转 #263 独立票** |

## 审查意见

- `complete_image_generation(require_pending=False)` 覆盖 failed→succeeded：确认 store 允许非 pending 终态写入（测试 c 实证）；success_record 字段齐全含 upstream_task_id 闭环。
- provider 下线时构造 `{'id': pid, 'name': ''}` 盲试——窄误判可接受，已在 result 记风险。
- 过程教训：两轮 worker 因侦察摘要含幻觉 API 空转；总控亲验地图后第三派拿到实质进展，最后总控接管收尾（同 #239 模式）。**侦察摘要必须带 grep 证据才可进任务处方。**
