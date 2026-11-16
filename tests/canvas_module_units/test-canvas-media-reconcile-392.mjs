// #392 / U09: public mutation APIs and the real classic-script media module.
// Harness supplies the browser surface; the sole host stub below is a
// constant image-kind fixture (classification is not the seam under test).
import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { createCanvasContext, loadModules, globalFunction, evaluate } from './harness.mjs';

const require = createRequire(import.meta.url);
const mutations = require('../../static/js/project-canvas-mutations.js');
const plain = value => JSON.parse(JSON.stringify(value));

function rebaseNode(base, local, remote) {
    const baseline = { revision: 1, nodes: [base], connections: [], history: [] };
    return mutations.rebaseProjectCanvasResponse(
        { ...baseline, revision: 2, nodes: [remote] },
        mutations.snapshotFromCanvas(baseline),
        { ...baseline, nodes: [local] },
    );
}

function mediaContext() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js',
        'project-canvas-mutations.js', 'canvas-media-history.js']);
    evaluate(ctx, 'canvas = {project_id:"p", history:[]}; canvasCore.mediaKindForItem = () => "image"');
    return { ctx, reconcile: globalFunction(ctx, 'reconcileMaterializedMediaReferences') };
}

test('same asset and legacy id: output identity keeps local rename/delete/reorder plus remote addition', () => {
    const a = { output_id: 'a', id: 'legacy', asset_id: 'shared', name: 'A' };
    const b = { output_id: 'b', id: 'legacy', asset_id: 'shared', name: 'B' };
    const c = { output_id: 'c', id: 'legacy', asset_id: 'shared', name: 'C' };
    const d = { output_id: 'd', id: 'legacy', asset_id: 'shared', name: 'remote D' };
    const base = { id: 'n', images: [a, b, c] };
    const result = rebaseNode(base, { ...base, images: [{ ...c, name: 'local C' }, a] },
        { ...base, images: [a, b, c, d] });
    assert.deepEqual(result.canvas.nodes[0].images, [{ ...c, name: 'local C' }, a, d]);
    assert.equal(JSON.parse(result.baseline.nodes.n).images.length, 4, 'server shadow stays pure');
    assert.equal(result.mutations.length, 1, 'local collection edits stay pending');
});

test('ordered prompt arrays replace locally, including legitimate duplicate empty fields', () => {
    for(const [before, localValues, remoteValues] of [[['old'],['L'],['R']], [['a','b'],['',''],['R','b']]]){
        const base={id:'p',type:'smart-prompt',variablePrompts:before};
        assert.deepEqual(rebaseNode(base,{...base,variablePrompts:localValues},{...base,variablePrompts:remoteValues}).canvas.nodes[0].variablePrompts,localValues);
    }
});

test('group scalar members merge deletions and local order without losing remote additions', () => {
    const base = { id: 'g', items: ['a', 'b', 'c'] };
    const local = { ...base, items: ['c', 'a'] };
    const remote = { ...base, items: ['a', 'b', 'c', 'd'] };
    assert.deepEqual(rebaseNode(base, local, remote).canvas.nodes[0].items, ['c', 'a', 'd']);
    assert.deepEqual(mutations.mergeCanvasValue(base, local, remote), { id: 'g', items: ['c', 'a', 'd'] });
});

test('ambiguous anonymous duplicate assets report a toastable conflict and do not consume local intent', () => {
    const base = { id: 'n', images: [{ asset_id: 'shared', name: 'A' }, { asset_id: 'shared', name: 'B' }] };
    const local = { ...base, images: [{ asset_id: 'shared', name: 'local B' }] };
    const remote = { ...base, images: [...base.images, { asset_id: 'new' }] };
    const frozenLocal = plain(local);
    assert.throws(() => rebaseNode(base, local, remote), error => {
        assert.equal(error.name, 'CanvasCollectionConflict');
        assert.match(error.message, /冲突/);
        assert.match(error.message, /保留/);
        return true;
    });
    assert.deepEqual(local, frozenLocal);
});

