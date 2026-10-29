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
