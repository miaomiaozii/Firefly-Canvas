// #392 stage 2a: agreed public seams are the pure canvasCore ownership,
// action-delta and pending-journal APIs. No manual entry wiring is tested here.
// Real classic core only; browser globals come from the existing harness.
// No journal/delta/merge implementation is stubbed. Collection tests below load
// the production mutations helper once that seam is needed.
import assert from 'node:assert/strict';
import test from 'node:test';
import { performance } from 'node:perf_hooks';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function core({ collections = false } = {}) {
    const { ctx } = createCanvasContext();
    loadModules(ctx, collections ? ['canvas-node-types.js', 'canvas-core.js', 'project-canvas-mutations.js'] : ['canvas-core.js']);
    return ctx.canvasCore;
}
const plain = fromContext;

test('action delta replays nested scalar intent by entity ID without changing remote siblings', () => {
    const api = core();
    const before = { nodes: [{ id: 'n', text: 'old', config: { enabled: true, reset: 9 } }], connections: [] };
    const after = { nodes: [{ id: 'n', text: '', config: { enabled: false, reset: null } }], connections: [] };
    const delta = api.smartCanvasActionDelta(before, after);
    assert.equal(delta.version, 1);
    assert.equal(delta.changes.length, 3);
    const remote = { nodes: [{ id: 'remote' }, { id: 'n', text: 'server', config: { enabled: true, reset: 10, keep: 'remote' } }], connections: [] };
    assert.deepEqual(plain(api.applySmartCanvasDelta(remote, delta)), {
        canvas: { nodes: [{ id: 'remote' }, { id: 'n', text: '', config: { enabled: false, reset: null, keep: 'remote' } }], connections: [] },
        conflicts: [],
    });
    assert.equal(remote.nodes[1].text, 'server', 'source must not be mutated');
    assert.equal(before.nodes[0].text, 'old');
});

test('pending high seq compensates false/null restoration after A was sent; A ack cannot clear B', () => {
    const api = core();
    assert.equal(typeof api.createSmartCanvasPendingJournal, 'function', 'the real core must provide a pending journal');
    const journal = api.createSmartCanvasPendingJournal();
    const shadow = { nodes: [{ id: 'n', text: 'old', userTouchedAt: null, promptEditedByUser: false }], connections: [] };
    const a = { nodes: [{ id: 'n', text: 'edited', userTouchedAt: 1700000000123, promptEditedByUser: true }], connections: [] };
    journal.record(shadow, shadow);
    assert.deepEqual(plain(journal.checkpoint()), { seq: 0 }, 'no-op does not allocate a seq');
    journal.record(shadow, a, { actionId: 'A' });
    const sentA = journal.checkpoint();
    journal.record(a, shadow, { actionId: 'undo-A' });
    assert.deepEqual(plain(journal.checkpoint()), { seq: 2 });
    journal.acknowledge(sentA);
    assert.equal(journal.hasPending(), true);
    const entries = plain(journal.entries());
    assert.equal(entries.length, 1);
    assert.equal(entries[0].seq, 2);
    assert.equal(entries[0].actionId, 'undo-A');
    assert.equal(entries[0].delta.changes.length, 3, 'store field deltas, not a whole canvas');
    assert.equal('nodes' in entries[0], false);
    assert.deepEqual(plain(journal.replay(a)), { canvas: shadow, conflicts: [] }, 'compensation must force null/false, not OR/max them');
    assert.deepEqual(plain(journal.replay(shadow)), { canvas: shadow, conflicts: [] }, 'even an old server shadow cannot erase compensation intent');
    const observed = journal.entries();
    observed[0].delta.changes[0].after = 'observer corruption';
    sentA.seq = 0;
    assert.deepEqual(plain(journal.replay(a)).canvas, shadow, 'observation/checkpoint are detached copies');
    journal.acknowledge({ seq: 1 });
    assert.equal(journal.hasPending(), true, 'duplicate/old ack keeps higher seq');
    journal.acknowledge(journal.checkpoint());
    assert.equal(journal.hasPending(), false);
    journal.record(shadow, a);
    assert.deepEqual(plain(journal.checkpoint()), { seq: 3 }, 'seq never resets after ack');
});

