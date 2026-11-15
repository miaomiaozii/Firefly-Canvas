(function(){
    'use strict';

    // #377：项目在另一个标签页被删掉后，画布页还在按防抖周期往一个不存在的 project_id
    // 打 PATCH —— 旧 access 日志里这样刷了 15,183 次 404。
    //
    // 这个 client 在真实 headless-Chrome 里跑完整链路：真发 PATCH、真删项目（走 HTTP 路由），
    // 然后数 PATCH 的次数。修好前：每轮防抖都再打一次，PATCH 次数 >= 2，而且没有任何提示。
    // 修好后：只打一次 404，随后弹常驻提示，自动保存停掉，画布内容还能导出。

    const result = { assertions: [], errors: [], notes: {}, pass: false };
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
        if(!await waitFor(() => typeof window.saveCanvas === 'function'
            && typeof window.scheduleSave === 'function')){
            result.errors.push('saveCanvas / scheduleSave 未就绪');
            return postResult();
        }
        if(!await waitFor(() => {
            const nodes = window.canvasState && window.canvasState.nodes;
            return Array.isArray(nodes) && nodes.length > 0;
        })){
            result.errors.push('画布没加载出节点，测不了保存');
            return postResult();
        }

        const projectId = new URLSearchParams(location.search).get('project') || '';
        if(!projectId){ result.errors.push('URL 里没有 project 参数'); return postResult(); }

        // 只盯 PATCH /api/project-library/<id>/canvas —— 票面点名的就是这一条。
        const patchCalls = [];
        let metaCalls = 0;   // #377 附带：项目没了也不该再每 8 秒去摸 /meta
        const realFetch = window.fetch;
        window.fetch = function(input, init){
            const url = typeof input === 'string' ? input : (input && input.url) || '';
            const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
            let slot = null;
            if(method === 'PATCH' && new RegExp(`/api/project-library/[^/]+/canvas$`).test(url)){
                slot = {status: 0};
                patchCalls.push(slot);
            }
            if(/\/api\/canvases\/[^/]+\/meta$/.test(url)) metaCalls += 1;
            const promise = realFetch.apply(window, arguments);
            if(!slot) return promise;
            return promise.then((response) => { slot.status = response.status; return response; });
        };

        // 真删项目：走生产路由（回收站 → 彻底删），删完 apply_mutations 就 ProjectNotFound。
        const metaCallsAtDelete = metaCalls;
        const trashResponse = await realFetch(`/api/project-library/${encodeURIComponent(projectId)}`, {method:'DELETE'});
        const purgeResponse = await realFetch(`/api/project-library/trash/${encodeURIComponent(projectId)}`, {method:'DELETE'});
        result.notes.trash_status = trashResponse.status;
        result.notes.purge_status = purgeResponse.status;
        if(!trashResponse.ok || !purgeResponse.ok){
            result.errors.push(`删除测试项目没成功：trash=${trashResponse.status} purge=${purgeResponse.status}`);
            return postResult();
        }

        // 改一个节点，走真实的防抖保存链路（DEBOUNCED_SAVE_MS = 3000）。
        const edited = window.canvasState.nodes[0];
        edited.text = `${edited.text || ''} #377-edited`;
        window.scheduleSave();

        if(!await waitFor(() => patchCalls.length >= 1 && patchCalls[0].status !== 0, 20000)){
            result.errors.push('删掉项目后一次 PATCH 都没发，测试链路没跑起来');
            return postResult();
        }
        const firstStatus = patchCalls[0].status;

        // 关键断言：给足时间（真实世界里它是每 20 分钟一次、连打好几天），PATCH 不能变多。
        // 6s 已超过 3s 防抖窗口两轮——旧缺陷在这里会再发一次。
        await sleep(6000);
        window.scheduleSave();
        if(typeof window.markContentDirty === 'function') window.markContentDirty();
        await sleep(6000);

        assertion('第一次保存必须撞到 404', firstStatus === 404, `status=${firstStatus}`);
        assertion('PATCH 只发一次（旧缺陷会按防抖/周期反复发）', patchCalls.length === 1,
                  `count=${patchCalls.length} statuses=${patchCalls.map(c => c.status).join(',')}`);
        assertion('页面必须认定项目已删除', typeof window.canvasProjectDeleted === 'function'
                  && window.canvasProjectDeleted() === true,
                  typeof window.canvasProjectDeleted === 'function'
                      ? String(window.canvasProjectDeleted()) : `没有 canvasProjectDeleted（typeof=${typeof window.canvasProjectDeleted}）`);

        const banner = document.getElementById('canvasGoneNotice');
        assertion('必须出现「项目已被删除」提示', Boolean(banner)
                  && /项目已被删除/.test(banner.textContent || ''),
                  banner ? banner.textContent.trim().slice(0, 80) : 'banner 不存在');

        // 手动保存也不能再发 PATCH（票面：不再发 PATCH）。
        const manual = await window.saveCanvas();
        assertion('手动保存不再发 PATCH 且返回 false', manual === false && patchCalls.length === 1,
                  `manual=${manual} count=${patchCalls.length}`);

        // 内容不能静默丢：导出按钮要能把当前画布（含刚才那次改动）写成文件。
        let exportedBlob = null;
        const realSaveBlob = window.canvasSaveBlobAsFile;
        window.canvasSaveBlobAsFile = (blob) => { exportedBlob = blob; return 'blob:stub'; };
        if(banner){
            const buttons = [...banner.querySelectorAll('button')];
            const exportButton = buttons.find(b => /导出/.test(b.textContent || ''));
            const backButton = buttons.find(b => /返回列表/.test(b.textContent || ''));
            assertion('提示里要有导出和返回两个出口', Boolean(exportButton) && Boolean(backButton),
                      `buttons=${buttons.map(b => (b.textContent || '').trim()).join('|')}`);
            if(exportButton) exportButton.click();
        } else {
            assertion('提示里要有导出和返回两个出口', false, 'banner 不存在');
        }
        window.canvasSaveBlobAsFile = realSaveBlob;
        let exportedText = '';
        if(exportedBlob){
            try { exportedText = await exportedBlob.text(); } catch(e){ result.errors.push(`读导出内容失败: ${e.message}`); }
        }
        assertion('导出内容包含未落库的本地改动（不静默丢内容）',
                  exportedText.includes('#377-edited'),
                  `exported_bytes=${exportedBlob ? exportedBlob.size : 0}`);

        // 提示不能自动消失（不然用户回头就没了）。
        await sleep(1500);
        assertion('提示常驻，不会自动消失', Boolean(document.getElementById('canvasGoneNotice')),
                  document.getElementById('canvasGoneNotice') ? '仍在' : '已消失');
        assertion('全程只发过一次 PATCH', patchCalls.length === 1, `count=${patchCalls.length}`);

        result.notes.patch_statuses = patchCalls.map(c => c.status);
        result.notes.meta_calls_after_delete = metaCalls - metaCallsAtDelete;
        assertion('meta 轮询也停掉（不再每 8 秒敲一个不存在的项目）', metaCalls - metaCallsAtDelete === 0,
                  `meta_after_delete=${metaCalls - metaCallsAtDelete}`);
        return postResult();
        return postResult();
    }

    main().catch((error) => {
        result.errors.push(`fatal: ${error && error.message}`);
        postResult();
    });
})();
