# 审核 issue-250-review.md：#250 ADR 0021 ops.db 每项目 SQLite sidecar

**判定：PASS**（总控全文通读 + 合并 3d3fca4，票已关）

## 逐条对照

| 验收项 | 结果 |
|---|---|
| 编号 0021、格式照 0019/0020（frontmatter status/date） | PASS |
| 决策四要素：每项目租户、路径约定、边界、迁移 | PASS：`<projects_root>/<project_id>/ops.db` 懒创建；租户 key=路径 project_id；租户白名单四个（tasks/undo/versions/usage），新租户须修 ADR |
| schema 归属防抢跑 | PASS：`ops_migrations(key,done_at)` 由 #234 创建持有；表前缀 tasks_*/undo_*/versions_*/usage_* 各票自持；Migration done-row 幂等（照 api/project_import.py:59-76） |
| 与 ADR 0010/0018 边界 | PASS：画布文档保持 JSON，0010 一字不动（Q17=A 依据引用了 #250 评论） |
| 连接不变量 | PASS：WAL+foreign_keys pragma（照 api/db.py:100-105）、按路径缓存连接、每项目锁、单进程前提 main.py:1110 |
| 下游可消费性 | PASS：#234=第一个入住（建基础表+pendingTasks 迁移细则归它）；#251/#257/#258 各自表前缀；.fly 包纳入 ops.db 记为开放项归下游票 |
| status | 「提议（待人工签署）」——**需用户单行改 frontmatter 签署后，#251/#257/#258 才允许建表** |

## 过程备注

- 首派 45m 超时（探索预算耗尽）；resume `del_mvr05qi2_ar4l` 完成。CONTEXT.md 同步 +6/-1（术语表运行数据条目）。
- 亮点：ADR 主动固定了「迁移失败=项目打不开（可见报错）」和「duplicate 后新项目获得全新空 ops.db」两条易漏语义。
