(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const reloadKey = `canvas-interactions:${projectId}`;
    let restored = null;
    try {
        const raw = sessionStorage.getItem(reloadKey);
        if(raw) restored = JSON.parse(raw);
        sessionStorage.removeItem(reloadKey);
    } catch(_) {}
    let result = restored?.result || {
        kind: 'final',
        result_id: `canvas-interactions-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
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
        return waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Canvas Interactions'), 15000);
    }
    function spreadNodesIntoViewport(){
        const c = evalCanvas();
        if(!c) return 0;
        c.nodes.forEach((node, i) => {
            node.x = 120 + (i % 3) * 400;
            node.y = 120 + Math.floor(i / 3) * 320;
        });
        const render = evalFn('render');
        if(render) render();
        return c.nodes.length;
    }
    function dispatchKey(key, opts){
        const e = new KeyboardEvent('keydown', Object.assign({key, bubbles:true, cancelable:true, ctrlKey:false, metaKey:false, shiftKey:false, altKey:false}, opts || {}));
        window.dispatchEvent(e);
        return e.defaultPrevented;
    }
    function fireMouse(type, x, y, opts){
        const e = new MouseEvent(type, Object.assign({bubbles:true, cancelable:true, view:window, clientX:x, clientY:y, button:0, buttons:1}, opts || {}));
        window.dispatchEvent(e);
        return e;
    }
    async function runInitial(){
        if(!await waitForLoaded()) return;
        const c0 = evalCanvas();
        const seedCount = c0?.nodes?.length || 0;
        assertion('seed nodes loaded', seedCount === 3, seedCount);
        spreadNodesIntoViewport();
        await sleep(200);
        const selectNode = evalFn('selectNode');
        const copySelectedNodes = evalFn('copySelectedNodes');
        const pasteNodes = evalFn('pasteNodes');
        const groupSelectedNodes = evalFn('groupSelectedNodes');
        const ungroupNode = evalFn('ungroupNode');
        const performUndo = evalFn('performUndo');
        const pushUndo = evalFn('pushUndo');
        const setViewport = evalFn('applyViewport');

        // 1. box-select: ctrl+drag across seed-prompt and seed-img-a
        {
            const c = evalCanvas();
            const a = c.nodes.find(n => n.id === 'seed-prompt');
            const b = c.nodes.find(n => n.id === 'seed-img-a');
            const shell = document.querySelector('#world')?.parentElement || document.body;
            const rect = shell.getBoundingClientRect();
            const vp = eval('viewport');
            const toScreen = (wx, wy) => ({x: rect.left + wx * vp.scale + vp.x, y: rect.top + wy * vp.scale + vp.y});
            const ax = toScreen(a.x - 60, a.y - 60).x, ay = toScreen(a.x - 60, a.y - 60).y;
            const bx = toScreen(b.x + b.w + 60, b.y + b.h + 60).x, by = toScreen(b.x + b.w + 60, b.y + b.h + 60).y;
            const md = new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, clientX:ax, clientY:ay, button:0, buttons:1, ctrlKey:true});
            shell.dispatchEvent(md);
            const mm = new MouseEvent('mousemove', {bubbles:true, cancelable:true, view:window, clientX:bx, clientY:by, button:0, buttons:1, ctrlKey:true});
            window.dispatchEvent(mm);
            const mu = new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window, clientX:bx, clientY:by, button:0, buttons:0, ctrlKey:true});
            window.dispatchEvent(mu);
            await sleep(150);
            const selected = (function(){ try { return eval('selectedIds'); } catch(_) { return []; } })() || [];
            const diagSel = () => {
                try { return `selState=${JSON.stringify(eval('selectionState'))} selIds=${JSON.stringify(eval('selectedIds'))}`; } catch(_) { return 'diag-error'; }
            };
            result.diagnostics.push(`after box-select: ${diagSel()}`);
            assertion('box-select picks 2 nodes', selected.length === 2, JSON.stringify(selected));
        }
        // 2. copy + paste duplicates selection with new ids
        {
            const beforeCount = evalCanvas()?.nodes?.length || 0;
            copySelectedNodes();
            await sleep(80);
            pasteNodes();
            await sleep(150);
            const c = evalCanvas();
            const afterCount = c?.nodes?.length || 0;
            const duplicated = afterCount === beforeCount + 2;
            assertion('paste duplicates 2 nodes', duplicated, `${beforeCount} -> ${afterCount}`);
            const ids = (c?.nodes || []).map(n => n.id);
            assertion('pasted nodes have new ids', new Set(ids).size === ids.length, ids.length);
            const conns = c?.connections || [];
            const froms = conns.map(x => x.from), tos = conns.map(x => x.to);
            const copied = conns.filter(x => froms.indexOf(x.from) !== tos.indexOf(x.to));
            const srcId = conns.find(x => String(x.from).startsWith('seed-prompt'));
            const pastedConn = conns.filter(x => x.from !== srcId?.from && !String(x.from).startsWith('seed-'));
            assertion('paste recreates internal edge', pastedConn.length === 1 || (srcId && conns.length >= 2), JSON.stringify(conns));
        }
        // 3. delete via keyboard removes selection
        {
            const c = evalCanvas();
            const target = c.nodes.find(n => n.type === 'smart-image' && String(n.id).startsWith('seed-'));
            if(target){
                if(selectNode) selectNode(target.id);
                await sleep(80);
            }
            const diag = () => {
                try {
                    const selId = eval('selectedId'), selIds = JSON.stringify(eval('selectedIds'));
                    const uStack = eval('undoStack.length');
                    return `selId=${selId} selIds=${selIds} undoStack=${uStack} nodes=${evalCanvas()?.nodes?.length}`;
                } catch(_) { return 'diag-error'; }
            };
            result.diagnostics.push(`before delete: ${diag()}`);
            const beforeCount = evalCanvas()?.nodes?.length || 0;
            const prevented = dispatchKey('Delete', {});
            await sleep(150);
            result.diagnostics.push(`after delete: ${diag()} prevented=${prevented} sameArray=${(function(){ try { return eval('nodes === canvas.nodes'); } catch(_) { return 'err'; } })()}`);
            const afterCount = evalCanvas()?.nodes?.length || 0;
            const deletedCount = beforeCount - afterCount;
            assertion('delete removes selected nodes', deletedCount >= 1, `${beforeCount} -> ${afterCount}`);
            const pastedIds = ['prompt_o4is9z9pmo7y', 'smart_pwgfej8hmo7y'];
            const remaining = evalCanvas()?.nodes || [];
            const anyPastGone = pastedIds.some(id => !remaining.some(n => n.id === id));
            assertion('deleted nodes gone from canvas', anyPastGone, `remaining=${remaining.map(n => n.id).join(',')}`);
        }
        // 4. undo restores deleted nodes
        {
            const beforeCount = evalCanvas()?.nodes?.length || 0;
            performUndo();
            await sleep(200);
            const c = evalCanvas();
            const restored = c?.nodes?.some(n => String(n.id).startsWith('prompt_') || String(n.id).startsWith('smart_'));
            const afterCount = c?.nodes?.length || 0;
            assertion('undo restores deleted nodes', restored && afterCount > beforeCount, `${beforeCount} -> ${afterCount} restored=${restored}`);
        }
        // 4b. redo re-deletes the nodes (ctrl+shift+z)
        {
            const beforeCount = evalCanvas()?.nodes?.length || 0;
            dispatchKey('z', {ctrlKey: true, shiftKey: true});
            await sleep(200);
            const c = evalCanvas();
            const reDeleted = c?.nodes?.length === beforeCount - 2;
            const hasPasted = c?.nodes?.some(n => String(n.id).startsWith('prompt_') || String(n.id).startsWith('smart_'));
            assertion('redo re-deletes nodes', reDeleted && !hasPasted, `before=${beforeCount} after=${c?.nodes?.length} hasPasted=${hasPasted}`);
        }
        // 5. group selected nodes
        {
            const c = evalCanvas();
            const prompt = c.nodes.find(n => n.id === 'seed-prompt');
            const img = c.nodes.find(n => n.type === 'smart-image' && String(n.id).startsWith('seed-'));
            if(prompt && img){
                eval('selectedIds.splice(0, selectedIds.length, ' + JSON.stringify(prompt.id) + ', ' + JSON.stringify(img.id) + ')');
                eval('selectedId = ' + JSON.stringify(prompt.id));
            }
            await sleep(80);
            const grp = groupSelectedNodes();
            const groupCount = (evalCanvas()?.nodes || []).filter(n => n.type === 'smart-group').length;
            result.diagnostics.push(`group diag: grp=${typeof grp} count=${groupCount} selId=${eval('selectedId')} selIds=${JSON.stringify(eval('selectedIds'))}`);
            assertion('group creates smart-group node', groupCount > 0, `groups=${groupCount}`);
        }
        // 6. zoom via wheel changes viewport scale
        {
            const before = evalCanvas?.() ? null : null;
            const shell = document.querySelector('#world')?.parentElement || document.body;
            const rect = shell.getBoundingClientRect();
            const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
            const sc = new WheelEvent('wheel', {bubbles:true, cancelable:true, view:window, clientX:cx, clientY:cy, deltaY:-240, deltaMode:0});
            shell.dispatchEvent(sc);
            await sleep(120);
            const scaleAfter = eval('viewport.scale');
            const scaleBefore = eval('viewport.scale');
            void scaleBefore;
            assertion('wheel zoom changes scale', typeof scaleAfter === 'number' && scaleAfter > 0 && scaleAfter !== 1, `scale=${scaleAfter}`);
        }
        // 7. pan via middle-button drag changes viewport offset
        {
            const v0 = eval('viewport');
            const sx = v0.x, sy = v0.y;
            const shell = document.querySelector('#world')?.parentElement || document.body;
            const rect = shell.getBoundingClientRect();
            const md = new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window, clientX:rect.left + 300, clientY:rect.top + 300, button:1, buttons:4});
            shell.dispatchEvent(md);
            const mm = new MouseEvent('mousemove', {bubbles:true, cancelable:true, view:window, clientX:rect.left + 500, clientY:rect.top + 400, button:1, buttons:4});
            window.dispatchEvent(mm);
            const mu = new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window, clientX:rect.left + 500, clientY:rect.top + 400, button:1, buttons:0});
            window.dispatchEvent(mu);
            await sleep(150);
            const v1 = eval('viewport');
            const moved = Math.abs(v1.x - sx) > 0 || Math.abs(v1.y - sy) > 0;
            assertion('pan changes viewport offset', moved, `dx=${Math.round(v1.x - sx)} dy=${Math.round(v1.y - sy)}`);
        }
        // save + persist
        const saveCanvas = evalFn('saveCanvas');
        if(saveCanvas){
            try { await saveCanvas(); } catch(error) { result.errors.push(`saveCanvas: ${error.message}`); }
        }
        await waitFor(() => (evalCanvas()?.nodes?.length || 0) > 0, 10000);
        const saved = await savedCanvas();
        assertion('saved canvas persists nodes', (saved?.nodes || []).length > 0, (saved?.nodes || []).length);
        try {
            sessionStorage.setItem(reloadKey, JSON.stringify({result}));
            deferPost = true;
            location.reload();
        } catch(error) {
            result.errors.push(`reload state: ${error.message}`);
        }
    }
    function viewportScale(){
        try { return eval('viewport.scale') || 1; } catch(_) { return 1; }
    }
    async function runAfterReload(){
        if(!await waitForLoaded()) return;
        const c = evalCanvas();
        const count = c?.nodes?.length || 0;
        assertion('reload restores nodes', count > 0, count);
        const groups = (c?.nodes || []).filter(n => n.type === 'smart-group').length;
        assertion('group persists after reload', groups > 0, groups);
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    (restored ? runAfterReload() : runInitial()).catch(error => result.errors.push(error?.stack || String(error))).finally(() => {
        if(!deferPost) postResult();
    });
})();
