// #421: real module with a stateful DOM boundary (tree, bubbling, focus, scroll/zoom geometry).
// No browser/package dependency: this runs in the project's existing Node unit runner.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/canvas-agent-navigation.js', import.meta.url), 'utf8');
const rect = (left, top, width, height) => ({left, top, width, height, right: left + width, bottom: top + height});
const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

class Target {
    listeners = new Map();
    addEventListener(type, fn, options) {
        const capture = options === true || !!options?.capture;
        const list = this.listeners.get(type) || [];
        if (!list.some(item => item.fn === fn && item.capture === capture)) list.push({fn, capture});
        this.listeners.set(type, list);
    }
    removeEventListener(type, fn, options) {
        const capture = options === true || !!options?.capture;
        this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item.fn !== fn || item.capture !== capture));
    }
    listenerCount() { return [...this.listeners.values()].reduce((sum, list) => sum + list.length, 0); }
    dispatchEvent(event) {
        event.target ??= this;
        event.preventDefault ??= () => { event.defaultPrevented = true; };
        event.stopPropagation ??= () => { event.stopped = true; };
        for (const {fn} of [...(this.listeners.get(event.type) || [])]) fn(event);
        if (event.bubbles && !event.stopped) this.parentNode?.dispatchEvent(event);
        return !event.defaultPrevented;
    }
}

class Element extends Target {
    children = [];
    parentNode = null;
    style = {};
    dataset = {};
    attributes = new Map();
    hidden = false;
    textContent = '';
    id = '';
    className = '';
    tabIndex = -1;
    scrollTop = 0;
    childChanges = 0;
    focusCalls = 0;
    constructor(doc, tag) {
        super(); this.ownerDocument = doc; this.tagName = tag.toUpperCase();
        this.classList = {add: name => { this.className = [...new Set([...this.className.split(' ').filter(Boolean), name])].join(' '); }};
    }
    set innerHTML(_) { throw new Error('HTML parsing is forbidden for navigation/readonly previews'); }
    get clientHeight() { return this.hidden ? 0 : (this.localHeight ?? 0); }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    hasAttribute(name) { return this.attributes.has(name); }
    removeAttribute(name) { this.attributes.delete(name); }
    appendChild(child) { return this.insertBefore(child, null); }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    insertBefore(child, before) {
        if (child === before) return child;
        child.remove();
        const index = before ? this.children.indexOf(before) : this.children.length;
        assert.ok(index >= 0, 'insertBefore reference must be a child');
        this.children.splice(index, 0, child); child.parentNode = this; this.childChanges++;
        return child;
    }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    remove() {
        if (!this.parentNode) return;
        if (this.contains(this.ownerDocument.activeElement)) this.ownerDocument.activeElement.blur();
        const parent = this.parentNode;
        parent.children.splice(parent.children.indexOf(this), 1); parent.childChanges++; this.parentNode = null;
    }
    focus(options) {
        this.focusCalls++; this.lastFocusOptions = options;
        const previous = this.ownerDocument.activeElement;
        if (previous === this) return;
        this.ownerDocument.activeElement = this;
        previous?.dispatchEvent({type: 'focusout', bubbles: true, relatedTarget: this});
        this.dispatchEvent({type: 'focusin', bubbles: true, relatedTarget: previous});
    }
    blur() {
        this.ownerDocument.activeElement = this.ownerDocument.body;
        this.dispatchEvent({type: 'focusout', bubbles: true, relatedTarget: this.ownerDocument.body});
    }
    getBoundingClientRect() {
        if (this.hidden) return rect(0, 0, 0, 0);
        if (this.geometry) return this.geometry();
        if (this.className === 'agent-message-navigation-rail') {
            const box = this.parentNode.getBoundingClientRect();
            return rect(box.left, box.top, 24 * this.ownerDocument.zoom, box.height);
        }
        if (this.className === 'agent-message-tick') {
            const box = this.parentNode.getBoundingClientRect(), z = this.ownerDocument.zoom;
            return rect(box.left + 4 * z, box.top + (parseFloat(this.style.top) - 1) * z, parseFloat(this.style.width) * z, 2 * z);
        }
        if (this.className === 'agent-message-preview') return rect(parseFloat(this.style.left) || 0, parseFloat(this.style.top) || 0, parseFloat(this.style.width), 72);
        return rect(0, 0, 0, 0);
    }
}

