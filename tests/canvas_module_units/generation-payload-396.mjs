import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const canvas = {...input.canvas, format:'firefly-canvas-v1', project_id:input.project};
const target = canvas.nodes.find(n => n.id === 'source');
const ctx = vm.createContext({
    canvas, canvasUsesConnections:Object.hasOwn(canvas, 'connections'), canvasState:{nodes:canvas.nodes, projectMediaLibrary:{items:[]}},
    URL, window:{location:{origin:'http://localhost'}}, assetLibraryOpen:false, projectMediaImportCache:new Map(),
    projectMediaUrl:id=>`/api/project-library/${input.project}/media/${id}`,
    projectMediaPreviewUrl:id=>`/api/project-library/${input.project}/media/${id}/preview`,
    withProjectMediaImportSlot:operation=>operation(),
    promptInput:{innerHTML:input.promptHtml ?? target.promptDraftHtml ?? target.prompt}, settings:target.runSettings,
    console:{info(){}}, tr:key => key, nowMs:Date.now, uid:() => '396',
    apiProviders:input.providers, modelSpecs:{},
    validOutpaintSize:n => n?.outpaintSize || null,
    mediaKindForItem:r => r.kind || 'image', imageRefsOnly:refs => refs.filter(r => (r.kind || 'image') === 'image'),
    beginSmartPendingHistoryLog:() => '396', smartGenerationSubmissions:() => [],
    flushProjectCanvasBeforeGeneration:async () => true,
    fetch:async (url, options) => {
        if (url.endsWith('/image-reference-resolution')) return {ok:true, json:async () => input.resolution};
        if (url.endsWith('/media/import') && input.legacyImportId) {
            if (JSON.parse(options.body).source_url !== input.legacySource) throw Error('Legacy URL was changed');
            return {ok:true, json:async () => ({media:{asset_id:input.legacyImportId, kind:'image'}})};
        }
        if (!url.endsWith('/image-generation-tasks')) throw Error(url);
        process.stdout.write(options.body);
        return {ok:true, json:async () => ({task_id:'test'})};
    },
});
const smart = fs.readFileSync('static/js/smart-canvas.js', 'utf8');
function body(source, name, kind='function') {
    const start = source.indexOf(`${kind} ${name}`);
    if (start < 0) throw Error(name);
    return source.slice(start, source.indexOf('\n}', start) + 2);
}
for (const name of ['SIZE_MAP', 'IMAGE_RATIO_PRESETS', 'RES_LONG_SIDE', 'RES_PIXEL_LIMIT']) {
    vm.runInContext(smart.match(new RegExp(`const ${name} = [\\s\\S]*?;`))[0], ctx);
}
for (const name of ['parseSizeValue', 'parseRatioValue', 'apiImageSize', 'apiImageAllowedAspectRatios', 'ratioLog',
    'alignRatioToModel', 'apiImageUsesExplicitResolution', 'sizeForRun', 'isGptImageAutoSizeModel',
    'imageReferenceInput', 'imageProviders', 'primaryProviderId', 'volcengineProvider', 'orderedProviderModels',
    'apiProviderById', 'imageProviderSelection']) {
    vm.runInContext(body(smart, name + '('), ctx);
}
for (const name of ['fetchImageReferenceResolution', 'importProjectMediaReference']) {
    vm.runInContext(body(smart, name + '(', 'async function'), ctx);
}
vm.runInContext(fs.readFileSync('static/js/canvas-generation-tasks.js', 'utf8'), ctx);
ctx.canvasCore = {alignRatioToModel:ctx.alignRatioToModel, parseSizeValue:ctx.parseSizeValue,
    imageProviderSelection:ctx.imageProviderSelection,
    canvasId:input.project, importProjectMediaReference:ctx.importProjectMediaReference,
    scheduleSave(){}, async acceptProjectGenerationRegistration(){}};
ctx.target = target;
if (process.argv.includes('--resolution-input')) {
    process.stdout.write(ctx.imageReferenceInput(target));
} else {
    await vm.runInContext(`(async () => {
        const request = await fetchImageReferenceResolution(canvas.project_id, imageReferenceInput(target));
        const result = canvas.nodes.find(n => n.id === 'browser-result');
        result.outpaintSize = target.outpaintSize;
        const settings = {...target.runSettings};
        if (target.outpaintSize) Object.assign(settings, {ratio:'custom', customRatio:target.outpaintSize.aspectRatio});
        await runApiGeneration(request.prompt, request.refs, settings, result, {registrationQueued:true});
    })()`, ctx);
}
