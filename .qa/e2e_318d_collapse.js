// #322 票 D 验收：工具段折叠「已执行 N 步 · 总 X.Xs」真机 DOM 断言 + 折叠/展开像素对。
// 手法：QA capture_agent_shots.js 同款——临时 agent 节点（id 对齐 d3-* 真事件日志），
// 走真 WS + 票 C 事件回放链路重建 UI（不碰服务端持久化）。
// 对比层声明：步数/耗时/结构断言=DOM 级（本脚本硬断言）；两张截图=像素级辅助
// （折叠 summary 条 vs 展开子卡+耗时，肉眼可辨；脚本校验两图字节不同）。
// checklist 说明：pi 宿主无 todo 工具不发 todo_list 事件——checklist 分支由
// tests/stubs/fake_codex.py（codex 宿主真词汇）+ mjs 单测断言覆盖，像素不造假。
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const crypto = require('crypto');
const fs = require('fs');
const BASE = process.env.BASE || 'http://127.0.0.1:8791';
const PID = process.argv[2] || '004b512038e440d6a9e38f171b9ccfed';
const NODE = process.argv[3] || 'd3-1794528753';
const SHOT = n => `D:/Firefly-Canvas/.qa/shots/318d_${n}.png`;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/static/smart-canvas.html?project_mode=1&id=${PID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2200);
  await page.evaluate(({ nid, w, h }) => {
    if (!canvasState.nodes.some(n => n.id === nid)) {
      canvasState.nodes.push({ id: nid, type: NODE_TYPE_AGENT, x: 160, y: 160, w, h, data: { agentName: '322D-e2e' } });
    }
    render();
    renderAgentNode(nid);
    const n = canvasState.nodes.find(x => x.id === nid);
    centerViewportOnWorldPoint({ x: n.x + w / 2, y: n.y + h / 2 });
  }, { nid: NODE, w: 460, h: 460 });
  // 真 WS → session 确认 → 票 C 回放拉取 d3 事件（含 duration_ms）
  const group = page.locator(`[data-id="${NODE}"] details.agent-tui-tool-group`).first();
  await group.waitFor({ timeout: 20000 });
  const summary = (await group.locator('summary').innerText()).replace(/\s+/g, ' ');
  if (!/已执行 \d+ 步 · 总 [\d.]+s/.test(summary)) throw new Error('summary 缺步数或耗时: ' + summary);
  if (await group.evaluate(el => el.open)) throw new Error('默认不应展开');
  console.log('DOM_ASSERT summary=' + JSON.stringify(summary));
  await page.locator(`[data-id="${NODE}"]`).screenshot({ path: SHOT('collapsed') });
  await group.locator('summary').click();
  if (!await group.evaluate(el => el.open)) throw new Error('点击后未展开');
  const cards = await page.locator(`[data-id="${NODE}"] .agent-tui-tool-group[open] .agent-tui-tool`).count();
  if (cards < 1) throw new Error('展开后无子卡');
  await page.locator(`[data-id="${NODE}"]`).screenshot({ path: SHOT('expanded') });
  const md5 = f => crypto.createHash('md5').update(fs.readFileSync(f)).digest('hex');
  if (md5(SHOT('collapsed')) === md5(SHOT('expanded'))) throw new Error('两图字节相同（监理证据规不收）');
  console.log('PASS cards=' + cards + ' md5=' + md5(SHOT('collapsed')) + '/' + md5(SHOT('expanded')));
  await browser.close();
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
