import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {evaluate, fromContext, JS_DIR, loadModules} from './harness.mjs';
import {editor, event} from './smart-human-handlers-harness-392.mjs';

const catalog494 = [
    {id:'other', name:'Other', primary:true, image_models:['first', 'second']},
    {id:'soon', name:'Soon', image_models:['soon-only', 'second'], chat_models:['chat-only'], video_models:['video-only'],
        available_models:{image:['unchecked']}, recommended_models:{image:'unchecked'}},
];
const used494 = {engine:'api', apiKind:'image', provider_id:'soon', model:'second', routingMode:'auto', ratio:'square', resolution:'1k'};
const storageKey494 = 'smart_canvas_recent_run_settings_v1';

function recentImageEditor494({catalog=catalog494, store}={}){
    const e = editor([{id:'existing-blank', type:'smart-image', images:[]}]);
    const {ctx, harness} = e;
    loadModules(ctx, ['canvas-node-ops.js', 'canvas-placement.js', 'canvas-agent-tui.js']);
    const source = readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8');
    for(const name of ['IMAGE_RATIO_PRESETS', 'IMAGE_RATIO_PRESET_KEYS_BY_RATIO', 'SIZE_MAP']){
        evaluate(ctx, source.match(new RegExp(`^const ${name} = [\\s\\S]*?;`, 'm'))[0]);
    }
    const strings = {};
    ctx.StudioI18n = {register(values){Object.assign(strings, values);}, t:key => strings[key]?.zh || key};
    evaluate(ctx, readFileSync(path.join(JS_DIR, 'i18n', 'smart-canvas.js'), 'utf8'));
    Object.assign(ctx, {
        RECENT_SMART_SETTINGS_KEY:storageKey494, MEDIA_NODE_DEFAULT_SCALE:2,
        engineSelect:harness.makeElement(), apiKindToggle:harness.makeElement(), createMenu:harness.makeElement(),
        shell:{clientWidth:1200, clientHeight:800},
    });
    evaluate(ctx, `apiProviders=${JSON.stringify(catalog)};
        settings={engine:'api', apiKind:'image', provider_id:'other', model:'first', routingMode:'auto', ratio:'square', resolution:'1k'};
        canvasDefaultSmartSettings=cloneSmartSettings(settings);
        canvasState.lastComposerNodeId='existing-blank'; canvasState.selectedId='existing-blank';`);
    if(store){
        for(const [key, value] of store) harness.store.set(key, value);
        evaluate(ctx, 'loadRecentSmartSettings(); applyRecentSmartSettingsForCurrentMode(); canvasDefaultSmartSettings=cloneSmartSettings(settings);');
    }
    return e;
}
function displaySettings494(e, node){
    e.ctx.canvasState.selectedId = node.id;
    e.ctx.canvasState.lastComposerNodeId = node.id;
    e.ctx.subject494 = node;
    evaluate(e.ctx, 'settings=smartSettingsForNode(subject494)');
    e.ctx.renderDynamicParams();
    return e.ctx.dynamicParams.innerHTML;
}
async function assertUnselected494(e, node){
    const before = fromContext(node);
    const resolved = e.ctx.smartSettingsForNode(node);
    assert.equal(resolved.provider_id, '');
    assert.equal(resolved.model || '', '');
    assert.match(displaySettings494(e, node), /请先选择服务商/);
    assert.match(e.ctx.imageGenerationSelectionError(resolved), /请先选择服务商/);
    await e.ctx.runGeneration();
    assert.match(e.harness.toasts.at(-1), /请先选择服务商/);
    assert.deepEqual(fromContext(node), before, 'render and blocked Generate must not write defaults');
}

