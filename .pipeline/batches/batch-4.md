# 任务卡 batch-4：#162 画布前端模块化 P3：转 ES module

> 本卡已由主会话细化（2026-09-27）。batch-4 拆成 3 个子批 **4a → 4b → 4c**，每子批独立可验证、系统始终处于可运行+全绿状态。当前派发 **4a**。

## Issue 原文（验收标准，照抄不改写）

> ## Parent issue
> #154（画布前端单文件模块化拆分）
>
> ## What's wrong
> feature 模块拆分后（仍共享全局作用域），画布前端可进一步转为真正的 ES module 以获得模块隔离。
>
> ## What I expected
> 画布前端用 `type="module"` + import/export，无全局作用域泄漏，行为完全不变。
>
> ## Verification（验收）
> - 语法检查通过。
> - 浏览器冒烟：全部画布交互（节点/连线/撤销/媒体/Agent/历史）与拆分前一致。
>
> ## Blocked by
> #157、#158、#159、#160、#161（需先完成各 feature 模块拆分）。
>
> ## Additional context
> 高风险，放最后。跨引用全部改 import/export。

## 主会话实测数据（2026-09-27，作为执行依据）

- 主脚本 `smart-canvas.js`：21453 行 / 1091 顶层函数 / 350 顶层 const/let（174 let/var + 176 const）。
- 五个已抽模块：canvas-layout-utils(5 函数)、canvas-connections(26)、canvas-undo(12)、canvas-node-ops(16)、canvas-media-history(107)。
- 小模块 → 主脚本 需引用的标识符：**168 个**（函数 + const + 只读 let）。
- 主脚本 → 小模块 需引用的函数：**98 个**。
- **跨模块被重新赋值的可变状态：13 个**（ES module import 是只读绑定，不能跨模块 `x = ...`，必须走状态容器）：
  `nodes` / `selectedId` / `selectedIds` / `selectedImage` / `projectMediaLibrary` / `activeComposerSubject` / `lastComposerNodeId` / `connectionLayerRaf` / `createMenuGroupId` / `smartHistoryDrawerCollapsed` / `smartHistoryPointerState` / `smartHistorySelectedImage` / `smartHistoryTap`
- 这 13 个变量的引用面：**743 处**（主脚本 537 + connections 25 + undo 52 + node-ops 52 + media-history 77 + project-canvas-mutations 少量）。
- **唯一函数内同名遮蔽**：`canvas-undo.js` L158 `const nodes = snapshotNodes.map(...)`（局部 `nodes`，改写时必须保留为局部，不得改成 `canvasState.nodes`）。
- 边界"漏网点"（必须纳入范围，否则 module 化后断链）：
  - `project-canvas-mutations.js`（391 行）引用 `canvas` / `nodes` / `viewport`。
  - `excel-import.js` 引用 `canvasId` / `escapeHtml` / `fitBoundsViewport` / `params` / `shell` / `toast`。
  - `static/smart-canvas.html` 内联 `on*` 事件引用 **29 个** canvas 函数（内联事件是 window 查找，函数离开全局即断）。
  - `outpaint-ratios.js` 0 引用，可不转。

## 13 个状态变量当前顶层声明原文（初始值必须照抄，勿改）

```
L98    let smartHistoryDrawerCollapsed = false;
L99    let smartHistoryPointerState = null;
L100   let smartHistoryTap = null;
L101   let smartHistorySelectedImage = null;
L167   let nodes = [];
L168   let selectedId = '';
L169   let selectedIds = [];
L170   let selectedImage = {nodeId:'', index:-1};
L243   let projectMediaLibrary = {items:[]};
L273   let createMenuGroupId = '';
L5130  let connectionLayerRaf = 0;
L14641 let lastComposerNodeId = '';
L14642 let activeComposerSubject = null;
```

## 总体方案（3 子批）

**设计决策（主会话已定，twin 执行时不得更改方案，只可报告问题）**
1. 状态容器只包这 13 个真正跨模块可变的变量，不做全量 350 变量容器化（避免过度设计）。
2. 4a 先把状态容器做成 **classic 脚本**（`window.canvasState`）+ 批量改写引用——纯重构、零语义变化、无 module 时序风险，单独可验证。
3. 4b 再把主脚本 + 状态容器转 `type="module"`；状态容器改 `export const state`，消费方用 `import { state as canvasState }` **别名导入 → 引用零改写**。
4. 4c 把 5 个小模块 + project-canvas-mutations + excel-import 转 module，加 import/export，移除 4b 的临时 window 桥，处理 29 个内联事件。
5. 每子批 gate：`node --check`（module 文件用 `node --input-type=module --check`）+ 浏览器全量冒烟 + 全量 pytest（基线 1040 passed / 351 subtests）。
6. **不要提交 git**（主会话审核后提交）。

