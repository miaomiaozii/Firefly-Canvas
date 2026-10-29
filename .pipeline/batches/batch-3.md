# 任务卡 batch-3：#161 画布前端模块化 P2d：抽取媒体/历史模块

## Issue 原文（验收标准，照抄不改写）

> ## Parent issue
> #154（画布前端单文件模块化拆分）
>
> ## What's wrong
> 媒体/历史管理（删除时级联归档无引用媒体、历史面板、回收站）与画布脚本其余部分混在一起。
>
> ## What I expected
> 媒体/历史逻辑独立成一个模块；删除归档媒体 + 历史面板行为完全不变。
>
> ## Verification（验收）
> - 语法检查通过。
> - 浏览器冒烟：删节点归档无引用媒体、历史面板开合、恢复均正常。
>
> ## Additional context
> 共享全局作用域拆分。

## 相关文件/模块提示（主会话已侦察；⚠️ 行号基于 batch-1 之前的 24233 行版本，前两批抽走函数后会偏移，以 grep 复核为准）

- 主脚本：`static/js/smart-canvas.js`；HTML：`static/smart-canvas.html`（script 区）。新模块 `static/js/canvas-media-history.js` 的 `<script>` 插在 `canvas-node-ops.js` 之后、`smart-canvas.js` 之前（沿用模式，无 `?v=` 参数）。
- **这是 P2 最大的簇**（P0 文档估 ~157 个函数；当前文件 grep `^function` 匹配 media/history/trash/archive 模式的有 ~148 个）。预计工作量最大，**允许跨多次 resume 完成**：每次会话结束前把「已完成/未完成清单」写进 result.md，下次 resume 从清单续做。
- **已定位的候选函数**：
  - 媒体归档：`scheduleMediaArchive`(313)、`cancelPendingMediaArchives`(321)、`pendingMediaArchives`(312)、`ARCHIVE_DELAY_MS`(311)（batch-1 会留在主脚本，本批搬走）、`cascadeArchiveDeletedMedia`(7024, async)
  - 回收站面板簇（相对集中 L6563-6750）：`formatTrashTime`(6563)、`trashDaysLeft`(6570)、`renderSmartTrashItems`(6592)、`openTrashMediaPreview`(6696)、`closeTrashMediaPreview`(6739)、`closeSmartTrashPanel`(6747) 等
  - 历史面板（主会话补充侦察）：~82 个 history 函数，分布广：`smartHistory*` 簇 L5623-9485（记录合并/时间戳/排序/拖拽/发送回画布等）、历史抽屉 `setupSmartHistoryDrawer`(10974)、`toggleSmartHistoryDrawerCollapsed`(10934)、`updateSmartHistoryDrawerCollapseUi`(10924)；另有关联面板：未使用媒体面板（`closeSmartUnusedPanel` 6880 附近）、标记面板（`renderSmartMarkedPanel` 8759、`toggleSmartMarkedPanel` 8789）
  - 媒体 URL/存储簇（L791-1218）：`smartOriginalMediaUrl`(791)、`smartMediaPreviewUrl`(804)、`mediaItemForStorage`(1040)、`mediaReferencesForStorage`(1057)、`projectMediaUrl`(1067)、`projectMediaPreviewUrl`(1071)、`hydrateProjectMediaReferences`(1074)、`mediaReferenceSource`(1184)、`reconcileMaterializedMediaReferences`(1191)、`syncLiveProjectMediaReferences`(1218)——注意后两个疑似保存流程的一部分，需核对调用方再定搬/留
- **边界纪律**：
  - undo 栈操作函数（`stripDeletedMediaFromUndoStacks`、`removeProjectOriginalUndoTokens` 等）batch-1 已归入 canvas-undo.js，本批**不碰**。
  - 与渲染/同步核心状态机深度纠缠的混合函数留主脚本，result.md 说明理由。
  - 顶层执行代码（DOM 引用、事件绑定）不搬进模块。
  - 媒体库 API 调用（`/api/project-library/...`）相关纯媒体函数可搬；若某函数同时是核心保存/加载流程的一部分，留主脚本。

## 执行要求

1. 先 grep 精确识别全部媒体/历史/回收站函数，逐一核对函数体引用的主作用域标识符，确定最终搬/留清单（清单先写进 result.md 再动手搬）。
2. 搬移 = 纯位置移动，**零逻辑改动**（连注释一起搬）。
3. HTML 加 `<script src="/static/js/canvas-media-history.js">`（位置见上）。
4. 验证（全部必须做；若分批完成，验证在最后一批完成后做）：
   - `node --check` 两个文件；
   - grep 确认被搬函数在主脚本无重复定义、无残留引用问题；
   - 浏览器冒烟（Playwright 无头，Chrome：`C:/Program Files/Google/Chrome/Application/chrome.exe`；参考 `tmp/qa/qa17-p2a-extract.js` 骨架）：
     - 删节点后无引用媒体被归档（#146 延迟归档逻辑：2.5s 窗口内 undo 可取消归档）；
     - 历史面板开合正常、条目渲染正常；
     - 回收站开合、条目渲染、恢复（restore）正常；
     - 0 个 ReferenceError、0 个 console error；
     - 清理测试数据后显式 `await manualSaveCanvas()` 持久化。
   - 全量回归：`python -m pytest tests/`（基线 1040 passed / 351 subtests）。
5. **不要提交 git**（提交由主会话审核后执行）。

## 输出要求

完成后把自报结果写进 `.pipeline/batches/batch-3-result.md`：
- 最终搬/留函数清单（含排除理由）；
- 改了什么（文件、行数变化）；
- 为什么（关键决策）；
- 验证结果（每项 PASS/FAIL + 关键输出）；
- 遗留风险；
- 若跨多次 resume：每次更新「已完成/未完成清单」。
