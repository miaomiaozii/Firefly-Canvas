// #194 canvas-undo.js 行为单元测试。
//
// canvas-undo.js 是画布撤销/重做模块（#159 P2b 从 smart-canvas.js 抽离）：维护
// undoStack/redoStack 快照、UNDO_LIMIT=40 淘汰、按钮同步、媒体悬空引用过滤、
// projectOriginalDelete 令牌撤销。它引用主脚本的 11 个外部名（canvas/canvasState/
// toast/tr/render/scheduleSave/scheduleMediaArchive/cancelPendingMediaArchives/
// restoreSmartProjectOriginalUndo/sourceProjectId/canvasId）+ 加载期的 document（两个按钮）。
//
// 防的回归（探针 #154 已证实这些行为真实可观察，本文件用 harness 复刻并把它们钉死）：
//   - performUndo 的桩调用**顺序**（cancelPendingMediaArchives → render → scheduleSave →
//     toast:smart.toastUndone）——顺序错位说明恢复流程被改了；
//   - toast 的 i18n **key**（smart.toastUndone / toastNoUndo / toastRedone）——key 漂移会让
//     用户看到未翻译的错文案；
//   - UNDO_LIMIT=40 的 FIFO 淘汰、空栈/redo 边界、undoSuppressed 抑制门、
//     projectOriginalDelete 令牌走 restore 分支而非 render 分支；
//   - stripDeletedMediaFromUndoStacks 对已硬删媒体的快照清理（返回移除数量）。
//
// 每条断言都基于**可观察的副作用或返回值**（栈长度、按钮 disabled、桩调用序列、函数返回值），
// 不是「调用不抛错就算过」——那样会被自动桩让函数立刻返回而假绿（issue #194 点破的陷阱）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 把 canvas-undo.js 单独加载进假核心，注入它引用的 11 个外部名（手写桩，非自动桩）。
// 每个桩要么记录调用（calls），要么是可断言的真实行为；按钮用 harness.element 显式注册，
// 这样 syncUndoRedoButtons 设的 .disabled 是模块**真正写的 DOM 状态**，不是桩造的。
function loadUndoModule({ nodes = [], selectedId = '' } = {}) {
    const { ctx, harness } = createCanvasContext();
    const calls = [];
    const connections = [];
    ctx.canvasState = {
        nodes,
        connections,
        selectedId,
        selectedIds: [],
        selectedImage: {},
        activeComposerSubject: null,
        lastComposerNodeId: '',
        projectMediaLibrary: { items: [] },
    };
    ctx.canvas = {
        id: 'cv',
        nodes,
        connections,
        project: 'proj',
        project_id: 'proj',
    };
    ctx.sourceProjectId = 'proj';
    ctx.canvasId = 'cv';
    ctx.toast = (m) => calls.push('toast:' + m);
    ctx.tr = (k) => k;
    ctx.render = () => calls.push('render');
    ctx.scheduleSave = () => calls.push('scheduleSave');
    ctx.scheduleMediaArchive = (imgs) => calls.push('scheduleMediaArchive:' + (Array.isArray(imgs) ? imgs.length : 0));
    ctx.cancelPendingMediaArchives = () => calls.push('cancelPendingMediaArchives');
    // 令牌撤销分支：记录被委托的 projectOriginalDelete.assetId，证明走的是 restore 而非 render。
    ctx.restoreSmartProjectOriginalUndo = async (t) => {
        calls.push('restoreOriginal:' + ((t && t.projectOriginalDelete && t.projectOriginalDelete.assetId) || ''));
    };
    // 两个按钮必须在 loadModules 之前注册：模块加载期就 document.getElementById 捕获引用。
    harness.element('smartUndoButton');
    harness.element('smartRedoButton');
    loadModules(ctx, ['canvas-undo.js']);
    return { ctx, harness, calls };
}

const undoBtn = (harness) => harness.elementsById.get('smartUndoButton');
const redoBtn = (harness) => harness.elementsById.get('smartRedoButton');

