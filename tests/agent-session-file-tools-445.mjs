// Standalone SDK conformance, separate from pytest. Run only through run_445.py:
// python D:/ff-qa/a-445-443-464/run_445.py --label <unique> --root <isolated-worktree> --timeout 120 -- node tests/agent-session-file-tools-445.mjs --sdk-entry <SDK>/dist/index.js
// Uses the installed SDK's real extension loader, no CLI/session/model/auth discovery.
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { deflateSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const args = process.argv.slice(2);
const option = name => args[args.indexOf(name) + 1];
assert.ok(args.includes('--sdk-entry'), 'Provide an explicit installed SDK dist/index.js fixture');
const sdkEntry = path.resolve(option('--sdk-entry'));
const sdk = await import(pathToFileURL(sdkEntry).href);
assert.equal(sdk.VERSION, '1.5.1', 'This conformance suite requires the reviewed SDK 1.5.1');
const extensionPath = fileURLToPath(new URL('../app/agent_extensions/canvas-tools.ts', import.meta.url));
const selected = args.includes('--case') ? option('--case') : undefined;
const tests = [];
const limits = [];
const test = (name, run) => tests.push({ name, run });

async function fixture() {
    const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'firefly-files-445-'));
    const root = path.join(temp, 'project', 'agent_nodes', 'node-a', 'sessions');
    const outside = path.join(temp, 'outside');
    await fs.mkdir(root, { recursive: true });
    await fs.mkdir(outside);
    await fs.writeFile(path.join(outside, 'sentinel.txt'), 'EXTERNAL SENTINEL\r\n');
    return { temp, root, outside };
}

async function load(f, root = f.root) {
    if (root === undefined || root === null) delete process.env.FIREFLY_AGENT_WORKSPACE_ROOT;
    else process.env.FIREFLY_AGENT_WORKSPACE_ROOT = root;
    const loaded = await sdk.discoverAndLoadExtensions([extensionPath], f.outside, path.join(f.temp, 'private-agent'));
    assert.deepEqual(loaded.errors, [], 'Real native extension load must succeed');
    assert.equal(loaded.extensions.length, 1);
    const extension = loaded.extensions[0];
    const tools = new Map([...extension.tools].map(([name, registered]) => [name, registered.definition]));
    return {
        tools, extension,
        execute: (name, params, signal, onUpdate) => {
            assert.ok(tools.has(name), `Registered tool ${name} is required`);
            // The SDK itself normally prefers ctx.cwd: it must never become the authority here.
            return tools.get(name).execute('qa-445', params, signal, onUpdate, { cwd: f.outside });
        },
    };
}

const text = result => result.content.filter(block => block.type === 'text').map(block => block.text).join('\n');
const denial = /\[session-file-tools\] Access denied:/;
async function denyAll(api, target) {
    await assert.rejects(api.execute('read', { path: target }), denial, `read ${target}`);
    await assert.rejects(api.execute('write', { path: target, content: 'MUST NOT WRITE' }), denial, `write ${target}`);
    await assert.rejects(api.execute('edit', { path: target, edits: [{ oldText: 'EXTERNAL SENTINEL', newText: 'CORRUPTED' }] }), denial, `edit ${target}`);
}
async function snapshot(dir) {
    const entries = [];
    for (const name of (await fs.readdir(dir)).sort()) {
        const file = path.join(dir, name);
        const stats = await fs.lstat(file);
        assert.equal(stats.isSymbolicLink(), false, 'External fixture must not contain links');
        entries.push([name, stats.isDirectory() ? await snapshot(file) : (await fs.readFile(file)).toString('base64')]);
    }
    return entries;
}

test('registration', async f => {
    const { tools, extension } = await load(f);
    for (const name of ['read', 'write', 'edit']) {
        assert.ok(tools.has(name), `Register restricted ${name}`);
        assert.deepEqual(tools.get(name).parameters, sdk[`create${name[0].toUpperCase()}${name.slice(1)}ToolDefinition`](f.root).parameters);
    }
    for (const name of ['canvas.list_nodes', 'generate.trigger', 'agent.present', 'ops.undo']) assert.ok(tools.has(name));
    for (const name of ['bash', 'powershell', 'codemode', 'exec', 'shell.run', 'ls', 'grep', 'find']) assert.equal(tools.has(name), false);
    assert.equal(extension.commands.size, 0);
});

