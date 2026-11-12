// 318 票 A 验收证据：隔离实例 :8791（含 PiAdapter 重构的工作区代码）上，
// 真实 UI 路径创建 Agent 节点 → 输入 prompt → 等真实 pi 回复 → 节点截图。
// 用法: node .qa/capture_agent_ticketA.js
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const SHOTS = 'D:/Firefly-Canvas/.qa/shots';
const BASE = 'http://127.0.0.1:8791';
const PID = '004b512038e440d6a9e38f171b9ccfed';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message));
  await page.goto(`${BASE}/static/smart-canvas.html?project_mode=1&project=${PID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);

  // 真实 UI 路径：右键画布 → 创建菜单 → Agent 卡片
  await page.mouse.click(760, 430, { button: 'right' });
  await page.waitForSelector('.create-menu [data-create-type="agent"]', { timeout: 5000 });
  await page.click('.create-menu [data-create-type="agent"]');
  await page.waitForTimeout(800);

  const node = page.locator('.image-node[data-id]').last();
  const nodeId = await node.getAttribute('data-id');
  console.log('NODE_ID:', nodeId);
  await node.click({ position: { x: 40, y: 30 } });
  await page.waitForSelector('[data-agent-tui-input]', { timeout: 5000 });

  const input = page.locator('[data-agent-tui-input]');
  await input.fill('E2E-PROBE(票A): 只回复两个字：收到。不要调用任何工具。');
  await input.press('Enter');

  // 等真实回复（pi 经 PiAdapter spawn）
  let got = false;
  for (let i = 0; i < 60; i++) {
    const txt = await page.locator(`[data-id="${nodeId}"]`).innerText();
    if (txt.includes('收到') && txt.includes('E2E-PROBE')) { got = true; break; }
    await page.waitForTimeout(1000);
  }
  console.log('REPLY_SEEN:', got);
  await page.waitForTimeout(800);
  await page.locator(`[data-id="${nodeId}"]`).screenshot({ path: `${SHOTS}/318A_agent_pi_round.png` });
  await page.screenshot({ path: `${SHOTS}/318A_agent_pi_round_full.png` });
  console.log('SHOTS: 318A_agent_pi_round.png / 318A_agent_pi_round_full.png');

  // 回写清理：删掉探针节点（避免污染 scratch 项目——scratch 本身待裁决）
  await browser.close();
})().catch((e) => { console.error('FAIL:', e.message); process.exit(1); });
