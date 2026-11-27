import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {evaluate, fromContext, JS_DIR, loadModules} from './harness.mjs';
import {declarations, editor, identity, event} from './smart-human-handlers-harness-392.mjs';

const source = readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8').replace(/\r\n/g, '\n');
const photo = (id, x, extra = {}) => ({id, type:'smart-image', ...identity, images:[{url:`/${id}.png`, natural_w:600, natural_h:300}], x, y:20, w:200, h:140, scale:1, ...extra});

function gestures(nodes = [photo('a',20), photo('b',320), photo('c',620)]){
    const e = editor(nodes, harness => harness.element('imageEditModal'));
    const {ctx, harness} = e;
    loadModules(ctx, ['canvas-node-ops.js','canvas-snap.js']);
    evaluate(ctx, declarations('canvas-agent-tui.js'));
    Object.assign(ctx, {
        shell:harness.makeElement(), selectionBox:harness.makeElement(), CSS:{escape:String},
        smartMinimapDrag:null, portDragState:null, promptResizeState:null, selectionState:null, selectionJustFinished:false,
        previewCompareDrag:null, previewPanDrag:null, imageEditPanDrag:null, cropDrag:null, cropState:null, resizeState:null,
        llmInstructionResizeState:null, promptSplitResizeState:null, thumbDragState:null, panState:null, dragState:null,
        composerDragSettleTimer:0, interactionLayerRaf:0, suppressNodeClickUntil:0, suppressImageClickUntil:0,
        imageClickTimer:null, zoomPreviewState:null, didPan:false, isRKeyDown:false, Element:class {},
        MEDIA_NODE_DEFAULT_SCALE:2, MEDIA_GROUP_THUMB_BASE:224, MEDIA_GROUP_MAX_VISIBLE_ROWS:3,
        closeCreateMenu(){}, applyViewport(){}, syncSelectionUi(){}, updateComposer(){}, scheduleComposerUpdate(){},
        activeSmartCascadeCount:()=>0,
    });
    ctx.document.elementFromPoint = () => null;
    evaluate(ctx, source.slice(source.indexOf('shell.onmousedown ='), source.indexOf("minimap?.addEventListener('mousedown'")));
    evaluate(ctx, source.slice(source.indexOf('window.onmousemove ='), source.indexOf('function canvasWheelIsReserved')));
    evaluate(ctx, source.slice(source.indexOf("window.addEventListener('keydown', e => {\n    const key"), source.indexOf('engineSelect.onchange =')));
    const els = new Map(nodes.map(({id}) => {
        const el = harness.makeElement(), resize = harness.makeElement(), thumb = harness.makeElement();
        el.dataset.id = id;
        thumb.dataset.imageIndex = '0';
        el.querySelector = s => s === '.node-resize-handle' ? resize : null;
        el.querySelectorAll = s => s === '.thumb-item,.image-wrap' || s === '.thumb-item,.smart-group-single-thumb' ? [thumb] : [];
        return [id,{el,resize,thumb}];
    }));
    ctx.world.querySelectorAll = s => s === '.image-node' ? [...els.values()].map(({el}) => el) : [];
    ctx.bindNodeEvents();
    const pointer = extra => event({closest:()=>null}, {clientX:0, clientY:0, button:0, detail:1, stopImmediatePropagation(){}, ...extra});
    const key = (type, value, shiftKey) => harness.windowListeners.get(type).forEach(fn => fn(pointer({type,key:value,shiftKey})));
    const down = (id, extra) => els.get(id).el.onmousedown(pointer(extra));
    const move = extra => ctx.onmousemove(pointer(extra));
    const up = extra => ctx.onmouseup(pointer(extra));
    const click = (id, extra = {}) => { down(id,extra); up(extra); els.get(id).el.onclick(pointer(extra)); };
    const boxDown = (x,y,extra) => ctx.shell.onmousedown(pointer({clientX:x,clientY:y,...extra}));
    const box = (x,y,ex,ey,extra) => {
        boxDown(x,y,extra); move({clientX:ex,clientY:ey,...extra});
        const appearance = ctx.selectionBox.classList.toString();
        up({clientX:ex,clientY:ey,...extra}); ctx.shell.onclick(pointer(extra));
        for(const timer of harness.timers.filter(t => t.ms === 0 && !t.fired && !t.cancelled)){
            timer.fired = true;
            timer.fn();
        }
        return appearance;
    };
    const seed = (ids, image = {nodeId:'c', index:0}) => {
        ctx.canvasState.selectedIds = ids.length > 1 ? ids : [];
        ctx.canvasState.selectedId = ids.length === 1 ? ids[0] : '';
        ctx.canvasState.selectedImage = image;
    };
    const selection = () => fromContext(ctx.selectedNodeIds());
    const focus = () => fromContext(ctx.canvasState.selectedImage);
    const history = () => evaluate(ctx, 'undoStack.length');
    const resize = (id,extra) => els.get(id).resize.dispatchEvent(pointer({type:'mousedown',...extra}));
    const size = (id = 'a') => [e.node(id).w,e.node(id).h];
    return {...e,els,pointer,key,down,move,up,click,boxDown,box,seed,selection,focus,history,resize,size};
}

