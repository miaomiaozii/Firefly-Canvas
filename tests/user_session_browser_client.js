(function(){
    'use strict';

    // Real-user QA flow: project list -> create project -> canvas edits ->
    // Agent node (plan/confirm/execute) -> history drawer -> reload.
    // Phases are chained through sessionStorage so the same injected script
    // runs on the list page, the canvas page, and the reloaded canvas page.

    const STATE_KEY = 'user-session-qa-flow-v1';
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    let state = null;
    try {
        const raw = sessionStorage.getItem(STATE_KEY);
        if(raw) state = JSON.parse(raw);
    } catch(_) { state = null; }

    const result = {
        kind: 'final',
        result_id: `user-session-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        clientScriptLoaded: true,
        viewport: {width: innerWidth, height: innerHeight},
        steps: Array.isArray(state?.result?.steps) ? state.result.steps : [],
        consoleErrors: Array.isArray(state?.result?.consoleErrors) ? state.result.consoleErrors : [],
        pageErrors: Array.isArray(state?.result?.pageErrors) ? state.result.pageErrors : [],
        exceptions: Array.isArray(state?.result?.exceptions) ? state.result.exceptions : [],
        apiFailures: Array.isArray(state?.result?.apiFailures) ? state.result.apiFailures : [],
        projectId: state?.result?.projectId || '',
        projectName: state?.result?.projectName || '',
        timeline: Array.isArray(state?.result?.timeline) ? state.result.timeline : [],
        pass: false
    };
    result.viewport = {width: innerWidth, height: innerHeight};

    const isListPage = Boolean(document.querySelector('#projectGrid')) || location.pathname === '/';
    const raceProbe = new URLSearchParams(location.search).get('qaRaceProbe') === '1';
    const phase = raceProbe ? 'race-probe' : (state?.phase || (isListPage ? 'list' : 'canvas'));
    const promptTextInitial = '一只发光的萤火虫';
    const promptTextFinal = '一只发光的萤火虫，尾部泛着翠绿光晕';
    const agentGoal = '为萤火虫主题生成两张在线宣传图，第二张基于第一张的构图风格';
    const secondTaskPrompt = '第二张任务卡：萤火虫在夜色荷塘上飞舞';

    let posted = false;
    let reloadRequested = false;
    let navigatedAway = false;

    // Timeline probe: record the last #status / #smartTitle mutation so the
    // report can show whether the i18n DOMContentLoaded apply() overwrote a
    // value the page already cleared (list loading text / canvas title).
    const timeline = [];
    try {
        const probe = document.querySelector('#status') ? '#status' : (document.querySelector('#smartTitle') ? '#smartTitle' : '');
        if(probe){
            const el = document.querySelector(probe);
            const recorded = {el: probe, last: el.textContent};
            const obs = new MutationObserver(() => {
                recorded.last = el.textContent;
                recorded.t = Date.now();
                recorded.gridChildren = document.querySelector('#projectGrid')?.children.length;
                recorded.emptyShown = document.querySelector('#emptyState') ? !document.querySelector('#emptyState').hidden : null;
                recorded.title = document.querySelector('#smartTitle')?.textContent;
                timeline.push({...recorded});
            });
            obs.observe(el, {childList: true, characterData: true, subtree: true});
        }
    } catch(_) {}
    window.addEventListener('DOMContentLoaded', () => {
        timeline.push({t: Date.now(), label: 'DOMContentLoaded', status: document.querySelector('#status')?.textContent, title: document.querySelector('#smartTitle')?.textContent});
    });

    function currentStep(){
        return result.steps[result.steps.length - 1];
    }
    function beginStep(name){
        const step = {step: result.steps.length + 1, name, pass: true, assertions: []};
        result.steps.push(step);
        return step;
    }
    function assertion(name, pass, actual){
        const step = currentStep();
        step.assertions.push({name, pass: Boolean(pass), actual});
        if(!pass) step.pass = false;
    }
    function noteObservation(name, detail){
        const step = currentStep();
        step.assertions.push({name, pass: null, actual: detail, observation: true});
    }
    async function waitFor(predicate, timeout=15000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(predicate()) return true; } catch(_) {}
            await sleep(60);
        }
        return false;
    }
    function projectIdFromUrl(){
        return new URLSearchParams(location.search).get('project') || new URLSearchParams(location.search).get('id') || '';
    }
    async function fetchProject(projectId){
        const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
        if(!response.ok) return null;
        return response.json();
    }
    async function waitForServerCanvas(projectId, predicate, timeout=10000){
        const deadline = performance.now() + timeout;
        let last = null;
        while(performance.now() < deadline){
            try {
                const data = await fetchProject(projectId);
                last = data?.canvas || last;
                if(last && predicate(last)) return last;
            } catch(_) {}
            await sleep(120);
        }
        return last;
    }
    async function waitSavedState(timeout=20000){
        return waitFor(() => document.querySelector('#projectSaveState')?.dataset?.state === 'saved', timeout);
    }
    async function saveCanvasNow(){
        document.querySelector('#smartSaveButton')?.click?.();
        return waitSavedState();
    }
    async function postResult(){
        if(posted) return;
        posted = true;
        result.timeline = [...result.timeline, ...timeline];
        result.pass = result.exceptions.length === 0 && result.steps.length > 0 && result.steps.every(item => item.pass);
        try {
            await fetch('/__user_session_browser_results', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify(result)
            });
        } catch(error) {
            result.exceptions.push(`result upload: ${error.message}`);
        }
    }
    function saveStateToSessionStorage(overrides){
        try {
            sessionStorage.setItem(STATE_KEY, JSON.stringify({
                phase: 'canvas',
                expectedName: state?.expectedName || '',
                projectId: result.projectId,
                projectName: result.projectName,
                ...overrides,
                result
            }));
        } catch(error) {
            result.exceptions.push(`sessionStorage: ${error.message}`);
        }
    }
    function dragNode(el, dx, dy){
        const rect = el.getBoundingClientRect();
        const sx = rect.left + Math.min(48, rect.width / 2);
        const sy = rect.top + Math.min(26, rect.height / 2);
        const before = {left: el.style.left, top: el.style.top};
        el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, button:0, buttons:1, clientX:sx, clientY:sy}));
        window.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, cancelable:true, button:0, buttons:1, clientX:sx + dx, clientY:sy + dy}));
        window.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, button:0, clientX:sx + dx, clientY:sy + dy}));
        return before;
    }

    // ---- console / network / error capture (observations, not auto-fails) ----
    const originalConsoleError = console.error.bind(console);
    console.error = (...args) => {
        const text = args.map(item => {
            try {
                if(item instanceof Error) return `${item.name}: ${item.message}`;
                return typeof item === 'object' ? JSON.stringify(item) : String(item);
            } catch(_) { return String(item); }
        }).join(' ');
        if(result.consoleErrors.length < 60) result.consoleErrors.push(text);
        originalConsoleError(...args);
    };
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if(!response.ok && String(args[0] || '').startsWith('/api/')){
            const entry = {url: String(args[0]).split('?')[0], status: response.status};
            if(!result.apiFailures.some(item => item.url === entry.url && item.status === entry.status)) result.apiFailures.push(entry);
        }
        return response;
    };
    window.addEventListener('error', event => {
        if(event?.error?.message && !result.pageErrors.includes(event.error.message)) result.pageErrors.push(event.error.message);
    });
    window.addEventListener('unhandledrejection', event => {
        const text = String(event?.reason?.message || event?.reason || 'unhandled rejection');
        if(!result.exceptions.includes(text)) result.exceptions.push(text);
    });

    // =====================================================================
    // Phase 1: project list page (/)
    // =====================================================================
    async function runListPhase(){
        const step1 = beginStep('打开项目列表页');
        const listLoaded = await waitFor(() => {
            const grid = document.querySelector('#projectGrid');
            const empty = document.querySelector('#emptyState');
            if(!grid || !empty) return false;
            return grid.children.length > 0 || !empty.hidden;
        }, 15000);
        assertion('列表页可加载，项目库渲染完成（项目卡片或空状态出现）', listLoaded, {
            gridChildren: document.querySelector('#projectGrid')?.children.length,
            emptyHidden: document.querySelector('#emptyState')?.hidden,
            statusText: document.querySelector('#status')?.textContent
        });
        assertion('加载完成后状态栏清除“正在读取项目库…”提示', document.querySelector('#status')?.textContent === '', document.querySelector('#status')?.textContent || '');
        if(!listLoaded){ await postResult(); return; }

        const step2 = beginStep('新建项目（真实 UI 对话框）');
        const showCreate = document.querySelector('#showCreate');
        assertion('列表页提供新建项目按钮', Boolean(showCreate), showCreate?.outerHTML || '');
        showCreate?.click();
        const dialogOpen = await waitFor(() => document.querySelector('#createDialog')?.open === true, 5000);
        assertion('新建项目对话框打开', dialogOpen, document.querySelector('#createDialog')?.open);
        const nameInput = document.querySelector('#projectName');
        const projectName = `QA运营工作流-${Date.now().toString(36)}`;
        assertion('对话框提供项目名称输入', Boolean(nameInput), Boolean(nameInput));
        if(nameInput){
            nameInput.value = projectName;
            nameInput.dispatchEvent(new Event('input', {bubbles:true}));
        }
        assertion('项目名称已填写', nameInput?.value === projectName, nameInput?.value);
        state = {phase:'canvas', expectedName: projectName, projectName, result};
        saveStateToSessionStorage();
        const submit = document.querySelector('#createForm button[type="submit"]');
        window.addEventListener('pagehide', () => { navigatedAway = true; }, {once:true});
        submit?.click();
        await sleep(6000);
        if(!navigatedAway){
            result.exceptions.push('项目创建提交后未跳转到画布页');
            await postResult();
        }
    }

    // =====================================================================
    // Canvas phases
    // =====================================================================
    async function waitCanvasLoaded(){
        const expectedName = state?.expectedName || '';
        return waitFor(() => {
            if(!document.querySelector('#world')) return false;
            const title = document.querySelector('#smartTitle')?.textContent || '';
            return expectedName ? title.includes(expectedName) : Boolean(title);
        }, 20000);
    }
    async function openCreateMenu(){
        const shell = document.querySelector('#shell');
        shell?.dispatchEvent(new MouseEvent('contextmenu', {
            bubbles:true, cancelable:true, button:2,
            clientX: Math.round(innerWidth * 0.58),
            clientY: Math.round(innerHeight * 0.5)
        }));
        return waitFor(() => document.querySelector('#createMenu.open [data-create-type]'), 3000);
    }
    async function createNodeViaMenu(type){
        await openCreateMenu();
        const card = document.querySelector(`#createMenu.open [data-create-type="${type}"]`);
        if(!card) return null;
        card.click();
        return card;
    }
    async function runCanvasPhase(){
        result.projectId = projectIdFromUrl();
        result.projectName = state?.projectName || '';

        const step3 = beginStep('进入项目画布');
        const loaded = await waitCanvasLoaded();
        assertion('画布加载完成，标题为项目名', loaded && result.projectId, {
            title: document.querySelector('#smartTitle')?.textContent,
            projectId: result.projectId
        });
        assertion('项目画布显示保存状态指示', document.querySelector('#projectSaveState') && document.querySelector('#projectSaveState')?.dataset?.state, document.querySelector('#projectSaveState')?.dataset?.state || 'missing');
        if(!loaded || !result.projectId){ await postResult(); return; }

        // Step 4: add a prompt node
        const step4 = beginStep('添加提示词节点');
        const promptCard = await createNodeViaMenu('prompt');
        assertion('创建菜单可选择提示词节点', Boolean(promptCard), promptCard?.outerHTML || 'missing');
        const promptAppeared = await waitFor(() => document.querySelectorAll('.prompt-smart-node').length > 0, 6000);
        const promptNode = [...document.querySelectorAll('.prompt-smart-node')].pop();
        assertion('画布上出现提示词节点', promptAppeared && Boolean(promptNode), document.querySelectorAll('.prompt-smart-node').length);
        if(!promptNode){ await postResult(); return; }
        const promptNodeId = promptNode.dataset.id;
        result.steps.forEach(item => { if(item.step === 4 && item.name === '添加提示词节点') item.assertions.push({name:'提示词节点默认处于选中状态', pass: promptNode.classList.contains('selected'), actual: promptNode.className}); });
        state = {phase:'canvas', expectedName: state.expectedName, projectName: result.projectName, promptNodeId};
        saveStateToSessionStorage();

        // Step 5: type the Chinese prompt, verify display + save state
        const step5 = beginStep('输入提示词文本并保存');
        const textarea = () => document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"] .prompt-node-text`);
        const typed = await waitFor(() => Boolean(textarea()), 5000);
        assertion('提示词节点包含文本输入框', typed, Boolean(textarea()));
        if(textarea()){
            textarea().value = promptTextInitial;
            textarea().dispatchEvent(new Event('input', {bubbles:true}));
        }
        const shown = await waitFor(() => (textarea()?.value || '') === promptTextInitial, 3000);
        assertion('输入的中文提示词显示在节点里', shown, textarea()?.value || '');
        const dirty = await waitFor(() => document.querySelector('#projectSaveState')?.dataset?.state === 'dirty', 5000);
        assertion('输入后保存状态变为未保存(dirty)', dirty, document.querySelector('#projectSaveState')?.dataset?.state || '');
        const saved = await saveCanvasNow();
        assertion('手动保存后状态变为已保存(saved)', saved, document.querySelector('#projectSaveState')?.dataset?.state || '');

        // Step 6: server-side truth
        const step6 = beginStep('提示词文本已持久化到服务端');
        const serverCanvas1 = await waitForServerCanvas(result.projectId, canvas => (canvas.nodes || []).some(node => node.id === promptNodeId), 10000);
        const serverNode1 = (serverCanvas1?.nodes || []).find(node => node.id === promptNodeId);
        assertion('服务端画布包含提示词节点', Boolean(serverNode1), serverNode1 || null);
        assertion('服务端节点文本与输入一致', serverNode1?.text === promptTextInitial, {expected: promptTextInitial, actual: serverNode1?.text});
        const originalPos = serverNode1 ? {x: Number(serverNode1.x), y: Number(serverNode1.y)} : null;

        // Step 7: node selectable
        const step7 = beginStep('提示词节点可选中');
        const nodeEl = document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"]`);
        nodeEl?.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, button:0}));
        await sleep(150);
        const selected = document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"]`)?.classList.contains('selected');
        assertion('点击节点后进入选中状态', Boolean(selected), document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"]`)?.className || '');

        // Step 8: drag the node
        const step8 = beginStep('拖动节点位置');
        const el = document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"]`);
        const beforeDrag = dragNode(el, 150, 90);
        await sleep(300);
        const elAfter = document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"]`);
        const moved = Boolean(elAfter) && (elAfter.style.left !== beforeDrag.left || elAfter.style.top !== beforeDrag.top);
        assertion('节点被拖动到新位置', moved, {before: beforeDrag, after: elAfter ? {left: elAfter.style.left, top: elAfter.style.top} : null});
        const saveStateAfterDrag = document.querySelector('#projectSaveState')?.dataset?.state || '';
        assertion('拖动后保存状态进入未保存(dirty)——位置变化需进入保存队列', saveStateAfterDrag === 'dirty', saveStateAfterDrag);
        await saveCanvasNow();
        const serverCanvas2 = await waitForServerCanvas(result.projectId, canvas => {
            const node = (canvas.nodes || []).find(item => item.id === promptNodeId);
            if(!node || !originalPos) return false;
            return Math.abs(Number(node.x) - originalPos.x) > 5 || Math.abs(Number(node.y) - originalPos.y) > 5;
        }, 10000);
        const serverNode2 = (serverCanvas2?.nodes || []).find(item => item.id === promptNodeId);
        assertion('手动保存后新位置持久化到服务端', Boolean(serverNode2) && (!originalPos || Math.abs(Number(serverNode2.x) - originalPos.x) > 5 || Math.abs(Number(serverNode2.y) - originalPos.y) > 5), {
            original: originalPos,
            server: serverNode2 ? {x: serverNode2.x, y: serverNode2.y} : null,
            draggedCss: elAfter ? {left: elAfter.style.left, top: elAfter.style.top} : null
        });

        // Step 9: edit the prompt text
        const step9 = beginStep('修改提示词文本并保存');
        const editTarget = () => document.querySelector(`.prompt-smart-node[data-id="${promptNodeId}"] .prompt-node-text`);
        if(editTarget()){
            editTarget().value = promptTextFinal;
            editTarget().dispatchEvent(new Event('input', {bubbles:true}));
        }
        const shownFinal = await waitFor(() => (editTarget()?.value || '') === promptTextFinal, 3000);
        assertion('修改后的文本显示在节点里', shownFinal, editTarget()?.value || '');
        const dirty2 = await waitFor(() => document.querySelector('#projectSaveState')?.dataset?.state === 'dirty', 5000);
        assertion('修改后保存状态变为未保存(dirty)', dirty2, document.querySelector('#projectSaveState')?.dataset?.state || '');
        await saveCanvasNow();
        const serverCanvas3 = await fetchProject(result.projectId);
        const serverNode3 = (serverCanvas3?.canvas?.nodes || []).find(node => node.id === promptNodeId);
        assertion('修改后的文本持久化到服务端', serverNode3?.text === promptTextFinal, {expected: promptTextFinal, actual: serverNode3?.text});

        // Step 10: add an Agent node
        const step10 = beginStep('添加 Agent 节点');
        const agentCard = await createNodeViaMenu('agent');
        assertion('创建菜单可选择 Agent 节点', Boolean(agentCard), agentCard?.outerHTML || 'missing');
        const agentAppeared = await waitFor(() => document.querySelectorAll('.agent-smart-node').length > 0, 6000);
        const agentNode = [...document.querySelectorAll('.agent-smart-node')].pop();
        assertion('画布上出现 Agent 节点', agentAppeared && Boolean(agentNode), document.querySelectorAll('.agent-smart-node').length);
        if(!agentNode){ await postResult(); return; }
        const agentNodeId = agentNode.dataset.id;
        state = {phase:'canvas', expectedName: state.expectedName, projectName: result.projectName, promptNodeId, agentNodeId};
        saveStateToSessionStorage();

        // Step 11: goal + two image task cards + dependency
        const step11 = beginStep('配置 Agent 目标、任务卡与依赖');
        const liveAgent = () => document.querySelector(`.agent-smart-node[data-id="${agentNodeId}"]`);
        const goalEl = liveAgent()?.querySelector('[data-agent-goal]');
        assertion('Agent 暴露目标输入框', Boolean(goalEl), Boolean(goalEl));
        if(goalEl){
            goalEl.value = agentGoal;
            goalEl.dispatchEvent(new Event('input', {bubbles:true}));
        }
        const addBtn = liveAgent()?.querySelector('[data-agent-draft-add]');
        addBtn?.click();
        await sleep(250);
        const draftCount = liveAgent()?.querySelectorAll('[data-agent-draft-index]').length || 0;
        assertion('添加后有两张任务卡', draftCount === 2, draftCount);
        if(draftCount === 2){
            const firstKind = liveAgent()?.querySelector('[data-agent-draft-index="0"] [data-agent-kind]');
            const secondKind = liveAgent()?.querySelector('[data-agent-draft-index="1"] [data-agent-kind]');
            const secondPrompt = liveAgent()?.querySelector('[data-agent-draft-index="1"] [data-agent-draft-prompt]');
            if(firstKind){ firstKind.value = 'image'; firstKind.dispatchEvent(new Event('change', {bubbles:true})); }
            if(secondKind){ secondKind.value = 'image'; secondKind.dispatchEvent(new Event('change', {bubbles:true})); }
            if(secondPrompt){ secondPrompt.value = secondTaskPrompt; secondPrompt.dispatchEvent(new Event('input', {bubbles:true})); }
            await sleep(200);
            const kinds = [...liveAgent()?.querySelectorAll('[data-agent-kind]') || []].map(select => select.value);
            assertion('两张任务卡均为在线图片任务', kinds.length === 2 && kinds.every(kind => kind === 'image'), kinds);
            const depsSelect = liveAgent()?.querySelector('[data-agent-draft-deps="1"]');
            const hasDepsControl = Boolean(depsSelect);
            assertion('第二张任务卡提供依赖控件', hasDepsControl, depsSelect?.outerHTML || 'missing');
            if(depsSelect){
                depsSelect.value = '0';
                depsSelect.dispatchEvent(new Event('change', {bubbles:true}));
                await sleep(150);
                assertion('第二张任务卡已设置依赖第一张', depsSelect.value === '0', depsSelect.value);
            }
        }

        // Step 12: generate the plan
        const step12 = beginStep('生成 Agent 计划');
        liveAgent()?.querySelector('[data-agent-plan]')?.click();
        const planned = await waitFor(() => (liveAgent()?.querySelector('[data-agent-plan-status]')?.textContent || '').includes('等待确认'), 30000);
        assertion('计划生成并进入等待确认状态', planned, liveAgent()?.querySelector('[data-agent-plan-status]')?.textContent || '');
        const planTasks = liveAgent()?.querySelectorAll('[data-agent-plan-task]').length || 0;
        assertion('计划包含两张任务卡', planTasks === 2, planTasks);
        const planTaskKinds = [...(liveAgent()?.querySelectorAll('[data-agent-plan-task]') || [])].map(el => el.textContent.includes('在线图片') ? 'image' : 'other');
        assertion('两张计划任务均为在线图片', planTaskKinds.length === 2 && planTaskKinds.every(kind => kind === 'image'), planTaskKinds);

        // Step 13: confirm and execute
        const step13 = beginStep('确认执行 Agent 计划');
        const confirmBtn = liveAgent()?.querySelector('[data-agent-confirm]');
        assertion('确认执行按钮可用', Boolean(confirmBtn && !confirmBtn.disabled), confirmBtn?.outerHTML || '');
        if(confirmBtn && !confirmBtn.disabled){
            confirmBtn.click();
            const completed = await waitFor(() => {
                const el = document.querySelector(`.agent-smart-node[data-id="${agentNodeId}"]`);
                if(!el) return false;
                const planStatus = el.querySelector('[data-agent-plan-status]')?.textContent || '';
                const taskStatuses = [...el.querySelectorAll('[data-agent-task-status][data-status]')].map(item => item.dataset.status);
                return planStatus.includes('已完成') && taskStatuses.length === 2 && taskStatuses.every(status => status === 'succeeded');
            }, 90000);
            const planTaskStatuses = () => [...(document.querySelector(`.agent-smart-node[data-id="${agentNodeId}"]`)?.querySelectorAll('[data-agent-task-status][data-status]') || [])].map(item => ({status: item.dataset.status, text: item.textContent.trim()}));
            let captured = planTaskStatuses();
            for(let retry = 0; retry < 5 && captured.length < 2; retry++){
                await sleep(200);
                captured = planTaskStatuses();
            }
            assertion('两张生图任务均执行成功', completed && captured.length === 2 && captured.every(item => item.status === 'succeeded'), captured);
            if(!completed){
                const agentEl = document.querySelector(`.agent-smart-node[data-id="${agentNodeId}"]`);
                const taskErrors = [...document.querySelectorAll('.agent-task-node [data-agent-task-error]')].map(el => el.textContent.trim());
                const taskStatuses = [...document.querySelectorAll('.agent-task-node [data-agent-task-status]')].map(el => ({status: el.dataset.status, text: el.textContent.trim()}));
                noteObservation('任务失败详情', {
                    planStatus: agentEl?.querySelector('[data-agent-plan-status]')?.textContent,
                    planTaskStatuses: [...(agentEl?.querySelectorAll('[data-agent-task-status][data-status]') || [])].map(el => ({status: el.dataset.status, text: el.textContent.trim()})),
                    taskCardStatuses: taskStatuses,
                    taskErrors
                });
            }
            const taskCards = await waitFor(() => document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status="succeeded"]').length >= 2, 15000);
            assertion('画布出现两张已完成的 Agent 任务卡', taskCards, document.querySelectorAll('.agent-task-node').length);
            const planStatus = document.querySelector(`.agent-smart-node[data-id="${agentNodeId}"] [data-agent-plan-status]`)?.textContent || '';
            assertion('Agent 计划状态显示已完成', planStatus.includes('已完成'), planStatus);
            const serverCanvas4 = await fetchProject(result.projectId);
            const agentLogs = (serverCanvas4?.canvas?.history || []).filter(item => item.type === 'agent-image');
            assertion('服务端记录 Agent 生图历史', agentLogs.length >= 2, agentLogs.length);
            noteObservation('生图适配器', '测试环境使用离线 fake image 适配器（无真实 API key），线上环境该步骤可能因缺 key 失败');
        }

        // Step 14: history drawer
        const step14 = beginStep('打开历史记录侧边栏');
        document.querySelector('#smartHistoryToggle')?.click();
        const drawerOpen = await waitFor(() => document.querySelector('#smartHistoryModal')?.classList.contains('open') && document.querySelectorAll('#smartHistoryList .log-item').length >= 2, 12000);
        assertion('历史侧边栏打开且包含操作记录', drawerOpen, document.querySelectorAll('#smartHistoryList .log-item').length);
        if(drawerOpen){
            const firstLog = document.querySelector('#smartHistoryList .log-item');
            noteObservation('历史记录样例', firstLog?.textContent?.slice(0, 120) || '');
        }

        // Step 15: reload and verify persistence
        state = {phase:'reload', expectedName: state.expectedName, projectName: result.projectName, promptNodeId, agentNodeId, promptTextFinal};
        try {
            sessionStorage.setItem(STATE_KEY, JSON.stringify({...state, result}));
            reloadRequested = true;
            location.reload();
        } catch(error) {
            result.exceptions.push(`reload setup: ${error.message}`);
            await postResult();
        }
    }

    // =====================================================================
    // Reload phase
    // =====================================================================
    async function runReloadPhase(){
        result.projectId = projectIdFromUrl();
        result.projectName = state?.projectName || '';
        const step15 = beginStep('刷新页面后画布内容仍在');
        const loaded = await waitCanvasLoaded();
        assertion('刷新后画布重新加载', loaded, document.querySelector('#smartTitle')?.textContent || '');
        if(!loaded || !result.projectId){ await postResult(); return; }

        const promptNode = document.querySelector(`.prompt-smart-node[data-id="${state?.promptNodeId}"]`);
        assertion('刷新后提示词节点仍在', Boolean(promptNode), document.querySelectorAll('.prompt-smart-node').length);
        const text = promptNode?.querySelector('.prompt-node-text')?.value || '';
        assertion('刷新后提示词文本仍在', text === (state?.promptTextFinal || promptTextFinal), text);

        const agentNode = document.querySelector(`.agent-smart-node[data-id="${state?.agentNodeId}"]`);
        assertion('刷新后 Agent 节点仍在', Boolean(agentNode), document.querySelectorAll('.agent-smart-node').length);
        const planStatus = agentNode?.querySelector('[data-agent-plan-status]')?.textContent || '';
        assertion('刷新后 Agent 计划状态保留', planStatus.includes('已完成'), planStatus);
        const taskCards = await waitFor(() => document.querySelectorAll('.agent-task-node [data-agent-task-status][data-status="succeeded"]').length >= 2, 15000);
        assertion('刷新后两张 Agent 任务卡仍在', taskCards, document.querySelectorAll('.agent-task-node').length);

        const saved = await waitSavedState(15000);
        assertion('刷新后保存状态为已保存', saved, document.querySelector('#projectSaveState')?.dataset?.state || '');

        const serverCanvas = await fetchProject(result.projectId);
        const serverNodes = (serverCanvas?.canvas?.nodes || []).map(node => node.id);
        const serverPrompt = (serverCanvas?.canvas?.nodes || []).find(node => node.id === state?.promptNodeId);
        assertion('服务端节点清单包含提示词与 Agent 节点', Boolean(serverPrompt) && serverNodes.includes(state?.agentNodeId || ''), serverNodes);
        assertion('服务端提示词文本为最终修改值', serverPrompt?.text === (state?.promptTextFinal || promptTextFinal), serverPrompt?.text);
    }

    // =====================================================================
    // Race probe: deterministic reproduction of the list-page status race
    // =====================================================================
    async function runRaceProbe(){
        const step = beginStep('i18n 竞态探测：快响应下项目列表加载完成');
        const rendered = await waitFor(() => {
            const grid = document.querySelector('#projectGrid');
            const empty = document.querySelector('#emptyState');
            return Boolean(grid && empty) && (grid.children.length > 0 || !empty.hidden);
        }, 8000);
        await sleep(800);
        const statusText = document.querySelector('#status')?.textContent || '';
        assertion('项目列表内容渲染完成（卡片或空状态出现）', rendered, {
            gridChildren: document.querySelector('#projectGrid')?.children.length,
            emptyHidden: document.querySelector('#emptyState')?.hidden
        });
        assertion('加载完成后状态栏应被清除，不应残留“正在读取项目库…”', statusText === '', statusText);
    }

    async function run(){
        try {
            if(phase === 'race-probe') await runRaceProbe();
            else if(phase === 'list') await runListPhase();
            else if(phase === 'reload') await runReloadPhase();
            else await runCanvasPhase();
        } catch(error) {
            result.exceptions.push(error?.stack || String(error));
        } finally {
            if(!reloadRequested && !(phase === 'list' && navigatedAway)) await postResult();
        }
    }

    run();
})();
