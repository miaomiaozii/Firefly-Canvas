#!/usr/bin/env bash
# scenario_mouse.sh — #299 用户实测鼠标操作验收（真实指针事件，非合成 click）
# 覆盖：点选节点、拖拽节点（连线跟随）、滚轮缩放（焦点保持）、框选、双击编辑。
# 用法：bash tools/e2e/scenario_mouse.sh  （需应用在 FIREFLY_BASE 跑着；项目由夹具自建自删）
# tabbit 沙箱写不了磁盘：断言全部在页内完成，证据以 JSON 回传。
# #360：不再用用户打开的项目——本套会真拖节点、真改视口，写进的是用户画布。
set -u
source "$(dirname "$0")/e2e_common.sh"
e2e_start "mouse" "Firefly Mouse QA" 180000

# M1 点选：真实 mousedown/up 命中节点 → canvasState.selectedId 非空，且节点获得选中态
e2e_run m1-click-select <<'JS'
const box = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2}; });
await page.mouse.move(box.x, box.y);
await page.mouse.down(); await page.mouse.up();
await page.waitForTimeout(600);
const ev = await page.evaluate(() => JSON.stringify({ sel: canvasState.selectedId, ids: canvasState.selectedIds, dom: !!document.querySelector('.image-node.selected') }));
const j = JSON.parse(ev);
if (!j.sel && !(j.ids && j.ids.length)) throw new Error('SELECT_FAIL: ' + ev);
if (!j.dom) throw new Error('SELECT_NOT_REFLECTED_IN_DOM: ' + ev);
return { evidence: 'selected=' + (j.sel || j.ids.join(',')) + ' .image-node.selected 在位' };
JS

# M2 拖拽：从 .node-head 按住移动 +120/+60 → 屏幕位移≈该值；world 坐标同步（不是只改 style）
# 为什么从 head 抓：空卡中央是 .node-drop 上传热区（实测 elementFromPoint 命中 svg/drop），
# 从中央拖不会动卡——真实用户也是拖标题栏。
e2e_run m2-drag-node <<'JS'
const grab = async (id) => await page.evaluate((nid) => {
  const el = document.querySelector(`.image-node[data-id="${nid}"] .node-head`);
  if (!el) throw new Error('NO_NODE_HEAD: ' + nid);
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
}, id);
const id0 = await page.evaluate(() => document.querySelector('.image-node').dataset.id);
const before = await page.evaluate((id) => {
  const n = document.querySelector(`.image-node[data-id="${id}"]`);
  const w = canvasState.nodes.find(v => v.id === id);
  const r = n.getBoundingClientRect();
  return { sx: r.x + r.width / 2, sy: r.y + r.height / 2, wx: w.x, wy: w.y };
}, id0);
const p = await grab(id0);
await page.mouse.move(p.x, p.y);
await page.mouse.down();
for (let s = 1; s <= 10; s++) await page.mouse.move(p.x + s * 12, p.y + s * 6);
await page.mouse.up();
await page.waitForTimeout(600);
const after = await page.evaluate((id) => {
  const n = document.querySelector(`.image-node[data-id="${id}"]`);
  const w = canvasState.nodes.find(v => v.id === id);
  const r = n.getBoundingClientRect();
  return { sx: r.x + r.width / 2, sy: r.y + r.height / 2, wx: w.x, wy: w.y };
}, id0);
const dx = after.sx - before.sx, dy = after.sy - before.sy;
if (Math.abs(dx - 120) > 25 || Math.abs(dy - 60) > 25) throw new Error(`DRAG_FAIL dx=${dx.toFixed(1)} dy=${dy.toFixed(1)} before=(${before.wx},${before.wy}) after=(${after.wx},${after.wy})`);
// 拖完必须落进画布状态（只改 style 的话刷新就丢，用户看到的是假位移）
if (Math.abs(after.wx - before.wx) < 20 || Math.abs(after.wy - before.wy) < 10) throw new Error('DRAG_NOT_IN_STATE: ' + JSON.stringify({before, after}));
return { evidence: `dx=${dx.toFixed(1)} dy=${dy.toFixed(1)} world=(${before.wx.toFixed(0)},${before.wy.toFixed(0)})→(${after.wx.toFixed(0)},${after.wy.toFixed(0)})` };
JS

