import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createCanvasContext, loadModules, HTML_SCRIPT_ORDER, ROOT, evaluate } from './harness.mjs';

function loadProductModules(ctx, names){
    for(const name of HTML_SCRIPT_ORDER.filter(name => names.includes(name))){
        const flag = name === 'canvas-agent-viewport.js' ? '--viewport-source=' : name === 'smart-canvas.js' ? '--smart-source=' : null;
        const source = flag && process.argv.find(arg => arg.startsWith(flag));
        if(source) vm.runInContext(readFileSync(source.slice(flag.length), 'utf8'), ctx);
        else loadModules(ctx, [name]);
    }
}

function setup(){
    const { ctx, harness } = createCanvasContext();
    loadProductModules(ctx, ['canvas-agent-viewport.js']);
    const shell = harness.element('shell');
    shell.getBoundingClientRect = () => ({left:100, top:50, width:1000, height:600});
    const world = harness.element('world');
    const nodes = [{id:'target', type:'smart-prompt', x:9999, y:8888, w:1, h:1}];
    const element = harness.makeElement();
    element.dataset.id = 'target';
    element.isConnected = true;
    element.getClientRects = () => [element.getBoundingClientRect()];
    element.getBoundingClientRect = () => ({left:1500, top:900, width:400, height:200});
    world.querySelectorAll = selector => {
        assert.equal(selector, '.image-node[data-id]');
        return [element];
    };
    let project = 'project-A';
    const viewport = {x:40, y:-20, scale:2};
    const applied = [];
    const controller = ctx.FireflyAgentViewport.create({
        getProjectId: () => project,
        getViewport: () => viewport,
        getNodes: () => nodes,
        shell, world,
        applyViewport: next => {
            Object.assign(viewport, next);
            world.style.transform = `translate(${next.x}px, ${next.y}px) scale(${next.scale})`;
            applied.push({...next});
        },
    });
    const peer = () => ({readyState:1, sent:[], send(text){ this.sent.push(JSON.parse(text)); }});
    const socket = peer();
    const receive = (data, connection=socket) => controller.handleMessage(connection, data);
    const handshake = () => {
        controller.connect(socket);
        controller.ready();
        receive({type:'canvas_viewport_ready_ack', project_id:project, generation:'generation-1'});
    };
    const command = (kind, fields={}) => ({type:'canvas_viewport_command', project_id:project,
        generation:'generation-1', command_id:'command-1', command:kind, ...fields});
    return {ctx, harness, shell, world, nodes, element, viewport, applied, controller, peer, socket,
        receive, handshake, command, setProject: value => { project = value; }};
}

// R18/R34: readiness is explicit and never applies a view.
test('ready advertises only a loaded project on the connected open socket', () => {
    const s = setup();
    s.controller.connect(s.socket);
    assert.deepEqual(s.socket.sent, []);
    s.controller.ready();
    s.controller.ready();
    assert.deepEqual(s.socket.sent, [{type:'canvas_viewport_ready', project_id:'project-A'}]);
    assert.deepEqual(s.applied, []);
});

// R00/R20/R23 (VM only): current client bounds, not persisted node geometry.
test('focus centers the rendered target at current scale and ACKs only after drawing', () => {
    const s = setup();
    s.handshake();
    const before = JSON.stringify(s.nodes);
    s.receive(s.command('focus', {node_id:'target'}));
    assert.deepEqual(s.viewport, {x:-1060, y:-670, scale:2});
    assert.equal(s.socket.sent.length, 1, 'no success before the drawing observation');
    s.harness.flushRafs();
    assert.deepEqual(s.socket.sent.at(-1), {type:'canvas_viewport_ack', project_id:'project-A',
        generation:'generation-1', command_id:'command-1', command:'focus', status:'applied',
        viewport:{x:-1060, y:-670, scale:2}});
    assert.equal(JSON.stringify(s.nodes), before);
});

