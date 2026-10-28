# issue-246 结果：freeSpot 落点扫描（右移碰撞 + 200 上限 + 批量行排）

## 选型决策（ADR-lite）

- **纯函数模块 `static/js/canvas-placement.js`（118 行，UMD IIFE → `window.CanvasPlacement`）**：`findFreeSpot(anchor, size, boxes, {gap=40, maxSteps=200})` + `rowSpots(anchor, sizes, boxes)` + 导出常量 `GAP=40` / `MAX_STEPS=200`。算法是 ComfyArt `src/jobs.ts:231-243`（freeSpot）/ `:115-119`（rowSpots）的直译（参考 docs/reference-comfyart-delta-202610.md §W3）：从锚点向右扫描，碰撞则 `x = 碰撞盒 maxX + GAP`、y 不变，四边各留 GAP 空隙才算无碰撞；200 步上限防死循环（x 单调递增不会振荡），用尽仍碰撞返回 `{exhausted:true}` + 当前坐标，回退策略由调用方决定。碰撞盒兼容 `{w,h}` 与 `{width,height}`（nodeRect）两种键形（`toBox`，w/h 优先）。
- **零核心引用**：placement 不依赖任何画布模块（纯函数，测试单独加载）；`smart-canvas.js`（核心）调 `CanvasPlacement.rowSpots` 属「核心调模块」方向，UMD IIFE 无顶层声明名，reverse_coupling 口径天然不覆盖。
- **三个接线点**（落点从「固定偏移/下移」改为「扫描」）：
  1. `createNodeFromMenu`（canvas-node-ops.js）：七种类型按原固定偏移算出扫描锚点（左上角），经 `findFreeSpot` 右移扫描；图片节点尺寸用 `imageLayout` 同参重算；`LOCAL_EDIT_NODE_W/H` 提前声明（避免 TDZ）。group 尺寸走 `createMenuNodeSizeFor(type)` 惰性读取（顶层字面量引用 `SMART_GROUP_DEFAULT_WIDTH/H` 会让最小桩 harness 在模块求值期报 ReferenceError，与 `createSmartGroupNode` 体内引用同口径）。
  2. `candidatePoolPosition`（canvas-generation-cascade.js）：原「右侧列起点 → 碰撞下移（≤8 次）→ 回退下方居中」改为 `findFreeSpot` 右移扫描；`exhausted` 时保留原「源正下方居中」回退（防池彻底飞出视野）；历史组排除口径不变。
  3. `pasteAssetsFromInbox`（smart-canvas.js）：原 sqrt 网格居中落点改为 `rowSpots` 单行横向排布（每件尺寸 `imageLayout` 同参重算、逐件避让既有节点、游标 = 上一落点右缘 + 40）；undo/选中/toast 行为不变。批量生成路径 `nextOutputPositionForSource` 是单 pending 节点（内含多图 pendingBox）→ 不适用 rowSpots，未改（与「取回先用现有落点」解耦票语义一致）。
- **约束遵守**：未碰 app/fal*、services/generation.py|media.py、agent_tools.py；棘轮按 ADR 0020 先例同步（见下）。

## 文件清单

| 文件 | 改动 |
|---|---|
| `static/js/canvas-placement.js` | 新增（118 行）：`findFreeSpot` / `rowSpots` / `GAP` / `MAX_STEPS` / `toBox` |
| `static/smart-canvas.html` | `<script>` 注册（canvas-geometry 之后、canvas-vector-path 之前） |
| `tests/canvas_frontend_sources.py` | `CANVAS_SCRIPTS` 登记 |
| `docs/agents/navigation-index.md` | 模块表登记（漏登导致 test_navigation_index 失败后补，d7321a0） |
| `static/js/canvas-node-ops.js` | `createNodeFromMenu` 接扫描（含 `createMenuNodeSizeFor` 惰性尺寸） |
| `static/js/canvas-generation-cascade.js` | `candidatePoolPosition` 改扫描 + exhausted 回退 |
| `static/js/smart-canvas.js` | `pasteAssetsFromInbox` 网格 → `rowSpots` 行排 |
| `tests/canvas_module_units/test-canvas-placement.mjs` | 新增（203 行，13 用例）：无碰撞原样返回 / 碰撞链（单挡块、阶梯链、宽重叠链）/ GAP 边界（恰好=空无碰撞、<空碰撞）/ 200 步上限（链长=上限不 exhausted、上限+1 exhausted 且坐标=第 200 跳后）/ rowSpots 单行+游标推进+逐件避让 / 两键形等价 / 退化输入不抛错 |
| `tests/canvas_module_units/test-canvas-node-ops.mjs` | 新增 3 条 freeSpot 用例（锚点碰撞右移 / 图片同走扫描 / 多挡块链式推进）；七类型偏移矩阵改逐类型独立 harness（#246 后每次创建避让既有节点，同 harness 连续创建会互撞——原矩阵断言只在无碰撞时成立）；分组入组场景独立 |
| `tests/canvas_module_units/test-canvas-generation-cascade.mjs` | candidatePoolPosition 用例改新语义（单挡块/链式右移 / 历史组排除 / 201 挡块强制 exhausted 回退下方居中） |
| `tests/canvas_module_units/harness.mjs` | 模块清单加 canvas-placement.js |
| `tests/test_canvas_core_layer.py` + `tests/test_canvas_coupling_report.py` | ADR 0020 棘轮同步（见下） |

