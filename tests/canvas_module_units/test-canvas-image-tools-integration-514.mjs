import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasContext, evaluate, fromContext, loadModules} from './harness.mjs';

const project = 'project-514';
const media = (id, projectId = project) => `/api/project-library/${projectId}/media/${id}`;
const endpoint = op => `/api/project-library/${project}/image-tools/${op}`;
const image = (id, name = `${id}.png`, fields = {}) => ({asset_id:id, name, kind:'image', url:media(id), ...fields});
const file = (source, output) => ({...image(output), source_asset_id:source, mime_type:'image/png', size:1024});
const node = (id, images) => ({id, type:'smart-image', created_at:123, images});
const mask = (id, source, name) => image(id, name, {role:'mask', ...(source ? {source_media_id:source} : {})});
const json = (payload, status = 200) => ({ok:status < 400, status, json:async () => payload});

function boot(nodes, {selected = nodes.map(n => n.id), thumb = nodes[0]?.id, index = 0} = {}) {
    const {ctx, harness} = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'project-canvas-mutations.js', 'canvas-media-history.js', 'canvas-undo.js', 'canvas-image-editor.js']);
    const requests = [], toasts = [], decoded = [], dimensions = new Map(), saves = [], panels = [];
    harness.element('toast');
    const toolbar = {id:'image-tools-anchor'};
    ctx.document.querySelector = sel => sel === `[data-image-toolbar="${thumb}"]` ? toolbar : null;
    Object.assign(ctx, {
        StudioI18n:{t:key => key === 'imageTools.errorDetail' ? `imageTools.errorDetail: {reason}` : key},
        canvasId:project, sourceProjectId:project, canvas:{id:project, project_id:project, format:'firefly-canvas-v1', connections:[], markedAssetIds:['a']},
        FormData, CSS:{escape:s => s},
        canvasForStorage: ({source}) => ({...source, nodes:source.nodes.map(n => ({...n, images:n.images.map(ctx.mediaItemForStorage)}))}),
        isProjectCanvasData: () => ctx.canvas.format === 'firefly-canvas-v1',
        smartProjectId: () => ctx.canvas.project_id,
        mediaKindForItem: item => item.kind,
        render: () => {}, scheduleSave: () => saves.push('save'),
        smartNodeToolbarImageIndex: n => ctx.canvasState.selectedImage.nodeId === n.id ? ctx.canvasState.selectedImage.index : 0,
        selectedNodeIds: () => ctx.canvasState.selectedIds,
        Image: class {
            set src(url) {
                decoded.push(url);
                const size = dimensions.get(url);
                [this.naturalWidth, this.naturalHeight] = size || [0, 0];
                this.loaded = size ? Promise.resolve() : Promise.reject(new Error('cannot decode'));
                this.loaded.then(() => this.onload?.(), () => this.onerror?.());
            }
            decode() { return this.loaded; }
        },
        ffToast:{
            success: (message, options) => toasts.push({type:'success', message, options}),
            error: message => toasts.push({type:'error', message}),
            info: message => toasts.push({type:'info', message}),
        },
        CanvasImageColor:{open(options) {
            const panel = {options, close:() => options.onClose?.('cancel'), reposition:() => panel.positions++, positions:0};
            panels.push(panel);
            return panel;
        }},
    });
    evaluate(ctx, 'canvas = window.canvas');
    Object.assign(ctx.canvasState, {nodes, selectedIds:selected, selectedId:thumb, selectedImage:{nodeId:thumb, index}});
    let transport = async (url, init) => {
        assert.equal(url, endpoint('flip'));
        const {asset_ids} = JSON.parse(init.body);
        return json({files:asset_ids.toReversed().map(id => file(id, `${id}-flipped`))});
    };
    ctx.fetch = (url, init) => {
        requests.push({url, init});
        return transport(url, init);
    };
    return {
        ctx, harness, requests, toasts, decoded, dimensions, saves, panels,
        transport(fn) { transport = fn; },
        get undo() { return evaluate(ctx, 'undoStack'); },
    };
}

function deferred() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return {promise, resolve};
}