test('pushUndo pushes a deep-copied snapshot, merges extra, enables the undo button, with no side-effect calls', () => {
    const { ctx, harness, calls } = loadUndoModule({ nodes: [{ id: 'n1', x: 10 }], selectedId: 'n1' });
    const pushUndo = globalFunction(ctx, 'pushUndo');

    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'fresh context: empty undoStack');

    pushUndo({ label: 'move-n1' });

    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'pushUndo grows undoStack by exactly 1');
    // 快照是**深拷贝**：撤销栈里的节点不能随 live 状态漂移，否则 undo 会恢复到「未来的状态」。
    ctx.canvasState.nodes[0].x = 999;
    assert.equal(evaluate(ctx, 'undoStack[0].nodes[0].x'), 10, 'snapshot is a deep copy, not a live reference');
    assert.equal(evaluate(ctx, 'undoStack[0].label'), 'move-n1', 'extra fields are Object.assigned onto the snapshot');
    assert.equal(evaluate(ctx, 'undoStack[0].selectedId'), 'n1', 'snapshot carries the selection at push time');
    assert.equal(evaluate(ctx, 'undoStack[0].selectedIds.length'), 0, 'selectedIds is sliced, independent of live state');
    // 按钮同步是模块写进真实（桩）DOM 的状态：有东西可撤 → undo 按钮启用；无 redo → redo 按钮禁用。
    assert.equal(undoBtn(harness).disabled, false, 'undo button enabled once there is something to undo');
    assert.equal(redoBtn(harness).disabled, true, 'redo button stays disabled (empty redoStack)');
    // pushUndo 只记录状态，不触发渲染/保存/提示——若这里出现任何桩调用说明流程被串改。
    assert.deepEqual(calls, [], 'pushUndo has no side-effect stub calls');
});

test('performUndo drains the stack, restores state, and calls stubs in the exact observed order (probe chain)', async () => {
    const { ctx, harness, calls } = loadUndoModule({ nodes: [{ id: 'n1', x: 10 }], selectedId: 'n1' });
    const pushUndo = globalFunction(ctx, 'pushUndo');
    const performUndo = globalFunction(ctx, 'performUndo');

    pushUndo({ label: 'before-mutation' });
    // 变更 live 状态，让撤销「真的恢复了东西」——否则测不出状态回滚。
    ctx.canvasState.nodes = [{ id: 'n1', x: 10 }, { id: 'n2', x: 20 }];
    ctx.canvasState.selectedId = 'n2';

    const redoBefore = evaluate(ctx, 'redoStack.length');
    await performUndo();

    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'undoStack drained after a single undo');
    assert.equal(evaluate(ctx, 'redoStack.length'), redoBefore + 1, 'a redo entry (pre-undo state) is pushed');
    // 状态真的回滚到快照：节点数、选中项都恢复。
    assert.equal(ctx.canvasState.nodes.length, 1, 'nodes restored to the snapshot count');
    assert.equal(ctx.canvasState.selectedId, 'n1', 'selectedId restored to the snapshot value');
    assert.equal(ctx.canvasState.activeComposerSubject, null, 'activeComposerSubject cleared on restore');
    assert.equal(ctx.canvasState.lastComposerNodeId, '', 'lastComposerNodeId cleared on restore');
    // 精确的桩调用序列（探针 #154 观测到的那条链）——顺序是回归敏感点。
    assert.deepEqual(calls, [
        'cancelPendingMediaArchives',
        'render',
        'scheduleSave',
        'toast:smart.toastUndone',
    ]);
    // 按钮同步：无东西可撤 → undo 禁用；有一条 redo → redo 启用。
    assert.equal(undoBtn(harness).disabled, true, 'undo button disabled after the last entry is consumed');
    assert.equal(redoBtn(harness).disabled, false, 'redo button enabled (one redo available)');
});

test('performUndo on an empty stack toasts smart.toastNoUndo and does NOT render/save', async () => {
    const { ctx, harness, calls } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const performUndo = globalFunction(ctx, 'performUndo');

    await performUndo();

    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'still empty');
    assert.equal(evaluate(ctx, 'redoStack.length'), 0, 'no redo entry created from a no-op undo');
    // cancelPendingMediaArchives 在 performUndo 顶部无条件执行；随后只弹一个「无操作」toast。
    // 关键反陷阱断言：这里**没有** render / scheduleSave / toast:smart.toastUndone——
    // 若空栈也走主恢复路径，就会白渲染并弹错文案。
    assert.deepEqual(calls, [
        'cancelPendingMediaArchives',
        'toast:smart.toastNoUndo',
    ]);
    assert.equal(undoBtn(harness).disabled, true, 'undo button stays disabled on an empty stack');
    assert.equal(redoBtn(harness).disabled, true, 'redo button stays disabled');
});