// R01/R24/R25: shell center currently denotes world (230, 160).
test('zoom sets an absolute level and preserves the shell center world point', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('zoom', {level:1.75}));
    assert.deepEqual(s.viewport, {x:97.5, y:20, scale:1.75});
    assert.equal(s.socket.sent.length, 1);
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).status, 'applied');
    assert.deepEqual(s.socket.sent.at(-1).viewport, {x:97.5, y:20, scale:1.75});
    s.receive(s.command('zoom', {command_id:'same-level', level:1.75}));
    s.harness.flushRafs();
    assert.deepEqual(s.viewport, {x:97.5, y:20, scale:1.75});
});

// R29: a docked Agent is outside world and must not be silently undocked.
test('focus rejects a docked Agent without moving or changing its model', () => {
    const s = setup();
    Object.assign(s.nodes[0], {type:'smart-agent', dock:'left'});
    const before = JSON.stringify(s.nodes);
    s.handshake();
    s.receive(s.command('focus', {node_id:'target'}));
    s.harness.flushRafs();
    assert.deepEqual(s.socket.sent.at(-1), {type:'canvas_viewport_ack', project_id:'project-A',
        generation:'generation-1', command_id:'command-1', command:'focus', status:'rejected', reason:'DOCKED_TARGET'});
    assert.deepEqual(s.applied, []);
    assert.equal(JSON.stringify(s.nodes), before);
});

test('focus rejects a node deleted after dispatch even if stale DOM remains', () => {
    const s = setup();
    s.handshake();
    s.nodes.length = 0;
    s.receive(s.command('focus', {node_id:'target'}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).reason, 'NODE_NOT_FOUND');
    assert.deepEqual(s.applied, []);
});

for(const shape of ['no DOM', 'detached', 'no client rect', 'zero width', 'zero height', 'nonfinite']){
    test(`focus rejects a nonrenderable target: ${shape}`, () => {
        const s = setup();
        if(shape === 'no DOM') s.world.querySelectorAll = () => [];
        if(shape === 'detached') s.element.isConnected = false;
        if(shape === 'no client rect') s.element.getClientRects = () => [];
        if(shape === 'zero width') s.element.getBoundingClientRect = () => ({left:1500, top:900, width:0, height:200});
        if(shape === 'zero height') s.element.getBoundingClientRect = () => ({left:1500, top:900, width:400, height:0});
        if(shape === 'nonfinite') s.element.getBoundingClientRect = () => ({left:NaN, top:900, width:400, height:200});
        s.handshake();
        assert.doesNotThrow(() => s.receive(s.command('focus', {node_id:'target'})));
        s.harness.flushRafs();
        assert.equal(s.socket.sent.at(-1).reason, 'TARGET_NOT_RENDERABLE');
        assert.deepEqual(s.applied, []);
    });
}

// R26: this is browser input defense, not REST/MCP validation coverage.
for(const [label, level] of [['bool',true], ['string','1.75'], ['null',null], ['NaN',NaN],
    ['Infinity',Infinity], ['below',0.09], ['above',8.01], ['missing',undefined]]){
    test(`zoom rejects invalid level: ${label}`, () => {
        const s = setup();
        s.handshake();
        s.receive(s.command('zoom', {level}));
        s.harness.flushRafs();
        assert.equal(s.socket.sent.at(-1).reason, 'INVALID_ARGUMENT');
        assert.deepEqual(s.applied, []);
    });
}

for(const node_id of ['', '   ', true, 42, null, undefined]){
    test(`focus rejects invalid node_id: ${JSON.stringify(node_id)}`, () => {
        const s = setup();
        s.handshake();
        s.receive(s.command('focus', {node_id}));
        assert.equal(s.socket.sent.at(-1).reason, 'INVALID_ARGUMENT');
        assert.deepEqual(s.applied, []);
    });
}

// R11/R16: a duplicate ID never re-applies, including a changed action.
test('duplicate command IDs neither apply twice nor acquire a different meaning', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('zoom', {level:1.75}));
    s.receive(s.command('focus', {node_id:'target'}));
    s.harness.flushRafs();
    s.receive(s.command('zoom', {level:8}));
    s.harness.flushRafs();
    assert.deepEqual(s.applied, [{x:97.5, y:20, scale:1.75}]);
    assert.equal(s.socket.sent.length, 2);
});

