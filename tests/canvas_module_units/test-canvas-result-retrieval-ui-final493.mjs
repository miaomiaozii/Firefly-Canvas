import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
import {inspect} from 'node:util';
import {loadModules, evaluate} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

const root = process.env.FIREFLY_UI_FINAL_SOURCE || fileURLToPath(new URL('../../', import.meta.url));
const read = file => readFileSync(path.join(root, file), 'utf8');
const classic = read('static/js/smart-canvas.js');
const decode = text => text.replace(/&(amp|lt|gt|quot|#39);/g, (_, name) => ({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"}[name]));

// Keep the focus model local: shared inert stubs cannot detect replacement-focus loss.
// Parsed renderer markup and event routing are source contracts, not browser/layout acceptance.
class Element {
    [inspect.custom]() { return `<${this.tagName.toLowerCase()} ${JSON.stringify(this.attrs)}>`; }
    constructor(document, tag, attrs = {}) {
        this.ownerDocument = document; this.tagName = tag.toUpperCase(); this.attrs = attrs;
        this.children = []; this.parentNode = null; this.handlers = new Map(); this.scrollTop = 0;
    }
    get dataset() { return Object.fromEntries(Object.entries(this.attrs).filter(([key]) => key.startsWith('data-')).map(([key,value]) => [key.slice(5).replace(/-([a-z])/g, (_,letter) => letter.toUpperCase()), value])); }
    set className(value) { this.attrs.class = value; }
    get hidden() { return this.hasAttribute('hidden'); }
    set hidden(value) { value ? this.setAttribute('hidden','') : this.removeAttribute('hidden'); }
    get disabled() { return this.hasAttribute('disabled'); }
    get open() { return this.hasAttribute('open'); }
    set open(value) { value ? this.setAttribute('open','') : this.removeAttribute('open'); }
    hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
    getAttribute(name) { return this.attrs[name] ?? null; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    removeAttribute(name) { delete this.attrs[name]; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    contains(element) { return this === element || this.children.some(child => child.contains(element)); }
    matches(selector) {
        return selector.split(',').some(part => {
            const descendant = part.trim().match(/^(.+)\s+([^\s]+)$/);
            if (descendant) return this.matches(descendant[2]) && Boolean(this.parentNode?.closest(descendant[1]));
            const match = part.trim().match(/^([\w-]+)?(?:\.([\w-]+))?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
            assert.ok(match, `unsupported selector ${part}`);
            return (!match[1] || this.tagName === match[1].toUpperCase()) && (!match[2] || (this.attrs.class || '').split(/\s+/).includes(match[2])) && (!match[3] || this.hasAttribute(match[3]) && (match[4] === undefined || this.attrs[match[3]] === match[4]));
        });
    }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    set innerHTML(html) {
        const doc = this.ownerDocument;
        if (doc.activeElement !== this && this.contains(doc.activeElement)) doc.activeElement = doc.body;
        this.children.forEach(child => { child.parentNode = null; }); this.children = []; this.markup = html;
        const stack = [this];
        for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
            if (token.startsWith('</')) assert.equal(stack.pop().tagName, token.slice(2,-1).toUpperCase());
            else if (token.startsWith('<')) {
                const tag = token.match(/^<([\w-]+)/)[1];
                const attrs = Object.fromEntries([...token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].slice(1).map(match => [match[1], decode(match[2] ?? '')]));
                const child = stack.at(-1).appendChild(new Element(doc, tag, attrs));
                if (!token.endsWith('/>') && !['img','input','br'].includes(tag)) stack.push(child);
            } else stack.at(-1).textContent = (stack.at(-1).textContent || '') + decode(token);
        }
        assert.equal(stack.length, 1, 'balanced source-rendered markup');
    }
    get innerHTML() { return this.markup || ''; }
    focus(options) {
        if (this.disabled || this.closest('[hidden]') || !this.ownerDocument.body.contains(this)) return;
        this.ownerDocument.activeElement = this; this.focusOptions = options;
    }
    addEventListener(type, handler) { this.handlers.set(type, handler); }
    dispatch(type, fields = {}) {
        const event = {type, target:this, defaultPrevented:false, stopped:false, preventDefault(){this.defaultPrevented=true;}, stopPropagation(){this.stopped=true;}, ...fields};
        for (let element = this; element && !event.stopped; element = element.parentNode) {
            element[`on${type}`]?.(event); element.handlers.get(type)?.(event);
        }
        return event;
    }
}
function documentFixture() {
    const document = {createElement:tag => new Element(document, tag)};
    document.body = document.createElement('body'); document.activeElement = document.body;
    document.getElementById = id => document.body.querySelectorAll('[id]').find(element => element.getAttribute('id') === id) || null;
    const html = read('static/smart-canvas.html');
    for (const id of ['smartRetrievalToggle','smartRetrievalCount','smartRetrievalPanel']) {
        const tag = html.match(new RegExp(`<([\\w-]+)[^>]*id="${id}"`))[1];
        const element = document.createElement(tag); element.setAttribute('id', id);
        if (id === 'smartRetrievalPanel') element.className = 'retrieval-panel';
        (id === 'smartRetrievalCount' ? document.getElementById('smartRetrievalToggle') : document.body).appendChild(element);
    }
    return document;
}
const group = history => ({project_id:'fixture-project',history_id:history,node_id:`node-${history}`,task_id:`task-${history}`,items:[0,1].map(index => ({id:`item:${index}`,index,status:'failed',can_retrieve:true,remaining_seconds:90,error:'<failure> & detail'}))});
const tick = () => new Promise(resolve => setImmediate(resolve));
async function setup() {
    const document = documentFixture(), timers = new Map(), calls = [];
    const state = {project:'fixture-project',groups:[group('h:"a'), group('h:b')],failure:'',repaints:0};
    const ctx = vm.createContext({document, console, setTimeout:(fn,ms) => {const id=Symbol();timers.set(id,{fn,ms});return id;},clearTimeout:id => timers.delete(id)});
    ctx.window = ctx; ctx.StudioI18n = {lang:() => 'en'};
    vm.runInContext(read('static/js/canvas-result-retrieval.js'), ctx);
    const api = ctx.canvasResultRetrieval;
    api.configure({getProjectId:() => state.project,getHistory:() => [],adoptCanonical:async() => true,repaint:() => {state.repaints++;api.sync();},fetch:async(url,options) => {
        calls.push({url,options});
        if (state.failure) return {ok:false,status:503,json:async() => ({detail:state.failure})};
        return {ok:true,json:async() => options.method ? structuredClone(state.groups.find(group => url.includes(encodeURIComponent(group.history_id)))) : {groups:structuredClone(state.groups)}};
    }});
    await api.refresh();
    const badge = document.getElementById('smartRetrievalToggle'), panel = document.getElementById('smartRetrievalPanel');
    badge.focus(); badge.dispatch('click'); await tick();
    const find = (history,item,tag='button') => panel.querySelectorAll(tag).find(element => element.closest('[data-retrieval-group]')?.dataset.retrievalGroup === history && element.closest('[data-retrieval-slot]')?.dataset.retrievalSlot === item);
    const item = (history,id,tag='button') => find(history,id,tag) || panel.querySelectorAll(tag).find(element => element.closest('section')?.querySelector('h3')?.textContent === history && element.closest('[data-retrieval-slot]')?.dataset.retrievalSlot === id);
    const poll = async ms => {const timer=[...timers.values()].find(timer => timer.ms===ms);assert.ok(timer,`${ms}ms production poll scheduled`);timers.clear();timer.fn();await tick();};
    return {ctx,api,document,panel,badge,state,calls,item,poll};
}

test('DOM model removes descendant focus on innerHTML replacement and enforces hidden/disabled focus', () => {
    const document = documentFixture(), panel = document.getElementById('smartRetrievalPanel');
    panel.innerHTML = '<button>first</button>'; const old = panel.querySelector('button'); old.focus(); assert.equal(document.activeElement,old);
    panel.innerHTML = '<button disabled>new</button>'; assert.equal(document.activeElement,document.body); assert.equal(old.parentNode,null);
    panel.querySelector('button').focus(); assert.equal(document.activeElement,document.body);
    panel.hidden=true; old.focus(); assert.equal(document.activeElement,document.body);
    const composer = document.body.appendChild(document.createElement('div')); composer.className='composer';
    const popover = composer.appendChild(document.createElement('div')); popover.className='smart-popover';
    assert.equal(popover.matches('.composer .smart-popover'),true);
    assert.equal(popover.matches('.other .smart-popover'),false);
    assert.equal(composer.matches('.composer .smart-popover'),false);
});

test('actual shell wheel route reserves project panel descendants, never the canvas background', () => {
    const document = documentFixture(), shell = document.body, panel = document.getElementById('smartRetrievalPanel');
    panel.innerHTML='<section><div class="retrieval-slot"><button>retrieve</button></div></section>';
    let zooms=0; const viewport={x:0,y:0,scale:1};
    shell.getBoundingClientRect=()=>({left:0,top:0});
    const ctx=vm.createContext({shell,viewport,composerPromptView:{mode:'normal'},promptNodeFlowView:{mode:'normal'},window:{innerHeight:800},canvasWheelZoomFactor:()=>2,safeScale:value=>value,applyViewport:()=>zooms++});
    vm.runInContext(classic.slice(classic.indexOf('function canvasWheelIsReserved('),classic.indexOf('shell.ondragover =')),ctx);
    for(const target of [panel,panel.querySelector('section'),panel.querySelector('button')]) {
        assert.equal(ctx.canvasWheelIsReserved(target),true); assert.equal(target.dispatch('wheel',{clientX:10,clientY:10}).defaultPrevented,false);
    }
    assert.equal(zooms,0); assert.equal(viewport.scale,1);
    assert.equal(ctx.canvasWheelIsReserved(shell),false); assert.equal(shell.dispatch('wheel',{clientX:10,clientY:10}).defaultPrevented,true); assert.equal(zooms,1); assert.equal(viewport.scale,2);
    const grid=shell.appendChild(document.createElement('div'));grid.setAttribute('data-thumb-scroll','1');assert.equal(ctx.canvasWheelIsReserved(grid),true);
});

test('successful unchanged and TTL polls retain item keyboard focus, expanded details, open panel and scroll', async () => {
    const {api,document,panel,badge,state,item,poll}=await setup(), h=state.groups[0].history_id;
    item(h,'item:1').focus(); item(h,'item:1','details').open=true; panel.scrollTop=87;
    await poll(10000);
    assert.equal(document.activeElement,item(h,'item:1')); assert.equal(item(h,'item:1','details').open,true);
    state.groups[0].items[1].remaining_seconds=65; await api.refresh();
    assert.equal(document.activeElement,item(h,'item:1')); assert.equal(item(h,'item:1','details').open,true);
    assert.match(item(h,'item:1').closest('[data-retrieval-slot]').querySelector('small').textContent,/1m 5s/);
    assert.equal(panel.scrollTop,87); assert.equal(panel.hidden,false); assert.equal(badge.getAttribute('aria-expanded'),'true'); assert.ok(state.repaints>=3);
    document.activeElement.dispatch('keydown',{key:'Escape'}); assert.equal(panel.hidden,true); assert.equal(document.activeElement,badge);
});

test('summary focus and expansion follow history/item identity, not reordered groups or duplicate item IDs', async () => {
    const {api,document,state,item}=await setup(), h=state.groups[0].history_id, other=state.groups[1].history_id;
    item(h,'item:0','summary').focus(); item(h,'item:0','details').open=true;
    state.groups.reverse(); state.groups.find(group=>group.history_id===h).items.reverse(); await api.refresh();
    assert.equal(document.activeElement,item(h,'item:0','summary')); assert.equal(item(h,'item:0','details').open,true); assert.equal(item(other,'item:0','details').open,false);
    item(h,'item:0','details').open=false; await api.refresh(); assert.equal(item(h,'item:0','details').open,false);
});

test('one-second active polls and sibling retrieval repaints retain another item focus and error details', async () => {
    const {api,document,state,item,poll}=await setup(),h=state.groups[0].history_id;
    item(h,'item:1').focus(); item(h,'item:1','details').open=true;
    Object.assign(state.groups[0].items[0],{status:'downloading',can_retrieve:false}); await api.refresh(); await poll(1000);
    assert.equal(document.activeElement,item(h,'item:1')); assert.equal(item(h,'item:1','details').open,true);
    Object.assign(state.groups[0].items[0],{status:'failed',can_retrieve:true}); await api.refresh();
    await api.retrieve(h,['item:0'],true); assert.equal(document.activeElement,item(h,'item:1')); assert.equal(item(h,'item:1','details').open,true);
});

test('list errors retain existing item focus/details and reload/close focus survive repaint', async () => {
    const {api,document,panel,badge,state,item}=await setup(),h=state.groups[0].history_id;
    item(h,'item:0').focus();item(h,'item:0','details').open=true;state.failure='offline';await api.refresh();
    assert.equal(document.activeElement,item(h,'item:0'));assert.equal(item(h,'item:0','details').open,true);
    panel.querySelector('[data-retrieval-refresh]').focus();await api.refresh();assert.equal(document.activeElement,panel.querySelector('[data-retrieval-refresh]'));
    panel.querySelector('[data-retrieval-close]').focus();state.failure='';await api.refresh();assert.equal(document.activeElement,panel.querySelector('[data-retrieval-close]'));
    document.activeElement.dispatch('click');assert.equal(panel.hidden,true);assert.equal(document.activeElement,badge);
});

test('outside or closed focus is never stolen, and vanished/disabled focused controls leave an Escape-capable panel', async () => {
    const {api,document,panel,badge,state,item}=await setup(),h=state.groups[0].history_id;
    const outside=document.body.appendChild(document.createElement('input'));outside.focus();await api.refresh();assert.equal(document.activeElement,outside);
    item(h,'item:0').focus();Object.assign(state.groups[0].items[0],{status:'unavailable',can_retrieve:false});await api.refresh();
    assert.equal(document.activeElement,panel.querySelector('[data-retrieval-close]'));assert.equal(document.activeElement.disabled,false);
    item(h,'item:1').focus();state.groups.shift();await api.refresh();assert.equal(document.activeElement,panel.querySelector('[data-retrieval-close]'));
    document.activeElement.dispatch('keydown',{key:'Escape'});outside.focus();await api.refresh();assert.equal(panel.hidden,true);assert.equal(document.activeElement,outside);
    badge.dispatch('click');await tick();item(state.groups[0].history_id,'item:0').focus();state.project='another-owned-fixture';state.groups=[];api.sync();await tick();
    assert.equal(panel.hidden,true);assert.equal(badge.getAttribute('aria-expanded'),'false');assert.notEqual(document.activeElement,badge);
});

function declarations(css,selector) {
    const blocks=[...css.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(block=>block[1].trim()===selector);
    assert.equal(blocks.length,1,`one source rule ${selector}`);
    return Object.fromEntries(blocks[0][2].split(';').filter(value=>value.trim()).map(value=>value.trim().split(/\s*:\s*/)));
}
test('#495 legacy smart-group CSS alone matches gap12 and symmetric20 without ordinary-grid/font/cell changes', () => {
    const css=read('static/css/smart-canvas.css'), ordinary=declarations(css,'.thumb-grid'), scoped=declarations(css,'.smart-group-card .smart-group-thumb-grid');
    assert.equal(ordinary.gap,'8px');assert.equal(ordinary['padding-right'],'2px');assert.equal(scoped.gap,'12px');assert.equal(scoped['padding-right'],'0');
    assert.deepEqual(Object.keys(scoped).filter(key=>!['flex','align-self','max-width','min-height','gap','padding-right'].includes(key)),[]);
    const ctx=vm.createContext({});vm.runInContext(read('static/js/canvas-layout-utils.js'),ctx);
    for(const count of [2,4,6,8]) {
        const grid=ctx.getBatchThumbGridSize(count), width=grid.cols*192+(grid.cols-1)*Number.parseInt(scoped.gap)+Number.parseInt(scoped['padding-right']);
        assert.equal(width,grid.gridW);assert.equal((grid.boxW-width)/2,20);assert.equal(grid.gridH,grid.rows*144+(grid.rows-1)*12);
    }
    assert.equal(declarations(css,'.thumb-item img')['object-fit'],'contain');
    assert.match(classic,/class="thumb-grid smart-group-thumb-grid" data-thumb-scroll="1"/);
});

test('batch identity survives reorder; disabled batch, removed summary and removed reload fall back without scrolling', async () => {
    const {api,document,panel,state,item}=await setup(), h=state.groups[0].history_id;
    const batch = () => panel.querySelectorAll('[data-retrieval-all]').find(control => control.dataset.retrievalHistory === h);
    batch().focus(); state.groups.reverse(); await api.refresh();
    assert.equal(document.activeElement,batch()); assert.equal(document.activeElement.focusOptions.preventScroll,true);
    state.groups.find(group => group.history_id===h).items.forEach(item => Object.assign(item,{status:'downloading',can_retrieve:false}));
    await api.refresh(); assert.equal(document.activeElement,panel.querySelector('[data-retrieval-close]'));
    item(h,'item:0','summary').focus(); delete state.groups.find(group => group.history_id===h).items[0].error;
    await api.refresh(); assert.equal(document.activeElement,panel.querySelector('[data-retrieval-close]'));
    state.failure='offline'; await api.refresh(); panel.querySelector('[data-retrieval-refresh]').focus();
    state.failure=''; await api.refresh(); assert.equal(document.activeElement,panel.querySelector('[data-retrieval-close]'));
});

test('actual history renderer interleaves two saved cards and two missing slots while retaining per-output download identity', async () => {
    const fixture = JSON.parse(readFileSync(new URL('./fixtures/retrieval-canonical-case10-493.json',import.meta.url),'utf8'));
    const log = fixture.history[0], expected = log.outputs.map(output => output.output_id);
    // The renderer consumes hydrated media; fixture-local URLs never leave this VM.
    log.outputs.forEach(output => { output.url=`/offline/${output.asset_id}.png`; });
    log.outputs.reverse();
    const {ctx,harness} = editor(fixture.nodes);
    loadModules(ctx,['canvas-result-retrieval.js']);
    Object.assign(ctx,{smartHistorySizeSummary:()=>'',smartHistoryResolutionTier:()=>'',historyLogModelMeta:()=>({}),formatRunDuration:()=>'',trf:x=>x,ico:()=>'',smartHistoryList:harness.makeElement()});
    evaluate(ctx,`canvas=${JSON.stringify(fixture)}`);
    const api=ctx.canvasResultRetrieval;
    api.configure({getProjectId:()=>fixture.id,getHistory:()=>fixture.history,fetch:async(_url,options)=>{
        assert.equal(options.method,undefined); return {ok:true,json:async()=>({groups:[log.download]})};
    }});
    await api.refresh();
    const document=documentFixture(), rendered=document.body.appendChild(document.createElement('div'));
    const before=JSON.stringify(fixture);
    rendered.innerHTML=ctx.smartHistoryLogCardHtml(log,0);
    const cells=rendered.querySelectorAll('.log-item');
    assert.equal(cells.length,4);
    assert.ok(cells.every(cell=>cell.querySelector('.log-output-gallery').querySelectorAll('.log-media-card').length===1));
    const cards=rendered.querySelectorAll('.log-media-card');
    assert.equal(cards.length,4);
    assert.deepEqual(cards.slice(0,2).map(card=>card.getAttribute('data-output-id')),expected);
    assert.deepEqual(cards.slice(0,2).map(card=>card.querySelector('.log-media-download-original').getAttribute('data-history-download-original')),['1','0']);
    for (const card of cards.slice(0,2)) {
        assert.equal(card.querySelectorAll('.log-media-download-original').length,1);
        assert.equal(card.querySelectorAll('.log-media-delete-original').length,0);
        assert.equal(card.querySelectorAll('.retrieval-slot').length,0);
    }
    assert.deepEqual(cards.slice(2).map(card=>card.querySelector('.retrieval-slot').dataset.retrievalSlot),log.download.items.slice(2).map(item=>item.id));
    for (const card of cards.slice(2)) {
        assert.equal(card.querySelectorAll('.log-media-download-original').length,0);
        assert.equal(card.querySelectorAll('[data-retrieval-item]').length,1);
    }
    assert.equal(rendered.querySelectorAll('.log-output-preview').length,0);
    assert.equal(rendered.querySelectorAll('[data-retrieval-all]').length,1);
    assert.equal(JSON.stringify(fixture),before);
});
