(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const result = {
        kind: 'final',
        result_id: `undo-redo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
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
    function undoBtn(){
        return document.querySelector('#smartUndoButton');
    }
    function redoBtn(){
        return document.querySelector('#smartRedoButton');
    }
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
        assertion('undo button present', Boolean(undoBtn()), Boolean(undoBtn()));
        assertion('redo button present', Boolean(redoBtn()), Boolean(redoBtn()));
        assertion('undo disabled initially', undoBtn()?.disabled === true, undoBtn()?.disabled);
        assertion('redo disabled initially', redoBtn()?.disabled === true, redoBtn()?.disabled);
        const base = (evalCanvas()?.nodes || []).length;
        assertion('seed nodes present', base === 2, base);

        const clicked = createViaMenu('image');
        const created = await waitFor(() => createdNodes().length === 1, 8000);
        assertion('create image node via menu', clicked && created, `clicked=${clicked} created=${createdNodes().length}`);
        assertion('undo enabled after create', undoBtn()?.disabled === false, undoBtn()?.disabled);

        undoBtn()?.click();
        const undone = await waitFor(() => createdNodes().length === 0, 8000);
        assertion('undo click removes node', undone, `created=${createdNodes().length}`);
        assertion('redo enabled after undo', redoBtn()?.disabled === false, redoBtn()?.disabled);

        redoBtn()?.click();
        const redone = await waitFor(() => createdNodes().length === 1, 8000);
        assertion('redo click restores node', redone, `created=${createdNodes().length}`);
        assertion('undo enabled after redo', undoBtn()?.disabled === false, undoBtn()?.disabled);

        createViaMenu('image');
        const second = await waitFor(() => createdNodes().length === 2, 8000);
        assertion('second create works', second, `created=${createdNodes().length}`);
        assertion('redo disabled after new op', redoBtn()?.disabled === true, redoBtn()?.disabled);

        const target = createdNodes()[createdNodes().length - 1];
        if(target){
            const el = document.querySelector(`[data-id="${target.id}"]`);
            el?.dispatchEvent(new MouseEvent('pointerdown', {bubbles:true, pointerId:1}));
            el?.dispatchEvent(new MouseEvent('pointerup', {bubbles:true, pointerId:1}));
            await sleep(200);
            document.dispatchEvent(new KeyboardEvent('keydown', {key:'Delete', bubbles:true}));
            const deleted = await waitFor(() => createdNodes().length === 1, 8000);
            assertion('delete key removes selected node', deleted, `created=${createdNodes().length}`);
            assertion('undo enabled after delete', undoBtn()?.disabled === false, undoBtn()?.disabled);

            undoBtn()?.click();
            const restored = await waitFor(() => createdNodes().length === 2, 8000);
            assertion('undo restores deleted node', restored, `created=${createdNodes().length}`);
            assertion('redo enabled after delete-undo', redoBtn()?.disabled === false, redoBtn()?.disabled);
        }

        const saveCanvas = evalFn('saveCanvas');
        if(saveCanvas){
            try { await saveCanvas(); } catch(error) { result.errors.push(`saveCanvas: ${error.message}`); }
        }
        const deadline = performance.now() + 10000;
        let saved = null;
        while(performance.now() < deadline){
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if(response.ok){
                    saved = (await response.json()).canvas || null;
                    if((saved?.nodes || []).length === base + 2) break;
                }
            } catch(_) {}
            await sleep(100);
        }
        assertion('server canvas persists restored nodes', (saved?.nodes || []).length === base + 2, (saved?.nodes || []).length);
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
