// #272 canvas-annotations.js 单元测试：画布批注层（可持久画布对象）的锚定/恢复/撤销纯逻辑。
//
// 防的回归（全部断言可观察输出）：
//   - 锚定语义：气泡按节点内归一化坐标 x/y∈[0,1] 定位（left/top %），文本截断与 XSS 转义；
//     status=converted 时加 anno-converted 样式类（runAll 转化后只读展示，下一票写入）；
//   - 刷新恢复：restorePersistedAnnotations 拉 /annotations → 过滤脏项进 canvasState.annotations，
//     404/网络失败 = 空批注（不抛错，回到「无批注」旧行为）；
//   - undo 接线：删除批注先压 undo 快照（含 annotations），失败回滚本地状态并弹掉快照；
//   - API URL：project_id 走 encodeURIComponent，无后缀时无尾部斜杠。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, evaluate, fromContext } from './harness.mjs';

function loadAnnotations() {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-core.js',
        'canvas-state.js',
        'canvas-undo.js',
        'canvas-annotations.js',
    ]);
    // canvas 是 canvas-core.js 顶层 let（词法环境，非全局属性）→ 必须 evaluate 赋值。
    evaluate(ctx, 'canvas = { project_id: "p1" }');
    ctx.toast = () => {};
    return { ctx, harness };
}

test('annoBubbleHtml: anchored by normalized node-relative coords (x/y → %)', () => {
    const { ctx } = loadAnnotations();
    const html = evaluate(ctx, `annoBubbleHtml({id:7, node_id:'n1', text:'把背景改成红色', x:0.25, y:0.5, status:'pending'})`);
    assert.match(html, /left:25\.0?%|left:25%/);
    assert.match(html, /top:50%/);
    assert.match(html, /data-anno-id="7"/);
    assert.match(html, /把背景改成红色/);
    assert.doesNotMatch(html, /anno-converted/);
});

test('annoBubbleHtml: text escaped + truncated for long text', () => {
    const { ctx } = loadAnnotations();
    const html = evaluate(ctx, `annoBubbleHtml({id:8, node_id:'n1', text:'<script>alert(1)</script>', x:0.5, y:0.2, status:'pending'})`);
    assert.doesNotMatch(html, /<script>alert/);
    assert.match(html, /&lt;script&gt;/);
    const long = 'x'.repeat(60);
    const html2 = evaluate(ctx, `annoBubbleHtml({id:9, node_id:'n1', text:${JSON.stringify(long)}, x:0.5, y:0.2, status:'pending'})`);
    // 气泡文本截断（title 属性保留全文供查看，不截断）
    assert.match(html2, /anno-bubble-text">x{23}…</);
    assert.doesNotMatch(html2, /anno-bubble-text">x{24}/);
});

test('annoBubbleHtml: converted status gets read-only style class', () => {
    const { ctx } = loadAnnotations();
    const html = evaluate(ctx, `annoBubbleHtml({id:10, node_id:'n1', text:'ok', x:0.5, y:0.2, status:'converted'})`);
    assert.match(html, /anno-converted/);
});

test('annoApiUrl: project id escaped, no trailing slash without suffix', () => {
    const { ctx } = loadAnnotations();
    assert.equal(evaluate(ctx, `annoApiUrl('p/x', '')`), '/api/project-library/p%2Fx/annotations');
    assert.equal(evaluate(ctx, `annoApiUrl('p1', '3')`), '/api/project-library/p1/annotations/3');
});

test('annoState: lazily initializes canvasState.annotations once', () => {
    const { ctx } = loadAnnotations();
    const first = evaluate(ctx, 'annoState()');
    assert.deepEqual(fromContext(first), []);
    const same = evaluate(ctx, 'annoState() === canvasState.annotations');
    assert.equal(same, true);
});

test('restorePersistedAnnotations: fetches /annotations and filters dirty items into state', async () => {
    const { ctx } = loadAnnotations();
    const items = [
        { id: 1, node_id: 'n1', text: 'a', x: 0.1, y: 0.2, status: 'pending' },
        { id: 2, node_id: 'n2', text: 'b', x: 0.3, y: 0.4, status: 'pending' },
        { id: 3, text: 'missing node_id' },
        'garbage',
    ];
    ctx.fetch = async (url, opts) => {
        assert.equal(url, '/api/project-library/p1/annotations');
        assert.equal(opts, undefined);
        return { ok: true, json: async () => ({ project_id: 'p1', items }) };
    };
    await evaluate(ctx, 'restorePersistedAnnotations()');
    const state = fromContext(evaluate(ctx, 'canvasState.annotations'));
    assert.deepEqual(state, [items[0], items[1]]);
});

test('restorePersistedAnnotations: 404 (no ops.db) and network failure = empty state, no throw', async () => {
    const { ctx } = loadAnnotations();
    ctx.fetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
    await evaluate(ctx, 'restorePersistedAnnotations()');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.annotations')), []);
    ctx.fetch = async () => { throw new Error('network down'); };
    await evaluate(ctx, 'restorePersistedAnnotations()');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.annotations')), []);
});

test('deleteAnnotation: pushes undo snapshot first, splices state, calls DELETE; failure rolls back', async () => {
    const { ctx, harness } = loadAnnotations();
    const a1 = { id: 1, node_id: 'n1', text: 'a', x: 0.1, y: 0.2, status: 'pending' };
    const a2 = { id: 2, node_id: 'n2', text: 'b', x: 0.3, y: 0.4, status: 'pending' };
    evaluate(ctx, 'canvasState.annotations = JSON.parse(JSON.stringify([A1, A2]))'.replace('A1', JSON.stringify(a1)).replace('A2', JSON.stringify(a2)));

    let deleteCalled = null;
    ctx.fetch = async (url, opts) => {
        deleteCalled = { url, method: opts.method };
        return { ok: true, json: async () => ({}) };
    };
    await evaluate(ctx, 'deleteAnnotation(1)');
    assert.deepEqual(deleteCalled, { url: '/api/project-library/p1/annotations/1', method: 'DELETE' });
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.annotations')), [a2]);
    const undoCount = evaluate(ctx, 'undoStack.length');
    assert.equal(undoCount, 1);
    const snapAnnotations = fromContext(evaluate(ctx, 'undoStack[undoStack.length - 1].annotations'));
    assert.deepEqual(snapAnnotations, [a1, a2]);  // 撤销能恢复删除前的批注

    // 失败回滚：DELETE 500 → 本地状态恢复为删除前（[a2]） + 刚压的快照弹掉
    ctx.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
    await evaluate(ctx, 'deleteAnnotation(2)');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.annotations')), [a2]);
    assert.equal(evaluate(ctx, 'undoStack.length'), 1);
});

test('renderAnnotationLayer: no-op without DOM (stub world) does not throw', () => {
    const { ctx, harness } = loadAnnotations();
    harness.element('world');
    evaluate(ctx, 'canvasState.annotations = [{id:1, node_id:"n1", text:"a", x:0.1, y:0.2, status:"pending"}]');
    assert.doesNotThrow(() => evaluate(ctx, 'renderAnnotationLayer()'));
});
