import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {evaluate, fromContext, JS_DIR, loadModules} from './harness.mjs';
import {editor as baseEditor, identity, event, declarations} from './smart-human-handlers-harness-392.mjs';

const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
function editor(nodes){
    const e=baseEditor(nodes);
    e.ctx.CSS={escape:value=>value};
    e.ctx.File=File;
    e.ctx.FormData=FormData;
    Object.assign(e.ctx,{smartNodeRunTokens:new Map(),smartRunTokens:new Map(),smartGenerationNodeRevisions:new Map(),smartHistoryModal:null});
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    evaluate(e.ctx,source.split(/\r?\n/).filter(line=>/^const (?:MEDIA_|SMART_GROUP_ARRANGE_|SMART_UPLOAD_)\w+ =/.test(line)).join('\n'));
    return e;
}

const original=()=>({id:'original',type:'smart-prompt',...identity,userTouchedAt:700,promptEditedByUser:true,created_at:11,x:0,y:0,text:'keep'});

test('generic clone preserves source ownership for automatic callers',()=>{
    const e=editor([original()]);
    const cloned=fromContext(e.ctx.cloneSmartNode(e.node(),32,32));
    assert.equal(cloned.createdBy,'agent');
    assert.equal(cloned.agentNodeId,'original-agent');
    assert.equal(cloned.userTouchedAt,700);
    assert.equal(cloned.promptEditedByUser,true);
    assert.equal(cloned.created_at,11);
});

test('human nudge touches only moved group members and undo restores their ownership',async()=>{
    const e=editor([{id:'g',type:'smart-group',...identity,x:0,y:0,items:['original'],images:[]},original(),{...original(),id:'other'}]);
    const before=e.stored();
    evaluate(e.ctx,"canvasState.selectedId='g'");
    e.ctx.nudgeSelectedNodes('arrowright',10,false);
    assert.equal(e.node('g').userTouchedAt,1234);
    assert.equal(e.node('original').userTouchedAt,1234);
    assert.equal(e.node('other').userTouchedAt,700);
    assert.equal(e.node('original').promptEditedByUser,true);
    assert.equal(e.node('original').agentNodeId,'original-agent');
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),before);
});

test('arrange selection touches actual geometry changes but not an unselected node',()=>{
    const e=editor([original(),{...original(),id:'second',x:12,y:17},{...original(),id:'other',x:900}]);
    evaluate(e.ctx,"canvasState.selectedIds=['original','second']");
    e.ctx.arrangeSelectedSmartNodes();
    const moved=e.stored().filter(n=>n.id!=='other'&&(n.x!== (n.id==='original'?0:12)||n.y!==(n.id==='original'?0:17)));
    assert.ok(moved.length);
    assert.ok(moved.every(n=>n.userTouchedAt===1234&&n.createdBy==='agent'));
    assert.equal(e.node('other').userTouchedAt,700);
    assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),true);
});

test('group and split create user identities, preserve member provenance and undo ownership',async()=>{
    const e=editor([original(),{id:'photo',type:'smart-image',...identity,x:900,y:100,images:[{url:'/a.png'}]}]);
    const before=e.stored();
    evaluate(e.ctx,"canvasState.selectedIds=['original','photo']");
    e.ctx.groupSelectedNodes();
    const group=e.stored().find(n=>n.type==='smart-group');
    assert.equal(group.createdBy,'user');
    assert.equal(group.userTouchedAt,null);
    assert.equal(group.agentNodeId,null);
    assert.equal(e.node('original').createdBy,'agent');
    assert.equal(e.node('original').userTouchedAt,1234);
    e.ctx.ungroupNode(group.id);
    const image=e.stored().find(n=>n.type==='smart-image');
    assert.equal(image.createdBy,'user');
    assert.equal(image.agentNodeId,null);
    assert.equal(image.userTouchedAt,null);
    assert.equal(image.promptEditedByUser,false);
    await e.ctx.performUndo();
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),before);
});

test('ungroup a multi-image node resets split identities and touches only disconnected surviving targets',()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,x:0,y:0,images:[{url:'/a.png'},{url:'/b.png'}]}, {...original(),inputNodeIds:['photo']},{...original(),id:'other'}]);
    e.ctx.ungroupNode('photo');
    assert.ok(e.stored().filter(n=>n.type==='smart-image').every(n=>n.createdBy==='user'&&n.userTouchedAt===null&&n.promptEditedByUser===false));
    assert.equal(e.node('original').userTouchedAt,1234);
    assert.equal(e.node('other').userTouchedAt,700);
});

test('quick rename touches durable title only, same value and cancel do not stamp or add undo',()=>{
    const e=editor([{...original(),title:'old',promptEditedByUser:false}]);
    loadModules(e.ctx,['canvas-quick-edit.js']);
    const anchor=e.harness.makeElement();
    e.ctx.world.querySelector=()=>({querySelector:()=>anchor});
    const create=e.ctx.document.createElement;
    e.ctx.document.createElement=tag=>Object.assign(create(tag),{select(){}});
    const rename=(value,key)=>{
        e.ctx.quickEditNode(e.node());
        const input=e.ctx.document.body.children.at(-1);
        input.value=value;
        input.dispatchEvent(event(input,{type:'keydown',key}));
    };
    rename('old','Enter');
    assert.equal(e.node().userTouchedAt,700);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    rename('cancel','Escape');
    assert.equal(e.node().title,'old');
    rename('new','Enter');
    assert.equal(e.node().title,'new');
    assert.equal(e.node().userTouchedAt,1234);
    assert.equal(e.node().promptEditedByUser,false);
});

