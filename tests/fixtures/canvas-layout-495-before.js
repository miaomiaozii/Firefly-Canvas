// Frozen pre-#495 geometry for migration/compatibility tests, not a runtime implementation.
const BATCH_THUMB = {ASPECT:{w:4,h:3},WIDTH:136,HEIGHT:102,GAP:9};
const SMART_GROUP_LAYOUT = {PADDING:16,SUMMARY_H:14,SUMMARY_GAP:8};
const SINGLE_IMAGE_LAYOUT = {MAX_WIDTH:260,MAX_HEIGHT:380,UNGROUP_GAP:16};
function getBatchThumbGridSize(count){
    const c = Math.max(2, Math.min(8, count));
    let cols = null;
    for(let cand = 4; cand >= 2; cand--){
        if(c % cand === 0 && Math.ceil(c / cand) >= 2 && Math.ceil(c / cand) <= 4){
            cols = cand;
            break;
        }
    }
    if(cols === null){
        cols = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(c))));
    }
    const rows = Math.ceil(c / cols);
    const w = BATCH_THUMB.WIDTH;
    const h = BATCH_THUMB.HEIGHT;
    const gap = BATCH_THUMB.GAP;
    const pad = SMART_GROUP_LAYOUT.PADDING * 2;
    const gridW = cols * w + (cols - 1) * gap;
    const gridH = rows * h + (rows - 1) * gap;
    return {cols,rows,gridW,gridH,boxW:gridW+pad,boxH:SMART_GROUP_LAYOUT.SUMMARY_H+SMART_GROUP_LAYOUT.SUMMARY_GAP+gridH+pad};
}
function getSingleImageLayoutSize(aspect, maxWidth){
    const maxW = maxWidth || SINGLE_IMAGE_LAYOUT.MAX_WIDTH;
    const maxH = SINGLE_IMAGE_LAYOUT.MAX_HEIGHT;
    const pad = SMART_GROUP_LAYOUT.PADDING * 2;
    let imgW = maxW;
    let imgH = Math.round(imgW * aspect.h / Math.max(1, aspect.w));
    if(imgH > maxH){
        imgH = maxH;
        imgW = Math.round(imgH * aspect.w / Math.max(1, aspect.h));
    }
    return {imgW,imgH,boxW:imgW+pad,boxH:imgH+pad,groupBoxW:imgW+pad,groupBoxH:SMART_GROUP_LAYOUT.SUMMARY_H+SMART_GROUP_LAYOUT.SUMMARY_GAP+imgH+pad};
}
function getUngroupedImageSize(aspect){
    const refW = BATCH_THUMB.WIDTH;
    let imgW = refW;
    let imgH = Math.round(imgW * aspect.h / Math.max(1, aspect.w));
    if(imgW > 260){
        imgW = 260;
        imgH = Math.round(imgW * aspect.h / Math.max(1, aspect.w));
    }
    if(imgH > 380){
        imgH = 380;
        imgW = Math.round(imgH * aspect.w / Math.max(1, aspect.h));
    }
    if(imgW < 80){
        imgW = 80;
        imgH = Math.round(imgW * aspect.h / Math.max(1, aspect.w));
    }
    if(imgH < 60){
        imgH = 60;
        imgW = Math.round(imgH * aspect.w / Math.max(1, aspect.h));
    }
    return {w:imgW,h:imgH};
}
