# 任务卡 batch-2：#160 画布前端模块化 P2c：抽取节点操作模块

## Issue 原文（验收标准，照抄不改写）

> ## Parent issue
> #154（画布前端单文件模块化拆分）
>
> ## What's wrong
> 节点操作（创建/删除/移动/缩放/重命名，跨 image/prompt/loop/conversation/agent/agent-task/local-edit/smart-group 各类型）与画布脚本其余部分混在一起。
>
> ## What I expected
> 节点操作独立成一个模块；各类型节点的 CRUD + 缩放行为完全不变。
>
> ## Verification（验收）
> - 语法检查通过。
> - 浏览器冒烟：各类型节点创建/删除/移动/缩放/重命名均正常。
>
> ## Additional context
> 共享全局作用域拆分。

## 相关文件/模块提示（主会话已侦察；⚠️ 行号基于 batch-1 之前的 24233 行版本，batch-1 抽走 undo 簇后会偏移，以 grep 复核为准）

**偏移规则（batch-1 后实测）**：undo 簇位于原 L305-540（文件头部），净删 200 行 → 原 L305 之前的行号不变；原 L540 之后的行号一律 −200（如 createNode 7804→~7604）。当前 smart-canvas.js 为 24033 行。

- 主脚本：`static/js/smart-canvas.js`；HTML：`static/smart-canvas.html`（script 区）。新模块 `static/js/canvas-node-ops.js` 的 `<script>` 插在 `canvas-undo.js` 之后、`smart-canvas.js` 之前（沿用 P2a/P2b 模式，无 `?v=` 参数）。
- **已定位的候选函数**（P0 文档估 ~18 个，实际以 grep 为准）：
  - 创建簇（相对集中）：`createNode`(7804)、`createPromptNode`(7821)、`createConversationNode`(7851)、`createAgentNode`(7870)、`createLocalEditNode`(7889)、`createLocalEditNodeFromImage`(7901)、`createLoopNode`(7915)、`createSmartGroupNode`(7924)、`createImageNodeAt`(1750)、`createNodeFromMenu`(22406)
  - 删除：`deleteNode`(14803)、`deleteNodeFromButton`(14866)
  - 移动/缩放/重命名（主会话补充侦察）：**节点拖拽内嵌在全局事件处理器里**（`dragState` 顶层状态 L170；mousedown 设 `dragState` L14707；drag 处理 L14763、L22944 附近；`moveNodeElementsDuringDrag`(8105) 是拖拽中的 DOM 同步函数）——拖拽事件接线属核心状态机，**留主脚本**；可评估搬 `moveNodeElementsDuringDrag` 本身。缩放：画布级 `zoomPanAroundPoint`(15407) 属画布视口（留主脚本）；节点尺寸相关纯辅助 `nodeScale`(1689)/`safeScale`(1685)/`mediaNodeDefaultScale`(1746) 是布局工具（P1 已决定留主脚本的保持不动）。重命名：`renameSmartNodeImage`(14972, async，节点内图片重命名)、`renamePromptTemplateGroup`(5277) 需评估归属
- **边界纪律**（P0 文档 + P2a 经验）：
  - 只抽「节点操作」语义清晰的函数；与渲染/同步核心状态机（`render`/`syncCanvas`/`scheduleSave` 等）深度纠缠的混合函数**留主脚本**，在 result.md 说明理由。
  - 创建函数内部调用 `pushUndo`（batch-1 已搬去 canvas-undo.js）——共享全局作用域下跨模块调用无碍，不用改。
  - 顶层执行代码（DOM 引用、事件绑定）不搬进模块。
- 排除项参考：连线相关（P2a 已搬 canvas-connections.js）、undo/redo（batch-1 已搬 canvas-undo.js）、媒体归档（P2d 领域，本批不碰）。

## 执行要求

1. 先 grep 精确识别全部节点操作函数（创建/删除/移动/缩放/重命名 × 各类型），逐一核对函数体引用的主作用域标识符，确定最终搬/留清单。
2. 搬移 = 纯位置移动，**零逻辑改动**（连注释一起搬）。
3. HTML 加 `<script src="/static/js/canvas-node-ops.js">`（位置见上）。
4. 验证（全部必须做）：
   - `node --check` 两个文件；
   - grep 确认被搬函数在主脚本无重复定义、无残留引用问题；
   - 浏览器冒烟（Playwright 无头，Chrome：`C:/Program Files/Google/Chrome/Application/chrome.exe`；参考 `tmp/qa/qa17-p2a-extract.js` 骨架；右键菜单 `.create-card[data-create-type="..."]` 可创建各类型节点）：
     - 各类型节点（至少 image/prompt/loop/conversation/agent/local-edit/smart-group）创建正常；
     - 删除节点（含 #65 生成中禁删守卫路径不回归）；
     - 拖拽移动节点（位置正确落位）；
     - 缩放节点（尺寸正确）；
     - 重命名（若有重命名入口）；
     - 0 个 ReferenceError、0 个 console error；
     - 清理测试数据后显式 `await manualSaveCanvas()` 持久化。
   - 全量回归：`python -m pytest tests/`（基线 1040 passed / 351 subtests）。
5. **不要提交 git**（提交由主会话审核后执行）。

## 输出要求

完成后把自报结果写进 `.pipeline/batches/batch-2-result.md`：
- 最终搬/留函数清单（含排除理由）；
- 改了什么（文件、行数变化）；
- 为什么（关键决策）；
- 验证结果（每项 PASS/FAIL + 关键输出）；
- 遗留风险。
