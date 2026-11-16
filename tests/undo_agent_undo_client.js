(function(){
    'use strict';
    // #375：agent 服务端写入的撤回链（独立浏览器会话跑，避免与主撤销套件的
    // 保存/竞态动力学相互干扰——2026-11-16 实测合并两阶段到单页会让末条既有断言
    // 在增量保存基线上抖动）。传输层（真 WS→前端）由 tools/e2e/scenario_delete_undo.sh
    // d4/d5/d4r/d5r 在真 uvicorn 上覆盖；这里经 handleCanvasUpdatedMessage（真实 WS
    // 的同一入口）喂与服务器广播同构的 {type:'canvas_updated', source:'agent'} 帧，
    // 再走 mergeReloadCanvasNow 真实合并——harness 的 HTTP 前置不转发 WS 升级。
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const result = {
        kind: 'final',
        result_id: `undo-agent-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        clientScriptLoaded: true,
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        diagnostics: [],
        pass: false,
    };
    let posted = false;
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
    async function postResult(){
        if(posted) return;
        posted = true;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    function undoBtn(){ return document.querySelector('#smartUndoButton'); }
    function createViaMenu(type){
        document.querySelector('#createMenuButton, [data-create-menu]')?.click?.();
        const card = [...document.querySelectorAll('[data-create-type]')].find(c => c.dataset.createType === type);
        card?.click?.();
        return Boolean(card);
    }
    function createdNodes(){
        return (evalCanvas()?.nodes || []).filter(n => !['seed-image','seed-prompt'].includes(n.id));
    }
    async function run(){
        if(!await waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Undo Redo Suite'), 15000)) return;
        if(!await waitFor(() => (evalCanvas()?.nodes || []).length === 2, 10000)){
            result.errors.push('seed nodes missing');
        }
        // 用户自己的两步：给撤回链一个真实的「agent 写入前」状态。两次创建之间留 1.5s：
        // capture/commit 型创建共用一个 pending 快照，不留间隔两步会合并成一步，
        // 「合并前快照」会少一个用户节点（实测 2026-11-16）。
        createViaMenu('image');
        await waitFor(() => createdNodes().length === 1, 8000);
        await sleep(1500);
        createViaMenu('image');
        await waitFor(() => createdNodes().length === 2, 8000);
        assertion('user nodes created', createdNodes().length === 2, `created=${createdNodes().length}`);
        const USER_IDS = createdNodes().map(n => n.id).sort();
        // 先把用户两步显式落盘（与主套件同口径：headless 下自动保存周期不可靠，
        // 套件靠显式 saveCanvas 驱动）——否则 agent 合并前的增量基线少一步，
        // agent 撤回的 diff 发不出第二个用户节点的 upsert（实测 2026-11-16）。
        const saveNow = evalFn('saveCanvas');
        if (saveNow) { try { await saveNow({automatic: false}); } catch(_) {} }
        let userPersisted = false;
        for (let i = 0; i < 40; i++) {
            for (let j = 0; j < 20 && eval('canvasSyncInFlight'); j++) await sleep(250);
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if (response.ok) {
                    const ids = (((await response.json()).canvas || {}).nodes || []).map(n => n.id);
                    if (USER_IDS.every(id => ids.includes(id))) { userPersisted = true; break; }
                }
            } catch(_) {}
            if (saveNow && i % 5 === 4) { try { await saveNow({automatic: false}); } catch(_) {} }
            await sleep(300);
        }
        assertion('user nodes persisted before agent write', userPersisted, `want=${JSON.stringify(USER_IDS)}`);

        // #375：agent 服务端写入必须进撤回链，并且撤回要真正落盘（服务端也被移除）。
        // 起点清撤回栈：agent 段的 Ctrl+Z 必须精确弹出 agent 合并前快照，
        // 不能连带弹掉用户自己的步骤。
        eval('undoStack.length = 0; redoStack.length = 0; syncUndoRedoButtons();');
        const writeResp = await fetch(`/api/projects/${encodeURIComponent(projectId)}/canvas/nodes`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'x-agent-project': projectId},
            body: JSON.stringify({node: {type: 'image', title: 'agent probe', x: 900, y: 120, w: 180, h: 140}}),
        });
        const writeStatus = writeResp.status;
        const agentProbeId = String(((await writeResp.json().catch(() => ({}))) || {}).node_id || '');
        assertion('agent node write accepted', [200, 201].includes(writeStatus) && agentProbeId.startsWith('node-'), `${writeStatus} ${agentProbeId}`);
        const metaResp = await fetch(`/api/canvases/${encodeURIComponent(projectId)}/meta`);
        const metaJson = await metaResp.json().catch(() => ({}));
        const handler = evalFn('handleCanvasUpdatedMessage');
        if (handler) {
            handler({type: 'canvas_updated', canvas_id: projectId, source: 'agent', updated_at: Number(metaJson.updated_at || Date.now()), client_id: 'server-side-agent'});
        } else {
            result.errors.push('handleCanvasUpdatedMessage not resolvable — source guard untestable in harness');
        }
        await (evalFn('waitForCanvasRefresh') || evalFn('mergeReloadCanvasNow'))();
        const agentVisible = await waitFor(() => (evalCanvas()?.nodes || []).some(n => n.id === agentProbeId), 8000);
        assertion('agent node merged into canvas', agentVisible, agentVisible);
        assertion('undo enabled after agent merge', undoBtn()?.disabled === false, undoBtn()?.disabled);
        undoBtn()?.click();
        const agentUndone = await waitFor(() => !(evalCanvas()?.nodes || []).some(n => n.id === agentProbeId), 8000);
        assertion('undo removes agent node', agentUndone, agentUndone);
        assertion('user nodes intact after agent undo', createdNodes().map(n => n.id).sort().join(',') === USER_IDS.join(','), `before=${JSON.stringify(USER_IDS)} after=${JSON.stringify(createdNodes().map(n => n.id))}`);
        let agentGoneOnServer = false;
        const persistDeadline = performance.now() + 15000;
        while (performance.now() < persistDeadline) {
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if (response.ok) {
                    const canvas = (await response.json()).canvas || null;
                    agentGoneOnServer = !(canvas?.nodes || []).some(n => n.id === agentProbeId);
                    if (agentGoneOnServer) break;
                }
            } catch(_) {}
            await sleep(200);
        }
        assertion('agent undo persists to server', agentGoneOnServer, agentGoneOnServer);

        // 落盘终局：等 agent 撤回自己调度的保存链全部落定（去抖 3s + 在飞保存），
        // 服务端必须同时「有全部用户节点、无 agent 节点」。
        for (let i = 0; i < 40 && eval('canvasSyncInFlight'); i++) await sleep(250);
        await sleep(4000);
        // 终局对齐：显式存一次（saveCanvas 内部会把 canvas.nodes 与 canvasState.nodes
        // 同步后做增量 diff），并轮询重发直到服务端终局正确——测的是终局一致，
        // 不是 headless 下的自动保存时序。
        let saved = null;
        const saveSettle = evalFn('saveCanvas');
        const deadline = performance.now() + 20000;
        while(performance.now() < deadline){
            if(saveSettle){ try { await saveSettle({automatic: false}); } catch(_) {} }
            for (let j = 0; j < 40 && eval('canvasSyncInFlight'); j++) await sleep(250);
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if(response.ok){
                    saved = (await response.json()).canvas || null;
                    const ids = (saved?.nodes || []).map(n => n.id);
                    if(saved && !ids.includes(agentProbeId) && USER_IDS.every(id => ids.includes(id))) break;
                }
            } catch(_) {}
            await sleep(400);
        }
        const serverIds = (saved?.nodes || []).map(n => n.id);
        assertion('server has no agent node after settle', !serverIds.includes(agentProbeId), JSON.stringify(serverIds));
        assertion('server canvas persists user nodes', USER_IDS.every(id => serverIds.includes(id)) && serverIds.length === 4, JSON.stringify(serverIds) + ' local=' + JSON.stringify((evalCanvas()?.nodes || []).map(n => n.id)) + ' want=' + JSON.stringify(USER_IDS));
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    run().catch(error => result.errors.push(`${error.message} @ ${error.stack}`)).finally(postResult);
    setTimeout(() => { if(!posted) { result.errors.push('timeout'); void postResult(); } }, 90000);
})();