test('Ctrl and Cmd clicks union with the selection, Alt subtracts without changing image focus or history', () => {
    const e = gestures();
    e.seed(['a']);
    e.click('b',{ctrlKey:true});
    assert.deepEqual(e.selection(),['a','b']);
    assert.equal(e.ctx.canvasState.selectedId,'');
    e.click('b',{metaKey:true});
    e.click('c',{altKey:true});
    assert.deepEqual(e.selection(),['a','b']);
    e.click('a',{altKey:true});
    assert.deepEqual(e.selection(),['b']);
    assert.equal(e.ctx.canvasState.selectedId,'b');
    e.click('b',{altKey:true});
    assert.deepEqual(e.selection(),[]);
    assert.equal(e.ctx.canvasState.selectedId,'');
    assert.deepEqual(e.focus(),{nodeId:'c',index:0});
    assert.equal(e.stored().length,3);
    assert.equal(e.history(),0);
});

test('modified boxes are additive/subtractive and distinguishable; R replaces and plain blank clears', () => {
    const e = gestures();
    e.seed(['a']);
    const add = e.box(300,0,530,175,{ctrlKey:true});
    assert.deepEqual(e.selection(),['a','b']);
    e.box(600,0,830,175,{metaKey:true});
    assert.deepEqual(e.selection(),['a','b','c']);
    assert.equal(e.box(300,0,530,175,{altKey:true}) === add,false);
    assert.deepEqual(e.selection(),['a','c']);
    assert.equal(e.ctx.selectionBox.style.display,'none');
    assert.deepEqual(e.focus(),{nodeId:'c',index:0});
    e.key('keydown','r');
    assert.equal(e.box(300,0,530,175,{}).includes('subtract'),false);
    e.key('keyup','r');
    assert.deepEqual(e.selection(),['b']);
    assert.equal(e.ctx.canvasState.selectedId,'b');
    assert.deepEqual(e.focus(),{nodeId:'',index:-1});
    e.click('c');
    assert.deepEqual(e.selection(),['c']);
    e.boxDown(900,0,{}); e.up({clientX:900});
    e.ctx.shell.onclick(e.pointer());
    assert.deepEqual(e.selection(),[]);
    assert.equal(e.history(),0);
});

test('Shift still starts the blank eraser, even when a box modifier is held', () => {
    const e = gestures();
    e.seed(['a']);
    e.ctx.updateConnectionEraseTrail = () => {};
    e.ctx.clearConnectionEraseTrail = () => {};
    e.boxDown(300,0,{shiftKey:true,altKey:true});
    assert.equal(e.ctx.shell.classList.contains('connection-erasing'),true);
    assert.equal(e.ctx.selectionState,null);
    e.up({clientX:300,shiftKey:true,altKey:true});
    assert.deepEqual(e.selection(),['a']);
});

test('thumbnail modifier clicks preserve focus while plain click and double click still target the thumbnail', () => {
    const e = gestures();
    const thumb = e.els.get('b').thumb;
    const opened = [];
    e.ctx.openSmartImageDetail = (...args) => opened.push(args);
    e.seed(['a']);
    const thumbClick = (extra) => thumb.dispatchEvent(e.pointer({type:'click',...extra}));
    thumbClick({ctrlKey:true});
    assert.deepEqual(e.selection(),['a','b']);
    thumb.dispatchEvent(e.pointer({type:'mousedown',altKey:true,detail:2}));
    thumbClick({altKey:true,detail:2});
    thumb.dispatchEvent(e.pointer({type:'dblclick',altKey:true,detail:2}));
    assert.deepEqual(e.selection(),['a']);
    assert.deepEqual(opened,[]);
    assert.deepEqual(e.focus(),{nodeId:'c',index:0});
    thumbClick({});
    const timer = e.harness.timers.find(t => t.ms === 220 && !t.cancelled);
    assert.ok(timer);
    timer.fn();
    assert.deepEqual(e.selection(),['b']);
    assert.deepEqual(e.focus(),{nodeId:'b',index:0});
    thumb.dispatchEvent(e.pointer({type:'dblclick',detail:2}));
    assert.deepEqual(opened,[['b',0]]);
});

test('modifier drag on a multi-image thumbnail clones the node rather than detaching the image', async () => {
    const a = photo('a',20,{images:[{url:'/a.png'},{url:'/detail.png'}]});
    const e = gestures([a,photo('b',320)]);
    const {el,thumb} = e.els.get('a');
    let stopped = false;
    const target = {closest:s=>s.includes('.thumb-item') ? thumb : null};
    const press = {...e.pointer({type:'mousedown',altKey:true}),target,stopPropagation(){stopped = true;}};
    thumb.dispatchEvent(press);
    if(!stopped) el.onmousedown(press);
    e.move({clientX:5,altKey:true});
    assert.equal(e.stored().length,3);
    e.move({clientX:90,clientY:40,altKey:true});
    e.up({clientX:90,clientY:40,altKey:true});
    assert.equal(e.node('a').images.length,2);
    assert.equal(e.stored().find(n=>!['a','b'].includes(n.id)).images.length,2);
    assert.equal(e.history(),1);
    await e.ctx.performUndo();
    assert.equal(e.stored().length,2);
});

