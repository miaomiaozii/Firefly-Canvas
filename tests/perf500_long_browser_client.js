(async () => {
  const result = { pass: false, assertions: [], errors: [], timings: {} };
  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = String(detail);
    result.assertions.push(item);
    if (!item.pass) result.errors.push(item);
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(label, predicate, timeout = 120000) {
    const deadline = Date.now() + timeout;
    let last = '';
    while (Date.now() < deadline) {
      try {
        const value = await predicate();
        if (value) return value;
        last = 'predicate false';
      } catch (error) {
        last = error?.message || String(error);
      }
      await sleep(100);
    }
    throw new Error(`${label} timeout: ${last}`);
  }
  function dragNode(element, dx, dy) {
    const rect = element.getBoundingClientRect();
    const x = rect.left + Math.min(48, rect.width / 2);
    const y = rect.top + Math.min(26, rect.height / 2);
    element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, buttons: 1, clientX: x, clientY: y }));
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, button: 0, buttons: 1, clientX: x + dx, clientY: y + dy }));
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0, clientX: x + dx, clientY: y + dy }));
  }
  async function run() {
    const phase = sessionStorage.getItem('fireflyPerf500Phase') || 'initial';
    const started = performance.now();
    if (phase === 'reload') {
      try {
        const initial = JSON.parse(sessionStorage.getItem('fireflyPerf500Initial') || '{}');
        result.assertions.push(...(initial.assertions || []));
        result.errors.push(...(initial.errors || []));
        result.timings = { ...(initial.timings || {}) };
      } catch (error) {
        result.errors.push({ name: 'restore initial stress result', pass: false, detail: String(error) });
      }
    }
    await waitFor('canvas title', () => document.getElementById('smartTitle')?.textContent?.trim(), 60000);
    const prompt = await waitFor('prompt node', () => document.querySelector('.prompt-smart-node[data-id="perf500-prompt"]'), 60000);
    const nodes = document.querySelectorAll('.image-node[data-id^="perf500-image-"]');
    check(`${phase} renders 500 image nodes`, nodes.length === 500, nodes.length);
    if (phase === 'initial') {
      const loadStart = performance.now();
      let loaded = 0;
      await waitFor('450 images loaded', () => {
        loaded = [...document.querySelectorAll('.image-node[data-id^="perf500-image-"] .node-img')]
          .filter(image => image.complete && image.naturalWidth > 0).length;
        return loaded >= 450;
      }, 180000);
      result.timings.imageLoadMs = Math.round(performance.now() - loadStart);
      check('450 real images loaded', loaded >= 450, loaded);
      const dirtyStart = performance.now();
      dragNode(prompt, 120, 80);
      await waitFor('dirty state', () => document.querySelector('#projectSaveState')?.dataset?.state === 'dirty', 10000);
      result.timings.dragToDirtyMs = Math.round(performance.now() - dirtyStart);
      check('drag enters dirty state', true);
      const saveStart = performance.now();
      await waitFor('save settles', () => document.querySelector('#projectSaveState')?.dataset?.state === 'saved', 180000);
      result.timings.saveSettleMs = Math.round(performance.now() - saveStart);
      check('save settles after drag', true);
      result.timings.initialTotalMs = Math.round(performance.now() - started);
      sessionStorage.setItem('fireflyPerf500Initial', JSON.stringify({
        assertions: result.assertions,
        errors: result.errors,
        timings: result.timings,
      }));
      sessionStorage.setItem('fireflyPerf500Phase', 'reload');
      location.reload();
      return;
    }
    const ids = [...document.querySelectorAll('.image-node[data-id^="perf500-image-"]')].map(node => node.dataset.id);
    check('reload has no duplicate node ids', new Set(ids).size === 500, new Set(ids).size);
    check('reload restored prompt text', document.querySelector('.prompt-smart-node[data-id="perf500-prompt"] textarea')?.value === '500 node stress prompt');
    result.timings.totalMs = Math.round(performance.now() - started);
    result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
    await fetch('/__conversation_browser_results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    });
  }
  try {
    await run();
  } catch (error) {
    result.errors.push({ name: 'perf500 run', pass: false, detail: error?.stack || String(error) });
    result.pass = false;
    await fetch('/__conversation_browser_results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    }).catch(() => {});
  }
})();
