# issue-250 结果报告（ADR 0021：运行数据走每项目 SQLite sidecar（ops.db），画布文档保持 JSON）

> 分支 `issue/250`（基于 `pipeline/canvas-modularization`）。**未 push、未关票、未合并**（工作规则 1）。

## 状态：完成（草案，待人工签署）

- commit：`78b2cca` — `docs(adr): 0021 运行数据每项目 SQLite sidecar (#250)`（2 files changed, +85/−1）
- ADR：`docs/adr/0021-per-project-sqlite-sidecar-ops-db.md`（front matter `status: 提议（待人工签署）`，`date: 2026-10-28`，沿用 0017/0018 的 YAML status/date 惯例）
- 领域词条同步（票面验收标准）：`CONTEXT.md` 新增「运行数据 sidecar」词条 + 「项目目录」词条补入 sidecar；术语守卫 `tests/test_terminology_guard.py` 使用精选 FORBIDDEN_TERMS 列表，新增 _Avoid_ 词条不自动生效，零破坏（已跑门验证）。

## 关键决策点（ADR 正文）

1. **每项目租户 key**：`<projects_root>/<project_id>/ops.db`（root = ADR 0003 项目库，`firefly_projects.py:103` `default_projects_root()`，默认 `D:\Firefly-Canvas-Projects`）；租户 key 即路径里的 project_id，库本身单租户、表内不带冗余 project/user 列；不建中央库，不在项目库根下新增数据目录。
2. **零新依赖**：标准库 `sqlite3` 直连；不引入 ComfyArt 所用的 SQLAlchemy，也不引入 Alembic/ORM。
3. **连接不变量**：每次连接 `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`（照 ComfyArt `api/db.py:100-105`）；连接按项目（路径）缓存；跨线程访问用每项目锁串行化（沿用 `firefly_projects.py:234` `_project_operation_lock`）；前提是单进程（`main.py:1110` 单例 store）。
4. **懒创建**：`ops.db` 不随 `create_project`（`firefly_projects.py:580-618`）生成，首次有租户写入才建；存量项目无 ops.db，读取方必须容忍缺失。
5. **租户白名单（只有四个）**：tasks（#234 第一个入住）/ undo 栈（#251）/ 版本树（#257）/ LLM 用量（#258）；新增租户 = 修订本 ADR。
6. **schema 归属**：共享基础表 `ops_migrations(key TEXT PRIMARY KEY, done_at INTEGER)` DDL 由 ADR 固定、#234 创建持有；租户表 DDL 归各持有票，表名前缀固定：`tasks_*`/`undo_*`/`versions_*`/`usage_*`；迁移走 `Migration(key)` done-row 幂等模式（照 `api/project_import.py:59-76`），迁移失败 = 项目无法打开（可见报错，不静默重建）。
7. **画布文档不迁移**：canvas.json/project.json/history.json 保持 JSON；ADR 0010 一字不动（其「不引入 SQLite」仅约束画布文档，Q17=A 用户签署的解释）、ADR 0018 文档恢复语义不变。
8. **边界焊死**：节点/连线永不进库；媒体永不进 BLOB（ops.db 只按 asset ID 引用媒体）。

## 后果（ADR 正文）

- **备份**：WAL 三件套 `ops.db`+`ops.db-wal`+`ops.db-shm` 随项目目录整体备份/移动/删除；损坏不可恢复时按 ADR 0018 命名惯例隔离为 `ops.db.corrupt-<epoch_ms>` + 重建空库 + 界面提示（细节归 #234）；ops.db 不做 `.bak` 轮转（WAL 已保证崩溃不损坏文件，与 canvas.json 的两级损坏模型不同）。
- **并发写**：单进程前提；WAL = 读不阻写、写全局串行；同库多线程由每项目锁串行；跨进程并发写不在本 ADR 范围。
- **项目复制**：`duplicate_project` 语义不变（剥 `runtime_keys`，`firefly_projects.py:655-658`），新项目获全新空 ops.db。
- **Fly 包（ADR 0011）**：`firefly-package-v1` 暂不含 ops.db（导入端 `fixed_files` 白名单 `firefly_projects.py:1007`，纳入需包格式升版）——**记为开放项，下游票处理**；期间 .fly 重导入后运行数据从零开始，画布/媒体不受影响。
- **ADR 0017 边界**：tasks 入住后 provider 任务标识宿主从 canvas pendingTasks 迁到 tasks 租户；「提交成功即持久化、重启重轮询绝不重新提交」语义不变；存量 pendingTasks 双写/迁移细则归 #234。#234 落地前零兼容成本。

