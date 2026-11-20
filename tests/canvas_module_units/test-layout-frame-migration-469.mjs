import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {evaluate, fromContext, JS_DIR, loadModules} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

const mediaConstants = readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8').split(/\r?\n/)
    .filter(line => /^const MEDIA_(NODE_DEFAULT_SCALE|GROUP_THUMB_BASE|GROUP_MAX_VISIBLE_ROWS) =/.test(line)).join('\n');
const copy = value => JSON.parse(JSON.stringify(value));
const image = id => ({asset_id:id, url:`/api/project-library/unit-469/media/${id}`, name:`${id}.png`, kind:'image', natural_w:1024, natural_h:1024});

function seed() {
    const canvas = {id:'unit-469', project_id:'unit-469', format:'firefly-canvas-v1', revision:1,
        title:'Layout frames', icon:'sparkles', nodes:[], connections:[], history:[]};
    for(const section of ['main', 'details']) {
        const items = [];
        for(let i = 0; i < 2; i++) {
            const prompt = `${section}-prompt-${i}`, result = `${section}-result-${i}`;
            items.push(prompt, result);
            canvas.nodes.push(
                {id:prompt, type:'smart-prompt', text:`Connected instruction ${section} ${i}`, x:i * 850, y:100, w:316, h:240},
                {id:result, type:'smart-image', images:[], x:i * 850 + 350, y:100, w:292, h:292,
                    runSettings:{engine:'api', apiKind:'image', provider_id:'offline-fixture', prompt:''}}
            );
            canvas.connections.push({id:`edge-${result}`, from:prompt, to:result, kind:'input'});
        }
        canvas.nodes.push({id:section, type:'smart-group', layoutFrame:true, items, x:0, y:50, w:1650, h:400});
    }
    return canvas;
}

function fixture(initial = seed()) {
    const e = editor([]);
    loadModules(e.ctx, ['canvas-node-ops.js', 'canvas-agent-tui.js']);
    evaluate(e.ctx, mediaConstants);
    // Exercise real load/migrate/complete/save code; DOM, background polling and HTTP are unit seams.
    Object.assign(e.ctx, {
        canvasId:'unit-469', sourceProjectId:'unit-469', canvasLoadReady:false, smartTitleText:null,
        conversationCanvasLastAcceptedRevision:0, smartNodeRunTokens:new Map(), smartCascadeRuns:new Map(),
        smartGenerationNodeRevisions:new Map(), smartEdgeReveal:null,
        loadProjectViewport:() => ({x:0, y:0, scale:1}),
    });
    for(const name of [
        'rebuildMarkedAssetIdsFromNodes', 'updateSmartMarkedBadge', 'loadRecentSmartSettings',
        'applyRecentSmartSettingsForCurrentMode', 'updateProviderModels', 'applyViewport',
        'frameSmartCanvasContentIfNeeded', 'resumeConversationTasks', 'resumeSmartPendingTasks',
        'resumeSmartPendingActionResumes', 'startCanvasMetaPoll', 'restorePersistedUndoStack',
        'restorePersistedAnnotations',
    ]) e.ctx[name] = () => {};
    let server = copy(initial);
    const requests = [];
    e.ctx.fetch = async (url, options = {}) => {
        const method = options.method || 'GET';
        const body = options.body ? JSON.parse(options.body) : null;
        requests.push({url, method, body});
        if(url === '/api/canvases/unit-469' && method === 'GET') {
            return {ok:true, status:200, json:async () => ({canvas:copy(server)})};
        }
        if(url === '/api/project-library/unit-469/canvas' && method === 'PATCH') {
            assert.equal(body.base_revision, server.revision);
            for(const mutation of body.mutations) {
                assert.equal(mutation.op, 'upsert_node', 'this scenario must never remove cards or rewrite edges');
                const index = server.nodes.findIndex(node => node.id === mutation.node.id);
                assert.notEqual(index, -1);
                server.nodes[index] = copy(mutation.node);
            }
            server.revision++;
            return {ok:true, status:200, json:async () => ({saved:true, canvas:copy(server), revision:server.revision})};
        }
        assert.fail(`unexpected unit I/O: ${method} ${url}`);
    };
    e.snapshot = () => fromContext(e.ctx.canvasForStorage());
    e.server = () => copy(server);
    e.requests = requests;
    e.load = async () => assert.equal(await e.ctx.loadCanvas(), true, JSON.stringify(e.harness.toasts));
    e.save = async () => assert.equal(await e.ctx.saveCanvas(), true, JSON.stringify(e.harness.toasts));
    e.complete = () => {
        for(const node of e.ctx.canvasState.nodes.filter(e.ctx.isSmartImageNode)) {
            // One ordinary result and one multi-output card in each frame; no provider is called.
            const images = node.id.endsWith('-0') ? [image(node.id)] : [image(`${node.id}-a`), image(`${node.id}-b`)];
            e.ctx.finalizePendingNode(node, images, {createdAt:1234, hideTimer:true});
        }
    };
    return e;
}