test('selected thumbnails and bound masks flip once, matched by source, with one guarded undo', async () => {
    const first = node('n1', [image('a'), image('b'), mask('ma', 'a'), mask('mb', 'b')]);
    const second = node('n2', [image('c'), mask('mc', 'c')]);
    const h = boot([first, second], {index:1});
    assert.equal(await h.ctx.flipImageToolSelection('n1', 'vertical'), true);
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].url, endpoint('flip'));
    assert.equal(h.requests[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(h.requests[0].init.body), {asset_ids:['b','mb','c','mc'], direction:'vertical'});
    assert.deepEqual(first.images.map(i => i.asset_id), ['a','b-flipped','ma','mb-flipped']);
    assert.deepEqual(second.images.map(i => i.asset_id), ['c-flipped','mc-flipped']);
    assert.equal(first.images[3].source_media_id, 'b-flipped');
    assert.equal(second.images[1].source_media_id, 'c-flipped');
    assert.equal(h.undo.length, 1);
    assert.deepEqual(fromContext(h.undo[0].nodes.map(n => n.images.map(i => i.asset_id))), [['a','b','ma','mb'], ['c','mc']]);
    assert.deepEqual(h.saves, ['save']);

    const action = h.toasts.find(t => t.type === 'success')?.options?.action;
    assert.equal(typeof action?.run, 'function');
    h.ctx.pushUndo();
    first.title = 'a later human edit';
    h.ctx.finalizePendingCanvasUndo();
    action.run();
    assert.equal(h.undo.length, 2);
    assert.equal(first.title, 'a later human edit');
});

test('success toast undoes the complete batch when its exact entry is still on top', async () => {
    const h = boot([node('n1', [image('a'), mask('m', 'a')]), node('n2', [image('b')])]);
    assert.equal(await h.ctx.flipImageToolSelection('n1', 'horizontal'), true);
    const action = h.toasts.find(t => t.type === 'success').options.action;
    await action.run();
    const restored = h.ctx.canvasState.nodes;
    assert.deepEqual(fromContext(restored.map(n => n.images.map(i => i.asset_id))), [['a','m'], ['b']]);
    assert.equal(restored[0].images[1].source_media_id, 'a');
    assert.equal(h.undo.length, 0);
    assert.equal(evaluate(h.ctx, 'redoStack.length'), 1);
});

test('late flip rejects thumbnail change, deselection, in-place edit, mask replacement/addition, recreation and project or view switch', async t => {
    const staleChanges = {
        thumbnail: (h) => { h.ctx.canvasState.selectedImage.index = 1; },
        deselection: h => { h.ctx.canvasState.selectedIds = ['n1']; },
        occurrence: (_, first) => { first.images[0].name = 'new.png'; },
        mask: (_, first) => { first.images[2] = {...first.images[2]}; },
        'new mask': (_, first) => { first.images.push(mask('new-mask', 'a')); },
        recreation: (h, first) => { h.ctx.canvasState.nodes[0] = {...first, created_at:456}; },
        project: h => { h.ctx.canvas.project_id = 'other-project'; },
        view: h => { h.ctx.canvas.format = 'classic'; },
    };
    for(const [name, change] of Object.entries(staleChanges)) await t.test(name, async () => {
        const first = node('n1', [image('a'), image('b'), mask('m', 'a')]);
        const second = node('n2', [image('c')]);
        const h = boot([first, second]);
        const waiting = deferred();
        h.transport(() => waiting.promise);
        const pending = h.ctx.flipImageToolSelection('n1', 'horizontal');
        await Promise.resolve();
        change(h, first);
        waiting.resolve(json({files:[file('a','af'), file('m','mf'), file('c','cf')]}));
        assert.equal(await pending, false);
        assert.equal(first.images[0].asset_id, 'a');
        assert.equal(second.images[0].asset_id, 'c');
        assert.equal(h.undo.length, 0);
        assert.deepEqual(h.saves, []);
        assert.equal(h.toasts.at(-1)?.message, 'smart.imageEditTargetChanged');
    });
});

test('invalid flip batch is atomic and retry uses the new direction', async () => {
    const first = node('n1', [image('a')]);
    const second = node('n2', [image('b')]);
    const h = boot([first, second]);
    h.transport(() => json({files:[file('a','af'), file('a','other')]}));
    assert.equal(await h.ctx.flipImageToolSelection('n1', 'horizontal'), false);
    assert.deepEqual([first.images[0].asset_id, second.images[0].asset_id], ['a','b']);
    assert.equal(h.undo.length, 0);
    h.transport(() => json({files:[file('b','bv'), file('a','av')]}));
    assert.equal(await h.ctx.flipImageToolSelection('n1', 'vertical'), true);
    assert.deepEqual(h.requests.map(({init}) => JSON.parse(init.body).direction), ['horizontal', 'vertical']);
    assert.deepEqual([first.images[0].asset_id, second.images[0].asset_id], ['av','bv']);
    assert.equal(h.undo.length, 1);
});

