(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const reloadKey = `node-e2e:${projectId}`;
    let restored = null;
    try {
        const raw = sessionStorage.getItem(reloadKey);
        if(raw) restored = JSON.parse(raw);
        sessionStorage.removeItem(reloadKey);
    } catch(_) {}
    let result = restored?.result || {
        kind: 'final',
        result_id: `node-e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        clientScriptLoaded: true,
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        diagnostics: [],
        pass: false,
    };
    result.viewport = {width: innerWidth, height: innerHeight};
    let posted = false;
    let deferPost = false;
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
    const evalCanvas = () => { try { return eval('canvas'); } catch(_) { return null; } };
    const evalFn = name => { try { const f = eval(name); return typeof f === 'function' ? f : null; } catch(_) { return null; } };
    async function waitFor(predicate, timeout=15000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(predicate()) return true; } catch(_) {}
            await sleep(60);
        }
        return false;
    }
    async function savedCanvas(timeout=10000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if(response.ok) return (await response.json()).canvas || {};
            } catch(_) {}
            await sleep(100);
        }
        return {};
    }
    async function postResult(){
        if(posted) return;
        posted = true;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    async function waitForLoaded(){
        return waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Node E2E Suite'), 15000);
    }
    function nodeEl(id){
        return document.querySelector(`[data-id="${id}"]`);
    }
    function createViaMenu(type){
        document.querySelector('#createMenuButton, [data-create-menu]')?.click?.();
        const card = [...document.querySelectorAll('[data-create-type]')].find(c => c.dataset.createType === type);
        card?.click?.();
        return Boolean(card);
    }
    function waitNode(type){
        const idPrefix = type === 'image' ? 'smart' : type === 'prompt' ? 'prompt' : type === 'conversation' ? 'conversation' : type === 'agent' ? 'agent' : type === 'local-edit' ? 'local-edit' : type === 'loop' ? 'loop' : 'group';
        return waitFor(() => [...document.querySelectorAll('.image-node')].some(el => String(el.dataset.id || '').startsWith(idPrefix)), 8000);
    }
    function setInput(el, value){
        if(!el) return;
        el.value = value;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
    }
    function spreadNodesIntoViewport(){
        const c = evalCanvas();
        if(!c) return 0;
        const spacing = 220;
        c.nodes.forEach((node, i) => {
            node.x = 80 + (i % 4) * spacing;
            node.y = 80 + Math.floor(i / 4) * spacing;
        });
        const render = evalFn('render');
        if(render) render();
        return c.nodes.length;
    }
    async function runInitial(){
        if(!await waitForLoaded()) return;
        const created = {};
        for(const type of ['image','prompt','conversation','agent','local-edit','loop','group']){
            const clicked = createViaMenu(type);
            const appeared = await waitNode(type);
            created[type] = {clicked, appeared};
            assertion(`create ${type} node renders`, clicked && appeared, `${clicked}/${appeared}`);
            await sleep(80);
        }
        const allNodes = () => [...document.querySelectorAll('.image-node')].map(el => el.dataset.id || '');
        const ids = allNodes();
        const promptId = ids.find(id => id.startsWith('prompt'));
        const imageId = ids.find(id => id.startsWith('smart') && !id.startsWith('smart-image-'));
        assertion('prompt node exists', Boolean(promptId), promptId || '');
        assertion('image node exists', Boolean(imageId), imageId || '');
        const spread = spreadNodesIntoViewport();
        assertion('nodes spread into viewport', spread >= 9, spread);
        await sleep(200);
        const connectInputNode = evalFn('connectInputNode');
        let linked = false;
        if(connectInputNode && promptId && imageId){
            try {
                linked = connectInputNode(promptId, imageId) === true || evalCanvas()?.connections?.length > 0;
            } catch(error) {
                result.errors.push(`connectInputNode: ${error.message}`);
            }
        }
        await sleep(300);
        const connCount = () => (evalCanvas()?.connections || []).length;
        const edgePathCount = () => document.querySelectorAll('#world path').length;
        assertion('connect prompt->image creates an edge', linked && connCount() > 0, `linked=${linked} conns=${connCount()} paths=${edgePathCount()}`);
        if(promptId){
            const promptText = nodeEl(promptId)?.querySelector('[data-prompt-text], textarea, [contenteditable="true"]');
            setInput(promptText, '一只发光的萤火虫在夜空中');
            await sleep(120);
        }
        const saveCanvas = evalFn('saveCanvas');
        if(saveCanvas){
            try { await saveCanvas(); } catch(error) { result.errors.push(`saveCanvas: ${error.message}`); }
        }
        await waitFor(() => evalCanvas()?.connections?.length > 0, 10000);
        {
            const diffs = [];
            const sweepStart = performance.now();
            const deadline = performance.now() + 4000;
            let lastClean = true;
            const hasDurableCanvasChanges = evalFn('hasDurableCanvasChanges');
            while(performance.now() < deadline){
                const dirty = hasDurableCanvasChanges ? hasDurableCanvasChanges() : false;
                if(dirty !== lastClean){
                    diffs.push(`state flip at +${Math.round(performance.now() - sweepStart)}ms: dirty=${dirty}`);
                    lastClean = dirty;
                }
                await sleep(120);
            }
            if(diffs.length) result.diagnostics.push(`durable-dirty flips: ${JSON.stringify(diffs)}`);
            if(!lastClean && typeof evalFn('durableSnapshotForCanvas') === 'function'){
                try {
                    const durableSnapshotForCanvas = evalFn('durableSnapshotForCanvas');
                    const persistedDurableSnapshot = eval('persistedDurableSnapshot');
                    const local = durableSnapshotForCanvas(evalCanvas());
                    const persisted = persistedDurableSnapshot;
                    const diffNotes = [];
                    const localNodes = local.nodes || [];
                    const persNodes = persisted.nodes || [];
                    for(let i = 0; i < Math.max(localNodes.length, persNodes.length); i++){
                        const a = JSON.stringify(localNodes[i] || null), b = JSON.stringify(persNodes[i] || null);
                        if(a !== b) diffNotes.push(`node[${i}] local=${a} persisted=${b}`);
                    }
                    const le = JSON.stringify(local.edges || {}), pe = JSON.stringify(persisted.edges || {});
                    if(le !== pe) diffNotes.push(`edges local=${le} persisted=${pe}`);
                    result.diagnostics.push(`durable-diff: ${diffNotes.slice(0, 6).join(' || ')}`);
                } catch(error) {
                    result.diagnostics.push(`durable-diff skipped: ${error.message}`);
                }
            }
        }
        const saved = await savedCanvas();
        assertion('saved canvas contains 9+ nodes', (saved?.nodes || []).length >= 9, (saved?.nodes || []).length);
        const savedTypes = new Set((saved?.nodes || []).map(n => n.type));
        for(const t of ['smart-image','smart-prompt','smart-conversation','smart-agent','local-edit','smart-loop','smart-group']){
            assertion(`saved canvas has node type ${t}`, savedTypes.has(t), [...savedTypes].join(','));
        }
        const edgeIds = saved?.connections || [];
        assertion('saved canvas has the prompt->image edge', edgeIds.some(e => String(e.from || '').startsWith('prompt')), JSON.stringify(edgeIds));
        const promptNode = saved?.nodes?.find(n => String(n.id || '').startsWith('prompt'));
        assertion('prompt text persisted', Boolean(promptNode?.text), promptNode?.text || '');
        try {
            sessionStorage.setItem(reloadKey, JSON.stringify({result}));
            deferPost = true;
            location.reload();
        } catch(error) {
            result.errors.push(`reload state: ${error.message}`);
        }
    }
    async function runAfterReload(){
        if(!await waitForLoaded()) return;
        const restoredNodes = await waitFor(() => [...document.querySelectorAll('.image-node')].length >= 9, 20000);
        assertion('reload restores all nodes', restoredNodes, [...document.querySelectorAll('.image-node')].length);
        const connCount = () => (evalCanvas()?.connections || []).length;
        const restoredConns = await waitFor(() => connCount() > 0, 20000);
        const edgePaths = document.querySelectorAll('#world path').length;
        const conns = connCount();
        assertion('reload restores edges', restoredConns && conns > 0, `paths=${edgePaths} conns=${conns}`);
        const promptText = [...document.querySelectorAll('.image-node')].find(el => String(el.dataset.id || '').startsWith('prompt'))?.querySelector('[data-prompt-text], textarea, [contenteditable="true"]');
        assertion('reload restores prompt text', Boolean(promptText && (promptText.value || promptText.textContent || '').includes('萤火虫')), promptText?.value || promptText?.textContent || '');
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    (restored ? runAfterReload() : runInitial()).catch(error => result.errors.push(error?.stack || String(error))).finally(() => {
        if(!deferPost) postResult();
    });
})();
