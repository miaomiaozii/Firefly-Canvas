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
        const historyRail = document.getElementById('smartLogList');
        const renderedHistoryCount = () => historyRail.querySelectorAll('.log-item').length;
        const representedHistoryCount = () => renderedHistoryCount() + [...historyRail.querySelectorAll('[data-history-spacer]')]
            .reduce((total, spacer) => total + Number(spacer.dataset.historyOmitted || 0), 0);
        assertion('history drawer keeps a 120-to-160-card DOM window for large project history', renderedHistoryCount() >= 120 && renderedHistoryCount() <= 160, renderedHistoryCount());
        assertion('history drawer has no manual pagination controls or status', !document.querySelector('.history-page-nav,[data-history-page-prev],[data-history-page-next],[data-history-page-status]'), document.querySelector('.history-page-nav,[data-history-page-prev],[data-history-page-next],[data-history-page-status]')?.outerHTML || 'none');
        assertion('history spacers preserve all 5000 logical records', Boolean(historyRail.querySelector('[data-history-spacer="before"]')) && Boolean(historyRail.querySelector('[data-history-spacer="after"]')) && representedHistoryCount() === 5000, {represented:representedHistoryCount(), spacers:[...historyRail.querySelectorAll('[data-history-spacer]')].map(spacer => Number(spacer.dataset.historyOmitted || 0))});
        assertion('history renders the newest record at the far left after loading', historyRail.querySelector('.log-item')?.dataset?.canvasLogId === 'history-first', historyRail.querySelector('.log-item')?.dataset?.canvasLogId || '');
        const initialCard = historyCard('history-first');
        const initialThumb = initialCard?.querySelector('.log-thumb-square');
        const initialCardRect = initialCard?.getBoundingClientRect();
        const initialThumbRect = initialThumb?.getBoundingClientRect();
        const thumbLeftInset = initialCardRect && initialThumbRect ? initialThumbRect.left - initialCardRect.left : -1;
        const thumbRightInset = initialCardRect && initialThumbRect ? initialCardRect.right - initialThumbRect.right : -1;
        assertion('history thumbnail is horizontally centered inside its card', Boolean(initialCardRect && initialThumbRect) && Math.abs(thumbLeftInset - thumbRightInset) <= 1, {left:thumbLeftInset, right:thumbRightInset});
        assertion('history thumbnail grows with the card width', Boolean(initialCardRect && initialThumbRect) && thumbLeftInset <= 7 && thumbRightInset <= 7, {cardWidth:initialCardRect?.width, thumbWidth:initialThumbRect?.width, left:thumbLeftInset, right:thumbRightInset});
        const friendlyModelChip = initialCard?.querySelector('.log-chip.model-chip');
        assertion('history shows the friendly model name and keeps the raw id as a tooltip', friendlyModelChip?.textContent.trim() === 'Nano Banana 2' && friendlyModelChip?.title === 'gemini-3.1-flash-image-preview', {text:friendlyModelChip?.textContent, title:friendlyModelChip?.title});
        assertion('history keeps the full friendly model name visible', Boolean(friendlyModelChip) && friendlyModelChip.scrollWidth <= friendlyModelChip.clientWidth + 1, friendlyModelChip ? {text:friendlyModelChip.textContent.trim(), scrollWidth:friendlyModelChip.scrollWidth, clientWidth:friendlyModelChip.clientWidth} : 'missing');
        const resolutionChips = initialCard ? [...initialCard.querySelectorAll('.log-chip.resolution-chip')] : [];
        assertion('history shows exactly one resolution tier that follows the request', resolutionChips.length === 1 && resolutionChips[0].textContent.trim() === '4K', resolutionChips.map(chip => chip.textContent.trim()));
        assertion('history model name uses adaptive space instead of a fixed cap', Boolean(friendlyModelChip) && getComputedStyle(friendlyModelChip).maxWidth === 'none' && getComputedStyle(friendlyModelChip).flexShrink === '1', friendlyModelChip ? {maxWidth:getComputedStyle(friendlyModelChip).maxWidth, flexShrink:getComputedStyle(friendlyModelChip).flexShrink, width:friendlyModelChip.getBoundingClientRect().width} : 'missing');
        const historyButtons = initialCard?.querySelector('.log-buttons');
        const deleteButton = historyButtons?.querySelector('[data-log-delete="media"]');
        const duration = historyButtons?.querySelector('.log-duration');
        const deleteRect = deleteButton?.getBoundingClientRect();
        const durationRect = duration?.getBoundingClientRect();
        const deleteIconRect = deleteButton?.querySelector('svg')?.getBoundingClientRect();
        const durationTextRange = document.createRange();
        if(duration) durationTextRange.selectNodeContents(duration);
        const durationTextRect = duration ? durationTextRange.getBoundingClientRect() : null;
        const visibleBottomDiff = deleteIconRect && durationTextRect
            ? Math.abs(deleteIconRect.bottom - durationTextRect.bottom)
            : Number.POSITIVE_INFINITY;
        assertion('history duration follows the delete action at the bottom', Boolean(deleteButton && duration) && duration.previousElementSibling === deleteButton && duration.textContent.trim() === '1:44s' && !initialCard?.querySelector('.log-meta .log-duration') && visibleBottomDiff <= 2, {buttons:historyButtons?.textContent?.trim(), duration:duration?.textContent?.trim(), deleteRect, deleteIconRect, durationRect, durationTextRect, visibleBottomDiff});
        assertion('history card is wide enough for readable metadata', Number(initialCardRect?.width || 0) >= 184, initialCardRect?.width || 0);
        const failedCard = document.querySelector('.log-item.failed');
        const failedChip = failedCard?.querySelector('.log-chip.status-failed');
        const failedCardRect = failedCard?.getBoundingClientRect();
        const failedChipRect = failedChip?.getBoundingClientRect();
        const successCard = document.querySelector('.log-item:not(.failed)');
        assertion('failed status chip stays intact inside the card', Boolean(failedCardRect && failedChipRect) && failedChipRect.left >= failedCardRect.left && failedChipRect.right <= failedCardRect.right && failedChip.scrollWidth <= failedChip.clientWidth + 1, {card:failedCardRect, chip:failedChipRect, scrollWidth:failedChip?.scrollWidth, clientWidth:failedChip?.clientWidth});
        assertion('failed card uses the same quiet surface as successful cards', Boolean(failedCard && successCard) && getComputedStyle(failedCard).backgroundColor === getComputedStyle(successCard).backgroundColor && getComputedStyle(failedCard).borderColor === getComputedStyle(successCard).borderColor, {failedBackground:getComputedStyle(failedCard).backgroundColor, successBackground:getComputedStyle(successCard).backgroundColor, failedBorder:getComputedStyle(failedCard).borderColor, successBorder:getComputedStyle(successCard).borderColor});

        const focusedHistoryControl = historyCard('history-first')?.querySelector('.log-history-image');
        await waitFor(() => focusedHistoryControl && getComputedStyle(focusedHistoryControl).visibility === 'visible', 600);
        focusedHistoryControl?.focus();
        assertion('history focus probe is established before virtual scrolling', document.activeElement === focusedHistoryControl, document.activeElement?.tagName || 'none');
        const endKey = new KeyboardEvent('keydown', {key:'End', bubbles:true, cancelable:true});
        focusedHistoryControl?.dispatchEvent(endKey);
        const reachedRemoteHistory = await waitFor(() => historyRail.scrollLeft >= historyRail.scrollWidth - historyRail.clientWidth - 2, 1800);
        assertion('End reaches the remote end of a 5000-record virtual history', reachedRemoteHistory && endKey.defaultPrevented, {reached:reachedRemoteHistory, scrollLeft:historyRail.scrollLeft, scrollWidth:historyRail.scrollWidth});
        assertion('remote history rendering remains DOM-bounded', renderedHistoryCount() <= 160 && representedHistoryCount() === 5000, {rendered:renderedHistoryCount(), represented:representedHistoryCount()});
        assertion('a focused history card is not unloaded by a remote virtual-window jump', document.activeElement === focusedHistoryControl && focusedHistoryControl?.isConnected && Boolean(historyCard('history-first')), {active:document.activeElement === focusedHistoryControl, connected:focusedHistoryControl?.isConnected});
        const blockedEndWheel = new WheelEvent('wheel', {deltaY:120, bubbles:true, cancelable:true});
        historyRail.dispatchEvent(blockedEndWheel);
        assertion('wheel is not prevented when the history rail cannot move farther', !blockedEndWheel.defaultPrevented, {defaultPrevented:blockedEndWheel.defaultPrevented, scrollLeft:historyRail.scrollLeft});

        const homeKey = new KeyboardEvent('keydown', {key:'Home', bubbles:true, cancelable:true});
        focusedHistoryControl?.dispatchEvent(homeKey);
        await waitFor(() => Boolean(historyCard('history-first')) && historyRail.scrollLeft < 1, 1800);
        assertion('Home returns the virtual history to its first records', homeKey.defaultPrevented && Boolean(historyCard('history-first')) && historyRail.scrollLeft < 1, historyRail.scrollLeft);
        const verticalWheel = new WheelEvent('wheel', {deltaY:120, bubbles:true, cancelable:true});
        historyRail.dispatchEvent(verticalWheel);
        assertion('vertical wheel delta moves the history rail horizontally', verticalWheel.defaultPrevented && historyRail.scrollLeft > 0, {defaultPrevented:verticalWheel.defaultPrevented, scrollLeft:historyRail.scrollLeft});
        const beforeHorizontalWheel = historyRail.scrollLeft;
        const horizontalWheel = new WheelEvent('wheel', {deltaX:120, bubbles:true, cancelable:true});
        historyRail.dispatchEvent(horizontalWheel);
        assertion('horizontal wheel delta remains native to the history rail', horizontalWheel.defaultPrevented && historyRail.scrollLeft > beforeHorizontalWheel, {before:beforeHorizontalWheel, after:historyRail.scrollLeft});
        const beforeArrow = historyRail.scrollLeft;
        const rightKey = new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true, cancelable:true});
        focusedHistoryControl?.dispatchEvent(rightKey);
        const afterRight = historyRail.scrollLeft;
        const leftKey = new KeyboardEvent('keydown', {key:'ArrowLeft', bubbles:true, cancelable:true});
        focusedHistoryControl?.dispatchEvent(leftKey);
        assertion('ArrowRight and ArrowLeft move the history rail by one card stride', rightKey.defaultPrevented && leftKey.defaultPrevented && afterRight > beforeArrow && historyRail.scrollLeft < afterRight, {before:beforeArrow, right:afterRight, left:historyRail.scrollLeft});
        const resetHomeKey = new KeyboardEvent('keydown', {key:'Home', bubbles:true, cancelable:true});
        focusedHistoryControl?.dispatchEvent(resetHomeKey);
        await waitFor(() => historyRail.scrollLeft < 1, 600);
        const drawer = document.getElementById('smartLogModal');
        const shell = document.getElementById('shell');
        const minimap = document.getElementById('minimap');
        const arrange = document.getElementById('smartArrangeBtn');
        const shellRect = shell.getBoundingClientRect();
        const drawerRect = drawer.getBoundingClientRect();
        const minimapRect = minimap.getBoundingClientRect();
        const arrangeRect = arrange.getBoundingClientRect();
        const defaultHeight = Math.min(324, shell.clientHeight - 32);
        assertion('drawer uses the fixed image-first height contract', Math.abs(drawerRect.height - defaultHeight) <= 1, {actual:drawerRect.height, expected:defaultHeight});
        assertion('drawer stays attached to the bottom rail of the canvas', Math.abs((shellRect.bottom - drawerRect.bottom) - 16) <= 1, {drawerBottom:drawerRect.bottom, shellBottom:shellRect.bottom, gutter:shellRect.bottom - drawerRect.bottom});
        const drawerSideGutter = shell.clientWidth <= 620 ? 12 : 16;
        assertion('drawer spans the bottom rail with symmetric safe gutters', Math.abs(drawerRect.left - drawerSideGutter) <= 1 && Math.abs((shellRect.right - drawerRect.right) - drawerSideGutter) <= 1, {left:drawerRect.left, rightGap:shellRect.right - drawerRect.right, expected:drawerSideGutter});
        assertion('top-right minimap stays above the history drawer', minimapRect.bottom + 8 <= drawerRect.top, {minimapBottom:minimapRect.bottom, drawerTop:drawerRect.top});
        assertion('top-right arrange control stays above the history drawer', arrangeRect.bottom + 8 <= drawerRect.top, {arrangeBottom:arrangeRect.bottom, drawerTop:drawerRect.top});

        const collapse = document.getElementById('smartHistoryCollapse');
        collapse.click();
        await sleep(60);
        assertion('drawer can collapse to its compact height', drawer.classList.contains('collapsed') && Math.abs(drawer.getBoundingClientRect().height - 46) <= 1, drawer.getBoundingClientRect().height);
        collapse.click();
        await sleep(60);
        assertion('drawer can expand again', !drawer.classList.contains('collapsed') && drawer.getBoundingClientRect().height > 46, drawer.getBoundingClientRect().height);
        assertion('fixed history drawer does not expose a resize handle', !drawer.querySelector('[data-history-resize-handle]'), drawer.querySelector('[data-history-resize-handle]')?.outerHTML || 'none');

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
        assertion('double click opens generation detail', document.getElementById('smartLogLightbox')?.classList.contains('open'), document.getElementById('smartLogLightbox')?.className || 'closed');
        assertion('generation detail exposes shared preview frame, zoom, metadata and sharing actions', ['.smart-log-detail-frame','.smart-log-detail-zoom-tools','.smart-log-detail-model','.smart-log-detail-time','.smart-log-detail-prompt','.smart-log-detail-download','.smart-log-detail-copy-2k'].every(selector => document.querySelector(selector)), document.querySelector('.smart-log-detail')?.innerText || 'missing');
        const detailStage = document.querySelector('.smart-log-detail-stage');
        const detailWheel = new WheelEvent('wheel', {deltaY:-120, clientX:firstPoint.x, clientY:firstPoint.y, bubbles:true, cancelable:true});
        detailStage?.dispatchEvent(detailWheel);
        await sleep(30);
        assertion('generation detail reuses wheel zoom preview behavior', detailWheel.defaultPrevented && Number(document.querySelector('.smart-log-detail-frame')?.dataset.zoom || 1) > 1, {defaultPrevented:detailWheel.defaultPrevented, zoom:document.querySelector('.smart-log-detail-frame')?.dataset.zoom});
        const detailEscape = new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true});
        document.dispatchEvent(detailEscape);
        await sleep(30);
        assertion('Escape closes only generation detail and keeps history open', detailEscape.defaultPrevented && !document.getElementById('smartLogLightbox')?.classList.contains('open') && drawer.classList.contains('open'), {defaultPrevented:detailEscape.defaultPrevented, detailOpen:document.getElementById('smartLogLightbox')?.classList.contains('open'), historyOpen:drawer.classList.contains('open')});
        const historyEscape = new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true});
        document.dispatchEvent(historyEscape);
        await sleep(30);
        assertion('Escape never closes the generation history drawer', drawer.classList.contains('open'), drawer.className);
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
        historyCard('history-first')?.querySelector('[data-log-send-all]')?.click();
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

        const requestCountsBeforeImportedDelete = await fetch('/__history_request_counts').then(response => response.json());
        historyCard('history-imported')?.querySelector('[data-log-delete="media"]')?.click();
        const importedRecordDeleted = await waitFor(() => !historyCard('history-imported'), 1800);
        const importedMediaStatus = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.importedAsset)}`).then(response => response.status).catch(() => 0);
        const requestCountsAfterImportedDelete = await fetch('/__history_request_counts').then(response => response.json());
        assertion('deleting an imported history record keeps user media', importedRecordDeleted && importedMediaStatus === 200 && Number(requestCountsAfterImportedDelete.history_delete_with_media || 0) === Number(requestCountsBeforeImportedDelete.history_delete_with_media || 0) + 1, {recordDeleted:importedRecordDeleted, mediaStatus:importedMediaStatus, requestCountsAfterImportedDelete});

        await sleep(800);
        const savedAfterReuse = await fetch(`/api/canvases/${encodeURIComponent(expected.projectId)}`).then(response => response.json()).catch(() => ({}));
        const persistedProjectImage = (savedAfterReuse.canvas?.nodes || []).flatMap(node => node.images || []).find(image => image.asset_id === expected.firstAsset || image.asset_id === expected.secondAsset);
        assertion('reused and dragged originals persist as project media IDs without URLs', Boolean(persistedProjectImage?.asset_id) && !persistedProjectImage.url, persistedProjectImage || null);
        const reusedHistoryNode = (savedAfterReuse.canvas?.nodes || []).find(node => node.historyLogId === 'history-first');
        assertion('send to canvas carries the original prompt and generation settings', reusedHistoryNode?.runPrompt === '第一条历史提示词' && reusedHistoryNode?.runSettings?.model === 'offline-browser-model' && reusedHistoryNode?.runSettings?.size === '1024x1024', reusedHistoryNode || null);
        const h2Card = historyCard('history-second');
        h2Card?.querySelector('[data-log-delete="media"]')?.click();
        await waitFor(() => !historyCard('history-second'), 1800);
        const removedSecondMediaStatus = expected.secondAsset ? (await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.secondAsset)}`)).status : 0;
        assertion('deleting a history record removes its generated media', !historyCard('history-second') && removedSecondMediaStatus === 404, {card:!!historyCard('history-second'), mediaStatus:removedSecondMediaStatus});

        const h1Card = historyCard('history-first');
        const countsBeforeGeneratedDelete = await fetch('/__history_request_counts').then(response => response.json());
        h1Card?.querySelector('[data-log-delete="media"]')?.click();
        const firstRecordDeleted = await waitFor(() => !historyCard('history-first'), 1800);
        const deletedMediaStatus = expected.firstAsset ? (await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`)).status : 0;
        const savedCanvas = await fetch(`/api/canvases/${encodeURIComponent(expected.projectId)}`).then(response => response.json()).catch(() => ({}));
        const generator = (savedCanvas.canvas?.nodes || []).find(node => node.id === 'generator');
        const deletedCounts = await fetch('/__history_request_counts').then(response => response.json());
        assertion('delete button removes the history record and all generated media in one action', firstRecordDeleted && deletedMediaStatus === 404 && Number(deletedCounts.history_delete_with_media || 0) === Number(countsBeforeGeneratedDelete.history_delete_with_media || 0) + 1, {recordDeleted:firstRecordDeleted, mediaStatus:deletedMediaStatus, counts:deletedCounts});
        assertion('combined deletion leaves the generator ready to regenerate', generator?.needsRegeneration === true && generator?.generationState === 'ready' && generator?.runPrompt === '第一条历史提示词', generator || null);
        assertion('combined deletion marks downstream references without losing output', Boolean(world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker')) && (savedCanvas.canvas?.nodes || []).find(node => node.id === 'downstream')?.images?.length === 1, world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker')?.textContent || 'missing');
        const purgeTrash = document.getElementById('smartHistoryPurgeTrash');
        await waitFor(() => purgeTrash && !purgeTrash.hidden, 1200);
        assertion('combined deletion exposes the project-trash control', purgeTrash?.hidden === false, purgeTrash?.hidden);
        purgeTrash?.click();
        assertion('emptying project trash exposes its in-flight state', purgeTrash?.getAttribute('aria-busy') === 'true' && purgeTrash?.disabled === true, {busy:purgeTrash?.getAttribute('aria-busy'), disabled:purgeTrash?.disabled});
        await waitFor(() => purgeTrash?.getAttribute('aria-busy') === 'false' && purgeTrash?.disabled === false, 1800);
        const purgedMediaStatus = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.firstAsset)}`).then(response => response.status).catch(() => 0);
        assertion('explicitly emptying project trash permanently removes the deleted original', purgedMediaStatus === 404 && purgeTrash?.hidden === true, {mediaStatus:purgedMediaStatus, trashHidden:purgeTrash?.hidden});
        if(!drawer.classList.contains('open')) toggle.click();
        await waitFor(() => historyCard('history-third')?.querySelector('[data-log-delete="media"]'));
        historyCard('history-third')?.querySelector('[data-log-delete="media"]')?.click();
        const thirdRecordDeleted = await waitFor(() => !historyCard('history-third'), 1800);
        const thirdMediaAfterDelete = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}/media/${encodeURIComponent(expected.thirdAsset)}`).then(response => response.status).catch(() => 0);
        assertion('a second generated record uses the same combined delete transaction', thirdRecordDeleted && thirdMediaAfterDelete === 404, {recordDeleted:thirdRecordDeleted, mediaStatus:thirdMediaAfterDelete});
        assertion('history is limited to the current project', ![...document.querySelectorAll('.log-item')].some(item => item.textContent.includes(expected.otherPrompt)), [...document.querySelectorAll('.log-item')].map(item => item.textContent.trim().slice(0, 80)));
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
