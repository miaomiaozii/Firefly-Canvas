// #392 real media handlers, durable projection, pending journal and action-delta undo.
// Browser pixels and the upload network are boundaries; no ownership/undo mocks.
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {createCanvasContext,loadModules,evaluate,fromContext,JS_DIR} from './harness.mjs';
import {declarations,identity} from './smart-human-handlers-harness-392.mjs';

const imageNode=(id,extra={})=>({id,type:'smart-image',x:0,y:0,scale:.5,created_at:12,...identity,images:[{url:'/original.png',name:'original.png',kind:'image'}],...extra});
function mediaEditor(nodes){
    const {ctx,harness}=createCanvasContext();
    for(const id of ['imageEditModal','cropImage','cropCanvas','editDrawCanvas','editTextCanvas','imageEditStage','previewStage','cropBox','outpaintSizeHint']) harness.element(id);
    harness.element('previewStage').style.setProperty=()=>{};
    const pixels={drawImage(){},fillRect(){},clearRect(){},getImageData(){return {data:new Uint8ClampedArray([0,0,0,255])};},createImageData(){return {data:new Uint8ClampedArray(4)};},putImageData(){}};
    for(const id of ['editDrawCanvas','editTextCanvas']){
        const el=harness.element(id);el.width=100;el.height=100;el.getContext=()=>pixels;
    }
    const create=ctx.document.createElement;
    ctx.document.createElement=tag=>{const el=create(tag);if(tag==='canvas'){el.getContext=()=>pixels;el.toBlob=resolve=>resolve({});}return el;};
    Object.assign(harness.element('cropImage'),{naturalWidth:100,naturalHeight:100,width:100,height:100,clientWidth:100,clientHeight:100});
    harness.element('imageEditModal').classList.add('open');
    loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','project-canvas-mutations.js','canvas-layout-utils.js','canvas-connections.js','canvas-media-history.js','canvas-undo.js','canvas-node-ops.js','canvas-geometry.js','canvas-placement.js','canvas-image-editor.js','canvas-generation-cascade.js','canvas-generation-tasks.js']);
    const constants=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8').split(/\r?\n/).filter(line=>/^const (?:PROMPT_|MEDIA_|ZOOM_PREVIEW_NODE_|SMART_GROUP_)\w+ = (?:[\d]|')/.test(line)).join('\n');
    const sizes=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8').match(/^const (?:SIZE_MAP|IMAGE_RATIO_PRESETS|RES_LONG_SIDE|RES_PIXEL_LIMIT) = [\s\S]*?;\r?$/gm).join('\n');
    vm.runInContext(constants+'\n'+sizes+'\n'+declarations('smart-canvas.js'),ctx);
    Object.assign(ctx,{
        canvasId:'unit-392',sourceProjectId:'unit-392',SMART_LOG_PREVIEW_NODE_ID:'__preview__',
        SMART_ONLINE_ENGINES:new Set(['api','volcengine']),PROMPT_FIELD_SIZING_OK:true,PROMPT_SPLIT_RESIZE_BAR_H:8,
        smartEdgeReveal:null,viewport:{x:0,y:0,scale:1},canvasDefaultSmartSettings:{},initialSmartSettings:{},recentSmartSettingsByMode:{},
        canvasUsesConnections:true,canvasLoadReady:true,canvasProjectGone:false,canvasSyncInFlight:false,contentDirty:false,debouncedSaveTimer:null,DEBOUNCED_SAVE_MS:3000,
        persistedCoreSnapshot:null,persistedDurableSnapshot:null,smartCanvasServerShadow:null,smartSaveButton:null,
        pendingRemoteUpdatedAt:0,pendingAgentMergeFingerprint:'',pendingAgentMergeUndo:false,smartClientId:'unit-392',projectMediaImportCache:new Map(),activeProjectMediaImports:0,projectMediaImportWaiters:[],assetLibraryOpen:false,
        promptNodeFlowView:{mode:'idle'},composerPromptView:{mode:'manual'},longContentSession:{mode:'idle'},smartLoopPromptVisiting:new Set(),smartLoopContext:null,
        promptInput:harness.makeElement(),composer:harness.makeElement(),world:harness.makeElement(),dynamicParams:harness.makeElement(),
        transientSmartCloudLinks:[],rhConfigs:[],modelSpecs:{},smartRhRandomValues:{},
        render(){},renderDynamicParams(){},refreshIcons(){},updateNodeElementDuringResize(){},scheduleConnectionLayerRefresh(){},
        toast(message){harness.toasts.push(String(message));},
        FormData:class {append(){}},
    });
    ctx.composer.classList.add('open');
    evaluate(ctx,`Date.now=()=>1234;canvas={id:'unit-392',project_id:'unit-392',format:'firefly-canvas-v1',nodes:${JSON.stringify(nodes)},connections:[],history:[]};canvasState.nodes=canvas.nodes;cropState={nodeId:${JSON.stringify(nodes[0].id)},imageIndex:0,x:0,y:0,w:50,h:50};imageEditBaseW=100;imageEditBaseH=100;`);
    ctx.initialSnapshot=ctx.FireflyProjectCanvasMutations.snapshotFromCanvas(evaluate(ctx,'canvas'));
    evaluate(ctx,'persistedCoreSnapshot=initialSnapshot');ctx.smartCanvasPendingState();
    ctx.fetch=async url=>{assert.equal(url,'/api/ai/upload');return {ok:true,json:async()=>({files:[{url:'/crop.png',name:'crop.png',kind:'image'}]})};};
    return {ctx,harness,node:id=>evaluate(ctx,`canvasState.nodes.find(n=>n.id===${JSON.stringify(id)})`),stored:()=>fromContext(ctx.canvasForStorage()).nodes};
}
function deferredUpload(e){
    let release;
    const response=new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({files:[{url:'/crop.png',name:'crop.png',kind:'image'}]})});});
    e.ctx.fetch=async url=>{assert.equal(url,'/api/ai/upload');return response;};
    return release;
}
function failedLoopEditor(historyNodes){
    const e=mediaEditor([
        imageNode('root',{promptDraftText:'scene',promptDraftHtml:'scene',runSettings:{engine:'api',apiKind:'image',provider_id:'unit-provider',model:'unit-model'}}),
        imageNode('slot',{images:[{url:'/prior.png',kind:'image'}]}),
        ...historyNodes,{id:'loop',type:'smart-loop',images:[]},
    ]);
    evaluate(e.ctx,`canvas.format='classic';settings={engine:'api',apiKind:'image',provider_id:'unit-provider',model:'unit-model'};apiProviders=[{id:'unit-provider',image_models:['unit-model']}];`);
    const create=e.ctx.document.createElement;
    e.ctx.document.createElement=tag=>{const el=create(tag);if(tag==='div'){Object.defineProperty(el,'innerHTML',{get(){return this._html||'';},set(value){this._html=value;this.childNodes=[{nodeType:3,textContent:value}];}});}return el;};
    e.ctx.Node={TEXT_NODE:3,ELEMENT_NODE:1};
    return e;
}
function ownership(node){return Object.fromEntries(['createdBy','agentNodeId','userTouchedAt','promptEditedByUser','created_at'].filter(key=>key in node).map(key=>[key,node[key]]));}

