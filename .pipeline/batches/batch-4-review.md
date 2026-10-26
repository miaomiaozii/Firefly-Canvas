# batch-4 审核（#162 P2e ES module）— 4a 子批

**判定：PASS**（主会话修复 twin 的 81 处属性链损坏 + 2 处测试适配后，全 gate 通过）

## 特殊情况

twin 完成搬移框架后 1200000ms 超时。其批量改写工具引入 **81 处属性链损坏**（`X.nodes` → `X.canvasState.nodes`，70 属性链 + 11 可选链）。主会话接管：精准正则还原 + 测试适配 + 全量验证。twin 的 canvas-state.js 容器与 13 声明删除本身正确。

## 逐条对照（4a 验收）

| 验收项 | 结果 |
|---|---|
| 状态容器 classic 脚本，13 键初始值与原声明一致 | PASS（逐键核对） |
| 13 顶层声明从主脚本删除 | PASS |
| 13 变量引用全部改为 `canvasState.*`，无遗漏裸引用 | PASS（扫描：仅剩合法对象键/函数参数/L158 局部 const nodes） |
| 纯重构零语义变化 | PASS（属性链损坏已还原至与 HEAD 逐字一致；简写属性正确展开） |
| 不引入任何 module（4a 范围） | PASS（canvas-state.js 为 classic 脚本） |
| HTML 插入位置正确 | PASS（L543，project-canvas-mutations.js 之前） |
| node --check 全 7 文件 | PASS |
| 浏览器冒烟（13 键/原全局名消失/创建/选中/撤销/0 ReferenceError/0 console error） | 8/8 PASS |
| 全量 pytest 1040/351 | PASS |

## 审核方法（可复用）

- **属性链损坏检测**：`canvasState` 在 HEAD 0 出现 → 任何 `标识符.canvasState.` 必为损坏。正则 `([A-Za-z0-9_$])\.canvasState\.` 与 `\?\.canvasState\.` 精准还原，不误伤合法裸引用。
- **简写属性**：4a 后顶层变量移入容器，原简写 `{selectedId}` 必须展开为 `{selectedId: canvasState.selectedId}`（twin 已正确处理 L40）。
- **测试 stub**：`node -e` 跑抽出的函数时，stub 环境需补 `canvasState` 对象（引用既有 stub const）。

## 流程发现（影响 4b/4c 策略）

twin 的批量改写对"属性链 vs 裸引用"区分不可靠（81 处损坏）。4b/4c 的 import/export 改写面更大（168 import + 98 export），**建议主会话用 scope-aware 解析（acorn）执行机械改写**，twin 仅承担浏览器冒烟等验证类工作；或 twin 小步执行 + 主会话逐行 diff review。
