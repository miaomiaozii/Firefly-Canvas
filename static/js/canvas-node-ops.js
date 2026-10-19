// 画布节点操作模块（从 smart-canvas.js 抽离，#160 P2c）。
// 节点创建/删除/移动/缩放/重命名操作函数。
// 引用主脚本全局（canvasState.nodes/canvas/canvasState.selectedId/canvasState.selectedIds/canvasState.selectedImage/toast/tr/render
// scheduleSave/pushUndo/uid/imageLayout/nodeScale/mediaNodeDefaultScale 等），
// 本文件只含函数定义，先于 smart-canvas.js 加载，所有调用都发生在主脚本全局
// 初始化之后，安全。
// 注意：拖拽事件接线（dragState/mousedown 处理器）属核心状态机，留主脚本；
// 缩放/布局辅助函数（safeScale/nodeScale/mediaNodeDefaultScale）属 P1 布局工具，
// 留主脚本；复制/粘贴/Alt 拖拽复制（cloneSmartNode/copySelectedNodes/pasteNodes/
// duplicateForAltDrag）属剪贴板操作，留主脚本。

function createImageNodeAt(point, images=[], options={}){
    const layout = imageLayout(images || [], mediaNodeDefaultScale({type:NODE_TYPE_IMAGE, images:images || []}), {type:NODE_TYPE_IMAGE, images:images || []});
    return createNode((point?.x || 0) - Math.round(layout.width / 2), (point?.y || 0) - Math.round(layout.height / 2), images, options);
}

function createNode(x, y, images=[], options={}){
    if(!options.skipUndo) pushUndo();
    const nodeImages = (images || []).map(img => ({...img}));
    const node = {id:uid('smart'), type:NODE_TYPE_IMAGE, x, y, title:nodeImages.length > 1 ? 'Group' : nodeImages.length ? 'Image' : tr('smart.createImportNode'), images:nodeImages, created_at:Date.now()};
    node.scale = nodeImages.length > 1 ? MEDIA_GROUP_DEFAULT_SCALE : mediaNodeDefaultScale(node);
    if(options.title) node.title = String(options.title);
    if(options.runMeta) attachRunMeta(node, options.runMeta);
    if(options.historyId) node.historyId = String(options.historyId);
    if(options.needsRegeneration != null) node.needsRegeneration = Boolean(options.needsRegeneration);
    if(options.generationState) node.generationState = String(options.generationState);
    inheritNodeMetaFromImage(node);
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}

function createPromptNode(x, y, options={}){
    if(!options.skipUndo) pushUndo();
    const providerId = resolveChatProviderId();
    const node = {
        id:uid('prompt'),
        type:NODE_TYPE_PROMPT,
        x,
        y,
        w:316,
        h:240,
        title:'Prompt',
        text:String(options.text || ''),
        promptSeparator:';',
        promptSplitEnabled:false,
        llmEnabled:false,
        llmProvider:providerId,
        llmModel:resolveChatModel('', providerId),
        llmSystemEnabled:false,
        llmSystemPrompt:'You are a helpful prompt assistant.',
        llmInstruction:'',
        created_at:Date.now()
    };
    if(options.sourceNodeId) node.source_node_id = String(options.sourceNodeId);
    if(options.sourceMessageId) node.source_message_id = String(options.sourceMessageId);
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}

function createConversationNode(x, y, options={}){
    if(!options.skipUndo) pushUndo();
    const node = {
        id:uid('conversation'),
        type:NODE_TYPE_CONVERSATION,
        x,
        y,
        w:390,
        h:430,
        title:'Conversation',
        messages:[],
        created_at:Date.now()
    };
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}

function createAgentNode(x, y, options={}){
    if(!options.skipUndo) pushUndo();
    const node = {
        id:uid('agent'),
        type:NODE_TYPE_AGENT,
        x,
        y,
        w:382,
        h:360,
        title:'Agent',
        agentGoal:'',
        created_at:Date.now()
    };
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}

function createLocalEditNode(x, y, options={}){
    if(!options.skipUndo) pushUndo();
    const node = {id:uid('local-edit'), type:NODE_TYPE_LOCAL_EDIT, x, y, w:LOCAL_EDIT_NODE_W, h:LOCAL_EDIT_NODE_H, title:'', mode:'modify',
        original_asset_id:'', reference_asset_ids:[], prompt:'', localEditInputRoles:{originalNode:'', referenceNodes:[], promptNode:'', promptNodes:[]}, localEditActiveRole:'original', aspect_ratio:'1:1', generation_size:'1024x1024', generation_resolution:'1k', selectionType:'polygon', selection:[], feather_radius:8, images:[], created_at:Date.now()};
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}
// 工具排「局部编辑」：在图片节点旁创建 local-edit 节点并自动连好原图角色，
// 用户只需要画选区+写提示词，不需要先学端口连线。

