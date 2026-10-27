---
status: 提议（待人工签署）
date: 2026-10-28
---

# 运行数据走每项目 SQLite sidecar（ops.db），画布文档保持 JSON

本 ADR 记录 issue #250 的决策（spec #233 追加决议，拷问 Q17=A 用户签署）：任务、undo 栈、版本树、LLM 用量四类**运行数据**统一存入每项目一个的 SQLite sidecar `ops.db`；**画布文档保持 JSON 文件，ADR 0010 一字不动**。这是下游 #234（任务持久化）、#251（undo 栈）、#257（版本树）、#258（用量遥测）的共同前置。

## 背景

现状病灶——运行数据骑在画布保存血管上：

- 任务生命周期随画布全量快照落盘：`firefly_projects.py:218` `snapshot_delay=1.2` 秒节流，`firefly_projects.py:3341` `threading.Timer(self.snapshot_delay, self.flush, ...)` 触发 `flush()`（`firefly_projects.py:3002`）整文件重写 `canvas.json`（`firefly_projects.py:3012`，`keep_backup=True, backup_count=3`）、`history.json`（`firefly_projects.py:3019`）、`project.json`（`firefly_projects.py:3024`）。
- provider 任务标识随快照持久化：`attach_media_generation_provider_task`（`firefly_projects.py:2282`，docstring「把服务商侧任务标识（task_id/提交地址等）持久化到节点的 pendingTasks 条目……用于服务重启后按 provider task id 重新轮询（避免重复计费，#82）」）——ADR 0017 的落盘载体是 canvas 节点的 `pendingTasks`，这是 #234 死因之一。
- 运行状态已长在画布节点上：`duplicate_project` 复制项目时必须逐个剥离 `runtime_keys`（`firefly_projects.py:655-658`：`pending`/`running`/`queued`/`pendingTasks`/`jimengPending`/`runStartedAt`/`runFinishedAt`/`runElapsedMs`/…）。
- 待建四件事（任务持久化 #234、undo 栈 #251、版本树 #257、用量遥测 #258）各自要求一个小型存储；对 JSON 文档打四个补丁 = 四套轮子，且每套都加重 1.2s 全量快照的负担。

参考产品 ComfyArt 解剖（`docs/reference-comfyart-canvas.md` §60）：其画布文档本身也是 JSON 大对象存进 SQLite 信封（`api/project_store.py:80-90` 复制的是 document JSON），信封真正承载的是文档之外的运行数据。照抄的两处手法：

- `api/db.py:100-105`：连接时注入 `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`，engine 按路径缓存（§60.3 第 4 条：「如果我们用 SQLite，这两行必须加」）；
- `api/project_import.py:59-76`：`Migration(key)` done-row 标记，迁移幂等（§60.2 `comfyart_migrations(user, name)` done 表同构）。

拷问 Q17=A（用户签署，#250 评论 2026-10-27）：ADR 0010 的「不引入 SQLite」仅约束**画布文档**存储，不约束应用其余部分。因此 ADR 0010 一字不动，本 ADR 管辖其之外的运行数据存储。

## 决策

1. **每项目一个 sidecar（每项目租户模型）**：每个项目目录新增 `ops.db`，路径约定 `<projects_root>/<project_id>/ops.db`（root 即 ADR 0003 的项目库，`firefly_projects.py:103` `default_projects_root()`，默认 `D:\Firefly-Canvas-Projects`，可用 `FIREFLY_PROJECTS_ROOT` 覆盖）。**租户 key 是路径里的 project_id**：库本身单租户，表内不携带冗余 project/user 列；跨项目隔离在文件层完成。不建中央库/全局库，不在项目库根下新增任何数据目录。
2. **标准库 `sqlite3`，零新依赖**：ComfyArt 用 SQLAlchemy，我们不引入（也不引入 ORM/迁移框架如 Alembic），直接用标准库 `sqlite3` 模块。
3. **连接不变量**：
   - 每次新建连接执行 `PRAGMA journal_mode=WAL`（读写不互阻）与 `PRAGMA foreign_keys=ON`（SQLite 默认关外键，必须显式开，照 `api/db.py:100-105`）；
   - 连接按项目（路径）缓存：一个项目一个连接，进程内按路径复用（对照 ComfyArt `_engines` 按 path 缓存）；
   - 跨线程访问同一库用每项目锁串行化（沿用 `firefly_projects.py:234` `_project_operation_lock` 语义）；
   - 前提是当前单进程实例（`main.py:1110` `FIREFLY_PROJECT_STORE = ProjectStore()` 单例）；
   - **懒创建**：`ops.db` 不随 `create_project` 生成（`firefly_projects.py:580-618` 的项目布局不变），首次有租户写入时才建文件；存量项目磁盘上没有 `ops.db`，读取方必须容忍缺失（缺失 = 该租户空数据）。
4. **租户白名单（只有四个，边界焊死）**：
   | 租户 | 内容 | 持有票 |
   |---|---|---|
   | tasks | 生成任务生命周期、provider 任务标识、重启可恢复任务 | #234（第一个入住） |
   | undo 栈 | 画布 undo/redo 栈持久化（刷新不丢） | #251 |
   | 版本树 | 版本快照、回滚前 diff 确认、KEEP_VERSIONS 保留策略 | #257 |
   | LLM 用量 | 逐次调用的 usage/成本记录（normalize_usage/merge_usage） | #258 |
   新增租户 = 修订本 ADR，下游票无权自行加入。
