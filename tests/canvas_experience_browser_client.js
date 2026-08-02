(async () => {
  const resultId = `canvas-experience-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const projectId = new URLSearchParams(location.search).get('project') || '';
  const assertions = [];
  const errors = [];

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

  const revealVisible = () => evalFn('() => document.getElementById("smartEdgeReveal").classList.contains("visible")')();
  const nodeInViewport = () => evalFn(`() => {
    const n = nodes.find(n => n.id === 'edge-prompt');
    if (!n) return null;
    const r = nodeRect(n);
    const cx = (r.x + r.width / 2) * viewport.scale + viewport.x;
    const cy = (r.y + r.height / 2) * viewport.scale + viewport.y;
    return cx >= 0 && cx <= shell.clientWidth && cy >= 0 && cy <= shell.clientHeight;
  }`)();

  function resetNodeForReuse(x, y) {
    evalFn(`() => {
      const n = nodes.find(n => n.id === 'edge-prompt');
      if (!n) return;
      n.images = [];
      n.pending = 0;
      n.queued = false;
      delete n.runFinishedAt;
      delete n.runElapsedMs;
      delete n.outputKind;
      delete n.title;
      delete n.w;
      delete n.h;
      n.x = ${x};
      n.y = ${y};
      render();
    }`)();
  }

  async function runNode(prompt) {
    const node = await waitFor('节点 DOM', () => document.querySelector('.image-node[data-id="edge-prompt"]'));
    node.click();
    await waitFor('composer 打开', () => document.querySelector('#composer.open'));
    const promptInput = document.querySelector('#promptInput');
    const runButton = document.querySelector('#runBtn');
    promptInput.textContent = prompt;
    promptInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: prompt }));
    await waitFor('运行按钮可用', () => !runButton.disabled, 12000);
    runButton.click();
    await waitFor('生成完成', () => {
      const n = evalFn('() => nodes.find(n => n.id === "edge-prompt")')();
      window.__diagLast = n ? { images: (n.images || []).length, pending: n.pending, fin: n.runFinishedAt, title: n.title } : null;
      return n && (n.images || []).some(img => img?.url) && !n.pending && n.runFinishedAt ? true : null;
    }, 60000, 250);
  }

  try {
    await waitFor('画布加载', () => evalFn('() => nodes.some(n => n.id === "edge-prompt")')(), 20000);

    const diag = evalFn('() => { const el = document.getElementById("smartEdgeReveal"); return el ? { found: true, cls: el.className } : { found: false }; }')();
    check('初始无边缘指示', !revealVisible(), 'diag=' + JSON.stringify(diag));

    await runNode('edge reveal prompt 1');
    check('视口内完成不显示边缘指示', !revealVisible());

    resetNodeForReuse(9000, 9000);
    await runNode('edge reveal prompt 2');
    const diag3 = evalFn(`() => {
      const n = nodes.find(n => n.id === 'edge-prompt');
      const r = n && nodeRect ? nodeRect(n) : null;
      const el = document.getElementById('smartEdgeReveal');
      return { x: n && n.x, y: n && n.y, rect: r, nodeId: smartEdgeRevealNodeId, timer: smartEdgeRevealTimer > 0, cls: el && el.className, vp: { x: viewport.x, y: viewport.y, s: viewport.scale } };
    }`)();
    check('视口外完成显示边缘指示', revealVisible(), 'visible=' + revealVisible() + ' diag=' + JSON.stringify(diag3));
    const label = evalFn('() => document.getElementById("smartEdgeRevealLabel").textContent')();
    check('指示文本为新结果', /新图片已生成/.test(label), label);
    const pos = evalFn('() => { const el = document.getElementById("smartEdgeReveal"); return {left: el.style.left, top: el.style.top}; }')();
    check('指示定位在视口边缘', Boolean(pos.left && pos.top), JSON.stringify(pos));

    document.getElementById('shell').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    await waitFor('pointerdown 关闭指示', () => !revealVisible(), 5000);
    check('pointerdown 关闭指示', true);

    resetNodeForReuse(12000, 12000);
    await runNode('edge reveal prompt 3');
    check('再次显示边缘指示', revealVisible(), 'visible=' + revealVisible());
    check('节点仍在视口外', nodeInViewport() === false, 'inViewport=' + nodeInViewport());
    document.getElementById('smartEdgeReveal').click();
    await waitFor('点击后指示关闭', () => !revealVisible(), 5000);
    check('点击跳转后节点进入视口', nodeInViewport() === true, 'inViewport=' + nodeInViewport());

    resetNodeForReuse(15000, 15000);
    await runNode('edge reveal prompt 4');
    check('第四次显示边缘指示', revealVisible(), 'visible=' + revealVisible());
    await sleep(8500);
    check('8 秒后自动隐藏', !revealVisible(), 'visible=' + revealVisible());
  } catch (error) {
    errors.push((error?.stack || String(error)) + ' | diagLast=' + JSON.stringify(evalFn('() => window.__diagLast ?? null')()));
  }

  const result = {
    kind: 'final',
    result_id: resultId,
    assertions,
    errors,
    pass: assertions.length > 0 && errors.length === 0,
  };
  fetch('/__conversation_browser_results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(result),
  });
})();
