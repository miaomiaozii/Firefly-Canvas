import assert from 'node:assert/strict';
import test from 'node:test';
import {evaluate, fromContext, loadModules} from './harness.mjs';
import {editor, event} from './smart-human-handlers-harness-392.mjs';

const image = (asset_id, url = `/test/${asset_id}.png`) => ({asset_id, url, name:asset_id, kind:'image'});
const edge = (id, asset_id, role, order, enabled = true, to = 'target') => ({
    id, from:'source', to, kind:'input', agentRef:true, asset_id, role, order, enabled
});
const ordinary = (from, to = 'target', kind = 'input') => ({id:`${from}.${to}.${kind}`, from, to, kind});
const selectedRefs = refs => Array.from(refs, ref => ref.asset_id);

function setup(nodes, edges = []) {
    const e = editor(nodes);
    const {ctx, harness} = e;
    loadModules(ctx, ['canvas-agent-tui.js']);
    Object.assign(ctx, {
        SMART_REFERENCE_IMAGE_MAX:12, Node:{TEXT_NODE:3, ELEMENT_NODE:1},
        validOutpaintSize:() => null,
        smartCascadeRuns:new Map(), smartNodeRunTokens:new Map(),
        nodeRect:node => ({x:node.x || 0, y:node.y || 0, width:100, height:80}),
        scheduleSave(){},
        resumeSmartPendingTasks(){},
        recoverStuckLoopOutputsFromHistory:() => false,
        clearCompletedNodeBusyStates:() => false,
        setSmartTitleText(){},
        smartHistoryModal:null,
        mentionInsertMode:'token',
        closeMentionPicker(){},
        inputThumbsRow:harness.makeElement(),
        bindSmartPreviewImageFallbacks(){},
        smartPreviewImgHtml:() => '<img>',
    });
    evaluate(ctx, `canvas.connections = ${JSON.stringify(edges)}; canvasState.selectedId='target';`);
    e.edges = () => fromContext(evaluate(ctx, 'canvas.connections'));
    e.installEdges = connections => evaluate(ctx, `canvas.connections = ${JSON.stringify(connections)}`);
    e.target = () => e.node('target');
    e.prompt = (...parts) => {
        ctx.promptInput.childNodes = parts.map(part => {
            if(typeof part === 'string') return {nodeType:3, textContent:part};
            const el = harness.makeElement('span');
            el.nodeType = 1;
            el.classList.add('mention-image-token');
            el.dataset = part;
            return el;
        });
    };
    e.prompt('draw');
    e.request = (override = null, loopCtx = null) => fromContext(ctx.buildPromptRequest(e.target(), override, false, loopCtx));
    return e;
}

function fixture() {
    const scene = image('scene');
    const product = image('product');
    const hand = {url:scene.url, name:'Hand-added scene', kind:'image'};
    const nodes = [
        {id:'frame', type:'smart-group', items:['source','target','text']},
        {id:'source', type:'smart-image', x:20, images:[product, image('disabled'), scene, image('unpicked')]},
        {id:'text', type:'smart-prompt', text:'Never relay frame prompt'},
        {id:'target', type:'smart-image', x:520, images:[], inputNodeIds:['source','standard'], manualInputRefs:[hand]},
        {id:'standard', type:'smart-image', images:[image('ordinary'), image('scene', '/alternate/scene.png')]},
        {id:'flow', type:'smart-image', images:[image('flow')]},
    ];
    // Intentionally out of order, including a role that would sort before scene_style.
    // Changing from source to frame also exercises images nested under the source.
    const edges = [ordinary('standard'), edge('e-product', 'product', 'product', 3),
        edge('e-disabled', 'disabled', 'detail', 2, false), edge('e-scene', 'scene', 'scene_style', 1),
        {...edge('e-frame', 'frame-missing', 'layout', 4, false), from:'frame'}, ordinary('flow','target','flow')];
    return {nodes, edges, scene, product, hand};
}

test('legacy hand refs, blockedInputRefs and @图 mention numbering are unchanged', () => {
    const manual = {url:'/test/manual.png', name:'hand', asset_id:'legacy-hand', kind:'image'};
    const lead = image('lead');
    const mention = {url:'/test/mentioned.png', name:'extra', kind:'image'};
    const e = setup([{id:'target', type:'smart-image', manualInputRefs:[manual]}, {id:'up', type:'smart-image', images:[lead]}], [ordinary('up')]);
    e.prompt('Use ', {url:manual.url, name:manual.name}, ' and ', mention, ' @图7');
    assert.deepEqual(e.request(), {
        prompt:'smart.refMapHeader\n图1：lead\n图2：hand\n图3：extra\n\nsmart.refUserNeed\nUse 图2 and 图3 @图7',
        displayPrompt:'Use @hand and @extra @图7', mentioned:true,
        refs:[lead, manual, mention].map((img, i) => ({url:img.url, name:img.name, kind:'image', asset_uris:{}, role:`image_${i+1}`})),
    });
    e.ctx.renderInputThumbsRow(e.target());
    assert.deepEqual([...e.ctx.inputThumbsRow.innerHTML.matchAll(/data-reference-marker="(\d+)"/g)].map(m => m[1]), ['1','2','3']);
    e.target().blockedInputRefs = ['up|0', 'url|/test/mentioned.png'];
    const blocked = e.request();
    assert.deepEqual(blocked.refs.map(img => img.url), [manual.url]);
    assert.match(blocked.prompt, /Use 图1 and @extra @图7$/);
    // The legacy thumb list includes blocked refs; only the request omits them.
    assert.equal(e.ctx.visibleReferenceImagesFor(e.target()).length, 3);
});

