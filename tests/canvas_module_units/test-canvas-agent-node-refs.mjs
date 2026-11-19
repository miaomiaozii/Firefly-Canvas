// Public TUI actions against real classic-script module; mock only DOM/network/time boundaries.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../static/js/canvas-agent-tui.js', import.meta.url), 'utf8');
function fixture() {
    const requests = [], timers = new Map(), events = new EventTarget();
    class CustomEvent extends Event {
        constructor(type, options={}) { super(type); this.detail=options.detail; }
    }
    const document = {querySelector:()=>null, querySelectorAll:()=>[], getElementById:()=>null, activeElement:null, title:'Test'};
    const canvasState = {nodes:[
        {id:'a',type:'smart-agent',title:'Agent',dock:'right'},
        {id:'b',type:'smart-agent',title:'Other agent'},
        {id:'i',type:'smart-image',title:'Original'},
        {id:'p',type:'smart-prompt',title:'Prompt',text:'private prompt'},
    ],selectedIds:['i'],selectedId:''};
    const context = vm.createContext({console,document,canvasState,canvasId:'project',
        NODE_TYPE_AGENT:'smart-agent',NODE_TYPE_TASK:'agent-task',NODE_TYPE_IMAGE:'smart-image',
        NODE_TYPE_PROMPT:'smart-prompt',NODE_TYPE_GROUP:'smart-group',NODE_TYPE_CONVERSATION:'smart-conversation',
        WebSocket:{OPEN:1}, window:events, Event, CustomEvent, locate:()=>{}, tr:key=>key,
        setTimeout:fn=>{const id=timers.size+1;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
        fetch:async(url,options)=>{const body=JSON.parse(options.body);requests.push({url,body});return {ok:true,json:async()=>({success:true,request_id:body.request_id,input_type:url.split('/').pop(),node_refs:(body.refs||[]).map(r=>({node_id:r.node_id,type:'smart-image',title:'Canonical'}))})};}
    });
    context.window.canvasState=canvasState;
    vm.runInContext(source,context,{filename:'canvas-agent-tui.js'});
    return {context,canvasState,requests,timers,run:code=>vm.runInContext(code,context)};
}
test('attachments travel with prompt and follow-up and replay retains file names', async()=>{
    for (const busy of [false,true]) {
        const f=fixture();
        f.context.input={value:'read files',style:{}};
        f.run(`getAgentTuiState('a').pendingImages=[{name:'plan.xlsx',attachmentId:'file-id'}];getAgentTuiState('a').status='${busy?'busy':'idle'}';agentTuiSendInput(input,'a','project')`);
        await new Promise(resolve=>setImmediate(resolve));
        assert.deepEqual(f.requests[0].body.attachments,['file-id']);
        assert.equal(f.requests[0].url.split('/').pop(),busy?'follow_up':'prompt');
        const requestId=f.requests[0].body.request_id;
        f.context.replay={type:busy?'queued_input':'user_prompt',text:'read files',request_id:requestId,input_type:busy?'follow_up':'prompt',node_refs:[],attachments:[{name:'plan.xlsx'}]};
        f.run("getAgentTuiState('a').messages=[];agentNodeRefsApplyEvent('a',replay)");
        assert.equal(f.run("getAgentTuiState('a').messages[0].attachments[0]"),'plan.xlsx');
    }
});
test('selected canvas references travel with this prompt and do not leak to the next', async()=>{
    const f=fixture();
    f.run("syncAgentInputNodeRefs('a')");
    await f.run("sendAgentPrompt('a','project','inspect')");
    assert.deepEqual(f.requests[0].body.refs,[{node_id:'i'}]);
    assert.equal(f.run("getAgentTuiState('a').messages[0].node_refs[0].title"),'Canonical');
    f.run("syncAgentInputNodeRefs('a')");
    await f.run("sendAgentPrompt('a','project','no implicit references')");
    assert.equal('refs' in f.requests[1].body,false);
    assert.equal(JSON.stringify(f.canvasState.nodes).includes('nodeRefs'),false);
});
test('WebSocket send is pending until the matching server receipt, including steer and follow_up',()=>{
    for(const [send,kind] of [['sendAgentPrompt','prompt'],['sendAgentSteer','steer'],['sendAgentFollowUp','follow_up']]) {
        const f=fixture(), sent=[];
        f.context.wire=payload=>sent.push(JSON.parse(payload));
        f.run("getAgentTuiState('a').socket={readyState:1,send:wire};syncAgentInputNodeRefs('a')");
        f.run(`${send}('a','project','input')`);
        assert.equal(sent[0].type,kind);
        assert.deepEqual(sent[0].refs,[{node_id:'i'}]);
        assert.equal(f.run("getAgentTuiState('a').messages[0].delivery"),'pending');
        f.context.receipt={type:'input_accepted',input_type:kind,request_id:'wrong',node_refs:[]};
        f.run("applyAgentEventBody('a',receipt)");
        assert.equal(f.run("getAgentTuiState('a').messages[0].delivery"),'pending');
        f.context.receipt={type:'input_accepted',input_type:kind,request_id:sent[0].request_id,node_refs:[{node_id:'i',type:'smart-image',title:'Server title'}]};
        f.run("applyAgentEventBody('a',receipt)");
        assert.equal(f.run("getAgentTuiState('a').messages[0].delivery"),'accepted');
        assert.equal(f.run("getAgentTuiState('a').messages[0].node_refs[0].title"),'Server title');
        assert.equal(f.timers.size,0);
    }
});
test('rejection restores the failed draft and references, but never overwrites a newer draft',async()=>{
    for(const newer of [false,true]) {
        const f=fixture();let reject;
        f.context.fetch=()=>new Promise((_resolve,r)=>{reject=r;});
        f.run("syncAgentInputNodeRefs('a')");
        f.context.input={value:'  failed draft  ',style:{}};
        f.run("agentTuiSendInput(input,'a','project')");
        if(newer) f.run("getAgentTuiState('a').draft='newer draft';canvasState.selectedIds=['p'];syncAgentInputNodeRefs('a')");
        reject(new Error('offline'));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.run("getAgentTuiState('a').draft"),newer?'newer draft':'  failed draft  ');
        f.context.fetch=async(url,options)=>{const body=JSON.parse(options.body);f.requests.push(body);return {ok:true,json:async()=>({request_id:body.request_id,input_type:'prompt',node_refs:[]})};};
        await f.run("sendAgentPrompt('a','project','retry')");
        assert.deepEqual(f.requests[0].refs,[{node_id:newer?'p':'i'}]);
    }
});
test('references are deduplicated per Agent and never mutate a saved canvas node',async()=>{
    const f=fixture();
    f.canvasState.selectedIds=['i','p','i','a'];
    f.run("syncAgentInputNodeRefs('a')");
    f.canvasState.selectedIds=['p'];
    f.run("syncAgentInputNodeRefs('b')");
    await f.run("sendAgentPrompt('a','project','first')");
    await f.run("sendAgentPrompt('b','project','second')");
    assert.deepEqual(f.requests[0].body.refs,[{node_id:'i'},{node_id:'p'}]);
    assert.deepEqual(f.requests[1].body.refs,[{node_id:'p'}]);
    assert.equal(f.canvasState.nodes[0].nodeRefsDraft,undefined);
});
test('timeout does not restore a possibly-delivered draft and late REST or WS receipts reconcile it',async()=>{
    for(const transport of ['rest','ws']) {
        const f=fixture(), sent=[];let respond;
        if(transport==='ws') {
            f.context.wire=payload=>sent.push(JSON.parse(payload));
            f.run("getAgentTuiState('a').socket={readyState:1,send:wire}");
        } else f.context.fetch=(_url,options)=>{sent.push(JSON.parse(options.body));return new Promise(resolve=>{respond=resolve;});};
        f.run("syncAgentInputNodeRefs('a')");
        f.context.input={value:'possibly delivered',style:{}};
        f.run("agentTuiSendInput(input,'a','project')");
        [...f.timers.values()][0]();
        assert.equal(f.run("getAgentTuiState('a').messages[0].delivery"),'unconfirmed');
        assert.equal(f.run("getAgentTuiState('a').draft"),'');
        const receipt={request_id:sent[0].request_id,input_type:'prompt',node_refs:[{node_id:'i',title:'Late canonical'}]};
        if(transport==='ws') {f.context.receipt={type:'input_accepted',...receipt};f.run("applyAgentEventBody('a',receipt)");}
        else {respond({ok:true,json:async()=>receipt});await new Promise(resolve=>setImmediate(resolve));}
        assert.equal(f.run("getAgentTuiState('a').messages[0].delivery"),'accepted');
        assert.equal(f.run("getAgentTuiState('a').messages[0].node_refs[0].title"),'Late canonical');
        assert.equal(f.run("getAgentTuiState('a').nodeRefsDraft.error"),'');
        assert.equal(f.run("getAgentTuiState('a').nodeRefsDraft.pending.size"),0);
    }
});
test('rejection cannot combine failed text or refs with a newer attachment-only draft',async()=>{
    const f=fixture();let reject;
    f.context.fetch=()=>new Promise((_resolve,r)=>{reject=r;});
    f.run("syncAgentInputNodeRefs('a')");
    f.context.input={value:'old text',style:{}};
    f.run("getAgentTuiState('a').pendingImages=[{name:'old.png',dataUrl:'data:image/png;base64,OLD'}];agentTuiSendInput(input,'a','project')");
    f.run("getAgentTuiState('a').pendingImages=[{name:'new.png',dataUrl:'data:image/png;base64,NEW'}]");
    reject(new Error('offline'));await new Promise(resolve=>setImmediate(resolve));
    assert.equal(f.run("getAgentTuiState('a').draft"),'');
    assert.equal(f.run("getAgentTuiState('a').pendingImages[0].name"),'new.png');
    assert.equal(f.run("agentNodeRefsIds(getAgentTuiState('a')).length"),0);
});
test('all rejected overlapping inputs release optimistic busy status regardless of receipt order',()=>{
    for(const order of [[0,1],[1,0]]) {
        const f=fixture(),sent=[];
        f.context.wire=payload=>sent.push(JSON.parse(payload));
        f.context.input={value:'first invalid input',style:{}};
        f.run("getAgentTuiState('a').socket={readyState:1,send:wire};agentTuiSendInput(input,'a','project')");
        f.context.input.value='second invalid input';
        f.run("agentTuiSendInput(input,'a','project')");
        assert.deepEqual(sent.map(row=>row.type),['prompt','follow_up']);
        for(const index of order) {
            f.context.receipt={type:'input_rejected',request_id:sent[index].request_id,input_type:sent[index].type,message:'Invalid reference'};
            f.run("applyAgentEventBody('a',receipt)");
        }
        f.context.input.value='retry after all rejections';
        f.run("agentTuiSendInput(input,'a','project')");
        assert.equal(sent[2].type,'prompt');
    }
});
test('failed input rollback cannot undo an accepted prompt or a real newer Agent turn',()=>{
    for(const protection of ['accepted prompt','new turn']) {
        const f=fixture(),sent=[];
        f.context.wire=payload=>sent.push(JSON.parse(payload));
        f.context.input={value:'first input',style:{}};
        f.run("getAgentTuiState('a').socket={readyState:1,send:wire};agentTuiSendInput(input,'a','project')");
        f.context.input.value='second input';
        f.run("agentTuiSendInput(input,'a','project')");
        f.context.receipt={type:protection==='accepted prompt'?'input_accepted':'input_rejected',request_id:sent[0].request_id,input_type:'prompt',node_refs:[],message:'Invalid reference'};
        f.run("applyAgentEventBody('a',receipt)");
        if(protection==='new turn') f.run("applyAgentEventBody('a',{type:'agent_start'})");
        f.context.receipt={type:'input_rejected',request_id:sent[1].request_id,input_type:'follow_up',message:'Invalid follow-up'};
        f.run("applyAgentEventBody('a',receipt)");
        f.context.input.value='next input while genuinely busy';
        f.run("agentTuiSendInput(input,'a','project')");
        assert.equal(sent[2].type,'follow_up');
    }
});

