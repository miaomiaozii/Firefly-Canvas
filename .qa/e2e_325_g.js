// #325 票 G E2E：agent 卡头部「由外部 CLI 接管」按钮 → dialog 含两条命令与复制按钮。
// 前置：:8791@当前树 运行中，项目 ceceaa41388e435b8c6407f6333b5823 已含 g_demo_1 节点（脚本内幂等重建）。
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const PID = process.argv[2] || 'ceceaa41388e435b8c6407f6333b5823';
const BASE = 'http://127.0.0.1:8791';

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.goto(`${BASE}/static/smart-canvas.html?id=${PID}&project=${PID}&project_mode=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  const btn = await page.$('.agent-tui-btn-cli');
  console.log('btn-found:', Boolean(btn));
  if (!btn) { await page.screenshot({ path: 'D:/Firefly-Canvas/.qa/shots/325_g_btn_missing.png' }); await browser.close(); process.exit(1); }

  await btn.click();
  await page.waitForSelector('.agent-cli-dialog', { timeout: 5000 });
  const codexText = await page.$eval('.agent-cli-codex-cmd', el => el.value);
  const claudeText = await page.$eval('.agent-cli-claude-cmd', el => el.value);
  console.log('codex-cmd:', codexText.slice(0, 80));
  console.log('claude-cmd:', claudeText.slice(0, 80));
  console.log('bind-node-in-url:', codexText.includes('node=g_demo_1'));
  const copyBtns = await page.$$('[data-agent-cli-copy]');
  console.log('copy-buttons:', copyBtns.length);
  await copyBtns[0].click();
  await page.waitForTimeout(300);
  console.log('copy-feedback:', await copyBtns[0].textContent());
  await page.screenshot({ path: 'D:/Firefly-Canvas/.qa/shots/325_g_takeover_dialog.png' });
  console.log('pageerrors:', errs.length ? errs.join(' | ') : 'none');
  await browser.close();
})();
