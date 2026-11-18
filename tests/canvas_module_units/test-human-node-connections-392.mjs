import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {editor, identity} from './smart-human-handlers-harness-392.mjs';
import {loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';

function nodeEditor(nodes){
    const state = editor(nodes);
    loadModules(state.ctx, ['canvas-node-ops.js', 'canvas-agent-tui.js']);
    evaluate(state.ctx, readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8').split(/\r?\n/)
        .filter(line => /^const MEDIA_NODE_DEFAULT_SCALE =/.test(line)).join('\n'));
    return state;
}

test('reference-frame toggle commits only the container refFrame and restores missing fields on undo', async () => {
    const {ctx, node} = nodeEditor([
        {id:'group', type:'smart-group', items:['member']},
        imageNode('member'),
    ]);
    assert.equal(ctx.toggleGroupRefFrame('group'), true);
    assert.equal(node('group').userTouchedAt, 1234, 'human reference-frame toggle must stamp ownership T');
    assert.equal(node('group').refFrame, true);
    assert.equal(Object.hasOwn(node('group'), 'promptEditedByUser'), false);
    assert.equal(node('member').userTouchedAt, null);
    await ctx.performUndo();
    assert.equal(Object.hasOwn(node('group'), 'refFrame'), false);
    assert.equal(Object.hasOwn(node('group'), 'userTouchedAt'), false);
    await ctx.performRedo();
    assert.equal(node('group').refFrame, true);
    assert.equal(node('group').userTouchedAt, 1234);
    assert.equal(ctx.toggleGroupRefFrame('member'), false);
    assert.equal(ctx.toggleGroupRefFrame('missing'), false);
    assert.equal(node('member').userTouchedAt, null);
});

const imageNode = (id, extra={}) => ({id, type:'smart-image', x:0, y:0, images:[], ...identity, ...extra});

test('delete stamps only surviving changed input, incoming-edge, membership and history targets, never a ghost/source', async () => {
    const {ctx, node, stored} = nodeEditor([
        imageNode('removed'), imageNode('upstream'), imageNode('edge-only'),
        imageNode('input', {inputNodeIds:['removed', 'upstream'], promptEditedByUser:true}),
        imageNode('unaffected', {inputNodeIds:['upstream']}),
        {id:'group', type:'smart-group', items:['removed', 'unaffected'], ...identity},
        imageNode('history', {isHistoryGroup:true, historyFor:'removed', title:'Candidate pool'}),
    ]);
    evaluate(ctx, `canvas.connections=[{from:'removed',to:'edge-only',kind:'flow'}, {from:'upstream',to:'removed',kind:'input'}];`);
    const removed = node('removed');
    ctx.deleteNode('removed');
    assert.equal(node('input').userTouchedAt, 1234, 'delete must stamp actual surviving input changes');
    for(const id of ['edge-only', 'group', 'history']) assert.equal(node(id).userTouchedAt, 1234, id);
    for(const id of ['upstream', 'unaffected']) assert.equal(node(id).userTouchedAt, null, id);
    assert.equal(node('input').promptEditedByUser, true);
    assert.equal(node('edge-only').promptEditedByUser, false);
    assert.equal(node('history').historyFor, undefined);
    assert.equal(removed.userTouchedAt, null, 'deleted object is not stamped');
    assert.equal(node('removed'), undefined);
    assert.equal(stored().some(item => item.id === 'removed'), false);
    evaluate(ctx, `canvasState.nodes.push({id:'remote',type:'smart-prompt',text:'remote'}); canvasState.nodes.find(n=>n.id==='input').remoteNote='keep';`);
    await ctx.performUndo();
    assert.equal(node('removed').userTouchedAt, null);
    assert.equal(node('input').userTouchedAt, null);
    assert.deepEqual(fromContext(node('input').inputNodeIds), ['removed', 'upstream']);
    assert.equal(node('history').historyFor, 'removed');
    assert.equal(node('remote').text, 'remote');
    assert.equal(node('input').remoteNote, 'keep');
    await ctx.performRedo();
    assert.equal(node('removed'), undefined);
    assert.equal(node('input').userTouchedAt, 1234);
    assert.equal(node('remote').text, 'remote');
    evaluate(ctx, 'Date.now=()=>2345');
    ctx.deleteNode('removed');
    assert.equal(node('input').userTouchedAt, 1234, 'repeated absent deletion does not retouch');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'absent deletion adds no durable undo step');
});

