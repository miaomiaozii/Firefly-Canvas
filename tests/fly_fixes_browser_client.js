(function(){
    'use strict';
    const result = {trace: [], viewport: {width: innerWidth, height: innerHeight}, assertions: [], errors: [], pass: false};
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass: Boolean(pass), actual});
    const trace = (label) => result.trace.push(label + '@' + Math.round(performance.now()/1000));
    async function waitFor(predicate, timeout=12000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(await predicate()) return true; } catch(_) {}
            await sleep(50);
        }
        return false;
    }
    const resultId = `flyfixes-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    // 拦截所有任务状态/提交请求，记录真实后端返回（诊断 T5）
    const taskLog = [];
    window.__TASK_LOG = taskLog;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async function(input, init){
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        const response = await nativeFetch(input, init);
        try {
            if(url.indexOf('image-generation-tasks') !== -1){
                const cl = response.clone();
                const data = await cl.json();
                const taskStatus = data && (data.status !== undefined ? data.status : (Array.isArray(data) ? (data[0] && data[0].status) : undefined));
                taskLog.push({t:Math.round(performance.now()/1000), url:url.slice(-40), status:response.status, taskStatus:taskStatus, error:data && data.error ? String(data.error).slice(0,80) : undefined, hasResult:!!(data && data.result), keys:data && typeof data === 'object' ? Object.keys(data).slice(0,12).join(',') : typeof data});
            }
        } catch(_){}
        return response;
    };
    async function postResult(){
        if(result._posted) return;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        result._posted = true;
        await fetch('/__conversation_browser_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({kind:'final', result_id:resultId, ...result})
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    function evalFn(fn){ return Function('"use strict";return (' + fn + ')')(); }
    function nodes(){ return (0, eval)('nodes'); }
    function findNode(id){ return nodes().find(n => n && n.id === id); }
    function nodeName(nodeId){ const n = findNode(nodeId); return n && n.images && n.images[0] ? n.images[0].name : null; }
    function editMode(){ return (0, eval)('imageEditMode'); }
    function modalOpen(){ return document.getElementById('imageEditModal')?.classList.contains('open') === true; }
    function cropImageLoaded(){ const img = document.getElementById('cropImage'); return !!(img && img.naturalWidth > 0); }
    function cropStateReady(){ const c = (0, eval)('cropState'); return !!(c && c.w > 0 && c.h > 0); }
    async function fetchImage(url){
        const res = await fetch(url);
        if(!res.ok) throw new Error('fetch ' + url + ' -> ' + res.status);
        const bmp = await createImageBitmap(await res.blob());
        const c = document.createElement('canvas');
        c.width = bmp.width; c.height = bmp.height;
        const ctx = c.getContext('2d');
        ctx.drawImage(bmp, 0, 0);
        const data = ctx.getImageData(0, 0, c.width, c.height).data;
        return {w: bmp.width, h: bmp.height, data};
    }
    async function openEditor(nodeId){
        return waitFor(() => {
            try {
                const node = document.querySelector('#world .image-node[data-id="' + nodeId + '"]');
                if(!node) return false;
                evalFn('(id) => { openImageEditor(id, 0); return true; }')(nodeId);
                return modalOpen();
            } catch(_) { return false; }
        }, 6000);
    }
    async function runNodeGeneration(nodeId, promptText){
        // 通过 composer UI 触发生成（对齐 generation_browser_client.js 的 runNode）
        const nodeEl = document.querySelector('.image-node[data-id="' + nodeId + '"]');
        if(!nodeEl) return false;
        nodeEl.click();
        const composerOk = await waitFor(() => document.querySelector('#composer.open'), 8000);
        if(!composerOk) return false;
        const promptInput = document.querySelector('#promptInput');
        const runButton = document.querySelector('#runBtn');
        if(!promptInput || !runButton) return false;
        // composer 不会自动从提示词节点预填：显式写入测试 prompt
        promptInput.textContent = promptText || ('gen prompt ' + nodeId);
        promptInput.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
        const enabled = await waitFor(() => !runButton.disabled, 15000);
        if(!enabled){ result.errors.push('runButton disabled for ' + nodeId); return false; }
        runButton.click();
        return true;
    }
    async function waitApplied(nodeId, beforeName){
        return waitFor(() => {
            try {
                const n = findNode(nodeId);
                if(!n) return false;
                return !modalOpen() && n.images[0] && n.images[0].name !== beforeName;
            } catch(_) { return false; }
        }, 8000);
    }
    async function run(){
        trace('run-start');
        const bootOk = await waitFor(() => {
            try { return nodes().some(n => n.id === 'edit-node'); } catch(_) { return false; }
        }, 6000);
        trace('boot-done:' + bootOk);
        assertion('canvas boot with edit-node', bootOk, bootOk ? 'ok' : 'nodes missing');

        // ===== T1: 扩图红底 + 4:3 比例 =====
        trace('t1-outpaint-step');
        const t1Open = await openEditor('edit-node') && await waitFor(cropImageLoaded, 12000);
        assertion('T1 editor opened for outpaint', t1Open);
        if(t1Open){
            const ready = await waitFor(() => {
                try { evalFn('() => { setImageEditMode("outpaint", true); return true; }')(); return cropStateReady(); } catch(_) { return false; }
            }, 5000);
            assertion('T1 outpaint cropState ready', ready);
            if(ready){
                // 选 4:3 预设
                evalFn('() => { setOutpaintAspectPreset("4:3"); return true; }')();
                const preset = (0, eval)('outpaintAspectPreset');
                assertion('T1 4:3 preset active', preset === '4:3', preset);
                const before = nodeName('edit-node');
                evalFn('() => { applyImageEdit(); return true; }')();
                const applied = await waitApplied('edit-node', before);
                assertion('T1 outpaint applied and modal closed', applied);
                if(applied){
                    const img = findNode('edit-node').images[0];
                    assertion('T1 outpaint image name has _outpaint', (img.name || '').includes('_outpaint'), img.name);
                    try {
                        const pix = await fetchImage(img.url);
                        // 检查红底：找接近 #ff0044 (255,0,68) 的像素
                        let redCount = 0;
                        for(let i = 0; i < pix.data.length; i += 4){
                            if(pix.data[i] > 200 && pix.data[i+1] < 80 && pix.data[i+2] > 40 && pix.data[i+2] < 120) redCount++;
                        }
                        assertion('T1 red background present in composite', redCount >= 20, 'red px=' + redCount);
                        // 检查比例接近 4:3
                        const ratio = pix.w / pix.h;
                        assertion('T1 ratio close to 4:3', Math.abs(ratio - 4/3) < 0.15, 'w=' + pix.w + ' h=' + pix.h + ' ratio=' + ratio.toFixed(2));
                        // 检查 asset_id（无"媒体资源 ID"错误的前提）
                        assertion('T1 outpaint image has asset_id', !!img.asset_id, String(img.asset_id || ''));
                        // 检查 outpaintSize
                        const node = findNode('edit-node');
                        const os = node.outpaintSize || {};
                        assertion('T1 outpaintSize set', os.width > 0 && os.height > 0, JSON.stringify(os));
                        if(os.width && os.height){
                            const osRatio = os.width / os.height;
                            assertion('T1 outpaintSize ratio 4:3', Math.abs(osRatio - 4/3) < 0.2, 'osRatio=' + osRatio.toFixed(2));
                        }
                        assertion('T1 prompt draft set', (node.promptDraftText || '').includes('Remove red area'), String(node.promptDraftText));
                    } catch(e){ result.errors.push('T1 fetch: ' + e.message); }
                }
            }
        }

        // ===== T4: 历史原图删除 =====
        trace('t4-history-delete-step');
        // 通过 composer 运行 gen-node 生成
        await runNodeGeneration('gen-node', 't4gen prompt one');
        const genReady = await waitFor(() => {
            try { const n = findNode('gen-node'); return n && n.images && n.images.length > 0 && !n.pending; } catch(_) { return false; }
        }, 30000);
        assertion('T4 gen-node has image', genReady);
        if(genReady){
            const genImg = findNode('gen-node').images[0];
            const origUrl = genImg.url;
            const genName = genImg.name;
            // 打开历史，找到该图的原图删除按钮并点击
            const hasHistory = await waitFor(() => {
                try {
                    const logs = (0, eval)('canvas?.history || []');
                    return logs.length > 0;
                } catch(_) { return false; }
            }, 8000);
            assertion('T4 history logs exist', hasHistory);
            if(hasHistory && origUrl){
                // 调试：捕获节点图片与日志输出的 output_id 对比
                const dbg = await (async () => {
                    try {
                        const logs = (0, eval)('canvas?.history || []');
                        const log = logs.find(lg => (lg.outputs || []).some(o => (o.url || o.asset_id) && String(o.asset_id) === String(genImg.asset_id)));
                        const output = log ? (log.outputs || []).find(o => String(o.asset_id) === String(genImg.asset_id)) : null;
                        return {
                            nodeImgAsset: genImg.asset_id,
                            nodeImgOutputId: genImg.output_id || '(none)',
                            logFound: !!log,
                            logOutputId: output ? (output.output_id || '(none)') : '(no output)',
                            logOutputIndex: output ? output.outputIndex : -1,
                            historyId: log ? log.id : '(no log)',
                        };
                    } catch(e){ return {error: e.message}; }
                })();
                assertion('T4 debug ids', true, JSON.stringify(dbg));
                // 模拟删除：用 deleteSmartProjectOriginal
                const delOk = await waitFor(async () => {
                    try {
                        const logs = (0, eval)('canvas?.history || []');
                        const log = logs.find(lg => (lg.outputs || []).some(o => (o.url || o.asset_id) && String(o.asset_id) === String(genImg.asset_id)));
                        if(!log) return false;
                        const output = (log.outputs || []).find(o => String(o.asset_id) === String(genImg.asset_id));
                        if(!output) return false;
                        // 自动确认 confirm
                        (0, eval)('window.confirm = () => true;');
                        (0, eval)('deleteSmartProjectOriginal("' + log.id + '",' + (output.outputIndex ?? 0) + ')');
                        return true;
                    } catch(_) { return false; }
                }, 5000);
                assertion('T4 deleteSmartProjectOriginal invoked', delOk);
                // 等待删除生效（gen-node 图片应消失或变为已删除态）
                const deleted = await waitFor(() => {
                    try {
                        const n = findNode('gen-node');
                        return !n || !n.images || n.images.length === 0 || !n.images[0].url || n.images[0].url !== origUrl;
                    } catch(_) { return false; }
                }, 10000);
                // 删除后的状态调试
                const afterDbg = await (async () => {
                    try {
                        const n = findNode('gen-node');
                        const logs = (0, eval)('canvas?.history || []');
                        const log = logs.find(lg => lg.id === dbg.historyId);
                        return {
                            nodeImages: n && n.images ? n.images.map(im => ({asset: (im.asset_id||'').slice(0,8), outId: im.output_id||'(none)', url: (im.url||'').slice(-20)})) : 'no node',
                            logStatus: log ? log.status : '(no log)',
                            logOutputs: log && log.outputs ? log.outputs.length : -1,
                            logTombstones: log && log.deletedOutputTombstones ? log.deletedOutputTombstones.length : -1,
                        };
                    } catch(e){ return {error: e.message}; }
                })();
                assertion('T4 debug after delete', true, JSON.stringify(afterDbg));
                assertion('T4 image removed from gen-node after delete', deleted);
            }
        }

        // ===== T5: 生成失败取回 =====
        trace('t5-failure-recovery-step');
        // 通过 composer 运行 gen-node2（首次调用抛错）
        await runNodeGeneration('gen-node2', 't5gen prompt two');
        // 等待 gen-node2 失败（首次调用抛错）
        const failReady = await waitFor(() => {
            try {
                const n = findNode('gen-node2');
                const failedTasks = n && n.failedGenerationTasks;
                return failedTasks && failedTasks.length > 0;
            } catch(_) { return false; }
        }, 30000);
        assertion('T5 gen-node2 has failed task', failReady, 'failedGenerationTasks=' + JSON.stringify((findNode('gen-node2')||{}).failedGenerationTasks));
        if(failReady){
            // 检查失败 UI 出现重试按钮
            const hasRetry = await waitFor(() => {
                try { return !!document.querySelector('.generation-failed-retry[data-image-task-retry="gen-node2"]'); } catch(_) { return false; }
            }, 6000);
            assertion('T5 retry button shown on failure', hasRetry);
            if(hasRetry){
                // 点击重试（第二次调用成功）
                const btn = document.querySelector('.generation-failed-retry[data-image-task-retry="gen-node2"]');
                btn.click();
                const recovered = await waitFor(() => {
                    try {
                        const n = findNode('gen-node2');
                        return n && n.images && n.images.length > 0 && n.images[0] && n.images[0].url;
                    } catch(_) { return false; }
                }, 20000);
                assertion('T5 image recovered after retry', recovered);
            }
        }

        // ===== T2: PS 往返机制验证 =====
        trace('t2-ps-roundtrip-step');
        const projectId = (0, eval)('canvas?.project_id || canvas?.project');
        // 通过 API 创建 PS round-trip
        let psRequestId = '';
        try {
            const rtResp = await fetch(`/api/project-library/${projectId}/photoshop/round-trips`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({source_node_id: 'edit-node', source_media_id: findNode('edit-node').images[0].asset_id, ttl_seconds: 60})
            });
            if (rtResp.ok) {
                const rtData = await rtResp.json();
                psRequestId = rtData.request_id;
            }
        } catch(e) { result.errors.push('T2 round-trip create: ' + e.message); }
        assertion('T2 PS round-trip created', !!psRequestId, psRequestId || 'create failed');

        if (psRequestId) {
            // 验证源图可获取
            const srcOk = await (async () => {
                try {
                    const srcResp = await fetch(`/api/project-library/${projectId}/photoshop/round-trips/${psRequestId}/source`);
                    if (!srcResp.ok) return {ok: false, status: srcResp.status};
                    const srcData = await srcResp.json();
                    return {ok: true, hasBase64: !!(srcData.bytes_base64 && srcData.bytes_base64.length > 0), mime: srcData.mime_type};
                } catch(e) { return {ok: false, error: e.message}; }
            })();
            assertion('T2 PS round-trip source accessible', srcOk.ok && srcOk.hasBase64, JSON.stringify(srcOk));

            // 验证画布按钮 sendSmartNodeImageToPhotoshop 可用（函数存在 + 可调用）
            const sendBtnOk = await (async () => {
                try {
                    const fnExists = typeof (0, eval)('sendSmartNodeImageToPhotoshop') === 'function';
                    if (!fnExists) return false;
                    // 调用函数，不抛错即成功
                    (0, eval)('sendSmartNodeImageToPhotoshop')('edit-node', 0);
                    await sleep(300);
                    return true; // 没抛错就认为成功
                } catch(e) { result.errors.push('T2 sendBtn check: ' + e.message); return false; }
            })();
            assertion('T2 sendSmartNodeImageToPhotoshop button works', sendBtnOk);
        }

        // ===== T3: 局部编辑节点验证 =====
        trace('t3-local-edit-step');
        // 检查节点存在
        const leNode = findNode('local-edit-node');
        assertion('T3 local-edit node exists', !!leNode, leNode ? 'ok' : 'node missing');

        if (leNode) {
            // 检查节点已连接原图
            const hasOriginal = !!leNode.original_asset_id;
            assertion('T3 local-edit has original asset', hasOriginal, leNode.original_asset_id || 'missing');

            // 检查节点有 prompt
            const hasPrompt = !!(leNode.prompt && leNode.prompt.length > 0);
            assertion('T3 local-edit has prompt', hasPrompt, leNode.prompt || 'missing');

            // 通过 UI 提交局部编辑
            const submitBtn = document.querySelector('[data-local-edit-submit="local-edit-node"]');
            assertion('T3 local-edit submit button exists', !!submitBtn, submitBtn ? 'ok' : 'button missing');

            if (submitBtn) {
                submitBtn.click();
                // 等待输出图片生成
                const hasOutput = await waitFor(() => {
                    try {
                        const n = findNode('local-edit-node');
                        return n && n.images && n.images.length > 0 && n.images[0].url;
                    } catch(_) { return false; }
                }, 15000);
                assertion('T3 local-edit output generated', hasOutput, hasOutput ? 'ok' : 'no output after submit');
            }
        }

        // ===== T6: 画布项目重命名 =====
        trace('t6-rename-step');
        {
            const originalTitle = String((0, eval)('canvas?.title') || '');
            assertion('T6 original title readable', originalTitle !== '', originalTitle);
            (0, eval)('openSmartTitleEdit()');
            const inputOk = await waitFor(() => !!document.getElementById('smartTitleInput'), 3000);
            assertion('T6 rename input opened', inputOk, document.getElementById('smartTitleInput') ? 'ok' : 'missing');
            if(inputOk){
                const input = document.getElementById('smartTitleInput');
                input.value = 'e2e-renamed-canvas';
                input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
                await sleep(300);
                const newTitle = String((0, eval)('canvas?.title') || '');
                assertion('T6 rename applied to canvas.title', newTitle === 'e2e-renamed-canvas', newTitle);
                // 恢复原标题（避免影响其他测试段）
                (0, eval)('openSmartTitleEdit()');
                const inputOk2 = await waitFor(() => !!document.getElementById('smartTitleInput'), 3000);
                let restored = '';
                if(inputOk2){
                    const input2 = document.getElementById('smartTitleInput');
                    input2.value = originalTitle;
                    input2.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
                    await sleep(300);
                    restored = String((0, eval)('canvas?.title') || '');
                }
                assertion('T6 title restored', restored === originalTitle, restored);
            }
        }

        // ===== T7: 整理按钮位于左上角工具栏 =====
        trace('t7-arrange-position-step');
        {
            const arrangeBtn = document.getElementById('smartArrangeBtn');
            assertion('T7 arrange button exists', !!arrangeBtn, arrangeBtn ? 'ok' : 'missing');
            const inToolbar = !!(arrangeBtn && arrangeBtn.closest('.smart-top-left'));
            assertion('T7 arrange button in top-left toolbar', inToolbar, inToolbar ? 'in .smart-top-left' : 'not in toolbar');
        }

        // ===== T9: 资源库删除（完整 UI 路径：引用警告 → 确认 → 服务端硬删 + 引用清理）=====
        trace('t9-media-delete-step');
        {
            const t9Asset = await (async () => {
                try {
                    const items = (0, eval)('projectMediaLibrary?.items || []');
                    return (items || []).find(it => String(it.name || '').indexOf('t9-seed') === 0) || null;
                } catch(_){ return null; }
            })();
            assertion('T9 t9 asset present in project library', !!t9Asset, t9Asset ? (t9Asset.name || '') : 'missing');
            if(t9Asset){
                const t9AssetId = String(t9Asset.asset_id || '');
                const toggle = document.getElementById('assetToggle');
                if(toggle) toggle.click();
                const delBtn = await waitFor(() => {
                    try { return !!document.querySelector('[data-delete-project-asset="' + t9AssetId + '"]'); } catch(_) { return false; }
                }, 5000);
                assertion('T9 delete button rendered in asset library', delBtn);
                if(delBtn){
                    // 记录 DELETE 响应码：服务端 500 时前端会 toast 失败且不刷新全局资源库 —— 直接抓住请求层故障
                    let t9DeleteStatus = null;
                    const origFetch = window.fetch;
                    window.fetch = async(...fargs) => {
                        const fresp = await origFetch(...fargs);
                        try {
                            const furl = String(typeof fargs[0] === 'string' ? fargs[0] : (fargs[0] && fargs[0].url) || '');
                            if(furl.indexOf('/media/') !== -1 && fargs[1] && String(fargs[1].method || '').toUpperCase() === 'DELETE'){
                                t9DeleteStatus = fresp.status;
                            }
                        } catch(_){}
                        return fresp;
                    };
                    document.querySelector('[data-delete-project-asset="' + t9AssetId + '"]').click();
                    const dialogOk = await waitFor(() => !!document.querySelector('.asset-delete-warning-dialog'), 5000);
                    assertion('T9 reference warning dialog shown', dialogOk);
                    const dialogText = dialogOk ? (document.querySelector('.asset-delete-warning-dialog').textContent || '') : '';
                    assertion('T9 dialog lists referencing node', dialogText.indexOf('T9 Ref Node') !== -1, dialogText.slice(0, 140));
                    if(dialogOk){
                        document.querySelector('.asset-delete-warning-confirm').click();
                        // 成功分支会调用 removeProjectMediaFromCache；500/异常分支不会 —— 资产仍在库中即可抓住请求失败
                        const assetGone = await waitFor(() => {
                            try {
                                const items = (0, eval)('projectMediaLibrary?.items || []');
                                return !(items || []).some(it => String(it.asset_id || '') === t9AssetId);
                            } catch(_) { return false; }
                        }, 10000);
                        assertion('T9 asset removed from library after delete', assetGone);
                        const cleaned = await waitFor(() => {
                            try {
                                const n = findNode('t9-ref-node');
                                return !!(n && n.images && n.images.length === 0);
                            } catch(_) { return false; }
                        }, 10000);
                        assertion('T9 delete request returned 200', t9DeleteStatus === 200, 'status=' + t9DeleteStatus);
                        try { window.fetch = origFetch; } catch(_){}
                        assertion('T9 node image cleared in UI after delete', cleaned);
                        const serverState = await (async () => {
                            try {
                                const projResp = await fetch('/api/project-library/' + projectId);
                                const projData = await projResp.json();
                                const t9Node = ((projData && projData.canvas && projData.canvas.nodes) || []).find(n => n && n.id === 't9-ref-node');
                                const mediaResp = await fetch('/api/project-library/' + projectId + '/media/' + t9AssetId);
                                return {
                                    status: mediaResp.status,
                                    nodeImages: t9Node && Array.isArray(t9Node.images) ? t9Node.images.length : -1,
                                    tombstones: t9Node && Array.isArray(t9Node.deletedInputTombstones) ? t9Node.deletedInputTombstones.length : -1,
                                };
                            } catch(e){ return {error: e.message}; }
                        })();
                        assertion('T9 server canvas reference cleaned', serverState.nodeImages === 0, JSON.stringify(serverState));
                        assertion('T9 deleted-input tombstone recorded', serverState.tombstones === 1, 'tombstones=' + serverState.tombstones);
                        assertion('T9 media file no longer served', serverState.status !== 200, 'status=' + serverState.status);
                    }
                }
            }
        }

        trace('run-end');
        await postResult();
    }
    run().catch(err => { result.errors.push('run: ' + (err && err.message)); return postResult(); });
    setTimeout(() => { if(!result.pass) postResult(); }, 150000);
})();
