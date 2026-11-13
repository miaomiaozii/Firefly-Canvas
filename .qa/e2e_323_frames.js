// Refs #323 票 E 验收①：会话 frame 自动落位（playwright 开画布桥 + 纯 MCP HTTP 造节点）。
// 用法: node .qa/e2e_323_frames.js
// 前置: :8791 隔离实例（双 env），项目已建（脚本自动取 library 第一个）。
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8791';
const AGENT_NODE = `ext-e2e-323-${Date.now()}`;

async function mcp(tool, args, projectId) {
  const res = await fetch(`${BASE}/api/mcp?project=${projectId}&node=${AGENT_NODE}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream',
               origin: BASE },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call',
      params: { name: tool, arguments: args } }),
  });
  const text = await res.text();
  const line = text.split('\n').find(l => l.startsWith('data:')) || text;
  const payload = JSON.parse(line.replace(/^data:\s*/, ''));
  if (payload.error) throw new Error(`${tool}: ${JSON.stringify(payload.error)}`);
  const c = payload.result?.content?.[0]?.text;
  if (!c) return payload.result;
  try { return JSON.parse(c); } catch { throw new Error(`${tool} 非 JSON 返回: ${c.slice(0, 160)}`); }
}

(async () => {
  const list = await (await fetch(`${BASE}/api/project-library`, { headers: { origin: BASE } })).json();
  const projectId = process.argv[2] || list.projects[0].id;
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 } })).newPage();
  await page.goto(`${BASE}/static/smart-canvas.html?project_mode=1&id=${projectId}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500); // 画布标签页注册（MCP 桥前提）

  const seed = await mcp('canvas.create_node', { node: { type: 'smart-prompt', title: 'seed', x: 10, y: 10 } }, projectId);
  // 外部 MCP client 的 agent 节点由 ensure_external_agent_node 物化（id=mcp-agent-*，
  // 复用首个 mcp_session 节点）——落位链用的是那个 id，不是 URL ?node=。
  const listed = await mcp('canvas.list_nodes', {}, projectId);
  const mine = (listed.nodes || []).find((n) => String(n.id).startsWith('mcp-agent-'));
  if (!mine) throw new Error('未找到 MCP 物化 agent 节点: ' + JSON.stringify(listed).slice(0, 200));
  console.log('project', projectId, 'agent', mine.id);
  const frameId = `group-e2e-${Date.now()}`;
  await mcp('canvas.create_node', { node: { id: frameId, type: 'smart-group', x: 400, y: 400,
                                            w: 800, h: 360, title: 'E2E 会话框', items: [] } }, projectId);
  await mcp('canvas.update_node', { node_id: String(mine.id), updates: { chatFrameId: frameId } }, projectId);
  const made = [];
  for (let i = 0; i < 4; i++) {
    const r = await mcp('canvas.create_node', { node: { type: 'smart-prompt', title: `E1-${i}` } }, projectId);
    made.push(r.node_id);
  }
  const nodes = [];
  for (const id of made) nodes.push(await mcp('canvas.get_node', { node_id: id }, projectId));
  const frame = await mcp('canvas.get_node', { node_id: frameId }, projectId);
  const pos = nodes.map(n => [n.x, n.y]);
  const want = [[420, 460], [676, 460], [932, 460], [420, 616]];
  const ok = JSON.stringify(pos) === JSON.stringify(want)
    && (frame.items || []).length === 4 && frame.h >= 376;
  console.log('pos', JSON.stringify(pos), 'h=', frame.h, 'items=', (frame.items || []).length);
  // 浏览器现物截图：frame+4 节点肉眼可见
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000); // 重新拉取 store 画布（MCP 写入在 server 侧）
  await page.evaluate((fid) => {
    const n = canvasState.nodes.find((x) => x.id === fid);
    centerViewportOnWorldPoint({ x: n.x + n.w / 2, y: n.y + n.h / 2 });
    render();
  }, frameId);
  await page.waitForTimeout(600);
  await page.screenshot({ path: 'D:/tmp/fc_shots/323_e1_frames.png' });
  console.log(ok ? 'E1 PASS（网格落位+items 追加+按行长高）截图 D:/tmp/fc_shots/323_e1_frames.png'
                 : `E1 FAIL want ${JSON.stringify(want)}`);
  await browser.close();
  process.exitCode = ok ? 0 : 1;
})().catch(e => { console.error('E2E ERROR', e.message); process.exit(2); });
