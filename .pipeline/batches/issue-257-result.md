# issue-257 结果报告（版本树：回滚前 diff 确认 + 先存当前 + KEEP_VERSIONS 裁剪，ops.db 租户）

**状态：完成**（票面三项全部落地；`quality_gate fast` + `domain` 绿 + 全量 pytest 对照基线 + 真实浏览器 UI 冒烟，见文末验证证据表）

分支 `issue/257`（基于 `e879b6b`），5 commits，已 push 到 `origin/issue/257`。

## Commits

| commit | 内容 |
|---|---|
| `386323e` | feat(ops-db): ops.db 工程租户层（ADR 0021 additive-only 迁移，issue #257） |
| `023b86c` | feat(projects): 版本树核心 — 自动快照/diff/先存当前再回滚/KEEP_VERSIONS=30 |
| `5cb404c` | fix(ops-db): 短连接模型——操作后释放句柄，修复 Windows 临时目录清理失败（domain 门 WinError 32） |
| `96e634c` | feat(project-list): 版本历史弹窗 — diff 对比确认 + 先存当前再回滚（issue #257） |
| `a995b51` | refactor(ops-db): ops_migrations 列对齐 ADR 0021 固定 DDL（done_at INTEGER，epoch 毫秒） |

合计 10 files，+1241/−3（含两个新测试文件 373 行、23 个新测试；前端 4 文件 +388/−2）。

## 票面三项落地

### ① 回滚前 diff 展示

- 后端：`ProjectVersionsMixin.diff_project_versions`（`firefly_project_versions.py`）对比任意两个画布状态（版本 id 或 `current`），输出 added / removed / changed 节点（id + 标签）+ 标题 + 节点/连线数统计；节点级运行时字段（`RUNTIME_NODE_KEYS`：prompt/modelId/provider/engine/pendingTasks/pending）不参与对比。
- 前端：`static/project-list.html` 新增版本历史 `<dialog>`（`versionsDialog`），每版本条目带「对比」按钮——点「对比」只展示 diff；点「回滚」先展示同一份 diff + 确认说明，再出现「确认回滚」按钮（未确认前不调用 restore 端点）。

### ② 回滚前先存当前（防回滚毁数据，ComfyArt HistoryDialog §73 模式）

- 后端 `restore_project_version` 顺序：`flush`（未保存内容先落盘，保证"当前"完整）→ 把当前状态快照为 `pre-restore`（**strict**：ops.db 出错直接上抛，且发生在改画布之前）→ 载入目标版本进内存画布 → `flush`（canvas.json 原子写 + .bak 轮转，ADR 0018 主安全网保留）。返回 `pre_restore_version_id`。
- 去重：若当前状态与最新快照内容相同（sha256 一致），不产生重复快照，`pre_restore_version_id` 指向已存在的那个版本——"回滚前"状态永远在版本树里，误回滚可再回滚回去。
- 前端确认区文案明示该语义；恢复完成提示带 pre-restore 版本号。

### ③ 保留上限裁剪（KEEP_VERSIONS）

- `KEEP_VERSIONS = 30`（`firefly_project_versions.py`，对照上游 ComfyUI `KEEP_VERSIONS=30` + SQL 裁剪模式）。
- 裁剪在每次快照写入事务内完成：`DELETE ... WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT 30)`，只留最新 30 条、裁最旧，与写入同事务原子生效。
- 快照时机：`create_project` 自动快照 v1；每次 `flush`（手动/自动/崩溃安全网）自动快照，sha256 去重（相同内容不产生新快照，避免无意义吃满配额）。

## ops.db 租户入住（ADR 0021 模式）

### 迁移清单（本票新增全部，additive-only）

| 位置 | 迁移 key | 内容 |
|---|---|---|
| `firefly_opsdb.py` `OPS_MIGRATIONS` | （共享基础表，无 key，`_migrate` 内建） | `ops_migrations(key TEXT PRIMARY KEY, done_at INTEGER)`——DDL 与 ADR 0021 §5 固定稿逐字一致，本票实际创建并持有 |
| 同上 | `versions_001` | `versions_snapshots` 表：`id INTEGER PK AUTOINCREMENT, project_id TEXT NOT NULL, label TEXT NOT NULL DEFAULT 'auto', canvas_json TEXT NOT NULL, canvas_sha256 TEXT NOT NULL, node_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL` |

