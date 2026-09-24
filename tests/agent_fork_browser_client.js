(async function () {
  const query = new URLSearchParams(location.search);
  window.__agentForkPid = query.get('project') || '';
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
        body: JSON.stringify({ pass: errors.length === 0, assertions: results, errors, result_id: 'agent-fork-' + runId }),
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

  try {
    // Wait for canvas to load
    const canvasReady = await waitFor('canvas ready', () => {
      return evalFn('(typeof canvas !== "undefined" && canvas && Array.isArray(canvas.nodes) && canvas.nodes.length > 0) ? canvas.nodes.length : 0');
    }, 20000);
    record('canvas loaded', Boolean(canvasReady), 'nodes=' + canvasReady);

    // Wait for the agent node to render
    const agentNode = await waitFor('agent node rendered', () => {
      return document.querySelector('.agent-smart-node[data-id="fork-agent"]') ? true : false;
    }, 10000);
    if (!agentNode) throw new Error('agent node not rendered');
    record('agent node rendered', true);

    // The agent TUI card should be present (fork button is always rendered in the TUI body)
    const tuiCard = await waitFor('agent TUI card rendered', () => {
      return document.querySelector('.agent-smart-node[data-id="fork-agent"] .agent-tui-card') ? true : false;
    }, 10000);
    record('agent TUI card rendered', Boolean(tuiCard));

    // Verify the fork button appears
    const forkBtn = document.querySelector('.agent-smart-node[data-id="fork-agent"] .agent-tui-btn-fork');
    record('fork button rendered', Boolean(forkBtn), forkBtn ? 'title=' + forkBtn.title : 'missing');

    // Verify the branch select appears (node is seeded with branches)
    const branchSelect = document.querySelector('.agent-smart-node[data-id="fork-agent"] .agent-tui-branch-select');
    record('branch select rendered', Boolean(branchSelect), branchSelect ? 'options=' + branchSelect.options.length : 'missing');
    if (branchSelect) {
      const optionValues = Array.from(branchSelect.options).map((o) => o.value);
      record('branch select has original branch', optionValues.includes('branch-original'), 'options=' + optionValues.join(','));
      record('branch select has forked branch', optionValues.some((v) => v.startsWith('branch-') && v !== 'branch-original'), 'options=' + optionValues.join(','));
    }

    // Verify the fork button has a click handler attached (dispatch a click, should not throw)
    if (forkBtn) {
      let clickOk = true;
      let clickErr = '';
      try {
        // Prevent the actual fork picker (prompt) from blocking; just verify the handler is bound.
        // We stub showForkPicker to avoid the browser prompt dialog.
        const origShowForkPicker = window.showForkPicker;
        window.__forkPickerCalled = false;
        window.showForkPicker = function () { window.__forkPickerCalled = true; };
        forkBtn.click();
        await sleep(50);
        record('fork button click handler bound', window.__forkPickerCalled === true, 'called=' + window.__forkPickerCalled);
        window.showForkPicker = origShowForkPicker;
      } catch (e) {
        clickOk = false;
        clickErr = String(e);
      }
      record('fork button click did not throw', clickOk, clickErr);
    }

    // Verify the branch select has a change handler attached
    if (branchSelect) {
      let changeOk = true;
      let changeErr = '';
      try {
        const origSwitchAgentBranch = window.switchAgentBranch;
        window.__branchSwitchCalled = null;
        window.switchAgentBranch = function (nodeId, projectId, branchId) { window.__branchSwitchCalled = branchId; };
        // Change to the original branch
        branchSelect.value = 'branch-original';
        branchSelect.dispatchEvent(new Event('change', { bubbles: true }));
        await sleep(50);
        record('branch select change handler bound', window.__branchSwitchCalled === 'branch-original', 'called=' + window.__branchSwitchCalled);
        window.switchAgentBranch = origSwitchAgentBranch;
      } catch (e) {
        changeOk = false;
        changeErr = String(e);
      }
      record('branch select change did not throw', changeOk, changeErr);
    }

    // Verify the model select and autonomy select still render (regression: #140/#141 not broken)
    const modelSelect = document.querySelector('.agent-smart-node[data-id="fork-agent"] .agent-tui-model-select');
    record('model select still renders', Boolean(modelSelect));
    const autonomySelect = document.querySelector('.agent-smart-node[data-id="fork-agent"] .agent-tui-autonomy-select');
    record('autonomy select still renders', Boolean(autonomySelect));

  } catch (e) {
    errors.push(String(e && e.stack || e));
  } finally {
    await post();
  }
})();