test('pending collection leaves three-way merge each action in seq order without losing earlier unsent additions', () => {
    const api = core({ collections: true });
    const mergeValue = api.FireflyProjectCanvasMutations.mergeCanvasValue;
    const journal = api.createSmartCanvasPendingJournal({ mergeValue });
    const board = images => ({ nodes: [{ id: 'n', images }], connections: [] });
    const base = board([{ id: 'a' }]);
    const a = board([{ id: 'a' }, { id: 'b' }]);
    const b = board([{ id: 'a' }, { id: 'b' }, { id: 'c' }]);
    journal.record(base, a, { actionId: 'append-b' });
    journal.record(a, b, { actionId: 'append-c' });
    assert.equal(journal.entries()[0].delta.changes.length, 1, 'arrays are value leaves');
    assert.deepEqual(plain(journal.replay(board([{ id: 'a' }, { id: 'r' }]))), {
        canvas: board([{ id: 'a' }, { id: 'r' }, { id: 'b' }, { id: 'c' }]), conflicts: [],
    });
    assert.equal(journal.entries().length, 2, 'replay does not acknowledge anything');
});

test('remote deletion is not resurrected by residual field patches; explicit entity redo still restores identity', () => {
    const api = core();
    const empty = { nodes: [], connections: [] };
    const before = { nodes: [{ id: 'n', text: 'before', promptEditedByUser: false }], connections: [] };
    const after = { nodes: [{ id: 'n', text: 'after', promptEditedByUser: true }], connections: [] };
    const journal = api.createSmartCanvasPendingJournal();
    journal.record(before, after, { actionId: 'edit-deleted-node' });
    const applied = plain(journal.replay(empty));
    assert.deepEqual(applied.canvas, empty);
    assert.equal(applied.conflicts.length, 2);
    assert.equal(applied.conflicts[0].name, 'CanvasEntityMissing');
    assert.equal(applied.conflicts[0].seq, 1);
    assert.equal(applied.conflicts[0].actionId, 'edit-deleted-node');
    assert.deepEqual(applied.conflicts[0].path, ['nodes', { id: 'n' }, 'text']);
    assert.equal(journal.hasPending(), true);
    const deletion = api.smartCanvasActionDelta(before, empty);
    assert.deepEqual(plain(api.applySmartCanvasDelta(after, deletion)), { canvas: empty, conflicts: [] });
    assert.deepEqual(plain(api.applySmartCanvasDelta(empty, deletion, { direction: 'backward' })), { canvas: before, conflicts: [] });
    const creation = api.smartCanvasActionDelta(empty, after);
    assert.deepEqual(plain(api.applySmartCanvasDelta(empty, creation)), { canvas: after, conflicts: [] });
    assert.deepEqual(plain(api.applySmartCanvasDelta(after, creation, { direction: 'backward' })), { canvas: empty, conflicts: [] });
});

test('ambiguous legacy arrays return collection conflicts and keep pending instead of throwing or overwriting', () => {
    const api = core({ collections: true });
    const mergeValue = api.FireflyProjectCanvasMutations.mergeCanvasValue;
    const board = (images, text) => ({ nodes: [{ id: 'n', images, text }], connections: [] });
    const base = board([{ asset_id: 'shared', name: 'one' }, { asset_id: 'shared', name: 'two' }], 'before');
    const local = board([{ asset_id: 'shared', name: 'local' }, { asset_id: 'shared', name: 'two' }], 'human');
    const remote = board([{ asset_id: 'shared', name: 'one' }, { asset_id: 'shared', name: 'remote' }], 'server');
    assert.throws(() => mergeValue(base.nodes[0].images, local.nodes[0].images, remote.nodes[0].images), { name: 'CanvasCollectionConflict' }, 'the real injected helper detects ambiguity');
    const journal = api.createSmartCanvasPendingJournal({ mergeValue });
    journal.record(base, local, { actionId: 'ambiguous-images' });
    const entries = plain(journal.entries());
    const applied = plain(journal.replay(remote));
    assert.deepEqual(applied.canvas, board(remote.nodes[0].images, 'human'));
    assert.equal(applied.conflicts.length, 1);
    assert.equal(applied.conflicts[0].name, 'CanvasCollectionConflict');
    assert.deepEqual(applied.conflicts[0].path, ['nodes', { id: 'n' }, 'images']);
    assert.equal(applied.conflicts[0].seq, 1);
    assert.equal(applied.conflicts[0].actionId, 'ambiguous-images');
    assert.equal(typeof applied.conflicts[0].message, 'string');
    assert.deepEqual(plain(journal.entries()), entries, 'failure cannot consume or rewrite pending');
    assert.deepEqual(plain(journal.replay(remote)), applied, 'replay is deterministic and retryable');
});

