// Layering contract browser client for issue #163.
//
// Replicates the reported setup in the shipped canvas (real headless Chrome,
// served by tests/conversation_browser.py):
//   1. seed: a normal node (layer-node), a smart-group frame (layer-group)
//      holding a member (layer-member) — see seed_store() in the driver;
//   2. select layer-node → the prompt composer (提示词框, #composer) opens;
//   3. move the generation group frame (生成分组框, .smart-group-node) until it
//      covers a hit point on the composer's top band — the exact #163 overlap
//      (group frame over composer) without flaky pointer drags;
//   4. open the history drawer (历史抽屉, #smartHistoryModal), then collapse it
//      — the user's persistent state: "开过一次历史抽屉后长期存在（折叠不解，
//      关闭才解）". Collapsed keeps the `.open` class (so the #152 node lift
//      stays active) while the panel shrinks to a 46px bottom strip that does
//      not cover the composer's top band;
//   5. assert the layering contract (bottom → top):
//      连线 < 生成分组框（背板） < 普通节点 < 选中/拖动中的节点 < 提示词框
//      < 面板类浮层 < 历史抽屉
//
// Contract checks:
//   * fully open: the drawer panel is the topmost surface at a point it paints
//     and that no canvas node covers (面板类浮层高于画布 — by design);
//   * open (collapsed, the #163 state): the group frame keeps its bottom
//     backboard layer (z below a normal node — the #152 lift must exclude
//     .smart-group-node); the composer is above both the group frame and the
//     lifted nodes; elementFromPoint at the overlap hits the composer, not the
//     group frame (the prompt box stays operable);
//   * baseline (drawer closed): the group frame corner still hits the group
//     frame itself, and the composer still beats the group frame.
//
// Result protocol: tests/conversation_browser.py (kind: final, result_id,
// assertions[{name,pass,detail?}], errors[], pass, diag).
(async () => {
  const resultId = `layering-contract-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const assertions = [];
  const errors = [];
  const diag = { clientVersion: 'v6' };
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
  const world = el('world');
  const groupEl = () => document.querySelector('#world .image-node[data-id="layer-group"]');
  const nodeEl = () => document.querySelector('#world .image-node[data-id="layer-node"]');
  const zOf = elm => getComputedStyle(elm).zIndex;
  const rectOf = elm => { const r = elm.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; };
  const rectCenter = rect => ({ x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2 });
  const containsPoint = (rect, p) => p.x >= rect.left && p.x <= rect.right && p.y >= rect.top && p.y <= rect.bottom;
  const describe = elm => elm ? `${elm.tagName.toLowerCase()}.${String(elm.className).slice(0, 60)} z=${zOf(elm)}` : 'none';
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
    // 1) 画布就绪：组框（成员以缩略图渲染在组框内，无独立 .image-node）与组外普通节点都已渲染
    await waitFor('canvas nodes rendered', () =>
      Boolean(groupEl()) && Boolean(nodeEl()));
    check('画布已渲染组框与普通节点', Boolean(groupEl()) && Boolean(nodeEl()), JSON.stringify(domSnapshot()));

    const groupBoxBefore = rectOf(groupEl());

    // 2) 基线（抽屉关闭）：组框是唯一覆盖其角落的画布元素 → 命中组框本体（最底层背板）
    const corner = { x: groupBoxBefore.left + 8, y: groupBoxBefore.top + 8 };
    const cornerHit = document.elementFromPoint(corner.x, corner.y);
    check('基线: 组框角落命中组框本体（最底层背板可命中）',
      Boolean(cornerHit) && groupEl().contains(cornerHit), describe(cornerHit));

    // 3) 选中普通节点 → 提示词框浮出（每次轮询重复选中：应用的渲染循环会覆写选中态）
    await waitFor('composer open', () => { reselect(); return composer.classList.contains('open'); });
    check('选中节点后提示词框浮出', composer.classList.contains('open'));
    await sleep(400); // 等待 composer 打开过渡（opacity/transform）结束

    // 4) #163 命中点：提示词框顶部条带（y = top + 12）——折叠后的抽屉只占底部
    //    46px 条带，该点必然在条带之外；随后把组框移到覆盖该点，复刻用户拖拽出的重叠。
    const composerRect = rectOf(composer);
    const hitPoint = { x: (composerRect.left + composerRect.right) / 2, y: composerRect.top + 12 };
    diag.composerRect = composerRect;
    diag.hitPoint = { x: Math.round(hitPoint.x), y: Math.round(hitPoint.y) };

    // 5) 复刻 #163 重叠：把组框移动到覆盖命中点（组框与其成员同步移动）
    let covered = false;
    for (let i = 0; i < 6 && !covered; i++) {
      const r = rectOf(groupEl());
      if (containsPoint(r, hitPoint)) { covered = true; break; }
      const d = { x: hitPoint.x - rectCenter(r).x, y: hitPoint.y - rectCenter(r).y };
      evalFn(`(dx, dy) => {
        const scale = (typeof viewport !== 'undefined' && viewport && Number(viewport.scale) > 0) ? Number(viewport.scale) : 1;
        ['layer-group', 'layer-member'].forEach(id => {
          const g = canvasState.nodes.find(n => n.id === id);
          if (g) { g.x = (Number(g.x) || 0) + dx / scale; g.y = (Number(g.y) || 0) + dy / scale; }
        });
        render();
      }`)(d.x, d.y);
      await sleep(150);
    }
    const groupBoxAfter = rectOf(groupEl());
    check('组框已覆盖提示词框命中点（#163 重叠场景就绪）', covered && containsPoint(groupBoxAfter, hitPoint),
      JSON.stringify({ before: groupBoxBefore, after: groupBoxAfter }));

    // 6) 打开历史抽屉（完全展开）：面板是画布上方的顶层面板——在其绘制区域
    //    （composer/组框未覆盖的面板左上区域）仍须命中抽屉内容。
    drawer.classList.add('open');
    await waitFor('drawer panel visible', () => getComputedStyle(drawer.querySelector('.log-panel')).visibility === 'visible', 5000);
    check('历史抽屉已打开', drawer.classList.contains('open'));
    await sleep(300); // 等待展开过渡结束
    const openPanelRect = rectOf(drawer.querySelector('.log-panel'));
    const openDrawerPoint = { x: openPanelRect.left + 20, y: openPanelRect.top + 20 };
    check('抽屉面板测试点位于面板内且未被组框/提示词框覆盖',
      containsPoint(openPanelRect, openDrawerPoint) &&
      !containsPoint(rectOf(groupEl()), openDrawerPoint) &&
      !containsPoint(composerRect, openDrawerPoint),
      JSON.stringify({ panel: openPanelRect, point: { x: Math.round(openDrawerPoint.x), y: Math.round(openDrawerPoint.y) },
        group: rectOf(groupEl()), composer: composerRect }));
    const openDrawerHit = document.elementFromPoint(openDrawerPoint.x, openDrawerPoint.y);
    check('完全展开: 抽屉面板在其绘制区域仍是最上层（面板类浮层高于画布）',
      Boolean(openDrawerHit) && drawer.contains(openDrawerHit), describe(openDrawerHit));
    diag.openPanelRect = openPanelRect;

    // 7) 折叠抽屉——用户的长期状态（.open 仍在，#152 抬升仍生效，面板缩为底部条带）
    drawer.classList.add('collapsed');
    await sleep(500); // 等待折叠过渡结束
    const stripRect = rectOf(drawer);
    diag.collapseStrip = stripRect;
    check('折叠后抽屉为底部窄条带且不覆盖命中点',
      stripRect.bottom - stripRect.top <= 60 && !containsPoint(stripRect, hitPoint),
      JSON.stringify({ strip: stripRect, hitPoint: { x: Math.round(hitPoint.x), y: Math.round(hitPoint.y) } }));
    if (!composer.classList.contains('open')) { reselect(); await sleep(400); }

    // 8) 核心契约（抽屉 open+collapsed = #163 用户状态）：
    //    生成分组框保持背板层、提示词框位于所有画布节点之上、重叠处可命中提示词框。
    const zGroup = zOf(groupEl());
    const zNode = zOf(nodeEl());
    const zComposer = zOf(composer);
    diag.zOrderDrawerOpen = { group: zGroup, node: zNode, composer: zComposer, drawer: zOf(drawer), worldTransform: getComputedStyle(world).transform };
    check('抽屉打开: 生成分组框未被抬升（组框 z < 普通节点 z）',
      Number(zGroup) < Number(zNode), `group=${zGroup} node=${zNode}`);
    check('抽屉打开: 提示词框位于生成分组框与普通节点之上（提示词框可操作）',
      Number(zComposer) > Number(zNode) && Number(zComposer) > Number(zGroup),
      `composer=${zComposer} node=${zNode} group=${zGroup}`);
    const hit = document.elementFromPoint(hitPoint.x, hitPoint.y);
    check('#163: 重叠处最上层命中目标是提示词框（可点击、可输入）',
      Boolean(hit) && composer.contains(hit),
      `hit=${describe(hit)} point=${JSON.stringify({ x: Math.round(hitPoint.x), y: Math.round(hitPoint.y) })}`);

    // 9) 关闭抽屉：提示词框仍须位于组框之上（回归检查）
    drawer.classList.remove('open');
    drawer.classList.remove('collapsed');
    await sleep(400);
    if (!composer.classList.contains('open')) { reselect(); await sleep(400); }
    const zGroupClosed = zOf(groupEl());
    const zNodeClosed = zOf(nodeEl());
    const zComposerClosed = zOf(composer);
    diag.zOrderDrawerClosed = { group: zGroupClosed, node: zNodeClosed, composer: zComposerClosed };
    check('抽屉关闭: 生成分组框仍是背板层（组框 z < 普通节点 z）',
      Number(zGroupClosed) < Number(zNodeClosed), `group=${zGroupClosed} node=${zNodeClosed}`);
    const closedHit = document.elementFromPoint(hitPoint.x, hitPoint.y);
    check('抽屉关闭: 重叠处仍命中提示词框而非组框',
      Boolean(closedHit) && composer.contains(closedHit),
      `hit=${describe(closedHit)} composerZ=${zComposerClosed} groupZ=${zGroupClosed}`);
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
