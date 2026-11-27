// Approved seam: real DOM handlers -> production serializer, pending journal and undo.
// Only DOM/layout, time and network are boundary fakes; no ownership or storage mocks.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';
import {declarations, identity, event, editor} from './smart-human-handlers-harness-392.mjs';

export {declarations, identity, event, editor};
function bodyClock(e){
    let now=1234,sequence=1;const timers=new Map();
    e.ctx.bodyTestNow=()=>now;evaluate(e.ctx,'Date.now=()=>bodyTestNow()');
    e.ctx.setTimeout=(fn,delay,...args)=>{const id=sequence++;timers.set(id,{at:now+delay,fn,args});return id;};
    e.ctx.clearTimeout=id=>timers.delete(id);
    e.ctx.fetch=async()=>({ok:true,json:async()=>({saved:true,revision:1})});
    return {advance(ms){
        const until=now+ms;
        for(;;){
            const due=[...timers].filter(([,timer])=>timer.at<=until).sort((a,b)=>a[1].at-b[1].at)[0];
            if(!due)break;
            now=due[1].at;timers.delete(due[0]);due[1].fn(...due[1].args);
        }
        now=until;
    }};
}
function bodyInput(e){
    const input=e.harness.makeElement('textarea');input.value=e.node().text;
    e.ctx.bindPromptNodeControls(e.form({'.prompt-node-text':[input]}),e.node());return input;
}
test('template group/library-only edits and cloud transient URLs never adopt canvas nodes',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'keep',promptPresetId:'shared'}]);const {ctx}=e;
    Object.assign(ctx,{promptPresets:[{id:'shared',text:'keep',category:'group'}],promptLibraries:[],activePromptLibraryId:'system',builtinPromptTemplates:[],promptTemplateGroups:[{id:'group',name:'Group'}],promptTemplateCategory:'group',promptTemplateOverrides:{editedBuiltins:{}},promptTemplateBody:null,promptTemplateCats:null,promptTemplatePanel:e.harness.makeElement(),ffDialog:{ask:async()=>true}});
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');vm.runInContext(source.slice(source.indexOf("promptTemplatePanel?.addEventListener('click'"),source.indexOf('if(promptPresetClose)')),ctx);
    const before=e.stored();ctx.promptTemplatePanel.dispatchEvent(event({closest:s=>s==='[data-template-cat-delete]'?{dataset:{templateCatDelete:'group'}}:null},{type:'click'}));
    await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(e.stored(),before);
    assert.equal(ctx.promptPresets[0].category,'mine');
    ctx.fetch=async()=>({ok:true,json:async()=>({url:'https://example.test/transient.png'})});
    await ctx.uploadMediaRefToCloud({url:'/local/source.png',kind:'image'});
    assert.deepEqual(e.stored(),before,'cloud upload cache does not change durable node settings');
});

test('a composer template panel cannot migrate its original target after the composer selection changes',()=>{
    const e=editor([{id:'a',type:'smart-image',...identity,promptDraftText:'original'},{id:'b',type:'smart-image',...identity,promptDraftText:'new selection'}]);const {ctx}=e;
    Object.assign(ctx,{promptPresets:[{id:'shared',text:'template'}],promptLibraries:[],activePromptLibraryId:'system',builtinPromptTemplates:[],promptTemplateSelectedId:'mine:shared',promptTemplatePanel:e.harness.makeElement(),composerTemplateBtn:null,renderInputThumbsRow(){}});
    ctx.promptTemplatePanel.dataset={target:'composer',nodeId:'a'};ctx.promptInput.childNodes=[];evaluate(ctx,"canvasState.lastComposerNodeId='b';canvasState.selectedId='b'");
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');vm.runInContext(source.slice(source.indexOf("promptTemplatePanel?.addEventListener('click'"),source.indexOf('if(promptPresetClose)')),ctx);
    const before=e.stored();ctx.promptTemplatePanel.dispatchEvent(event({closest:s=>s==='[data-template-apply]'?{dataset:{templateApply:'positive'}}:null},{type:'click'}));
    assert.deepEqual(e.stored(),before,'stale composer panel is rejected, not redirected to the newly active node');
});

test('a user-origin node retains identity and seals content+ownership together for production undo/redo',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,createdBy:'user',agentNodeId:null,text:'before'}]);const input=e.harness.makeElement();
    e.ctx.bindPromptNodeControls(e.form({'.prompt-node-text':[input]}),e.node());
    input.value='after';input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
    assert.equal(evaluate(e.ctx,'undoStack.length'),1,'actual text input and blur must establish their own undo step');
    const paths=fromContext(evaluate(e.ctx,'undoStack[0].actionDelta.changes')).map(change=>change.path.at(-1));
    assert.ok(paths.includes('userTouchedAt'));assert.ok(paths.includes('promptEditedByUser'));
    await e.ctx.performUndo();assert.equal(e.stored()[0].text,'before');assert.equal(e.stored()[0].userTouchedAt,null);assert.equal(e.stored()[0].promptEditedByUser,false);
    await e.ctx.performRedo();assert.equal(e.stored()[0].text,'after');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,true);
    assert.equal(e.stored()[0].createdBy,'user');assert.equal(e.stored()[0].agentNodeId,null);
});

test('continuous prompt body typing is one step and a 1000ms pause starts the next step',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'before',userTouchedAt:17,promptEditedByUser:true}]);
    const clock=bodyClock(e),input=bodyInput(e),before=e.stored();
    input.value='one';input.oninput(event(input));clock.advance(400);
    input.value='two';input.oninput(event(input));clock.advance(400);
    input.value='three';input.oninput(event(input));clock.advance(999);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0,'each keystroke must not be a separate step');
    clock.advance(1);assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    assert.equal(e.stored()[0].userTouchedAt,2034,'ending the session does not invent a later edit timestamp');
    input.value='next session';input.oninput(event(input));clock.advance(1000);
    assert.equal(evaluate(e.ctx,'undoStack.length'),2);
    await e.ctx.performUndo();assert.equal(e.stored()[0].text,'three');assert.equal(e.stored()[0].userTouchedAt,2034);
    await e.ctx.performUndo();assert.deepEqual(e.stored(),before);
    await e.ctx.performRedo();assert.equal(e.stored()[0].text,'three');assert.equal(e.stored()[0].userTouchedAt,2034);
    await e.ctx.performRedo();assert.equal(e.stored()[0].text,'next session');assert.equal(e.stored()[0].userTouchedAt,3034);
    assert.equal(e.stored()[0].createdBy,'agent');assert.equal(e.stored()[0].agentNodeId,'original-agent');
});

