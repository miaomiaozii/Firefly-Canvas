// Run in an owned, offline-backed production canvas page; no TUI functions are replaced.
import assert from 'node:assert/strict';

async function api(page, actor, path, body) {
    return page.evaluate(async ({ actor, path, body }) => {
        const project = new URL(location.href).searchParams.get('project');
        const response = await fetch(`/api/projects/${encodeURIComponent(project)}/${path.replace('{actor}', encodeURIComponent(actor))}`, {
            method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-Agent-Project': project },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        return { status: response.status, body: await response.json() };
    }, { actor, path, body });
}
async function start(page, actor = 'actor', nodeIds = ['a'], options = {}) {
    const preview = await api(page, actor, `generate/preflight?agent_node_id=${actor}`, { node_ids: nodeIds });
    assert.equal(preview.status, 200, JSON.stringify(preview));
    const receipt = await api(page, actor, `generate/request_batch?agent_node_id=${actor}`, { preflight_id: preview.body.preflight_id, ...options });
    assert.equal(receipt.status, 200, JSON.stringify(receipt));
    assert.equal(receipt.body.status, 'awaiting_confirmation');
    const gate = await previewOf(page, actor, receipt.body);
    return { ...receipt.body, preflight: gate.preflight };
}
async function previewOf(page, actor, batch) {
    const result = await api(page, actor, `agent-nodes/${actor}/batches/${batch.batch_id}/preview`);
    assert.equal(result.status, 200, JSON.stringify(result));
    return result.body;
}
function decision(gate, approved, ids = gate.preflight.items.map(row => row.item_id)) {
    return { confirm_id: gate.confirm_id, approved, snapshot_sha256: gate.preflight.snapshot_sha256,
        ...(approved ? { approved_item_ids: ids } : {}) };
}
function approve(card) { return card.locator('[data-agent-confirm-approve]'); }
function cancel(card) { return card.getByRole('button', { name: '取消', exact: true }); }
async function status(page, actor, batch) {
    return api(page, actor, `generate/batch_status?agent_node_id=${actor}`, { batch_id: batch.batch_id });
}
async function stop(page, actor, batch) {
    await api(page, actor, `generate/stop_batch?agent_node_id=${actor}`, { batch_id: batch.batch_id });
}

export async function independentPendingBatches({ page, expect }) {
    const actor = 'actor', first = await start(page, actor), second = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const decisions = [];
    try {
        await expect(card).toBeVisible();
        let response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await cancel(card).click();
        let receipt = await response;
        assert.equal(receipt.status(), 200);
        assert.deepEqual(receipt.request().postDataJSON(), decision(second, false));
        decisions.push(receipt.request().postDataJSON());
        await expect.poll(async () => (await status(page, actor, second)).body.status).toBe('rejected');
        assert.equal((await status(page, actor, first)).body.status, 'awaiting_confirmation');
        await expect(card).toBeVisible();
        response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await card.getByRole('button', { name: '取消', exact: true }).click();
        receipt = await response;
        assert.equal(receipt.status(), 200);
        assert.deepEqual(receipt.request().postDataJSON(), decision(first, false));
        decisions.push(receipt.request().postDataJSON());
        await expect.poll(async () => (await status(page, actor, first)).body.status).toBe('rejected');
        await expect(card).toHaveCount(0);
        const final = [(await status(page, actor, first)).body, (await status(page, actor, second)).body];
        for (const batch of final) {
            assert.equal(batch.spent, 0);
            assert.equal(batch.items[0].task_id, null);
        }
        return { scenario: 'independentPendingBatches', decisions, final };
    } finally {
        await stop(page, actor, first);
        await stop(page, actor, second);
    }
}

export async function bufferedReconnectKeepsIndependentApproval({ page, expect }) {
    const actor = 'actor', first = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const historyPattern = '**/agent-nodes/actor/events?*';
    const decisions = [];
    const observeDecision = request => {
        if (request.url().endsWith('/agent-nodes/actor/confirm') && request.method() === 'POST') decisions.push(request.postDataJSON());
    };
    let release, snapshot, second;
    const held = new Promise(resolve => { release = resolve; });
    const interceptHistory = async route => {
        snapshot = await route.fetch();
        await held;
        await route.fulfill({ response: snapshot });
    };
    page.on('request', observeDecision);
    await page.route(historyPattern, interceptHistory, { times: 1 });
    try {
        await expect(card).toBeVisible();
        await expect(page.locator('.ff-dialog-card')).toHaveCount(0);
        await page.waitForFunction(() => getAgentTuiState('actor').socket?.readyState === WebSocket.OPEN);
        await page.evaluate(() => getAgentTuiState('actor').socket.close(1000, 'Owned reconnect ordering test'));
        await expect.poll(() => Boolean(snapshot)).toBe(true);
        second = await start(page, actor);
        const rejectSecond = await api(page, actor, 'agent-nodes/{actor}/confirm', decision(second, false));
        assert.equal(rejectSecond.status, 200);
        await page.waitForFunction(id => {
            const buffer = getAgentTuiState('actor')._replayBuffer || [];
            return buffer.some(event => event.type === 'confirm' && event.confirm_id === id)
                && buffer.some(event => event.type === 'confirm_resolved' && event.confirm_id === id);
        }, second.confirm_id);
        const buffered = await page.evaluate(() => getAgentTuiState('actor')._replayBuffer.map(event => ({ type: event.type, confirm_id: event.confirm_id, seq: event.seq })));
        release();
        await page.waitForFunction(() => !getAgentTuiState('actor')._replayInFlight);
        await expect(card).toBeVisible();
        const untouched = (await status(page, actor, first)).body;
        assert.equal(untouched.status, 'awaiting_confirmation');
        assert.equal(decisions.filter(value => value.confirm_id === first.confirm_id).length, 0);
        const response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await card.getByRole('button', { name: '取消', exact: true }).click();
        assert.equal((await response).status(), 200);
        await expect.poll(async () => (await status(page, actor, first)).body.status).toBe('rejected');
        await expect(card).toHaveCount(0);
        const final = [(await status(page, actor, first)).body, (await status(page, actor, second)).body];
        assert.deepEqual(decisions.map(value => value.confirm_id), [second.confirm_id, first.confirm_id]);
        for (const batch of final) {
            assert.equal(batch.spent, 0);
            assert.equal(batch.items[0].task_id, null);
        }
        return { scenario: 'bufferedReconnectKeepsIndependentApproval', buffered, untouchedStatus: untouched.status, decisions, final };
    } finally {
        release();
        page.off('request', observeDecision);
        await page.unroute(historyPattern, interceptHistory);
        await stop(page, actor, first);
        if (second) await stop(page, actor, second);
    }
}

export async function lateCautiousSelection({ page, expect }) {
    const actor = 'cautious', batch = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const pattern = '**/agent-nodes/cautious/confirm';
    let release, accepted;
    const held = new Promise(resolve => { release = resolve; });
    const intercept = async route => { accepted = await route.fetch(); await held; await route.fulfill({ response: accepted }); };
    await page.route(pattern, intercept, { times: 1 });
    try {
        await expect(approve(card)).toHaveText('确认所选 1 张');
        await approve(card).click();
        await expect.poll(async () => {
            const value = await api(page, actor, `agent-nodes/{actor}/batches/${batch.batch_id}/preview`);
            return value.body.confirm_id && value.body.confirm_id !== batch.confirm_id;
        }).toBe(true);
        const next = (await api(page, actor, `agent-nodes/{actor}/batches/${batch.batch_id}/preview`)).body;
        const selected = (await status(page, actor, batch)).body;
        assert.equal(selected.spent, 0);
        assert.equal(selected.approved_count, 0);
        await expect(card).toBeVisible();
        const response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/cautious/confirm'));
        release();
        assert.equal((await response).status(), 200);
        await expect(card).toBeVisible();
        await expect(page.locator('[data-id="cautious"] .agent-tui-msg-system').filter({ hasText: '✅ 已批准' })).toHaveCount(0);
        const paidResponse = page.waitForResponse(r => r.url().endsWith('/agent-nodes/cautious/confirm'));
        await expect(card.getByRole('heading')).toHaveText('逐项生成确认');
        await expect(approve(card)).toHaveText('生成 1 张');
        await approve(card).click();
        const paid = await paidResponse;
        assert.equal(paid.status(), 200);
        assert.deepEqual(paid.request().postDataJSON(), decision(next, true));
        await expect.poll(async () => (await status(page, actor, batch)).body.status, { timeout: 10000 }).toBe('done');
        const final = (await status(page, actor, batch)).body;
        assert.equal(final.spent, 1);
        assert.equal(final.approved_count, 1);
        assert.equal(final.items[0].attempts, 1);
        return { scenario: 'lateCautiousSelection', selectionConfirmId: batch.confirm_id, paidConfirmId: next.confirm_id, selected, final };
    } finally {
        release();
        await page.unroute(pattern, intercept);
        await stop(page, actor, batch);
    }
}

export async function lateReplacedGate({ page, expect }) {
    const actor = 'actor', batch = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const pattern = '**/agent-nodes/actor/confirm';
    let release, request;
    const held = new Promise(resolve => { release = resolve; });
    const intercept = async route => { request = route.request(); await held; await route.continue(); };
    await page.route(pattern, intercept, { times: 1 });
    try {
        await approve(card).click();
        await expect.poll(() => Boolean(request)).toBe(true);
        const original = (await api(page, actor, `agent-nodes/{actor}/batches/${batch.batch_id}/preview`)).body.preflight;
        const edited = await api(page, actor, `agent-nodes/{actor}/batches/${batch.batch_id}/preview/edit`, {
            preview_revision: original.preview_revision, snapshot_sha256: original.snapshot_sha256,
            item_id: original.items[0].item_id, patch: { prompt: 'Only this batch edited body' },
        });
        assert.equal(edited.status, 200);
        assert.notEqual(edited.body.confirm_id, batch.confirm_id);
        await expect(card.getByText('Only this batch edited body')).toBeAttached();
        const response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        release();
        assert.equal((await response).status(), 409);
        await expect(card).toBeVisible();
        await expect(page.locator('[data-id="actor"] .agent-tui-msg-system').filter({ hasText: '确认未获接受回执' })).toHaveCount(0);
        const nextResponse = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await cancel(card).click();
        const rejected = await nextResponse;
        assert.equal(rejected.status(), 200);
        assert.deepEqual(rejected.request().postDataJSON(), decision(edited.body, false));
        await expect.poll(async () => (await status(page, actor, batch)).body.status).toBe('rejected');
        const final = (await status(page, actor, batch)).body;
        assert.equal(final.spent, 0);
        assert.equal(final.items[0].task_id, null);
        return { scenario: 'lateReplacedGate', oldConfirmId: batch.confirm_id, newConfirmId: edited.body.confirm_id, lateStatus: 409, final };
    } finally {
        release();
        await page.unroute(pattern, intercept);
        await stop(page, actor, batch);
    }
}

export async function transportFailure({ page, expect, mode }) {
    const actor = 'actor', batch = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const pattern = '**/agent-nodes/actor/confirm';
    const intercept = route => mode === 'network' ? route.abort('failed')
        : route.fulfill({ status: mode === 'nonjson' ? 503 : 200,
            contentType: mode === 'nonjson' ? 'text/plain' : 'application/json',
            body: mode === 'nonjson' ? 'unavailable' : JSON.stringify({ ok: true, confirm_id: 'another-gate', approved: true }) });
    await page.route(pattern, intercept, { times: 1 });
    try {
        await approve(card).click();
        await expect(page.locator('[data-id="actor"] .agent-tui-msg-system').filter({ hasText: '确认未获接受回执' })).toBeVisible();
        await expect(card).toBeVisible();
        if (mode === 'nonjson') await expect(page.locator('[data-id="actor"] .agent-tui-msg-system').filter({ hasText: 'HTTP 503' })).toBeVisible();
        assert.equal((await status(page, actor, batch)).body.status, 'awaiting_confirmation');
        const retry = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await card.getByRole('button', { name: '取消', exact: true }).click();
        assert.equal((await retry).status(), 200);
        await expect.poll(async () => (await status(page, actor, batch)).body.status).toBe('rejected');
        return { scenario: 'transportFailure', mode, final: (await status(page, actor, batch)).body };
    } finally {
        await page.unroute(pattern, intercept);
        await stop(page, actor, batch);
    }
}

export async function failedRejectCanRetry({ page, expect, capture = async () => {} }) {
    const actor = 'actor', batch = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const pattern = '**/agent-nodes/actor/confirm';
    const intercept = async route => {
        const headers = { ...route.request().headers() };
        delete headers['x-agent-project'];
        await route.continue({ headers });
    };
    await page.route(pattern, intercept, { times: 1 });
    try {
        await expect(card).toBeVisible();
        const rejectedResponse = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await cancel(card).click();
        const rejected = await rejectedResponse;
        assert.equal(rejected.status(), 403);
        assert.equal((await rejected.json()).detail.code, 'PROJECT_MISMATCH');
        await expect(page.locator('[data-id="actor"] .agent-tui-msg-system').filter({ hasText: 'X-Agent-Project must match' })).toBeVisible({ timeout: 1500 });
        await expect(card).toBeVisible();
        await expect(card.getByRole('button', { name: '取消', exact: true })).toBeEnabled();
        await capture();
        assert.equal((await status(page, actor, batch)).body.status, 'awaiting_confirmation');
        const retryResponse = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await card.getByRole('button', { name: '取消', exact: true }).click();
        const accepted = await retryResponse;
        assert.equal(accepted.status(), 200);
        assert.equal(accepted.request().postDataJSON().approved, false);
        await expect.poll(async () => (await status(page, actor, batch)).body.status).toBe('rejected');
        const final = (await status(page, actor, batch)).body;
        assert.equal(final.spent, 0);
        assert.equal(final.items[0].attempts, 0);
        assert.equal(final.items[0].task_id, null);
        await expect(card).toHaveCount(0);
        return { scenario: 'failedRejectCanRetry', rejectedStatus: rejected.status(), retryStatus: accepted.status(), final };
    } finally {
        await page.unroute(pattern, intercept);
        await stop(page, actor, batch);
    }
}

export async function editThenApproveSubset({ page, expect, capture = async () => {} }) {
    const actor = 'actor', batch = await start(page, actor, ['a', 'b'], { fallback_models: ['fallback'] });
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const [first, second] = batch.preflight.items;
    const row = card.locator('.agent-batch-row').filter({ has: page.locator(`[data-agent-batch-select="${first.item_id}"]`) });
    try {
        await expect(approve(card)).toHaveText('生成 2 张');
        await expect(card.getByText('备选模型：fallback')).toBeVisible();
        assert.ok(first.aspect_ratio && first.size, JSON.stringify(first));
        await expect(row.locator('.agent-batch-meta')).toHaveText(`${first.aspect_ratio} · ${first.size} · ${first.count} 张 · ${first.model}`);
        const thumbnail = row.locator('.agent-batch-ref img');
        await expect(thumbnail).toBeVisible();
        await expect.poll(() => thumbnail.evaluate(img => img.naturalWidth)).toBeGreaterThan(0);
        await expect(row.locator('.agent-batch-ref')).toHaveAttribute('aria-label', '参考图 1 · product');
        await row.getByText('最终提示词').click();
        await expect(row.locator('pre')).toHaveText(first.final_prompt);
        await card.locator(`[data-agent-batch-select="${second.item_id}"]`).uncheck();
        await expect(approve(card)).toHaveText('生成 1 张');
        await card.locator(`[data-agent-batch-select="${first.item_id}"]`).uncheck();
        await expect(approve(card)).toBeDisabled();
        await card.locator(`[data-agent-batch-select="${first.item_id}"]`).check();
        await capture('preview', card);
        await card.getByRole('button', { name: '改一下', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '批量生成预检' });
        const editRow = dialog.locator('details').filter({ has: page.locator(`[data-agent-batch-edit-row="${first.item_id}"]`) });
        await editRow.locator('summary').click();
        const task = editRow.getByRole('textbox', { name: '任务正文（参考图前缀由预检生成）' });
        await expect(task).toHaveValue(first.user_prompt);
        await task.fill('Offline revised sculpture with the same reference');
        await capture('editor', dialog);
        const editedResponse = page.waitForResponse(r => r.url().endsWith('/preview/edit'));
        await editRow.getByRole('button', { name: '保存并重新预检' }).click();
        const edit = await editedResponse;
        assert.equal(edit.status(), 200);
        const revised = await edit.json();
        assert.notEqual(revised.confirm_id, batch.confirm_id);
        assert.notEqual(revised.preflight.snapshot_sha256, batch.preflight.snapshot_sha256);
        assert.equal(revised.preflight.preview_revision, batch.preflight.preview_revision + 1);
        assert.deepEqual(revised.preflight.items.map(item => item.item_id), [first.item_id, second.item_id]);
        assert.deepEqual(revised.preflight.items[0].refs, first.refs);
        await expect(dialog).toHaveCount(0);
        await expect(row.locator('pre')).toHaveText(revised.preflight.items[0].final_prompt);
        await expect(approve(card)).toHaveText('生成 1 张');
        await expect(card.locator(`[data-agent-batch-select="${second.item_id}"]`)).not.toBeChecked();
        const settled = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await approve(card).click();
        const accepted = await settled;
        assert.equal(accepted.status(), 200);
        assert.deepEqual(accepted.request().postDataJSON(), decision(revised, true, [first.item_id]));
        await expect.poll(async () => (await status(page, actor, batch)).body.status).toBe('done');
        const final = (await status(page, actor, batch)).body;
        assert.equal(final.spent, 1);
        assert.deepEqual(final.items.map(item => item.status), ['succeeded', 'skipped']);
        return { scenario: 'editThenApproveSubset', revision: revised.preflight.preview_revision, final };
    } finally { await stop(page, actor, batch); }
}

export async function removeAllStopsBatch({ page, expect }) {
    const actor = 'actor', batch = await start(page, actor, ['a', 'b']);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    try {
        let gate = batch;
        for (const row of batch.preflight.items) {
            const response = page.waitForResponse(r => r.url().endsWith('/preview/remove'));
            await card.locator(`[data-agent-batch-remove="${row.item_id}"]`).click();
            const removed = await response;
            assert.equal(removed.status(), 200);
            assert.deepEqual(removed.request().postDataJSON(), { item_id: row.item_id,
                preview_revision: gate.preflight.preview_revision, snapshot_sha256: gate.preflight.snapshot_sha256 });
            gate = await removed.json();
            await expect(card.locator(`[data-agent-batch-remove="${row.item_id}"]`)).toHaveCount(0);
        }
        assert.equal(gate.confirm_id, null);
        assert.equal(gate.actionable, false);
        await expect(card).toHaveCount(0);
        const final = (await status(page, actor, batch)).body;
        assert.equal(final.status, 'stopped');
        assert.equal(final.reason, 'empty_batch');
        assert.equal(final.spent, 0);
        return { scenario: 'removeAllStopsBatch', final };
    } finally { await stop(page, actor, batch); }
}

export async function batchFailureCopy({ page, expect, capture = async () => {} }) {
    const actor = 'actor';
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    const recover = await start(page, actor, ['recover'], { fallback_models: ['fallback'] });
    try {
        await approve(card).click();
        await expect(recoveryCard(page, actor, recover).getByText(/切换至 fallback 后已生成/)).toBeVisible();
        const recovered = (await status(page, actor, recover)).body;
        assert.equal(recovered.items[0].status, 'succeeded');
        assert.equal(recovered.items[0].attempts, 2);
        assert.equal(recovered.items[0].last_failure.code, 'MODEL_UNAVAILABLE');
    } finally { await stop(page, actor, recover); }
    const quota = await start(page, actor, ['quota', 'b']);
    try {
        await approve(card).click();
        const recovery = recoveryCard(page, actor, quota);
        const notice = recovery.getByText('服务商额度用尽，整个批次已暂停。', { exact: true });
        await expect(notice).toBeVisible();
        await expect(recovery.getByRole('button', { name: '恢复批次', exact: true })).toBeEnabled();
        const paused = (await status(page, actor, quota)).body;
        assert.equal(paused.status, 'paused');
        assert.equal(paused.reason, 'QUOTA_EXHAUSTED');
        assert.equal(paused.spent, 1);
        assert.deepEqual(paused.items.map(item => item.status), ['paused', 'queued']);
        await capture('diagnosis', notice);
        return { scenario: 'batchFailureCopy', paused };
    } finally { await stop(page, actor, quota); }
}

function recoveryCard(page, actor, batch) {
    return page.locator(`[data-id="${actor}"] [data-agent-batch-status="${batch.batch_id}"]`);
}

export async function batchRecovery501({ page, expect, fixture, capture = async () => {} }) {
    const actor = 'actor', project = new URL(page.url()).searchParams.get('project');
    const batch = await start(page, actor, ['quota-501', 'b']);
    const [first, second] = batch.preflight.items.map(item => item.item_id);
    const snapshot = batch.preflight.snapshot_sha256;
    const confirmPath = `/agent-nodes/${actor}/confirm`;
    const batchPath = `/agent-nodes/${actor}/batches/${batch.batch_id}`;
    const recovery = recoveryCard(page, actor, batch);
    const heading = recovery.getByRole('heading');
    const failed = recovery.locator(`[data-agent-batch-status-item="${first}"]`);
    const queued = recovery.locator(`[data-agent-batch-status-item="${second}"]`);
    const retry = failed.getByRole('button', { name: '申请付费重试', exact: true });
    const skip = failed.getByRole('button', { name: '跳过', exact: true });
    const resume = recovery.getByRole('button', { name: '恢复批次', exact: true });
    const success = failed.getByText('独立付费重试已生成，原任务记录保留。', { exact: true });
    const actorNode = page.locator(`[data-id="${actor}"]`);
    const approvedRetry = actorNode.getByText('Agent 操作确认已批准：generate.retry', { exact: true });
    const auditedApproval = actorNode.locator('.agent-tui-msg-system')
        .getByText('Agent 操作确认已批准：generate.retry', { exact: true });
    const read = async path => {
        const result = await api(page, actor, path);
        assert.equal(result.status, 200, JSON.stringify(result));
        return result.body;
    };
    const readStatus = () => read(`${batchPath.slice(1)}/status`);
    const audit = async () => (await read(`agent-nodes/${actor}/events`)).events.map(row => row.event)
        .filter(event => event.batch_id === batch.batch_id);
    const responseFor = path => page.waitForResponse(r => new URL(r.url()).pathname === `/api/projects/${project}${path}` && r.request().method() === 'POST');
    const verifyReceipt = async (receipt, body) => {
        assert.equal(receipt.status(), 200);
        assert.equal(receipt.request().headers()['x-agent-project'], project);
        assert.deepEqual(receipt.request().postDataJSON(), body);
        return receipt.json();
    };
    const mutate = async (button, path, body) => {
        const response = responseFor(path);
        await button.click();
        return verifyReceipt(await response, body);
    };
    const original = ({ status, attempts, task_id, failure }) => ({ status, attempts, task_id, failure });
    const untouched = { status: 'queued', attempts: 0, task_id: null, failure: null };
    const verifyPaused = data => {
        assert.deepEqual([data.batch_id, data.snapshot_sha256, data.status, data.reason, data.spent],
            [batch.batch_id, snapshot, 'paused', 'QUOTA_EXHAUSTED', 1]);
        assert.deepEqual(data.approved_item_ids, [first, second]);
        assert.deepEqual(original(data.items[1]), untouched);
    };
    try {
        const confirm = page.locator(`section[data-agent-confirm="${actor}"]`);
        await mutate(approve(confirm), confirmPath, decision(batch, true));
        await expect(heading).toHaveText('批量生成状态 · 已暂停');
        await expect(failed.getByText(/遇到服务商额度限制/)).toBeVisible();
        await expect(queued.getByText('尚未启动', { exact: true })).toBeVisible();
        await expect(queued.getByRole('button', { name: '申请付费重试', exact: true })).toHaveCount(0);
        await expect(resume).toBeEnabled();
        const paused = await readStatus();
        verifyPaused(paused);
        const failedBefore = original(paused.items[0]);
        assert.equal(failedBefore.status, 'paused');
        assert.equal(failedBefore.attempts, 1);
        assert.ok(failedBefore.task_id);
        assert.equal(failedBefore.failure.code, 'QUOTA_EXHAUSTED');
        expect(await fixture.calls()).toEqual(['quota']);

        const itemBody = { snapshot_sha256: snapshot, item_id: first };
        const retryReceipt = await mutate(retry, `${batchPath}/retry`, itemBody);
        assert.equal(retryReceipt.status, 'awaiting_confirmation');
        assert.match(retryReceipt.confirm_id, /^confirm-batch-retry-/);
        const retryEvents = async () => (await audit()).filter(event => event.confirm_id === retryReceipt.confirm_id);
        const dialog = page.getByRole('alertdialog').filter({ hasText: '确认 Agent 操作' });
        await expect(dialog.getByText(/重新生成 1 张图片（重新消耗外部额度）/)).toBeVisible();
        await expect(confirm).toHaveCount(0);
        const [opened] = await retryEvents();
        assert.equal(opened.type, 'confirm');
        assert.equal(opened.tool, 'generate.retry');
        assert.equal(opened.item_id, first);
        assert.equal(opened.params.task_id, failedBefore.task_id);
        expect(await fixture.calls()).toEqual(['quota']);
        await capture('retry-dialog', dialog);

        // Fence the mock transport and provider independently: a decision is not an audit or a result.
        const approved = dialog.getByRole('button', { name: '批准', exact: true });
        await fixture.beforeRetryDecision?.();
        const retryDecision = { confirm_id: retryReceipt.confirm_id, approved: true };
        const decisionResponse = responseFor(confirmPath);
        try {
            await approved.click();
            await expect.poll(fixture.decision).toEqual(retryDecision);
            assert.deepEqual((await retryEvents()).map(event => event.type), ['confirm']);
            await expect(approvedRetry).toHaveCount(0);
            await expect(success).toHaveCount(0);
            expect(await fixture.calls()).toEqual(['quota']);
        } finally {
            await fixture.releaseDecision();
            void decisionResponse.catch(() => {});
        }
        const decisionReceipt = await verifyReceipt(await decisionResponse, retryDecision);
        assert.deepEqual(decisionReceipt, { ok: true, ...retryDecision });
        await expect.poll(async () => (await retryEvents()).map(event => event.type)).toEqual(['confirm', 'confirm_resolved']);
        const [, accepted] = await retryEvents();
        assert.deepEqual([accepted.tool, accepted.item_id, accepted.approved], ['generate.retry', first, true]);
        await expect(auditedApproval).toBeVisible();
        await expect.poll(fixture.calls).toEqual(['quota', 'retry']);
        const pending = await readStatus();
        verifyPaused(pending);
        assert.equal(pending.items[0].retry_pending, true);
        assert.equal(pending.items[0].retry_result, undefined);
        await expect(failed.getByRole('status')).toHaveText('此项重试处理中，以付费确认和任务状态为准。');
        await expect(success).toHaveCount(0);
        await expect(resume).toHaveCount(0);
        await capture('retry-pending', recovery);
        await fixture.releaseRetry();

        await expect(success).toBeVisible({ timeout: 10000 });
        await expect(heading).toHaveText('批量生成状态 · 已暂停');
        await expect(queued.getByText('尚未启动', { exact: true })).toBeVisible();
        const retried = await readStatus();
        verifyPaused(retried);
        assert.deepEqual(original(retried.items[0]), failedBefore);
        const newTask = retried.items[0].retry_result;
        assert.equal(newTask.status, 'succeeded');
        assert.ok(newTask.task_id && newTask.task_id !== failedBefore.task_id);
        expect(await fixture.calls()).toEqual(['quota', 'retry']);
        await expect(retry).toHaveCount(0);
        await capture('retry-paused', recovery);

        const skipped = await mutate(skip, `${batchPath}/skip`, itemBody);
        verifyPaused(skipped);
        assert.deepEqual(original(skipped.items[0]), failedBefore);
        assert.deepEqual(skipped.items[0].retry_result, newTask);
        assert.equal(skipped.items[0].user_skipped, true);
        await expect(heading).toHaveText('批量生成状态 · 已暂停');
        await expect(failed.getByText('已暂停 · 已手动跳过', { exact: true })).toBeVisible();
        await expect(failed.getByText(/遇到服务商额度限制/)).toBeVisible();
        await expect(skip).toHaveCount(0);
        await expect(resume).toBeEnabled();
        assert.deepEqual(await readStatus(), skipped);
        expect(await fixture.calls()).toEqual(['quota', 'retry']);
        await capture('skip-paused', recovery);

        const resumed = await mutate(resume, `${batchPath}/resume`, { snapshot_sha256: snapshot });
        assert.equal(resumed.status, 'running');
        await expect(heading).toHaveText('批量生成状态 · 执行结束', { timeout: 10000 });
        await expect(queued.getByText('已生成', { exact: true })).toBeVisible();
        await expect(resume).toHaveCount(0);
        const final = await readStatus();
        assert.equal(final.status, 'done');
        assert.equal(final.spent, 2);
        assert.equal(final.approval_sha256, paused.approval_sha256);
        assert.deepEqual(final.approved_item_ids, [first, second]);
        assert.deepEqual(original(final.items[0]), failedBefore);
        assert.equal(final.items[0].user_skipped, true);
        assert.deepEqual(final.items[0].retry_result, newTask);
        assert.equal(final.items[1].status, 'succeeded');
        assert.equal(final.items[1].attempts, 1);
        const taskIds = [failedBefore.task_id, newTask.task_id, final.items[1].task_id];
        assert.ok(taskIds.every(Boolean));
        assert.equal(new Set(taskIds).size, 3);
        const tasks = await Promise.all(taskIds.map(id => read(`generate/tasks/${id}`)));
        assert.equal(tasks[0].failure.code, 'QUOTA_EXHAUSTED');
        assert.deepEqual(tasks.map(task => task.status), ['failed', 'succeeded', 'succeeded']);
        expect(await fixture.calls()).toEqual(['quota', 'retry', 'queued']);
        const recoveryAudit = (await audit()).filter(event => ['batch_item_skipped', 'batch_resumed'].includes(event.type));
        assert.deepEqual(recoveryAudit.map(event => [event.type, event.item_id ?? event.item_ids]),
            [['batch_item_skipped', first], ['batch_resumed', [second]]]);
        await capture('resume-done', recovery);
        return { scenario: 'batchRecovery501', retryReceipt, decisionReceipt, accepted, paused, pending, retried, skipped, resumed, final, recoveryAudit };
    } finally {
        await fixture.releaseDecision();
        await fixture.releaseRetry();
        await stop(page, actor, batch);
    }
}

export async function pendingApprove({ page, expect, disconnect = false }) {
    const actor = 'actor';
    const batch = await start(page, actor);
    const card = page.locator(`[data-agent-confirm="${actor}"]`);
    await expect(card).toBeVisible();
    await page.waitForFunction(() => getAgentTuiState('actor').socket?.readyState === WebSocket.OPEN);
    if (disconnect) {
        await page.evaluate(() => getAgentTuiState('actor').socket.close(1000, 'Owned offline browser test'));
        await page.waitForFunction(() => getAgentTuiState('actor').socket?.readyState !== WebSocket.OPEN);
    }
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let request;
    const requests = [];
    const pattern = '**/agent-nodes/actor/confirm';
    const intercept = async route => { request = route.request(); requests.push(request); await held; await route.continue(); };
    await page.route(pattern, intercept);
    try {
        await approve(card).click();
        await expect(card).toBeVisible({ timeout: 1500 });
        await expect.poll(() => Boolean(request), { timeout: 1500 }).toBe(true);
        const project = new URL(page.url()).searchParams.get('project');
        assert.equal(request.headers()['x-agent-project'], project);
        assert.equal(request.method(), 'POST');
        assert.deepEqual(request.postDataJSON(), decision(batch, true));
        await expect(approve(card)).toBeDisabled();
        await expect(cancel(card)).toBeDisabled();
        await approve(card).dispatchEvent('click');
        await cancel(card).dispatchEvent('click');
        assert.equal(requests.length, 1, 'An in-flight gate must not send another decision');
        await expect(page.locator('.agent-tui-msg-system').filter({ hasText: '✅ 已批准' })).toHaveCount(0);
        assert.equal((await status(page, actor, batch)).body.status, 'awaiting_confirmation');
        const response = page.waitForResponse(r => r.url() === request.url() && r.request().method() === 'POST');
        release();
        const accepted = await response;
        assert.equal(accepted.status(), 200);
        assert.equal((await accepted.json()).ok, true);
        await expect(card).toHaveCount(0);
        await expect.poll(async () => (await status(page, actor, batch)).body.status, { timeout: 10000 }).toBe('done');
        const final = (await status(page, actor, batch)).body;
        assert.equal(final.approved_count, 1);
        assert.equal(final.spent, 1);
        assert.equal(final.items.length, 1);
        assert.equal(final.items[0].attempts, 1);
        assert.equal(final.items[0].status, 'succeeded');
        const task = await api(page, actor, `generate/tasks/${final.items[0].task_id}`);
        assert.equal(task.status, 200);
        assert.equal(task.body.status, 'succeeded');
        const duplicate = await api(page, actor, 'agent-nodes/{actor}/confirm', decision(batch, true));
        assert.equal(duplicate.status, 409);
        assert.equal((await status(page, actor, batch)).body.spent, 1);
        return { scenario: 'pendingApprove', disconnectedAtClick: disconnect, request: { url: request.url(), projectHeader: request.headers()['x-agent-project'], body: request.postDataJSON() }, final, taskStatus: task.body.status, duplicateStatus: duplicate.status };
    } finally {
        release();
        await page.unroute(pattern, intercept);
        await stop(page, actor, batch);
    }
}
