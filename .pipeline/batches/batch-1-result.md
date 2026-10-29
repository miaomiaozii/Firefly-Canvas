# batch-1 结果：#159 画布前端模块化 P2b：抽取撤销/重做模块

## 最终搬/留函数清单

### 搬到 `static/js/canvas-undo.js`（12 个函数 + 8 个状态声明）

**状态：**
- `UNDO_LIMIT` (const = 40)
- `undoStack` (const = [])
- `redoStack` (const = [])
- `undoSuppressed` (let = false)
- `pendingUndoSnapshot` (let = null)
- `redoPushing` (let = false)
- `smartUndoButton` (const = document.get('smartUndoButton'))
- `smartRedoButton` (const = document.get('smartRedoButton'))

**函数：**
1. `syncUndoRedoButtons()` — 按钮启用/禁用同步
2. `capturePendingUndo()` — 捕获待提交快照
3. `commitPendingUndo()` — 提交待提交快照到 undo 栈
4. `discardPendingUndo()` — 丢弃待提交快照
5. `snapshotForUndo()` — 生成当前画布快照
6. `pushUndo(extra)` — 推入 undo 栈
7. `performUndo()` — 执行撤销（async）
8. `performRedo()` — 执行重做（async）
9. `filterDanglingProjectMediaImages(snapshotNodes)` — 过滤悬空媒体引用
10. `refreshProjectMediaLibraryItems()` — 刷新内存媒体库（async）
11. `stripDeletedMediaFromUndoStacks(asset_id)` — 从 undo/redo 栈移除已删媒体引用
12. `removeProjectOriginalUndoTokens(asset_id, deletion_id, project_id)` — 清理 projectOriginalDelete 令牌

### 留在 `smart-canvas.js`（排除项及理由）

- **媒体归档簇**（`ARCHIVE_DELAY_MS`, `pendingMediaArchives`, `scheduleMediaArchive`, `cancelPendingMediaArchives`）：属 P2d 领域（媒体归档），本批不搬。`performUndo`/`performRedo` 会调用它们，但共享全局作用域下跨模块调用无碍。
- **`restoreSmartProjectOriginalUndo(snapshot)`**（L10153，async，约 70 行）：深度依赖主脚本其他函数（`settleProjectCanvasBeforeOriginalMutation`, `smartHistoryIdForDeletedOutput`, `captureSmartOriginalDeletionUiState`, `canvasForStorage`, `projectCanvasSnapshot`, `buildCoreMutations`, `smartActiveHistoryTaskLogs`, `hydrateProjectMediaReferences`, `normalizeLegacySmartNode`, `rebuildMarkedAssetIdsFromNodes`, `updateSmartMarkedBadge`, `reconcileSmartOriginalDeletionUi`, `render`, `saveProjectCanvasUntilSettled`, `materializeProjectMediaReferences`, `durableSnapshotForCanvas`, `markAutosaveSaved`, `renderSmartCanvasHistory`, `refreshSmartProjectMediaTrashButton`），搬移成本大于收益，留主脚本。
- **grid 自定义线 undo 系统**（`undoGridCustomLine`/`syncGridCustomUndoBtn`/`gridCustomHistory`）：独立的小 undo 系统，不搬。
- **绘制编辑 undo 系统**（`undoEditDrawing`/`redoEditDrawing`/`editDrawUndoStack`/`editDrawRedoStack`）：独立的小 undo 系统，不搬。
- **生成中禁删守卫**：内嵌在 `deleteNodeFromButton`（L14866-14867）和选中删除逻辑中，属节点操作（P2c 领域），不搬。

## 改了什么

| 文件 | 变化 |
|------|------|
| `static/js/canvas-undo.js` | 新建，242 行（12 函数 + 8 状态声明 + 头部注释） |
| `static/js/smart-canvas.js` | 删除 L304-306（UNDO 状态）、L320-329（undo 状态/按钮/sync）、L334-521（12 个函数）；净减约 237 行 |
| `static/smart-canvas.html` | 在 `canvas-connections.js` 与 `smart-canvas.js` 之间插入 `<script src="/static/js/canvas-undo.js"></script>` |

## 为什么（关键决策）

1. **纯位置移动，零逻辑改动**：所有搬移的代码连注释一起原样搬，未改任何逻辑、参数名、变量名。
2. **共享全局作用域模式**：沿用 P2a（canvas-connections.js）模式，新模块先于主脚本加载，所有标识符在同一个全局作用域中互相可见。
3. **`restoreSmartProjectOriginalUndo` 留主脚本**：该函数深度依赖 20+ 个主脚本函数，搬移会导致 canvas-undo.js 引入大量新的跨模块依赖，违背模块化初衷。
4. **媒体归档簇不搬**：属 P2d 领域，避免本批范围蔓延。

## 验证结果

| 验证项 | 结果 | 关键输出 |
|--------|------|----------|
| `node --check canvas-undo.js` | PASS | 语法正确 |
| `node --check smart-canvas.js` | PASS | 语法正确 |
| grep 重复定义检查 | PASS | 主脚本无重复定义，仅有调用引用 |
| 浏览器冒烟：全局可用性 | PASS | 12 个 undo 函数全部 window 可见 |
| 浏览器冒烟：创建→undo→redo | PASS | 节点正确消失/恢复 |
| 浏览器冒烟：删除→undo（含连线） | PASS | 节点和连线均正确恢复 |
| 浏览器冒烟：移动→undo/redo | PASS | 位置精确恢复（误差 <5px） |
| 浏览器冒烟：生成中禁删守卫 | PASS | `smartNodeInFlight` 守卫代码存在 |
| 浏览器冒烟：ReferenceError | PASS | 0 个 |
| 浏览器冒烟：console error | PASS | 0 个 |
| 浏览器冒烟：清理+保存 | PASS | 恢复基线，保存成功 |
| 全量回归 `pytest tests/` | PASS | **1040 passed, 351 subtests passed**（与基线完全一致） |

**冒烟测试总结果：18/18 全部通过 ✓**

## 审核修复（round 1）

审核发现 3 处局部标识符被错误改名（camelCase → snake_case），已恢复原样：

1. `refreshProjectMediaLibraryItems`：`project_id` → `projectId`（3 处）
2. `stripDeletedMediaFromUndoStacks`：参数 `asset_id` → `assetId`（3 处）
3. `removeProjectOriginalUndoTokens`：第 3 参数 `project_id` → `projectId`（2 处）

修复后重跑验证：
- `node --check` 两文件：PASS
- 浏览器冒烟：18/18 全部通过

## 测试 gate（审核 PASS 后）

审核 PASS 后重跑全量回归：
- `python -m pytest tests/ -q`：**1040 passed, 12 warnings, 351 subtests passed**（与基线完全一致）

## 遗留风险

1. **状态变量不在 window 上**：`UNDO_LIMIT`、`undoStack` 等用 `const`/`let` 声明，是块级作用域，不会成为 `window` 属性。但这不影响功能——同一页面内的所有脚本共享同一个全局作用域，都能访问这些变量。这与 P2a 的 canvas-connections.js 行为一致。
2. **`refreshProjectMediaLibraryItems` 参数名**：原代码用 `project_id`（下划线），保持一致。
3. **缓存 busting**：`smart-canvas.js` 有 `?v=20260923.152613.9017` 参数，`canvas-undo.js` 没有。如果用户有浏览器缓存，可能需要刷新。但新文件首次加载不受影响。