for(const change of ['reordered','replaced','ambiguous','recreated','project']){
    test(`quick image rename revalidates its original target: ${change}`,async()=>{
        const e=editor([{id:'photo',type:'smart-image',...identity,created_at:100,
            images:[{url:'/original.png',asset_id:'original',name:'old'}]}]);
        loadModules(e.ctx,['canvas-quick-edit.js']);
        const anchor=e.harness.makeElement();
        e.ctx.world.querySelector=()=>({querySelector:()=>anchor});
        const create=e.ctx.document.createElement;
        e.ctx.document.createElement=tag=>Object.assign(create(tag),{select(){}});
        e.ctx.quickEditNode(e.node());
        const input=e.ctx.document.body.children.at(-1);
        if(change==='reordered')e.node().images.unshift({url:'/other.png',asset_id:'other',name:'other'});
        if(change==='replaced')e.node().images[0]={url:'/other.png',asset_id:'other',name:'other'};
        if(change==='ambiguous')e.node().images.push({...e.node().images[0]});
        if(change==='recreated')e.node().created_at=200;
        if(change==='project')evaluate(e.ctx,"canvas.project_id='other-project'");
        const before=e.stored();
        input.value='new';input.dispatchEvent(event(input,{type:'keydown',key:'Enter'}));
        if(change==='reordered'){
            assert.equal(e.node().images[0].name,'other');
            assert.equal(e.node().images[1].name,'new');
            assert.equal(e.node().userTouchedAt,1234);
            await e.ctx.performUndo();
            assert.deepEqual(e.stored(),before);
        }else{
            assert.deepEqual(e.stored(),before);
            assert.equal(evaluate(e.ctx,'undoStack.length'),0);
            assert.ok(e.harness.toasts.length>0,'stale target must be visibly rejected');
        }
    });
}

test('human copy rejects the entire unresolved outbox or formal pending request before undo or clipboard changes',()=>{
    for(const intent of [{pendingActionResumes:[{kind:'conversation',actionId:'a',payload:{requestId:'r',text:'send'}}]}, {messages:[{id:'receipt',role:'user',status:'pending',request_id:'r',text:'send'}]}]){
        for(const operation of ['copy','paste','duplicate','alt']){
            const pending={...original(),type:'smart-conversation',...intent};
            const e=editor([pending,{...original(),id:'completed'}]);
            evaluate(e.ctx,`canvasState.selectedIds=['original','completed'];nodeClipboard={nodes:${JSON.stringify([pending])},connections:[]};lastMouseWorld={x:100,y:100}`);
            const before=e.stored();
            const clipboard=fromContext(evaluate(e.ctx,'nodeClipboard'));
            if(operation==='copy') e.ctx.copySelectedNodes();
            if(operation==='paste') e.ctx.pasteNodes();
            if(operation==='duplicate') e.ctx.duplicateSelectedNodes();
            if(operation==='alt') assert.equal(e.ctx.duplicateForAltDrag(e.node()),null);
            assert.deepEqual(e.stored(),before,operation);
            assert.deepEqual(fromContext(evaluate(e.ctx,'nodeClipboard')),clipboard,operation);
            assert.equal(evaluate(e.ctx,'undoStack.length'),0,operation);
            assert.match(e.harness.toasts.at(-1),/未决.*对话/);
        }
    }
});

test('completed content pastes and Alt-copies with new ownership and remapped inputs, source unchanged',()=>{
    for(const operation of ['paste','alt']){
        const e=editor([original(),{...original(),id:'target',inputNodeIds:['original']}]);
        evaluate(e.ctx,"canvasState.selectedIds=['original','target'];lastMouseWorld={x:100,y:100};canvas.connections=[{from:'original',to:'target',kind:'input'}]");
        const before=e.stored();
        if(operation==='paste'){e.ctx.copySelectedNodes();e.ctx.pasteNodes();}
        else e.ctx.duplicateForAltDrag(e.node(),true);
        const copies=e.stored().filter(n=>!['original','target'].includes(n.id));
        assert.equal(copies.length,2);
        assert.ok(copies.every(n=>n.createdBy==='user'&&n.agentNodeId===null&&n.userTouchedAt===null&&n.promptEditedByUser===false&&n.created_at===1234));
        assert.equal(copies[1].inputNodeIds[0],copies[0].id);
        assert.deepEqual(e.stored().filter(n=>['original','target'].includes(n.id)),before);
    }
});

for(const operation of ['paste','duplicate','alt']){
    test(`#451 ${operation} saves copied links independently of the originals`,()=>{
        const e=editor([original(),{...original(),id:'target',inputNodeIds:['original']}]);
        const edge={id:'edge-source',from:'original',to:'target',fromPort:'out',toPort:'in',kind:'input'};
        e.ctx.sourceEdge=edge;
        evaluate(e.ctx,`canvas.connections=[{...sourceEdge}];canvasState.selectedIds=['original','target'];lastMouseWorld={x:100,y:100}`);
        const baseline=e.ctx.projectCanvasSnapshot(e.ctx.canvasForStorage());
        e.ctx.advanceSmartCanvasShadow(e.ctx.canvasForStorage());
        e.ctx.settleSmartCanvasPendingView();
        let copyCount=1;
        if(operation==='paste'){
            e.ctx.copySelectedNodes();
            e.ctx.pasteNodes();
            e.ctx.pasteNodes();
            copyCount=2;
        }else if(operation==='duplicate') e.ctx.duplicateSelectedNodes();
        else e.ctx.duplicateForAltDrag(e.node(),true);
        const stored=fromContext(e.ctx.canvasForStorage());
        const copies=stored.nodes.slice(2);
        assert.equal(copies.length,2*copyCount);
        assert.deepEqual(stored.connections[0],edge);
        assert.equal(stored.connections.length,1+copyCount);
        assert.equal(new Set(stored.connections.map(link=>link.id)).size,1+copyCount);
        for(let i=0;i<copyCount;i++){
            const link=stored.connections[i+1];
            assert.ok(link.id);
            assert.deepEqual(link,{...edge,id:link.id,from:copies[2*i].id,to:copies[2*i+1].id});
            assert.deepEqual(copies[2*i+1].inputNodeIds,[copies[2*i].id]);
        }
        const {mutations}=fromContext(e.ctx.buildCoreMutations(stored,baseline));
        assert.deepEqual(mutations.filter(m=>m.op==='upsert_edge').map(m=>m.edge),stored.connections.slice(1));
        assert.equal(mutations.some(m=>m.op==='remove_edge'),false);
    });
}

