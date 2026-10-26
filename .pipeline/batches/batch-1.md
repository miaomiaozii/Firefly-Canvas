# 任务卡 batch-1：#159 画布前端模块化 P2b：抽取撤销/重做模块

## Issue 原文（验收标准，照抄不改写）

> ## Parent issue
> #154（画布前端单文件模块化拆分）
>
> ## What's wrong
> 撤销/重做（undo 栈 push、undo、redo、生成中禁删守卫）与画布脚本其余部分混在一起。
>
> ## What I expected
> 撤销/重做独立成一个模块；各操作的 undo/redo 行为完全不变。
>
> ## Verification（验收）
> - 语法检查通过。
> - 浏览器冒烟：创建/删除/移动/缩放后 undo/redo 均正确恢复；生成中节点禁删守卫仍生效。
>
> ## Additional context
> 共享全局作用域拆分。

## 相关文件/模块提示（主会话已侦察，行号为当前 24233 行版本，以 grep 复核为准）

- 主脚本：`static/js/smart-canvas.js`；HTML：`static/smart-canvas.html`（script 区 L543-547：`project-canvas-mutations.js` → `canvas-layout-utils.js` → `canvas-connections.js` → `smart-canvas.js`）。新模块 `static/js/canvas-undo.js` 的 `<script>` 插在 `canvas-connections.js` 之后、`smart-canvas.js` 之前（沿用 P2a 模式，无 `?v=` 参数）。
- **核心簇集中在 L305-540**（同一区域，便于整段搬）：
  - 状态：`UNDO_LIMIT`(L305)、`undoStack`(L305)、`redoStack`(L306)、`undoSuppressed`/`pendingUndoSnapshot`/`redoPushing`(L323-325)
  - 顶层 DOM 引用：`smartUndoButton`/`smartRedoButton`(L326-327) —— **注意：这是顶层执行代码**。按钮元素在 HTML body 内、script 在 body 末尾，加载时 DOM 已就绪，可搬；但按钮的 `addEventListener`(L24209-24210) 与键盘绑定(L23286-23291) 留主脚本。
  - 函数：`syncUndoRedoButtons`(328)、`capturePendingUndo`(346)、`commitPendingUndo`(347)、`discardPendingUndo`(354)、`snapshotForUndo`(355)、`pushUndo`(364)、`performUndo`(374)、`performRedo`(419)、`filterDanglingProjectMediaImages`(~474)、`refreshProjectMediaLibraryItems`(~497)、`stripDeletedMediaFromUndoStacks`(503)、`removeProjectOriginalUndoTokens`(521)
- **同区混入的媒体归档函数**（P2d 领域，本批**不搬**，留主脚本）：`ARCHIVE_DELAY_MS`(L311)、`pendingMediaArchives`(L312)、`scheduleMediaArchive`(313)、`cancelPendingMediaArchives`(321)。`performUndo`/`performRedo` 会调用它们——共享全局作用域下跨模块调用无碍（函数声明提升，调用发生在运行期）。
- **undo 流程的远端函数**：`restoreSmartProjectOriginalUndo`(L10153，async，处理 `projectOriginalDelete` 令牌)——属于 undo 簇，评估是否搬（若它深度依赖主脚本其他函数则留主脚本，在 result.md 说明）。
- **排除项（各自独立的小 undo 系统，不搬）**：grid 自定义线（`undoGridCustomLine`/`syncGridCustomUndoBtn`/`gridCustomHistory` ~L16309-16311）、绘制编辑（`undoEditDrawing`/`redoEditDrawing` ~L15790-15796、`editDrawUndoStack`）。
- **「生成中禁删守卫」不是独立函数**：它内嵌在 `deleteNodeFromButton`(L14866-14867，#65 注释) 与选中删除逻辑(~L23297) 里，属节点操作（P2c 领域），本批不搬——验收只要求守卫行为仍生效（冒烟验证）。
- 调用点（留主脚本，不动）：键盘 Ctrl+Z/Y(L23286-23291)、按钮 click(L24209-24210)、各操作函数内的 `pushUndo(...)` 调用（全文件多处，不动）。

## 执行要求

1. 先精确核对每个候选函数体引用的主作用域标识符，确定最终搬/留清单（混合函数留主脚本）。
2. 搬移 = 纯位置移动，**零逻辑改动**（连注释一起搬）。
3. HTML 加 `<script src="/static/js/canvas-undo.js">`（位置见上）。
4. 验证（全部必须做）：
   - `node --check` 两个文件；
   - grep 确认被搬函数在主脚本无重复定义、无残留引用问题；
   - 浏览器冒烟（Playwright 无头，Chrome：`C:/Program Files/Google/Chrome/Application/chrome.exe`；参考 `tmp/qa/qa17-p2a-extract.js` 的骨架）：
     - 创建节点 → undo → 节点消失；redo → 恢复；
     - 删除节点 → undo 恢复（含连线）；
     - 移动/缩放节点 → undo/redo 位置/尺寸正确恢复；
     - 生成中节点禁删守卫仍生效（若无生成中节点，验证守卫代码路径存在且逻辑未变即可，在 result.md 说明）；
     - 0 个 ReferenceError、0 个 console error；
     - 清理测试数据后显式 `await manualSaveCanvas()` 持久化。
   - 全量回归：`python -m pytest tests/`（基线 1040 passed / 351 subtests）。
5. **不要提交 git**（提交由主会话审核后执行）。

## 输出要求

完成后把自报结果写进 `.pipeline/batches/batch-1-result.md`：
- 最终搬/留函数清单（含排除理由）；
- 改了什么（文件、行数变化）；
- 为什么（关键决策）；
- 验证结果（每项 PASS/FAIL + 关键输出）；
- 遗留风险。