test('disconnect commits only real incoming targets, including edge-only and local-edit/history content', async () => {
    const {ctx, node, stored} = nodeEditor([
        imageNode('source', {images:[{asset_id:'original', url:'original.png'}]}),
        imageNode('edge-only'), imageNode('unaffected'),
        imageNode('history', {historyFor:'source', isHistoryGroup:true, title:'Candidate pool'}),
        {id:'edit', type:'local-edit', inputNodeIds:['source'], localEditInputRoles:{originalNode:'source', referenceNodes:[], promptNode:'', promptNodes:[]}, original_asset_id:'original', reference_asset_ids:[], runInputRefs:[{asset_id:'original'}], runPromptRefs:['old'], sourceNodeId:'source', ...identity},
        {id:'group', type:'smart-group', items:['edit', 'edge-only'], ...identity},
    ]);
    evaluate(ctx, `canvas.connections=[{from:'source',to:'edge-only',kind:'flow'}, {from:'source',to:'edit',kind:'input',role:'original'}, {from:'source',to:'history',kind:'history'}, {from:'source',to:'unaffected',kind:'input'}];`);
    ctx.disconnectConnections('0,1,2');
    assert.equal(node('edge-only').userTouchedAt, 1234, 'human edge removal stamps its input target even without cached input ids');
    assert.equal(node('edit').userTouchedAt, 1234);
    assert.equal(node('history').userTouchedAt, 1234);
    for(const id of ['source', 'unaffected', 'group']) assert.equal(node(id).userTouchedAt, null, id);
    assert.equal(node('edit').promptEditedByUser, false, 'references are not prompt edits');
    assert.equal(node('edit').original_asset_id, '');
    assert.equal(Object.hasOwn(node('edit'), 'runInputRefs'), false);
    assert.equal(Object.hasOwn(node('edit'), 'sourceNodeId'), false);
    assert.equal(node('history').historyFor, undefined);
    assert.equal(stored().find(item => item.id === 'edit').userTouchedAt, 1234);
    evaluate(ctx, `canvasState.nodes.push({id:'remote',type:'smart-prompt',text:'remote'}); canvas.connections.push({from:'remote',to:'unaffected',kind:'input'});`);
    await ctx.performUndo();
    assert.equal(node('edit').original_asset_id, 'original');
    assert.equal(node('edge-only').userTouchedAt, null);
    assert.equal(node('history').historyFor, 'source');
    assert.equal(node('remote').text, 'remote');
    assert.equal(evaluate(ctx, 'canvas.connections.length'), 5);
    await ctx.performRedo();
    assert.equal(node('edit').userTouchedAt, 1234);
    assert.equal(node('remote').text, 'remote');
    assert.equal(evaluate(ctx, 'canvas.connections.length'), 2);
});