test('same-value and reverted prompt body sessions preserve prior T/P presence and create no step',()=>{
    for(const prior of [{userTouchedAt:null,promptEditedByUser:false},{userTouchedAt:17,promptEditedByUser:true},{}]){
        const source={id:'p',type:'smart-prompt',createdBy:'agent',agentNodeId:'original-agent',created_at:42,text:'before',...prior};
        const e=editor([source]),input=e.harness.makeElement('textarea');input.value='before';
        e.ctx.bindPromptNodeControls(e.form({'.prompt-node-text':[input]}),e.node());
        const before=e.stored();
        input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);assert.deepEqual(e.stored(),before);
        input.value='temporary';input.oninput(event(input));
        input.value='before';input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
        assert.deepEqual(e.stored(),before,'an empty session restores exact prior T/P, including missing fields');
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    }
});

test('reverting split prompt body does not leave unrelated persisted geometry',()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,userTouchedAt:1234,promptEditedByUser:true,text:'before',promptSplitEnabled:true}]);
    const input=e.harness.makeElement('textarea');input.value='before';
    e.ctx.bindPromptNodeControls(e.form({'.prompt-node-text':[input]}),e.node());
    const before=e.stored();
    input.value='temporary';input.oninput(event(input));
    input.value='before';input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
    assert.deepEqual(e.stored(),before,'a body-only empty session must not materialize unrelated w/h');
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
});

test('cancelled composition resumes the preceding edit pause without capturing the composing text',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'before'}]);
    const clock=bodyClock(e),input=bodyInput(e),before=e.stored();
    input.value='typed';input.oninput(event(input));clock.advance(300);
    input.dispatchEvent(event(input,{type:'compositionstart'}));
    input.value='typed-ni';input.oninput(event(input,{isComposing:true}));clock.advance(1100);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0,'composition must not end the open body step');
    assert.equal(e.stored()[0].text,'typed');
    input.value='typed';input.dispatchEvent(event(input,{type:'compositionend'}));
    clock.advance(999);assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    clock.advance(1);assert.equal(evaluate(e.ctx,'undoStack.length'),1,'cancelled IME must not leave the prior edit uncommitted forever');
    await e.ctx.performUndo();assert.deepEqual(e.stored(),before);
});

test('blur during prompt body composition waits for the final composition value',()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'before'}]),input=bodyInput(e);
    input.dispatchEvent(event(input,{type:'compositionstart'}));input.value='ni';input.oninput(event(input,{isComposing:true}));
    input.dispatchEvent(event(input,{type:'blur'}));assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    input.value='你';input.dispatchEvent(event(input,{type:'compositionend'}));
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);assert.equal(e.stored()[0].text,'你');
});

test('prompt body undo restores absent legacy T/P and empty sessions preserve a real redo branch',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',createdBy:'agent',agentNodeId:'original-agent',text:'before'}]);
    let input=bodyInput(e);const before=e.stored();
    input.value='after';input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
    await e.ctx.performUndo();assert.deepEqual(e.stored(),before);assert.equal(evaluate(e.ctx,'redoStack.length'),1);
    input=bodyInput(e);input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
    input.value='temporary';input.oninput(event(input));input.value='before';input.oninput(event(input));input.dispatchEvent(event(input,{type:'blur'}));
    assert.deepEqual(e.stored(),before);assert.equal(evaluate(e.ctx,'undoStack.length'),0);assert.equal(evaluate(e.ctx,'redoStack.length'),1);
    await e.ctx.performRedo();assert.equal(e.stored()[0].text,'after');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,true);
});

test('prompt body session undo preserves unrelated changes from the real server receiver',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'before'},{id:'other',type:'smart-prompt',...identity,text:'remote before'}]);
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    const server=fromContext(e.ctx.canvasForStorage()),input=bodyInput(e);
    input.value='local';input.oninput(event(input));
    server.revision=1;server.nodes[1].text='remote after';server.nodes.push({id:'new',type:'smart-prompt',...identity,text:'new remote'});
    e.ctx.applyMergedServerCanvas(server);
    input.value='local after receive';input.oninput(event(input));
    assert.equal(e.node('p').text,'local after receive','the retained body binding must edit the current receiver node');
    input.dispatchEvent(event(input,{type:'blur'}));
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);await e.ctx.performUndo();
    assert.equal(e.node('p').text,'before');assert.equal(e.node('p').userTouchedAt,null);
    assert.equal(e.node('other').text,'remote after');assert.equal(e.node('new').text,'new remote');
});

test('prompt body redo restores its own input rather than a later server text edit',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'before'}]);
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    const input=bodyInput(e);input.value='local';input.oninput(event(input));
    const accepted=fromContext(e.ctx.canvasForStorage());accepted.revision=1;
    e.ctx.fetch=async()=>({ok:true,status:200,json:async()=>({canvas:accepted,saved:true,revision:1})});
    await e.ctx.saveCanvas();
    const remote=structuredClone(accepted);remote.revision=2;remote.nodes[0].text='later agent text';
    e.ctx.applyMergedServerCanvas(remote);assert.equal(e.node().text,'later agent text');
    input.dispatchEvent(event(input,{type:'blur'}));await e.ctx.performUndo();
    // Existing scalar undo is an explicit write, not a new conflict policy.
    assert.equal(e.node().text,'before');assert.equal(e.node().userTouchedAt,null);assert.equal(e.node().promptEditedByUser,false);
    await e.ctx.performRedo();
    assert.equal(e.node().text,'local','redo must use the actual input after-value, not the later agent value at blur');
    assert.equal(e.node().userTouchedAt,1234);assert.equal(e.node().promptEditedByUser,true);
});

