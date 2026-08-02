(function () {
  const results = [];
  let cvs = null;

  function record(name, ok, detail) {
    results.push({ name, ok, detail });
  }

  function evalFn(fn) {
    return eval('(' + fn + ')()');
  }

  async function waitFor(desc, fn, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 15000);
    let last;
    while (Date.now() < deadline) {
      try {
        last = await fn();
        if (last) return last;
      } catch (e) {
        last = String(e);
      }
      await new Promise(r => setTimeout(r, 250));
    }
    return last;
  }

  function assetPanelOpen() {
    const panel = document.getElementById('assetPanel');
    return !!(panel && panel.classList.contains('open'));
  }

  function assetSelectOptions() {
    const sel = document.getElementById('assetLibrarySelect');
    if (!sel) return [];
    return Array.from(sel.options).map(o => o.textContent.trim());
  }

  function categoryOptions() {
    const sel = document.getElementById('assetCategorySelect');
    if (!sel) return [];
    return Array.from(sel.options).map(o => o.textContent.trim());
  }

  function gridItems() {
    return Array.from(document.querySelectorAll('#assetGrid .asset-item'));
  }

  function clickAssetToggle() {
    const el = document.querySelector('#assetToggle');
    if (!el) return false;
    el.click();
    return true;
  }

  function selectLibrary(id) {
    const sel = document.getElementById('assetLibrarySelect');
    if (!sel) return false;
    sel.value = id;
    sel.dispatchEvent(new Event('change'));
    return true;
  }

  function selectCategory(id) {
    const sel = document.getElementById('assetCategorySelect');
    if (!sel) return false;
    sel.value = id;
    sel.dispatchEvent(new Event('change'));
    return true;
  }

  async function runInitial() {
    await waitFor('canvas', () => { try { const c = eval('canvas'); return !!(c && c.id); } catch (e) { return false; } });
    cvs = eval('canvas');
    record('canvas loaded', !!cvs && !!cvs.id, 'canvas present');

    const toggleWorked = clickAssetToggle();
    await waitFor('panel', () => assetPanelOpen());
    record('asset panel opens', toggleWorked && assetPanelOpen(), assetPanelOpen() ? 'open' : 'closed');

    await waitFor('library select', () => assetSelectOptions().length > 0);
    const libs = assetSelectOptions();
    record('library selector rendered', libs.length >= 1, libs.join('|'));

    const cats = categoryOptions();
    record('categories rendered', cats.length >= 4, cats.join('|'));

    const items = gridItems();
    record('project media grid populated', items.length >= 1, 'count=' + items.length);

    const first = items[0];
    record('asset card metadata', !!first && !!first.dataset.assetId, first ? (first.dataset.assetId + ' ' + first.dataset.name + ' ' + first.dataset.kind) : 'none');

    const videos = cats.filter(c => c.indexOf('视频') >= 0 || c.indexOf('Videos') >= 0 || c.indexOf('video') >= 0).length;
    record('media-kind categories present', videos >= 1, 'video cats=' + videos);

    const dropZone = document.getElementById('assetDropZone');
    record('drop zone visible', !!dropZone && getComputedStyle(dropZone).display !== 'none', dropZone ? getComputedStyle(dropZone).display : 'missing');

    const renameBtn = document.getElementById('assetRenameCategoryBtn');
    const addBtn = document.getElementById('assetAddCategoryBtn');
    record('project mode disables category edit', !!(renameBtn && renameBtn.disabled) && !!(addBtn && addBtn.disabled), 'rename=' + (renameBtn ? renameBtn.disabled : '?') + ' add=' + (addBtn ? addBtn.disabled : '?'));

    const before = gridItems().length;
    if (videos >= 1) {
      const videoCat = cats.findIndex(c => c.indexOf('视频') >= 0 || c.indexOf('Videos') >= 0 || c.indexOf('video') >= 0);
      if (videoCat >= 0) {
        const sel = document.getElementById('assetCategorySelect');
        sel.selectedIndex = videoCat;
        sel.dispatchEvent(new Event('change'));
        await waitFor('video grid', () => gridItems().length >= 1);
        record('switching category updates grid', gridItems().length >= 1, 'video items=' + gridItems().length);
      }
    }
    record('grid populated in both categories', before >= 1, 'all=' + before);

    clickAssetToggle();
    await waitFor('panel closed', () => !assetPanelOpen(), 5000);
    record('asset panel closes', !assetPanelOpen(), 'closed');

    const ok = results.every(r => r.ok);
    const payload = {
      pass: ok,
      assertions: results.map(r => ({ name: r.name, pass: r.ok, detail: r.detail })),
      errors: results.filter(r => !r.ok).map(r => r.name + ': ' + r.detail),
      result_id: 'asset-library',
      done: true,
    };
    try {
      await fetch('/__conversation_browser_results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      document.title = 'POST-FAIL:' + String(e);
    }
  }

  runInitial();
})();

