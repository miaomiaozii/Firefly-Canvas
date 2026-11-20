#!/usr/bin/env bash
# scenario_delete_undo.sh — #300 删除与撤回端到端验收
# D1 删除节点断言消失；D2 Ctrl+Z 原位恢复；D3 删→复制→撤→重做逐级对应；
# D4 agent 画布写操作进同一撤回链并落盘（#375 已修，硬断言）。
# D6/D6B 生成中撤占位卡：只允许隔离的本地假服务商，任务终态写回不得复活卡片。
set -u
source "$(dirname "$0")/e2e_common.sh"
export E2E_AGENT_NODE=1
e2e_start "delete_undo" "Delete Undo QA" 60000

# D1 真实点击选中首节点 → 其 .node-delete 出现 → 真实点击删除 → 节点数 -1 且 DOM 中消失
e2e_run d1-delete <<'JS'
const nodeId = E2E_NODE_ID;
const node = `.image-node[data-id="${nodeId}"]`;
await selectImageCard(nodeId);
await showEmptyCardDelete(nodeId);
const pre = await page.evaluate(() => canvasState.nodes.length);
await clickAt(page.locator(`${node}.empty-node.selected .node-head .node-delete, ${node}.selected .floating-node-actions .node-delete`));
await page.waitForTimeout(800);
const post = await page.evaluate((id) => ({
  n: canvasState.nodes.length,
  gone: !canvasState.nodes.find(v => v.id === id) && !document.querySelector('.image-node[data-id="' + id + '"]')
}), nodeId);
if (post.n !== pre - 1 || !post.gone) throw new Error('DELETE_FAIL: ' + JSON.stringify({pre, post}));
return { evidence: `nodes ${pre}→${post.n}, 节点 ${nodeId} DOM 已移除` };
JS

# D2 Ctrl+Z 原位恢复：节点数还原 + nodeRect 位置与删除前一致（容差 2px）
e2e_run d2-undo-restore <<'JS'
const nodeId = E2E_NODE_ID;
const node = `.image-node[data-id="${nodeId}"]`;
const probe = () => page.evaluate((id) => {
  const rec = canvasState.nodes.find(v => v.id === id);
  const el = document.querySelector('.image-node[data-id="' + id + '"]');
  return { n: canvasState.nodes.length, exists: !!rec, x: rec?.x, y: rec?.y, dom: !!el };
}, nodeId);
const orig = await probe();
if (!orig.exists || !orig.dom) throw new Error('D2_SEED_MISSING: ' + JSON.stringify(orig));
await selectImageCard(nodeId);
await showEmptyCardDelete(nodeId);
await clickAt(page.locator(`${node}.empty-node.selected .node-head .node-delete, ${node}.selected .floating-node-actions .node-delete`));
await page.waitForTimeout(700);
const deleted = await probe();
if (deleted.exists || deleted.dom || deleted.n !== orig.n - 1) throw new Error('D2_DELETE_FAIL: ' + JSON.stringify(deleted));
await page.keyboard.press('Control+z');
await page.waitForTimeout(700);
const after = await probe();
if (!after.exists || !after.dom || after.n !== orig.n) throw new Error('UNDO_RESTORE_FAIL: ' + JSON.stringify({orig, after}));
if (Math.abs(after.x - orig.x) > 2 || Math.abs(after.y - orig.y) > 2) throw new Error('POS_DRIFT: ' + JSON.stringify({orig, after}));
return { evidence: `原位恢复 (${after.x.toFixed(1)},${after.y.toFixed(1)} vs 删除前 (${orig.x.toFixed(1)},${orig.y.toFixed(1)})` };
JS

