// #493 §4.4：压缩标注的三个渲染/携带接缝（节点标记、历史格、结果吸收）。
// 只测真实渲染函数与真实携带函数的行为；文案是后端预渲染数据，前端不重复实现阶梯语义。
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createCanvasContext,loadModules,HTML_SCRIPT_ORDER} from './harness.mjs';
import {Element} from './history-dom-493.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

function parsedHost(){
    return new Element('div');
}

const NOTE = '已为本次请求压缩：PNG→WebP q90，4096→3072';

function classicContext(){
    const html=readFileSync(new URL('../../static/smart-canvas.html',import.meta.url),'utf8');
    const {ctx,harness}=createCanvasContext({locationSearch:'?id=ref-note-493'});
    for(const match of html.matchAll(/\bid="([^"]+)"/g)){
        const el=harness.element(match[1]);el.style.setProperty=(key,value)=>el.style[key]=value;el.style.removeProperty=key=>delete el.style[key];
    }
    const world=harness.element('world');world.childNodes=[];world.insertAdjacentHTML=()=>{};
    const create=ctx.document.createElement;ctx.document.createElement=tag=>{const el=create(tag);if(tag==='template')el.content={querySelector:()=>null};return el;};
    ctx.getComputedStyle=()=>({display:'none',getPropertyValue:()=>''});
    ctx.ResizeObserver=class{observe(){} disconnect(){}};ctx.MutationObserver=class{observe(){} disconnect(){}};
    ctx.Image=class{};ctx.CSS={escape:x=>x,supports:()=>false};ctx.BroadcastChannel=class{};
    ctx.fetch=async()=>({ok:true,json:async()=>({canvas:{nodes:[],connections:[],history:[]},groups:[],snapshots:[],annotations:[]})});
    loadModules(ctx,HTML_SCRIPT_ORDER);
    return {ctx,harness};
}

test('node reference note marker renders escaped data, stays empty without a note, and survives absorb replacement',()=>{
    const {ctx}=classicContext();
    assert.equal(typeof ctx.nodeReferenceNoteHtml,'function');
    assert.equal(ctx.nodeReferenceNoteHtml({}), '');
    assert.equal(ctx.nodeReferenceNoteHtml({reference_note:''}), '');
    const marker=ctx.nodeReferenceNoteHtml({reference_note:NOTE});
    assert.ok(marker.includes('node-reference-prepared'));
    assert.ok(marker.includes(NOTE));
    const evil=ctx.nodeReferenceNoteHtml({reference_note:'<script>alert(1)</script>'});
    assert.ok(!evil.includes('<script>'));
    assert.ok(evil.includes('&lt;script&gt;'));
    // absorb 语义：新结果带文案则覆盖；新结果不带则清除旧文案（节点状态镜像最新一次任务）。
    assert.equal(typeof ctx.applyResultReferenceNote,'function');
    const node={id:'n1',reference_note:'旧文案'};
    ctx.applyResultReferenceNote(node,{reference_note:NOTE});
    assert.equal(node.reference_note,NOTE);
    ctx.applyResultReferenceNote(node,{history_record:{reference_note:'历史文案'}});
    assert.equal(node.reference_note,'历史文案');
    ctx.applyResultReferenceNote(node,{});
    assert.ok(!('reference_note' in node));
});

test('history log card renders the note as its own line and omits the block when absent',()=>{
    const fixture=JSON.parse(readFileSync(new URL('./fixtures/retrieval-canonical-case10-493.json',import.meta.url),'utf8'));
    const log=structuredClone(fixture.history[0]);
    log.reference_note=NOTE;
    log.outputs.forEach(output=>{output.url=`/offline/${output.asset_id}.png`;});
    const {ctx,harness}=editor(fixture.nodes);
    loadModules(ctx,['canvas-result-retrieval.js']);
    Object.assign(ctx,{smartHistorySizeSummary:()=>'',smartHistoryResolutionTier:()=>'',historyLogModelMeta:()=>({}),formatRunDuration:()=>'',trf:x=>x,ico:()=>'',smartHistoryList:harness.makeElement()});
    const rendered=parsedHost();
    rendered.innerHTML=ctx.smartHistoryLogCardHtml(log,0);
    const noteEl=rendered.querySelector('.log-reference-prepared');
    assert.ok(noteEl,'note line must render for a compressed record');
    assert.ok(rendered.markup.includes(NOTE));
    const bare=structuredClone(log);delete bare.reference_note;
    const bareRendered=parsedHost();
    bareRendered.innerHTML=ctx.smartHistoryLogCardHtml(bare,0);
    assert.equal(bareRendered.querySelector('.log-reference-prepared'),null);
});