function assertIndependent(actual, expected) {
    assert.equal(actual.nodes.filter(node => node.type === 'smart-image').length, 4);
    for(const frame of actual.nodes.filter(node => node.layoutFrame === true)) {
        assert.equal(frame.images?.length || 0, 0, 'layout frames must not absorb result images');
        assert.equal(frame.items.length, 4);
        assert.ok(frame.items.every(id => actual.nodes.some(node => node.id === id)));
    }
    assert.deepEqual(actual.nodes, expected.nodes, 'member IDs, content, settings and saved geometry must survive');
    assert.deepEqual(actual.connections, expected.connections, 'prompt input edges must retain their original card endpoints');
}

test('explicit layout frame migration is an idempotent no-op for generated cards and prompt input edges', async () => {
    const e = fixture();
    await e.load();
    e.complete();
    const before = e.snapshot();
    assert.equal(e.ctx.migrateSmartGroupImageMembers(), false);
    assert.equal(e.ctx.migrateSmartGroupImageMembers(), false);
    assertIndependent(e.snapshot(), before);
});

test('real loadCanvas preserves completed independent cards and prompt edges after reload', async () => {
    const generated = fixture();
    await generated.load();
    generated.complete();
    const before = generated.snapshot();
    const reloaded = fixture(before);
    await reloaded.load();
    assertIndependent(reloaded.snapshot(), before);
    assert.deepEqual(reloaded.requests.map(({method, url}) => [method, url]), [['GET', '/api/canvases/unit-469']]);
});

test('incremental save and fresh reopen preserve layout cards, images, geometry and prompt edges', async () => {
    const e = fixture();
    await e.load();
    e.complete();
    // Project saves replace the completed local media URLs with durable asset IDs.
    const before = fromContext(await e.ctx.materializeProjectMediaReferences(e.ctx.canvasForStorage()));
    await e.save();
    assert.ok(e.requests.some(request => request.method === 'PATCH'), 'generation must really enter the save protocol');
    assertIndependent(e.server(), before);

    const reopened = fixture(e.server());
    await reopened.load();
    assertIndependent(reopened.snapshot(), before);
    // A subsequent edit/save must not persist a hidden migration, either.
    reopened.node('main-prompt-0').text += ' edited after reopening';
    const edited = reopened.snapshot();
    await reopened.save();
    assert.ok(reopened.requests.some(request => request.method === 'PATCH'));
    assertIndependent(reopened.server(), edited);
    const again = fixture(reopened.server());
    await again.load();
    assertIndependent(again.snapshot(), edited);
});

test('legacy thumbnail-group migration still absorbs images, reroutes edges and remains idempotent', () => {
    for(const marker of [{}, {layoutFrame:false}, {layoutFrame:'true'}, {chatFrame:true},
        {layoutFrame:true, chatFrame:true}, {layoutFrame:true, refFrame:true}]) {
        const e = editor([
            {id:'legacy', type:'smart-group', ...marker, items:['prompt','result'], w:810, h:380},
            {id:'prompt', type:'smart-prompt', text:'Legacy instruction'},
            {id:'result', type:'smart-image', images:[image('one'), image('two')]},
            {id:'empty', type:'smart-image', images:[]},
        ]);
        loadModules(e.ctx, ['canvas-node-ops.js', 'canvas-agent-tui.js']);
        evaluate(e.ctx, 'canvas.connections=[{from:"prompt",to:"result",kind:"input"}]');
        e.node('legacy').items.push('empty');
        assert.equal(e.ctx.migrateSmartGroupImageMembers(), true, JSON.stringify(marker));
        assert.equal(e.node('result'), undefined);
        assert.deepEqual(fromContext(e.node('legacy').images).map(img => img.asset_id), ['one','two']);
        assert.deepEqual(fromContext(e.node('legacy').items), ['prompt','empty']);
        assert.equal(e.node('legacy').w, undefined);
        assert.equal(e.node('legacy').h, undefined);
        assert.ok(e.node('empty'), 'empty legacy members are not absorbed');
        assert.deepEqual(fromContext(evaluate(e.ctx, 'canvas.connections')), [{from:'prompt',to:'legacy',kind:'input'}]);
        assert.equal(e.ctx.migrateSmartGroupImageMembers(), false);
    }
});