function fixture({count = 3, height = 160, zoom = 1, width = 1280, viewportHeight = 720, x = 790, y = 110} = {}) {
    const view = new Target(), doc = new Target(), frames = new Map(), observers = [];
    Object.assign(view, {innerWidth: width, innerHeight: viewportHeight});
    view.visualViewport = Object.assign(new Target(), {offsetLeft: 0, offsetTop: 0, width, height: viewportHeight});
    let sequence = 0;
    view.requestAnimationFrame = fn => { const id = ++sequence; frames.set(id, fn); return id; };
    view.cancelAnimationFrame = id => frames.delete(id);
    view.ResizeObserver = class {
        observed = new Set();
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe(element) { this.observed.add(element); }
        unobserve(element) { this.observed.delete(element); }
        disconnect() { this.observed.clear(); this.disconnected = true; }
    };
    doc.defaultView = view; doc.zoom = zoom;
    doc.createElement = tag => new Element(doc, tag);
    doc.body = doc.createElement('body'); doc.body.parentNode = doc; doc.activeElement = doc.body;
    const body = doc.createElement('div'), mount = doc.createElement('nav'), input = doc.createElement('textarea');
    doc.body.append(body, mount, input);
    body.geometry = () => rect(x, y, 420 * doc.zoom, 340 * doc.zoom);
    mount.localHeight = height;
    mount.geometry = () => rect(x + 2 * doc.zoom, y + 12 * doc.zoom, 24 * doc.zoom, mount.clientHeight * doc.zoom);
    const entries = [];
    function addEntry(id, top) {
        const element = doc.createElement('article'); element.id = `user-${id}`;
        element.geometry = () => rect(x + 32 * doc.zoom, y + (top - body.scrollTop) * doc.zoom, 360 * doc.zoom, 70 * doc.zoom);
        body.appendChild(element);
        const entry = {id, label: '你', text: `用户消息 ${id}`, element}; entries.push(entry); return entry;
    }
    for (let i = 0; i < count; i++) addEntry(`u${i}`, i * 220);
    const assistant = doc.createElement('article'); assistant.textContent = 'Not a navigation entry'; body.appendChild(assistant);
    const context = vm.createContext({console});
    vm.runInContext(source, context, {filename: 'canvas-agent-navigation.js'});
    const f = {view, doc, body, mount, input, entries, frames, observers, addEntry, jumps: [], reading: null, create: context.createAgentMessageNavigation};
    f.nav = context.createAgentMessageNavigation({body, mount, entries, onJump: entry => { f.jumps.push(entry); f.onJump?.(entry); }, readingRect: () => f.reading});
    f.rail = mount.children[0]; f.preview = doc.body.children.find(child => child.className === 'agent-message-preview');
    f.ticks = () => [...f.rail.children];
    f.visible = () => f.ticks().filter(tick => tick.dataset.tickVisible === 'true');
    f.current = () => f.ticks().filter(tick => tick.getAttribute('aria-current') === 'true').map(tick => tick.dataset.entryId);
    f.flush = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); };
    f.emit = (target, type, props = {}) => { const event = {type, bubbles: true, ...props}; target.dispatchEvent(event); return event; };
    f.point = index => { const box = f.ticks()[index].getBoundingClientRect(); return {clientY: box.top + box.height / 2, detail: 1}; };
    return f;
}

function assertSafe(f) {
    assert.equal(f.preview.hidden, false);
    const box = f.preview.getBoundingClientRect(), v = f.view.visualViewport;
    assert.ok(box.left >= v.offsetLeft + 8 && box.right <= v.offsetLeft + v.width - 8, 'card fits horizontal visual viewport');
    assert.ok(box.top >= v.offsetTop + 8 && box.bottom <= v.offsetTop + v.height - 8, 'card fits vertical visual viewport');
    if (f.reading) assert.equal(overlaps(box, f.reading), false, 'card never covers the reading rectangle');
}

