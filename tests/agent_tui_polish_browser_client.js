(async function () {
  const query = new URLSearchParams(location.search);
  window.__agentTuiPolishPid = query.get('project') || '';
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
        body: JSON.stringify({ pass: errors.length === 0, assertions: results, errors, result_id: 'agent-tui-polish-' + runId }),
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

    // Wait for agent node to render
    const agentNode = await waitFor('agent node rendered', () => {
      return document.querySelector('.agent-smart-node[data-id="tui-agent"]') ? true : false;
    }, 10000);
    if (!agentNode) throw new Error('agent node not rendered');
    record('agent node rendered', true);

    // Verify status light on node card (agent-card-status)
    const statusEl = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-card-status');
    record('node card has status light', Boolean(statusEl), statusEl ? 'class=' + statusEl.className : 'missing');

    // Verify textarea input (not input[type=text])
    const inputEl = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-tui-input');
    const isTextarea = inputEl && inputEl.tagName === 'TEXTAREA';
    record('TUI input is textarea', isTextarea, inputEl ? 'tag=' + inputEl.tagName : 'missing');

    // Verify TUI body is rendered
    const tuiBody = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-tui-body');
    record('TUI body rendered', Boolean(tuiBody), tuiBody ? 'present' : 'missing');

    // Verify markdown rendering: send a message with markdown
    // First, set up the agent TUI state directly to test markdown rendering
    const mdTest = evalFn(`(function() {
      if (typeof getAgentTuiState !== 'function') return 'no getAgentTuiState';
      const state = getAgentTuiState('tui-agent');
      if (!state) return 'no state';
      state.messages.push({ role: 'assistant', text: '**Bold text** and *italic* and \`inline code\`' });
      if (typeof renderAgentNode === 'function') renderAgentNode('tui-agent');
      return 'ok';
    })()`);
    record('markdown test setup', mdTest === 'ok', mdTest);

    // Check for rendered markdown (strong, em, code tags)
    await sleep(500);
    const strongEl = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-tui-msg-assistant strong');
    record('markdown bold renders as <strong>', Boolean(strongEl), strongEl ? 'text=' + strongEl.textContent : 'missing');
    const emEl = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-tui-msg-assistant em');
    record('markdown italic renders as <em>', Boolean(emEl), emEl ? 'text=' + emEl.textContent : 'missing');
    const codeEl = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-tui-msg-assistant .agent-md-inline-code');
    record('markdown inline code renders', Boolean(codeEl), codeEl ? 'text=' + codeEl.textContent : 'missing');

    // Verify link protocol sanitization: safe links keep href, javascript: links are neutralized
    const linkTest = evalFn(`(function() {
      if (typeof getAgentTuiState !== 'function') return 'no getAgentTuiState';
      const state = getAgentTuiState('tui-agent');
      if (!state) return 'no state';
      state.messages.push({ role: 'assistant', text: '[safe](https://example.com/a) and [evil](javascript:alert(1))' });
      if (typeof renderAgentNode === 'function') renderAgentNode('tui-agent');
      return 'ok';
    })()`);
    record('markdown link test setup', linkTest === 'ok', linkTest);
    await sleep(500);
    const linkEls = document.querySelectorAll('.agent-smart-node[data-id="tui-agent"] .agent-tui-msg-assistant a');
    let safeOk = false, evilOk = false;
    linkEls.forEach((a) => {
      const href = a.getAttribute('href') || '';
      if (a.textContent === 'safe') safeOk = href === 'https://example.com/a';
      if (a.textContent === 'evil') evilOk = href === '#' || !/^javascript:/i.test(href);
    });
    record('markdown safe link keeps https href', safeOk, safeOk ? 'href=https://example.com/a' : 'missing/wrong');
    record('markdown javascript: link neutralized', evilOk, evilOk ? 'href neutralized' : 'DANGEROUS href present');

    // Verify collapsible tool activity rows
    const toolTest = evalFn(`(function() {
      if (typeof getAgentTuiState !== 'function') return 'no getAgentTuiState';
      const state = getAgentTuiState('tui-agent');
      if (!state) return 'no state';
      if (!state.toolActivities) state.toolActivities = new Map();
      state.toolActivities.set('test-tool-1', { name: 'canvas.list_nodes', status: 'done', args: { type: 'all' }, result: '3 nodes', expanded: false });
      if (typeof renderAgentNode === 'function') renderAgentNode('tui-agent');
      return 'ok';
    })()`);
    record('tool activity test setup', toolTest === 'ok', toolTest);

    await sleep(500);
    const toolRow = document.querySelector('.agent-smart-node[data-id="tui-agent"] [data-tool-call-id="test-tool-1"]');
    record('tool activity row rendered', Boolean(toolRow), toolRow ? 'present' : 'missing');

    // Verify collapsed state (details hidden)
    const detailsEl = toolRow ? toolRow.querySelector('.agent-tui-tool-details') : null;
    const isCollapsed = detailsEl ? window.getComputedStyle(detailsEl).display === 'none' : false;
    record('tool row collapsed by default', isCollapsed, detailsEl ? 'display=' + window.getComputedStyle(detailsEl).display : 'missing');

    // Click to expand
    if (toolRow) {
      toolRow.click();
      await sleep(500);
    }
    const toolRowAfter = document.querySelector('.agent-smart-node[data-id="tui-agent"] [data-tool-call-id="test-tool-1"]');
    const detailsAfter = toolRowAfter ? toolRowAfter.querySelector('.agent-tui-tool-details') : null;
    const isExpanded = detailsAfter ? window.getComputedStyle(detailsAfter).display !== 'none' : false;
    record('tool row expands on click', isExpanded, detailsAfter ? 'display=' + window.getComputedStyle(detailsAfter).display : 'missing');

    // Verify last line preview on node card
    const previewEl = document.querySelector('.agent-smart-node[data-id="tui-agent"] .agent-card-preview');
    record('node card has last line preview', Boolean(previewEl), previewEl ? 'text=' + previewEl.textContent : 'missing');

  } catch (e) {
    errors.push(String(e));
  } finally {
    await post();
  }
})();
