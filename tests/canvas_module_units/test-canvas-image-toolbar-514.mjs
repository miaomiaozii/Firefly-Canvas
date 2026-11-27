import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import {Element} from './history-dom-493.mjs';

const week = 7 * 24 * 60 * 60 * 1000;
const storageKey = 'ff.imageToolbar.usage.v1';
const allKeys = ['preview','crop','flip','color','local-edit','outpaint','brush','grid','flip-vertical','restore','download','send-ps'];
const defaultKeys = ['preview','crop','flip','color','local-edit'];
const rootSelector = '[data-image-toolbar]';
const barSelector = '[role="toolbar"]';
const actionSelector = '[data-image-toolbar-action]';
const pinSelector = '[data-image-toolbar-pin]';
const feedbackSelector = '[data-image-toolbar-feedback]';
const controlSelector = '[data-image-toolbar-control]';

function fixture({preferences, disabled = [], supplied = allKeys, blockedStorage = false} = {}){
    const context = vm.createContext({window:{}});
    vm.runInContext(readFileSync(new URL('../../static/js/canvas-image-toolbar.js', import.meta.url), 'utf8'), context);
    const storageValues = new Map(preferences ? [[storageKey, JSON.stringify(preferences)]] : []);
    const storage = {
        getItem:key => storageValues.get(key) ?? null,
        setItem(key, value){
            if(blockedStorage) throw Error('Storage denied');
            storageValues.set(key,value);
        }
    };
    let now = 0;
    const create = () => context.window.CanvasImageToolbar.create({storage, now:() => now});
    const controller = create();
    const actions = supplied.map(key => ({key, label:key, icon:key, enabled:!disabled.includes(key)}));
    const translate = key => key;
    const render = (id = 'a', target = controller, overrides = {}) => target.render({id, actions, translate, icon: key => `<svg aria-hidden="true"><use href="#i-${key}"/></svg>`, ...overrides});
    const tree = html => { const host = new Element(); host.innerHTML = html; return host; };
    const slots = (html = render()) => tree(html).querySelector(barSelector)?.querySelectorAll(actionSelector).map(el => el.dataset.imageToolbarAction);
    return {controller, storageValues, create, render, tree, slots, setTime:time => {now = time;}};
}

function ui(f, id = 'a'){
    f.controller.sync(id);
    const host = f.tree(f.render(id));
    const root = host.querySelector(rootSelector);
    const bar = root.querySelector(barSelector);
    const calls = [];
    const handlers = new Map();
    const documentHandlers = new Map();
    const ownerDocument = {
        documentElement:{clientWidth:390,clientHeight:650},
        activeElement:null,
        addEventListener(type,handler){ documentHandlers.set(type,handler); },
        removeEventListener(type){ documentHandlers.delete(type); }
    };
    host.ownerDocument = root.ownerDocument = ownerDocument;
    host.addEventListener = (type, handler) => handlers.set(type, handler);
    host.removeEventListener = type => handlers.delete(type);
    root.offsetWidth = 240;
    root.getBoundingClientRect = () => ({left:90,right:330,width:240,top:120,bottom:158,height:38});
    const fire = (type, target, props = {}, recipient = handlers) => {
        const event = {target, preventDefault(){this.prevented=true;}, stopPropagation(){this.stopped=true;}, ...props};
        recipient.get(type)?.(event);
        return event;
    };
    for(const el of [...root.querySelectorAll('button'), ...root.querySelectorAll('[data-image-toolbar-popup]')]){
        Object.defineProperty(el, 'tabIndex', {get(){return Number(this.getAttribute('tabindex') ?? 0);}, set(n){this.setAttribute('tabindex',n);}});
        el.focus = () => {
            const previous = ownerDocument.activeElement;
            if(previous && previous !== el) fire('focusout', previous, {relatedTarget:el});
            ownerDocument.activeElement = el;
            fire('focusin', el, {relatedTarget:previous});
        };
        el.offsetWidth = 288;
        el.getBoundingClientRect = () => ({width:288});
        el.scrollHeight = 270;
        el.style.setProperty = (key,value) => {el.style[key]=value;};
    }
    const unbind = f.controller.bind(host,{run:(...args) => calls.push(args), translate:key => key});
    const action = (key, section = bar) => section.querySelector(`[data-image-toolbar-action="${key}"]`);
    const toggle = key => root.querySelector(`[data-image-toolbar-toggle="${key}"]`);
    const popup = key => root.querySelector(`[data-image-toolbar-popup="${key}"]`);
    const pin = key => root.querySelector(`[data-image-toolbar-pin="${key}"]`);
    const press = (key, props = {}) => fire('keydown', ownerDocument.activeElement, {key,...props});
    const click = el => fire('click', el.querySelector('use') || el);
    return {root, bar, calls, action, toggle, popup, pin, press, click, fire, documentHandlers, unbind, active:() => ownerDocument.activeElement};
}