test('read-text', async f => {
    await fs.writeFile(path.join(f.root, 'notes.txt'), 'alpha\nbeta\ngamma\n');
    const api = await load(f);
    const result = await api.execute('read', { path: 'notes.txt', offset: 2, limit: 1 });
    assert.equal(text(result), 'beta\n\n[2 more lines in file. Use offset=3 to continue.]');
    assert.equal(result.structuredContent, text(result));
});

test('write-text', async f => {
    const api = await load(f);
    const result = await api.execute('write', { path: 'work/新建.txt', content: 'first\n' });
    assert.match(text(result), /Successfully wrote/);
    assert.equal(text(await api.execute('read', { path: 'work/新建.txt' })), 'first\n');
    await api.execute('write', { path: path.join(f.root, 'work/新建.txt'), content: 'rewritten\n' });
    assert.equal(text(await api.execute('read', { path: '@work/新建.txt' })), 'rewritten\n');
});

test('edit-text', async f => {
    await fs.writeFile(path.join(f.root, 'edit.txt'), '\ufeffalpha\r\nbeta\r\ngamma\r\n');
    const api = await load(f);
    const result = await api.execute('edit', { path: 'edit.txt', edits: [
        { oldText: 'alpha\nbeta', newText: 'A\nB' }, { oldText: 'gamma', newText: 'G' },
    ] });
    assert.equal(await fs.readFile(path.join(f.root, 'edit.txt'), 'utf8'), '\ufeffA\r\nB\r\nG\r\n');
    assert.match(text(result), /Successfully replaced 2 block/);
    assert.equal(result.details.firstChangedLine, 1);
    assert.match(result.details.patch, /-alpha/);
    await assert.rejects(api.execute('edit', { path: 'edit.txt', edits: [{ oldText: 'missing', newText: 'X' }] }));
    await api.execute('write', { path: 'ambiguous.txt', content: 'repeat\nrepeat\n' });
    await assert.rejects(api.execute('edit', { path: 'ambiguous.txt', edits: [{ oldText: 'repeat', newText: 'X' }] }));
    assert.equal(text(await api.execute('read', { path: 'ambiguous.txt' })), 'repeat\nrepeat\n');
});

test('outside-denial', async f => {
    const otherNode = path.join(f.temp, 'project/agent_nodes/node-b/sessions');
    const otherProject = path.join(f.temp, 'other-project/agent_nodes/node-a/sessions');
    await fs.mkdir(otherNode, { recursive: true });
    await fs.mkdir(otherProject, { recursive: true });
    for (const dir of [otherNode, otherProject]) await fs.writeFile(path.join(dir, 'sentinel.txt'), 'EXTERNAL SENTINEL\r\n');
    const api = await load(f);
    const before = await Promise.all([f.outside, otherNode, otherProject].map(snapshot));
    const outsideFile = path.join(f.outside, 'sentinel.txt');
    for (const target of [outsideFile, path.join(otherNode, 'sentinel.txt'), path.join(otherProject, 'sentinel.txt'),
        '../sentinel.txt', '../../../../outside/sentinel.txt', '@../sentinel.txt',
        'not-created/../../outside/deep/file.txt', 'not-created/CON', 'not-created/name. ',
        'work/../system-prompt.txt', 'work/../session.jsonl',
        path.join(f.root + '-sibling', 'sentinel.txt'), path.join(f.outside, 'not-created/deep/file.txt'),
        'C:relative.txt', 'D:relative.txt', '\\root-relative.txt', '/etc/passwd', '/d/outside/sentinel.txt',
        '\\\\server\\share\\sentinel.txt', '\\\\?\\C:\\outside\\sentinel.txt', '\\\\.\\NUL',
        'file:///C:/outside/sentinel.txt', '~/sentinel.txt', '~\\sentinel.txt', 'notes.txt:stream',
        'notes.txt::$DATA', 'bad\u0000name', 'bad\nname', 'name.', 'name ', 'SYSTEM~1.TXT', 'WORKSP~1/file.txt',
        'CON', 'nul.txt', 'COM1.log', 'LPT9', 'COM¹.txt', 'conin$', 'conout$.txt', 'clock$',
        'name\u00a0space.txt', '', '@']) {
        await denyAll(api, target);
    }
    assert.deepEqual(await Promise.all([f.outside, otherNode, otherProject].map(snapshot)), before);
    assert.equal(await fs.readFile(outsideFile, 'utf8'), 'EXTERNAL SENTINEL\r\n');
    assert.deepEqual(await fs.readdir(f.root), [], 'Denied writes must not create in-root parents either');
});

