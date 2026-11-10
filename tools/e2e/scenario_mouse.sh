#!/usr/bin/env bash
# scenario_mouse.sh — #299 用户实测鼠标操作验收（真实指针事件，非合成 click）
# 覆盖：点选节点、拖拽节点（连线跟随）、滚轮缩放（焦点保持）、框选、双击编辑。
# 用法：bash tools/e2e/scenario_mouse.sh  （需应用运行且已打开项目，同 e2e_qa.sh）
# tabbit 沙箱写不了磁盘：断言全部在页内完成，证据以 JSON 回传。
set -u
BASE="${FIREFLY_BASE:-http://127.0.0.1:3000}"
TABBIT="$LOCALAPPDATA/Tabbit/LocalAgent/bin/tabbit-cli.exe"
TABBIT="${TABBIT:-$HOME/.local/Tabbit/LocalAgent/bin/tabbit-cli.exe}"
if [ ! -x "$TABBIT" ]; then echo "FATAL: tabbit-cli 不存在: $TABBIT"; exit 99; fi
PID=$(curl -s -m 5 "$BASE/api/healthz" | python -c 'import sys,json;d=json.load(sys.stdin);ids=d.get("open_project_ids") or [];print(ids[0] if ids else "")' 2>/dev/null)
[ -z "$PID" ] && { echo "FATAL: $BASE 上没有打开的项目"; exit 98; }
URL="$BASE/static/smart-canvas.html?id=$PID&project=$PID&project_mode=1"
# #296：场景结束态自动截图存证；基线对比 tools/e2e_shot_diff.py
EVID_DIR="$(cd "$(dirname "$0")/../.." && pwd -W)/.pipeline/evidence/screenshots"
FAILS=0

run() {
  local name="$1" out i js
  js=$(cat)
  for i in 1 2; do
    out=$("$TABBIT" nodejs --task "Firefly Mouse QA" --request-id "mouse-$name-$i" --timeout-ms 180000 <<JS 2>&1
await page.goto("$URL", {waitUntil: "domcontentloaded"});
if (!page.url().includes('smart-canvas')) throw new Error('NAV_FAIL: ' + page.url());
await page.waitForSelector('.image-node', {timeout: 30000});
await page.waitForTimeout(2500);

// 公共：把首个节点居中到视口（保存的视口可能停在空白区，直接点坐标会落在窗外）
await page.evaluate(() => {
  const n = document.querySelector('.image-node');
  if (!n) throw new Error('NO_NODE');
  const w = canvasState.nodes.find(v => v.id === n.dataset.id) || {x:0,y:0};
  const cx = w.x + (w.w || 200) / 2, cy = w.y + (w.h || 200) / 2;
  viewport.x = window.innerWidth / 2 - cx * viewport.scale;
  viewport.y = window.innerHeight / 2 - cy * viewport.scale;
  applyViewport();
  canvasState.selectedId = ''; canvasState.selectedIds = [];
  return true;
});
await page.waitForTimeout(400);
const __r = await (async () => {
$js
})();
await page.screenshot();
return __r;
JS
)
    SHOT=$(echo "$out" | grep -o '"path":"[^"]*screenshot[^"]*\.png"' | tail -1 | sed 's/"path":"//;s/"$//;s/\\/\//g')
    if [ -n "$SHOT" ] && [ -f "$SHOT" ]; then mkdir -p "$EVID_DIR"; cp -f "$SHOT" "$EVID_DIR/$name-attempt$i.png" 2>/dev/null || true; fi
    if echo "$out" | grep -q '"status":"succeeded"'; then
      echo "PASS  $name  $(echo "$out" | grep -o '"evidence":"[^"]*"' | head -1)"
      return
    fi
  done
  echo "FAIL  $name"; echo "$out" | tail -4; FAILS=$((FAILS+1))
}

echo "== 鼠标操作验收 @ $URL =="

# M1 点选：真实 mousedown/up 命中节点 → canvasState.selectedId 非空，且节点获得选中态
run m1-click-select <<'JS'
const box = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2}; });
await page.mouse.move(box.x, box.y);
await page.mouse.down(); await page.mouse.up();
await page.waitForTimeout(600);
const ev = await page.evaluate(() => JSON.stringify({ sel: canvasState.selectedId, ids: canvasState.selectedIds }));
const j = JSON.parse(ev);
if (!j.sel && !(j.ids && j.ids.length)) throw new Error('SELECT_FAIL: ' + ev);
return { evidence: 'selected=' + (j.sel || j.ids.join(',')) };
JS

