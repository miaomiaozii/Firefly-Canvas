#!/usr/bin/env bash
# scenario_mouse.sh — #299 用户实测鼠标操作验收（真实指针事件，非合成 click）
set -u
source "$(dirname "$0")/e2e_common.sh"
e2e_start "mouse" "Firefly Mouse QA" 25000

# M1：focus_node 已选中种子，先真点击空白清空，避免把夹具初始选中误报为通过。
e2e_run m1-click-select <<'JS'
await clearSelection();
await clickAt(page.locator(`.image-node[data-id="${E2E_NODE_ID}"] .node-head`));
const state = await page.evaluate(id => ({
  selected: canvasState.selectedId === id || canvasState.selectedIds.includes(id),
  dom: !!document.querySelector(`.image-node.selected[data-id="${id}"]`),
}), E2E_NODE_ID);
if (!state.selected || !state.dom) throw new Error('SELECT_FAIL: ' + JSON.stringify(state));
return { evidence: 'selected=' + E2E_NODE_ID + ' .image-node.selected 在位' };
JS

# M2：空卡中央是上传热区；从标题栏拖，验证屏幕位移和画布世界坐标。
e2e_run m2-drag-node <<'JS'
await clearSelection();
const probe = () => page.evaluate(id => {
  const n = document.querySelector(`.image-node[data-id="${id}"]`);
  const w = canvasState.nodes.find(v => v.id === id);
  const r = n.getBoundingClientRect();
  return { sx: r.x + r.width / 2, sy: r.y + r.height / 2, wx: w.x, wy: w.y };
}, E2E_NODE_ID);
const before = await probe();
const p = await pointOn(nodeHead(E2E_NODE_ID), 120, 60);
await page.mouse.move(p.x, p.y);
await page.mouse.down();
for (let s = 1; s <= 10; s++) await page.mouse.move(p.x + s * 12, p.y + s * 6);
await page.mouse.up();
const after = await probe();
const dx = after.sx - before.sx, dy = after.sy - before.sy;
if (Math.abs(dx - 120) > 25 || Math.abs(dy - 60) > 25) throw new Error(`DRAG_FAIL dx=${dx.toFixed(1)} dy=${dy.toFixed(1)}`);
if (Math.abs(after.wx - before.wx) < 20 || Math.abs(after.wy - before.wy) < 10) throw new Error('DRAG_NOT_IN_STATE: ' + JSON.stringify({before, after}));
return { evidence: `dx=${dx.toFixed(1)} dy=${dy.toFixed(1)} world=(${before.wx},${before.wy})→(${after.wx},${after.wy})` };
JS

# M3：轮子在真实可达的画布背景发出；同时验证视口比例、DOM transform 和鼠标锚点。
e2e_run m3-wheel-zoom <<'JS'
const p = await page.evaluate(() => {
  const shell = document.getElementById('shell');
  const r = shell.getBoundingClientRect();
  const left = Math.max(1, r.left), right = Math.min(innerWidth - 1, r.right);
  const top = Math.max(1, r.top), bottom = Math.min(innerHeight - 1, r.bottom);
  for (const fy of [0.35, 0.65, 0.25, 0.75]) for (const fx of [0.2, 0.8, 0.35, 0.65]) {
    const x = left + (right - left) * fx, y = top + (bottom - top) * fy;
    const hit = document.elementFromPoint(x, y);
    if (hit === shell || hit === world) return {x, y, sx: x - r.left, sy: y - r.top};
  }
  throw new Error('NO_WHEEL_ANCHOR');
});
const view = () => page.evaluate(() => ({ t: world.style.transform, vx: viewport.x, vy: viewport.y, s: viewport.scale }));
const t0 = await view();
await page.mouse.move(p.x, p.y);
await page.mouse.wheel(0, -240);
await page.waitForFunction(s => Math.abs(viewport.scale - s) >= 0.01, t0.s, {timeout: 2500});
const t1 = await view();
if (t1.s <= t0.s || t1.t === t0.t) throw new Error('ZOOM_FAIL: ' + JSON.stringify([t0, t1]));
const err = Math.max(Math.abs((p.sx - t1.vx) / t1.s - (p.sx - t0.vx) / t0.s),
                     Math.abs((p.sy - t1.vy) / t1.s - (p.sy - t0.vy) / t0.s));
