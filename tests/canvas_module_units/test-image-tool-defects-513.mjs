import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

function section(file, start, end){
    const source = readFileSync(new URL(`../../static/js/${file}.js`, import.meta.url), 'utf8');
    return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start) + start.length));
}

function editFixture(){
    const image = {name:'source.png', url:'/source.png', asset_id:'source', natural_w:60, natural_h:40};
    const node = {id:'source', x:20, y:30, images:[image], promptDraftText:'preserve me'};
    const output = [];
    const calls = [];
    const fakeCanvas = () => ({getContext:() => ({fillRect(){}, drawImage(){}}), toBlob(cb){cb(new Blob(['png']));}});
    const ctx = vm.createContext({
        document:{getElementById:() => ({naturalWidth:60, naturalHeight:40}), createElement:fakeCanvas},
        cropState:{}, outpaintAspectPreset:'4:3', settings:{}, cloneSmartSettings:value => ({...value}),
        currentEditImage:() => ({node,image}), clampOutpaint(){}, editScaleRectToNatural:() => ({x:2,y:4,w:80,h:60}),
        captureImageEditTarget:() => ({nodeId:'source'}), liveImageEditTarget:()=>({node}),
        uploadCroppedBlob:async()=>({url:'/composite',name:'composite.png'}),
        importProjectMediaReference:async()=>({asset_id:'composite'}), replaceEditedImage:()=>{calls.push('replace'); return true;},
        applyOutpaintSizeToSmartParams:(_w,_h,subject)=>calls.push(subject.id),
        setPromptDraftForNode:(subject,text)=>{subject.promptDraftText=text;}, activeSettingsSubject:()=>null,
        canvasState:{nodes:[node]}, canvasCore:{nodeRect:()=>({x:20,y:30,width:60,height:40}), imageLayout:()=>({width:60})},
        createNode:(x,y,images)=>{ const made={id:'new',x,y,images}; output.push(made); return made; },
        finishImageEdit:()=>calls.push('finish'), tr:key=>key, toast:message=>calls.push(message),
        safeExportFileName:name=>name, downloadNameForMediaItem:item=>item.name, gridOperationMode:'split',
        gridSplitRects:()=>[{row:0,col:0,x:0,y:0,w:30,h:40},{row:0,col:1,x:30,y:0,w:30,h:40}],
        gridLayoutFromRects:()=>({cols:2}), nodeScale:()=>1,
        uploadImageBlobs:async()=>[{url:'/only-one',name:'tile.png'}],
        currentGridJoinItems:()=>[{index:0,source:image,item:image},{index:1,source:image,item:image}],
        ensureGridJoinLayout:()=>({items:[{index:0,x:0,y:0,w:30,h:30},{index:1,x:30,y:0,w:30,h:20}]}),
        gridJoinCanvasSize:()=>({w:60,h:30}), gridJoinOutputSize:1024,
        imageEditTarget:()=>({nodeId:'source'}), loadGridJoinImage:async()=>({naturalWidth:60,naturalHeight:40}),
        drawImageCover(){}, window:{ffDialog:{ask:async message=>{calls.push(message.message);return false;}}},
        createImageNodeAt:()=>{output.push({id:'joined'});return output.at(-1);},
    });
    return {ctx,node,output,calls};
}

test('outpaint creates a project-owned input on the right, leaving source and prompt unchanged', async () => {
    const {ctx,node,output,calls} = editFixture();
    vm.runInContext(section('canvas-image-editor','async function applyImageOutpaint(', 'async function applyImageMask('),ctx);
    await ctx.applyImageOutpaint();
    assert.equal(output.length,1);
    assert.ok(output[0].x > node.x + 60);
    assert.equal(output[0].images[0].asset_id,'composite');
    assert.equal(output[0].promptDraftText,'smart.outpaintPromptSuggestion');
    assert.equal(node.promptDraftText,'preserve me');
    assert.equal(node.images[0].asset_id,'source');
    assert.deepEqual(calls,['new','finish']);
});

test('grid split rejects a partial upload instead of publishing a shifted grid', async () => {
    const {ctx,output,calls} = editFixture();
    vm.runInContext(section('canvas-image-editor','async function applyImageGridSplit(', 'function loadGridJoinImage('),ctx);
    await ctx.applyImageGridSplit();
    assert.equal(output.length,0);
    assert.deepEqual(calls,['smart.gridSplitUploadFailed']);
});

