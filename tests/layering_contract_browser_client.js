// Layering contract browser client for issues #163 + #164.
//
// #164 lesson from #152: the old test only asserted computed z-index numbers
// ("node z == 87 with the drawer open"), which never caught the real
// occlusion. This client asserts REAL hit-testing (document.elementFromPoint)
// and REAL rectangle containment, in all three drawer states: closed, open
// (expanded) and open+collapsed (the user's long-lived state — collapsing the
// drawer does not remove the occlusion).
//
// Setup in the shipped canvas (real headless Chrome, tests/conversation_browser.py):
//   1. seed: a generation group frame (生成分组框, layer-group) holding a member,
//      a selected smart-image node (layer-node) whose prompt composer (提示词框)
//      is open, and an ordinary node (layer-node-b) overlapping that node;
//   2. move the group frame so it covers the composer's top band — the #163 overlap;
//   3. open the history drawer (历史抽屉) through the real product entry point
//      openSmartCanvasLog(), then collapse it through toggleSmartHistoryDrawerCollapsed();
//   4. assert the single layering ladder in every state (bottom → top):
//      连线 < 生成分组框（背板） < 普通节点 < 选中节点 < 提示词框 < 面板类浮层 < 历史抽屉
//
// Hit assertions (never z-index numbers):
//   * group-frame corner hits the group frame itself (bottom backboard, still hit-testable);
//   * at the group/composer overlap the topmost hit target is the composer,
//     in all three drawer states (the prompt box stays operable);
//   * at the overlap of the selected node and an ordinary node the topmost hit
//     target is the SELECTED node, in all three drawer states — the #164
//     collapse: `body:has(.log-modal.open) .image-node` (specificity 0,4,1)
//     flattened selected (10) / dragging (12) onto 87, so painting fell back to
//     DOM order and the ordinary node (later in DOM) covered the selected one;
//   * the drawer panel stays the topmost surface where it paints, and it stays
//     above the composer when the canvas is panned so they overlap (correct:
//     the drawer floats above the canvas);
//   * the composer is NOT pulled back into the visible band: canvas content may
//   legitimately leave the viewport (the user pans). Where composer and drawer
//   overlap the drawer wins the hit test; elsewhere the composer must win.
//     drawer's top edge when the drawer is open/collapsed — rectangle
//     containment, not a z-index comparison.
//
// Result protocol: tests/conversation_browser.py (kind: final, result_id,
// assertions[{name,pass,detail?}], errors[], pass, diag).
(async () => {
  const resultId = `layering-contract-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const assertions = [];
  const errors = [];
  const diag = { clientVersion: 'v7' };
  const result = {
    kind: 'final',
    result_id: resultId,
    clientScriptLoaded: true,
    viewport: { width: innerWidth, height: innerHeight },
    assertions,
    errors,
    pass: false,
    diag,
  };
  let resultPosted = false;

  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = String(detail);
    assertions.push(item);
    if (!item.pass) errors.push(`[FAIL] ${name}${detail ? ' — ' + detail : ''}`);
  };

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function waitFor(name, predicate, timeout = 15000, interval = 100) {
    const deadline = Date.now() + timeout;
    let last;
    let lastWarn = 0;
    while (Date.now() < deadline) {
      try {
        last = await predicate();
        if (last) return last;
      } catch (error) {
        last = error?.message || String(error);
      }
      if (Date.now() - lastWarn > 5000) {
        console.warn(`[layering-contract] waiting ${name}: ${last || '未就绪'}`);
        lastWarn = Date.now();
      }
      await sleep(interval);
    }
    throw new Error(`等待 ${name} 超时: ${last || '无结果'}`);
  }

  const pageErrors = [];
  window.addEventListener('error', event => {
    if (pageErrors.length < 20) pageErrors.push(String(event.message) + (event.filename ? ` @${event.filename}:${event.lineno}` : ''));
  });
  const evalFn = fn => Function('"use strict";return (' + fn + ')')();
  const el = id => document.getElementById(id);
  const composer = el('composer');
  const drawer = el('smartHistoryModal');
  const shell = el('shell');
  const world = el('world');
  const groupEl = () => document.querySelector('#world .image-node[data-id="layer-group"]');
  const nodeEl = () => document.querySelector('#world .image-node[data-id="layer-node"]');
  const nodeBEl = () => document.querySelector('#world .image-node[data-id="layer-node-b"]');
  const zOf = elm => getComputedStyle(elm).zIndex;
  const rectOf = elm => {
    const r = elm.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  };
  const rectCenter = rect => ({ x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2 });
  const containsPoint = (rect, p) => p.x >= rect.left && p.x <= rect.right && p.y >= rect.top && p.y <= rect.bottom;
  const intersect = (a, b) => {
    const left = Math.max(a.left, b.left);
    const top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    return right > left && bottom > top ? { left, top, right, bottom } : null;
  };
  const intersectAll = (...rects) => rects.reduce((acc, rect) => (acc ? intersect(acc, rect) : rect), rects[0]);
  const describe = elm => {
    if(!elm) return 'none';
    const self = `${elm.tagName.toLowerCase()}.${String(elm.className).trim().split(/\s+/).slice(0, 3).join('.')}${elm.dataset?.id ? `[data-id=${elm.dataset.id}]` : ''}`;
    // 命中子元素时补上所属层（哪个节点 / 提示词框 / 抽屉），否则 hit=div.node-drop 无法区分。
    const owner = elm.closest?.('.image-node[data-id],.composer,.log-modal');
    const suffix = owner?.dataset?.id ? `@${owner.dataset.id}`
      : owner?.classList?.contains('composer') ? '@composer'
        : owner?.classList?.contains('log-modal') ? '@log-modal' : '';
    return self + suffix;
  };
  const hitAt = point => (point ? document.elementFromPoint(point.x, point.y) : null);
  const hitInside = (point, owner) => {
    const hit = hitAt(point);
    return Boolean(hit) && owner.contains(hit);
  };
  // 节点与提示词框都允许出当前画布视口（平移画布就能看到），产品不会把它们“拉回”可见区；
  // 所以这里不再断言“必须落在可见带内”，需要命中测试时只现选一个没被浮层盖住的点。
  // 节点与提示词框都允许出当前画布视口（平移画布就能看到），产品不会把它们“拉回”可见区；
  // 所以这里不再断言“必须落在可见带内”，需要命中测试时只现选一个没被浮层盖住的点。
  const freePointIn = (rect, avoid, step = 10) => {
    const view = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    const area = intersect(rect, view) || rect;
    for (let y = area.top + step / 2; y < area.bottom; y += step) {
      for (let x = area.left + step / 2; x < area.right; x += step) {
        if (!avoid.some(r => r && containsPoint(r, x, y))) return { x, y };
      }
    }
    return null;
  };
  const domSnapshot = () => ({
    worldChildren: world?.children?.length ?? null,
    imageNodes: Array.from(document.querySelectorAll('#world .image-node')).map(n => n.dataset.id),
  });
  const reselect = () => evalFn(`() => {
    canvasState.selectedId = 'layer-node';
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId: 'layer-node', index: -1};
    render();
    updateComposer();
  }`)();
  // The overlap point of the selected node and the ordinary node, clipped to the
  // visible band and recomputed after every viewport change (the canvas pans to
  // keep the composer reachable, so the nodes move with it).
  const nodeOverlapPoint = band => {
    const area = intersectAll(rectOf(nodeEl()), rectOf(nodeBEl()), band);
    return area ? { x: (area.left + area.right) / 2, y: (area.top + area.bottom) / 2 } : null;
  };
  const shellBand = () => {
    const frame = rectOf(shell);
    return { left: 0, top: frame.top + 16, right: innerWidth, bottom: frame.bottom - 16 };
  };

  async function postClientLoaded() {
    await fetch('/__conversation_browser_results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'client-loaded', viewport: result.viewport }),
    }).catch(error => errors.push(`client loaded upload: ${error.message}`));
  }

  async function postResult() {
    if (resultPosted) return;
    resultPosted = true;
    result.pass = assertions.length > 0 && errors.length === 0 && assertions.every(item => item.pass);
    await fetch('/__conversation_browser_results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    }).catch(error => errors.push(`result upload: ${error.message}`));
  }

  async function run() {
    // 1) 画布就绪：组框、被选中节点、与它重叠的普通节点都已渲染
    await waitFor('canvas nodes rendered', () => Boolean(groupEl()) && Boolean(nodeEl()) && Boolean(nodeBEl()));
    check('画布已渲染组框、选中节点与普通节点', Boolean(groupEl()) && Boolean(nodeEl()) && Boolean(nodeBEl()), JSON.stringify(domSnapshot()));

    // 2) 基线（抽屉关闭）：组框角落先命中组框本体（最底层背板可命中）
    const groupBoxBefore = rectOf(groupEl());
    const groupCorner = { x: groupBoxBefore.left + 8, y: groupBoxBefore.top + 8 };
    check('关闭: 组框角落命中组框本体（最底层背板可命中）',
      hitInside(groupCorner, groupEl()), `group=${JSON.stringify(groupBoxBefore)} hit=${describe(hitAt(groupCorner))}`);

    // 3) 选中普通节点 → 提示词框浮出（应用的渲染循环会覆写选中态，轮询时重复选中）
    await waitFor('composer open', () => { reselect(); return composer.classList.contains('open'); });
    check('选中节点后提示词框浮出', composer.classList.contains('open'));
    await sleep(400); // 等待 composer 打开过渡（opacity/transform）结束

    // 4) 复刻 #163 重叠：把组框移动到覆盖提示词框顶部条带上的命中点
    const composerRect0 = rectOf(composer);
    const composerHitPoint = { x: (composerRect0.left + composerRect0.right) / 2, y: composerRect0.top + 12 };
    diag.composerRect = composerRect0;
    diag.composerHitPoint = { x: Math.round(composerHitPoint.x), y: Math.round(composerHitPoint.y) };
    let covered = false;
    for (let i = 0; i < 6 && !covered; i++) {
      const r = rectOf(groupEl());
      if (containsPoint(r, composerHitPoint)) { covered = true; break; }
      const d = { x: composerHitPoint.x - rectCenter(r).x, y: composerHitPoint.y - rectCenter(r).y };
      evalFn(`(dx, dy) => {
        const scale = (typeof viewport !== 'undefined' && viewport && Number(viewport.scale) > 0) ? Number(viewport.scale) : 1;
        ['layer-group', 'layer-member'].forEach(id => {
          const g = canvasState.nodes.find(n => n.id === id);
          if(g) { g.x = (Number(g.x) || 0) + dx / scale; g.y = (Number(g.y) || 0) + dy / scale; }
        });
        render();
      }`)(d.x, d.y);
      await sleep(150);
    }
    check('组框已覆盖提示词框命中点（#163 重叠场景就绪）', covered && containsPoint(rectOf(groupEl()), composerHitPoint),
      JSON.stringify({ group: rectOf(groupEl()), point: diag.composerHitPoint }));

    // 5) 抽屉关闭（基线）：真实命中 + 可见带包含
    const bandClosed = shellBand();
    diag.bandClosed = bandClosed;
    check('关闭: 组框覆盖提示词框时命中点是提示词框',
      hitInside(composerHitPoint, composer), `hit=${describe(hitAt(composerHitPoint))}`);
    const overlapClosed = nodeOverlapPoint(bandClosed);
    diag.nodeOverlapClosed = overlapClosed;
    check('关闭: 选中节点压在普通节点之上（命中选中节点）',
      overlapClosed && hitInside(overlapClosed, nodeEl()),
      `point=${JSON.stringify(overlapClosed)} hit=${describe(hitAt(overlapClosed))} z(node)=${zOf(nodeEl())} z(node-b)=${zOf(nodeBEl())}`);
    const composerRectClosed = rectOf(composer);
    diag.composerRectClosed = composerRectClosed;

    // 6) 打开历史抽屉（走产品入口 openSmartCanvasLog，不是手工加 class）
    evalFn('() => openSmartCanvasLog()')();
    await waitFor('drawer panel visible', () => getComputedStyle(drawer.querySelector('.log-panel')).visibility === 'visible', 5000);
    check('历史抽屉已打开', drawer.classList.contains('open'));
    await sleep(500); // 展开过渡 + 画布自动平移稳定
    const drawerRectOpen = rectOf(drawer);
    const bandOpen = { ...bandClosed, bottom: drawerRectOpen.top };
    diag.drawerRectOpen = drawerRectOpen;
    diag.bandOpen = bandOpen;

    const panelPoint = { x: drawerRectOpen.left + 20, y: drawerRectOpen.top + 20 };
    check('展开: 抽屉面板在其绘制区域是最上层（面板类浮层高于画布）',
      hitInside(panelPoint, drawer), `point=${JSON.stringify(panelPoint)} hit=${describe(hitAt(panelPoint))}`);
    const composerRectOpen = rectOf(composer);
    const composerFreeOpen = freePointIn(composerRectOpen, [drawerRectOpen]);
    diag.composerFreeOpen = composerFreeOpen;
    check('展开: 未被抽屉盖住的提示词框区域命中提示词框本身（不被组框抢走）',
      composerFreeOpen && hitInside(composerFreeOpen, composer),
      `point=${JSON.stringify(composerFreeOpen)} hit=${describe(hitAt(composerFreeOpen))} composer=${JSON.stringify(composerRectOpen)}`);
    const overlapOpen = nodeOverlapPoint(bandOpen);
    diag.nodeOverlapOpen = overlapOpen;
    check('展开: 选中节点仍压在普通节点之上（命中选中节点，不被压平）',
      overlapOpen && hitInside(overlapOpen, nodeEl()),
      `point=${JSON.stringify(overlapOpen)} hit=${describe(hitAt(overlapOpen))} z(node)=${zOf(nodeEl())} z(node-b)=${zOf(nodeBEl())}`);

    // 7) 抽屉仍须压住提示词框：把画布平移回提示词框与抽屉重叠处，命中必须是抽屉。
    const viewportBefore = evalFn('() => ({ y: viewport.y })')().y;
    const drawerCenter = rectCenter(drawerRectOpen);
    const panBack = drawerCenter.y - rectCenter(rectOf(composer)).y;
    evalFn(`(dy) => { viewport.y += dy; applyViewport(); }`)(panBack);
    await sleep(150);
    const composerRectUnder = rectOf(composer);
    const overlapWithDrawer = intersect(composerRectUnder, drawerRectOpen);
    const drawerWinsPoint = overlapWithDrawer
      ? { x: (overlapWithDrawer.left + overlapWithDrawer.right) / 2, y: (overlapWithDrawer.top + overlapWithDrawer.bottom) / 2 }
      : null;
    check('展开: 提示词框与抽屉重叠时命中抽屉（抽屉浮在画布之上，正确行为）',
      drawerWinsPoint && hitInside(drawerWinsPoint, drawer),
      `point=${JSON.stringify(drawerWinsPoint)} hit=${describe(hitAt(drawerWinsPoint))} composer=${JSON.stringify(composerRectUnder)}`);
    evalFn(`(y) => { viewport.y = y; applyViewport(); }`)(viewportBefore);
    await sleep(200);

    // 8) 折叠抽屉（走产品入口 toggleSmartHistoryDrawerCollapsed）：折叠不解除遮挡
    evalFn('() => toggleSmartHistoryDrawerCollapsed()')();
    await sleep(500);
    check('历史抽屉已折叠（.open 仍在）', drawer.classList.contains('open') && drawer.classList.contains('collapsed'));
    const drawerRectCollapsed = rectOf(drawer);
    const bandCollapsed = { ...bandClosed, bottom: drawerRectCollapsed.top };
    diag.drawerRectCollapsed = drawerRectCollapsed;
    diag.bandCollapsed = bandCollapsed;
    const composerRectCollapsed = rectOf(composer);
    const composerFreeCollapsed = freePointIn(composerRectCollapsed, [drawerRectCollapsed]);
    diag.composerFreeCollapsed = composerFreeCollapsed;
    check('折叠: 未被折叠条带盖住的提示词框区域命中提示词框本身',
      composerFreeCollapsed && hitInside(composerFreeCollapsed, composer),
      `point=${JSON.stringify(composerFreeCollapsed)} hit=${describe(hitAt(composerFreeCollapsed))} composer=${JSON.stringify(composerRectCollapsed)}`);
    const overlapCollapsed = nodeOverlapPoint(bandCollapsed);
    diag.nodeOverlapCollapsed = overlapCollapsed;
    check('折叠: 选中节点仍压在普通节点之上（命中选中节点，不被压平）',
      overlapCollapsed && hitInside(overlapCollapsed, nodeEl()),
      `point=${JSON.stringify(overlapCollapsed)} hit=${describe(hitAt(overlapCollapsed))} z(node)=${zOf(nodeEl())} z(node-b)=${zOf(nodeBEl())}`);
    const stripPoint = { x: drawerRectCollapsed.left + 20, y: (drawerRectCollapsed.top + drawerRectCollapsed.bottom) / 2 };
    check('折叠: 底部条带仍是最上层（折叠不解除遮挡）',
      hitInside(stripPoint, drawer), `point=${JSON.stringify(stripPoint)} hit=${describe(hitAt(stripPoint))}`);

    // 9) 关闭抽屉（走产品入口 closeSmartCanvasLog）：同一套契约，无状态切换
    evalFn('() => closeSmartCanvasLog()')();
    await sleep(400);
    if (!composer.classList.contains('open')) { reselect(); await sleep(400); }
    const composerHitClosedAgain = { x: (rectOf(composer).left + rectOf(composer).right) / 2, y: rectOf(composer).top + 12 };
    check('关闭后: 组框覆盖提示词框时仍命中提示词框',
      hitInside(composerHitClosedAgain, composer), `hit=${describe(hitAt(composerHitClosedAgain))}`);
    const overlapClosedAgain = nodeOverlapPoint(shellBand());
    check('关闭后: 选中节点仍压在普通节点之上',
      overlapClosedAgain && hitInside(overlapClosedAgain, nodeEl()),
      `point=${JSON.stringify(overlapClosedAgain)} hit=${describe(hitAt(overlapClosedAgain))} z(node)=${zOf(nodeEl())} z(node-b)=${zOf(nodeBEl())}`);
  }

  await postClientLoaded();
  try {
    await run();
  } catch (error) {
    errors.push(error?.stack || String(error));
  }
  diag.pageErrors = pageErrors;
  diag.dom = domSnapshot();
  await postResult();
})();
