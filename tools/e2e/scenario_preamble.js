// Browser evaluation only reads app/DOM state; all user input uses mouse/keyboard.
const E2E_NODE_ID = 'e2e-n1';
const E2E_OFFLINE = true;
const fixturePost = async (path, body, headers = {}) => {
  if (path !== `/api/projects/${E2E_PID}/canvas/nodes`) throw new Error('Fixture POST path not allowed');
  const resp = await fetch(E2E_BASE + path, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(5000),
    headers: { ...headers, Origin: E2E_BASE, 'Content-Type': 'application/json' },
    body: JSON.stringify({...body, agent_node_id: 'e2e-a1'}),
  });
  return { status: resp.status, ok: resp.ok, data: await resp.json() };
};
const pointOn = async (locator, dx = 0, dy = 0) => {
  await locator.waitFor({ state: 'visible', timeout: 4000 });
  return locator.evaluate((el, { dx, dy }) => {
    const r = el.getBoundingClientRect();
    const left = Math.max(1, r.left, 1 - dx), right = Math.min(r.right, innerWidth - 1, innerWidth - 1 - dx);
    const top = Math.max(1, r.top, 1 - dy), bottom = Math.min(r.bottom, innerHeight - 1, innerHeight - 1 - dy);
    if (right <= left || bottom <= top) throw new Error('TARGET_OUT_OF_VIEW: ' + JSON.stringify(r));
    for (const fy of [0.5, 0.25, 0.75]) for (const fx of [0.5, 0.25, 0.75]) {
      const x = left + (right - left) * fx, y = top + (bottom - top) * fy;
      const hit = document.elementFromPoint(x, y);
      if (el.contains(hit) && (!(dx || dy) || !hit.closest('button,input,textarea,[contenteditable="true"]'))) {
        return { x, y };
      }
    }
    throw new Error('TARGET_OBSCURED: ' + JSON.stringify({ rect: r, dx, dy }));
  }, { dx, dy });
};
const clickAt = async (locator) => {
  const { x, y } = await pointOn(locator);
  await page.mouse.move(x, y);
  await page.mouse.click(x, y);
};
const blankCanvas = () => page.evaluate(() => {
  const shell = document.getElementById('shell');
  const r = shell.getBoundingClientRect();
  const right = Math.min(innerWidth - 1, r.right), bottom = Math.min(innerHeight - 1, r.bottom);
  for (const fy of [.35, .65, .25, .75]) for (const fx of [.2, .8, .35, .65]) {
    const x = r.left + (right - r.left) * fx, y = r.top + (bottom - r.top) * fy;
    const hit = document.elementFromPoint(x, y);
    if (hit === shell || hit === world) return {x, y};
  }
  throw new Error('NO_CANVAS_BACKGROUND');
});
const clearSelection = async () => {
  const {x, y} = await blankCanvas();
  await page.mouse.click(x, y);
  await page.waitForFunction(() => !canvasState.selectedId && !canvasState.selectedIds.length &&
    !document.getElementById('composer').classList.contains('open'), null, {timeout: 3000});
};
const nodeHead = id => page.locator(`.image-node[data-id="${id}"] .node-head`);
const clickNode = id => clickAt(nodeHead(id));
// Composer 在选中后的 animation frame 定位；等待它到卡片下方再验收控件。
const waitForNodeComposer = id => page.waitForFunction(id => {
  const card = document.querySelector(`.image-node.selected[data-id="${CSS.escape(id)}"]`);
  const composer = document.querySelector('#composer.open');
  return card && composer &&
    composer.getBoundingClientRect().top >= card.getBoundingClientRect().bottom;
}, id, {timeout: 4000});
const moveToNodeHead = async id => {
  const {x, y} = await pointOn(nodeHead(id));
  await page.mouse.move(x, y);
};
const selectImageCard = async id => {
  await clearSelection();
  await clickNode(id);
  await waitForNodeComposer(id);
};
const showEmptyCardDelete = async id => {
  // 渲染可能替换鼠标下的卡片；选中后重新悬停，删除按钮才可命中。
  await moveToNodeHead(id);
  await page.waitForFunction(id => {
    const button = document.querySelector(`.image-node.empty-node.selected[data-id="${CSS.escape(id)}"] .node-head .node-delete`);
    if (!button) return false;
    const r = button.getBoundingClientRect();
    return r.width > 0 && r.height > 0 &&
      button.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) &&
      Number(getComputedStyle(button).opacity) > 0;
  }, id, {timeout: 3000});
};
const waitForSeedNodes = () => page.waitForFunction(ids => typeof canvasState !== 'undefined' && ids.every(id =>
  canvasState.nodes.some(node => node.id === id) &&
  Boolean(document.querySelector(`.image-node[data-id="${CSS.escape(id)}"]`))
), E2E_SEED.nodeIds, { timeout: 8000 });

await page.setViewportSize({ width: 1440, height: 960 });
const nav = await page.goto(E2E_URL, { waitUntil: 'domcontentloaded', timeout: 10000 });
if (!nav?.ok() || page.url() !== E2E_URL) throw new Error('NAV_FAIL: ' + page.url());
await page.waitForSelector(`.image-node[data-id="${E2E_NODE_ID}"]`, { timeout: 8000 });
await page.waitForFunction(({ nodes, edges }) => {
  const actualNodes = canvasState.nodes.map(n => `${n.id}@${n.x},${n.y}`).sort();
  const actualEdges = canvas.connections.map(e => `${e.id}:${e.from}>${e.to}`).sort();
  return JSON.stringify(actualNodes) === JSON.stringify(nodes) &&
         JSON.stringify(actualEdges) === JSON.stringify(edges);
}, E2E_SEED, { timeout: 5000 });
// URL focus 在画布载入后的 animation frame 执行。先等其选中目标，再计算鼠标坐标。
await page.waitForFunction(id => canvasState.selectedId === id && Boolean(
  document.querySelector(`.image-node.selected[data-id="${id}"]`)
), E2E_NODE_ID, {timeout: 5000});