test('human port connection stamps only its input target, repeats and automatic shared connections stay negative',async()=>{
    const e=editor([{id:'source',type:'smart-image',...identity,images:[],x:0,y:0},{...original(),id:'target',promptEditedByUser:false}]);
    const nodeEl={dataset:{id:'target'},getBoundingClientRect:()=>({left:100,width:200})};
    const port={dataset:{port:'in'},closest:()=>nodeEl};
    e.ctx.document.elementFromPoint=()=>({closest:s=>s==='.node-port'?port:nodeEl});
    e.ctx.capturePendingUndo();
    e.ctx.handlePortDrop({fromId:'source',fromPort:'out',moved:true},{clientX:100,clientY:0});
    assert.equal(e.node('target').userTouchedAt,1234);
    assert.equal(e.node('target').promptEditedByUser,false);
    assert.equal(e.node('source').userTouchedAt,null);
    await e.ctx.performUndo();
    assert.equal(e.node('target').userTouchedAt,700);
    e.ctx.connectInputNode('source','target');
    assert.equal(e.node('target').userTouchedAt,700);
});

test('local-edit original rewire pins the selected thumbnail and clears the old pixel selection', async()=>{
    const e=editor([
        {id:'old',type:'smart-image',images:[{asset_id:'old-image'}]},
        {id:'source',type:'smart-image',images:[{asset_id:'first'},{asset_id:'mask',role:'mask'}, {asset_id:'picked'}]},
        {id:'target',type:'local-edit',mode:'modify',localEditActiveRole:'original',source_media_id:'old-image',
            original_asset_id:'old-image',selection:[0,0,4,4],prompt:'edit'},
    ]);
    loadModules(e.ctx,['canvas-node-ops.js']);
    evaluate(e.ctx,"canvas.connections=[{from:'old',to:'target',kind:'input',role:'original'}]; canvasState.selectedImage={nodeId:'source',index:2}");
    const nodeEl={dataset:{id:'target'}};
    const port={dataset:{port:'in'},closest:()=>nodeEl};
    e.ctx.document.elementFromPoint=()=>({closest:s=>s==='.node-port'?port:nodeEl});
    e.ctx.capturePendingUndo();
    e.ctx.handlePortDrop({fromId:'source',fromPort:'out',moved:true},{clientX:100,clientY:0});
    const target=e.node('target');
    assert.equal(target.source_media_id,'picked');
    assert.deepEqual(fromContext(target.selection),[]);
    const html=e.ctx.localEditBodyHtml(target);
    assert.match(html,/data-local-edit-preview src="\/api\/project-library\/unit-392\/media\/picked"/);
    assert.equal(target.original_asset_id,'picked');
    await e.ctx.performUndo();
    assert.equal(e.node('target').source_media_id,'old-image');
    assert.deepEqual(fromContext(e.node('target').selection),[0,0,4,4]);
});

test('local-edit original rewire with no selected thumbnail does not guess between several originals',()=>{
    const e=editor([
        {id:'old',type:'smart-image',images:[{asset_id:'old-image'}]},
        {id:'source',type:'smart-image',images:[{asset_id:'first'},{asset_id:'second'}]},
        {id:'target',type:'local-edit',mode:'modify',localEditActiveRole:'original',source_media_id:'old-image',
            original_asset_id:'old-image',selection:[0,0,4,4],prompt:'edit'},
    ]);
    loadModules(e.ctx,['canvas-node-ops.js']);
    evaluate(e.ctx,"canvas.connections=[{from:'old',to:'target',kind:'input',role:'original'}]");
    const nodeEl={dataset:{id:'target'}};
    const port={dataset:{port:'in'},closest:()=>nodeEl};
    e.ctx.document.elementFromPoint=()=>({closest:s=>s==='.node-port'?port:nodeEl});
    const drop=()=>e.ctx.handlePortDrop({fromId:'source',fromPort:'out',moved:true},{clientX:100,clientY:0});
    drop();
    assert.equal(e.harness.toasts.at(-1),'smart.localEditPickOriginalThumbnail');
    assert.equal(e.node('target').source_media_id,'old-image');
    assert.equal(evaluate(e.ctx,'canvas.connections[0].from'),'old');
    e.node('source').images.pop();
    drop();
    assert.equal(e.node('target').source_media_id,'first');
    assert.equal(evaluate(e.ctx,'canvas.connections[0].from'),'source');
});

test('local-edit preview and request share the saved original, and a missing pin blocks replay',async()=>{
    const e=editor([
        {id:'source',type:'smart-image',images:[{asset_id:'mask',role:'mask'}, {asset_id:'other'}, {asset_id:'picked',url:'https://example.invalid/wrong.png'}]},
        {id:'target',type:'local-edit',mode:'modify',source_media_id:'picked',original_asset_id:'picked',selection:[0,0,4,4],prompt:'edit'},
    ]);
    loadModules(e.ctx,['canvas-node-ops.js']);
    evaluate(e.ctx,"canvas.connections=[{from:'source',to:'target',kind:'input',role:'original'}]");
    const target=e.node('target');
    const requests=[];
    e.ctx.fetch=async(url,opts)=>{
        requests.push(JSON.parse(opts.body));
        return {ok:true,json:async()=>({assets:{}})};
    };
    assert.match(e.ctx.localEditBodyHtml(target),/data-local-edit-preview src="\/api\/project-library\/unit-392\/media\/picked"/);
    await e.ctx.submitLocalEditRequest(target);
    assert.equal(requests[0].original_asset_id,'picked');
    target.source_media_id='other';
    await e.ctx.submitLocalEditRequest(target,'picked');
    assert.equal(requests.length,1);
    assert.equal(e.harness.toasts.at(-1),'smart.localEditOriginalChanged');
    target.source_media_id='picked';
    e.node('source').images.pop();
    assert.doesNotMatch(e.ctx.localEditBodyHtml(target),/data-local-edit-preview/);
    await e.ctx.submitLocalEditRequest(target);
    assert.equal(requests.length,1);
    assert.equal(target.source_media_id,'picked');
    assert.equal(e.harness.toasts.at(-1),'smart.localEditPinnedOriginalUnavailable');
});

