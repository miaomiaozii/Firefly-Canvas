import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const canvas = {...input.canvas, format:'firefly-canvas-v1', project_id:input.project};
const target = canvas.nodes.find(n => n.id === 'source');
const mediaUrl = id => `/api/project-library/${input.project}/media/${id}`;
const ctx = vm.createContext({
    canvas, canvasState:{nodes:canvas.nodes}, smartLoopContext:{}, promptInput:{}, settings:target.runSettings,
    console:{info(){}}, tr:key => key, nowMs:Date.now, uid:() => '396',
    apiProviderById:id => input.providers.find(p => p.id === id), modelSpecs:{},
    isProjectCanvasData:() => true, projectMediaUrl:mediaUrl, imagesForNode:n => n.images || [],
    inputNodesFor:() => [], smartImageUsesWorkflowInput:() => false,
    collectPromptParts:() => [{type:'text', text:target.prompt}],
    inputPromptTextFor:() => '', isSmartGroupNode:() => false, validOutpaintSize:() => null,
    mediaKindForItem:r => r.kind || 'image', imageRefsOnly:refs => refs.filter(r => (r.kind || 'image') === 'image'),
    beginSmartPendingHistoryLog:() => '396', smartGenerationSubmissions:() => [],
    flushProjectCanvasBeforeGeneration:async () => true,
    fetch:async (url, options) => {
        if (!url.endsWith('/image-generation-tasks')) throw Error(url);
        process.stdout.write(options.body);
        return {ok:true, json:async () => ({task_id:'test'})};
    },
});
const smart = fs.readFileSync('static/js/smart-canvas.js', 'utf8');
const connections = fs.readFileSync('static/js/canvas-connections.js', 'utf8');
function body(source, name, kind='function') {
    const start = source.indexOf(`${kind} ${name}`);
    if (start < 0) throw Error(name);
    return source.slice(start, source.indexOf('\n}', start) + 2);
}
for (const name of ['SIZE_MAP', 'IMAGE_RATIO_PRESETS', 'RES_LONG_SIDE', 'RES_PIXEL_LIMIT']) {
    vm.runInContext(smart.match(new RegExp(`const ${name} = [\\s\\S]*?;`))[0], ctx);
}
for (const name of ['isManagedAgentReference(', 'managedAgentReferenceOrder(']) vm.runInContext(body(connections, name), ctx);
for (const name of ['parseSizeValue', 'parseRatioValue', 'apiImageSize', 'apiImageAllowedAspectRatios', 'ratioLog',
    'alignRatioToModel', 'apiImageUsesExplicitResolution', 'sizeForRun', 'isGptImageAutoSizeModel',
    'managedAgentReferenceEdgesFor', 'managedAgentReferenceImagesFor', 'inputImagesFor', 'inputRefKey',
    'blockedInputRefKeys', 'manualReferenceImagesFor', 'referenceCandidatesFor', 'defaultReferenceImagesFor',
    'referenceCandidateFilter', 'uniqueReferenceImages', 'originalPromptTextFromParts', 'buildPromptRequest']) {
    vm.runInContext(body(smart, name + '('), ctx);
}
vm.runInContext(fs.readFileSync('static/js/canvas-generation-tasks.js', 'utf8'), ctx);
ctx.canvasCore = {alignRatioToModel:ctx.alignRatioToModel, parseSizeValue:ctx.parseSizeValue,
    canvasId:input.project, importProjectMediaReference:async ref => ref,
    scheduleSave(){}, async acceptProjectGenerationRegistration(){}};
ctx.target = target;
await vm.runInContext(`(async () => {
    const request = buildPromptRequest(target);
    await runApiGeneration(request.prompt, request.refs, {...target.runSettings},
        canvas.nodes.find(n => n.id === 'browser-result'), {registrationQueued:true});
})()`, ctx);