test('Alt clones only past four CSS pixels, clones once, and undoes copy and placement together', async () => {
    const e = gestures();
    e.ctx.viewport.scale = 2;
    e.seed(['a']);
    const before = e.stored();
    e.down('a',{altKey:true});
    assert.deepEqual(e.stored(),before);
    e.move({clientX:4,altKey:true});
    assert.deepEqual(e.stored(),before);
    assert.equal(e.history(),0);
    e.up({clientX:4,altKey:true});
    e.els.get('a').el.onclick(e.pointer({clientX:4,altKey:true}));
    assert.deepEqual(e.selection(),[]);
    assert.equal(e.history(),0);
    e.down('a',{altKey:true});
    e.move({clientX:5,altKey:true});
    assert.equal(e.stored().length,4);
    e.move({clientX:76,clientY:30,altKey:true});
    e.up({clientX:76,clientY:30,altKey:true});
    const copy = e.stored().find(n => !['a','b','c'].includes(n.id));
    assert.ok(copy.x > e.node('a').x);
    assert.equal(copy.createdBy,'user');
    assert.equal(e.history(),1);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),before);
    await e.ctx.performRedo();
    assert.deepEqual(e.stored().find(n => n.id === copy.id),copy);
});

test('Ctrl jitter stays a click at high zoom-out, Cmd drag still creates a real input edge', async () => {
    const e = gestures();
    e.ctx.viewport.scale = 0.25;
    e.seed(['c']);
    e.down('a',{ctrlKey:true});
    e.move({clientX:4,ctrlKey:true});
    assert.equal(e.node('a').x,20);
    e.up({clientX:4,ctrlKey:true});
    e.els.get('a').el.onclick(e.pointer({clientX:4,ctrlKey:true}));
    assert.deepEqual(e.selection(),['c','a']);
    assert.equal(e.history(),0);
    e.seed(['c']);
    e.down('a',{metaKey:true});
    e.move({clientX:75,metaKey:true});
    e.up({clientX:75,metaKey:true});
    const edges = () => fromContext(evaluate(e.ctx,'canvas.connections')).map(({from,to,kind})=>({from,to,kind}));
    assert.deepEqual(edges(),[{from:'a',to:'b',kind:'input'}]);
    assert.equal(e.node('a').x,20);
    assert.equal(e.node('b').userTouchedAt,1234);
    assert.equal(e.history(),1);
    await e.ctx.performUndo();
    assert.deepEqual(edges(),[]);
});

test('Shift toggles immediately at a stationary pointer; the active image wins over the batch ratio', async () => {
    const e = gestures([photo('a',20,{_generationAspect:{w:4,h:3},images:[{url:'/wide.png',natural_w:600,natural_h:300},{url:'/tall.png',natural_w:200,natural_h:400}]})]);
    const start = e.stored();
    e.seed(['a'],{nodeId:'a',index:1});
    e.resize('a');
    e.move({clientX:20,clientY:70});
    assert.deepEqual(e.size(),[220,210]);
    e.key('keydown','Shift',true);
    assert.deepEqual(e.size(),[105,210]);
    e.key('keyup','Shift',false);
    assert.deepEqual(e.size(),[220,210]);
    e.key('keydown','Shift',true);
    assert.deepEqual(e.size(),[105,210]);
    e.up({clientX:20,clientY:70,shiftKey:true});
    assert.equal(e.node('a').userTouchedAt,1234);
    assert.equal(e.node('a').promptEditedByUser,false);
    assert.equal(e.history(),1);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored(),start);
});

test('Shift resize clamps both edges together, and unknown active dimensions fall back to the starting box', () => {
    const e = gestures([photo('a',20), photo('b',320,{_generationAspect:{w:16,h:9},images:[{url:'/wide.png',natural_w:600,natural_h:300},{url:'/unknown.png',preview_w:99,preview_h:99}]})]);
    e.resize('a',{shiftKey:true});
    e.move({clientX:-500,clientY:-450,shiftKey:true});
    assert.deepEqual(e.size(),[96,48]);
    e.up({clientX:-500,clientY:-450,shiftKey:true});
    e.seed(['b'],{nodeId:'b',index:1});
    e.resize('b',{shiftKey:true});
    e.move({clientX:100,clientY:10,shiftKey:true});
    assert.deepEqual(e.size('b'),[300,210]);
});

test('an already proportional start has no Shift jump at the origin, and non-image resize stays free', () => {
    const e = gestures([photo('a',20,{h:100}),photo('b',320,{type:'agent-task',w:320,h:240})]);
    e.resize('a');
    e.key('keydown','Shift',true);
    assert.deepEqual(e.size(),[200,100]);
    e.up({shiftKey:true});
    assert.equal(e.history(),0);
    e.resize('b',{shiftKey:true});
    e.move({clientX:20,clientY:60,shiftKey:true});
    assert.deepEqual(e.size('b'),[340,300]);
});
