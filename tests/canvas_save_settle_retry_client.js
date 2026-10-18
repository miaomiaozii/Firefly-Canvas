(function(){
    'use strict';

    // #190：saveProjectCanvasUntilSettled 的重试循环是死代码。
    //
    //   for(let attempt = 0; attempt < maxAttempts; attempt++){
    //       ...
    //       const saved = await saveCanvas({automatic:false});
    //       if(saved !== true) return false;   // ← 第一次失败就退出，maxAttempts 形同虚设
    //       if(!hasDurableCanvasChanges()) return true;
    //   }
    //
    // 这里在 saveCanvas 边界注入失败，直接数它被调了几次。修好前：失败一次就
    // return false，只调 1 次。修好后：应该重试到 maxAttempts，并且中途成功时
    // 返回 true。
    //
    // 说明：注入点在 saveCanvas 的返回值边界，不是真 409 网络层。这是有意的——
    // 缺陷本身就是「settle 循环如何对待 saveCanvas 的返回值」，与失败来源无关。
    // 真 409 场景由 tests/history_delete_save_conflict_native_browser.py 覆盖
    // （那个测的是内层 mutation 层的 rebase 重试，它本来就是好的）。

    const result = {
        assertions: [],
        errors: [],
        pass: false,
    };
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass: Boolean(pass), actual});

    async function waitFor(predicate, timeout = 20000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(await predicate()) return true; } catch(_) {}
            await sleep(50);
        }
        return false;
    }

    async function postResult(){
        result.pass = result.errors.length === 0 && result.assertions.every((item) => item.pass);
        await fetch('/__conversation_browser_results', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(result),
        }).catch((error) => result.errors.push(`result upload: ${error.message}`));
    }

    async function main(){
        if(!await waitFor(() => typeof window.saveProjectCanvasUntilSettled === 'function'
            && typeof window.saveCanvas === 'function')){
            result.errors.push('saveProjectCanvasUntilSettled / saveCanvas 未就绪');
            return postResult();
        }
        // 注意：smart-canvas.js 里的 `let canvas` 是顶层词法绑定，**不**在 window 上；
        // canvasState 是 canvas-state.js 显式挂到 window 的，用它判断加载完成。
        if(!await waitFor(() => {
            const nodes = window.canvasState && window.canvasState.nodes;
            return Array.isArray(nodes) && nodes.length > 0;
        })){
            result.errors.push('画布未加载出节点，测不了保存');
            return postResult();
        }

        const realSave = window.saveCanvas;

        // 场景 1：前两次失败、第三次成功。修好后应返回 true 且至少调 3 次。
        let calls = 0;
        window.saveCanvas = async (options) => {
            calls += 1;
            if(calls <= 2) return false;
            return await realSave.call(window, options);
        };
        let settled = null;
        let callsDuring = 0;
        try {
            calls = 0;
            settled = await window.saveProjectCanvasUntilSettled();
            callsDuring = calls;
        } catch(error){
            result.errors.push(`场景1 抛异常: ${error && error.message}`);
        } finally {
            window.saveCanvas = realSave;
        }
        assertion('transient-failure-then-success: 返回值必须是 true', settled === true, String(settled));
        assertion('transient-failure-then-success: 必须重试（调用次数 >= 3）', callsDuring >= 3, String(callsDuring));

        // 场景 2：一直失败。必须重试满 maxAttempts 后返回 false，不能只调一次。
        const maxAttempts = 4;
        calls = 0;
        window.saveCanvas = async () => { calls += 1; return false; };
        let exhausted = null;
        callsDuring = 0;
        try {
            settled = null;
            settled = await window.saveProjectCanvasUntilSettled({maxAttempts});
            callsDuring = calls;
        } catch(error){
            result.errors.push(`场景2 抛异常: ${error && error.message}`);
        } finally {
            window.saveCanvas = realSave;
        }
        assertion('always-fails: 返回值必须是 false', settled === false, String(settled));
        assertion('always-fails: 必须用满重试预算（调用次数 >= maxAttempts）', callsDuring >= maxAttempts, String(callsDuring));
        assertion('always-fails: 不得无限重试（调用次数 <= maxAttempts）', callsDuring <= maxAttempts, String(callsDuring));

        // 场景 3：一次就成功时不得多跑（防止把修复写成无条件重试）。
        calls = 0;
        window.saveCanvas = async (options) => { calls += 1; return await realSave.call(window, options); };
        let clean = null;
        callsDuring = 0;
        try {
            clean = await window.saveProjectCanvasUntilSettled();
            callsDuring = calls;
        } catch(error){
            result.errors.push(`场景3 抛异常: ${error && error.message}`);
        } finally {
            window.saveCanvas = realSave;
        }
        assertion('clean-save: 返回值必须是 true', clean === true, String(clean));
        assertion('clean-save: 无改动时只调一次', callsDuring === 1, String(callsDuring));

        return postResult();
    }

    main().catch((error) => {
        result.errors.push(String((error && error.stack) || error));
        return postResult();
    });
})();
