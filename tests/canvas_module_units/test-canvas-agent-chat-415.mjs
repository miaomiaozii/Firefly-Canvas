import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {createCanvasContext, loadModules, evaluate, JS_DIR, ROOT} from './harness.mjs';

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const msg = (seq = 20, extra = {}) => ({message_id: `old:codex:${seq}`, node_id: 'old', host: 'codex', seq,
    timestamp: 1700000000, turn_id: 'real-logical-turn', role: 'assistant', text: `正文 ${seq}`, text_truncated: false, ...extra});
const hit = (seq = 20, extra = {}) => { const {text, ...row} = msg(seq, extra); return {...row, snippet: '命中 中文😀 <img src=x onerror="bad()">\n"quote"'}; };
const conversation = (extra = {}) => ({conversation_id: 'old:codex', node_id: 'old', host: 'codex', retired: true,
    title: '旧 Agent <script>bad()</script>', last_message_id: 'old:codex:20', last_seq: 20,
    timestamp: 1700000000, snippet: '旧会话 中文😀', message_count: 2, ...extra});
const canvasData = (extra = {}) => ({project_id: 'project1', scope: 'canvas', node_id: null, host: null,
    has_more: false, truncated: false, conversations: [conversation()], ...extra});
const searchData = (query = '中文', extra = {}) => canvasData({query, hits: [hit()], ...extra});
const historyData = (extra = {}) => ({project_id: 'project1', scope: 'node', node_id: 'old', host: 'codex',
    messages: [msg(10, {role: 'user'}), msg()], has_more: false, next_before_seq: null, truncated: false, ...extra});

