(async () => {
  const resultId = `crash-recovery-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const projectId = new URLSearchParams(location.search).get('project') || '';
  const assertions = [];
  const errors = [];
  const STATE_KEY = 'firefly_crash_recovery_state';

  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = String(detail);
    assertions.push(item);
    if (!item.pass) errors.push(item);
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(name, predicate, timeout = 30000, interval = 100) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
      try {
        last = await predicate();
        if (last) return last;
      } catch (error) {
        last = error?.message || String(error);
      }
      await sleep(interval);
    }
    throw new Error(`等待 ${name} 超时: ${last || '无结果'}`);
  }
  const evalFn = fn => Function('"use strict";return (' + fn + ')')();
  // #213：2d3381b（#162 P2e 4a）起 nodes 不再是裸全局，挂在 window.canvasState.nodes
  // （static/js/canvas-state.js:8）；兼容访问器同 tests/fly_fixes_browser_client.js:48。
  const nodesRef = '(function(){ const s = window.canvasState; return (s && Array.isArray(s.nodes)) ? s.nodes : (0, eval)("nodes"); })()';
  const nodeCount = () => evalFn(`() => { const list = ${nodesRef}; return list.filter(n => n.id !== "seed-prompt" && n.id !== "seed-image").length; }`)();
  const nodeIds = () => evalFn(`() => { const list = ${nodesRef}; return list.filter(n => n.id !== "seed-prompt" && n.id !== "seed-image").map(n => n.id); }`)();

  async function serverCanvasHasNode(nodeId) {
    const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
    if (!response.ok) return false;
    const data = await response.json();
    const canvas = data?.canvas || data?.project?.canvas || {};
    const list = canvas?.nodes || [];
    return list.some(n => n?.id === nodeId);
  }
  async function postResult() {
    await fetch('/__conversation_browser_results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: 'final',
        result_id: resultId,
        assertions,
        errors,
        pass: assertions.length > 0 && errors.length === 0,
      }),
    });
  }

  window.addEventListener('error', event => {
    if (event?.error?.message) errors.push(event.error.message);
  });
  window.addEventListener('unhandledrejection', event => errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));

  const restoredState = (() => {
    try { return JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null'); } catch (e) { return null; }
  })();

  const hasSeedNodes = () => {
    const ids = evalFn(`() => { const list = ${nodesRef}; return list.map(n => n.id); }`)();
    return ids.includes('seed-prompt') && ids.includes('seed-image');
  };

  if (!restoredState) {
    try {
      await waitFor('画布加载', () => hasSeedNodes() && evalFn('() => typeof render === "function"'), 30000);
      check('画布加载完成（seed 两节点）', hasSeedNodes());

      const preDiag = evalFn(`() => { const list = ${nodesRef}; return { timer: !!debouncedSaveTimer, dirty: contentDirty, durable: hasDurableCanvasChanges(), inflight: canvasSyncInFlight, rev: canvas?.revision, nodes: list.length, saved: (document.getElementById("projectSaveState")||{}).dataset?.state }; }`)();
      check('基线状态（createNode 前）', Boolean(preDiag), JSON.stringify(preDiag));
      const createdId = evalFn('() => { const n = createNode(900, 400); return n.id; }')();
      check('创建新节点（3s 防抖窗口内）', Boolean(createdId) && nodeCount() === 1, String(createdId));

      await sleep(300);
      const baseDiag = evalFn(`() => { const list = ${nodesRef}; return { timer: !!debouncedSaveTimer, dirty: contentDirty, durable: hasDurableCanvasChanges(), inflight: canvasSyncInFlight, rev: canvas?.revision, nodes: list.length }; }`)();
      check('防抖计时器尚未触发（仍在窗口内）', baseDiag.timer === true, JSON.stringify(baseDiag));

      await sleep(300);
      const afterDiag = evalFn(`() => { const list = ${nodesRef}; return { timer: !!debouncedSaveTimer, dirty: contentDirty, durable: hasDurableCanvasChanges(), inflight: canvasSyncInFlight, rev: canvas?.revision, nodes: list.length }; }`)();
      check('触发 pagehide（模拟关页/崩溃）', true, JSON.stringify(afterDiag));
      window.dispatchEvent(new PageTransitionEvent('pagehide'));

      await waitFor('兜底保存落盘', () => serverCanvasHasNode(createdId), 30000);
      check('3s 窗口内改动已落盘（兜底保存）', true);

      sessionStorage.setItem(STATE_KEY, JSON.stringify({ createdId, firstAssertions: assertions.slice(), firstErrors: errors.slice() }));
      location.reload();
      await new Promise(resolve => setTimeout(resolve, 30000));
      return;
    } catch (error) {
      errors.push((error?.stack || String(error)));
      await postResult();
      return;
    }
  }

  try {
    await waitFor('重载后画布加载', () => hasSeedNodes() && evalFn('() => typeof render === "function"'), 30000);
    await waitFor('重载后新节点出现', () => nodeIds().includes(restoredState.createdId), 30000);
    check('重载后新节点恢复', nodeIds().includes(restoredState.createdId), 'ids=' + JSON.stringify(nodeIds()));

    const domPresent = await waitFor('重载后新节点 DOM 可见', () => {
      const id = restoredState.createdId;
      return Boolean(document.querySelector(`.image-node[data-id="${id}"]`));
    }, 30000);
    check('重载后新节点 DOM 渲染', domPresent);

    const inFlight = evalFn('() => canvasSyncInFlight')();
    check('保存链无卡死', inFlight === false, 'inFlight=' + inFlight);
    sessionStorage.removeItem(STATE_KEY);
  } catch (error) {
    errors.push((error?.stack || String(error)));
  }
  if (restoredState?.firstAssertions) {
    restoredState.firstAssertions.forEach(item => assertions.push(item));
  }
  await postResult();
})();
