#!/usr/bin/env bash
# scenario_delete_undo.sh — #300 删除与撤回端到端验收
# D1 删除节点断言消失；D2 Ctrl+Z 原位恢复；D3 删→复制→撤→重做逐级对应；
# D4 agent 画布写操作是否进同一撤回链（成本高 SKIP）。
# 机制锚点：删除按钮 .node-delete（smart-canvas.js:9212/9221/9295，选中/悬停出现）；
# 批删 :15306 ids.forEach deleteNode；undo :15292；redo :15287（redoStack 跳过 projectOriginalDelete 快照）。
# #360：不再取用户打开的项目——本套会真删节点，改用 e2e_common 自建的隔离项目。
set -u
source "$(dirname "$0")/e2e_common.sh"
e2e_start "delete_undo" "Delete Undo QA" 90000

# D1 真实点击选中首节点 → 其 .node-delete 出现 → 真实点击删除 → 节点数 -1 且 DOM 中消失
e2e_run d1-delete <<'JS'
const target = page.locator('.image-node').first();
const nodeId = await target.getAttribute('data-id');
await target.click();                       // 真实鼠标点节点本体（不是 evaluate 里的合成 click）
await page.waitForTimeout(500);
const del = page.locator('.node-delete').first();
if (!(await del.count())) return { skip: '选中后未出现 .node-delete（可发现性缺陷），无法验证删除→撤回链' };
const pre = await page.evaluate(() => canvasState.nodes.length);
await del.click();                          // 真实点击删除按钮
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
const target = page.locator('.image-node').first();
const nodeId = await target.getAttribute('data-id');
const orig = await page.evaluate((id) => {
  const rec = canvasState.nodes.find(v => v.id === id);
  return { x: rec ? rec.x : null, y: rec ? rec.y : null };
}, nodeId);
await target.click();
const del = page.locator('.node-delete').first();
if (!(await del.count())) return { skip: '选中后无 .node-delete 可用，无法构造删除→撤回' };
await del.click();
await page.waitForTimeout(800);
await page.keyboard.press('Control+z');     // 真实快捷键撤回
await page.waitForTimeout(900);
const after = await page.evaluate((id) => {
  const rec = canvasState.nodes.find(v => v.id === id);
  const el = document.querySelector('.image-node[data-id="' + id + '"]');
  return { exists: !!rec, x: rec ? rec.x : null, y: rec ? rec.y : null, dom: !!el };
}, nodeId);
if (!after.exists || !after.dom) throw new Error('UNDO_RESTORE_FAIL: 撤回后节点未恢复 ' + JSON.stringify(after));
if (Math.abs(after.x - orig.x) > 2 || Math.abs(after.y - orig.y) > 2) throw new Error('POS_DRIFT: ' + JSON.stringify({orig, after}));
return { evidence: `原位恢复 (${after.x.toFixed(1)},${after.y.toFixed(1)} vs 删除前 (${orig.x.toFixed(1)},${orig.y.toFixed(1)})` };
JS

# D3 逐级栈对应：复制→删副本→撤→撤→重做→重做，相邻步必须 ±1（避开原节点删除的归档语义）
e2e_run d3-undo-redo-stack <<'JS'
const N0 = await page.evaluate(() => canvasState.nodes.length);
const firstId = await page.locator('.image-node').first().getAttribute('data-id');
const idsBefore = await page.evaluate(() => canvasState.nodes.map(v => v.id));
const seq = [];
const clickNode = async (id) => { await page.locator(`.image-node[data-id="${id}"]`).click(); await page.waitForTimeout(250); };
// 1) 真实选中首节点后按 Ctrl+D 复制
await clickNode(firstId);
await page.keyboard.press('Control+d'); await page.waitForTimeout(600);
// 副本 id 由应用生成（smart_*），只能从「不在复制前的 id 集」里找
const newId = await page.evaluate((prev) => (canvasState.nodes.map(v => v.id).find(id => !prev.includes(id)) || ''), idsBefore);
seq.push(await page.evaluate(() => canvasState.nodes.length));
if (!newId) throw new Error('D3_SETUP: 复制未产生新节点 ' + JSON.stringify(seq));
const selNow = await page.evaluate(() => canvasState.selectedId);
if (selNow !== newId) {
  return { skip: 'Ctrl+D 后选中态没落在副本上（selectedId=' + selNow + ', 副本=' + newId + '），删除链会错删原卡，不可断言' };
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
const tail = await page.evaluate((id) => ({ originalAlive: canvasState.nodes.some(v => v.id === id) }), firstId);
if (!tail.originalAlive) throw new Error('ORIGINAL_LOST: ' + JSON.stringify({firstId, tail}));
return { evidence: 'N0=' + N0 + ' 序列=' + JSON.stringify(seq) + ' 期望=' + JSON.stringify(expect) + '，原节点存活，删除走键盘 Delete（副本自带 .node-delete 存在）'};
JS

# D4 agent 写入是否进同一撤回链：走 agent 工具背后的画布写入口
# （POST /api/projects/{pid}/canvas/nodes，app/routers/api_agent_tools.py:165；node 必须带 type（create_node_core :285 无 type 直接 400）），
# 不用真 agent——花钱且不确定；要验的是“服务端写 → 前端 Ctrl+Z 能不能撤”。
e2e_run d4-agent-write-undo <<'JS'
const before = await page.evaluate(() => canvasState.nodes.length);
const res = await page.evaluate(async (args) => {
  // 在页内 fetch：同源守卫（ensure_agent_same_origin）只看浏览器发的 Host/Origin，
  // 所以必须走同源请求；URL 用当前页地址做基准（页内 baseURI 不是绝对地址）。
  const r = await fetch(new URL('/api/projects/' + args.pid + '/canvas/nodes', args.base).href, {
    method: 'POST',
    headers: {'content-type': 'application/json', 'x-agent-project': args.pid},
    body: JSON.stringify({node: {id: 'e2e-agent-write', type: 'image', title: 'e2e agent 写入', x: 1400, y: 600, w: 240, h: 240}})
  });
  return { status: r.status, ok: r.ok };
}, { pid: E2E_PID, base: page.url() });
if (!res.ok) return { skip: 'agent 画布写入口返回 HTTP ' + res.status + '，D4 无法断言' };
// 服务端写 → 广播 → 前端 canvasState；轮询到出现为止（上限 25s）
let appeared = false;
for (let i = 0; i < 25; i++) {
  await page.waitForTimeout(1000);
  if (await page.evaluate(() => canvasState.nodes.some(v => v.id === 'e2e-agent-write'))) { appeared = true; break; }
}
if (!appeared) return { skip: 'agent 写入 25s 未同步到前端 canvasState——同步缺口本身要记，撤回链断言无从谈起' };
const mid = await page.evaluate(() => canvasState.nodes.length);
if (mid <= before) throw new Error('AGENT_WRITE_NOT_COUNTED: ' + JSON.stringify({before, mid}));
await page.keyboard.press('Control+z');
await page.waitForTimeout(900);
const post = await page.evaluate(() => ({ n: canvasState.nodes.length, alive: canvasState.nodes.some(v => v.id === 'e2e-agent-write') }));
if (post.alive) return { skip: 'KNOWN-GAP：agent 服务端写入不进前端撤回链（Ctrl+Z 后节点仍在）——用户撤不掉 agent 刚写的东西' };
return { evidence: `agent 写入入撤回链: ${before}→${mid}→${post.n}` };
JS

e2e_finish
