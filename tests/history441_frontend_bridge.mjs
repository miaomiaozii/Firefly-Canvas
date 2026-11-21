import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createInterface} from 'node:readline';
import {createCanvasContext, loadModules, evaluate, globalFunction, fromContext} from './canvas_module_units/harness.mjs';

// #441：网络适配器只转交真实 ASGI 请求与响应，不替换任务轮询、历史账本或项目持久化。
const input = createInterface({input:process.stdin});
const pending = new Map();
let sequence = 0, initialize;
const initialized = new Promise(resolve=>{ initialize = resolve; });
input.on('line', line=>{
    const message = JSON.parse(line);
    if(message.initial) initialize(message.initial);
    else { const resolve = pending.get(message.id); pending.delete(message.id); resolve(message); }
});
const initial = await initialized;
const {ctx, harness} = createCanvasContext();
harness.element('toast');
loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-media-history.js', 'canvas-generation-tasks.js']);
const source = readFileSync('static/js/smart-canvas.js', 'utf8');
for(const name of ['acceptProjectGenerationRegistration', 'copyMediaSizeFields', 'mediaKindForItem',
    'stripImageGenerationMeta', 'parseSizeAspect', 'apiErrorMessage',
    'isFileMediaItem', 'isTextMediaItem', 'isAudioMediaItem', 'isVideoMediaItem']){
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, name);
    const asyncStart = source.slice(start - 6, start) === 'async ' ? start - 6 : start;
    evaluate(ctx, source.slice(asyncStart, source.indexOf('\n}', start) + 2));
}
ctx.__initial = initial.canvas;
evaluate(ctx, 'canvas = hydrateProjectMediaReferences(__initial); settings = {engine:"api", apiKind:"image"};');
ctx.canvasState.nodes = evaluate(ctx, 'canvas.nodes');
ctx.canvasId = initial.project;
ctx.sourceProjectId = initial.project;
ctx.persistedCoreSnapshot = {nodes:{}};
ctx.smartSaveButton = null;
ctx.smartSettingsForNode = ()=>({engine:'api', apiKind:'image', provider_id:'history441-external', model:'history441-image'});
// 本切片不验证 DOM、音效及保存调度；真正落盘与重开均由 Python 侧产品 API 验证。
ctx.render = ()=>{};
ctx.scheduleSave = ()=>{};
ctx.scheduleStateSave = ()=>{};
ctx.requestSmartEdgeReveal = ()=>{};
ctx.setProjectSaveState = ()=>{};
ctx.playGenerationCompleteSound = ()=>{};
ctx.mediaNodeDefaultScale = ()=>1;
ctx.smartGroupContainingNode = ()=>null;
ctx.trf = key=>key;
ctx.sleep = async()=>{};
ctx.fetch = async (url, options={})=>{
    const id = ++sequence;
    const promise = new Promise(resolve=>pending.set(id, resolve));
    process.stdout.write(JSON.stringify({id, url, options}) + '\n');
    const response = await promise;
    return {ok:response.status >= 200 && response.status < 300, status:response.status,
        json:async()=>JSON.parse(response.body), text:async()=>response.body};
};
const tick = ()=>new Promise(resolve=>setTimeout(resolve, 2));
async function settle(predicate){
    for(let index=0; index<400 && !predicate(); index++){ harness.advanceTimers(); await tick(); }
    assert.ok(predicate(), '真实任务轮询应在测试预算内结束');
}
const node = ctx.canvasState.nodes.find(item=>item.id === 'result');
globalFunction(ctx, 'ensureSmartPendingHistoryLogs')(node);
const before = fromContext(evaluate(ctx, 'canvas.history'));
const foreignPending = before.find(record=>(record.task_id || record.request?.task_id) === initial.pendingId);
assert.ok(foreignPending);
let settled = false;
const polling = globalFunction(ctx, 'resumeSmartPendingNode')(node, {
    run:{nodeId:'result', run_id:'history441-shared-run'}, taskIds:[initial.failedId, initial.successId],
}).finally(()=>{ settled = true; });
await settle(()=>settled);
await polling;
let history = fromContext(evaluate(ctx, 'canvas.history'));
assert.equal(history.length, 3);
assert.deepEqual(history.filter(record=>record.status === 'pending'), [foreignPending]);
const failed = history.find(record=>record.task_id === initial.failedId);
assert.equal(failed.status, 'failed');
assert.equal(failed.error, 'history441 服务商拒绝');
assert.equal(history.find(record=>record.task_id === initial.successId).run_id, 'history441-shared-run');
assert.equal(node.failedGenerationTasks[0].taskId, initial.failedId);
const originalOutput = fromContext(node.images[0]);
assert.ok(originalOutput.output_id);
let retrySettled = false;
const retrying = globalFunction(ctx, 'retryFailedGeneration')('result').finally(()=>{ retrySettled = true; });
await settle(()=>retrySettled);
await retrying;
assert.equal(evaluate(ctx, 'canvas.history').filter(record=>record.status === 'succeeded').length, 2);
history = fromContext(evaluate(ctx, 'canvas.history'));
assert.equal(history.length, 4);
assert.deepEqual(history.find(record=>record.task_id === initial.failedId), failed);
assert.deepEqual(history.filter(record=>record.status === 'pending'), [foreignPending]);
assert.deepEqual(fromContext(node.pendingTasks.map(task=>task.taskId)), [initial.pendingId]);
assert.equal(node.images.length, 2);
assert.ok(node.images.some(item=>item.output_id === originalOutput.output_id));
const retried = history.find(record=>record.status === 'succeeded' && record.task_id !== initial.successId);
assert.ok(retried.task_id !== initial.failedId);
assert.equal(retried.run_id, 'history441-shared-run');
assert.equal(history.filter(record=>record.task_id === initial.failedId).length, 1);
process.stdout.write(JSON.stringify({done:true, retryId:retried.task_id, history}) + '\n');
input.close();
