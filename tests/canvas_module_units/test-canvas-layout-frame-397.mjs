import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {evaluate, fromContext, JS_DIR, loadModules} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

const source = readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8');
const start = source.indexOf('    const nodeHtmlEntries = canvasState.nodes');
const end = source.indexOf('    const tpl = document.createElement', start);
assert.ok(start >= 0 && end > start);
// Use the actual render() template without needing focus, media or layout APIs from a browser.
const nodeMarkup = `(() => { ${source.slice(start, end)} return nodeHtmlEntries; })()`;
const picture = (id = 'result') => ({asset_id:id, url:`/project-media/${id}.png`, width:1024, height:1024});
const edge = (from, to, kind = 'input') => ({from, to, kind});
const frame = {id:'main', type:'smart-group', layoutFrame:true, title:'Main <images>', x:50, y:70, w:810, h:380, items:['result','prompt']};
const result = {id:'result', type:'smart-image', x:70, y:130, w:292, h:292, images:[]};
const prompt = {id:'prompt', type:'smart-prompt', x:390, y:130, w:316, h:240, text:'Describe the product'};
const sourceCard = {id:'source', type:'smart-image', x:-300, y:130, w:250, h:250, images:[picture('source')]};

function fixture(nodes = [frame, result, prompt, sourceCard], links = []) {
    const e = editor(nodes);
    loadModules(e.ctx, ['canvas-node-ops.js','canvas-agent-tui.js']);
    evaluate(e.ctx, source.split(/\r?\n/).filter(line => /^const MEDIA_(NODE_DEFAULT_SCALE|GROUP_THUMB_BASE|GROUP_MAX_VISIBLE_ROWS) =/.test(line)).join('\n'));
    Object.assign(e.ctx, {
        File, dragState:null, loopInsertPreview:null, promptTemplatePanel:null, smartEdgeReveal:null,
        smartCascadeRuns:new Map(), smartNodeRunTokens:new Map(),
        scheduleSave(){}, smartPreviewImgHtml:img => `<img src="${img.url}">`,
    });
    evaluate(e.ctx, `canvas.connections=${JSON.stringify(links)}`);
    e.links = () => fromContext(evaluate(e.ctx, 'canvas.connections'));
    e.rect = id => fromContext(e.ctx.nodeRect(e.node(id)));
    e.html = () => Object.fromEntries(Array.from(evaluate(e.ctx, nodeMarkup), entry => [entry.node.id, entry.html]));
    return e;
}

function assertInside(outer, inner) {
    assert.ok(inner.x >= outer.x && inner.y >= outer.y);
    assert.ok(inner.x + inner.width <= outer.x + outer.width);
    assert.ok(inner.y + inner.height <= outer.y + outer.height);
}

function assertBox(html, {x, y, width, height}) {
    assert.ok(html.includes(`style="left:${x}px;top:${y}px;width:${width}px;height:${height}px"`));
}

function curve(svg, index) {
    return svg.match(new RegExp(`conn-line" data-conn-index="${index}"[^>]* d="([^"]+)"`))?.[1];
}

