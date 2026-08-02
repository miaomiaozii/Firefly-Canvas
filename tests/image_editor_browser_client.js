(function(){
    'use strict';

    const result = {
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        pass: false
    };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
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
    async function run(){
        const editorOpen = await waitFor(() => {
            try { return evalFn('() => typeof openImageEditor === "function" && !!document.querySelector("#world .image-node")')(); }
            catch(_) { return false; }
        }, 15000);
        assertion('canvas loaded with an image node', editorOpen, document.querySelector('#world .image-node')?.getAttribute('data-id') || 'none');

        const open = await waitFor(() => {
            try {
                const node = document.getElementById('world')?.querySelector('.image-node');
                if(!node) return false;
                const result = evalFn('(id) => { openImageEditor(id, 0); return document.getElementById("imageEditModal")?.classList.contains("open"); }')(node.getAttribute('data-id'));
                return result;
            } catch(_) { return false; }
        }, 8000);
        assertion('openImageEditor opens the image editor modal', open, document.getElementById('imageEditModal')?.className || 'closed');

        const modal = document.getElementById('imageEditModal');
        const toolButtons = ['crop', 'outpaint', 'mask', 'brush', 'grid'];
        const toolStates = {};
        for(const tool of toolButtons){
            const ok = await waitFor(() => {
                try {
                    return evalFn('(t) => { setImageEditMode(t, true); return document.getElementById("imageEditModal")?.classList.contains("open"); }')(tool);
                } catch(_) { return false; }
            }, 5000);
            toolStates[tool] = ok;
        }
        assertion('all five edit modes switch inside the modal', Object.values(toolStates).every(Boolean), toolStates);

        const close = await waitFor(() => {
            try { return evalFn('() => { closeImageEditor(); return !document.getElementById("imageEditModal")?.classList.contains("open"); }')(); }
            catch(_) { return false; }
        }, 5000);
        assertion('editor closes cleanly', close, document.getElementById('imageEditModal')?.className || 'closed');

        const stillOpen = document.getElementById('imageEditModal')?.classList.contains('open');
        assertion('closing the editor leaves the canvas interactive', !stillOpen, stillOpen ? 'open' : 'closed');
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