test('connections use stable IDs or legacy endpoint/port identity, never positions or mutable edge content', () => {
    const api = core();
    const legacy = { from: 'a', to: 'b', fromPort: 'out', toPort: 'in', enabled: true };
    const parallel = { from: 'a', to: 'b', fromPort: 'out', toPort: 'other', enabled: true };
    const identified = { id: 'edge', from: 'b', to: 'c', enabled: true };
    const before = { nodes: [], connections: [legacy, parallel, identified] };
    const after = { nodes: [], connections: [{ ...identified, enabled: false }, { ...parallel }, { ...legacy, enabled: false }] };
    const delta = api.smartCanvasActionDelta(before, after);
    assert.equal(delta.changes.length, 2, 'reordering alone creates no changes');
    const added = { from: 'remote', to: 'b', enabled: true };
    const remote = { nodes: [], connections: [added, { ...parallel, remote: 42 }, { ...identified, remote: 8 }, { ...legacy, remote: 9 }] };
    assert.deepEqual(plain(api.applySmartCanvasDelta(remote, delta)), {
        canvas: { nodes: [], connections: [added, { ...parallel, remote: 42 }, { ...identified, enabled: false, remote: 8 }, { ...legacy, enabled: false, remote: 9 }] }, conflicts: [],
    });
    assert.deepEqual(plain(api.applySmartCanvasDelta(after, delta, { direction: 'backward' })).canvas.connections,
        [identified, parallel, legacy], 'inverse restores values without restoring array offsets');
});

test('ownership reads strict legacy defaults without repairing or coercing stored values', () => {
    const api = core();
    const defaults = { createdBy: 'user', agentNodeId: null, userTouchedAt: null, promptEditedByUser: false };
    const legacy = { id: 'legacy' };
    assert.deepEqual(plain(api.smartNodeOwnership(legacy)), defaults);
    assert.deepEqual(legacy, { id: 'legacy' }, 'read is not a migration');
    assert.deepEqual(plain(api.smartNodeOwnership(null)), defaults);
    assert.deepEqual(plain(api.smartNodeOwnership({ createdBy: 'agent', agentNodeId: ' agent-opaque ', userTouchedAt: 0, promptEditedByUser: true })),
        { createdBy: 'agent', agentNodeId: ' agent-opaque ', userTouchedAt: 0, promptEditedByUser: true });
    for(const invalid of [undefined, null, '', ' ', false, 9]) {
        assert.deepEqual(plain(api.smartNodeOwnership({ createdBy: 'agent', agentNodeId: invalid, userTouchedAt: 123.5, promptEditedByUser: true })),
            { ...defaults, userTouchedAt: 123.5, promptEditedByUser: true }, 'corrupt identity cannot discard valid protection values');
    }
    for(const invalid of [null, true, false, -1, NaN, Infinity, '123', [], {}]) {
        assert.equal(api.smartNodeOwnership({ userTouchedAt: invalid }).userTouchedAt, null);
    }
    for(const invalid of [1, 'true', {}, []]) assert.equal(api.smartNodeOwnership({ promptEditedByUser: invalid }).promptEditedByUser, false);
});