if (err > 2) throw new Error(`FOCUS_FAIL err=${err.toFixed(2)}px`);
return { evidence: `scale ${t0.s.toFixed(3)}→${t1.s.toFixed(3)} focus-err=${err.toFixed(2)}px` };
JS

# M4：DOM rect 是 client 坐标，避免将相对于 shell 的世界坐标直接交给鼠标。
e2e_run m4-marquee <<'JS'
await clearSelection();
await page.keyboard.press('Control+0');
const p = await page.evaluate(id => {
  const r = document.querySelector(`.image-node[data-id="${id}"]`).getBoundingClientRect();
  const b = document.getElementById('shell').getBoundingClientRect();
  const sx = Math.max(1, b.left + 1, r.left - 14), sy = Math.max(1, b.top + 1, r.top - 14);
  const ex = Math.min(innerWidth - 1, b.right - 1, r.right + 14), ey = Math.min(innerHeight - 1, b.bottom - 1, r.bottom + 14);
  const hit = document.elementFromPoint(sx, sy);
  if (sx >= r.left || sy >= r.top || ex <= r.left || ey <= r.top || (hit !== world && hit !== document.getElementById('shell'))) throw new Error('MARQUEE_GEOMETRY');
  return {sx, sy, ex, ey};
}, E2E_NODE_ID);
await page.keyboard.down('Control');
await page.mouse.move(p.sx, p.sy);
await page.mouse.down();
await page.mouse.move(p.ex, p.ey, {steps: 6});
const mid = await page.evaluate(() => selectionBox.style.display);
await page.mouse.up();
await page.keyboard.up('Control');
const state = await page.evaluate(() => ({ ids: canvasState.selectedIds, box: selectionBox.style.display }));
if (mid !== 'block') throw new Error('MARQUEE_BOX_MISSING: ' + mid);
if (state.ids.length !== 1 || state.ids[0] !== E2E_NODE_ID) throw new Error('MARQUEE_FAIL: ' + JSON.stringify(state));
if (state.box !== 'none') throw new Error('MARQUEE_LEFTOVER: ' + state.box);
return { evidence: 'marquee selected ' + E2E_NODE_ID };
JS

# M4b：用 Ctrl+0 让相距较远的两张种子卡同时可见；两张都须随同一手势移动。
e2e_run m4b-marquee-then-drag <<'JS'
const ids = [E2E_NODE_ID, 'e2e-n2'];
await clearSelection();
await page.keyboard.press('Control+0');
const p = await page.evaluate(ids => {
  const shell = document.getElementById('shell');
  const cards = ids.map(id => document.querySelector(`.image-node[data-id="${id}"]`));
  const rects = cards.map(el => el.getBoundingClientRect());
  const b = shell.getBoundingClientRect();
  const sx = Math.max(1, b.left + 1, Math.min(...rects.map(r => r.left)) - 14);
  const sy = Math.max(1, b.top + 1, Math.min(...rects.map(r => r.top)) - 14);
  const ex = Math.min(innerWidth - 1, b.right - 1, Math.max(...rects.map(r => r.right)) + 14);
  const ey = Math.min(innerHeight - 1, b.bottom - 1, Math.max(...rects.map(r => r.bottom)) + 14);
  const hit = document.elementFromPoint(sx, sy);
  if ((hit !== shell && hit !== world) || ex <= rects[1].left || ey <= rects[1].top) throw new Error('MULTI_MARQUEE_GEOMETRY');
  return {sx, sy, ex, ey};
}, ids);
await page.keyboard.down('Control');
await page.mouse.move(p.sx, p.sy);
await page.mouse.down();
await page.mouse.move(p.ex, p.ey, {steps: 6});
await page.mouse.up();
await page.keyboard.up('Control');
const selected = await page.evaluate(() => [...canvasState.selectedIds].sort());
if (JSON.stringify(selected) !== JSON.stringify([...ids].sort())) throw new Error('MULTI_MARQUEE_SELECTION: ' + JSON.stringify(selected));
const head = await pointOn(nodeHead(ids[0]), 90, 60);
const positions = () => page.evaluate(ids => ids.map(id => {
  const n = canvasState.nodes.find(v => v.id === id);
  return {x: n.x, y: n.y};
}), ids);
const before = await positions();
await page.mouse.move(head.x, head.y);
await page.mouse.down();
for (let s = 1; s <= 6; s++) await page.mouse.move(head.x + s * 15, head.y + s * 10);
await page.mouse.up();
const after = await positions();
const [a, b] = after.map((n, i) => ({dx: n.x - before[i].x, dy: n.y - before[i].y}));
if (a.dx < 30 || a.dy < 20 || Math.abs(a.dx - b.dx) > 2 || Math.abs(a.dy - b.dy) > 2) throw new Error('MULTI_DRAG_DELTA: ' + JSON.stringify({a, b}));
return { evidence: `多选=${selected.join(',')} world delta=(${a.dx},${a.dy})` };
JS

