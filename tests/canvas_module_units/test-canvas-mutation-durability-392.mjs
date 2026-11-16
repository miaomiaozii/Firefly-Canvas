// #392 A2 2a: real mutation flush; only transport/durability I/O is faked.
import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {flushProjectCanvasMutations, snapshotFromCanvas} =
    require('../../static/js/project-canvas-mutations.js');
const copy = value => JSON.parse(JSON.stringify(value));

function freeze(value){
    if(value && typeof value === 'object'){
        Object.values(value).forEach(freeze);
        Object.freeze(value);
    }
    return value;
}

function request(){
    const original = {
        project_id:'durability-392', revision:4, updated_at:400,
        title:'Durability', icon:'sparkles', history:[], connections:[],
        nodes:[{id:'prompt', type:'smart-prompt', text:'before'}],
    };
    const node = {...original.nodes[0], text:'saved draft'};
    return freeze({
        canvas:{...original, nodes:[node]},
        baseline:snapshotFromCanvas(original),
        mutations:[{op:'upsert_node', node}],
    });
}

function latestCanvas(batch){
    return {...copy(batch.canvas), revision:5, updated_at:500};
}

test('lost PATCH acknowledgement is saved only after identical conflict state becomes durable', async () => {
    const batch = request();
    let accepted;
    let durable;
    const options = {
        ...batch,
        send:async revision => {
            assert.equal(revision, 4, 'identical accepted content must not be patched again');
            if(!accepted){
                accepted = latestCanvas(batch);
                throw new Error('PATCH acknowledgement lost');
            }
            return {status:409, canvas:copy(accepted)};
        },
        confirmSaved:async (_response, canvas) => {
            durable = copy(canvas);
            return true;
        },
    };
    await assert.rejects(flushProjectCanvasMutations(options), /acknowledgement lost/);
    const result = await flushProjectCanvasMutations(options);
    assert.deepEqual({saved:result.saved, durable}, {saved:true, durable:accepted});
});

test('failed no-write confirmation leaves caller intent unacknowledged and replayable', async () => {
    const batch = request();
    const before = copy(batch);
    const latest = latestCanvas(batch);
    const response = {status:409, canvas:latest};
    const result = await flushProjectCanvasMutations({
        ...batch,
        send:async () => response,
        confirmSaved:async () => false,
    });
    assert.equal(result.saved, false);
    assert.deepEqual(batch, before, 'caller pending mutations and request baseline must remain intact');
    assert.equal(result.canvas.revision, 5);
    assert.deepEqual(result.serverCanvas, latest);
    assert.deepEqual(result.baseline, snapshotFromCanvas(latest));
    assert.deepEqual(result.response, response);
});

test('throwing no-write confirmation rejects without consuming caller pending intent', async () => {
    const batch = request();
    const before = copy(batch);
    const failure = new Error('durability transport failed');
    await assert.rejects(flushProjectCanvasMutations({
        ...batch,
        send:async () => ({status:409, canvas:latestCanvas(batch)}),
        confirmSaved:async () => { throw failure; },
    }), error => error === failure);
    assert.deepEqual(batch, before);
});

test('retained request can retry failed confirmation without rewriting accepted content', async () => {
    const batch = request();
    const before = copy(batch);
    let diskAvailable = false;
    let durable;
    const options = {
        ...batch,
        send:async revision => {
            assert.equal(revision, 4);
            return {status:409, canvas:latestCanvas(batch)};
        },
        confirmSaved:async (_response, canvas) => {
            if(!diskAvailable) return false;
            durable = copy(canvas);
            return true;
        },
    };
    const failed = await flushProjectCanvasMutations(options);
    assert.equal(failed.saved, false);
    diskAvailable = true;
    const retried = await flushProjectCanvasMutations(options);
    assert.equal(retried.saved, true);
    assert.deepEqual(durable, latestCanvas(batch));
    assert.deepEqual(retried.mutations, []);
    assert.deepEqual(batch, before);
});

test('an initially empty batch remains a no-I/O success rather than a new durability request', async () => {
    const canvas = freeze(latestCanvas(request()));
    const result = await flushProjectCanvasMutations({
        canvas,
        baseline:freeze(snapshotFromCanvas(canvas)),
        mutations:freeze([]),
        send:async () => { throw new Error('empty batch must not send'); },
        confirmSaved:async () => { throw new Error('empty batch must not confirm'); },
    });
    assert.deepEqual(result, {
        saved:true, canvas, mutations:[], conflictRetries:0, confirmedWithoutWrite:true,
    });
});

test('no-write acknowledgement waits for confirmation of the latest server revision', async () => {
    const batch = request();
    const before = copy(batch);
    const latest = {
        ...latestCanvas(batch), revision:9, updated_at:900,
        nodes:[...copy(batch.canvas.nodes), {id:'remote-only', type:'smart-prompt', text:'peer edit'}],
    };
    let release;
    const diskReady = new Promise(resolve => { release = resolve; });
    let durableRevision = 4;
    let requiredRevision;
    let settled = false;
    const flushing = flushProjectCanvasMutations({
        ...batch,
        send:async () => ({status:409, canvas:latest}),
        confirmSaved:async (accepted, canvas) => {
            requiredRevision = accepted.revision;
            assert.equal(canvas.revision, 9);
            await diskReady;
            return durableRevision >= requiredRevision;
        },
    });
    flushing.then(() => { settled = true; });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(settled, false, 'save must not acknowledge while disk confirmation is pending');
    assert.equal(requiredRevision, 9, '409 response itself has no top-level revision');
    durableRevision = 9;
    release();
    const result = await flushing;
    assert.equal(result.saved, true);
    assert.equal(result.canvas.revision, 9);
    assert.equal(result.conflictRetries, 1);
    assert.deepEqual(result.serverCanvas, latest);
    assert.deepEqual(result.baseline, snapshotFromCanvas(latest));
    assert.deepEqual(result.response, {status:409, canvas:latest});
    assert.deepEqual(batch, before);
});

test('library callers without a confirmation adapter retain legacy no-write success', async () => {
    const batch = request();
    const latest = latestCanvas(batch);
    const result = await flushProjectCanvasMutations({
        ...batch,
        send:async revision => {
            assert.equal(revision, 4, 'legacy no-write resolution must not add another PATCH');
            return {status:409, canvas:latest};
        },
    });
    assert.equal(result.saved, true);
    assert.equal(result.confirmedWithoutWrite, true);
    assert.equal(result.canvas.revision, 5);
    assert.deepEqual(result.mutations, []);
    assert.deepEqual(result.serverCanvas, latest);
    assert.deepEqual(result.response, {status:409, canvas:latest});
});