test('undoStack evicts the oldest entry FIFO once it exceeds UNDO_LIMIT (40)', () => {
    const { ctx } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const pushUndo = globalFunction(ctx, 'pushUndo');

    assert.equal(evaluate(ctx, 'UNDO_LIMIT'), 40, 'contract: the cap is exactly 40');
    for (let i = 1; i <= 41; i++) pushUndo({ label: 'p' + i });

    assert.equal(evaluate(ctx, 'undoStack.length'), 40, 'capped at UNDO_LIMIT, not 41');
    assert.equal(evaluate(ctx, 'undoStack[0].label'), 'p2', 'oldest (p1) evicted by FIFO shift');
    assert.equal(evaluate(ctx, 'undoStack[39].label'), 'p41', 'newest retained');
    assert.equal(evaluate(ctx, "undoStack.some(s => s.label === 'p1')"), false, 'p1 is gone from the stack');
});

test('undo then redo round-trips: redo restores the pre-undo state with the redone toast', async () => {
    const { ctx, calls } = loadUndoModule({ nodes: [{ id: 'n1' }], selectedId: 'n1' });
    const pushUndo = globalFunction(ctx, 'pushUndo');
    const performUndo = globalFunction(ctx, 'performUndo');
    const performRedo = globalFunction(ctx, 'performRedo');

    pushUndo({ label: 's0' });                                   // 快照 S0（1 节点）
    ctx.canvasState.nodes = [{ id: 'n1' }, { id: 'n2' }];        // 变更到 S1（2 节点）
    ctx.canvasState.selectedId = 'n2';

    await performUndo();
    assert.equal(ctx.canvasState.nodes.length, 1, 'undo reverted to the 1-node snapshot');

    calls.length = 0;                                            // 隔离 redo 自己的桩调用
    await performRedo();

    assert.equal(ctx.canvasState.nodes.length, 2, 'redo restored the 2-node state');
    assert.equal(ctx.canvasState.selectedId, 'n2', 'redo restored the pre-undo selection');
    assert.equal(evaluate(ctx, 'redoStack.length'), 0, 'redo stack drained');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'redo pushed the current state back onto undoStack');
    // redo 走自己的分支：toast 的 key 是 toastRedone（不是 toastUndone），且不调 scheduleMediaArchive（无丢失图片）。
    assert.deepEqual(calls, [
        'cancelPendingMediaArchives',
        'render',
        'scheduleSave',
        'toast:smart.toastRedone',
    ]);
});

test('stripDeletedMediaFromUndoStacks removes every snapshot still referencing a hard-deleted asset', () => {
    const { ctx } = loadUndoModule({ nodes: [{ id: 'n1', images: [{ asset_id: 'A1' }] }] });
    const pushUndo = globalFunction(ctx, 'pushUndo');
    const strip = globalFunction(ctx, 'stripDeletedMediaFromUndoStacks');

    pushUndo({ label: 'has-A1' });                                        // 快照引用 A1
    ctx.canvasState.nodes = [{ id: 'n1', images: [{ asset_id: 'A1' }, { asset_id: 'B2' }] }];
    pushUndo({ label: 'has-A1-and-B2' });                                 // 快照引用 A1（也引用 B2）
    ctx.canvasState.nodes = [{ id: 'n1', images: [{ asset_id: 'C3' }] }];
    pushUndo({ label: 'no-A1' });                                         // 快照只引用 C3
    assert.equal(evaluate(ctx, 'undoStack.length'), 3, 'three snapshots recorded');

    const removed = strip('A1');

    assert.equal(removed, 2, 'the two snapshots referencing A1 are removed (returned count)');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'only the non-referencing snapshot remains');
    assert.equal(evaluate(ctx, 'undoStack[0].label'), 'no-A1', 'the C3 snapshot survives the A1 strip');

    assert.equal(strip('C3'), 1, 'the C3 snapshot is removed next (returns 1)');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'stack fully drained of matching snapshots');
    assert.equal(strip('A1'), 0, 'stripping an already-removed asset removes nothing (returns 0)');
});

