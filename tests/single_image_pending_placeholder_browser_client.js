// 浏览器回归测试客户端：单图 pending 节点的占位框必须落在节点 16px padding 之内。
//
// 背景（0b19d51 引入的回归）：.image-node.node-pending 新增了 16px padding（border-box），
// 批量分支占位容器已改为 width/height:100%，但单图分支仍是内联固定尺寸
// （width:${layout.width}px，= boxW = 节点外框宽），导致占位框贴住/越出外框、
// 与 pending 呼吸边框（.node-body::after inset:0）错位。
//
// 本脚本由测试服务器注入 smart-canvas.html，通过 URL 参数配置节点，
// 直接向 nodes 注入单图 pending 节点并调用 render()，然后测量占位框几何。
(function () {
    'use strict';

    const config = new URLSearchParams(location.search);
    const nodeId = config.get('pendingNodeId') || 'h1-single-pending';

    const result = {
        viewport: {w: window.innerWidth, h: window.innerHeight},
        assertions: [],
        errors: [],
        pass: false,
        nodeRect: null,
        placeholderRect: null,
        nodePaddingLeft: null
    };

    function assertion(name, ok, actual) {
        result.assertions.push({name: name, pass: !!ok, actual: actual === undefined ? null : actual});
    }

    async function waitFor(predicate, timeoutMs) {
        const timeout = timeoutMs || 12000;
        const start = Date.now();
        while (Date.now() - start < timeout) {
            try {
                if (predicate()) return true;
            } catch (err) { /* 页面尚未就绪，继续轮询 */ }
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        return false;
    }

    function rectOf(el) {
        const r = el.getBoundingClientRect();
        return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height};
    }

    async function run() {
        const ready = await waitFor(() =>
            typeof render === 'function' &&
            typeof nodes !== 'undefined' &&
            document.getElementById('world') !== null
        );
        result.diagnostics = {
            ready: ready,
            typeofRender: typeof render,
            typeofNodes: (typeof nodes === 'undefined') ? 'undefined' : typeof nodes,
            worldFound: document.getElementById('world') !== null,
            nodesLen: (typeof nodes !== 'undefined' && Array.isArray(nodes)) ? nodes.length : -1
        };
        assertion('app globals ready', ready);
        if (!ready) return finish();

        // 等待页面初始 loadCanvas 完成（canvas 从 null → 服务端画布对象，同块同步重赋 nodes 并渲染）；
        // 否则它的异步 nodes 替换会把注入的节点抹掉。
        const canvasLoaded = await waitFor(() => {
            try { return typeof canvas !== 'undefined' && canvas !== null; } catch (err) { return false; }
        });
        assertion('initial canvas load finished before injection', canvasLoaded);
        if (!canvasLoaded) return finish();

        // 单图 pending 节点：1 张并发、无已写回图片、固化 4:3 比例。
        // 与 runGeneration 提交时的节点字段保持一致（pending=1, _batchTotal=1, _generationAspect）。
        const node = {
            id: nodeId,
            type: 'smart-image',
            x: 300,
            y: 200,
            pending: 1,
            _batchTotal: 1,
            _generationAspect: {w: 4, h: 3},
            images: []
        };
        let renderError = null;
        try {
            nodes.push(node);
            render();
        } catch (err) {
            renderError = String((err && err.stack) || err);
            result.errors.push('render failed: ' + renderError);
        }
        result.diagnostics.renderError = renderError;
        result.diagnostics.nodesLenAfter = (typeof nodes !== 'undefined' && Array.isArray(nodes)) ? nodes.length : -1;

        const found = await waitFor(() =>
            document.querySelector(`.image-node[data-id="${nodeId}"] .loading-cell.single`) !== null
        );
        result.diagnostics.placeholderFound = found;
        result.diagnostics.imageNodeCount = document.querySelectorAll('.image-node').length;
        result.diagnostics.imageNodeIds = Array.from(document.querySelectorAll('.image-node')).map(el => el.getAttribute('data-id')).slice(0, 20);
        assertion('single pending placeholder rendered', found);
        if (!found) return finish();

        const nodeEl = document.querySelector(`.image-node[data-id="${nodeId}"]`);
        const placeholderEl = nodeEl.querySelector('.loading-cell.single');
        const nodeStyle = getComputedStyle(nodeEl);
        const padLeft = parseFloat(nodeStyle.paddingLeft) || 0;
        const padTop = parseFloat(nodeStyle.paddingTop) || 0;
        const padRight = parseFloat(nodeStyle.paddingRight) || 0;
        const padBottom = parseFloat(nodeStyle.paddingBottom) || 0;
        result.nodePaddingLeft = padLeft;

        assertion('node has node-pending class', nodeEl.classList.contains('node-pending'));
        // 场景前提：0b19d51 给独立 pending 节点加的 16px padding 必须生效，否则测试不成立
        assertion('node has 16px padding', padLeft === 16 && padTop === 16 && padRight === 16 && padBottom === 16,
            {left: padLeft, top: padTop, right: padRight, bottom: padBottom});

        const nr = rectOf(nodeEl);
        const pr = rectOf(placeholderEl);
        result.nodeRect = nr;
        result.placeholderRect = pr;

        const EPS = 1;
        // 设计意图：占位框必须落在节点 16px padding 之内（padding 即外边距），
        // 而不是贴住/越出节点外框。
        assertion('placeholder left inside node padding', pr.left >= nr.left + padLeft - EPS,
            {placeholder: pr.left, node: nr.left, pad: padLeft});
        assertion('placeholder top inside node padding', pr.top >= nr.top + padTop - EPS,
            {placeholder: pr.top, node: nr.top, pad: padTop});
        assertion('placeholder right inside node padding', pr.right <= nr.right - padRight + EPS,
            {placeholder: pr.right, node: nr.right, pad: padRight});
        assertion('placeholder bottom inside node padding', pr.bottom <= nr.bottom - padBottom + EPS,
            {placeholder: pr.bottom, node: nr.bottom, pad: padBottom});

        // ===== M2：批量生成节点尺寸被 imageLayout 锁定 → resize 手柄不应渲染 =====
        const singleCompletedId = 'm2-single-completed';
        const batchCompletedId = 'm2-batch-completed';
        const batchPendingId = 'm2-batch-pending';
        const makeImg = (i) => ({url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=', kind: 'image', name: 'img' + i + '.png'});
        nodes.push(
            {id: singleCompletedId, type: 'smart-image', x: 700, y: 200, images: [makeImg(0)], _batchTotal: 1, _generationAspect: {w: 4, h: 3}},
            {id: batchCompletedId, type: 'smart-image', x: 1000, y: 200, images: [0,1,2,3,4,5,6,7].map(makeImg), _batchTotal: 8, _generationAspect: {w: 4, h: 3}},
            {id: batchPendingId, type: 'smart-image', x: 1000, y: 560, pending: 1, _batchTotal: 8, _generationAspect: {w: 4, h: 3}, images: []}
        );
        render();
        const m2Ready = await waitFor(() =>
            document.querySelector(`.image-node[data-id="${batchCompletedId}"]`) !== null &&
            document.querySelector(`.image-node[data-id="${batchPendingId}"] .loading-skeleton`) !== null
        );
        assertion('m2/l4 scenario nodes rendered', m2Ready);
        if (m2Ready) {
            const hasHandle = (id) => document.querySelector(`.image-node[data-id="${id}"] .node-resize-handle`) !== null;
            assertion('batch completed node has no resize handle', !hasHandle(batchCompletedId));
            assertion('batch pending node has no resize handle', !hasHandle(batchPendingId));
            assertion('single pending node has no resize handle', !hasHandle(nodeId));
            assertion('single completed node keeps resize handle', hasHandle(singleCompletedId));

            // ===== L4：手动删图 → _batchTotal 随剩余图片下调，外框/网格自适应，不留空位 =====
            const batchNode = nodes.find(n => n.id === batchCompletedId);
            const beforeRect = rectOf(document.querySelector(`.image-node[data-id="${batchCompletedId}"]`));
            assertion('batch 8 completed outer frame 603x277', Math.round(beforeRect.width) === 603 && Math.round(beforeRect.height) === 277,
                {w: beforeRect.width, h: beforeRect.height});

            deleteImage(batchCompletedId, 0);
            const l4ok = await waitFor(() => (Number(batchNode?._batchTotal) || 0) === 7);
            assertion('L4 _batchTotal follows image count (8->7)', l4ok && batchNode._batchTotal === 7, batchNode._batchTotal);
            const emptySlots = document.querySelectorAll(`.image-node[data-id="${batchCompletedId}"] .thumb-empty-slot`).length;
            assertion('L4 no dashed empty slots after delete', emptySlots === 0, emptySlots);
            const afterRect = rectOf(document.querySelector(`.image-node[data-id="${batchCompletedId}"]`));
            // 7 张 → getBatchThumbGridSize(7) = 3列×3行：gridW=3*136+2*9=426, gridH=3*102+2*9=324；外框 +32/+64
            assertion('L4 outer frame adapts to 7-image grid (458x388)', Math.round(afterRect.width) === 458 && Math.round(afterRect.height) === 388,
                {before: {w: beforeRect.width, h: beforeRect.height}, after: {w: afterRect.width, h: afterRect.height}});

            // 删光 → 回到普通空节点（避免残留批量几何把空节点撑成网格）
            let guard = 0;
            while ((batchNode.images || []).length > 0 && guard < 20) { deleteImage(batchCompletedId, 0); guard++; }
            const l4empty = await waitFor(() => (batchNode.images || []).length === 0);
            assertion('L4 all deleted: _batchTotal=0', l4empty && (Number(batchNode._batchTotal) || 0) === 0, batchNode._batchTotal);
            assertion('L4 all deleted: _generationAspect removed', batchNode._generationAspect === undefined);
            const emptySlotsAll = document.querySelectorAll(`.image-node[data-id="${batchCompletedId}"] .thumb-empty-slot`).length;
            assertion('L4 all deleted: no empty slots', emptySlotsAll === 0, emptySlotsAll);
        }

        finish();
    }

    let finished = false;
    function finish() {
        if (finished) return;
        finished = true;
        result.pass = result.assertions.length > 0 && result.assertions.every(a => a.pass) && result.errors.length === 0;
        fetch('/__single_pending_placeholder_results', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(result)
        }).catch((e) => { result.errors.push('fetch-rejected: ' + String(e && e.stack || e)); });
    }

    run().catch(err => {
        result.errors.push(String(err && err.stack || err));
        finish();
    });
})();