# M3 滚轮缩放：world transform scale 变化；焦点保持——光标下世界坐标误差 <2px
e2e_run m3-wheel-zoom <<'JS'
const shell = await page.$('#shell') || await page.$('.shell') || (await page.evaluate(() => { const s=document.getElementById('shell')||document.querySelector('.shell'); const r=s.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2}; }));
const cx = typeof shell.x === 'number' ? shell.x : (await shell.boundingBox()).x + 300;
const cy = typeof shell.y === 'number' ? shell.y : (await shell.boundingBox()).y + 300;
const t0 = await page.evaluate(() => ({ t: world.style.transform, vx: viewport.x, vy: viewport.y, s: viewport.scale }));
await page.mouse.move(cx, cy);
await page.mouse.wheel(0, -240);
await page.waitForTimeout(500);
const t1 = await page.evaluate(() => ({ t: world.style.transform, vx: viewport.x, vy: viewport.y, s: viewport.scale }));
if (Math.abs(t1.s - t0.s) < 0.01) throw new Error('ZOOM_FAIL: scale 不变 ' + JSON.stringify([t0.s, t1.s]));
const rect = await page.evaluate(() => { const r=document.getElementById('shell')?.getBoundingClientRect()||document.querySelector('.shell').getBoundingClientRect(); return {l:r.left, t:r.top}; });
const sx = cx - rect.l, sy = cy - rect.t;
const w0x = (sx - t0.vx) / t0.s, w0y = (sy - t0.vy) / t0.s;
const w1x = (sx - t1.vx) / t1.s, w1y = (sy - t1.vy) / t1.s;
const err = Math.max(Math.abs(w1x - w0x), Math.abs(w1y - w0y));
if (err > 2) throw new Error(`FOCUS_FAIL err=${err.toFixed(2)}px`);
return { evidence: `scale ${t0.s.toFixed(3)}→${t1.s.toFixed(3)} focus-err=${err.toFixed(2)}px` };
JS

# M4 框选：空白处按住拖出矩形覆盖首个节点 → selectedIds 含该节点；松手后框选层清理
e2e_run m4-marquee <<'JS'
const geo = await page.evaluate(() => { const r = nodeRect(canvasState.nodes[0]); return {r:{x:r.x,y:r.y,w:r.width,h:r.height}, vp:{x:viewport.x,y:viewport.y,s:viewport.scale}}; });
const W2S = (wx,wy) => [wx*geo.vp.s + geo.vp.x, wy*geo.vp.s + geo.vp.y];
const [nx0, ny0] = W2S(geo.r.x, geo.r.y);
const sx = nx0 - 150, sy = ny0 - 150;
const ex = nx0 + geo.r.w*geo.vp.s + 80, ey = ny0 + geo.r.h*geo.vp.s + 80;
await page.keyboard.down('Control');
await page.mouse.move(sx, sy);
await page.mouse.down();
await page.mouse.move(sx + 60, sy + 60, {steps: 3});
await page.mouse.move(ex, ey, {steps: 6});
const mid = await page.evaluate(() => selectionBox.style.display);
await page.mouse.up();
await page.keyboard.up('Control');
await page.waitForTimeout(600);
const ev = await page.evaluate(() => JSON.stringify({ ids: canvasState.selectedIds, box: selectionBox.style.display }));
const j = JSON.parse(ev);
if (mid !== 'block') throw new Error('MARQUEE_BOX_MISSING: ' + mid);
if (!j.ids || !j.ids.length) throw new Error('MARQUEE_FAIL: ' + ev);
if (j.box !== 'none') throw new Error('MARQUEE_LEFTOVER: ' + ev);
return { evidence: 'marquee selected ' + j.ids.length };
JS

