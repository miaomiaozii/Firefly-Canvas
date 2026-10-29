# batch-3 结果报告（#161 P2d 媒体/历史模块）

**状态：完成**（twin 完成搬移 + 冒烟后会话损坏，pytest 回归 + 修复 + 报告由主会话接管）

## 改动文件

| 文件 | 变化 |
|---|---|
| `static/js/canvas-media-history.js` | 新建 2373 行（2256 行纯移动 + 7 行头部注释） |
| `static/js/smart-canvas.js` | 23709 → 21453（−2256 净；phases 数组与 10 行事件绑定留在/回到主脚本） |
| `static/smart-canvas.html` | +1 行 `<script src="/static/js/canvas-media-history.js">`（L546，canvas-connections.js 之后、canvas-undo.js 之前，无 ?v=） |
| `tests/test_project_entry_ui.py` 等 5 个测试文件 | 25 行：读 smart-canvas.js 的断言改为拼接读取 `smart-canvas.js + canvas-media-history.js`（断言意图不变） |

## 搬移内容（~100 函数 + 状态声明）

- **媒体归档**：scheduleMediaArchive、cancelPendingMediaArchives、pendingMediaArchives、ARCHIVE_DELAY_MS、cascadeArchiveDeletedMedia（async）
- **回收站**：formatTrashTime、trashDaysLeft、openSmartTrashPanel、renderSmartTrashItems、openTrashMediaPreview、closeTrashMediaPreview、closeSmartTrashPanel、smartTrashRestoreItem、smartTrashPurgeItem、refreshSmartProjectMediaTrashButton、purgeSmartProjectMediaTrash、smartHistoryIdForDeletedOutput
- **历史面板**：~82 个 smartHistory* 函数（记录合并/时间戳/排序/拖拽/发送回画布/详情视图/抽屉/窗口渲染）+ 状态（smartHistoryDrawerCollapsed 等）
- **标记面板**：getMarkedAssetIds、updateSmartMarkedBadge、setMarkedImage、rebuildMarkedAssetIdsFromNodes、syncMarkedImageButtons、toggleMarkedImage、markedImageItems、markedItemDisplayName、renderSmartMarkedPanel、toggleSmartMarkedPanel
- **未使用媒体面板**：closeSmartUnusedPanel、openSmartUnusedPanel、smartUnusedCleanAll 等
- **媒体 URL/存储**：smartOriginalMediaUrl、smartMediaPreviewUrl、mediaItemForStorage、mediaReferencesForStorage、projectMediaUrl、projectMediaPreviewUrl、hydrateProjectMediaReferences、mediaReferenceSource、reconcileMaterializedMediaReferences、syncLiveProjectMediaReferences
- **历史组节点**：isHistoryGroupNode、cleanHistoryImages、demoteHistoryGroupNode、historyGroupForNode、positionHistoryGroupForNode、ensureHistoryGroupForNode、replaceOutputsToNodeWithHistory

## 留在主脚本（边界决策）

- **GENERATION_PROGRESS_PHASES**（顶层 const 数组，初始化调 tr()）：留主脚本原位置——模块先加载，搬走会改变 i18n 初始化时序（twin 初版搬走 + 加 tr stub 导致数组持原始 key 而非译文，已修复回移）。
- **回收站/未使用面板的 5 处事件绑定**（10 行）：留主脚本原位置（const 声明后）——任务卡纪律"顶层执行代码不搬"；twin 初版搬进模块靠 Window 命名访问（元素 ID 自动成 window 属性）侥幸工作，脆弱，已修复回移。
- **undo 栈操作函数**（stripDeletedMediaFromUndoStacks 等）：batch-1 已归 canvas-undo.js，本批未碰。
- **拖拽事件接线**：核心状态机，留主脚本。
- **reconcileMaterializedMediaReferences / syncLiveProjectMediaReferences**：纯函数，已搬（调用方在主脚本，全局作用域跨文件调用无碍）。

## 关键决策与事故

1. **tr stub 事故（已修）**：twin 为模块内顶层 tr() 调用加了 `if (typeof tr === 'undefined') { function tr(key){return key} }` stub。因 i18n.js 用 document.write 同步注入 bundle，模块执行时真实 tr 的依赖（window.StudioI18n.t）其实已就绪，但 tr 函数本身定义在后加载的主脚本——stub 让顶层数组拿到原始 key。修复：数组回主脚本 + 删 stub。
2. **CRLF 事故（已修）**：twin 用 `python -c` 内嵌字符串替换修测试文件时，替换串的转义换行变成真实换行，test_project_entry_ui.py 7 处 Python 源码断裂。主会话用 chr(10)/chr(92) 构造字符串修复（教训：Windows 下改文件优先 edit/write 工具，不用 python -c 内嵌替换）。
3. **twin 会话损坏（接管）**：batch-3 是最大批（~100 函数），twin 会话涨到 3.5M tokens/203 turns，pytest 阶段触发 compaction 后 resume 两次均 `stopReason "length"` 无输出。主会话接管剩余步骤（pytest + 审核修复 + 报告）。

## 验证结果

| 项 | 结果 |
|---|---|
| node --check 两文件 | PASS |
| 模块顶层零执行代码（括号追踪扫描） | PASS（仅函数定义 + 状态声明） |
| 回插内容（phases/绑定）与原始逐字节一致 | PASS |
| 页面 0 ReferenceError / 0 console error | PASS（Playwright 实测） |
| 36 个抽查函数全局可用 | PASS |
| 回收站/未使用面板按钮绑定生效 | PASS（onclick 非空） |
| GENERATION_PROGRESS_PHASES 持译文 | PASS（["排队","准备参考图",...]） |
| twin 冒烟 12/12（删节点归档/历史面板/回收站/标记/媒体 URL/清理） | PASS |
| 全量 pytest | PASS：1040 passed / 351 subtests（与基线一致） |

## 遗留风险

- 冒烟对"回收站恢复（restore）"只验证了函数可用 + 按钮绑定，未做端到端恢复流程（需真实删除数据，twin 冒烟选择函数级验证）。风险低：smartTrashRestoreItem 纯移动未改。
- 测试文件拼接读取的适配方式：若未来批次继续抽模块，这 5 个测试文件的拼接列表需同步加新模块（或改为读整个 js 目录）。