// This DOM models native form defaults and removable listeners, not a browser renderer.
function dom(document) {
    function make(tag = 'div') {
        const attrs = new Map(), listeners = new Map();
        let text = '', html = '';
        const el = {tagName: tag.toUpperCase(), className: '', children: [], parentNode: null, value: '', style: {},
            disabled: false, selectionStart: 2, selectionEnd: 5, selectionDirection: 'backward', scrollTop: 0,
            scrollHeight: 400, dataset: {}, focused: 0, removed: false,
            get isConnected() { return el === document.body || !!el.parentNode?.isConnected; },
            get textContent() { return text + el.children.map(child => child.textContent).join(''); },
            set textContent(value) { text = String(value); el.replaceChildren(); },
            get innerHTML() { return html; },
            set innerHTML(value) { html = value; throw new Error('reader must use textContent, not HTML'); },
            classList: {contains: value => el.className.split(' ').includes(value)},
            setAttribute(name, value) { attrs.set(name, String(value)); },
            getAttribute: name => attrs.get(name) ?? null,
            hasAttribute: name => attrs.has(name),
            appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
            replaceChildren(...children) { for (const child of el.children) child.parentNode = null; el.children = []; for (const child of children) el.appendChild(child); },
            remove() { if (el.parentNode) el.parentNode.children = el.parentNode.children.filter(child => child !== el); el.parentNode = null; el.removed = true; },
            contains(other) { return el === other || el.children.some(child => child.contains(other)); },
            querySelectorAll(selector) {
                const match = child => selector.startsWith('.') ? child.classList.contains(selector.slice(1))
                    : selector.startsWith('[') ? child.hasAttribute(selector.slice(1, -1)) : child.tagName.toLowerCase() === selector;
                return el.children.flatMap(child => [...(match(child) ? [child] : []), ...child.querySelectorAll(selector)]);
            },
            querySelector(selector) { return el.querySelectorAll(selector)[0] || null; },
            addEventListener(name, handler) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(handler); },
            removeEventListener(name, handler) { listeners.get(name)?.delete(handler); },
            listenerCount: () => [...listeners.values()].reduce((n, set) => n + set.size, 0),
            dispatchEvent(event) {
                event.target ||= el;
                // Native propagation freezes its ancestor path before a close handler can remove the portal.
                if (!event.ancestorPath) {
                    event.ancestorPath = [];
                    for (let target = el; target; target = target.parentNode) event.ancestorPath.push(target);
                }
                event.preventDefault ||= () => { event.defaultPrevented = true; };
                event.stopPropagation ||= () => { event.stopped = true; };
                for (const handler of [...(listeners.get(event.type) || [])]) handler(event);
                if (!event.stopped) event.ancestorPath[event.ancestorPath.indexOf(el) + 1]?.dispatchEvent(event);
                return !event.defaultPrevented;
            },
            focus() { document.activeElement = el; el.focused++; },
            setSelectionRange(start, end, direction) { el.selectionStart=start; el.selectionEnd=end; el.selectionDirection=direction; },
            getBoundingClientRect() { return {top: el.classList.contains('agent-chat-target') ? 280 : 100}; },
        };
        return el;
    }
    return make;
}
function setup() {
    const {ctx, harness} = createCanvasContext();
    const make = dom(ctx.document);
    ctx.document.createElement = make;
    ctx.document.body = make('body');
    ctx.document.querySelectorAll = selector => ctx.document.body.querySelectorAll(selector);
    ctx.document.querySelector = selector => ctx.document.body.querySelector(selector);
    ctx.URLSearchParams = URLSearchParams;
    ctx.AbortController = AbortController;
    ctx.canvasId = 'project1';
    for (const name of ['centerViewportOnWorldPoint', 'focusAndSelectNode', 'scheduleSave', 'saveCanvas', 'deleteNode', 'createAgentNode']) {
        ctx[name] = () => { throw new Error(`forbidden write/pan: ${name}`); };
    }
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-chat.js', 'canvas-agent-tui.js']);
    evaluate(ctx, `canvasState.nodes = [{id:'agent1', type:'smart-agent', agentCli:'pi', text:'untouched'}];`);
    const state = ctx.getAgentTuiState('agent1');
    state.projectId = 'project1'; state.historyLoaded = true; state.draft = '正在写作 中文😀';
    const composer = make('textarea'); composer.value = state.draft; composer.setAttribute('data-agent-tui-input', 'agent1');
    ctx.document.body.appendChild(composer); composer.focus();
    const api = ctx.canvasAgentChat;
    api.configure({getProjectId: () => ctx.canvasId}); ctx.agentChatBridge();
    const calls = [];
    let handler = () => response(canvasData());
    ctx.fetch = async (url, options) => {
        assert.equal(options?.method, 'GET', 'all requests must be reads');
        const parsed = new URL(url, 'http://unit.invalid');
        assert.match(parsed.pathname, /^\/api\/projects\/[^/]+\/chat\/(history|search)$/);
        calls.push({url, options, params: parsed.searchParams});
        return handler(parsed, options);
    };
    function header() {
        const root = make('header');
        for (const mode of ['search', 'history']) { const btn = make('button'); btn.setAttribute('data-agent-chat-open', mode); root.appendChild(btn); }
        ctx.document.body.appendChild(root);
        ctx.bindAgentHeadControls(root, {id: 'agent1'});
        return root;
    }
    const head = header();
    const click = el => { assert.ok(el, 'expected clickable element'); el.dispatchEvent({type: 'click'}); };
    const panel = () => ctx.document.body.querySelector('.agent-chat-panel');
    const input = () => panel().querySelector('input');
    const byText = text => panel().querySelectorAll('button').find(el => el.textContent === text);
    const open = (mode = 'search') => click(head.children[mode === 'search' ? 0 : 1]);
    const submit = value => { input().value = value; panel().querySelector('form').dispatchEvent({type:'submit'}); };
    const enter = (extra = {}) => {
        const event = {type:'keydown', key:'Enter', ...extra}; input().dispatchEvent(event);
        if (!event.defaultPrevented) panel().querySelector('form').dispatchEvent({type:'submit'});
        return event;
    };
    return {ctx, harness, make, state, composer, api, calls, head, header, click, panel, input, byText, open, submit, enter,
        handle: next => { handler = next; }, text: () => panel()?.textContent || '',
        result: () => panel().querySelector('.agent-chat-result'),
        snapshot: () => JSON.stringify(evaluate(ctx, 'canvasState.nodes'))};
}

