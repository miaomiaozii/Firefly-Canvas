import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {editor} from './smart-human-handlers-harness-392.mjs';
import {loadModules, evaluate} from './harness.mjs';

const read = file => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
const fixture = JSON.parse(readFileSync(new URL('./fixtures/retrieval-canonical-case10-493.json', import.meta.url), 'utf8'));
const css = read('static/css/canvas-result-retrieval.css');
function rule(selector) {
    const blocks = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)];
    const block = blocks.find(match => match[1].trim().split(/\s*,\s*/).includes(selector));
    assert.ok(block, selector);
    return Object.fromEntries(block[2].split(';').filter(value => value.trim()).map(value => { const i=value.indexOf(':'); return [value.slice(0,i).trim(),value.slice(i+1).trim()]; }));
}
async function setup(saved = 0, count = 4) {
    const data = structuredClone(fixture), node = data.nodes[0], group = data.history[0].download;
    node.images = node.images.slice(0,saved); node._batchTotal=count;
    group.items = group.items.slice(0,count);
    group.items.forEach((item,index) => Object.assign(item,{status:index<saved?'succeeded':'failed',can_retrieve:index>=saved,remaining_seconds:28619}));
    node.pendingTasks[0].download=group;
    const {ctx,harness} = editor([node]);
    loadModules(ctx,['canvas-node-ops.js','canvas-agent-tui.js','canvas-result-retrieval.js']);
    evaluate(ctx,read('static/js/smart-canvas.js').split(/\r?\n/).filter(line=>/^const MEDIA_(NODE_DEFAULT_SCALE|GROUP_THUMB_BASE|GROUP_MAX_VISIBLE_ROWS) =/.test(line)).join('\n'));
    evaluate(ctx,`canvas.history=${JSON.stringify(data.history)}`);
    let paints=0, delay=0, scheduled;
    ctx.setTimeout=(fn,ms)=>{scheduled=fn;delay=ms;return 1;}; ctx.clearTimeout=()=>{};
    const api=ctx.canvasResultRetrieval;
    api.configure({getProjectId:()=>data.id,getHistory:()=>data.history,adoptCanonical:async()=>true,repaint:()=>paints++,fetch:async()=>({ok:true,json:async()=>({groups:[structuredClone(group)]})})});
    await api.refresh();
    const layout=()=>ctx.imageLayout(node.images,1,node);
    return {ctx,harness,api,node,group,layout,paints:()=>paints,delay:()=>delay,poll:async()=>{scheduled();await new Promise(resolve=>setImmediate(resolve));}};
}

test('R1 public slot renderer uses human minute TTL across all boundaries, never seconds',async()=>{
    const {api,group}=await setup();
    for(const [seconds,label] of [[28619,'剩余约 7 小时 57 分'],[3600,'剩余约 1 小时 0 分'],[3599,'剩余 60 分钟'],[65,'剩余 2 分钟'],[60,'剩余 1 分钟'],[59,'即将过期'],[0,'即将过期']]) {
        const html=api.slotHtml(group,{...group.items[0],remaining_seconds:seconds});
        assert.ok(html.includes(`<small>${label}</small>`),`${seconds}: ${html}`);
        assert.doesNotMatch(html,/\d+s\b|\d+m\b/);
    }
});

test('R1 each compact single and batch CTA has an adjacent always-visible no-charge label and full title',async()=>{
    const {api,group}=await setup();
    for(const html of [api.slotHtml(group,group.items[0]),api.summaryHtml(group)]) {
        assert.match(html,/<button[^>]*title="[^"]*不会重新生成、不扣费[^"]*"[^>]*>[\s\S]*?<\/button><span class="retrieval-no-charge">不扣费<\/span>/);
    }
    assert.equal(rule('.retrieval-action').display,'inline-flex');
    assert.equal(rule('.retrieval-no-charge')['white-space'],'nowrap');
    assert.ok(!rule('.retrieval-no-charge').display || rule('.retrieval-no-charge').display!=='none');
});

