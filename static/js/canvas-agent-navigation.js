// @ts-check

/** @typedef {{id: string, label: string, text: string, element: HTMLElement}} AgentNavigationEntry */
/** @typedef {{left: number, right: number, top: number, bottom: number}} AgentNavigationRect */

/** Independent classic-script navigation; receives user messages, never the timeline. */
var createAgentMessageNavigation = (function () {
    let instance = 0;

    /**
     * @param {{body: HTMLElement, mount: HTMLElement, entries: readonly AgentNavigationEntry[], onJump: (entry: AgentNavigationEntry) => void, readingRect: (currentId?: string) => AgentNavigationRect | null}} options
     * @returns {{refresh: (entries: readonly AgentNavigationEntry[]) => void, update: () => void, destroy: () => void}}
     */
    return function createAgentMessageNavigation({body, mount, entries: initialEntries, onJump, readingRect}) {
        const doc = body.ownerDocument;
        const view = /** @type {Window & typeof globalThis} */ (doc.defaultView);
        const prefix = `agent-message-navigation-${++instance}`;
        const rail = doc.createElement('div');
        rail.className = 'agent-message-navigation-rail';
        rail.setAttribute('role', 'group');
        rail.setAttribute('aria-label', '用户消息导航');
        // Buttons remain keyboard targets, but never compete for compressed pointer hits.
        Object.assign(rail.style, {position: 'relative', width: '24px', height: '100%'});
        mount.classList.add('agent-message-navigation');
        mount.appendChild(rail);

        const preview = doc.createElement('div');
        preview.className = 'agent-message-preview';
        preview.id = `${prefix}-preview`;
        preview.setAttribute('role', 'tooltip');
        preview.hidden = true;
        Object.assign(preview.style, {position: 'fixed', pointerEvents: 'none', boxSizing: 'border-box'});
        const source = doc.createElement('strong');
        source.className = 'agent-message-preview-source';
        const text = doc.createElement('p');
        text.className = 'agent-message-preview-text';
        preview.append(source, text);
        doc.body.appendChild(preview);

        /** @type {AgentNavigationEntry[]} */
        let entries = [];
        /** @type {Map<string, {entry: AgentNavigationEntry, button: HTMLButtonElement}>} */
        const records = new Map();
        /** @type {number[]} */
        let centers = [];
        let currentId = '';
        /** @type {{id: string, scrollTop: number} | null} */
        let jumpTarget = null;
        let rovingId = '';
        let previewId = '';
        let hoverId = '';
        let layoutHeight = -1;
        let frame = 0;
        let destroyed = false;

        /** @returns {AgentNavigationRect} */
        function viewport() {
            const visual = view.visualViewport;
            return visual
                ? {left: visual.offsetLeft, top: visual.offsetTop, right: visual.offsetLeft + visual.width, bottom: visual.offsetTop + visual.height}
                : {left: 0, top: 0, right: view.innerWidth, bottom: view.innerHeight};
        }

        /** @param {AgentNavigationRect} a @param {AgentNavigationRect} b */
        function intersects(a, b) {
            return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
        }

        function hidePreview() {
            if (previewId) records.get(previewId)?.button.removeAttribute('aria-describedby');
            previewId = '';
            preview.hidden = true;
        }

        /** Position in viewport CSS pixels, not the transformed node's local coordinates. */
        function positionPreview() {
            const record = records.get(previewId);
            if (!record) return;
            const bounds = viewport();
            const gap = 8;
            const width = Math.min(200, bounds.right - bounds.left - gap * 2);
            if (width <= 0) { hidePreview(); return; }
            preview.style.width = `${width}px`;
            preview.style.visibility = 'hidden';
            preview.hidden = false;
            const height = preview.getBoundingClientRect().height;
            const minX = bounds.left + gap, maxX = bounds.right - width - gap;
            const minY = bounds.top + gap, maxY = bounds.bottom - height - gap;
            if (maxY < minY) { hidePreview(); return; }
            const bodyBox = body.getBoundingClientRect();
            const railBox = rail.getBoundingClientRect();
            const buttonBox = record.button.getBoundingClientRect();
            const canvasLeft = (bodyBox.left + bodyBox.right) / 2 >= (bounds.left + bounds.right) / 2;
            const outerX = canvasLeft ? bodyBox.left - width - gap : bodyBox.right + gap;
            const innerX = (railBox.left + railBox.right) / 2 < (bodyBox.left + bodyBox.right) / 2
                ? railBox.right + gap : railBox.left - width - gap;
            const clampX = (/** @type {number} */ x) => Math.max(minX, Math.min(x, maxX));
            const clampY = (/** @type {number} */ y) => Math.max(minY, Math.min(y, maxY));
            const preferredX = outerX >= minX && outerX <= maxX ? outerX : clampX(innerX);
            const preferredY = clampY((buttonBox.top + buttonBox.bottom - height) / 2);
            const reading = readingRect(currentId);
            const xs = [preferredX];
            const ys = [preferredY];
            if (reading) {
                ys.push(reading.bottom + gap, reading.top - height - gap);
                xs.push(reading.left - width - gap, reading.right + gap);
            }
            ys.push(minY, maxY, bodyBox.top - height - gap, bodyBox.bottom + gap);
            xs.push(minX, maxX);
            // Search whole free rows first, then free columns. No overlap fallback.
            for (const x of xs) {
                if (x < minX || x > maxX) continue;
                for (const y of ys) {
                    if (y < minY || y > maxY) continue;
                    if (reading && intersects({left: x, right: x + width, top: y, bottom: y + height}, reading)) continue;
                    preview.style.left = `${x}px`;
                    preview.style.top = `${y}px`;
                    preview.dataset.placement = x === outerX ? 'outer' : 'inner';
                    preview.style.visibility = '';
                    return;
                }
            }
            // A full-viewport reading region has no safe place for a card.
            hidePreview();
        }

        /** @param {string} id */
        function showPreview(id) {
            hidePreview();
            const record = records.get(id);
            if (!record) return;
            previewId = id;
            source.textContent = record.entry.label;
            text.textContent = record.entry.text;
            record.button.setAttribute('aria-describedby', preview.id);
            positionPreview();
        }

        /** @param {string} id */
        function setRoving(id) {
            rovingId = id;
            for (const [key, record] of records) {
                const tabIndex = key === id ? 0 : -1;
                if (record.button.tabIndex !== tabIndex) record.button.tabIndex = tabIndex;
            }
        }

        /** @param {string} id */
        function setCurrent(id) {
            if (id === currentId) return;
            const previous = records.get(currentId)?.button;
            if (previous) { previous.setAttribute('aria-current', 'false'); previous.style.width = '6px'; }
            currentId = id;
            const current = records.get(id)?.button;
            if (current) { current.setAttribute('aria-current', 'true'); current.style.width = '12px'; }
        }

        function layout() {
            const height = mount.clientHeight;
            if (height === layoutHeight) return;
            layoutHeight = height;
            const inset = Math.min(12, height / 2);
            const step = entries.length > 1 ? Math.min(16, Math.max(0, height - inset * 2) / (entries.length - 1)) : 0;
            centers = entries.map((entry, index) => {
                const center = inset + step * index;
                const button = /** @type {HTMLButtonElement} */ (records.get(entry.id)?.button);
                button.style.top = `${center}px`;
                return center;
            });
        }

        /** Refresh geometry/current only: no child replacement or focus changes on scroll. */
        function update() {
            if (destroyed || !entries.length) return;
            layout();
            const box = body.getBoundingClientRect();
            const bounds = viewport();
            const top = Math.max(box.top, bounds.top), bottom = Math.min(box.bottom, bounds.bottom);
            if (bottom > top) {
                const reading = readingRect();
                const start = reading ? Math.max(top, reading.top) : top;
                const end = reading ? Math.min(bottom, reading.bottom) : bottom;
                const anchor = end > start ? start + Math.min(48, (end - start) / 4) : top + Math.min(48, (bottom - top) / 4);
                // Near the end, scrolling can clamp before the chosen row reaches the anchor.
                const jumped = jumpTarget && records.get(jumpTarget.id)?.entry.element.getBoundingClientRect();
                if (jumpTarget && jumped && body.scrollTop === jumpTarget.scrollTop && jumped.top < bottom && jumped.bottom > top) {
                    setCurrent(jumpTarget.id);
                } else {
                    jumpTarget = null;
                    // Each user owns the following assistant/tool segment until the next user.
                    let selected = entries[0].id;
                    for (const entry of entries) {
                        if (entry.element.getBoundingClientRect().top > anchor) break;
                        selected = entry.id;
                    }
                    setCurrent(selected);
                }
            }
            if (previewId) positionPreview();
        }

        function scheduleUpdate() {
            if (!destroyed && !frame) frame = view.requestAnimationFrame(() => { frame = 0; update(); });
        }
        const observer = new view.ResizeObserver(scheduleUpdate);
        observer.observe(body);
        observer.observe(mount);

        /** Reconcile stable user identities; unchanged IDs retain their actual button nodes. @param {readonly AgentNavigationEntry[]} next */
        function refresh(next) {
            if (destroyed) return;
            const sameOrder = next.length === entries.length && next.every((entry, index) => entry.id === entries[index].id);
            const wanted = new Set(next.map(entry => entry.id));
            const focused = entryFromTarget(doc.activeElement);
            const previousIndex = entries.findIndex(entry => entry.id === rovingId);
            for (const [id, record] of records) {
                if (wanted.has(id)) continue;
                observer.unobserve(record.entry.element);
                record.button.remove();
                records.delete(id);
            }
            next.forEach((entry, index) => {
                let record = records.get(entry.id);
                if (!record) {
                    const button = doc.createElement('button');
                    button.type = 'button';
                    button.className = 'agent-message-tick';
                    button.id = `${prefix}-tick-${encodeURIComponent(entry.id)}`;
                    button.dataset.entryId = entry.id;
                    button.setAttribute('aria-current', 'false');
                    Object.assign(button.style, {position: 'absolute', left: '4px', width: '6px', height: '2px', padding: '0', border: '0', transform: 'translateY(-50%)', pointerEvents: 'none'});
                    record = {entry, button};
                    records.set(entry.id, record);
                    observer.observe(entry.element);
                } else if (record.entry.element !== entry.element) {
                    observer.unobserve(record.entry.element);
                    observer.observe(entry.element);
                }
                record.entry = entry;
                const label = `跳转到用户消息 ${index + 1}：${entry.label}`;
                if (record.button.getAttribute('aria-label') !== label) record.button.setAttribute('aria-label', label);
                if (entry.element.id) {
                    if (record.button.getAttribute('aria-controls') !== entry.element.id) record.button.setAttribute('aria-controls', entry.element.id);
                } else if (record.button.hasAttribute('aria-controls')) record.button.removeAttribute('aria-controls');
                // Inserting new siblings does not detach a focused, surviving button.
                if (rail.children[index] !== record.button) rail.insertBefore(record.button, rail.children[index] || null);
            });
            entries = next.slice();
            if (mount.hidden !== (entries.length === 0)) mount.hidden = entries.length === 0;
            if (!sameOrder) layoutHeight = -1;
            const fallback = entries[Math.max(0, Math.min(previousIndex, entries.length - 1))]?.id || '';
            setRoving(wanted.has(rovingId) ? rovingId : fallback);
            // Moving a focused button in a reordered list can blur it in the DOM.
            if (focused && entries.length && doc.activeElement !== records.get(focused.id)?.button) {
                const target = records.get(focused.id)?.button || records.get(rovingId)?.button;
                target?.focus({preventScroll: true});
            }
            if (previewId) {
                const entry = records.get(previewId)?.entry;
                if (entry) {
                    if (source.textContent !== entry.label) source.textContent = entry.label;
                    if (text.textContent !== entry.text) text.textContent = entry.text;
                } else hidePreview();
            }
            if (!wanted.has(currentId)) currentId = '';
            update();
        }

        /** @param {EventTarget | null} target @returns {AgentNavigationEntry | undefined} */
        function entryFromTarget(target) {
            for (const record of records.values()) if (record.button === target) return record.entry;
        }

        /** @param {number} clientY @returns {AgentNavigationEntry | undefined} */
        function nearest(clientY) {
            if (!entries.length) return;
            const box = rail.getBoundingClientRect();
            if (!box.height) return;
            const y = (clientY - box.top) * mount.clientHeight / box.height;
            let closest = 0;
            for (let index = 1; index < centers.length; index++) {
                if (Math.abs(centers[index] - y) < Math.abs(centers[closest] - y)) closest = index;
            }
            return entries[closest];
        }

        /** @param {AgentNavigationEntry} entry */
        function jump(entry) {
            hidePreview();
            setRoving(entry.id);
            setCurrent(entry.id);
            onJump(entry);
            jumpTarget = {id: entry.id, scrollTop: body.scrollTop};
        }
        /** @param {PointerEvent} event */
        function pointerMove(event) {
            const entry = nearest(event.clientY);
            if (entry && hoverId !== entry.id) { hoverId = entry.id; showPreview(entry.id); }
        }
        function pointerLeave() {
            hoverId = '';
            const focused = entryFromTarget(doc.activeElement);
            if (focused) showPreview(focused.id);
            else hidePreview();
        }
        /** @param {PointerEvent} event */
        function pointerDown(event) {
            // Preserve the composer selection/focus, and do not start a canvas drag.
            event.preventDefault();
            event.stopPropagation();
        }
        /** @param {MouseEvent} event */
        function click(event) {
            const entry = event.detail === 0 ? entryFromTarget(event.target) : nearest(event.clientY);
            if (entry) { event.preventDefault(); event.stopPropagation(); jump(entry); }
        }
        /** @param {FocusEvent} event */
        function focusIn(event) {
            const entry = entryFromTarget(event.target);
            if (entry) { setRoving(entry.id); showPreview(entry.id); }
        }
        /** @param {FocusEvent} event */
        function focusOut(event) {
            if (!entryFromTarget(event.relatedTarget)) hidePreview();
        }
        /** @param {KeyboardEvent} event */
        function keyDown(event) {
            const entry = entryFromTarget(event.target);
            if (!entry) return;
            const index = entries.findIndex(item => item.id === entry.id);
            let next = index;
            if (event.key === 'ArrowDown') next = Math.min(entries.length - 1, index + 1);
            else if (event.key === 'ArrowUp') next = Math.max(0, index - 1);
            else if (event.key === 'Home') next = 0;
            else if (event.key === 'End') next = entries.length - 1;
            else if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault(); event.stopPropagation(); jump(entry); return;
            } else return;
            event.preventDefault();
            event.stopPropagation();
            const button = /** @type {HTMLButtonElement} */ (records.get(entries[next].id)?.button);
            button.focus({preventScroll: true});
        }
        /** @param {KeyboardEvent} event */
        function escape(event) {
            if (event.key === 'Escape') { hoverId = ''; hidePreview(); }
        }

        rail.addEventListener('pointermove', pointerMove);
        rail.addEventListener('pointerleave', pointerLeave);
        rail.addEventListener('pointerdown', pointerDown);
        rail.addEventListener('click', click);
        rail.addEventListener('focusin', focusIn);
        rail.addEventListener('focusout', focusOut);
        rail.addEventListener('keydown', keyDown);
        doc.addEventListener('keydown', escape);
        body.addEventListener('scroll', scheduleUpdate, {passive: true});
        view.addEventListener('resize', scheduleUpdate);
        view.addEventListener('scroll', scheduleUpdate, true);
        view.visualViewport?.addEventListener('resize', scheduleUpdate);
        view.visualViewport?.addEventListener('scroll', scheduleUpdate);

        function destroy() {
            if (destroyed) return;
            destroyed = true;
            if (frame) view.cancelAnimationFrame(frame);
            observer.disconnect();
            hidePreview();
            body.removeEventListener('scroll', scheduleUpdate);
            view.removeEventListener('resize', scheduleUpdate);
            view.removeEventListener('scroll', scheduleUpdate, true);
            view.visualViewport?.removeEventListener('resize', scheduleUpdate);
            view.visualViewport?.removeEventListener('scroll', scheduleUpdate);
            doc.removeEventListener('keydown', escape);
            rail.removeEventListener('pointermove', pointerMove);
            rail.removeEventListener('pointerleave', pointerLeave);
            rail.removeEventListener('pointerdown', pointerDown);
            rail.removeEventListener('click', click);
            rail.removeEventListener('focusin', focusIn);
            rail.removeEventListener('focusout', focusOut);
            rail.removeEventListener('keydown', keyDown);
            rail.remove();
            preview.remove();
            records.clear();
            entries = [];
        }
        refresh(initialEntries);
        return {refresh, update, destroy};
    };
})();
