// @ts-check

/** @typedef {{id: string, type?: string, dock?: string | null, agentCli?: string, agentStatus?: string, lastFailure?: {message?: string, hint?: string, code?: string}, running?: boolean, pending?: boolean, status?: string, error?: string}} CanvasActivityNode */
/** @typedef {{node_id: string, kind: 'added' | 'modified', op_id: string, turn_id?: string | null}} CanvasActivityDiffMark */
/** @typedef {{getProjectId: () => string, getNodes: () => CanvasActivityNode[], getElements: () => Iterable<HTMLElement>, focusTargets: (ids: string[]) => void, isTaskNode?: (node: CanvasActivityNode) => boolean, followButton?: HTMLElement | null}} CanvasActivityDependencies */

var canvasAgentActivity = (function () {
    /** @type {CanvasActivityDependencies | null} */
    let dependencies = null;
    let projectId = '';
    let following = false;
    /** @type {Map<string, {targets: Set<string>, ending: boolean, followPending: boolean, timer: number | null}>} */
    const operations = new Map();
    /** @type {Map<string, {actor: string, host: string, targets: string[], resolved: boolean, seq: number}>} */
    const confirmations = new Map();
    /** @type {Map<string, CanvasActivityDiffMark>} */
    const diffMarks = new Map();
    /** @type {WeakMap<HTMLElement, {root: HTMLElement, label: HTMLElement, text: HTMLElement, info: SVGSVGElement, mark: HTMLButtonElement}>} */
    const presentations = new WeakMap();
    /** @type {WeakSet<HTMLElement>} */
    const boundButtons = new WeakSet();

    /** @param {unknown} value @returns {string[]} */
    function ids(value) {
        return Array.isArray(value) ? [...new Set(value.filter(item => typeof item === 'string' && item.trim()))] : [];
    }

    function updateFollowButton() {
        const button = dependencies?.followButton;
        if (!button) return;
        button.setAttribute('aria-pressed', String(following));
        button.setAttribute('aria-label', `跟随 Agent 视角：${following ? '开' : '关'}`);
        button.setAttribute('title', `跟随 Agent 视角：${following ? '开' : '关'}（仅跟随新的操作）`);
    }

    function currentProject() {
        const next = dependencies?.getProjectId() || '';
        if (next !== projectId) {
            operations.forEach(operation => { if (operation.timer !== null) clearTimeout(operation.timer); });
            operations.clear();
            confirmations.clear();
            diffMarks.clear();
            following = false;
            projectId = next;
            updateFollowButton();
        }
        return projectId;
    }

    /** @param {HTMLElement} element */
    function presentation(element) {
        let parts = presentations.get(element);
        if (parts) return parts;
        const root = document.createElement('span');
        root.className = 'canvas-agent-activity';
        const edge = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        edge.setAttribute('class', 'canvas-agent-activity-edge');
        edge.setAttribute('aria-hidden', 'true');
        for (const name of ['status', 'flow', 'modifying']) {
            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('class', `canvas-agent-activity-${name}`);
            rect.setAttribute('x', '0');
            rect.setAttribute('y', '0');
            rect.setAttribute('width', '100%');
            rect.setAttribute('height', '100%');
            rect.setAttribute('rx', '10');
            rect.setAttribute('pathLength', '100');
            rect.setAttribute('vector-effect', 'non-scaling-stroke');
            edge.appendChild(rect);
        }
        const label = document.createElement('span');
        label.className = 'canvas-agent-activity-label';
        const info = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        info.setAttribute('class', 'ico');
        info.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', '#i-info');
        info.appendChild(use);
        const text = document.createElement('span');
        label.appendChild(info);
        label.appendChild(text);
        const mark = document.createElement('button');
        mark.type = 'button';
        mark.className = 'canvas-agent-diff-mark';
        // Decoration never replaces node bodies: IME, selection and reading anchors remain owned by the TUI.
        for (const type of ['pointerdown', 'mousedown', 'dblclick']) {
            mark.addEventListener(type, event => { event.stopPropagation(); if (type !== 'dblclick') event.preventDefault(); });
        }
        mark.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            if (!currentProject()) return;
            const selected = diffMarks.get(element.dataset.id || '');
            if (selected) window.dispatchEvent(new CustomEvent('studio-canvas-agent-diff-reveal', {
                detail: {project_id: projectId, ...selected},
            }));
        });
        root.appendChild(edge);
        root.appendChild(label);
        root.appendChild(mark);
        element.appendChild(root);
        parts = {root, label, text, info, mark};
        presentations.set(element, parts);
        return parts;
    }

    function sync() {
        currentProject();
        if (!dependencies) return;
        const nodes = new Map(dependencies.getNodes().map(node => [node.id, node]));
        // A creation can announce start before its node exists; only that opt-in start authorizes a later focus.
        operations.forEach(operation => {
            if (!operation.followPending || !following) return;
            const available = [...operation.targets].filter(id => {
                const node = nodes.get(id);
                return node && node.dock !== 'left' && node.dock !== 'right';
            });
            if (available.length) {
                operation.followPending = false;
                dependencies?.focusTargets(available);
            }
        });
        // Host authority, not the currently browsed transcript, owns pending confirmations.
        confirmations.forEach((confirmation, key) => {
            const actor = nodes.get(confirmation.actor);
            if (!actor || (actor.agentCli || 'pi') !== confirmation.host) confirmations.delete(key);
        });
        const modifying = new Set();
        const active = new Set();
        operations.forEach(operation => operation.targets.forEach(id => {
            modifying.add(id);
            if (!operation.ending) active.add(id);
        }));
        const pending = new Set();
        confirmations.forEach(confirmation => {
            if (!confirmation.resolved) confirmation.targets.forEach(id => pending.add(id));
        });
        for (const element of dependencies.getElements()) {
            const id = element.dataset.id || '';
            const node = nodes.get(id);
            const task = Boolean(node && dependencies.isTaskNode?.(node));
            const failed = node?.agentStatus === 'failed' || (task && node?.status === 'failed');
            const generating = Boolean(node && (node.agentStatus === 'generating' || node.running || node.pending || (task && node.status === 'running')));
            const changed = modifying.has(id);
            const waiting = pending.has(id);
            const mark = diffMarks.get(id);
            let parts = presentations.get(element);
            if (!parts && !failed && !generating && !changed && !waiting && !mark) continue;
            parts = parts || presentation(element);
            parts.root.hidden = !node || !(failed || generating || changed || waiting || mark);
            parts.root.classList.toggle('is-modifying', changed);
            parts.root.classList.toggle('is-ending', changed && !active.has(id));
            parts.root.classList.toggle('is-generating', generating);
            parts.root.classList.toggle('is-failed', failed);
            parts.root.classList.toggle('is-confirming', waiting);
            const text = failed ? '失败' : waiting ? '待确认' : changed ? '修改中' : '';
            if (parts.text.textContent !== text) parts.text.textContent = text;
            parts.info.style.display = waiting && !failed ? '' : 'none';
            parts.label.hidden = !text;
            parts.label.setAttribute('title', failed ? (node?.lastFailure?.message || node?.lastFailure?.hint || node?.lastFailure?.code || (task && node?.error) || '生成失败，原因未提供') : text);
            parts.mark.hidden = !mark;
            if (mark) {
                parts.mark.textContent = mark.kind === 'added' ? '+' : '~';
                parts.mark.setAttribute('aria-label', `${mark.kind === 'added' ? '新增' : '修改'}节点：查看改动前后对比`);
                parts.mark.setAttribute('title', '查看改动前后对比');
            }
        }
    }

    /** @param {CanvasActivityDependencies} callbacks */
    function configure(callbacks) {
        dependencies = callbacks;
        currentProject();
        const button = callbacks.followButton;
        if (button && !boundButtons.has(button)) {
            boundButtons.add(button);
            button.addEventListener('click', event => {
                event.stopPropagation();
                setFollowing(!following);
            });
            for (const type of ['pointerdown', 'mousedown']) button.addEventListener(type, event => event.stopPropagation());
        }
        updateFollowButton();
    }

    /** @param {boolean} enabled */
    function setFollowing(enabled) {
        currentProject();
        following = Boolean(enabled && projectId);
        if (!following) operations.forEach(operation => { operation.followPending = false; });
        updateFollowButton();
    }

    /**
     * End is only a lifecycle boundary. Success, failure and diffs must come from authority.
     * @param {{type?: string, canvas_id?: string, source?: string, phase?: string, op_id?: string, touched_node_ids?: unknown}} data
     * @param {{replay?: boolean}} [options]
     */
    function consumeCanvasUpdate(data, options = {}) {
        if (!currentProject() || data.canvas_id !== projectId || data.source !== 'agent' || data.type !== 'canvas_updated') return;
        const targets = ids(data.touched_node_ids);
        const opId = typeof data.op_id === 'string' ? data.op_id : '';
        if (data.phase === 'start' && opId) {
            const existing = operations.get(opId);
            if (!existing) {
                operations.set(opId, {targets: new Set(targets), ending: false,
                    followPending: following && !options.replay && targets.length > 0, timer: null});
            } else if (!existing.ending) {
                targets.forEach(id => existing.targets.add(id));
            }
        } else if (data.phase === 'end' && opId) {
            const operation = operations.get(opId);
            if (operation && !operation.ending) {
                targets.forEach(id => operation.targets.add(id));
                operation.ending = true;
                operation.timer = window.setTimeout(() => { operations.delete(opId); sync(); }, 1200);
            }
        } else if (data.phase === 'undo') {
            const operation = operations.get(opId);
            if (operation) {
                if (operation.timer !== null) clearTimeout(operation.timer);
                operations.delete(opId);
            }
            diffMarks.forEach((mark, id) => {
                if (targets.includes(id) || (opId && mark.op_id === opId)) diffMarks.delete(id);
            });
        } else return;
        sync();
    }

    /**
     * Latest-page replay can restore pending targets, but older/read-only pages cannot resurrect them.
     * @param {{project_id: string, node_id: string, host: string, event: {type: string, confirm_id?: string, target_node_ids?: unknown, seq?: number}}} input
     */
    function consumeConfirmation({project_id, node_id, host, event}) {
        if (!currentProject() || project_id !== projectId || !event.confirm_id || !['confirm', 'confirm_resolved'].includes(event.type)) return;
        const key = JSON.stringify([node_id, host, event.confirm_id]);
        const prior = confirmations.get(key);
        const seq = Number(event.seq) || 0;
        if (prior && (prior.seq > seq || (prior.resolved && event.type === 'confirm'))) return;
        confirmations.set(key, {actor: node_id, host, targets: ids(event.target_node_ids), resolved: event.type === 'confirm_resolved', seq});
        sync();
    }

    /** Authoritative D7 op-derived marks replace the selected diff view; phase:end never creates marks.
     * @param {string} scope @param {readonly CanvasActivityDiffMark[]} marks @returns {boolean}
     */
    function setDiffMarks(scope, marks) {
        if (!currentProject() || scope !== projectId || !Array.isArray(marks)) return false;
        diffMarks.clear();
        for (const mark of marks) {
            if (!mark || typeof mark.node_id !== 'string' || !mark.node_id || typeof mark.op_id !== 'string' || !mark.op_id
                || !['added', 'modified'].includes(mark.kind) || (mark.turn_id != null && typeof mark.turn_id !== 'string')) continue;
            diffMarks.set(mark.node_id, {node_id: mark.node_id, kind: mark.kind, op_id: mark.op_id,
                ...(mark.turn_id !== undefined ? {turn_id: mark.turn_id} : {})});
        }
        sync();
        return true;
    }

    return {configure, sync, consumeCanvasUpdate, consumeConfirmation, setFollowing, setDiffMarks};
})();
