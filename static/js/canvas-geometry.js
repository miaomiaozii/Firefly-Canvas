// #255 分数坐标骨架：0..1 公共坐标，统一 crop / mask / brush 的像素换算。
//
// ComfyArt 参考（docs/reference-comfyart-canvas.md §70，EditLayers.tsx 205 行）：
// 「uv 一律 clamp [0,1]」，拖完的矩形走 norm 归一化。我方图像编辑器里 crop / mask / brush
// 三套工具各自做 client 像素 → 画布像素换算，口径不一时笔迹位置会漂移。本模块把换算收进
// 一组纯函数：
//   - 0..1 分数是唯一公共坐标系，像素空间只是它的一次投影；
//   - 不钳制对（fractionFromPoint / pointFromFraction）与旧「直接缩放」行为逐式同构，
//     供既有渲染路径原样切换；
//   - 钳制对（unitFractionFromPoint）把结果 clamp 到 [0,1]，供命中检测 / 图层面板使用；
//   - normalizeUnitRect：拖拽归一化（clamp + 翻转 + 宽高取正），EditLayers norm 语义；
//   - pointInPolygon：even-odd 射线法点-多边形命中（票面对应 ComfyArt utils.ts:250-268 的
//     槽位；该文件在 ComfyArt 仓库中不存在，此处为自研标准 even-odd 实现，见 NOTICE.md）。
//
// 纯函数：无 DOM、无依赖其他模块。对外 window.CanvasGeometry。
// 算法概念参考 ComfyArt（MIT，Copyright (c) 2026 Terry Jia），代码为自行编写的 JS 移植。
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.CanvasGeometry = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function clamp(value, min, max) {
        const n = Number(value);
        if (Number.isNaN(n)) return min;
        if (n === Infinity) return max;
        if (n === -Infinity) return min;
        if (n < min) return min;
        if (n > max) return max;
        return n;
    }

    function clampUnit(value) {
        return clamp(value, 0, 1);
    }

    function toNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? n : 0;
    }

    function toBox(box) {
        return {
            x: toNumber(box?.x),
            y: toNumber(box?.y),
            w: toNumber(box?.w),
            h: toNumber(box?.h)
        };
    }

    // 像素点 → 0..1 分数（不钳制；与既有「(p - box.x) / box.w」直连换算同构）。
    function fractionFromPoint(point, box) {
        const b = toBox(box);
        return {
            x: b.w ? (toNumber(point?.x) - b.x) / b.w : 0,
            y: b.h ? (toNumber(point?.y) - b.y) / b.h : 0
        };
    }

    // 0..1 分数 → 像素点（不钳制）。
    function pointFromFraction(fraction, box) {
        const b = toBox(box);
        return {
            x: b.x + toNumber(fraction?.x) * b.w,
            y: b.y + toNumber(fraction?.y) * b.h
        };
    }

    // 像素点 → 0..1 分数（钳制到 [0,1]；ComfyArt：uv 一律 clamp [0,1]）。
    function unitFractionFromPoint(point, box) {
        const f = fractionFromPoint(point, box);
        return { x: clampUnit(f.x), y: clampUnit(f.y) };
    }

    // 像素矩形 → 0..1 矩形（不钳制）。
    function fractionRectFromRect(rect, box) {
        const b = toBox(box);
        const r = toBox(rect);
        return {
            x: b.w ? (r.x - b.x) / b.w : 0,
            y: b.h ? (r.y - b.y) / b.h : 0,
            w: b.w ? r.w / b.w : 0,
            h: b.h ? r.h / b.h : 0
        };
    }

    // 0..1 矩形 → 像素矩形（不钳制）。
    function rectFromFractionRect(rect, box) {
        const b = toBox(box);
        return {
            x: toNumber(rect?.x) * b.w,
            y: toNumber(rect?.y) * b.h,
            w: toNumber(rect?.w) * b.w,
            h: toNumber(rect?.h) * b.h
        };
    }

    // 拖拽归一化：[x0, y0, x1, y1] → clamp 到 [0,1] + 翻转使 x1>=x0 / y1>=y0 → [x, y, w, h]。
    // EditLayers norm 语义（docs/reference-comfyart-canvas.md §70）。
    function normalizeUnitRect(coords) {
        const list = Array.isArray(coords) ? coords : [];
        const a = clampUnit(list[0]);
        const b = clampUnit(list[1]);
        const c = clampUnit(list[2]);
        const d = clampUnit(list[3]);
        return [Math.min(a, c), Math.min(b, d), Math.abs(c - a), Math.abs(d - b)];
    }

    // even-odd 射线法：点-多边形命中（标准 even-odd 规则）。
    // polygon 支持两种形态：
    //   - 单个多边形：[{x, y}, ...]（首尾自动闭合）；
    //   - 多子路径（孔洞）：[[{x, y}, ...], [{x, y}, ...]]（SVG M...Z 子路径语义）。
    // 注意：孔洞必须作为独立子路径传入；把外环+内环顶点拼成一个平坦列表会产生
    // 虚假连接边（最后一顶点连回首顶点），命中结果不可靠。
    function pointInPolygon(point, polygon) {
        const px = Number(point?.x);
        const py = Number(point?.y);
        if (!Number.isFinite(px) || !Number.isFinite(py)) return false;
        if (!Array.isArray(polygon) || !polygon.length) return false;
        const rings = polygon.length && Array.isArray(polygon[0]) ? polygon : [polygon];
        let inside = false;
        for (const ring of rings) {
            const n = Array.isArray(ring) ? ring.length : 0;
            // 退化防御：只要有一个顶点非有限，边配对语义就不可靠——该子路径整体无效。
            let valid = n > 0;
            for (let k = 0; valid && k < n; k++) {
                if (!Number.isFinite(Number(ring[k]?.x)) || !Number.isFinite(Number(ring[k]?.y))) valid = false;
            }
            if (!valid) continue;
            for (let i = 0, j = n - 1; i < n; j = i++) {
                const xi = Number(ring[i].x);
                const yi = Number(ring[i].y);
                const xj = Number(ring[j].x);
                const yj = Number(ring[j].y);
                if (yi > py !== yj > py) {
                    const xCross = ((xj - xi) * (py - yi)) / (yj - yi) + xi;
                    if (px < xCross) inside = !inside;
                }
            }
        }
        return inside;
    }

    // 像素空间多边形 → 0..1 多边形（在分数坐标系里做路径命中测试用）。
    function unitPolygonFromPoints(points, box) {
        if (!Array.isArray(points)) return [];
        return points.map(p => unitFractionFromPoint(p, box));
    }

    return {
        clamp,
        clampUnit,
        fractionFromPoint,
        pointFromFraction,
        unitFractionFromPoint,
        fractionRectFromRect,
        rectFromFractionRect,
        normalizeUnitRect,
        pointInPolygon,
        unitPolygonFromPoints
    };
});
