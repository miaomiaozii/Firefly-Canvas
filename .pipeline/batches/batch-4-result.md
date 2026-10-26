# batch-4 结果报告（#162 P2e ES module）

> 拆 4a/4b/4c 子批。本文件按子批追加。**当前完成：4a。**

## 4a：状态容器 classic 脚本 + 13 变量引用改写

**状态：完成**（twin 搬移后超时，主会话修复 81 处属性链损坏 + 2 处测试适配后全绿）

### 改动文件

| 文件 | 变化 |
|---|---|
| `static/js/canvas-state.js` | 新建（classic 脚本，`window.canvasState`，13 键，初始值与 13 个原顶层声明逐一一致） |
| `static/js/smart-canvas.js` | 删 13 顶层声明；~500 处裸引用 → `canvasState.*` |
| `static/js/canvas-connections.js` | 25 处裸引用 → `canvasState.*` |
| `static/js/canvas-undo.js` | 52 处裸引用 → `canvasState.*`（L158 局部 `const nodes` 保留） |
| `static/js/canvas-node-ops.js` | 52 处裸引用 → `canvasState.*` |
| `static/js/canvas-media-history.js` | 77 处裸引用 → `canvasState.*` |
| `static/js/project-canvas-mutations.js` | 少量裸引用 → `canvasState.*` |
| `static/smart-canvas.html` | +1 行 `<script src="/static/js/canvas-state.js">`（L543，project-canvas-mutations.js 之前） |
| `tests/test_creative_history_experience.py` | 1 处断言字符串加 `canvasState.` 前缀 |
| `tests/test_project_image_generation_ui.py` | `node -e` stub 环境补 `const canvasState={...}` |

### 13 个状态变量（去重后，非 14）

`nodes` / `selectedId` / `selectedIds` / `selectedImage` / `projectMediaLibrary` / `activeComposerSubject` / `lastComposerNodeId` / `connectionLayerRaf` / `createMenuGroupId` / `smartHistoryDrawerCollapsed` / `smartHistoryPointerState` / `smartHistorySelectedImage` / `smartHistoryTap`

### 关键事故与修复（主会话）

1. **twin 超时**：1200000ms 超时。超时前进度：canvas-state.js 已建正确、13 声明已删、6 文件已改，但**改写工具把 `canvasState.` 误插入属性链**——`X.nodes`（X 是对象）被改成 `X.canvasState.nodes`，共 **81 处**（70 属性链 + 11 可选链 `?.`）。
2. **主会话修复**：`tmp/qa/fix_4a_corruption.py` 两条正则精准还原——`([A-Za-z0-9_$])\.canvasState\.` → `$1.`（70）、`\?\.canvasState\.` → `?.`（11）。依据：`canvasState` 在 HEAD 中 0 出现，故任何 `标识符.canvasState.` 必为损坏；合法裸引用 `canvasState.nodes`（前无标识符+点）不受影响。
3. **测试适配**：2 个 UI 契约测试读 JS 源码/用 `node -e` 跑函数，stub 环境缺 `canvasState` → 补 stub + 更新断言字符串。

### 验证结果（全过）

| 项 | 结果 |
|---|---|
| node --check 全部 7 文件 | PASS |
| 属性链损坏残留（`X.canvasState`） | 0 |
| 双插入（`canvasState.canvasState`） | 0 |
| 13 裸标识符无遗漏独立引用 | PASS（仅剩合法：对象键/函数参数/L158 局部 const nodes） |
| 13 顶层声明已从主脚本删除 | PASS |
| canvas-state.js 13 键齐全 | PASS |
| HTML 插入位置（project-canvas-mutations 之前） | PASS |
| 浏览器冒烟（canvasState 13 键/原全局名消失/创建/选中/撤销/0 错误） | 8/8 PASS |
| 全量 pytest | PASS：1040 passed / 351 subtests（与基线一致） |

### 遗留风险 / 流程发现

- **twin 的机械改写工具不可靠**：81 处属性链损坏说明 twin 的批量替换对"属性链 vs 裸引用"区分有误。4b/4c 的 import/export 改写风险更高（168+98 个标识符），建议主会话用 scope-aware 工具（acorn）做，或 twin 只做小步 + 主会话逐行 review。
- 4a 是纯重构（classic 脚本），无 module 时序风险，行为不变已由冒烟 + pytest 确认。