test('new ownership clones a fresh identity instead of inheriting source protection; only explicit agent context opts in', () => {
    const api = core();
    const source = { id: 'copy', created_at: 1, createdBy: 'agent', agentNodeId: 'old-agent', userTouchedAt: 12, promptEditedByUser: true, images: [{ id: 'image' }] };
    const start = Date.now();
    const user = api.smartNewNodeOwnership(source);
    assert.deepEqual(plain(api.smartNodeOwnership(user)), { createdBy: 'user', agentNodeId: null, userTouchedAt: null, promptEditedByUser: false });
    assert.ok(user.created_at >= start && user.created_at <= Date.now(), 'new identity uses epoch milliseconds, like node constructors');
    user.images[0].id = 'new';
    assert.equal(source.images[0].id, 'image', 'nested source must not be mutated');
    assert.equal(source.agentNodeId, 'old-agent');
    const agent = api.smartNewNodeOwnership(source, { createdBy: 'agent', agentNodeId: ' new-agent ' });
    assert.deepEqual(plain(api.smartNodeOwnership(agent)), { createdBy: 'agent', agentNodeId: ' new-agent ', userTouchedAt: null, promptEditedByUser: false });
    for(const invalid of [null, '', ' ', 2]) assert.throws(() => api.smartNewNodeOwnership(source, { createdBy: 'agent', agentNodeId: invalid }), { name: 'TypeError' });
    assert.throws(() => api.smartNewNodeOwnership(source, { createdBy: 'automatic' }), { name: 'TypeError' });
});

test('human edit stamps only actual content changes and preserves immutable creation identity and existing protection', () => {
    const api = core();
    const before = { id: 'n', createdBy: 'agent', agentNodeId: 'a', userTouchedAt: null, promptEditedByUser: false, text: 'before', runSettings: { model: 'one' } };
    const after = { ...before, text: '', createdBy: 'user', agentNodeId: null };
    const edited = api.smartHumanEditedNode(before, after, { prompt: true, at: 1700000000123 });
    assert.deepEqual(plain(edited), { ...before, text: '', userTouchedAt: 1700000000123, promptEditedByUser: true });
    assert.equal(after.createdBy, 'user', 'does not mutate supplied after');
    assert.equal(before.userTouchedAt, null);
    const moved = api.smartHumanEditedNode(edited, { ...edited, x: 0 }, { at: 1700000000999 });
    assert.equal(moved.promptEditedByUser, true, 'ordinary edits never clear true');
    assert.equal(moved.userTouchedAt, 1700000000999);
    const model = api.smartHumanEditedNode(before, { ...before, runSettings: { model: 'two' } }, { prompt: true, at: 0 });
    assert.equal(model.userTouchedAt, 0);
    assert.equal(model.promptEditedByUser, false, 'even declared prompt edits need changed prompt content');
    const noop = api.smartHumanEditedNode(edited, { ...edited, runSettings: { model: 'one' } }, { prompt: true, at: 1800000000000 });
    assert.deepEqual(plain(noop), plain(edited), 'same value never gets a fresh timestamp');
    const legacy = { id: 'old', text: 'same' };
    assert.deepEqual(plain(api.smartHumanEditedNode(legacy, { ...legacy }, { at: 123 })), legacy, 'no-op must not migrate legacy metadata');
    const start = Date.now();
    const timed = api.smartHumanEditedNode(legacy, { ...legacy, x: 1 });
    assert.ok(timed.userTouchedAt >= start && timed.userTouchedAt <= Date.now());
    assert.equal('createdBy' in timed, false, 'legacy creation presence must be preserved');
});