test('a command while drawing is rejected rather than queued or applied', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('zoom', {level:1.75}));
    s.receive(s.command('zoom', {command_id:'busy-command', level:8}));
    assert.equal(s.socket.sent.at(-1).reason, 'NOT_READY');
    s.harness.flushRafs();
    assert.deepEqual(s.applied, [{x:97.5, y:20, scale:1.75}]);
    s.receive(s.command('state', {command_id:'after-draw'}));
    assert.equal(s.socket.sent.at(-1).status, 'observed');
});

// R10/R16/R19: only the exact handshake authorizes a command.
for(const fault of ['socket', 'project', 'generation', 'empty-id', 'bool-id', 'long-id', 'empty-generation', 'changed-ready-generation']){
    test(`invalid command identity is ignored: ${fault}`, () => {
        const s = setup();
        s.handshake();
        const command = s.command('zoom', {level:1.75});
        let socket = s.socket;
        if(fault === 'socket') socket = s.peer();
        if(fault === 'project') command.project_id = 'project-B';
        if(fault === 'generation') command.generation = 'old-generation';
        if(fault === 'empty-id') command.command_id = '';
        if(fault === 'bool-id') command.command_id = true;
        if(fault === 'long-id') command.command_id = 'x'.repeat(1025);
        if(fault === 'empty-generation'){
            s.receive({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:''});
            command.generation = '';
        }
        if(fault === 'changed-ready-generation'){
            s.receive({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'replacement'});
            command.generation = 'replacement';
        }
        s.receive(command, socket);
        s.harness.flushRafs();
        assert.deepEqual(s.applied, []);
        assert.equal(s.socket.sent.length, 1);
    });
}

test('reconnect requires a fresh handshake and drops the old drawing receipt without replay', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('zoom', {level:1.75}));
    s.controller.disconnect(s.socket);
    const next = s.peer();
    s.controller.connect(next);
    s.harness.flushRafs();
    assert.equal(s.socket.sent.length, 1, 'old socket gets no late receipt');
    assert.deepEqual(next.sent, [{type:'canvas_viewport_ready', project_id:'project-A'}]);
    s.receive(s.command('zoom', {command_id:'before-handshake', level:8}), next);
    assert.equal(s.applied.length, 1);
    s.receive({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'generation-2'}, next);
    s.receive(s.command('zoom', {command_id:'old-generation', level:8}), next);
    assert.equal(s.applied.length, 1);
    s.receive(s.command('state', {generation:'generation-2'}), next);
    assert.equal(next.sent.at(-1).status, 'observed');
    assert.equal(next.sent.at(-1).generation, 'generation-2');
    assert.equal(s.applied.length, 1);
});

test('destroy cancels pending observation and permanently disables remote control', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('zoom', {level:1.75}));
    s.controller.destroy();
    s.harness.flushRafs();
    s.controller.connect(s.socket);
    s.controller.ready();
    s.receive(s.command('focus', {command_id:'after-destroy', node_id:'target'}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.length, 1);
    assert.equal(s.applied.length, 1);
});

test('malformed and oversized wire data do not kill the next valid serialized message', () => {
    const s = setup();
    s.handshake();
    for(const data of ['{bad', ' '.repeat(16385), null, [], {type:'unrelated'}, {type:'canvas_viewport_command'}]){
        assert.doesNotThrow(() => s.receive(data));
    }
    s.receive(JSON.stringify(s.command('state')));
    assert.equal(s.socket.sent.at(-1).status, 'observed');
    assert.deepEqual(s.applied, []);
});

test('deduplication fails closed at a bounded generation budget rather than evicting old IDs', () => {
    const s = setup();
    s.handshake();
    for(let i=0; i<4096; i++) s.receive(s.command('state', {command_id:`receipt-${i}`}));
    s.receive(s.command('zoom', {command_id:'overflow', level:8}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).reason, 'NOT_READY');
    s.receive(s.command('zoom', {command_id:'receipt-0', level:8}));
    s.harness.flushRafs();
    assert.deepEqual(s.applied, []);
});