# D3 逐级栈对应：复制→删副本→撤→撤→重做→重做，相邻步必须 ±1（避开原节点删除的归档语义）
e2e_run d3-undo-redo-stack <<'JS'
const N0 = await page.evaluate(() => canvasState.nodes.length);
const firstId = E2E_NODE_ID;
const idsBefore = await page.evaluate(() => canvasState.nodes.map(v => v.id));
const seq = [];
// 1) 真实选中首节点后按 Ctrl+D 复制
await selectImageCard(firstId);
await page.keyboard.press('Control+d'); await page.waitForTimeout(600);
// 副本 id 由应用生成（smart_*），只能从「不在复制前的 id 集」里找
const newId = await page.evaluate((prev) => (canvasState.nodes.map(v => v.id).find(id => !prev.includes(id)) || ''), idsBefore);
seq.push(await page.evaluate(() => canvasState.nodes.length));
if (!newId) throw new Error('D3_SETUP: 复制未产生新节点 ' + JSON.stringify(seq));
const selNow = await page.evaluate(() => canvasState.selectedId);
if (selNow !== newId) {
  throw new Error('D3_DUPLICATE_NOT_SELECTED: selectedId=' + selNow + ' 副本=' + newId);
}
// 2) 删除副本走键盘 Delete（#347 修的就是这条路径）。不用鼠标点卡片删除钮：
//    Ctrl+D 的副本落在原卡 +32/+32，两张 292px 的卡大面积重叠，副本删除钮被原卡压住
//    （探针实测：点 .node-delete 被邻卡 intercept，5s 超时）；而拖开副本再点会给撤回栈
//    多塞一步拖拽，断言就不是「复制→删→撤→重做逐级对应」了。
//    副本的 .node-delete 仍然断言存在——「点错卡/删错卡」这个 bug 类靠它兜。
const delBtn = page.locator('.image-node[data-id="' + newId + '"] .node-delete').first();
if (!(await delBtn.count())) throw new Error('DELETE_BTN_MISSING: 副本 ' + newId + ' 没有自己的删除钮');
await page.keyboard.press('Delete');
await page.waitForTimeout(600);
const gone = await page.evaluate((id) => !canvasState.nodes.some(v => v.id === id), newId);
if (!gone) throw new Error('DELETE_NOOP: 副本 ' + newId + ' 还在（键盘 Delete 没删掉）');
seq.push(await page.evaluate(() => canvasState.nodes.length));
// 3) 撤→撤→重做→重做（真实快捷键）
for (const k of ['Control+z', 'Control+z', 'Control+Shift+z', 'Control+Shift+z']) {
  await page.keyboard.press(k); await page.waitForTimeout(600);
  seq.push(await page.evaluate(() => canvasState.nodes.length));
}
// 期望序列（精确，不是“逐级 ±1”这种弱断言）：
// 复制 N0+1 → 删副本 N0 → 撤删除 N0+1 → 撤复制 N0 → 重做复制 N0+1 → 重做删除 N0
const expect = [N0 + 1, N0, N0 + 1, N0, N0 + 1, N0];
for (let s = 0; s < expect.length; s++) {
  if (seq[s] !== expect[s]) throw new Error(`STACK_STEP_BAD step=${s} got=${seq[s]} expect=${expect[s]} seq=${JSON.stringify(seq)} N0=${N0}`);
}
// 链尾：原始节点还在（删除只针对副本），副本不在
const tail = await page.evaluate(() => canvasState.nodes.map(v => v.id).sort());
if (JSON.stringify(tail) !== JSON.stringify(idsBefore.sort())) throw new Error('D3_TAIL_IDS: ' + JSON.stringify({idsBefore, tail}));
return { evidence: 'N0=' + N0 + ' 序列=' + JSON.stringify(seq) + ' 期望=' + JSON.stringify(expect) + '，原节点存活，删除走键盘 Delete（副本自带 .node-delete 存在）'};
JS

# D4 agent 写入进同一撤回链并真正落盘（#375）：服务端写 → 广播合并后撤回钮必须可用 →
# Ctrl+Z 后节点消失 → 等过 8s 轮询兜底+保存窗口后再查前端/服务端（D4R 另查整页重载）。
# 走 agent 工具背后的画布写入口（POST /api/projects/{pid}/canvas/nodes，
# app/routers/api_agent_tools.py；node 必须带 type），不用真 agent。
# 注：本套必须在隔离 31xx 实例上跑（FIREFLY_BASE + FIREFLY_RUNTIME_ROOT/FIREFLY_PROJECTS_ROOT，
# #329 口径），不碰用户 3000（review 补点 2）。
e2e_run d4-agent-write-undo <<'JS'
// 先把残留撤回栈排空（种子节点无媒体，弹出快照不改变节点集），让 Ctrl+Z 必弹 agent 快照。
for (let i = 0; i < 6; i++) {
  if (await page.evaluate(() => document.getElementById('smartUndoButton')?.disabled ?? true)) break;
  await page.keyboard.press('Control+z'); await page.waitForTimeout(400);
}
const before = await page.evaluate(() => canvasState.nodes.length);
// 外部 actor 写入，浏览器 evaluate 只观察。#343：使用服务端分配的 node_id。
const res = await fixturePost(`/api/projects/${E2E_PID}/canvas/nodes`,
  { node: {type: 'image', title: 'e2e agent 写入', x: 1400, y: 600, w: 240, h: 240} },
  {'x-agent-project': E2E_PID});