test('runtime/preview-only changes do not take human ownership; empty prompt structure changes do', () => {
    const api = core();
    const before = { id: 'n', userTouchedAt: 7, promptEditedByUser: false, images: [{ id: 'a', layout_w: 10 }], inputImage: { id: 'ref', layout_h: 20 }, variablePrompts: [''] };
    const after = { ...before, running: true, pendingTasks: 2, conversationSending: true, images: [{ id: 'a', layout_w: 100, layout_h: 80, _naturalSizeLoading: true }], inputImage: { id: 'ref', layout_h: 30 } };
    const untouched = api.smartHumanEditedNode(before, after, { prompt: true, at: 99 });
    assert.equal(untouched.userTouchedAt, 7);
    assert.equal(untouched.promptEditedByUser, false);
    assert.deepEqual(plain(untouched.images), after.images, 'runtime fields can still pass through without becoming human changes');
    const loop = api.smartHumanEditedNode(before, { ...before, variablePrompts: ['', ''] }, { prompt: true, at: 100 });
    assert.equal(loop.promptEditedByUser, true, 'adding an empty prompt field changes persisted structure');
    assert.equal(loop.userTouchedAt, 100);
    const media = api.smartHumanEditedNode(before, { ...before, images: [{ id: 'a', name: 'human' }] }, { at: 101 });
    assert.equal(media.userTouchedAt, 101);
    assert.equal(media.promptEditedByUser, false);
    for(const at of [null, false, '100', -1, Infinity]) assert.throws(() => api.smartHumanEditedNode(before, { ...before, x: 2 }, { at }), { name: 'TypeError' });
});

test('high-seq nested compensation preserves absent/null/false and reports replaced parents without dropping pending', () => {
    const api = core();
    const before = { nodes: [{ id: 'n', config: { touched: null, promptEdited: false, remove: false } }], connections: [] };
    const a = { nodes: [{ id: 'n', config: { touched: 123, promptEdited: true, added: null } }], connections: [] };
    const journal = api.createSmartCanvasPendingJournal();
    journal.record(before, a);
    const sent = journal.checkpoint();
    journal.record(a, before, { actionId: 'nested-undo' });
    journal.acknowledge(sent);
    const applied = plain(journal.replay(a));
    assert.deepEqual(applied, { canvas: before, conflicts: [] });
    assert.equal(Object.hasOwn(applied.canvas.nodes[0].config, 'added'), false, 'missing is not null/false');
    const replacedParent = { nodes: [{ id: 'n', config: null }], connections: [] };
    const conflicted = plain(journal.replay(replacedParent));
    assert.deepEqual(conflicted.canvas, replacedParent, 'a replaced container is not silently rewritten');
    assert.equal(conflicted.conflicts.length, 3, 'restoring null/false/present values conflicts; the deleted added child is already absent under null');
    assert.ok(conflicted.conflicts.every(item => item.name === 'CanvasPathConflict' && item.seq === 2));
    assert.equal(journal.entries()[0].seq, 2);
    assert.deepEqual(plain(journal.replay(a)), applied, 'a subsequent unambiguous replay still restores compensation');
});

test('concurrent collection edits without an injected resolver stay pending instead of silently replacing remote arrays', () => {
    const api = core();
    const board = items => ({ nodes: [{ id: 'n', items }], connections: [] });
    const base = board(['a']);
    const local = board(['a', 'b']);
    const remote = board(['a', 'r']);
    const journal = api.createSmartCanvasPendingJournal();
    journal.record(base, local);
    assert.deepEqual(plain(journal.replay(base)), { canvas: local, conflicts: [] }, 'nonconcurrent leaves do not need a merge policy');
    assert.deepEqual(plain(journal.replay(local)), { canvas: local, conflicts: [] }, 'already-applied leaves are idempotent');
    const applied = plain(journal.replay(remote));
    assert.deepEqual(applied.canvas, remote);
    assert.equal(applied.conflicts[0].name, 'CanvasCollectionConflict');
    assert.equal(journal.hasPending(), true);
});

test('ambiguous entity identities are rejected instead of collapsing duplicate IDs or legacy parallel edges', () => {
    const api = core();
    const empty = { nodes: [], connections: [] };
    const duplicateNodes = { nodes: [{ id: 'n', text: 'one' }, { id: 'n', text: 'two' }], connections: [] };
    const duplicateEdges = { nodes: [], connections: [{ from: 'a', to: 'b', enabled: true }, { from: 'a', to: 'b', enabled: false }] };
    const journal = api.createSmartCanvasPendingJournal();
    for(const ambiguous of [duplicateNodes, duplicateEdges]) {
        assert.throws(() => journal.record(empty, ambiguous), { name: 'CanvasCollectionConflict' });
        assert.deepEqual(plain(journal.checkpoint()), { seq: 0 }, 'failed record cannot allocate or partially append');
        assert.equal(journal.hasPending(), false);
    }
    const before = { nodes: [{ id: 'n', text: 'old' }], connections: [] };
    const after = { nodes: [{ id: 'n', text: 'new' }], connections: [] };
    const applied = plain(api.applySmartCanvasDelta(duplicateNodes, api.smartCanvasActionDelta(before, after)));
    assert.deepEqual(applied.canvas, duplicateNodes);
    assert.equal(applied.conflicts[0].name, 'CanvasCollectionConflict');
});

