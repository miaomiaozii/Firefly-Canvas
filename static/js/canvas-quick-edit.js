// canvas-quick-edit.js — 节点就地快速重命名 + 右键操作卡可见性（issue #253 第 3/2 项）
//
// 纯函数核心（window.FireflyQuickEdit；module.exports 供 node 端单测）：
//   - operationCardVisibility(context) -> { rename, tidy }
//     context: { nodeId, nodeCount }。右键菜单（createMenu）新增操作卡的过滤：
//       rename 卡：右键落在节点上（nodeId 非空）才可见；
//       tidy 卡：选中节点 >= 2 才可见（arrangeSelectedSmartNodes 少于 2 个是 no-op）。
//   - quickEditRenameTarget(node) -> { kind: "image"|"title" } | null
//     节点的可重命名目标：图片节点改首图名（显示名徽章），其余节点类型改 node.title。
//
// DOM 层（同一 window.FireflyQuickEdit 对象上挂 openQuickEdit）：
//   - openQuickEdit({ anchor, getValue, onCommit, placeholder }) -> { done, cancel }
//     在 anchor（名称徽章/.node-title）原位盖一个 input：Enter/失焦提交（trim 后非空），
//     Escape 取消；position:fixed 用 anchor 的屏幕坐标定位，不依赖画布容器坐标系。
//     onCommit(value) 由调用方接线（写回 node.title / images[0].name + render + scheduleSave）。
// 本模块不引用 smart-canvas.js 的任何顶层名（反向引用棘轮 0）。

(function (global, factory) {
  if (typeof module !== "undefined" && module.exports) module.exports = factory();
  if (typeof window !== "undefined") Object.assign(global.FireflyQuickEdit = global.FireflyQuickEdit || {}, factory());
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function operationCardVisibility(context) {
    const c = context || {};
    return {
      rename: Boolean(c.nodeId),
      tidy: Number(c.nodeCount || 0) >= 2,
    };
  }

  function quickEditRenameTarget(node, imageType) {
    if (!node || typeof node !== "object") return null;
    const isImage = imageType ? node.type === imageType : (node.type === "image" || node.type === "smart-image" || node.type === undefined || node.type === null);
    if (isImage) {
      const imgs = Array.isArray(node.images) ? node.images : [];
      if (!imgs.length) return null;
      return { kind: "image" };
    }
    return { kind: "title" };
  }

  return { operationCardVisibility, quickEditRenameTarget };
});

// ------------------------- DOM 就地编辑层 -------------------------
(function () {
  if (typeof window === "undefined") return;

  function openQuickEdit(options) {
    const opts = options || {};
    const anchor = opts.anchor;
    if (!anchor || typeof anchor.getBoundingClientRect !== "function") return { done: Promise.resolve(), cancel() {} };

    let settled = false;
    let finish = null;
    const done = new Promise(resolve => { finish = resolve; });

    const rect = anchor.getBoundingClientRect();
    const input = document.createElement("input");
    input.type = "text";
    input.className = "quick-edit-input";
    input.value = typeof opts.getValue === "function" ? String(opts.getValue() || "") : String(opts.value || "");
    if (opts.placeholder) input.placeholder = opts.placeholder;
    input.style.cssText =
      "position:fixed;z-index:1200;background:var(--bg-secondary,#1e1e2e);" +
      "border:1px solid var(--accent,#4a9eff);border-radius:4px;padding:2px 6px;" +
      "color:var(--text,#e5e5e5);font-size:12px;outline:none;" +
      "left:" + rect.left + "px;top:" + rect.top + "px;width:" + Math.max(140, rect.width + 40) + "px;";

    function cleanup() {
      if (input.parentNode) input.parentNode.removeChild(input);
      anchor.style.visibility = "";
    }
    function commit(save) {
      if (settled) return;
      settled = true;
      const value = input.value.trim();
      cleanup();
      if (save && value && typeof opts.onCommit === "function") {
        try { opts.onCommit(value); } catch (err) { console.warn("[quick-edit] onCommit failed", err); }
      }
      finish();
    }

    // 编辑期间隐藏原名称，避免双份文字。
    const prevVisibility = anchor.style.visibility;
    anchor.style.visibility = "hidden";
    document.body.appendChild(input);
    input.addEventListener("keydown", event => {
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); commit(true); }
      else if (event.key === "Escape") { event.preventDefault(); commit(false); }
    });
    input.addEventListener("blur", () => commit(true));
    input.focus();
    input.select();
    return {
      done,
      cancel() { commit(false); },
    };
  }

  const api = window.FireflyQuickEdit || (window.FireflyQuickEdit = {});
  api.openQuickEdit = openQuickEdit;
})();
