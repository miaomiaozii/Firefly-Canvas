## 侦察补充（实现入口清单）

- 拖拽：节点 mousedown → dragState（支持组拖、Ctrl 成组）；画布空白 mousedown → 平移 panState（smart-canvas.js:11055/14385 附近）
- 缩放三条路：滚轮（以光标为锚点，canvasWheelZoomFactor）、Ctrl +/-（×1.25）、Ctrl+0 全览 fitAllNodesViewport；另有底部按钮 zoomViewportAroundCenter（smart-canvas.js:15121/15262-15276/15800）
- 框选：空白处 左键+Ctrl/⌘（或按住 R）拖 → #selectionBox；finishSelection 按 nodeRect 命中
- 注意：**「双击进入编辑」当前不存在**——本票该断言改为「双击=无副作用」防回归；触屏只有 touch-mouse.js 的原生 click，触屏不在本票范围。