test('local-edit pointer selection is measured in the pinned preview’s original pixels',async()=>{
    const e=editor([
        {id:'source',type:'smart-image',images:[{asset_id:'other',width:40,height:20},{asset_id:'picked'}]},
        {id:'target',type:'local-edit',mode:'retain',selectionType:'polygon',source_media_id:'picked',selection:[],prompt:'edit'},
    ]);
    loadModules(e.ctx,['canvas-node-ops.js']);
    evaluate(e.ctx,"canvas.connections=[{from:'source',to:'target',kind:'input',role:'original'}]");
    const preview=e.harness.makeElement(), drawing=e.harness.makeElement(), prompt=e.harness.makeElement();
    preview.naturalWidth=400;
    preview.naturalHeight=200;
    drawing.getBoundingClientRect=()=>({left:0,top:0,width:200,height:100});
    drawing.getContext=()=>null;
    prompt.value='edit';
    const form=e.form({'[data-local-edit-preview]':[preview],'[data-local-edit-selection-canvas]':[drawing],'[data-local-edit-role="prompt"]':[prompt]});
    const target=e.node('target');
    e.ctx.bindLocalEditNodeControls(form,target);
    [[20,10],[100,30],[120,80]].forEach(([clientX,clientY])=>
        drawing.dispatchEvent(event(drawing,{type:'pointerdown',button:0,clientX,clientY})));
    drawing.dispatchEvent(event(drawing,{type:'dblclick'}));
    const requests=[];
    e.ctx.fetch=async(url,opts)=>{
        requests.push(JSON.parse(opts.body));
        return {ok:true,json:async()=>({assets:{}})};
    };
    await e.ctx.submitLocalEditRequest(target);
    assert.equal(requests[0].original_asset_id,'picked');
    assert.deepEqual(requests[0].selection.points,[[40,20],[200,60],[240,160]]);
});

test('inserting a loop touches the two changed incoming-edge targets, not the outgoing source',()=>{
    const e=editor([{id:'source',type:'smart-image',...identity,images:[]},{id:'loop',type:'smart-loop',...identity,imageInput:true},{...original(),id:'target',inputNodeIds:['source'],promptEditedByUser:false}]);
    evaluate(e.ctx,"canvas.connections=[{from:'source',to:'target',kind:'flow'}]");
    e.ctx.insertLoopNodeIntoConnection(e.node('loop'),{index:0,conn:{from:'source',to:'target',kind:'flow'}});
    assert.equal(e.node('loop').userTouchedAt,1234);
    assert.equal(e.node('target').userTouchedAt,1234);
    assert.equal(e.node('target').promptEditedByUser,false);
    assert.equal(e.node('source').userTouchedAt,null);
});

test('paste preserves original source but touches an existing external input target receiving the copied edge',()=>{
    const e=editor([original(),{...original(),id:'external',promptEditedByUser:false}]);
    evaluate(e.ctx,"canvasState.selectedId='original';lastMouseWorld={x:100,y:100};canvas.connections=[{from:'original',to:'external',kind:'input'}]");
    e.ctx.copySelectedNodes();
    e.ctx.pasteNodes();
    assert.equal(e.node('original').userTouchedAt,700);
    assert.equal(e.node('external').userTouchedAt,1234);
    assert.equal(e.node('external').promptEditedByUser,false);
});

function pointerEditor(nodes){
    const e=editor(nodes);
    loadModules(e.ctx,['canvas-node-ops.js','canvas-snap.js']);
    Object.assign(e.ctx,{shell:e.harness.makeElement(),smartMinimapDrag:null,portDragState:null,promptResizeState:null,selectionState:null,previewCompareDrag:null,previewPanDrag:null,imageEditPanDrag:null,cropDrag:null,cropState:null,resizeState:null,llmInstructionResizeState:null,promptSplitResizeState:null,thumbDragState:null,panState:null,dragState:null,composerDragSettleTimer:0,interactionLayerRaf:0,suppressNodeClickUntil:0});
    evaluate(e.ctx,source.slice(source.indexOf('window.onmousemove ='),source.indexOf('function canvasWheelIsReserved')));
    const resize=e.harness.makeElement(),el=e.harness.makeElement();
    el.dataset.id=nodes[0].id;
    el.querySelector=s=>s==='.node-resize-handle'?resize:null;
    e.ctx.world.querySelectorAll=s=>s==='.image-node'?[el]:[];
    e.ctx.document.elementFromPoint=()=>null;
    e.ctx.bindNodeEvents();
    const target={closest:()=>null};
    return {...e,el,resize,pointer:extra=>event(target,{clientX:0,clientY:0,button:0,...extra})};
}

test('pointer resize and drag touch geometry with same-action undo; click and rejected Alt drag stay negative',async()=>{
    for(const gesture of ['resize','drag']){
        const e=pointerEditor([{id:'photo',type:'smart-image',...identity,images:[],x:0,y:0,w:200,h:200,scale:1}]);
        const before=e.stored();
        const down=()=>gesture==='resize'?e.resize.dispatchEvent({...e.pointer(),type:'mousedown'}):e.el.onmousedown(e.pointer());
        down();e.ctx.onmouseup(e.pointer());
        assert.equal(e.node().userTouchedAt,null);
        down();e.ctx.onmousemove(e.pointer({clientX:50,clientY:30}));e.ctx.onmouseup(e.pointer({clientX:50,clientY:30}));
        assert.equal(e.node().userTouchedAt,1234,gesture);
        assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),true,gesture);
        assert.equal(e.node().promptEditedByUser,false);
        await e.ctx.performUndo();
        assert.equal(e.node().userTouchedAt,null);
        assert.equal(e.node().x,before[0].x);
        assert.equal(e.node().w,before[0].w);
    }
    const e=pointerEditor([{id:'photo',type:'smart-image',...identity,images:[],pendingActionResumes:[{kind:'conversation',actionId:'a',payload:{requestId:'r',text:'send'}}]}]);
    e.el.onmousedown(e.pointer({altKey:true}));
    assert.equal(e.ctx.dragState,null);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
});

test('dropping into an existing group touches its changed media and moved members, never a removed source',()=>{
    const e=pointerEditor([{id:'photo',type:'smart-image',...identity,images:[{url:'/a.png'}],x:0,y:0,w:100,h:100,scale:1},{id:'g',type:'smart-group',...identity,items:[],images:[],x:500,y:0,w:400,h:400}]);
    e.el.onmousedown(e.pointer());
    e.ctx.onmousemove(e.pointer({clientX:600,clientY:100}));
    e.ctx.onmouseup(e.pointer({clientX:600,clientY:100}));
    assert.equal(e.node('g').userTouchedAt,1234);
    assert.equal(e.node('g').promptEditedByUser,false);
    assert.equal(e.stored().some(n=>n.id==='photo'),false);
});