test('fixed preview and defaults are independent of supplied ordering, only the selected node is rendered', () => {
    const f = fixture({supplied:[...allKeys].reverse()});
    assert.equal(f.render(), '');
    f.controller.sync('a');
    assert.equal(f.render('b'), '');
    assert.deepEqual(f.slots(), defaultKeys);
    f.controller.sync('');
    assert.equal(f.render(), '');
});

test('7-day half-life is applied when updating and ranking; scores decide slots, not display order', () => {
    const f = fixture({preferences:{usage:{crop:{score:20,at:0},flip:{score:19,at:0},color:{score:18,at:0},'local-edit':{score:6,at:0}}}});
    f.controller.sync('a');
    assert.deepEqual(f.slots(), defaultKeys);
    f.setTime(week);
    f.controller.recordUse('crop');
    for(let n = 0; n < 4; n++) f.controller.recordUse('download');
    f.controller.sync('a');
    assert.deepEqual(f.slots(), defaultKeys);
    assert.deepEqual(JSON.parse(f.storageValues.get(storageKey)).usage.crop, {score:11,at:week});
    f.controller.sync(null);
    f.controller.sync('a');
    assert.deepEqual(f.slots(), ['preview','crop','flip','color','download']);
    const otherProject = f.create();
    otherProject.sync('b');
    assert.deepEqual(f.slots(f.render('b', otherProject)), ['preview','crop','flip','color','download']);
});

test('open controllers share one storage record but keep their own open snapshots', () => {
    const f = fixture();
    const other = f.create();
    other.sync('b');
    const renderOther = () => f.slots(f.render('b', other));
    assert.deepEqual(renderOther(), defaultKeys);
    f.controller.recordUse('brush');
    other.recordUse('download');
    assert.deepEqual(renderOther(), defaultKeys);
    other.sync(null);
    other.sync('b');
    assert.deepEqual(renderOther(), ['preview','crop','flip','brush','download']);
});

test('equal scores retain the default tie order, even for newer rather than simply higher raw usage', () => {
    const f = fixture({preferences:{usage:{crop:{score:11,at:0},flip:{score:10,at:0},color:{score:9,at:0},'local-edit':{score:8,at:0},brush:{score:4,at:week},grid:{score:4,at:week}}}});
    f.setTime(week);
    f.controller.sync('a');
    assert.deepEqual(f.slots(), defaultKeys);
});

test('pins have exactly four persistent positions; overflow is explicit and pin/unpin applies next open only', () => {
    const f = fixture();
    const u = ui(f);
    u.click(u.toggle('more'));
    for(const key of ['download','brush','outpaint','flip-vertical']){
        u.click(u.pin(key));
        assert.equal(u.pin(key).getAttribute('aria-pressed'),'true');
    }
    assert.equal(u.root.querySelector(feedbackSelector).textContent,'imageToolbar.pinsNext');
    u.click(u.pin('grid'));
    assert.equal(u.pin('grid').getAttribute('aria-pressed'),'false');
    assert.equal(u.root.querySelector(feedbackSelector).textContent,'imageToolbar.pinLimit');
    assert.equal(u.pin('preview').disabled,true);
    assert.deepEqual(f.slots(), defaultKeys);
    f.controller.sync('b');
    assert.deepEqual(f.slots(f.render('b')), ['preview','outpaint','brush','flip-vertical','download']);
    const next = ui(f,'b');
    next.click(next.toggle('more'));
    next.click(next.pin('brush'));
    next.click(next.pin('grid'));
    assert.deepEqual(f.slots(f.render('b')), ['preview','outpaint','brush','flip-vertical','download']);
    f.controller.sync('a');
    assert.deepEqual(f.slots(), ['preview','outpaint','grid','flip-vertical','download']);
});

test('an open snapshot retains disabled/removed entries; a later cycle skips unavailable, non-pinned actions', () => {
    const f = fixture();
    f.controller.sync('a');
    assert.deepEqual(f.slots(), defaultKeys);
    const smaller = [{key:'preview', label:'Preview'}, {key:'crop', label:'Crop', enabled:false}, {key:'download', label:'Download'}];
    const html = f.render('a', f.controller, {actions:smaller});
    assert.deepEqual(f.slots(html), defaultKeys);
    const bar = f.tree(html).querySelector(barSelector);
    assert.ok(bar.querySelector('[data-image-toolbar-action="flip"]').disabled);
    f.controller.sync(null);
    f.controller.sync('a');
    assert.deepEqual(f.slots(f.render('a', f.controller, {actions:smaller})), ['preview','download']);
});

test('storage write failure does not interrupt in-session use or pin preferences', () => {
    const f = fixture({blockedStorage:true});
    const u = ui(f);
    u.click(u.toggle('more'));
    u.click(u.pin('outpaint'));
    u.click(u.action('download', u.popup('more')));
    assert.deepEqual(u.calls, [['a','download']]);
    f.controller.sync('b');
    assert.deepEqual(f.slots(f.render('b')), ['preview','crop','flip','outpaint','download']);
});