async function production({embedded=false, metaStatus=200}={}){
    const {ctx, harness} = createCanvasContext({locationSearch:'?id=project-A&project_mode=1'});
    const html = readFileSync(`${ROOT}/static/smart-canvas.html`, 'utf8');
    for(const [, id] of html.matchAll(/\bid="([^"]+)"/g)) harness.element(id);
    const shell = harness.element('shell');
    shell.getBoundingClientRect = () => ({left:0, top:0, width:1000, height:600});
    shell.clientWidth = 1000;
    shell.clientHeight = 600;
    ctx.getComputedStyle = () => ({display:'none', getPropertyValue:() => ''});
    ctx.BroadcastChannel = class { close(){} };
    if(embedded) ctx.parent = {};
    const sockets = [];
    ctx.WebSocket = class {
        constructor(){ this.readyState = 1; this.sent = []; sockets.push(this); }
        send(text){ this.sent.push(JSON.parse(text)); }
        close(){}
    };
    let resolveProject;
    ctx.fetch = async url => {
        if(url === '/api/canvases/project-A') return new Promise(resolve => { resolveProject = resolve; });
        if(url === '/api/canvases/project-A/meta') return {ok:metaStatus === 200, status:metaStatus, json:async () => ({})};
        return {ok:true, json:async () => ({})};
    };
    harness.store.set('firefly_canvas_viewport_v1:project-A', JSON.stringify({x:120,y:-70,scale:1.25}));
    loadProductModules(ctx, HTML_SCRIPT_ORDER);
    const loading = ctx.onload();
    for(let i=0; i<100 && !resolveProject; i++) await Promise.resolve();
    assert.equal(typeof resolveProject, 'function', 'project request reached');
    return {ctx, harness, sockets, async failInitial(){
        resolveProject({ok:false, status:500});
        await loading;
        assert.equal(evaluate(ctx, 'canvasLoadReady'), false);
        harness.flushRafs();
    }, async recover(){
        const previous = resolveProject;
        harness.element('smartRecoveryRetry').click();
        for(let i=0; i<100 && resolveProject === previous; i++) await Promise.resolve();
        assert.notEqual(resolveProject, previous, 'Retry makes a new public project request');
        resolveProject({ok:true, json:async () => ({canvas:{id:'project-A', format:'firefly-canvas-v1', nodes:[], connections:[], history:[], revision:1}})});
        for(let i=0; i<100 && !evaluate(ctx, 'canvasLoadReady'); i++) await Promise.resolve();
        assert.equal(evaluate(ctx, 'canvasLoadReady'), true, 'Retry finishes a successful production load');
        await Promise.resolve();
        harness.flushRafs();
    }, async finish(){
        resolveProject({ok:true, json:async () => ({canvas:{id:'project-A', format:'firefly-canvas-v1', nodes:[], connections:[], history:[], revision:1}})});
        await loading;
        assert.equal(evaluate(ctx, 'canvasLoadReady'), true, 'production project loaded successfully');
        harness.flushRafs();
    }};
}

test('production recovery Retry enables fresh state and subsequent reconnect after initial load failure', async () => {
    const p = await production();
    const socket = p.sockets[0];
    socket.onopen();
    await p.failInitial();
    assert.deepEqual(socket.sent, []);
    await p.recover();
    assert.deepEqual(socket.sent, [{type:'canvas_viewport_ready', project_id:'project-A'}]);
    socket.onmessage({data:JSON.stringify({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'recovered'})});
    socket.onmessage({data:JSON.stringify({type:'canvas_viewport_command', project_id:'project-A', generation:'recovered', command_id:'recovered-state', command:'state'})});
    assert.deepEqual(socket.sent.at(-1).viewport, {x:120,y:-70,scale:1.25});
    socket.onclose({code:1006});
    for(let i=0; i<20; i++) await Promise.resolve();
    const retry = p.harness.timers.find(t => t.ms === 3000 && !t.cancelled);
    assert.ok(retry);
    retry.fn();
    p.sockets[1].onopen();
    assert.deepEqual(p.sockets[1].sent, [{type:'canvas_viewport_ready', project_id:'project-A'}]);
});

