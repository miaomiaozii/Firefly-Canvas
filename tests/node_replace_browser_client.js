(async () => {
  const resultId = `node-replace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const params = new URLSearchParams(location.search);
  const projectId = params.get('id') || '';
  const seedAsset = params.get('seedAsset') || '';
  const nodeId = 'replace-node';
  const assertions = [];
  const errors = [];

  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = detail;
    assertions.push(item);
    if (!item.pass) errors.push(item);
  };

  const windowErrors = [];
  window.addEventListener('error', event => {
    windowErrors.push(event.message || String(event.error || ''));
  });
  window.addEventListener('unhandledrejection', event => {
    windowErrors.push('unhandledrejection: ' + (event.reason?.message || String(event.reason || '')));
  });

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

  async function canvasSnapshot() {
    const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
    if (!response.ok) throw new Error(`canvas GET ${response.status}`);
    const payload = await response.json();
    return payload.canvas || payload;
  }

  try {
    await waitFor('画布加载节点', () => document.querySelector(`.image-node[data-id="${nodeId}"]`));

    const node = document.querySelector(`.image-node[data-id="${nodeId}"]`);
    node.click();
    await waitFor('composer 打开', () => document.querySelector('#composer.open'));
    const promptInput = document.querySelector('#promptInput');
    const runButton = document.querySelector('#runBtn');
    check('composer 控件存在', Boolean(promptInput && runButton));
    promptInput.textContent = 'replace node image in place';
    promptInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: 'replace node image in place' }));
    await waitFor('运行按钮可用', () => !runButton.disabled, 12000);
    check('runBtn 可点击', !runButton.disabled, `disabled=${runButton.disabled}`);
    runButton.click();

    const saved = await waitFor('生成流程结束且当前节点图更新', async () => {
      const canvas = await canvasSnapshot();
      const current = (canvas.nodes || []).find(item => item.id === nodeId);
      // #85：原位替换会把被替换的旧图归档进「候选池」(isHistoryGroup/historyFor 节点)，
      // 它不属于「新建输出节点」，不参与本合约的节点数判定。
      const newNodes = (canvas.nodes || []).filter(item => item.id !== nodeId && !item.isHistoryGroup && !item.historyFor);
      const firstImg = current?.images?.[0]?.asset_id;
      if (newNodes.length || (firstImg && firstImg !== seedAsset && !current.pending)) {
        return { canvas, current, newNodes };
      }
      return null;
    }, 60000, 250);

    const realNodes = canvas => (canvas.nodes || []).filter(item => !item.isHistoryGroup && !item.historyFor);
    check('画布节点数保持 1（未新建节点）', realNodes(saved.canvas).length === 1,
      `nodes=${realNodes(saved.canvas).length}`);
    check('当前节点图片被替换（asset_id 变化）', saved.current.images[0]?.asset_id !== seedAsset,
      `old=${seedAsset} new=${saved.current.images[0]?.asset_id} imgCount=${saved.current.images?.length} nodeIds=${(saved.canvas.nodes||[]).map(n=>n.id).join(',')}`);
    check('当前节点不再 pending', saved.current.pending === 0 || !saved.current.pending,
      `pending=${saved.current.pending}`);
    const rendered = await waitFor('节点 DOM 渲染新图', () => {
      const element = document.querySelector(`.image-node[data-id="${nodeId}"]`);
      return element?.querySelector('.node-img, .media-card') ? element : null;
    }, 10000);
    check('当前节点 DOM 有渲染结果', Boolean(rendered));

    const reloaded = await canvasSnapshot();
    const reloadedReal = (reloaded.nodes || []).filter(item => !item.isHistoryGroup && !item.historyFor);
    check('刷新后节点数仍为 1', reloadedReal.length === 1,
      `nodes=${reloadedReal.length}`);
    const reloadedCurrent = reloadedReal.find(item => item.id === nodeId);
    check('刷新后当前节点仍持有新图', reloadedCurrent?.images?.[0]?.asset_id !== seedAsset,
      `new=${reloadedCurrent?.images?.[0]?.asset_id} imgCount=${reloadedCurrent?.images?.length}`);
  } catch (error) {
    errors.push({ name: 'node replace flow', pass: false, detail: error?.stack || String(error) });
  }

  const payload = {
    kind: 'node-replace-browser-result',
    result_id: resultId,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    assertions,
    errors,
    windowErrors,
    pass: errors.length === 0 && assertions.every(item => item.pass),
  };
  await fetch('/__conversation_browser_results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
})();
