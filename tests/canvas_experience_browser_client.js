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

    const foundationLink = document.querySelector('link[href*="/static/css/studio-foundation.css"]');
    check('画布加载共享 UI foundation', Boolean(foundationLink), foundationLink?.getAttribute('href') || 'missing');
    check('共享控件字号 token 可用', getComputedStyle(document.documentElement).getPropertyValue('--font-control').trim() === '12px', getComputedStyle(document.documentElement).getPropertyValue('--font-control'));

    const shell = document.getElementById('shell');
    const shellStyle = getComputedStyle(shell);
    const shellBackground = shellStyle.backgroundImage;
    const shellBefore = getComputedStyle(shell, '::before');
    check('画布背景只有一层均匀点阵', (shellBackground.match(/radial-gradient/g) || []).length === 1 && !/linear-gradient|ellipse/i.test(shellBackground), shellBackground);
    check('画布点阵间距为 24-26px', /^2[4-6]px\s+2[4-6]px$/.test(shellStyle.backgroundSize), shellStyle.backgroundSize);
    check('画布没有 ambient 伪元素 band', shellBefore.content === 'none' && shellBefore.backgroundImage === 'none', `${shellBefore.content} / ${shellBefore.backgroundImage}`);

    const toolbar = document.querySelector('.smart-toolbar[role="toolbar"]');
    const toolbarControls = toolbar ? [...toolbar.children] : [];
    const toolbarOrder = [
      toolbarControls[0]?.matches('.smart-back'),
      toolbarControls[1]?.id === 'smartUndoButton',
      toolbarControls[2]?.id === 'smartRedoButton',
      toolbarControls[3]?.id === 'smartSettingsLink',
      toolbarControls[4]?.id === 'smartShortcutToggle',
      toolbarControls[5]?.id === 'smartHistoryToggle',
      toolbarControls[6]?.id === 'assetToggle',
      toolbarControls[7]?.id === 'smartSaveButton',
    ];
    check('八个画布控件位于唯一工具栏并保持规范顺序', toolbarControls.length === 8 && toolbarOrder.every(Boolean), toolbarControls.map(control => control.id || control.className).join(' > '));
    const allChromeControls = [...document.querySelectorAll('.smart-back,.smart-undo,.smart-redo,.smart-settings,.smart-save,.smart-shortcut-toggle,.smart-log-toggle,.asset-toggle')];
    check('顶部右侧没有第二工具栏', allChromeControls.length === 8 && allChromeControls.every(control => control.closest('.smart-toolbar') === toolbar), allChromeControls.length);
    const toolbarRect = toolbar?.getBoundingClientRect();
    const controlRects = toolbarControls.map(control => control.getBoundingClientRect());
    const controlStyles = toolbarControls.map(control => getComputedStyle(control));
    const toolbarStyle = toolbar && getComputedStyle(toolbar);
    check('工具栏控件共享 30px 几何与 12px 字号', controlRects.length === 8 && controlRects.every((rect, index) => Math.abs(Number.parseFloat(controlStyles[index].height) - 30) <= 0.5 && Math.abs(rect.top - controlRects[0].top) <= 0.5) && controlStyles.every(style => style.fontSize === '12px'), JSON.stringify(controlRects.map((rect, index) => ({renderedHeight: rect.height, cssHeight: controlStyles[index]?.height, top: rect.top, font: controlStyles[index]?.fontSize}))));
    check('工具栏使用左上安全边距', Boolean(toolbarStyle) && Number.parseFloat(toolbarStyle.left) >= 12 && Number.parseFloat(toolbarStyle.left) <= 16.5 && Number.parseFloat(toolbarStyle.top) >= 12 && Number.parseFloat(toolbarStyle.top) <= 16.5, toolbarStyle && JSON.stringify({left: toolbarStyle.left, top: toolbarStyle.top}));
    check('所有图标工具控件有可访问名称', toolbarControls.every(control => Boolean(control.getAttribute('aria-label') || control.textContent.trim())), toolbarControls.map(control => control.getAttribute('aria-label') || '').join('|'));
    const toolbarLabelsHidden = toolbarControls.every(control => getComputedStyle(control.querySelector('span')).display === 'none');
    check('工具栏始终只显示图标并保留可访问名称', toolbarLabelsHidden, `width=${innerWidth}, labelsHidden=${toolbarLabelsHidden}`);

    const minimap = document.getElementById('minimap');
    const minimapRect = minimap?.getBoundingClientRect();
    const minimapStyle = minimap && getComputedStyle(minimap);
    const minimapVisible = minimapRect && getComputedStyle(minimap).display !== 'none' && minimapRect.width > 0 && minimapRect.height > 0;
    const chromeOverlaps = toolbarRect && minimapRect && !(toolbarRect.right <= minimapRect.left || minimapRect.right <= toolbarRect.left || toolbarRect.bottom <= minimapRect.top || minimapRect.bottom <= toolbarRect.top);
    check('小地图始终可见且不与工具栏重叠', minimapVisible && !chromeOverlaps, JSON.stringify({toolbar: toolbarRect && {right: toolbarRect.right, bottom: toolbarRect.bottom}, minimap: minimapRect && {left: minimapRect.left, top: minimapRect.top, width: minimapRect.width, height: minimapRect.height}}));
    check('小地图位于右上安全区', Boolean(minimapRect) && Number.parseFloat(minimapStyle.right) >= 11 && Number.parseFloat(minimapStyle.right) <= 16.5 && (innerWidth >= 1100 ? Number.parseFloat(minimapStyle.top) >= 15 && Number.parseFloat(minimapStyle.top) <= 16.5 : minimapRect.top >= toolbarRect.bottom + 6), minimapRect && JSON.stringify({cssRight: minimapStyle.right, cssTop: minimapStyle.top, renderedTop: minimapRect.top, toolbarBottom: toolbarRect?.bottom}));

    document.getElementById('smartShortcutToggle')?.click();
    const shortcut = await waitFor('快捷键面板打开', () => document.querySelector('.shortcut-modal.open'));
    const shortcutRect = shortcut.getBoundingClientRect();
    const titleRect = document.getElementById('smartTitle')?.getBoundingClientRect();
    const saveState = document.getElementById('projectSaveState');
    const saveStateRect = saveState && !saveState.hidden ? saveState.getBoundingClientRect() : null;
    const leftChromeBottom = Math.max(toolbarRect?.bottom || 0, titleRect?.bottom || 0, saveStateRect?.bottom || 0);
    const shortcutObstacles = [...document.querySelectorAll('.image-node'), document.getElementById('composer')]
        .filter(Boolean)
        .map(element => ({rect:element.getBoundingClientRect(), style:getComputedStyle(element)}))
        .filter(item => item.style.display !== 'none' && item.style.visibility !== 'hidden')
        .map(item => item.rect)
        .filter(rect => rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight);
    const overlapArea = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
        * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    const actualShortcutOverlap = shortcutObstacles.reduce((total, rect) => total + overlapArea(shortcutRect, rect), 0);
    const baselineShortcutRect = {
      left:shortcutRect.left,
      right:shortcutRect.right,
      top:leftChromeBottom + 8,
      bottom:leftChromeBottom + 8 + shortcutRect.height,
    };
    const baselineShortcutOverlap = shortcutObstacles.reduce((total, rect) => total + overlapArea(baselineShortcutRect, rect), 0);
    const shortcutMinimapOverlap = minimapRect && !(shortcutRect.right <= minimapRect.left || minimapRect.right <= shortcutRect.left || shortcutRect.bottom <= minimapRect.top || minimapRect.bottom <= shortcutRect.top);
    const worldWhileShortcutOpen = getComputedStyle(document.getElementById('world')).translate;
    check('快捷键面板从左侧展开并避开顶部状态区', shortcutRect.left >= 11 && shortcutRect.left <= 16.5 && shortcutRect.top >= leftChromeBottom + 8, JSON.stringify({shortcut:{left:shortcutRect.left, top:shortcutRect.top}, leftChromeBottom}));
    check('快捷键面板选择重叠更少的左侧垂直位置', actualShortcutOverlap <= baselineShortcutOverlap + 1, JSON.stringify({actualShortcutOverlap, baselineShortcutOverlap, top:shortcutRect.top}));
    check('快捷键面板不遮挡右上小地图', !shortcutMinimapOverlap, JSON.stringify({shortcut:{right:shortcutRect.right, bottom:shortcutRect.bottom}, minimap:minimapRect && {left:minimapRect.left, top:minimapRect.top}}));
    check('桌面快捷键面板作为左侧抽屉让画布内容让位', innerWidth < 901 || (document.getElementById('shell')?.classList.contains('shortcut-panel-open') && worldWhileShortcutOpen !== 'none'), JSON.stringify({viewport:innerWidth, translate:worldWhileShortcutOpen}));
    shortcut.querySelector('.shortcut-head button')?.click();
    await waitFor('快捷键面板关闭', () => !document.querySelector('.shortcut-modal.open'));
    check('关闭快捷键抽屉后画布恢复原位', !document.getElementById('shell')?.classList.contains('shortcut-panel-open') && getComputedStyle(document.getElementById('world')).translate === 'none', getComputedStyle(document.getElementById('world')).translate);

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