test('a projectOriginalDelete token is delegated to restoreSmartProjectOriginalUndo, NOT the render path', async () => {
    const { ctx, calls } = loadUndoModule({ nodes: [] });
    const pushUndo = globalFunction(ctx, 'pushUndo');
    const performUndo = globalFunction(ctx, 'performUndo');

    pushUndo({ projectOriginalDelete: { assetId: 'orig-1' } });

    await performUndo();

    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'the token snapshot is consumed');
    assert.equal(calls[0], 'cancelPendingMediaArchives', 'pending archives are still cancelled first');
    assert.ok(calls.includes('restoreOriginal:orig-1'), 'delegates to restoreSmartProjectOriginalUndo carrying the assetId');
    assert.ok(calls.includes('toast:smart.toastUndone'), 'still toasts the undone key for a token undo');
    // 令牌分支的关键区别：不走通用渲染/保存路径（由 restore 自己负责持久化与 UI）。
    assert.ok(!calls.includes('render'), 'does NOT run the generic render path for a token undo');
    assert.ok(!calls.includes('scheduleSave'), 'does NOT scheduleSave on the token path');
});

test('pushUndo is a no-op while undoSuppressed (a restore must not record a fresh undo entry)', () => {
    const { ctx } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const pushUndo = globalFunction(ctx, 'pushUndo');

    pushUndo({ label: 'recorded' });
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'baseline: one entry recorded');

    evaluate(ctx, 'undoSuppressed = true');
    pushUndo({ label: 'should-be-dropped' });
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'a suppressed push does not grow the stack');
    assert.equal(evaluate(ctx, "undoStack.some(s => s.label === 'should-be-dropped')"), false, 'suppressed entry is not present');

    evaluate(ctx, 'undoSuppressed = false');
    pushUndo({ label: 'recorded-again' });
    assert.equal(evaluate(ctx, 'undoStack.length'), 2, 'recording resumes once suppression is cleared');
});

// ── #251：undo/redo 栈跨刷新持久化（ops.db ADR 0021 租户之二，undo_snapshots）──────────────
// 防的回归：刷新后 undo 栈失忆。可观察行为：
//   - 栈变更 → 防抖合并 → 单条 PUT /api/project-library/{projectId}/undo-stack（整栈深拷贝）；
//   - 持久化失败（网络/5xx）静默吞掉，不炸画布；
//   - 页面加载时 GET 恢复（restorePersistedUndoStack 种回栈 + 按钮同步），本地栈非空不覆盖；
//   - 非项目画布（无 project_id）不触发任何请求。

// 宿主侧等一拍，让 vm 里的 async persist 跑完（fetch 是宿主注入的 stub，微任务链跨 realm）。
const tick = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

test('#251: rapid stack mutations coalesce into a single debounced PUT carrying the whole stack', async () => {
    const { ctx, harness } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const requests = [];
    ctx.fetch = async (url, options) => {
        requests.push({ url, method: options && options.method, body: options && options.body });
        return { ok: true, json: async () => ({}) };
    };
    const pushUndo = globalFunction(ctx, 'pushUndo');

    pushUndo({ label: 'p1' });
    pushUndo({ label: 'p2' });
    // 两次快速变更在防抖窗口内：只有一个定时器存活（前一个被 clearTimeout）。
    const fired = harness.advanceTimers();
    assert.equal(fired, 1, 'two rapid pushes coalesce into one debounced timer');
    await tick();

    assert.equal(requests.length, 1, 'exactly one PUT in flight');
    assert.equal(requests[0].method, 'PUT');
    assert.ok(requests[0].url.includes('/api/project-library/proj/undo-stack'), `project-scoped URL, got ${requests[0].url}`);
    const payload = JSON.parse(requests[0].body);
    assert.deepEqual(fromContext(payload), {
        undo: [
            { nodes: [{ id: 'n1' }], connections: [], selectedId: '', selectedIds: [], selectedImage: {}, label: 'p1' },
            { nodes: [{ id: 'n1' }], connections: [], selectedId: '', selectedIds: [], selectedImage: {}, label: 'p2' },
        ],
        redo: [],
    }, 'payload is a deep copy of the stacks at fire time (both entries, redo empty)');
});

test('#251: persist failures (network error / 5xx) are swallowed, canvas keeps working', async () => {
    const { ctx } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const persistUndoStackNow = globalFunction(ctx, 'persistUndoStackNow');

    ctx.fetch = async () => { throw new Error('network down'); };
    await persistUndoStackNow('proj');   // 不得抛出

    ctx.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
    await persistUndoStackNow('proj');   // 5xx 也不得抛出

    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'stack untouched by the persist attempts');
});

