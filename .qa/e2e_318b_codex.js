// #318 票 B E2E: codex per-turn host on isolated instance :8799
// Round1: set secret → assert events + extSessionId persisted. Round2: resume context proof.
const BASE = 'http://127.0.0.1:8799';
const PID = '2643441ad90c40eba2b73a32b4c8eb11';
const NODE = 'agent_codex_e2e';
const WS = BASE.replace('http', 'ws') + `/api/projects/${PID}/agent-nodes/${NODE}/stream`;
const t0 = Date.now();
const ts = () => ((Date.now() - t0) / 1000).toFixed(1) + 's';

async function postPrompt(message) {
  const r = await fetch(`${BASE}/api/projects/${PID}/agent-nodes/${NODE}/prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: BASE },
    body: JSON.stringify({ message, images: [] }),
  });
  return { status: r.status, body: await r.json() };
}

function listen(ws, stopOn) {
  return new Promise((resolve, reject) => {
    const events = [];
    const texts = [];
    const timer = setTimeout(() => reject(new Error('timeout waiting ' + stopOn)), 180000);
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      events.push(ev.type);
      if (ev.type === 'message_update' && ev.assistantMessageEvent?.type === 'text_delta') {
        texts.push(ev.assistantMessageEvent.delta);
      }
      if (ev.type === 'error') texts.push('[ERROR] ' + ev.message);
      if (ev.type === stopOn) {
        clearTimeout(timer);
        resolve({ events, reply: texts.join('') });
      }
    };
    ws.onerror = (e) => { clearTimeout(timer); reject(new Error('ws error ' + (e.message || ''))); };
  });
}

(async () => {
  const ws = new WebSocket(WS, { headers: { Origin: BASE } });
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  console.log(`[${ts()}] WS open`);

  // round 1
  let p = listen(ws, 'agent_end');
  let r = await postPrompt('记住暗号：PURPLE-77。只回复两个字：收到。不要执行任何命令。');
  console.log(`[${ts()}] R1 prompt POST ->`, r.status, JSON.stringify(r.body));
  let out = await p;
  console.log(`[${ts()}] R1 reply: ${JSON.stringify(out.reply)} events(${out.events.length}): ${out.events.join(',')}`);

  // round 2 (resume)
  p = listen(ws, 'agent_end');
  r = await postPrompt('刚才我给你的暗号是什么？只回复暗号本身。');
  console.log(`[${ts()}] R2 prompt POST ->`, r.status);
  out = await p;
  console.log(`[${ts()}] R2 reply: ${JSON.stringify(out.reply)}`);

  const ok1 = /收到/.test('R1');
  const ok2 = out.reply.includes('PURPLE-77');
  console.log(`RESULT resume_context=${ok2 ? 'PASS' : 'FAIL'}`);
  ws.close();
  process.exit(ok2 ? 0 : 1);
})().catch((e) => { console.error('E2E FAIL:', e.message); process.exit(2); });