test('selection events synchronously update the owning Agent without a direct host call',()=>{
    const f=fixture();
    const locate=()=>{};
    f.context.locate=locate;
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(f.run("JSON.stringify(agentNodeRefsIds(getAgentTuiState('a')))"),'["i"]');
    assert.equal(f.canvasState.agentNodeRefsLocate,locate);
    f.canvasState.selectedIds=['p','p'];
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(f.run("JSON.stringify(agentNodeRefsIds(getAgentTuiState('a')))"),'["p"]');
    assert.equal(f.canvasState.agentNodeRefsLocate,locate);
});
test('selection notifications target the active Agent rather than another docked Agent',()=>{
    const f=fixture();
    f.run("syncAgentInputNodeRefs('b')");
    f.canvasState.selectedIds=['p'];
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(f.run("JSON.stringify(agentNodeRefsIds(getAgentTuiState('b')))"),'["p"]');
    assert.equal(f.run("agentNodeRefsIds(getAgentTuiState('a')).length"),0);
});
test('repeat render notifications cannot resurrect references consumed by a real send',async()=>{
    const f=fixture();
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    await f.run("sendAgentPrompt('a','project','first')");
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    await f.run("sendAgentPrompt('a','project','second')");
    assert.deepEqual(f.requests[0].body.refs,[{node_id:'i'}]);
    assert.equal('refs' in f.requests[1].body,false);
    f.canvasState.selectedIds=['p'];
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(f.run("JSON.stringify(agentNodeRefsIds(getAgentTuiState('a')))"),'["p"]');
});
test('selection notifications filter deleted nodes and tolerate the absence of Agents',()=>{
    const f=fixture();
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    f.canvasState.nodes=f.canvasState.nodes.filter(n=>n.id!=='i');
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(f.run("agentNodeRefsIds(getAgentTuiState('a')).length"),0);
    const empty=fixture();
    empty.canvasState.nodes=empty.canvasState.nodes.filter(n=>n.type!=='smart-agent');
    empty.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(empty.run('agentTuiState.size'),0);
});
test('selection events preserve focused composition, text selection, draft and timeline state',()=>{
    const f=fixture(),attributes=new Map();
    const input={value:'正在组合的草稿',selectionStart:2,selectionEnd:4,
        getAttribute:name=>name==='data-agent-tui-input'?'a':null,
        setAttribute:(name,value)=>attributes.set(name,value),removeAttribute:name=>attributes.delete(name)};
    f.context.document.querySelectorAll=selector=>selector==='[data-agent-tui-input]'?[input]:[];
    f.context.document.activeElement=input;
    f.run("getAgentTuiState('a').draft='正在组合的草稿';getAgentTuiState('a').composing=true;getAgentTuiState('a').scrollState={scrollTop:123,anchor:{key:'entry:66',offset:-31}}");
    const scroll=f.run("getAgentTuiState('a').scrollState");
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    assert.equal(f.context.document.activeElement,input);
    assert.equal(input.value,'正在组合的草稿');
    assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,4);
    assert.equal(f.run("getAgentTuiState('a').draft"),input.value);
    assert.equal(f.run("getAgentTuiState('a').composing"),true);
    assert.equal(f.run("getAgentTuiState('a').scrollState"),scroll);
    assert.equal(attributes.get('role'),'combobox');
});