test('legacy edge intent survives server-assigned IDs when endpoints and ports still uniquely identify the edge', () => {
    const api = core();
    const legacy = { source: 'a', target: 'b', sourcePort: 'out', targetPort: 'in', enabled: true };
    const before = { nodes: [], connections: [legacy] };
    const after = { nodes: [], connections: [{ ...legacy, enabled: false }] };
    const delta = api.smartCanvasActionDelta(before, after);
    const remote = { nodes: [], connections: [{ id: 'server-edge', from: 'a', to: 'b', fromPort: 'out', toPort: 'in', enabled: true, role: 'remote' }] };
    assert.deepEqual(plain(api.applySmartCanvasDelta(remote, delta)), {
        canvas: { nodes: [], connections: [{ ...remote.connections[0], enabled: false }] }, conflicts: [],
    });
    const parallel = { nodes: [], connections: [remote.connections[0], { ...remote.connections[0], id: 'another' }] };
    const ambiguous = plain(api.applySmartCanvasDelta(parallel, delta));
    assert.deepEqual(ambiguous.canvas, parallel);
    assert.equal(ambiguous.conflicts[0].name, 'CanvasCollectionConflict', 'do not guess which explicitly identified parallel edge was legacy');
});

test('initial/missing collections still record entity deltas and inverse restores container presence without deleting remote additions', () => {
    const api = core();
    const before = {};
    const after = { nodes: [{ id: 'n', config: { value: null, enabled: false } }], connections: [] };
    const delta = api.smartCanvasActionDelta(before, after);
    assert.ok(delta.changes.some(change => change.path.length === 2 && change.path[0] === 'nodes' && change.path[1].id === 'n'), 'initial creation is an entity operation, not a nodes-array snapshot');
    assert.ok(delta.changes.every(change => !Array.isArray(change.after) || change.after.length === 0), 'collection-container markers contain no entities');
    assert.deepEqual(plain(api.applySmartCanvasDelta(before, delta)), { canvas: after, conflicts: [] });
    assert.deepEqual(plain(api.applySmartCanvasDelta(after, delta, { direction: 'backward' })), { canvas: before, conflicts: [] });
    const remote = { nodes: [{ id: 'r' }], connections: [] };
    const merged = plain(api.applySmartCanvasDelta(remote, delta));
    assert.deepEqual(merged, { canvas: { nodes: [{ id: 'r' }, after.nodes[0]], connections: [] }, conflicts: [] });
    const undone = plain(api.applySmartCanvasDelta(merged.canvas, delta, { direction: 'backward' }));
    assert.deepEqual(undone.canvas.nodes, [{ id: 'r' }], 'container restoration cannot erase unrelated remote entities');
    assert.equal(undone.conflicts[0].name, 'CanvasCollectionConflict');
});

test('field conflicts do not normalize a remote missing/null entity collection or recreate deleted entities', () => {
    const api = core();
    const before = { nodes: [{ id: 'n', promptEditedByUser: true, userTouchedAt: 12 }], connections: [] };
    const after = { nodes: [{ id: 'n', promptEditedByUser: false, userTouchedAt: null }], connections: [] };
    const journal = api.createSmartCanvasPendingJournal();
    journal.record(before, after);
    for(const remote of [{ connections: [] }, { nodes: null, connections: [] }]) {
        const applied = plain(journal.replay(remote));
        assert.deepEqual(applied.canvas, remote);
        assert.equal(applied.conflicts.length, 2);
        assert.ok(applied.conflicts.every(item => item.name === 'CanvasEntityMissing'));
        assert.equal(journal.hasPending(), true);
    }
});