test('production stats socket stays unready until project rendering and local viewport restore finish', async () => {
    const {ctx, harness, sockets, finish} = await production();
    assert.equal(sockets.length, 1);
    const socket = sockets[0];
    socket.onopen?.();
    assert.deepEqual(socket.sent, []);
    await finish();
    assert.deepEqual(socket.sent, [{type:'canvas_viewport_ready', project_id:'project-A'}]);
    socket.onmessage({data:JSON.stringify({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'production-generation'})});
    socket.onmessage({data:JSON.stringify({type:'canvas_viewport_command', project_id:'project-A', generation:'production-generation', command_id:'production-state', command:'state'})});
    assert.deepEqual(socket.sent.at(-1).viewport, {x:120,y:-70,scale:1.25});
    assert.equal(evaluate(ctx, 'world.style.transform'), 'translate(120px, -70px) scale(1.25)');
});

for(const kind of ['focus', 'zoom', 'state']){
    test(`invalid authoritative snapshot is never coerced or applied: ${kind}`, () => {
        const s = setup();
        s.handshake();
        s.viewport.x = '40';
        assert.doesNotThrow(() => s.receive(s.command(kind, {node_id:'target', level:1.75})));
        s.harness.flushRafs();
        assert.equal(s.socket.sent.at(-1).reason, 'NOT_READY');
        assert.deepEqual(s.applied, []);
    });
}

for(const kind of ['focus', 'zoom']){
    test(`nonrenderable shell cannot claim an applied command: ${kind}`, () => {
        const s = setup();
        s.handshake();
        s.shell.getBoundingClientRect = () => ({left:100,top:50,width:0,height:600});
        s.receive(s.command(kind, {node_id:'target', level:1.75}));
        s.harness.flushRafs();
        assert.equal(s.socket.sent.at(-1).reason, 'NOT_READY');
        assert.deepEqual(s.applied, []);
    });
}

test('a closed connection cannot apply commands', () => {
    const s = setup();
    s.handshake();
    s.socket.readyState = 3;
    s.receive(s.command('zoom', {level:8}));
    s.harness.flushRafs();
    assert.deepEqual(s.applied, []);
    assert.equal(s.socket.sent.length, 1);
});

test('transport send errors are contained and do not prevent the next live observation', () => {
    const s = setup();
    s.controller.connect(s.socket);
    const originalSend = s.socket.send;
    s.socket.send = () => { throw new Error('external transport failure'); };
    assert.doesNotThrow(() => s.controller.ready());
    s.socket.send = originalSend;
    s.controller.ready();
    s.receive({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'generation-1'});
    s.socket.send = () => { throw new Error('external transport failure'); };
    assert.doesNotThrow(() => s.receive(s.command('state')));
    s.socket.send = originalSend;
    s.receive(s.command('state', {command_id:'next-live'}));
    assert.equal(s.socket.sent.at(-1).status, 'observed');
});

// R20/R21: literal client bounds deliberately disagree with persisted node w/h/x/y.
for(const kind of ['prompt', 'image', 'group', 'frame', 'resized', 'floating-agent']){
    test(`focus uses current DOM geometry for ${kind}`, () => {
        const s = setup();
        s.nodes[0].type = {'prompt':'smart-prompt','image':'smart-image','group':'smart-group',
            'frame':'smart-group','resized':'smart-image','floating-agent':'smart-agent'}[kind];
        if(kind === 'frame') s.nodes[0].layoutFrame = true;
        s.element.getBoundingClientRect = () => ({left:1500,top:900,width:600,height:400});
        s.handshake();
        s.receive(s.command('focus', {node_id:'target'}));
        s.harness.flushRafs();
        assert.deepEqual(s.socket.sent.at(-1).viewport, {x:-1160,y:-770,scale:2});
    });
}
for(const node_id of ['quote"id', 'back\\slash', 'white space', '中文节点', 'a][data-id="wrong', '#.>:+*']){
    test(`focus matches an opaque ID safely: ${node_id}`, () => {
        const s = setup();
        s.nodes[0].id = node_id;
        s.element.dataset.id = node_id;
        s.handshake();
        s.receive(s.command('focus', {node_id}));
        s.harness.flushRafs();
        assert.deepEqual(s.socket.sent.at(-1).viewport, {x:-1060,y:-670,scale:2});
    });
}

