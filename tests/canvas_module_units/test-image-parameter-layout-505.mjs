import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasContext, evaluate} from './harness.mjs';
import {declarations} from './smart-human-handlers-harness-392.mjs';

function imageLayoutContext(values = {}) {
    const {ctx} = createCanvasContext();
    evaluate(ctx, `let imageParamResizeObserver;\n${declarations('canvas-core.js')}\n${declarations('smart-canvas.js')}`);
    Object.assign(ctx, values);
    return ctx;
}

test('line count depends on control minima and the container, not model text width', () => {
    const compact = [];
    const model = {minWidth:'44px', offsetWidth:55};
    const rows = {
        clientWidth:279,
        columnGap:'6px',
        classList:{toggle(name, force){assert.equal(name, 'image-param-two-lines'); compact.push(force);}},
        querySelectorAll(){return [model, {minWidth:'44px'}, {minWidth:'max-content', offsetWidth:52},
            {minWidth:'max-content', offsetWidth:121}];},
    };
    let onResize;
    const dynamicParams = {querySelector:() => rows};
    const ctx = imageLayoutContext({dynamicParams, composer:{classList:{contains:() => true}},
        getComputedStyle:entry => entry,
        ResizeObserver:class {constructor(callback){onResize = callback;} observe(target){assert.equal(target, dynamicParams);}},
    });
    ctx.syncImageParamRows();
    model.offsetWidth = 900;
    ctx.syncImageParamRows();
    rows.clientWidth--;
    onResize();
    model.offsetWidth = 50;
    ctx.syncImageParamRows();
    assert.deepEqual(compact, [false, false, true, true]);
});

test('pill title and keyboard-accessible name both preserve the selected label', () => {
    const label = 'Long model <"variant">';
    const ctx = imageLayoutContext({settings:{provider_id:'fixture', model:'model-id'},
        apiProviders:[{id:'fixture', model_names:{'model-id':label}}], searchableModelOptions:() => '',
        tr:value => value,
    });
    const html = ctx.renderModelControl(['model-id']);
    assert.match(html, /title="Long model &lt;&quot;variant&quot;&gt;" aria-label="Long model &lt;&quot;variant&quot;&gt;"/);
    assert.match(html, /<span class="sub">Long model &lt;&quot;variant&quot;&gt;<\/span>/);
    assert.match(html, /class="smart-popover compact-popover"/);
});

test('visible image editor fits horizontally without reanchoring video or an offscreen image', () => {
    const style = {removeProperty() {}};
    let image = true;
    const ctx = imageLayoutContext({
        composer:{style, offsetWidth:360, classList:{contains:() => image}}, composerPromptView:{mode:'manual'},
        nodeRect:node => node, shell:{getBoundingClientRect:() => ({left:20, right:620})},
        world:{offsetWidth:6000,getBoundingClientRect:() => ({left:0,width:3000})},
    });
    const node = {x:60, y:30, width:100, height:100};
    ctx.positionComposerForNode(node);
    assert.equal(style.left, '40px');
    assert.equal(style.top, '144px');
    image = false;
    ctx.positionComposerForNode(node);
    assert.equal(style.left, '-70px');
    image = true;
    ctx.positionComposerForNode({...node,x:-200});
    assert.equal(style.left, '-330px');
});