# M4b 框选后真实拖拽选中集：框住首节点 → 按住它拖 → 该节点真的位移（批量拖拽不是单选拖拽）
e2e_run m4b-marquee-then-drag <<'JS'
const geo = await page.evaluate(() => { const r = nodeRect(canvasState.nodes[0]); return {id: canvasState.nodes[0].id, r:{x:r.x,y:r.y,w:r.width,h:r.height}, vp:{x:viewport.x,y:viewport.y,s:viewport.scale}}; });
const W2S = (wx,wy) => [wx*geo.vp.s + geo.vp.x, wy*geo.vp.s + geo.vp.y];
const [nx0, ny0] = W2S(geo.r.x, geo.r.y);
await page.keyboard.down('Control');
await page.mouse.move(nx0 - 150, ny0 - 150);
await page.mouse.down();
await page.mouse.move(nx0 + geo.r.w*geo.vp.s + 80, ny0 + geo.r.h*geo.vp.s + 80, {steps: 6});
await page.mouse.up();
await page.keyboard.up('Control');
await page.waitForTimeout(500);
const sel = await page.evaluate(() => (canvasState.selectedIds || []).length);
if (!sel) return { skip: '框选未产生多选（selectedIds 为空），批量拖拽无从断言' };
// 拖拽起点用 .node-head（中央是上传热区，拖不动）
const head = await page.evaluate((id) => {
  const el = document.querySelector(`.image-node[data-id="${id}"] .node-head`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
}, geo.id);
if (!head) return { skip: '卡片上没有 .node-head，批量拖拽无拖拽点' };
const before = await page.evaluate((id) => { const w = canvasState.nodes.find(v => v.id === id); return { x: w.x, y: w.y }; }, geo.id);
await page.mouse.move(head.x, head.y);
await page.mouse.down();
for (let s = 1; s <= 6; s++) await page.mouse.move(head.x + s * 15, head.y + s * 15);
await page.mouse.up();
await page.waitForTimeout(700);
const after = await page.evaluate((id) => { const w = canvasState.nodes.find(v => v.id === id); return { x: w.x, y: w.y }; }, geo.id);
const moved = Math.max(Math.abs(after.x - before.x), Math.abs(after.y - before.y));
if (moved < 30) throw new Error('MULTI_DRAG_NO_MOVE: ' + JSON.stringify({before, after, sel}));
return { evidence: `多选=${sel} 拖后位移=${moved.toFixed(0)}px world=(${after.x.toFixed(0)},${after.y.toFixed(0)})` };
JS

# M5 双击：图像节点双击 → 出现编辑/预览层（modal/lightbox/编辑器任一）；无编辑语义则 skip
e2e_run m5-dblclick <<'JS'
const box = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2, cls:n.className}; });
await page.mouse.click(box.x, box.y, { clickCount: 2, delay: 80 });
await page.waitForTimeout(900);
const ev = await page.evaluate(() => { const m=document.querySelector('.modal, .lightbox, .image-editor, .smart-preview, [class*="preview"], [class*="editor"]:not(.agent-tui-input)'); return m ? 'layer:' + m.className.split(' ')[0] : 'none'; });
if (ev === 'none') return { skip: '该节点类型双击无编辑层语义 (' + box.cls + ')' };
return { evidence: ev };
JS

# M6 拖拽后连线跟随：连线 svg path 的 d 随节点位移同步。
# 不读 canvasState.connections——实测 canvasState 上根本没有这个键（连线在模块内），
# 读不到会把有线的画布误判成“没连线”而假 skip；改成看 DOM 里真的画了几条。
e2e_run m6-edges-follow <<'JS'
const id0 = await page.evaluate(() => document.querySelector('.image-node').dataset.id);
const pre = await page.evaluate(() => {
  const paths = [...document.querySelectorAll('svg.connection-layer path')];
  return { npath: paths.length, d: paths.map(p => p.getAttribute('d')).join('|') };
});
if (!pre.npath) throw new Error('NO_CONNECTION_RENDERED: 种子有 e2e-e1 连线但 connection-layer 里没有 path');
const head = await page.evaluate((id) => {
  const el = document.querySelector(`.image-node[data-id="${id}"] .node-head`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
}, id0);
if (!head) return { skip: '卡片无 .node-head，无法拖拽验证连线跟随' };
await page.mouse.move(head.x, head.y);
await page.mouse.down();
for (let s = 1; s <= 6; s++) await page.mouse.move(head.x + s * 20, head.y);
await page.mouse.up();
await page.waitForTimeout(700);
const after = await page.evaluate(() => [...document.querySelectorAll('svg.connection-layer path')].map(p => p.getAttribute('d')).join('|'));
if (after === pre.d) throw new Error('EDGE_STALE: connection-layer path d 未随节点更新');
return { evidence: 'paths=' + pre.npath + ' 拖拽后 path d 已更新' };
JS

e2e_finish
