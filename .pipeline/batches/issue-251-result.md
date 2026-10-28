# issue-251 结果报告（undo 栈跨刷新持久化：ops.db 租户之二 undo_snapshots）

**状态：完成**（票面要求全部落地：画布级 undo/redo 栈（`canvas-undo.js`，UNDO_LIMIT=40 那套）整栈按项目持久化进工程 ops.db 的 `undo_snapshots` 租户（ADR 0021 additive 迁移 `undo_001`），页面加载时前端拉回、刷新/重启后 undo 连续性恢复；与 #257 `versions_snapshots` 不同概念、不同表（版本树 vs 会话级 undo 连续性），未混表；未碰 app/fal*、app/services/generation.py|media.py、app/agent_tools.py；TDD 全绿 + 全量 pytest 以 origin/wave1-live 基线对照零漂移，见文末验证证据表）

分支 `issue/251`（基于 `origin/wave1-live` @ `a1b1f95`——开工时 wave1-live 头是 `cb2e8f3`，收尾前发现已前移，核对新基线未动本次触及的任何文件后干净 ff 到 `a1b1f95`），HEAD `1a40bd5`，1 commit。已 push 并 assign `miaomiaozii`。

## Commits

| commit | 内容 |
|---|---|
| `1a40bd5` | feat(#251): undo 栈跨刷新持久化——ops.db undo_snapshots 租户（ADR 0021 租户之二）：后端 mixin+迁移+路由 / 前端防抖自动保存+加载恢复 / 棘轮钉值与 ADR 0020 漂移日志同步 / TDD 后端 12 + 前端 6 用例 |

## 票面要求落地

### ① 目标确认：画布级 undo 栈（不是图片编辑器那套）

开工时票面方向里「EDIT_DRAW_HISTORY_MAX 那套」经核对实为 `canvas-image-editor.js` 的**画内笔刷**历史（`editDrawUndoStack`，编辑器会话级、与刷新无关，票面也明说"不是图片编辑器那套 editDraw 历史"）。真目标以 `gh issue view 251` 票面为准：**`static/js/canvas-undo.js` 的画布级 `undoStack`/`redoStack`**（`UNDO_LIMIT = 40`，`buildSavePayload` 不含 undoStack、canvas 加载也不重建 → 刷新即失忆）。

### ② 后端：ops.db 租户机制复用（与 #257 versions 同机制、不同表）

| 文件 | 内容 |
|---|---|
| `firefly_opsdb.py` | additive 迁移 `undo_001`（append 到 `OPS_MIGRATIONS` 注册表末尾，符合 ADR 0021「追加即可新增租户」）：`undo_snapshots(project_id TEXT PRIMARY KEY, undo_json, redo_json, updated_at)`——每项目单行整栈 upsert |
| `firefly_project_undo.py` | `ProjectUndoStackMixin`（158 行）：`save_undo_stack(project_id, undo, redo, strict)` / `load_undo_stack(project_id)`。`_sanitize_entries` 校验形状（list）+ JSON 可序列化，裁剪到 `UNDO_STACK_MAX_ENTRIES = 40`（与前端 `UNDO_LIMIT` 同口径，保留最新 N 条 = 前端 `push → if(>LIMIT) shift` FIFO 语义）；依赖 `ProjectVersionsMixin` 的 `_opsdb` / `_require_project_id`（同库同锁，ADR 0021 每项目锁不变量）；失败模型同 ADR 0021（strict 上抛 / 非 strict 记日志返回 None；无 ops.db = 空栈） |
| `app/routers/api_project_undo.py` | `GET/PUT /api/project-library/{project_id}/undo-stack`（`project_store_call` 模式同 #258 usage 路由）：404 项目不存在（KeyError）/ 400 形状非法（ValueError）/ 500 存储失败（strict=False 返回 None） |
| `firefly_projects.py` + `main.py` | `ProjectStore` 增 `ProjectUndoStackMixin` 基类；路由注册 |

租户隔离 = 每项目独立 ops.db 文件 + project_id 过滤（ADR 0021）；与 `versions_snapshots`（flush 触发、供回滚 diff/restore）是**不同表、不同写入路径**，只共享租户机制。

### ③ 前端：canvas-undo.js 为主体，核心只接线

- **防抖自动保存**（`canvas-undo.js` +55 行）：全部 6 个栈变更点（`pushUndo` / `commitPendingUndo` / `performUndo`（含 projectOriginalDelete token 路径）/ `performRedo` / `stripDeletedMediaFromUndoStacks` / `removeProjectOriginalUndoTokens`）触发 `scheduleUndoStackPersist()`——1.5s 防抖合并连串操作，静默窗口发一条 `PUT`（整栈 `JSON` 深拷贝，防后续变动）。持久化失败（网络/5xx）**静默**（`console.warn`），画布操作不受影响（best-effort，同 usage 遥测口径）。
- **加载恢复**：`smart-canvas.js` `loadCanvas` 成功后 fire-and-forget 调 `restorePersistedUndoStack()`（核心侧净增 3 行，含注释）——GET 拉回整栈种回 `undoStack`/`redoStack` + `syncUndoRedoButtons()`。本地栈非空不覆盖（项目切换保护）；404（无 ops.db = 首次使用）/ 失败静默（回到旧行为，不阻塞渲染）。
- **守卫不变**：恢复的快照走既有 `performUndo`/`performRedo` 路径——悬空媒体守卫（#62 L1/B-5：`stripDeletedMediaFromUndoStacks`）、#146 原件删除 token（`removeProjectOriginalUndoTokens` / `restoreSmartProjectOriginalUndo` 委托）原样生效，恢复不绕任何守卫。
- **范围限定**：仅项目画布（`canvas.project_id` 存在）持久化；classic 画布无 project_id → 零请求（API 是 project-library 作用域）。

### ④ 棘轮同步（ADR 0020 先例）

- `tests/test_canvas_coupling_report.py` 钉值 **246/964/174/811 → 247/965/175/812**（`+1/+1/+1/+1`）：`smart-canvas.js` loadCanvas 接线 `restorePersistedUndoStack()`（1 处「核心调模块」引用），方向非新债（持久化/恢复逻辑全在 canvas-undo.js 内，核心只接线）。钉值注释 + ADR 0020 漂移日志各加一条（owner 分布 `canvas-undo.js` 15→16）。
- `tests/test_firefly_opsdb.py` 迁移序列钉值同步（`undo_001` append 后 applied = `["versions_001", "usage_001", "undo_001"]`，表集合断言补 `undo_snapshots`）——共享机制测试随注册表增长同步，合法。

## 验证证据

| 项 | 结果 |
|---|---|
| TDD 后端 `tests/test_project_undo_stack.py` | **12 passed**（序列化往返 deep-equal / 裁剪 45→40 保留最新 / 租户隔离 A/B 互不串 / 无 save 读空 / 覆盖写 / 项目不存在 KeyError / 形状非法 ValueError（非 list / 项不可序列化）+ TestClient：PUT→GET 往返 / 空 GET / 超限 PUT 裁剪 / 404 / 400） |
| TDD 前端 `tests/canvas_module_units/test-canvas-undo.mjs` | **14 passed**（既有 8 + 新 6：防抖合并单条 PUT 整栈深拷贝 / 网络+5xx 失败吞掉 / 加载恢复种栈+按钮同步 / 本地栈非空零请求不覆盖 / 404 静默按钮对齐 / 非项目画布零网络） |
| JS 单测全集 `tests/canvas_module_units/` 21 文件逐个跑 | **全绿，合计 391 pass**（目录模式 `node --test <dir>` 的失败为既有 quirk，纯基线同样复现，与本次无关） |
| 棘轮 `tests/test_canvas_coupling_report.py` + `tests/test_canvas_core_layer.py` | **11 passed + 19 passed**（新钉值 247/965/175/812 验证通过） |
| `python tools/quality_gate.py fast` | **passed**（verdict=passed，final_exit_code=0，16.8s，证据 `20261029-041221-fast-71488`） |
| `python tools/quality_gate.py domain` | **passed**（verdict=passed，final_exit_code=0，131.1s，浏览器步一次通过，无需重试；证据 `20261029-043306-domain-44396`） |
| 全量 pytest 基线对照（`origin/wave1-live` @ `a1b1f95`，worktree `wt-251-base`） | 基线 **1526 passed, 1 skipped, 628 subtests**（9:05）；本分支 **1538 passed, 1 skipped, 628 subtests**（9:09）。差值 **+12 = 恰好为新增 `test_project_undo_stack.py` 12 用例**，0 failed，subtests 与 skip 完全一致，无回归 |
| 约束核对 | 未触碰 `app/fal*`、`app/services/generation.py`、`app/services/media.py`、`app/agent_tools.py`（`git diff --name-only` 逐一核对） |

## 已知边界

- **恢复发生在画布加载完成后异步 fire-and-forget**：首屏渲染与恢复无竞态（恢复前 undo 按钮保持禁用态；恢复后按钮与栈对齐），但极端快速操作（加载完成后 <1s 内连续 undo）理论上可能先于恢复完成——此时用户操作走内存栈，恢复因「本地栈非空」自动放弃，不覆盖用户现场。
- **持久化 best-effort**：PUT 失败仅 `console.warn`（同 usage 遥测口径）——极端情况下刷新丢失最近 <1.5s 内的栈变更，回到旧行为而非损坏数据。
- 快照 JSON 对后端不透明（前端 schema 为准），后端只校验 list 形状 + 可序列化 + 裁剪；将来快照 schema 变化需前后端同步审视（当前 schema：`{nodes, connections, selectedId, selectedIds, selectedImage, label?}`）。
- 未做 #251 专属浏览器旅程 runner（票面门 = fast + domain，domain 的 `native-browser-evidence` 步一次通过）；端到端「刷新后 Ctrl+Z 回退」链路由 前端 6 用例（恢复种栈）+ 后端 12 用例（往返/隔离/裁剪）+ domain 门浏览器步共同覆盖。