test('append media to an existing node carries T with its undo and leaves prompt provenance alone',async()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,x:0,y:0,images:[{url:'/a.png'}]}]);
    loadModules(e.ctx,['canvas-node-ops.js']);
    const before=e.stored();
    e.ctx.appendImagesToSmartNode([{url:'/b.png'}],'photo');
    assert.equal(e.node().userTouchedAt,1234);
    assert.equal(e.node().promptEditedByUser,false);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),before);
});

test('upload completion preserves concurrent server media and does not retarget a removed node or switched project',async()=>{
    for(const interleave of ['remote','removed','project']){
        const e=editor([{id:'photo',type:'smart-image',...identity,x:0,y:0,images:[{url:'/a.png'}]}]);
        loadModules(e.ctx,['canvas-node-ops.js']);
        let resolve;
        e.ctx.fetch=()=>new Promise(done=>{resolve=done;});
        const uploading=e.ctx.handleFiles([new File(['x'],'b.png',{type:'image/png'})],'photo');
        if(interleave==='remote'){
            const remote=fromContext(e.ctx.canvasForStorage());remote.revision=1;
            remote.nodes[0].images.push({url:'/remote.png'});
            remote.nodes.push({...original(),id:'remote'});
            e.ctx.applyMergedServerCanvas(remote);
        }else if(interleave==='removed')evaluate(e.ctx,"canvasState.nodes=[];canvas.nodes=canvasState.nodes");
        else evaluate(e.ctx,"canvas.project_id='other-project'");
        resolve({ok:true,json:async()=>({files:[{url:'/b.png'}]})});
        await uploading;
        if(interleave==='remote'){
            assert.deepEqual(fromContext(e.node().images).map(img=>img.url),['/a.png','/remote.png','/b.png']);
            assert.equal(e.node('remote').userTouchedAt,700);
            await e.ctx.performUndo();
            assert.deepEqual(fromContext(e.node().images).map(img=>img.url),['/a.png','/remote.png']);
            assert.ok(e.node('remote'));
        }else{
            assert.equal(evaluate(e.ctx,'undoStack.length'),0);
            assert.equal(e.stored().some(n=>n.images?.some(img=>img.url==='/b.png')),false);
        }
    }
});

test('single image delete pairs media and T in undo; absent image is not an action',async()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[{url:'/a.png'},{url:'/b.png'}]}]);
    const before=e.stored();
    await e.ctx.deleteImage('photo',8);
    assert.equal(e.node().userTouchedAt,null);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    await e.ctx.deleteImage('photo',0);
    assert.equal(e.node().userTouchedAt,1234);
    assert.equal(e.node().promptEditedByUser,false);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),before);
});

test('delete confirmation revalidates the same output and project without overwriting concurrent media',async()=>{
    for(const interleave of ['remote','removed','missing-images','project']){
        const e=editor([{id:'photo',type:'smart-image',...identity,images:[{url:'/a.png',asset_id:'a',output_id:'out-a'},{url:'/b.png',output_id:'out-b'}]}]);
        evaluate(e.ctx,"canvas.history=[{id:'history',outputs:[{url:'/a.png',asset_id:'a',output_id:'out-a'}]}]");
        const deleting=e.ctx.deleteImage('photo',0);
        if(interleave==='remote'){
            const remote=fromContext(e.ctx.canvasForStorage());remote.revision=1;
            remote.nodes[0].images.push({url:'/remote.png',output_id:'remote'});
            e.ctx.applyMergedServerCanvas(remote);
        }else if(interleave==='removed')evaluate(e.ctx,"canvasState.nodes=[];canvas.nodes=canvasState.nodes");
        else if(interleave==='missing-images')delete e.node().images;
        else evaluate(e.ctx,"canvas.project_id='another'");
        const dialog=e.ctx.document.body.children.find(el=>el.id==='smartImageDeleteScopeBackdrop');
        assert.ok(dialog);
        dialog.dispatchEvent(event({closest:()=>({dataset:{scope:'canvas'}})},{type:'click'}));
        await deleting;
        if(interleave==='remote'){
            assert.deepEqual(fromContext(e.node().images).map(img=>img.url),['/b.png','/remote.png']);
            assert.equal(e.node().userTouchedAt,1234);
        }else assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    }
});

test('original media undo rejects a resurrected conversation intent before POST and leaves its stack intact',async()=>{
    const e=editor([{id:'chat',type:'smart-conversation',...identity,messages:[]}]);
    let server=fromContext(e.ctx.canvasForStorage()),posts=0;
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/original/restore')){posts++;return {ok:true,status:200,json:async()=>({canvas:server})};}
        if(options.method==='PATCH'){
            for(const mutation of JSON.parse(options.body).mutations)if(mutation.op==='upsert_node')server.nodes[server.nodes.findIndex(n=>n.id===mutation.node.id)]=mutation.node;
            server.revision++;return {ok:true,status:200,json:async()=>({saved:true,canvas:server,revision:server.revision})};
        }
        return {ok:true,status:200,json:async()=>({saved:true,revision:server.revision})};
    };
    const before={...e.node('chat'),messages:[{id:'old-server',role:'user',status:'pending',request_id:'old-request',text:'old input'}]};
    const delta=e.ctx.canvasCore.smartCanvasActionDelta({nodes:[before]},{nodes:[e.node('chat')]});
    const token={projectOriginalDelete:{assetId:'asset-old',deletionId:'deletion-old',projectId:'unit-392',localActionDelta:delta}};
    e.ctx.pushUndo(token);
    await e.ctx.performUndo();
    assert.equal(posts,0,'no restore POST may precede the D3 preflight');
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    assert.deepEqual(fromContext(e.node('chat').messages),[]);
    assert.equal(e.node('chat').userTouchedAt,null);
    assert.ok(e.harness.toasts.includes('smart.canvasConflict'));
});