test('manual crop content and T share one undo/redo, preserving source identity and P',async()=>{
    const e=mediaEditor([imageNode('a')]);
    await e.ctx.applyImageCrop();
    assert.deepEqual(ownership(e.node('a')),{...identity,userTouchedAt:1234,created_at:12});
    assert.equal(e.node('a').images[0].url,'/crop.png');
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    assert.equal(e.ctx.smartCanvasPendingState().journal.entries().length>0,true);
    await e.ctx.performUndo();
    assert.deepEqual(ownership(e.node('a')),{...identity,created_at:12});
    assert.equal(e.node('a').images[0].url,'/original.png');
    await e.ctx.performRedo();
    assert.deepEqual(ownership(e.node('a')),{...identity,userTouchedAt:1234,created_at:12});
    assert.equal(e.node('a').images[0].url,'/crop.png');
});

test('brush apply changes only durable media/T and is undoable',async()=>{
    const e=mediaEditor([imageNode('a',{promptEditedByUser:true})]);
    await e.ctx.applyImageBrush();
    assert.equal(e.node('a').images[0].url,'/crop.png');
    assert.deepEqual(ownership(e.node('a')),{...identity,promptEditedByUser:true,userTouchedAt:1234,created_at:12});
    await e.ctx.performUndo();
    assert.equal(e.node('a').images[0].url,'/original.png');
    assert.equal(e.node('a').userTouchedAt,null);
});