test('a late prompt body pause cannot create history in a different project',()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,created_at:42,text:'before'}]);
    const clock=bodyClock(e),input=bodyInput(e);input.value='typed';input.oninput(event(input));
    evaluate(e.ctx,"canvas={...canvas,id:'other-project',project_id:'other-project',nodes:[{id:'p',type:'smart-prompt',created_at:42,text:'other project'}]};canvasState.nodes=canvas.nodes");
    clock.advance(1000);assert.equal(evaluate(e.ctx,'undoStack.length'),0);assert.equal(e.node().text,'other project');
});

test('prompt body composition commits only the final value and creates its own undo on blur',async()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,created_at:42,text:'before'}]);
    const input=e.harness.makeElement('textarea');input.value='before';
    e.ctx.bindPromptNodeControls(e.form({'.prompt-node-text':[input]}),e.node());
    const before=e.stored();
    input.dispatchEvent(event(input,{type:'compositionstart'}));
    input.value='ni';input.oninput(event(input,{isComposing:true}));
    assert.deepEqual(e.stored(),before,'uncommitted composition must not write content or T/P');
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    input.value='你';input.dispatchEvent(event(input,{type:'compositionend'}));
    input.oninput(event(input,{isComposing:false}));
    input.dispatchEvent(event(input,{type:'blur'}));
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    assert.equal(e.stored()[0].text,'你');assert.equal(e.stored()[0].promptEditedByUser,true);
    await e.ctx.performUndo();assert.deepEqual(e.stored(),before);
    await e.ctx.performRedo();
    assert.equal(e.stored()[0].text,'你');assert.equal(e.stored()[0].userTouchedAt,1234);
    assert.equal(e.stored()[0].createdBy,'agent');assert.equal(e.stored()[0].agentNodeId,'original-agent');assert.equal(e.stored()[0].created_at,42);
});

test('prompt-node flow entry/exit are negative and flow typing reuses the durable human text handler',()=>{
    const e=editor([{id:'p',type:'smart-prompt',...identity,text:'before',w:316,h:200}]);const input=e.harness.makeElement('textarea');
    const el=e.form({'.prompt-node-text':[input]});el.style.setProperty=(key,value)=>{el.style[key]=value;};el.style.removeProperty=key=>{delete el.style[key];};
    e.ctx.world.querySelector=()=>el;Object.assign(e.ctx,{CSS:{escape:value=>value},DOMMatrix:class {a=1;d=1;},getComputedStyle:()=>({transform:'none'}),innerWidth:1000,innerHeight:800});
    e.ctx.bindPromptNodeControls(el,e.node());e.ctx.enterPromptNodeFlow('p');e.ctx.exitPromptNodeFlow();
    assert.equal(e.stored()[0].userTouchedAt,null);assert.equal(e.stored()[0].promptEditedByUser,false);
    e.ctx.enterPromptNodeFlow('p');input.value='flow typed';input.oninput(event(input));
    assert.equal(e.stored()[0].text,'flow typed');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,true);
    assert.equal(e.stored()[0].w,316);assert.equal(e.stored()[0].h,200,'flow geometry is not a persisted edit');
});

test('conversation collapse is durable T-only; a fresh manual send captures intent on the original pending request without claiming T',async()=>{
    const e=editor([{id:'conversation',type:'smart-conversation',...identity,messages:[],conversation_collapsed:false}]);
    const {ctx}=e;const input=e.harness.makeElement();input.value='manual message';const send=e.harness.makeElement();const collapse=e.harness.makeElement();
    ctx.bindConversationNodeControls(e.form({'[data-conversation-input]':[input],'[data-conversation-send]':[send],'[data-conversation-collapse]':[collapse]}),e.node());
    collapse.dispatchEvent(event(collapse,{type:'click'}));
    assert.equal(e.stored()[0].conversation_collapsed,true);assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    const fresh=editor([{id:'conversation',type:'smart-conversation',...identity,messages:[]}]);
    fresh.node().title='unsaved title';fresh.ctx.scheduleSave();
    fresh.ctx.fetch=async()=>{throw Error('offline boundary');};
    const freshInput=fresh.harness.makeElement();freshInput.value='manual message';const freshSend=fresh.harness.makeElement();
    fresh.ctx.bindConversationNodeControls(fresh.form({'[data-conversation-input]':[freshInput],'[data-conversation-send]':[freshSend]}),fresh.node());
    freshSend.dispatchEvent(event(freshSend,{type:'click'}));
    await new Promise(resolve=>setImmediate(resolve));
    const pending=fresh.stored()[0].pendingActionResumes?.[0];
    assert.equal(pending?.kind,'conversation',JSON.stringify({stored:fresh.stored(),toasts:fresh.harness.toasts}));assert.equal(pending?.payload.humanIntent,true);
    assert.equal(pending.payload.requestId,fresh.stored()[0].messages[0].request_id);
    assert.equal(fresh.stored()[0].userTouchedAt,null,'a local save is not server registration');
    const firstId=pending.payload.requestId;freshSend.dispatchEvent(event(freshSend,{type:'click'}));
    for(let i=0;i<5;i++) await Promise.resolve();
    assert.equal(fresh.stored()[0].pendingActionResumes.length,1);assert.equal(fresh.stored()[0].pendingActionResumes[0].payload.requestId,firstId);
    assert.deepEqual(pending.payload,{conversationProtocol:1,projectId:'unit-392',nodeId:'conversation',
        requestId:firstId,text:'manual message',humanIntent:true});
    assert.equal(fresh.stored()[0].messages.length,1);assert.equal(fresh.stored()[0].userTouchedAt,null);
    // This DOM test owns click -> immutable original intent. The protocol suite
    // exercises the real queue/persister/receiver on replay and formal ACK, rather
    // than calling the rebuilder with a storage record lacking its runtime nodeId.
    assert.deepEqual(fresh.stored()[0].pendingActionResumes[0].payload,pending.payload);
});