test('empty and single user messages have explicit, finite layout; assistant DOM is not copied', () => {
    const empty = fixture({count: 0});
    assert.equal(empty.mount.hidden, true); assert.equal(empty.ticks().length, 0);
    empty.nav.update(); empty.emit(empty.rail, 'click', {detail: 1, clientY: 100}); assert.equal(empty.jumps.length, 0);
    const one = fixture({count: 1});
    assert.equal(one.mount.hidden, false); assert.equal(one.ticks().length, 1);
    assert.equal(one.ticks()[0].style.top, '12px'); assert.equal(one.ticks()[0].style.width, '12px');
    assert.deepEqual(one.current(), ['u0']);
    const entry = empty.addEntry('first', 0); empty.nav.refresh([entry]);
    assert.equal(empty.ticks().length, 1); assert.deepEqual(empty.current(), ['first']);
    one.nav.destroy(); empty.nav.destroy();
});

test('nominal centers are 16px; long conversations sample ink at proportional centers and keep the endpoints', () => {
    const regular = fixture({count: 4});
    assert.deepEqual(regular.ticks().map(tick => parseFloat(tick.style.top)), [12, 28, 44, 60]);
    assert.deepEqual(regular.ticks().map(tick => tick.style.width), ['12px', '6px', '6px', '6px']);
    const long = fixture({count: 501, height: 124});
    const positions = long.ticks().map(tick => parseFloat(tick.style.top));
    assert.equal(positions[0], 12); assert.equal(positions.at(-1), 112);
    positions.slice(1).forEach((p, i) => assert.ok(Math.abs(p - positions[i] - 0.2) < 1e-10));
    assert.equal(new Set(long.ticks().map(tick => tick.id)).size, 501);
    const visible = long.visible();
    assert.ok(visible.length <= 40);
    assert.equal(visible[0], long.ticks()[0]); assert.equal(visible.at(-1), long.ticks().at(-1));
    visible.slice(1).forEach((tick, i) => assert.ok(parseFloat(tick.style.top) - parseFloat(visible[i].style.top) >= 8));
    long.mount.localHeight = 224; long.observers[0].callback(); long.flush();
    assert.equal(long.ticks().at(-1).style.top, '212px');
    regular.nav.destroy(); long.nav.destroy();
});

test('a single 24px rail selects nearest y, not overlapping buttons; pointer jump retains input selection/focus', () => {
    for (const zoom of [0.5, 1, 1.75]) {
        const f = fixture({count: 101, height: 124, zoom});
        assert.equal(f.rail.style.width, '24px');
        f.input.value = 'draft'; f.input.selectionStart = 1; f.input.selectionEnd = 4; f.input.focus();
        for (const index of [0, 25, 50, 75, 100]) {
            const point = f.point(index);
            f.emit(f.rail, 'pointermove', point);
            assert.equal(f.preview.children[1].textContent, f.entries[index].text);
            const down = f.emit(f.rail, 'pointerdown', point);
            assert.equal(down.defaultPrevented, true); assert.equal(down.stopped, true);
            f.emit(f.rail, 'click', point);
            assert.equal(f.jumps.at(-1), f.entries[index]); assert.deepEqual(f.current(), [f.entries[index].id]);
            assert.equal(f.doc.activeElement, f.input); assert.equal(f.input.selectionStart, 1); assert.equal(f.input.selectionEnd, 4);
        }
        assert.ok(f.ticks().every(tick => tick.style.pointerEvents === 'none'));
        // Even a click reported on the last button must use pointer y, not that target.
        f.emit(f.ticks().at(-1), 'click', f.point(25)); assert.equal(f.jumps.at(-1), f.entries[25]);
        const a = f.point(25).clientY, b = f.point(33).clientY;
        f.emit(f.rail, 'click', {detail: 1, clientY: a + (b - a) * 0.75});
        assert.equal(f.jumps.at(-1), f.entries[33]); f.nav.destroy();
    }
});