test('R1 actual imageLayout and renderer spend one footer budget and symmetric20 for all missing/mixed, ignoring stale generated height',async()=>{
    for(const saved of [0,1,2]) {
        const {ctx,api,node,layout}=await setup(saved);
        for(const cached of [undefined,428,380]) {
            if(cached) {node.w=436;node.h=cached;} else {delete node.w;delete node.h;}
            const size=layout(), html=ctx.nodeBodyHtml(node,size);
            assert.equal(size.thumb,192); assert.equal(size.gridHeight,300); assert.equal(size.width,436);
            assert.equal(size.height,300+40+48,`saved=${saved}, cached=${cached}`);
            assert.equal((html.match(/class="retrieval-summary batch-statusbar"/g)||[]).length,1);
            assert.equal((html.match(/data-retrieval-slot=/g)||[]).length,4-saved);
            assert.equal(api.slots(api.nodeGroup(node),node.images).length,4);
            assert.equal(size.height-40-size.gridHeight-48,0,'no flex remainder below the footer');
            for(const scale of [.5,2]) assert.equal((size.height-40-size.gridHeight-48)*scale,0);
        }
    }
    assert.equal(rule('.image-node:has(> .node-body > .retrieval-node:not(.retrieval-single):not(.retrieval-frame))').padding,'20px');
    assert.equal(rule('.image-node:has(> .node-body > .retrieval-node:not(.retrieval-single):not(.retrieval-frame))')['border-width'],'0');
    assert.equal(rule('.image-node:has(> .node-body > .retrieval-node:not(.retrieval-single):not(.retrieval-frame)) > .node-body').padding,'0');
    assert.equal(rule('.retrieval-node > .thumb-grid').flex,'1 1 auto');
    assert.equal(rule('.retrieval-node > .thumb-grid')['padding-right'],'0');
});

test('R1 retrieval 0s pill disappears but genuine generation and completed timings remain',async()=>{
    const {ctx,node}=await setup();
    assert.equal(ctx.runTimePillHtml(node),'');
    assert.match(ctx.runTimePillHtml({...node,id:'ordinary'}),/>0s<\/span>/);
    assert.match(ctx.runTimePillHtml({...node,runStartedAt:234}),/>1s<\/span>/);
    assert.match(ctx.runTimePillHtml({...node,id:'ordinary',pending:0,runStartedAt:1,runFinishedAt:2001,runElapsedMs:2000}),/>2s<\/span>/);
});

test('R1 idle TTL polling never runs each second or repaints when only seconds change in same display minute',async()=>{
    const state=await setup();
    assert.ok(state.delay()>=10000);
    const before=state.paints(); state.group.items.forEach(item=>item.remaining_seconds--); await state.api.refresh();
    assert.equal(state.paints(),before,'rounded TTL unchanged; no canvas repaint');
    state.group.items[0].remaining_seconds-=60; await state.api.refresh();
    assert.equal(state.paints(),before+1,'visible minute change repaints');
});

test('R1 single retrieval uses existing flex viewport and normal saved nodes keep original geometry',async()=>{
    const {ctx,api,node,group}=await setup(0,1), size=ctx.imageLayout(node.images,1,node);
    assert.equal(size.single,true);
    const html=api.nodeHtml(node,size,()=>{throw Error('no saved output');});
    assert.match(html,/retrieval-single/);assert.match(html,/width:100%;height:100%/);
    assert.equal(rule('.retrieval-single > .thumb-grid')['max-height'],'none');
    group.items.forEach(item=>Object.assign(item,{status:'succeeded',can_retrieve:false}));await api.refresh();
    node.pending=0;node._batchTotal=4;node.images=structuredClone(fixture.nodes[0].images);node.w=436;node.h=380;
    const saved=ctx.imageLayout(node.images,1,node);assert.equal(saved.height,380);assert.equal(saved.width,436);
    assert.equal(api.nodeHtml(node,saved,()=>''),'');
});