test('original media undo does not treat a malformed present local delta as an absent legacy field',async()=>{
    for(const delta of [null,false]){
        const e=editor([{id:'photo',type:'smart-image',...identity,images:[]}]);
        const server=fromContext(e.ctx.canvasForStorage());let restores=0;
        e.ctx.fetch=async(url,options={})=>{
            if(url.endsWith('/original/restore'))restores++;
            return {ok:true,status:200,json:async()=>({canvas:server,items:[],saved:true,revision:server.revision})};
        };
        e.ctx.pushUndo({projectOriginalDelete:{assetId:'a',deletionId:'d',projectId:'unit-392',localActionDelta:delta}});
        const content=()=>e.stored().map(node=>fromContext(e.ctx.smartHumanEditContent(node)));
        const before=content();
        await e.ctx.performUndo();
        assert.equal(restores,0,'malformed stored delta must fail before restoring external media');
        assert.deepEqual(content(),before);
        assert.deepEqual(Object.fromEntries(Object.keys(identity).map(key=>[key,e.node('photo')[key]])),identity);
        assert.equal(evaluate(e.ctx,'undoStack.length'),1);
        assert.ok(e.harness.toasts.length>0);
    }
});

test('original restore uses the unified receiver and preserves post-request local edits and newer server nodes',async()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[]},original()]);
    e.ctx.persistedDurableSnapshot=e.ctx.FireflyProjectCanvasMutations.durableSnapshotFromCanvas(e.ctx.canvasForStorage());
    let resolveRestore,server=fromContext(e.ctx.canvasForStorage());
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/original/restore'))return new Promise(resolve=>{resolveRestore=resolve;});
        if(options.method==='PATCH'){
            for(const mutation of JSON.parse(options.body).mutations){
                if(mutation.op==='upsert_node')server.nodes[server.nodes.findIndex(n=>n.id===mutation.node.id)]=mutation.node;
            }
            server.revision++;
            return {ok:true,status:200,json:async()=>({saved:true,canvas:server,revision:server.revision})};
        }
        return {ok:true,status:200,json:async()=>({saved:true,revision:server.revision})};
    };
    const restoring=e.ctx.restoreSmartProjectOriginalUndo({projectOriginalDelete:{assetId:'a',deletionId:'d',projectId:'unit-392'}});
    for(let i=0;i<20&&!resolveRestore;i++)await new Promise(resolve=>setImmediate(resolve));
    assert.ok(resolveRestore);
    const restored=fromContext(e.ctx.canvasForStorage());restored.revision=1;restored.nodes[0].images=[{asset_id:'a'}];
    server=structuredClone(restored);server.revision=2;server.nodes.push({...original(),id:'remote'});
    e.ctx.applyMergedServerCanvas(server);
    e.node('original').text='late local';e.ctx.scheduleSave();
    resolveRestore({ok:true,json:async()=>({canvas:restored})});
    await restoring;
    assert.ok(e.node('remote'));
    assert.equal(e.node('original').text,'late local');
    assert.equal(e.node('photo').userTouchedAt,null);
    assert.equal(fromContext(e.ctx.smartCanvasPendingState().server).nodes.find(n=>n.id==='original').text,'late local');
});

test('delete original scopes T to the exact output owner and restores its previous T in the same media undo',async()=>{
    const image={asset_id:'a',output_id:'out-a'};
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[image]},{...original(),id:'other'}]);
    let server=fromContext(e.ctx.canvasForStorage());server.revision=1;server.history=[{id:'history',result_node_id:'photo',outputs:[image]}];
    e.ctx.applyMergedServerCanvas(server);
    const response=data=>({ok:true,status:200,json:async()=>structuredClone(data)});
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/original/delete')){
            server.nodes[0].images=[];server.history[0].outputs=[];server.revision++;
            server.nodes.push({...original(),id:'remote'});
            return response({canvas:server,deleted:{deletion_id:'d',result_node_id:'photo',output_id:'out-a'}});
        }
        if(url.endsWith('/original/restore')){
            server.nodes[0].images=[image];server.history[0].outputs=[image];server.revision++;
            return response({canvas:server,restored:{deletion_id:'d',result_node_id:'photo',output_id:'out-a'}});
        }
        if(options.method==='PATCH'){
            for(const mutation of JSON.parse(options.body).mutations)if(mutation.op==='upsert_node')server.nodes[server.nodes.findIndex(n=>n.id===mutation.node.id)]=mutation.node;
            server.revision++;return response({canvas:server,saved:true,revision:server.revision});
        }
        return response({saved:true,revision:server.revision});
    };
    await e.ctx.deleteSmartProjectOriginal('history',0);
    assert.equal(e.node('photo').userTouchedAt,1234);
    assert.equal(e.node('other').userTouchedAt,700);
    assert.equal(e.node('remote').userTouchedAt,700);
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    await e.ctx.performUndo();
    assert.equal(e.node('photo').userTouchedAt,null);
    assert.equal(e.node('photo').createdBy,'agent');
    assert.equal(e.node('photo').promptEditedByUser,false);
    assert.equal(e.node('photo').images[0].output_id,'out-a');
    assert.ok(e.node('remote'));
});

function conversationEditor(){
    const payload={conversationProtocol:1,projectId:'unit-392',nodeId:'talk',requestId:'r',text:'hello',humanIntent:true};
    const record={kind:'conversation',actionId:'a',payload};
    const e=editor([{id:'talk',type:'smart-conversation',...identity,messages:[],pendingActionResumes:[record]}]);
    Object.assign(e.ctx,{AbortController,conversationCanvasLastAcceptedRevision:0,conversationTaskPolls:new Map()});
    return {...e,entry:{...record,nodeId:'talk'},payload,server:fromContext(e.ctx.canvasForStorage())};
}