test('roving keyboard visits every compressed tick, Enter jumps, Escape closes without moving focus', () => {
    const f = fixture({count: 80, height: 60});
    assert.equal(f.ticks().filter(tick => tick.tabIndex === 0).length, 1);
    f.ticks()[0].focus();
    for (let i = 1; i < f.entries.length; i++) {
        const event = f.emit(f.doc.activeElement, 'keydown', {key: 'ArrowDown'});
        assert.equal(event.defaultPrevented, true); assert.equal(f.doc.activeElement, f.ticks()[i]);
        assert.ok(f.visible().includes(f.ticks()[i]), 'focused unsampled entry draws at its actual position');
        assert.equal(f.ticks().filter(tick => tick.tabIndex === 0).length, 1);
    }
    assert.equal(f.doc.activeElement.dataset.entryId, 'u79');
    f.emit(f.doc.activeElement, 'keydown', {key: 'Enter'}); assert.equal(f.jumps.at(-1), f.entries[79]);
    assert.deepEqual(f.current(), ['u79']);
    f.emit(f.doc.activeElement, 'keydown', {key: 'ArrowUp'}); assert.equal(f.doc.activeElement, f.ticks()[78]);
    assert.equal(f.preview.hidden, false);
    f.emit(f.doc.activeElement, 'keydown', {key: 'Escape'}); assert.equal(f.preview.hidden, true);
    assert.equal(f.doc.activeElement, f.ticks()[78]);
    f.emit(f.doc.activeElement, 'keydown', {key: 'Home'}); assert.equal(f.doc.activeElement, f.ticks()[0]);
    f.emit(f.doc.activeElement, 'keydown', {key: 'End'}); assert.equal(f.doc.activeElement, f.ticks()[79]);
    f.nav.destroy();
});

test('an unsampled reading segment draws a visible current tick at its position without detaching keyboard targets', () => {
    const f = fixture({count: 150, height: 260});
    const ticks = f.ticks();
    const index = ticks.findIndex(tick => tick.dataset.tickVisible === 'false');
    assert.ok(index > 0);
    f.body.scrollTop = index * 220; f.emit(f.body, 'scroll'); f.flush();
    assert.deepEqual(f.current(), [`u${index}`]);
    assert.ok(f.visible().includes(ticks[index]));
    assert.equal(ticks[index].style.width, '12px');
    assert.ok(f.visible().length <= 40);
    assert.deepEqual(f.ticks(), ticks);
    f.nav.destroy();
});

test('both endpoint marks remain pointer reachable next to the current mark', () => {
    const f = fixture({count: 150, height: 260});
    for (const [reading, endpoint] of [[1, 0], [148, 149]]) {
        f.body.scrollTop = reading * 220; f.emit(f.body, 'scroll'); f.flush();
        assert.deepEqual(f.current(), [`u${reading}`]);
        assert.ok(f.visible().includes(f.ticks()[endpoint]));
        f.emit(f.rail, 'click', f.point(endpoint));
        assert.equal(f.jumps.at(-1), f.entries[endpoint]);
    }
    f.nav.destroy();
});

test('current follows visible user segments, including assistant-only view, not the last array entry', () => {
    const f = fixture({count: 5});
    assert.deepEqual(f.current(), ['u0']);
    const nodes = f.ticks(), changes = f.rail.childChanges;
    nodes[3].focus(); const focusCalls = nodes[3].focusCalls;
    // The first user's assistant section is visible, while the next user is also in view.
    f.body.scrollTop = 120; f.emit(f.body, 'scroll'); f.flush(); assert.deepEqual(f.current(), ['u0']);
    f.body.scrollTop = 300; f.emit(f.body, 'scroll'); f.flush(); assert.deepEqual(f.current(), ['u1']);
    f.body.scrollTop = 700; f.emit(f.body, 'scroll'); f.flush(); assert.deepEqual(f.current(), ['u3']);
    f.body.scrollTop = 880; f.emit(f.body, 'scroll'); f.flush(); assert.deepEqual(f.current(), ['u4']);
    assert.deepEqual(f.ticks(), nodes); assert.equal(f.rail.childChanges, changes);
    assert.equal(f.doc.activeElement, nodes[3]); assert.equal(nodes[3].focusCalls, focusCalls);
    // An explicitly supplied visible reading message selects its owning user segment.
    f.body.scrollTop = 0; f.reading = rect(822, 350, 350, 80); f.nav.update(); assert.deepEqual(f.current(), ['u1']);
    f.nav.destroy();
});

