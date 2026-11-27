import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasContext, evaluate, fromContext, loadModules} from './harness.mjs';

function connect(host) {
    const {ctx, harness} = createCanvasContext();
    Object.assign(ctx, {atob, File});
    loadModules(ctx, ['canvas-chat-capture.js']);
    ctx.FireflyChatCapture.connect(host);
    const receive = harness.windowListeners.get('message')[0];
    const event = (body, overrides = {}) => {
        let response;
        let done;
        const closed = new Promise(resolve => { done = resolve; });
        receive({isTrusted:true, source:evaluate(ctx, 'window'), origin:ctx.location.origin,
            data:{channel:'firefly.chat-capture.v1', site:'chatgpt.com', ...body},
            ports:[{postMessage(value) { response = fromContext(value); }, close() { done(response); }}], ...overrides});
        return {closed, response:() => response};
    };
    return event;
}

test('only trusted same-window messages with bounded exact data reach capture actions', async () => {
    let changes = 0;
    const send = connect({project:() => 'p1', viewCenter:() => ({x:0, y:0}), occupied:() => [],
        findSpot:() => ({x:0, y:0, exhausted:false}), addTextNode:() => { changes++; return {id:'text'}; }, save() {}});
    const text = {action:'new-text', text:'capture me'};
    const rejected = [send(text, {isTrusted:false}), send(text, {source:{}}), send(text, {origin:'https://example.test'}),
        send(text, {ports:[]}), send({...text, site:'sub.chatgpt.com'}), send({...text, sourceUrl:'https://chatgpt.com/private'}),
        send({...text, text:'x'.repeat(20001)})];
    assert.deepEqual(await send(text).closed, {ok:true});
    assert.equal(changes, 1);
    assert.ok(rejected.every(attempt => attempt.response() === undefined));
});

test('an in-flight image upload cannot create a node in the newly selected project', async () => {
    let project = 'p1';
    let settleUpload;
    let uploadStarted;
    const started = new Promise(resolve => { uploadStarted = resolve; });
    const binary = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');
    const send = connect({project:() => project, uploadImage(file) {
        uploadStarted(file);
        return new Promise(resolve => { settleUpload = resolve; });
    }, addImageNode() { assert.fail('capture must not create a node in the new project'); }, save() { assert.fail('unexpected save'); }});
    const pending = send({action:'new-image', mime:'image/gif', bytes:binary.toString('base64')});
    const file = await started;
    assert.deepEqual(Buffer.from(await file.arrayBuffer()), binary);
    project = 'p2';
    settleUpload([{url:'/original.gif'}]);
    assert.deepEqual(await pending.closed, {ok:false, error:'PROJECT_CHANGED'});
});
