// 画布批注层（issue #272；#259 产品决策·维护者已批：批注=画布可持久对象）。
// 产品口径：①批注存 ops.db 新租户 anno_*（additive 迁移 anno_001；可撤销、刷新恢复、
// 随画板走）；②批注→改图经 #242 确认门由 runAll 逐条转化（下一票，本模块只预留
// status 显示样式）；③批注不进 #262 任务中心首版。
//
// 数据流：
//   内存 canvasState.annotations = [{id, node_id, text, x, y, status, created_at, updated_at}]
//   ↔ 后端 /api/project-library/{id}/annotations（app/routers/api_project_annotations.py，
//     ops.db anno_annotations 表；无 ops.db = 空列表）。
//
// 锚定语义：node_id（画布节点 id，随画板走）+ 节点内归一化坐标 x/y ∈ [0,1]
// （画布缩放不失真）。孤儿批注（节点已删）隐藏不渲染、不删库（下票 runAll 前
// 清理）；节点 id 重建后批注自动复现。
//
// 接线面（最小）：
//   - smart-canvas.js render()：renderAnnotationLayer()（每次画布渲染后重绘层）
//   - smart-canvas.js loadCanvas：restorePersistedAnnotations()（刷新恢复，#251 同模式）
//   - canvas-undo.js snapshotForUndo：annotations 进 undo 快照（可撤销）
//   - 本文件事件用 world 捕获阶段委托，不碰 bindNodeEvents（与 #262 并行改 smart-canvas.js
//     的冲突面最小化）。

function annoState(){
    if(!canvasState.annotations) canvasState.annotations = [];
    return canvasState.annotations;
}

function annoProjectId(){
    // canvas 由 smart-canvas.js 声明（本模块先加载，运行时才调用）；typeof 守卫防提前调用。
    if(typeof canvas === 'undefined') return '';
    return canvas?.project_id || canvas?.project || '';
}

function annoApiUrl(projectId, suffix){
    return `/api/project-library/${encodeURIComponent(projectId)}/annotations${suffix ? `/${suffix}` : ''}`;
}

