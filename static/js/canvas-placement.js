// #246 freeSpot 落点扫描：从锚点向右扫描首个无碰撞空位（ComfyArt src/jobs.ts:231-243
// 算法直译，参考 docs/reference-comfyart-delta-202610.md §W3）。
//
// 现有三处落点（新建节点菜单固定偏移 / 素材粘贴网格 / 级联候选池）各写各的碰撞判定，
// 口径不一且无上限。本模块把落点收进一组纯函数：
//   - findFreeSpot(anchor, size, boxes)：从 anchor（新节点左上角）起向右扫描，
//     碰撞则 x = 碰撞盒 maxX + GAP（GAP=40，与 ComfyArt 同一套间距语义），
//     最多 200 步防死循环；返回 {x, y, exhausted}（exhausted=true 表示用尽步数仍碰撞，
//     位置照返由调用方决定回退策略——ComfyArt 原语义即如此，x 单调递增保证收敛）。
//   - rowSpots(anchor, sizes, boxes)：批量行排（ComfyArt src/jobs.ts:115-119 直译）——
//     单行横向排布，游标 = 上一落点右缘 + GAP；每件各自避让静态碰撞盒，
//     件间水平分离由游标保证（与参考实现同语义：不回头重查本批已摆位置）。
//
// 碰撞语义（ComfyArt 原文）：四边各留 GAP 空隙才算无碰撞——
//   x < b.maxX + GAP && x + w + GAP > b.minX && y < b.maxY + GAP && y + h + GAP > b.minY
// 每步 x = hit.maxX + GAP 严格前进（碰撞蕴含 hit.maxX > x - GAP），不会振荡，
// 步数上限是兜底而非防振荡。
//
// 纯函数：无 DOM、不依赖其他模块（碰撞盒由调用方用 nodeRect 等算好传入）。
// 对外 window.CanvasPlacement。算法参考 ComfyArt（MIT），代码为自行编写的 JS 移植。
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.CanvasPlacement = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const GAP = 40;
    const MAX_STEPS = 200;

    function toNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : 0;
    }

    function toBox(box) {
        // 兼容两种键形：{w,h}（本模块规范形）与 {width,height}（canvas-core nodeRect 口径）。
        return {
            x: toNumber(box?.x),
            y: toNumber(box?.y),
            w: toNumber(box && (box.w !== undefined ? box.w : box.width)),
            h: toNumber(box && (box.h !== undefined ? box.h : box.height))
        };
    }

    function toSize(size) {
        const b = toBox(size);
        return { w: Math.max(0, b.w), h: Math.max(0, b.h) };
    }

    function normalizeOptions(options) {
        const o = options || {};
        const gap = Number.isFinite(Number(o.gap)) ? Number(o.gap) : GAP;
        let steps = Number.isFinite(Number(o.maxSteps)) ? Number(o.maxSteps) : MAX_STEPS;
        if (!Number.isFinite(steps)) steps = MAX_STEPS;
        return { gap, maxSteps: Math.max(0, Math.floor(steps)) };
    }

    function toBoxes(boxes) {
        return (Array.isArray(boxes) ? boxes : []).map(toBox).filter(b => b.w > 0 && b.h > 0);
    }

    // 四边留 GAP 的 AABB 碰撞判定（ComfyArt freeSpot 原文条件）。
    function collidesAt(x, y, w, h, box, gap) {
        return x < box.x + box.w + gap
            && x + w + gap > box.x
            && y < box.y + box.h + gap
            && y + h + gap > box.y;
    }

    function firstHit(boxes, x, y, w, h, gap) {
        for (let i = 0; i < boxes.length; i++) {
            if (collidesAt(x, y, w, h, boxes[i], gap)) return boxes[i];
        }
        return null;
    }

    // 从 anchor（左上角）向右扫描首个无碰撞空位；碰撞则 x = 碰撞盒 maxX + GAP。
    // 返回 {x, y, exhausted}：exhausted=true 表示 maxSteps 用尽后仍碰撞（位置照返）。
    function findFreeSpot(anchor, size, boxes, options) {
        const opts = normalizeOptions(options);
        const s = toSize(size);
        const list = toBoxes(boxes);
        let x = toNumber(anchor?.x);
        const y = toNumber(anchor?.y);
        for (let i = 0; i < opts.maxSteps; i++) {
            const hit = firstHit(list, x, y, s.w, s.h, opts.gap);
            if (!hit) return { x, y, exhausted: false };
            x = hit.x + hit.w + opts.gap;
        }
        // 步数用尽：报告最终位置是否仍碰撞（循环内每步后位置已前移，不能复用上一步的 hit）。
        return { x, y, exhausted: Boolean(firstHit(list, x, y, s.w, s.h, opts.gap)) };
    }

    // 批量行排：单行横向排布，游标从 anchor 起、每件 = 上一落点右缘 + GAP。
    // sizes: [{w, h}, ...]；返回与 sizes 等长的 [{x, y, exhausted}, ...]。
    function rowSpots(anchor, sizes, boxes, options) {
        const opts = normalizeOptions(options);
        const list = toBoxes(boxes);
        const sizeList = (Array.isArray(sizes) ? sizes : []).map(toSize);
        const out = [];
        let cursorX = toNumber(anchor?.x);
        const cursorY = toNumber(anchor?.y);
        for (const s of sizeList) {
            const spot = findFreeSpot({ x: cursorX, y: cursorY }, s, list, opts);
            out.push({ x: spot.x, y: spot.y, exhausted: spot.exhausted });
            cursorX = spot.x + s.w + opts.gap;
        }
        return out;
    }

    return {
        GAP,
        MAX_STEPS,
        findFreeSpot,
        rowSpots
    };
});