test('fixed-root-normal-io', async f => {
    const api = await load(f);
    process.env.FIREFLY_AGENT_WORKSPACE_ROOT = f.outside;
    const before = await snapshot(f.outside);
    const absolute = path.join(f.root, 'work', 'nested', 'Note.txt');
    const nativeSeparators = absolute;
    const forwardSlashes = process.platform === 'win32' ? absolute.replace(/\\/g, '/') : absolute;
    await api.execute('write', { path: './work/nested/Note.txt', content: 'alpha\r\nbeta\r\n' });
    assert.equal(text(await api.execute('read', { path: nativeSeparators })), 'alpha\r\nbeta\r\n');
    await api.execute('edit', { path: forwardSlashes, edits: [{ oldText: 'alpha', newText: 'ALPHA' }] });
    assert.equal(text(await api.execute('read', { path: '@work/nested/Note.txt' })), 'ALPHA\r\nbeta\r\n');
    await api.execute('write', { path: forwardSlashes, content: 'gamma\n' });
    await api.execute('edit', { path: './work/nested/Note.txt', edits: [{ oldText: 'gamma', newText: 'GAMMA' }] });
    assert.equal(text(await api.execute('read', { path: forwardSlashes })), 'GAMMA\n');
    assert.equal(await fs.readFile(absolute, 'utf8'), 'GAMMA\n');
    assert.deepEqual(await snapshot(f.outside), before, 'ctx.cwd and environment changes must not rebind any tool');
});

test('fixed-root-case-sibling-boundary', async f => {
    // fsutil requires an empty directory; only this test's fresh QA tree may change its flag.
    const agentNodes = path.join(f.temp, 'case-project', 'agent_nodes');
    await fs.mkdir(agentNodes, { recursive: true });
    const root = path.join(agentNodes, 'node-a', 'sessions');
    const siblingNode = path.join(agentNodes, 'NODE-A');
    const siblingRoot = path.join(siblingNode, 'sessions');
    const capability = { platform: process.platform, commands: [] };
    if (process.platform === 'win32') {
        for (const operation of [['setCaseSensitiveInfo', agentNodes, 'enable'], ['queryCaseSensitiveInfo', agentNodes]]) {
            const args = ['file', ...operation];
            const result = spawnSync('fsutil.exe', args, { windowsHide: true, timeout: 10000 });
            const receipt = { command: ['fsutil.exe', ...args], status: result.status, signal: result.signal,
                stdoutBase64: result.stdout?.toString('base64'), stderrBase64: result.stderr?.toString('base64'),
                error: result.error?.message };
            capability.commands.push(receipt);
            console.log(JSON.stringify({ caseSensitiveCommand: receipt }));
        }
    }
    await fs.mkdir(root, { recursive: true });
    let distinctSibling = false;
    try {
        await fs.mkdir(siblingNode);
        distinctSibling = true;
    } catch (error) {
        assert.equal(error.code, 'EEXIST', 'Only case-insensitive alias collision is an expected capability limit');
    }
    const sentinel = 'EXTERNAL SENTINEL\r\n';
    await fs.writeFile(path.join(root, 'sentinel.txt'), distinctSibling ? 'INTERNAL SENTINEL\r\n' : sentinel);
    if (distinctSibling) {
        await fs.mkdir(siblingRoot);
        await fs.writeFile(path.join(siblingRoot, 'sentinel.txt'), sentinel, { flag: 'wx' });
        const names = await fs.readdir(agentNodes);
        assert.ok(names.includes('node-a') && names.includes('NODE-A'), 'Both exact sibling names must coexist');
        assert.notEqual((await fs.stat(root)).ino, (await fs.stat(siblingRoot)).ino);
        assert.equal(await fs.readFile(path.join(root, 'sentinel.txt'), 'utf8'), 'INTERNAL SENTINEL\r\n');
        assert.equal(await fs.readFile(path.join(siblingRoot, 'sentinel.txt'), 'utf8'), sentinel);
    } else {
        limits.push({ case: 'fixed-root-case-sibling-boundary', status: 'LIMIT',
            reason: 'Distinct node-a/NODE-A directories unavailable; alias contract tested, no native case-sensitive sibling proof', ...capability });
    }
    console.log(JSON.stringify({ caseSiblingCapability: { ...capability, status: distinctSibling ? 'AVAILABLE' : 'LIMIT', distinctSibling } }));
    const api = await load(f, root);
    process.env.FIREFLY_AGENT_WORKSPACE_ROOT = f.outside;
    assert.equal(text(await api.execute('read', { path: 'sentinel.txt' })), distinctSibling ? 'INTERNAL SENTINEL\r\n' : sentinel);
    await api.execute('write', { path: 'work/inside.txt', content: 'INSIDE' });
    await api.execute('edit', { path: path.join(root, 'work/inside.txt'), edits: [{ oldText: 'INSIDE', newText: 'EDITED INSIDE' }] });
    assert.equal(text(await api.execute('read', { path: 'work/inside.txt' })), 'EDITED INSIDE');
    const before = await Promise.all([root, siblingRoot, f.outside].map(snapshot));
    await denyAll(api, path.join(siblingRoot, 'sentinel.txt'));
    await denyAll(api, path.join(siblingRoot, 'not-created', 'deep', 'new.txt'));
    await denyAll(api, '@' + path.join(siblingRoot, 'sentinel.txt').replace(/\\/g, '/'));
    assert.deepEqual(await Promise.all([root, siblingRoot, f.outside].map(snapshot)), before,
        'Denied read/write/edit must not change sibling bytes or create parents');
    assert.equal(await fs.readFile(path.join(siblingRoot, 'sentinel.txt'), 'utf8'), sentinel);
});

