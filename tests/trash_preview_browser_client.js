/* In-page driver for tests/trash_preview_browser.py (issues #207 / #208).
 *
 * Installed by the Python runner via CDP Runtime.evaluate (template
 * placeholders __PROJECT_ID__ / __ASSET_ID__ / __ORIGINAL_B64__ are
 * substituted server-side). Exposes
 * window.__trashPreviewDriver with async stages; the Python runner drives
 * the stages one by one and screenshots between them, so every screenshot
 * is taken at a deterministic page state.
 *
 * Automated (asserted in the runner): which panel is open, image src
 * (view=original), byte-identity of the served media against the seeded
 * original/preview files, X-Canvas-Media-View header, restore-button
 * lifecycle, download-output bytes + filename.
 * Human review (screenshots): that the open panel visually IS the 生成详情
 * panel (title, meta/prompt rows, actions) and that the original image
 * (magenta "ORIGINAL" artwork) is what is displayed.
 */
(function () {
  "use strict";

  const PID = "__PROJECT_ID__";
  const ASSET = "__ASSET_ID__";
  const ORIGINAL_B64 = "__ORIGINAL_B64__";

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function waitFor(fn, timeout, label) {
    const deadline = performance.now() + (timeout || 20000);
    while (performance.now() < deadline) {
      try {
        if (await fn()) return true;
      } catch (_) {}
      await sleep(100);
    }
    throw new Error("timeout waiting for " + label);
  }

  function b64(arrayBuffer) {
    const bytes = new Uint8Array(arrayBuffer);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 8192) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    }
    return btoa(bin);
  }

  const driver = {
    /* Wait until the canvas node and its image are rendered. */
    async s1_boot() {
      await waitFor(() => !!document.querySelector('#world .image-node[data-id="trash-gen-node"]'), 30000, "canvas node rendered");
      const img = () => document.querySelector('#world .image-node[data-id="trash-gen-node"] .node-img');
      await waitFor(() => !!(img() && img().naturalWidth > 0), 20000, "node image loaded");
      return { node: true, imgNatural: img().naturalWidth + "x" + img().naturalHeight };
    },

    /* Delete the generated original through the app's own 项目范围删原图 action
     * (smart-canvas.js deleteProjectOriginalWide) — the same function the UI
     * delete dialog calls. The orphaned media moves to the trash (回收区). */
    async s2_delete() {
      await deleteProjectOriginalWide(ASSET, null);
      const btn = () => document.getElementById("smartTrashOpenBtn");
      await waitFor(() => !!(btn() && !btn().hidden), 20000, "trash button visible after delete");
      const remaining = document.querySelectorAll('#world .image-node[data-id="trash-gen-node"] .node-img').length;
      return { trashBtnVisible: true, nodeImagesLeft: remaining };
    },

    /* Open the trash panel and wait for the rendered item. */
    async s3_open_panel() {
      document.getElementById("smartTrashOpenBtn").click();
      const itemSelector = `.smart-trash-item[data-trash-asset="${ASSET}"]`;
      await waitFor(() => !!document.querySelector(itemSelector), 20000, "trash item rendered");
      const item = document.querySelector(itemSelector);
      const thumb = item ? item.querySelector("img.smart-trash-thumb") : null;
      return {
        itemCount: document.querySelectorAll(".smart-trash-item").length,
        thumbSrc: thumb ? String(thumb.getAttribute("src") || "") : null,
        thumbLoaded: !!(thumb && thumb.naturalWidth > 0),
      };
    },

    /* Click the item thumbnail → the preview must open in the unified
     * 生成详情 panel (#smartHistoryLightbox), not the custom #smartTrashPreview. */
    async s4_preview() {
      const wrap = document.querySelector(`.smart-trash-thumb-wrap[data-trash-preview="${ASSET}"]`);
      if (!wrap) throw new Error("trash thumbnail wrapper not found");
      wrap.click();
      const box = document.getElementById("smartHistoryLightbox");
      await waitFor(() => !!(box && box.classList.contains("open")), 20000, "unified detail panel open");
      const img = box.querySelector("img.smart-log-detail-image");
      await waitFor(() => !!(img && img.naturalWidth > 0), 20000, "detail image loaded");
      const restore = box.querySelector(".smart-trash-detail-restore");
      return {
        lightboxOpen: true,
        customPanelAbsent: !document.getElementById("smartTrashPreview"),
        imgSrc: String(img.getAttribute("src") || ""),
        imgNatural: img.naturalWidth + "x" + img.naturalHeight,
        restoreBtnPresent: !!restore,
        restoreLabel: restore ? String(restore.textContent || "").trim() : null,
        panelTitle: String((box.querySelector("#smartHistoryDetailTitle") || {}).textContent || "").trim(),
      };
    },

    /* Byte-level proof that the preview endpoint can serve the original. */
    async s5_bytes() {
      const base = `/api/project-library/${encodeURIComponent(PID)}/trash/media/${encodeURIComponent(ASSET)}/preview`;
      async function probe(view) {
        const res = await fetch(base + "?view=" + view, { cache: "no-store" });
        const buf = await res.arrayBuffer();
        const encoded = b64(buf);
        const out = {
          view: view,
          status: res.status,
          header: res.headers.get("X-Canvas-Media-View"),
          contentType: res.headers.get("Content-Type"),
          // preview 文件由应用按 {asset_id}-{width}.webp 生成，字节与磁盘文件比对由 Python 端做；
          // 这里只确认不是原件字节。
          bytesMatchOriginal: encoded === ORIGINAL_B64,
        };
        if (view === "preview") out.bytesB64 = encoded; // 供 Python 端与磁盘文件比对
        return out;
      }
      return { original: await probe("original"), preview: await probe("preview") };
    },

    /* Close the panel and verify the injected 恢复 button is cleaned up. */
    async s6_close() {
      const closeBtn = document.querySelector("#smartHistoryLightbox .smart-log-lightbox-close");
      if (closeBtn) closeBtn.click();
      await waitFor(() => !document.querySelector("#smartHistoryLightbox.open"), 10000, "panel closed");
      const box = document.getElementById("smartHistoryLightbox");
      const img = box ? box.querySelector("img.smart-log-detail-image") : null;
      return {
        closed: true,
        restoreBtnCleaned: !box || !box.querySelector(".smart-trash-detail-restore"),
        detailImageCleared: !img || !img.getAttribute("src"),
      };
    },
  };

  window.__trashPreviewDriver = driver;
  return { installed: true, asset: ASSET, pid: PID };
})()