test('engine and API kind DOM switches stamp persisted mode snapshots before render without prompt protection',()=>{
    const e=editor([{id:'image',type:'smart-image',...identity,runSettings:{engine:'api',apiKind:'image'}}]);
    const {ctx}=e;evaluate(ctx,"canvasState.lastComposerNodeId='image';settings={engine:'api',apiKind:'image'}");
    const kind=e.harness.makeElement();kind.dataset.kind='video';ctx.apiKindToggle=e.form({'[data-kind]':[kind]});ctx.engineSelect=e.harness.makeElement();ctx.engineSelect.value='volcengine';ctx.dynamicParams=null;
    Object.assign(ctx,{RECENT_SMART_SETTINGS_KEY:'recent-test',modelSpecs:{},DEFAULT_VIDEO_MODELS:[]});
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');vm.runInContext(source.slice(source.indexOf('engineSelect.onchange ='),source.indexOf('let promptResizeState =')),ctx);
    ctx.engineSelect.onchange();assert.equal(e.stored()[0].runSettings.engine,'volcengine');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    e.node().userTouchedAt=null;kind.onclick(event(kind));assert.equal(e.stored()[0].runSettings.apiKind,'video');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
});

test('dynamic toggle, size and RunningHub inputs/dice are T-only explicit settings actions',()=>{
    for(const selector of ['[data-toggle-param]','[data-size-scope]','[data-rh-param]','[data-rh-random]','[data-rh-bool]','[data-rh-pick]']){
        const e=editor([{id:'image',type:'smart-image',...identity,runSettings:{engine:'api',resolution:'1k',videoMultimodal:false,rhConfigKey:'app:demo',rhParams:{},rhRandomActive:{}}}]);
        const {ctx}=e;evaluate(ctx,"canvasState.lastComposerNodeId='image';settings=cloneSmartSettings(canvasState.nodes[0].runSettings)");
        Object.assign(ctx,{RECENT_SMART_SETTINGS_KEY:'recent-test',modelSpecs:{},apiProviders:[{id:'runninghub',rh_apps:[{id:'demo',fields:[{nodeId:'seed',fieldName:'seed',fieldType:'NUMBER',random_enabled:true},{nodeId:'flag',fieldName:'flag',fieldType:'BOOLEAN'}]}]}]});
        ctx.providersFixture=ctx.apiProviders;evaluate(ctx,'apiProviders=providersFixture');
        const control=e.harness.makeElement();control.closest=()=>null;control.dataset={toggleParam:'videoMultimodal',sizeScope:'custom',rhParam:'seed::seed',rhRandom:'seed::seed',rhBool:'flag::flag',rhPick:'seed::seed',rhValue:'42'};control.value='12';
        ctx.dynamicParams=e.form({[selector]:[control]});ctx.bindDynamicParams();ctx.dynamicParams=null;
        (control.oninput||control.onclick)(event(control));
        assert.equal(e.stored()[0].userTouchedAt,1234,selector+' '+JSON.stringify({fields:ctx.rhActiveFields(),stored:e.stored()[0]}));assert.equal(e.stored()[0].promptEditedByUser,false,selector);
    }
});

test('manual video source+URL is atomic on cancel/invalid and stays bound to original live target after await',async()=>{
    const e=editor([{id:'image',type:'smart-image',...identity,runSettings:{engine:'api',videoTrustedSource:'library',videoTempShLinks:[],count:1}},{id:'other',type:'smart-image',...identity,runSettings:{count:9}}]);
    const {ctx}=e;evaluate(ctx,"canvasState.lastComposerNodeId='image';canvasState.selectedId='image';settings={engine:'api',videoTrustedSource:'library',videoTempShLinks:[],count:1}");
    Object.assign(ctx,{RECENT_SMART_SETTINGS_KEY:'recent-test',apiProviders:[],modelSpecs:{}});
    const ref=e.harness.makeElement();ref.dataset.url='/local/source.png';ctx.inputThumbsRow=e.form({'.input-thumb':[ref]});
    const manual=e.harness.makeElement();manual.dataset.trustedSource='manual';ctx.dynamicParams=e.form({'[data-trusted-source]':[manual]});ctx.bindDynamicParams();ctx.dynamicParams=null;
    const before=e.stored();ctx.openAssetNameDialog=async()=>null;
    await manual.onclick(event(manual));assert.deepEqual(e.stored(),before,'cancel leaves all persistent fields unchanged');
    ctx.openAssetNameDialog=async()=> 'not-a-url';await manual.onclick(event(manual));assert.deepEqual(e.stored(),before,'invalid URL');
    let release;ctx.openAssetNameDialog=()=>new Promise(resolve=>{release=resolve;});
    const pending=manual.onclick(event(manual));
    evaluate(ctx,"canvasState.nodes=canvasState.nodes.map(n=>({...n}));canvas.nodes=canvasState.nodes;canvasState.lastComposerNodeId='other';canvasState.selectedId='other';settings={count:9}");
    e.node('image').runSettings.count=5; // unrelated remote field while dialog is open
    release('https://example.test/video.mp4');await pending;
    assert.equal(e.stored()[0].runSettings.videoTrustedSource,'manual');assert.equal(e.stored()[0].runSettings.videoTempShLinks[0].url,'https://example.test/video.mp4');
    assert.equal(e.stored()[0].runSettings.count,5);assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    assert.deepEqual(e.stored()[1],before[1]);assert.equal(evaluate(ctx,'settings.count'),9);
    evaluate(ctx,"canvasState.lastComposerNodeId='image';canvasState.selectedId='image';settings=cloneSmartSettings(canvasState.nodes[0].runSettings)");
    e.node('image').userTouchedAt=222;
    ctx.openAssetNameDialog=async()=> 'https://example.test/video.mp4';await manual.onclick(event(manual));
    assert.equal(e.stored()[0].userTouchedAt,222,'same normalized URL/source');
    e.node('image').runSettings.videoTempShLinks.unshift({source:'/remote/ref.png',url:'https://example.test/cloud-v1.mp4',manual:false});
    evaluate(ctx,'settings=cloneSmartSettings(canvasState.nodes[0].runSettings)');
    ctx.openAssetNameDialog=()=>new Promise(resolve=>{release=resolve;});
    const unchanged=manual.onclick(event(manual));
    e.node('image').runSettings.videoTempShLinks[0].url='https://example.test/cloud-v2.mp4';
    release('https://example.test/video.mp4');await unchanged;
    assert.equal(e.stored()[0].userTouchedAt,222,'remote link change during a same-value confirmation is not a human edit');
    assert.equal(e.node('image').runSettings.videoTempShLinks.find(link=>link.source==='/remote/ref.png').url,'https://example.test/cloud-v2.mp4');
    ctx.openAssetNameDialog=()=>new Promise(resolve=>{release=resolve;});
    const revertRemote=manual.onclick(event(manual));
    e.node('image').runSettings.videoTempShLinks.find(link=>link.manual).url='https://example.test/remote-change.mp4';
    e.node('image').userTouchedAt=333;
    release('https://example.test/video.mp4');await revertRemote;
    assert.equal(e.stored()[0].runSettings.videoTempShLinks[0].url,'https://example.test/video.mp4');
    assert.equal(e.stored()[0].userTouchedAt,1234,'confirm compares the live baseline, not the pre-dialog value');
    ctx.openAssetNameDialog=()=>new Promise(resolve=>{release=resolve;});const removed=manual.onclick(event(manual));
    evaluate(ctx,"canvasState.nodes=canvasState.nodes.filter(n=>n.id!=='image');canvas.nodes=canvasState.nodes");release('https://example.test/late.mp4');await removed;
    assert.deepEqual(e.stored(),[before[1]],'deleted target is not resurrected or migrated');
});