test('a legacy mask requires exactly one old derived name and matching decoded pixels', async () => {
    const first = node('n1', [image('a', 'shared.png'), image('b', 'shared.jpg'), mask('m', '', 'shared_mask.png')]);
    const h = boot([first]);
    for(const id of ['a','b','m']) h.dimensions.set(media(id), [30, 40]);
    assert.equal(await h.ctx.flipImageToolSelection('n1', 'horizontal'), false);
    assert.equal(h.requests.length, 0);
    assert.equal(h.undo.length, 0);
    assert.equal(h.toasts.at(-1)?.message, 'imageTools.maskAmbiguous');
    h.dimensions.set(media('b'), [30, 20]);
    assert.equal(await h.ctx.flipImageToolSelection('n1', 'horizontal'), true);
    assert.deepEqual(JSON.parse(h.requests[0].init.body).asset_ids, ['a','m']);
    assert.equal(first.images[2].source_media_id, 'a-flipped');
    assert.ok(h.decoded.includes(media('a')) && h.decoded.includes(media('b')) && h.decoded.includes(media('m')));
});

test('internal mask attachment owns its bytes and binds to the live source after a title rebase', async () => {
    const original = node('n1', [image('a', 'source.png')]);
    const h = boot([original]);
    const draw = h.harness.element('editDrawCanvas');
    const rgba = [255, 255, 255, 255];
    const pixels = {getImageData:() => ({data:rgba}), createImageData:() => ({data:[]}), putImageData() {}};
    Object.assign(draw, {width:1, height:1, getContext:() => pixels});
    const create = h.ctx.document.createElement;
    h.ctx.document.createElement = tag => tag === 'canvas' ? {
        getContext:() => pixels, toBlob:done => done(new Blob(['mask'], {type:'image/png'}))
    } : create(tag);
    evaluate(h.ctx, `cropState = {nodeId:'n1', imageIndex:0}`);
    h.ctx.currentEditImage = () => ({node:h.ctx.canvasState.nodes[0], index:0});
    h.ctx.importProjectMediaReference = item => item;
    const started = deferred(), uploaded = deferred();
    h.transport((url, init) => {
        assert.equal(url, '/api/ai/upload');
        assert.equal(init.body.get('files').name, 'source_mask.png');
        started.resolve();
        return uploaded.promise;
    });
    const pending = h.ctx.applyImageMask();
    await started.promise;
    const rebased = {...structuredClone(original), title:'A subsequent title edit'};
    h.ctx.canvasState.nodes[0] = rebased;
    evaluate(h.ctx, 'cropState = null');
    uploaded.resolve(json({files:[image('attached-mask','source_mask.png')]}));
    await pending;
    assert.equal(original.images.length, 1);
    assert.equal(rebased.title, 'A subsequent title edit');
    assert.equal(rebased.images.length, 2);
    const attached = h.ctx.mediaItemForStorage(rebased.images[1]);
    assert.equal(attached.asset_id, 'attached-mask');
    assert.equal(attached.source_media_id, 'a');
    assert.equal(attached.role, 'mask');
    assert.equal(h.undo.length, 1);
});

function colorChange(target, encoded, params = {exposure:1}) {
    return {target, file:encoded, metadata:{source_asset_id:target.source_asset_id, op:'color', params, algo_version:1}};
}