test('unidentified nodes cannot be journaled using fabricated edge identity or array offsets', () => {
    const api = core();
    const journal = api.createSmartCanvasPendingJournal();
    for(const orphan of [{ text: 'no id' }, { id: null }, null]) {
        assert.throws(() => journal.record({ nodes: [], connections: [] }, { nodes: [orphan], connections: [] }), { name: 'CanvasCollectionConflict' });
        assert.deepEqual(plain(journal.entries()), []);
        assert.deepEqual(plain(journal.checkpoint()), { seq: 0 });
    }
});

test('new nested objects expose array leaves to three-way merge and keep remote nested siblings', () => {
    const api = core({ collections: true });
    const mergeValue = api.FireflyProjectCanvasMutations.mergeCanvasValue;
    const before = { nodes: [{ id: 'n' }], connections: [] };
    const after = { nodes: [{ id: 'n', config: { items: ['local'], enabled: false, reset: null, empty: {} } }], connections: [] };
    const delta = api.smartCanvasActionDelta(before, after);
    assert.ok(delta.changes.some(change => change.path.at(-1) === 'items' && Array.isArray(change.after)), 'a newly introduced parent cannot hide its arrays inside a wholesale object patch');
    const remote = { nodes: [{ id: 'n', config: { items: ['remote'], keep: 42 } }], connections: [] };
    assert.deepEqual(plain(api.applySmartCanvasDelta(remote, delta, { mergeValue })), {
        canvas: { nodes: [{ id: 'n', config: { items: ['remote', 'local'], keep: 42, enabled: false, reset: null, empty: {} } }], connections: [] }, conflicts: [],
    });
    assert.deepEqual(plain(api.applySmartCanvasDelta(after, delta, { direction: 'backward', mergeValue })), { canvas: before, conflicts: [] });
    const removed = api.smartCanvasActionDelta(after, before);
    assert.ok(removed.changes.some(change => change.path.at(-1) === 'items' && change.afterPresent === false));
    assert.deepEqual(plain(api.applySmartCanvasDelta(before, removed, { direction: 'backward', mergeValue })), { canvas: after, conflicts: [] });
});

test('inverse of newly present collections removes only local members and restores absence only without remote data', () => {
    const api = core({ collections: true });
    const mergeValue = api.FireflyProjectCanvasMutations.mergeCanvasValue;
    const board = node => ({ nodes: [{ id: 'n', ...node }], connections: [] });
    const local = { output_id: 'local', asset_id: 'a' };
    const remote = { output_id: 'remote', asset_id: 'b' };
    const imageDelta = api.smartCanvasActionDelta(board({}), board({ images: [local] }));
    assert.deepEqual(plain(api.applySmartCanvasDelta(board({ images: [local, remote] }), imageDelta,
        { direction: 'backward', mergeValue })), { canvas: board({ images: [remote] }), conflicts: [] });
    assert.deepEqual(plain(api.applySmartCanvasDelta(board({ images: [local] }), imageDelta,
        { direction: 'backward', mergeValue })), { canvas: board({}), conflicts: [] });
    for(const previous of [{}, { config: null }, { config: false }]) {
        const after = board({ config: { items: ['local'] } });
        const delta = api.smartCanvasActionDelta(board(previous), after);
        assert.deepEqual(plain(api.applySmartCanvasDelta(board({ config: { items: ['local', 'remote'], keep: 42 } }),
            delta, { direction: 'backward', mergeValue })),
            { canvas: board({ config: { items: ['remote'], keep: 42 } }), conflicts: [] });
        assert.deepEqual(plain(api.applySmartCanvasDelta(board({ config: { items: ['local'], keep: 42 } }),
            delta, { direction: 'backward', mergeValue })),
            { canvas: board({ config: { keep: 42 } }), conflicts: [] });
        assert.deepEqual(plain(api.applySmartCanvasDelta(after, delta, { direction: 'backward', mergeValue })),
            { canvas: board(previous), conflicts: [] }, 'empty inverse restores missing/null/false exactly');
    }
});