---

## 4a 执行细则（本次派发范围）

**目标**：状态容器 classic 脚本 + 批量改写 13 变量引用。**纯重构，行为完全不变，不引入任何 module。**

### 步骤

1. **新建 `static/js/canvas-state.js`**（classic 脚本），内容：
   ```js
   // canvas-state.js — 画布共享可变状态容器（P2e 第 1 步，classic 脚本）
   // 13 个跨模块可变状态集中于此；4b 起改为 ES module 导出 state。
   window.canvasState = {
       smartHistoryDrawerCollapsed: false,
       smartHistoryPointerState: null,
       smartHistoryTap: null,
       smartHistorySelectedImage: null,
       nodes: [],
       selectedId: '',
       selectedIds: [],
       selectedImage: {nodeId:'', index:-1},
       projectMediaLibrary: {items:[]},
       createMenuGroupId: '',
       connectionLayerRaf: 0,
       lastComposerNodeId: '',
       activeComposerSubject: null
   };
   ```
   （初始值与上方声明原文逐一一致。）

2. **从 `smart-canvas.js` 删除这 13 行顶层声明**（L98-101、L167-170、L243、L273、L5130、L14641-14642，按当前行号定位，删后行号会变，按文本匹配删）。

3. **批量改写引用**：把 6 个文件里对这 13 个标识符的**独立引用**改成 `canvasState.<name>`：
   - `smart-canvas.js`（~537 处）、`canvas-connections.js`（~25）、`canvas-undo.js`（~52）、`canvas-node-ops.js`（~52）、`canvas-media-history.js`（~77）、`project-canvas-mutations.js`（少量）。
   - **只改独立标识符引用**（前后非 `[A-Za-z0-9_$]`），不改：字符串字面量、注释、对象键名（如 `{nodes: ...}` 的键）、`canvasState.nodes` 已改过的。
   - **必须保留** `canvas-undo.js` L158 的局部 `const nodes = snapshotNodes.map(...)` 及其函数内对该局部 `nodes` 的引用——不得改成 `canvasState.nodes`。
   - 推荐用 **Node.js 脚本 + 真实 JS 解析**（如 acorn，`npx acorn`）做 scope-aware 改写；若用正则，改完必须逐行 review diff 并用 `node --check` 验证。
   - **禁止**用 `python -c` 内嵌含换行的替换串（batch-3 CRLF 事故教训）。用 edit/write 工具或独立 .js/.py 脚本文件。
   - 保持文件原有换行符（CRLF/LF 勿混）。

4. **HTML**：在 `static/smart-canvas.html` 的 `project-canvas-mutations.js`（L543）**之前**插入 `<script src="/static/js/canvas-state.js"></script>`（无 ?v=，与已抽模块一致）。

### 4a 验证 gate（全过才算完成）

- `node --check` 全部 7 个 JS 文件（6 个 canvas + canvas-state.js）通过。
- 浏览器冒烟：节点创建/删除/移动、连线、撤销/重做、媒体归档、回收站、历史面板、Agent——与改前一致，0 ReferenceError / 0 console error。
- 全量 pytest：1040 passed / 351 subtests（与基线一致）。
- 确认 `window.canvasState` 存在且 13 个键齐全；确认 13 个原全局名**不再**是独立全局（`window.nodes` 等应为 undefined）。

### 4a 输出

完成后把自报结果写进 `.pipeline/batches/batch-4-result.md` 的 **4a 小节**（格式：改了什么/为什么/验证结果/遗留风险）。**不要提交 git。**

---

## 4b / 4c 预告（4a 审核通过后再细化派发）

- **4b**：`canvas-state.js` 改 `export const state`（删 window.canvasState）；`smart-canvas.js` 转 `type="module"` + `import { state as canvasState }`（引用零改写）+ 给 168 个被小模块引用的标识符加 `export` + 临时 window 桥（`window.x = x`，4c 移除）；`project-canvas-mutations.js`、`excel-import.js` 转 module（import state + 所需标识符）；处理 29 个内联 `on*` 事件（改 addEventListener 或纳入临时桥）。
- **4c**：5 个小模块转 `type="module"` + `export` 各自函数 + `import { state as canvasState }` + `import { ...168... } from './smart-canvas.js'`；主脚本把 98 个对小模块函数的全局引用改 `import`；移除全部临时 window 桥；最终"无全局泄漏"检查（canvas 函数不应再挂 window）。