if (!res.ok) throw new Error('AGENT_WRITE_ENDPOINT_HTTP_' + res.status + ': ' + JSON.stringify(res.data));
const AGENT_ID = String(res.data?.node_id || '');
if (!AGENT_ID) throw new Error('AGENT_WRITE_NO_NODE_ID');
await page.waitForFunction(id => canvasState.nodes.some(v => v.id === id), AGENT_ID, {timeout: 10000});
const mid = await page.evaluate(() => canvasState.nodes.length);
if (mid !== before + 1) throw new Error('AGENT_WRITE_NOT_COUNTED: ' + JSON.stringify({before, mid}));
// 修复后：agent 合并必须把合并前快照推进撤回栈 → 钮可用（修复前此处即炸，KNOWN-GAP 硬红）
const undoReady = await page.evaluate(() => { const b = document.getElementById('smartUndoButton'); return !!b && !b.disabled; });
if (!undoReady) throw new Error('AGENT_WRITE_NOT_IN_UNDO_CHAIN: 合并后撤回钮仍禁用（agent 写入没进撤回栈）');
await page.keyboard.press('Control+z');
await page.waitForTimeout(900);
const post = await page.evaluate((nid) => ({ n: canvasState.nodes.length, alive: canvasState.nodes.some(v => v.id === nid) }), AGENT_ID);
if (post.alive || post.n !== before) throw new Error('AGENT_UNDO_FAIL: ' + JSON.stringify({before, mid, post}));
// 落盘断言：等过 8s 轮询兜底 + 3s 保存去抖（共 12s）——前端不能被合并带回来，服务端必须已没有它
await page.waitForTimeout(12000);
const settled = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), AGENT_ID);
if (settled) throw new Error('AGENT_UNDO_MERGED_BACK: 12s 后 agent 节点又被合并回前端');
const server = await page.evaluate(async (args) => {
  const r = await fetch('/api/canvases/' + encodeURIComponent(args.pid));
  if (!r.ok) throw new Error('D4_CANVAS_HTTP_' + r.status);
  const j = await r.json();
  return j.canvas.nodes.some(v => v.id === args.nid);
}, { pid: E2E_PID, nid: AGENT_ID });
if (server) throw new Error('AGENT_UNDO_NOT_PERSISTED: 服务端仍有 ' + AGENT_ID);
return { evidence: `agent 写入入撤回链并落盘: ${before}→${mid}→${post.n}，12s 后前端/服务端均无 ${AGENT_ID}` };
JS

# D5 两次 agent 写入（间隔 5s 跨去抖窗口）= 两步撤回，顺序正确；最终同样落盘。
e2e_run d5-agent-write-undo-twice <<'JS'
for (let i = 0; i < 6; i++) {
  if (await page.evaluate(() => document.getElementById('smartUndoButton')?.disabled ?? true)) break;
  await page.keyboard.press('Control+z'); await page.waitForTimeout(400);
}
const N0 = await page.evaluate(() => canvasState.nodes.length);
const postNode = async (title, x) => {
  const res = await fixturePost(`/api/projects/${E2E_PID}/canvas/nodes`,
    { node: {type: 'image', title, x, y: 600, w: 240, h: 240} }, {'x-agent-project': E2E_PID});
  const id = String(res.data?.node_id || '');
  if (![200, 201].includes(res.status) || !id.startsWith('node-')) throw new Error(`AGENT_WRITE_FAIL ${title}: HTTP ${res.status} id=${id}`);
  await page.waitForFunction(nid => canvasState.nodes.some(v => v.id === nid), id, {timeout: 10000});
  return id;
};
const ID_A = await postNode('e2e-agent-a', 1400);
const nA = await page.evaluate(() => canvasState.nodes.length);
if (nA !== N0 + 1) throw new Error('D5_STEP1_COUNT: ' + JSON.stringify({N0, nA}));
await page.waitForTimeout(5000);   // 跨过去抖合并窗口：两次写入必须分属两步
const ID_B = await postNode('e2e-agent-b', 1700);
const nB = await page.evaluate(() => canvasState.nodes.length);
if (nB !== N0 + 2) throw new Error('D5_STEP2_COUNT: ' + JSON.stringify({N0, nA, nB}));
const undoReady = await page.evaluate(() => { const b = document.getElementById('smartUndoButton'); return !!b && !b.disabled; });
if (!undoReady) throw new Error('AGENT_WRITES_NOT_IN_UNDO_CHAIN');
await page.keyboard.press('Control+z'); await page.waitForTimeout(900);
const s1 = await page.evaluate((ids) => ({ n: canvasState.nodes.length,
  a: canvasState.nodes.some(v => v.id === ids[0]), b: canvasState.nodes.some(v => v.id === ids[1]) }), [ID_A, ID_B]);