test('mask apply appends to the captured live node with T only and restores metadata on undo',async()=>{
    const e=mediaEditor([imageNode('a')]);
    await e.ctx.applyImageMask();
    assert.equal(e.node('a').images.length,2);
    assert.equal(e.node('a').images[1].role,'mask');
    assert.deepEqual(ownership(e.node('a')),{...identity,userTouchedAt:1234,created_at:12});
    await e.ctx.performUndo();
    assert.equal(e.node('a').images.length,1);
    assert.deepEqual(ownership(e.node('a')),{...identity,created_at:12});
});

test('resize apply stamps T only and restores original dimensions/media together',async()=>{
    const e=mediaEditor([imageNode('a')]);
    await e.ctx.applyImageResize();
    assert.equal(e.node('a').images[0].url,'/crop.png');
    assert.equal(e.node('a').images[0].natural_w,50);
    assert.deepEqual(ownership(e.node('a')),{...identity,userTouchedAt:1234,created_at:12});
    await e.ctx.performUndo();
    assert.equal(e.node('a').images[0].url,'/original.png');
    assert.deepEqual(ownership(e.node('a')),{...identity,created_at:12});
});

test('outpaint applies captured media/settings and an automatic prompt suggestion without P in one undo',async()=>{
    const e=mediaEditor([imageNode('a',{promptDraftText:'old draft',promptDraftHtml:'old draft'})]);
    evaluate(e.ctx,`imageEditMode='outpaint';cropState={nodeId:'a',imageIndex:0,x:10,y:10,w:120,h:120};`);
    e.ctx.fetch=async url=>{
        if(url==='/api/ai/upload') return {ok:true,json:async()=>({files:[{url:'/outpaint.png',name:'outpaint.png',kind:'image'}]})};
        assert.equal(url,'/api/project-library/unit-392/media/import');
        return {ok:true,json:async()=>({media:{asset_id:'outpaint-asset',kind:'image'}})};
    };
    await e.ctx.applyImageOutpaint();
    assert.equal(e.node('a').images[0].asset_id,'outpaint-asset');
    assert.equal(e.node('a').promptDraftText,'Remove red area and fill the scene');
    assert.deepEqual(ownership(e.node('a')),{...identity,userTouchedAt:1234,created_at:12});
    await e.ctx.performUndo();
    assert.equal(e.node('a').promptDraftText,'old draft');
    assert.equal(e.node('a').images[0].url,'/original.png');
    assert.equal(e.node('a').outpaintSize,undefined);
    assert.deepEqual(ownership(e.node('a')),{...identity,created_at:12});
});

test('human shared-asset mark stamps only changed targets, couples curated/T undo, and same mark preserves redo',async()=>{
    const e=mediaEditor([
        imageNode('a',{images:[{asset_id:'shared',url:'/shared.png'}]}),
        imageNode('b',{images:[{asset_id:'shared',url:'/shared.png',marked:true}],curated:false}),
        imageNode('unrelated',{images:[{asset_id:'other',url:'/other.png'}],curated:true}),
    ]);
    e.ctx.setMarkedImage('shared',true);
    assert.deepEqual(ownership(e.node('a')),{...identity,userTouchedAt:1234,created_at:12});
    assert.equal(e.node('a').curated,true);
    assert.deepEqual(ownership(e.node('b')),{...identity,created_at:12});
    assert.equal(e.node('b').curated,false,'same mark must not repair unrelated curation');
    assert.deepEqual(ownership(e.node('unrelated')),{...identity,created_at:12});
    assert.equal(e.node('unrelated').curated,true);
    await e.ctx.performUndo();
    assert.equal(e.node('a').images[0].marked,undefined);
    assert.deepEqual(ownership(e.node('a')),{...identity,created_at:12});
    assert.equal(e.node('b').images[0].marked,true);
    await e.ctx.performRedo();
    const steps=evaluate(e.ctx,'undoStack.length');
    e.ctx.setMarkedImage('shared',true);
    assert.equal(evaluate(e.ctx,'undoStack.length'),steps);
    await e.ctx.performUndo();
    e.ctx.setMarkedImage('missing',false);
    assert.equal(evaluate(e.ctx,'redoStack.length'),1);
});

