# batch-3 审核（#161 P2d 媒体/历史模块）

**判定：PASS**（主会话审核 + 主会话修复后复审通过）

## 特殊情况说明

twin 完成搬移 + 冒烟 12/12 后，会话在 pytest 回归阶段损坏（3.5M tokens/203 turns 触发 compaction，resume 两次均 `stopReason "length"` 无输出）。剩余工作（pytest + 审核 + 报告）由主会话接管，接管理由与过程记录在 result.md。

## 逐条对照

| 验收项 | 结果 |
|---|---|
| 语法检查（node --check 两文件） | PASS |
| 新模块函数全局可用（36 个抽查） | PASS（页面实测 typeof=function） |
| HTML `<script>` 位置（canvas-connections.js 之后、canvas-undo.js 之前，无 ?v=） | PASS（L546） |
| 纯位置移动零逻辑改动 | 初版 FAIL → 修复后 PASS（见下） |
| 顶层执行代码不搬进模块（任务卡边界纪律） | 初版 FAIL → 修复后 PASS（见下） |
| 0 ReferenceError / 0 console error | PASS（Playwright 实测 0） |
| 删节点归档无引用媒体（冒烟） | PASS（twin 冒烟 12/12 之一） |
| 历史面板开合/条目渲染（冒烟） | PASS（twin 冒烟 + 主会话函数实测） |
| 回收站开合/条目/恢复（冒烟） | PASS（twin 冒烟 + 主会话按钮绑定实测） |
| 全量回归 1040 passed / 351 subtests | PASS（主会话修复后重跑，与基线一致） |

## 初版问题清单（均由主会话修复并验证）

1. **i18n 行为回归（必须修）**：`GENERATION_PROGRESS_PHASES`（顶层 const 数组，初始化时调用 `tr()` 6 次）被搬进模块。模块先于主脚本加载，twin 为此新增 `tr()` stub——但 stub 返回原始 key，导致数组持有 `["smart.phaseQueued", ...]` 而非译文。页面实测：stub 初始化值 `["smart.phaseQueued","smart.phasePreparingReference","smart.phaseUploading"]` vs 真实 tr 返回 `["排队","准备参考图","上传"]`（i18n bundle 含该 key，lang=zh）。生成进度 UI 会显示原始 key。
   - 修复：数组移回主脚本原位置（`generationProgressBytes` 之前，与原始 L8520 相对位置一致），删除 tr stub。
   - 验证：页面实测数组值 `["排队","准备参考图"]` ✓，与原始文本逐字节一致 ✓。
2. **顶层事件绑定被搬进模块（违反任务卡纪律）**：5 行 `if(xxxBtn) xxxBtn.onclick=...`（trash 3 行 + unused 3 行，共 10 行含注释）被搬进模块。初版能工作是因为 HTML 元素 ID 的 Window 命名访问（`<button id="smartTrashCloseBtn">` 自动成为 `window.smartTrashCloseBtn` 属性，6 个 ID 均在 HTML 中）——但这是脆弱依赖：若将来 HTML 移除某 ID，模块加载即 ReferenceError 且整个模块（100+ 函数）失效；原实现（const=getElementById → null → if 跳过）优雅降级。
   - 修复：10 行绑定移回主脚本原位置（trash const 块后 / `let smartUnusedBusy` 后），与原始文本逐字节一致。
   - 验证：页面实测 `trashCloseBound=true`、`unusedDetectBound=true`，0 错误 ✓。
3. **console.log 加载标记（一致性）**：模块尾部 `console.log("canvas-media-history.js done loading")` 为新增代码（batch-1/2 模块均无）。已删除。

## 修复后最终状态（复审通过）

- 模块 = 2256 行纯位置移动 + 7 行头部注释（新，说明模块边界，与 batch-1/2 头部注释模式一致）。
- diff 对照 HEAD：删除 2266 非空行 = 模块 2256 行 + trash 绑定 7 行 + unused 绑定 3 行（后两组回主脚本）；phases 数组回主脚本。
- 回插内容（phases 9 行、trash 绑定 5 行、unused 绑定 3 行）与原始文件逐字节一致。
- 模块顶层零执行代码（括号追踪扫描确认：仅函数定义 + 状态声明）。
- 测试文件适配：5 个测试文件因"断言字符串在 smart-canvas.js"失败（函数搬走），改为拼接读取 `smart-canvas.js + canvas-media-history.js`（最小改动 25 行，断言意图不变）。其中 test_project_entry_ui.py 曾遭 twin 的 CRLF 事故损坏（7 处字符串断裂），主会话用 chr() 构造替换串修复。
- 全量 pytest：1040 passed / 351 subtests（与基线一致）。

## 审核方法备注（可复用）

- Window 命名访问陷阱：HTML 元素 ID 自动暴露为 window 属性，会掩盖"引用后声明 const"的 ReferenceError。审核抽取类批次时，模块内顶层代码引用主脚本 const 变量不能只看"没报错"，要查 ID 是否在 HTML 中。
- 顶层可执行代码（const 初始化调 tr()、事件绑定）搬进先加载的模块会改变初始化时序——i18n 类回归的通用模式。
