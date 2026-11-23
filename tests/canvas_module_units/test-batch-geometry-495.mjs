import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {evaluate, fromContext, JS_DIR, loadModules} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';

const source = readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8');
const image = i => ({url:`/offline/${i}.png`, width:1024, height:768});
function fixture(nodes = []) {
    const e = editor(nodes);
    loadModules(e.ctx, ['canvas-node-ops.js','canvas-agent-tui.js']);
    evaluate(e.ctx, source.split(/\r?\n/).filter(line => /^const MEDIA_(NODE_DEFAULT_SCALE|GROUP_THUMB_BASE|GROUP_MAX_VISIBLE_ROWS) =/.test(line)).join('\n'));
    return e;
}
const dimensions = [[2,2,1,436],[4,2,2,436],[6,3,2,640],[8,4,2,844]];
function oldApi() {
    const root = process.env.FIREFLY_495_EVIDENCE;
    const ctx = vm.createContext({});
    const before = root ? path.join(root, 'before/canvas-layout-utils.js') : new URL('../fixtures/canvas-layout-495-before.js', import.meta.url);
    vm.runInContext(readFileSync(before, 'utf8'), ctx);
    return code => fromContext(vm.runInContext(code, ctx));
}

test('2/4/6/8 exact old/new bounds and unchanged columns, summary and status bar', () => {
    const e = fixture(), old = oldApi();
    for(const [count,cols,rows,width] of dimensions) {
        const previous = old(`getBatchThumbGridSize(${count})`);
        assert.deepEqual([previous.cols,previous.rows,previous.boxW,previous.boxH], [cols,rows,cols*136+(cols-1)*9+32,rows*102+(rows-1)*9+54]);
        const grid = fromContext(e.ctx.getBatchThumbGridSize(count));
        assert.deepEqual([grid.cols,grid.rows,grid.boxW,grid.boxH], [cols,rows,width,rows*144+(rows-1)*12+62]);
        const node = {id:'batch',type:'smart-image',_batchTotal:count,pending:count,images:[]};
        const pending = e.ctx.imageLayout([],1,node);
        const box = e.ctx.pendingBoxSize(count,{node});
        assert.deepEqual([pending.width,pending.height,box.w,box.h], [width,grid.gridH+128,width,grid.gridH+128]);
        const complete = e.ctx.imageLayout(Array.from({length:count},(_,i)=>image(i)),1,{...node,pending:0});
        assert.deepEqual([complete.width,complete.height,complete.thumb], [width,grid.gridH+80,192]);
    }
    assert.deepEqual(fromContext(evaluate(e.ctx,'[SMART_GROUP_LAYOUT.SUMMARY_H,SMART_GROUP_LAYOUT.SUMMARY_GAP,BATCH_STATUSBAR_H]')), [14,8,48]);
});

test('saved batch rows use their 12px gap while ordinary grids retain 8px', () => {
    const e = fixture();
    for(const [count,cols,rows] of dimensions.filter(([count]) => count > 2)) {
        const images = Array.from({length:count}, (_, i) => image(i));
        assert.equal(e.ctx.mediaGroupGridHeight(images,cols,rows,192,12),300);
        assert.equal(e.ctx.mediaGroupGridHeight(images,cols,rows,192),296);
    }
    assert.match(source,/mediaGroupGridHeight\(refs\.map\(ref => ref\.item\), grid\.cols, visibleRows, BATCH_THUMB\.WIDTH, BATCH_THUMB\.GAP\)/);
});

test('upgrade only both exact old dimensions for same count; retain every coordinate and neighbor', () => {
    const old = oldApi();
    for(const [count] of dimensions) for(const pending of [0,count]) {
        const grid = old(`getBatchThumbGridSize(${count})`);
        const w = grid.boxW, h = grid.gridH+64+(pending?48:0);
        const base = {type:'smart-image',x:123,y:-456,_batchTotal:count,pending,images:pending?[]:Array.from({length:count},(_,i)=>image(i))};
        const e = fixture([{...base,id:'auto',w,h},{...base,id:'manual-w',w:w+1,h},{...base,id:'manual-h',w,h:h+1}, {id:'neighbor',type:'smart-prompt',x:444,y:22,w:316,h:240}]);
        const before = e.stored();
        assert.equal(e.ctx.migrateSmartBatchSizes(),true);
        assert.equal(e.ctx.migrateSmartBatchSizes(),false);
        const upgraded = e.node('auto');
        assert.equal(upgraded.w,e.ctx.getBatchThumbGridSize(count).boxW);
        assert.equal(upgraded.h,e.ctx.getBatchThumbGridSize(count).gridH+80+(pending?48:0));
        assert.deepEqual(e.stored().map(n=>[n.id,n.x,n.y]),before.map(n=>[n.id,n.x,n.y]));
        assert.deepEqual(e.stored().slice(1),before.slice(1));
        for(const id of ['manual-w','manual-h']) {
            const n = e.node(id), layout=e.ctx.imageLayout(n.images,1,n);
            assert.deepEqual([layout.width,layout.height],[n.w,n.h]);
        }
    }
});

