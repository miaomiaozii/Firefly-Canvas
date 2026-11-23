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
async function start(page, actor = 'actor') {
    const preview = await api(page, actor, `generate/preflight?agent_node_id=${actor}`, { node_ids: ['a'] });
    assert.equal(preview.status, 200, JSON.stringify(preview));
    const receipt = await api(page, actor, `generate/request_batch?agent_node_id=${actor}`, { preflight_id: preview.body.preflight_id });
    assert.equal(receipt.status, 200, JSON.stringify(receipt));
    assert.equal(receipt.body.status, 'awaiting_confirmation');
    return receipt.body;
}
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '取消', exact: true }).click();
        let receipt = await response;
        assert.equal(receipt.status(), 200);
        assert.deepEqual(receipt.request().postDataJSON(), { confirm_id: second.confirm_id, approved: false });
        decisions.push(receipt.request().postDataJSON());
        await expect.poll(async () => (await status(page, actor, second)).body.status).toBe('rejected');
        assert.equal((await status(page, actor, first)).body.status, 'awaiting_confirmation');
        await expect(card).toBeVisible();
        response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await card.getByRole('button', { name: '取消', exact: true }).click();
        receipt = await response;
        assert.equal(receipt.status(), 200);
        assert.deepEqual(receipt.request().postDataJSON(), { confirm_id: first.confirm_id, approved: false });
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
        await expect(page.locator('.ff-dialog-card')).toBeVisible();
        await page.waitForFunction(() => getAgentTuiState('actor').socket?.readyState === WebSocket.OPEN);
        await page.evaluate(() => getAgentTuiState('actor').socket.close(1000, 'Owned reconnect ordering test'));
        await expect.poll(() => Boolean(snapshot)).toBe(true);
        second = await start(page, actor);
        const rejectSecond = await api(page, actor, 'agent-nodes/{actor}/confirm', { confirm_id: second.confirm_id, approved: false });
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '批准', exact: true }).click();
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '批准', exact: true }).click();
        const paid = await paidResponse;
        assert.equal(paid.status(), 200);
        assert.deepEqual(paid.request().postDataJSON(), { confirm_id: next.confirm_id, approved: true });
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '批准', exact: true }).click();
        await expect.poll(() => Boolean(request)).toBe(true);
        const original = (await api(page, actor, `agent-nodes/{actor}/batches/${batch.batch_id}/preview`)).body.preflight;
        const edited = await api(page, actor, `agent-nodes/{actor}/batches/${batch.batch_id}/preview/edit`, {
            preview_revision: original.preview_revision, snapshot_sha256: original.snapshot_sha256,
            item_id: original.items[0].item_id, patch: { prompt: 'Only this batch edited body' },
        });
        assert.equal(edited.status, 200);
        assert.notEqual(edited.body.confirm_id, batch.confirm_id);
        await expect(page.locator('.ff-dialog-card')).toBeVisible();
        const response = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        release();
        assert.equal((await response).status(), 409);
        await expect(card).toBeVisible();
        await expect(page.locator('[data-id="actor"] .agent-tui-msg-system').filter({ hasText: '确认未获接受回执' })).toHaveCount(0);
        const nextResponse = page.waitForResponse(r => r.url().endsWith('/agent-nodes/actor/confirm'));
        await page.locator('.ff-dialog-card').getByRole('button', { name: '取消', exact: true }).click();
        const rejected = await nextResponse;
        assert.equal(rejected.status(), 200);
        assert.deepEqual(rejected.request().postDataJSON(), { confirm_id: edited.body.confirm_id, approved: false });
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '批准', exact: true }).click();
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '取消', exact: true }).click();
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
        await page.locator('.ff-dialog-card').getByRole('button', { name: '批准', exact: true }).click();
        await expect(card).toBeVisible({ timeout: 1500 });
        await expect.poll(() => Boolean(request), { timeout: 1500 }).toBe(true);
        const project = new URL(page.url()).searchParams.get('project');
        assert.equal(request.headers()['x-agent-project'], project);
        assert.equal(request.method(), 'POST');
        assert.deepEqual(request.postDataJSON(), { confirm_id: batch.confirm_id, approved: true });
        await card.getByRole('button', { name: '批准', exact: true }).click();
        await card.getByRole('button', { name: '取消', exact: true }).click();
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
        const duplicate = await api(page, actor, 'agent-nodes/{actor}/confirm', { confirm_id: batch.confirm_id, approved: true });
        assert.equal(duplicate.status, 409);
        assert.equal((await status(page, actor, batch)).body.spent, 1);
        return { scenario: 'pendingApprove', disconnectedAtClick: disconnect, request: { url: request.url(), projectHeader: request.headers()['x-agent-project'], body: request.postDataJSON() }, final, taskStatus: task.body.status, duplicateStatus: duplicate.status };
    } finally {
        release();
        await page.unroute(pattern, intercept);
        await stop(page, actor, batch);
    }
}
