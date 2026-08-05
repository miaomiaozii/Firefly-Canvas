(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const result = {
        kind: 'final',
        role: 'probe',
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
        const settings = smartSettings();
        assertion('settings loaded', Boolean(settings), Boolean(settings));

        const ref = nodeById('img-ref');
        assertion('img-ref node has reference image', Boolean(ref?.images?.length), JSON.stringify(ref?.images || []));

        const genNode = document.querySelector('[data-id="gen-conn"]');
        genNode?.click();
        const composerOpen = await waitFor(() => document.querySelector('#composer')?.classList?.contains('open'), 8000);
        const paramsRendered = await waitFor(() => Boolean(document.querySelector('#dynamicParams .model-control .smart-pill')), 8000);
        assertion('select gen-conn opens composer', composerOpen, composerOpen);
        assertion('dynamic params render model control', paramsRendered, paramsRendered);

        const shell = document.getElementById('shell');
        const toolbar = document.querySelector('.smart-toolbar');
        const minimap = document.getElementById('minimap');
        const shellBackground = getComputedStyle(shell).backgroundImage;
        assertion('workspace keeps one dot background layer', (shellBackground.match(/radial-gradient/g) || []).length === 1 && !/linear-gradient|ellipse/i.test(shellBackground), shellBackground);
        assertion('workspace chrome remains one toolbar plus visible minimap', toolbar?.children.length === 8 && [...document.querySelectorAll('.smart-back,.smart-undo,.smart-redo,.smart-settings,.smart-save,.smart-shortcut-toggle,.smart-log-toggle,.asset-toggle')].every(control => control.closest('.smart-toolbar') === toolbar) && getComputedStyle(minimap).display !== 'none', {toolbar:toolbar?.children.length, minimap:getComputedStyle(minimap).display});

        const composer = document.getElementById('composer');
        const composerRect = composer.getBoundingClientRect();
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
        const composerWidthContract = innerWidth < 760
            ? composer.style.width === '' && Math.abs(composerCssWidth - (innerWidth - 24)) <= 1
            : composer.style.width === '' && composerCssWidth >= 700 && composerCssWidth <= 720;
        assertion('composer width is CSS-owned and follows its responsive width band', composerWidthContract, {viewport:innerWidth, inlineWidth:composer.style.width, cssWidth:composerCssWidth});
        assertion('composer stays inside the viewport', composerRect.left >= shellRect.left - 1 && composerRect.right <= shellRect.right + 1 && composerRect.top >= shellRect.top - 1 && composerRect.bottom <= shellRect.bottom + 1, {composer:{left:composerRect.left, right:composerRect.right, top:composerRect.top, bottom:composerRect.bottom}, shell:{left:shellRect.left, right:shellRect.right, top:shellRect.top, bottom:shellRect.bottom}});
        assertion('composer prompt uses 13px body typography', getComputedStyle(document.getElementById('promptInput')).fontSize === '13px', getComputedStyle(document.getElementById('promptInput')).fontSize);
        assertion('composer uses the compact prompt height by default', promptRect.height >= 72 && promptRect.height <= 90, promptRect.height);
        assertion('composer parameter controls share 30-32px geometry and 12px typography', mainControlGeometry.length >= 4 && mainControlGeometry.every(item => item.height >= 30 && item.height <= 32.5 && Number.parseFloat(item.font) >= 12) && runRect.height >= 30 && runRect.height <= 32.5 && getComputedStyle(document.getElementById('runBtn')).fontSize === '12px', {controls:mainControlGeometry, run:{height:runRect.height, font:getComputedStyle(document.getElementById('runBtn')).fontSize}});
        const desktopSingleRow = paramRects.length >= 4 && paramRects.every(rect => Math.abs(rect.top - paramRects[0].top) <= 1) && Math.abs(runRect.top - paramRects[0].top) <= 1;
        const narrowWrap = paramRects.length >= 4
            && new Set(paramRects.map(rect => Math.round(rect.top))).size >= 2
            && paramRects.every(rect => rect.left >= composerRect.left - 1 && rect.right <= composerRect.right + 1)
            && runRect.top >= Math.max(...paramRects.map(rect => rect.bottom)) - 1;
        assertion('composer uses the responsive parameter layout contract', innerWidth >= 760 ? desktopSingleRow : narrowWrap, {viewport:innerWidth, tops:paramRects.map(rect => rect.top), runTop:runRect.top});
        assertion('composer does not create a horizontal scrollbar', composer.scrollWidth <= composer.clientWidth + 1, {scrollWidth:composer.scrollWidth, clientWidth:composer.clientWidth});

        const defaultModel = smartSettings()?.model;
        assertion('default model is browser-image-model', defaultModel === 'browser-image-model', defaultModel);

        clickPill('model-control');
        const optionsShown = await waitFor(() => document.querySelectorAll('#dynamicParams [data-model-option]').length >= 2, 8000);
        const optionCount = document.querySelectorAll('#dynamicParams [data-model-option]').length;
        assertion('model popover lists 2 models', optionsShown && optionCount === 2, optionCount);
        const modelPopoverRect = document.querySelector('#dynamicParams .model-control .smart-popover')?.getBoundingClientRect();
        assertion('composer popover stays inside the viewport without horizontal overflow', Boolean(modelPopoverRect) && modelPopoverRect.left >= 8 && modelPopoverRect.right <= innerWidth - 8 && composer.scrollWidth <= composer.clientWidth + 1, modelPopoverRect ? {left:modelPopoverRect.left, right:modelPopoverRect.right, viewport:innerWidth, scrollWidth:composer.scrollWidth, clientWidth:composer.clientWidth} : 'missing');

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
        runBtn?.click();
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
        const genImages = nodeById('gen-conn')?.images || [];
        const urlImage = genImages.find(i => i.url);
        assertion('generated image has url', Boolean(urlImage?.url), urlImage?.url || '');

        const captured = await (await fetch('/__captured_requests')).json();
        const last = captured[captured.length - 1] || {};
        result.diagnostics.push(`captured=${JSON.stringify(captured)}`);
        assertion('at least one request captured', captured.length >= 1, captured.length);
        assertion('payload model is switched model', last.model === 'browser-image-model-2', last.model);
        assertion('payload prompt text correct', String(last.prompt || '').includes('连通性测试提示词'), last.prompt);
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
            const n = nodes.find(n => n.id === 'gen-conn');
            if(!n) return false;
            n.images = [];
            n.pending = false;
            n.queued = false;
            delete n.runFinishedAt;
            n.runSettings = {engine: 'api', apiKind: 'image', routingMode: 'auto', ratio: 'square', resolution: '1k', quality: 'auto', count: 1};
            return true;
        }`)();
        const reSelect = await evalFn(`(p) => {
            const n = nodes.find(n => n.id === 'gen-conn');
            if(!n) return false;
            selectedId = n.id;
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
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
