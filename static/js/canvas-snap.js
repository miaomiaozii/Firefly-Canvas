// canvas-snap.js — 拖拽对齐吸附 + 参考线（issue #253 第 1 项）
//
// 纯函数核心（window.FireflySnap；module.exports 供 node 端单测）：
//   - computeSnap(target, others, opts) -> { dx, dy, guides: [{axis, value}] }
//     target/others 为 { x, y, width, height, cx, cy }（世界坐标 px）。
//     与相邻节点的 left/right/centerX 与 top/bottom/centerY 做阈值吸附，
//     取每轴最小位移；guides 携带吸附线坐标（value = 对齐后的边/中心位置）。
//   - snapDelta(base, snap) -> { dx, dy }：把吸附位移叠加到拖拽增量上。
//   - guidesToPixels(guides, canvas) -> [{ axis, value, from, to }]：参考线延伸到画布边界。
//
// DOM 层（window.FireflySnapUI）：
//   - showGuides(container, guides) / clearGuides(container)
//     container 由调用方传入（画布 #world，参考线随视口缩放/平移），
//     guides 为世界坐标；模块本身不引用 smart-canvas.js 的任何顶层名（棘轮 0）。

(function (global, factory) {
  if (typeof module !== "undefined" && module.exports) module.exports = factory();
  global.FireflySnap = factory();
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function boxCenter(box) {
    const x = Number(box.x) || 0;
    const y = Number(box.y) || 0;
    const w = Number(box.width) || 0;
    const h = Number(box.height) || 0;
    return {
      x, y, w, h,
      cx: box.cx != null ? Number(box.cx) : x + w / 2,
      cy: box.cy != null ? Number(box.cy) : y + h / 2,
    };
  }

  function computeSnap(target, others, opts) {
    const o = Object.assign({ threshold: 8, enableCentering: true }, opts || {});
    if (!target || typeof target !== "object" || !isFinite(Number(target.x)) || !isFinite(Number(target.y))) {
      return { dx: 0, dy: 0, guides: [] };
    }
    const t = boxCenter(target);
    const best = { x: null, y: null };
    const consider = (axis, refPos, delta) => {
      if (!isFinite(delta) || Math.abs(delta) > o.threshold) return;
      const cur = best[axis];
      if (cur === null || Math.abs(delta) < Math.abs(cur.delta)) best[axis] = { pos: refPos, delta };
    };
    if (Array.isArray(others)) {
      for (const s of others) {
        if (!s || typeof s !== "object" || !isFinite(Number(s.x)) || !isFinite(Number(s.y))) continue;
        const b = boxCenter(s);
        consider("x", b.x, b.x - t.x);
        consider("x", b.x + b.w, b.x + b.w - (t.x + t.w));
        if (o.enableCentering) consider("x", b.cx, b.cx - t.cx);
        consider("y", b.y, b.y - t.y);
        consider("y", b.y + b.h, b.y + b.h - (t.y + t.h));
        if (o.enableCentering) consider("y", b.cy, b.cy - t.cy);
      }
    }
    const guides = [];
    if (best.x) guides.push({ axis: "x", value: best.x.pos });
    if (best.y) guides.push({ axis: "y", value: best.y.pos });
    return { dx: best.x ? best.x.delta : 0, dy: best.y ? best.y.delta : 0, guides };
  }

  function snapDelta(base, snap) {
    return {
      dx: (base ? base.dx : 0) + (snap && snap.dx ? snap.dx : 0),
      dy: (base ? base.dy : 0) + (snap && snap.dy ? snap.dy : 0),
    };
  }

  function guidesToPixels(guides, canvas) {
    const out = [];
    const w = canvas ? canvas.width : 0;
    const h = canvas ? canvas.height : 0;
    for (const g of guides || []) {
      if (!g || (g.axis !== "x" && g.axis !== "y")) continue;
      if (g.axis === "x") out.push({ axis: "x", value: g.value, from: 0, to: h });
      else out.push({ axis: "y", value: g.value, from: 0, to: w });
    }
    return out;
  }

  return { computeSnap, snapDelta, guidesToPixels };
});

// ------------------------- DOM overlay 层 -------------------------
(function () {
  if (typeof window === "undefined") return;
  const layers = new WeakMap();

  function ensureLayer(container) {
    if (!container) return null;
    let layer = layers.get(container);
    if (layer && layer.isConnected) return layer;
    layer = document.createElement("div");
    layer.className = "snap-guide-layer";
    layer.style.cssText = "position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;z-index:5;";
    container.appendChild(layer);
    layers.set(container, layer);
    return layer;
  }

  function clearGuides(container) {
    const layer = container && layers.get(container);
    if (layer) layer.innerHTML = "";
  }

  /**
   * 画吸附参考线。guides: [{ axis: "x"|"y", value }]（container 的本地坐标，
   * 调用方传 #world 时即世界坐标，随视口变换）。
   */
  function showGuides(container, guides) {
    const layer = ensureLayer(container);
    if (!layer) return;
    layer.innerHTML = "";
    const color = "var(--accent, #4a9eff)";
    for (const g of guides || []) {
      if (!g || (g.axis !== "x" && g.axis !== "y")) continue;
      const line = document.createElement("div");
      line.className = "snap-guide-line";
      if (g.axis === "x") {
        line.style.cssText = "position:absolute;top:0;bottom:0;width:1px;background:" + color + ";left:" + (g.value - 0.5) + "px;";
      } else {
        line.style.cssText = "position:absolute;left:0;right:0;height:1px;background:" + color + ";top:" + (g.value - 0.5) + "px;";
      }
      layer.appendChild(line);
    }
  }

  window.FireflySnapUI = { showGuides, clearGuides };
})();