test('erase marks/cancellation are UI-only; one finish commits T-only once and remains undoable', async () => {
    const {ctx, node} = nodeEditor([imageNode('source'), imageNode('target', {inputNodeIds:['source']}), imageNode('other')]);
    evaluate(ctx, `canvas.connections=[{from:'source',to:'target',kind:'input'}, {from:'source',to:'other',kind:'flow'}]; connectionEraseState={indices:new Set(),started:false,count:0};`);
    const hit = {dataset:{connIndex:'0'}};
    hit.closest = () => hit;
    ctx.document.elementFromPoint = () => hit;
    assert.equal(ctx.eraseConnectionsAtClientPoint(5, 5), true);
    assert.equal(node('target').userTouchedAt, null, 'marking is not a human durable commit');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0);
    evaluate(ctx, 'connectionEraseState=null');
    assert.equal(ctx.finishConnectionErase(), false, 'cancelled gesture does not commit');
    assert.equal(node('target').userTouchedAt, null);
    evaluate(ctx, 'connectionEraseState={indices:new Set(),started:false,count:0}');
    ctx.eraseConnectionsAtClientPoint(5, 5);
    assert.equal(ctx.finishConnectionErase(), true);
    assert.equal(node('target').userTouchedAt, 1234, 'human erase commit stamps its real input target');
    assert.equal(node('source').userTouchedAt, null);
    assert.equal(node('other').userTouchedAt, null);
    assert.equal(node('target').promptEditedByUser, false);
    ctx.scheduleSave(); // The pointer-up caller owns saving, just as in the real UI.
    assert.equal(ctx.finishConnectionErase(), false, 'finished gesture cannot delete the next indexed edge');
    assert.equal(evaluate(ctx, 'canvas.connections.length'), 1);
    assert.equal(evaluate(ctx, 'undoStack.length'), 1);
    await ctx.performUndo();
    assert.equal(node('target').userTouchedAt, null);
    assert.deepEqual(fromContext(node('target').inputNodeIds), ['source']);
    await ctx.performRedo();
    assert.equal(node('target').userTouchedAt, 1234);
});

test('image rename commits T-only and its own undo delta without taking over remote content', async () => {
    const {ctx, node, stored} = nodeEditor([imageNode('image', {images:[{url:'image.png', name:'old.png'}]})]);
    ctx.openAssetNameDialog = async () => 'new.png'; // User dialog boundary, not an ownership helper.
    await ctx.renameSmartNodeImage('image', 0);
    assert.equal(node().userTouchedAt, 1234, 'human image rename must stamp ownership T');
    assert.equal(node().createdBy, 'agent');
    assert.equal(node().agentNodeId, 'original-agent');
    assert.equal(node().promptEditedByUser, false);
    assert.equal(stored()[0].images[0].name, 'new.png');
    evaluate(ctx, `canvasState.nodes.push({id:'remote',type:'smart-prompt',text:'remote'}); canvasState.nodes[0].remoteNote='keep';`);
    await ctx.performUndo();
    assert.equal(node('image').images[0].name, 'old.png');
    assert.equal(node('image').userTouchedAt, null);
    assert.equal(node('image').remoteNote, 'keep');
    assert.equal(node('remote').text, 'remote');
    await ctx.performRedo();
    assert.equal(node('image').images[0].name, 'new.png');
    assert.equal(node('image').userTouchedAt, 1234);
    assert.equal(node('image').remoteNote, 'keep');
    assert.equal(node('remote').text, 'remote');
});

test('rename resolves the live target after its dialog and same-value/cancel/disappeared targets stay untouched', async () => {
    const {ctx, node} = nodeEditor([imageNode('image', {images:[{url:'image.png', name:'old.png'}], promptEditedByUser:true})]);
    let resolveDialog;
    ctx.openAssetNameDialog = () => new Promise(resolve => { resolveDialog = resolve; });
    const rename = ctx.renameSmartNodeImage('image', 0);
    evaluate(ctx, `canvasState.nodes=canvasState.nodes.map(n=>({...n,images:n.images.map(img=>({...img}))})); canvas.nodes=canvasState.nodes;`);
    resolveDialog('live.png');
    await rename;
    assert.equal(node().userTouchedAt, 1234, 'dialog confirmation stamps the surviving live object, not the stale object');
    assert.equal(node().images[0].name, 'live.png');
    assert.equal(node().promptEditedByUser, true);
    for(const answer of [null, '', '   ', 'live.png']){
        evaluate(ctx, 'Date.now=()=>2345');
        ctx.openAssetNameDialog = async () => answer;
        await ctx.renameSmartNodeImage('image', 0);
        assert.equal(node().userTouchedAt, 1234);
        assert.equal(evaluate(ctx, 'undoStack.length'), 1);
    }
    ctx.openAssetNameDialog = () => new Promise(resolve => { resolveDialog = resolve; });
    const vanished = ctx.renameSmartNodeImage('image', 0);
    evaluate(ctx, 'canvasState.nodes=[]; canvas.nodes=canvasState.nodes');
    resolveDialog('vanished.png');
    await vanished;
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'deleted dialog target creates no ghost undo step');
});

