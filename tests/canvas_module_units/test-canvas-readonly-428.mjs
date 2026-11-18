// Approved #428 seam: real composer -> durable serializer/journal and exit events.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {evaluate, fromContext, JS_DIR} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

function composer(source={}){
    const e=editor([{id:'legacy',type:'smart-image',promptDraftHtml:'',promptDraftText:'',...source}]);
    e.ctx.RECENT_SMART_SETTINGS_KEY='unit-428-settings';
    e.ctx.Node={TEXT_NODE:3,ELEMENT_NODE:1};
    e.ctx.promptInput.childNodes=[{nodeType:3,textContent:source.promptDraftText || source.runPrompt || ''}];
    evaluate(e.ctx,"canvasState.lastComposerNodeId='legacy:node'; canvasState.selectedId='legacy'; settings=smartSettingsForNode(canvasState.nodes[0]);");
    e.ctx.loadPromptDraft(e.node());
    return e;
}

function exits(e){
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
    vm.runInContext(source.slice(source.indexOf("window.addEventListener('pagehide'"),source.indexOf("smartUndoButton?.addEventListener('click'")),e.ctx);
    e.ctx.refreshPersistedDurableSnapshot(e.ctx.canvasForStorage());
    const writes=[];
    e.ctx.fetch=async(url,options={})=>{
        if(options.method==='PATCH'){
            const body=JSON.parse(options.body);writes.push({url,body});
            return {ok:true,status:200,json:async()=>({canvas:{...fromContext(e.ctx.canvasForStorage()),revision:writes.length}})};
        }
        if(url.endsWith('/flush'))return {ok:true,status:200,json:async()=>({saved:true,revision:writes.length})};
        throw Error(`unexpected request ${options.method || 'GET'} ${url}`);
    };
    return {writes,async fire(kind){
        if(kind==='pagehide')e.harness.windowListeners.get('pagehide').forEach(fn=>fn({type:kind}));
        else {e.ctx.document.visibilityState='hidden';e.harness.fireDocumentEvent('visibilitychange');}
        await new Promise(resolve=>setImmediate(resolve));
    }};
}

test('rendering a legacy prompt node never materializes LLM or separator display defaults',()=>{
    const e=editor([{id:'legacy-prompt',type:'smart-prompt',text:'Old prompt'}]);
    Object.assign(e.ctx,{apiProviders:[],promptTemplatePanel:null});
    const before=e.stored();
    const html=e.ctx.promptNodeBodyHtml(e.node());
    assert.ok(html.includes('Old prompt'),'the real prompt renderer still displays the old text');
    assert.deepEqual(e.stored(),before,'render-only defaults must stay out of the durable node');
    e.ctx.observeSmartCanvasPending();
    assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),false);
});

test('opening default composer settings is a durable no-op for a legacy node without runSettings',()=>{
    const e=composer(),before=e.stored();
    e.ctx.persistActiveSmartSettings();
    assert.deepEqual(e.stored(),before,'display defaults must not become a node edit');
    e.ctx.observeSmartCanvasPending();
    assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),false);
});

test('saving an unchanged loaded draft does not materialize default runSettings or missing legacy draft fields',()=>{
    for(const source of [{},{promptDraftHtml:undefined,promptDraftText:undefined}]){
        const e=composer(source),before=e.stored();
        e.ctx.savePromptDraftForCurrent();
        assert.deepEqual(e.stored(),before,'a read-only composer save must retain the exact legacy document');
        e.ctx.observeSmartCanvasPending();
        assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),false);
    }
});

test('pagehide and hidden-page saves of a loaded legacy fallback draft never PATCH, including repeated refreshes',async()=>{
    const e=composer({runPrompt:'old prompt',promptDraftHtml:undefined,promptDraftText:undefined});
    const io=exits(e),before=e.stored();
    for(const kind of ['pagehide','visibilitychange','pagehide']){
        evaluate(e.ctx,'contentDirty=true');
        await io.fire(kind);
        assert.deepEqual(e.stored(),before);
        assert.deepEqual(io.writes,[]);
    }
});

test('a real prompt edit is saved on pagehide and does not get lost to the read-only guard',async()=>{
    const e=composer({runPrompt:'old prompt',promptDraftHtml:undefined,promptDraftText:undefined}),io=exits(e);
    e.ctx.promptInput.innerHTML='new prompt';
    e.ctx.promptInput.childNodes=[{nodeType:3,textContent:'new prompt'}];
    e.ctx.savePromptDraftForCurrent({human:true,nodeId:'legacy'});
    e.ctx.scheduleSave();
    await io.fire('pagehide');
    assert.equal(io.writes.length,1);
    const node=io.writes[0].body.mutations.find(m=>m.op==='upsert_node').node;
    assert.equal(node.promptDraftText,'new prompt');
    assert.equal(node.promptDraftHtml,'new prompt');
    assert.equal(node.userTouchedAt,1234);
    assert.equal(node.promptEditedByUser,true);
    assert.equal(Object.hasOwn(node,'runSettings'),false);
});

test('a real settings change still persists while repeated same-value writes remain no-ops',()=>{
    const e=composer();
    evaluate(e.ctx,"settings.resolution='2k'");e.ctx.persistActiveSmartSettings();
    assert.equal(e.stored()[0].runSettings.resolution,'2k');
    const after=e.stored();e.ctx.persistActiveSmartSettings();
    assert.deepEqual(e.stored(),after);
});