test('a jump clamped near the end keeps its visible target current until the scroll position changes', () => {
    const f = fixture({count: 5, height: 40});
    f.onJump = () => { f.body.scrollTop = 700; };
    f.emit(f.rail, 'click', f.point(4));
    f.emit(f.body, 'scroll'); f.flush();
    assert.deepEqual(f.current(), ['u4']);
    assert.equal(f.ticks()[4].style.width, '12px');
    assert.equal(f.ticks()[3].style.width, '6px');
    f.body.scrollTop = 695; f.emit(f.body, 'scroll'); f.flush();
    assert.deepEqual(f.current(), ['u3']);
    f.nav.destroy();
});

test('refresh reconciles identities without rebuilding; reorder, deletion and metadata preserve the right focus', () => {
    const f = fixture(); const nodes = f.ticks(), ids = nodes.map(tick => tick.id), changes = f.rail.childChanges;
    nodes[1].focus(); const focusCalls = nodes[1].focusCalls;
    f.nav.refresh(f.entries.map(entry => ({...entry})));
    assert.deepEqual(f.ticks(), nodes); assert.equal(f.rail.childChanges, changes); assert.equal(nodes[1].focusCalls, focusCalls);
    const edited = f.entries.map(entry => ({...entry, label: '<b>你</b>', text: '<img src=x onerror=alert(1)>'}));
    f.nav.refresh(edited);
    assert.equal(f.preview.children[0].tagName, 'STRONG');
    assert.equal(f.preview.children[0].textContent, '<b>你</b>'); assert.equal(f.preview.children[1].textContent, '<img src=x onerror=alert(1)>');
    assert.equal(f.preview.children[1].children.length, 0);
    f.nav.refresh([edited[2], edited[0], edited[1]]);
    assert.equal(f.doc.activeElement, nodes[1]); assert.deepEqual(f.ticks().map(tick => tick.id), [ids[2], ids[0], ids[1]]);
    f.nav.refresh([edited[2], edited[0]]); assert.equal(f.doc.activeElement, nodes[0]);
    f.input.focus(); f.nav.refresh([edited[2]]); assert.equal(f.doc.activeElement, f.input);
    f.nav.refresh([]); assert.equal(f.mount.hidden, true); assert.equal(f.preview.hidden, true);
    f.nav.refresh(edited); assert.deepEqual(f.ticks().map(tick => tick.id), ids);
    assert.equal(f.doc.activeElement, f.input); f.nav.destroy();
});

test('portal uses viewport coordinates at node zoom, prefers canvas side and avoids the reading region', () => {
    for (const zoom of [0.5, 1, 1.5]) {
        const f = fixture({zoom, x: 730, y: 130});
        f.reading = rect(762, 130, 400, 340);
        f.emit(f.rail, 'pointermove', f.point(1)); assertSafe(f);
        assert.equal(f.preview.parentNode, f.doc.body); assert.equal(f.preview.style.position, 'fixed');
        assert.equal(f.preview.style.width, '200px'); assert.equal(f.preview.dataset.placement, 'outer');
        assert.equal(f.preview.getBoundingClientRect().right, f.body.getBoundingClientRect().left - 8);
        f.emit(f.rail, 'pointerleave'); assert.equal(f.preview.hidden, true); f.nav.destroy();
    }
    const left = fixture({x: 12}); left.emit(left.rail, 'pointermove', left.point(0)); assertSafe(left);
    assert.equal(left.preview.getBoundingClientRect().left, left.body.getBoundingClientRect().right + 8); left.nav.destroy();
});

