(function(){
    'use strict';

    const query = new URLSearchParams(location.search);
    const expected = {
        projectId: query.get('conversationProject') || '',
        selectedText: query.get('selectedText') || ''
    };
    const reloadKey = 'issue13-conversation-browser-reload-v1';
    let reloadState = null;
    let reloadRequested = false;
    try {
        reloadState = JSON.parse(sessionStorage.getItem(reloadKey) || 'null');
        if(reloadState) sessionStorage.removeItem(reloadKey);
    } catch(error) {
        reloadState = null;
    }
    const resultId = `issue13-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const result = {
        kind: 'final',
        result_id: resultId,
        clientScriptLoaded: false,
        viewport: {width: innerWidth, height: innerHeight},
        assertions: Array.isArray(reloadState?.assertions) ? reloadState.assertions : [],
        errors: Array.isArray(reloadState?.errors) ? reloadState.errors : [],
        pass: false
    };
    let resultPosted = false;
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
    async function waitFor(predicate, timeout=12000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                if(predicate()) return true;
            } catch(_) {}
            await sleep(40);
        }
        return false;
    }
    async function postClientLoaded(){
        await fetch('/__conversation_browser_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({kind:'client-loaded', viewport:result.viewport})
        }).catch(error => result.errors.push(`client loaded upload: ${error.message}`));
    }
    async function postResult(){
        if(resultPosted) return;
        resultPosted = true;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    async function waitForSavedCanvasNode(nodeId, timeout=5000, predicate=null){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}`);
                if(response.ok){
                    const data = await response.json();
                    const node = (data.canvas?.nodes || []).find(item => item.id === nodeId);
                    if(node && (!predicate || predicate(node))) return {data, node};
                }
            } catch(_) {}
            await sleep(80);
        }
        return {data:null, node:null};
    }
    function nextFrame(){
        return new Promise(resolve => requestAnimationFrame(() => resolve()));
    }
    async function selectContents(element){
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(element);
        selection.removeAllRanges();
        selection.addRange(range);
        await nextFrame();
        return selection.toString();
    }
    function extractionMenu(){
        return document.querySelector('[data-extract-prompt-selection]');
    }
    async function openExtractionMenu(sourceMessage){
        await nextFrame();
        const point = sourceMessage.getBoundingClientRect();
        const eventInit = {
            bubbles:true,
            cancelable:true,
            button:2,
            buttons:2,
            clientX:point.left + 20,
            clientY:point.top + 20,
            pointerType:'mouse'
        };
        if(typeof PointerEvent === 'function'){
            sourceMessage.dispatchEvent(new PointerEvent('pointerdown', eventInit));
            sourceMessage.dispatchEvent(new PointerEvent('pointerup', {...eventInit, buttons:0}));
        }
        sourceMessage.dispatchEvent(new MouseEvent('mousedown', eventInit));
        sourceMessage.dispatchEvent(new MouseEvent('mouseup', {...eventInit, buttons:0}));
        sourceMessage.dispatchEvent(new MouseEvent('contextmenu', {
            ...eventInit,
            clientX:point.left + 20,
            clientY:point.top + 20,
            buttons:0
        }));
        return waitFor(() => extractionMenu()?.classList.contains('open') && extractionMenu()?.querySelector('[data-extract-prompt-action]') === document.activeElement, 1500);
    }
    async function runInitial(){
        const loaded = await waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Issue 13 对话画布'), 15000);
        assertion('real product page loaded the temporary project canvas', loaded, document.querySelector('#smartTitle')?.textContent || '');
        const conversation = document.querySelector('.conversation-smart-node');
        assertion('canvas renders an independent conversation node', Boolean(conversation), document.querySelectorAll('.conversation-smart-node').length);
        if(!conversation) return;

        assertion('conversation renders durable multi-turn history', document.querySelectorAll('.conversation-node-message').length === 2, document.querySelectorAll('.conversation-node-message').length);
        const referenceKinds = [...document.querySelectorAll('.conversation-node-reference')].map(item => item.dataset.referenceKind).sort();
        assertion('conversation shows every explicitly connected media reference', referenceKinds.join(',') === 'audio,image,video', referenceKinds);

        const promptCountBeforeSend = document.querySelectorAll('.prompt-smart-node').length;
        const input = conversation.querySelector('[data-conversation-input]');
        const send = conversation.querySelector('[data-conversation-send]');
        assertion('conversation exposes a local message composer', Boolean(input && send), {input:Boolean(input), send:Boolean(send)});
        if(!input || !send) return;
        input.value = '继续说明参考素材之间的关系。';
        input.dispatchEvent(new Event('input', {bubbles:true}));
        send.click();
        send.click();
        const sent = await waitFor(() => document.querySelectorAll('.conversation-node-message').length === 4, 25000);
        assertion('conversation sends a second turn through the local adapter boundary', sent, document.querySelectorAll('.conversation-node-message').length);
        assertion('assistant reply is stored in the same node history', document.querySelector('.conversation-node-message[data-role="assistant"]:last-of-type')?.textContent.includes('离线假适配器回复'), document.querySelectorAll('.conversation-node-message[data-role="assistant"]').length);
        assertion('same-node duplicate send is suppressed while pending', document.querySelectorAll('.conversation-node-message').length === 4, document.querySelectorAll('.conversation-node-message').length);
        assertion('conversation history is not automatically copied to a prompt node', document.querySelectorAll('.prompt-smart-node').length === promptCountBeforeSend, document.querySelectorAll('.prompt-smart-node').length);
        const timeoutMerged = await waitForSavedCanvasNode('conversation-1', 5000, node => (node.messages || []).length === 4);
        const timeoutMessages = (timeoutMerged.node?.messages || []).slice(-2);
        const timeoutRequestIds = [...new Set(timeoutMessages.map(message => message.request_id).filter(Boolean))];
        assertion('timed-out POST rebase keeps one server-identified user for its request', timeoutMessages.length === 2
            && timeoutRequestIds.length === 1
            && timeoutMessages.filter(message => message.role === 'user').length === 1
            && timeoutMessages.every(message => !String(message.id || '').startsWith('local-'))
            && timeoutMessages.every(message => message.status !== 'pending'), timeoutMessages);
        const liveInput = document.querySelector('.conversation-smart-node [data-conversation-input]');
        const liveSend = document.querySelector('.conversation-smart-node [data-conversation-send]');
        liveInput.value = '超时恢复后继续发送。';
        liveInput.dispatchEvent(new Event('input', {bubbles:true}));
        liveSend.click();
        const continuedAfterTimeout = await waitFor(() => document.querySelectorAll('.conversation-node-message').length === 6, 10000);
        assertion('conversation can continue after the timed-out request is reconciled', continuedAfterTimeout, document.querySelectorAll('.conversation-node-message').length);

        // Real browser/HTTP out-of-order regression: fetch the current server
        // canvas, synthesize an older response that omits the newest turn, and
        // pass it through the product response handler.  The revision
        // watermark must reject it without changing the rendered history.
        const currentResponse = await fetch(`/api/project-library/${encodeURIComponent(expected.projectId)}`);
        const currentData = currentResponse.ok ? await currentResponse.json() : {};
        const currentCanvas = currentData.canvas || {};
        const staleCanvas = JSON.parse(JSON.stringify(currentCanvas));
        staleCanvas.revision = Math.max(0, Number(currentCanvas.revision || 0) - 1);
        const staleNode = (staleCanvas.nodes || []).find(item => item.id === 'conversation-1');
        if(staleNode?.messages?.length) staleNode.messages = staleNode.messages.slice(0, -1);
        const beforeStaleCount = document.querySelectorAll('.conversation-node-message').length;
        const staleApplied = window.__issue13ApplyConversationCanvasResponse?.(staleCanvas);
        await nextFrame();
        assertion('older HTTP canvas response is rejected without losing newer conversation turns', staleApplied === false
            && document.querySelectorAll('.conversation-node-message').length === beforeStaleCount, {
            staleApplied,
            before:beforeStaleCount,
            after:document.querySelectorAll('.conversation-node-message').length,
            currentRevision:currentCanvas.revision,
            staleRevision:staleCanvas.revision,
        });

        await waitFor(() => document.querySelector('#projectSaveState')?.dataset.state === 'saved', 5000);
        const sourceMessageElement = () => document.querySelector('.conversation-node-message-text[data-message-id="message-assistant-1"]');
        let sourceMessage = sourceMessageElement();
        assertion('assistant message exposes a selectable source message', Boolean(sourceMessage), document.querySelectorAll('.conversation-node-message-text').length);
        if(!sourceMessage) return;
        assertion('message uses context-menu semantics rather than a false button role', sourceMessage.getAttribute('role') !== 'button' && sourceMessage.getAttribute('aria-haspopup') === 'menu', {role:sourceMessage.getAttribute('role'), haspopup:sourceMessage.getAttribute('aria-haspopup')});
        window.getSelection()?.removeAllRanges();
        sourceMessage.focus();
        sourceMessage.dispatchEvent(new KeyboardEvent('keydown', {key:'F10', shiftKey:true, bubbles:true, cancelable:true}));
        await sleep(120);
        assertion('Shift+F10 without a selection does not open extraction', !extractionMenu()?.classList.contains('open'), extractionMenu()?.className || 'closed');
        await openExtractionMenu(sourceMessage);
        await sleep(120);
        assertion('right click without a selection does not open extraction', !extractionMenu()?.classList.contains('open'), extractionMenu()?.className || 'closed');
        assertion('no-selection activation never creates a prompt', document.querySelectorAll('.prompt-smart-node').length === promptCountBeforeSend, document.querySelectorAll('.prompt-smart-node').length);
        await selectContents(sourceMessage);
        sourceMessage.focus();
        sourceMessage.dispatchEvent(new KeyboardEvent('keydown', {key:'F10', shiftKey:true, bubbles:true, cancelable:true}));
        const keyboardMenuOpen = await waitFor(() => extractionMenu()?.classList.contains('open'), 1500);
        assertion('Shift+F10 opens prompt extraction without a pointer', keyboardMenuOpen, extractionMenu()?.className || 'closed');
        extractionMenu()?.querySelector('[data-extract-prompt-action]')?.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
        sourceMessage = sourceMessageElement();
        const selected = await selectContents(sourceMessage);
        const menuOpen = await openExtractionMenu(sourceMessage);
        const menu = extractionMenu();
        const menuItem = menu?.querySelector('[data-extract-prompt-action]');
        assertion('selected conversation text opens the explicit prompt extraction menu', menuOpen, menu?.className || 'closed');
        assertion('extraction menu exposes the correct keyboard menu semantics', menu?.getAttribute('role') === 'menu' && menuItem?.getAttribute('role') === 'menuitem' && menuItem?.tabIndex === 0, {role:menu?.getAttribute('role'), itemRole:menuItem?.getAttribute('role'), tabIndex:menuItem?.tabIndex});
        assertion('extraction menu moves focus to its keyboard action', document.activeElement === menuItem, document.activeElement?.outerHTML || '');

        menuItem?.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
        assertion('Escape closes the extraction menu', !(await waitFor(() => extractionMenu()?.classList.contains('open'), 800)), extractionMenu()?.className || 'closed');
        sourceMessage = sourceMessageElement();
        await selectContents(sourceMessage);
        const reopenedForOutsideClick = await openExtractionMenu(sourceMessage);
        document.body.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
        assertion('outside click closes the extraction menu', reopenedForOutsideClick && !(await waitFor(() => extractionMenu()?.classList.contains('open'), 800)), extractionMenu()?.className || 'closed');

        sourceMessage = sourceMessageElement();
        await selectContents(sourceMessage);
        await openExtractionMenu(sourceMessage);
        extractionMenu()?.querySelector('[data-extract-prompt-action]')?.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}));
        const extracted = await waitFor(() => document.querySelectorAll('.prompt-smart-node').length === promptCountBeforeSend + 1, 2500);
        assertion('keyboard extraction creates one new prompt node', extracted, document.querySelectorAll('.prompt-smart-node').length);
        if(!extracted) return;
        const promptNodes = [...document.querySelectorAll('.prompt-smart-node')];
        const extractedNode = promptNodes[promptNodes.length - 1];
        const promptText = extractedNode.querySelector('.prompt-node-text')?.value || '';
        assertion('new prompt contains only the selected text', promptText === selected && promptText === expected.selectedText, {selected, expected:expected.selectedText, actual:promptText});
        assertion('new prompt preserves source node and message identifiers', extractedNode.dataset.sourceNodeId === 'conversation-1' && extractedNode.dataset.sourceMessageId === 'message-assistant-1', {node:extractedNode.dataset.sourceNodeId, message:extractedNode.dataset.sourceMessageId});
        const savedResult = await waitForSavedCanvasNode(extractedNode.dataset.id);
        const saved = savedResult.data || {};
        const savedNode = savedResult.node;
        const touchedEdges = (saved.canvas?.connections || []).filter(edge => edge.from === savedNode?.id || edge.to === savedNode?.id);
        assertion('extracted prompt has no automatic graph connection', touchedEdges.length === 0, touchedEdges);
        assertion('extracted prompt persists without chat messages', Boolean(savedNode) && !Object.prototype.hasOwnProperty.call(savedNode, 'messages'), savedNode || null);
        assertion('saved prompt preserves source identifiers', Boolean(savedNode) && savedNode.source_node_id === 'conversation-1' && savedNode.source_message_id === 'message-assistant-1', savedNode || null);
        assertion('extracted prompt does not include the rest of chat history', !promptText.includes('请先分析') && !promptText.includes('离线假适配器回复'), promptText);
        assertion('selection was read from the rendered message rather than a global session list', selected === expected.selectedText && !document.querySelector('.conversation-global-list'), {selected, globalList:Boolean(document.querySelector('.conversation-global-list'))});

        const collapseButton = document.querySelector('.conversation-smart-node [data-conversation-collapse]');
        assertion('conversation exposes an accessible independent collapse button', Boolean(collapseButton) && collapseButton.getAttribute('aria-expanded') === 'true', collapseButton?.outerHTML || '');
        collapseButton?.click();
        const collapsed = await waitFor(() => document.querySelector('.conversation-smart-node [data-conversation-panel]')?.hidden === true, 1500);
        const collapsedButton = document.querySelector('.conversation-smart-node [data-conversation-collapse]');
        assertion('conversation collapses accumulated messages independently', collapsed && collapsedButton?.getAttribute('aria-expanded') === 'false', {collapsed, expanded:collapsedButton?.getAttribute('aria-expanded')});
        const savedConversation = await waitForSavedCanvasNode('conversation-1', 5000, node => node.conversation_collapsed === true);
        assertion('conversation collapsed state persists in canvas', savedConversation.node?.conversation_collapsed === true, savedConversation.node || null);
        assertion('conversation transient sending guard is not persisted', !Object.prototype.hasOwnProperty.call(savedConversation.node || {}, 'conversationSending'), savedConversation.node || null);

        try {
            sessionStorage.setItem(reloadKey, JSON.stringify({
                phase:'after-reload',
                assertions:result.assertions,
                errors:result.errors,
                promptId:extractedNode.dataset.id
            }));
            reloadRequested = true;
            location.reload();
        } catch(error) {
            result.errors.push(`reload setup: ${error.message}`);
        }
    }
    async function runAfterReload(){
        const loaded = await waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Issue 13 对话画布'), 15000);
        assertion('reopened project retains its product title after reload', loaded, document.querySelector('#smartTitle')?.textContent || '');
        const conversation = document.querySelector('.conversation-smart-node');
        assertion('reopened DOM contains the same independent conversation node', Boolean(conversation), document.querySelectorAll('.conversation-smart-node').length);
        const reopenedCollapse = conversation?.querySelector('[data-conversation-collapse]');
        const reopenedPanel = conversation?.querySelector('[data-conversation-panel]');
        assertion('reopened conversation restores collapsed state and accessible semantics', reopenedPanel?.hidden === true && reopenedCollapse?.getAttribute('aria-expanded') === 'false', {hidden:reopenedPanel?.hidden, expanded:reopenedCollapse?.getAttribute('aria-expanded')});
        reopenedCollapse?.click();
        const expandedAfterReload = await waitFor(() => document.querySelector('.conversation-smart-node [data-conversation-panel]')?.hidden === false, 1500);
        assertion('collapsed conversation can be expanded after reload', expandedAfterReload && document.querySelector('.conversation-smart-node [data-conversation-collapse]')?.getAttribute('aria-expanded') === 'true', expandedAfterReload);
        assertion('reopened DOM restores all durable turns', document.querySelectorAll('.conversation-node-message').length === 6, document.querySelectorAll('.conversation-node-message').length);
        const referenceKinds = [...document.querySelectorAll('.conversation-node-reference')].map(item => item.dataset.referenceKind).sort();
        assertion('reopened DOM restores explicit image video and audio references', referenceKinds.join(',') === 'audio,image,video', referenceKinds);
        const prompt = [...document.querySelectorAll('.prompt-smart-node')].find(node => node.dataset.id === reloadState?.promptId);
        assertion('reopened DOM restores the extracted prompt node', Boolean(prompt), document.querySelectorAll('.prompt-smart-node').length);
        if(prompt){
            const text = prompt.querySelector('.prompt-node-text')?.value || '';
            assertion('reopened prompt retains only the selected source text', text === expected.selectedText && !text.includes('请先分析') && !text.includes('离线假适配器回复'), text);
            assertion('reopened prompt retains source node and message identifiers', prompt.dataset.sourceNodeId === 'conversation-1' && prompt.dataset.sourceMessageId === 'message-assistant-1', {node:prompt.dataset.sourceNodeId, message:prompt.dataset.sourceMessageId});
        }
        const reopenedCanvas = await waitForSavedCanvasNode(reloadState?.promptId || '');
        const saved = reopenedCanvas.data || {};
        const savedNode = reopenedCanvas.node;
        const touchedEdges = (saved.canvas?.connections || []).filter(edge => edge.from === savedNode?.id || edge.to === savedNode?.id);
        assertion('reopened saved prompt still has no graph connection', Boolean(savedNode) && touchedEdges.length === 0, touchedEdges);
        assertion('reopened saved prompt still has no chat history field', Boolean(savedNode) && !Object.prototype.hasOwnProperty.call(savedNode, 'messages'), savedNode || null);
        assertion('reopened project has no global conversation list', !document.querySelector('.conversation-global-list'), Boolean(document.querySelector('.conversation-global-list')));
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    async function run(){
        result.clientScriptLoaded = true;
        await postClientLoaded();
        if(reloadState?.phase === 'after-reload') await runAfterReload();
        else await runInitial();
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(() => {
        if(!reloadRequested) postResult();
    });
})();