if (s1.n !== N0 + 1 || !s1.a || s1.b) throw new Error('D5_UNDO1_BAD: 第一次 Ctrl+Z 应只撤走 b ' + JSON.stringify({N0, s1}));
await page.keyboard.press('Control+z'); await page.waitForTimeout(900);
const s2 = await page.evaluate((ids) => ({ n: canvasState.nodes.length,
  a: canvasState.nodes.some(v => v.id === ids[0]), b: canvasState.nodes.some(v => v.id === ids[1]) }), [ID_A, ID_B]);
if (s2.n !== N0 || s2.a || s2.b) throw new Error('D5_UNDO2_BAD: 第二次 Ctrl+Z 应只撤走 a ' + JSON.stringify({N0, s1, s2}));
// 落盘断言（同 D4：等过轮询+保存窗口，再查服务端与刷新后 DOM）
await page.waitForTimeout(12000);
const settled = await page.evaluate((ids) => canvasState.nodes.some(v => v.id === ids[0] || v.id === ids[1]), [ID_A, ID_B]);
if (settled) throw new Error('D5_MERGED_BACK');
const serverAlive = await page.evaluate(async (args) => {
  const r = await fetch('/api/canvases/' + encodeURIComponent(args.pid));
  if (!r.ok) throw new Error('D5_CANVAS_HTTP_' + r.status);
  const j = await r.json();
  return j.canvas.nodes.filter(v => args.ids.includes(v.id)).map(v => v.id);
}, { pid: E2E_PID, ids: [ID_A, ID_B] });
if (serverAlive.length) throw new Error('D5_NOT_PERSISTED: 服务端仍有 ' + JSON.stringify(serverAlive));
return { evidence: '两步撤回序列 N0=' + N0 + ' →b(' + ID_B + ')撤→' + s1.n + '（a ' + ID_A + ' 存活）→a撤→' + s2.n + '，12s 后前端/服务端均无 agent 节点' };
JS

# D4R/D5R 刷新断言（review 补点 1 的「刷新页面仍在」半边）：撤完 agent 写入后
# 整页重新加载，种子的 DOM 完好、agent 节点在 DOM/画布/服务端都不存在。
e2e_run d4r-agent-undo-fresh-load <<'JS'
const res = await fixturePost(`/api/projects/${E2E_PID}/canvas/nodes`,
  { node: {type: 'image', title: 'e2e agent 写入r', x: 1400, y: 600, w: 240, h: 240} },
  {'x-agent-project': E2E_PID});
const id = String(res.data?.node_id || '');
if (!res.ok || !id) throw new Error(`D4R_WRITE_FAIL HTTP ${res.status} id=${id}`);
await page.waitForFunction(nid => canvasState.nodes.some(v => v.id === nid), id, {timeout: 10000});
await page.keyboard.press('Control+z');
await page.waitForTimeout(900);
const gone = await page.evaluate((nid) => !canvasState.nodes.some(v => v.id === nid), id);
if (!gone) throw new Error('D4R_UNDO_FAIL ' + id);
await page.waitForTimeout(12000);   // 轮询兜底 + 保存窗口
const url = page.url().split('#')[0];
await page.goto(url + (url.includes('?') ? '&' : '?') + 'freshload=1', {waitUntil: 'domcontentloaded', timeout: 7000});
await waitForSeedNodes();
const remnant = await page.evaluate((nid) => !!document.querySelector('.image-node[data-id="' + nid + '"]') || canvasState.nodes.some(v => v.id === nid || String(v.title || '').startsWith('e2e agent')), id);
if (remnant) throw new Error('D4R_AGENT_REVIVED_AFTER_REFRESH ' + id);
const serverIds = await page.evaluate(async (pid) => {
  const r = await fetch('/api/canvases/' + encodeURIComponent(pid));
  if (!r.ok) throw new Error('D4R_CANVAS_HTTP_' + r.status);
  const j = await r.json();
  return j.canvas.nodes.map(v => v.id);
}, E2E_PID);
if (serverIds.includes(id)) throw new Error('D4R_SERVER_STILL_HAS ' + id);
return { evidence: '刷新后仍没：写 ' + id + ' →Ctrl+Z→整页重载：种子 DOM 完好，画布/服务端均无 agent 节点（服务端 ids=' + serverIds.length + '）' };
JS

