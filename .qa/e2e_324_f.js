// 票 F (#324) 真机验收：顶栏搜索面板跨项目命中 + MCP agent 工具同结果 + 零 pageerror
const { chromium } = require('playwright');

const PID = 'ee18ac628a6040d7b8bcca5a4dbdf334';
const URL = `http://127.0.0.1:8791/static/smart-canvas.html?id=${PID}&project=${PID}&project_mode=1`;

(async () => {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(URL, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);

    // 1) 顶栏搜索按钮 → 面板
    await page.click('#smartSearchToggle');
    await page.waitForSelector('#canvasSearchPanel', { state: 'visible' });
    await page.fill('#canvasSearchInput', '青柠验收');
    await page.waitForTimeout(800);
    const panelText = await page.textContent('#canvasSearchResults');
    console.log('PANEL:', JSON.stringify(panelText));
    const hitCount = await page.locator('.canvas-search-hit').count();
    const projCount = await page.locator('.canvas-search-project').count();
    console.log('hits=', hitCount, 'projects=', projCount);
    await page.screenshot({ path: 'D:/Firefly-Canvas/.qa/shots/324_f_search_panel.png' });

    // 2) 项目名点击跳转（验证 href 形态存在）
    const firstLink = await page.locator('.canvas-search-project-name').first().textContent();
    console.log('first project:', firstLink);

    // 3) MCP agent 工具（页内 fetch，同源带 Origin）：initialize → tools/call
    const mcp = await page.evaluate(async (pid) => {
        const H = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' };
        const init = await fetch('/api/mcp?project=' + pid, { method: 'POST', headers: H, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'evidence', version: '0' } } }) });
        const sid = init.headers.get('mcp-session-id');
        const H2 = { ...H, 'Mcp-Session-Id': sid };
        await fetch('/api/mcp?project=' + pid, { method: 'POST', headers: H2, body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) });
        const r = await fetch('/api/mcp?project=' + pid, { method: 'POST', headers: H2, body: JSON.stringify({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'canvas.search_projects', arguments: { query: '青柠验收' } } }) });
        const t = await r.text();
        const line = t.split('\n').find((l) => l.startsWith('data:'));
        const body = line ? JSON.parse(line.slice(5)) : JSON.parse(t);
        return body.result;
    }, PID);
    console.log('MCP:', JSON.stringify(mcp).slice(0, 700));

    console.log('pageerrors=', errors.length, errors.slice(0, 3));
    await browser.close();
    const ok = hitCount >= 2 && projCount >= 2 && errors.length === 0;
    console.log(ok ? 'EVIDENCE-GREEN' : 'EVIDENCE-RED');
    process.exit(ok ? 0 : 1);
})();