for(const change of ['reordered', 'replaced', 'ambiguous', 'project', 'recreated']){
    test(`image rename identifies its original media after the dialog: ${change}`, async () => {
        const {ctx, node, stored, harness} = nodeEditor([imageNode('image', {images:[{url:'original.png', name:'old.png'}]})]);
        let confirm;
        ctx.openAssetNameDialog = () => new Promise(resolve => { confirm = resolve; });
        const pending = ctx.renameSmartNodeImage('image', 0);
        if(change === 'reordered') node().images.unshift({url:'other.png', name:'other.png'});
        if(change === 'replaced') node().images[0] = {url:'other.png', name:'other.png'};
        if(change === 'ambiguous') node().images.push({...node().images[0]});
        if(change === 'project') evaluate(ctx, "canvas.id='other-canvas';canvas.project_id='other-project'");
        if(change === 'recreated') node().created_at = 54321;
        const before = stored();
        confirm('new.png');
        await pending;
        if(change === 'reordered'){
            assert.equal(node().images[0].name, 'other.png', 'the item moved into the old index is not the target');
            assert.equal(node().images[1].name, 'new.png');
            assert.equal(node().userTouchedAt, 1234);
            await ctx.performUndo();
            assert.equal(node().images[0].name, 'other.png');
            assert.equal(node().images[1].name, 'old.png');
            assert.equal(node().userTouchedAt, null);
        } else {
            assert.deepEqual(stored(), before, 'stale or ambiguous target cannot be mutated');
            assert.equal(evaluate(ctx, 'undoStack.length'), 0);
            assert.ok(harness.toasts.length, 'conflict is visible');
        }
    });
}

test('public server receive replays a batch title/T delta while undo preserves remote fields and nodes', async () => {
    const {ctx, harness, node} = nodeEditor([
        {id:'first',type:'smart-prompt',text:'first',title:'old',...identity},
        {id:'second',type:'smart-prompt',text:'second',title:'Image_02',...identity},
    ]);
    const remote = fromContext(evaluate(ctx, 'canvas'));
    remote.nodes[0].remoteNote = 'remote field';
    remote.nodes.push({id:'remote',type:'smart-prompt',text:'remote addition'});
    ctx.canvasState.selectedIds = ['first', 'second'];
    harness.element('smartBatchRenamePrefix').value = 'Image_';
    harness.element('smartBatchRenamePad').value = '2';
    harness.element('smartBatchRenameStart').value = '1';
    ctx.applyBatchRename();
    assert.equal(ctx.applyMergedServerCanvas(remote), true);
    assert.equal(node('first').title, 'Image_01');
    assert.equal(node('first').userTouchedAt, 1234);
    assert.equal(node('first').remoteNote, 'remote field');
    assert.equal(node('second').userTouchedAt, null);
    await ctx.performUndo();
    assert.equal(node('first').title, 'old');
    assert.equal(node('first').userTouchedAt, null);
    assert.equal(node('first').remoteNote, 'remote field');
    assert.equal(node('remote').text, 'remote addition');
    await ctx.performRedo();
    assert.equal(node('first').title, 'Image_01');
    assert.equal(node('first').userTouchedAt, 1234);
    assert.equal(node('remote').text, 'remote addition');
});

test('automatic shared connection/reroute/layout helpers and invalid/cancelled UI paths do not claim ownership', () => {
    const {ctx, node} = nodeEditor([
        {id:'source', type:'smart-prompt', x:0, y:0, w:316, h:240, text:'auto', ...identity},
        imageNode('target', {x:800, y:400}), imageNode('alternate', {x:1200}),
    ]);
    ctx.addConnection('source', 'target', 'flow');
    assert.equal(ctx.connectInputNode('source', 'target'), true);
    ctx.rerouteSmartConnections('source', 'alternate');
    assert.equal(ctx.arrangeSmartIdsByConnections(['source', 'target']), true);
    ctx.disconnectConnections('99,-1,not-an-index');
    ctx.closeBatchRenameDialog();
    ctx.applyBatchRename(); // No multi-selection: rejected, not a durable action.
    for(const id of ['source', 'target', 'alternate']){
        assert.equal(node(id).userTouchedAt, null, id);
        assert.equal(node(id).promptEditedByUser, false, id);
        assert.equal(node(id).createdBy, 'agent', id);
    }
    assert.equal(evaluate(ctx, 'undoStack.length'), 0);
});