test('conversation preparation revalidates the original intent after each registration await',async()=>{
    for(const phase of [0,1,2,3]){
        const e=conversationEditor();
        let step=0,release;
        const pause=async()=>{if(step++===phase)await new Promise(resolve=>{release=resolve;});};
        e.ctx.fetch=async url=>{
            await pause();
            return {ok:true,status:200,json:async()=>{await pause();return url.endsWith('/messages/r')?{detail:'not registered'}:{canvas:e.server};}};
        };
        const preparing=e.ctx.prepareConversationAction(e.entry);
        for(let i=0;i<20&&!release;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.ok(release);
        e.node().pendingActionResumes=[];
        release();
        await assert.rejects(preparing,/smart.canvasConflict/);
        assert.equal(e.node().userTouchedAt,null);
    }
});

test('conversation POST rejects malformed present outboxes even when a registered message matches',async()=>{
    for(const bad of [null,false,{},[null],[{kind:'conversation',actionId:'a',payload:null}]]){
        const e=conversationEditor();let externalCalls=0;
        e.entry.preparedConversationContext=e.ctx.conversationActionContext(e.entry);
        e.node().pendingActionResumes=bad;
        e.node().messages=[{id:'receipt',role:'user',request_id:'r',text:'hello',status:'pending',user_edit:true}];
        e.ctx.fetch=async()=>{externalCalls++;throw new Error('request reached external boundary');};
        await assert.rejects(e.ctx.sendConversationMessageRequest(e.node(),'hello','r',true,e.entry),
            /Invalid (pendingActionResumes|pending action record|conversation intent payload)/);
        assert.equal(externalCalls,0);
    }
});

test('conversation POST checks the prepared packet and original live intent, while same-key reload remains valid',async()=>{
    for(const change of ['outbox','packet','project','reload']){
        const e=conversationEditor();let posts=0;
        e.ctx.fetch=async(url,options={})=>{
            if(options.method==='POST'){
                posts++;
                const accepted=structuredClone(e.server);accepted.revision=2;
                accepted.nodes[0].pendingActionResumes=[];
                accepted.nodes[0].messages=[{id:'receipt',role:'user',request_id:'r',text:'hello',status:'completed',user_edit:true}];
                return {ok:true,status:200,json:async()=>({canvas:accepted})};
            }
            return {ok:true,status:200,json:async()=>url.endsWith('/messages/r')?{detail:'not registered'}:{canvas:e.server}};
        };
        assert.equal(await e.ctx.prepareConversationAction(e.entry),true);
        if(change==='outbox')e.node().pendingActionResumes=[];
        if(change==='packet')e.entry.payload={...e.payload,text:'different'};
        if(change==='project')evaluate(e.ctx,"canvas.project_id='other'");
        if(change==='reload')e.ctx.applyMergedServerCanvas({...e.server,revision:1});
        const sending=e.ctx.sendConversationMessageRequest(e.node(),'hello','r',true,e.entry);
        if(change==='reload')await sending;else await assert.rejects(sending,/smart.canvasConflict/);
        assert.equal(posts,change==='reload'?1:0,change);
    }
});

test('thumb detach keeps a fresh user identity and includes source media T in the same undo',async()=>{
    const e=pointerEditor([{id:'photo',type:'smart-image',...identity,images:[{url:'/a.png'},{url:'/b.png'}],x:0,y:0}]);
    const before=e.stored();
    e.ctx.capturePendingUndo();
    e.ctx.thumbDragState={nodeId:'photo',imgIndex:0,startX:0,startY:0,detached:false};
    e.ctx.onmousemove(e.pointer({clientX:50,clientY:30}));
    e.ctx.onmouseup(e.pointer({clientX:50,clientY:30}));
    assert.equal(e.node('photo').userTouchedAt,1234);
    const detached=e.stored().find(node=>node.id!=='photo');
    assert.equal(detached.createdBy,'user');assert.equal(detached.userTouchedAt,null);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),before);
});

test('group and ungroup attribute rerouted or removed incoming edges even without inputNodeIds',()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[{url:'/a.png'}],x:0,y:0},original()]);
    evaluate(e.ctx,"canvas.connections=[{from:'photo',to:'original',kind:'flow'}];canvasState.selectedId='photo'");
    e.ctx.groupSelectedNodes();
    assert.equal(e.node('original').userTouchedAt,1234);
    e.node('original').userTouchedAt=1500;
    const group=e.stored().find(node=>node.type==='smart-group');
    e.ctx.ungroupNode(group.id);
    assert.equal(e.node('original').userTouchedAt,1234);
});

test('canvas plus original deletion is one undo step restoring both its image and prior T',async()=>{
    const image={asset_id:'a',output_id:'out-a'};
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[image]}]);
    let server=fromContext(e.ctx.canvasForStorage());server.revision=1;server.history=[{id:'history',result_node_id:'photo',outputs:[image]}];
    e.ctx.applyMergedServerCanvas(server);
    const response=data=>({ok:true,status:200,json:async()=>structuredClone(data)});
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/original/delete')){server.nodes[0].images=[];server.history[0].outputs=[];server.revision++;return response({canvas:server,deleted:{deletion_id:'d',result_node_id:'photo',output_id:'out-a'}});}
        if(url.endsWith('/original/restore')){server.nodes[0].images=[image];server.history[0].outputs=[image];server.revision++;return response({canvas:server});}
        if(options.method==='PATCH'){
            for(const mutation of JSON.parse(options.body).mutations)if(mutation.op==='upsert_node')server.nodes[0]=mutation.node;
            server.revision++;return response({canvas:server,saved:true,revision:server.revision});
        }
        return response({saved:true,revision:server.revision});
    };
    const deleting=e.ctx.deleteImage('photo',0);
    e.ctx.document.body.children.find(el=>el.id==='smartImageDeleteScopeBackdrop').dispatchEvent(event({closest:()=>({dataset:{scope:'both'}})},{type:'click'}));
    await deleting;
    for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    await e.ctx.performUndo();
    assert.equal(e.node().userTouchedAt,null);
    assert.equal(e.node().images[0].output_id,'out-a');
});

test('Alt-dragging a group keeps copied container new but touches actually moved existing members',()=>{
    const e=pointerEditor([{id:'g',type:'smart-group',...identity,images:[],items:['member'],x:0,y:0,w:400,h:400},{id:'member',type:'smart-prompt',...identity,x:20,y:20,w:300,h:200,text:'keep'}]);
    e.el.onmousedown(e.pointer({altKey:true}));
    e.ctx.onmousemove(e.pointer({clientX:500,clientY:500}));
    e.ctx.onmouseup(e.pointer({clientX:500,clientY:500}));
    const copy=e.stored().find(node=>!['g','member'].includes(node.id));
    assert.equal(copy.createdBy,'user');assert.equal(copy.userTouchedAt,null);
    assert.equal(e.node('member').userTouchedAt,1234);
    assert.equal(e.node('g').userTouchedAt,null);
});