test('#251: restorePersistedUndoStack seeds the stacks on page load and syncs buttons', async () => {
    const { ctx, harness } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const serverUndo = [
        { nodes: [{ id: 'n0' }], connections: [], selectedId: '', selectedIds: [], selectedImage: {} },
        { nodes: [{ id: 'n0' }, { id: 'n1' }], connections: [{ from: 'n0', to: 'n1' }], selectedId: 'n1', selectedIds: ['n1'], selectedImage: {} },
    ];
    const serverRedo = [{ nodes: [{ id: 'n1' }], connections: [], selectedId: 'n1', selectedIds: ['n1'], selectedImage: {} }];
    const urls = [];
    ctx.fetch = async (url) => {
        urls.push(url);
        return { ok: true, json: async () => ({ project_id: 'proj', undo: serverUndo, redo: serverRedo, updated_at: '2026-01-01T00:00:00Z' }) };
    };
    const restorePersistedUndoStack = globalFunction(ctx, 'restorePersistedUndoStack');

    await restorePersistedUndoStack();

    assert.equal(urls.length, 1, 'one GET on restore');
    assert.ok(urls[0].includes('/api/project-library/proj/undo-stack'), `project-scoped GET, got ${urls[0]}`);
    assert.equal(evaluate(ctx, 'undoStack.length'), 2, 'undo stack seeded from the server');
    assert.equal(evaluate(ctx, 'redoStack.length'), 1, 'redo stack seeded from the server');
    assert.deepEqual(fromContext(JSON.parse(JSON.stringify(serverUndo))), fromContext(evaluate(ctx, 'undoStack')), 'entries kept verbatim (dangling-media guards run later, in performUndo)');
    assert.equal(undoBtn(harness).disabled, false, 'undo button enabled after restore');
    assert.equal(redoBtn(harness).disabled, false, 'redo button enabled after restore');
});

test('#251: restore is a no-op when local stacks are non-empty (project switch keeps in-flight state)', async () => {
    const { ctx } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    const pushUndo = globalFunction(ctx, 'pushUndo');
    const restorePersistedUndoStack = globalFunction(ctx, 'restorePersistedUndoStack');

    pushUndo({ label: 'local' });
    let fetchCalls = 0;
    ctx.fetch = async () => {
        fetchCalls++;
        return { ok: true, json: async () => ({ undo: [{ nodes: [{ id: 'server' }] }], redo: [] }) };
    };

    await restorePersistedUndoStack();

    assert.equal(fetchCalls, 0, 'no request when the local stack is already non-empty');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'local stack untouched');
    assert.equal(evaluate(ctx, "undoStack[0].label"), 'local', 'no server entry leaked in');
});

test('#251: restore no-ops silently on 404 (project has no ops.db yet)', async () => {
    const { ctx, harness } = loadUndoModule({ nodes: [{ id: 'n1' }] });
    ctx.fetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
    const restorePersistedUndoStack = globalFunction(ctx, 'restorePersistedUndoStack');

    await restorePersistedUndoStack();

    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'stack stays empty');
    assert.equal(undoBtn(harness).disabled, true, 'buttons stay disabled');
});

test('#251: non-project canvas (no project_id) never touches the network', async () => {
    const { ctx, harness } = createCanvasContext();
    const connections = [];
    ctx.canvasState = { nodes: [{ id: 'n1' }], connections, selectedId: '', selectedIds: [], selectedImage: {}, activeComposerSubject: null, lastComposerNodeId: '', projectMediaLibrary: { items: [] } };
    ctx.canvas = { id: 'classic-canvas', nodes: ctx.canvasState.nodes, connections };   // 无 project / project_id
    ctx.sourceProjectId = '';
    ctx.canvasId = 'classic-canvas';
    ctx.toast = () => {};
    ctx.tr = (k) => k;
    ctx.render = () => {};
    ctx.scheduleSave = () => {};
    ctx.scheduleMediaArchive = () => {};
    ctx.cancelPendingMediaArchives = () => {};
    ctx.restoreSmartProjectOriginalUndo = async () => {};
    harness.element('smartUndoButton');
    harness.element('smartRedoButton');
    let fetchCalls = 0;
    ctx.fetch = async () => { fetchCalls++; return { ok: true, json: async () => ({}) }; };
    loadModules(ctx, ['canvas-undo.js']);
    const pushUndo = globalFunction(ctx, 'pushUndo');
    const restorePersistedUndoStack = globalFunction(ctx, 'restorePersistedUndoStack');

    pushUndo({ label: 'classic' });
    harness.advanceTimers();   // 防抖窗口走完也不得发出请求
    await restorePersistedUndoStack();
    await tick();

    assert.equal(fetchCalls, 0, 'no persist/restore request for a classic (non-project) canvas');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'in-memory undo still works locally');
});