test('provider picker compares settled image/video model values before its first real parameter render',()=>{
    for(const apiKind of ['image','video']){
        const key=apiKind==='video'?'videoProvider':'provider_id',modelKey=apiKind==='video'?'videoModel':'model';
        const e=editor([{id:'image',type:'smart-image',...identity,runSettings:{engine:'api',apiKind,[key]:'p',[modelKey]:'m1',routingMode:'manual',videoRoutingMode:'manual',resolution:'1k',ratio:'square'}}]);
        const {ctx}=e;
        const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
        for(const name of ['IMAGE_RATIO_PRESETS','IMAGE_RATIO_PRESET_KEYS_BY_RATIO']){
            const declaration=source.match(new RegExp(`^const ${name} = [\\s\\S]*?;`,'m'))?.[0];
            assert.ok(declaration,name);vm.runInContext(declaration,ctx);
        }
        Object.assign(ctx,{engineSelect:e.harness.makeElement(),apiKindToggle:e.harness.makeElement(),RECENT_SMART_SETTINGS_KEY:'recent-test'});
        evaluate(ctx,"apiProviders=[{id:'p',name:'P',image_models:['m1','m2'],video_models:['m1','m2']},{id:'q',name:'Q',image_models:['q1'],video_models:['q1']}];canvasState.lastComposerNodeId='image';canvasState.selectedId='image';settings=cloneSmartSettings(canvasState.nodes[0].runSettings)");
        const picker=e.harness.makeElement();picker.dataset.smartParam=key;picker.dataset.smartValue='p';
        ctx.dynamicParams=e.form({'[data-smart-param]':[picker]});
        const publications=[];let markup='';
        Object.defineProperty(ctx.dynamicParams,'innerHTML',{get:()=>markup,set:value=>{markup=value;publications.push(e.stored());}});
        evaluate(ctx,'normalizeSmartVideoModeSettings(canvasState.nodes[0].runSettings);normalizeSmartVideoModeSettings(settings)');
        ctx.renderDynamicParams();const before=e.stored();publications.length=0;
        picker.onclick(event(picker));
        assert.deepEqual(e.stored(),before,`${apiKind}: same provider and settled first model is not an edit`);
        assert.ok(publications.length,'production parameter renderer really ran');
        evaluate(ctx,`settings.${modelKey}='m2';canvasState.nodes[0].runSettings.${modelKey}='m2'`);publications.length=0;
        picker.onclick(event(picker));
        assert.equal(e.stored()[0].runSettings[modelKey],'m1','reselect still resets a nondefault model, preserving existing behavior');
        assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
        assert.equal(publications[0][0].userTouchedAt,1234,'the first renderer publication already sees the ownership stamp');
        picker.dataset.smartValue='q';e.node().userTouchedAt=null;publications.length=0;picker.onclick(event(picker));
        assert.equal(e.stored()[0].runSettings[key],'q');assert.equal(e.stored()[0].runSettings[modelKey],'q1');
        assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(publications[0][0].userTouchedAt,1234);
    }
});

test('dynamic parameter DOM changes compare normalized durable settings only; automatic, same, running and readonly are negative',()=>{
    const e=editor([{id:'image',type:'smart-image',...identity,runSettings:{engine:'api',count:2,model:'old',routingMode:'manual'}}]);
    const {ctx}=e;evaluate(ctx,"canvasState.lastComposerNodeId='image';settings={engine:'api',count:2,model:'old',routingMode:'manual'}");
    Object.assign(ctx,{RECENT_SMART_SETTINGS_KEY:'recent-test',apiProviders:[],modelSpecs:{}});
    const input=e.harness.makeElement('input');input.dataset.param='count';input.value='2';input.closest=()=>null;
    ctx.dynamicParams=e.form({'[data-param]':[input]});ctx.bindDynamicParams();ctx.dynamicParams=null;
    input.oninput(event(input));assert.equal(e.stored()[0].userTouchedAt,null,'numeric same value');
    input.value='3';input.oninput(event(input));assert.equal(e.stored()[0].runSettings.count,3);assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    e.node().userTouchedAt=null;ctx.setDynamicSetting('count',4);assert.equal(e.stored()[0].userTouchedAt,null,'automatic invocation');
    e.node().running=true;input.value='5';input.oninput(event(input));assert.equal(e.stored()[0].runSettings.count,4);assert.equal(e.stored()[0].userTouchedAt,null);
    e.node().running=false;input.readOnly=true;input.oninput(event(input));assert.equal(e.stored()[0].runSettings.count,4);
    input.readOnly=false;input.dataset.param='model';input.value='new-model';input.oninput(event(input));
    assert.equal(e.stored()[0].runSettings.model,'new-model');assert.equal(e.stored()[0].promptEditedByUser,false);
    e.node().promptEditedByUser=true;input.value='another-model';input.oninput(event(input));assert.equal(e.stored()[0].promptEditedByUser,true,'ordinary parameters never clear prior protection');
});