test('mixed normal/workflow inputs resolve exact assets, use backend order, and never relay a managed source prompt', () => {
    const {nodes, edges, scene, product} = fixture();
    const e = setup(nodes, edges);
    const {ctx} = e;
    assert.deepEqual(selectedRefs(e.request().refs), ['scene','product','ordinary']);
    assert.deepEqual(selectedRefs(ctx.visibleReferenceImagesFor(e.target())), ['scene','product','ordinary']);
    assert.deepEqual(e.request().refs.map(img => img.role), ['image_1','image_2','image_3']);
    assert.deepEqual(selectedRefs(e.request(null, {forceWorkflow:true, roundOutputs:new Map([['source',[image('wrong-round')]]])}).refs), ['scene','product','ordinary','flow']);
    assert.deepEqual(selectedRefs(e.request([image('batch')], {forceWorkflow:true}).refs), ['scene','product','batch']);
    e.prompt('Use ', {url:scene.url, assetId:'scene', name:'scene'}, ' with ', {url:product.url, assetId:'product', name:'product'});
    assert.match(e.request().prompt, /Use 图1 with 图2$/);
    e.prompt('Use ', {url:'/alternate/scene.png', name:'Alternate URL'});
    assert.match(e.request().prompt, /Use 图1$/);
    assert.deepEqual(selectedRefs(ctx.visibleReferenceImagesFor(e.target())), ['scene','product','ordinary']);

    const fromFrame = {...edges[4], enabled:true, asset_id:'product', order:1};
    e.installEdges([fromFrame]);
    e.prompt('draw');
    e.target().inputNodeIds = ['frame'];
    ctx.canvasUsesConnections = false; // legacy mirror must not widen a managed source to a frame input
    assert.deepEqual(e.request().refs.map(img => img.url), [product.url, scene.url]); // separate hand ref / token
    assert.equal(ctx.inputNodesFor(e.target()).length, 0);
    assert.equal(ctx.inputPromptTextFor(e.target()), '');
    assert.deepEqual(selectedRefs(ctx.inputMentionCandidateImages(e.target())), ['product', undefined]);
});

test('explicit same-URL manual ref remains independent across disable and replacement', () => {
    const {nodes, scene, hand} = fixture();
    const managedScene = edge('e-scene', 'scene', 'scene_style', 1);
    delete nodes.find(node => node.id === 'target').manualInputRefs;
    const e = setup(nodes);
    const {ctx} = e;
    const server = fromContext(evaluate(ctx, 'canvas'));
    const receive = (revision, connections) => assert.equal(ctx.applyMergedServerCanvas({...server, revision, connections}), true);
    receive(1, [managedScene]);
    ctx.addManualReferenceToSelectedNode(hand);
    assert.deepEqual(selectedRefs(ctx.visibleReferenceImagesFor(e.target())), ['scene']);
    assert.equal(e.target().manualInputRefs[0].url, scene.url);
    receive(2, [{...managedScene, enabled:false}]);
    assert.equal(e.request().refs[0].name, hand.name);
    assert.equal(ctx.visibleReferenceImagesFor(e.target())[0].manualAdded, true);
    receive(3, [edge('new-choice', 'product', 'general', 1)]);
    assert.deepEqual(e.request().refs.map(img => img.url), ['/test/product.png', scene.url]);
    receive(4, []);
    assert.deepEqual(e.request().refs.map(img => img.url), [scene.url]);
    assert.equal(e.target().manualInputRefs[0].url, scene.url);
});

test('loop preview and batching consume asset selections rather than widening to source outputs', () => {
    const {nodes, edges} = fixture();
    const target = nodes.find(node => node.id === 'target');
    Object.assign(target, {type:'smart-loop', imageInput:true, imageBatchSize:2, loopStart:1});
    const e = setup(nodes, edges);
    assert.deepEqual(selectedRefs(e.ctx.smartLoopPreviewImages(e.target())), ['scene','product','ordinary']);
    assert.deepEqual(selectedRefs(e.ctx.smartLoopInputImages(e.target(), {index:1})), ['scene','product']);
    assert.deepEqual(selectedRefs(e.ctx.smartLoopInputImages(e.target(), {index:2})), ['product','ordinary']);
});