test('terminal task response completes runtime state without replacing pending media or prompt edits', () => {
    const a = { output_id: 'a', asset_id: 'shared', name: 'A' };
    const b = { output_id: 'b', asset_id: 'shared', name: 'B' };
    const c = { output_id: 'c', asset_id: 'shared', name: 'C' };
    const d = { output_id: 'd', asset_id: 'shared', name: 'D' };
    const base = { id: 'n', type: 'smart-image', generationState: 'queued', pending: 1,
        running: true, prompt: 'before', images: [a, b, c] };
    const local = { ...base, generationState: 'running', prompt: 'local prompt', images: [{ ...c, name: 'local C' }, a] };
    const remote = { ...base, generationState: 'succeeded', pending: 0, running: false, images: [a, b, c, d] };
    assert.deepEqual(rebaseNode(base, local, remote).canvas.nodes[0], {
        ...remote, prompt: 'local prompt', images: [{ ...c, name: 'local C' }, a, d],
    });
});

test('materialize fills only the sent identities and preserves in-flight addition/deletion/reorder/rename', () => {
    const { reconcile } = mediaContext();
    const a = { output_id: 'a', id: 'legacy', url: '/a.png', name: 'A', kind: 'image' };
    const b = { output_id: 'b', id: 'legacy', url: '/b.png', name: 'B', kind: 'image' };
    const c = { output_id: 'c', id: 'legacy', url: '/c.png', name: 'C', kind: 'image' };
    const added = { output_id: 'new', url: '/new.png', name: 'added while waiting' };
    const before = { images: [a, b, c] };
    const current = { images: [added, { ...c, name: 'local C', caption: 'local caption' }, a] };
    const saved = { images: [a, b, c].map(({ url, ...item }) => ({ ...item, asset_id: 'shared' })) };
    const result = reconcile(current, before, saved);
    assert.deepEqual(plain(result.images), [added,
        { ...c, name: 'local C', caption: 'local caption', asset_id: 'shared',
            url: '/api/project-library/p/media/shared', preview_url: '/api/project-library/p/media/shared/preview' },
        { ...a, asset_id: 'shared', url: '/api/project-library/p/media/shared',
            preview_url: '/api/project-library/p/media/shared/preview' },
    ]);
    assert.deepEqual(before.images, [a, b, c], 'frozen request is not modified');
    assert.equal(current.images[1].asset_id, undefined, 'reconciliation is pure');
});

test('unique legacy URLs match live entries after reorder without assigning a new entry the old response', () => {
    const { reconcile } = mediaContext();
    // Production materialize strips source URLs but preserves its frozen input
    // order. Only that request/response pairing may use position, never live.
    const before = ['/a.png', '/b.png'];
    const current = ['/b.png', '/new.png', '/a.png'];
    const result = reconcile(current, before, [{ asset_id: 'aa' }, { asset_id: 'bb' }]);
    assert.deepEqual(plain(result), [
        { asset_id: 'bb', url: '/api/project-library/p/media/bb', preview_url: '/api/project-library/p/media/bb/preview' },
        '/new.png',
        { asset_id: 'aa', url: '/api/project-library/p/media/aa', preview_url: '/api/project-library/p/media/aa/preview' },
    ]);
});

test('ambiguous legacy materialize responses fail explicitly rather than overwrite by index or silently skip', () => {
    const { reconcile } = mediaContext();
    const before = [{ url: '/same.png', name: 'A' }, { url: '/same.png', name: 'B' }];
    const current = [{ url: '/same.png', name: 'local B' }];
    const saved = [{ asset_id: 'shared', name: 'A' }, { asset_id: 'shared', name: 'B' }];
    assert.throws(() => reconcile(current, before, saved), error => {
        assert.equal(error.name, 'CanvasCollectionConflict');
        assert.match(error.message, /冲突.*保留/);
        return true;
    });
    assert.deepEqual(current, [{ url: '/same.png', name: 'local B' }]);
});

test('a reference replaced while materialize waits is never backfilled with the previous media', () => {
    const { reconcile } = mediaContext();
    const before = { output_id: 'a', asset_id: 'old', url: '/old.png', name: 'A' };
    const current = { ...before, url: '/replacement.png', name: 'replacement' };
    assert.deepEqual(plain(reconcile(current, before, { output_id: 'a', asset_id: 'old', name: 'catalog A' })), current);
    const anonymous = { url: '/a.png' };
    const alreadyReplaced = { url: '/a.png', asset_id: 'newer' };
    assert.deepEqual(plain(reconcile(alreadyReplaced, anonymous, { asset_id: 'stale' })), alreadyReplaced);
});