e2e_run d5r-agent-undo-twice-fresh-load <<'JS'
const postNode = async (title, x) => {
  const res = await fixturePost(`/api/projects/${E2E_PID}/canvas/nodes`,
    { node: {type: 'image', title, x, y: 600, w: 240, h: 240} }, {'x-agent-project': E2E_PID});
  const id = String(res.data?.node_id || '');
  if (!res.ok || !id.startsWith('node-')) throw new Error(`D5R_WRITE_FAIL ${title}: HTTP ${res.status} id=${id}`);
  await page.waitForFunction(nid => canvasState.nodes.some(v => v.id === nid), id, {timeout: 10000});
  return id;
};
const ID_A = await postNode('e2e-agent-a-r', 1400);
await page.waitForTimeout(5000);
const ID_B = await postNode('e2e-agent-b-r', 1700);
await page.keyboard.press('Control+z'); await page.waitForTimeout(900);
await page.keyboard.press('Control+z'); await page.waitForTimeout(900);
const cleared = await page.evaluate((ids) => !canvasState.nodes.some(v => ids.includes(v.id)), [ID_A, ID_B]);
if (!cleared) throw new Error('D5R_UNDO_FAIL ' + JSON.stringify({ID_A, ID_B}));
await page.waitForTimeout(12000);
const url = page.url().split('#')[0];
await page.goto(url + (url.includes('?') ? '&' : '?') + 'freshload=1', {waitUntil: 'domcontentloaded', timeout: 7000});
await waitForSeedNodes();
const remnant = await page.evaluate((ids) => ids.some(id => !!document.querySelector('.image-node[data-id="' + id + '"]') || canvasState.nodes.some(v => v.id === id)), [ID_A, ID_B]);
if (remnant) throw new Error('D5R_AGENT_REVIVED_AFTER_REFRESH');
const serverIds = await page.evaluate(async (pid) => {
  const r = await fetch('/api/canvases/' + encodeURIComponent(pid));
  if (!r.ok) throw new Error('D5R_CANVAS_HTTP_' + r.status);
  const j = await r.json();
  return j.canvas.nodes.map(v => v.id);
}, E2E_PID);
if (serverIds.some(id => [ID_A, ID_B].includes(id))) throw new Error('D5R_SERVER_STILL_HAS');
return { evidence: '两步撤回后刷新仍没：' + ID_A + '/' + ID_B + ' 重载后 DOM/画布/服务端全干净' };
JS

# D6/D6B：E2E_LOCAL_IMAGE_FIXTURE 可选；failure/success 分别确认默认生成路由只指向本地假服务商。
# D6 的假服务商要在挂起窗口内可撤，之后才失败。
e2e_run d6-agent-gen-undo-placeholder <<'JS'
if (E2E_OFFLINE || typeof E2E_LOCAL_IMAGE_FIXTURE === 'undefined' || E2E_LOCAL_IMAGE_FIXTURE?.failure !== true) {
  return {skip: 'KNOWN-GAP: 无本地生成失败夹具；未发送生成请求，未验证占位卡终态写回'};
}
for (let i = 0; i < 6; i++) {
  if (await page.evaluate(() => document.getElementById('smartUndoButton')?.disabled ?? true)) break;
  await page.keyboard.press('Control+z'); await page.waitForTimeout(400);
}
const src = await fixturePost(`/api/projects/${E2E_PID}/canvas/nodes`,
  { node: {type: 'image', title: 'e2e d6 源节点', prompt: 'e2e d6 生成占位卡测试', x: 1400, y: 900, w: 240, h: 240} },
  {'x-agent-project': E2E_PID});
