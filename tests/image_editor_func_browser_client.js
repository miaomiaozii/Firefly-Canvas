(function(){
    'use strict';

    const result = {
        trace: [],
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        pass: false
    };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
    const trace = (label) => result.trace.push(label + '@' + Math.round(performance.now()/1000));
    async function waitFor(predicate, timeout=12000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                if(await predicate()) return true;
            } catch(_) {}
            await sleep(40);
        }
        return false;
    }
    async function postResult(){
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    function evalFn(fn){
        return Function('"use strict";return (' + fn + ')')();
    }
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
        return {w: bmp.width, h: bmp.height, data: data};
    }

    async function openEditor(){
        const ok = await waitFor(() => {
            try {
                const node = document.querySelector('#world .image-node[data-id="edit-node"]');
                if(!node) return false;
                evalFn('(id) => { openImageEditor(id, 0); return true; }')('edit-node');
                return modalOpen();
            } catch(_) { return false; }
        }, 5000);
        return ok;
    }

    async function waitApplied(beforeName, imagesLen){
        return waitFor(() => {
            try {
                const n = findNode('edit-node');
                if(!n) return false;
                if(imagesLen !== undefined && n.images.length !== imagesLen) return false;
                return !modalOpen() && n.images[0] && n.images[0].name !== beforeName;
            } catch(_) { return false; }
        }, 5000);
    }

    async function run(){
        trace('run-start');
        trace('boot-wait');
        const bootOk = await waitFor(() => {
            try { return nodes().some(n => n.id === 'edit-node'); } catch(_) { return false; }
        }, 5000);
        trace('boot-done:' + bootOk);
        assertion('canvas boot with edit-node', bootOk, bootOk ? 'ok' : 'nodes missing');

        trace('crop-step');
        // --- crop ---
        assertion('editor opens with image loaded', await openEditor() && await waitFor(cropImageLoaded, 12000), 'modal+image');
        const cropReady = await waitFor(() => { try { evalFn('() => { setImageEditMode("crop", true); return true; }')(); return cropStateReady(); } catch(_) { return false; } }, 5000);
        assertion('crop mode cropState ready', cropReady);
        if(cropReady){
            const before = nodeName('edit-node');
            const crop = evalFn('() => { const c = eval("cropState"); c.w = Math.round(c.w / 2); return { w: c.w, h: c.h }; }')();
            const scaleXY = evalFn('() => { const img = document.getElementById("cropImage"); return { sx: img.naturalWidth / img.clientWidth, sy: img.naturalHeight / img.clientHeight }; }')();
            const expectW = Math.round(crop.w * scaleXY.sx);
            const expectH = Math.round(crop.h * scaleXY.sy);
            evalFn('() => { eval("applyImageEdit()"); return true; }')();
            const applied = await waitApplied(before);
            assertion('crop applied and modal closed', applied);
            if(applied){
                const img = findNode('edit-node').images[0];
                assertion('crop image name', (img.name || '').includes('_crop'), img.name);
                try {
                    const pix = await fetchImage(img.url);
                    assertion('crop width', Math.abs(pix.w - expectW) <= 2, pix.w + ' vs ' + expectW);
                    assertion('crop height preserved', Math.abs(pix.h - expectH) <= 2, pix.h + ' vs ' + expectH);
                } catch(e){ result.errors.push('crop fetch: ' + e.message); }
            }
        }

        trace('brush-step');
        // --- brush ---
        const brushOpen = await openEditor() && await waitFor(cropImageLoaded, 12000);
        assertion('editor reopened for brush', brushOpen);
        if(brushOpen){
            const mode = await waitFor(() => { try { evalFn('() => { setImageEditMode("brush", true); return true; }')(); return editMode() === 'brush'; } catch(_) { return false; } }, 5000);
            assertion('brush mode active', mode);
            if(mode){
                const painted = evalFn('() => { const c = eval("editDrawCanvas()"); const ctx = c.getContext("2d"); ctx.fillStyle = "#000000"; ctx.fillRect(4, 4, 20, 20); return c.width + "x" + c.height; }')();
                assertion('brush stroke drawn', !!painted, String(painted));
                const before = nodeName('edit-node');
                evalFn('() => { eval("applyImageEdit()"); return true; }')();
                const applied = await waitApplied(before);
                assertion('brush applied and modal closed', applied);
                if(applied){
                    const img = findNode('edit-node').images[0];
                    assertion('brush image name', (img.name || '').includes('_paint'), img.name);
                    try {
                        const pix = await fetchImage(img.url);
                        let dark = 0;
                        for(let i = 0; i < pix.data.length && dark < 5; i += 4){
                            if(pix.data[i] < 15 && pix.data[i+1] < 15 && pix.data[i+2] < 15) dark++;
                        }
                        assertion('brush pixels visible in result', dark >= 5, 'dark px=' + dark);
                    } catch(e){ result.errors.push('brush fetch: ' + e.message); }
                }
            }
        }

        trace('mask-step');
        // --- mask ---
        const maskOpen = await openEditor() && await waitFor(cropImageLoaded, 12000);
        assertion('editor reopened for mask', maskOpen);
        if(maskOpen){
            const mode = await waitFor(() => { try { evalFn('() => { setImageEditMode("mask", true); return true; }')(); return editMode() === 'mask'; } catch(_) { return false; } }, 5000);
            assertion('mask mode active', mode);
            if(mode){
                evalFn('() => { const c = eval("editDrawCanvas()"); const ctx = c.getContext("2d"); ctx.fillStyle = "#000000"; ctx.fillRect(30, 30, 20, 20); return true; }')();
                const before = nodeName('edit-node');
                evalFn('() => { eval("applyImageEdit()"); return true; }')();
                const applied = await waitFor(() => {
                    try {
                        const n = findNode('edit-node');
                        return !modalOpen() && n && n.images.length === 2;
                    } catch(_) { return false; }
                }, 5000);
                assertion('mask applied, second image appended', applied);
                if(applied){
                    const imgs = findNode('edit-node').images;
                    assertion('mask image role', imgs[1] && imgs[1].role === 'mask', imgs[1] && imgs[1].role);
                    const selIdx = evalFn('() => { const s = eval("selectedImage"); return s && s.index; }')();
                    assertion('selectedImage index after mask', selIdx === 1, String(selIdx));
                }
            }
        }

        trace('outpaint-step');
        // --- outpaint ---
        const outOpen = await openEditor() && await waitFor(cropImageLoaded, 12000);
        assertion('editor reopened for outpaint', outOpen);
        if(outOpen){
            const ready = await waitFor(() => { try { evalFn('() => { setImageEditMode("outpaint", true); return true; }')(); return cropStateReady(); } catch(_) { return false; } }, 5000);
            assertion('outpaint cropState ready', ready);
            if(ready){
                const grown = evalFn('() => { const c = eval("cropState"); c.w = Math.round(c.w * 1.5); c.h = Math.round(c.h * 1.5); return c.w; }')();
                assertion('outpaint region expanded', grown > 0, 'w=' + grown);
                const before = nodeName('edit-node');
                evalFn('() => { eval("applyImageEdit()"); return true; }')();
                const applied = await waitApplied(before);
                assertion('outpaint applied and modal closed', applied);
                if(applied){
                    const img = findNode('edit-node').images[0];
                    assertion('outpaint image name', (img.name || '').includes('_outpaint'), img.name);
                    try {
                        const pix = await fetchImage(img.url);
                        assertion('outpaint width grew', pix.w > 64, pix.w + ' > 64');
                        const node = findNode('edit-node');
                        const os = node.outpaintSize || {};
                        assertion('outpaintSize matches', os.width === pix.w, JSON.stringify(os) + ' vs ' + pix.w);
                        assertion('outpaint prompt draft set', (node.promptDraftText || '').includes('Remove white area and fill the scene'), String(node.promptDraftText));
                    } catch(e){ result.errors.push('outpaint fetch: ' + e.message); }
                }
            }
        }

        trace('grid-step');
        // --- grid split ---
        const nodeCountBefore = nodes().length;
        const gridOpen = await openEditor() && await waitFor(cropImageLoaded, 12000);
        assertion('editor reopened for grid split', gridOpen);
        if(gridOpen){
            const mode = await waitFor(() => { try { evalFn('() => { setImageEditMode("grid", true); applyGridPreset(2, 2); return true; }')(); return editMode() === 'grid'; } catch(_) { return false; } }, 5000);
            assertion('grid mode active with 2x2 preset', mode);
            const gridDiag = evalFn('() => { let s = null; try { s = eval("gridSplitSettings()"); } catch(e) { s = "ERR:" + e.message; } return { h: (document.getElementById("gridHorizontalLines") || {}).value, v: (document.getElementById("gridVerticalLines") || {}).value, settings: s }; }')();
            assertion('grid diag h/v/settings', !!(gridDiag && gridDiag.settings && gridDiag.settings.rows === 2 && gridDiag.settings.cols === 2), JSON.stringify(gridDiag));
            if(mode){
                evalFn('() => { eval("applyImageEdit()"); return true; }')();
                const applied = await waitFor(() => {
                    try { return !modalOpen() && nodes().length === nodeCountBefore + 1; } catch(_) { return false; }
                }, 5000);
                assertion('grid split created new node', applied);
                if(applied){
                    const gridNode = nodes().find(n => n.title === 'Grid');
                    assertion('grid node titled Grid', !!gridNode);
                    assertion('grid node has 4 images', !!(gridNode && gridNode.images && gridNode.images.length === 4), gridNode && gridNode.images && String(gridNode.images.length));
                    assertion('grid images carry grid metadata', !!(gridNode && gridNode.images && gridNode.images[0] && gridNode.images[0].grid));
                }
            }
        }

        trace('resize-step');
        // --- resize ---
        const resOpen = await openEditor() && await waitFor(cropImageLoaded, 12000);
        assertion('editor reopened for resize', resOpen);
        if(resOpen){
            const mode = await waitFor(() => { try { evalFn('() => { setImageEditMode("resize", true); setImageResizeScale(0.5); return true; }')(); return editMode() === 'resize'; } catch(_) { return false; } }, 5000);
            assertion('resize mode active', mode);
            if(mode){
                const before = nodeName('edit-node');
                evalFn('() => { eval("applyImageEdit()"); return true; }')();
                const applied = await waitApplied(before);
                assertion('resize applied and modal closed', applied);
                if(applied){
                    const img = findNode('edit-node').images[0];
                    assertion('resize image name', (img.name || '').includes('_resize_50pct'), img.name);
                    assertion('resize natural size', img.natural_w === 32 && img.natural_h === 32, img.natural_w + 'x' + img.natural_h);
                }
            }
        }

        trace('persist-step');
        // --- persist ---
        const canvasId = (0, eval)('canvasId');
        trace('persist-wait');
        const persisted = await waitFor(async () => {
            try {
                const res = await fetch('/api/canvases/' + encodeURIComponent(canvasId));
                if(!res.ok) return false;
                const text = JSON.stringify(await res.json());
                return text.includes('_resize_50pct') && text.includes('"Grid"');
            } catch(_) { return false; }
        }, 20000);
        assertion('server canvas persisted all ops', persisted, 'final=' + persisted);
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
