(function(){
    'use strict';

    const query = new URLSearchParams(location.search);
    const expected = {
        projectId: query.get('historyProject') || '',
        otherPrompt: query.get('otherPrompt') || '',
        firstAsset: query.get('firstAsset') || '',
        secondAsset: query.get('secondAsset') || '',
        thirdAsset: query.get('thirdAsset') || '',
        importedAsset: query.get('importedAsset') || ''
    };
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
    function pointer(target, type, x, y, id=1){
        target.dispatchEvent(new PointerEvent(type, {
            bubbles:true,
            cancelable:true,
            pointerId:id,
            pointerType:'mouse',
            isPrimary:true,
            button:type === 'pointerdown' ? 0 : -1,
            buttons:type === 'pointerup' ? 0 : 1,
            clientX:x,
            clientY:y
        }));
    }
    function center(element){
        const rect = element.getBoundingClientRect();
        return {x:rect.left + rect.width / 2, y:rect.top + rect.height / 2};
    }
    function historyCard(logId){
        return document.querySelector(`[data-canvas-log-id="${CSS.escape(logId)}"]`);
    }
    async function postResult(){
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__history_browser_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    function installClipboardProbe(){
        window.__historyCopiedText = '';
        const originalExecCommand = document.execCommand?.bind(document);
        document.execCommand = command => {
            if(command === 'copy'){
                window.__historyCopiedText = document.querySelector('[data-prompt]')?.dataset.prompt || '';
                return true;
            }
            return originalExecCommand ? originalExecCommand(command) : false;
        };
        try {
            Object.defineProperty(navigator, 'clipboard', {
                configurable:true,
                value:{
                    writeText:async value => { window.__historyCopiedText = String(value || ''); },
                    readText:async () => window.__historyCopiedText
                }
            });
        } catch(_) {}
        window.confirm = () => true;
    }
    async function run(){
        installClipboardProbe();
        const loaded = await waitFor(() => document.querySelector('#smartLogToggle') && document.title.includes('浏览器历史项目'), 15000);
        assertion('real product page loaded its canvas', loaded, document.querySelector('#smartTitle')?.textContent || '');
        const toggle = document.getElementById('smartLogToggle');
        toggle?.click();
        const opened = await waitFor(() => document.getElementById('smartLogModal')?.classList.contains('open') && document.querySelectorAll('.log-history-image').length >= 2);
        assertion('history drawer opens with current project records', opened, document.querySelectorAll('.log-history-image').length);
        if(!opened){ await postResult(); return; }
        assertion('history drawer keeps a bounded DOM for large project history', document.querySelectorAll('.log-item').length <= 100, document.querySelectorAll('.log-item').length);
        assertion('history drawer exposes page navigation for bounded history rendering', Boolean(document.querySelector('[data-history-page-next]')) && document.querySelector('[data-history-page-status]')?.textContent.includes('5000'), document.querySelector('[data-history-page-status]')?.textContent || 'missing');

        const drawer = document.getElementById('smartLogModal');
        const shell = document.getElementById('shell');
        const minimap = document.getElementById('minimap');
        const arrange = document.getElementById('smartArrangeBtn');
        const shellRect = shell.getBoundingClientRect();
        const drawerRect = drawer.getBoundingClientRect();
        const minimapRect = minimap.getBoundingClientRect();
        const arrangeRect = arrange.getBoundingClientRect();
        const maxHeight = Math.floor(shell.clientHeight / 3);
        const defaultHeight = Math.min(240, maxHeight);
        assertion('drawer uses the short-viewport default height contract', Math.abs(drawerRect.height - defaultHeight) <= 1, {actual:drawerRect.height, expected:defaultHeight});
        assertion('drawer stays attached to the bottom rail of the canvas', Math.abs((shellRect.bottom - drawerRect.bottom) - 16) <= 1, {drawerBottom:drawerRect.bottom, shellBottom:shellRect.bottom, gutter:shellRect.bottom - drawerRect.bottom});
        assertion('drawer reserves the minimap lane', drawerRect.right <= minimapRect.left + 1, {drawerRight:drawerRect.right, minimapLeft:minimapRect.left});
        assertion('drawer also stays clear of the arrange control', drawerRect.right <= arrangeRect.left + 1, {drawerRight:drawerRect.right, arrangeLeft:arrangeRect.left});

        const collapse = document.getElementById('smartHistoryCollapse');
        collapse.click();
        await sleep(60);
        assertion('drawer can collapse to its compact height', drawer.classList.contains('collapsed') && Math.abs(drawer.getBoundingClientRect().height - 46) <= 1, drawer.getBoundingClientRect().height);
        collapse.click();
        await sleep(60);
        assertion('drawer can expand again', !drawer.classList.contains('collapsed') && drawer.getBoundingClientRect().height > 46, drawer.getBoundingClientRect().height);
        const resizeHandle = drawer.querySelector('[data-history-resize-handle]');
        assertion('history resize handle exposes separator keyboard semantics', resizeHandle?.getAttribute('role') === 'separator' && resizeHandle?.tabIndex === 0 && resizeHandle?.getAttribute('aria-orientation') === 'horizontal', {role:resizeHandle?.getAttribute('role'), tabIndex:resizeHandle?.tabIndex, orientation:resizeHandle?.getAttribute('aria-orientation')});
        const resizePoint = center(resizeHandle);
        const beforeResize = drawer.getBoundingClientRect().height;
        pointer(resizeHandle, 'pointerdown', resizePoint.x, resizePoint.y, 71);
        pointer(resizeHandle, 'pointermove', resizePoint.x, resizePoint.y + 28, 71);
        pointer(resizeHandle, 'pointerup', resizePoint.x, resizePoint.y + 28, 71);
        await sleep(50);
        const afterResize = drawer.getBoundingClientRect().height;
        assertion('drawer resize stays within the 160px-to-viewport-third range', afterResize >= Math.min(160, maxHeight) - 1 && afterResize <= maxHeight + 1, {before:beforeResize, after:afterResize, max:maxHeight});
        resizeHandle?.focus();
        resizeHandle?.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowUp', bubbles:true, cancelable:true}));
        await sleep(40);
        assertion('history resize handle supports keyboard resizing', drawer.getBoundingClientRect().height >= afterResize, {before:afterResize, after:drawer.getBoundingClientRect().height});

        const firstImage = document.querySelector('.log-history-image');
        const firstCard = firstImage.closest('.log-media-card');
        const firstPoint = center(firstImage);
        assertion('history originals are keyboard-focusable buttons', firstImage.getAttribute('role') === 'button' && firstImage.tabIndex === 0 && Boolean(firstImage.getAttribute('aria-label')), {role:firstImage.getAttribute('role'), tabIndex:firstImage.tabIndex, label:firstImage.getAttribute('aria-label')});
        pointer(firstImage, 'pointerdown', firstPoint.x, firstPoint.y, 11);
        pointer(firstImage, 'pointerup', firstPoint.x, firstPoint.y, 11);
        await sleep(360);
        assertion('single click selects only after the double-click window', firstCard.classList.contains('is-selected') && !document.getElementById('smartLogLightbox')?.classList.contains('open'), firstCard.dataset.historySelected || 'none');

        pointer(firstImage, 'pointerdown', firstPoint.x, firstPoint.y, 12);
        pointer(firstImage, 'pointerup', firstPoint.x, firstPoint.y, 12);
        await sleep(60);
        pointer(firstImage, 'pointerdown', firstPoint.x + 4, firstPoint.y + 3, 13);
        pointer(firstImage, 'pointerup', firstPoint.x + 4, firstPoint.y + 3, 13);
        await sleep(60);
        assertion('double click opens the lightweight preview', document.getElementById('smartLogLightbox')?.classList.contains('open'), document.getElementById('smartLogLightbox')?.className || 'closed');
        document.getElementById('smartLogLightbox')?.click();
        await sleep(30);
        firstImage.focus();
        firstImage.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}));
        await sleep(30);
        assertion('keyboard Enter selects a history original', firstCard.classList.contains('is-selected') && firstImage.getAttribute('aria-pressed') === 'true', {selected:firstCard.classList.contains('is-selected'), pressed:firstImage.getAttribute('aria-pressed')});
        firstImage.dispatchEvent(new KeyboardEvent('keydown', {key:' ', bubbles:true, cancelable:true}));
        await sleep(30);
        assertion('keyboard Space previews a history original', document.getElementById('smartLogLightbox')?.classList.contains('open'), document.getElementById('smartLogLightbox')?.className || 'closed');
        document.getElementById('smartLogLightbox')?.click();
        await sleep(30);

        const copyButton = historyCard('history-first')?.querySelector('[data-log-copy-prompt]');
        copyButton?.click();
        await sleep(100);
        assertion('copy prompt exposes the recorded prompt to the clipboard boundary', window.__historyCopiedText === '第一条历史提示词', window.__historyCopiedText);

        const world = document.getElementById('world');
        const initialNodes = world.querySelectorAll('.image-node').length;
        historyCard('history-first')?.querySelector('[data-log-send-output]')?.click();
        await waitFor(() => world.querySelectorAll('.image-node').length === initialNodes + 1, 1500);
        assertion('reuse action creates one image node', world.querySelectorAll('.image-node').length === initialNodes + 1, world.querySelectorAll('.image-node').length);
        toggle.click();
        await waitFor(() => drawer.classList.contains('open') && document.querySelectorAll('.log-history-image').length >= 2);

        const secondImage = document.querySelectorAll('.log-history-image')[1];
        const secondCard = secondImage.closest('.log-media-card');
        const secondPoint = center(secondImage);
        const beforePointerDrag = world.querySelectorAll('.image-node').length;
        pointer(secondImage, 'pointerdown', secondPoint.x, secondPoint.y, 21);
        pointer(secondImage, 'pointermove', secondPoint.x + 8, secondPoint.y, 21);
        pointer(secondImage, 'pointerup', 96, 116, 21);
        await waitFor(() => world.querySelectorAll('.image-node').length === beforePointerDrag + 1, 1500);
        assertion('8px PointerEvent drag creates exactly one node', world.querySelectorAll('.image-node').length === beforePointerDrag + 1, world.querySelectorAll('.image-node').length - beforePointerDrag);
        assertion('pointer drag does not select the dragged image', !secondCard.classList.contains('is-selected'), secondCard.dataset.historySelected || 'not-selected');

        toggle.click();
        await waitFor(() => drawer.classList.contains('open') && document.querySelector('.log-history-image'));
        const guardedImage = document.querySelector('.log-history-image');
        const guardedPoint = center(guardedImage);
        const fixedControlPoint = center(document.querySelector('.smart-back'));
        const beforeRejectedPointerDrop = world.querySelectorAll('.image-node').length;
        pointer(guardedImage, 'pointerdown', guardedPoint.x, guardedPoint.y, 31);
        pointer(guardedImage, 'pointermove', guardedPoint.x + 8, guardedPoint.y, 31);
        pointer(guardedImage, 'pointerup', fixedControlPoint.x, fixedControlPoint.y, 31);
        await sleep(120);
        assertion('pointer release over fixed navigation does not create a canvas node', world.querySelectorAll('.image-node').length === beforeRejectedPointerDrop, world.querySelectorAll('.image-node').length - beforeRejectedPointerDrop);

        if(!drawer.classList.contains('open')) toggle.click();
        await waitFor(() => drawer.classList.contains('open') && document.querySelectorAll('.log-history-image').length >= 2);
        const nativeImage = document.querySelector('.log-history-image');
        const nativePoint = center(nativeImage);
        const beforeNativeDrag = world.querySelectorAll('.image-node').length;
        const transfer = new DataTransfer();
        nativeImage.dispatchEvent(new DragEvent('dragstart', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:nativePoint.x, clientY:nativePoint.y}));
        shell.dispatchEvent(new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:118, clientY:132}));
        shell.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:118, clientY:132}));
        nativeImage.dispatchEvent(new DragEvent('dragend', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:118, clientY:132}));
        await waitFor(() => world.querySelectorAll('.image-node').length === beforeNativeDrag + 1, 1500);
        assertion('DragEvent image drop creates exactly one node', world.querySelectorAll('.image-node').length === beforeNativeDrag + 1, world.querySelectorAll('.image-node').length - beforeNativeDrag);
        assertion('only the image element is draggable', nativeImage.draggable === true && nativeImage.closest('.log-media-card').draggable === false, {image:nativeImage.draggable, card:nativeImage.closest('.log-media-card').draggable});

        const restoreCountBeforeRejectedDelete = await fetch('/__history_request_counts').then(response => response.json()).then(data => Number(data.original_restore || 0));
        const beforeRejectedDeleteUndo = world.querySelectorAll('.image-node').length;
        historyCard('history-imported')?.querySelector('[data-log-delete-original]')?.click();
        await sleep(250);
        window.dispatchEvent(new KeyboardEvent('keydown', {key:'z', ctrlKey:true, bubbles:true, cancelable:true}));
        await sleep(450);
        const restoreCountAfterRejectedDelete = await fetch('/__history_request_counts').then(response => response.json()).then(data => Number(data.original_restore || 0));
        const importedMediaStatus = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.importedAsset)}`).then(response => response.status).catch(() => 0);
        assertion('a rejected original delete does not push a project restore token', importedMediaStatus === 200 && restoreCountAfterRejectedDelete === restoreCountBeforeRejectedDelete && world.querySelectorAll('.image-node').length < beforeRejectedDeleteUndo, {mediaStatus:importedMediaStatus, restoresBefore:restoreCountBeforeRejectedDelete, restoresAfter:restoreCountAfterRejectedDelete, nodesBefore:beforeRejectedDeleteUndo, nodesAfter:world.querySelectorAll('.image-node').length});

        await sleep(800);
        const savedAfterReuse = await fetch(`/api/canvases/${encodeURIComponent(expected.projectId)}`).then(response => response.json()).catch(() => ({}));
        const persistedProjectImage = (savedAfterReuse.canvas?.nodes || []).flatMap(node => node.images || []).find(image => image.asset_id === expected.firstAsset || image.asset_id === expected.secondAsset);
        assertion('reused and dragged originals persist as project media IDs without URLs', Boolean(persistedProjectImage?.asset_id) && !persistedProjectImage.url, persistedProjectImage || null);
        const h2Card = historyCard('history-second');
        h2Card?.querySelector('[data-log-delete="record"]')?.click();
        await waitFor(() => !historyCard('history-second'), 1800);
        const keptMediaStatus = expected.secondAsset ? (await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.secondAsset)}`)).status : 0;
        assertion('deleting a history record leaves its project media', !historyCard('history-second') && keptMediaStatus === 200, {card:!!historyCard('history-second'), mediaStatus:keptMediaStatus});

        const h1Card = historyCard('history-first');
        h1Card?.querySelector('[data-log-delete-original]')?.click();
        await waitFor(() => historyCard('history-first')?.querySelector('.log-deleted-media'), 1800);
        const deletedMediaStatus = expected.firstAsset ? (await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`)).status : 0;
        const savedCanvas = await fetch(`/api/canvases/${encodeURIComponent(expected.projectId)}`).then(response => response.json()).catch(() => ({}));
        const generator = (savedCanvas.canvas?.nodes || []).find(node => node.id === 'generator');
        assertion('deleting an original keeps the history card and removes the media', Boolean(historyCard('history-first')?.querySelector('.log-deleted-media')) && deletedMediaStatus === 404, {card:!!historyCard('history-first'), mediaStatus:deletedMediaStatus});
        assertion('original deletion leaves the generator ready to regenerate', generator?.needsRegeneration === true && generator?.generationState === 'ready' && generator?.runPrompt === '第一条历史提示词', generator || null);
        assertion('downstream node visibly marks its removed upstream reference without losing output', Boolean(world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker')) && (savedCanvas.canvas?.nodes || []).find(node => node.id === 'downstream')?.images?.length === 1, world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker')?.textContent || 'missing');
        await waitFor(() => document.getElementById('smartHistoryPurgeTrash') && !document.getElementById('smartHistoryPurgeTrash').hidden, 1200);
        assertion('deleted originals expose an explicit project-trash control', document.getElementById('smartHistoryPurgeTrash')?.hidden === false, document.getElementById('smartHistoryPurgeTrash')?.hidden);
        window.dispatchEvent(new KeyboardEvent('keydown', {key:'z', ctrlKey:true, bubbles:true, cancelable:true}));
        await sleep(500);
        const restoredMediaStatus = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`).then(response => response.status).catch(() => 0);
        const restoredCanvas = await fetch(`/api/canvases/${encodeURIComponent(expected.projectId)}`).then(response => response.json()).catch(() => ({}));
        const restoredGenerator = (restoredCanvas.canvas?.nodes || []).find(node => node.id === 'generator');
        assertion('project undo restores the original while it is in trash', restoredMediaStatus === 200 && restoredGenerator?.images?.some(image => image.asset_id === expected.firstAsset) && restoredCanvas.canvas?.logs?.some(log => log.id === 'history-first' && log.outputs?.some(output => output.asset_id === expected.firstAsset)), {mediaStatus:restoredMediaStatus, generator:restoredGenerator || null});
        assertion('restoring the original clears the downstream deletion marker', !world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker'), world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker')?.textContent || 'cleared');
        const restoredHistoryReady = await waitFor(() => document.getElementById('smartHistoryPurgeTrash')?.hidden === true
            && historyCard('history-first')?.querySelector('[data-log-delete-original]')
            && document.getElementById('projectSaveState')?.dataset.state === 'saved');
        assertion('restoring an original reaches a saved UI state before its delete action is reused', restoredHistoryReady, {trashHidden:document.getElementById('smartHistoryPurgeTrash')?.hidden, deleteAction:!!historyCard('history-first')?.querySelector('[data-log-delete-original]'), saveState:document.getElementById('projectSaveState')?.dataset.state || ''});
        const deleteCountBeforeSecondDelete = await fetch('/__history_request_counts').then(response => response.json()).then(data => Number(data.original_delete || 0));
        historyCard('history-first').querySelector('[data-log-delete-original]').click();
        const purgeTrash = document.getElementById('smartHistoryPurgeTrash');
        let mediaStatusBeforePurge = 0;
        const secondDeleteSettled = await waitFor(async () => {
            mediaStatusBeforePurge = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`).then(response => response.status).catch(() => 0);
            return mediaStatusBeforePurge === 404 && purgeTrash?.hidden === false;
        });
        const deleteCountAfterSecondDelete = await fetch('/__history_request_counts').then(response => response.json()).then(data => Number(data.original_delete || 0));
        assertion('the second original deletion settles before project trash is emptied', secondDeleteSettled, {mediaStatus:mediaStatusBeforePurge, trashHidden:purgeTrash?.hidden, deletesBefore:deleteCountBeforeSecondDelete, deletesAfter:deleteCountAfterSecondDelete, toast:document.getElementById('toast')?.textContent || ''});
        purgeTrash?.click();
        assertion('emptying project trash exposes its in-flight state', purgeTrash?.getAttribute('aria-busy') === 'true' && purgeTrash?.disabled === true, {busy:purgeTrash?.getAttribute('aria-busy'), disabled:purgeTrash?.disabled});
        await waitFor(() => purgeTrash?.getAttribute('aria-busy') === 'false' && purgeTrash?.disabled === false, 1800);
        const purgedMediaStatus = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`).then(response => response.status).catch(() => 0);
        assertion('explicitly emptying project trash permanently removes the deleted original', purgedMediaStatus === 404 && purgeTrash?.hidden === true, {mediaStatus:purgedMediaStatus, trashHidden:purgeTrash?.hidden});
        const beforeUndoAfterPurge = world.querySelectorAll('.image-node').length;
        window.dispatchEvent(new KeyboardEvent('keydown', {key:'z', ctrlKey:true, bubbles:true, cancelable:true}));
        await sleep(500);
        const mediaAfterPurgedUndo = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`).then(response => response.status).catch(() => 0);
        assertion('purging trash invalidates only its delete token and leaves older canvas undo usable', mediaAfterPurgedUndo === 404 && world.querySelectorAll('.image-node').length < beforeUndoAfterPurge, {mediaStatus:mediaAfterPurgedUndo, before:beforeUndoAfterPurge, after:world.querySelectorAll('.image-node').length});
        if(!drawer.classList.contains('open')) toggle.click();
        await waitFor(() => historyCard('history-third')?.querySelector('[data-log-delete-original]'));
        historyCard('history-third')?.querySelector('[data-log-delete-original]')?.click();
        await waitFor(() => historyCard('history-third')?.querySelector('.log-deleted-media'), 1800);
        await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/trash/media`, {method:'DELETE'});
        const beforePermanentUndo = world.querySelectorAll('.image-node').length;
        window.dispatchEvent(new KeyboardEvent('keydown', {key:'z', ctrlKey:true, bubbles:true, cancelable:true}));
        await sleep(500);
        const thirdMediaAfterUndo = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.thirdAsset)}`).then(response => response.status).catch(() => 0);
        assertion('a permanently unavailable restore token is discarded and does not block older undo', thirdMediaAfterUndo === 404 && world.querySelectorAll('.image-node').length < beforePermanentUndo, {mediaStatus:thirdMediaAfterUndo, before:beforePermanentUndo, after:world.querySelectorAll('.image-node').length});
        assertion('history is limited to the current project', ![...document.querySelectorAll('.log-item')].some(item => item.textContent.includes(expected.otherPrompt)), [...document.querySelectorAll('.log-item')].map(item => item.textContent.trim().slice(0, 80)));
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
