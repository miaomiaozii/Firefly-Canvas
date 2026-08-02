(function(){
    'use strict';

    const query = new URLSearchParams(location.search);
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
    function projectCard(name){
        return [...document.querySelectorAll('.project-card')].find(card => card.querySelector('h2')?.textContent === name);
    }
    function canvasNodes(){
        const world = document.getElementById('world');
        return world ? world.querySelectorAll('.image-node, .prompt-smart-node, .smart-prompt-node').length : 0;
    }
    function promptText(){
        const node = document.getElementById('world')?.querySelector('.prompt-node-text');
        return (node?.value || node?.textContent || '').trim();
    }
    async function runListStage(){
        const listLoaded = await waitFor(() => document.querySelectorAll('.project-card').length >= 2, 15000);
        assertion('project list renders both projects', listLoaded, document.querySelectorAll('.project-card').length);
        const alpha = projectCard('Alpha Project');
        const beta = projectCard('Beta Project');
        assertion('both projects have an open action', Boolean(alpha?.querySelector('.project-open')) && Boolean(beta?.querySelector('.project-open')), {alpha:Boolean(alpha), beta:Boolean(beta)});
        await postResult();
        if(!alpha) return;
        alpha.querySelector('.project-open').click();
    }
    async function runCanvasStage(){
        const nodes = await waitFor(() => canvasNodes() > 0, 20000);
        assertion('canvas renders at least one node', nodes, canvasNodes());
        const text = await waitFor(() => promptText().length > 0, 8000);
        assertion('canvas keeps its own prompt text', text, promptText());
        const back = document.querySelector('.smart-back, [data-nav-back], .back-button');
        assertion('canvas offers a back control', Boolean(back), back?.className || 'missing');
        await postResult();
        if(back){ back.click(); }
    }
    async function run(){
        if(location.pathname.includes('project-list')){
            await runListStage();
        } else if(location.pathname.includes('smart-canvas')){
            await runCanvasStage();
        } else {
            assertion('unexpected page reached', false, location.pathname);
            await postResult();
        }
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(() => {});
})();
