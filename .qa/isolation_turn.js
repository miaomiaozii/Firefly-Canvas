// #318 隔离尾单验收：单轮 codex turn（WS 等 agent_end）
const BASE = 'http://127.0.0.1:8791';
const PID = '3e3ed8530d3d4aa3bac520e104276c0f';
const NODE = process.argv[2] || 'final_cx_host';
(async () => {
  const ws = new WebSocket(BASE.replace('http', 'ws') + `/api/projects/${PID}/agent-nodes/${NODE}/stream`, { headers: { Origin: BASE } });
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const done = new Promise((res) => { ws.onmessage = (e) => { const ev = JSON.parse(e.data); if (ev.type === 'agent_end') res(); }; });
  const r = await fetch(`${BASE}/api/projects/${PID}/agent-nodes/${NODE}/prompt`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: BASE },
    body: JSON.stringify({ message: '只回复两个字：隔离。不要执行任何命令。', images: [] }),
  });
  console.log('prompt http:', r.status);
  await done;
  console.log('agent_end received');
  ws.close();
  process.exit(0);
})().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
