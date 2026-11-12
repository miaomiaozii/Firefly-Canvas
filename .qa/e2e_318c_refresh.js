// #318 票 C 验收：刷新前后消息区重建对比（codex 节点，隔离实例 :8799）。
// 节点用 in-memory push（QA 证据脚本同法）——events.jsonl 在服务端，回放只认
// (project,node) 键，与节点 DOM 来源无关。用法: node .qa/e2e_318c_refresh.js [base] [pid] [nodeId]
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const path = require('path');
const SHOTS = 'D:/Firefly-Canvas/.qa/shots';
const base = process.argv[2] || 'http://127.0.0.1:8799';
const pid = process.argv[3] || '2643441ad90c40eba2b73a32b4c8eb11';
const nodeId = process.argv[4] || 'agent_codex_e2e';
const TOK1 = process.argv[5] || '100', TOK2 = process.argv[6] || '坐标';
const SHOTP = process.argv[7] || 'codex';

async function openWithNode(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(`${base}/static/smart-canvas.html?project_mode=1&id=${pid}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2200);
  await page.evaluate(async ({ nid, type }) => {
    const n = { id: nid, type, x: 120, y: 120, w: 480, h: 420, data: { agentName: '票C回放验收' } };
    canvasState.nodes.push(n);
    render();
    renderAgentNode(n.id);
    centerViewportOnWorldPoint({ x: n.x + n.w / 2, y: n.y + n.h / 2 });
    await new Promise(r => setTimeout(r, 400));
  }, { nid: nodeId, type: 'smart-agent' });
  // 选中节点 → 卡片 WS → session 事件 → GET events 回放
  await page.locator(`[data-id="${nodeId}"]`).click({ position: { x: 30, y: 20 } });
  await page.waitForTimeout(4200);
  const stats = await page.evaluate(({ nid, t1, t2 }) => {
    const card = document.querySelector(`[data-id="${nid}"]`);
    const msgs = card ? card.querySelectorAll('.agent-tui-msg').length : 0;
    const text = card ? (card.innerText || '') : '';
    return { msgs, hasX: text.includes(t1), hasPrompt: text.includes(t2) };
  }, { nid: nodeId, t1: TOK1, t2: TOK2 });
  return { page, errs, stats };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const A = await openWithNode(browser);
  await A.page.locator(`[data-id="${nodeId}"]`).screenshot({ path: path.join(SHOTS, `318c_${SHOTP}_first.png`) });

  await A.page.reload({ waitUntil: 'domcontentloaded' });
  const B = await openWithNode(browser); // reload 后新 page state 全空=刷新等效
  await B.page.locator(`[data-id="${nodeId}"]`).screenshot({ path: path.join(SHOTS, `318c_${SHOTP}_after.png`) });

  console.log(JSON.stringify({ firstOpen: A.stats, afterRefresh: B.stats, errs: B.errs.slice(0, 3) }, null, 1));
  const pass = A.stats.msgs > 0 && B.stats.msgs > 0 && B.stats.hasX && B.stats.hasPrompt;
  console.log('REPLAY_AFTER_RELOAD=' + (pass ? 'PASS' : 'FAIL'));
  await browser.close();
  process.exit(pass ? 0 : 1);
})();
