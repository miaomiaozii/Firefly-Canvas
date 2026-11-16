#!/usr/bin/env bash
# scenario_ux_rules.sh — #301 体验规则回归（文字溢出 / 徽章折行 / 命中尺寸 / 图标可访问名 / 控件出界）
# 用法：bash tools/e2e/scenario_ux_rules.sh （需应用运行；项目由夹具自建自删）
# 机制锚点：卡片 .image-node（canvas-nodes.js:142）；卡片内控件 .smart-node-floating-menu 等；
# 折行检测：Range.getClientRects() 每个行片段一个 rect（>1 = 折行/竖排）。
# #360：不再 inspect 用户打开的项目——inspect 结果取决于画布上有什么，借用户画布就不可复现。
# 夹具种子给出确定画布；要看真项目：FIREFLY_E2E_PROJECT=<id> 只读跑（不建不删）。
set -u
source "$(dirname "$0")/e2e_common.sh"
e2e_start "ux_rules" "UX Rules QA" 90013

# U1 文字溢出/截断：卡片内被 overflow:hidden 裁剪（水平或垂直）且无 ellipsis 豁免、不可滚动的文字
e2e_run u1-text-overflow <<'JS'
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
      if (cw < 4 || ch < 4) return; // 零尺寸盒：跳过
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
e2e_run u2-badge-single-line <<'JS'
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
e2e_run u3-min-hit-size <<'JS'
const r = await page.evaluate(() => {
  const fails = []; let checked = 0;
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  document.querySelectorAll('.image-node button, .image-node a[href]').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility !== 'visible') return;
    const w = el.offsetWidth, h = el.offsetHeight;
    if (w < 4 || h < 4) return; // 零尺寸：跳过
    checked++;
    if (w < 20 || h < 20) fails.push(cls(el) + ': ' + w + 'x' + h + 'px');
  });
  return JSON.stringify({ checked, fails: fails.slice(0, 8) });
});
const j = JSON.parse(r);
if (j.fails.length) throw new Error('MIN_HIT: ' + j.fails.join(' | '));
return { evidence: 'targets=' + j.checked + ' min>=20px OK' };
JS

# U4 纯图标按钮必须有可访问名：button/a 无文字且含图标（svg/img）→ 需 aria-label/title/alt
e2e_run u4-icon-accessible-name <<'JS'
const r = await page.evaluate(() => {
  const fails = []; let checked = 0;
  const cls = el => (typeof el.className === 'string' ? el.className : '').split(' ').filter(Boolean).slice(0, 2).join('.') || el.tagName.toLowerCase();
  document.querySelectorAll('.image-node button, .image-node a[href]').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility !== 'visible') return;
    const text = (el.textContent || '').replace(/[\s\u200b]/g, '');
    const hasIcon = !!el.querySelector('svg, img');
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
e2e_run u5-control-in-bounds <<'JS'
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

# U6 空画布也要有可点控件：种子节点上悬停/选中后卡片内控件仍在界内（保证夹具本身有覆盖面）
e2e_run u6-controls-present-on-seed <<'JS'
const first = page.locator('.image-node').first();
await first.click();
await page.waitForTimeout(500);
const seen = await page.evaluate(() => {
  const card = document.querySelector('.image-node.selected, .image-node');
  return {
    card: !!card,
    controls: card ? [...card.querySelectorAll('button, a[href]')].length : 0,
    selected: !!document.querySelector('.image-node.selected'),
  };
});
if (!seen.card) return { skip: '种子节点未渲染成卡片，覆盖面无从谈起' };
if (!seen.selected) return { skip: '真实点击后节点未进入 selected 态，卡片控件覆盖面未验证' };
if (!seen.controls) throw new Error('NO_CONTROLS_ON_SELECTED_SEED: ' + JSON.stringify(seen));
return { evidence: '选中态卡片控件数=' + seen.controls + '（U3/U4/U5 在这个覆盖面之上才有意义）' };
JS

e2e_finish
