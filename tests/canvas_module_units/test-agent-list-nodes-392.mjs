// #392 stage 1: run the real handwritten pi executors with a fake fetch.
// Built-in TS stripping avoids pi-stable/typebox runtime dependencies; no HTTP service.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const sourceUrl = new URL('../../app/agent_extensions/canvas-tools.ts', import.meta.url);
const generated = readFileSync(new URL('../../app/agent_extensions/canvas-tools.generated.ts', import.meta.url), 'utf8');
// Load the generated declarations: registration completes some schemas using their properties.
const generatedSource = stripTypeScriptTypes(generated
    .replace(/^import \{ Type \} from .*;\r?\n/m, '')
    .replace('export const CANVAS_TOOL_DEFS', 'const CANVAS_TOOL_DEFS'));
const Type = new Proxy({
    Object: (properties, opts) => ({ type: 'object', properties, ...opts }),
    Array: (items, opts) => ({ type: 'array', items, ...opts }),
    Union: (anyOf, opts) => ({ anyOf, ...opts }),
    Literal: (value, opts) => ({ const: value, ...opts }),
    Optional: (schema) => schema,
}, { get: (methods, name) => methods[name] ?? ((opts) => ({ type: name.toLowerCase(), ...opts })) });
const defs = vm.runInNewContext(`${generatedSource}\nCANVAS_TOOL_DEFS;`, { Type });
const source = stripTypeScriptTypes(readFileSync(sourceUrl, 'utf8')
    .replace(/^import \{ CANVAS_TOOL_DEFS \} from .*;\r?\n/m, '')
    .replace('export default function', 'function registerCanvasTools'));
const BASE = 'http://127.0.0.1:9999';
const PROJECT = 'test-project-392';
const AGENT = 'agent owner&/?中文';

function loadExecutors({ ok = true, payload = { nodes: [], count: 0, groups: [] } } = {}) {
    const tools = new Map();
    const calls = [];
    vm.runInNewContext(source + '\nregisterCanvasTools(pi);', {
        process: { env: {
            FIREFLY_AGENT_API_BASE: BASE,
            FIREFLY_AGENT_PROJECT_ID: PROJECT,
            FIREFLY_AGENT_NODE_ID: AGENT,
        } },
        URLSearchParams,
        CANVAS_TOOL_DEFS: defs,
        pi: { registerTool(def) { tools.set(def.name, def); } },
        fetch: async (url, options) => {
            calls.push({ url: new URL(url), options });
            return { ok, status: ok ? 200 : 400, json: async () => payload };
        },
    }, { filename: sourceUrl.pathname });
    return {
        calls,
        execute: (name, params) => tools.get(name).execute('call-392', params),
    };
}

function assertReadRequest(call) {
    assert.equal(call.url.origin, BASE);
    assert.equal(call.url.pathname, `/api/projects/${PROJECT}/canvas/nodes`);
    assert.equal(call.options.method, 'GET');
    assert.ok(!Object.hasOwn(call.options, 'body'), 'GET must not send a JSON body');
    assert.equal(call.options.headers['X-Agent-Project'], PROJECT);
    assert.ok(!call.url.searchParams.has('agent_node_id'), 'readonly list must not borrow gate identity');
}

test('#392 omitted fields remain omitted (all blocks), no trailing query or GET body', async () => {
    const runner = loadExecutors();
    await runner.execute('canvas.list_nodes', {});
    await runner.execute('canvas.list_nodes', { fields: undefined, group_id: undefined, created_by: undefined });
    assert.equal(runner.calls.length, 2);
    for (const call of runner.calls) {
        assertReadRequest(call);
        assert.equal(call.url.search, '');
    }
});

test('#392 explicit [] encodes fields=summary, not omission', async () => {
    const runner = loadExecutors();
    await runner.execute('canvas.list_nodes', { fields: [] });
    const call = runner.calls[0];
    assertReadRequest(call);
    assert.deepEqual([...call.url.searchParams], [['fields', 'summary']]);
});

test('#392 fields use repeated query keys; filters preserve exact encoded strings', async () => {
    const runner = loadExecutors();
    const group = 'group /?&=+# 中文';
    await runner.execute('canvas.list_nodes', {
        group_id: group, created_by: 'agent', fields: ['prompt', 'refs', 'run', 'prompt'],
    });
    const call = runner.calls[0];
    assertReadRequest(call);
    assert.equal(call.url.searchParams.get('group_id'), group);
    assert.equal(call.url.searchParams.get('created_by'), 'agent');
    assert.deepEqual(call.url.searchParams.getAll('fields'), ['prompt', 'refs', 'run', 'prompt']);
    assert.ok(!call.url.search.includes('prompt%2C'), 'comma abbreviation is not supported');
    assert.ok(!call.url.search.includes('#'), 'query strings must not turn into fragments');
    await runner.execute('canvas.list_nodes', { created_by: 'user' });
    assert.equal(runner.calls[1].url.searchParams.get('created_by'), 'user');
    assert.ok(!runner.calls[1].url.searchParams.has('fields'));
});

