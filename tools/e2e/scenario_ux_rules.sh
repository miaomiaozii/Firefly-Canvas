#!/usr/bin/env bash
# scenario_ux_rules.sh — #297 体验规则自动断言（页内静态规则，无指针交互）
# 覆盖：
#   U1 文字溢出/截断 —— 卡片内 overflow:hidden 裁剪且无 ellipsis/不可滚动的文字 → FAIL
#   U2 徽章单行      —— badge/pill/status 类文字竖排或折行（Range 多行片段）→ FAIL
#   U3 最小命中尺寸  —— 卡片内 button/a 命中盒 <20px（布局 px，不受画布缩放影响）→ FAIL
#   U4 图标按钮可访问名 —— 纯图标 button/a 无 aria-label/title/img-alt → FAIL
#   U5 控件不出容器  —— button/a/input/textarea/select boundingRect 超出卡片边界（2px 容差）→ FAIL
# 原则：宁可 PASS-skip 不可误报 FAIL（先跑一轮看误报再收紧）。
# tabbit 沙箱写不了磁盘：断言全部在页内 page.evaluate 完成，证据以 JSON 回传。
# 用法：bash tools/e2e/scenario_ux_rules.sh  （需应用运行且已打开项目，同 e2e_qa.sh）
set -u
BASE="${FIREFLY_BASE:-http://127.0.0.1:3000}"
TABBIT="$LOCALAPPDATA/Tabbit/LocalAgent/bin/tabbit-cli.exe"
TABBIT="${TABBIT:-$HOME/.local/Tabbit/LocalAgent/bin/tabbit-cli.exe}"
if [ ! -x "$TABBIT" ]; then echo "FATAL: tabbit-cli 不存在: $TABBIT"; exit 99; fi

# 浏览器并发锁：tabbit 被多套件共享，mkdir 取独占锁；失败 sleep 30s 重试（最多 10 分钟）
LOCK="D:/ff-wt/.browser.lock"
n=0
until mkdir "$LOCK" 2>/dev/null; do
  n=$((n+1))
  if [ "$n" -ge 12 ]; then echo "FATAL: 浏览器锁 $LOCK 等待超时 (10min)"; exit 97; fi
  echo "等待浏览器锁 ($n)..."; sleep 30
done
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

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
    out=$("$TABBIT" nodejs --task "UX Rules QA" --request-id "ux-$name-$i" --timeout-ms 180000 <<JS 2>&1
await page.goto("$URL", {waitUntil: "domcontentloaded"});
if (!page.url().includes('smart-canvas')) throw new Error('NAV_FAIL: ' + page.url());
await page.waitForSelector('.image-node', {timeout: 30000});
await page.waitForTimeout(2500);

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

echo "== UX 规则断言 @ $URL =="

# U1 文字溢出/截断：卡片内被 overflow:hidden 裁剪（水平或垂直）且无 ellipsis 豁免、不可滚动的文字
run u1-text-overflow <<'JS'
const r = await page.evaluate(() => {
  const cards = [...document.querySelectorAll('.image-node')];
  const fails = []; let checked = 0, ellipsisWl = 0, scrollWl = 0;
  const css = el => getComputedStyle(el);
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  cards.forEach(card => {
    const targets = [card, ...card.querySelectorAll('*')];
    targets.forEach(el => {
      const s = css(el);
      if (s.display === 'none' || s.visibility !== 'visible') return;
      const hidden = s.overflowX === 'hidden' || s.overflowY === 'hidden';
      if (!hidden) return;
      const cw = el.clientWidth, ch = el.clientHeight;
      if (cw < 4 || ch < 4) return; // 零尺寸盒：PASS-skip
      checked++;
      const hw = el.scrollWidth > cw + 1;   // 水平被裁
      const hh = el.scrollHeight > ch + 1;  // 垂直被裁
      if (!hw && !hh) return;
      if (s.overflowX === 'auto' || s.overflowY === 'auto' || s.overflowX === 'scroll' || s.overflowY === 'scroll') { scrollWl++; return; } // 可滚动：内容可达
      if (s.textOverflow === 'ellipsis' && !hh) { ellipsisWl++; return; } // 白名单：ellipsis 截断（仅对水平生效）
      fails.push(card.dataset.id + ' > ' + cls(el) + ': scroll=' + el.scrollWidth + 'x' + el.scrollHeight + ' client=' + cw + 'x' + ch + ' text=' + (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 20));
    });
  });
  return JSON.stringify({ cards: cards.length, checked, ellipsisWl, scrollWl, fails: fails.slice(0, 6) });
});
const j = JSON.parse(r);
if (j.fails.length) throw new Error('TEXT_OVERFLOW: ' + j.fails.join(' | ').slice(0, 1500));
return { evidence: 'cards=' + j.cards + ' clip-checked=' + j.checked + ' ellipsis-wl=' + j.ellipsisWl + ' scroll-wl=' + j.scrollWl };
JS

# U2 徽章竖排折行：badge/pill/status/chip 类元素文字跨多行（折行/竖排）→ FAIL
run u2-badge-single-line <<'JS'
const r = await page.evaluate(() => {
  const fails = []; let checked = 0, nowrap = 0;
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  document.querySelectorAll('.image-node *').forEach(el => {
    const c = typeof el.className === 'string' ? el.className : '';
    if (!/(badge|pill|chip|status|tag|count|marker)/i.test(c)) return;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility !== 'visible') return;
    if (el.offsetWidth < 4 || el.offsetHeight < 4) return;
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) return;
    if (s.whiteSpace === 'nowrap') { nowrap++; return; } // 不可能折行
    checked++;
    // 取首末文本节点建 Range：getClientRects 每个行片段一个 rect，>1 即折行/竖排
    const textNodes = [...el.childNodes].filter(n => n.nodeType === 3 && n.nodeValue.trim());
    if (!textNodes.length) return;
    const range = document.createRange();
    range.setStart(textNodes[0], 0);
    range.setEnd(textNodes[textNodes.length - 1], textNodes[textNodes.length - 1].nodeValue.length);
    const rects = range.getClientRects();
    if (rects.length > 1) { fails.push(cls(el) + ': lines=' + rects.length + ' text=' + text.slice(0, 16)); return; }
    if (rects.length === 1 && rects[0].height > 3 * parseFloat(s.fontSize)) fails.push(cls(el) + ': vertical-stack h=' + rects[0].height.toFixed(0) + 'px text=' + text.slice(0, 16));
  });
  return JSON.stringify({ checked, nowrap, fails: fails.slice(0, 6) });
});
const j = JSON.parse(r);
if (j.fails.length) throw new Error('BADGE_WRAP: ' + j.fails.join(' | '));
return { evidence: 'badges=' + j.checked + ' nowrap-skip=' + j.nowrap };
JS