const id = String(src.data?.node_id || '');
if (!src.ok || !id) throw new Error(`D6_SRC_NODE_FAIL HTTP ${src.status} id=${id}`);
const seen = nid => page.waitForFunction(nodeId => canvasState.nodes.some(v => v.id === nodeId), nid, {timeout: 10000});
await seen(id);
// 不带 x-agent-project：确认门由 #351/#389 覆盖；本场景验证占位卡写回。
const res = await fixturePost(`/api/projects/${E2E_PID}/generate/trigger`, {node_id: id});
const trig = { task: String(res.data?.task_id || ''), rid: String(res.data?.result_node_id || '') };
if (res.status !== 200 || !trig.task || !trig.rid) throw new Error(`D6_TRIGGER_FAIL HTTP ${res.status} ${JSON.stringify(res.data?.detail || '')}`);
await seen(trig.rid);
// 3) 生成中（任务未终态）撤占位卡
const taskNow = await page.evaluate(async (args) => {
  const r = await fetch('/api/projects/' + encodeURIComponent(args.pid) + '/generate/tasks/' + encodeURIComponent(args.task));
  const j = await r.json().catch(() => ({}));
  return String(j.status || r.status);
}, { pid: E2E_PID, task: trig.task });
if (!['queued', 'running', 'pending'].includes(taskNow)) {
  throw new Error('D6_UNDO_WINDOW_MISSED: status=' + taskNow);
}
await page.keyboard.press('Control+z');
await page.waitForTimeout(900);
const ph = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), trig.rid);
if (ph) throw new Error('D6_UNDO_FAIL: 生成中 Ctrl+Z 没撤掉占位卡 ' + trig.rid);
// 4) 等任务终态（写回发生）；占位卡不得复活（前端+服务端）
let finalStatus = 'unknown';
for (let i = 0; i < 15; i++) {
  const s = await page.evaluate(async (args) => {
    const r = await fetch('/api/projects/' + encodeURIComponent(args.pid) + '/generate/tasks/' + encodeURIComponent(args.task));
    const j = await r.json().catch(() => ({}));
    return String(j.status || r.status);
  }, { pid: E2E_PID, task: trig.task });
  if (['succeeded', 'failed', 'timeout', 'cancelled'].includes(s)) { finalStatus = s; break; }
  await page.waitForTimeout(1000);
}
if (finalStatus === 'unknown') throw new Error('D6_TASK_NOT_TERMINAL: 15s 内任务未终态');
await page.waitForTimeout(3000);   // 终态写回后的广播合并窗口
const revived = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), trig.rid);
if (revived) throw new Error('D6_GHOST_REVIVED: 任务终态（' + finalStatus + '）写回复活了被撤占位卡 ' + trig.rid);
const serverGhost = await page.evaluate(async (args) => {
  const r = await fetch('/api/canvases/' + encodeURIComponent(args.pid));
  if (!r.ok) throw new Error('D6_CANVAS_HTTP_' + r.status);
  const j = await r.json();
  return j.canvas.nodes.filter(v => v.id === args.rid).map(v => v.id);
}, { pid: E2E_PID, rid: trig.rid });
if (serverGhost.length) throw new Error('D6_SERVER_GHOST: 服务端仍有 ' + JSON.stringify(serverGhost) + '（终态=' + finalStatus + '）');
await page.waitForTimeout(9000);   // 再跨一次 8s 轮询兜底
const settled = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), trig.rid);
if (settled) throw new Error('D6_MERGED_BACK_AFTER_POLL');
return { evidence: '生成中撤占位卡不复活：trigger=' + trig.task + ' 终态=' + finalStatus + '，占位卡 ' + trig.rid + ' 撤销后 12s（含终态写回+轮询兜底）前端/服务端均无' };
JS