test('focusing another Agent preserves the callback installed by the host selection event',()=>{
    const f=fixture(),handlers=new Map(),locate=f.context.locate;
    f.run("window.dispatchEvent(new CustomEvent('studio-canvas-selection-sync',{detail:{locate}}))");
    const input={addEventListener:(type,handler)=>handlers.set(type,handler)};
    f.context.el={querySelector:selector=>selector==='[data-agent-tui-input]'?input:null,querySelectorAll:()=>[]};
    f.run("agentNodeRefsBind(el,canvasState.nodes.find(node=>node.id==='b'))");
    handlers.get('focus')();
    assert.equal(f.canvasState.agentNodeRefsLocate,locate);
    assert.equal(f.run("JSON.stringify(agentNodeRefsIds(getAgentTuiState('b')))"),'["i"]');
});

test('legacy queued inputs without a correlation ID or references remain visible on replay',()=>{
    const f=fixture();
    f.run("applyAgentEventBody('a',{type:'queued_input',input_type:'steer',text:'legacy steer'});applyAgentEventBody('a',{type:'queued_input',input_type:'follow_up',text:'legacy follow-up'})");
    assert.equal(f.run("getAgentTuiState('a').messages[0].text"),'↪ steer: legacy steer');
    assert.equal(f.run("getAgentTuiState('a').messages[1].text"),'⏳ follow_up queued: legacy follow-up');
});
