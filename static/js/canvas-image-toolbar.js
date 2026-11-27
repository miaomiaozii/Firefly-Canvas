(function(global){
    'use strict';

    const storageKey = 'ff.imageToolbar.usage.v1';
    const halfLife = 7 * 24 * 60 * 60 * 1000;
    const capacity = 4;
    const order = ['preview','crop','flip','color','local-edit','outpaint','brush','grid','flip-vertical','restore','download','send-ps'];
    const paid = new Set(['local-edit','outpaint']);
    const rootSelector = '[data-image-toolbar]';
    const barSelector = '.ff-image-toolbar__bar';
    const popupSelector = '[data-image-toolbar-popup]';
    const controlSelector = '[data-image-toolbar-control]';
    const pinGlyph = '<svg aria-hidden="true" viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3l5 5-3 1-4 4v4l-2 2-3-4-5 5m5-5-4-3 2-2h4l4-4z"/></svg>';
    let nextCycle = 0;

    function escapeText(value){
        return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
    }
    function browserStorage(){
        try{ return global.localStorage; }catch{ return null; }
    }
    function readPreferences(storage){
        if(!storage) return null;
        let data;
        try{ data = JSON.parse(storage.getItem(storageKey) ?? 'null'); }catch{ return null; }
        const usage = {};
        for(const key of order.slice(1)){
            const entry = data?.usage?.[key];
            if(Number.isFinite(entry?.score) && entry.score > 0 && Number.isFinite(entry.at)){
                usage[key] = {score:entry.score, at:entry.at};
            }
        }
        const pins = order.slice(1).filter(key => Array.isArray(data?.pins) && data.pins.includes(key)).slice(0, capacity);
        return {usage, pins};
    }
    function scoreAt(entry, time){
        return entry ? entry.score * 2 ** (-Math.max(0, time - entry.at) / halfLife) : 0;
    }
    function inGroup(action, group){
        const index = order.indexOf(action.key);
        return group === 'view' ? index === 0 : group === 'export' ? index >= order.indexOf('download') : index > 0 && index < order.indexOf('download');
    }
    function displayGroup(key){
        if(key === 'preview') return 'view';
        if(paid.has(key)) return 'paid';
        if(order.indexOf(key) >= order.indexOf('download')) return 'export';
        if(order.indexOf(key) >= order.indexOf('brush')) return 'edit';
        return 'local';
    }
    function availableControls(section){
        return [...section.querySelectorAll(controlSelector)].filter(button => !button.disabled);
    }
    function positionPopup(root){
        const panel = [...root.querySelectorAll(popupSelector)].find(el => !el.hidden);
        if(!panel) return;
        const rect = root.getBoundingClientRect();
        const viewport = root.ownerDocument?.documentElement;
        if(!viewport || !rect.width) return;
        const scale = rect.width / root.offsetWidth || 1;
        const width = panel.getBoundingClientRect().width || panel.offsetWidth * scale;
        const margin = 12;
        const gap = 6 * scale;
        const top = rect.top - margin - gap;
        const bottom = viewport.clientHeight - rect.bottom - margin - gap;
        const wanted = Math.min(panel.scrollHeight * scale, viewport.clientHeight * .6, 420 * scale);
        const above = bottom < wanted && top > bottom;
        panel.setAttribute('data-image-toolbar-side', above ? 'above' : 'below');
        const left = Math.max(margin, Math.min(rect.right - width, viewport.clientWidth - margin - width));
        panel.style.setProperty('--ff-image-toolbar-popup-left', `${(left - rect.left) / scale}px`);
        panel.style.setProperty('--ff-image-toolbar-popup-height', `${Math.max(0, (above ? top : bottom) / scale)}px`);
    }

    function create({storage = browserStorage(), now = Date.now} = {}){
        let prefs = {usage:{}, pins:[]};
        let session = null;
        let unsavedPreferences = false;

        function refreshPreferences(){
            if(!unsavedPreferences) prefs = readPreferences(storage) || prefs;
        }
        function persist(){
            if(!storage) return;
            try{
                storage.setItem(storageKey, JSON.stringify(prefs));
                unsavedPreferences = false;
            }catch{ unsavedPreferences = true; }
        }
        function sync(selectedId){
            const id = selectedId == null ? '' : String(selectedId);
            if((session?.id ?? '') === id) return;
            session = id ? {id, cycle:String(++nextCycle), slots:null, actions:new Map(), popup:'', barStop:'', popupStop:'', feedback:''} : null;
        }
        function recordUse(key){
            if(key === 'preview' || !order.includes(key)) return;
            refreshPreferences();
            const entry = prefs.usage[key];
            const time = Math.max(now(), entry?.at ?? -Infinity);
            prefs.usage[key] = {score:scoreAt(entry, time) + 1, at:time};
            persist();
        }
        function togglePin(key){
            refreshPreferences();
            if(prefs.pins.includes(key)) prefs.pins = prefs.pins.filter(pin => pin !== key);
            else if(prefs.pins.length < capacity) prefs.pins.push(key);
            else return 'pinLimit';
            persist();
            return 'pinsNext';
        }
        function snapshot(actions){
            refreshPreferences();
            const time = now();
            const pins = prefs.pins.filter(key => actions.has(key));
            const ranked = order.slice(1).filter(key => actions.get(key)?.enabled !== false && actions.has(key) && !pins.includes(key));
            ranked.sort((a,b) => scoreAt(prefs.usage[b], time) - scoreAt(prefs.usage[a], time) || order.indexOf(a) - order.indexOf(b));
            const chosen = new Set(['preview', ...pins, ...ranked.slice(0, capacity - pins.length)]);
            return order.filter(key => chosen.has(key) && actions.has(key)).map(key => actions.get(key));
        }
        function render({id, actions = [], translate = key => key, icon = () => '', escape = escapeText}){
            if(!session || session.id !== String(id)) return '';
            const byKey = new Map(actions.filter(action => order.includes(action?.key)).map(action => [action.key, action]));
            session.actions = byKey;
            // #514: changes to availability, pinning and scores must not move a visible slot.
            session.slots ??= snapshot(byKey);
            const slots = session.slots.map(action => byKey.get(action.key) || {...action, enabled:false});
            const all = order.filter(key => byKey.has(key)).map(key => byKey.get(key));
            const enabled = action => Boolean(action && action.enabled !== false);
            const flipSplit = slots.some(action => action.key === 'flip') && byKey.has('flip-vertical');
            if(!flipSplit && session.popup === 'flip') session.popup = '';
            const t = key => translate(`imageToolbar.${key}`);
            const iconHtml = name => name ? icon(name) : '';
            const panelId = kind => `ff-image-toolbar-${session.cycle}-${kind}`;
            const titleFor = action => [action.title || action.label, paid.has(action.key) ? t('paid') : '', action.status].filter(Boolean).join(' · ');
            const actionName = key => `run:${key}`;
            const toggleName = kind => `toggle:${kind}`;
            const pinName = key => `pin:${key}`;
            const barControls = slots.flatMap(action => [
                ...(enabled(action) ? [actionName(action.key)] : []),
                ...(action.key === 'flip' && flipSplit && enabled(byKey.get('flip-vertical')) ? [toggleName('flip')] : []),
            ]);
            barControls.push(toggleName('more'));
            const tabStop = (names, saved) => names.includes(saved) ? saved : names[0];
            const barStop = tabStop(barControls, session.barStop);
            const button = ({control, label, title, extra = '', body, disabled = false, tab = false, classes = ''}) =>
                `<button type="button" class="ff-image-toolbar__button${classes}" data-image-toolbar-control="${control}" ${extra} tabindex="${tab && !disabled ? 0 : -1}" title="${escape(title || label)}" aria-label="${escape(label)}"${disabled ? ' disabled' : ''}>${body}</button>`;
            const actionButton = (action, stop, withStatus = false) => button({
                control:actionName(action.key), label:[action.label, paid.has(action.key) ? t('paid') : '', action.status].filter(Boolean).join(' · '), title:titleFor(action),
                extra:`data-image-toolbar-action="${action.key}"`, disabled:!enabled(action), tab:stop === actionName(action.key),
                body:`${iconHtml(action.icon)}<span class="ff-image-toolbar__label">${escape(action.label)}</span>${paid.has(action.key) ? '<span class="ff-image-toolbar__badge" aria-hidden="true">✦</span>' : ''}${withStatus && action.status ? `<span class="ff-image-toolbar__action-status" aria-hidden="true">${escape(action.status)}</span>` : ''}`,
            });
            const trigger = (kind, label, body, disabled = false) => button({
                control:toggleName(kind), label, body, disabled, tab:barStop === toggleName(kind),
                extra:`data-image-toolbar-toggle="${kind}" aria-controls="${panelId(kind)}" aria-expanded="${session.popup === kind}"`,
                classes:kind === 'flip' ? ' ff-image-toolbar__split-toggle' : '',
            });
            const panel = (kind, label, body) => `<div class="ff-image-toolbar__popup" id="${panelId(kind)}" data-image-toolbar-popup="${kind}" role="group" aria-label="${escape(label)}"${session.popup === kind ? '' : ' hidden'}>${body}</div>`;
            const moreControls = all.flatMap(action => [
                ...(enabled(action) ? [actionName(action.key)] : []), ...(action.key !== 'preview' ? [pinName(action.key)] : []),
            ]);
            const moreStop = tabStop(moreControls, session.popup === 'more' ? session.popupStop : '');
            const pinButton = action => {
                const fixed = action.key === 'preview';
                const pinned = fixed || prefs.pins.includes(action.key);
                const label = `${t(fixed ? 'previewFixed' : 'pin')} · ${action.label}`;
                return button({control:pinName(action.key), label, body:pinGlyph, disabled:fixed, tab:moreStop === pinName(action.key),
                    extra:`data-image-toolbar-pin="${action.key}" aria-pressed="${pinned}"`, classes:' ff-image-toolbar__pin'});
            };
            const moreSections = ['view','edit','export'].map(group => {
                const items = all.filter(action => inGroup(action, group));
                if(!items.length) return '';
                return `<div class="ff-image-toolbar__section" role="group" aria-label="${escape(t(group))}"><div class="ff-image-toolbar__section-title" aria-hidden="true">${escape(t(group))}</div>${items.map(action => `<div class="ff-image-toolbar__row">${actionButton(action, moreStop, true)}${pinButton(action)}</div>`).join('')}</div>`;
            }).join('');
            const feedback = session.feedback ? escape(t(session.feedback)) : '';
            const more = panel('more', t('more'), `${moreSections}<div class="ff-image-toolbar__feedback" data-image-toolbar-feedback role="status">${feedback}</div>`);
            const vertical = byKey.get('flip-vertical');
            const flip = flipSplit ? panel('flip', t('flipOptions'), actionButton(vertical, actionName(vertical.key), true)) : '';
            const bar = slots.map((action, index) => {
                const divider = index > 0 && displayGroup(action.key) !== displayGroup(slots[index - 1].key) ? ' ff-image-toolbar__slot--divider' : '';
                const split = action.key === 'flip' && flipSplit;
                return `<div class="ff-image-toolbar__slot${divider}${split ? ' ff-image-toolbar__split' : ''}">${actionButton(action, barStop)}${split ? trigger('flip', t('flipOptions'), iconHtml('chevron-down'), !enabled(vertical)) : ''}</div>`;
            }).join('');
            return `<div class="ff-image-toolbar" data-image-toolbar="${escape(session.id)}" data-image-toolbar-cycle="${session.cycle}"><div class="ff-image-toolbar__bar" role="toolbar" aria-label="${escape(t('toolbar'))}">${bar}<div class="ff-image-toolbar__slot${slots.length ? ' ff-image-toolbar__slot--divider' : ''}">${trigger('more', t('more'), `${iconHtml('ellipsis')}<span class="ff-image-toolbar__label">${escape(t('more'))}</span>`)}</div></div>${more}${flip}</div>`;
        }

        function bind(el, {run = () => {}, translate = key => key} = {}){
            const owner = el.ownerDocument;
            const current = root => session && root?.dataset.imageToolbar === session.id && root.dataset.imageToolbarCycle === session.cycle;
            const rootFor = target => {
                const root = target?.closest?.(rootSelector);
                return root && el.contains(root) ? root : null;
            };
            const findPanel = (root, kind) => root.querySelector(`[data-image-toolbar-popup="${kind}"]`);
            const findTrigger = (root, kind) => root.querySelector(`[data-image-toolbar-toggle="${kind}"]`);
            const boundRoot = () => el.matches?.(rootSelector) ? el : el.querySelector(rootSelector);
            function setStop(section, button){
                for(const control of section.querySelectorAll(controlSelector)) control.tabIndex = control === button ? 0 : -1;
                const name = button.dataset.imageToolbarControl;
                if(section.hasAttribute('data-image-toolbar-popup')) session.popupStop = name;
                else session.barStop = name;
            }
            function focus(section, button){
                if(!button) return;
                setStop(section, button);
                button.focus({preventScroll:!section.hasAttribute('data-image-toolbar-popup')});
            }
            function close(root, restoreFocus = false){
                const kind = session.popup;
                if(!kind) return;
                session.popup = session.popupStop = '';
                const panel = findPanel(root, kind);
                if(panel) panel.hidden = true;
                const trigger = findTrigger(root, kind);
                trigger?.setAttribute('aria-expanded', 'false');
                if(restoreFocus && trigger && !trigger.disabled) focus(root.querySelector(barSelector), trigger);
            }
            function refreshPins(root){
                for(const button of root.querySelectorAll('[data-image-toolbar-pin]')){
                    const key = button.dataset.imageToolbarPin;
                    button.setAttribute('aria-pressed', String(key === 'preview' || prefs.pins.includes(key)));
                }
            }
            function open(root, kind){
                const panel = findPanel(root, kind);
                const trigger = findTrigger(root, kind);
                if(!panel || !trigger || trigger.disabled) return;
                close(root);
                if(kind === 'more'){
                    refreshPreferences();
                    refreshPins(root);
                }
                session.popup = kind;
                panel.hidden = false;
                trigger.setAttribute('aria-expanded', 'true');
                setStop(root.querySelector(barSelector), trigger);
                positionPopup(root);
                focus(panel, availableControls(panel)[0]);
            }
            function click(event){
                const root = rootFor(event.target);
                if(!root) return;
                event.stopPropagation();
                const button = event.target.closest(controlSelector);
                if(!current(root) || !button || button.disabled) return;
                const popup = button.closest(popupSelector);
                if(popup?.hidden) return;
                const {imageToolbarToggle:toggle, imageToolbarPin:pin, imageToolbarAction:key} = button.dataset;
                if(toggle){
                    if(session.popup === toggle) close(root, true);
                    else open(root, toggle);
                }else if(pin && pin !== 'preview' && session.actions.has(pin)){
                    session.feedback = togglePin(pin);
                    refreshPins(root);
                    root.querySelector('[data-image-toolbar-feedback]').textContent = translate(`imageToolbar.${session.feedback}`);
                    positionPopup(root);
                }else if(key && session.actions.has(key) && session.actions.get(key).enabled !== false){
                    const id = session.id;
                    if(popup) close(root, true);
                    else if(session.popup) close(root);
                    if(current(root)){
                        recordUse(key);
                        run(id, key);
                    }
                }
            }
            function keydown(event){
                const root = rootFor(event.target);
                if(!current(root) || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing) return;
                const button = event.target.closest(controlSelector);
                if(!button || button.disabled) return;
                const popup = button.closest(popupSelector);
                const section = popup || button.closest(barSelector);
                if(!section || section.hidden) return;
                if(event.key === 'Escape'){
                    if(!session.popup) return;
                    close(root, true);
                }else if(event.key === 'ArrowDown' && !popup && button.dataset.imageToolbarToggle){
                    open(root, button.dataset.imageToolbarToggle);
                }else{
                    const buttons = availableControls(section);
                    const index = buttons.indexOf(button);
                    let next;
                    if(event.key === 'Home') next = 0;
                    else if(event.key === 'End') next = buttons.length - 1;
                    else if(event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % buttons.length;
                    else if(event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length;
                    else return;
                    focus(section, buttons[next]);
                }
                event.preventDefault();
                event.stopPropagation();
            }
            function focusin(event){
                const root = rootFor(event.target);
                const button = event.target.closest?.(controlSelector);
                if(current(root) && button && !button.disabled){
                    const section = button.closest(popupSelector) || button.closest(barSelector);
                    if(section && !section.hidden) setStop(section, button);
                }
            }
            function focusout(event){
                const root = rootFor(event.target);
                if(current(root) && !root.contains(event.relatedTarget)) close(root);
            }
            function outside(event){
                if(!session?.popup || rootFor(event.target)) return;
                const root = boundRoot();
                if(current(root)) close(root);
            }
            function shield(event){
                if(rootFor(event.target)) event.stopPropagation();
            }
            // Capture before node/card handlers, while leaving native focus and button activation intact.
            const handlers = {click, keydown, focusin, focusout, pointerdown:shield, mousedown:shield, dblclick:shield, wheel:shield};
            for(const [type, handler] of Object.entries(handlers)) el.addEventListener(type, handler, true);
            owner?.addEventListener('pointerdown', outside, true);
            return () => {
                const root = boundRoot();
                if(current(root)) close(root);
                for(const [type, handler] of Object.entries(handlers)) el.removeEventListener(type, handler, true);
                owner?.removeEventListener('pointerdown', outside, true);
            };
        }
        return Object.freeze({sync, render, bind, recordUse, positionPopup});
    }

    global.CanvasImageToolbar = Object.freeze({create});
})(window);
