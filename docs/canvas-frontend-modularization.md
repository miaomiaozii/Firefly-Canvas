# 画布前端模块化 · P0 依赖分析与模块边界图

> 对应 issue：#156（P0）、驱动 #157-#162（P1-P3）。
> 目标文件：`static/js/smart-canvas.js`（24,816 行）。
> 本文是**只读分析**，不改代码。

## 1. 现状

- **加载方式**：普通 `<script src="/static/js/smart-canvas.js">`（**非** `type="module"`），全局作用域，顶部大量 `const` 全局声明。
- **规模**：1,257 个顶层函数 + 378 个顶层 `const/let/var` = **1,635 个顶层声明**。
- **组织方式**：**按功能添加时间（chronological）组织，非按 feature**。同一 feature 的函数散落全文件（例：`pushUndo` L364、`moveNode` L8408、`deleteNode` L15144、`cascadeArchiveDeletedMedia` L7237、`undo` L16279）。
- **推论**：抽 feature 模块 = 从全文件各处"捞"函数聚拢，而非按行范围整块切。纯函数与状态函数交错，不能整块搬。

## 2. 模块边界（按 feature 簇）

按函数名前缀聚类 + 关键函数定位，识别出以下 feature 簇（函数数为近似，边界以抽取时精确核对为准）：

| 模块 | 规模 | 代表函数（行号） | 纯/状态 | 抽取优先级 |
|---|---|---|---|---|
| **布局/几何** | ~20 | `resolveImageAspect`(1741)、`getBatchThumbGridSize`(1759)、`getSingleImageLayoutSize`、`getUngroupedImageSize`、`smartGroupLayoutSize`、`smartGroupMembers`、`isSmartGroupableNode`、`smartGroupZoom`（区 1704-1908） | **纯为主** | **P1（最先，低风险）** |
| **连线** | ~30 | `connect`(2635)、`reroute`(1909)、`renderConnections`(8307)、`disconnectConnection`(15215) | 状态 | P2a |
| **撤销/重做** | ~26 | `pushUndo`(364)、`undo`(16279)、`redo`(16285) | 状态 | P2b |
| **节点操作** | ~18 | `createImageNodeAt`(1857)、`moveNode`(8408)、`deleteNode`(15144) | 状态 | P2c |
| **媒体/历史** | ~157 | `scheduleMediaArchive`(311)、`cascadeArchiveDeletedMedia`(7237)、历史面板/回收站系列 | 状态 | P2d（最大） |
| **Agent 节点** | ~40 | `handleAgent`(11505)、`createSmartAgentNode` 等 | 状态 | P2e（可选并入 P2） |
| **对话节点** | ~30 | `sendConversation`(13923) 等 | 状态 | P2f（可选并入 P2） |
| **渲染/同步** | ~80 | `syncCanvas`(12954)、`renderCanvas`、`render*`(51) | 状态 | 留主脚本（核心） |
| **其余**（prompt/run/save/edit/toggle 等） | ~1000+ | 杂项 | 混合 | 留主脚本，后续按需 |

> 说明：「渲染/同步」+「其余」占大头（~1000+ 函数），是画布核心状态机，**不建议**在 P1-P2 动它们（风险最高）。P1-P2 只抽**边界清晰、可验证**的簇（布局/连线/撤销/节点操作/媒体）。

## 3. P1 纯函数清单（最先抽取，零状态依赖）

以下函数**入参驱动、不读写主作用域状态**（已核对：仅引用入参 + 本区常量 + 其他纯函数），可安全抽到独立脚本：

- 常量：`BATCH_THUMB`、`SMART_GROUP_LAYOUT`、`BATCH_NODE_PAD_H/V`、`BATCH_STATUSBAR_H`、`SINGLE_IMAGE_LAYOUT`、`SMART_GROUP_DEFAULT_WIDTH/HEIGHT`、`SMART_GROUP_LEGACY_HEIGHT`、`SMART_GROUP_MIN_WIDTH/HEIGHT`、`SMART_GROUP_MAX_MEMBER_ZOOM`
- 纯函数：`resolveImageAspect(node)`、`getBatchThumbGridSize(count)`、`getSingleImageLayoutSize(aspect,maxWidth)`、`getUngroupedImageSize(aspect)`、`smartGroupLayoutSize(node)`、`smartGroupMembers(node)`、`smartGroupCompactMembers(node)`、`isSmartGroupCompactMember(node)`、`isSmartGroupableNode(node)`、`smartGroupZoom(group)`、`scaleSmartGroupMemberToZoom(group,member,zoom)`、`mediaNodeDefaultScale(node)`

> ⚠️ 同区但**状态**函数（`createImageNodeAt` 会改 canvas）**不抽**，留主脚本。抽取时逐一核对每个函数体是否真的零状态（grep 函数体内是否引用 `canvas`/`nodes`/`document`/`world` 等主作用域标识符）。

## 4. 加载顺序（共享全局作用域拆分）

关键约束：**顶层执行代码**（非函数定义）在加载时运行，其依赖必须先加载；**函数定义**可引用后加载的全局（只要调用发生在所有脚本加载后）。

建议 `<script>` 顺序（`smart-canvas.html`）：
1. `canvas-layout-utils.js`（P1 纯工具，无依赖）
2. `canvas-connections.js`（P2a）
3. `canvas-undo.js`（P2b）
4. `canvas-node-ops.js`（P2c）
5. `canvas-media-history.js`（P2d）
6. `smart-canvas.js`（主脚本，最后——它含顶层初始化代码，依赖前面所有全局）

> 注意：拆出的脚本若含**顶层执行代码**（如 `const x = document.getElementById(...)`），需确认其依赖的 DOM/全局已就绪。纯函数脚本无此问题。

## 5. 风险与验证

- **每步验证**：`node --check`（语法）+ 浏览器核心交互冒烟 + 全量回归（`pytest`，~5 分钟）。
- **最大风险**：状态模块（P2）与主脚本核心状态机纠缠，抽取时漏搬一个跨引用 → 运行时 `ReferenceError`。缓解：抽取后 grep 确认被搬函数在主脚本无残留引用 + 浏览器冒烟覆盖该 feature。
- **不建议一把梭**：每次只抽一个模块 + 验证 + 提交，跨多次会话推进。

## 6. 执行顺序建议

1. **#157 (P1)**：抽布局/几何纯工具 → 验证 → 提交。（最先，最低风险，验证整个拆分流程）
2. **#158-#161 (P2a-d)**：按连线 → 撤销 → 节点操作 → 媒体/历史，逐个抽 + 验证 + 提交。
3. **#162 (P3)**：（可选）转 ES module，最后做。
