(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const result = {
        kind: 'final',
        result_id: `node-flow-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
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
        return waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Node Flow Suite'), 15000);
    }
    function setInput(el, value){
        if(!el) return;
        el.value = value;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
    }
    function nodeIdsByPrefix(prefix){
        return [...document.querySelectorAll('.image-node')].map(el => el.dataset.id || '').filter(id => id.startsWith(prefix));
    }
    async function runInitial(){
        if(!await waitForLoaded()) return;
        // seed nodes: seed-prompt (text) -> seed-image (generated image)
        await waitFor(() => nodeIdsByPrefix('seed-prompt').length && nodeIdsByPrefix('seed-image').length, 10000);
        const seedPrompt = nodeIdsByPrefix('seed-prompt')[0];
        const seedImage = nodeIdsByPrefix('seed-image')[0];
        assertion('seed prompt node exists', Boolean(seedPrompt), seedPrompt || '');
        assertion('seed image node exists', Boolean(seedImage), seedImage || '');
        const c = evalCanvas();
        if(c){
            result.diagnostics.push(`canvas nodes: ${c.nodes.map(n => `${n.id}:${n.type}`).join(',')}`);
            const moduleNodes = eval('nodes');
            const mn = moduleNodes?.map(n => n.id);
            result.diagnostics.push(`module nodes (${moduleNodes?.length}): ${mn ? mn.join(',') : 'N/A'}`);
            c.nodes.forEach((node, i) => {
                node.x = 80 + (i % 3) * 300;
                node.y = 80 + Math.floor(i / 3) * 260;
            });
            evalFn('render')?.();
        }
        await sleep(200);
        // connect seed-prompt -> seed-image -> downstream smart-prompt2 -> downstream smart-image2
        const connectInputNode = evalFn('connectInputNode');
        // collect node ids by type from canvas
        const smartPrompt = c?.nodes?.find(n => String(n.id).startsWith('smart-') && n.type === 'smart-prompt');
        const smartImage = c?.nodes?.find(n => String(n.id).startsWith('smart-') && n.type === 'smart-image');
        assertion('downstream prompt node found', Boolean(smartPrompt), smartPrompt?.id || '');
        assertion('downstream image node found', Boolean(smartImage), smartImage?.id || '');
        if(connectInputNode && smartPrompt && smartImage){
            let r1 = false, r2 = false, r3 = false;
            try { r1 = connectInputNode(seedPrompt, smartPrompt.id) === true; } catch(e) { result.errors.push(`c1: ${e.message}`); }
            try { r2 = connectInputNode(seedImage, smartImage.id) === true; } catch(e) { result.errors.push(`c2: ${e.message}`); }
            try { r3 = connectInputNode(smartPrompt.id, smartImage.id) === true; } catch(e) { result.errors.push(`c3: ${e.message}`); }
            result.diagnostics.push(`connect returns: ${r1}/${r2}/${r3}`);
            await sleep(300);
        }
        const conns = () => (evalCanvas()?.connections || []).length;
        assertion('three edges created', conns() >= 3, conns());
        // set node-level settings on smart-image2: provider/model/size override
        if(smartImage){
            const runSettings = smartImage.runSettings || {};
            smartImage.runSettings = {
                ...runSettings,
                engine:'api',
                apiKind:'image',
                provider_id:'comfly',
                model:'nano-banana-pro',
                size:'1024x1024',
                n:1
            };
            const strip = evalFn('stripOutpaintDisplaySettings');
            const smartSettingsForNode = evalFn('smartSettingsForNode');
            const nodeSettings = strip ? strip(smartImage.runSettings, smartImage) : smartImage.runSettings;
            const effective = smartSettingsForNode ? smartSettingsForNode(smartImage) : nodeSettings;
            assertion('node settings override global', effective?.model === 'nano-banana-pro' && effective?.provider_id === 'comfly', `model=${effective?.model} provider=${effective?.provider_id}`);
        }
        // run cascade: source = smart-prompt, target = smart-image
        const selectNode = evalFn('selectNode');
        if(selectNode && smartImage) selectNode(smartImage.id);
        await sleep(200);
        const runSmartCascade = evalFn('runSmartCascade');
        const canRunSmartCascade = evalFn('canRunSmartCascade');
        const smartCascadeGraphForTail = evalFn('smartCascadeGraphForTail');
        if(canRunSmartCascade && smartCascadeGraphForTail && smartImage){
            const nodeObj = evalCanvas()?.nodes?.find(n => n.id === smartImage.id);
            const can = canRunSmartCascade(nodeObj);
            const g = smartCascadeGraphForTail(nodeObj);
            result.diagnostics.push(`canRun=${can} edges=${g?.edges?.length} path=${(g?.path || []).map(n => n?.id).join('>')}`);
            const seedImg = evalCanvas()?.nodes?.find(n => n.id === 'seed-image');
            result.diagnostics.push(`seed-image images: ${JSON.stringify(seedImg?.images || [])}`);
            const chain = evalFn('smartImageChainTo');
            const chainRes = chain ? chain(smartImage.id, {includeFlow:true}).map(n => n?.id) : null;
            result.diagnostics.push(`smartImageChainTo: ${chainRes ? chainRes.join('>') : 'N/A'}`);
            const cg = evalCanvas()?.connections || [];
            result.diagnostics.push(`conns=${cg.length} ${JSON.stringify(cg.map(c => `${c.from}->${c.to}:${c.kind}`))}`);
        }
        let cascadeOk = false;
        if(runSmartCascade){
            try {
                const before = (evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || []).length;
                const runResult = await runSmartCascade(evalCanvas()?.nodes?.find(n => n.id === smartImage.id));
                cascadeOk = true;
                result.diagnostics.push(`cascade finished; images before=${before} after=${(evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || []).length}`);
            } catch(error) {
                result.errors.push(`runSmartCascade: ${error.message}`);
            }
        }
        assertion('cascade completes', cascadeOk, 'runSmartCascade resolved');
        await waitFor(() => (evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || []).length > 0, 15000);
        const finalImages = evalCanvas()?.nodes?.find(n => n.id === smartImage?.id)?.images || [];
        assertion('downstream image node received output media', finalImages.length > 0, `images=${finalImages.length}`);
        // verify captured adapter params via fetch to a diagnostics endpoint the python side exposes
        const saveCanvasFn = evalFn('saveCanvas');
        if(saveCanvasFn){
            try { await saveCanvasFn(); } catch(error) { result.errors.push(`saveCanvas: ${error.message}`); }
        }
        await sleep(1500);
        const probe = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
        const probeJson = await probe.json().catch(() => ({}));
        const canvasNode = probeJson?.canvas?.nodes || [];
        const savedSmartImage = canvasNode.find(n => n.id === smartImage?.id);
        result.diagnostics.push(`saved runSettings: ${JSON.stringify(savedSmartImage?.runSettings || null)}`);
        assertion('node settings persisted after save', savedSmartImage?.runSettings?.model === 'nano-banana-pro' && savedSmartImage?.runSettings?.provider_id === 'comfly', JSON.stringify(savedSmartImage?.runSettings));
        await postResult();
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    runInitial().catch(error => result.errors.push(error?.stack || String(error))).finally(() => postResult());
})();
