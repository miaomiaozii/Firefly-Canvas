(async function () {
  const query = new URLSearchParams(location.search);
  window.__agentNodeSettingsPid = query.get('project') || '';
  const runId = Date.now() + Math.random().toString(36).slice(2);
  const results = [];
  const errors = [];

  function record(name, ok, detail) {
    results.push({ name, ok: Boolean(ok), detail: detail == null ? '' : String(detail).slice(0, 200) });
  }

  async function post() {
    try {
      await fetch('/__conversation_browser_results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pass: errors.length === 0, assertions: results, errors, result_id: 'agent-node-settings-' + runId }),
      });
    } catch (e) {
      errors.push('post failed: ' + String(e));
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitFor(name, pred, timeout) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const value = pred();
      if (value) return value;
      await sleep(250);
    }
    return null;
  }

  function evalFn(code) {
    try {
      return (0, eval)(code);
    } catch (e) {
      return 'ERR:' + String(e);
    }
  }

  const NODE_ID = 'settings-agent';
  // Two-phase reload test: phase 1 sets autonomy + persists + reloads;
  // phase 2 (after reload) verifies the persisted value survives the refresh.
  const isReload = localStorage.getItem('settings_reload_test') === '1';

  try {
    if (!isReload) {
      // ---- Phase 1: first load ----
      const canvasReady = await waitFor('canvas ready', () => {
        return evalFn('(typeof canvas !== "undefined" && canvas && Array.isArray(canvas.nodes) && canvas.nodes.length > 0) ? canvas.nodes.length : 0');
      }, 20000);
      record('canvas loaded', Boolean(canvasReady), 'nodes=' + canvasReady);

      await waitFor('agent node', () => evalFn('document.querySelector("[data-id=\'' + NODE_ID + '\']")'), 10000);
      record('agent node rendered', Boolean(evalFn('document.querySelector("[data-id=\'' + NODE_ID + '\']")')));

      await waitFor('TUI card', () => evalFn('document.querySelector(".agent-tui-card")'), 10000);
      record('TUI card rendered', Boolean(evalFn('document.querySelector(".agent-tui-card")')));

      const modelSelect = evalFn('document.querySelector("[data-agent-model]")');
      record('model select rendered', Boolean(modelSelect));

      const autonomySelect = evalFn('document.querySelector("[data-agent-autonomy]")');
      record('autonomy select rendered', Boolean(autonomySelect));

      if (autonomySelect) {
        const optionCount = evalFn('document.querySelector("[data-agent-autonomy]").options.length');
        record('autonomy select has 3 options', optionCount === 3, 'got ' + optionCount);
        const defaultAutonomy = evalFn('document.querySelector("[data-agent-autonomy]").value');
        record('default autonomy is cautious', defaultAutonomy === 'cautious', 'got ' + defaultAutonomy);
      }

      if (modelSelect) {
        const modelOptionCount = evalFn('document.querySelector("[data-agent-model]").options.length');
        record('model select has options', modelOptionCount >= 1, 'got ' + modelOptionCount);
      }

      // Set autonomy to autonomous (persists to node attribute via API)
      if (autonomySelect) {
        evalFn('document.querySelector("[data-agent-autonomy]").value = "autonomous"');
        evalFn('document.querySelector("[data-agent-autonomy]").dispatchEvent(new Event("change", { bubbles: true }))');
        await sleep(1500); // allow the POST /autonomy to persist
        const afterSet = evalFn('document.querySelector("[data-agent-autonomy]").value');
        record('autonomy select updated to autonomous', afterSet === 'autonomous', 'got ' + afterSet);
      }

      // Persist a flag, then reload to verify the value survives the refresh
      localStorage.setItem('settings_reload_test', '1');
      location.reload();
      return; // page is reloading; phase 2 will run assertions + post
    }

    // ---- Phase 2: after reload ----
    await waitFor('canvas ready (reload)', () => {
      return evalFn('(typeof canvas !== "undefined" && canvas && Array.isArray(canvas.nodes) && canvas.nodes.length > 0) ? canvas.nodes.length : 0');
    }, 20000);
    record('canvas loaded (reload)', Boolean(evalFn('(typeof canvas !== "undefined" && canvas && Array.isArray(canvas.nodes)) ? canvas.nodes.length : 0')));

    await waitFor('TUI card (reload)', () => evalFn('document.querySelector(".agent-tui-card")'), 15000);
    record('TUI card rendered (reload)', Boolean(evalFn('document.querySelector(".agent-tui-card")')));

    // KEY assertion: the persisted autonomy value survives the page refresh
    const autonomyValue = evalFn('document.querySelector("[data-agent-autonomy]") ? document.querySelector("[data-agent-autonomy]").value : null');
    record('autonomy persisted after refresh', autonomyValue === 'autonomous', 'got ' + autonomyValue);

    // Model select should still render after reload
    record('model select rendered (reload)', Boolean(evalFn('document.querySelector("[data-agent-model]")')));

    // Autonomous mode: send a prompt, expect no confirm dialog
    const input = evalFn('document.querySelector("[data-agent-tui-input]")');
    if (input) {
      evalFn('document.querySelector("[data-agent-tui-input]").value = "列出所有节点"');
      evalFn('document.querySelector("[data-agent-tui-input]").dispatchEvent(new Event("input", { bubbles: true }))');
      evalFn('document.querySelector("[data-agent-tui-input]").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }))');
      await sleep(5000);
      const confirmDialog = evalFn('document.querySelector(".agent-tui-confirm")');
      record('no confirm dialog in autonomous mode', !confirmDialog);
    }
  } catch (e) {
    errors.push(String(e));
  }

  await post();
})();
