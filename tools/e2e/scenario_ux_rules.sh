#!/usr/bin/env bash
# scenario_ux_rules.sh — #301 体验规则回归（文字溢出 / 徽章折行 / 命中尺寸 / 图标可访问名 / 控件出界）
# 用法：bash tools/e2e/scenario_ux_rules.sh （需应用运行；项目由夹具自建自删）
# 机制锚点：卡片 .image-node（canvas-nodes.js:142）；卡片内控件 .smart-node-floating-menu 等；
# 折行检测：Range.getClientRects() 每个行片段一个 rect（>1 = 折行/竖排）。
# #360：只检查 e2e_common 的隔离种子画布。
set -u
source "$(dirname "$0")/e2e_common.sh"
e2e_start "ux_rules" "UX Rules QA" 25000

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
if (!j.checked && !j.nowrap) return {skip: 'KNOWN-GAP: 种子卡未渲染徽章，未验证徽章折行'};
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

# U6：确认真实选中的种子卡上有可见、可命中、没有越界的关键控件，避免空覆盖。
e2e_run u6-controls-present-on-seed <<'JS'
await selectImageCard(E2E_NODE_ID);
await showEmptyCardDelete(E2E_NODE_ID);
const seen = await page.evaluate(id => {
  const card = document.querySelector(`.image-node.selected[data-id="${id}"]`);
  if (!card) return {selected: false, controls: []};
  const bounds = card.getBoundingClientRect();
  const controls = [...card.querySelectorAll('button, a[href]')].filter(el => !el.closest('.smart-node-floating-menu')).map(el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const x = (Math.max(r.left, 0) + Math.min(r.right, innerWidth)) / 2;
    const y = (Math.max(r.top, 0) + Math.min(r.bottom, innerHeight)) / 2;
    return {
      cls: el.className,
      hit: !el.disabled && r.width > 0 && r.height > 0 && s.visibility === 'visible'
        && s.opacity !== '0' && el.contains(document.elementFromPoint(x, y)),
      inBounds: r.left >= bounds.left - 2 && r.right <= bounds.right + 2
        && r.top >= bounds.top - 2 && r.bottom <= bounds.bottom + 2,
    };
  }).filter(control => control.hit);
  return {selected: canvasState.selectedId === id, controls};
}, E2E_NODE_ID);
if (!seen.selected || !seen.controls.length || seen.controls.some(control => !control.inBounds)) {
  throw new Error('SELECTED_SEED_CONTROLS: ' + JSON.stringify(seen));
}
return { evidence: '选中种子卡可命中且未越界的控件数=' + seen.controls.length };
JS

e2e_finish
