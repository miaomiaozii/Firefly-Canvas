(function(){
    const result = {
        pass: false,
        assertions: [],
        timeline: [],
        consoleErrors: [],
        exceptions: [],
        timings: {},
    };
    function assertion(name, pass, actual){
        result.assertions.push({ name, pass, actual: String(actual) });
    }
    function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

    const origError = window.onerror;
    window.addEventListener('error', ev => result.consoleErrors.push(String(ev.message || ev.error)));
    const origConsoleError = console.error;
    console.error = (...args) => { result.consoleErrors.push(args.map(a => String(a)).join(' ')); origConsoleError.apply(console, args); };

    async function waitFor(fn, ms, label){
        const t0 = Date.now();
        while(Date.now() - t0 < ms){
            try {
                const v = await fn();
                if(v) return v;
            } catch(e) {}
            await sleep(50);
        }
        result.exceptions.push(`waitFor timeout: ${label}`);
        return null;
    }

    function dragNode(el, dx, dy){
        const rect = el.getBoundingClientRect();
        const sx = rect.left + Math.min(48, rect.width / 2);
        const sy = rect.top + Math.min(26, rect.height / 2);
        const before = { left: el.style.left, top: el.style.top };
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, buttons: 1, clientX: sx, clientY: sy }));
        const wx = sx + dx, wy = sy + dy;
        document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, button: 0, buttons: 1, clientX: wx, clientY: wy }));
        document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0, clientX: wx, clientY: wy }));
        return before;
    }

    async function run(){
        const t0 = Date.now();
        const titleEl = await waitFor(() => document.getElementById('smartTitle') && document.getElementById('smartTitle').textContent.trim(), 30000, 'canvas title');
        result.timeline.push({ t: Date.now() - t0, label: 'canvas loaded', title: titleEl && titleEl.textContent });

        // wait for prompt node rendered
        const promptNode = await waitFor(() => document.querySelector('.prompt-smart-node[data-id="prompt-0"]'), 30000, 'prompt node');
        assertion('画布加载且提示词节点出现', Boolean(promptNode), promptNode && promptNode.className);

        // count image nodes actually rendered
        await sleep(800);
        const imgNodes = document.querySelectorAll('.image-node[data-id^="img-"]');
        result.timings.renderedImageNodes = imgNodes.length;
        assertion('200 个图片节点渲染', imgNodes.length >= 180, imgNodes.length);

        // wait for images to load (naturalWidth > 0)
        let loadedCount = 0;
        const tLoad0 = Date.now();
        await waitFor(() => {
            loadedCount = [...document.querySelectorAll('.image-node[data-id^="img-"] .node-img')].filter(im => im.complete && im.naturalWidth > 0).length;
            return loadedCount >= 180;
        }, 60000, 'images loaded');
        result.timings.imageLoadMs = Date.now() - tLoad0;
        result.timings.loadedImages = loadedCount;
        assertion('180+ 图片真实加载', loadedCount >= 180, loadedCount);

        // now drag the prompt node
        const stateBefore = document.querySelector('#projectSaveState')?.dataset?.state || '';
        const tDrag0 = Date.now();
        dragNode(promptNode, 120, 80);
        await sleep(400);
        const stateAfterDrag = document.querySelector('#projectSaveState')?.dataset?.state || '';
        result.timings.dragToDirtyMs = Date.now() - tDrag0;
        assertion('拖动后进入 dirty', stateAfterDrag === 'dirty', stateAfterDrag + ' (before: ' + stateBefore + ')');

        // measure autosave latency: how long from dirty -> saved
        const tSave0 = Date.now();
        const savedOk = await waitFor(() => {
            const s = document.querySelector('#projectSaveState')?.dataset?.state || '';
            return s === 'saved';
        }, 120000, 'save settle (saved)');
        result.timings.saveSettleMs = Date.now() - tSave0;
        result.timings.finalState = document.querySelector('#projectSaveState')?.dataset?.state || '';
        assertion('自动保存收敛到已保存', Boolean(savedOk), savedOk);

        // measure a second autosave burst (drag 5 nodes quickly) to check coalescing
        const tBurst0 = Date.now();
        for(let i = 0; i < 5; i++){
            const el = document.querySelector(`.image-node[data-id="img-${String(i).padStart(3, '0')}"]`);
            if(el) dragNode(el, 30, 20);
            await sleep(30);
        }
        const dirtyAfterBurst = document.querySelector('#projectSaveState')?.dataset?.state === 'dirty';
        result.timings.burstDragMs = Date.now() - tBurst0;
        assertion('连续拖 5 个节点后 dirty', dirtyAfterBurst, dirtyAfterBurst);

        result.timings.totalMs = Date.now() - t0;
        result.pass = result.assertions.filter(a => !a.pass).length === 0;
        return result;
    }

    run().then(r => {
        window.__perfResult = r;
        try {
            fetch('/__conversation_browser_results', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(r),
            }).catch(err => result.exceptions.push('post: ' + err.message));
        } catch(e) { result.exceptions.push('post throw: ' + e.message); }
    }).catch(err => {
        result.exceptions.push('run throw: ' + (err && err.stack || err));
        result.pass = false;
        try {
            fetch('/__conversation_browser_results', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(result),
            });
        } catch(e) {}
    });
})();