function annoTruncate(text, max=24){
    const s = String(text || '');
    return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

// ── 渲染：批注气泡 + 钉按钮（每次 render() 后调用；幂等：先清旧层再注入）────────

function renderAnnotationLayer(){
    const worldEl = document.getElementById('world');
    if(!worldEl) return;
    worldEl.querySelectorAll('.anno-layer').forEach(el => el.remove());
    const annotations = annoState();
    if(!annotations.length) return;
    const nodeById = new Map((canvasState.nodes || []).map(n => [n.id, n]));
    for(const a of annotations){
        if(!a || !a.node_id) continue;
        const node = nodeById.get(a.node_id);
        if(!node) continue;  // 孤儿批注（节点已删）：隐藏，不删库
        const nodeEl = worldEl.querySelector(`.image-node[data-id="${CSS.escape(String(a.node_id))}"]`);
        if(!nodeEl) continue;
        const layer = document.createElement('div');
        layer.className = 'anno-layer';
        layer.dataset.annoFor = a.node_id;
        layer.innerHTML = annoBubbleHtml(a);
        nodeEl.appendChild(layer);
    }
    // 钉按钮：有图片的节点（空节点/提示词/Agent 节点不开放）
    for(const node of (canvasState.nodes || [])){
        if(!(node.images || []).length) continue;
        const nodeEl = worldEl.querySelector(`.image-node[data-id="${CSS.escape(String(node.id))}"]`);
        if(!nodeEl || nodeEl.querySelector(':scope > .anno-pin')) continue;
        const pin = document.createElement('button');
        pin.className = 'anno-pin';
        pin.type = 'button';
        pin.title = tr('smart.annoPin');
        pin.setAttribute('aria-label', tr('smart.annoPin'));
        pin.textContent = '+';
        nodeEl.appendChild(pin);
    }
}

function annoBubbleHtml(a){
    const converted = a.status === 'converted';
    return `<div class="anno-bubble${converted ? ' anno-converted' : ''}" data-anno-id="${a.id}" role="button" tabindex="0" style="left:${a.x * 100}%;top:${a.y * 100}%" title="${escapeHtml(a.text)}${converted ? '（已转任务）' : ''}">
        <span class="anno-bubble-dot"></span><span class="anno-bubble-text">${escapeHtml(annoTruncate(a.text))}</span>
        <button class="anno-bubble-del" type="button" title="${escapeHtml(tr('smart.annoDelete'))}" aria-label="${escapeHtml(tr('smart.annoDelete'))}">×</button>
    </div>`;
}

// ── 编辑器（节点内小弹层：textarea + 保存/取消）───────────────────────────────

let annoEditorEl = null;
let annoEditorSave = null;

function closeAnnotationEditor(){
    if(annoEditorEl){ annoEditorEl.remove(); annoEditorEl = null; }
    annoEditorSave = null;
}

function openAnnotationEditor(nodeId, annoId, x, y){
    const projectId = annoProjectId();
    if(!projectId || !canvas) return;
    const worldEl = document.getElementById('world');
    const nodeEl = worldEl ? worldEl.querySelector(`.image-node[data-id="${CSS.escape(String(nodeId))}"]`) : null;
    if(!nodeEl) return;
    closeAnnotationEditor();
    const existing = annoId != null ? annoState().find(a => a.id === annoId) : null;

    const editor = document.createElement('div');
    editor.className = 'anno-editor';
    const ta = document.createElement('textarea');
    ta.maxLength = 500;  // 与后端 ANNOTATION_MAX_TEXT_LEN=500 同口径
    ta.placeholder = tr('smart.annoPlaceholder');
    ta.value = existing ? existing.text : '';
    const actions = document.createElement('div');
    actions.className = 'anno-editor-actions';
    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'anno-editor-save';
    saveBtn.textContent = tr('smart.annoSave');
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.textContent = tr('smart.annoCancel');
    actions.append(saveBtn, cancelBtn);
    editor.append(ta, actions);

    const host = nodeEl.querySelector(':scope > .anno-layer') || nodeEl;
    host.appendChild(editor);
    const leftPct = Math.min(0.9, Math.max(0.1, Number(x) || 0.8));
    const topPct = Math.min(0.85, Math.max(0.05, Number(y) || 0.05));
    editor.style.left = `${leftPct * 100}%`;
    editor.style.top = `${topPct * 100}%`;
    if(leftPct > 0.5) editor.style.transform = 'translateX(-100%)';

    annoEditorEl = editor;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    annoEditorSave = async () => {
        const text = ta.value.trim();
        if(!text){ closeAnnotationEditor(); return; }
        // undo：先压快照（snapshotForUndo 已含当前 annotations），再改状态、发请求。
        pushUndo();
        try {
            let saved;
            if(existing){
                const res = await fetch(annoApiUrl(projectId, String(existing.id)), {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text}),
                });
                if(!res.ok) throw new Error(`HTTP ${res.status}`);
                saved = await res.json();
                const idx = annoState().findIndex(a => a.id === existing.id);
                if(idx >= 0) annoState()[idx] = saved; else annoState().push(saved);
            } else {
                const res = await fetch(annoApiUrl(projectId, ''), {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({node_id: String(nodeId), text, x: Number(x) || 0.8, y: Number(y) || 0.05}),
                });
                if(!res.ok) throw new Error(`HTTP ${res.status}`);
                saved = await res.json();
                annoState().push(saved);
            }
            closeAnnotationEditor();
            renderAnnotationLayer();
        } catch(err){
            console.warn('[canvas-annotations] 批注保存失败', err);
            // 回滚：状态未改，把刚压的 no-op 快照弹掉，避免空撤销条目。
            undoStack.pop();
            syncUndoRedoButtons();
            toast(tr('smart.annoToastFail'));
        }
    };
    ta.addEventListener('keydown', e => {
        if(e.key === 'Escape'){ e.preventDefault(); e.stopPropagation(); closeAnnotationEditor(); }
        else if(e.key === 'Enter' && (e.ctrlKey || e.metaKey)){ e.preventDefault(); void annoEditorSave(); }
    });
    saveBtn.addEventListener('click', () => void annoEditorSave());
    cancelBtn.addEventListener('click', () => closeAnnotationEditor());
}

