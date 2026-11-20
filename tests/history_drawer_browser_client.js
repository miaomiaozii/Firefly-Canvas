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
    const historySelector = historyId => `[data-canvas-history-id="${CSS.escape(historyId)}"]`;
    const historyCard = historyId => document.querySelector(historySelector(historyId));
    const deleteSelector = historyId => `${historySelector(historyId)} [data-history-delete="media"]`;
    const dangerConfirm = '.ff-dialog-card[role="alertdialog"][aria-modal="true"] .ff-dialog-btn-danger';
    const dialogCancel = '.ff-dialog-card[role="alertdialog"] .ff-dialog-actions button:not(.ff-dialog-btn-danger)';
    const projectApi = `/api/project-library/${encodeURIComponent(expected.projectId)}`;
    const canvasApi = `/api/canvases/${encodeURIComponent(expected.projectId)}`;
    let inputId = 0;
    async function nativeClick(selector, screenshot){
        // The Python driver reads this request and supplies real Chrome mouse input.
        let clicked = false;
        const observeClick = event => { if(event.isTrusted && event.target.closest(selector)) clicked = true; };
        document.addEventListener('click', observeClick, true);
        window.__historyInput = {id:++inputId, selector, screenshot};
        try {
            if(!(await waitFor(() => clicked, 8000))) throw new Error(`trusted click not received: ${selector}`);
        } finally {
            document.removeEventListener('click', observeClick, true);
            window.__historyInput = null;
        }
    }
    async function readJson(url){
        const response = await fetch(url);
        if(!response.ok) throw new Error(`${url} returned ${response.status}`);
        return response.json();
    }
    const counts = () => readJson('/__history_request_counts');
    const trashItems = () => readJson(`${projectApi}/trash/media`).then(data => data.items);
    const mediaStatus = assetId => fetch(`${projectApi}/media/${encodeURIComponent(assetId)}`).then(response => response.status);
    async function runConfirmedAction(selector, screenshot, {cancel=false, requestKey='history_delete_with_media'}={}){
        const before = (await counts())[requestKey];
        await nativeClick(selector);
        if(!(await waitFor(() => document.querySelector(dangerConfirm), 3000))) throw new Error(`no app confirmation: ${selector}`);
        const dialog = document.querySelector(dangerConfirm).closest('[role="alertdialog"]');
        const message = dialog.querySelector('.ff-dialog-message')?.textContent.trim();
        const beforeAnswer = (await counts())[requestKey];
        assertion('destructive action asks for app confirmation before any request', Boolean(message) && beforeAnswer === before, {selector, message, before, beforeAnswer});
        await nativeClick(cancel ? dialogCancel : dangerConfirm, screenshot);
        if(!(await waitFor(() => !dialog.isConnected, 3000))) throw new Error(`app confirmation did not close: ${selector}`);
        return before;
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
    }
    async function run(){
        installClipboardProbe();
        const loaded = await waitFor(() => document.querySelector('#smartHistoryToggle') && document.title.includes('浏览器历史项目'), 15000);
        assertion('real product page loaded its canvas', loaded, document.querySelector('#smartTitle')?.textContent || '');
        const toggle = document.getElementById('smartHistoryToggle');
        toggle?.click();
        const opened = await waitFor(() => document.getElementById('smartHistoryModal')?.classList.contains('open') && document.querySelectorAll('.log-history-image').length >= 2);
        assertion('history drawer opens with current project records', opened, document.querySelectorAll('.log-history-image').length);
        if(!opened){ await postResult(); return; }
        const historyRail = document.getElementById('smartHistoryList');
        const renderedHistoryCount = () => historyRail.querySelectorAll('.log-item').length;
        const representedHistoryCount = () => renderedHistoryCount() + [...historyRail.querySelectorAll('[data-history-spacer]')]
            .reduce((total, spacer) => total + Number(spacer.dataset.historyOmitted || 0), 0);
        assertion('history drawer keeps a 120-to-160-card DOM window for large project history', renderedHistoryCount() >= 120 && renderedHistoryCount() <= 160, renderedHistoryCount());
        assertion('history drawer has no manual pagination controls or status', !document.querySelector('.history-page-nav,[data-history-page-prev],[data-history-page-next],[data-history-page-status]'), document.querySelector('.history-page-nav,[data-history-page-prev],[data-history-page-next],[data-history-page-status]')?.outerHTML || 'none');
        assertion('history spacers preserve all 5000 logical records', Boolean(historyRail.querySelector('[data-history-spacer="before"]')) && Boolean(historyRail.querySelector('[data-history-spacer="after"]')) && representedHistoryCount() === 5000, {represented:representedHistoryCount(), spacers:[...historyRail.querySelectorAll('[data-history-spacer]')].map(spacer => Number(spacer.dataset.historyOmitted || 0))});
        assertion('history renders the newest record at the far left after loading', historyRail.querySelector('.log-item')?.dataset?.canvasHistoryId === 'history-first', historyRail.querySelector('.log-item')?.dataset?.canvasHistoryId || '');
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
        const deleteButton = historyButtons?.querySelector('[data-history-delete="media"]');
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
        const drawer = document.getElementById('smartHistoryModal');
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
        assertion('single click selects only after the double-click window', firstCard.classList.contains('is-selected') && !document.getElementById('smartHistoryLightbox')?.classList.contains('open'), firstCard.dataset.historySelected || 'none');

        pointer(firstImage, 'pointerdown', firstPoint.x, firstPoint.y, 12);
        pointer(firstImage, 'pointerup', firstPoint.x, firstPoint.y, 12);
        await sleep(60);
        pointer(firstImage, 'pointerdown', firstPoint.x + 4, firstPoint.y + 3, 13);
        pointer(firstImage, 'pointerup', firstPoint.x + 4, firstPoint.y + 3, 13);
        await sleep(60);
        assertion('double click opens generation detail', document.getElementById('smartHistoryLightbox')?.classList.contains('open'), document.getElementById('smartHistoryLightbox')?.className || 'closed');
        assertion('generation detail exposes shared preview frame, zoom, metadata and sharing actions', ['.smart-log-detail-frame','.smart-log-detail-zoom-tools','.smart-log-detail-model','.smart-log-detail-time','.smart-log-detail-prompt','.smart-log-detail-download','.smart-log-detail-copy-2k'].every(selector => document.querySelector(selector)), document.querySelector('.smart-log-detail')?.innerText || 'missing');
        const detailStage = document.querySelector('.smart-log-detail-stage');
        const detailWheel = new WheelEvent('wheel', {deltaY:-120, clientX:firstPoint.x, clientY:firstPoint.y, bubbles:true, cancelable:true});
        detailStage?.dispatchEvent(detailWheel);
        await sleep(30);
        assertion('generation detail reuses wheel zoom preview behavior', detailWheel.defaultPrevented && Number(document.querySelector('.smart-log-detail-frame')?.dataset.zoom || 1) > 1, {defaultPrevented:detailWheel.defaultPrevented, zoom:document.querySelector('.smart-log-detail-frame')?.dataset.zoom});
        const detailEscape = new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true});
        document.dispatchEvent(detailEscape);
        await sleep(30);
        assertion('Escape closes only generation detail and keeps history open', detailEscape.defaultPrevented && !document.getElementById('smartHistoryLightbox')?.classList.contains('open') && drawer.classList.contains('open'), {defaultPrevented:detailEscape.defaultPrevented, detailOpen:document.getElementById('smartHistoryLightbox')?.classList.contains('open'), historyOpen:drawer.classList.contains('open')});
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
        assertion('keyboard Space previews a history original', document.getElementById('smartHistoryLightbox')?.classList.contains('open'), document.getElementById('smartHistoryLightbox')?.className || 'closed');
        document.getElementById('smartHistoryLightbox')?.click();
        await sleep(30);

        const copyButton = historyCard('history-first')?.querySelector('[data-history-copy-prompt]');
        copyButton?.click();
        await sleep(100);
        assertion('copy prompt exposes the recorded prompt to the clipboard boundary', window.__historyCopiedText === '第一条历史提示词', window.__historyCopiedText);

        const world = document.getElementById('world');
        const nodeIds = () => [...world.querySelectorAll('.image-node[data-id]')].map(node => node.dataset.id);
        const newNodeIds = before => nodeIds().filter(id => !before.includes(id));
        const initialNodes = nodeIds();
        const initialRevision = (await readJson(canvasApi)).canvas.revision;
        await nativeClick(`${historySelector('history-first')} [data-history-send-all]`, 'send-to-canvas');
        await waitFor(() => nodeIds().length === initialNodes.length + 1, 1500);
        const [sentNodeId] = newNodeIds(initialNodes);
        assertion('reuse action creates one image node', nodeIds().length === initialNodes.length + 1 && Boolean(sentNodeId), {before:initialNodes, after:nodeIds()});
        const toastText = () => document.querySelector('#toast.show')?.textContent.trim() || '';
        assertion('configured history model only shows the existing send confirmation', toastText() === '已发送 1 张原图到画布', toastText());

        toggle.click();
        await waitFor(() => drawer.classList.contains('open') && historyCard('history-second')?.querySelector('.log-history-image'));
        const beforeFallback = nodeIds();
        await nativeClick(`${historySelector('history-second')} [data-history-send-all]`);
        const fallbackNodes = newNodeIds(beforeFallback);
        const [fallbackNodeId] = fallbackNodes;
        assertion('sending a history record with a removed model creates exactly one node', fallbackNodes.length === 1, fallbackNodes);
        const fallbackNotice = '历史模型「removed-history-model」已不可用，已切换为默认模型「gemini-3.1-flash-image-preview」。';
        const fallbackShown = await waitFor(() => toastText() === fallbackNotice, 1000);
        assertion('removed history model shows both the old and replacement model', fallbackShown, toastText());
        const reusedRunButton = document.getElementById('runBtn');
        assertion('fallback node is immediately runnable without refreshing its composer', reusedRunButton?.disabled === false && !reusedRunButton.title.includes('removed-history-model'), {disabled:reusedRunButton?.disabled, title:reusedRunButton?.title});
        if(fallbackShown && query.get('captureEvidence') === '1'){
            await waitFor(() => getComputedStyle(document.getElementById('toast')).opacity === '1', 500);
            window.__historyScreenshot = 'history-model-fallback';
            if(!(await waitFor(() => window.__historyScreenshot === null, 2500))) throw new Error('fallback screenshot not captured');
        }

        toggle.click();
        await waitFor(() => drawer.classList.contains('open') && historyCard('history-second')?.querySelector('.log-history-image'));
        const secondImage = historyCard('history-second').querySelector('.log-history-image');
        const secondCard = secondImage.closest('.log-media-card');
        const secondPoint = center(secondImage);
        const beforePointerDrag = nodeIds();
        pointer(secondImage, 'pointerdown', secondPoint.x, secondPoint.y, 21);
        pointer(secondImage, 'pointermove', secondPoint.x + 8, secondPoint.y, 21);
        pointer(secondImage, 'pointerup', 96, 116, 21);
        await waitFor(() => nodeIds().length === beforePointerDrag.length + 1, 1500);
        const [pointerNodeId] = newNodeIds(beforePointerDrag);
        assertion('8px PointerEvent drag creates exactly one node', nodeIds().length === beforePointerDrag.length + 1 && Boolean(pointerNodeId), nodeIds().length - beforePointerDrag.length);
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
        const nativeImage = historyCard('history-first').querySelector('.log-history-image');
        const nativePoint = center(nativeImage);
        const beforeNativeDrag = nodeIds();
        const transfer = new DataTransfer();
        nativeImage.dispatchEvent(new DragEvent('dragstart', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:nativePoint.x, clientY:nativePoint.y}));
        shell.dispatchEvent(new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:118, clientY:132}));
        shell.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:118, clientY:132}));
        nativeImage.dispatchEvent(new DragEvent('dragend', {bubbles:true, cancelable:true, dataTransfer:transfer, clientX:118, clientY:132}));
        await waitFor(() => nodeIds().length === beforeNativeDrag.length + 1, 1500);
        const [dragNodeId] = newNodeIds(beforeNativeDrag);
        assertion('DragEvent image drop creates exactly one node', nodeIds().length === beforeNativeDrag.length + 1 && Boolean(dragNodeId), nodeIds().length - beforeNativeDrag.length);
        assertion('only the image element is draggable', nativeImage.draggable === true && nativeImage.closest('.log-media-card').draggable === false, {image:nativeImage.draggable, card:nativeImage.closest('.log-media-card').draggable});

        // The product autosaves after a 3-second quiet period. Check durability
        // before deletion (which can itself flush a pending save or reset these nodes).
        const saveState = document.querySelector('#projectSaveState');
        const saveIsReady = await waitFor(async () => {
            if(saveState?.dataset.state !== 'saved') return false;
            const status = await readJson(`${projectApi}/save-status`);
            return status.saved === true && status.saved_revision > initialRevision;
        });
        const savedAfterReuse = (await readJson(canvasApi)).canvas;
        const reusedNodes = [sentNodeId, pointerNodeId, dragNodeId].map(id => savedAfterReuse.nodes.find(node => node.id === id));
        const reusedImages = reusedNodes.map(node => node?.images?.[0]);
        const expectedAssets = [expected.firstAsset, expected.secondAsset, expected.firstAsset];
        assertion('reused and dragged originals persist as project media IDs without URLs', saveIsReady && new Set(reusedNodes.filter(Boolean).map(node => node.id)).size === 3 && reusedImages.every((image, index) => image?.asset_id === expectedAssets[index] && !Object.hasOwn(image, 'url')), {saveIsReady, state:saveState?.dataset.state, revision:savedAfterReuse.revision, reusedImages});
        const fallbackNode = savedAfterReuse.nodes.find(node => node.id === fallbackNodeId);
        assertion('send to canvas saves one node with the configured default model after fallback', saveIsReady && Boolean(fallbackNodeId) && fallbackNode?.historyId === 'history-second' && fallbackNode.runSettings?.provider_id === 'history-provider' && fallbackNode.runSettings?.model === 'gemini-3.1-flash-image-preview' && fallbackNode.runSettings?.size === '1024x1024', fallbackNode || null);
        const originalHistory = savedAfterReuse.history.find(log => log.id === 'history-second');
        assertion('fallback does not change the model in the history record', originalHistory?.params?.provider_id === 'history-provider' && originalHistory.params.model === 'removed-history-model', originalHistory?.params);
        const reusedHistoryNode = reusedNodes[0];
        assertion('send to canvas carries the original prompt and generation settings', saveIsReady && reusedHistoryNode?.historyId === 'history-first' && reusedHistoryNode.runPrompt === '第一条历史提示词' && reusedHistoryNode.runSettings?.model === 'offline-browser-model' && reusedHistoryNode.runSettings?.size === '1024x1024' && reusedHistoryNode.runSettings?.resolution === '4k', reusedHistoryNode || null);

        if(!drawer.classList.contains('open')) await nativeClick('#smartHistoryToggle');
        const beforeCancel = await runConfirmedAction(deleteSelector('history-imported'), 'cancel-imported-delete', {cancel:true});
        const cancelCounts = await counts();
        const cancelCanvas = (await readJson(canvasApi)).canvas;
        assertion('canceling combined delete changes neither the history nor the original', Boolean(historyCard('history-imported')) && cancelCanvas.revision === savedAfterReuse.revision && cancelCanvas.history.some(log => log.id === 'history-imported') && await mediaStatus(expected.importedAsset) === 200 && cancelCounts.history_delete_with_media === beforeCancel, {revision:cancelCanvas.revision, beforeCancel, cancelCounts});

        async function deleteHistory(historyId, assetId, screenshot){
            const before = await runConfirmedAction(deleteSelector(historyId), screenshot);
            const cardRemoved = await waitFor(() => !historyCard(historyId), 5000);
            const canvas = (await readJson(canvasApi)).canvas;
            const after = await counts();
            const status = await mediaStatus(assetId);
            return {recordDeleted:cardRemoved && !canvas.history.some(log => log.id === historyId), mediaStatus:status, before, after, canvas};
        }
        const imported = await deleteHistory('history-imported', expected.importedAsset);
        const importedTrash = await trashItems();
        assertion('deleting an imported history record keeps user media', imported.recordDeleted && imported.mediaStatus === 200 && imported.after.history_delete_with_media === imported.before + 1 && !importedTrash.some(item => item.asset_id === expected.importedAsset), {recordDeleted:imported.recordDeleted, mediaStatus:imported.mediaStatus, counts:imported.after, trash:importedTrash});
        const second = await deleteHistory('history-second', expected.secondAsset);
        assertion('deleting a history record removes its generated media', second.recordDeleted && second.mediaStatus === 404 && second.after.history_delete_with_media === second.before + 1, {recordDeleted:second.recordDeleted, mediaStatus:second.mediaStatus, counts:second.after});

        const first = await deleteHistory('history-first', expected.firstAsset, 'confirm-generated-delete');
        const generator = first.canvas.nodes.find(node => node.id === 'generator');
        assertion('delete button removes the history record and all generated media in one action', first.recordDeleted && first.mediaStatus === 404 && first.after.history_delete_with_media === first.before + 1 && first.after.original_delete === cancelCounts.original_delete, {recordDeleted:first.recordDeleted, mediaStatus:first.mediaStatus, counts:first.after});
        assertion('combined deletion leaves the generator ready to regenerate', generator?.needsRegeneration === true && generator?.generationState === 'ready' && generator?.runPrompt === '第一条历史提示词', generator || null);
        const downstreamMarker = world.querySelector('.image-node[data-id="downstream"] .node-upstream-deleted-marker');
        const downstreamAfter = first.canvas.nodes.find(node => node.id === 'downstream')?.images;
        const downstreamBefore = savedAfterReuse.nodes.find(node => node.id === 'downstream')?.images;
        assertion('combined deletion marks downstream references without losing output', Boolean(downstreamMarker) && downstreamAfter?.length === 1 && downstreamAfter[0].asset_id === downstreamBefore?.[0]?.asset_id, {marker:downstreamMarker?.textContent, images:downstreamAfter});
        const purgeSelector = '#smartHistoryPurgeTrash';
        const purgeTrash = document.querySelector(purgeSelector);
        await waitFor(() => purgeTrash && !purgeTrash.hidden, 3000);
        const trashedAssets = (await trashItems()).map(item => item.asset_id);
        assertion('combined deletion exposes the project-trash control', purgeTrash?.hidden === false && trashedAssets.length === 2 && trashedAssets.includes(expected.firstAsset) && trashedAssets.includes(expected.secondAsset), {hidden:purgeTrash?.hidden, trashedAssets});
        const purgeBefore = await counts();
        const beforePurgeClick = await runConfirmedAction(purgeSelector, 'confirm-trash-purge', {requestKey:'trash_purge'});
        let purgeRequested = false;
        try {
            purgeRequested = await waitFor(async () => (await counts()).trash_purge > beforePurgeClick, 3000);
            const current = await counts();
            const pendingAssets = (await trashItems()).map(item => item.asset_id);
            assertion('emptying project trash exposes its in-flight state', purgeRequested && current.trash_purge === beforePurgeClick + 1 && purgeTrash.getAttribute('aria-busy') === 'true' && purgeTrash.disabled === true && current.trash_items_purged === purgeBefore.trash_items_purged && pendingAssets.includes(expected.firstAsset), {busy:purgeTrash.getAttribute('aria-busy'), disabled:purgeTrash.disabled, pendingAssets, counts:current});
        } finally {
            const release = await fetch('/__history_release_purge', {method:'POST'});
            if(!release.ok) throw new Error(`could not release trash purge: ${release.status}`);
        }
        const purgeComplete = await waitFor(() => purgeTrash.getAttribute('aria-busy') === 'false' && !purgeTrash.disabled && purgeTrash.hidden, 5000);
        const trashAfterPurge = await trashItems();
        const purgeAfter = await counts();
        const purgedMediaStatus = await mediaStatus(expected.firstAsset);
        assertion('explicitly emptying project trash permanently removes the deleted original', purgeRequested && purgeComplete && trashAfterPurge.length === 0 && purgeAfter.trash_items_purged === purgeBefore.trash_items_purged + trashedAssets.length && purgedMediaStatus === 404, {mediaStatus:purgedMediaStatus, trashHidden:purgeTrash.hidden, trashAfterPurge, counts:purgeAfter});
        const third = await deleteHistory('history-third', expected.thirdAsset);
        assertion('a second generated record uses the same combined delete transaction', third.recordDeleted && third.mediaStatus === 404 && third.after.history_delete_with_media === third.before + 1, {recordDeleted:third.recordDeleted, mediaStatus:third.mediaStatus, counts:third.after});
        assertion('history is limited to the current project', ![...document.querySelectorAll('.log-item')].some(item => item.textContent.includes(expected.otherPrompt)), [...document.querySelectorAll('.log-item')].map(item => item.textContent.trim().slice(0, 80)));
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
