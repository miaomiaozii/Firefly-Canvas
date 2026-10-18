// #194 canvas-node-types.js 行为单元测试。
// 防的回归：8 个 NODE_TYPE_* 常量的取值漂移。这些值是前后端之间的契约
// （后端 app/constants.py 同名常量，parity 由 tests/test_node_type_constants.py 锁），
// 且被 canvas-core.js 的 isSmartImageNode/isSmartGroupNode 与 project-canvas-mutations.js
// 的 UMD require 路径消费。这里测的是「加载进共享全局作用域后读到的真实值」，
// 而不是源码文本。
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, evaluate, JS_DIR } from './harness.mjs';

const EXPECTED = {
    NODE_TYPE_PROMPT: 'smart-prompt',
    NODE_TYPE_IMAGE: 'smart-image',
    NODE_TYPE_AGENT: 'smart-agent',
    NODE_TYPE_CONVERSATION: 'smart-conversation',
    NODE_TYPE_TASK: 'agent-task',
    NODE_TYPE_LOOP: 'smart-loop',
    NODE_TYPE_GROUP: 'smart-group',
    NODE_TYPE_LOCAL_EDIT: 'local-edit',
};

test('all 8 NODE_TYPE_* constants carry the exact contract values', () => {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js']);
    for (const [name, value] of Object.entries(EXPECTED)) {
        assert.equal(evaluate(ctx, name), value, `${name} must be ${JSON.stringify(value)}`);
    }
});

test('FireflyNodeTypes is exposed on globalThis with exactly the 8 contract keys', () => {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js']);
    const types = ctx.FireflyNodeTypes;
    assert.ok(types && typeof types === 'object', 'globalThis.FireflyNodeTypes must exist (top-level const is not a globalThis property)');
    assert.deepEqual(Object.keys(types).sort(), Object.keys(EXPECTED).sort());
    for (const [name, value] of Object.entries(EXPECTED)) {
        assert.equal(types[name], value);
    }
});

test('constants are visible to later scripts in the same shared lexical scope', () => {
    // 这是 classic script 共享全局词法作用域的核心契约：canvas-core.js 的
    // isSmartImageNode 读 NODE_TYPE_IMAGE（它自己没声明）。同 context 顺序加载后，
    // 后加载文件必须看得见先加载文件的顶层 const。
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js']);
    const isSmartImageNode = ctx.isSmartImageNode;
    assert.equal(typeof isSmartImageNode, 'function');
    assert.equal(isSmartImageNode({ type: 'smart-image' }), true);
    assert.equal(isSmartImageNode({ type: 'smart-prompt' }), false);
    assert.equal(isSmartImageNode(null), false);
});

test('require() path (UMD) exports the same FireflyNodeTypes object shape', () => {
    // project-canvas-mutations.js 通过 require('./canvas-node-types.js') 读同一份值。
    // 用 node -e 走 CommonJS require 验证 .js 在两种加载器下都可用（现有
    // tests/test_project_canvas_mutation_client.py 依赖这条路径）。
    const out = execFileSync(
        process.execPath,
        ['-e', `const t = require(${JSON.stringify(JS_DIR + '/canvas-node-types.js')}); console.log(JSON.stringify(Object.keys(t).sort()));`],
        { encoding: 'utf8' },
    );
    assert.equal(out.trim(), JSON.stringify(Object.keys(EXPECTED).sort()));
});
