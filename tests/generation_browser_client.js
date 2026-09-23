(async () => {
  const resultId = `generation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const projectId = new URLSearchParams(location.search).get('id') || '';
  const assertions = [];
  const errors = [];

  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = detail;
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

  async function canvasSnapshot() {
    const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}`);
    if (!response.ok) throw new Error(`canvas GET ${response.status}`);
    const payload = await response.json();
    return payload.canvas || payload;
  }

  async function runNode(nodeId, prompt, kind) {
    const node = await waitFor(`${nodeId} DOM`, () => document.querySelector(`.image-node[data-id="${nodeId}"]`));
    node.click();
    await waitFor(`${nodeId} composer`, () => document.querySelector('#composer.open'));
    const engine = document.querySelector('#engineSelect');
    const promptInput = document.querySelector('#promptInput');
    const runButton = document.querySelector('#runBtn');
    check(`${kind} composer uses API engine`, engine?.value === 'api', engine?.value || 'missing');
    check(`${kind} composer controls exist`, Boolean(promptInput && runButton));
    if (kind === 'video') {
      check('video mode toggle is visible', Boolean(document.querySelector('#apiKindToggle')));
      check('video mode is selected', Boolean(document.querySelector('#apiKindToggle [data-kind="video"].active, #apiKindToggle .active')));
    }
    promptInput.textContent = prompt;
    promptInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: prompt }));
    await waitFor(`${kind} run button enabled`, () => !runButton.disabled, 12000);
    runButton.click();

    const saved = await waitFor(`${kind} task result`, async () => {
      const canvas = await canvasSnapshot();
      const current = (canvas.nodes || []).find(item => item.id === nodeId);
      const logs = (canvas.history || []).filter(item => item.result_node_id === nodeId && item.status === 'succeeded');
      return current?.images?.length && !current.pending && logs.length ? { canvas, current, logs } : null;
    }, 60000, 250);
    const current = saved.current;
    const media = current.images || [];
    check(`${kind} result has project asset`, Boolean(media[0]?.asset_id));
    check(`${kind} generation history is persisted`, saved.history.some(item => item.outputs?.length));
    const rendered = await waitFor(`${kind} result DOM`, () => {
      const nodeElement = document.querySelector(`.image-node[data-id="${nodeId}"]`);
      return nodeElement?.querySelector('.node-img, .media-card') || null;
    }, 10000);
    check(`${kind} result node is rendered`, Boolean(rendered));
    if (kind === 'image') {
      check('image result kind is image', media[0]?.kind === 'image', media[0]?.kind || 'missing');
    } else {
      check('video result kind is video', media[0]?.kind === 'video', media[0]?.kind || 'missing');
      const videoCard = await waitFor('video preview card', () => document.querySelector(`.image-node[data-id="${nodeId}"] .media-video-card`), 10000);
      check('video preview card is rendered', Boolean(videoCard));
    }
    return saved;
  }

  try {
    await waitFor('canvas boot', () => document.querySelector('.image-node[data-id="browser-image-node"]'));
    check('project generation fixture loaded', Boolean(document.querySelector('.image-node[data-id="browser-video-node"]')));
    await runNode('browser-image-node', 'browser image generation', 'image');
    await runNode('browser-video-node', 'browser video generation', 'video');
    const reloaded = await canvasSnapshot();
    check('both generated nodes survive canvas reload',
      ['browser-image-node', 'browser-video-node'].every(id => (reloaded.nodes || []).some(node => node.id === id && node.images?.length)));
  } catch (error) {
    errors.push({ name: 'generation browser flow', pass: false, detail: error?.stack || String(error) });
  }

  const payload = {
    kind: 'generation-browser-result',
    result_id: resultId,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    assertions,
    errors,
    pass: errors.length === 0 && assertions.every(item => item.pass),
  };
  await fetch('/__conversation_browser_results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
})();