function createLocalEditNodeFromImage(nodeId, imageIndex=null){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node) return;
    const source = imageIndex == null ? (node.images || [])[0] : (node.images || [])[imageIndex];
    if(!source){ toast(tr('smart.noExportableMedia')); return; }
    const rect = nodeRect(node);
    const point = {x:rect.x + rect.width + 140, y:Math.max(40, rect.y + Math.max(0, rect.height / 2 - LOCAL_EDIT_NODE_H / 2))};
    const created = createLocalEditNode(point.x, point.y, {select:true});
    created.localEditActiveRole = 'original';
    const connected = connectInputNode(nodeId, created.id);
    render();
    scheduleSave();
    toast(connected ? tr('smart.localEditCreated') : tr('smart.localEditConnectHint'));
}

function createLoopNode(x, y, options={}){
    if(!options.skipUndo) pushUndo();
    const node = {id:uid('loop'), type:NODE_TYPE_LOOP, x, y, w:340, h:168, title:'Loop', count:1, mode:'serial', showPrompt:false, imageInput:false, loopStart:1, imageBatchSize:1, variablePrompt:'', created_at:Date.now()};
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}

function createSmartGroupNode(x, y, options={}){
    if(!options.skipUndo) pushUndo();
    const node = {id:uid('group'), type:NODE_TYPE_GROUP, x, y, w:SMART_GROUP_DEFAULT_WIDTH, h:SMART_GROUP_DEFAULT_HEIGHT, title:'智能分组', items:[], created_at:Date.now()};
    canvasState.nodes.push(node);
    if(options.select !== false) canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}

function moveNodeElementsDuringDrag(){
    if(!dragState) return;
    const groupItems = dragState.group || [{id:dragState.id}];
    groupItems.map(item => item.id).forEach(id => {
        const n = canvasState.nodes.find(x => x.id === id);
        const el = world.querySelector(`.image-node[data-id="${CSS.escape(id)}"]`);
        if(n && el){
            el.style.left = `${n.x || 0}px`;
            el.style.top = `${n.y || 0}px`;
        }
    });
    const active = selectedNode();
    if(active && (dragState.group || [{id:dragState.id}]).some(item => item.id === active.id)){
        positionComposerForNode(active);
    }
    scheduleInteractionLayerRefresh();
}

function updateNodeElementDuringResize(node){
    if(!node) return;
    const el = world.querySelector(`.image-node[data-id="${CSS.escape(node.id)}"]`);
    if(!el){
        render();
        return;
    }
    const imgs = isSmartGroupNode(node) ? smartGroupImageRefs(node).map(ref => ref.item) : (node.images || []);
    const layout = imageLayout(imgs, nodeScale(node), node);
    el.style.width = `${layout.width}px`;
    el.style.height = `${layout.height}px`;
    const body = el.querySelector('.node-body');
    if(body){
        const loadingSingle = body.querySelector('.loading-cell.single');
        if(loadingSingle){
            // 单图占位与批量分支同规范：铺满 node-body（width/height:100%），
            // 节点 16px padding 即外边距；此前用 layout.width/height（=节点外框尺寸）
            // 会让占位框贴住/越出外框、与 pending 呼吸边框错位。
            loadingSingle.style.width = '100%';
            loadingSingle.style.height = '100%';
        }
        const loadingGrid = body.querySelector('.loading-skeleton');
        if(loadingGrid){
            // 批量占位格必须与 generationPlaceholderHtml/imageLayout 保持同一套规范：
            // 容器铺满 node-body（节点 16px padding 提供外边距），格子固定 136×102，
            // 列/行由 _batchTotal 经 getBatchThumbGridSize 决定（此前用 sqrt+1fr 会在
            // 图片写回触发本函数时把占位网格拉变形，导致格子越界/图片浮出框外）。
            const count = Math.max(2, Math.min(8, Number(node._batchTotal) || Number(node.pending) || 2));
            const batch = getBatchThumbGridSize(count);
            loadingGrid.style.width = '100%';
            loadingGrid.style.height = '100%';
            loadingGrid.style.gridTemplateColumns = `repeat(${batch.cols}, ${BATCH_THUMB.WIDTH}px)`;
            loadingGrid.style.gridTemplateRows = `repeat(${batch.rows}, ${BATCH_THUMB.HEIGHT}px)`;
        }
        const maxVisibleRows = isSmartGroupNode(node)
            ? (smartGroupCompactMembers(node).length ? Number(layout.rows || 1) : SMART_GROUP_MAX_VISIBLE_ROWS)
            : MEDIA_GROUP_MAX_VISIBLE_ROWS;
        const grid = body.querySelector('.thumb-grid');
        if(grid){
            grid.style.setProperty('--thumb-cols', layout.cols);
            grid.style.setProperty('--thumb-size', `${layout.thumb}px`);
            const visibleRows = Math.max(1, Math.min(maxVisibleRows, Number(layout.visibleRows || layout.rows || 1)));
            const maxHeight = Number(layout.gridHeight) || mediaGroupGridHeight(imgs, layout.cols, visibleRows, layout.thumb);
            grid.style.setProperty('--thumb-max-height', `${maxHeight}px`);
            grid.querySelectorAll('.thumb-item').forEach((itemEl, index) => {
                applyThumbDisplaySizeToElement(itemEl, imgs[index], layout.thumb);
            });
        }
        const wrap = body.querySelector('.image-wrap');
        if(wrap){
            // 分组单图卡片含 16px 内边距（PAD=32），图片按内边距内的尺寸显示，避免溢出边框。
            const wrapW = isSmartGroupNode(node) ? Math.max(24, Number(layout.innerW || 0) || (Number(layout.width) - 32)) : layout.width;
            const wrapH = isSmartGroupNode(node) ? Math.max(24, Number(layout.innerH || 0) || (Number(layout.height) - 60)) : layout.height;
            wrap.style.setProperty('--node-img-w', `${wrapW}px`);
            wrap.style.setProperty('--node-img-h', `${wrapH}px`);
        }
        const media = body.querySelector('.node-img');
        if(media){
            const mediaW = isSmartGroupNode(node) ? Math.max(24, Number(layout.innerW || 0) || (Number(layout.width) - 32)) : layout.width;
            const mediaH = isSmartGroupNode(node) ? Math.max(24, Number(layout.innerH || 0) || (Number(layout.height) - 60)) : layout.height;
            media.style.width = `${mediaW}px`;
            media.style.height = `${mediaH}px`;
        }
    }
    const active = selectedNode();
    if(active?.id === node.id) positionComposerForNode(active);
    scheduleInteractionLayerRefresh();
}

