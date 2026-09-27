// 画布布局/几何纯工具（从 smart-canvas.js 抽离，#157 P1）。
// 纯函数 + 布局常量，零主作用域状态依赖。先于 smart-canvas.js 加载，
// 以全局名供主脚本使用。

const BATCH_THUMB = {
    ASPECT: {w:4, h:3},    // 4:3 横图
    WIDTH: 136,             // 宽度（与生图历史一致）
    HEIGHT: 102,            // 高度（136 × 3/4）
    GAP: 9,                 // 网格间距（与生图历史一致）
};

const SMART_GROUP_LAYOUT = {
    PADDING: 16,            // 四边内边距
    SUMMARY_H: 14,          // summary 行高度（图标+统计文字）
    SUMMARY_GAP: 8,         // summary 到内容的间距
};

const BATCH_NODE_PAD_H = SMART_GROUP_LAYOUT.PADDING * 2; // 32

const BATCH_NODE_PAD_V = SMART_GROUP_LAYOUT.PADDING * 4; // 64

const BATCH_STATUSBAR_H = 48;

const SINGLE_IMAGE_LAYOUT = {
    MAX_WIDTH: 260,         // 单图最大宽度
    MAX_HEIGHT: 380,        // 单图最大高度
    UNGROUP_GAP: 16,        // 解组后排列间距
};

function getBatchThumbGridSize(count){
    const c = Math.max(2, Math.min(8, count));
    // 优先选择能整除 count 的列数（4→2），避免网格出现空位；
    // 8张=4×2, 6张=3×2, 4张=2×2, 2张=2×1；无整除列时就近取 sqrt 列数
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
    
    return {
        cols,
        rows,
        gridW,
        gridH,
        boxW: gridW + pad,
        boxH: SMART_GROUP_LAYOUT.SUMMARY_H + SMART_GROUP_LAYOUT.SUMMARY_GAP + gridH + pad
    };
}

function getSingleImageLayoutSize(aspect, maxWidth){
    const maxW = maxWidth || SINGLE_IMAGE_LAYOUT.MAX_WIDTH;
    const maxH = SINGLE_IMAGE_LAYOUT.MAX_HEIGHT;
    const pad = SMART_GROUP_LAYOUT.PADDING * 2;
    
    let imgW = maxW;
    let imgH = Math.round(imgW * aspect.h / Math.max(1, aspect.w));
    
    // 高度超限，按高度反算
    if(imgH > maxH){
        imgH = maxH;
        imgW = Math.round(imgH * aspect.w / Math.max(1, aspect.h));
    }
    
    return {
        imgW,
        imgH,
        // 独立节点（生成卡片等）：四边等距，无 summary 行
        boxW: imgW + pad,
        boxH: imgH + pad,
        // 智能分组卡片：含顶部 summary 行
        groupBoxW: imgW + pad,
        groupBoxH: SMART_GROUP_LAYOUT.SUMMARY_H + SMART_GROUP_LAYOUT.SUMMARY_GAP + imgH + pad
    };
}

function getUngroupedImageSize(aspect){
    const refW = BATCH_THUMB.WIDTH;
    let imgW = refW;
    let imgH = Math.round(imgW * aspect.h / Math.max(1, aspect.w));
    
    // 限制范围并保持比例
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
    
    return {w:imgW, h:imgH};
}

const SMART_GROUP_DEFAULT_WIDTH = 340;

const SMART_GROUP_DEFAULT_HEIGHT = 286;

const SMART_GROUP_LEGACY_HEIGHT = 220;

const SMART_GROUP_MIN_WIDTH = 150;

const SMART_GROUP_MIN_HEIGHT = 130;

const SMART_GROUP_MAX_MEMBER_ZOOM = 4;

function smartGroupLayoutSize(node){
    const explicitW = Number(node?.w);
    const explicitH = Number(node?.h);
    const width = Number.isFinite(explicitW) && explicitW >= SMART_GROUP_MIN_WIDTH ? explicitW : SMART_GROUP_DEFAULT_WIDTH;
    const height = !Number.isFinite(explicitH) || explicitH === SMART_GROUP_LEGACY_HEIGHT
        ? SMART_GROUP_DEFAULT_HEIGHT
        : Math.max(explicitH, SMART_GROUP_MIN_HEIGHT);
    return {
        width:Math.round(width),
        height:Math.round(height)
    };
}

function smartGroupZoom(group){
    const z = Number(group?._memberZoom);
    return Number.isFinite(z) && z > 0 ? z : 1;
}