test('more renders every tool with accessible action and pin, paid marking, and plain-text factual PS status', () => {
    const f = fixture();
    f.controller.sync('a&b');
    const escaped = f.render('a&b', f.controller, {actions: allKeys.map(key => ({
        key, label:key, title:key === 'flip' ? 'Horizontal flip (Shift+H)' : undefined,
        enabled:key !== 'send-ps', status:key === 'send-ps' ? '<Offline & "not ready">' : undefined
    }))});
    const root = f.tree(escaped).querySelector(rootSelector);
    assert.equal(root.dataset.imageToolbar,'a&b');
    const more = root.querySelector('[data-image-toolbar-popup="more"]');
    assert.deepEqual(more.querySelectorAll(actionSelector).map(el => el.dataset.imageToolbarAction),allKeys);
    assert.deepEqual(more.querySelectorAll(pinSelector).map(el => el.dataset.imageToolbarPin),allKeys);
    assert.equal(root.querySelector('[role="toolbar"]').querySelector('[data-image-toolbar-action="flip"]').getAttribute('title'),'Horizontal flip (Shift+H)');
    assert.equal(more.querySelector('[data-image-toolbar-action="send-ps"]').disabled,true);
    assert.match(escaped, /&lt;Offline &amp; &quot;not ready&quot;&gt;/);
    assert.equal(more.querySelectorAll('.ff-image-toolbar__badge').length,2);
});

test('native Tab stops rove past disabled tools, wrap, respond to Home/End and preserve unhandled modifiers', () => {
    const f = fixture({disabled:['crop'], preferences:{pins:['crop']}});
    const u = ui(f);
    const stops = () => u.bar.querySelectorAll(controlSelector).filter(el => el.tabIndex === 0);
    assert.deepEqual(stops(), [u.action('preview')]);
    u.action('preview').focus();
    assert.equal(u.press('ArrowRight').prevented,true);
    assert.equal(u.active(),u.action('flip'));
    assert.deepEqual(stops(), [u.action('flip')]);
    assert.equal(u.press('ArrowLeft',{ctrlKey:true}).prevented,undefined);
    assert.equal(u.active(),u.action('flip'));
    u.press('Home');
    u.press('ArrowLeft');
    assert.equal(u.active(),u.toggle('more'));
    u.press('ArrowRight');
    assert.equal(u.active(),u.action('preview'));
    u.press('End');
    assert.equal(u.active(),u.toggle('more'));
    assert.equal(u.press('Tab').prevented,undefined);
    assert.equal(u.press('Escape').prevented,undefined);
});

test('split defaults to horizontal, popup roves, Escape restores the trigger, and disabled/stale actions do not run', () => {
    const f = fixture({disabled:['send-ps']});
    const u = ui(f);
    assert.equal(u.click(u.action('flip')).stopped,true);
    assert.deepEqual(u.calls, [['a','flip']]);
    const split = u.toggle('flip');
    const splitPopup = u.popup('flip');
    split.focus();
    assert.equal(u.press('ArrowDown').prevented,true);
    assert.equal(split.getAttribute('aria-expanded'),'true');
    assert.equal(splitPopup.hidden,false);
    assert.equal(u.active(),u.action('flip-vertical',splitPopup));
    assert.equal(u.press('Escape').stopped,true);
    assert.equal(u.active(),split);
    assert.equal(splitPopup.hidden,true);
    u.click(split);
    u.click(u.action('flip-vertical',splitPopup));
    assert.deepEqual(u.calls, [['a','flip'],['a','flip-vertical']]);
    assert.equal(u.active(),split);
    assert.equal(splitPopup.hidden,true);
    const more = u.toggle('more');
    const morePopup = u.popup('more');
    u.click(more);
    u.press('End');
    assert.equal(u.active(),u.pin('send-ps'));
    u.press('ArrowDown');
    assert.equal(u.active(),u.action('preview',morePopup));
    assert.equal(u.click(u.action('send-ps',morePopup)).stopped,true);
    assert.equal(morePopup.hidden,false);
    assert.equal(u.press('Escape').prevented,true);
    assert.equal(u.active(),more);
    u.root.getBoundingClientRect = () => ({left:12,right:252,width:240,top:568,bottom:606,height:38});
    u.click(more);
    assert.equal(morePopup.getAttribute('data-image-toolbar-side'),'above');
    assert.equal(morePopup.style['--ff-image-toolbar-popup-left'],'0px');
    u.fire('pointerdown',new Element(),{},u.documentHandlers);
    assert.equal(morePopup.hidden,true);
    f.controller.sync(null);
    u.click(u.action('flip'));
    assert.deepEqual(u.calls, [['a','flip'],['a','flip-vertical']]);
    assert.deepEqual(Object.keys(JSON.parse(f.storageValues.get(storageKey)).usage), ['flip','flip-vertical']);
    u.unbind();
    assert.equal(u.documentHandlers.size,0);
});