test('header uses existing search/history sprite and loader registers the isolated reader', () => {
    const f = setup(), html = f.ctx.agentHeadActionButtonsHtml({id:'agent1'});
    assert.match(html, /data-agent-chat-open="search"/); assert.match(html, /#i-search/);
    assert.match(html, /data-agent-chat-open="history"/); assert.match(html, /#i-history/);
    assert.doesNotMatch(html, /data-lucide/);
    const source = readFileSync(path.join(ROOT, 'static/smart-canvas.html'), 'utf8');
    assert.ok(source.indexOf('/canvas-agent-chat.js') < source.indexOf('/canvas-agent-tui.js'));
    const reader = readFileSync(path.join(JS_DIR, 'canvas-agent-chat.js'), 'utf8');
    assert.doesNotMatch(reader, /\.scrollIntoView\s*\(|innerHTML\s*=|method:\s*['"](?:POST|PUT|PATCH|DELETE)['"]/g);
});

test('target location uses the sole outer reader scroll owner so nested clipping cannot hide the target', async () => {
    const f = setup(); f.handle(() => response(searchData())); f.open(); f.submit('中文'); await flush();
    f.handle(() => response(historyData())); f.click(f.result()); await flush();
    const content = f.panel().querySelector('.agent-chat-content');
    assert.equal(content.scrollTop, 180);
    assert.equal(f.panel().querySelector('.agent-chat-thread').scrollTop, 0);
    const css = readFileSync(path.join(ROOT, 'static/css/smart-canvas.css'), 'utf8');
    assert.match(css, /\.agent-chat-content\s*\{[^}]*overflow:auto/);
    assert.doesNotMatch(css, /\.agent-chat-thread\s*\{[^}]*(?:overflow\s*:|max-height\s*:)/);
    assert.equal(f.ctx.document.activeElement, f.input());
    assert.equal(f.composer.value, '正在写作 中文😀');
});

test('native reader resize realigns an explicit target until manual reading and disconnects on close', async () => {
    const f = setup(), observers = [];
    f.ctx.ResizeObserver = class {
        constructor(callback) { this.callback=callback; observers.push(this); }
        observe(element) { this.element=element; }
        disconnect() { this.disconnected=true; }
    };
    f.handle(() => response(searchData())); f.open(); f.submit('中文'); await flush();
    f.handle(() => response(historyData())); f.click(f.result()); await flush();
    assert.equal(observers.length,1);
    const content=f.panel().querySelector('.agent-chat-content'),target=f.panel().querySelector('.agent-chat-target');
    assert.equal(observers[0].element,content);
    target.getBoundingClientRect=()=>({top:460-content.scrollTop});
    content.clientWidth=320;content.clientHeight=500;
    observers[0].callback();assert.equal(content.scrollTop,360);
    content.dispatchEvent({type:'wheel'});content.scrollTop=75;
    observers[0].callback();assert.equal(content.scrollTop,75,'manual reading must not snap back to the hit');
    f.api.close();assert.equal(observers[0].disconnected,true);
    observers[0].callback();assert.equal(content.scrollTop,75,'detached observer cannot reposition a closed reader');
    assert.equal(f.composer.value,'正在写作 中文😀');
});

test('canvas-wide history lists retained conversations including deleted Agent + explicit host, and never writes', async () => {
    const f = setup(), before = f.snapshot(); f.open('history');
    assert.match(f.text(), /正在读取/); await flush();
    assert.equal(f.calls[0].params.toString(), 'scope=canvas&limit=50');
    assert.equal(f.calls[0].options.headers['X-Agent-Project'], 'project1');
    assert.match(f.text(), /旧 Agent <script>bad\(\)<\/script>/); assert.match(f.text(), /Codex · 已删除 Agent · 只读/);
    assert.equal(f.panel().querySelector('script'), null);
    f.handle(() => response(historyData())); f.click(f.result()); await flush();
    assert.equal(f.calls[1].params.toString(), 'scope=node&node_id=old&host=codex&limit=50');
    assert.match(f.text(), /已删除 Agent（不恢复节点）/); assert.match(f.text(), /正文 20/);
    assert.equal(f.state.viewHost, 'pi'); assert.equal(f.state.activeHost, 'pi'); assert.equal(f.snapshot(), before);
    assert.equal(f.composer.value, '正在写作 中文😀');
});

test('native form submit reads snippets (no hit.text), then verifies and scrolls only exact authoritative target', async () => {
    const f = setup(); f.handle(() => response(searchData())); f.open();
    f.input().value = '中文'; f.enter(); await flush();
    assert.equal(f.calls[0].params.toString(), 'scope=canvas&limit=50&query=%E4%B8%AD%E6%96%87');
    assert.match(f.text(), /命中 中文😀 <img src=x onerror="bad\(\)">\n"quote"/);
    assert.equal(f.panel().querySelector('img'), null);
    f.handle(() => response(historyData({messages: [msg(19, {turn_id:'different'}), msg()]})));
    f.click(f.result()); await flush();
    assert.equal(f.calls[1].params.get('before_seq'), '21');
    const target = f.panel().querySelector('.agent-chat-target');
    assert.equal(target.getAttribute('data-message-id'), 'old:codex:20');
    assert.equal(target.getAttribute('data-message-seq'), '20');
    assert.equal(target.getAttribute('data-turn-id'), 'real-logical-turn');
    assert.equal(f.panel().querySelector('.agent-chat-content').scrollTop, 180);
    assert.equal(f.panel().querySelector('.agent-chat-thread').scrollTop, 0);
    assert.match(target.textContent, /正文 20/); assert.match(f.text(), /已定位消息 old:codex:20/);
    assert.equal(f.ctx.document.activeElement, f.input(), 'async completion must never steal focus');
    assert.equal(f.state.messages.length, 0, 'reader must not project into the live timeline');
});

test('legacy null turn stays unknown on hit and authoritative message, with no invented identifier', async () => {
    const f = setup(); f.handle(() => response(searchData('old', {hits: [hit(20, {turn_id:null, text_truncated:true})]})));
    f.open(); f.submit('old'); await flush(); assert.match(f.text(), /旧历史：逻辑轮未知/);
    f.handle(() => response(historyData({messages:[msg(20,{turn_id:null, text_truncated:true})], truncated:true})));
    f.click(f.result()); await flush();
    assert.match(f.text(), /正文已截断/); assert.match(f.text(), /扫描上限/);
    const target = f.panel().querySelector('.agent-chat-target');
    assert.equal(target.hasAttribute('data-turn-id'), false); assert.match(target.textContent, /逻辑轮未知/);
});

test('unavailable/mismatched logical target never claims a jump or highlights a nearby message', async t => {
    for (const [label, rows] of [['missing', [msg(19)]], ['turn changed', [msg(20,{turn_id:'not-the-hit'})]], ['role changed',[msg(20,{role:'user'})]]]) {
        await t.test(label, async () => {
            const f = setup(); f.handle(() => response(searchData())); f.open(); f.submit('中文'); await flush();
            f.handle(() => response(historyData({messages:rows, truncated:true}))); f.click(f.result()); await flush();
            assert.match(f.text(), /目标消息不可用，未执行跳转/); assert.match(f.text(), /未扫描历史/);
            assert.equal(f.panel().querySelector('.agent-chat-target'), null); assert.doesNotMatch(f.text(), /已定位消息/);
        });
    }
});

test('node paging uses strict older cursor, chronological messages, sticky truncation and anchored reader only', async () => {
    const f = setup(); f.open('history'); await flush();
    f.handle(() => response(historyData({has_more:true, next_before_seq:10, truncated:true})));
    f.click(f.result()); await flush();
    const scroller = f.panel().querySelector('.agent-chat-content'); scroller.scrollTop = 25;
    f.handle(() => response(historyData({messages:[msg(2),msg(8)], has_more:false, next_before_seq:null})));
    f.click(f.byText('加载更早的消息')); await flush();
    assert.equal(f.calls.at(-1).params.get('before_seq'), '10'); assert.equal(f.calls.at(-1).params.get('limit'), '50');
    const rows = f.panel().querySelectorAll('.agent-chat-message');
    assert.deepEqual(rows.map(el=>el.getAttribute('data-message-seq')), ['2','8','10','20']);
    assert.equal(f.panel().querySelector('.agent-chat-content').scrollTop, 25);
    assert.equal(f.panel().querySelector('.agent-chat-thread').scrollTop, 0); assert.match(f.text(), /未扫描历史/);
    assert.equal(f.byText('加载更早的消息'), undefined);
});

test('malformed/backward cursor or out-of-scope page fails closed and is retryable', async t => {
    for (const patch of [{has_more:true,next_before_seq:20}, {messages:[msg(20),msg(10)]},
        {messages:[msg(21)]}, {messages:[msg(20,{host:'pi',message_id:'old:pi:20'})]}, {messages:[msg(20,{message_id:'fake'})]}]) {
        await t.test(JSON.stringify(patch), async () => {
            const f = setup(); f.handle(() => response(searchData())); f.open(); f.submit('中文'); await flush();
            f.handle(() => response(historyData(patch))); f.click(f.result()); await flush();
            assert.match(f.text(), /读取失败/); assert.doesNotMatch(f.text(), /已定位消息/);
            assert.equal(f.panel().querySelector('.agent-chat-target'), null); assert.ok(f.byText('重试会话读取'));
        });
    }
});

test('empty/capped/truncated scans disclose bounded scope and allow explicit 200 limit but not a fake cursor', async () => {
    const f = setup(); f.handle(() => response(canvasData({conversations:[],truncated:true,has_more:true})));
    f.open('history'); await flush(); assert.match(f.text(), /本次保留扫描中没有历史会话/); assert.match(f.text(), /未扫描历史/);
    f.click(f.byText('读取最多 200 条结果')); await flush();
    assert.equal(f.calls.at(-1).params.get('limit'), '200'); assert.match(f.text(), /已达 200 条结果上限/);
    assert.equal(f.calls.at(-1).params.has('before_seq'), false); assert.equal(f.calls.at(-1).params.has('node_id'), false);
    f.handle(() => response(searchData('空', {hits:[],truncated:true}))); f.submit('空'); await flush();
    assert.match(f.text(), /本次保留扫描中没有匹配消息/); assert.match(f.text(), /未扫描历史/);
});

test('query validation is literal, trims edges only and honors Unicode 200 scalar limit', async () => {
    const f = setup(); f.open(); f.submit('   '); await flush(); assert.equal(f.calls.length,0);
    f.submit('字'.repeat(201)); await flush(); assert.equal(f.calls.length,0); assert.match(f.text(), /1–200/);
    f.handle(url => response(searchData(url.searchParams.get('query'),{hits:[]})));
    f.submit('  [a.*] 中文😀 "x"\nnext  '); await flush();
    assert.equal(f.calls.at(-1).params.get('query'), '[a.*] 中文😀 "x"\nnext');
    f.submit('😀'.repeat(200)); await flush(); assert.equal(f.calls.at(-1).params.get('query'), '😀'.repeat(200));
});

test('HTTP/JSON/network errors remain safe text and explicit retry recovers; no empty-success lie', async () => {
    const f = setup(); f.handle(() => response({detail:{message:'<img onerror="bad()">不可读'}},409));
    f.open('history'); await flush(); assert.match(f.text(), /读取失败：<img onerror="bad\(\)">不可读/);
    assert.equal(f.panel().querySelector('img'),null); assert.match(f.text(), /不能据此判断没有历史/);
    f.handle(() => { throw new Error('offline'); }); f.click(f.byText('重试')); await flush(); assert.match(f.text(), /offline/);
    f.handle(() => response(canvasData())); f.click(f.byText('重试')); await flush(); assert.ok(f.result());
    f.handle(() => ({ok:true,json:async()=>{ throw new Error('bad JSON'); }}));
    f.click(f.byText('本画布历史会话')); await flush(); assert.match(f.text(), /bad JSON/);
});

test('wrong response project/scope/node/host/query and over-limit results are rejected, not displayed', async t => {
    for (const patch of [{project_id:'foreign'}, {scope:'node'}, {node_id:'other'}, {host:'pi'}, {query:'wrong'},
        {hits:Array.from({length:51},(_,i)=>hit(i+1))}, {hits:[hit(),hit()]}]) {
        await t.test(JSON.stringify(Object.keys(patch)), async () => {
            const f = setup(); f.handle(() => response(searchData('中文',patch))); f.open(); f.submit('中文'); await flush();
            assert.match(f.text(), /读取失败/); assert.equal(f.result(), null);
        });
    }
});

test('new search cancels late old results even if transport ignores AbortSignal', async () => {
    const f = setup(), old = deferred(), next = deferred(); let n = 0;
    f.handle(() => (++n === 1 ? old.promise : next.promise)); f.open(); f.submit('first'); f.submit('second');
    assert.equal(f.calls[0].options.signal.aborted,true);
    next.resolve(response(searchData('second',{hits:[hit(22)]}))); await flush();
    old.resolve(response(searchData('first'))); await flush(); assert.match(f.text(), /old:codex:22/); assert.doesNotMatch(f.text(), /old:codex:20/);
});

test('an invalid newer submit cancels an older valid request without allowing late results to hide validation', async () => {
    const f=setup(), pending=deferred(); f.handle(()=>pending.promise); f.open(); f.submit('valid');
    f.submit('   '); assert.equal(f.calls[0].options.signal.aborted,true);
    pending.resolve(response(searchData('valid'))); await flush();
    assert.match(f.text(),/1–200/); assert.equal(f.result(),null);
});

test('out-of-order conversation node/host requests cannot replace selected readonly thread', async () => {
    const f = setup(); f.handle(() => response(canvasData({conversations:[conversation(),conversation({node_id:'other',host:'pi',
        conversation_id:'other:pi',last_message_id:'other:pi:20'})]})));
    f.open('history'); await flush(); const results = f.panel().querySelectorAll('.agent-chat-result');
    const first = deferred(), second = deferred(); let n = 0; f.handle(() => ++n === 1 ? first.promise : second.promise);
    f.click(results[0]); f.click(results[1]);
    second.resolve(response(historyData({node_id:'other',host:'pi',messages:[msg(20,{node_id:'other',host:'pi',message_id:'other:pi:20',text:'selected thread'})]})));
    await flush(); first.resolve(response(historyData())); await flush();
    assert.match(f.text(), /selected thread/); assert.doesNotMatch(f.text(), /正文 20/); assert.equal(f.calls[1].options.signal.aborted,true);
});

test('owner project/node/host/epoch/generation/close lifetime invalidates pending results and never reopens', async t => {
    for (const [label, mutate] of [
        ['project', f=>{f.ctx.canvasId='other';}], ['node deleted',f=>evaluate(f.ctx,'canvasState.nodes=[]')],
        ['view host',f=>{f.state.viewHost='codex';}], ['active host',f=>{f.state.activeHost='codex';}],
        ['epoch',f=>{f.state.viewEpoch++;}], ['generation',f=>{f.state._timelineGeneration=1;}],
        ['closing',f=>{f.state.closing=true;}], ['state replaced',f=>evaluate(f.ctx,"agentTuiState.delete('agent1'); getAgentTuiState('agent1')")],
        ['explicit close',f=>f.api.close()], ['forget',f=>f.api.forget('agent1')], ['dispose',f=>f.api.dispose()],
    ]) await t.test(label, async () => {
        const f = setup(), pending = deferred(); f.handle(()=>pending.promise); f.open('history'); const root=f.panel();
        mutate(f); pending.resolve(response(canvasData())); await flush();
        assert.equal(f.panel(),null); assert.equal(root.isConnected,false);
        assert.equal(f.calls[0].options.signal.aborted,true);
    });
});

test('IME native Enter/229 cannot search or close early; composer draft/selection/reading and live events survive', async () => {
    const f = setup(), before=f.snapshot();
    f.state.scrollState={anchorKey:'live-message',offset:12,atBottom:false}; const reading=f.state.scrollState;
    f.state.composing=true; f.open(); assert.equal(f.panel(),null,'opening must not interrupt composer composition');
    f.state.composing=false; f.open(); const input=f.input(), selection=[f.composer.selectionStart,f.composer.selectionEnd,f.composer.selectionDirection];
    input.value='中文'; input.dispatchEvent({type:'compositionstart'}); assert.equal(f.enter().defaultPrevented,true);
    f.click(f.byText('搜索')); f.panel().querySelector('form').dispatchEvent({type:'submit'}); await flush(); assert.equal(f.calls.length,0);
    input.dispatchEvent({type:'compositionend'}); assert.equal(f.enter({keyCode:229}).defaultPrevented,true); assert.equal(f.calls.length,0);
    f.handle(()=>response(searchData())); f.enter(); await flush(); assert.equal(f.calls.length,1);
    f.ctx.applyAgentEventBody('agent1',{type:'user_prompt',text:'live event still arrives',host:'pi',seq:88});
    assert.ok(f.state.messages.some(row=>row.text==='live event still arrives'));
    assert.equal(f.state.scrollState,reading); assert.equal(f.snapshot(),before);
    assert.equal(f.composer.value,'正在写作 中文😀'); assert.equal(f.state.draft,'正在写作 中文😀');
    assert.deepEqual([f.composer.selectionStart,f.composer.selectionEnd,f.composer.selectionDirection],selection);
    input.dispatchEvent({type:'compositionstart'}); f.click(f.byText('关闭')); assert.ok(f.panel());
    input.dispatchEvent({type:'compositionend'}); assert.equal(f.panel(),null); assert.equal(f.ctx.document.activeElement,f.composer);
});

test('rerender/rebinding keeps portal/input/query/results and removes old header listeners; disposal is final', async () => {
    const f=setup(); f.handle(()=>response(searchData())); f.open(); f.submit('中文'); await flush();
    const root=f.panel(), input=f.input(), oldButton=f.head.children[0]; input.selectionStart=1; input.selectionEnd=2;
    const newHeader=f.header(); f.head.remove();
    assert.equal(oldButton.listenerCount(),0); assert.equal(f.panel(),root); assert.equal(f.input(),input);
    assert.equal(input.value,'中文'); assert.equal(input.selectionStart,1); assert.ok(f.result());
    f.api.bind(newHeader,'agent1'); assert.equal(newHeader.children[0].listenerCount(),2,'no duplicate listener');
    f.api.close(); assert.equal(root.listenerCount(),0); f.api.open('agent1'); assert.equal(f.input().value,'中文');
    f.api.dispose(); assert.equal(f.panel(),null); assert.equal(newHeader.children[0].listenerCount(),0);
    f.click(oldButton); f.click(newHeader.children[0]); assert.equal(f.panel(),null);
});

test('full canvas remount carries the detached composer selection without changing reader focus or owned query', () => {
    const f=setup(); f.open(); f.input().value='owned query'; const root=f.panel(), input=f.input();
    f.composer.selectionStart=4; f.composer.selectionEnd=8; f.composer.scrollTop=22;
    f.composer.remove(); const next=f.make('textarea'); next.value=f.state.draft;
    next.setAttribute('data-agent-tui-input','agent1'); f.ctx.document.body.appendChild(next);
    f.header();
    assert.equal(f.panel(),root); assert.equal(f.input(),input); assert.equal(input.value,'owned query');
    assert.deepEqual([next.selectionStart,next.selectionEnd,next.selectionDirection,next.scrollTop],[4,8,'backward',22]);
    assert.equal(f.ctx.document.activeElement,input); f.api.close(); assert.equal(f.ctx.document.activeElement,next);
});

test('real TUI live rerender preserves a blurred composer selection and restores its replacement on explicit reader close', () => {
    const f=setup(); f.composer.selectionStart=3; f.composer.selectionEnd=7; f.composer.scrollTop=14;
    f.open(); const searchInput=f.input(), portal=f.panel();
    let composer=f.composer;
    const bodyContainer={querySelector: selector=>selector==='[data-agent-tui-input]'?composer:null};
    Object.defineProperty(bodyContainer,'innerHTML',{set(){
        composer.remove(); composer=f.make('textarea'); composer.value=f.state.draft;
        composer.setAttribute('data-agent-tui-input','agent1'); composer.selectionStart=0; composer.selectionEnd=0;
        f.ctx.document.body.appendChild(composer);
    }});
    const nodeElement={querySelector: selector=>selector==='[data-agent-tui-input]'?composer:selector==='.node-body'?bodyContainer:null};
    const query=f.ctx.document.querySelector;
    f.ctx.document.querySelector=selector=>selector==='[data-id="agent1"]'?nodeElement:query(selector);
    f.ctx.imageLayout=()=>({}); f.ctx.nodeBodyHtml=()=>''; f.ctx.bindAgentNodeControls=()=>{};
    f.ctx.renderAgentNode('agent1');
    assert.equal(f.panel(),portal); assert.equal(f.ctx.document.activeElement,searchInput);
    assert.notEqual(composer,f.composer); assert.equal(composer.value,'正在写作 中文😀');
    assert.deepEqual([composer.selectionStart,composer.selectionEnd,composer.selectionDirection,composer.scrollTop],[3,7,'backward',14]);
    f.api.close(); assert.equal(f.ctx.document.activeElement,composer);
    assert.equal(composer.selectionStart,3); assert.equal(composer.selectionEnd,7);
});

test('Escape stack owns only this overlay, releases on close, and detached old callbacks cannot close a reopened panel', async () => {
    const f=setup(), stack=[];
    f.ctx.FireflyEscapeStack={push(close,label){ const entry={close,label}; stack.push(entry); return ()=>{ const i=stack.indexOf(entry); if(i>=0) stack.splice(i,1); }; }};
    f.open(); const old=f.panel(), oldClose=f.byText('关闭');
    assert.equal(stack.length,1); assert.equal(stack[0].label,'agent-chat');
    stack[0].close(); assert.equal(f.panel(),null); assert.equal(stack.length,0);
    f.open(); const current=f.panel(); f.click(oldClose); assert.equal(f.panel(),current);
    assert.equal(old.listenerCount(),0); f.api.dispose(); assert.equal(stack.length,0);
});

test('native close click cannot escape a removed portal and let canvas document handlers steal restored composer focus', () => {
    const f=setup(); let outsideClicks=0;
    f.ctx.document.body.addEventListener('click', () => { outsideClicks++; f.ctx.document.activeElement=f.ctx.document.body; });
    f.open(); const root=f.panel(); f.click(f.byText('关闭'));
    assert.equal(f.panel(),null); assert.equal(root.isConnected,false);
    assert.equal(outsideClicks,0,'closing must stop its original event before listeners and portal are destroyed');
    assert.equal(f.ctx.document.activeElement,f.composer);
});

test('close+reopen and invalidate without an in-flight request retain no stale content or focus effects', async () => {
    const f=setup(), late=deferred(); f.handle(()=>late.promise); f.open('history'); const old=f.panel();
    f.click(f.byText('关闭')); f.open(); const root=f.panel(), input=f.input();
    late.resolve(response(canvasData())); await flush();
    assert.equal(f.panel(),root); assert.equal(f.input(),input); assert.equal(f.ctx.document.activeElement,input); assert.equal(old.isConnected,false);
    f.state.viewEpoch++; f.api.invalidate(); assert.equal(f.panel(),null);
});

test('thread errors retry the same exact host/target and never poison retained result navigation', async () => {
    const f=setup(); f.handle(()=>response(searchData())); f.open(); f.submit('中文'); await flush();
    f.handle(()=>response(historyData({project_id:'foreign'}))); f.click(f.result()); await flush();
    assert.match(f.text(),/项目 \/ 节点 \/ 宿主身份不匹配/); assert.equal(f.panel().querySelector('.agent-chat-target'),null);
    assert.match(f.text(),/不能判断是否有历史/); assert.doesNotMatch(f.text(),/当前保留范围没有可显示消息/);
    f.handle(()=>response(historyData())); f.click(f.byText('重试会话读取')); await flush();
    assert.ok(f.panel().querySelector('.agent-chat-target')); assert.equal(f.calls.at(-1).params.get('before_seq'),'21');
    f.click(f.byText('返回结果')); assert.ok(f.result()); assert.equal(f.input().value,'中文');
    assert.equal(f.panel().querySelector('.agent-chat-content').getAttribute('aria-busy'),'false');
});

test('explicit close respects outside focus and does not move focus into an IME composer', () => {
    const f=setup(); f.open(); const outside=f.make('input'); f.ctx.document.body.appendChild(outside); outside.focus();
    f.api.close(); assert.equal(f.ctx.document.activeElement,outside);
    f.composer.focus(); f.open(); f.state.composing=true; f.api.close(); assert.notEqual(f.ctx.document.activeElement,f.composer);
});