test('fixed-root-case-alias', async f => {
    const api = await load(f);
    const aliases = [
        path.join(path.dirname(path.dirname(f.root)), 'NODE-A', 'sessions'),
        path.join(path.dirname(f.root), 'SESSIONS'),
        f.root.replace(`${path.sep}agent_nodes${path.sep}`, `${path.sep}AGENT_NODES${path.sep}`),
        f.root.replace(`${path.sep}project${path.sep}`, `${path.sep}PROJECT${path.sep}`),
    ];
    if (process.platform === 'win32') aliases.push((f.root[0] === f.root[0].toUpperCase() ? f.root[0].toLowerCase() : f.root[0].toUpperCase()) + f.root.slice(1));
    for (const index of aliases.keys()) {
        for (const name of ['read', 'write', 'edit']) {
            await fs.writeFile(path.join(f.root, `${index}-${name}.txt`), 'EXTERNAL SENTINEL\r\n');
        }
    }
    const before = await snapshot(f.root);
    const accepted = [];
    for (const [index, alias] of aliases.entries()) {
        assert.notEqual(alias, f.root, 'Fixture must change a fixed-root component');
        for (const name of ['read', 'write', 'edit']) {
            const filename = `${index}-${name}.txt`;
            const target = path.join(alias, filename);
            const params = name === 'write' ? { content: 'MUST NOT WRITE' }
                : name === 'edit' ? { edits: [{ oldText: 'EXTERNAL SENTINEL', newText: 'CORRUPTED' }] } : {};
            try {
                const result = await api.execute(name, { path: target, ...params });
                accepted.push({ name, target, result: text(result), bytesAfter: await fs.readFile(path.join(f.root, filename), 'utf8') });
            } catch (error) {
                assert.match(error.message, denial, `${name} must fail closed for ${target}`);
            }
        }
    }
    console.log(JSON.stringify({ caseAliasAccepted: accepted }));
    assert.deepEqual(accepted, [], 'Alternate-case fixed-root components are unsupported even on case-insensitive Windows');
    assert.deepEqual(await snapshot(f.root), before, 'Denied read/write/edit must leave fixture bytes unchanged');
});

test('reserved-name-space-alias', async f => {
    const api = await load(f);
    await denyAll(api, 'CON .txt');
    await denyAll(api, 'NUL .log');
    assert.deepEqual(await fs.readdir(f.root), []);
});