test('local-edit pointercancel discards draft; prompt and persisted active role are human edits, default fills are not',()=>{
    const e=editor([
        {id:'local',type:'local-edit',...identity,mode:'modify',prompt:'before',selection:[0,0,20,20],localEditActiveRole:'original',source_media_id:'original'},
        {id:'source',type:'smart-image',images:[{asset_id:'original',kind:'image'}]},
    ]);
    evaluate(e.ctx,"canvas.connections=[{from:'source',to:'local',kind:'input',role:'original'}]");
    const canvas=e.harness.makeElement('canvas');canvas.getContext=()=>null;canvas.getBoundingClientRect=()=>({left:0,top:0,width:100,height:100});
    const preview=e.harness.makeElement('img');preview.naturalWidth=100;preview.naturalHeight=100;preview.closest=()=>null;
    const prompt=e.harness.makeElement();prompt.value='before';const role=e.harness.makeElement();role.dataset.localEditConnectRole='reference';
    e.ctx.bindLocalEditNodeControls(e.form({'[data-local-edit-selection-canvas]':[canvas],'[data-local-edit-preview]':[preview],'[data-local-edit-role="prompt"]':[prompt],'[data-local-edit-connect-role]':[role]}),e.node());
    assert.equal(e.stored()[0].userTouchedAt,null,'initial sync is nonhuman');
    const pointer=(type,x,y)=>canvas.dispatchEvent(event(canvas,{type,button:0,pointerId:1,clientX:x,clientY:y}));
    pointer('pointerdown',10,10);pointer('pointermove',70,70);pointer('pointercancel',70,70);
    assert.deepEqual(e.stored()[0].selection,[0,0,20,20]);assert.equal(e.stored()[0].userTouchedAt,null);
    pointer('pointerdown',10,10);pointer('pointermove',70,70);pointer('pointerup',70,70);
    assert.deepEqual(e.stored()[0].selection,[10,10,70,70]);assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    e.node().userTouchedAt=777;role.dispatchEvent(event(role,{type:'click'}));assert.equal(e.stored()[0].localEditActiveRole,'reference');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    prompt.value='changed';prompt.dispatchEvent(event(prompt,{type:'input'}));
    assert.equal(e.stored()[0].prompt,'changed');assert.equal(e.stored()[0].promptEditedByUser,true);
    e.node().userTouchedAt=777;prompt.readOnly=true;prompt.value='readonly synthetic input';prompt.dispatchEvent(event(prompt,{type:'input'}));
    assert.equal(e.stored()[0].prompt,'changed');assert.equal(e.stored()[0].userTouchedAt,777);
});

test('template apply and legacy preset buttons mark content vs ID binding before first render, automatic apply stays nonhuman',()=>{
    const e=editor([{id:'a',type:'smart-prompt',...identity,text:'same'},{id:'b',type:'smart-prompt',...identity,text:'other',promptPresetId:'shared'}]);
    const {ctx}=e;
    Object.assign(ctx,{promptPresets:[{id:'shared',name:'Shared',text:'same'}],promptLibraries:[],activePromptLibraryId:'system',builtinPromptTemplates:[],promptTemplateSelectedId:'mine:shared',promptTemplatePanel:e.harness.makeElement(),promptPresetPanel:e.harness.makeElement(),promptPresetStatus:null,promptPresetDeleteArmed:false,composerTemplateBtn:null});
    ctx.promptTemplatePanel.dataset.nodeId='a';ctx.promptPresetPanel.dataset.nodeId='a';
    for(const name of ['Apply','Save','New','Delete','Name','Text','Select']) ctx['promptPreset'+name]=e.harness.makeElement();
    ctx.promptPresetSelect.value='shared';ctx.promptPresetName.select=()=>{};
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
    vm.runInContext(source.slice(source.indexOf("promptTemplatePanel?.addEventListener('click'"),source.indexOf('if(promptPresetClose)'))+'\n'+source.slice(source.indexOf('if(promptPresetApply)'),source.indexOf('if(assetLibrarySelect)',source.indexOf('if(promptPresetApply)'))),ctx);
    ctx.promptTemplatePanel.dispatchEvent(event({closest:s=>s==='[data-template-apply]'?{dataset:{templateApply:'positive'}}:null},{type:'click'}));
    assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false,'ID-only apply');
    e.node().userTouchedAt=null;ctx.promptPresetText.value='changed';ctx.promptPresetName.value='Shared';ctx.promptPresetSave.onclick();
    assert.equal(e.stored()[0].text,'changed');assert.equal(e.stored()[0].promptEditedByUser,true);
    e.node().promptEditedByUser=false;e.node().userTouchedAt=null;
    ctx.promptPresetDelete.onclick();assert.equal(e.stored()[0].userTouchedAt,null,'armed but not deleted');
    ctx.promptPresetDelete.onclick();assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[1].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    e.node().userTouchedAt=null;ctx.promptPresetNew.onclick();assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    e.node().userTouchedAt=null;e.node().text='automatically reset';ctx.promptTemplateSelectedId='mine:'+e.node().promptPresetId;ctx.applyPromptTemplateToNode();
    assert.equal(e.stored()[0].userTouchedAt,null);
});

