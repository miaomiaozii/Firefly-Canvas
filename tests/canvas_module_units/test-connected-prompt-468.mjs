import assert from 'node:assert/strict';
import test from 'node:test';
import {evaluate, fromContext, loadModules} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

function setup({kind='flow', connected=true, text='Connected instruction', own=''}={}) {
    const runSettings = {engine:'api', apiKind:'image', provider_id:'mock', model:'picked'};
    const e = editor([
        {id:'target', type:'smart-image', promptDraftText:own, runSettings},
        {id:'prompt', type:'smart-prompt', text},
        {id:'image', type:'smart-image', images:[{url:'/test/ref.png', name:'Reference'}]},
    ]);
    const {ctx, harness} = e;
    loadModules(ctx, ['canvas-agent-tui.js']);
    Object.assign(ctx, {
        Node:{TEXT_NODE:3, ELEMENT_NODE:1}, SMART_REFERENCE_IMAGE_MAX:20,
        validOutpaintSize:() => null, runBtn:harness.makeElement('button'),
        smartCascadeRuns:new Map(), smartNodeRunTokens:new Map(),
    });
    // Detached drafts here are plain text; rich-token composition is exercised below
    // through the real collectPromptParts path rather than an HTML-parser imitation.
    ctx.document.createElement = tag => {
        const root = harness.makeElement(tag);
        Object.defineProperty(root, 'innerHTML', {
            set(text) {
                assert.doesNotMatch(text, /[<>]/);
                this.childNodes = [{nodeType:3, textContent:text}];
            },
        });
        return root;
    };
    evaluate(ctx, `settings=${JSON.stringify(runSettings)};
        apiProviders=[{id:'mock', enabled:true, image_models:['picked']}]; canvasState.selectedId='target';
        canvas.connections=${JSON.stringify(connected ? [{from:'prompt', to:'target', kind}] : [])};`);
    e.target = () => e.node('target');
    e.live = (...parts) => {
        ctx.promptInput.childNodes = parts.map(part => {
            if(typeof part === 'string') return {nodeType:3, textContent:part};
            const token = harness.makeElement('span');
            token.nodeType = 1;
            token.classList.add('mention-image-token');
            token.dataset = part;
            return token;
        });
    };
    e.live(own);
    e.request = () => fromContext(ctx.buildPromptRequest(e.target()));
    return e;
}

for(const kind of ['input', 'flow', undefined]) {
    test(`${kind ?? 'default flow'} connected prompt enables the real generate button with an empty own draft`, () => {
        const e = setup({kind});
        if(kind === undefined) evaluate(e.ctx, 'delete canvas.connections[0].kind');
        e.ctx.syncRunButtonState(e.target());
        assert.equal(e.ctx.runBtn.disabled, false, 'nonempty connected prompt must enable generation');
        assert.equal(e.ctx.buildPromptRequestForNode(e.target(), null).prompt, 'Connected instruction');
    });
    test(`${kind ?? 'default flow'} connected prompt replaces the live and saved own instruction`, () => {
        const e = setup({kind, own:'Discard own instruction'});
        if(kind === undefined) evaluate(e.ctx, 'delete canvas.connections[0].kind');
        assert.equal(e.request().prompt, 'Connected instruction');
        assert.equal(e.request().displayPrompt, 'Connected instruction');
        assert.equal(e.ctx.buildPromptRequestForNode(e.target(), null).prompt, 'Connected instruction');
    });
}

for(const connected of [false, true]) {
    test(`${connected ? 'empty connected' : 'no connected'} prompt retains nonempty own fallback, otherwise stays disabled`, () => {
        const e = setup({connected, text:' \n ', own:'Own fallback'});
        assert.equal(e.request().prompt, 'Own fallback');
        e.ctx.syncRunButtonState(e.target());
        assert.equal(e.ctx.runBtn.disabled, false);
        e.target().promptDraftText = '';
        e.live('');
        assert.equal(e.request().prompt, '');
        e.ctx.syncRunButtonState(e.target());
        assert.equal(e.ctx.runBtn.disabled, true);
    });
}

test('replacement preserves reference numbering/map but not the superseded own instruction', () => {
    const e = setup();
    evaluate(e.ctx, `canvas.connections.push({from:'image', to:'target', kind:'input'})`);
    e.live('Discard own ', {url:'/test/ref.png', name:'Reference', nodeId:'image', imageIndex:'0'});
    const request = e.request();
    assert.equal(request.prompt, 'smart.refMapHeader\n图1：Reference\n\nsmart.refUserNeed\nConnected instruction');
    assert.equal(request.displayPrompt, 'Connected instruction');
    assert.equal(request.refs[0].url, '/test/ref.png');
});

test('ordinary image flow is not promoted to a default reference by prompt-flow support', () => {
    const e = setup();
    evaluate(e.ctx, `canvas.connections.push({from:'image', to:'target', kind:'flow'})`);
    assert.deepEqual(e.request().refs, []);
});