test('grid join asks before a crop and cancellation does not publish a node', async () => {
    const {ctx,output,calls} = editFixture();
    vm.runInContext(section('canvas-image-editor','async function applyImageGridJoin(', 'async function applyImageResize('),ctx);
    assert.equal(await ctx.applyImageGridJoin(), false);
    assert.deepEqual(calls,['smart.gridJoinCropMessage']);
    assert.equal(output.length,0);
});

test('Photoshop action stays in the toolbar but cannot send a video asset', async () => {
    const calls = [];
    const ctx = vm.createContext({
        NODE_TYPE_IMAGE:'smart-image', smartNodeToolbarImageIndex:()=>0, imageForDisplay:item=>item,
        mediaKindForItem:item=>item.kind, tr:key=>key, toast:key=>calls.push(key),
        window:{}, canvas:{project_id:'project'},
        imageToolNodeEditable:()=>true, imageToolIsImage:item=>item?.kind === 'image',
        imageToolScope:()=>({}), imageToolItemAssetId:(_scope,item)=>item.asset_id,
        imageToolColorProvenance:item=>item.edit, isLocalEditOriginalItem:()=>true,
        psSendRoundTrip:async()=>calls.push('sent'),
    });
    const node={id:'node',type:'smart-image',images:[{asset_id:'owned',kind:'video'}]};
    vm.runInContext(section('smart-canvas','const smartImageToolbar =', 'function duplicateSmartNodeMediaToCanvas('),ctx);
    vm.runInContext(section('smart-canvas','async function sendSmartNodeImageToPhotoshop(', 'function runSmartNodeToolbarAction('),ctx);
    vm.runInContext('smartImageToolbarPsConnected = true;', ctx);
    const photoshop = ctx.smartImageToolbarActions(node, ctx.smartImageToolbarMedia(node), false).find(action => action.key === 'send-ps');
    assert.equal(photoshop.enabled, false);
    await ctx.sendSmartNodeImageToPhotoshop(node,0);
    assert.deepEqual(calls,['smart.noProjectMediaOriginal']);
    node.images[0] = {asset_id:'owned',kind:'image',url:'/source.png'};
    const media = ctx.smartImageToolbarMedia(node);
    const enabled = multi => Array.from(ctx.smartImageToolbarActions(node, media, multi).filter(action => action.enabled), action => action.key);
    assert.deepEqual(enabled(true), ['preview','flip','color','flip-vertical']);
    assert.ok(enabled(false).includes('send-ps'));
    vm.runInContext('smartImageToolbarPsConnected = false;', ctx);
    assert.ok(!enabled(false).includes('send-ps'));
    assert.ok(!enabled(false).includes('restore'));
    node.images[0].edit = {source_asset_id:'original'};
    assert.ok(enabled(false).includes('restore'));
});

test('single preview download reports an unsuccessful HTTP response and saves a valid result', async () => {
    const events=[];
    let ok=false;
    const ctx = vm.createContext({
        document:{createElement:()=>({click(){events.push('unverified-link');},remove(){}}),body:{appendChild(){}}},
        downloadNameForMediaItem:()=> 'out.png', fetch:async()=>({ok}),
        canvasReceiveBlob:async response=>{if(!response.ok) throw new Error('server message'); return {blob:'verified'};},
        canvasSaveBlobAsFile:(blob,name)=>events.push([blob,name]),
        console:{warn(){}}, toast:text=>events.push(text), tr:key=>key,
    });
    vm.runInContext(section('smart-canvas','function downloadPreviewImage(', 'function previewDownloadGroupItems('),ctx);
    await ctx.downloadPreviewFile({url:'/source'});
    assert.deepEqual(events,['smart.previewDownloadFailed']);
    ok=true;
    await ctx.downloadPreviewFile({url:'/source'});
    assert.deepEqual(events,['smart.previewDownloadFailed',['verified','out.png']]);
});

test('mask application and image-editor toolbar controls use bilingual action labels', () => {
    const labels = section('i18n/canvas','"canvas.applyMask":','"canvas.gridHint":');
    const html = readFileSync(new URL('../../static/smart-canvas.html', import.meta.url), 'utf8');
    assert.match(labels, /zh: "添加遮罩", en: "Add Mask"/);
    assert.match(labels, /zh: "输出切分图片", en: "Output Split Images"/);
    assert.match(html, /id="gridSplitModeBtn"[^>]*data-i18n="canvas.gridSplit"/);
    assert.match(html, /id="brushUndoBtn"[^>]*data-i18n-title="canvas.editUndo"/);
});