function deleteNode(id){
    // #147: 删除智能分组 = 解散分组（图片拆回画布、成员保留、只删容器），与"解散分组"入口行为一致，无数据丢失。
    // ungroupNode 内部已 pushUndo，这里不再重复。
    const groupNode = canvasState.nodes.find(n => n.id === id);
    if(groupNode && isSmartGroupNode(groupNode)){
        ungroupNode(id);
        return;
    }
    pushUndo();
    const deleteIds = new Set([id]);
    canvasState.nodes.forEach(node => {
        if(isHistoryGroupNode(node) && node.historyFor === id){
            // #188: 候选池是画布上的独立内容（归档历史图/扩图红底合成图），
            // 删除源节点不再连带删池——解绑 history 关联、降级为普通图片节点，池内图片全部保留。
            // 与用户手动断开 history 连线时的 demoteHistoryGroupNode 行为一致；
            // 撤销由上方 pushUndo 的深快照恢复原关联（池重新成为候选池）。
            demoteHistoryGroupNode(node);
        }
    });
    const removedNodes = canvasState.nodes.filter(node => deleteIds.has(node.id));
    // Clean up agent TUI state for deleted agent nodes
    removedNodes.forEach(node => {
        if (isSmartAgentNode(node)) {
            cleanupAgentTuiState(node.id);
        }
    });
    canvasState.nodes = canvasState.nodes.filter(node => !deleteIds.has(node.id)); if(canvas) canvas.nodes = canvasState.nodes;
    if(canvas) canvas.connections = (canvas.connections || []).filter(c => !deleteIds.has(c.from) && !deleteIds.has(c.to));
    canvasState.nodes.forEach(node => {
        if(Array.isArray(node.inputNodeIds)) node.inputNodeIds = node.inputNodeIds.filter(inputId => !deleteIds.has(inputId));
        if(isSmartGroupNode(node) && Array.isArray(node.items)) node.items = node.items.filter(itemId => !deleteIds.has(itemId));
    });
    if(canvasState.selectedId === id) canvasState.selectedId = '';
    canvasState.selectedIds = canvasState.selectedIds.filter(selected => !deleteIds.has(selected));
    if(deleteIds.has(canvasState.selectedImage.nodeId)) canvasState.selectedImage = {nodeId:'', index:-1};
    render();
    scheduleSave();
    // #74 (D1) + #146: 级联归档——被删节点中无其他引用的媒体移入回收区（可恢复），不再堆在资源库成孤儿。
    // #146: 延迟到撤销窗口结束才归档，撤销时取消，避免撤销恢复的节点引用已归档资产 → 404。
    scheduleMediaArchive(removedNodes.flatMap(node => node.images || []));
}

