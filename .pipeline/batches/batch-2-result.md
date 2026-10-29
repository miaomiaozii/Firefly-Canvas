# batch-2 结果：#160 画布前端模块化 P2c：抽取节点操作模块

## 最终搬/留函数清单

### 搬到 `static/js/canvas-node-ops.js`（16 个函数）

**创建簇（9 个）：**
1. `createImageNodeAt(point, images, options)` — 在指定位置创建图片节点
2. `createNode(x, y, images, options)` — 创建通用节点
3. `createPromptNode(x, y, options)` — 创建提示词节点
4. `createConversationNode(x, y, options)` — 创建对话节点
5. `createAgentNode(x, y, options)` — 创建 Agent 节点
6. `createLocalEditNode(x, y, options)` — 创建局部编辑节点
7. `createLocalEditNodeFromImage(nodeId, imageIndex)` — 从图片节点创建局部编辑节点
8. `createLoopNode(x, y, options)` — 创建循环节点
9. `createSmartGroupNode(x, y, options)` — 创建智能分组节点

**删除簇（3 个）：**
10. `deleteNode(id)` — 删除节点
11. `clearNodeMediaBeforeDelete(id)` — 删除前清空节点媒体
12. `deleteNodeFromButton(id)` — 从按钮删除节点（含 #65 生成中禁删守卫）

**移动/缩放 DOM 同步（2 个）：**
13. `moveNodeElementsDuringDrag()` — 拖拽中同步节点 DOM 位置
14. `updateNodeElementDuringResize(node)` — 缩放中同步节点 DOM 尺寸

**重命名（1 个）：**
15. `renameSmartNodeImage(nodeId, imageIndex)` — 重命名节点内图片（async）

**菜单创建（1 个）：**
16. `createNodeFromMenu(type)` — 从右键菜单创建节点

### 留在 `smart-canvas.js`（排除项及理由）

- **布局/缩放辅助函数**（`safeScale`/`nodeScale`/`mediaNodeDefaultScale`）：P1 已决定留主脚本的布局工具，不搬。
- **画布视口缩放**（`zoomPanAroundPoint`）：属画布视口操作，不是节点操作。
- **拖拽事件接线**（`dragState`、mousedown 处理器、drag 处理逻辑）：核心状态机，深度纠缠渲染/同步，留主脚本。`moveNodeElementsDuringDrag` 和 `updateNodeElementDuringResize` 是拖拽/缩放中的 DOM 同步函数，可独立搬移。
- **复制/粘贴/Alt 拖拽复制**（`cloneSmartNode`/`copySelectedNodes`/`pasteNodes`/`duplicateForAltDrag`）：属剪贴板操作，不是核心 CRUD。
- **`renamePromptTemplateGroup`**：提示词模板组重命名，不是画布节点操作。
- **连线相关**：P2a 已搬 canvas-connections.js。
- **undo/redo**：batch-1 已搬 canvas-undo.js。
- **媒体归档**：P2d 领域，本批不碰。

## 改了什么

| 文件 | 变化 |
|------|------|
| `static/js/canvas-node-ops.js` | 新建，341 行（16 个函数 + 头部注释） |
| `static/js/smart-canvas.js` | 24033 → 23709 行（净减 324 行） |
| `static/smart-canvas.html` | 在 `canvas-undo.js` 与 `smart-canvas.js` 之间插入 `<script src="/static/js/canvas-node-ops.js"></script>` |

## 为什么（关键决策）

1. **纯位置移动，零逻辑改动**：所有搬移的代码连注释一起原样搬，未改任何逻辑。
2. **共享全局作用域模式**：沿用 P2a/P2b 模式，新模块先于主脚本加载，所有标识符在同一个全局作用域中互相可见。
3. **只搬语义清晰的节点操作函数**：与渲染/同步核心状态机深度纠缠的混合函数（拖拽事件接线、复制/粘贴）留主脚本。
4. **`moveNodeElementsDuringDrag`/`updateNodeElementDuringResize` 搬移**：这两个函数是拖拽/缩放中的 DOM 同步函数，不依赖拖拽状态机本身，可独立搬移。

## 验证结果

| 验证项 | 结果 | 关键输出 |
|--------|------|----------|
| `node --check canvas-node-ops.js` | PASS | 语法正确 |
| `node --check smart-canvas.js` | PASS | 语法正确 |
| grep 重复定义检查 | PASS | 主脚本无重复定义，16 个函数全部在新模块 |
| 浏览器冒烟：全局可用性 | PASS | 16 个节点操作函数全部 window 可见 |
| 浏览器冒烟：节点创建（image/prompt/loop/conversation/agent/local-edit/group） | PASS | 7/7 类型创建成功 |
| 浏览器冒烟：删除节点 | PASS | 节点正确删除 |
| 浏览器冒烟：moveNodeElementsDuringDrag 可用 | PASS | typeof = function |
| 浏览器冒烟：updateNodeElementDuringResize 可用 | PASS | typeof = function |
| 浏览器冒烟：拖拽移动节点 | PASS | dx=100 dy=80 |
| 浏览器冒烟：缩放节点 | PASS | size: 340x286 -> 390x336 |
| 浏览器冒烟：renameSmartNodeImage 可用 | PASS | typeof = function |
| 浏览器冒烟：生成中禁删守卫 | PASS | smartNodeInFlight 守卫代码存在 |
| 浏览器冒烟：ReferenceError | PASS | 0 个 |
| 浏览器冒烟：console error | PASS | 0 个 |
| 浏览器冒烟：清理+保存 | PASS | 恢复基线，保存成功 |
| 全量回归 `pytest tests/` | PASS | **1040 passed, 351 subtests passed**（与基线完全一致） |

**冒烟测试总结果：19/19 全部通过 ✓**

## 审核修复（round 1）

审核发现测试脚本 `tmp/qa/qa19-p2c-nodeops.js` 的 spot 计算有 bug：重叠检查只在循环前查询一次节点 box，不含循环中创建的节点，导致 agent/group 节点的右键落在已有节点上而非空白画布。

修复：循环内每次右键前重新查询所有类型节点的 box 做重叠检查。

修复后重跑验证：
- 浏览器冒烟：**19/19 全部通过**（agent/group 创建 PASS，拖拽/缩放 best-effort 也 PASS）
- 无 ReferenceError、无 console error

## 遗留风险

1. **状态变量不在 window 上**：与 batch-1 相同，const/let 声明的状态变量不在 window 对象上，但同一页面全局作用域内可访问，不影响功能。
2. **缓存 busting**：canvas-node-ops.js 无 ?v= 参数，首次加载不受影响。
