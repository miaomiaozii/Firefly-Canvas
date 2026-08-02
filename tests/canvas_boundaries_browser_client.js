// 画布边界浏览器测试：超长文本、空文本、undo 压力、保存收敛。
(() => {
  const RESULTS_PATH = '/__conversation_browser_results';
  const assertions = [];
  let total = 0;

  function record(name, ok, detail) {
    total += 1;
    assertions.push({ name, ok, detail: detail || '' });
  }

  function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

  function evalFn(code) {
    try {
      return (0, eval)(code);
    } catch (err) {
      return { __error: String(err && err.message || err) };
    }
  }

  function canvasState() { return evalFn('canvas'); }
  function nodes() { return evalFn('nodes'); }

  async function saveAndWait() {
    await sleep(400);
    const ok = await evalFn('(async () => { try { const r = await saveCanvas(); return !!r; } catch (e) { return false; } })()');
    let settled = false;
    for (let i = 0; i < 30; i++) {
      await sleep(300);
      const st = evalFn('saveState');
      if (st === 'saved') { settled = true; break; }
    }
    return { ok, settled };
  }

  async function run() {
    try {
      await sleep(1200);

      const first = canvasState();
      const n = nodes();
      const big = (n || []).find((nd) => nd.id === 'seed-big');
      const empty = (n || []).find((nd) => nd.id === 'seed-empty');

      record('canvas loaded', !!first, 'canvas present');
      record('big node rendered', !!big, `id=${big && big.id}`);
      record('empty node rendered', !!empty, `id=${empty && empty.id}`);

      const bigTextLen = (big && big.text || '').length;
      record('big text length persisted', bigTextLen > 2000, `len=${bigTextLen}`);

      const bigEl = document.querySelector('.image-node[data-id="seed-big"]');
      record('big node element present', !!bigEl, bigEl ? bigEl.tagName : 'missing');
      if (bigEl) {
        const textEl = bigEl.querySelector('.prompt-llm-instruction, .prompt-node-text, textarea, [contenteditable="true"]');
        record('big node text area present', !!textEl, textEl ? textEl.tagName + '.' + (textEl.className || '') : 'missing');
        if (textEl) {
          const visible = textEl.value !== undefined ? textEl.value.length : (textEl.textContent || '').length;
          record('big text visible in editor', visible > 2000, `visible=${visible}`);
        }
      }

      const edits = await evalFn('(async () => { try { const el = document.querySelector(\'.image-node[data-id="seed-big"] .prompt-llm-instruction, .image-node[data-id="seed-big"] .prompt-node-text, .image-node[data-id="seed-big"] textarea\'); if (!el) return \'no-el\'; if (el.value !== undefined) { el.value = el.value + \'\\n追加边界文本段落。\'; } else { el.textContent = el.textContent + \'\\n追加边界文本段落。\'; } el.dispatchEvent(new Event(\'input\', { bubbles: true })); await new Promise((r) => setTimeout(r, 600)); return \'edited\'; } catch (e) { return String(e); } })()');
      record('big text edit applied', edits === 'edited', String(edits));

      const undoInfo = await evalFn('(async () => { try { let ok = true; for (let i = 0; i < 60; i++) { if (!undoStack || !performUndo) { ok = false; break; } performUndo(); await new Promise((r) => setTimeout(r, 20)); } return ok; } catch (e) { return String(e); } })()');
      record('undo x60 runs', undoInfo === true, String(undoInfo));
      const redoInfo = await evalFn('(async () => { try { let ok = true; for (let i = 0; i < 60; i++) { if (!redoStack || !performRedo) { ok = false; break; } performRedo(); await new Promise((r) => setTimeout(r, 20)); } return ok; } catch (e) { return String(e); } })()');
      record('redo x60 runs', redoInfo === true, String(redoInfo));
      record('undo/redo stacks bounded', (await evalFn('undoStack.length + redoStack.length')) < 5000, `u=${await evalFn('undoStack.length')} r=${await evalFn('redoStack.length')}`);

      const saved = await evalFn('(async () => { try { const r = await saveCoreCanvasIncrementally(canvasForStorage()); return JSON.stringify(r); } catch (e) { return "ERR:" + String(e); } })()');
      record('save settles after stress', !String(saved).startsWith('ERR') && String(saved).includes('"saved":true'), String(saved));

      const afterNodes = nodes();
      record('nodes alive after stress', (afterNodes || []).length >= 3, `count=${(afterNodes || []).length}`);

      sessionStorage.setItem('boundariesReload', '1');
      const payload = { assertions, total, ok: assertions.every((a) => a.ok), session: 'before-reload' };
      await fetch(RESULTS_PATH, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }).catch((e) => console.error('results post failed', e));
      location.reload();
    } catch (err) {
      const payload = { assertions, total, ok: false, fatal: String(err && err.stack || err) };
      fetch(RESULTS_PATH, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }).catch(() => {});
    }
  }

  async function runAfterReload() {
    try {
      await sleep(1500);
      const n = nodes();
      const big = (n || []).find((nd) => nd.id === 'seed-big');
      record('after reload big node', !!big, `len=${big && (big.text || '').length}`);
      record('after reload empty node', !!(n || []).find((nd) => nd.id === 'seed-empty'), 'seed-empty present');
      record('after reload big text intact', !!big && (big.text || '').length > 2000, `len=${big && (big.text || '').length}`);

      const saved = await evalFn('(async () => { try { return !!await saveCanvas(); } catch (e) { return false; } })()');
      record('after reload save works', saved, '');

      const payload = { assertions, total, ok: assertions.every((a) => a.ok), session: 'after-reload' };
      await fetch(RESULTS_PATH, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }).catch((e) => console.error('results post failed', e));
    } catch (err) {
      const payload = { assertions, total, ok: false, fatal: String(err && err.stack || err) };
      fetch(RESULTS_PATH, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }).catch(() => {});
    }
  }

  if (sessionStorage.getItem('boundariesReload') === '1') {
    sessionStorage.removeItem('boundariesReload');
    runAfterReload();
  } else {
    run();
  }
})();