test('narrow/visual viewports use an inner free row/column, and hide rather than cover full-screen reading', () => {
    for (const width of [240, 360, 440]) {
        const f = fixture({width, x: 10, height: 300});
        f.reading = rect(0, 110, width, 380);
        f.emit(f.rail, 'pointermove', f.point(0)); assertSafe(f); assert.equal(f.preview.dataset.placement, 'inner');
        f.view.visualViewport.offsetLeft = 20; f.view.visualViewport.offsetTop = 60;
        f.view.visualViewport.width = width - 20; f.view.visualViewport.height = 580;
        f.emit(f.view.visualViewport, 'resize'); f.flush(); assertSafe(f);
        f.reading = rect(0, 0, width, 720); f.nav.update(); assert.equal(f.preview.hidden, true);
        f.nav.destroy();
    }
    const column = fixture({width: 700, viewportHeight: 300, x: 230, y: 0});
    column.reading = rect(260, 0, 440, 300); column.emit(column.rail, 'pointermove', column.point(0));
    assertSafe(column); assert.ok(column.preview.getBoundingClientRect().right <= 252); column.nav.destroy();
});

test('element replacement updates observation; destroy cancels frames, all listeners, observer and portal', () => {
    const f = fixture(); const observer = f.observers[0], rail = f.rail, preview = f.preview;
    const old = f.entries[0].element, replacement = f.doc.createElement('article');
    replacement.geometry = old.geometry; replacement.id = 'replacement-user'; f.body.appendChild(replacement);
    const next = f.entries.map((entry, i) => i === 0 ? {...entry, element: replacement} : entry);
    f.nav.refresh(next);
    assert.equal(observer.observed.has(old), false); assert.equal(observer.observed.has(replacement), true);
    assert.equal(f.ticks()[0].getAttribute('aria-controls'), 'replacement-user');
    f.emit(rail, 'pointermove', f.point(0)); f.emit(f.body, 'scroll'); assert.equal(f.frames.size, 1);
    f.nav.destroy(); f.nav.destroy();
    assert.equal(f.frames.size, 0); assert.equal(observer.disconnected, true); assert.equal(observer.observed.size, 0);
    assert.equal(rail.parentNode, null); assert.equal(preview.parentNode, null);
    for (const target of [rail, f.body, f.doc, f.view, f.view.visualViewport]) assert.equal(target.listenerCount(), 0);
    f.emit(f.body, 'scroll'); observer.callback(); f.flush(); f.nav.update(); f.nav.refresh(next);
    f.emit(rail, 'click', {detail: 1, clientY: 120}); assert.equal(f.jumps.length, 0); assert.equal(f.mount.children.length, 0);
});

test('multiple instances never share preview IDs, current state, or cleanup ownership', () => {
    const f = fixture(), secondMount = f.doc.createElement('nav');
    secondMount.localHeight = 120; secondMount.geometry = f.mount.geometry; f.doc.body.appendChild(secondMount);
    // All calls use the same classic-script factory, within one document.
    const factory = f.create;
    const a = factory({body: f.body, mount: secondMount, entries: f.entries, onJump() {}, readingRect: () => null});
    const thirdMount = f.doc.createElement('nav'); thirdMount.localHeight = 120; thirdMount.geometry = f.mount.geometry; f.doc.body.appendChild(thirdMount);
    const b = factory({body: f.body, mount: thirdMount, entries: f.entries.slice(0, 1), onJump() {}, readingRect: () => null});
    const ids = [f.ticks()[0].id, secondMount.children[0].children[0].id, thirdMount.children[0].children[0].id];
    assert.equal(new Set(ids).size, 3);
    a.destroy(); assert.equal(thirdMount.children[0].children.length, 1); b.update(); b.destroy(); f.nav.destroy();
});