test('single and ungrouped geometry/caps match the actual pre-change implementation', () => {
    const e = fixture(),old=oldApi();
    for(const aspect of [{w:4,h:3},{w:1,h:10},{w:16,h:9},{w:3,h:4}]) {
        assert.deepEqual(fromContext(e.ctx.getSingleImageLayoutSize(aspect)),old(`getSingleImageLayoutSize(${JSON.stringify(aspect)})`));
        assert.deepEqual(fromContext(e.ctx.getUngroupedImageSize(aspect)),old(`getUngroupedImageSize(${JSON.stringify(aspect)})`));
        assert.deepEqual(fromContext(e.ctx.getSingleImageLayoutSize(aspect,640)),old(`getSingleImageLayoutSize(${JSON.stringify(aspect)},640)`));
    }
});

test('placeholder, filled and missing result slots use identical 192x144 cells and resize gap', () => {
    const e=fixture();
    for(const [count] of dimensions) {
        const n={id:'batch',type:'smart-image',_batchTotal:count,pending:count,images:[image(0),image(1)]};
        const layout=e.ctx.imageLayout(n.images,1,n);
        const pending=e.ctx.generationPlaceholderHtml(layout,count,false,{},n.images,[],null,n.id);
        assert.match(pending,/repeat\(\d, 192px\)/);
        assert.match(pending,/repeat\(\d, 144px\)/);
        assert.match(pending,/gap:12px/);
        assert.match(pending,/generation-filled-cell/);
        const partial={...n,pending:0,images:[image(0)]};
        const partialLayout=e.ctx.imageLayout(partial.images,1,partial);
        assert.equal(partialLayout.width,e.ctx.getBatchThumbGridSize(count).boxW);
        const partialHtml=e.ctx.nodeBodyHtml(partial,partialLayout);
        assert.equal((partialHtml.match(/thumb-empty-slot/g)||[]).length,count-1);
        assert.match(partialHtml,/--thumb-w:192px;--thumb-h:144px/);
        if(count>2) {
            const done=e.ctx.nodeBodyHtml({...n,pending:0},layout);
            assert.equal((done.match(/thumb-empty-slot/g)||[]).length,count-2);
            assert.match(done,/--thumb-w:192px;--thumb-h:144px/);
        }
    }
    const text=readFileSync(path.join(JS_DIR,'canvas-node-ops.js'),'utf8');
    assert.match(text,/loadingGrid.style.gap = `\$\{layout.batchGap \?\? BATCH_THUMB.GAP\}px`/);
});

test('new batch output placement uses enlarged existing boxes without moving any node', () => {
    const e=fixture([{id:'source',type:'smart-prompt',x:0,y:0,w:316,h:240},
        {id:'previous',type:'smart-image',x:396,y:0,pending:4,_batchTotal:4,images:[]},
        {id:'neighbor',type:'smart-prompt',x:700,y:900,w:316,h:240}]);
    evaluate(e.ctx,'canvas.connections=[{from:"source",to:"previous",kind:"input"}]');
    const before=e.stored();
    const box=e.ctx.pendingBoxSize(8,{node:e.node('source')});
    const pos=fromContext(e.ctx.nextOutputPositionForSource(e.node('source'),box));
    assert.deepEqual([box.w,box.h],[844,428]);
    assert.deepEqual(pos,{x:396,y:456});
    assert.deepEqual(e.stored(),before);
});

test('single imageLayout geometry remains independent of batch padding', () => {
    const e=fixture();
    const node={id:'single',type:'smart-image',_batchTotal:1,_generationAspect:{w:4,h:3},images:[]};
    const pending=e.ctx.imageLayout([],1,node);
    assert.deepEqual([pending.width,pending.height],[292,227]);
    const done=e.ctx.imageLayout([image(0)],1,node);
    assert.deepEqual([done.width,done.height],[520,390]);
    const tall=e.ctx.imageLayout([{url:'/offline/tall.png',width:100,height:1000}],1,node);
    assert.deepEqual([tall.width,tall.height],[72,440]);
});

test('history drawer source stays byte-identical and independent of batch constants', () => {
    const root=process.env.FIREFLY_495_EVIDENCE;
    const after=readFileSync(path.join(JS_DIR,'canvas-media-history.js'));
    if(root) assert.deepEqual(after,readFileSync(path.join(root,'before/canvas-media-history.js')));
    assert.doesNotMatch(after.toString(),/BATCH_THUMB|SMART_GROUP_LAYOUT/);
});

