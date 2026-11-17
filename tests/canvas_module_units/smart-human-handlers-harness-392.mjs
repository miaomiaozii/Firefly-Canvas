// Real handler/storage/journal/undo harness shared by #392 behavior suites.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';

export function declarations(file){
    const lines=readFileSync(path.join(JS_DIR,file),'utf8').split(/\r?\n/), result=[];
    for(let i=0;i<lines.length;i++){
        if(!/^(?:async )?function \w+\(/.test(lines[i])) continue;
        const start=i;
        if(!/}\s*$/.test(lines[i])) while(++i<lines.length && !/^}/.test(lines[i])){}
        if(i===lines.length) throw Error(lines[start]);
        result.push(lines.slice(start,i+1).join('\n'));
    }
    return result.join('\n');
}
export const identity={createdBy:'agent',agentNodeId:'original-agent',userTouchedAt:null,promptEditedByUser:false};
export const event=(target,extra={})=>({target,preventDefault(){},stopPropagation(){},...extra});
export function editor(nodes){
    const {ctx,harness}=createCanvasContext();
    loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','project-canvas-mutations.js','canvas-layout-utils.js','canvas-connections.js','canvas-media-history.js','canvas-undo.js','canvas-geometry.js','canvas-image-editor.js','canvas-generation-tasks.js']);
    const constants=readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8').split(/\r?\n/).filter(line=>/^const PROMPT_\w+ = (?:[\d]|')/.test(line)).join('\n');
    vm.runInContext(constants+'\n'+declarations('canvas-generation-cascade.js')+'\n'+declarations('smart-canvas.js'),ctx);
    const renders=[];
    Object.assign(ctx,{
        canvasId:'unit-392',sourceProjectId:'unit-392',SMART_LOG_PREVIEW_NODE_ID:'__preview__',
        SMART_ONLINE_ENGINES:new Set(['api','volcengine']),PROMPT_FIELD_SIZING_OK:true,PROMPT_SPLIT_RESIZE_BAR_H:8,
        viewport:{x:0,y:0,scale:1},canvasDefaultSmartSettings:{},initialSmartSettings:{},recentSmartSettingsByMode:{},
        canvasUsesConnections:true,canvasLoadReady:true,canvasProjectGone:false,canvasSyncInFlight:false,contentDirty:false,debouncedSaveTimer:null,DEBOUNCED_SAVE_MS:3000,
        persistedCoreSnapshot:null,persistedDurableSnapshot:null,smartCanvasServerShadow:null,smartSaveButton:null,
        pendingRemoteUpdatedAt:0,pendingAgentMergeFingerprint:'',pendingAgentMergeUndo:false,smartClientId:'unit-392',projectMediaImportCache:new Map(),activeProjectMediaImports:0,projectMediaImportWaiters:[],assetLibraryOpen:false,
        promptNodeFlowView:{mode:'idle'},composerPromptView:{mode:'manual'},longContentSession:{mode:'idle'},smartLoopPromptVisiting:new Set(),smartLoopContext:null,
        promptInput:harness.makeElement(),composer:harness.makeElement(),world:harness.makeElement(),dynamicParams:harness.makeElement(),
        recentSmartSettingsByMode:{},transientSmartCloudLinks:[],rhConfigs:[],modelSpecs:{},smartRhRandomValues:{},
        render(){renders.push(fromContext(ctx.canvasForStorage()));},refreshIcons(){},updateNodeElementDuringResize(){},
        toast(message){harness.toasts.push(String(message));},scheduleConnectionLayerRefresh(){},
    });
    ctx.composer.classList.add('open');
    evaluate(ctx,`Date.now=()=>1234; canvas={id:'unit-392',project_id:'unit-392',format:'firefly-canvas-v1',nodes:${JSON.stringify(nodes)},connections:[],history:[]};canvasState.nodes=canvas.nodes;`);
    ctx.initialSnapshot=ctx.FireflyProjectCanvasMutations.snapshotFromCanvas(evaluate(ctx,'canvas'));
    evaluate(ctx,'persistedCoreSnapshot=initialSnapshot');
    ctx.smartCanvasPendingState();
    const form=selectors=>{const el=harness.makeElement();el.querySelector=s=>(selectors[s]||[])[0]||null;el.querySelectorAll=s=>selectors[s]||[];el.contains=()=>true;return el;};
    return {ctx,harness,renders,form,node:(id=nodes[0].id)=>evaluate(ctx,`canvasState.nodes.find(n=>n.id===${JSON.stringify(id)})`),stored:()=>fromContext(ctx.canvasForStorage()).nodes};
}
