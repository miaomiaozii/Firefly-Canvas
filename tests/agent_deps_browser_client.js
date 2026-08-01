(function(){
    'use strict';
    const query = new URLSearchParams(location.search);
    const projectId = query.get('project') || '';
    const reloadKey = `issue14-agent-deps:${projectId}`;
    let restored = null;
    try {
        const raw = sessionStorage.getItem(reloadKey);
        if(raw) restored = JSON.parse(raw);
        sessionStorage.removeItem(reloadKey);
    } catch(_) {}
    let result = restored?.result || {
        kind: 'final',
        result_id: `issue14-deps-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
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
    async function postResult(){
        if(posted) return;
        posted = true;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    const liveAgentEl = () => document.querySelector('.agent-smart-node[data-id="agent-deps"]');
    async function waitForLoaded(){
        return waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Issue 14 Agent Deps'), 15000);
    }
    async function runInitial(){
        if(!await waitForLoaded()) return;
        const agent = liveAgentEl();
        assertion('Agent node renders', Boolean(agent), liveAgentEl()?.dataset?.id || '');
        const goal = agent?.querySelector('[data-agent-goal]');
        if(goal){
            goal.value = '先生成底图，再基于底图生成海报';
            goal.dispatchEvent(new Event('input', {bubbles:true}));
        }
        await sleep(120);
        const add = liveAgentEl()?.querySelector('[data-agent-draft-add]');
        add?.click();
        await sleep(120);
        const cards = () => liveAgentEl()?.querySelectorAll('[data-agent-draft-index]') || [];
        assertion('second task card added', cards().length === 2, cards().length);
        const firstPrompt = liveAgentEl()?.querySelector('[data-agent-draft-index="0"] [data-agent-draft-prompt]');
        if(firstPrompt){
            firstPrompt.value = '生成一张底图';
            firstPrompt.dispatchEvent(new Event('input', {bubbles:true}));
        }
        const secondPrompt = liveAgentEl()?.querySelector('[data-agent-draft-index="1"] [data-agent-draft-prompt]');
        if(secondPrompt){
            secondPrompt.value = '基于底图生成海报';
            secondPrompt.dispatchEvent(new Event('input', {bubbles:true}));
        }
        await sleep(120);
        const depsSelect = liveAgentEl()?.querySelector('[data-agent-draft-index="1"] [data-agent-draft-deps]');
        assertion('dependency selector renders on second card', Boolean(depsSelect), depsSelect?.outerHTML || '');
        if(depsSelect){
            depsSelect.value = '0';
            depsSelect.dispatchEvent(new Event('change', {bubbles:true}));
        }
        await sleep(150);
        document.querySelector('#smartSaveButton')?.click?.();
        await waitFor(() => document.querySelector('#projectSaveState')?.dataset?.state === 'saved', 10000);
        const saved1 = await savedCanvas();
        const savedNode = saved1?.nodes?.find(n => n.id === 'agent-deps');
        assertion('draft with dependency persists to the saved canvas', Array.isArray(savedNode?.agentTaskDrafts) && savedNode.agentTaskDrafts[1]?.dependencies?.[0] === 0, savedNode?.agentTaskDrafts);
        liveAgentEl()?.querySelector('[data-agent-plan]')?.click();
        const planned = await waitFor(() => liveAgentEl()?.querySelector('[data-agent-plan-status]')?.textContent.includes('等待确认'), 15000);
        assertion('plan reaches awaiting confirmation', planned, liveAgentEl()?.querySelector('[data-agent-plan-status]')?.textContent || '');
        const planTasks = liveAgentEl()?.querySelectorAll('[data-agent-plan-task]') || [];
        assertion('plan panel lists both tasks', planTasks.length === 2, planTasks.length);
        liveAgentEl()?.querySelector('[data-agent-confirm]')?.click();
        const done = await waitFor(() => {
            const inPlan = [...(liveAgentEl()?.querySelectorAll('.agent-plan-task-status[data-status]') || [])].map(el => el.dataset.status || '');
            const inNodes = [...document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status]')].map(el => el.dataset.status || '');
            const merged = [...inPlan, ...inNodes];
            return merged.length >= 2 && merged.every(s => ['succeeded','failed','cancelled'].includes(s));
        }, 30000);
        assertion('both tasks reach a terminal state', done, [...(liveAgentEl()?.querySelectorAll('.agent-plan-task-status[data-status]') || [])].map(el => el.dataset.status || '').join(','));
        const statuses = [...document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status]')].map(el => el.dataset.status || '');
        assertion('both tasks succeeded', statuses.length >= 2 && statuses.every(s => s === 'succeeded'), statuses.join(','));
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
        const restoredCards = await waitFor(() => document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status="succeeded"]').length >= 2, 20000);
        assertion('reload restores both task cards as succeeded', restoredCards, document.querySelectorAll('.agent-task-node [data-agent-task-status]').length);
        await waitFor(() => document.querySelector('#projectSaveState')?.dataset?.state === 'saved', 10000);
        let planStatus = '';
        const planDeadline = performance.now() + 20000;
        while(performance.now() < planDeadline){
            const canvas = await savedCanvas(4000);
            const plan = canvas?.nodes?.find(n => n.id === 'agent-deps')?.agent_plan;
            planStatus = plan?.status || '';
            if(['succeeded','completed','failed'].includes(planStatus)) break;
            await sleep(150);
        }
        assertion('saved plan records a terminal status', ['succeeded','completed','failed'].includes(planStatus), planStatus);
        const taskNodes = canvas?.nodes?.filter(n => String(n.task_id || '').startsWith('agent-task-')) || [];
        const task2 = taskNodes.find(n => n.prompt === '基于底图生成海报');
        const refs = task2?.references || task2?.params?.reference_asset_ids || [];
        assertion('downstream task inherits upstream output media', task2 && Array.isArray(refs) && refs.length > 0, JSON.stringify(refs));
    }
    window.addEventListener('error', event => {
        if(event?.error?.message) result.errors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => result.errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));
    (restored ? runAfterReload() : runInitial()).catch(error => result.errors.push(error?.stack || String(error))).finally(() => {
        if(!deferPost) postResult();
    });
})();
