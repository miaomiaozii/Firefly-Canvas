// #255 MIT 移植：ComfyArt src/vectorPath.ts（v1.1.0 @ 13396a8，54 行）。
//
// Copyright (c) 2026 Terry Jia
// SPDX-License-Identifier: MIT
// 完整版权声明见 NOTICE.md（保留原 copyright 行）。
//
// 移植范围（TS → JS 逐式对照，逻辑零改动）：
//   - scalePath(d, w, h)：SVG path 字符串坐标缩放到 w×h（0..1 归一化路径 → 画布像素）；
//     PAIRED 指令（MLCSQTmlcsqt）参数按 x,y 交替分配轴，H/h 恒 x 轴，其余单参数指令（V/v）恒 y 轴；
//     数值 round 到 1e-5。
//   - nodesToPath(nodes, closed)：贝塞尔节点（x/y + 手柄 hx/hy）→ 归一化 0..1 SVG path；
//     bbox 取 x/hx 三点（手柄外扩），w/h 下限 1（退化不除零），closed 追加「末→首」段 + Z。
// 未移植：loadPaper / normalized（依赖 paper.js 运行时，超出本票范围，NOTICE.md 已注明）。
// 移植加固（仅两处，均在源码边界）：空/null 节点列表返回空路径，而非原 TS 的 Infinity 坐标。
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.CanvasVectorPath = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const round = (v) => Math.round(v * 1e5) / 1e5;
    const MIN_SIZE = 1e-6;
    const PAIRED = 'MLCSQTmlcsqt';

    function scalePath(d, w, h) {
        const tokens = String(d || '').match(/[a-zA-Z]|-?\d*\.?\d+(?:e[-+]?\d+)?/g) || [];
        let cmd = '';
        let index = 0;
        return tokens.map((tok) => {
            if (/[a-zA-Z]/.test(tok)) {
                cmd = tok;
                index = 0;
                return tok;
            }
            const v = Number(tok);
            const axis = PAIRED.includes(cmd) ? index++ % 2 : cmd === 'H' || cmd === 'h' ? 0 : 1;
            return String(round(v * (axis ? h : w)));
        }).join(' ');
    }

    function nodesToPath(nodes, closed) {
        const list = Array.isArray(nodes) ? nodes : [];
        if (!list.length) return { x: 0, y: 0, w: 1, h: 1, d: '' };
        const xs = list.flatMap((n) => [n.x, n.x + n.hx, n.x - n.hx]);
        const ys = list.flatMap((n) => [n.y, n.y + n.hy, n.y - n.hy]);
        const x = Math.min(...xs);
        const y = Math.min(...ys);
        const w = Math.max(1, Math.max(...xs) - x);
        const h = Math.max(1, Math.max(...ys) - y);
        const nx = (v) => round((v - x) / w);
        const ny = (v) => round((v - y) / h);
        const seg = (a, b) => `C ${nx(a.x + a.hx)} ${ny(a.y + a.hy)} ${nx(b.x - b.hx)} ${ny(b.y - b.hy)} ${nx(b.x)} ${ny(b.y)}`;
        const parts = [`M ${nx(list[0].x)} ${ny(list[0].y)}`, ...list.slice(1).map((n, i) => seg(list[i], n))];
        if (closed) parts.push(seg(list[list.length - 1], list[0]), 'Z');
        return { x, y, w, h, d: parts.join(' ') };
    }

    return { scalePath, nodesToPath, MIN_SIZE };
});