test('bulk coordinate inverse preserves every remote sibling with linear public selector reads', () => {
    const api = core();
    const measurements = [];
    for(const count of [500, 1000, 2000]) {
        const before = { nodes: Array.from({ length: count }, (_, i) => ({ id: `n${i}`, x: 0, y: 0 })), connections: [] };
        const after = { nodes: before.nodes.map(node => ({ ...node, x: 10, y: 20 })), connections: [] };
        const delta = api.smartCanvasActionDelta(before, after);
        let reads = 0;
        // Count reads at the public selector-input boundary, not elapsed time or
        // calls to implementation helpers. A field must not probe every entity.
        for(const change of delta.changes) change.path[1] = new Proxy(change.path[1], {
            get(target, key, receiver) { reads++; return Reflect.get(target, key, receiver); },
        });
        const remote = { nodes: [{ id: 'remote', x: 77, y: 88 }, ...after.nodes.map(node => ({ ...node, keep: 'remote' }))], connections: [] };
        const start = performance.now();
        const applied = api.applySmartCanvasDelta(remote, delta, { direction: 'backward' });
        const ms = performance.now() - start;
        console.log(`bulk inverse nodes=${count} fields=${delta.changes.length} ms=${ms.toFixed(3)} selectorReads=${reads}`);
        measurements.push({ count, reads, fields: delta.changes.length });
        assert.deepEqual(plain(applied), { canvas: { nodes: [{ id: 'remote', x: 77, y: 88 },
            ...before.nodes.map(node => ({ ...node, keep: 'remote' }))], connections: [] }, conflicts: [] });
    }
    // Exercise a single application with changing collection membership and
    // server-ID legacy fallback, including ambiguity that is resolved in-order.
    const legacy = { from: 'a', to: 'b', enabled: true };
    const empty = { nodes: [], connections: [] };
    const first = { nodes: [{ id: 'a', x: 0 }], connections: [{ ...legacy, id: 'server-a' }] };
    const second = { nodes: [{ id: 'b', x: 0 }], connections: [...first.connections, { ...legacy, id: 'server-b' }] };
    const edgeEdit = api.smartCanvasActionDelta({ connections: [legacy] }, { connections: [{ ...legacy, enabled: false }] });
    const final = { nodes: [{ id: 'b', x: 1 }], connections: [first.connections[0]] };
    const combined = { version: 1, changes: [
        ...api.smartCanvasActionDelta(empty, first).changes,
        ...api.smartCanvasActionDelta(first, second).changes,
        ...edgeEdit.changes,
        ...api.smartCanvasActionDelta(second, final).changes,
        ...edgeEdit.changes,
    ] };
    const applied = plain(api.applySmartCanvasDelta(empty, combined));
    assert.deepEqual(applied.canvas, { nodes: [{ id: 'b', x: 1 }], connections: [{ ...first.connections[0], enabled: false }] });
    assert.equal(applied.conflicts.length, 1, 'only the temporarily ambiguous legacy selector conflicts');
    assert.equal(applied.conflicts[0].name, 'CanvasCollectionConflict');
    for(const { count, reads, fields } of measurements) assert.ok(reads <= 20 * (count + fields),
        `${count} nodes exceeded the linear selector-read budget: ${reads}`);
});

test('object-to-null/false/missing transitions replay idempotently without phantom child-deletion conflicts', () => {
    const api = core();
    const before = { nodes: [{ id: 'n', config: { nested: { enabled: false }, items: ['a'] } }], connections: [] };
    for(const config of [null, false, undefined]) {
        const after = { nodes: [{ id: 'n', ...(config === undefined ? {} : { config }) }], connections: [] };
        const delta = api.smartCanvasActionDelta(before, after);
        assert.deepEqual(plain(api.applySmartCanvasDelta(after, delta)), { canvas: after, conflicts: [] });
        assert.deepEqual(plain(api.applySmartCanvasDelta(before, delta)), { canvas: after, conflicts: [] });
        assert.deepEqual(plain(api.applySmartCanvasDelta(after, delta, { direction: 'backward' })), { canvas: before, conflicts: [] });
    }
});