test('color persists provenance, reopens and encodes from full original pixels, then restores a retained original', async () => {
    const first = node('n1', [image('a', 'original.png', {marked:true}), mask('m', 'a')]);
    const h = boot([first]);
    const png = new Blob(['pixels'], {type:'image/png'});
    h.dimensions.set(media('a'), [4000, 3000]);
    let result = 'colored1';
    h.transport((url, init) => {
        if(url.endsWith('/media')) return json({items:[{...image('a','original.png'), original_url:media('a')}]});
        assert.equal(url, endpoint('color-encode'));
        assert.equal(init.method, 'POST');
        assert.equal(init.body.get('source_asset_id'), 'a');
        assert.deepEqual([...init.body.keys()], ['source_asset_id','pixels']);
        assert.equal(init.body.get('pixels').type, 'image/png');
        return json({file:file('a', result)});
    });
    h.ctx.openImageToolColor('n1');
    const {options:firstPanel} = h.panels[0];
    assert.equal(firstPanel.anchor().id, 'image-tools-anchor');
    let [target] = firstPanel.targets;
    const pixels = await firstPanel.loadImage(target.url, target);
    assert.equal(pixels.naturalWidth, 4000);
    const output = await firstPanel.encode(target, png);
    await firstPanel.commit([colorChange(target, output)]);
    assert.equal(first.images[0].asset_id, 'colored1');
    assert.equal(first.images[0].marked, undefined);
    assert.equal(first.images[1].source_media_id, 'colored1');
    assert.deepEqual(fromContext(h.ctx.mediaItemForStorage(first.images[0]).edit), colorChange(target, output).metadata);
    assert.equal(h.undo.length, 1);
    h.panels[0].close();

    h.ctx.openImageToolColor('n1');
    const {options:nextPanel} = h.panels[1];
    [target] = nextPanel.targets;
    assert.equal(target.url, media('a'));
    assert.equal(target.current.exposure, 1);
    h.ctx.repositionImageToolColor();
    assert.equal(h.panels[1].positions, 1);
    result = 'colored2';
    await nextPanel.loadImage(target.url, target);
    await nextPanel.commit([colorChange(target, await nextPanel.encode(target, png), {exposure:2})]);
    assert.equal(first.images[0].asset_id, 'colored2');
    assert.equal(first.images[0].edit.source_asset_id, 'a');
    assert.deepEqual(h.decoded, [media('a'), media('a')]);
    assert.equal(await h.ctx.restoreImageToolOriginal('n1'), true);
    assert.equal(first.images[0].asset_id, 'a');
    assert.equal(first.images[0].name, 'original.png');
    assert.equal(first.images[0].edit, undefined);
    assert.equal(first.images[1].source_media_id, 'a');
    assert.deepEqual(h.ctx.canvas.markedAssetIds, ['a']);
    assert.equal(h.undo.length, 3);
});

test('color cannot partially publish a selected batch if a mask changes after encode', async () => {
    const first = node('n1', [image('a'), mask('m', 'a')]);
    const second = node('n2', [image('b'), mask('n', 'b')]);
    const h = boot([first, second]);
    const png = new Blob(['pixels'], {type:'image/png'});
    h.transport((url, init) => {
        assert.equal(url, endpoint('color-encode'));
        assert.equal(init.mode, 'same-origin');
        assert.equal(init.redirect, 'error');
        const id = init.body.get('source_asset_id');
        return json({file:file(id, `${id}-color`)});
    });
    async function encoded(panel){
        const changes = [];
        for(const target of panel.targets) changes.push(colorChange(target, await panel.encode(target, png)));
        return changes;
    }
    const original = h.ctx.openImageToolColor('n1').options;
    const batch = await encoded(original);
    second.images[1] = mask('changed-mask', 'b');
    await assert.rejects(() => original.commit(batch), /smart.imageEditTargetChanged/);
    assert.deepEqual([first.images[0].asset_id, second.images[0].asset_id], ['a','b']);
    assert.equal(h.undo.length, 0);
    const retry = h.ctx.openImageToolColor('n1').options;
    await retry.commit(await encoded(retry));
    assert.deepEqual([first.images[0].asset_id, second.images[0].asset_id], ['a-color','b-color']);
    assert.deepEqual([first.images[1].source_media_id, second.images[1].source_media_id], ['a-color','b-color']);
    assert.equal(h.undo.length, 1);
    assert.deepEqual(h.saves, ['save']);
});

test('color backend reasons reach the panel, and stale restore never writes', async () => {
    const first = node('n1', [image('current', 'colored.png', {edit:{source_asset_id:'a', op:'color', params:{}, algo_version:1}})]);
    const h = boot([first]);
    h.transport(() => json({detail:{code:'unsupported_media_kind', message:'specific image error'}}, 415));
    h.ctx.openImageToolColor('n1');
    const {options} = h.panels[0];
    await assert.rejects(() => options.encode(options.targets[0], new Blob(['x'], {type:'image/png'})));
    assert.match(options.translate('imageColor.applyError'), /specific image error/);
    assert.equal(h.undo.length, 0);
    assert.equal(await h.ctx.restoreImageToolOriginal('n1'), false);
    h.transport(() => json({items:[]}));
    assert.equal(await h.ctx.restoreImageToolOriginal('n1'), false);
    const waiting = deferred();
    h.transport(() => waiting.promise);
    const restoring = h.ctx.restoreImageToolOriginal('n1');
    first.images[0] = image('later');
    waiting.resolve(json({items:[{...image('a'), original_url:media('a')}] }));
    assert.equal(await restoring, false);
    assert.equal(first.images[0].asset_id, 'later');
    assert.equal(h.undo.length, 0);
    assert.deepEqual(h.saves, []);
});
