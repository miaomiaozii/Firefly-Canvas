(function(){
    'use strict';

    // #382：画布 /ws/stats 断线后，对已删除/不存在的项目要停止重连。
    //
    // 本测试环境的桥接层把 WS 升级请求当普通 GET 转发（不支持 websocket），
    // 所以这里每次 /ws/stats 尝试都会 404——浏览器报 close 1006。真实形态一致：
    // 项目被删后升级请求同样 404，1006 与网络断线无法区分。旧代码每 3 秒无条件
    // 重连，删除后无限刷 404；修复后：重连前先摸一次画布 meta，404 = 项目没了
    // → markCanvasProjectGone（复用 #377：停自动保存 + 导出/返回出口），停止重试。
    //
    // 本 client 在真实 headless-Chrome 里包 WebSocket 构造器数 /ws/stats 请求：
    //   阶段 A（项目存在）：重连持续发生（对照：项目存在时断线仍重连是期望行为）；
    //   真删项目（走 HTTP 路由：回收站 → 彻底删）；
    //   阶段 B：等超过 2 个重连周期（3s/次）→ 请求次数不再增长。

    const result = { assertions: [], errors: [], notes: {}, pass: false };
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass: Boolean(pass), actual});

    // 顶层包 WebSocket 构造器（脚本在 </body> 前同步执行，早于应用 window.onload 的首次连接）。
    const wsAttempts = [];
    const RealWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols){
        const target = String(url || '');
        if(/\/ws\/stats(\?|$)/.test(target)){
            wsAttempts.push({t: Date.now()});
        }
        return protocols === undefined ? new RealWebSocket(url) : new RealWebSocket(url, protocols);
    };
    window.WebSocket.prototype = RealWebSocket.prototype;
    window.WebSocket.CONNECTING = 0;
    window.WebSocket.OPEN = 1;
    window.WebSocket.CLOSING = 2;
    window.WebSocket.CLOSED = 3;

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
            result.errors.push('画布没加载出节点，测不了重连');
            return postResult();
        }

        const projectId = new URLSearchParams(location.search).get('project') || '';
        if(!projectId){ result.errors.push('URL 里没有 project 参数'); return postResult(); }

        // 阶段 A：项目存在 → /ws/stats 重连持续发生（对照：删了才该停，没删不能停）。
        const a0 = wsAttempts.length;
        await sleep(10000);
        const a1 = wsAttempts.length;
        assertion('阶段 A：项目存在时 /ws/stats 断线仍重连', a1 - a0 >= 2,
                  `10s 内新增 ${a1 - a0} 次（3s/次，至少 2 个周期）`);

        // 真删项目：走生产路由（回收站 → 彻底删），与 #377 同一链路。
        const realFetch = window.fetch;
        const trashResponse = await realFetch(`/api/project-library/${encodeURIComponent(projectId)}`, {method: 'DELETE'});
        const purgeResponse = await realFetch(`/api/project-library/trash/${encodeURIComponent(projectId)}`, {method: 'DELETE'});
        result.notes.trash_status = trashResponse.status;
        result.notes.purge_status = purgeResponse.status;
        if(!trashResponse.ok || !purgeResponse.ok){
            result.errors.push(`删除测试项目没成功：trash=${trashResponse.status} purge=${purgeResponse.status}`);
            return postResult();
        }

        // 阶段 B：等超过 2 个重连周期（3s/次 → 10s），请求次数必须停止增长。
        await sleep(10000);
        const b1 = wsAttempts.length;
        await sleep(4000);
        const b2 = wsAttempts.length;
        const afterDelete = b1 - a1;
        assertion('删除后 /ws/stats 只剩收尾的个别请求（不再持续刷）', afterDelete <= 2,
                  `删除到首个观测点共 ${afterDelete} 次`);
        assertion('超过 2 个重连周期后 /ws/stats 请求次数停止增长', b2 - b1 === 0,
                  `最后 4s 新增 ${b2 - b1} 次（删除后累计 ${b2 - a1} 次）`);
        assertion('页面认定项目已删除（canvasProjectDeleted=true）',
                  typeof window.canvasProjectDeleted === 'function' && window.canvasProjectDeleted() === true,
                  typeof window.canvasProjectDeleted === 'function'
                      ? String(window.canvasProjectDeleted()) : 'canvasProjectDeleted 不存在');
        const banner = document.getElementById('canvasGoneNotice');
        assertion('出现「项目已被删除」常驻提示（导出/返回出口）', Boolean(banner)
                  && /项目已被删除/.test(banner.textContent || ''),
                  banner ? banner.textContent.trim().slice(0, 80) : 'banner 不存在');

        result.notes.ws_attempts_total = b2;
        result.notes.ws_attempts_phase_a = a1 - a0;
        result.notes.ws_requests_after_delete = b2 - a1;
        return postResult();
    }

    main().catch((error) => {
        result.errors.push(`fatal: ${error && error.message}`);
        postResult();
    });
})();