test('#392 schema-bypassing invalid arguments are not silently dropped/defaulted', async () => {
    const runner = loadExecutors();
    await runner.execute('canvas.list_nodes', {
        group_id: '', created_by: 'robot', fields: ['prompt', 'unknown', null, 7, ['run']],
    });
    const call = runner.calls[0];
    assertReadRequest(call);
    assert.equal(call.url.searchParams.get('group_id'), '');
    assert.equal(call.url.searchParams.get('created_by'), 'robot');
    assert.deepEqual(call.url.searchParams.getAll('fields'), ['prompt', 'unknown', 'null', '7', '["run"]']);
    await runner.execute('canvas.list_nodes', { fields: null });
    assert.deepEqual(runner.calls[1].url.searchParams.getAll('fields'), ['null']);
    await runner.execute('canvas.list_nodes', { fields: 'prompt' });
    assert.deepEqual(runner.calls[2].url.searchParams.getAll('fields'), ['"prompt"']);
});

test('#392 list payload is not media-normalized and existing generation gate identity stays intact', async () => {
    const payload = { nodes: [{ title: 'online_title.png' }], count: 1, groups: [] };
    const runner = loadExecutors({ payload });
    const result = await runner.execute('canvas.list_nodes', {});
    assert.deepEqual(JSON.parse(result.content[0].text), payload);
    await runner.execute('generate.trigger', { node_id: 'source-392' });
    await runner.execute('canvas.batch_edit', { node_ids: ['source-392'], prompt: 'new prompt' });
    for (const call of runner.calls.slice(1)) {
        assert.equal(call.url.searchParams.get('agent_node_id'), AGENT);
        assert.equal(call.options.method, 'POST');
        assert.equal(call.options.headers['X-Agent-Project'], PROJECT);
    }
    assert.deepEqual(JSON.parse(runner.calls[1].options.body), { node_id: 'source-392' });
    assert.deepEqual(JSON.parse(runner.calls[2].options.body), { node_ids: ['source-392'], prompt: 'new prompt' });
});

test('#400 Pi batch calls forward scope and options once, with no client-side confirmation', async () => {
    const runner = loadExecutors({ payload: { status: 'awaiting_confirmation' } });
    const cases = {
        preflight: { group_id: 'frame-400' },
        request_batch: { preflight_id: 'p', fallback_models: ['model-2'], max_retries: 0 },
        batch_status: { batch_id: 'b', wait_seconds: 0.5 },
        stop_batch: { batch_id: 'b' },
    };
    for (const [name, args] of Object.entries(cases)) {
        await runner.execute(`generate.${name}`, args);
        const call = runner.calls.at(-1);
        assert.equal(call.url.pathname, `/api/projects/${PROJECT}/generate/${name}`);
        assert.equal(call.url.searchParams.get('agent_node_id'), AGENT);
        assert.equal(call.options.headers['X-Agent-Project'], PROJECT);
        assert.equal(call.options.method, 'POST');
        assert.deepEqual(JSON.parse(call.options.body), args);
    }
    assert.equal(runner.calls.length, 4);
});

test('#401 Pi retry forwards the selector, options and actor to the server gate once', async () => {
    const runner = loadExecutors({ payload: { task_id: 'new-task' } });
    const args = { task_id: 'old-task', use_snapshot: false, model: 'approved-model' };
    await runner.execute('generate.retry', args);
    assert.equal(runner.calls.length, 1);
    const [call] = runner.calls;
    assert.equal(call.url.pathname, `/api/projects/${PROJECT}/generate/retry`);
    assert.equal(call.url.searchParams.get('agent_node_id'), AGENT);
    assert.equal(call.options.headers['X-Agent-Project'], PROJECT);
    assert.equal(call.options.method, 'POST');
    assert.deepEqual(JSON.parse(call.options.body), args);
});

test('#392 list surfaces the shared structured server error without [object Object]', async () => {
    const runner = loadExecutors({ ok: false, payload: { detail: {
        code: 'INVALID_ARGUMENT', message: 'invalid fields', hint: 'use summary', retryable: false,
    } } });
    await assert.rejects(runner.execute('canvas.list_nodes', { fields: ['unknown'] }), (error) => {
        assert.match(error.message, /\[INVALID_ARGUMENT\] invalid fields/);
        assert.match(error.message, /use summary/);
        assert.ok(!error.message.includes('[object Object]'));
        return true;
    });
    assertReadRequest(runner.calls[0]);
});