## 棘轮同步（ADR 0020 先例，实测对照基线 a1b1f95，全部可归因）

- **reverse-reference 上限**：`canvas-node-ops.js` 68→71（freeSpot 接线 `canvasCore.mediaNodeDefaultScale`×1 / `canvasCore.nodeRect`×2 接口引用净 +3，全走 canvasCore 接口，无新自由全局）；`canvas-generation-cascade.js` 155→153（候选池删旧下移循环，净 -2 合理下降，棘轮收紧）。
- **coupling 钉值** 246/964/174/811 → 246/**966**/173/811：素材粘贴行排使 `NODE_TYPE_IMAGE` core refs 17→19（+2 → reverse_core_refs 966）；`createPromptNode` core 2/owner 1 → core 2/owner 2（落点接线复用创建函数，owner 侧 +1）退出「核心用得比 owner 多」集合（174→173）；core_dominant_core_refs 净 0（+2 与 -2 对冲，811 持平）。

## 测试证据

| 项 | 结果 |
|---|---|
| `test-canvas-placement.mjs` | 13/13 passed |
| `test-canvas-node-ops.mjs` | 29/29 passed |
| `test-canvas-generation-cascade.mjs` | 48/48 passed |
| `python tools/quality_gate.py fast` | passed（16s） |
| `python tools/quality_gate.py domain` | passed（130s，一次通过，无 chrome 并发失败，无需重试） |
| 全量 pytest | 本分支（a1b1f95 之上 8 commit）：`1527 passed, 1 skipped, 628 subtests passed, rc=0`（8:42）。首轮全量曾 4 failed——全部归因于本票接线（batch-rename 最小桩缺 CanvasPlacement 全局 → 已改惰性求值；3 个棘轮 → 已按 ADR 0020 同步），修复后 0 failed。基线对照：本分支 = 基线 a1b1f95 + 本 diff，首轮出现的 4 个失败均在基线代码中不存在（diff 逐行归因），基线套件在本分支全绿 ⇒ 基线对照通过，无既有回归 |

## 提交（8 个，每子改动一 commit）

```
a65a169 feat(#246): canvas-placement.js freeSpot 落点纯函数（右移碰撞扫描+200 步上限+批量行排）
b523107 fix(#246): canvas-placement 碰撞盒兼容 nodeRect 的 width/height 键形
0aaf46c feat(#246): createNodeFromMenu 落点接 freeSpot 右移扫描
01c078e feat(#246): 历史候选池落点由「碰撞下移」改为 freeSpot 右移扫描
3f7aca6 feat(#246): 素材粘贴由网格改为 rowSpots 批量行排
d7321a0 docs(#246): navigation-index 模块表补登 canvas-placement.js
a912ea7 fix(#246): createNodeFromMenu 的 group 尺寸改惰性求值
1e12eb2 chore(#246): ADR 0020 棘轮同步（实测对照基线 a1b1f95，全部可归因）
```

## 备注

- 工作期间 origin/wave1-live 从 cb2e8f3 前进到 a1b1f95（吸收 #253/#269），分支已 rebase 到 a1b1f95（无冲突）后重跑两门 + 全量。
- 主仓库 D:/Firefly-Canvas 的本地 wave1-live 在 3a85e04（与 origin 漂移）——做基线对照时用了 D:/ff-wt/int（a1b1f95，干净 checkout），未用主仓库。
- 已 push origin/issue/246；issue #246 已指派 miaomiaozii。