test('template panel save fanout and delete touch only changed bound nodes, with preset IDs T-only',async()=>{
    const e=editor([{id:'a',type:'smart-prompt',...identity,text:'old',promptPresetId:'shared'},{id:'b',type:'smart-prompt',...identity,text:'new',promptPresetId:'shared'},{id:'c',type:'smart-prompt',...identity,text:'other'}]);
    const {ctx}=e; const name=e.harness.makeElement();name.value='Shared';const text=e.harness.makeElement();text.value='new';
    Object.assign(ctx,{promptPresets:[{id:'shared',name:'Shared',text:'old'}],promptLibraries:[],activePromptLibraryId:'system',builtinPromptTemplates:[],promptTemplateGroups:[],promptTemplateSelectedId:'mine:shared',promptTemplateBody:null,promptTemplateCats:null,composerTemplateBtn:null,
        promptTemplatePanel:e.form({'[data-template-edit-name]':[name],'[data-template-edit-text]':[text]})});
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
    vm.runInContext(source.slice(source.indexOf("promptTemplatePanel?.addEventListener('click'"),source.indexOf('if(promptPresetClose)')),ctx);
    const click=selector=>ctx.promptTemplatePanel.dispatchEvent(event({closest:s=>s===selector?{}:null},{type:'click'}));
    click('[data-template-edit-save]');await Promise.resolve();
    assert.equal(e.stored()[0].text,'new');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,true);
    assert.equal(e.stored()[1].userTouchedAt,null,'same-value sibling');assert.equal(e.stored()[2].userTouchedAt,null,'unbound sibling');
    e.node('a').userTouchedAt=null;e.node('a').promptEditedByUser=false;
    click('[data-template-delete]');await Promise.resolve();
    assert.equal(e.stored()[0].promptPresetId,'');assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    assert.equal(e.stored()[1].userTouchedAt,1234);assert.equal(e.stored()[1].promptEditedByUser,false);
});

test('loop number clamping is T-only; showPrompt protects only when it adds prompt structure',()=>{
    const e=editor([{id:'loop',type:'smart-loop',...identity,count:100,mode:'serial',showPrompt:false,variablePrompts:['body'],variablePrompt:'body'}]);
    const number=e.harness.makeElement();number.dataset.loopNumberInput='count';number.value='999';
    const toggle=e.harness.makeElement();toggle.dataset.loopToggle='prompt';
    e.ctx.bindLoopNodeControls(e.form({'[data-loop-number-input]':[number],'[data-loop-toggle]':[toggle]}),e.node());
    number.oninput(event(number));assert.equal(e.stored()[0].userTouchedAt,null,'clamped same value');
    number.value='3';number.oninput(event(number));
    assert.equal(e.stored()[0].count,3);assert.equal(e.stored()[0].userTouchedAt,1234);assert.equal(e.stored()[0].promptEditedByUser,false);
    toggle.onclick(event(toggle));assert.equal(e.stored()[0].promptEditedByUser,false,'visibility alone is not prompt structure');
    e.node().showPrompt=false;e.node().variablePrompts=[''];e.node().variablePrompt='';
    toggle.onclick(event(toggle));assert.equal(e.stored()[0].promptEditedByUser,true,'default text inserted by explicit show action');
});

test('loop add/delete blank fields and Range paste preserve durable prompt structure; defaults remain automatic',()=>{
    const e=editor([{id:'loop',type:'smart-loop',...identity,variablePrompts:[''],variablePrompt:''}]);
    const input=e.harness.makeElement();input.dataset.loopPromptIndex='0';input.nodeType=1;input.childNodes=[];
    const add=e.harness.makeElement(); const remove=e.harness.makeElement();remove.dataset.loopPromptDelete='1';
    const token=e.harness.makeElement();token.dataset.loopToken='《计数》';
    const selectors={'.loop-smart-text':[input],'[data-loop-prompt-index]':[input],'[data-loop-prompt-add]':[add],'[data-loop-prompt-delete]':[remove],'[data-loop-token]':[token]};
    const el=e.form(selectors);
    e.ctx.bindLoopNodeControls(el,e.node());
    e.ctx.setSmartLoopPromptFieldValues(e.node(),['']);
    assert.equal(e.stored()[0].userTouchedAt,null);
    add.onclick(event(add));
    assert.deepEqual(e.stored()[0].variablePrompts,['','']);
    assert.equal(e.stored()[0].userTouchedAt,1234);
    assert.equal(e.stored()[0].promptEditedByUser,true);
    const second=e.harness.makeElement();second.childNodes=[];second.dataset.loopPromptIndex='1';selectors['[data-loop-prompt-index]']=[input,second];
    remove.onclick(event(remove));
    assert.deepEqual(e.stored()[0].variablePrompts,['']);selectors['[data-loop-prompt-index]']=[input];
    input.textContent='';
    const range={deleteContents(){},insertNode(n){input.textContent=n.textContent;input.childNodes=[n];},collapse(){}};
    e.ctx.Node={TEXT_NODE:3,ELEMENT_NODE:1};
    e.ctx.getSelection=()=>({rangeCount:1,getRangeAt:()=>range,removeAllRanges(){},addRange(){}});
    e.ctx.document.createTextNode=text=>({nodeType:3,textContent:text,nodeValue:text});
    input.dispatchEvent(event(input,{type:'paste',clipboardData:{getData:()=> 'range-pasted'}}));
    assert.deepEqual(e.stored()[0].variablePrompts,['range-pasted']);
    assert.equal(e.stored()[0].variablePrompt,'range-pasted');
    const chip=e.harness.makeElement();chip.nodeType=1;chip.dataset.token='《计数》';chip.classList.add('loop-smart-token-chip');
    e.ctx.document.createElement=()=>({innerHTML:'',firstElementChild:chip});input.contains=()=>false;input.appendChild=node=>input.childNodes.push(node);
    token.onclick(event(token));assert.equal(e.stored()[0].variablePrompt,'range-pasted《计数》');
});