test('persisted nonempty layout frame and independently rendered cards survive pending, completion and reload', () => {
    const e = fixture([frame, {...result, pending:1, _generationAspect:{w:1,h:3}}, prompt]);
    const before = {x:50,y:70,width:810,height:380};
    const resultRect = {x:70,y:130,width:292,height:292};
    const promptRect = {x:390,y:130,width:316,height:240};
    assert.deepEqual(e.rect('main'), before);
    assert.deepEqual(e.rect('result'), resultRect);
    assert.deepEqual(e.rect('prompt'), promptRect);
    assertInside(before, resultRect);
    assertInside(before, promptRect);
    const pending = e.html();
    assertBox(pending.main, before);
    assertBox(pending.result, resultRect);
    assertBox(pending.prompt, promptRect);
    assert.match(pending.result, /generation-placeholder/);
    assert.match(pending.main, /smart-layout-frame-node.*?Main &lt;images&gt;/s);
    assert.doesNotMatch(pending.main, /smart-group-action|data-resize|data-port|groupDropHint/);
    assert.doesNotMatch(pending.prompt, /smart-group-member-node|smart-group-member-grab/);
    assert.match(pending.prompt, /data-port="out"/);

    e.ctx.finalizePendingNode(e.node('result'), [picture()], {});
    const reloaded = fixture(e.stored());
    assert.equal(reloaded.node('main').layoutFrame, true);
    assert.deepEqual(fromContext(reloaded.node('main').items), frame.items);
    assert.equal(reloaded.node('main').images?.length || 0, 0);
    const complete = reloaded.html();
    assertBox(complete.main, before);
    assertBox(complete.result, resultRect);
    assertBox(complete.prompt, promptRect);
    assert.match(complete.result, /\/project-media\/result\.png/);
    assert.doesNotMatch(complete.main, /thumb-grid|smart-group-single-thumb|result\.png/);
    const small = fixture([{...frame, items:[], w:80, h:220}]);
    assertBox(small.html().main, {x:50,y:70,width:80,height:220});
});

test('a batch fits its saved card during partial writeback and completion; generation spacing skips frames', () => {
    const pending = {...result, pending:3, _batchTotal:4, _generationAspect:{w:4,h:3},
        pendingTasks:[{taskId:'task'}], images:[picture('partial')]};
    const e = fixture([frame, pending, prompt, sourceCard, {...sourceCard, id:'extra', x:950}], [edge('source','result')]);
    const rect = e.rect('result');
    const html = e.html().result;
    assert.match(html, /generation-filled-cell/);
    assert.match(html, /grid-template-columns:repeat\(2, 114px\)/);
    e.ctx.finalizeSmartPendingTask(e.node('result'), 'task', [picture('done')], 'image', 3);
    assert.deepEqual(e.rect('result'), rect);
    assert.match(e.html().result, /--thumb-w:114px;--thumb-h:86px/);
    const before = ['main','result','prompt'].map(e.rect);
    e.ctx.pushRightSideNodes(e.node('source'), 90);
    assert.deepEqual(['main','result','prompt'].map(e.rect), before);
    assert.equal(e.node('extra').x, 1040);
});

test('ordinary intra-frame edges and same-source edges to distinct cards use their original endpoints', () => {
    const links = [edge('prompt','result','flow'), edge('source','result'), edge('source','prompt')];
    const e = fixture(undefined, links);
    const svg = e.ctx.renderConnections();
    assert.match(curve(svg, 0), /^M706 250 .* 70 276$/);
    assert.match(curve(svg, 1), /^M-50 255 .* 70 276$/);
    assert.match(curve(svg, 2), /^M-50 255 .* 390 250$/);
    assert.deepEqual(e.links(), links);
});

test('upload onto a frame creates a separate card, and removing the frame retains existing cards and links', () => {
    const links = [edge('prompt','result')];
    const e = fixture(undefined, links);
    const {ctx} = e;
    ctx.undoSuppressed = false;
    const before = fromContext(e.node('main'));
    const node = ctx.appendImagesToSmartNode([picture('drop')], 'main', {point:{x:450,y:270},skipUndo:true});
    assert.equal(node.images[0].asset_id, 'drop');
    assert.deepEqual(fromContext(e.node('main')), before);
    assert.equal(e.node(node.id), node);
    ctx.deleteNode('main');
    assert.equal(e.node('main'), undefined);
    assert.ok(e.node('result') && e.node('prompt') && e.node(node.id));
    assert.deepEqual(e.links(), links);
});