test('coincident URLs can still identify distinct managed assets and mentions', () => {
    const {nodes} = fixture();
    nodes[1].images = [image('a','/same'), image('b','/same')];
    nodes.find(node => node.id === 'target').manualInputRefs = [];
    const e = setup(nodes, [edge('e-a','a','product',2), edge('e-b','b','general',1)]);
    e.prompt('Use ', {url:'/same', assetId:'a', name:'A'}, ' then ', {url:'/same', assetId:'b', name:'B'});
    assert.deepEqual(selectedRefs(e.request().refs), ['b','a']);
    assert.match(e.request().prompt, /Use 图2 then 图1$/);
});

test('same-frame SVG, managed thumbnail removal and line erase keep parallel edges independently addressable', () => {
    const {nodes, edges} = fixture();
    const injected = '"/><script>untrusted()</script>';
    const bad = edge('invalid','unpicked',injected,injected,false);
    const e = setup(nodes, [...edges, bad, ordinary('source')]);
    const {ctx, harness} = e;
    const svg = ctx.renderConnections();
    assert.match(svg, /agent-reference-layer" style="z-index:1"/);
    assert.match(svg, /data-conn-index="1" role="img" aria-label="Agent reference: product; enabled; order 3"/);
    assert.match(svg, /data-conn-index="2"[^>]*data-agent-ref-enabled="false" data-agent-ref-order="2"/);
    assert.match(svg, />#1 scene_style<\/text>/);
    assert.doesNotMatch(svg, /untrusted|data-conn-index="7"/); // ordinary internal line remains hidden
    const paths = [...svg.matchAll(/data-conn-index="[123]" role="img"[^>]* d="([^"]+)"/g)].map(match => match[1]);
    assert.equal(new Set(paths).size, 3);
    ctx.renderInputThumbsRow(e.target());
    assert.match(ctx.inputThumbsRow.innerHTML, /data-input-remove-reference="source\|2" data-agent-ref-edge-id="e-scene"/);

    const button = harness.makeElement('button');
    button.dataset.agentRefEdgeId = 'e-scene';
    ctx.inputThumbsRow.querySelectorAll = selector => selector === '[data-input-remove-reference]' ? [button] : [];
    ctx.bindInputThumbReferenceActions();
    button.dispatchEvent(event(button, {type:'click'}));
    assert.deepEqual(e.edges().filter(c => c.from === 'source').map(c => c.id), ['e-product','e-disabled','invalid','source.target.input']);
    assert.deepEqual(fromContext(e.target().inputNodeIds), ['source','standard']);
    assert.equal(e.target().manualInputRefs.length, 1);
    evaluate(ctx, 'connectionEraseState = {indices:new Set([1])}');
    assert.equal(ctx.finishConnectionErase(), true);
    assert.deepEqual(e.edges().filter(c => c.from === 'source').map(c => c.id), ['e-disabled','invalid','source.target.input']);
    assert.deepEqual(fromContext(e.target().inputNodeIds), ['source','standard']);
});

test('merge, reroute, save diff and undo keep explicit IDs on multiple selections', async () => {
    const {nodes, edges} = fixture();
    const e = setup(nodes);
    const {ctx} = e;
    const remote = {...fromContext(evaluate(ctx, 'canvas')), revision:1, connections:edges};
    assert.equal(ctx.applyMergedServerCanvas(remote), true);
    assert.deepEqual(e.edges(), edges);
    const client = ctx.FireflyProjectCanvasMutations;
    const baseline = client.snapshotFromCanvas(remote);
    const managed = edges.filter(c => c.agentRef);
    assert.deepEqual(Object.keys(baseline.edges).filter(id => id.startsWith('e-')), managed.map(c => c.id));
    ctx.uid = prefix => `${prefix}-test-copy`;
    assert.equal(ctx.copySmartConnection(managed[0], new Map([['source','copied-source']])), null);
    assert.deepEqual(fromContext(ctx.copySmartConnection(managed[0], new Map([['target','copied-target']]))),
        {...managed[0], id:'edge-test-copy', to:'copied-target'});
    const merged = ctx.mergeSmartConnections([managed[0]], edges, new Set(nodes.map(n => n.id)));
    assert.equal(merged.length, edges.length);
    assert.equal(ctx.connectInputNode('source','target'), true);
    assert.equal(e.edges().filter(c => c.from === 'source').length, 4);
    ctx.rerouteSmartConnections('source','frame');
    assert.deepEqual(e.edges().filter(c => c.agentRef).map(c => c.id), managed.map(c => c.id));
    e.installEdges(edges);
    ctx.disconnectConnections([1]);
    const removed = fromContext(client.mutationsFromCanvas(ctx.canvasForStorage(), baseline).mutations).filter(m => m.op.includes('edge'));
    assert.deepEqual(removed, [{op:'remove_edge', edge_id:'e-product'}]);
    await ctx.performUndo();
    assert.deepEqual(e.edges().filter(c => c.agentRef).sort((a,b) => a.order-b.order), managed.slice().sort((a,b) => a.order-b.order));
});