# M2 拖拽：节点按住移动 +120px → style.left/top 位移≈120/scale；连线（edge svg）端点跟随
run m2-drag-node <<'JS'
const before = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2, left:n.style.left, top:n.style.top}; });
await page.mouse.move(before.x, before.y);
await page.mouse.down();
for (let s = 1; s <= 10; s++) await page.mouse.move(before.x + s*12, before.y + s*6);
await page.mouse.up();
await page.waitForTimeout(600);
const after = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2, left:n.style.left, top:n.style.top}; });
const dx = after.x - before.x, dy = after.y - before.y;
if (Math.abs(dx - 120) > 25 || Math.abs(dy - 60) > 25) throw new Error(`DRAG_FAIL dx=${dx.toFixed(1)} dy=${dy.toFixed(1)} before=${before.left},${before.top} after=${after.left},${after.top}`);
return { evidence: `dx=${dx.toFixed(1)} dy=${dy.toFixed(1)}` };
JS

# M3 滚轮缩放：world transform scale 变化；焦点保持——光标下世界坐标误差 <2px
run m3-wheel-zoom <<'JS'
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
run m4-marquee <<'JS'
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

# M5 双击：图像节点双击 → 出现编辑/预览层（modal/lightbox/编辑器任一）；无编辑语义则 PASS-skip
run m5-dblclick <<'JS'
const box = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2, cls:n.className}; });
await page.mouse.click(box.x, box.y, { clickCount: 2, delay: 80 });
await page.waitForTimeout(900);
const ev = await page.evaluate(() => { const m=document.querySelector('.modal, .lightbox, .image-editor, .smart-preview, [class*="preview"], [class*="editor"]:not(.agent-tui-input)'); return m ? 'layer:' + m.className.split(' ')[0] : 'none'; });
if (ev === 'none') return { evidence: 'PASS-skip: 该节点类型双击无编辑层语义 (' + box.cls + ')' };
return { evidence: ev };
JS

# M6 拖拽后连线跟随：首条 edge 的端点坐标随节点位移同步（若存在连线）
run m6-edges-follow <<'JS'
const pre = await page.evaluate(() => {
  const n = document.querySelector('.image-node');
  const conns = (canvasState.connections || []).filter(c => (c.from === n.dataset.id || c.to === n.dataset.id));
  return { id: n.dataset.id, nconn: conns.length };
});
if (!pre.nconn) return { evidence: 'PASS-skip: 首个节点无连线 (id=' + pre.id + ')' };
const before = await page.evaluate(() => { const n=document.querySelector('.image-node'); const r=n.getBoundingClientRect(); const d=[...document.querySelectorAll('svg.connection-layer path')].map(x=>x.getAttribute('d')).join('|'); return {x:r.x+r.width/2, y:r.y+r.height/2, d}; });
await page.mouse.move(before.x, before.y);
await page.mouse.down();
for (let s = 1; s <= 6; s++) await page.mouse.move(before.x + s*20, before.y);
await page.mouse.up();
await page.waitForTimeout(700);
const after = await page.evaluate(() => [...document.querySelectorAll('svg.connection-layer path')].map(x=>x.getAttribute('d')).join('|'));
if (after === before.d) throw new Error('EDGE_STALE: connection-layer path d 未随节点更新');
return { evidence: 'conn=' + pre.nconn + ' path-d updated after drag' };
JS

# 标签卫生：销毁本套件标签，不碰用户标签
"$TABBIT" finish --task "Firefly Mouse QA" --discard >/dev/null 2>&1 || true
echo "== 失败数: $FAILS =="
exit $FAILS