test('focus rejects IDs above the shared 512-codepoint wire budget', () => {
    const s = setup();
    const node_id = '界'.repeat(513);
    s.nodes[0].id = node_id;
    s.element.dataset.id = node_id;
    s.handshake();
    s.receive(s.command('focus', {node_id}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).reason, 'INVALID_ARGUMENT');
    assert.deepEqual(s.applied, []);
});

test('focus accepts the inclusive 512-codepoint budget with astral Unicode IDs', () => {
    const s = setup();
    const node_id = '🪲'.repeat(512);
    s.nodes[0].id = node_id;
    s.element.dataset.id = node_id;
    s.handshake();
    s.receive(s.command('focus', {node_id}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).status, 'applied');
    assert.deepEqual(s.socket.sent.at(-1).viewport, {x:-1060,y:-670,scale:2});
});

test('production reconnect handshakes anew and stale socket callbacks cannot affect it', async () => {
    const p = await production();
    const old = p.sockets[0];
    old.onopen();
    await p.finish();
    old.onclose({code:1006});
    for(let i=0; i<20; i++) await Promise.resolve();
    const retry = p.harness.timers.find(t => t.ms === 3000 && !t.cancelled);
    assert.ok(retry, 'normal disconnect retains the 3000ms retry');
    retry.fn();
    const next = p.sockets[1];
    next.onopen();
    assert.deepEqual(next.sent, [{type:'canvas_viewport_ready', project_id:'project-A'}]);
    old.onclose({code:1013});
    old.onmessage({data:JSON.stringify({type:'canvas_viewport_command', project_id:'project-A', generation:'old', command_id:'late', command:'zoom', level:8})});
    assert.equal(evaluate(p.ctx, 'canvasProjectGone'), false);
    assert.equal(evaluate(p.ctx, 'viewport.scale'), 1.25);
});
for(const closure of ['meta404', '1013', 'unload']){
    test(`production teardown prevents remote commands and reconnect: ${closure}`, async () => {
        const p = await production({metaStatus:closure === 'meta404' ? 404 : 200});
        const socket = p.sockets[0];
        socket.onopen();
        await p.finish();
        if(closure === 'unload'){
            for(const fn of p.harness.windowListeners.get('beforeunload') || []) fn();
            socket.onclose({code:1006});
        } else socket.onclose({code:closure === '1013' ? 1013 : 1006});
        for(let i=0; i<20; i++) await Promise.resolve();
        socket.onmessage({data:JSON.stringify({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'late'})});
        socket.onmessage({data:JSON.stringify({type:'canvas_viewport_command', project_id:'project-A', generation:'late', command_id:'late', command:'zoom', level:8})});
        p.harness.flushRafs();
        assert.equal(evaluate(p.ctx, 'viewport.scale'), 1.25);
        assert.equal(p.harness.timers.filter(t => t.ms === 3000 && !t.cancelled).length, 0);
        if(closure !== 'unload') assert.equal(evaluate(p.ctx, 'canvasProjectGone'), true);
    });
}
test('embedded production canvas remains outside remote viewport candidacy', async () => {
    const p = await production({embedded:true});
    await p.finish();
    assert.deepEqual(p.sockets, []);
    assert.equal(evaluate(p.ctx, 'viewport.scale'), 1.25);
});

test('focus rejects a visibility-hidden target even when it still has bounds', () => {
    const s = setup();
    s.ctx.getComputedStyle = () => ({visibility:'hidden'});
    s.handshake();
    s.receive(s.command('focus', {node_id:'target'}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).reason, 'TARGET_NOT_RENDERABLE');
    assert.deepEqual(s.applied, []);
});