## 现状证据（调研核实，全部 file:line）

| 主张 | 证据 |
|---|---|
| 1.2s 节流全量快照 | `firefly_projects.py:218`（`snapshot_delay: float = 1.2`）、`firefly_projects.py:3341`（`threading.Timer(..., self.flush, ...)`） |
| flush 整文件重写三份 JSON | `firefly_projects.py:3002`；canvas `:3012`（`keep_backup=True, backup_count=3`）、history `:3019`、project `:3024` |
| 任务生命周期随画布快照落盘（#234 死因之一） | `firefly_projects.py:2282` `attach_media_generation_provider_task`（docstring 明载「持久化到节点的 pendingTasks 条目……避免重复计费，#82」，ADR 0017 机制） |
| 运行状态长在画布节点上 | `firefly_projects.py:655-658` `runtime_keys`（duplicate_project 剥离清单） |
| 项目目录布局（ops.db 将加入处） | `firefly_projects.py:580-618` `create_project`：media/previews/trash + 四份 JSON |
| 项目库根/单例 store | `firefly_projects.py:103-105`；`main.py:1110` `FIREFLY_PROJECT_STORE = ProjectStore()` |
| Fly 包导出/导入白名单 | `firefly_projects.py:738`（`json_files` 仅四份 JSON）、`firefly_projects.py:1007-1008`（`fixed_files` 校验） |
| 文档恢复现状（边界不动） | `firefly_projects.py:3245` `_load_canvas_with_recovery`、`:1201-1208` project.json .bak 回退（ADR 0018） |
| ComfyArt 参考手法 | `docs/reference-comfyart-canvas.md` §60（`api/db.py` WAL+外键 pragma、engine 按 path 缓存、`comfyart_migrations` done 表）；`api/project_store.py:80-90`（document JSON 进 SQLite 信封）、`api/project_import.py:59-76`（Migration(key) done-row）——引自 #250 票面已签 spec |

## 验收核对

- `docs/adr/0021-*.md` 起草完成：✅ `docs/adr/0021-per-project-sqlite-sidecar-ops-db.md`（获维护者批准合并 = 人工签署，票面状态即草案，未越权）。
- `CONTEXT.md` 领域词条同步：✅（新增「运行数据 sidecar」+ 更新「项目目录」）。
- `python tools/quality_gate.py domain` 通过：✅ **最终 `verdict: passed, final_exit_code: 0`**（evidence `20261028-023035-domain-66424`，7 步全绿：javascript-syntax / surface-contract / project-contract / provider-contract / native-browser-evidence / generation-lifecycle-evidence / egress-attribution）。
  - 过程记录：提交前跑过一次 passed（`20261028-015425-domain-15764`）；提交后立即重跑那次 failed，根因是门的 `worktree-preservation` 守卫——我在门运行期间编辑了 CONTEXT.md（脏工作区变化竞态，first_failure=`worktree-preservation`，7 个实质步骤本身全绿）；工作区干净后重跑即 passed，非产品问题。

## 偏离 / 阻塞

- 无阻塞。无代码改动（纯 docs：1 个新 ADR + CONTEXT.md 2 处词条），不触碰任何运行时行为。
- 票面一处票号漂移已按实际修正：#250 票面写「undo 栈 #250、版本树 #256、用量遥测 #257」，当前实际下游票为 **#251（undo 持久化）、#257（版本树）、#258（用量遥测）**（`gh issue view` 核实），ADR 一律使用当前票号；#234 保持。
- ADR 内明确记录的开放项（不属于本票范围，交下游票）：ops.db 进 .fly 包需 `firefly-package-v1` 升版 + 清单声明；ops.db 损坏隔离的界面提示细节（#234）；存量 pendingTasks 双写/迁移细则（#234）。
