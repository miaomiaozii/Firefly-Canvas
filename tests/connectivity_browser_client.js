(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const browserRole = query.get('role') || 'probe';
    const result = {
        kind: 'final',
        role: browserRole,
        result_id: `connectivity-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
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
    // #214 probe (diagnostics only, no behavior/assertion changes): wrap the app's
    // history-card creation and node-mutation entry points to identify (a) which
    // call path creates task-less failed history cards and (b) which path clears
    // gen-conn.images after the concurrent stage completes.
    const probe214 = {events: []};
    function probeInstall(){
        const t0 = performance.now();
        probe214.t0 = t0;
        const ts = () => Math.round(performance.now() - t0);
        const snap = () => {
            try {
                const c = evalCanvas();
                const n = (c?.nodes || []).find(x => x.id === 'gen-conn');
                return {
                    images: (n?.images || []).map(i => String(i.url || i.name || '').slice(-20)),
                    pending: n?.pending ?? null,
                    pendingTasks: (n?.pendingTasks || []).map(p => String(p.taskId || '').slice(-8)),
                    pendingLogs: (c?.history || []).filter(l => l.status === 'pending').map(l => String(l.id || '').slice(-8) + ':' + String(l.request?.task_id || l.task_id || '-').slice(-8)),
                    history: (c?.history || []).map(l => String(l.status) + '|' + String(l.task_id || l.request?.task_id || '-').slice(-8) + '|' + String(l.prompt || '').slice(0, 12)),
                };
            } catch(_) { return null; }
        };
        const record = event => probe214.events.push(Object.assign({t: ts()}, event));
        const origAdd = evalFn('addSmartGenerationHistory');
        if (typeof origAdd === 'function') {
            window.addSmartGenerationHistory = function(opts = {}){
                const before = snap();
                const stack = (new Error().stack || '').split('\n').slice(2, 5).map(s => s.trim()).join(' <- ');
                record({type: 'addHistory', prompt: String(opts.run?.prompt || '').slice(0, 20), outputs: (opts.outputs || []).length, error: String(opts.error || '').slice(0, 80), stack, before});
                return origAdd.call(this, opts);
            };
        }
        const origAbsorb = evalFn('absorbProjectGenerationResult');
        if (typeof origAbsorb === 'function') {
            window.absorbProjectGenerationResult = function(result, taskId = ''){
                const before = snap();
                const out = origAbsorb.call(this, result, taskId);
                const after = snap();
                record({type: 'absorb', task: String(taskId || '').slice(-8), status: result?.status, recorded: Boolean(result?.history_recorded), revision: result?.project_revision, fnImages: (result?.final_node?.images || []).length, imgBefore: before?.images?.length, imgAfter: after?.images?.length, changed: (before?.images?.length) !== (after?.images?.length)});
                return out;
            };
        }
        const origMerge = evalFn('applyMergedServerCanvas');
        if (typeof origMerge === 'function') {
            window.applyMergedServerCanvas = function(serverCanvas){
                const before = snap();
                const out = origMerge.call(this, serverCanvas);
                const after = snap();
                record({type: 'merge', revision: serverCanvas?.revision, imgBefore: before?.images?.length, imgAfter: after?.images?.length, changed: (before?.images?.length) !== (after?.images?.length), images: after?.images});
                return out;
            };
        }
        const origSave = evalFn('saveCanvas');
        if (typeof origSave === 'function') {
            window.saveCanvas = async function(...args){
                const before = snap();
                try {
                    const out = await origSave.apply(this, args);
                    record({type: 'save', imgAtSave: before?.images?.length});
                    return out;
                } catch(error){
                    record({type: 'save', imgAtSave: before?.images?.length, error: String(error?.message || error)});
                    throw error;
                }
            };
        }
        let lastSig = '';
        probe214.timer = setInterval(() => {
            const s = snap();
            if (!s) return;
            const sig = JSON.stringify(s);
            if (sig !== lastSig){ lastSig = sig; record({type: 'state', snap: s}); }
        }, 50);
    }
    async function waitFor(predicate, timeout=15000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(await predicate()) return true; } catch(_) {}
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
    function smartSettings(){
        try { return eval('settings'); } catch(_) { return null; }
    }
    function nodeById(id){
        return (evalCanvas()?.nodes || []).find(n => n.id === id);
    }
    function pillText(controlClass){
        return document.querySelector(`#dynamicParams .${controlClass} .smart-pill span`)?.textContent?.trim() || '';
    }
    function clickPill(controlClass){
        const el = document.querySelector(`#dynamicParams .${controlClass} .smart-pill`);
        if(!el) return false;
        el.click();
        return true;
    }
    function clickParam(controlClass, param, value){
        const btn = [...document.querySelectorAll(`#dynamicParams .${controlClass} [data-smart-param="${param}"][data-smart-value="${value}"]`)][0];
        if(!btn) return false;
        btn.click();
        return true;
    }
    async function run(){
        if(!await waitFor(() => (evalCanvas()?.nodes || []).length >= 3, 20000)) return;
        if(browserRole === 'secondary'){
            await fetch('/__multiclient_ready', {method:'POST'});
            let observedTaskIds = [];
            const observedBothPending = await waitFor(() => {
                const current = nodeById('gen-conn');
                const pending = current?.pendingTasks || [];
                const logs = evalCanvas()?.history || [];
                const ids = pending.map(item => String(item.taskId || '')).filter(Boolean);
                if(ids.length < 2 || !ids.every(id => logs.some(log => log.status === 'pending' && String(log.request?.task_id || '') === id))) return false;
                observedTaskIds = ids;
                evalFn('openSmartCanvasLog')?.();
                return ids.every(id => document.querySelector(`[data-history-cancel-task="${CSS.escape(id)}"]`));
            }, 30000);
            assertion('second client renders one independent status card per overlapping task', observedBothPending, {
                observedTaskIds,
                pending:nodeById('gen-conn')?.pendingTasks || [],
                logs:(evalCanvas()?.history || []).filter(log => log.status === 'pending').map(log => ({id:log.id, task:log.request?.task_id})),
            });
            if(observedBothPending) await fetch('/__multiclient_observed', {method:'POST'});
            const observedIndependentTerminals = await waitFor(() => {
                if(observedTaskIds.length < 2) return false;
                const logs = evalCanvas()?.history || [];
                const states = observedTaskIds.map(id => logs.find(log => String(log.task_id || log.request?.task_id || '') === id)?.status || '');
                return states.includes('cancelled') && states.includes('succeeded');
            }, 60000);
            const terminalStates = observedTaskIds.map(id => {
                const log = (evalCanvas()?.history || []).find(item => String(item.task_id || item.request?.task_id || '') === id);
                return {taskId:id, status:log?.status || ''};
            });
            assertion('second client merges remote cancelled and succeeded records by task identity', observedIndependentTerminals, terminalStates);
            assertion('second client clears only terminal pending identities', !(nodeById('gen-conn')?.pendingTasks || []).some(item => observedTaskIds.includes(String(item.taskId || ''))), nodeById('gen-conn')?.pendingTasks || []);
            return;
        }
        if(sessionStorage.getItem('t09-submission-recovery-phase') === 'verify'){
            probeInstall();
            const carried = JSON.parse(sessionStorage.getItem('t09-submission-recovery-result') || '{}');
            result.assertions.push(...(carried.assertions || []));
            result.errors.push(...(carried.errors || []));
            result.diagnostics.push(...(carried.diagnostics || []));
            const recoveredAfterReload = await waitFor(() => {
                const current = nodeById('gen-conn');
                const logs = evalCanvas()?.history || [];
                return !(current?.generationSubmissions || []).length
                    && logs.some(log => log.status === 'succeeded' && String(log.prompt || '').includes('RESPONSE_LOST_RECOVERY'));
            }, 60000);
            const recoveredCaptured = await (await fetch('/__captured_requests')).json();
            assertion('reload replays an unknown submission with the same durable key', recoveredAfterReload, {
                submissions:nodeById('gen-conn')?.generationSubmissions || [],
                matchingLogs:(evalCanvas()?.history || []).filter(log => String(log.prompt || '').includes('RESPONSE_LOST_RECOVERY')).map(log => ({task:log.task_id, status:log.status})),
            });
            assertion('response-loss recovery invokes the fake adapter only once', recoveredCaptured.filter(item => String(item.prompt || '').includes('RESPONSE_LOST_RECOVERY')).length === 1, recoveredCaptured.filter(item => String(item.prompt || '').includes('RESPONSE_LOST_RECOVERY')).length);
            if(probe214.timer) clearInterval(probe214.timer);
            result.diagnostics.push(`probe214-verify=${JSON.stringify(probe214.events)}`);
            sessionStorage.removeItem('t09-submission-recovery-phase');
            sessionStorage.removeItem('t09-submission-recovery-result');
            return;
        }
        const secondaryReady = await waitFor(async () => {
            const response = await fetch('/__multiclient_ready');
            return response.ok && Boolean((await response.json()).ready);
        }, 20000);
        assertion('second real browser client is ready before overlap begins', secondaryReady, secondaryReady);
        const settings = smartSettings();
        probeInstall();
        assertion('settings loaded', Boolean(settings), Boolean(settings));

        const mergeNode = evalFn('mergeSmartNode');
        const mergedOverlap = mergeNode?.(
            {id:'merge-probe', type:'smart-image', images:[{url:'project-media://merge-probe/existing.png'}], runFinishedAt:20, pending:0},
            {id:'merge-probe', type:'smart-image', images:[], pending:1, pendingTasks:[{taskId:'remote-live-task', count:1, kind:'image'}]},
            new Set(),
        );
        assertion('server merge preserves remote overlapping task beside completed local output', Boolean(
            mergedOverlap?.images?.some(item => item.url === 'project-media://merge-probe/existing.png')
            && mergedOverlap?.pendingTasks?.some(item => item.taskId === 'remote-live-task')
            && Number(mergedOverlap?.pending || 0) === 1
        ), mergedOverlap);

        const applyMergedCanvas = evalFn('applyMergedServerCanvas');
        const absorbGeneration = evalFn('absorbProjectGenerationResult');
        const probeNode = {id:'revision-probe', type:'smart-image', images:[{url:'project-media://revision-probe/local.png'}], runFinishedAt:10, pending:0};
        try {
            eval('canvasState.nodes').push(probeNode);
            applyMergedCanvas?.({
                revision:40,
                updated_at:Date.now(),
                nodes:[{...probeNode, images:[{url:'project-media://revision-probe/newest.png'}], runFinishedAt:40}],
                connections:[],
                logs:[],
            });
            await absorbGeneration?.({
                project_id:projectId,
                project_revision:39,
                registered_revision:38,
                result_node_id:'revision-probe',
                final_node:{...probeNode, images:[{url:'project-media://revision-probe/stale.png'}], runFinishedAt:39},
                history_record:{id:'revision-probe-history', task_id:'revision-probe-task', status:'succeeded', outputs:[]},
            });
            const revisionProbe = nodeById('revision-probe');
            assertion('delayed lower-revision generation response cannot overwrite merged newer node', Boolean(
                revisionProbe?.images?.some(item => item.url === 'project-media://revision-probe/newest.png')
                && !revisionProbe?.images?.some(item => item.url === 'project-media://revision-probe/stale.png')
            ), revisionProbe?.images);
        } finally {
            eval('canvasState.nodes').splice(0, eval('canvasState.nodes').length, ...eval('canvasState.nodes').filter(item => item.id !== 'revision-probe'));
            evalCanvas().nodes = eval('canvasState.nodes');
            evalCanvas().history = (evalCanvas().history || []).filter(item => item.id !== 'revision-probe-history');
        }

        const ref = nodeById('img-ref');
        assertion('img-ref node has reference image', Boolean(ref?.images?.length), JSON.stringify(ref?.images || []));

        document.querySelector('[data-id="prompt-conn"]')?.click();
        const promptNodeEditorReady = await waitFor(() => Boolean(document.querySelector('[data-id="prompt-conn"] .prompt-node-text')), 8000);
        const promptNodeEditor = document.querySelector('[data-id="prompt-conn"] .prompt-node-text');
        assertion('LLM prompt output remains directly editable', promptNodeEditorReady && promptNodeEditor?.readOnly === false, {ready:promptNodeEditorReady, readOnly:promptNodeEditor?.readOnly});
        if(promptNodeEditor){
            promptNodeEditor.value = 'edited prompt output';
            promptNodeEditor.dispatchEvent(new Event('input', {bubbles:true}));
        }
        assertion('editing the LLM prompt output updates the node text', nodeById('prompt-conn')?.text === 'edited prompt output', nodeById('prompt-conn')?.text);

        const genNode = document.querySelector('[data-id="gen-conn"]');
        genNode?.click();
        const composerOpen = await waitFor(() => document.querySelector('#composer')?.classList?.contains('open'), 8000);
        const paramsRendered = await waitFor(() => Boolean(document.querySelector('#dynamicParams .model-control .smart-pill')), 8000);
        await waitFor(() => Boolean(document.querySelector('#composer')?.style?.left && document.querySelector('#composer')?.style?.top), 1200);
        assertion('select gen-conn opens composer', composerOpen, composerOpen);
        assertion('dynamic params render model control', paramsRendered, paramsRendered);

        const shell = document.getElementById('shell');
        const toolbar = document.querySelector('.smart-toolbar');
        const minimap = document.getElementById('minimap');
        const shellBackground = getComputedStyle(shell).backgroundImage;
        assertion('workspace keeps one dot background layer', (shellBackground.match(/radial-gradient/g) || []).length === 1 && !/linear-gradient|ellipse/i.test(shellBackground), shellBackground);
        assertion('workspace chrome remains one toolbar plus visible minimap', toolbar?.children.length === 11 && [...document.querySelectorAll('.smart-back,.smart-undo,.smart-redo,.smart-settings,.smart-save,.smart-shortcut-toggle,.smart-log-toggle,.asset-toggle,#smartArrangeBtn,#smartExcelExportBtn,#smartMarkedToggle')].every(control => control.closest('.smart-toolbar') === toolbar) && getComputedStyle(minimap).display !== 'none', {toolbar:toolbar?.children.length, minimap:getComputedStyle(minimap).display});

        const composer = document.getElementById('composer');
        const composerRect = composer.getBoundingClientRect();
        const selectedNodeRect = document.querySelector('[data-id="gen-conn"]')?.getBoundingClientRect();
        const shellRect = shell.getBoundingClientRect();
        const composerCssWidth = Number.parseFloat(getComputedStyle(composer).width || '0');
        const visibleParamControls = [...document.querySelectorAll('#dynamicParams > *')].filter(control => {
            const rect = control.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        });
        const paramRects = visibleParamControls.map(control => control.getBoundingClientRect());
        const mainControlEls = [...document.querySelectorAll('#dynamicParams .smart-pill,#dynamicParams .setting-check,#dynamicParams .trusted-src-pill,#dynamicParams .num-with-dice,#dynamicParams .num-compact,#dynamicParams .inline-fields')].filter(control => {
            const rect = control.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        });
        const mainControlGeometry = mainControlEls.map(control => {
            const rect = control.getBoundingClientRect();
            return {className:control.className, height:rect.height, font:getComputedStyle(control).fontSize};
        });
        const runRect = document.getElementById('runBtn').getBoundingClientRect();
        const promptRect = document.getElementById('promptInput').getBoundingClientRect();
        const layoutViewportWidth = document.documentElement.clientWidth;
        const expectedComposerWidth = Math.min(720, layoutViewportWidth - 36);
        const mobileWidthPasses = composerCssWidth <= layoutViewportWidth - 24 + 1 && composerCssWidth >= layoutViewportWidth - 48;
        const composerWidthContract = composer.style.width === '' && (layoutViewportWidth < 760 ? mobileWidthPasses : Math.abs(composerCssWidth - expectedComposerWidth) <= 1);
        assertion('image composer uses a fixed desktop width and a viewport-safe mobile width', composerWidthContract, {viewport:innerWidth, layoutViewport:layoutViewportWidth, inlineWidth:composer.style.width, cssWidth:composerCssWidth, expectedComposerWidth, mobileWidthPasses});
        assertion('composer stays inside the viewport', composerRect.left >= shellRect.left - 1 && composerRect.right <= shellRect.right + 1 && composerRect.top >= shellRect.top - 1 && composerRect.bottom <= shellRect.bottom + 1, {composer:{left:composerRect.left, right:composerRect.right, top:composerRect.top, bottom:composerRect.bottom}, shell:{left:shellRect.left, right:shellRect.right, top:shellRect.top, bottom:shellRect.bottom}});
        const centeredLeft = selectedNodeRect.left + (selectedNodeRect.width - composerRect.width) / 2;
        const canCenterHorizontally = centeredLeft >= shellRect.left + 8 && centeredLeft + composerRect.width <= shellRect.right - 8;
        const canFitBelow = selectedNodeRect.bottom + 8 + composerRect.height <= shellRect.bottom - 8;
        const composerPlacementPasses = (!canCenterHorizontally || Math.abs((composerRect.left + composerRect.width / 2) - (selectedNodeRect.left + selectedNodeRect.width / 2)) <= 2)
            && (!canFitBelow || composerRect.top >= selectedNodeRect.bottom + 8);
        assertion('composer stays centered below the selected node when the viewport has room', composerPlacementPasses, {canCenterHorizontally, canFitBelow, composer:{left:composerRect.left, top:composerRect.top, width:composerRect.width, height:composerRect.height}, node:{left:selectedNodeRect.left, bottom:selectedNodeRect.bottom, width:selectedNodeRect.width}});
        assertion('composer prompt uses 13px body typography', getComputedStyle(document.getElementById('promptInput')).fontSize === '13px', getComputedStyle(document.getElementById('promptInput')).fontSize);
        assertion('composer uses the compact prompt height by default', promptRect.height >= 72 && promptRect.height <= 90, promptRect.height);
        assertion('composer parameter controls share 30-32px geometry and 12px typography', mainControlGeometry.length >= 4 && mainControlGeometry.every(item => item.height >= 30 && item.height <= 32.5 && Number.parseFloat(item.font) >= 12) && runRect.height >= 30 && runRect.height <= 32.5 && getComputedStyle(document.getElementById('runBtn')).fontSize === '12px', {controls:mainControlGeometry, run:{height:runRect.height, font:getComputedStyle(document.getElementById('runBtn')).fontSize}});
        const controlsStayInsideComposer = paramRects.length >= 4
            && paramRects.every(rect => rect.left >= composerRect.left - 1 && rect.right <= composerRect.right + 1)
            && runRect.left >= composerRect.left - 1 && runRect.right <= composerRect.right + 1;
        assertion('composer keeps wrapped parameter controls inside its fixed width', controlsStayInsideComposer, {viewport:innerWidth, composer:{left:composerRect.left, right:composerRect.right}, controls:paramRects.map(rect => ({left:rect.left, right:rect.right, top:rect.top})), run:{left:runRect.left, right:runRect.right, top:runRect.top}});
        const composerOverflowX = getComputedStyle(composer).overflowX;
        assertion('composer does not create a horizontal scrollbar', !['auto', 'scroll'].includes(composerOverflowX), {overflowX:composerOverflowX, scrollWidth:composer.scrollWidth, clientWidth:composer.clientWidth});

        const defaultModel = smartSettings()?.model;
        assertion('default model is browser-image-model', defaultModel === 'browser-image-model', defaultModel);

        clickPill('model-control');
        const optionsShown = await waitFor(() => document.querySelectorAll('#dynamicParams [data-model-option]').length >= 2, 8000);
        const modelOptionIds = [...document.querySelectorAll('#dynamicParams [data-model-option]')].map(el => String(el.dataset.modelOption || ''));
        // #214: generic OpenAI-compatible providers merge model_specs.json's adapted image
        // models into the configured list (mergeSpecModels), so the contract is "2 configured
        // fake models + every spec id not already listed" — compute the expected total from
        // /api/model-specs instead of hard-coding 2.
        let expectedModelCount = 2;
        try {
            const specJson = await fetch('/api/model-specs').then(r => (r.ok ? r.json() : null));
            const specImageIds = Array.isArray(specJson?.image_model_ids) ? specJson.image_model_ids : [];
            const configured = ['browser-image-model', 'browser-image-model-2'];
            expectedModelCount = configured.length + specImageIds.filter(id => id && !configured.some(m => String(m).toLowerCase() === String(String(id)).toLowerCase())).length;
        } catch(_){}
        const configuredListed = ['browser-image-model', 'browser-image-model-2'].every(id => modelOptionIds.some(m => String(m).toLowerCase() === id));
        assertion('model popover lists configured models plus spec-adapted models', optionsShown && modelOptionIds.length === expectedModelCount && configuredListed, {optionCount:modelOptionIds.length, expectedModelCount, modelOptionIds});
        const modelPopoverRect = document.querySelector('#dynamicParams .model-control .smart-popover')?.getBoundingClientRect();
        assertion('composer popover stays inside the viewport without horizontal overflow', Boolean(modelPopoverRect) && modelPopoverRect.left >= 8 && modelPopoverRect.right <= layoutViewportWidth - 8, modelPopoverRect ? {left:modelPopoverRect.left, right:modelPopoverRect.right, viewport:layoutViewportWidth} : 'missing');

        const second = [...document.querySelectorAll('#dynamicParams [data-model-option]')].find(o => (o.dataset.modelOption || o.textContent || '').includes('browser-image-model-2'));
        second?.click();
        const switched = await waitFor(() => smartSettings()?.model === 'browser-image-model-2', 8000);
        assertion('model switch updates settings.model', switched, smartSettings()?.model);
        assertion('model pill text updates', pillText('model-control') === 'browser-image-model-2', pillText('model-control'));

        const ratioClicked = clickPill('size-picker-control');
        const ratioPicked = await waitFor(() => clickParam('size-picker-control', 'ratio', 'portrait'), 8000);
        const ratioSet = await waitFor(() => smartSettings()?.ratio === 'portrait', 8000);
        assertion('ratio switch to portrait', ratioClicked && ratioPicked && ratioSet, smartSettings()?.ratio);

        const resClicked = clickPill('size-picker-control');
        const resPicked = await waitFor(() => clickParam('size-picker-control', 'resolution', '2k'), 8000);
        const resSet = await waitFor(() => smartSettings()?.resolution === '2k', 8000);
        assertion('resolution switch to 2k', resClicked && resPicked && resSet, smartSettings()?.resolution);

        const qClicked = clickPill('quality-control');
        const qPicked = await waitFor(() => clickParam('quality-control', 'quality', 'high'), 8000);
        const qSet = await waitFor(() => smartSettings()?.quality === 'high', 8000);
        assertion('quality switch to high', qClicked && qPicked && qSet, smartSettings()?.quality);
        const parameterLabels = [...document.querySelectorAll('#dynamicParams .smart-pill')]
            .filter(element => element.textContent.trim() && getComputedStyle(element).display !== 'none');
        const clippedParameterLabels = parameterLabels
            .filter(element => element.scrollWidth > element.clientWidth + 1)
            .map(element => ({text:element.textContent.trim(), scrollWidth:element.scrollWidth, clientWidth:element.clientWidth}));
        assertion('image composer keeps every visible parameter label readable', parameterLabels.length >= 4 && clippedParameterLabels.length === 0, {labels:parameterLabels.map(element => element.textContent.trim()), clipped:clippedParameterLabels});

        const promptEl = document.querySelector('#composer [data-prompt-text], #composer textarea, #composer [contenteditable="true"], #promptInput');
        if(promptEl){
            promptEl.textContent = '连通性测试提示词';
            promptEl.dispatchEvent(new Event('input', {bubbles:true}));
            promptEl.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:'连通性测试提示词'}));
        }
        await sleep(300);
        const expectedSize = (() => {
            try { return eval('apiImageSize(smartSettings().ratio, smartSettings().resolution)'); } catch(_) { return ''; }
        })();
        result.diagnostics.push(`expected size = ${expectedSize}`);

        const runBtn = document.querySelector('#runBtn');
        const runClickable = await waitFor(() => !runBtn?.disabled, 10000);
        assertion('run button enabled', runClickable, runBtn?.disabled);
        const setGenerationCount = async value => {
            const opened = clickPill('count-control');
            const picked = await waitFor(() => clickParam('count-control', 'count', String(value)), 3000);
            return opened && picked;
        };
        const firstCountSelected = await setGenerationCount(3);
        const firstCountApplied = await waitFor(() => Number(smartSettings()?.count || 0) === 3, 3000);
        assertion('first cancellable task requests three outputs', firstCountSelected && firstCountApplied, smartSettings()?.count);
        const historyCountBeforeRun = (evalCanvas()?.history || []).length;
        runBtn?.click();
        await sleep(120);
        const firstPendingNode = document.querySelector('[data-id="gen-conn"]');
        // #214: for n>1 batches the spinner + stage progress live in the node-bottom
        // .batch-statusbar (the #84 batch UI dropped the per-cell status panel); the
        // placeholder grid still renders one .generation-placeholder cell per output.
        const firstBatchStack = firstPendingNode?.querySelector('.pending-batch-stack');
        const firstPlaceholderSpinner = firstBatchStack?.querySelector('.batch-statusbar .generation-placeholder-spinner');
        const firstBatchCells = firstBatchStack?.querySelectorAll('.generation-placeholder-grid .generation-placeholder') || [];
        assertion('first generation shows a result placeholder', Boolean(firstPlaceholderSpinner) && firstBatchCells.length === 3, {
            spinner:Boolean(firstPlaceholderSpinner),
            cells:firstBatchCells.length,
            statusbar:firstBatchStack?.querySelector('.batch-statusbar')?.className || 'missing',
        });
        const firstNodeProgress = firstPendingNode?.querySelector('.generation-stage-progress');
        assertion('node placeholder shows six honest generation stages without a fake percent', Boolean(firstNodeProgress) && firstNodeProgress.querySelectorAll('.generation-stage-segment').length === 6 && !firstNodeProgress.textContent.includes('%'), firstNodeProgress?.outerHTML || 'missing');
        document.getElementById('smartHistoryToggle')?.click();
        const pendingHistoryVisible = await waitFor(() => Boolean(document.querySelector('#smartHistoryList .log-item.pending .log-history-placeholder')), 5000);
        const pendingHistoryCard = document.querySelector('#smartHistoryList .log-item.pending');
        const pendingHistoryPlaceholder = pendingHistoryCard?.querySelector('.log-history-placeholder');
        assertion('history immediately shows the running task as the newest pending card', pendingHistoryVisible && document.querySelector('#smartHistoryList .log-item') === pendingHistoryCard, pendingHistoryCard?.dataset?.canvasHistoryId || 'missing');
        assertion('history pending card has an animated placeholder', Boolean(pendingHistoryPlaceholder) && getComputedStyle(pendingHistoryPlaceholder).animationName !== 'none', pendingHistoryPlaceholder ? {className:pendingHistoryPlaceholder.className, animation:getComputedStyle(pendingHistoryPlaceholder).animationName} : 'missing');
        const firstHistoryProgress = pendingHistoryCard?.querySelector('.generation-stage-progress');
        assertion('history placeholder shows the same six generation stages', Boolean(firstHistoryProgress) && firstHistoryProgress.querySelectorAll('.generation-stage-segment').length === 6 && !firstHistoryProgress.textContent.includes('%'), firstHistoryProgress?.outerHTML || 'missing');
        // #214: sample the node/history phases throughout the window so a failure shows
        // exactly which tick the 2s poller landed on instead of only the final state.
        const livePhaseSamples = [];
        const sampleLivePhase = () => {
            const nodeProgress = document.querySelector('[data-id="gen-conn"] .generation-stage-progress');
            const historyProgress = document.querySelector('#smartHistoryList .log-item.pending .generation-stage-progress');
            return {node:nodeProgress?.dataset?.phase || '', history:historyProgress?.dataset?.phase || ''};
        };
        const livePhaseSynced = await waitFor(() => {
            const sample = sampleLivePhase();
            livePhaseSamples.push(sample);
            return sample.node === 'provider_generating' && sample.history === sample.node;
        }, 7000);
        result.diagnostics.push(`livePhaseSamples=${JSON.stringify(livePhaseSamples)}`);
        assertion('node and history progress advance to the same live provider stage', livePhaseSynced, {
            node:sampleLivePhase().node,
            history:sampleLivePhase().history,
            samples:livePhaseSamples,
        });
        const concurrentRunEnabled = await waitFor(() => !runBtn?.disabled, 3000);
        assertion('run button stays enabled while the current generation is pending', concurrentRunEnabled, runBtn?.disabled);
        if(promptEl){
            promptEl.textContent = '并发微调提示词';
            promptEl.dispatchEvent(new Event('input', {bubbles:true}));
            promptEl.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:'并发微调提示词'}));
        }
        assertion('prompt remains editable while generation is pending', promptEl?.getAttribute('contenteditable') !== 'false' && promptEl?.textContent === '并发微调提示词', {
            contenteditable:promptEl?.getAttribute('contenteditable'),
            text:promptEl?.textContent || '',
        });
        const secondCountSelected = await setGenerationCount(1);
        const secondCountApplied = await waitFor(() => Number(smartSettings()?.count || 0) === 1, 3000);
        assertion('overlapping survivor requests one output', secondCountSelected && secondCountApplied, smartSettings()?.count);
        runBtn?.click();
        const concurrentPendingVisible = await waitFor(() => document.querySelectorAll('#smartHistoryList .log-item.pending').length >= 2, 5000);
        assertion('a second run creates an independent pending history card', concurrentPendingVisible, document.querySelectorAll('#smartHistoryList .log-item.pending').length);
        const firstPendingLog = (evalCanvas()?.history || []).find(log => log.status === 'pending' && String(log.prompt || '').includes('连通性测试提示词'));
        const firstPendingCard = firstPendingLog ? document.querySelector(`[data-canvas-history-id="${firstPendingLog.id}"]`) : null;
        const firstCancelButton = firstPendingCard?.querySelector('[data-history-cancel-task]');
        assertion('each project pending card exposes a task-scoped cancel control', Boolean(firstCancelButton), firstPendingCard?.outerHTML || 'missing');
        const secondClientObservedOverlap = await waitFor(async () => {
            const response = await fetch('/__multiclient_observed');
            return response.ok && Boolean((await response.json()).ready);
        }, 15000);
        assertion('second real browser observes both task cards before cancellation', secondClientObservedOverlap, secondClientObservedOverlap);
        firstCancelButton?.click();
        const firstCancelled = await waitFor(() => {
            const logs = evalCanvas()?.history || [];
            const cancelled = logs.find(log => log.status === 'cancelled' && String(log.prompt || '').includes('连通性测试提示词'));
            const remaining = logs.filter(log => log.status === 'pending');
            return Boolean(cancelled) && remaining.length === 1;
        }, 5000);
        assertion('cancelling the first task preserves the overlapping second task', firstCancelled, (evalCanvas()?.history || []).map(log => ({task:log.task_id || log.request?.task_id, status:log.status, prompt:log.prompt})));
        const cancelledCountReconciled = await waitFor(() => {
            const current = nodeById('gen-conn');
            return Number(current?.pending || 0) === 1 && (current?.pendingTasks || []).length === 1;
        }, 5000);
        assertion('cancelling an n>1 task recomputes pending from the one surviving task', cancelledCountReconciled, {
            pending:nodeById('gen-conn')?.pending,
            tasks:(nodeById('gen-conn')?.pendingTasks || []).map(task => ({taskId:task.taskId, count:task.count})),
        });
        await sleep(2500);
        const toastText = document.querySelector('#toast, .toast, [class*="toast"]')?.textContent?.trim() || '';
        const diagNode = nodeById('gen-conn');
        result.diagnostics.push(`toast=${toastText}`);
        result.diagnostics.push(`nodeState=${JSON.stringify({pending: diagNode?.pending, images: (diagNode?.images || []).length, runFinishedAt: diagNode?.runFinishedAt, title: diagNode?.title})}`);
        const completed = await waitFor(() => {
            const n = nodeById('gen-conn');
            return Boolean(n?.images?.length && n.images.some(i => i.url) && !n.pending && n.runFinishedAt);
        }, 60000);
        assertion('generation completes on gen-conn', completed, JSON.stringify(nodeById('gen-conn')?.images?.map(i => i.url) || []));
        const pendingHistoryReplaced = await waitFor(() => {
            const logs = evalCanvas()?.history || [];
            return logs.length === historyCountBeforeRun + 2
                && logs.filter(log => log.status === 'cancelled').length >= 1
                && logs.filter(log => log.status === 'succeeded').length >= 1
                && !logs.some(log => log.status === 'pending')
                && Boolean(document.querySelector('#smartHistoryList .log-item:not(.pending) .log-history-image'));
        }, 8000);
        assertion('cancelled and completed tasks replace only their own history placeholders', pendingHistoryReplaced, {before:historyCountBeforeRun, after:(evalCanvas()?.history || []).length, statuses:(evalCanvas()?.history || []).map(log => log.status)});
        assertion('completed node no longer renders the stage progress placeholder', !document.querySelector('[data-id="gen-conn"] .generation-stage-progress'), document.querySelector('[data-id="gen-conn"] .generation-stage-progress')?.outerHTML || 'removed');
        result.diagnostics.push(`genImagesReadAt=${Math.round(performance.now() - (probe214.t0 || 0))} imgCount=${(nodeById('gen-conn')?.images || []).length} archived=${JSON.stringify((evalCanvas()?.history || []).filter(l => !l.status).map(l => ({images:(l.images||[]).length, title:l.title})))}`);
        const genImages = nodeById('gen-conn')?.images || [];
        const urlImage = genImages.find(i => i.url);
        assertion('generated image has url', Boolean(urlImage?.url), urlImage?.url || '');

        const captured = await (await fetch('/__captured_requests')).json();
        const last = [...captured].reverse().find(item => String(item.prompt || '').includes('并发微调提示词')) || {};
        result.diagnostics.push(`captured=${JSON.stringify(captured)}`);
        assertion('at least one request captured', captured.length >= 1, captured.length);
        assertion('payload model is switched model', last.model === 'browser-image-model-2', last.model);
        assertion('second payload carries the edited prompt', String(last.prompt || '').includes('并发微调提示词'), last.prompt);
        assertion('payload size matches ratio/resolution', last.size === expectedSize, `${last.size} vs ${expectedSize}`);
        assertion('payload quality is high', last.quality === 'high', last.quality);
        assertion('payload provider is browser-image', last.provider_id === 'browser-image', last.provider_id);
        const refs = last.references || [];
        assertion('reference image forwarded', refs.length >= 1 && refs.some(u => String(u).includes('/media/')), JSON.stringify(refs));

        const downloadResult = await (async () => {
            try {
                const res = await fetch(urlImage.url, {cache: 'no-store'});
                if(!res.ok) return {ok: false, size: 0, status: res.status};
                const buf = await res.arrayBuffer();
                const bytes = new Uint8Array(buf);
                const isPng = bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4E && bytes[3] === 0x47;
                return {ok: true, size: buf.byteLength, status: res.status, png: isPng};
            } catch (err) {
                return {ok: false, size: 0, status: String(err)};
            }
        })();
        assertion('generated image downloadable', downloadResult.ok && downloadResult.size >= 60 && downloadResult.png, JSON.stringify(downloadResult));

        const autoReset = await evalFn(`(p) => {
            const n = canvasState.nodes.find(n => n.id === 'gen-conn');
            if(!n) return false;
            n.pending = false;
            n.queued = false;
            delete n.runFinishedAt;
            n.runSettings = {engine: 'api', apiKind: 'image', routingMode: 'auto', ratio: 'square', resolution: '1k', quality: 'auto', count: 1};
            return true;
        }`)();
        const reSelect = await evalFn(`(p) => {
            const n = canvasState.nodes.find(n => n.id === 'gen-conn');
            if(!n) return false;
            canvasState.selectedId = n.id;
            updateComposer();
            renderDynamicParams();
            return true;
        }`)();
        await sleep(300);
        const promptEl2 = document.querySelector('#composer [data-prompt-text], #composer textarea, #composer [contenteditable="true"], #promptInput');
        if(promptEl2){
            promptEl2.textContent = '自动路由测试提示词';
            promptEl2.dispatchEvent(new Event('input', {bubbles:true}));
            promptEl2.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:'自动路由测试提示词'}));
        }
        await sleep(300);
        const runBtn2 = document.querySelector('#runBtn');
        const autoRunClickable = await waitFor(() => !runBtn2?.disabled, 10000);
        assertion('auto run button enabled', autoReset && reSelect && autoRunClickable, {autoReset, reSelect, disabled: runBtn2?.disabled});
        runBtn2?.click();
        await sleep(120);
        const rerunNode = document.querySelector('[data-id="gen-conn"]');
        const rerunPlaceholder = rerunNode?.querySelector('.generation-placeholder');
        const rerunPlaceholderSpinner = rerunPlaceholder?.querySelector('.generation-placeholder-spinner');
        assertion('rerunning a node with an old image replaces it visually with the placeholder', Boolean(rerunPlaceholderSpinner) && !rerunNode?.querySelector('.node-img'), rerunPlaceholderSpinner ? {className:rerunPlaceholderSpinner.className, oldImageVisible:Boolean(rerunNode?.querySelector('.node-img'))} : 'missing');
        const autoDone = await waitFor(() => {
            const n = nodeById('gen-conn');
            return Boolean(n?.images?.length && !n.pending && n.runFinishedAt);
        }, 60000);
        assertion('auto routing generation completes', autoDone, JSON.stringify(nodeById('gen-conn')?.images?.map(i => i.url) || []));
        const captured2 = await (await fetch('/__captured_requests')).json();
        const autoEntry = captured2[captured2.length - 1] || {};
        assertion('auto routing selects provider', autoEntry.provider_id === 'browser-image', autoEntry.provider_id);
        assertion('auto routing selects model', Boolean(autoEntry.model), autoEntry.model);
        assertion('auto routing forwards prompt', String(autoEntry.prompt || '').includes('自动路由测试提示词'), autoEntry.prompt);
        const autoHistorySettled = await waitFor(() => {
            const matching = (evalCanvas()?.history || []).filter(log => String(log.prompt || '').includes('自动路由测试提示词'));
            return matching.some(log => log.status === 'succeeded') && !matching.some(log => log.status === 'pending');
        }, 15000);
        assertion('auto routing history settles before the failure journey begins', autoHistorySettled, (evalCanvas()?.history || []).filter(log => String(log.prompt || '').includes('自动路由测试提示词')).map(log => log.status));

        const historyCountBeforeFailure = (evalCanvas()?.history || []).length;
        const failedCountBefore = (evalCanvas()?.history || []).filter(log => log.status === 'failed').length;
        const promptEl3 = document.querySelector('#composer [data-prompt-text], #composer textarea, #composer [contenteditable="true"], #promptInput');
        if(promptEl3){
            promptEl3.textContent = 'FORCE_HISTORY_FAILURE';
            promptEl3.dispatchEvent(new Event('input', {bubbles:true}));
            promptEl3.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:'FORCE_HISTORY_FAILURE'}));
        }
        await sleep(120);
        const failedRunBtn = document.querySelector('#runBtn');
        const failedRunClickable = await waitFor(() => !failedRunBtn?.disabled, 10000);
        assertion('forced failure run button enabled', failedRunClickable, failedRunBtn?.disabled);
        failedRunBtn?.click();
        const failedPendingVisible = await waitFor(() => {
            const card = document.querySelector('#smartHistoryList .log-item.pending');
            const placeholder = card?.querySelector('.log-history-placeholder');
            return Boolean(card && placeholder && getComputedStyle(placeholder).animationName !== 'none');
        }, 5000);
        assertion('failing generation first appears as an animated history placeholder', failedPendingVisible, document.querySelector('#smartHistoryList .log-item.pending')?.dataset?.canvasHistoryId || 'missing');
        const failedHistoryResolved = await waitFor(() => {
            const logs = evalCanvas()?.history || [];
            const matching = logs.filter(log => log.status === 'failed' && String(log.prompt || '').includes('FORCE_HISTORY_FAILURE'));
            return logs.length === historyCountBeforeFailure + 1
                && matching.length === 1
                && !logs.some(log => log.status === 'pending');
        }, 60000);
        assertion('failed result replaces the history placeholder without adding a duplicate', failedHistoryResolved, {
            before: historyCountBeforeFailure,
            after: (evalCanvas()?.history || []).length,
            pending: (evalCanvas()?.history || []).filter(log => log.status === 'pending').length,
            failed: (evalCanvas()?.history || []).filter(log => log.status === 'failed').length - failedCountBefore,
        });
        const failedLog = (evalCanvas()?.history || []).find(log => log.status === 'failed' && String(log.prompt || '').includes('FORCE_HISTORY_FAILURE'));
        evalFn('renderSmartCanvasLog')?.();
        const failedStageProgress = failedLog ? document.querySelector(`[data-canvas-history-id="${CSS.escape(String(failedLog.id || ''))}"] .generation-stage-progress.is-failed`) : null;
        assertion('failed history keeps the stage where generation stopped', failedStageProgress?.dataset?.phase === 'provider_generating', failedStageProgress?.outerHTML || failedLog?.progress || 'missing');
        const failedTaskId = failedLog?.task_id || failedLog?.request?.task_id || '';
        const retryButton = failedLog ? document.querySelector(`[data-canvas-history-id="${failedLog.id}"] [data-history-retry-task]`) : null;
        assertion('failed project task exposes an explicit retry control', Boolean(retryButton) && retryButton.dataset.historyRetryTask === failedTaskId, retryButton?.outerHTML || 'missing');
        retryButton?.click();
        const retryPending = await waitFor(() => {
            const retryLog = (evalCanvas()?.history || []).find(log => log.status === 'pending' && String(log.prompt || '').includes('FORCE_HISTORY_FAILURE'));
            const retryTaskId = retryLog?.task_id || retryLog?.request?.task_id || '';
            return retryTaskId && retryTaskId !== failedTaskId;
        }, 5000);
        assertion('retry creates a distinct pending task identity', retryPending, (evalCanvas()?.history || []).map(log => ({task:log.task_id || log.request?.task_id, status:log.status, prompt:log.prompt})));
        const retryCompleted = await waitFor(() => {
            const matching = (evalCanvas()?.history || []).filter(log => String(log.prompt || '').includes('FORCE_HISTORY_FAILURE'));
            return matching.some(log => log.status === 'failed')
                && matching.some(log => log.status === 'succeeded')
                && !matching.some(log => log.status === 'pending');
        }, 60000);
        assertion('explicit retry preserves the failed record and completes the new task', retryCompleted, (evalCanvas()?.history || []).filter(log => String(log.prompt || '').includes('FORCE_HISTORY_FAILURE')).map(log => ({task:log.task_id || log.request?.task_id, status:log.status})));
        const capturedAfterRetry = await (await fetch('/__captured_requests')).json();
        assertion('explicit retry invokes the fake adapter exactly once more', capturedAfterRetry.filter(item => String(item.prompt || '').includes('FORCE_HISTORY_FAILURE')).length === 2, capturedAfterRetry.filter(item => String(item.prompt || '').includes('FORCE_HISTORY_FAILURE')).length);

        const types = ['conversation', 'agent', 'local-edit', 'loop'];
        const beforeCount = (evalCanvas()?.nodes || []).length;
        const createdOk = {};
        for(const type of types){
            document.querySelector('#createMenuButton, [data-create-menu]')?.click?.();
            const card = [...document.querySelectorAll('[data-create-type]')].find(c => c.dataset.createType === type);
            card?.click?.();
            const appeared = await waitFor(() => (evalCanvas()?.nodes || []).length > beforeCount + Object.values(createdOk).filter(Boolean).length, 8000);
            createdOk[type] = appeared;
            await sleep(120);
        }
        assertion('other node types create (conversation/agent/local-edit/loop)', Object.values(createdOk).every(Boolean), JSON.stringify(createdOk));
        const finalCount = (evalCanvas()?.nodes || []).length;
        assertion('node count grew by 4', finalCount === beforeCount + 4, `${beforeCount} -> ${finalCount}`);

        const recoverySelected = await evalFn(`() => {
            const n = canvasState.nodes.find(n => n.id === 'gen-conn');
            if(!n) return false;
            canvasState.selectedId = n.id;
            n.runSettings = {...(n.runSettings || {}), engine:'api', apiKind:'image', routingMode:'auto', count:1};
            updateComposer();
            renderDynamicParams();
            return true;
        }`)();
        await sleep(250);
        const recoveryPrompt = document.querySelector('#composer [data-prompt-text], #composer textarea, #composer [contenteditable="true"], #promptInput');
        if(recoveryPrompt){
            recoveryPrompt.textContent = 'RESPONSE_LOST_RECOVERY';
            recoveryPrompt.dispatchEvent(new Event('input', {bubbles:true}));
            recoveryPrompt.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:'RESPONSE_LOST_RECOVERY'}));
        }
        const originalFetch = window.fetch.bind(window);
        let droppedSubmissionResponses = 0;
        window.fetch = async (input, init={}) => {
            const url = typeof input === 'string' ? input : String(input?.url || '');
            const isTarget = String(init?.method || 'GET').toUpperCase() === 'POST'
                && url.includes('/image-generation-tasks')
                && String(init?.body || '').includes('RESPONSE_LOST_RECOVERY');
            const response = await originalFetch(input, init);
            if(isTarget && droppedSubmissionResponses < 2){
                droppedSubmissionResponses += 1;
                throw new TypeError('simulated response loss after server accepted request');
            }
            return response;
        };
        const recoveryRunReady = await waitFor(() => !document.querySelector('#runBtn')?.disabled, 10000);
        assertion('response-loss recovery run is ready', recoverySelected && recoveryRunReady, {recoverySelected, disabled:document.querySelector('#runBtn')?.disabled});
        document.querySelector('#runBtn')?.click();
        const durableUnknownSubmission = await waitFor(() => {
            const submissions = nodeById('gen-conn')?.generationSubmissions || [];
            return droppedSubmissionResponses === 2 && submissions.some(item => item.idempotencyKey && item.requestPayload?.prompt === 'RESPONSE_LOST_RECOVERY');
        }, 15000);
        window.fetch = originalFetch;
        assertion('paid POST response loss leaves a durable unknown submission marker', durableUnknownSubmission, {
            droppedSubmissionResponses,
            submissions:nodeById('gen-conn')?.generationSubmissions || [],
        });
        if(probe214.timer) clearInterval(probe214.timer);
        result.diagnostics.push(`probe214=${JSON.stringify(probe214.events)}`);
        sessionStorage.setItem('t09-submission-recovery-result', JSON.stringify({assertions:result.assertions, errors:result.errors, diagnostics:result.diagnostics}));
        sessionStorage.setItem('t09-submission-recovery-phase', 'verify');
        location.reload();
        await new Promise(() => {});
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
