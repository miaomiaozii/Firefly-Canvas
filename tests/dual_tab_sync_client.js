(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const role = query.get('role') || 'a';
    const projectId = query.get('project') || '';
    const result = {
        kind: 'final',
        result_id: `dual-tab-${role}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        role: role,
        clientScriptLoaded: true,
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        diagnostics: [],
        pass: false,
    };
    let posted = false;
    let appWsLog = [];
    let syncTimerSamples = [];
    let timerProbe = null;
    window.addEventListener('load', () => {
        result.diagnostics.push(`window load fired at ${Math.round(performance.now())}ms`);
    });
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass: Boolean(pass), actual});
    const evalCanvas = () => { try { return eval('canvas'); } catch(_) { return null; } };
    async function waitFor(predicate, timeout=20000){
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
        if(timerProbe) clearInterval(timerProbe);
        result.diagnostics.push('app-ws final: ' + JSON.stringify(appWsLog));
        result.diagnostics.push('sync-timer samples: ' + syncTimerSamples.join(''));
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        const body = JSON.stringify(result);
        for(let attempt = 0; attempt < 3; attempt++){
            try {
                const response = await fetch('/__conversation_browser_results', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body
                });
                if(response.ok) return;
            } catch(error) {
                result.errors.push(`result upload attempt ${attempt}: ${error.message}`);
            }
            await sleep(1000);
        }
    }
    function nodeIdsInDom(){
        return [...document.querySelectorAll('.image-node')].map(el => el.dataset.id || '');
    }
    function nodeInDom(id){
        return Boolean(document.querySelector(`.image-node[data-id="${id}"]`));
    }
    function createImageNodeViaMenu(){
        document.querySelector('#createMenuButton, [data-create-menu]')?.click?.();
        const card = [...document.querySelectorAll('[data-create-type]')].find(c => c.dataset.createType === 'image');
        card?.click?.();
        return Boolean(card);
    }
    async function waitUnknownNode(known, timeout){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            const found = nodeIdsInDom().find(id => !known.includes(id));
            if(found) return found;
            await sleep(60);
        }
        return '';
    }
    async function waitServerNode(id, timeout=20000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if(response.ok){
                    const data = await response.json();
                    const node = (data.canvas?.nodes || []).find(item => item.id === id);
                    if(node) return data.canvas;
                }
            } catch(_) {}
            await sleep(100);
        }
        return null;
    }
    function disableMetaPoll(){
        try {
            eval('clearInterval(canvasMetaPollTimer)');
            return true;
        } catch(_) {
            return false;
        }
    }
    function saveState(){
        return document.querySelector('#projectSaveState')?.dataset?.state || '';
    }
    async function checkFinalState(ownId, peerId, savedCanvas){
        const ids = (savedCanvas?.nodes || []).map(n => n.id);
        const unique = new Set(ids);
        assertion('server canvas keeps every seed node and both new nodes', ['seed-prompt', 'seed-image', ownId, peerId].every(id => ids.includes(id)), ids);
        assertion('merged server canvas has no duplicate node ids', unique.size === ids.length, ids.length);
        assertion(`tab ${role} DOM shows both tabs nodes at the end`, nodeInDom(ownId) && nodeInDom(peerId), {own: nodeInDom(ownId), peer: nodeInDom(peerId)});
        assertion(`tab ${role} never showed a save error`, saveState() !== 'error', saveState());
    }
    function traceHandle(data){
        try {
            const out = [];
            out.push('type:' + (data.type === 'canvas_updated'));
            const canvasId = eval('canvasId');
            out.push('id:' + (canvasId && data.canvas_id === canvasId));
            const smartId = eval('smartClientId');
            out.push('self:' + (data.client_id && data.client_id === smartId));
            out.push('inflight:' + eval('canvasSyncInFlight'));
            const remote = Number(data.updated_at || 0);
            const cur = Number(eval('canvas.updated_at') || 0);
            out.push(`stale:${remote && remote <= cur} (${remote} vs ${cur})`);
            return out.join(' ');
        } catch(e) { return 'ERR ' + e.message; }
    }
    function probeEval(){
        const out = {};
        try { out.direct = typeof canvasId; } catch(e) { out.direct = 'ERR ' + e.message; }
        try { out.evalType = eval('typeof canvasId'); } catch(e) { out.evalType = 'ERR ' + e.message; }
        try { out.evalGet = eval('canvasId'); } catch(e) { out.evalGet = 'ERR ' + e.message; }
        try { out.indirect = (0, eval)('canvasId'); } catch(e) { out.indirect = 'ERR ' + e.message; }
        try { out.frameGet = window.eval('canvasId'); } catch(e) { out.frameGet = 'ERR ' + e.message; }
        return out;
    }
    function attachDebugWs(){
        const debugWs = new WebSocket(`ws://${location.host}/ws/stats?client_id=${role}-debug`);
        debugWs.onopen = () => result.diagnostics.push('debug-ws open');
        debugWs.onmessage = event => {
            const text = String(event.data);
            result.diagnostics.push(`debug-ws msg: ${text.slice(0, 160)}`);
            let pageState = '';
            try {
                pageState = 'ready=' + document.readyState
                    + ' typeofCanvasId=' + (typeof canvasId)
                    + ' typeofCanvas=' + (typeof canvas)
                    + ' onloadCalled=' + (window.__canvasOnloadDone ? 'y' : '?')
                    + ' isTop=' + (window === window.top);
            } catch(_) { pageState = 'probe-err'; }
            result.diagnostics.push('page-state: ' + pageState);
            result.diagnostics.push('probe: ' + JSON.stringify(probeEval()));
            try {
                const data = JSON.parse(text);
                if(data.type === 'canvas_updated') result.diagnostics.push('trace: ' + traceHandle(data));
            } catch(_) {}
        };
        debugWs.onclose = () => result.diagnostics.push('debug-ws closed');
        debugWs.onerror = () => result.diagnostics.push('debug-ws error');
    }
    function instrumentAppWebSocket(){
        const realWebSocket = window.WebSocket;
        const logs = [];
        window.WebSocket = function(url, protocols){
            const ws = protocols ? new realWebSocket(url, protocols) : new realWebSocket(url);
            const entry = {url: String(url), state: 'created', messages: 0};
            logs.push(entry);
            ws.addEventListener('open', () => { entry.state = 'open'; });
            ws.addEventListener('message', () => { entry.messages += 1; });
            ws.addEventListener('close', event => { entry.state = `closed(${event.code})`; });
            ws.addEventListener('error', () => { entry.state = 'error'; });
            return ws;
        };
        window.WebSocket.prototype = realWebSocket.prototype;
        window.WebSocket.CONNECTING = realWebSocket.CONNECTING;
        window.WebSocket.OPEN = realWebSocket.OPEN;
        window.WebSocket.CLOSING = realWebSocket.CLOSING;
        window.WebSocket.CLOSED = realWebSocket.CLOSED;
        return logs;
    }
    async function run(){
        appWsLog = instrumentAppWebSocket();
        if(!await waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Dual Tab Sync Suite'), 20000)){
            assertion('page loaded', false, document.querySelector('#smartTitle')?.textContent || '');
            return;
        }
        const metaPollDisabled = disableMetaPoll();
        attachDebugWs();
        await sleep(500);
        const initialIds = nodeIdsInDom();
        const initialUpdatedAt = Number(evalCanvas()?.updated_at || 0);
        const state = (() => {
            try { return {canvasId: eval('canvasId'), updatedAt: Number(eval('canvas.updated_at') || 0), syncInFlight: eval('canvasSyncInFlight')}; }
            catch(_) { return {}; }
        })();
        result.diagnostics.push(`role=${role} metaPollDisabled=${metaPollDisabled} state=${JSON.stringify(state)}`);
        syncTimerSamples = [];
        timerProbe = setInterval(() => {
            try {
                const timer = eval('canvasSyncTimer');
                if(timer) syncTimerSamples.push('t');
                else syncTimerSamples.push('-');
            } catch(_) { syncTimerSamples.push('?'); }
        }, 200);
        const stateProbe = setInterval(() => {
            try {
                const st = {u: Number(eval('canvas.updated_at') || 0), f: Boolean(eval('canvasSyncInFlight'))};
                result.diagnostics.push('state@' + Math.round(performance.now()) + ' ' + JSON.stringify(st));
            } catch(_) {}
        }, 1000);
        setTimeout(() => clearInterval(stateProbe), 40000);
        result.diagnostics.push('app-ws initial: ' + JSON.stringify(appWsLog));
        assertion('seed nodes render before any edit', initialIds.length >= 2, initialIds);
        if(role === 'a'){
            // 阶段 1：A 创建节点并等待自身落盘（广播在落盘后发出）
            const clicked = createImageNodeViaMenu();
            const idA = await waitUnknownNode(initialIds, 10000);
            assertion('tab A creates an image node through the create menu', clicked && idA, {clicked, idA});
            if(!idA) return;
            const savedA = await waitServerNode(idA, 20000);
            assertion('tab A persisted its own node', Boolean(savedA), idA);
            if(!savedA) return;
            // 阶段 2：等待 B 创建的节点经 WS 实时到达（meta poll 已禁用，只可能是 WS 广播）
            const known = [...initialIds, idA];
            const t0 = performance.now();
            const idB = await waitUnknownNode(known, 45000);
            const latencyMs = Math.round(performance.now() - t0);
            result.diagnostics.push(`role=a receiving tabB node latency=${latencyMs}ms id=${idB || ''}`);
            assertion('tab A receives tab B node without reload', Boolean(idB), idB || '');
            assertion('tab A receives tab B node via websocket within 10s', Boolean(idB) && latencyMs <= 10000, latencyMs);
            if(!idB) return;
            const savedBoth = await waitServerNode(idB, 20000) || savedA;
            assertion('tab A observes tab B node persisted on server', Boolean(savedBoth && (savedBoth.nodes || []).some(n => n.id === idB)), idB);
            assertion('tab A canvas updated_at advanced beyond its initial load', Number(evalCanvas()?.updated_at || 0) > initialUpdatedAt, {before: initialUpdatedAt, after: Number(evalCanvas()?.updated_at || 0)});
            await checkFinalState(idA, idB, savedBoth);
        } else {
            // 阶段 1：等待 A 创建的节点实时到达（meta poll 已禁用，只可能是 WS 广播）
            const t0 = performance.now();
            const idA = await waitUnknownNode(initialIds, 45000);
            const latencyMs = Math.round(performance.now() - t0);
            result.diagnostics.push(`role=b receiving tabA node latency=${latencyMs}ms id=${idA || ''}`);
            assertion('tab B receives tab A node without reload', Boolean(idA), idA || '');
            assertion('tab B receives tab A node via websocket within 10s', Boolean(idA) && latencyMs <= 10000, latencyMs);
            if(!idA) return;
            const savedA = await waitServerNode(idA, 20000);
            assertion('tab B observes tab A node persisted on server', Boolean(savedA), idA);
            if(!savedA) return;
            // 阶段 2：B 创建节点。A 已保存过，B 的 base_revision 必然过期，
            // 本步骤必然走过 409 冲突 + rebase 重试路径。
            const clicked = createImageNodeViaMenu();
            const idB = await waitUnknownNode([...initialIds, idA], 10000);
            assertion('tab B creates an image node through the create menu', clicked && idB && idB !== idA, {clicked, idB});
            if(!idB) return;
            const savedBoth = await waitServerNode(idB, 20000);
            assertion('tab B persisted its own node after conflict rebase', Boolean(savedBoth), idB);
            if(!savedBoth) return;
            assertion('tab B canvas updated_at advanced beyond its initial load', Number(evalCanvas()?.updated_at || 0) > initialUpdatedAt, {before: initialUpdatedAt, after: Number(evalCanvas()?.updated_at || 0)});
            await checkFinalState(idB, idA, savedBoth);
        }
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(() => postResult());
})();