async function deleteAnnotation(annoId){
    const projectId = annoProjectId();
    if(!projectId || !canvas) return;
    const idx = annoState().findIndex(a => a.id === annoId);
    if(idx < 0) return;
    const before = JSON.parse(JSON.stringify(annoState()));
    pushUndo();
    closeAnnotationEditor();
    annoState().splice(idx, 1);
    renderAnnotationLayer();
    try {
        const res = await fetch(annoApiUrl(projectId, String(annoId)), {method: 'DELETE'});
        if(!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch(err){
        console.warn('[canvas-annotations] 批注删除失败', err);
        // 回滚：恢复本地状态 + 弹掉刚压的快照。
        canvasState.annotations = before;
        undoStack.pop();
        syncUndoRedoButtons();
        renderAnnotationLayer();
        toast(tr('smart.annoToastFail'));
    }
}

// ── 刷新恢复（#251 restorePersistedUndoStack 同模式；loadCanvas 完成后调用）────

async function restorePersistedAnnotations(){
    const projectId = annoProjectId();
    if(!projectId || !canvas) return;
    // loadCanvas = 新画布载入（刷新/切项目）：先清旧项目批注，再拉当前项目的。
    canvasState.annotations = [];
    try {
        const res = await fetch(annoApiUrl(projectId, ''));
        if(!res.ok){ renderAnnotationLayer(); return; }  // 404（无 ops.db）= 首次使用
        const data = await res.json();
        const items = (data && Array.isArray(data.items)) ? data.items : [];
        canvasState.annotations = items.filter(a => a && a.id != null && a.node_id);
        renderAnnotationLayer();
    } catch(err){
        // 静默：恢复失败只回到「无批注」，不影响画布可用。
        renderAnnotationLayer();
    }
}

// ── 事件委托（world 捕获阶段：先于节点拖动/选中/点空白取消选中）─────────────────

(function bindAnnotationEvents(){
    const worldEl = document.getElementById('world');
    if(!worldEl) return;
    // mousedown 捕获拦截：批注元素上的按下不触发节点拖动/选中
    worldEl.addEventListener('mousedown', e => {
        if(e.target.closest('.anno-pin, .anno-bubble, .anno-editor')) e.stopPropagation();
    }, true);
    worldEl.addEventListener('click', e => {
        const pin = e.target.closest('.anno-pin');
        if(pin){
            const nodeEl = pin.closest('.image-node');
            const nodeId = nodeEl ? nodeEl.dataset.id : '';
            if(nodeId) openAnnotationEditor(nodeId, null, 0.8, 0.05);
            e.stopPropagation();
            return;
        }
        const del = e.target.closest('.anno-bubble-del');
        if(del){
            const bubble = del.closest('.anno-bubble');
            const id = bubble ? Number(bubble.dataset.annoId) : NaN;
            if(!Number.isNaN(id)) void deleteAnnotation(id);
            e.stopPropagation();
            return;
        }
        const bubble = e.target.closest('.anno-bubble');
        if(bubble){
            const id = Number(bubble.dataset.annoId);
            const nodeEl = bubble.closest('.image-node');
            const nodeId = nodeEl ? nodeEl.dataset.id : '';
            const a = Number.isNaN(id) ? null : annoState().find(x => x.id === id);
            if(nodeId && a) openAnnotationEditor(nodeId, id, a.x, a.y);
            e.stopPropagation();
            return;
        }
        // 编辑器内部点击：吞掉，避免冒泡到画布触发取消选中
        if(e.target.closest('.anno-editor')) e.stopPropagation();
    }, true);
    // 画布点空白 / Esc 全局键：Esc 由编辑器自身处理；画布空白点击经 world bubble 逻辑，
    // 编辑器在捕获阶段已 stopPropagation，不会被误关。
})();