test('legacy smart groups and chatFrames still render thumbnails and absorb dropped images', () => {
    for(const marker of [{}, {chatFrame:true}, {chatFrame:true, layoutFrame:true}, {layoutFrame:'true'}]) {
        const legacy = {...frame, ...marker, items:['result','prompt']};
        if(!Object.hasOwn(marker,'layoutFrame')) delete legacy.layoutFrame;
        const links = [edge('prompt','result'), edge('source','result')];
        const e = fixture([legacy, {...result, images:[picture()]}, prompt, sourceCard], links);
        assert.notEqual(e.rect('main').width, 810);
        const html = e.html();
        assert.match(html.main, /smart-group-thumb-grid/);
        assert.match(html.main, /data-smart-group-action="arrange"/);
        assert.match(html.main, /data-resize="1"/);
        assert.match(html.prompt, /smart-group-member-node/);
        const svg = e.ctx.renderConnections();
        assert.equal(curve(svg, 0), undefined);
        assert.match(curve(svg, 1), / 50 \d+(?:\.\d+)?$/);
        assert.equal(e.ctx.addNodeToSmartGroup(e.node('main'), e.node('source')), true);
        assert.equal(e.node('source'), undefined);
        assert.equal(e.node('main').images[0].asset_id, 'source');
        assert.deepEqual(e.links(), [links[0], edge('main','result')]);
        assert.equal(e.node('main').chatFrame, marker.chatFrame);
    }
});

test('layout frame and cards can be moved without nesting, absorbing, rearranging or changing links', () => {
    const ready = {...result, images:[picture()]};
    const legacy = {id:'legacy', type:'smart-group', x:1000,y:0,w:400,h:400,items:[],images:[picture('legacy')]};
    const links = [edge('prompt','result','flow'), edge('source','result')];
    const e = fixture([frame, ready, prompt, sourceCard, legacy], links);
    const {ctx} = e;
    const f = e.node('main'), r = e.node('result'), p = e.node('prompt'), old = e.node('legacy'), s = e.node('source');
    assert.deepEqual(fromContext(ctx.smartNodeDragIds(f, ['main'])), ['main','result','prompt']);
    assert.deepEqual(fromContext(ctx.smartNodeDragIds(s, ['source','main','prompt'])), ['source','main','prompt','result']);
    assert.deepEqual(fromContext(ctx.smartNodeDragIds(r, ['result'])), ['result']);
    ctx.translateSmartNodeWithMembers(f, 30, 20);
    assert.deepEqual([f.x,f.y,r.x,r.y,p.x,p.y], [80,90,100,150,420,150]);
    r.x = 1060; // A user can still move a card normally, including past the frame boundary.
    s.x = 120; // An unrelated card can overlap the frame without joining it.
    const before = e.stored();
    assert.equal(ctx.smartGroupTargetForDraggedNode(r), null);
    assert.equal(ctx.smartGroupTargetForDraggedNode(s), null);
    assert.equal(ctx.pruneSmartGroupMembershipsForNode(r), false);
    assert.equal(ctx.addNodeToSmartGroup(f, s), false);
    assert.equal(ctx.addNodeToSmartGroup(f, old), false);
    assert.equal(ctx.addNodeToSmartGroup(old, f), false);
    assert.equal(ctx.addNodeToSmartGroup(old, r), false);
    assert.equal(ctx.addDraggedNodesToSmartGroup([s,r], old), false);
    assert.equal(ctx.mergeImageNodesIntoGroup(s.id, r.id), false);
    assert.equal(ctx.arrangeSmartGroupMembers(f), false);
    ctx.runSmartGroupToolbarAction(f.id, 'arrange');
    assert.equal(ctx.rejectSmartHumanCopy([f]), true);
    assert.equal(ctx.rejectSmartHumanCopy([r,p]), false);
    assert.equal(ctx.isSmartRunnableNode(f), false);
    assert.equal(ctx.connectInputNode(f.id, s.id), false);
    assert.equal(ctx.connectInputNode(s.id, f.id), false);
    assert.equal(ctx.toggleGroupRefFrame(f.id), false);
    assert.deepEqual(e.stored(), before);
    assert.deepEqual(e.links(), links);
});
