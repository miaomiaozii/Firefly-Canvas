// #318 票 C/E MCP 链验收：codex → MCP 端点 → 浏览器画布桥（FIREFLY_CANVAS_ID）。
// 前置：server 以 FIREFLY_CANVAS_ID=<nodeId> 启动；页面已打开该节点卡片（桥=WS 在连）。
// 用法: node .qa/e2e_318c_mcp.js [base] [pid] [nodeId]
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const pid = process.argv[3] || '6d7805c8d51043bd8fb504bf0de5e275';
const nodeId = process.argv[4] || 'agent_codex_e2e';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  const page = await ctx.newPage();
  await page.goto(`${base}/static/smart-canvas.html?project_mode=1&id=${pid}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2200);
  await page.evaluate(async ({ nid }) => {
    const n = { id: nid, type: NODE_TYPE_AGENT, x: 120, y: 120, w: 480, h: 420, data: { agentName: '票C回放验收' } };
    canvasState.nodes.push(n);
    render();
    renderAgentNode(n.id);
    centerViewportOnWorldPoint({ x: n.x + n.w / 2, y: n.y + n.h / 2 });
    await new Promise(r => setTimeout(r, 400));
  }, { nid: nodeId });
  await page.locator(`[data-id="${nodeId}"]`).click({ position: { x: 30, y: 20 } });
  await page.waitForTimeout(3000); // WS 握手 = 画布桥就位

  const t0 = Date.now();
  const evs = [];
  let reply = '';
  const ws = new WebSocket(base.replace('http', 'ws') + `/api/projects/${pid}/agent-nodes/${nodeId}/stream`, { headers: { Origin: base } });
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    evs.push(ev.type);
    if (ev.type === 'message_update' && ev.assistantMessageEvent?.type === 'text_delta') reply += ev.assistantMessageEvent.delta;
    if (ev.type === 'agent_end') finish();
  };
  const r = await fetch(`${base}/api/projects/${pid}/agent-nodes/${nodeId}/prompt`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: base },
    body: JSON.stringify({ message: '调用画布工具 canvas.get_node（node_id=agent_codex_e2e），把该节点的 x 坐标数值原文回复。不要执行任何 shell 命令。', images: [] }),
  });
  console.log('prompt ->', r.status);

  function finish() {
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`[${secs}s] events(${evs.length}): ${evs.join(',')}`);
    console.log('REPLY=' + JSON.stringify(reply.slice(0, 160)));
    const gotName = reply.includes('100');
    console.log('MCP_BRIDGE_CANVAS_QUERY=' + (gotName ? 'PASS' : 'FAIL'));
    try { ws.close(); } catch (e) {}
    browser.close().then(() => process.exit(gotName ? 0 : 1));
  }
  setTimeout(() => { console.log('TIMEOUT 240s events(' + evs.length + '): ' + evs.join(',')); console.log('REPLY=' + JSON.stringify(reply.slice(0, 200))); browser.close().then(() => process.exit(2)); }, 240000);
})();