test('layout frames retain independently sized cards, even when a card matches old batch bounds', () => {
    const e=fixture([{id:'frame',type:'smart-group',layoutFrame:true,items:['card'],x:0,y:0,w:1000,h:500},
        {id:'card',type:'smart-image',x:88,y:99,w:313,h:325,_batchTotal:4,pending:4,images:[]}]);
    const before=e.stored();
    assert.equal(e.ctx.migrateSmartBatchSizes(),false);
    assert.equal(e.ctx.migrateSmartGroupImageMembers(),false);
    assert.deepEqual(e.stored(),before);
    const rect=e.ctx.imageLayout([],1,e.node('card'));
    assert.deepEqual([rect.width,rect.height],[313,325]);
});

test('real loadCanvas upgrades legacy batch pairs without moving neighbors or changing manual sizes', async () => {
    const nodes=[];
    for(const [count] of dimensions) {
        const g=oldApi()(`getBatchThumbGridSize(${count})`);
        nodes.push({id:`auto-${count}`,type:'smart-image',x:count*100,y:77,w:g.boxW,h:g.gridH+64,_batchTotal:count,images:Array.from({length:count},(_,i)=>image(i))});
        nodes.push({...nodes.at(-1),id:`manual-${count}`,w:g.boxW+1});
    }
    nodes.push({id:'neighbor',type:'smart-prompt',x:100,y:200,w:316,h:240});
    const e=fixture(nodes), before=fromContext(e.ctx.canvasForStorage());
    Object.assign(e.ctx,{canvasId:'unit-392',smartTitleText:null,conversationCanvasLastAcceptedRevision:0,smartNodeRunTokens:new Map(),smartCascadeRuns:new Map(),smartGenerationNodeRevisions:new Map(),smartEdgeReveal:null,
        loadProjectViewport:()=>({x:0,y:0,scale:1})});
    for(const name of ['rebuildMarkedAssetIdsFromNodes','updateSmartMarkedBadge','loadRecentSmartSettings','applyRecentSmartSettingsForCurrentMode','updateProviderModels','applyViewport','frameSmartCanvasContentIfNeeded','resumeConversationTasks','resumeSmartPendingTasks','resumeSmartPendingActionResumes','startCanvasMetaPoll','restorePersistedUndoStack','restorePersistedAnnotations']) e.ctx[name]=()=>{};
    let requests=0;
    e.ctx.fetch=async url=>{
        assert.equal(url,'/api/canvases/unit-392'); requests++;
        return {ok:true,json:async()=>JSON.parse(JSON.stringify(before))};
    };
    // loadCanvas expects the server envelope, not the document itself.
    const fetch=e.ctx.fetch;
    e.ctx.fetch=async url=>{const response=await fetch(url);return {ok:true,json:async()=>({canvas:await response.json()})};};
    assert.equal(await e.ctx.loadCanvas(),true,JSON.stringify(e.harness.toasts));
    assert.equal(requests,1);
    assert.deepEqual(e.stored().map(n=>[n.id,n.x,n.y]),before.nodes.map(n=>[n.id,n.x,n.y]));
    for(const [count] of dimensions) {
        const n=e.node(`auto-${count}`),g=e.ctx.getBatchThumbGridSize(count);
        assert.deepEqual([n.w,n.h],[g.boxW,g.gridH+80]);
        const original=before.nodes.find(n=>n.id===`manual-${count}`);
        assert.deepEqual([e.node(original.id).w,e.node(original.id).h],[original.w,original.h]);
    }
});

test('legacy thumbnail groups use actual natural-height old algorithm, not ticket width approximations', () => {
    for(const [count] of dimensions) {
        const e=fixture(),g=oldApi()(`getBatchThumbGridSize(${count})`),images=Array.from({length:count},(_,i)=>image(i));
        const h=e.ctx.mediaGroupGridHeight(images,g.cols,g.rows,136)+54;
        const group={id:'group',type:'smart-group',x:37,y:93,w:g.boxW,h,images,items:[]};
        evaluate(e.ctx,`canvasState.nodes=${JSON.stringify([group,{...group,id:'manual',h:h+1}])}`);
        assert.equal(e.ctx.migrateSmartBatchSizes(),true);
        assert.equal(e.node('group').w,e.ctx.getBatchThumbGridSize(count).boxW);
        assert.equal(e.node('manual').h,h+1);
        assert.deepEqual([e.node('group').x,e.node('group').y],[37,93]);
        const layout=e.ctx.smartGroupThumbLayout(e.node('manual'));
        assert.deepEqual([layout.width,layout.height],[g.boxW,h+1]);
    }
});