test('image/prompt/conversation/Agent constructors retain explicit new identities, without stamping their source', () => {
    const {ctx, node} = nodeEditor([imageNode('source')]);
    const created = [ctx.createNode(20, 30), ctx.createPromptNode(40, 50), ctx.createConversationNode(60, 70), ctx.createAgentNode(80, 90)];
    for(const item of created){
        assert.equal(item.createdBy, 'user');
        assert.equal(item.agentNodeId, null);
        assert.equal(item.userTouchedAt, null);
        assert.equal(item.promptEditedByUser, false);
        assert.equal(item.created_at, 1234);
    }
    assert.equal(node('source').userTouchedAt, null);
    assert.equal(node('source').createdBy, 'agent');
});

test('batch rename opens the backdrop and cancel restores the hidden state', () => {
    const {ctx, harness, stored} = nodeEditor([
        imageNode('first', {title:'First'}), imageNode('second', {title:'Second'}),
    ]);
    ctx.canvasState.selectedIds = ['first', 'second'];
    const backdrop = harness.element('smartBatchRenameBackdrop');
    backdrop.hidden = true;
    const before = stored();

    ctx.openBatchRenameDialog();
    assert.equal(backdrop.hidden, false);
    assert.equal(backdrop.classList.contains('open'), true);
    ctx.closeBatchRenameDialog();
    assert.equal(backdrop.hidden, true);
    assert.equal(backdrop.classList.contains('open'), false);
    assert.deepEqual(stored(), before);
    assert.equal(evaluate(ctx, 'undoStack.length'), 0);
});

test('batch rename touches only changed titles, preserving legacy ownership presence and existing P', async () => {
    const {ctx, harness, node} = nodeEditor([
        imageNode('same', {title:'Image_01', promptEditedByUser:true}),
        {id:'legacy', type:'smart-prompt', title:'old', text:'unchanged'},
        imageNode('null', {title:'old', createdBy:null, agentNodeId:null, promptEditedByUser:true}),
    ]);
    ctx.canvasState.selectedIds = ['same', 'legacy', 'null'];
    harness.element('smartBatchRenamePrefix').value = 'Image_';
    harness.element('smartBatchRenamePad').value = '2';
    harness.element('smartBatchRenameStart').value = '1';
    ctx.applyBatchRename();
    assert.equal(node('legacy').userTouchedAt, 1234, 'human batch title change must stamp ownership T');
    assert.equal(node('same').userTouchedAt, null);
    assert.equal(node('same').promptEditedByUser, true);
    assert.equal(Object.hasOwn(node('legacy'), 'createdBy'), false);
    assert.equal(Object.hasOwn(node('legacy'), 'agentNodeId'), false);
    assert.equal(Object.hasOwn(node('legacy'), 'promptEditedByUser'), false);
    assert.equal(node('legacy').title, 'Image_02');
    assert.equal(node('legacy').text, 'unchanged');
    assert.equal(node('null').createdBy, null);
    assert.equal(node('null').agentNodeId, null);
    assert.equal(node('null').promptEditedByUser, true);
    assert.equal(node('null').userTouchedAt, 1234);
    assert.equal(evaluate(ctx, 'undoStack.length'), 1);
    evaluate(ctx, 'Date.now=()=>2345');
    ctx.applyBatchRename();
    assert.equal(node('legacy').userTouchedAt, 1234, 'same names do not retouch');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'same names do not add undo');
    await ctx.performUndo();
    assert.equal(node('legacy').title, 'old');
    assert.equal(Object.hasOwn(node('legacy'), 'userTouchedAt'), false, 'undo restores missing T');
    assert.equal(node('null').userTouchedAt, null);
    await ctx.performRedo();
    assert.equal(node('legacy').userTouchedAt, 1234);
});