test('#494 manual image factories store the last used checked provider/model before first render/save', () => {
    for(const factory of [
        ctx => ctx.createNode(10, 20),
        ctx => ctx.createImageNodeAt({x:100, y:200}),
        ctx => ctx.createNodeFromMenu('image'),
        ctx => ctx.createCanvasStarterImage(false),
    ]){
        const e = recentImageEditor494();
        e.ctx.rememberRecentSmartSettings(used494);
        const node = factory(e.ctx);
        assert.equal(node.runSettings?.provider_id, 'soon');
        assert.equal(node.runSettings?.model, 'second');
        assert.equal(node.runSettings?.routingMode, 'auto');
        assert.equal(e.ctx.smartSettingsForNode(node).provider_id, 'soon');
        const stored = e.renders.at(-1).nodes.find(item => item.id === node.id);
        assert.equal(stored.runSettings?.provider_id, 'soon');
        assert.equal(stored.runSettings?.model, 'second');
        const markup = displaySettings494(e, node);
        assert.doesNotMatch(markup, /请先选择服务商/);
        assert.match(markup, /data-model-option="second"/);
        assert.doesNotMatch(markup, /data-model-option="unchecked"/);
        assert.equal(e.ctx.imageGenerationSelectionError(e.ctx.smartSettingsForNode(node)), '');
        assert.equal(node.userTouchedAt, null, 'creation defaults are not a human edit of an existing node');
    }
});

test('#494 deliberate provider/model selector edits feed the real recent-settings memory', () => {
    const e = recentImageEditor494();
    displaySettings494(e, e.node('existing-blank'));
    for(const [param, value] of [['provider_id', 'soon'], ['model', 'second']]){
        const option = e.harness.makeElement();
        Object.assign(option.dataset, {smartParam:param, smartValue:value});
        e.ctx.dynamicParams = e.form({'[data-smart-param]':[option]});
        e.ctx.bindDynamicParams();
        option.onclick(event(option));
    }
    assert.equal(e.node('existing-blank').runSettings.provider_id, 'soon');
    const next = e.ctx.createNode(10, 20);
    assert.equal(next.runSettings?.provider_id, 'soon');
    assert.equal(next.runSettings?.model, 'second');
    assert.equal(next.runSettings?.routingMode, 'manual');
});

for(const routingMode of ['auto', 'manual']){
    test(`#494 real ${routingMode} image submission preserves the explicit pair for later remembered/reloaded creation (offline wire)`, async () => {
        const e = recentImageEditor494();
        evaluate(e.ctx, "canvas.format='classic'");
        const submitted = {...used494, routingMode};
        const wires = [];
        e.ctx.fetch = async (url, options) => {
            wires.push({url, body:JSON.parse(options.body)});
            return {ok:true, json:async () => ({task_id:'offline-494'})};
        };
        e.ctx.rememberRecentSmartSettings(submitted);
        await e.ctx.runApiGeneration('offline prompt', [], submitted, null, {pendingHistoryId:'offline-pending-494'});
        assert.deepEqual(submitted, {...used494, routingMode}, 'the send path must not clear provider_id in automatic mode');
        assert.equal(wires[0].body.provider_id, 'soon');
        assert.equal(wires[0].body.model, 'second');
        e.ctx.rememberRecentSmartSettings(submitted);
        const reloaded = recentImageEditor494({store:e.harness.store});
        const next = reloaded.ctx.createNode(10, 20);
        assert.equal(next.runSettings?.provider_id, 'soon');
        assert.equal(next.runSettings?.model, 'second');
    });
}

test('#494 reload uses the persisted recent pair, not canvas defaults or a later unrelated composer state', () => {
    const before = recentImageEditor494();
    before.ctx.rememberRecentSmartSettings(used494);
    const reloaded = recentImageEditor494({store:before.harness.store});
    evaluate(reloaded.ctx, "settings={...settings,provider_id:'other',model:'first'}");
    const node = reloaded.ctx.createImageNodeAt({x:300, y:200});
    assert.equal(node.runSettings?.provider_id, 'soon');
    assert.equal(node.runSettings?.model, 'second');
    assert.equal(reloaded.ctx.smartSettingsForNode(node).provider_id, 'soon');
    assert.equal(JSON.parse(reloaded.harness.store.get(storageKey494))['api:image'].provider_id, 'soon');
});