test('new loop output resets inherited ownership/time, while generic clone and source keep exact ownership',()=>{
    const e=mediaEditor([imageNode('a',{userTouchedAt:88,promptEditedByUser:true})]);
    const source=fromContext(e.node('a'));
    const clone=e.ctx.cloneSmartNode(e.node('a'));
    assert.deepEqual(ownership(clone),ownership(source));
    const output=e.ctx.createLoopOutputSlot(e.node('a'),2);
    assert.deepEqual(ownership(output),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    assert.deepEqual(fromContext(e.node('a')),source);
});

test('new pending generation output is a user identity, never an inference from an Agent source',()=>{
    const e=mediaEditor([imageNode('a',{userTouchedAt:88,promptEditedByUser:true})]);
    const sourceOwnership=ownership(e.node('a'));
    const output=e.ctx.createPendingOutputFromSource(e.node('a'),1,null);
    assert.deepEqual(ownership(output),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    assert.deepEqual(ownership(e.node('a')),sourceOwnership);
});

test('automatic history archival creates a fresh N once, but group reuse/generation keep ownership exact',()=>{
    const e=mediaEditor([imageNode('a',{userTouchedAt:88,promptEditedByUser:true})]);
    const before=ownership(e.node('a'));
    e.ctx.replaceOutputsToNodeWithHistory(e.node('a'),[{url:'/generated.png',kind:'image'}],'image',null,{skipShift:true});
    const group=e.ctx.historyGroupForNode(e.node('a'));
    assert.deepEqual(ownership(group),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    Object.assign(group,{...identity,userTouchedAt:99,promptEditedByUser:true,created_at:22});
    e.ctx.replaceOutputsToNodeWithHistory(e.node('a'),[{url:'/generated-2.png',kind:'image'}],'image',null,{skipShift:true});
    assert.deepEqual(ownership(e.ctx.historyGroupForNode(e.node('a'))),{...identity,userTouchedAt:99,promptEditedByUser:true,created_at:22});
    assert.deepEqual(ownership(e.node('a')),before);
});

test('grid join creates a new user identity without touching its source and has one undo/redo',async()=>{
    const e=mediaEditor([imageNode('a',{userTouchedAt:88,promptEditedByUser:true,images:[{url:'/one.png',kind:'image'},{url:'/two.png',kind:'image'}]})]);
    const source=fromContext(e.node('a'));
    evaluate(e.ctx,`gridJoinImageCache.set(0,{complete:true,naturalWidth:100,naturalHeight:100});gridJoinImageCache.set(1,{complete:true,naturalWidth:100,naturalHeight:100});`);
    await e.ctx.applyImageGridJoin();
    const output=evaluate(e.ctx,`canvasState.nodes.find(n=>n.id!=='a')`);
    assert.deepEqual(ownership(output),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    assert.deepEqual(fromContext(e.node('a')),source);
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    const id=output.id;
    await e.ctx.performUndo();assert.equal(e.node(id),undefined);
    await e.ctx.performRedo();assert.deepEqual(ownership(e.node(id)),ownership(output));
});

test('automatic failed loop submission preserves concurrent history identity/metadata and unrelated media',async()=>{
    const e=failedLoopEditor([imageNode('history',{historyFor:'slot',isHistoryGroup:true,title:'Old history',images:[{url:'/older.png',kind:'image'}],w:450,h:210})]);
    e.ctx.fetch=async url=>{
        assert.equal(url,'/api/canvas-image-tasks');
        const history=e.node('history');
        Object.assign(history,{title:'Remote renamed history',userTouchedAt:777,promptEditedByUser:true});
        history.images.push({url:'/remote.png',kind:'image'});
        e.node('slot').images.push({url:'/remote-slot.png',kind:'image'});
        throw new Error('unit submission rejected');
    };
    await assert.rejects(e.ctx.runLoopRoundIntoSlot(e.node('loop'),e.node('root'),e.node('slot'),1,null),/unit submission rejected/);
    assert.equal(e.node('history').title,'Remote renamed history');
    assert.equal(e.node('history').userTouchedAt,777);
    assert.equal(e.node('history').promptEditedByUser,true);
    assert.deepEqual(fromContext(e.node('history').images).map(image=>image.url),['/older.png','/remote.png']);
    // Ordering follows the existing identity merger; both independent outputs survive.
    assert.deepEqual(fromContext(e.node('slot').images).map(image=>image.url).sort(),['/prior.png','/remote-slot.png']);
    assert.equal(e.node('slot').userTouchedAt,null);
});

test('automatic archive compensation restores exact old fields, removes untouched new pools, and preserves adopted new pools',async()=>{
    for(const kind of ['legacy','new','adopted']){
        const legacy={id:'history',type:'smart-image',historyFor:'slot',isHistoryGroup:true,title:'Legacy',images:[{url:'/older.png',kind:'image'}],w:400,h:200,scale:.8,created_at:21};
        const e=failedLoopEditor(kind==='legacy'?[legacy]:[]);
        const original=ownership(e.node('slot'));
        e.ctx.fetch=async url=>{
            assert.equal(url,'/api/canvas-image-tasks');
            if(kind==='adopted'){
                const pool=e.ctx.historyGroupForNode(e.node('slot'));
                Object.assign(pool,{title:'Adopted',userTouchedAt:888});
                pool.images.push({url:'/independent.png',kind:'image'});
            }
            throw new Error('unit submission rejected');
        };
        await assert.rejects(e.ctx.runLoopRoundIntoSlot(e.node('loop'),e.node('root'),e.node('slot'),1,null),/unit submission rejected/);
        assert.deepEqual(ownership(e.node('slot')),original,kind);
        assert.deepEqual(fromContext(e.node('slot').images),[{url:'/prior.png',kind:'image'}],kind);
        const pool=e.ctx.historyGroupForNode(e.node('slot'));
        if(kind==='legacy') assert.deepEqual(fromContext(pool),legacy);
        if(kind==='new') assert.equal(pool,null);
        if(kind==='adopted'){
            assert.equal(pool.title,'Adopted');assert.equal(pool.userTouchedAt,888);
            assert.deepEqual(fromContext(pool.images),[{url:'/independent.png',kind:'image'}]);
        }
    }
});

test('real each-image task entry creates a fresh output identity and never takes ownership from the source',async()=>{
    const e=failedLoopEditor([]);
    const source=fromContext(e.node('slot'));
    e.ctx.fetch=async url=>{assert.equal(url,'/api/canvas-image-tasks');return {ok:true,json:async()=>({task_id:'unit-task'})};};
    const result=await e.ctx.runEachImageGeneration([e.node('slot')],'scene',{engine:'api',apiKind:'image',provider_id:'unit-provider',model:'unit-model'});
    assert.equal(result.runs.length,1);
    const output=e.node(result.runs[0].resultNodeId);
    assert.deepEqual(ownership(output),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    assert.deepEqual(fromContext(e.node('slot')),source);
    await e.ctx.performUndo();assert.equal(e.node(output.id),undefined);
});

test('deleted, replaced and project-switched crop targets reject visibly without a history step or wrong stamp',async()=>{
    for(const drift of ['deleted','replaced','project']){
        const e=mediaEditor([imageNode('a'),imageNode('b')]);
        const release=deferredUpload(e);
        const pending=e.ctx.applyImageCrop();await Promise.resolve();await Promise.resolve();
        if(drift==='deleted') evaluate(e.ctx,`canvasState.nodes.splice(0,1);`);
        if(drift==='replaced') evaluate(e.ctx,`canvasState.nodes[0].images=[{url:'/replaced.png',kind:'image'}];`);
        if(drift==='project') e.ctx.canvasId='another-project';
        release();await pending;
        assert.equal(evaluate(e.ctx,'undoStack.length'),0,drift);
        assert.equal(e.node('b').userTouchedAt,null,drift);
        assert.equal(e.node('b').images[0].url,'/original.png',drift);
        assert.ok(e.harness.toasts.some(message=>message.includes('编辑目标已变化')),drift);
    }
});

test('same durable crop result and preview/cancel preserve ownership and the previous redo branch',async()=>{
    const initial=imageNode('a',{images:[{url:'/crop.png',name:'crop.png',kind:'image',asset_id:'',natural_w:0,natural_h:0}]});
    const e=mediaEditor([initial]);
    e.ctx.fetch=async()=>({ok:true,json:async()=>({files:[{url:'/different.png',name:'different.png',kind:'image'}]})});
    await e.ctx.applyImageCrop();await e.ctx.performUndo();
    const before=fromContext(e.node('a'));
    evaluate(e.ctx,`cropState={nodeId:'a',imageIndex:0,x:0,y:0,w:50,h:50};`);
    e.ctx.fetch=async()=>({ok:true,json:async()=>({files:[{url:'/crop.png',name:'crop.png',kind:'image'}]})});
    await e.ctx.applyImageCrop();
    assert.deepEqual(fromContext(e.node('a')),before);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    assert.equal(evaluate(e.ctx,'redoStack.length'),1);
    evaluate(e.ctx,`imageEditMode='preview';`);e.ctx.applyImageEdit();e.ctx.closeImageEditor();
    assert.deepEqual(fromContext(e.node('a')),before);
    assert.equal(evaluate(e.ctx,'redoStack.length'),1);
});

test('automatic mark-index rebuild/sync preserve null, false and missing ownership exactly',()=>{
    const legacy={id:'legacy',type:'smart-image',images:[{asset_id:'legacy-asset',marked:true}]};
    const e=mediaEditor([imageNode('a',{images:[{asset_id:'a',marked:true}]}),legacy]);
    const before=e.stored().map(ownership);
    e.ctx.rebuildMarkedAssetIdsFromNodes();e.ctx.syncCuratedFromMarks();
    assert.deepEqual(e.stored().map(ownership),before);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
});

test('grid split and history-to-canvas create new N/time, preserve the source, and undo the new identity only',async()=>{
    const e=mediaEditor([imageNode('a',{userTouchedAt:88,promptEditedByUser:true})]);
    const source=fromContext(e.node('a'));
    await e.ctx.applyImageGridSplit();
    const grid=evaluate(e.ctx,`canvasState.nodes.find(n=>n.id!=='a')`);
    assert.deepEqual(ownership(grid),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    assert.deepEqual(fromContext(e.node('a')),source);
    await e.ctx.performUndo();assert.equal(e.node(grid.id),undefined);
    e.ctx.shell=e.harness.makeElement();
    evaluate(e.ctx,`canvas.history=[{id:'history-result',status:'success',kind:'image',nodeId:'a',outputs:[{url:'/history-result.png',kind:'image'}]}];`);
    const sent=e.ctx.sendSmartHistoryOutputsToCanvas('history-result',0,{x:100,y:100});
    assert.deepEqual(ownership(sent),{createdBy:'user',agentNodeId:null,userTouchedAt:null,promptEditedByUser:false,created_at:1234});
    assert.deepEqual(fromContext(e.node('a')),source);
    await e.ctx.performUndo();assert.equal(e.node(sent.id),undefined);
});

test('history reuse only replaces stale image models within the recorded provider and redoes that exact model',async()=>{
    const e=mediaEditor([imageNode('a')]);
    e.ctx.shell=e.harness.makeElement();
    evaluate(e.ctx,`apiProviders=[
        {id:'primary',image_models:['other-provider-model','stale-model']},
        {id:'recorded',image_models:['first-eligible','preferred'],recommended_models:{image:'preferred'}},
        {id:'empty',image_models:[]}
    ];`);
    for(const [provider,expected] of [['recorded','preferred'],[undefined,'stale-model'],['empty','stale-model']]){
        const params={provider_id:provider,model:'stale-model',size:'1024x1024'};
        e.ctx.fixtureHistory={id:'history-result',status:'succeeded',outputs:[{url:'/result.png'}],params};
        evaluate(e.ctx,'canvas.history=[fixtureHistory];');
        const node=e.ctx.sendSmartHistoryOutputsToCanvas('history-result',0,{x:100,y:100});
        assert.equal(node.runSettings.model,expected);
        assert.equal(node.runSettings.provider_id,provider);
        assert.equal(node.runSettings.size,params.size);
        assert.equal(evaluate(e.ctx,'canvas.history[0].params.model'),'stale-model');
        await e.ctx.performUndo();
        assert.equal(e.node(node.id),undefined);
        await e.ctx.performRedo();
        assert.equal(e.node(node.id).runSettings.model,expected);
        assert.equal(e.node(node.id).runSettings.provider_id,provider);
    }
});

for(const failure of ['one tile','all tiles']){
    test(`grid split visibly rejects failed browser encoding: ${failure}`,async()=>{
        const e=mediaEditor([imageNode('a')]);
        e.harness.element('gridHorizontalLines').value='1';
        const create=e.ctx.document.createElement;
        let encoded=0,uploads=0;
        e.ctx.document.createElement=tag=>{
            const el=create(tag);
            if(tag==='canvas')el.toBlob=resolve=>resolve(failure==='all tiles'||encoded++===0?null:{});
            return el;
        };
        e.ctx.fetch=async url=>{uploads++;assert.equal(url,'/api/ai/upload');return {ok:true,json:async()=>({files:[{url:'/partial.png',kind:'image'}]})};};
        const before=e.stored();
        await e.ctx.applyImageGridSplit();
        assert.equal(uploads,0,'a failed tile must not become a partial upload/output');
        assert.deepEqual(e.stored(),before);
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
        assert.ok(e.harness.toasts.some(message=>message.includes('编码失败')));
    });
}

test('grid split snapshots every tile before the first await, not a later editor image',async()=>{
    const e=mediaEditor([imageNode('a'),imageNode('b',{images:[{url:'/b.png',kind:'image'}]})]);
    e.harness.element('gridHorizontalLines').value='1';
    e.harness.element('cropImage').src='/original.png';
    const create=e.ctx.document.createElement,drawn=[];
    let first=true;
    e.ctx.document.createElement=tag=>{
        const el=create(tag);
        if(tag==='canvas'){
            el.getContext=()=>({drawImage(image){drawn.push(image.src);}});
            el.toBlob=resolve=>queueMicrotask(()=>{
                if(first){first=false;e.harness.element('cropImage').src='/b.png';evaluate(e.ctx,`cropState={nodeId:'b',imageIndex:0};`);}
                resolve({});
            });
        }
        return el;
    };
    await e.ctx.applyImageGridSplit();
    assert.deepEqual(drawn,['/original.png','/original.png']);
    assert.equal(e.node('a').userTouchedAt,null);assert.equal(e.node('b').userTouchedAt,null);
});

test('grid join loads its captured source set, never images cached by a later editor session',async()=>{
    const e=mediaEditor([imageNode('a',{images:[{url:'/a0.png',kind:'image'},{url:'/a1.png',kind:'image'}]}),imageNode('b')]);
    evaluate(e.ctx,`gridJoinImageCache.set(0,{label:'a0',complete:true,naturalWidth:100,naturalHeight:100});gridJoinImageCache.set(1,{label:'a1',complete:true,naturalWidth:100,naturalHeight:100});`);
    const create=e.ctx.document.createElement,drawn=[];
    e.ctx.document.createElement=tag=>{const el=create(tag);if(tag==='canvas')el.getContext=()=>({fillRect(){},drawImage(image){drawn.push(image.label);}});return el;};
    const pending=e.ctx.applyImageGridJoin();
    evaluate(e.ctx,`cropState={nodeId:'b',imageIndex:0};gridJoinImageCache=new Map([[0,{label:'b0',complete:true,naturalWidth:100,naturalHeight:100}],[1,{label:'b1',complete:true,naturalWidth:100,naturalHeight:100}]]);`);
    await pending;
    assert.deepEqual(drawn,['a0','a1']);
    assert.equal(evaluate(e.ctx,'cropState.nodeId'),'b');
});

test('mask upload resolves a rebased live target rather than mutating its detached pre-await node',async()=>{
    const e=mediaEditor([imageNode('a')]);
    const detached=e.node('a'),release=deferredUpload(e);
    const pending=e.ctx.applyImageMask();await Promise.resolve();await Promise.resolve();
    evaluate(e.ctx,`canvasState.nodes=JSON.parse(JSON.stringify(canvasState.nodes));canvas.nodes=canvasState.nodes;canvasState.nodes[0].title='Remote title';`);
    release();await pending;
    assert.equal(detached.images.length,1);
    assert.equal(e.node('a').images.length,2);
    assert.equal(e.node('a').title,'Remote title');
    assert.equal(e.node('a').userTouchedAt,1234);
    await e.ctx.performUndo();
    assert.equal(e.node('a').images.length,1);assert.equal(e.node('a').title,'Remote title');
});

test('outpaint upload preserves a prompt edited concurrently, including P, while media/T undo stays local',async()=>{
    const e=mediaEditor([imageNode('a',{promptDraftText:'before',promptDraftHtml:'before'})]);
    evaluate(e.ctx,`imageEditMode='outpaint';cropState={nodeId:'a',imageIndex:0,x:10,y:10,w:120,h:120};`);
    let release;
    e.ctx.fetch=url=>{
        if(url==='/api/ai/upload') return new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({files:[{url:'/outpaint.png',name:'outpaint.png',kind:'image'}]})});});
        assert.equal(url,'/api/project-library/unit-392/media/import');
        return Promise.resolve({ok:true,json:async()=>({media:{asset_id:'outpaint-asset',kind:'image'}})});
    };
    const pending=e.ctx.applyImageOutpaint();await Promise.resolve();await Promise.resolve();
    Object.assign(e.node('a'),{promptDraftText:'concurrent prompt',promptDraftHtml:'concurrent prompt',userTouchedAt:777,promptEditedByUser:true});
    release();await pending;
    assert.equal(e.node('a').promptDraftText,'concurrent prompt');assert.equal(e.node('a').promptEditedByUser,true);
    await e.ctx.performUndo();
    assert.equal(e.node('a').promptDraftText,'concurrent prompt');
    assert.equal(e.node('a').userTouchedAt,777);assert.equal(e.node('a').promptEditedByUser,true);
    assert.equal(e.node('a').images[0].url,'/original.png');
});

test('outpaint keeps the original default settings context when another editor becomes active during upload',async()=>{
    const e=mediaEditor([imageNode('a'),imageNode('b')]);
    evaluate(e.ctx,`settings={engine:'api',model:'model-a'};imageEditMode='outpaint';cropState={nodeId:'a',imageIndex:0,x:10,y:10,w:120,h:120};`);
    let release;
    e.ctx.fetch=url=>{
        if(url==='/api/ai/upload') return new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({files:[{url:'/outpaint.png',name:'outpaint.png',kind:'image'}]})});});
        assert.equal(url,'/api/project-library/unit-392/media/import');
        return Promise.resolve({ok:true,json:async()=>({media:{asset_id:'outpaint-asset',kind:'image'}})});
    };
    const pending=e.ctx.applyImageOutpaint();await Promise.resolve();await Promise.resolve();
    evaluate(e.ctx,`settings={engine:'api',model:'model-b'};canvasState.selectedId='b';cropState={nodeId:'b',imageIndex:0};`);
    release();await pending;
    assert.equal(e.node('a').runSettings.model,'model-a');
    assert.equal(evaluate(e.ctx,'settings.model'),'model-b');
    assert.equal(e.node('b').userTouchedAt,null);
});

