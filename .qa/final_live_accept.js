// Final-accept ①: live user instance — create throwaway agent node in user's real project,
// send prompt via WS, verify events.jsonl persisted via REST, then remove node.
// Usage: node .qa/final_live_accept.js <TOKEN> [project] [node]
const BASE = 'http://127.0.0.1:3000';
const TOK = process.argv[2];
const project = process.argv[3] || '2c92ddd4e34c4345a8560e389d41b580';
const node = process.argv[4] || ('agent_final_' + Date.now().toString(36));
const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${TOK}` };

async function api(path, opts = {}) {
  const r = await fetch(BASE + path, { ...opts, headers: { ...H, ...(opts.headers || {}) } });
  const ct = r.headers.get('content-type') || '';
  const body = ct.includes('json') ? await r.json() : await r.text();
  return { status: r.status, body };
}

(async () => {
  console.log('node =', node, ' project =', project);

  // 1) create agent node in the live canvas (project data)
  let res = await api(`/api/projects/${project}/data`);
  const data = res.body;
  if (res.status !== 200) { console.log('READ FAIL', res.status, String(res.body).slice(0, 200)); process.exit(1); }
  let canvas;
  try { canvas = typeof data === 'string' ? JSON.parse(data) : (data.canvas ?? data); } catch { console.log('PARSE FAIL'); process.exit(1); }
  const nodes = canvas.nodes || canvas;
  console.log('canvas nodes:', Array.isArray(nodes) ? nodes.length : Object.keys(nodes).length);
  const agentNode = { id: node, type: 'smart-agent', position: { x: -2000, y: -2000 },
    data: { agentCli: 'pi', title: '终验探针(用完即删)', __selected: false } };
  if (Array.isArray(nodes)) nodes.push(agentNode); else nodes[node] = agentNode;
  res = await api(`/api/projects/${project}/data`, { method: 'PUT', body: JSON.stringify({ canvas }) });
  console.log('PUT node:', res.status, JSON.stringify(res.body).slice(0, 150));
  if (res.status !== 200) { console.log('CREATE FAIL'); process.exit(1); }

  // 2) WS prompt via bridge
  const ws = new WebSocket(BASE.replace('http', 'ws') + `/api/agent-nodes/${node}/stream`, { headers: { Origin: BASE } });
  const live = [];
  let promptSent = false;
  await new Promise((resolve) => {
    ws.onopen = () => {
      setTimeout(() => { ws.send(JSON.stringify({ type: 'prompt', message: '终验探针：只回复四个字：验收通过' })); promptSent = true; }, 400);
    };
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      live.push(ev.type + (ev.seq != null ? '#' + ev.seq : ''));
      if (ev.type === 'agent_end') { setTimeout(resolve, 500); ws.close(); }
    };
    ws.onerror = () => resolve();
    setTimeout(resolve, 60000);
  });
  console.log('LIVE events:', JSON.stringify(live));

  // 3) REST persisted check
  res = await api(`/api/projects/${project}/agent-nodes/${node}/events?after=0`);
  const persisted = res.body && res.body.events ? res.body.events : [];
  console.log('REST status:', res.status, 'persisted_count:', persisted.length);
  for (const e of persisted) console.log('  P', e.seq, e.type, e.host, JSON.stringify(e.payload ?? e.data ?? {}).slice(0, 60));

  // 4) delete node (cleanup)
  let res2 = await api(`/api/projects/${project}/data`);
  const c2 = res2.body; const n2 = c2.nodes || c2;
  if (Array.isArray(n2)) { const i = n2.findIndex(n => n.id === node); if (i >= 0) n2.splice(i, 1); }
  else delete n2[node];
  res = await api(`/api/projects/${project}/data`, { method: 'PUT', body: JSON.stringify({ canvas: c2 }) });
  console.log('DELETE node:', res.status);
  console.log('VERDICT:', persisted.length >= 5 && live.length >= 5 ? 'PASS' : 'FAIL',
    '| live=' + live.length, 'persisted=' + persisted.length);
  process.exit(0);
})();
