(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const reloadKey = `issue14-agent-browser:${projectId}`;
    let restored = null;
    try {
        const raw = sessionStorage.getItem(reloadKey);
        if(raw) restored = JSON.parse(raw);
        sessionStorage.removeItem(reloadKey);
    } catch(_) {}
    let result = restored?.result || {
        kind: 'final',
        result_id: `issue14-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        clientScriptLoaded: true,
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        pass: false,
    };
    result.viewport = {width: innerWidth, height: innerHeight};
    let posted = false;
    let deferPost = false;
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
    async function waitFor(predicate, timeout=15000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(predicate()) return true; } catch(_) {}
            await sleep(60);
        }
        return false;
    }
    async function savedCanvas(timeout=10000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
                if(response.ok) return (await response.json()).canvas || {};
            } catch(_) {}
            await sleep(100);
        }
        return {};
    }
    async function refreshCanvas(){
        const latest = await savedCanvas();
        const apply = window.applyConversationCanvasResponse || window.__issue13ApplyConversationCanvasResponse;
        if(latest && typeof apply === 'function'){
            try {
                apply(latest);
                // Applying an authoritative canvas replaces the in-memory node
                // objects; render again so DOM handlers bind to those objects.
                window.render?.();
            } catch(_) {}
        }
        return latest;
    }
    async function postResult(){
        if(posted) return;
        posted = true;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    async function waitForLoaded(){
        const loaded = await waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Issue 14 Agent'), 15000);
        assertion('real product page loaded the temporary Agent project', loaded, document.querySelector('#smartTitle')?.textContent || '');
        return loaded;
    }
    async function runInitial(){
        if(!await waitForLoaded()) return;
        const agent = document.querySelector('.agent-smart-node[data-id="agent-1"]') || document.querySelector('.agent-smart-node');
        assertion('create menu path renders an independent smart Agent node', Boolean(agent), document.querySelectorAll('.agent-smart-node').length);
        const shell = document.querySelector('#shell');
        shell?.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true, button:2, clientX:innerWidth - 40, clientY:innerHeight - 40}));
        const menuAgent = await waitFor(() => document.querySelector('#createMenu.open [data-create-type="agent"]'), 1200);
        assertion('create menu exposes the Agent node entry', menuAgent, document.querySelector('#createMenu')?.className || '');
        document.body.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
        const goal = agent?.querySelector('[data-agent-goal]');
        const planButton = agent?.querySelector('[data-agent-plan]');
        const kind = agent?.querySelector('[data-agent-kind]');
        const count = agent?.querySelector('[data-agent-count]');
        assertion('Agent exposes goal, media kind, count and plan controls', Boolean(goal && planButton && kind && count), {
            goal:Boolean(goal), kind:Boolean(kind), count:Boolean(count), plan:Boolean(planButton)
        });
        assertion('Agent task picker includes local prompt-node creation', Boolean(kind?.querySelector('option[value="prompt"]')), [...(kind?.options || [])].map(option => option.value));
        if(!agent || !goal || !planButton) return;
        goal.value = '为连接的参考图生成一张在线海报';
        goal.dispatchEvent(new Event('input', {bubbles:true}));
        kind.value = 'image';
        kind.dispatchEvent(new Event('change', {bubbles:true}));
        count.value = '1';
        count.dispatchEvent(new Event('input', {bubbles:true}));
        planButton.click();
        const liveAgentEl = () => document.querySelector('.agent-smart-node[data-id="agent-1"]');
        const planned = await waitFor(() => liveAgentEl()?.querySelector('[data-agent-plan-status]')?.textContent.includes('等待确认'), 12000);
        assertion('Agent shows a visible plan before execution', planned, liveAgentEl()?.querySelector('[data-agent-plan-status]')?.textContent || '');
        assertion('plan panel exposes exactly one online image task', liveAgentEl()?.querySelectorAll('[data-agent-plan-task]').length === 1, liveAgentEl()?.querySelectorAll('[data-agent-plan-task]').length || 0);
        assertion('planning does not add a generated result node', document.querySelectorAll('.image-node[data-id$="-result"]').length === 0, document.querySelectorAll('.image-node[data-id$="-result"]').length);
        const confirm = liveAgentEl()?.querySelector('[data-agent-confirm]');
        assertion('confirmation button becomes enabled only after a plan', Boolean(confirm && !confirm.disabled), confirm?.outerHTML || '');
        if(!confirm || confirm.disabled) return;
        confirm.click();
        const completed = await waitFor(() => liveAgentEl()?.querySelector('.agent-plan-task-status[data-status="succeeded"]') && document.querySelector('.agent-task-node [data-agent-task-status][data-status="succeeded"]'), 25000);
        assertion('confirmation runs the planned task through the offline adapter', completed, document.querySelector('.agent-task-node [data-agent-task-status]')?.textContent || '');
        assertion('every confirmed tool call has a visible persisted Agent task card', document.querySelectorAll('.agent-task-node [data-agent-task-status]').length >= 1, document.querySelectorAll('.agent-task-node').length);
        assertion('task card exposes model, count, params and a result link', Boolean(document.querySelector('.agent-task-node .agent-task-meta') && document.querySelector('.agent-task-node .agent-task-params') && document.querySelector('.agent-task-node [data-agent-result-link]')), document.querySelector('.agent-task-node')?.textContent || '');
        const canvas = await refreshCanvas();
        const resultNodes = (canvas.nodes || []).filter(node => node.agent_task_id && node.type === 'smart-image');
        assertion('successful task creates a project-owned result node', resultNodes.length === 1 && (resultNodes[0].images || []).length === 1, resultNodes);
        assertion('successful task records visible generation history', (canvas.history || []).some(item => item.type === 'agent-image' && item.status === 'succeeded' && item.model), (canvas.history || []).slice(-3));
        const planStatus = (canvas.nodes || []).find(node => node.id === agent.dataset.id)?.agent_plan?.status;
        assertion('durable plan status closes after the task succeeds', planStatus === 'completed', planStatus);
        // The first worker completion may advance the canvas revision while a
        // second Agent is still open.  Re-read the authoritative canvas before
        // planning the second tool call, and reuse a stable request id.
        await refreshCanvas();
        const promptAgentEl = () => document.querySelector('.agent-smart-node[data-id="agent-2"]');
        const promptAgent = promptAgentEl();
        const promptGoal = promptAgent?.querySelector('[data-agent-goal]');
        const promptKind = promptAgent?.querySelector('[data-agent-kind]');
        const promptPlanButton = promptAgent?.querySelector('[data-agent-plan]');
        assertion('a second Agent can independently plan a prompt-node task', Boolean(promptAgent && promptGoal && promptKind && promptPlanButton), promptAgent?.outerHTML || '');
        // P1: multi-task draft cards — add/remove task cards without executing.
        const multiDraftAdd = promptAgent?.querySelector('[data-agent-draft-add]');
        const multiDraftCards = () => promptAgentEl()?.querySelectorAll('[data-agent-draft-index]') || [];
        assertion('multi-task drafts start with a single task card', multiDraftCards().length === 1, multiDraftCards().length);
        multiDraftAdd?.click();
        await sleep(80);
        assertion('adding a task card grows the draft list', multiDraftCards().length === 2, multiDraftCards().length);
        const secondCardKind = promptAgentEl()?.querySelector('[data-agent-draft-index="1"] [data-agent-kind]');
        const secondCardPrompt = promptAgentEl()?.querySelector('[data-agent-draft-index="1"] [data-agent-draft-prompt]');
        if(secondCardKind){ secondCardKind.value = 'video'; secondCardKind.dispatchEvent(new Event('change', {bubbles:true})); }
        if(secondCardPrompt){ secondCardPrompt.value = '第二张卡片的提示词'; secondCardPrompt.dispatchEvent(new Event('input', {bubbles:true})); }
        await sleep(80);
        document.querySelector('#smartSaveButton')?.click?.();
        await waitFor(() => document.querySelector('#projectSaveState')?.dataset?.state === 'saved', 10000);
        await sleep(150);
        const savedDrafts = await savedCanvas();
        const savedAgent2 = savedDrafts?.nodes?.find?.(item => item.id === 'agent-2') || {};
        assertion('per-task kind and prompt persist to the saved canvas', Array.isArray(savedAgent2?.agentTaskDrafts) && savedAgent2.agentTaskDrafts.length === 2 && savedAgent2.agentTaskDrafts[1]?.kind === 'video' && savedAgent2.agentTaskDrafts[1]?.prompt === '第二张卡片的提示词', savedAgent2?.agentTaskDrafts);
        promptAgentEl()?.querySelector('[data-agent-draft-index="1"] [data-agent-draft-remove]')?.click();
        await sleep(80);
        assertion('removing a task card shrinks the draft list back to one', multiDraftCards().length === 1, multiDraftCards().length);
        if(promptAgent && promptGoal && promptKind && promptPlanButton){
            promptGoal.value = '新建一段可复用的提示词';
            promptGoal.dispatchEvent(new Event('input', {bubbles:true}));
            promptKind.value = 'prompt';
            promptKind.dispatchEvent(new Event('change', {bubbles:true}));
            promptPlanButton.click();
            assertion('prompt controls retain the requested values before planning', promptGoal.value === '新建一段可复用的提示词' && promptKind.value === 'prompt', {
                goal:promptGoal.value, kind:promptKind.value
            });
            await sleep(200);
            assertion('prompt controls retain the requested values after planning starts', promptAgentEl()?.querySelector('[data-agent-goal]')?.value === '新建一段可复用的提示词' && promptAgentEl()?.querySelector('[data-agent-kind]')?.value === 'prompt', {
                goal:promptAgentEl()?.querySelector('[data-agent-goal]')?.value || '', kind:promptAgentEl()?.querySelector('[data-agent-kind]')?.value || ''
            });
            const promptPlanned = await waitFor(() => promptAgentEl()?.querySelector('[data-agent-plan-status]')?.textContent.includes('等待确认'), 12000);
            const promptServerNode = (await savedCanvas()).nodes?.find(node => node.id === 'agent-2');
            assertion('prompt planning leaves the requested draft on the server', promptServerNode?.agentGoal === '新建一段可复用的提示词' && promptServerNode?.agentTaskDraft?.kind === 'prompt', {
                agentGoal:promptServerNode?.agentGoal || '', taskDraft:promptServerNode?.agentTaskDraft || null,
                saveState:document.querySelector('#projectSaveState')?.dataset.state || ''
            });
            assertion('prompt-node plan is visible before confirmation', promptPlanned, promptAgentEl()?.querySelector('[data-agent-plan-status]')?.textContent || '');
            assertion('prompt-node plan identifies the local task kind', promptAgentEl()?.querySelector('[data-agent-plan-task]')?.textContent.includes('创建提示词节点'), promptAgentEl()?.querySelector('[data-agent-plan-task]')?.textContent || '');
            const promptConfirm = promptAgentEl()?.querySelector('[data-agent-confirm]');
            promptConfirm?.click();
            const promptCompleted = await waitFor(() => promptAgentEl()?.querySelector('.agent-plan-task-status[data-status="succeeded"]') && document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status="succeeded"]').length >= 2, 15000);
            assertion('prompt-node confirmation completes without an online adapter call', promptCompleted, promptAgentEl()?.querySelector('[data-agent-task-status]')?.textContent || '');
            assertion('terminal task cards expose no cancel/retry controls', [...document.querySelectorAll('[data-agent-cancel-task],[data-agent-retry-task]')].length === 0, document.querySelectorAll('[data-agent-cancel-task],[data-agent-retry-task]').length);
            const promptCanvas = await refreshCanvas();
            const promptResult = (promptCanvas.nodes || []).find(node => node.agent_task_id && node.type === 'smart-prompt');
            const promptEdges = (promptCanvas.connections || []).filter(edge => edge.from === promptResult?.id || edge.to === promptResult?.id);
            assertion('prompt task creates a visible smart-prompt with no automatic connection', promptResult?.text === '一只发光的萤火虫' && promptEdges.length === 0, {promptResult, promptEdges});
            assertion('prompt task writes a project history record without media output', (promptCanvas.history || []).some(item => item.type === 'agent-prompt' && item.status === 'succeeded' && item.prompt_output?.text === '一只发光的萤火虫'), (promptCanvas.history || []).slice(-3));
        }
        const taskRects = [...document.querySelectorAll('.agent-task-node')].map(el => el.getBoundingClientRect());
        const overlap = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
        assertion('multiple Agent task cards are laid out without overlap', taskRects.every((rect, index) => taskRects.slice(index + 1).every(other => !overlap(rect, other))), taskRects.map(rect => ({left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom})));
        const artifactEls = [...document.querySelectorAll('.agent-smart-node,.agent-task-node,[data-id$="-result"]')];
        const artifactRects = artifactEls.map(el => el.getBoundingClientRect());
        assertion('Agent tasks/results do not cover either Agent node', artifactRects.every((rect, index) => artifactRects.slice(index + 1).every(other => !overlap(rect, other))), artifactRects.map(rect => ({left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom})));
        try {
            sessionStorage.setItem(reloadKey, JSON.stringify({result}));
            deferPost = true;
            location.reload();
        } catch(error) {
            result.errors.push(`reload state: ${error.message}`);
        }
    }
    async function runAfterReload(){
        if(!await waitForLoaded()) return;
        const firstTask = await waitFor(() => document.querySelector('.agent-task-node[data-id^="agent-task-"] [data-agent-task-status][data-status="succeeded"]'), 20000);
        const taskCards = [...document.querySelectorAll('.agent-task-node')];
        assertion('reload restores the Agent task result and terminal status', firstTask, document.querySelector('.agent-task-node [data-agent-task-status]')?.textContent || '');
        assertion('reload restores the prompt-node task result', Boolean(document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status="succeeded"]').length >= 2), document.querySelectorAll('.agent-task-node [data-agent-task-status]').length);
        assertion('reload keeps each persisted tool call visible as a task card', taskCards.length >= 2 && taskCards.every(card => card.querySelector('[data-agent-task-status]') && card.querySelector('.agent-task-result-link')), taskCards.length);
        const rects = taskCards.map(el => el.getBoundingClientRect());
        const overlap = (a,b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
        assertion('reload keeps task cards laid out without overlap', rects.every((rect, index) => rects.slice(index + 1).every(other => !overlap(rect, other))), rects.map(rect => ({left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom})));
        const artifactRects = [...document.querySelectorAll('.agent-smart-node,.agent-task-node,[data-id$="-result"]')].map(el => el.getBoundingClientRect());
        assertion('reload keeps Agent tasks/results away from both Agent nodes', artifactRects.every((rect, index) => artifactRects.slice(index + 1).every(other => !overlap(rect, other))), artifactRects.map(rect => ({left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom})));
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    (restored ? runAfterReload() : runInitial()).catch(error => result.errors.push(error?.stack || String(error))).finally(() => {
        if(!deferPost) postResult();
    });
})();