test('protected-control-files', async f => {
    await fs.writeFile(path.join(f.root, 'system-prompt.txt'), 'HOST PROMPT');
    await fs.writeFile(path.join(f.root, 'session.jsonl'), 'NATIVE HISTORY\n');
    const api = await load(f);
    for (const target of ['system-prompt.txt', 'SYSTEM-PROMPT.TXT', 'session.jsonl', 'SESSION.JSONL', 'future.JsOnL',
        path.join(f.root, 'system-prompt.txt'), '@system-prompt.txt']) {
        await assert.rejects(api.execute('write', { path: target, content: 'CORRUPTED' }), denial);
        await assert.rejects(api.execute('edit', { path: target, edits: [{ oldText: 'HOST', newText: 'MODEL' }] }), denial);
    }
    assert.equal(text(await api.execute('read', { path: 'system-prompt.txt' })), 'HOST PROMPT');
    assert.equal(text(await api.execute('read', { path: 'session.jsonl' })), 'NATIVE HISTORY\n');
    assert.equal(await fs.readFile(path.join(f.root, 'system-prompt.txt'), 'utf8'), 'HOST PROMPT');
    assert.equal(await fs.readFile(path.join(f.root, 'session.jsonl'), 'utf8'), 'NATIVE HISTORY\n');
    await api.execute('write', { path: 'work/session.jsonl', content: 'user work' });
    assert.equal(text(await api.execute('read', { path: 'work/session.jsonl' })), 'user work');
});

test('windows-junctions', async f => {
    assert.equal(process.platform, 'win32', 'Windows junction conformance is required, not silently skipped');
    await fs.symlink(f.outside, path.join(f.root, 'escape'), 'junction');
    const before = await snapshot(f.outside);
    const api = await load(f);
    await denyAll(api, 'escape/sentinel.txt');
    await denyAll(api, 'escape/not-created/deep/new.txt');
    await fs.mkdir(path.join(f.temp, 'alias'));
    await fs.symlink(f.root, path.join(f.temp, 'alias/sessions'), 'junction');
    await denyAll(await load(f, path.join(f.temp, 'alias/sessions')), 'new/deep/file.txt');
    await fs.symlink(path.dirname(f.root), path.join(f.temp, 'ancestor-link'), 'junction');
    await denyAll(await load(f, path.join(f.temp, 'ancestor-link/sessions')), 'new/deep/file.txt');
    // An in-root junction is still a link, even when its destination is also in-root.
    await fs.mkdir(path.join(f.root, 'regular'));
    await fs.writeFile(path.join(f.root, 'regular/note.txt'), 'INSIDE');
    await fs.symlink(path.join(f.root, 'regular'), path.join(f.root, 'inside-link'), 'junction');
    await denyAll(api, 'inside-link/note.txt');
    assert.deepEqual(await snapshot(f.outside), before);
    assert.equal(await fs.readFile(path.join(f.root, 'regular/note.txt'), 'utf8'), 'INSIDE');
    assert.equal((await fs.readdir(f.root)).includes('new'), false);
});

test('windows-hardlinks', async f => {
    assert.equal(process.platform, 'win32', 'Windows hardlink conformance is required, not silently skipped');
    await fs.link(path.join(f.outside, 'sentinel.txt'), path.join(f.root, 'hardlink.txt'));
    assert.equal((await fs.stat(path.join(f.root, 'hardlink.txt'))).nlink, 2);
    const before = await snapshot(f.outside);
    const api = await load(f);
    await denyAll(api, 'hardlink.txt');
    await fs.writeFile(path.join(f.root, 'system-prompt.txt'), 'HOST PROMPT');
    await fs.link(path.join(f.root, 'system-prompt.txt'), path.join(f.root, 'innocent.txt'));
    await denyAll(api, 'innocent.txt');
    assert.equal(await fs.readFile(path.join(f.root, 'system-prompt.txt'), 'utf8'), 'HOST PROMPT');
    assert.deepEqual(await snapshot(f.outside), before);
});