# D6b 成功路径守卫：占位卡被撤后任务成功完成，写回不得重建幽灵节点。
# 钉住 api_agent_tools.py complete_image_generation(..., require_pending=True)：未带该参数时
# firefly_projects.complete_media_generation 对缺失节点会在 (0,0) 新建幽灵卡并广播。
# 假服务商对含 SUCCESS 的 prompt 挂 8s 后返回合法图（不花钱）。
e2e_run d6b-agent-gen-undo-success-writeback <<'JS'
if (E2E_OFFLINE || typeof E2E_LOCAL_IMAGE_FIXTURE === 'undefined' || E2E_LOCAL_IMAGE_FIXTURE?.success !== true) {
  return {skip: 'KNOWN-GAP: 无本地生成成功夹具；未发送生成请求，未验证成功写回'};
}
for (let i = 0; i < 6; i++) {
  if (await page.evaluate(() => document.getElementById('smartUndoButton')?.disabled ?? true)) break;
  await page.keyboard.press('Control+z'); await page.waitForTimeout(400);
}
const src = await fixturePost(`/api/projects/${E2E_PID}/canvas/nodes`,
  { node: {type: 'image', title: 'e2e d6b 源节点', prompt: 'e2e d6b SUCCESS 写回不复活', x: 1400, y: 1200, w: 240, h: 240} },
  {'x-agent-project': E2E_PID});
const id = String(src.data?.node_id || '');
if (!src.ok || !id) throw new Error(`D6B_SRC_NODE_FAIL HTTP ${src.status} id=${id}`);
const seen = nid => page.waitForFunction(nodeId => canvasState.nodes.some(v => v.id === nodeId), nid, {timeout: 10000});
await seen(id);
const res = await fixturePost(`/api/projects/${E2E_PID}/generate/trigger`, {node_id: id});
const trig = { task: String(res.data?.task_id || ''), rid: String(res.data?.result_node_id || '') };
if (res.status !== 200 || !trig.task || !trig.rid) throw new Error(`D6B_TRIGGER_FAIL HTTP ${res.status} ${JSON.stringify(res.data?.detail || '')}`);
await seen(trig.rid);
await page.keyboard.press('Control+z');
await page.waitForTimeout(900);
const ph = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), trig.rid);
if (ph) throw new Error('D6B_UNDO_FAIL: Ctrl+Z 没撤掉占位卡 ' + trig.rid);
// 等任务成功（假服务商 8s 挂起后返回合法图）
let finalStatus = 'unknown';
let finalError = '';
for (let i = 0; i < 15; i++) {
  const s = await page.evaluate(async (args) => {
    const r = await fetch('/api/projects/' + encodeURIComponent(args.pid) + '/generate/tasks/' + encodeURIComponent(args.task));
    const j = await r.json().catch(() => ({}));
    return { status: String(j.status || r.status), error: String(j.error || '').slice(0, 300) };
  }, { pid: E2E_PID, task: trig.task });
  if (['succeeded', 'failed', 'timeout', 'cancelled'].includes(s.status)) { finalStatus = s.status; finalError = s.error; break; }
  await page.waitForTimeout(1000);
}
if (finalStatus === 'unknown') throw new Error('D6B_TASK_NOT_TERMINAL: 15s 内任务未终态');
// cancelled（目标卡被撤→require_pending/节点存在检查拒写回）与 succeeded 都是合法终局；
// 关键是两者都不得复活幽灵卡（无守卫时 store 会在 (0,0) 新建幽灵节点）。
if (!['succeeded', 'cancelled'].includes(finalStatus)) {
  throw new Error('D6B_UNEXPECTED_TERMINAL: ' + finalStatus + ' ' + finalError);
}
await page.waitForTimeout(3000);   // 成功写回的广播合并窗口
const revived = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), trig.rid);
if (revived) throw new Error('D6B_GHOST_REVIVED: 成功写回复活了被撤占位卡 ' + trig.rid);
const serverGhost = await page.evaluate(async (args) => {
  const r = await fetch('/api/canvases/' + encodeURIComponent(args.pid));
  if (!r.ok) throw new Error('D6B_CANVAS_HTTP_' + r.status);
  const j = await r.json();
  return j.canvas.nodes.filter(v => v.id === args.rid).map(v => v.id);
}, { pid: E2E_PID, rid: trig.rid });
if (serverGhost.length) throw new Error('D6B_SERVER_GHOST: 服务端幽灵 ' + JSON.stringify(serverGhost));
await page.waitForTimeout(9000);
const settled = await page.evaluate((nid) => canvasState.nodes.some(v => v.id === nid), trig.rid);
if (settled) throw new Error('D6B_MERGED_BACK_AFTER_POLL');
return { evidence: '成功写回不复活幽灵卡：trigger=' + trig.task + ' 终态=' + finalStatus + (finalError ? '（' + finalError + '）' : '') + '，占位卡 ' + trig.rid + ' 撤销后 12s 前端/服务端均无（幽灵卡守卫生效）' };
JS

e2e_finish
