## 侦察补充（撤回链现状）

- 栈：canvas-undo.js undoStack/redoStack + UNDO_LIMIT；快捷键 Ctrl/⌘+Z（performUndo）、Ctrl+Shift+Z 或 Ctrl+Y（performRedo）；工具栏 smartUndoButton/smartRedoButton。
- 删除：Delete/Backspace（选中时）先 pushUndo 可撤；生成中节点拒绝并 toast（smart.nodeDeleteBusy）；节点头另有 .node-delete 垃圾桶按钮。
- 断言锚点建议：删除前后节点计数、撤回后位置坐标与连线集合、生成中删除的 toast 文案。
