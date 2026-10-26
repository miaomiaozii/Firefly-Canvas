# batch-1 审核（#159 P2b 撤销/重做模块）

**判定：PASS**（修复轮 1 后复审通过）

## 修复轮 1 复审

重跑逐行 diff（HEAD 原段落 vs canvas-undo.js）：3 处标识符 hunk 全部消失，剩余 diff 均为预期/已接受项（未搬的 selection/conversation 状态、媒体归档簇、cascade 函数、crop 状态；7 个空行；兑底→兜底 错别字修正）。冒烟 18/18 重跑通过，node --check 通过。

## 首轮问题清单（已修复）

## 首轮逐条对照

| 验收项 | 结果 |
|---|---|
| 语法检查（node --check 两文件） | PASS |
| 新模块 `canvas-undo.js` 12 函数 + 8 状态全局可用 | PASS |
| HTML `<script>` 位置（canvas-connections.js 之后、smart-canvas.js 之前，无 ?v=） | PASS（L546） |
| 主脚本无重复定义、无残留 | PASS（grep 无匹配） |
| 搬/留清单符合任务卡（媒体归档簇/cascade/crop 状态/selection 状态留主脚本） | PASS |
| restoreSmartProjectOriginalUndo 留主脚本 + result.md 说明 | PASS |
| 浏览器冒烟 18/18（创建/删除/移动 undo/redo、禁删守卫、0 ReferenceError/console error、清理+持久化） | PASS |
| 全量回归 1040 passed / 351 subtests（与基线一致） | PASS |
| **纯位置移动、零逻辑改动（连注释一起搬）** | **FAIL** |

## 问题清单（需修复）

被搬代码中 3 处局部标识符被改名（camelCase → snake_case），违反"纯位置移动"要求，也与代码库约定不符（JS 局部变量/参数用 camelCase，snake_case 是数据字段名）：

1. `static/js/canvas-undo.js` `refreshProjectMediaLibraryItems`（约 L165-168）：
   - `const project_id = ...` → 恢复为 `const projectId = ...`
   - `if(!project_id) return false;` → `if(!projectId) return false;`
   - `` encodeURIComponent(project_id) `` → `` encodeURIComponent(projectId) ``
2. `static/js/canvas-undo.js` `stripDeletedMediaFromUndoStacks`（约 L178-185）：
   - 函数签名参数 `asset_id` → 恢复为 `assetId`
   - `if(!asset_id) return 0;` → `if(!assetId) return 0;`
   - `String(image?.asset_id || '') === asset_id` → `... === assetId`
3. `static/js/canvas-undo.js` `removeProjectOriginalUndoTokens`（约 L196-200）：
   - 函数签名第 3 参数 `project_id` → 恢复为 `projectId`
   - `(token.projectId || project_id) === project_id` → `(token.projectId || projectId) === projectId`

## 接受的偏差（无需修复，记录在案）

- 注释错别字修正：`兑底` → `兜底`（filterDanglingProjectMediaImages 上方注释，原代码笔误，修正零风险，接受）。
- 新增 7 个函数间空行（纯空白，接受）。

## 修复要求

- 只改上述 3 处标识符名，不做任何其他改动。
- 修复后重跑 `node --check` 两文件 + 浏览器冒烟（tmp/qa/qa18-p2b-undo.js）确认 18/18。
- 全量 pytest 不必重跑（纯改名，无逻辑变化；如你判断需要可跑）。
- 更新 `.pipeline/batches/batch-1-result.md`（追加修复说明）。