5. **schema 归属（哪张票建哪些表）**：
   - 共享基础表 `ops_migrations(key TEXT PRIMARY KEY, done_at INTEGER)`：DDL 由本 ADR 固定，由第一个入住的票（#234）创建并持有；其余票不得改动其 DDL。
   - 各租户表 DDL 由持有票在其票内固定，表名必须用固定前缀，且只能在自己的前缀下加迁移 key 建新表，不得触碰其他租户的表：
     | 租户 | 表前缀（持有票创建） |
     |---|---|
     | tasks | `tasks_*`（#234） |
     | undo | `undo_*`（#251） |
     | 版本树 | `versions_*`（#257） |
     | LLM 用量 | `usage_*`（#258） |
   - 迁移统一走 `Migration(key)` done-row 模式（照 `api/project_import.py:59-76`）：打开库时按序执行未 done 的迁移，已 done 的 key 不重跑（幂等）；**迁移失败 = 该项目无法打开**（可见报错，不静默重建、不丢数据）。
6. **画布文档不迁移**：`canvas.json` / `project.json` / `history.json` 保持 JSON 文件，继续走 ADR 0010 的增量传输 + 节流全量快照 + 原子写与 ADR 0018 的轮转备份/恢复语义。本 ADR 不修订 ADR 0010（其「不引入 SQLite」约束画布文档存储）与 ADR 0018（文档恢复语义）一字。

## 不做什么

- **节点/连线永不进库**——画布文档保持 JSON（ADR 0010 边界）。
- **媒体永不进 BLOB**——媒体保持外置文件，画布节点只存项目媒体资源 ID（ADR 0003/0010）；ops.db 对媒体只按 asset ID 引用。
- 不建中央/全局数据库，不在项目库根下新增数据目录。
- 不给 ops.db 做文件级 `.bak` 轮转备份（崩溃一致性已由 WAL 覆盖，见后果）。
- 不引入标准库之外的依赖（SQLAlchemy/Alembic/ORM 一律不进）。
- 不新增租户（白名单四个，见决策 4）。

## 后果

- **备份**：WAL 模式下 `ops.db` 与 `ops.db-wal`、`ops.db-shm` 是一组，项目目录的备份/移动/删除必须三者一并处理。打开时若 `ops.db` 损坏且不可恢复 → 按 ADR 0018 的命名惯例隔离为 `ops.db.corrupt-<epoch_ms>` + 重建空库 + 界面提示（细节由 #234 落地）。ops.db 不提供 `.bak` 轮转：WAL 已保证崩溃不损坏文件（最坏丢未提交的最后一个事务），与 `canvas.json` 的 primary + .bak 两级损坏模型不同。
- **迁移**：`ops_migrations` done-row，打开库时执行、幂等；只做前向迁移（加表/加列/加索引），不做破坏性迁移；某租户的表结构演进只落在该租户前缀下。
- **并发写**：前提是单进程本地应用（`main.py:1110` 单例 store）；WAL = 读不阻写、写全局串行；同库多线程访问由每项目锁串行化；不存在跨进程并发写场景，若将来出现则依赖 SQLite WAL 单写者锁（超出本 ADR 范围，需另行讨论）。
- **项目复制（duplicate）**：`duplicate_project` 语义不变——剥离 `runtime_keys`（`firefly_projects.py:655-658`），新项目获得全新空 ops.db，运行数据不随复制带走。
- **显式移动**：项目目录整体移动时 ops.db 随目录文件级迁移，无额外逻辑。
- **Fly 包（ADR 0011）**：`firefly-package-v1` 暂不含 ops.db——导入端有 `fixed_files` 白名单（`firefly_projects.py:1007`），纳入 ops.db 需要包格式升版 + 清单声明，**记为开放项，由下游票处理**；期间 .fly 重新导入后的新项目运行数据（任务/undo/版本/用量）从零开始，画布文档与媒体不受影响。
- **ADR 0017 边界**：tasks 入住后，provider 任务标识的持久化宿主从「canvas pendingTasks 随快照落盘」迁到 tasks 租户；「提交成功即持久化、重启 = 重轮询绝不重新提交、错误文案必带 provider task ID」（ADR 0017）的语义不变；存量 `pendingTasks` 数据的双写/迁移细则由 #234 决定。
- **过渡期**：#234 落地前，任务生命周期继续按 ADR 0017 机制随 canvas 快照持久化；ops.db 此时不存在（懒创建），零兼容成本。

## 关联

- issue #250（本票，起草本 ADR）；#233（spec，Q17=A 追加决议）；下游：#234、#251、#257、#258。
- ADR 0003（项目拥有自己的数据）、0010（画布文档 JSON，边界不动）、0011（.fly 项目包）、0017（重启重轮询已付费任务）、0018（项目文档恢复语义）。
- `docs/reference-comfyart-canvas.md` §60（ComfyArt 数据库层解剖：WAL/外键 pragma、按路径缓存 engine、done-row 迁移表）。
