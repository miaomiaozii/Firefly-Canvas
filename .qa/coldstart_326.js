// #326 codex 首 turn 冷启动现测：建项目→canvas 落 codex 节点→WS prompt 分段计时，连续 3 轮（cold vs warm 对照）
// Usage: node .qa/coldstart_326.js [base]
const fs = require('fs');
const base = process.argv[2] || 'http://127.0.0.1:8791';
const wsBase = base.replace(/^http/, 'ws');
const NODE = 'cold326_cx';

async function j(method, path, body) {
  const r = await fetch(base + path, {
    method,
    headers: { 'content-type': 'application/json', origin: base },
    body: body ? JSON.stringify(body) : undefined,
  });
  const t = await r.text();
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${t.slice(0, 200)}`);
  return JSON.parse(t);
}

(async () => {
  const created = await j('POST', '/api/project-library', { name: 'COLD326' });
  const project = created.project || created;
  const pid = project.id;
  await j('PATCH', `/api/project-library/${pid}/canvas`, {
    base_revision: 0,
    mutations: [{ op: 'upsert_node', node: { id: NODE, type: 'smart-agent', kind: 'agent', title: '冷启动探针', x: 0, y: 0, agentCli: 'codex' } }],
  });
  console.log('PROJECT', pid);

  const ws = new WebSocket(`${wsBase}/api/projects/${pid}/agent-nodes/${NODE}/stream`, { headers: { Origin: base } });
  let sessionMs = -1, turnStart = 0, turn = 0, t0 = Date.now();
  const ts = () => Date.now() - t0;
  const rows = [];
  ws.onopen = () => console.log(`[${ts()}ms] OPEN`);
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'session' && sessionMs < 0) {
      sessionMs = ts();
      console.log(`[${ts()}ms] SESSION status=${ev.status}`);
      sendTurn();
    }
    if (ev.type === 'agent_end' || ev.type === 'turn_completed') {
      rows.push({ turn: turn + 1, prompt_to_end_ms: ts() - turnStart });
      console.log(`[${ts()}ms] TURN${turn + 1} DONE prompt→end=${ts() - turnStart}ms`);
      if (turn === 0) { turn++; setTimeout(sendTurn, 500); }
      else { ws.close(); }
    }
    if (ev.type === 'error') console.log(`[${ts()}ms] ERROR ${JSON.stringify(ev).slice(0, 300)}`);
  };
  ws.onclose = () => { console.log('ROWS', JSON.stringify(rows)); console.log('COLDSTART-DONE'); process.exit(0); };
  function sendTurn() {
    turnStart = ts();
    ws.send(JSON.stringify({ type: 'prompt', message: `只回复两个字：${turn === 0 ? '冷' : '热'}。不要调用任何工具。` }));
    console.log(`[${ts()}ms] TURN${turn + 1} PROMPT sent`);
  }
  setTimeout(() => { console.log('TIMEOUT-180s ROWS', JSON.stringify(rows)); ws.close(); }, 180000);
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
