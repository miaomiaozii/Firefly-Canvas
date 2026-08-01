(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const result = {
        kind: 'final',
        result_id: `genfail-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
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
    async function waitForLoaded(){
        return waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Generation Failure Suite'), 15000);
    }
    function nodeIdsByPrefix(prefix){
        return [...document.querySelectorAll('.image-node')].map(el => el.dataset.id || '').filter(id => id.startsWith(prefix));
    }
    async function runInitial(){
        if(!await waitForLoaded()) return;
        await waitFor(() => nodeIdsByPrefix('seed-prompt').length && nodeIdsByPrefix('seed-image').length, 10000);
        const seedPrompt = nodeIdsByPrefix('seed-prompt')[0];
        const seedImage = nodeIdsByPrefix('seed-image')[0];
        assertion('seed prompt node exists', Boolean(seedPrompt), seedPrompt || '');
        assertion('seed image node exists', Boolean(seedImage), seedImage || '');
        const c = evalCanvas();
        if(c){
            c.nodes.forEach((node, i) => {
                node.x = 80 + (i % 3) * 300;
                node.y = 80 + Math.floor(i / 3) * 260;
            });
            evalFn('render')?.();
        }
        await sleep(200);
        const smartPrompt = c?.nodes?.find(n => String(n.id).startsWith('smart-') && n.type === 'smart-prompt');
        const smartImage = c?.nodes?.find(n => String(n.id).startsWith('smart-') && n.type === 'smart-image');
        assertion('downstream prompt node found', Boolean(smartPrompt), smartPrompt?.id || '');
        assertion('downstream image node found', Boolean(smartImage), smartImage?.id || '');
        // connect seed-prompt -> smart-image (prompt flows into output)
        const connectInputNode = evalFn('connectInputNode');
        if(connectInputNode && seedPrompt && smartImage){
            try { connectInputNode(seedPrompt, smartImage.id); } catch(e) { result.errors.push(`c: ${e.message}`); }
            await sleep(200);
        }
        // also connect seed-image -> smart-image (image chain) so cascade graph has edges
        if(connectInputNode && seedImage && smartImage){
            try { connectInputNode(seedImage, smartImage.id); } catch(e) { result.errors.push(`c2: ${e.message}`); }
            await sleep(200);
        }
        const conns = () => (evalCanvas()?.connections || []).length;
        assertion('edges created', conns() >= 2, conns());
        // configure the cascade SOURCE node (seed-image) with prompt + comfly + model + size.
        // runCascadeStepIntoNode uses requestNode = sourceNode for both prompt and settings.
        const cascadeSource = evalCanvas()?.nodes?.find(n => n.id === seedImage);
        if(cascadeSource){
            cascadeSource.runPrompt = '模拟失败后再次成功的测试提示词';
            cascadeSource.promptDraftText = cascadeSource.runPrompt;
            cascadeSource.runSettings = {
                ...(cascadeSource.runSettings || {}),
                engine:'api',
                apiKind:'image',
                provider_id:'comfly',
                model:'nano-banana-pro',
                size:'1024x1024',
                resolution:'1k',
                ratio:'square',
                n:1
            };
            result.diagnostics.push(`cascadeSource=${cascadeSource.id} type=${cascadeSource.type} runSettings=${JSON.stringify(cascadeSource.runSettings || null)}`);
        } else {
            result.diagnostics.push('cascadeSource NOT FOUND for seed-image');
        }
        // capture toasts to assert failure visibility
        const toasts = [];
        const origToast = evalFn('toast');
        if(origToast){
            try {
                window.__origToast__ = origToast;
                const wrapped = function(msg, ...rest){
                    toasts.push(String(msg || '').slice(0, 200));
                    if(window.__origToast__) return window.__origToast__.call(null, msg, ...rest);
                };
                const patched = Object.assign(wrapped, origToast);
                eval('toast = patched');
            } catch(e) { result.diagnostics.push(`toast patch: ${e.message}`); }
        }
        // intercept fetch to observe image-generation-tasks traffic
        const fetchCalls = [];
        const origFetch = window.fetch.bind(window);
        window.fetch = async function(url, options){
            const u = String(url);
            if(u.includes('image-generation-tasks') || u.includes('/tasks/') || u.includes('/v1/')){
                fetchCalls.push(u + '|' + String(options?.method || 'GET') + '|' + String(options?.body || '').slice(0, 600));
            }
            return origFetch(u, options);
        };
        // FIRST RUN: fake adapter raises -> failure path
        try { eval('selectedId = ' + JSON.stringify(smartImage?.id || null)); } catch(_) {}
        await sleep(200);
        const canRunSmartCascade = evalFn('canRunSmartCascade');
        if(canRunSmartCascade && smartImage){
            const nodeObj = evalCanvas()?.nodes?.find(n => n.id === smartImage.id);
            const can = canRunSmartCascade(nodeObj);
            result.diagnostics.push(`canRun=${can} graphEdges=${evalFn('smartCascadeGraphForTail')?.(nodeObj)?.edges?.length}`);
            if(!can){
                const g = evalFn('smartCascadeGraphForTail')?.(nodeObj);
                result.diagnostics.push(`graph: ${JSON.stringify(g || {})}`);
            }
        }
        const runSmartCascade = evalFn('runSmartCascade');
        let firstRunError = null;
        if(runSmartCascade && smartImage){
            const nodeObj = evalCanvas()?.nodes?.find(n => n.id === smartImage.id);
            result.diagnostics.push(`pre-run runSettings=${JSON.stringify(nodeObj?.runSettings || null)} images=${JSON.stringify(nodeObj?.images || [])}`);
            try {
                await runSmartCascade(nodeObj);
                result.diagnostics.push('first run resolved without error');
            } catch(error) {
                firstRunError = error;
                result.diagnostics.push(`first run error: ${error.message}`);
            }
        }
        await sleep(800);
        const firstImages = evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || [];
        result.diagnostics.push(`fetchCalls=${JSON.stringify(fetchCalls)} images0=${JSON.stringify(firstImages[0] || null)}`);
        assertion('first run surfaces error', Boolean(firstRunError) || toasts.some(t => t.includes('模拟') || t.includes('失败') || t.includes('Failed') || t.includes('error')), `${firstRunError?.message || 'no error'} toasts=${JSON.stringify(toasts.slice(-2))}`);
        assertion('failure toast shown', toasts.some(t => t.includes('模拟') || t.includes('失败') || t.includes('Fail')), JSON.stringify(toasts.slice(-2)));
        // node must not keep running forever after failure
        const nodeAfterFail = evalCanvas()?.nodes?.find(n => n.id === smartImage?.id);
        assertion('node not stuck running', !nodeAfterFail?.running, `running=${Boolean(nodeAfterFail?.running)} pending=${nodeAfterFail?.pending}`);
        assertion('no images after failure', (nodeAfterFail?.images || []).length === 0, `images=${(nodeAfterFail?.images || []).length}`);
        // SECOND RUN: fake succeeds -> output lands
        let secondRunOk = false;
        if(runSmartCascade && smartImage){
            try {
                await runSmartCascade(evalCanvas()?.nodes?.find(n => n.id === smartImage.id));
                secondRunOk = true;
            } catch(error) {
                result.errors.push(`second run: ${error.message}`);
            }
        }
        await waitFor(() => (evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || []).length > 0, 15000);
        const finalImages = evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || [];
        assertion('second run succeeds', secondRunOk, 'resolved');
        assertion('output media landed after retry', finalImages.length > 0, `images=${finalImages.length}`);
        // cleanup toast patch
        if(origToast){
            try { eval('toast = window.__origToast__'); } catch(_) {}
        }
        await postResult();
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    runInitial().catch(error => result.errors.push(error?.stack || String(error))).finally(() => postResult());
})();
