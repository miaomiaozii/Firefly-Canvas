// #317 re-verify: WS bridge E2E against live server (定稿 main working tree)
// Usage: node .qa/ws_e2e_317.js [base] [project]
const base = process.argv[2] || 'http://127.0.0.1:3000';
const project = process.argv[3] || '2c92ddd4e34c4345a8560e389d41b580';
const node = 'agent_e2e_probe_' + Date.now().toString(36);
const wsBase = base.replace(/^http/, 'ws');
const t0 = Date.now();
const ts = () => Date.now() - t0;
const counts = {};
const ws = new WebSocket(`${wsBase}/api/projects/${project}/agent-nodes/${node}/stream`, { headers: { Origin: base } });
let sessionMs = -1, promptSent = 0, endMs = -1;
const events = [];
ws.onopen = () => console.log(`[${ts()}ms] OPEN node=${node} project=${project}`);
ws.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  counts[ev.type] = (counts[ev.type] || 0) + 1;
  events.push({ t: ts(), type: ev.type });
  if (ev.type === 'session' && sessionMs < 0) {
    sessionMs = ts();
    console.log(`[${ts()}ms] session status=${ev.status} model=${JSON.stringify(ev.model)} autonomy=${ev.autonomy}`);
    const msg = 'E2E-PROBE: 只回复两个字：收到。不要调用任何工具。';
    promptSent = ts();
    ws.send(JSON.stringify({ type: 'prompt', message: msg }));
    console.log(`[${ts()}ms] PROMPT sent`);
  }
  if (ev.type === 'agent_end' || ev.type === 'turn_completed') {
    if (endMs < 0) endMs = ts();
    console.log(`[${ts()}ms] ${ev.type} stopReason=${ev.stop_reason ?? ev.data?.stopReason ?? '-'}`);
  }
  if (ev.type === 'message_update') {
    const am = [ev.assistantMessageEvent].filter(Boolean);
    for (const a of am) if (a.type === "text_delta" && a.delta) process.stdout.write(`  text: ${JSON.stringify(a.delta).slice(0, 80)}\n`);
  }
  if (ev.type === 'error') console.log(`[${ts()}ms] ERROR ${JSON.stringify(ev).slice(0, 200)}`);
};
ws.onerror = (e) => console.log(`[${ts()}ms] WS ERROR ${e.message || e.error?.message}`);
ws.onclose = (c) => {
  console.log(`[${ts()}ms] CLOSE code=${c.code}`);
  report();
};
function report() {
  console.log('--- NUMBERS ---');
  console.log(JSON.stringify({
    connect_to_session_ms: sessionMs,
    prompt_to_end_ms: endMs - promptSent,
    total_ms: ts(),
    events: counts,
    event_total: events.length,
    node, project,
  }));
  process.exit(0);
}
setTimeout(() => { console.log('TIMEOUT 150s'); report(); }, 150000);