# M5：空卡双击是打开文件选择器，不会进入编辑层。只有带本地图像的夹具才能验证预览。
e2e_run m5-dblclick <<'JS'
const target = await page.evaluate(id => {
  const card = document.querySelector(`.image-node[data-id="${id}"]`);
  if (!card) throw new Error('M5_SEED_MISSING');
  if (card.classList.contains('empty-node')) return null;
  const el = card.querySelector('.thumb-item img, .smart-group-single-thumb img');
  if (!el) throw new Error('M5_THUMB_MISSING');
  const r = el.getBoundingClientRect();
  const x = (Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2;
  const y = (Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2;
  if (document.elementFromPoint(x, y) !== el) throw new Error('M5_THUMB_NOT_HITTABLE');
  return {x, y};
}, E2E_NODE_ID);
if (!target) return {skip: 'KNOWN-GAP: 种子是空卡，无本地图像；双击上传热区不是预览'};
if (await page.evaluate(() => !!document.querySelector('.smart-log-lightbox.open'))) throw new Error('M5_PREVIEW_ALREADY_OPEN');
await page.mouse.click(target.x, target.y, {clickCount: 2, delay: 60});
await page.waitForSelector('.smart-log-lightbox.open[aria-hidden="false"]', {state: 'visible', timeout: 2500});
return { evidence: '双击图像缩略图打开可见的详情预览' };
JS

# M6：读真实连接线（排除 hit path / 装饰 path），必须保留原线并更新曲线几何。
e2e_run m6-edges-follow <<'JS'
await clearSelection();
const paths = () => page.evaluate(() => [...document.querySelectorAll('svg.connection-layer path.conn-line')]
  .map(p => [p.getAttribute('data-conn-index'), p.getAttribute('d')]));
const pre = await paths();
if (pre.length !== 1 || !pre[0][1]) throw new Error('NO_FIXTURE_EDGE: ' + JSON.stringify(pre));
const head = await pointOn(nodeHead(E2E_NODE_ID), 120, 0);
await page.mouse.move(head.x, head.y);
await page.mouse.down();
for (let s = 1; s <= 6; s++) await page.mouse.move(head.x + s * 20, head.y);
await page.mouse.up();
await page.waitForTimeout(350);
const after = await paths();
if (after.length !== 1 || after[0][0] !== pre[0][0] || !after[0][1] || after[0][1] === pre[0][1]) throw new Error('EDGE_STALE_OR_LOST: ' + JSON.stringify({pre, after}));
return { evidence: '拖拽后原连线 path d 已更新' };
JS

e2e_finish