for(const [reason, mutate, saved] of [
    ['disabled provider', providers => {providers[1].enabled=false;}],
    ['removed provider', providers => {providers.splice(1, 1);}],
    ['unchecked model (even when another provider allows it)', providers => {providers[1].image_models=['soon-only'];}],
    ['model allowed only for chat', providers => {providers[1].image_models=['soon-only'];}, {...used494, model:'chat-only'}],
    ['model allowed only for video', providers => {providers[1].image_models=['soon-only'];}, {...used494, model:'video-only'}],
    ['legacy auto memory without a provider', () => {}, {...used494, provider_id:''}],
    ['provider-only memory without a selected model', () => {}, {...used494, model:''}],
]){
    test(`#494 ${reason}: new image stays unselected and Generate uses the existing provider prompt`, async () => {
        const seed = recentImageEditor494();
        seed.ctx.rememberRecentSmartSettings(saved || used494);
        const providers = structuredClone(catalog494);
        mutate(providers);
        const e = recentImageEditor494({catalog:providers, store:seed.harness.store});
        const node = e.ctx.createImageNodeAt({x:100, y:200});
        await assertUnselected494(e, node);
        assert.equal(JSON.parse(e.harness.store.get(storageKey494))['api:image'].provider_id, (saved || used494).provider_id,
            'invalid memory is not replaced with a different provider');
    });
}

test('#494 no recent choice never seeds a new image from the default or primary provider', async () => {
    const e = recentImageEditor494();
    await assertUnselected494(e, e.ctx.createNode(10, 20));
});

test('#494 existing blank image remains blocked despite valid browser recent settings', async () => {
    const e = recentImageEditor494();
    e.ctx.rememberRecentSmartSettings(used494);
    const existing = e.node('existing-blank');
    assert.equal(e.ctx.smartSettingsForNode(existing).provider_id, '');
    assert.match(displaySettings494(e, existing), /请先选择服务商/);
    await e.ctx.runGeneration();
    assert.match(e.harness.toasts.at(-1), /请先选择服务商/);
    assert.equal(Object.hasOwn(existing, 'runSettings'), false);
    e.ctx.inheritNodeMetaFromImage(existing);
    assert.equal(Object.hasOwn(existing, 'runSettings'), false, 'media normalization is not new-node initialization');
});

test('#494 duplicate copies explicit node settings, including blank, instead of browser recent', () => {
    for(const saved of [{...used494, provider_id:'other', model:'first'}, {engine:'api', apiKind:'image', provider_id:'', model:''}, undefined]){
        const e = recentImageEditor494();
        e.ctx.rememberRecentSmartSettings(used494);
        if(saved) e.node('existing-blank').runSettings = saved;
        e.ctx.duplicateSelectedNodes();
        const copy = e.ctx.canvasState.nodes.at(-1);
        assert.notEqual(copy.id, 'existing-blank');
        assert.deepEqual(copy.runSettings && fromContext(copy.runSettings), saved);
        assert.equal(e.ctx.smartSettingsForNode(copy).provider_id, saved?.provider_id || '');
    }
});

test('#494 existing settings and media-derived copies never acquire creation defaults during normalization', () => {
    const e = recentImageEditor494();
    e.ctx.rememberRecentSmartSettings(used494);
    const withSettings = {id:'explicit-new', type:'smart-image', createdBy:'user', images:[], runSettings:{provider_id:'other', model:'first'}};
    e.ctx.inheritNodeMetaFromImage(withSettings);
    assert.deepEqual(withSettings.runSettings, {provider_id:'other', model:'first'});
    const mediaCopy = e.ctx.createImageNodeAt({x:10, y:20}, [{url:'/offline-copy.png'}]);
    assert.equal(Object.hasOwn(mediaCopy, 'runSettings'), false, 'copying media is not creating a blank generation slot');
});

test('#494 Agent-created images never inherit browser recent; explicit backend settings survive', () => {
    const e = recentImageEditor494();
    e.ctx.rememberRecentSmartSettings(used494);
    const node = e.ctx.createNode(10, 20, [], {creation:{createdBy:'agent', agentNodeId:'agent-494'}});
    assert.equal(Object.hasOwn(node, 'runSettings'), false);
    assert.equal(e.ctx.smartSettingsForNode(node).provider_id, '');
    node.runSettings = {...used494, provider_id:'other', model:'first'};
    e.ctx.inheritNodeMetaFromImage(node);
    assert.equal(e.ctx.smartSettingsForNode(node).provider_id, 'other');
    assert.equal(e.ctx.smartSettingsForNode(node).model, 'first');
});
