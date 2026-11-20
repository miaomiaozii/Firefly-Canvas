import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {evaluate, fromContext, JS_DIR} from './harness.mjs';
import {editor, event, identity} from './smart-human-handlers-harness-392.mjs';

const providers = [
    {id:'soon', name:'Soon', image_models:['soon-only', 'second'], chat_models:['chat-checked'], video_models:['video-checked'],
        available_models:{image:['unchecked']}, recommended_models:{image:'unchecked', chat:'chat-unchecked'}},
    {id:'other', name:'Other & Co.', primary:true, image_models:['first', 'old']},
    {id:'third', name:'Third', image_models:['old']},
    {id:'off', name:'Disabled', enabled:false, primary:true, image_models:['old']},
];
const fixture = (saved, catalog=providers) => {
    const e = editor([{id:'image', type:'smart-image', ...identity, runSettings:{engine:'api', apiKind:'image', ...saved}}]);
    const {ctx, harness} = e;
    const source = readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
    for(const name of ['IMAGE_RATIO_PRESETS','IMAGE_RATIO_PRESET_KEYS_BY_RATIO']){
        evaluate(ctx, source.match(new RegExp(`^const ${name} = [\\s\\S]*?;`, 'm'))[0]);
    }
    const strings = {};
    ctx.StudioI18n = {register(values){Object.assign(strings, values);}, t:key => strings[key]?.zh || key};
    evaluate(ctx, readFileSync(path.join(JS_DIR, 'i18n', 'smart-canvas.js'), 'utf8'));
    Object.assign(ctx,{engineSelect:harness.makeElement(), apiKindToggle:harness.makeElement(), RECENT_SMART_SETTINGS_KEY:'recent-test'});
    evaluate(ctx, `apiProviders=${JSON.stringify(catalog)};
        modelSpecs={image_model_ids:['spec-image'],video_model_ids:['spec-video'],chat_model_ids:['spec-chat']};
        canvasDefaultSmartSettings={provider_id:'soon', ratio:'square', resolution:'1k'};
        canvasState.lastComposerNodeId='image'; canvasState.selectedId='image'; settings=smartSettingsForNode(canvasState.nodes[0]);`);
    return e;
};
const options = markup => [...markup.matchAll(/data-model-option="([^"]+)"/g)].map(([, model]) => model);

test('#465 image menu renders only checked options; stale hints list all enabled display names and do not dirty the node', () => {
    const e = fixture({provider_id:'soon', model:'old', ratio:'square', resolution:'1k'});
    const before = fromContext(e.node().runSettings);
    for(let i = 0; i < 2; i++) e.ctx.renderDynamicParams();
    const markup = e.ctx.dynamicParams.innerHTML;
    assert.deepEqual(options(markup), ['soon-only', 'second']);
    assert.match(markup, /当前模型 old[^<]+请切换服务商到 Other &amp; Co\. \/ Third 或重新选择模型/);
    assert.doesNotMatch(markup, /Disabled|<span class="sub">old<\//);
    assert.deepEqual(fromContext(e.node().runSettings), before);
    assert.equal(evaluate(e.ctx, 'settings.model'), 'old');
    evaluate(e.ctx, "apiProviders[2].enabled=false");
    e.ctx.renderDynamicParams();
    assert.match(e.ctx.dynamicParams.innerHTML, /请切换服务商到 Other &amp; Co\. 或重新选择模型/);
    evaluate(e.ctx, "apiProviders[1].image_models=['first']");
    e.ctx.renderDynamicParams();
    assert.match(e.ctx.dynamicParams.innerHTML, /当前模型 old 未在任何服务商中启用，请在全局设置里勾选/);
    assert.deepEqual(fromContext(e.node().runSettings), before);
});

test('#465 unbound image shows a provider selection prompt; no default or primary is written', () => {
    const e = fixture({model:'soon-only', routingMode:'manual'});
    const before = fromContext(e.node().runSettings);
    assert.equal(evaluate(e.ctx, 'settings.provider_id'), '');
    e.ctx.renderDynamicParams();
    assert.deepEqual(options(e.ctx.dynamicParams.innerHTML), []);
    assert.match(e.ctx.dynamicParams.innerHTML, /请先选择服务商/);
    assert.equal(e.ctx.imageGenerationSelectionError(e.ctx.smartSettingsForNode(e.node())), '请先选择服务商');
    assert.deepEqual(fromContext(e.node().runSettings), before);
});

test('#465 only a deliberate selection fixes the model; changing to a matching provider keeps the old model', () => {
    const e = fixture({provider_id:'soon', model:'old'});
    const clickOption = (param, value) => {
        const option = e.harness.makeElement();
        Object.assign(option.dataset, {smartParam:param, smartValue:value});
        e.ctx.dynamicParams = e.form({'[data-smart-param]':[option]});
        e.ctx.bindDynamicParams();
        option.onclick(event(option));
    };
    clickOption('provider_id', 'other');
    assert.equal(e.node().runSettings.provider_id, 'other');
    assert.equal(e.node().runSettings.model, 'old');
    assert.equal(e.node().userTouchedAt, 1234);
    clickOption('model', 'first');
    assert.equal(e.node().runSettings.provider_id, 'other');
    assert.equal(e.node().runSettings.model, 'first');
    assert.equal(e.node().runSettings.routingMode, 'manual');
});

test('#465 chat/video model lists do not inject spec, stale or fallback model IDs', () => {
    const e = fixture({provider_id:'soon'});
    assert.deepEqual(fromContext(e.ctx.providerChatModels('soon')), ['chat-checked']);
    assert.equal(e.ctx.chatModelOptions('stale-chat','soon'), '<option value="chat-checked" selected>chat-checked</option>');
    assert.equal(e.ctx.chatModelOptions('stale-chat','missing'), '');
    assert.deepEqual(fromContext(e.ctx.providerVideoModels('soon')), ['video-checked']);
    assert.deepEqual(fromContext(e.ctx.providerVideoModels('volcengine')), []);
    evaluate(e.ctx, 'apiProviders[0].enabled=false');
    assert.deepEqual(fromContext(e.ctx.videoApiProviders()), []);
    assert.deepEqual(fromContext(e.ctx.providerVideoModels('soon')), []);
});