# U3 可点击目标最小命中尺寸：卡片内 button/a 布局尺寸 <20px 宽或高 → FAIL（布局 px，不受画布缩放影响）
run u3-min-hit-size <<'JS'
const r = await page.evaluate(() => {
  const fails = []; let checked = 0;
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  document.querySelectorAll('.image-node button, .image-node a[href]').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility !== 'visible') return;
    const w = el.offsetWidth, h = el.offsetHeight;
    if (w < 4 || h < 4) return; // 零尺寸：PASS-skip
    checked++;
    if (w < 20 || h < 20) fails.push(cls(el) + ': ' + w + 'x' + h + 'px');
  });
  return JSON.stringify({ checked, fails: fails.slice(0, 8) });
});
const j = JSON.parse(r);
if (j.fails.length) throw new Error('MIN_HIT: ' + j.fails.join(' | '));
return { evidence: 'targets=' + j.checked + ' min>=20px OK' };
JS

# U4 纯图标按钮必须有可访问名：button/a 无文字且含图标（lucide/svg/img）→ 需 aria-label/title/alt
run u4-icon-accessible-name <<'JS'
const r = await page.evaluate(() => {
  const fails = []; let checked = 0;
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  document.querySelectorAll('.image-node button, .image-node a[href]').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility !== 'visible') return;
    const text = (el.textContent || '').replace(/[\s\u200b]/g, '');
    const hasIcon = !!el.querySelector('[data-lucide], svg, img');
    if (!hasIcon || text.length) return; // 只查纯图标
    checked++;
    const name = (el.getAttribute('aria-label') || '').trim()
      || (el.getAttribute('title') || '').trim()
      || ((el.querySelector('img') || {}).alt || '').trim();
    if (!name) fails.push(cls(el) + ': ' + el.outerHTML.replace(/\s+/g, ' ').slice(0, 90));
  });
  return JSON.stringify({ checked, fails: fails.slice(0, 8) });
});
const j = JSON.parse(r);
if (j.fails.length) throw new Error('ICON_NO_NAME: ' + j.fails.join(' | '));
return { evidence: 'icon-only=' + j.checked + ' all named' };
JS

# U5 关键控件不重叠不出容器：button/a/input/textarea/select 的 boundingRect 不得超出卡片边界（2px 容差；浮层工具条豁免）
run u5-control-in-bounds <<'JS'
const r = await page.evaluate(() => {
  const TOL = 2;
  const fails = []; let checked = 0, floatingWl = 0;
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  document.querySelectorAll('.image-node button, .image-node a[href], .image-node input, .image-node textarea, .image-node select').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility !== 'visible') return;
    const cr = el.getBoundingClientRect();
    if (cr.width < 4 || cr.height < 4) return;
    const card = el.closest('.image-node');
    if (!card) return;
    if (el.closest('.smart-node-floating-menu')) { floatingWl++; return; } // 选中态浮层工具条：设计上悬浮于卡片上方
    checked++;
    const br = card.getBoundingClientRect();
    const over = [
      ['l', br.left - cr.left], ['r', cr.right - br.right],
      ['t', br.top - cr.top], ['b', cr.bottom - br.bottom]
    ].filter(([, v]) => v > TOL).map(([k, v]) => k + '+' + v.toFixed(1) + 'px');
    if (over.length) fails.push(card.dataset.id + ' ' + cls(el) + ': out ' + over.join(' '));
  });
  return JSON.stringify({ checked, floatingWl, fails: fails.slice(0, 8) });
});
const j = JSON.parse(r);
if (j.fails.length) throw new Error('CONTROL_OUT_OF_BOUNDS: ' + j.fails.join(' | '));
return { evidence: 'controls=' + j.checked + ' floating-wl=' + j.floatingWl + ' all in bounds' };
JS

# 标签卫生：销毁本套件标签，不碰用户标签
"$TABBIT" finish --task "UX Rules QA" --discard >/dev/null 2>&1 || true
echo "== 失败数: $FAILS =="
exit $FAILS