test('legacy media with unique URLs merges local rename/deletion and remote additions conservatively', () => {
    const a = { url: '/a.png', name: 'A' };
    const b = { url: '/b.png', name: 'B' };
    const c = { url: '/c.png', name: 'remote C' };
    const base = { id: 'n', images: [a, b] };
    assert.deepEqual(rebaseNode(base, { ...base, images: [{ ...a, name: 'local A' }] },
        { ...base, images: [a, b, c] }).canvas.nodes[0].images, [{ ...a, name: 'local A' }, c]);
});

test('unidentifiable media entries cannot silently turn a concurrent collection merge into local replacement', () => {
    const base = { id: 'n', images: [{ name: 'A' }, { name: 'B' }] };
    const local = { ...base, images: [{ name: 'local B' }] };
    const remote = { ...base, images: [...base.images, { name: 'remote C' }] };
    assert.throws(() => rebaseNode(base, local, remote), { name: 'CanvasCollectionConflict' });
});

test('a scalar media slot replaced by another output sharing its URL keeps the new output untouched', () => {
    const { reconcile } = mediaContext();
    const before = { output_id: 'old-output', id: 'legacy', url: '/same.png', name: 'old' };
    const current = { ...before, output_id: 'new-output', name: 'new' };
    assert.deepEqual(plain(reconcile(current, before,
        { output_id: 'old-output', id: 'legacy', asset_id: 'shared', name: 'old' })), current);
});

test('409 after lost ack recognizes identical anonymous collections as no-op instead of ambiguous concurrency', async () => {
    const baseNode = { id: 'n', images: [{ asset_id: 'shared', name: 'A' }, { asset_id: 'shared', name: 'B' }] };
    const localNode = { ...baseNode, images: [{ asset_id: 'shared', name: 'local A' }, baseNode.images[1]] };
    const base = { revision: 1, nodes: [baseNode], connections: [], history: [] };
    let calls = 0;
    const result = await mutations.flushProjectCanvasMutations({
        canvas: base, baseline: mutations.snapshotFromCanvas(base),
        mutations: [{ op: 'upsert_node', node: localNode }],
        send: async () => { calls++; return { status: 409, canvas: { ...base, revision: 2, nodes: [localNode] } }; },
    });
    assert.equal(result.saved, true);
    assert.equal(result.confirmedWithoutWrite, true);
    assert.equal(calls, 1);
    assert.deepEqual(result.canvas.nodes, [localNode]);
});

test('when materialized legacy items retain URLs those unique URLs take precedence over response order', () => {
    const { reconcile } = mediaContext();
    const a = { url: '/a.png', name: 'A' };
    const b = { url: '/b.png', name: 'B' };
    const current = [{ ...b, name: 'local B' }, a];
    assert.deepEqual(plain(reconcile(current, [a, b], [
        { ...b, asset_id: 'bb' }, { ...a, asset_id: 'aa' },
    ])), [{ ...b, name: 'local B', asset_id: 'bb' }, { ...a, asset_id: 'aa' }]);
});

test('locally inserted media keeps its chosen position while remote new outputs survive', () => {
    const a = { id: 'a', name: 'A' };
    const b = { id: 'b', name: 'B' };
    const x = { id: 'x', name: 'local X' };
    const d = { id: 'd', name: 'remote D' };
    const base = { id: 'n', images: [a, b] };
    assert.deepEqual(rebaseNode(base, { ...base, images: [x, a, b] },
        { ...base, images: [a, b, d] }).canvas.nodes[0].images, [x, a, b, d]);
});

test('append-only local additions retain existing remote insertion priority for journal replay', () => {
    const a = { id: 'a' };
    const b = { id: 'b' };
    const c = { id: 'c' };
    const r = { id: 'r' };
    const first = mutations.mergeCanvasValue([a], [a, b], [a, r]);
    assert.deepEqual(first, [a, r, b]);
    assert.deepEqual(mutations.mergeCanvasValue([a, b], [a, b, c], first), [a, r, b, c]);
});
