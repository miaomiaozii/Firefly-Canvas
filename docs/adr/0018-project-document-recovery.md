---
status: accepted
date: 2026-09-10
---

# 项目文档轮转备份与恢复入口（.bak 轮转 N=3 + 恢复 API + 前端向导）

本 ADR 记录 issue #94 的决策：项目核心文档（`canvas.json` / `project.json`）损坏时，用户提供**可见的恢复路径**，而不是静默拿到空画布（随后自动保存会覆盖损坏文件，数据不可逆丢失）。

## 背景

- 现状已有**单份**备份：`flush()`（`firefly_projects.py:2760`）写 `canvas.json` 时 `keep_backup=True` 产生 `canvas.json.bak`（上一版本）；`_load_canvas_with_recovery`（`firefly_projects.py:2883`）在 primary 损坏时自动回退 `.bak` 并重写 primary。
- 缺口：
  1. **无轮转**——只有 1 份备份。两次连续损坏写（或一次磁盘事故同时命中 primary 与 .bak）→ `ProjectStoreError("画布文件和备份均不可读取")`，用户零恢复路径。
  2. **`project.json`（项目元数据）无任何备份**（`keep_backup=False`，`firefly_projects.py:457`/`flush` 内）——元数据损坏 → `open_project` 抛裸 `ValueError` → API 500 → 前端静默。
  3. **前端静默吞错**——`loadCanvas()`（`static/js/smart-canvas.js:7254`）对 `!res.ok` 直接 `return`：用户看到空画布，无提示、无入口。空画布一旦编辑，自动保存就会把损坏文件**覆盖**为空白。
  4. `history.json` 损坏 → 静默 `[]`（`_load_history`，`firefly_projects.py:2911`）——历史是运行审计日志，可接受，保持现状（本 ADR 明确记录该丢失容忍度）。

## 决策

1. **轮转范围**：`canvas.json` 保留 N=3 份轮转备份：`canvas.json.bak`（最新，既有自动回退入口，命名不变）+ `canvas.json.bak-2` + `canvas.json.bak-3`。`project.json` 保留单份 `project.json.bak`（文件极小、变化频率低，单份足够）。
2. **轮转时机**：每次 `flush()` 写 `canvas.json`（`keep_backup=True` 路径）时同步左移：`bak-3` 删除 ← `bak-2`→`bak-3` ← `bak`→`bak-2` ← primary→`bak`，全部经既有 `_write_json_atomic` 同款的 tmp+`os.replace` 原子替换。**同步执行**（canvas 为 MB 级、媒体全部外置按 `asset_id` 引用），不引入异步备份线程；性能代价 = 既有 .bak 拷贝 + 最多 2 次额外文件拷贝。
3. **静默自动恢复（扩展既有）**：`_load_canvas_with_recovery` 的回退链从 `.bak` 扩展为 `.bak` → `.bak-2` → `.bak-3`（任一份可解析且 `id`/`project_id` 与项目一致即提升为 primary 并重写）。`open_project` 对 `project.json` 增加同构回退：primary 不可读 → `.bak` → 重写 primary；全部失败才抛 `ProjectStoreError`。**自动恢复无需用户参与**，与既有 `.bak` 回退行为一致。
4. **恢复 API（用户参与，自动恢复无效时）**——路由前缀跟随前端实际使用的 `/api/canvases/{pid}` 域（issue 建议的 `/api/project-library/{pid}` 语义等价，不新增平行域）：
   - `GET /api/canvases/{pid}/recovery/backups` → 每个可恢复文件（canvas.json、project.json）的 primary 与全部备份副本：`{name, size, mtime, sha256, valid}`（`valid` = 可解析为 JSON 对象；canvas 备份另校验 id 一致）。
   - `POST /api/canvases/{pid}/recovery/restore` body `{file, backup?}`（`backup` 缺省 = 最新有效副本）→ 校验目标备份 → **当前 primary 隔离命名为 `<file>.corrupt-<epoch_ms>`（绝不直接覆盖）** → 备份原子恢复为 primary → 清除 `self._canvases`/`self._saved_revisions` 内存缓存 → 返回恢复结果。走 `_project_operation_lock` 串行化。
5. **前端恢复向导（阻塞式）**：`loadCanvas()` 失败（`!res.ok`）时不再静默 `return`——拉取 backups 列表并弹向导模态：列出文件 × 备份副本（时间/大小/有效性）+「恢复」按钮；恢复成功后 `location.reload()` 重新进入项目。**「暂不恢复」= 保持阻塞警示层，不进入画布编辑、不启用自动保存**——绝不允许用户对着空画布编辑后自动保存覆盖损坏文件。**自动回退标记**：后端自动回退不能对前端透明（否则损坏不可见，违背“用户显式选择恢复目标”的本意）——`open_project` 在发生回退后于返回的 canvas 拷贝上挂 `_recovery: {file, restored_from, at}`（内存标记 `_recovery_marks`，不入磁盘，`_project_canvas_for_persistence` 显式剥离）；前端检测到该标记时同样打开阻塞向导，并在提示中说明“已自动回退到「备份 N」的副本展示”。标记在成功 `flush`（canvas）或 `restore_from_backup`（对应文件）后清除，避免陈旧提示。
6. **丢失容忍度（明确记录）**：与既有 `.bak` 相同——崩溃发生在两次 `flush` 之间时，最后一次保存可能丢失；轮转不扩大既有丢失窗口。`history.json` 损坏仍静默降级为空历史（不恢复、不备份）。
7. **恢复端点不校验登录/项目权限**以外的新边界：与同域其它 `/api/canvases/{pid}` 端点一致（本地应用，单用户）。

## 验收（#94）

- e2e：人为写坏 `canvas.json` + `project.json` → 打开项目不崩（API 明确错误）→ 向导列出备份 → 恢复 → 画布内容与所选备份一致；损坏文件被隔离为 `.corrupt-<ts>`。
- 自动恢复链：primary 坏 → 用 `.bak`；primary+.bak 坏 → 用 `.bak-2`（单测覆盖）。
- 「暂不恢复」路径不产生任何保存写（单测/浏览器验证）。
- 全量测试（基线 800）绿。

## 相关文件

- `firefly_projects.py`：`_write_json_atomic` / `flush` / `_load_canvas_with_recovery` / `open_project`
- `app/routers/api_canvases.py`：恢复端点（新）
- `static/js/smart-canvas.js`：`loadCanvas` 失败分支 → 恢复向导
- `static/smart-canvas.html` + `static/css/smart-canvas.css`：向导模态