test('mark undo restores the visible asset mark state so the next real toggle is not inverted',async()=>{
    const e=mediaEditor([imageNode('a',{images:[{asset_id:'solo',url:'/solo.png',kind:'image'}]})]);
    e.ctx.toggleMarkedImage(e.node('a'),0);
    await e.ctx.performUndo();
    assert.deepEqual(fromContext(e.ctx.getMarkedAssetIds()),[]);
    e.ctx.toggleMarkedImage(e.node('a'),0);
    assert.equal(e.node('a').images[0].marked,true);
    assert.equal(e.node('a').userTouchedAt,1234);
});

test('crop upload commits its captured image after editor navigation, not the new selection; undo retains remote work',async()=>{
    const e=mediaEditor([imageNode('a'),imageNode('b',{images:[{url:'/b.png',name:'b.png'}]})]);
    const release=deferredUpload(e);
    const pending=e.ctx.applyImageCrop();
    await Promise.resolve();await Promise.resolve();
    evaluate(e.ctx,`cropState={nodeId:'b',imageIndex:0,x:0,y:0,w:40,h:40};canvasState.nodes.push({id:'remote',type:'smart-image',images:[],createdBy:'agent',agentNodeId:'remote-agent',userTouchedAt:null,promptEditedByUser:false});`);
    release();await pending;
    assert.equal(e.node('a').images[0].url,'/crop.png');
    assert.equal(e.node('a').userTouchedAt,1234);
    assert.equal(e.node('b').images[0].url,'/b.png');
    assert.deepEqual(ownership(e.node('b')),{...identity,created_at:12});
    assert.equal(evaluate(e.ctx,'cropState.nodeId'),'b');
    await e.ctx.performUndo();
    assert.equal(e.node('a').images[0].url,'/original.png');
    assert.ok(e.node('remote'));
});
