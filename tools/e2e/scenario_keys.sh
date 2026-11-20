#!/usr/bin/env bash
# scenario_keys.sh — #298 快捷键与展开/收起端到端验收
# K1 帮助面板公示键位清单；K2 逐键实测行为断言；K3 输入框内吞键差异；K4 面板展开/收起两次切换。
# 单跑：python tests/tabbit_e2e_evidence.py --suite tools/e2e/scenario_keys.sh
set -u
source "$(dirname "$0")/e2e_common.sh"
e2e_start "keys" "Keys QA" 25000

# K1 帮助面板：打开后必须可见，公示的键位必须包含实测的快捷键。
e2e_run k1-help-panel <<'JS'
await clickAt(page.locator('#smartShortcutToggle'));
await page.waitForSelector('#smartShortcutModal.open .shortcut-item kbd', {state: 'visible', timeout: 2500});
const open1 = await page.evaluate(() => ({
  expanded: document.getElementById('smartShortcutToggle')?.getAttribute('aria-expanded'),
  rows: [...document.querySelectorAll('#smartShortcutModal .shortcut-item')].map(row =>
    [...row.querySelectorAll('kbd')].map(k => k.textContent.trim()).join('+')),
}));
if (open1.expanded !== 'true') throw new Error('HELP_NOT_OPEN: ' + JSON.stringify(open1));
const missing = ['Ctrl+Z', 'Ctrl+Shift+Z', 'Z', 'Ctrl+A', 'Ctrl+D'].filter(key => !open1.rows.includes(key));
if (missing.length) throw new Error('HELP_KEYS_MISSING: ' + missing.join(', '));
return { evidence: 'panel open, listed shortcuts=' + open1.rows.length };
JS

# K2a Ctrl+A 全选：每个节点都要选中。
e2e_run k2a-ctrl-a <<'JS'
await selectImageCard(E2E_NODE_ID);
const pre = await page.evaluate(() => ({ n: canvasState.nodes.length, sel: canvasState.selectedIds.length }));
if (pre.n < 2 || pre.sel >= pre.n) throw new Error('SELECTALL_SETUP: ' + JSON.stringify(pre));
await page.keyboard.press('Control+a');
const post = await page.evaluate(() => ({ selected: [...canvasState.selectedIds].sort(), nodes: canvasState.nodes.map(n => n.id).sort() }));
if (post.nodes.length !== pre.n || JSON.stringify(post.selected) !== JSON.stringify(post.nodes)) {
  throw new Error('SELECTALL_FAIL: ' + JSON.stringify({pre, post}));
}
return { evidence: `nodes=${post.nodes.length} selected=${post.selected.length}` };
JS

# K2b Ctrl+D 复制 → 节点+1；随后 Ctrl+Z 撤回 → 还原（同时实测撤回键）
e2e_run k2b-duplicate-undo <<'JS'
const pre = await page.evaluate(() => canvasState.nodes.length);
await selectImageCard(E2E_NODE_ID);
await page.keyboard.press('Control+d');
await page.waitForTimeout(700);
const mid = await page.evaluate(() => canvasState.nodes.length);
if (mid !== pre + 1) throw new Error('DUPLICATE_FAIL: ' + pre + '->' + mid);
await page.keyboard.press('Control+z');
await page.waitForTimeout(700);
const post = await page.evaluate(() => canvasState.nodes.length);
if (post !== pre) throw new Error('UNDO_FAIL: ' + mid + '->' + post + ' (expect ' + pre + ')');
return { evidence: `nodes ${pre}→${mid}→${post} (Ctrl+D 复制, Ctrl+Z 撤回)` };
JS

# K2c 裸 z：smart-canvas.js 的 enter/exitZoomPreview 用 #shell.zoom-preview 表示状态。
e2e_run k2c-zoom-preview-toggle <<'JS'
const zooming = () => page.evaluate(() => document.getElementById('shell').classList.contains('zoom-preview'));
const initial = await zooming();
await page.keyboard.press('z');
const first = await zooming();
await page.keyboard.press('z');
const second = await zooming();
if (initial || !first || second) throw new Error('ZOOM_PREVIEW_TOGGLE: ' + JSON.stringify({initial, first, second}));
return { evidence: 'z: zoom-preview off→on→off' };
JS

