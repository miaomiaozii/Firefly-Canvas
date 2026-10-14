// #170 运行时守卫的浏览器侧：断言 Agent 节点的 WebSocket 真的进入 OPEN，并通过
// 应用自己的函数发出真实 HTTP 请求（路径由应用字面量拼出，服务端 journal 记录状态码）。
// 这里刻意不断言 DOM——DOM 断言正是 #170 里 42 条断言集体失明的原因。
(async function () {
  const query = new URLSearchParams(location.search);
  const NODE_ID = 'route-agent';
  const pid = query.get('project') || '';
  const runId = Date.now() + Math.random().toString(36).slice(2);
  const results = [];
  const errors = [];

  function record(name, ok, detail) {
    results.push({ name, ok: Boolean(ok), detail: detail == null ? '' : String(detail).slice(0, 300) });
  }

  async function post() {
    try {
      await fetch('/__conversation_browser_results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pass: errors.length === 0 && results.every((item) => item.ok), assertions: results, errors, result_id: 'agent-routes-' + runId }),
      });
    } catch (e) {
      errors.push('post failed: ' + String(e));
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitFor(pred, timeout) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const value = pred();
      if (value) return value;
      await sleep(200);
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

  function tuiState() {
    return evalFn(`getAgentTuiState(${JSON.stringify(NODE_ID)})`);
  }

  try {
    const canvasReady = await waitFor(() => {
      return evalFn('(typeof canvas !== "undefined" && canvas && Array.isArray(canvas.nodes) && canvas.nodes.length > 0) ? canvas.nodes.length : 0');
    }, 20000);
    record('canvas loaded', Boolean(canvasReady), 'nodes=' + canvasReady);

    const agentRendered = await waitFor(() => Boolean(document.querySelector('.agent-smart-node[data-id="' + NODE_ID + '"]')), 15000);
    if (!agentRendered) throw new Error('agent node not rendered');
    record('agent node rendered', true);

    // 核心断言 1：应用自己拼出的 WebSocket 必须进入 OPEN（路径错则握手被拒，永远 OPEN 不了）。
    const open = await waitFor(() => {
      const state = tuiState();
      const socket = state && state.socket;
      return socket && socket.readyState === WebSocket.OPEN ? socket : null;
    }, 15000);
    record('agent websocket OPEN', Boolean(open), open ? open.url : 'socket=' + String(tuiState() && tuiState().socket));

    // 核心断言 2：服务端在 accept 后必发 session 事件；收到才说明这条 WS 真的通到路由。
    const settled = await waitFor(() => {
      const state = tuiState();
      return state && state.status !== 'connecting' ? state.status : null;
    }, 10000);
    record('agent websocket session event', Boolean(settled), 'status=' + String(settled));

    const wsUrl = open ? open.url : String(tuiState() && tuiState().socket && tuiState().socket.url);
    record('agent websocket url uses backend prefix', /\/agent-nodes\/[^/]+\/stream$/.test(wsUrl || ''), wsUrl);

    // 核心断言 3：让应用按自己的字面量打 HTTP 端点，状态码由服务端 journal 判定（不得 404）。
    const calls = [];
    function callAppHelper(label, code) {
      try {
        const value = evalFn(code);
        if (typeof value === 'string' && value.startsWith('ERR:')) {
          calls.push(label + '=' + value);
          return;
        }
        if (value && typeof value.catch === 'function') value.catch(() => {});
        calls.push(label + '=' + (value && typeof value.catch === 'function' ? 'invoked(async)' : 'invoked'));
      } catch (e) {
        calls.push(label + '=ERR:' + String(e));
      }
    }
    callAppHelper('setAgentModel', `setAgentModel(${JSON.stringify(NODE_ID)}, ${JSON.stringify(pid)}, 'route-guard-model')`);
    callAppHelper('setAgentAutonomy', `setAgentAutonomy(${JSON.stringify(NODE_ID)}, ${JSON.stringify(pid)}, 'standard')`);
    callAppHelper('showForkPicker', `showForkPicker(${JSON.stringify(NODE_ID)}, ${JSON.stringify(pid)})`);
    record('app HTTP helpers invoked', calls.every((item) => !item.includes('=ERR:')), calls.join(' '));
    await sleep(2500);
  } catch (e) {
    errors.push(String(e && e.message ? e.message : e));
  } finally {
    await post();
  }
})();
