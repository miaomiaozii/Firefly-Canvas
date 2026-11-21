// @ts-check

/** @typedef {{present: boolean, value?: any}} AgentDiffState */
/** @typedef {{entity: string, id: string, path: string[], before: AgentDiffState, after: AgentDiffState}} AgentDiff */
/** @typedef {{op_id: string, turn_id: string | null, agent_node_id: string, tool: string, before: any[], after: any[], touched_node_ids: string[], irreversible_effects: any[], undone: boolean}} AgentDiffOperation */
/** @typedef {{turnId: string | null, operations: AgentDiffOperation[], phase: string, notice: string, capped: boolean, foreign: boolean, request: number, expanded: Set<string>, busy: boolean}} AgentCompletionCard */
/** @typedef {{getProjectId: () => string, getView: (id: string) => any, getViews: () => [string, any][], hasNode: (id: string) => boolean, refreshView: (id: string) => void, append: (state: any, message: any) => void, locked: (state: any) => boolean, ask: (text: string) => Promise<boolean>, reload: (project: string) => Promise<boolean>, revealRow: (state: any, body: HTMLElement, row: Element) => void, marks: (project: string, marks: any[]) => void}} AgentCompletionDependencies */

var canvasAgentCompletion = (function () {
    /** @type {Partial<AgentCompletionDependencies>} */
    let deps = {};
    let listening = false;
    /** @type {Set<string>} */
    const writing = new Set();
    /** @type {WeakMap<AgentCompletionCard, () => boolean>} */
    const loads = new WeakMap();
    /** @type {{nodeId: string, opId: string, turnId: string | null, current: () => boolean} | null} */
    let revealTarget = null;
    const labels = {added: '新增节点', frames: '新增组框', prompts: '改提示词', moves: '移动', generation: '生成状态节点', other: '其他改动'};
    const generatedMediaNotice = '撤销生成节点时，媒体保留在资源库和历史里，不进入 7 天回收区。';

    /** @param {unknown} value */
    function safe(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch] || ch));
    }
    /** @param {unknown} value @returns {string | null} */
    function identity(value) { return typeof value === 'string' && value.trim() ? value : null; }
    /** @param {any} value */
    function display(value) { return typeof value === 'string' ? value : JSON.stringify(value) ?? '（无值）'; }
    /** @param {AgentDiffState} state */
    function stateText(state) { return state.present ? display(state.value) : '（不存在）'; }

    /** @param {string} before @param {string} after */
    function inlineDiff(before, after) {
        let start = 0, end = 0;
        const old = Array.from(before), next = Array.from(after);
        while (start < old.length && start < next.length && old[start] === next[start]) start++;
        while (end < old.length - start && end < next.length - start && old[old.length - 1 - end] === next[next.length - 1 - end]) end++;
        const removed = old.slice(start, old.length - end).join('');
        const added = next.slice(start, next.length - end).join('');
        return safe(old.slice(0, start).join('')) + (removed ? `<del>${safe(removed)}</del>` : '')
            + (added ? `<ins>${safe(added)}</ins>` : '') + safe(end ? old.slice(-end).join('') : '');
    }

    /** @param {AgentDiffOperation} operation @returns {AgentDiff[]} */
    function deltas(operation) {
        const key = (/** @type {any} */ entry) => JSON.stringify([entry.entity, entry.id, entry.path]);
        const valid = (/** @type {any} */ entry, /** @type {string} */ side) => entry && typeof entry.entity === 'string'
            && identity(entry.id) && Array.isArray(entry.path) && entry.path.every((/** @type {any} */ p) => typeof p === 'string')
            && entry[side] && typeof entry[side].present === 'boolean';
        if (!Array.isArray(operation.before) || !Array.isArray(operation.after)) throw new Error('日志差异不可读取');
        const after = new Map(operation.after.map(entry => {
            if (!valid(entry, 'after')) throw new Error('日志差异不可读取');
            return [key(entry), entry];
        }));
        if (after.size !== operation.after.length || operation.before.length !== after.size) throw new Error('日志差异不完整');
        const seen = new Set();
        return operation.before.map(entry => {
            if (!valid(entry, 'before')) throw new Error('日志差异不可读取');
            const id = key(entry), next = after.get(id);
            if (!next || seen.has(id)) throw new Error('日志差异不完整');
            seen.add(id);
            return {entity: entry.entity, id: entry.id, path: entry.path, before: entry.before, after: next.after};
        });
    }

    /** @param {AgentDiff} change @returns {keyof typeof labels} */
    function category(change) {
        if (change.entity === 'node') {
            if (!change.path.length && !change.before.present && change.after.present) return change.after.value?.type === 'smart-group' ? 'frames' : 'added';
            if (change.path.some(part => /^(text|prompt|base_prompt|overlay_prompt|promptVariants|promptSegments)$/.test(part))) return 'prompts';
            if (change.path.length === 1 && change.path[0] === 'runSettings'
                && [change.before, change.after].some(side => side.present && side.value && Object.hasOwn(side.value, 'prompt'))) return 'prompts';
            if (change.path.length === 1 && ['x', 'y'].includes(change.path[0])) return 'moves';
            if (['agentStatus', 'lastFailure'].includes(change.path[0])) return 'generation';
        }
        return 'other';
    }

    /** @param {AgentDiffOperation[]} operations */
    function aggregate(operations) {
        /** @type {Record<keyof typeof labels, {op: AgentDiffOperation, change: AgentDiff}[]>} */
        const groups = {added: [], frames: [], prompts: [], moves: [], generation: [], other: []};
        for (const op of operations) for (const change of deltas(op)) groups[category(change)].push({op, change});
        return groups;
    }

    /** @param {string} nodeId @param {any} state @param {AgentCompletionCard} card */
    function capture(nodeId, state, card) {
        const project = state.projectId, host = state.viewHost, epoch = state.viewEpoch, generation = state._timelineGeneration || 0;
        return {project, current: () => Boolean(project && deps.getProjectId?.() === project && deps.getView?.(nodeId) === state
            && deps.hasNode?.(nodeId) && !state.closing && state.projectId === project && state.viewHost === host
            && state.viewEpoch === epoch && (state._timelineGeneration || 0) === generation
            && state.messages.some((/** @type {any} */ message) => message.completion === card))};
    }

    function publishMarks() {
        const project = deps.getProjectId?.();
        if (!project) return;
        /** @type {any[]} */
        const marks = [];
        for (const [, state] of deps.getViews?.() || []) {
            if (state.projectId !== project || state.viewHost !== state.activeHost || state.closing) continue;
            for (const message of state.messages) {
                const card = message.completion;
                if (!card || card.phase !== 'ready') continue;
                // ops.list is newest-first; the painter keeps the last mark for a node.
                for (const op of [...card.operations].reverse()) if (!op.undone) for (const change of deltas(op)) {
                    if (change.entity === 'node' && change.after.present) marks.push({node_id: change.id,
                        kind: !change.path.length && !change.before.present ? 'added' : 'modified', op_id: op.op_id, turn_id: card.turnId});
                }
            }
        }
        deps.marks?.(project, marks);
    }

    /** @param {string} nodeId @param {any} state @param {AgentCompletionCard} card */
    async function load(nodeId, state, card) {
        if (!card.turnId) return;
        const context = capture(nodeId, state, card);
        if (!context.current()) return;
        const request = ++card.request;
        loads.set(card, context.current);
        card.phase = 'loading';
        try {
            const response = await fetch(`/api/projects/${encodeURIComponent(context.project)}/ops?turn_id=${encodeURIComponent(card.turnId)}&limit=1000`);
            const data = await response.json();
            if (!context.current() || request !== card.request) return;
            if (!response.ok || !Array.isArray(data.operations)) throw new Error('无法读取保留的操作日志');
            card.capped = data.operations.length >= 1000;
            card.foreign = data.operations.some((/** @type {AgentDiffOperation} */ op) => op.agent_node_id !== nodeId || op.turn_id !== card.turnId);
            card.operations = data.operations.filter((/** @type {AgentDiffOperation} */ op) => op.agent_node_id === nodeId && op.turn_id === card.turnId);
            if (new Set(card.operations.map(op => op.op_id)).size !== card.operations.length) throw new Error('操作身份重复');
            for (const op of card.operations) {
                if (!identity(op.op_id) || !Array.isArray(op.irreversible_effects) || typeof op.undone !== 'boolean') throw new Error('日志格式不可读取');
                deltas(op);
            }
            card.phase = 'ready';
        } catch (error) {
            if (!context.current() || request !== card.request) return;
            card.phase = 'error';
            card.operations = [];
            card.notice = `读取失败：${error instanceof Error ? error.message : String(error)}。不能据此判断本轮无改动。`;
        }
        if (!context.current() || request !== card.request) return;
        publishMarks();
        deps.refreshView?.(nodeId);
    }

    /** @param {string} nodeId @param {any} state @param {any} event */
    function complete(nodeId, state, event) {
        if (event.type !== 'agent_end' || event.willRetry === true || (event.host || 'pi') !== state.viewHost
            || (event.node_id && event.node_id !== nodeId) || (event.project_id && event.project_id !== state.projectId)) return;
        const turnId = identity(event.turn_id);
        let card = turnId && state.messages.find((/** @type {any} */ message) => message.completion?.turnId === turnId)?.completion;
        if (!card) {
            /** @type {AgentCompletionCard} */
            const next = {turnId, operations: [], phase: turnId ? 'idle' : 'legacy', notice: '', capped: false, foreign: false,
                request: 0, expanded: new Set(), busy: false};
            card = next;
            deps.append?.(state, {role: 'completion', text: '', completion: card});
        }
        // Older-page reducers have no project context. They may create rows, never issue effects.
        if (state.projectId) void load(nodeId, state, card);
    }

    /** @param {any} state @param {AgentCompletionCard} card */
    function canWrite(state, card) {
        return state.viewHost === state.activeHost && !state._applyingReplay && !state._batchHistory && !state.composing
            && !deps.locked?.(state) && !writing.has(state.projectId) && card.phase === 'ready' && !card.busy;
    }

    /** @param {string} nodeId @param {any} state @param {AgentCompletionCard} card @param {string | null} opId */
    async function undo(nodeId, state, card, opId) {
        const context = capture(nodeId, state, card);
        if (!context.current() || !canWrite(state, card)) return;
        const op = opId ? card.operations.find(item => item.op_id === opId && !item.undone) : null;
        if (opId ? !op : !card.turnId || card.capped || card.foreign || !card.operations.some(item => !item.undone)) return;
        const text = (op ? '撤销此操作及其全部后代（包括子操作），不是只撤销显示的一个节点或字段。' : '撤销这个真实逻辑轮的全部保留操作。')
            + '\n将逆转相应日志改动，包括删除这些操作新建的节点 / 组框；后代范围以服务器检查为准。'
            + '\n后续人工改动或引用冲突时服务器将拒绝，不会强制覆盖。生成可能尚未结束。'
            + '\n付费调用、外部效果不可撤回或退费。' + generatedMediaNotice
            + '\n若操作包含 media.delete，恢复仍依赖未过期、未清空的原回收条目。'
            + '\n日志不是备份，可能缺失或过期；这与本地 Ctrl+Z 独立。确认继续？';
        writing.add(context.project);
        card.busy = true;
        try {
            const approved = await deps.ask?.(text);
            if (!approved || !context.current() || state.viewHost !== state.activeHost || state.composing || deps.locked?.(state)) return;
            const payload = opId ? {op_id: opId} : {turn_id: card.turnId};
            const response = await fetch(`/api/projects/${encodeURIComponent(context.project)}/ops/undo`, {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-Agent-Project': context.project}, body: JSON.stringify(payload)});
            const result = await response.json();
            if (!context.current()) return;
            if (!response.ok) {
                const detail = result.detail || result;
                const conflicts = detail.details?.conflicts;
                card.notice = `${response.status === 409 ? '撤销未执行（冲突或生成进行中）' : response.status === 404 ? '操作日志已不可用，不能撤销' : '撤销结果未确认，请核对画布后再决定是否重试'}：${display(detail.message || detail.code || `HTTP ${response.status}`)}`
                    + (Array.isArray(conflicts) && conflicts.length ? `\n冲突：${display(conflicts)}` : '');
                return;
            }
            if (!['reverted', 'already_reverted'].includes(result.status) || result.error !== null
                || !Array.isArray(result.reverted) || !Array.isArray(result.already_reverted) || !Array.isArray(result.irreversible_effects)
                || !Array.isArray(result.conflicts) || result.conflicts.length) {
                card.notice = '撤销结果未确认；未报告成功，请核对画布和操作日志。';
                return;
            }
            card.notice = result.status === 'already_reverted' ? '服务器确认操作此前已撤销。' : '服务器已接受撤销。';
            if (Array.isArray(result.irreversible_effects) && result.irreversible_effects.length) card.notice += `\n未撤回的外部效果（不退费）：${display(result.irreversible_effects)}`;
            const merged = await deps.reload?.(context.project);
            if (!context.current()) return;
            card.notice += merged ? '\n已接收权威画布，保留无关的本地编辑。' : '\n权威画布刷新未确认；请刷新核对，不要据此宣称已恢复。';
            // An op may own descendants in another displayed card. Re-query every loaded logical turn.
            const cards = state.messages.map((/** @type {any} */ message) => message.completion).filter(Boolean);
            await Promise.all(cards.map((/** @type {AgentCompletionCard} */ item) => load(nodeId, state, item)));
        } catch (error) {
            if (context.current()) card.notice = `撤销结果未确认：${error instanceof Error ? error.message : String(error)}。请核对后再决定是否重试。`;
        } finally {
            writing.delete(context.project);
            card.busy = false;
            if (context.current()) deps.refreshView?.(nodeId);
        }
    }

    /** @param {AgentCompletionCard} card @param {any} state */
    function html(card, state) {
        const disabled = canWrite(state, card) ? '' : ' disabled';
        let body = '';
        if (card.phase === 'legacy') body = '<p>旧历史缺少真实逻辑轮 ID，无法关联差异或撤销；不会推测轮次。</p>';
        else if (card.phase === 'idle' || card.phase === 'loading') body = '<p>正在读取保留的操作日志…</p>';
        else if (card.phase === 'ready') {
            if (!card.operations.length) body = '<p>未查到本 Agent 本轮的保留日志；不能证明没有改动，也不能据此安全撤销。</p>';
            const groups = aggregate(card.operations);
            for (const name of /** @type {(keyof typeof labels)[]} */ (Object.keys(labels))) {
                const rows = groups[name];
                if (!rows.length) continue;
                const count = new Set(rows.map(row => `${row.change.entity}:${row.change.id}`)).size;
                body += `<details data-completion-group="${name}"${card.expanded.has(name) ? ' open' : ''}><summary>${labels[name]} ${count}${card.capped ? '（已加载）' : ''}</summary>`;
                for (const {op, change} of rows) {
                    const diff = name === 'prompts' ? inlineDiff(stateText(change.before), stateText(change.after))
                        : `${safe(stateText(change.before))} → ${safe(stateText(change.after))}`;
                    body += `<div class="agent-completion-delta" data-completion-op="${safe(op.op_id)}"><b>${safe(change.id)}</b> <span>${safe(change.path.join('.') || '整个实体')} · ${safe(op.op_id)}${op.undone ? ' · 已撤销' : ''}</span><div>${diff}</div></div>`;
                }
                body += '</details>';
            }
            body += '<p>上方仅展示日志中的状态证据；准入、响应结束均不代表生成完成。生成可能仍在进行，请刷新记录查看后续改动。</p>';
            if (card.capped) body += '<p>已达 1000 条读取上限，无分页；分类数可能不全，不提供完整本轮撤销入口。</p>';
            if (card.foreign) body += '<p>日志含其他 Agent 或轮次，仅展示本 Agent 的匹配操作；本轮撤销不可用。</p>';
            for (const op of card.operations) body += `<div class="agent-completion-operation"><span>${safe(op.tool)} · ${safe(op.op_id)}${op.undone ? ' · 已撤销' : ''}</span>`
                + `<button type="button" data-completion-undo="${safe(op.op_id)}"${op.undone ? ' disabled' : disabled}>撤销操作及全部后代</button>`
                + (op.irreversible_effects.length ? `<p>不可撤回的效果：${safe(display(op.irreversible_effects))}</p>` : '') + '</div>';
            if (card.operations.some(op => !op.undone) && !card.capped && !card.foreign) body += `<button type="button" data-completion-turn${disabled}>撤销本轮</button>`;
        }
        body += '<p class="agent-completion-limits">按保留日志分类，非净变化或完整备份；历史可能缺失 / 过期。撤销将删除所选操作新建的节点 / 组框；'
            + generatedMediaNotice + '撤销粒度是操作及其全部后代，不是单节点 / 字段；外部调用不退费。</p>';
        return `<section class="agent-completion" data-agent-completion="${safe(card.turnId || '')}" aria-label="响应结束的改动记录"><header>响应结束 · 改动记录</header>${body}`
            + `<p role="status">${safe(card.notice)}</p>${card.turnId ? '<button type="button" data-completion-refresh>刷新记录</button>' : ''}</section>`;
    }

    /** @param {HTMLElement} root @param {string} nodeId @param {any} state */
    function bind(root, nodeId, state) {
        if (revealTarget && !revealTarget.current()) revealTarget = null;
        root.querySelectorAll('[data-agent-completion]').forEach(section => {
            const card = state.messages.find((/** @type {any} */ message) => message.completion
                && (message.completion.turnId || '') === section.getAttribute('data-agent-completion'))?.completion;
            if (!card) return;
            for (const type of ['mousedown', 'pointerdown', 'dblclick']) section.addEventListener(type, event => event.stopPropagation());
            section.addEventListener('click', event => event.stopPropagation());
            section.querySelectorAll('[data-completion-group]').forEach(detail => detail.addEventListener('toggle', () => {
                const name = detail.getAttribute('data-completion-group');
                if (/** @type {HTMLDetailsElement} */ (detail).open) card.expanded.add(name); else card.expanded.delete(name);
            }));
            section.querySelectorAll('[data-completion-undo], [data-completion-turn], [data-completion-refresh]').forEach(button => button.addEventListener('click', event => {
                event.stopPropagation();
                if (state.composing) return;
                if (button.hasAttribute('data-completion-refresh')) void load(nodeId, state, card);
                else void undo(nodeId, state, card, button.getAttribute('data-completion-undo'));
            }));
            if (card.phase === 'idle' || (card.phase === 'loading' && !loads.get(card)?.())) void load(nodeId, state, card);
            if (revealTarget?.nodeId === nodeId && card.turnId === revealTarget.turnId && !state.composing) {
                const target = Array.from(section.querySelectorAll('[data-completion-op]')).find(row => row.getAttribute('data-completion-op') === revealTarget?.opId);
                if (target) {
                    const body = root.querySelector('[data-agent-tui-body]');
                    if (body) deps.revealRow?.(state, /** @type {HTMLElement} */ (body), target);
                    revealTarget = null;
                }
            }
        });
        publishMarks();
    }

    /** @param {any} detail */
    function reveal(detail) {
        if (!detail || detail.project_id !== deps.getProjectId?.() || !identity(detail.op_id)) return;
        for (const [nodeId, state] of deps.getViews?.() || []) {
            if (state.projectId !== detail.project_id || state.viewHost !== state.activeHost || state.closing) continue;
            const card = state.messages.find((/** @type {any} */ message) => message.completion?.phase === 'ready'
                && message.completion.turnId === detail.turn_id && message.completion.operations.some((/** @type {AgentDiffOperation} */ op) => op.op_id === detail.op_id
                    && deltas(op).some(change => change.entity === 'node' && change.id === detail.node_id)))?.completion;
            if (!card) continue;
            const op = card.operations.find((/** @type {AgentDiffOperation} */ item) => item.op_id === detail.op_id);
            for (const change of deltas(op)) card.expanded.add(category(change));
            const context = capture(nodeId, state, card);
            if (!context.current()) return;
            revealTarget = {nodeId, opId: detail.op_id, turnId: card.turnId, current: context.current};
            deps.refreshView?.(nodeId);
            return;
        }
    }

    /** @param {Partial<AgentCompletionDependencies>} next */
    function configure(next) {
        deps = {...deps, ...next};
        if (!listening) {
            window.addEventListener('studio-canvas-agent-diff-reveal', event => reveal(/** @type {CustomEvent} */ (event).detail));
            listening = true;
        }
    }
    function invalidate() {
        if (revealTarget && !revealTarget.current()) revealTarget = null;
        publishMarks();
    }
    return {configure, complete, html, bind, load, undo, inlineDiff, deltas, aggregate, reveal,
        fingerprint: (/** @type {AgentCompletionCard} */ card) => [card.turnId, card.phase, card.operations, card.notice], invalidate};
})();