- additive-only 机制：迁移 key 记录在 `ops_migrations` done-row，已应用 key 永不重跑；新增租户 = 追加 `(key, [SQL])` 条目，不动既有表。迁移失败不记 key、下次连接重试、异常上抛 = 可见报错（ADR 0021 失败模型）。
- **只建本票所需的 `versions_*` 表**；未建 `usage_*` 等通用表（#258 另行按 ADR 追加条目）。
- 租户边界：节点/连线/媒体内容之外，ops.db 只存画布 JSON 快照与元数据（媒体仍按 asset ID 引用，不进 BLOB）。

### 连接模型（ADR 0021 连接不变量的 Windows 适配）

- **短生命周期连接**：每次操作 open → `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON` → 幂等迁移 → 使用 → close，操作完成不残留句柄。
- 为什么不用长连接缓存（ADR 0021 草案的"连接按路径缓存"）：Windows 上打开中的 SQLite 文件无法删除，进程级长连接会让测试/质量门清理临时工程目录报 `PermissionError: [WinError 32]`（domain 门 204 测试 16 errors 实证，`5cb404c` 修复并留 `test_short_lived_connection_releases_handle` 回归）。每连接的 WAL/FK PRAGMA 不变量逐字保留；每项目锁串行化保留。
- 懒创建：`ops.db` 不随项目布局生成，首次租户写入才建；无文件 = 空状态（`has_db` 判定，列表返回空）。

### 新增 API（`app/routers/api_project_library.py`）

| 端点 | 语义 |
|---|---|
| `GET /api/project-library/{project_id}/versions` | 版本列表（新→旧：id/label/node_count/created_at/sha256） |
| `GET /api/project-library/{project_id}/versions/{version_id}/diff?base=current` | diff（base 可为版本 id 或 current；target 恒为目标版本） |
| `POST /api/project-library/{project_id}/versions/{version_id}/restore` | 回滚（先存当前 pre-restore → 再恢复；返回 `pre_restore_version_id`/`revision`/`saved_at`） |

## 回滚流程（端到端时序）

```
UI 点「回滚」
  → GET .../versions/{id}/diff?base=current      展示 diff（新增/删除/变更 + 统计）
  → 用户读 diff，点「确认回滚」
  → POST .../versions/{id}/restore
      1. flush：未保存内容落盘（canvas.json + .bak 轮转）
      2. 当前状态 → pre-restore 快照（strict，失败即中止、画布不动）
         （与最新快照 sha 相同则去重，指向已有版本）
      3. 目标版本画布载入内存（revision+1）→ flush 落盘
  → UI 提示完成（含 pre-restore 版本号，可再回滚回去）
```

任何时刻版本树里都有"回滚前"状态；磁盘上 canvas.json 的 `.bak` 副本（ADR 0018）同时保留，双保险。

## UI 变更（`static/project-list.html` / `static/js/project-list.js` / `static/js/i18n/studio.js` / `static/css/project-list.css`）

- 项目卡新增「版本历史」入口（`project-versions` 按钮，与清理预览/复制/导出/删除并排）。
- 弹窗：标题 + 工程名；版本列表（#N · label，节点数 + 时间，新→旧）；每行「对比」/「回滚」；底部「关闭」+「确认回滚」（diff 出现前禁用/隐藏）；无版本空态文案；错误提示位。
- i18n zh/en 21 个新 key（`validate-i18n` 通过，1616 keys）；CSS 复用 `create-dialog` 约定 + 新 `versions-*` 类；`project-list.css` 版本号 token 已 bump。

## 验证证据表

