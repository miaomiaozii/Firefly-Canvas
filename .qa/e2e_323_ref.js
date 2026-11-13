// Refs #323 票 E 验收②：reference frame 注入——codex 真轮描述参考图（紫/白圆/黑三角/ZEBRA-42）。
// 用法: node .qa/e2e_323_ref.js [projectId]
// 前置: :8791 隔离实例；/output/zebra42.png 存在（PIL 生成特征图）。
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8791';
const AGENT = 'agent-e2e323-codex-' + Date.now();
const GROUP = `group-ref-${Date.now()}`;
const IMG = `img-ref-${Date.now()}`;

async function mcp(tool, args) {
  const res = await fetch(`${BASE}/api/mcp?project=${PID}&node=${AGENT}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', origin: BASE },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }),
  });
  const t = await res.text();
  const line = t.split('\n').find(l => l.startsWith('data:')) || t;
  const p = JSON.parse(line.replace(/^data:\s*/, ''));
  if (p.error) throw new Error(`${tool}: ${JSON.stringify(p.error)}`);
  const c = p.result?.content?.[0]?.text;
  try { return JSON.parse(c); } catch { return c; }
}

let PID = '';
(async () => {
  const list = await (await fetch(`${BASE}/api/project-library`, { headers: { origin: BASE } })).json();
  PID = process.argv[2] || list.projects[0].id;
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  await page.goto(`${BASE}/static/smart-canvas.html?project_mode=1&id=${PID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  // 1) 素材入库（真媒体导入链）
  const imp = await (await fetch(`${BASE}/api/project-library/${PID}/media/import`, {
    method: 'POST', headers: { 'content-type': 'application/json', origin: BASE },
    body: JSON.stringify({ source_url: '/output/zebra42.png', expected_kind: 'image', name: 'zebra42.png' }),
  })).json();
  let taskId = imp.task.id, task;
  for (let i = 0; i < 30; i++) {
    task = await (await fetch(`${BASE}/api/project-library/${PID}/media/import/${taskId}`, { headers: { origin: BASE } })).json();
    if (task.task?.status === 'completed' || task.task?.status === 'failed') break;
    await new Promise(r => setTimeout(r, 500));
  }
  if (task.task?.status !== 'completed') throw new Error('导入失败: ' + JSON.stringify(task).slice(0, 200));
  const assetId = String(task.task.media.id || task.task.media.asset_id || '');
  console.log('asset', assetId, JSON.stringify(task.task.media).slice(0, 120));

  // 2) codex agent 节点 + 参考框 + 图节点（全部 server 侧 MCP 建）
  await mcp('canvas.create_node', { node: { id: AGENT, type: 'smart-agent', agentCli: 'codex',
    x: 60, y: 560, w: 520, h: 460, data: { agentName: 'E2 验收' } } });
  await mcp('canvas.create_node', { node: { id: GROUP, type: 'smart-group', x: 700, y: 560,
    w: 640, h: 360, title: 'E2 参考框', items: [IMG], refFrame: true } });
  await mcp('canvas.create_node', { node: { id: IMG, type: 'smart-image', x: 730, y: 620, w: 240, h: 180,
    title: 'zebra42', images: [{ asset_id: assetId, kind: 'image' }] } });
  await mcp('canvas.update_node', { node_id: AGENT, updates: { refFrameId: GROUP } });

  // 3) 真 codex 轮：WS 流 + prompt（send_prompt 注入链）
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const evs = []; let reply = ''; let userPromptEv = null;
  const ws = new WebSocket(BASE.replace('http', 'ws') + `/api/projects/${PID}/agent-nodes/${AGENT}/stream`, { headers: { Origin: BASE } });
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    evs.push(ev.type);
    if (ev.type === 'user_prompt') userPromptEv = ev;
    if (ev.type === 'message_update' && ev.assistantMessageEvent?.type === 'text_delta') reply += ev.assistantMessageEvent.delta;
    if (ev.type === 'agent_end') finish();
  };
  function finish() { setTimeout(async () => {
    const okDesc = /紫|purple/i.test(reply) && /ZEBRA-42/i.test(reply) && (/圆|circle/i.test(reply) || /三角|triangle/i.test(reply));
    try {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2500);
      await page.evaluate((a) => { const n = canvasState.nodes.find((x) => x.id === a);
        if (n) centerViewportOnWorldPoint({ x: n.x + n.w / 2, y: n.y + n.h / 2 }); render(); }, AGENT);
    } catch (e) { console.log('screenshot step warn:', e.message); }
    await page.screenshot({ path: 'D:/tmp/fc_shots/323_e2_ref.png' });
    // user_prompt 不上 WS（只入 events.jsonl）——refs 判定读 server 侧落盘（监理钉①）。
    const fs = require('fs');
    const dir = 'D:/tmp/fly_323/data/' + PID + '/agent-events';
    const f = fs.readdirSync(dir).filter((x) => x.startsWith('agent-e2e323-codex-'))
      .sort((a, b) => fs.statSync(dir + '/' + b).mtimeMs - fs.statSync(dir + '/' + a).mtimeMs)[0];
    const recs = fs.readFileSync(dir + '/' + f, 'utf8').split('\n').filter(Boolean).map(JSON.parse);
    const up = recs.filter((r) => r.event?.type === 'user_prompt').pop();
    console.log('events.jsonl user_prompt.refs =', JSON.stringify(up?.event?.refs));
    const okRefs = up?.event?.refs?.length === 1 && up.event.refs[0].name === 'zebra42';
    console.log('reply =', reply.slice(0, 300));
    console.log(okRefs && okDesc ? 'E2 PASS（refs 元数据+codex 准确描述素材）截图 D:/tmp/fc_shots/323_e2_ref.png'
      : `E2 FAIL refs=${okRefs} desc=${okDesc}`);
    await browser.close();
    process.exitCode = okRefs && okDesc ? 0 : 1;
  }, 500); }
  const r = await fetch(`${BASE}/api/projects/${PID}/agent-nodes/${AGENT}/prompt`, {
    method: 'POST', headers: { 'content-type': 'application/json', origin: BASE },
    body: JSON.stringify({ message: '你收到了一张注入的参考素材图片。用一句话描述它的颜色构成（背景色、形状）和图中出现的文字。不要执行任何 shell 命令。', images: [] }),
  });
  console.log('prompt http', r.status);
  setTimeout(() => { console.error('E2E TIMEOUT', evs.slice(0, 20), reply.slice(0, 100)); process.exit(2); }, 180000);
})().catch(e => { console.error('E2E ERROR', e.message); process.exit(2); });
