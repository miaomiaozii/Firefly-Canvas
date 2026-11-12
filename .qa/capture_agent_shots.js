// #311-#317 证据重拍脚本（监理补救清单五项，2026-11-13）。可重放：
//   依赖：repo 自带 node_modules/playwright；三个 firefly 实例：
//     :3000 用户实例（当前代码态）；
//     :3001 = git worktree 4ed1638^（#312 修复前缺陷态）；
//     :3002 = git worktree 4ed1638（#312 已修、#313/#316 未修的 pre 基线态）。
//   两个 worktree 实例必须带 FIREFLY_PROJECTS_ROOT 指向 scratch 目录（本脚本会在 scratch 库建临时项目，不触用户数据）。
// 产出：.qa/shots/ 下 314_before_discard.png / 314_after_reselect.png / 316_thinking_active.png /
//       03_agent_node_gap_full.png（缺陷态替换）/ 313_pre_4ed1638.png / 316_pre_4ed1638.png
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const path = require('path');
const SHOTS = 'D:/Firefly-Canvas/.qa/shots';

async function openAgentCard(browser, base, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  if (opts.noWs) await ctx.addInitScript(() => { const O = window.WebSocket; window.WebSocket = function (u, ...r) { if (String(u).includes('/agent-nodes/')) return { send() {}, close() {}, onmessage: null, onopen: null, onclose: null, onerror: null, addEventListener() {}, readyState: 3 }; return new O(u, ...r); }; });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  let pid = opts.pid || null;
  if (!pid) {
    await page.goto(base + '/static/smart-canvas.html?project_mode=1', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    pid = await page.evaluate(async () => {
      const r = await fetch('/api/projects', { headers: { accept: 'application/json' } }).then(x => x.json());
      const arr = Array.isArray(r) ? r : (r.projects || []);
      if (arr.length) return arr[0].id;
      const c = await fetch('/api/projects', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'SHOT-CAPTURE-TEMP' }) }).then(x => x.json()).catch(() => null);
      return c && (c.id || (c.project && c.project.id));
    });
  }
  await page.goto(`${base}/static/smart-canvas.html?project_mode=1&project=${pid}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2200);
  const nodeInfo = await page.evaluate(async ({ w, h }) => {
    const n = { id: 'shotcap-' + Date.now(), type: NODE_TYPE_AGENT, x: 120, y: 120, w, h, data: { agentName: '证据采集节点' } };
    canvasState.nodes.push(n);
    render();
    renderAgentNode(n.id);
    centerViewportOnWorldPoint({ x: n.x + w / 2, y: n.y + h / 2 });
    await new Promise(r => setTimeout(r, 500));
    return { id: n.id };
  }, { w: opts.w || 460, h: opts.h || 400 });
  return { page, errs, nodeInfo, pid };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = {};

  // ---- 314: 草稿在「取消选择→重选」前后（当前代码态 :3000）----
  {
    const { page, nodeInfo } = await openAgentCard(browser, 'http://localhost:3000', { noWs: true });
    const input = page.locator('[data-agent-tui-input]');
    await input.click();
    await input.type('这是 314 草稿证据：取消选择前应可见，重选后必须仍在。', { delay: 8 });
    await page.waitForTimeout(300);
    await page.locator(`[data-id="${nodeInfo.id}"]`).screenshot({ path: path.join(SHOTS, '314_before_discard.png') });
    // 取消选择：点画布空白
    await page.mouse.click(20, 900);
    await page.waitForTimeout(600);
    // 重选：点回节点
    await page.locator(`[data-id="${nodeInfo.id}"]`).click({ position: { x: 30, y: 20 } });
    await page.waitForTimeout(700);
    const draft = await page.evaluate(() => { const i = document.querySelector('[data-agent-tui-input]'); return i ? i.value : null; });
    await page.locator(`[data-id="${nodeInfo.id}"]`).screenshot({ path: path.join(SHOTS, '314_after_reselect.png') });
    report.shot314 = { draftAfterReselect: draft, preserved: !!draft };
    await page.close();
  }

  // ---- 316: thinking 激活态前后（两节点单图含两态, :3000）----
  {
    const page = await (await browser.newContext({ viewport: { width: 1500, height: 950 } })).newPage();
    await page.addInitScript(() => { const O = window.WebSocket; window.WebSocket = function (u, ...r) { if (String(u).includes('/agent-nodes/')) return { send() {}, close() {}, onmessage: null, onopen: null, onclose: null, onerror: null, addEventListener() {}, readyState: 3 }; return new O(u, ...r); }; });
    await page.goto('http://localhost:3000/static/smart-canvas.html?project_mode=1&project=2c92ddd4e34c4345a8560e389d41b580', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    const ids = await page.evaluate(async () => {
      const mk = (id, x) => { const n = { id, type: NODE_TYPE_AGENT, x, y: 150, w: 420, h: 380, data: { agentName: id } }; canvasState.nodes.push(n); };
      mk('shotcap-off', 100); mk('shotcap-on', 620);
      render(); renderAgentNode('shotcap-off'); renderAgentNode('shotcap-on');
      centerViewportOnWorldPoint({ x: 520, y: 340 });
      await new Promise(r => setTimeout(r, 500));
      return ['shotcap-off', 'shotcap-on'];
    });
    await page.locator('[data-id=shotcap-on] [data-agent-thinking]').click();
    await page.waitForTimeout(500);
    const states = await page.evaluate(() => ({
      off: document.querySelector('[data-id=shotcap-off] [data-agent-thinking]')?.getAttribute('aria-pressed'),
      on: document.querySelector('[data-id=shotcap-on] [data-agent-thinking]')?.getAttribute('aria-pressed'),
      activeCls: !!document.querySelector('[data-id=shotcap-on] .agent-tui-btn-active'),
    }));
    await page.screenshot({ path: path.join(SHOTS, '316_thinking_active.png') });
    report.shot316 = { states, ids };
    await page.close();
  }

  // ---- #312 缺陷态（:3001 = 4ed1638^，卡片不铺满）----
  {
    // 旧版 stream 端点会在 WS 连接时自动 spawn——禁用 WebSocket 避免拉起进程
    const ctxOpts = { viewport: { width: 1500, height: 950 } };
    const noWs = async (p) => p.addInitScript(() => { const O = window.WebSocket; window.WebSocket = function (u, ...r) { if (String(u).includes('/agent-nodes/')) return { send() {}, close() {}, onmessage: null, onopen: null, onclose: null, onerror: null, addEventListener() {}, readyState: 3 }; return new O(u, ...r); }; });
    const c1 = await browser.newContext(ctxOpts); await noWs(c1);
    const page = await c1.newPage();
    const nodeInfo = await (async () => {
      await page.goto('http://localhost:3001/static/smart-canvas.html?project_mode=1', { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1200);
      const pid = await page.evaluate(async () => {
        const r = await fetch('/api/projects', { headers: { accept: 'application/json' } }).then(x => x.json());
        const arr = Array.isArray(r) ? r : (r.projects || []);
        if (arr.length) return arr[0].id;
        const c = await fetch('/api/projects', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'SHOT-CAPTURE-TEMP' }) }).then(x => x.json());
        return c && (c.id || (c.project && c.project.id));
      });
      await page.goto(`http://localhost:3001/static/smart-canvas.html?project_mode=1&project=${pid}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2000);
      return page.evaluate(async () => {
        const n = { id: 'shotcap312', type: NODE_TYPE_AGENT, x: 120, y: 120, w: 806, h: 500, data: { agentName: '证据采集节点' } };
        canvasState.nodes.push(n); render(); renderAgentNode(n.id);
        centerViewportOnWorldPoint({ x: n.x + 403, y: n.y + 250 });
        await new Promise(r => setTimeout(r, 500));
        return { id: n.id };
      });
    })();
    const meas = await page.evaluate(id => {
      const card = document.querySelector(`[data-id="${id}"] .agent-tui-card`);
      const node = document.querySelector(`[data-id="${id}"]`);
      return card && node ? { nodeW: node.offsetWidth, cardW: card.offsetWidth } : null;
    }, nodeInfo.id);
    await page.locator(`[data-id="${nodeInfo.id}"]`).screenshot({ path: path.join(SHOTS, '03_agent_node_gap_full.png') });
    report.shot312defect = meas; // 期望 cardW << nodeW
    await page.close();
  }

  // ---- #313/#316 pre 基线（:3002 = 4ed1638：宽度已修，输入框不增高、旧 emoji 图标）----
  {
    const c2 = await browser.newContext({ viewport: { width: 1500, height: 950 } });
    await c2.addInitScript(() => { const O = window.WebSocket; window.WebSocket = function (u, ...r) { if (String(u).includes('/agent-nodes/')) return { send() {}, close() {}, onmessage: null, onopen: null, onclose: null, onerror: null, addEventListener() {}, readyState: 3 }; return new O(u, ...r); }; });
    const page = await c2.newPage();
    await page.goto('http://localhost:3002/static/smart-canvas.html?project_mode=1', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const pid = await page.evaluate(async () => {
      const r = await fetch('/api/projects', { headers: { accept: 'application/json' } }).then(x => x.json());
      const arr = Array.isArray(r) ? r : (r.projects || []);
      return arr.length ? arr[0].id : null;
    });
    await page.goto(`http://localhost:3002/static/smart-canvas.html?project_mode=1&project=${pid}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    const nodeInfo = await page.evaluate(async () => {
      const n = { id: 'shotcappre', type: NODE_TYPE_AGENT, x: 120, y: 120, w: 460, h: 420, data: { agentName: '证据采集节点' } };
      canvasState.nodes.push(n); render(); renderAgentNode(n.id);
      centerViewportOnWorldPoint({ x: n.x + 230, y: n.y + 210 });
      await new Promise(r => setTimeout(r, 500));
      return { id: n.id };
    });
    const input = page.locator('[data-agent-tui-input]');
    await input.click();
    // 无换行：纯靠折行演示「高度不增」。换行会触发 Enter=发送，避开。
    await input.type('第一行内容很长很长很长用来强制折行第二行继续第三行更多文字第四行第五行第六行第七行第八行第九行第十行继续追加', { delay: 2 });
    await page.waitForTimeout(400);
    const inputH = await page.evaluate(() => { const i = document.querySelector('[data-agent-tui-input]'); return i ? { h: i.offsetHeight, sh: i.scrollHeight } : null; });
    await page.locator(`[data-id="shotcappre"]`).screenshot({ path: path.join(SHOTS, '313_pre_4ed1638.png') });
    await page.locator(`[data-id="shotcappre"]`).screenshot({ path: path.join(SHOTS, '316_pre_4ed1638.png') });
    report.shot313pre = { inputH, note: 'h 固定（不随内容增高），sh 大于 h → 内容进滚动' };
    await page.close();
  }

  report.ok = true;
  console.log(JSON.stringify(report, null, 1));
  await browser.close();
})().catch(e => { console.log('CAPTURE ERR ' + e); process.exit(1); });