test('quick image rename submits against the current node after a server receive, preserving new media',()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[{url:'/a.png',name:'a'}]}]);
    loadModules(e.ctx,['canvas-quick-edit.js']);
    const anchor=e.harness.makeElement();
    e.ctx.world.querySelector=()=>({querySelector:()=>anchor});
    const create=e.ctx.document.createElement;e.ctx.document.createElement=tag=>Object.assign(create(tag),{select(){}});
    e.ctx.quickEditNode(e.node());
    const input=e.ctx.document.body.children.at(-1);
    const remote=fromContext(e.ctx.canvasForStorage());remote.revision=1;remote.nodes[0].images.push({url:'/remote.png'});
    e.ctx.applyMergedServerCanvas(remote);
    input.value='renamed';input.dispatchEvent(event(input,{type:'keydown',key:'Enter'}));
    assert.equal(e.node().images[0].name,'renamed');
    assert.equal(e.node().images.length,2);
    assert.equal(e.node().userTouchedAt,1234);
});

test('node-internal prompt panel resizing changes node geometry with T but never P',()=>{
    for(const selector of ['[data-llm-instruction-resize]','[data-prompt-split-resize]']){
        const e=pointerEditor([{id:'photo',type:'smart-prompt',...identity,x:0,y:0,w:316,h:500,text:'keep'}]);
        const handle=e.harness.makeElement();
        e.ctx.bindPromptNodeControls(e.form({[selector]:[handle]}),e.node());
        handle.dispatchEvent({...e.pointer(),type:'mousedown'});
        e.ctx.onmousemove(e.pointer({clientY:80}));e.ctx.onmouseup(e.pointer({clientY:80}));
        assert.equal(e.node().userTouchedAt,1234);
        assert.equal(e.node().promptEditedByUser,false);
        assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),true);
    }
});

test('zero pointer movement does not normalize implicit geometry or create a human edit',()=>{
    for(const selector of ['.node-resize-handle','[data-llm-instruction-resize]','[data-prompt-split-resize]']){
        const e=pointerEditor([{id:'photo',type:'smart-prompt',...identity,text:'keep',x:0,y:0}]);
        let handle=e.resize;
        if(selector!=='.node-resize-handle'){
            handle=e.harness.makeElement();e.ctx.bindPromptNodeControls(e.form({[selector]:[handle]}),e.node());
        }
        const before=e.stored();
        handle.dispatchEvent({...e.pointer(),type:'mousedown'});
        e.ctx.onmousemove(e.pointer());e.ctx.onmouseup(e.pointer());
        assert.deepEqual(e.stored(),before,selector);
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    }
});

test('a server geometry update during pointer-down without movement remains automatic',()=>{
    for(const gesture of ['resize','drag']){
        const e=pointerEditor([{id:'photo',type:'smart-image',...identity,x:0,y:0,w:200,h:200,scale:1,images:[]}]);
        if(gesture==='resize')e.resize.dispatchEvent({...e.pointer(),type:'mousedown'});else e.el.onmousedown(e.pointer());
        const remote=fromContext(e.ctx.canvasForStorage());remote.revision=1;remote.nodes[0].x=100;remote.nodes[0].w=250;
        e.ctx.applyMergedServerCanvas(remote);
        e.ctx.onmouseup(e.pointer());
        assert.equal(e.node().userTouchedAt,null,gesture);
        assert.equal(evaluate(e.ctx,'undoStack.length'),0,gesture);
        assert.equal(e.node().positionUpdatedAt,undefined,gesture);
    }
});

test('original deletion touches an owner whose exact output changes only through its tombstone',async()=>{
    const e=editor([{id:'photo',type:'smart-image',...identity,images:[]}]);
    let server=fromContext(e.ctx.canvasForStorage());server.revision=1;
    server.history=[{id:'history',result_node_id:'photo',outputs:[{asset_id:'a',output_id:'out-a'}]}];
    e.ctx.applyMergedServerCanvas(server);
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/original/delete')){
            server.revision++;server.history[0].outputs=[];
            server.nodes[0].deletedOutputTombstones=[{deleted_output_id:'out-a',deletion_id:'d'}];
            return {ok:true,status:200,json:async()=>({canvas:structuredClone(server),deleted:{deletion_id:'d',result_node_id:'photo',output_id:'out-a'}})};
        }
        if(options.method==='PATCH'){
            for(const mutation of JSON.parse(options.body).mutations)if(mutation.op==='upsert_node')server.nodes[0]=mutation.node;
            server.revision++;
        }
        return {ok:true,status:200,json:async()=>({canvas:structuredClone(server),saved:true,revision:server.revision})};
    };
    await e.ctx.deleteSmartProjectOriginal('history',0);
    assert.equal(e.node().userTouchedAt,1234,JSON.stringify(e.harness.toasts));
    assert.equal(e.node().promptEditedByUser,false);
});

for(const images of [undefined, []]){
    test(`stale media deletion with ${images === undefined ? 'missing' : 'empty'} images is a no-op`, async()=>{
        const original={id:'image',type:'smart-image',...identity};
        if(images !== undefined) original.images=images;
        const e=editor([original]);
        const before=e.stored();
        await e.ctx.deleteImage('image',0);
        assert.deepEqual(e.stored(),before);
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    });
}

test('human duplicate creates a fresh user identity without changing its source',()=>{
    const e=editor([original()]);
    const before=e.stored()[0];
    evaluate(e.ctx,"canvasState.selectedId='original'");
    e.ctx.duplicateSelectedNodes();
    const nodes=e.stored();
    assert.equal(nodes.length,2);
    assert.deepEqual(nodes.find(n=>n.id==='original'),before);
    const copy=nodes.find(n=>n.id!=='original');
    assert.equal(copy.createdBy,'user');
    assert.equal(copy.agentNodeId,null);
    assert.equal(copy.userTouchedAt,null);
    assert.equal(copy.promptEditedByUser,false);
    assert.equal(copy.created_at,1234);
    assert.equal(copy.text,'keep');
});
