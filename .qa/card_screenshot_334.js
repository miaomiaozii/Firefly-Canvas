// #334 双证补拍：respawn 后在节点卡片内真实问答 + 截图（监理验收钉：同卡片问、答对、截图+events 片段）
const { chromium } = require('playwright');

const PID = 'a8f7b9d3097f4c649aefbdf80cc58817';
const URL = `http://127.0.0.1:8791/static/smart-canvas.html?id=${PID}&project=${PID}&project_mode=1`;
const NODES = [
  { id: 'p334-i', word: 'DURIAN' },
  { id: 'p334-j', word: 'PAPAYA' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  for (const n of NODES) {
    const input = page.locator(`[data-agent-tui-input="${n.id}"]`);
    await input.waitFor({ timeout: 15000 });
    await input.scrollIntoViewIfNeeded();
    await input.click();
    await input.fill('之前让你记住的暗号是什么？只回答那个词。');
    await input.press('Enter');
    const msg = page.locator(`[data-agent-tui-messages="${n.id}"]`);
    const t0 = Date.now();
    let ok = false;
    while (Date.now() - t0 < 180000) {
      const txt = await msg.innerText().catch(() => '');
      if (txt.toUpperCase().includes(n.word)) { ok = true; break; }
      await page.waitForTimeout(2000);
    }
    const card = page.locator(`[data-agent-tui-input="${n.id}"]`).locator('xpath=ancestor::*[contains(@class,"agent-tui-card")][1]');
    const shot = `D:/Firefly-Canvas/.qa/shots/334_${n.id}_card_qa.png`;
    try { await (await card.elementHandle()).screenshot({ path: shot }); }
    catch { await page.screenshot({ path: shot }); }
    console.log('CARD-QA', n.id, ok ? 'PASS' : 'FAIL', 'shot=' + shot);
  }
  console.log('PAGEERRORS', errors.length, errors.slice(0, 3));
  await browser.close();
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