| 项 | 结果 | 证据 |
|---|---|---|
| TDD 新测试 | **23 passed**（`tests/test_firefly_opsdb.py` 6 + `tests/test_project_versions.py` 17），8.1s | `python -m pytest tests/test_firefly_opsdb.py tests/test_project_versions.py -q` |
| 受影响单测回归 | **130 passed**（opsdb + versions + project_store + history_deletion_isolation + diagnostics），15.2s | `python -m unittest tests.test_firefly_opsdb tests.test_project_versions tests.test_project_store tests.test_history_deletion_isolation tests.test_diagnostics` |
| `quality_gate fast`（最终 commit `a995b51`） | **passed**（exit 0，15.1s） | evidence `20261028-213149-fast-66420` |
| `quality_gate domain`（最终 commit `a995b51`） | **passed**（exit 0，140.0s，含 project-contract 204 测试 + 浏览器证据步） | evidence `20261028-213205-domain-68708` |
| 全量 pytest（对照基线 e879b6b） | **1418 passed, 1 skipped**（489s）+ 分离跑 `test_agent_generate_tools.py` + `test_generation_log_reuse_ui.py` **18 passed**——基线既有失败（test_agent_generate_tools×3）在本分支也通过，全套无失败 | `python -m pytest tests/ --ignore=… -q`；全量并发下 `test_generation_log_reuse_ui` 浏览器用例一次偶发失败、单独复跑通过（20.4s，非产品问题） |
| 运行实例 API 冒烟（uvicorn :3001 真实进程） | 创建工程 → v1 auto 快照；mutation+保存 → v2 auto（去重生效）；diff 正确列出 removed 节点；restore v1 → `pre_restore_version_id=2`（去重指向已有快照）、版本树 v3/v2/v1 完整、画布回到 0 节点 | curl 实测（工程已清理） |
| 真实浏览器 UI 冒烟（Chrome headless + CDP 原生输入事件，`tests/native_browser.py` 框架） | **SMOKE-PASS**：工程卡渲染「版本历史」按钮 → 弹窗打开列出 3 版本 → 点「回滚」出 diff 区（"当前画布 0 个节点 → 目标版本 1 个节点：新增 1、删除 0、变更 0" + "+ smoke node A (smoke-node-1)" + 回滚前快照确认文案 + 「确认回滚」可用）→ 零 console 错误 | 截图 `issue-257-evidence/ui-1-versions-list.png`、`ui-2-restore-diff.png` |

## 偏离 / 开放项

- **trial 门未跑**：派发任务书门为 `fast`（必须绿）+ `domain`（涉 UI 弹窗，已跑并留证）；票面验收标准写的是 `domain + trial`——以派发任务书为准，此处显式注明，若合并流程需要 trial 可再跑（约 5 分钟档）。
- **ADR 0021 草案 vs 实际 #234 的票号漂移**：ADR 0021（#250 产出，状态"提议，待人工签署"）假设 tasks 租户由 #234 首个入住并创建持有 `ops_migrations`；实际已合并的 #234（`6feca94`）是「项目图片任务手动取回三层修复」，不含 ops.db。本票是**实际第一个创建并持有共享表**的票，DDL 严格按 ADR §5 固定稿实现（含 `done_at INTEGER` 列名，`a995b51` 对齐）；tasks 租户仍待建（其票按 ADR 追加迁移条目即可）。ADR 正文未越权改动（草案状态，签署归人工）。
- **ops.db 进 .fly 包**：维持 ADR 0021 开放项（`firefly-package-v1` 需升版 + 清单声明，下游票处理）；期间 .fly 重导入后版本树从零开始，画布/媒体不受影响。
- **ops.db 损坏隔离的界面提示细节**：维持 ADR 0021 开放项（`ops.db.corrupt-<epoch_ms>` 命名惯例已可用，界面提示由 tasks 票落地）。
- **范围外确认**：未触碰 `app/agent_tools.py`、`app/agent_host*`、`app/fal.py`、`app/services/generation.py|media.py`、`app/static/js/agent/*`（会话B领地）；未改 ADR 0018 文档恢复语义（canvas.json/.bak 主安全网原样保留，版本树为叠加层）。