test('composer input, paste and mention token submit only the original composer draft, not incidental run settings',()=>{
    const e=editor([{id:'image',type:'smart-image',...identity,promptDraftHtml:'before',promptDraftText:'before',runSettings:{count:1}},{id:'other',type:'smart-image',...identity}]);
    const {ctx}=e;
    evaluate(ctx,"canvasState.lastComposerNodeId='image';canvasState.selectedId='other';settings.count=7");
    let body='before';
    Object.defineProperty(ctx.promptInput,'innerHTML',{get:()=>body,set:v=>{body=v;}});
    Object.defineProperty(ctx.promptInput,'childNodes',{get:()=>[{nodeType:3,textContent:body}]});
    ctx.Node={TEXT_NODE:3,ELEMENT_NODE:1};ctx.NodeFilter={SHOW_TEXT:4};
    const range={deleteContents(){},insertNode(node){body+=node.textContent||'<span>reference</span>';node.after=()=>{};},collapse(){},setStartAfter(){}};
    ctx.getSelection=()=>({rangeCount:1,getRangeAt:()=>range,removeAllRanges(){},addRange(){}});
    ctx.document.createTextNode=text=>({nodeType:3,textContent:text});
    ctx.document.createTreeWalker=()=>({nextNode:()=>false});
    Object.assign(ctx,{promptCompositionActive:true,mentionRange:null,maybeOpenMentionPicker(){},closeMentionPicker(){},renderInputThumbsRow(){},syncRunButtonState(){}});
    const source=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
    vm.runInContext(source.slice(source.indexOf("promptInput.addEventListener('paste'"),source.indexOf("promptInput.addEventListener('compositionstart'")),ctx);
    ctx.promptInput.dispatchEvent(new ctx.Event('input'));
    assert.equal(e.node().userTouchedAt,null,'same content despite incidental settings assignment');
    body='typed';ctx.promptInput.dispatchEvent(new ctx.Event('input'));
    assert.equal(e.stored()[0].promptDraftText,'typed');
    assert.equal(e.stored()[0].userTouchedAt,1234);
    assert.equal(e.stored()[0].promptEditedByUser,true);
    assert.equal(e.stored()[1].userTouchedAt,null,'selection must not redirect composer edits');
    ctx.promptInput.dispatchEvent(event(ctx.promptInput,{type:'paste',clipboardData:{getData:()=>'-pasted'}}));
    assert.equal(e.stored()[0].promptDraftText,'typed-pasted');
    range.commonAncestorContainer=ctx.promptInput;range.cloneRange=()=>range;ctx.mentionRange=range;
    ctx.insertInputReferenceMarker(1);
    assert.equal(e.stored()[0].promptDraftText,'typed-pasted@1','existing marker input event submits once');
    ctx.mentionRange=null;
    const mention=e.harness.makeElement();mention.dataset.mentionIndex='0';ctx.mentionPicker=e.form({'[data-mention-index]':[mention]});
    ctx.mentionPicker._items=[{url:'https://example.test/image.png',name:'reference',kind:'image'}];ctx.mentionInsertMode='token';
    const mentionStart=source.indexOf("    mentionPicker.querySelectorAll('[data-mention-index]')");
    vm.runInContext(source.slice(mentionStart,source.indexOf('    refreshIcons();',mentionStart)),ctx);
    mention.dispatchEvent(event(mention,{type:'mousedown'}));
    assert.match(e.stored()[0].promptDraftText,/reference/,'mention DOM handler explicitly submits input');
    e.node().userTouchedAt=null;e.node().promptEditedByUser=false;
    ctx.insertMentionToken({url:'https://example.test/automatic.png',name:'automatic',kind:'image'});
    assert.equal(e.stored()[0].userTouchedAt,null,'non-handler mention helper calls are not human actions');
    body='programmatically loaded';ctx.savePromptDraftForCurrent();
    assert.equal(e.stored()[0].userTouchedAt,null,'shared saver defaults to nonhuman');
});

test('prompt structure buttons protect content before rendering while LLM model parameters remain T-only',()=>{
    for(const [selector,field,before,after,prompt] of [
        ['.prompt-split-toggle','promptSplitEnabled',false,true,true],
        ['.prompt-system-toggle','llmSystemEnabled',false,true,true],
        ['.prompt-llm-model','llmModel','old','new',false],
        ['.prompt-llm-provider','llmProvider','old','new',false],
        ['.prompt-llm-toggle','llmEnabled',false,true,false],
        ['.prompt-node-separator','promptSeparator',';','|',true],
    ]){
        const e=editor([{id:'p',type:'smart-prompt',...identity,text:'body',h:400,[field]:before}]);
        const input=e.harness.makeElement(); input.value=after;
        e.ctx.bindPromptNodeControls(e.form({[selector]:[input]}),e.node());
        (input.onchange||input.oninput||input.onclick)(event(input));
        assert.equal(e.stored()[0][field],after);
        assert.equal(e.stored()[0].userTouchedAt,1234);
        assert.equal(e.stored()[0].promptEditedByUser,prompt);
        if(e.renders.length) assert.equal(e.renders[0].nodes[0].userTouchedAt,1234);
    }
});

test('prompt text/system/instruction DOM inputs protect only actual persisted content changes',()=>{
    for(const [selector,field] of [['.prompt-node-text','text'],['.prompt-llm-system','llmSystemPrompt'],['.prompt-llm-instruction','llmInstruction']]){
        const e=editor([{id:'p',type:'smart-prompt',...identity,[field]:'before'}]);
        const input=e.harness.makeElement('textarea'); const el=e.form({[selector]:[input]});
        e.ctx.bindPromptNodeControls(el,e.node());
        assert.equal(e.stored()[0].userTouchedAt,null,'binding/loading is automatic');
        input.value='before';input.oninput(event(input));
        assert.equal(e.stored()[0].userTouchedAt,null,'same value');
        input.value='';input.oninput(event(input));
        assert.equal(e.stored()[0][field],'');
        assert.equal(e.stored()[0].userTouchedAt,1234);
        assert.equal(e.stored()[0].promptEditedByUser,true);
        assert.equal(e.stored()[0].agentNodeId,'original-agent');
        assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),true);
    }
});