# K2d Ctrl+Shift+Z 重做：复制、撤回、重做的每一步都必须改变节点数。
e2e_run k2d-redo <<'JS'
const count = () => page.evaluate(() => canvasState.nodes.length);
const pre = await count();
await clickNode(E2E_NODE_ID);
await page.keyboard.press('Control+d');
const duplicated = await count();
if (duplicated !== pre + 1) throw new Error('SETUP_DUPLICATE_FAIL: ' + duplicated);
await page.keyboard.press('Control+z');
const afterUndo = await count();
if (afterUndo !== pre) throw new Error('SETUP_UNDO_FAIL: ' + pre + '->' + afterUndo);
await page.keyboard.press('Control+Shift+z');
const afterRedo = await count();
if (afterRedo !== pre + 1) throw new Error('REDO_FAIL: ' + afterUndo + '->' + afterRedo + ' (expect ' + (pre+1) + ')');
await page.keyboard.press('Control+z');
if (await count() !== pre) throw new Error('UNDO_AFTER_REDO_FAIL');
return { evidence: `undo=${afterUndo} redo=${afterRedo} (Ctrl+Shift+Z 生效)` };
JS

# K3：先造出可撤回的复制，再验证输入框里的 Ctrl+Z 不穿透（#303）和 z 正常输入。
e2e_run k3-input-swallow <<'JS'
const seedCount = await page.evaluate(() => canvasState.nodes.length);
await selectImageCard(E2E_NODE_ID);
await page.keyboard.press('Control+d');
await page.waitForFunction(n => canvasState.nodes.length === n + 1, seedCount, {timeout: 3000});
await selectImageCard(E2E_NODE_ID);
// 用 Playwright 原生鼠标点击，等入场动画与重排结束后才命中可编辑区域。
await page.locator('#promptInput[contenteditable="true"]').click({timeout: 4000});
await page.waitForFunction(() => document.getElementById('promptInput').contains(document.activeElement),
  null, {timeout: 3000});
const snap = () => page.evaluate(() => {
  const input = document.getElementById('promptInput');
  return {
    n: canvasState.nodes.length,
    selected: [canvasState.selectedId, ...canvasState.selectedIds],
    zoom: document.getElementById('shell').classList.contains('zoom-preview'),
    text: input.innerText,
    focused: input.contains(document.activeElement),
    undoReady: !document.getElementById('smartUndoButton').disabled,
  };
});
const pre = await snap();
if (!pre.focused || !pre.undoReady || pre.n !== seedCount + 1) throw new Error('INPUT_SETUP: ' + JSON.stringify(pre));
// 先测试无文本编辑时的 Ctrl+Z，避免浏览器把刚键入的字符正常撤掉而误报。
await page.keyboard.press('Control+z');
const afterUndo = await snap();
await page.keyboard.press('z');
await page.keyboard.press('z');
const post = await snap();
for (const s of [afterUndo, post]) {
  if (!s.focused || s.n !== pre.n || !s.undoReady || s.zoom !== pre.zoom || JSON.stringify(s.selected) !== JSON.stringify(pre.selected)) {
    throw new Error('INPUT_KEY_LEAK: ' + JSON.stringify({pre, afterUndo, post}));
  }
}
if (afterUndo.text !== pre.text || post.text !== pre.text + 'zz') throw new Error('INPUT_TEXT: ' + JSON.stringify({pre, afterUndo, post}));
await page.keyboard.press('Tab');
if ((await snap()).focused) throw new Error('INPUT_TAB_DID_NOT_LEAVE');
return { evidence: '#promptInput 接收 zz，Ctrl+Z 不撤销画布复制，Tab 离开输入框' };
JS

# K4：同一开关须能打开、收起并再次打开；不能只断言模态框存在。
e2e_run k4-panel-collapse <<'JS'
const toggle = page.locator('#smartShortcutToggle');
for (const open of [true, false, true]) {
  await clickAt(toggle);
  await page.waitForSelector('#smartShortcutModal', {state: open ? 'visible' : 'hidden', timeout: 2500});
  const state = await page.evaluate(() => ({
    modal: document.getElementById('smartShortcutModal').classList.contains('open'),
    shell: document.getElementById('shell').classList.contains('shortcut-panel-open'),
    expanded: document.getElementById('smartShortcutToggle').getAttribute('aria-expanded'),
  }));
  if (state.modal !== open || state.shell !== open || state.expanded !== String(open)) {
    throw new Error('HELP_TOGGLE_STATE: ' + JSON.stringify({open, state}));
  }
}
return { evidence: '同一开关 展开→收起→再次展开' };
JS

e2e_finish
