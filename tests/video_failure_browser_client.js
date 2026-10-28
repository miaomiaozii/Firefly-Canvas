/* Browser client: video generation failure path.
 * Python side patches generate_project_video to fail once then succeed.
 * Asserts: failure visible via toast, node resets, no media on failure,
 * retry lands video media.
 */
(() => {
    const CONVERSATION_URL = '/__conversation_browser_results';
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const evalFn = fn => { try { return eval(fn); } catch (_) { return undefined; } };
    const evalCanvas = () => { try { return eval('canvas'); } catch (_) { return undefined; } };
    const result = { pass: false, assertions: [], errors: [], diagnostics: [] };
    let finished = false;

    function assertion(name, ok, detail) {
        result.assertions.push({ name, pass: Boolean(ok), detail: String(detail == null ? '' : detail) });
    }

    function post() {
        if (finished) return;
        finished = true;
        result.pass = result.errors.length === 0 && result.assertions.length > 0 && result.assertions.every(a => a.pass);
        try {
            fetch(CONVERSATION_URL, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(result),
            }).catch(() => {});
        } catch (e) { /* ignore */ }
    }

    async function waitFor(fn, label, timeoutMs) {
        const t0 = Date.now();
        for (;;) {
            let ok = false;
            try { ok = fn(); } catch (_) {}
            if (ok) return true;
            if (Date.now() - t0 > timeoutMs) { result.errors.push('timeout: ' + label); return false; }
            await sleep(150);
        }
    }

    async function runInitial() {
        try {
            const ok = await waitFor(() => {
                const c = evalCanvas();
                return c && Array.isArray(c.nodes) && c.nodes.length >= 3;
            }, 'seed nodes', 30000);
            if (!ok) return post();
            const canvasObj = evalCanvas();
            const seedPrompt = canvasObj.nodes.find(n => n.id === 'vf-prompt');
            const seedImage = canvasObj.nodes.find(n => n.id === 'vf-source');
            const smartImage = canvasObj.nodes.find(n => n.id === 'vf-video');
            assertion('seed prompt node found', Boolean(seedPrompt));
            assertion('seed image node found', Boolean(seedImage));
            assertion('video target node found', Boolean(smartImage));

            const connectInputNode = evalFn('connectInputNode');
            if (connectInputNode && seedImage && smartImage) {
                try { connectInputNode(seedImage, smartImage.id); } catch (e) { result.errors.push('c: ' + e.message); }
                await sleep(200);
            }
            if (connectInputNode && seedPrompt && smartImage) {
                try { connectInputNode(seedPrompt, smartImage.id); } catch (e) { result.errors.push('c2: ' + e.message); }
                await sleep(200);
            }
            const conns = () => (evalCanvas()?.connections || []).length;
            assertion('edges created', conns() >= 2, conns());

            const cascadeSource = evalCanvas()?.nodes?.find(n => n.id === seedImage.id);
            if (cascadeSource) {
                cascadeSource.runPrompt = '模拟视频失败后再次成功的测试提示词';
                cascadeSource.promptDraftText = cascadeSource.runPrompt;
                cascadeSource.runSettings = {
                    ...(cascadeSource.runSettings || {}),
                    engine: 'api',
                    apiKind: 'video',
                    videoProvider: 'comfly',
                    videoModel: 'kling-video-v3',
                    videoAspect: '1:1',
                    videoResolution: '1k',
                    duration: 5,
                    n: 1,
                };
                result.diagnostics.push('cascadeSource=' + cascadeSource.id + ' runSettings=' + JSON.stringify(cascadeSource.runSettings || null));
            } else {
                result.errors.push('cascadeSource NOT FOUND for vf-source');
            }

            const toasts = [];
            const origToast = evalFn('toast');
            if (origToast) {
                try {
                    window.__origToast__ = origToast;
                    const wrapped = function (msg, ...rest) {
                        toasts.push(String(msg || '').slice(0, 200));
                        if (window.__origToast__) return window.__origToast__.call(null, msg, ...rest);
                    };
                    eval('toast = Object.assign(wrapped, origToast)');
                } catch (e) { result.diagnostics.push('toast patch: ' + e.message); }
            }

            try { eval('canvasState.selectedId = ' + JSON.stringify(smartImage?.id || null)); } catch (_) {}
            await sleep(200);
            const canRun = evalFn('canRunSmartCascade');
            if (canRun && smartImage) {
                const nodeObj = evalCanvas()?.nodes?.find(n => n.id === smartImage.id);
                const can = canRun(nodeObj);
                result.diagnostics.push('canRun=' + can + ' graphEdges=' + (evalFn('smartCascadeGraphForTail')?.(nodeObj)?.edges?.length));
                if (!can) {
                    const g = evalFn('smartCascadeGraphForTail')?.(nodeObj);
                    result.diagnostics.push('graph: ' + JSON.stringify(g || {}));
                }
            }
            const runSmartCascade = evalFn('runSmartCascade');
            if (!runSmartCascade || !smartImage) { result.errors.push('no runSmartCascade or smartImage'); return post(); }
            const nodeObj = evalCanvas()?.nodes?.find(n => n.id === smartImage.id);

            // FIRST RUN: fake adapter raises -> failure path
            await runSmartCascade(nodeObj);
            await waitFor(() => evalCanvas()?.nodes?.find(n => n.id === smartImage.id)?.running !== true, 'node not running', 15000);
            await sleep(800);
            const failToast = toasts.some(t => /失败|error|错误|服务|重试|模拟|service|simulated/i.test(t));
            const noMediaAfterFail = (evalCanvas()?.nodes?.find(n => n.id === smartImage.id)?.images?.length || 0) === 0;
            assertion('failure toast shown', failToast, toasts.join(' | ').slice(0, 300));
            assertion('no media after failure', noMediaAfterFail);

            // SECOND RUN: adapter succeeds -> media lands
            await runSmartCascade(evalCanvas()?.nodes?.find(n => n.id === smartImage.id));
            const landed = await waitFor(() => (evalCanvas()?.nodes?.find(n => n.id === smartImage.id)?.images?.length || 0) >= 1, 'media landed', 30000);
            assertion('video media landed on retry', landed);
            result.diagnostics.push('toasts=' + JSON.stringify(toasts));
            result.diagnostics.push('finalImages=' + JSON.stringify((evalCanvas()?.nodes?.find(n => n.id === smartImage.id)?.images || []).map(i => ({asset_id: i.asset_id, mime_type: i.mime_type, url: String(i.url || '').slice(0, 60)}))));
            const target = evalCanvas()?.nodes?.find(n => n.id === smartImage.id);
            const hasVideo = (target?.images || []).some(img => (img.mime_type || '').includes('video'));
            assertion('landed item is video', hasVideo, JSON.stringify((target?.images || []).map(i => i.mime_type)));

            // #238: rendered preview must carry tier poster + preload=none, and
            // hover (pointerover/out on the shell-delegated listener) drives playback.
            await sleep(400);
            const videoEl = document.querySelector('.media-video-card video[data-video-preview="1"]');
            assertion('video preview rendered with preload=none', videoEl && videoEl.getAttribute('preload') === 'none', videoEl ? videoEl.outerHTML.slice(0, 220) : 'no video[data-video-preview]');
            assertion('video preview has tier poster', videoEl && /([?&])w=(256|512|1024)/.test(videoEl.getAttribute('poster') || ''), (videoEl && videoEl.getAttribute('poster')) || 'no poster');
            if (videoEl) {
                videoEl.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
                await sleep(150);
                assertion('hover starts playback (flag set)', videoEl.dataset.hoverPlaying === '1');
                videoEl.dispatchEvent(new PointerEvent('pointerout', { bubbles: true }));
                await sleep(150);
                assertion('pointerout stops hover playback', videoEl.dataset.hoverPlaying !== '1');
            }
            post();
        } catch (err) {
            result.errors.push('exception: ' + (err && err.message) + ' @ ' + String((err && err.stack) || '').split('\n')[1]);
            post();
        }
    }

    window.addEventListener('load', () => setTimeout(runInitial, 600));
})();
