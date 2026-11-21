// @ts-check

/** @typedef {{getProjectId: () => string, getOwner: (id: string) => any, hasNode: (id: string) => boolean, getComposer: (id: string) => HTMLTextAreaElement | null}} AgentChatDependencies */

var canvasAgentChat = (function () {
    /** @type {Partial<AgentChatDependencies>} */
    let deps = {};
    /** @type {any} */
    let active = null;
    /** @type {Map<string, {buttons: Element[], destroy: () => void}>} */
    const bindings = new Map();
    /** @type {Map<string, string>} */
    const drafts = new Map();
    const pageSize = 50, maximum = 200;

    /** @param {string} tag @param {string} [className] @param {string} [text] */
    function element(tag, className = '', text = '') {
        const el = document.createElement(tag);
        el.className = className;
        el.textContent = text;
        return el;
    }
    /** @param {string} text @param {() => void} run */
    function button(text, run) {
        const el = document.createElement('button');
        el.type = 'button';
        el.textContent = text;
        el.addEventListener('click', event => {
            // Closing destroys the portal's bubbling guards, but must not leak its click to canvas document handlers.
            event.preventDefault();
            event.stopPropagation();
            run();
        });
        return el;
    }
    /** @param {unknown} value */
    function positive(value) { return Number.isSafeInteger(value) && Number(value) > 0; }
    /** @param {any} row */
    function stream(row) { return row && typeof row.node_id === 'string' && row.node_id.length > 0 && ['pi', 'codex'].includes(row.host); }
    /** @param {any} row @param {boolean} hit */
    function message(row, hit) {
        return stream(row) && positive(row.seq) && row.message_id === `${row.node_id}:${row.host}:${row.seq}`
            && (row.turn_id === null || (typeof row.turn_id === 'string' && row.turn_id.trim().length > 0))
            && ['user', 'assistant'].includes(row.role) && typeof row[hit ? 'snippet' : 'text'] === 'string'
            && Number.isFinite(row.timestamp) && row.timestamp >= 0 && typeof row.text_truncated === 'boolean';
    }
    /** @param {any} session */
    function ownerCurrent(session) {
        const owner = deps.getOwner?.(session.ownerId);
        return Boolean(owner && owner === session.owner && !owner.closing && deps.getProjectId?.() === session.project
            && owner.viewHost === session.host && owner.activeHost === session.activeHost && owner.viewEpoch === session.epoch
            && (owner._timelineGeneration || 0) === session.generation);
    }
    /** @param {any} session @param {number} serial */
    function current(session, serial) {
        if (active !== session || session.serial !== serial) return false;
        if (!ownerCurrent(session)) { close(false); return false; }
        return true;
    }
    /** @param {any} session */
    function begin(session) {
        session.abort?.abort();
        session.abort = typeof AbortController === 'function' ? new AbortController() : null;
        return ++session.serial;
    }
    /** @param {any} session @param {string} route @param {Record<string, string>} params */
    async function request(session, route, params) {
        const query = new URLSearchParams(params);
        const response = await fetch(`/api/projects/${encodeURIComponent(session.project)}/chat/${route}?${query}`, {
            method: 'GET', headers: {'X-Agent-Project': session.project}, signal: session.abort?.signal,
        });
        const data = await response.json();
        if (!response.ok) {
            const detail = data?.detail || data?.error || data;
            const reason = typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : `HTTP ${response.status}`;
            throw new Error(reason);
        }
        if (data?.project_id !== session.project || data.scope !== params.scope || data.node_id !== (params.node_id || null)
            || data.host !== (params.host || null) || typeof data.has_more !== 'boolean' || typeof data.truncated !== 'boolean') {
            throw new Error('历史响应的项目 / 节点 / 宿主身份不匹配');
        }
        return data;
    }
    /** @param {any} row */
    function time(row) { return row.timestamp ? new Date(row.timestamp * 1000).toLocaleString() : '时间未知'; }
    /** @param {any} row */
    function turn(row) { return row.turn_id === null ? '旧历史：逻辑轮未知' : `逻辑轮 ${row.turn_id}`; }
    /** @param {any} session @param {string} text @param {boolean} [error] */
    function status(session, text, error = false) {
        session.status.textContent = text;
        session.status.setAttribute('role', error ? 'alert' : 'status');
    }
    /** @param {any} session @param {boolean} hasMore @param {boolean} truncated */
    function bounds(session, hasMore, truncated) {
        const note = element('p', 'agent-chat-notice', '仅本画布保留的用户 / 助手正文；不是完整会话备份。');
        if (truncated) note.textContent += ' 读取达到扫描上限或部分日志不可读，未扫描历史可能仍有结果。';
        if (hasMore) note.textContent += ' 当前结果达到条数上限。';
        session.content.appendChild(note);
    }
    /** @param {any} session */
    function renderList(session) {
        session.content.setAttribute('aria-busy', 'false');
        session.content.replaceChildren();
        bounds(session, session.hasMore, session.truncated);
        for (const row of session.rows) {
            const isHit = session.mode === 'search';
            const retired = isHit ? !deps.hasNode?.(row.node_id) : row.retired;
            const item = button('', () => { void openThread(session, row, isHit); });
            item.className = 'agent-chat-result';
            item.appendChild(element('strong', '', isHit ? `${row.role === 'user' ? '你' : '助手'} · ${row.node_id}` : row.title));
            item.appendChild(element('span', 'agent-chat-meta', `${row.host === 'pi' ? 'Pi' : 'Codex'} · ${retired ? '已删除 Agent · 只读' : '只读'} · ${time(row)}`));
            item.appendChild(element('span', 'agent-chat-snippet', row.snippet));
            item.appendChild(element('small', 'agent-chat-meta', isHit
                ? `${row.message_id} · seq ${row.seq} · ${turn(row)}${row.text_truncated ? ' · 正文已截断' : ''}`
                : `${row.node_id} · 本次扫描保留 ${row.message_count} 条消息`));
            session.content.appendChild(item);
        }
        if (!session.rows.length) session.content.appendChild(element('p', 'agent-chat-empty', session.mode === 'search'
            ? '本次保留扫描中没有匹配消息。' : '本次保留扫描中没有历史会话。'));
        if (session.hasMore) session.content.appendChild(session.limit < maximum
            ? button('读取最多 200 条结果', () => { void loadList(session, session.mode, maximum); })
            : element('p', 'agent-chat-notice', '已达 200 条结果上限；请用更具体的关键词搜索。'));
    }
    /** @param {any} session @param {string} mode @param {number} [limit] */
    async function loadList(session, mode, limit = pageSize) {
        if (active !== session || !ownerCurrent(session) || session.composing) return;
        const raw = session.input.value;
        if (mode === 'search' && (!raw.trim() || Array.from(raw).length > maximum)) {
            begin(session);
            session.content.setAttribute('aria-busy', 'false');
            status(session, '请输入 1–200 字的关键词。', true); return;
        }
        const serial = begin(session);
        session.thread = null;
        session.mode = mode;
        session.limit = limit;
        session.query = raw.trim();
        session.rows = [];
        session.content.replaceChildren();
        status(session, mode === 'search' ? '正在搜索保留消息…' : '正在读取本画布历史会话…');
        session.content.setAttribute('aria-busy', 'true');
        try {
            const data = await request(session, mode === 'search' ? 'search' : 'history', {
                scope: 'canvas', limit: String(limit), ...(mode === 'search' ? {query: session.query} : {}),
            });
            if (!current(session, serial)) return;
            const rows = mode === 'search' ? data.hits : data.conversations;
            if (!Array.isArray(rows) || rows.length > limit || (mode === 'search' && data.query !== session.query)
                || rows.some(row => mode === 'search' ? !message(row, true) : !stream(row)
                    || row.conversation_id !== `${row.node_id}:${row.host}` || !positive(row.last_seq)
                    || row.last_message_id !== `${row.node_id}:${row.host}:${row.last_seq}` || typeof row.title !== 'string'
                    || typeof row.snippet !== 'string' || typeof row.retired !== 'boolean' || !positive(row.message_count)
                    || !Number.isFinite(row.timestamp) || row.timestamp < 0)
                || new Set(rows.map(row => mode === 'search' ? row.message_id : row.conversation_id)).size !== rows.length) {
                throw new Error('历史结果格式或消息身份不可核验');
            }
            session.rows = rows;
            session.hasMore = data.has_more;
            session.truncated = data.truncated;
            status(session, mode === 'search' ? `关键词「${session.query}」· 本次返回 ${rows.length} 条消息` : `本画布 · 本次返回 ${rows.length} 个历史会话`);
            renderList(session);
        } catch (error) {
            if (!current(session, serial)) return;
            status(session, `读取失败：${error instanceof Error ? error.message : String(error)}。不能据此判断没有历史。`, true);
            session.content.appendChild(button('重试', () => { void loadList(session, mode, limit); }));
        } finally {
            if (current(session, serial)) session.content.setAttribute('aria-busy', 'false');
        }
    }
    /** @param {any} session @param {any} row @param {boolean} isHit */
    async function openThread(session, row, isHit) {
        if (active !== session || !ownerCurrent(session)) return;
        session.thread = {node_id: row.node_id, host: row.host, title: isHit ? row.node_id : row.title,
            target: isHit ? row : null, messages: [], cursor: null, hasMore: false, truncated: false, unavailable: false};
        await loadThread(session, false);
    }
    /** @param {any} session @param {any} thread */
    function renderThread(session, thread) {
        session.content.replaceChildren();
        session.content.appendChild(button('返回结果', () => {
            if (active !== session || !ownerCurrent(session)) return;
            begin(session); session.thread = null;
            status(session, session.mode === 'search' ? `关键词「${session.query}」· ${session.rows.length} 条消息` : '本画布历史会话');
            renderList(session);
        }));
        session.content.appendChild(element('h3', '', `${thread.title} · ${thread.host === 'pi' ? 'Pi' : 'Codex'} · 只读`));
        session.content.appendChild(element('p', 'agent-chat-meta', `${thread.node_id}${deps.hasNode?.(thread.node_id) ? '' : ' · 已删除 Agent（不恢复节点）'}`));
        bounds(session, thread.hasMore, thread.truncated);
        if (thread.target) session.content.appendChild(element('p', 'agent-chat-notice', thread.unavailable
            ? `目标 ${thread.target.message_id} 在当前保留扫描内不可用或身份已变化，未执行跳转。可返回结果重试。`
            : `已定位消息 ${thread.target.message_id} · seq ${thread.target.seq} · ${turn(thread.target)}。显示目标及其之前的保留消息。`));
        if (thread.hasMore) session.content.appendChild(button('加载更早的消息', () => { void loadThread(session, true); }));
        const body = element('div', 'agent-chat-thread');
        body.setAttribute('role', 'region');
        body.setAttribute('aria-label', '只读会话正文');
        body.tabIndex = 0;
        // One reader scroll owner avoids nested clipping without panning canvas ancestors.
        session.scroller = session.content;
        session.content.appendChild(body);
        for (const row of thread.messages) {
            const target = !thread.unavailable && row.message_id === thread.target?.message_id;
            const article = element('article', `agent-chat-message${target ? ' agent-chat-target' : ''}`);
            article.setAttribute('data-message-id', row.message_id);
            article.setAttribute('data-message-seq', String(row.seq));
            if (row.turn_id !== null) article.setAttribute('data-turn-id', row.turn_id);
            if (target) article.setAttribute('aria-label', '已定位的消息');
            article.appendChild(element('strong', '', `${row.role === 'user' ? '你' : '助手'} · ${time(row)}`));
            article.appendChild(element('div', 'agent-chat-text', row.text));
            article.appendChild(element('small', 'agent-chat-meta', `${row.message_id} · seq ${row.seq} · ${turn(row)}${row.text_truncated ? ' · 正文已截断' : ''}`));
            body.appendChild(article);
            if (target) session.targetRow = article;
        }
        if (!thread.messages.length) body.appendChild(element('p', 'agent-chat-empty', thread.unreadable
            ? '消息尚未成功读取，不能判断是否有历史。' : '当前保留范围没有可显示消息。'));
    }
    /** @param {any} session */
    function alignTarget(session) {
        if (active !== session || !ownerCurrent(session) || !session.resizeTarget || !session.targetRow?.isConnected) return;
        const rect = session.content.getBoundingClientRect();
        session.content.scrollTop += session.targetRow.getBoundingClientRect().top - rect.top;
        session.autoScrollTop = session.content.scrollTop;
        session.readerWidth = session.content.clientWidth;
        session.readerHeight = session.content.clientHeight;
    }
    /** @param {any} session @param {boolean} older */
    async function loadThread(session, older) {
        const thread = session.thread;
        if (!thread || active !== session || !ownerCurrent(session) || (older && (!thread.hasMore || thread.loading))) return;
        const before = older ? thread.cursor : thread.target ? thread.target.seq + 1 : null;
        if (before !== null && !positive(before)) { status(session, '目标游标超出安全读取范围，未执行跳转。', true); return; }
        const serial = begin(session);
        const previousTop = session.scroller?.scrollTop || 0, previousHeight = session.scroller?.scrollHeight || 0;
        thread.loading = true;
        session.content.setAttribute('aria-busy', 'true');
        status(session, older ? '正在读取更早消息…' : '正在核验只读会话正文…');
        try {
            const data = await request(session, 'history', {scope: 'node', node_id: thread.node_id, host: thread.host,
                limit: String(pageSize), ...(before !== null ? {before_seq: String(before)} : {})});
            if (!current(session, serial) || session.thread !== thread) return;
            const rows = data.messages;
            if (!Array.isArray(rows) || rows.length > pageSize || rows.some((row, index) => !message(row, false)
                || row.node_id !== thread.node_id || row.host !== thread.host || (before !== null && row.seq >= before)
                || (index > 0 && row.seq <= rows[index - 1].seq))
                || (data.has_more ? !rows.length || data.next_before_seq !== rows[0].seq : data.next_before_seq !== null)) {
                throw new Error('会话消息或分页游标身份不可核验');
            }
            if (older && rows.some(row => thread.messages.some((/** @type {any} */ existing) => existing.message_id === row.message_id))) {
                throw new Error('分页返回重复消息，已停止读取');
            }
            thread.messages = older ? [...rows, ...thread.messages] : rows;
            thread.cursor = data.next_before_seq;
            thread.hasMore = data.has_more;
            thread.truncated = thread.truncated || data.truncated;
            if (thread.target && !older) {
                const target = rows.find(row => row.message_id === thread.target.message_id && row.seq === thread.target.seq
                    && row.turn_id === thread.target.turn_id && row.role === thread.target.role);
                thread.unavailable = !target;
                if (target) thread.target = target;
            }
            session.targetRow = null;
            renderThread(session, thread);
            if (older) session.scroller.scrollTop = previousTop + session.scroller.scrollHeight - previousHeight;
            else if (session.targetRow) {
                session.resizeTarget = true;
                session.content.scrollTop = 0;
                alignTarget(session);
            }
            status(session, thread.unavailable ? '目标消息不可用，未执行跳转。' : '只读历史 · 不切换当前宿主，不改变画布或输入草稿');
        } catch (error) {
            if (!current(session, serial) || session.thread !== thread) return;
            renderThread(session, {...thread, unreadable: true, unavailable: thread.unavailable || (!older && Boolean(thread.target))});
            status(session, `会话读取失败：${error instanceof Error ? error.message : String(error)}。未执行跳转。`, true);
            session.content.appendChild(button('重试会话读取', () => { void loadThread(session, older); }));
        } finally {
            thread.loading = false;
            if (current(session, serial)) session.content.setAttribute('aria-busy', 'false');
        }
    }
    /** @param {boolean} [restoreFocus] */
    function close(restoreFocus = true) {
        const session = active;
        if (!session) return;
        if (restoreFocus && session.composing) { session.pendingClose = true; return; }
        const focused = session.root.contains(document.activeElement);
        const valid = ownerCurrent(session);
        active = null;
        begin(session);
        drafts.set(JSON.stringify([session.project, session.ownerId]), session.input.value);
        session.destroy();
        session.root.remove();
        if (restoreFocus && focused && valid && !session.owner.composing) {
            const previous = session.previous?.isConnected ? session.previous
                : session.previousComposer ? deps.getComposer?.(session.ownerId) : bindings.get(session.ownerId)?.buttons[0];
            if (previous?.isConnected) previous.focus({preventScroll: true});
        }
    }
    /** @param {string} ownerId @param {string} [mode] */
    function open(ownerId, mode = 'search') {
        const owner = deps.getOwner?.(ownerId), project = deps.getProjectId?.();
        if (!owner || !project || owner.closing || owner.composing) return;
        if (active?.ownerId === ownerId && ownerCurrent(active)) {
            if (active.composing) return;
            if (mode === 'history') void loadList(active, 'history');
            else active.input.focus({preventScroll: true});
            return;
        }
        close(false);
        const root = element('section', 'agent-chat-panel');
        root.setAttribute('role', 'dialog');
        root.setAttribute('aria-modal', 'false');
        root.setAttribute('aria-label', '本画布对话历史搜索');
        const heading = element('div', 'agent-chat-heading');
        heading.appendChild(element('h2', '', '对话历史'));
        heading.appendChild(button('关闭', () => { if (active === session) close(); }));
        root.appendChild(heading);
        const form = document.createElement('form');
        form.className = 'agent-chat-form';
        const label = document.createElement('label');
        label.textContent = '搜索本画布对话';
        const input = document.createElement('input');
        input.type = 'search'; input.maxLength = 400; input.autocomplete = 'off';
        input.setAttribute('aria-label', '搜索本画布对话关键词（最多 200 字）');
        input.placeholder = '关键词 · 仅用户与助手正文';
        input.value = drafts.get(JSON.stringify([project, ownerId])) || '';
        label.appendChild(input);
        form.appendChild(label);
        const submit = document.createElement('button');
        submit.type = 'submit'; submit.textContent = '搜索';
        form.appendChild(submit);
        root.appendChild(form);
        root.appendChild(button('本画布历史会话', () => { void loadList(session, 'history'); }));
        const live = element('p', 'agent-chat-status');
        live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
        root.appendChild(live);
        const content = element('div', 'agent-chat-content');
        root.appendChild(content);
        const session = {ownerId, owner, project, host: owner.viewHost, activeHost: owner.activeHost, epoch: owner.viewEpoch,
            generation: owner._timelineGeneration || 0, previous: document.activeElement,
            previousComposer: document.activeElement === deps.getComposer?.(ownerId), composer: deps.getComposer?.(ownerId),
            root, input, content, status: live,
            serial: 0, abort: null, mode, limit: pageSize, query: '', rows: [], thread: null, composing: false,
            pendingClose: false, resizeTarget: false, autoScrollTop: 0, readerWidth: 0, readerHeight: 0, destroy: () => {}};
        active = session;
        /** @type {(() => void)[]} */
        const removers = [];
        /** @param {EventTarget} el @param {string} name @param {(event: any) => void} handler */
        const listen = (el, name, handler) => { el.addEventListener(name, handler); removers.push(() => el.removeEventListener(name, handler)); };
        listen(form, 'submit', event => { event.preventDefault(); if (!session.composing) void loadList(session, 'search'); });
        listen(input, 'compositionstart', () => { session.composing = true; });
        listen(input, 'compositionend', () => { session.composing = false; if (active === session && session.pendingClose) close(); });
        listen(root, 'keydown', event => {
            event.stopPropagation();
            if (active !== session) return;
            if (event.isComposing || event.keyCode === 229 || session.composing) {
                if (event.key === 'Enter') event.preventDefault();
                return;
            }
            if (event.key === 'Escape') { event.preventDefault(); close(); }
        });
        for (const type of ['pointerdown', 'mousedown', 'click', 'dblclick', 'wheel']) listen(root, type, event => event.stopPropagation());
        // Resizing can rewrap every preceding row. Keep the requested hit visible only until the user takes over reading.
        for (const type of ['wheel', 'touchmove', 'pointerdown']) listen(content, type, () => { session.resizeTarget = false; });
        listen(content, 'keydown', event => {
            if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) session.resizeTarget = false;
        });
        listen(content, 'scroll', () => {
            const sameSize = content.clientWidth === session.readerWidth && content.clientHeight === session.readerHeight;
            if (sameSize && Math.abs(content.scrollTop - session.autoScrollTop) > 1) session.resizeTarget = false;
        });
        const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => alignTarget(session)) : null;
        resize?.observe(content);
        const escapeStack = /** @type {{FireflyEscapeStack?: {push: (close: () => void, label: string) => () => void}}} */ (window);
        const releaseEscape = escapeStack.FireflyEscapeStack?.push(() => { if (active === session) close(); }, 'agent-chat');
        session.destroy = () => { resize?.disconnect(); releaseEscape?.(); removers.forEach(remove => remove()); };
        // A body portal survives live TUI rebuilds, so search never replaces the composer or its IME DOM.
        document.body.appendChild(root);
        input.focus({preventScroll: true});
        if (mode === 'history') void loadList(session, 'history');
        else { status(session, '输入关键词后按 Enter 或点击搜索。'); bounds(session, false, false); }
    }
    /** @param {HTMLElement} root @param {string} ownerId */
    function bind(root, ownerId) {
        const buttons = Array.from(root.querySelectorAll('[data-agent-chat-open]'));
        const previous = bindings.get(ownerId)?.buttons;
        if (!buttons.length || (previous?.length === buttons.length && previous.every((el, index) => el === buttons[index]))) return;
        bindings.get(ownerId)?.destroy();
        /** @type {(() => void)[]} */
        const removers = [];
        for (const el of buttons) {
            const down = (/** @type {Event} */ event) => { event.preventDefault(); event.stopPropagation(); };
            const click = (/** @type {Event} */ event) => { event.preventDefault(); event.stopPropagation(); open(ownerId, el.getAttribute('data-agent-chat-open') || 'search'); };
            el.addEventListener('mousedown', down); el.addEventListener('click', click);
            removers.push(() => { el.removeEventListener('mousedown', down); el.removeEventListener('click', click); });
        }
        bindings.set(ownerId, {buttons, destroy: () => removers.forEach(remove => remove())});
        invalidate();
    }
    function invalidate() {
        if (active && !ownerCurrent(active)) close(false);
        if (!active) return;
        const next = deps.getComposer?.(active.ownerId), previous = active.composer;
        // Detached textareas retain their last selection. Full canvas rebuilds must not erase it while the reader has focus.
        if (next && previous && next !== previous && !next.disabled && !active.owner.composing) {
            next.setSelectionRange(previous.selectionStart, previous.selectionEnd, previous.selectionDirection);
            next.scrollTop = previous.scrollTop;
        }
        active.composer = next;
    }
    /** @param {string} ownerId */
    function forget(ownerId) {
        if (active?.ownerId === ownerId) close(false);
        bindings.get(ownerId)?.destroy(); bindings.delete(ownerId);
        for (const key of drafts.keys()) if (JSON.parse(key)[1] === ownerId) drafts.delete(key);
    }
    function dispose() {
        close(false);
        for (const binding of bindings.values()) binding.destroy();
        bindings.clear(); drafts.clear();
    }
    /** @param {Partial<AgentChatDependencies>} next */
    function configure(next) { deps = {...deps, ...next}; invalidate(); }
    return {configure, bind, open, close, invalidate, forget, dispose};
})();