test('native-media-stream-abort', async f => {
    // A real 1x1 RGBA PNG, with correct zlib data and chunk CRCs (no image/tool stubs).
    const chunk = (type, data) => {
        const bytes = Buffer.concat([Buffer.from(type), data]);
        let crc = 0xffffffff;
        for (const byte of bytes) {
            crc ^= byte;
            for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
        }
        const length = Buffer.alloc(4), checksum = Buffer.alloc(4);
        length.writeUInt32BE(data.length);
        checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
        return Buffer.concat([length, bytes, checksum]);
    };
    const header = Buffer.from([0, 0, 0, 1, 0, 0, 0, 1, 8, 6, 0, 0, 0]);
    const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
        chunk('IDAT', deflateSync(Buffer.from([0, 255, 0, 0, 255]))), chunk('IEND', Buffer.alloc(0))]);
    await fs.writeFile(path.join(f.root, 'image-without-extension'), png);
    const api = await load(f);
    const result = await api.execute('read', { path: 'image-without-extension' });
    const native = await sdk.createReadToolDefinition(f.root).execute('native-oracle', { path: 'image-without-extension' });
    assert.deepEqual(result, native, 'Native PNG processing/result/structured output must be unchanged');
    assert.equal(result.content.find(block => block.type === 'image')?.mimeType, 'image/png', text(result));
    assert.equal(result.structuredContent.type, 'image');
    await fs.writeFile(path.join(f.root, 'large.txt'), Array.from({ length: 5000 }, (_, n) => `line-${n + 1}`).join('\n'));
    assert.deepEqual(await api.execute('read', { path: 'large.txt' }),
        await sdk.createReadToolDefinition(f.root).execute('native-oracle', { path: 'large.txt' }));
    assert.equal(text(await api.execute('read', { path: 'large.txt', offset: 4999, limit: 2 })), 'line-4999\nline-5000');
    const signal = AbortSignal.abort();
    await assert.rejects(api.execute('read', { path: 'large.txt' }, signal), /aborted/i);
    await assert.rejects(api.execute('write', { path: 'aborted/deep/file.txt', content: 'NO' }, signal), /aborted/i);
    await assert.rejects(api.execute('edit', { path: 'large.txt', edits: [{ oldText: 'line-1\n', newText: 'NO\n' }] }, signal), /aborted/i);
    assert.equal((await fs.readdir(f.root)).includes('aborted'), false);
    assert.equal((await fs.readFile(path.join(f.root, 'large.txt'), 'utf8')).startsWith('line-1\n'), true);
});

test('immutable-root-and-reload', async f => {
    const first = await load(f);
    await fs.writeFile(path.join(f.root, 'note.txt'), 'FIXED ROOT');
    await fs.writeFile(path.join(f.outside, 'note.txt'), 'NOT THE ROOT');
    process.env.FIREFLY_AGENT_WORKSPACE_ROOT = f.outside;
    assert.equal(text(await first.execute('read', { path: 'note.txt' })), 'FIXED ROOT');
    // Native new/resume/reload factories are reloaded by this same loader; old tools retain their own authority.
    for (let repeat = 0; repeat < 3; repeat++) {
        const reloaded = await load(f);
        assert.equal(text(await reloaded.execute('read', { path: 'note.txt' })), 'FIXED ROOT');
        await denyAll(reloaded, path.join(f.outside, 'note.txt'));
    }
    const noRoot = await load(f, null);
    for (const name of ['read', 'write', 'edit', 'bash', 'powershell', 'codemode']) assert.equal(noRoot.tools.has(name), false);
    assert.ok(noRoot.tools.has('canvas.list_nodes'), 'Canvas API remains available without file authority');
    for (const invalid of ['relative/sessions', f.outside, path.join(f.temp, 'missing/sessions'), 'D:relative/sessions']) {
        await denyAll(await load(f, invalid), 'note.txt');
    }
    assert.equal(await fs.readFile(path.join(f.outside, 'note.txt'), 'utf8'), 'NOT THE ROOT');
});

let passed = 0;
let failed = 0;
for (const { name, run } of tests.filter(test => !selected || test.name === selected)) {
    const f = await fixture();
    try {
        await run(f);
        passed++;
        console.log(`PASS ${name}`);
    } catch (error) {
        failed++;
        console.error(`FAIL ${name}\n${error.stack}`);
    } finally {
        delete process.env.FIREFLY_AGENT_WORKSPACE_ROOT;
        await fs.rm(f.temp, { recursive: true, force: true });
    }
}
assert.ok(passed + failed > 0, 'No matching conformance case');
const sourceSha256 = {};
for (const source of [fileURLToPath(import.meta.url), extensionPath, path.join(path.dirname(extensionPath), 'session-file-tools.ts'),
    sdkEntry, ...['read', 'write', 'edit', 'path-utils'].map(name => path.join(path.dirname(sdkEntry), 'core/tools', `${name}.js`)),
    path.join(path.dirname(sdkEntry), 'core/extensions/loader.js')]) {
    sourceSha256[source] = createHash('sha256').update(await fs.readFile(source)).digest('hex');
}
console.log(JSON.stringify({ sdkEntry, sdkVersion: sdk.VERSION, platform: process.platform, passed, failed, limits, sourceSha256,
    proof: 'real SDK loader + registered execute + temporary filesystem; not CLI/model/OS sandbox proof' }));
process.exitCode = failed ? 1 : 0;