function clearNodeMediaBeforeDelete(id){
    const node = canvasState.nodes.find(n => n.id === id);
    if(!node || (node.type && node.type !== NODE_TYPE_IMAGE)) return false;
    const hadMedia = Boolean((node.images || []).length || node.pending);
    if(!hadMedia) return false;
    const clearedImages = (node.images || []).slice();
    pushUndo();
    node.images = [];
    node.pending = 0;
    node.running = false;
    node.title = tr('smart.createImportNode');
    delete node.w;
    delete node.h;
    const history = historyGroupForNode(node);
    if(history){
        canvasState.nodes = canvasState.nodes.filter(n => n.id !== history.id); if(canvas) canvas.nodes = canvasState.nodes;
        if(canvas) canvas.connections = (canvas.connections || []).filter(c => c.from !== history.id && c.to !== history.id);
    }
    if(canvasState.selectedImage.nodeId === id) canvasState.selectedImage = {nodeId:'', index:-1};
    canvasState.selectedId = id;
    canvasState.selectedIds = [];
    render();
    scheduleSave();
    // #74 (D1) + #146: 清空的图片若已无其他引用 → 移入回收区（7 天可恢复）。
    // #146: 延迟归档，撤销时取消，避免撤销后图片 404。
    scheduleMediaArchive(clearedImages);
    return true;
}

function deleteNodeFromButton(id){
    // #65: 生成中的节点禁止删除——任务写回会落到不存在的节点上（孤儿资产或报错）。
    const buttonNode = canvasState.nodes.find(n => n.id === id);
    if(buttonNode && smartNodeInFlight(buttonNode)){ toast(tr('smart.nodeDeleteBusy')); return; }
    // #149 B5: 统一为一步直接删节点（与 Delete 键行为一致），不再对有图节点两步清图
    deleteNode(id);
}
// 断开一条或多条连线（合并到分组的连线会一次性断开其下所有成员连线）。spec 可为索引数组或逗号分隔字符串。

async function renameSmartNodeImage(nodeId, imageIndex){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    const index = Math.max(0, Number(imageIndex) || 0);
    const image = node?.images?.[index];
    if(!node || !image) return;
    const current = imageNameLabel(image);
    const name = await openAssetNameDialog({title:tr('smart.renameImage'), value:current, placeholder:tr('smart.imageName'), cancelValue:null});
    if(name === null) return;
    const next = String(name || '').trim();
    if(!next || next === current) return;
    pushUndo();
    image.name = next;
    canvasState.selectedId = node.id;
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:node.id, index};
    render();
    scheduleSave();
}

function createNodeFromMenu(type){
    const p = createMenuPoint || viewportCenter();
    const groupId = canvasState.createMenuGroupId;
    closeCreateMenu();
    if(type === 'group') return createSmartGroupNode(p.x - 170, p.y - 110);
    let created = null;
    if(type === 'prompt') created = createPromptNode(p.x - 158, p.y - 97);
    else if(type === 'conversation') created = createConversationNode(p.x - 195, p.y - 215);
    else if(type === 'agent') created = createAgentNode(p.x - 191, p.y - 180);
    else if(type === 'local-edit') created = createLocalEditNode(p.x - 180, p.y - 165);
    else if(type === 'loop') created = createLoopNode(p.x - 135, p.y - 95);
    else created = createImageNodeAt(p);
    canvasState.createMenuGroupId = groupId;
    addCreatedNodeToMenuGroup(created);
    canvasState.createMenuGroupId = '';
    return created;
}

// 这些名字本来属于本模块，#200 从 smart-canvas.js 搬回（核心不引用它们，纯搬家）。
let createMenuPoint = {x:0, y:0};
const SMART_GROUP_MAX_VISIBLE_ROWS = 4;
function scheduleInteractionLayerRefresh(){
    if(interactionLayerRaf) return;
    interactionLayerRaf = requestAnimationFrame(() => {
        interactionLayerRaf = 0;
        refreshConnectionLayer();
        renderMinimap();
    });
}
const LOCAL_EDIT_NODE_W = 400;
const LOCAL_EDIT_NODE_H = 520;
function addCreatedNodeToMenuGroup(node){
    const group = canvasState.createMenuGroupId ? canvasState.nodes.find(n => n.id === canvasState.createMenuGroupId) : null;
    if(addNodeToSmartGroup(group, node)){
        // 通过分组小菜单新建的节点入组后自动整理（节点创建已压过 undo，这里不再重复）。
        arrangeSmartGroupMembers(group, {skipUndo:true});
        render();
        scheduleSave();
    }
}