for(const [level, expected] of [[0.1,{x:477,y:284,scale:0.1}], [8,{x:-1340,y:-980,scale:8}]]){
    test(`absolute zoom accepts the inclusive boundary ${level}`, () => {
        const s = setup();
        s.handshake();
        s.receive(s.command('zoom', {level}));
        s.harness.flushRafs();
        assert.deepEqual(s.socket.sent.at(-1).viewport, expected);
    });
}
test('drawing receipt reports the fresh observed viewport rather than requested coordinates', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('zoom', {level:1.75}));
    Object.assign(s.viewport, {x:121,y:-2,scale:1.6});
    s.harness.flushRafs();
    assert.deepEqual(s.socket.sent.at(-1).viewport, {x:121,y:-2,scale:1.6});
});

test('finite inputs whose focus arithmetic overflows cannot poison the real viewport', () => {
    const s = setup();
    s.viewport.x = Number.MAX_VALUE;
    s.element.getBoundingClientRect = () => ({left:-Number.MAX_VALUE,top:900,width:400,height:200});
    s.handshake();
    s.receive(s.command('focus', {node_id:'target'}));
    s.harness.flushRafs();
    assert.equal(s.socket.sent.at(-1).reason, 'NOT_READY');
    assert.deepEqual(s.applied, []);
});

for(const gate of ['before-load', 'before-ready-ack', 'project-changed']){
    test(`commands cannot bypass the readiness gate: ${gate}`, () => {
        const s = setup();
        s.controller.connect(s.socket);
        if(gate !== 'before-load') s.controller.ready();
        if(gate !== 'before-ready-ack') s.receive({type:'canvas_viewport_ready_ack', project_id:'project-A', generation:'generation-1'});
        const command = s.command('zoom', {level:8});
        if(gate === 'project-changed') s.setProject('project-B');
        s.receive(command);
        s.harness.flushRafs();
        assert.deepEqual(s.applied, []);
    });
}
test('oversized but otherwise valid commands are not parsed into viewport work', () => {
    const s = setup();
    s.handshake();
    s.receive(JSON.stringify(s.command('zoom', {level:8, padding:'x'.repeat(16384)})));
    s.harness.flushRafs();
    assert.deepEqual(s.applied, []);
    s.receive(s.command('state', {command_id:'after-oversized'}));
    assert.equal(s.socket.sent.at(-1).status, 'observed');
});

// R02/R05/R28/R34: the observation is not a cached command or a default.
test('state returns a fresh finite observation without applying a viewport', () => {
    const s = setup();
    s.handshake();
    s.receive(s.command('state'));
    assert.deepEqual(s.socket.sent.at(-1), {type:'canvas_viewport_ack', project_id:'project-A',
        generation:'generation-1', command_id:'command-1', command:'state', status:'observed',
        viewport:{x:40, y:-20, scale:2}});
    Object.assign(s.viewport, {x:149.25, y:78.5, scale:12});
    s.receive(s.command('state', {command_id:'command-2'}));
    assert.deepEqual(s.socket.sent.at(-1).viewport, {x:149.25, y:78.5, scale:12});
    assert.deepEqual(s.applied, []);
});

test('large ordinary stats updates retain legacy Agent undo handling without moving the view', async () => {
    const p = await production();
    const socket = p.sockets[0];
    socket.onopen();
    await p.finish();
    const update = JSON.stringify({type:'canvas_updated', canvas_id:'project-A', source:'agent',
        phase:'start', op_id:'large-arrangement', touched_node_ids:Array.from({length:1500}, (_, i) => `arranged-node-${i}`)});
    assert.ok(update.length > 16384);
    socket.onmessage({data:update});
    p.harness.flushRafs();
    assert.equal(evaluate(p.ctx, 'pendingAgentMergeUndo'), true, 'Ordinary updates must still reach the production consumer');
    assert.equal(evaluate(p.ctx, 'viewport.scale'), 1.25);
    assert.equal(evaluate(p.ctx, 'viewport.x'), 120);
    assert.equal(evaluate(p.ctx, 'viewport.y'), -70);
});
