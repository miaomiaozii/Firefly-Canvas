// #326 A/B 对照：同 session 同 prompt，warmup 开/关 各 3 轮 turn 时长（监理要求 A/B 后才关票）
const BASE = 'http://127.0.0.1:8791';
const PID = process.argv[2];
const NODE = process.argv[3];

async function api(path, method = 'GET', body) {
  const res = await fetch(BASE + path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: res.status, json: await res.json().catch(() => ({})) };
}

async function waitIdle(timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const { json } = await api(`/api/projects/${PID}/agent-nodes/${NODE}/status`);
    if (!json.is_running) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

async function eventCount() {
  const { json } = await api(`/api/projects/${PID}/agent-nodes/${NODE}/events`);
  return (json.events || []).length;
}

async function waitTerminal(sinceCount, timeoutMs = 180000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const { json } = await api(`/api/projects/${PID}/agent-nodes/${NODE}/events`);
    const evs = (json.events || []);
    if (evs.length > sinceCount) {
      const fresh = evs.slice(sinceCount).map((r) => r.event || r);
      if (fresh.some((e) => e.type === 'agent_end' || e.type === 'error')) return 'end';
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  return 'TIMEOUT';
}

async function oneTurn(label) {
  await waitIdle();
  const before = await eventCount();
  const t0 = Date.now();
  let pr = await api(`/api/projects/${PID}/agent-nodes/${NODE}/prompt`, 'POST', {
    message: '只回复数字 1，不要其他内容。',
  });
  if (pr.status === 500) { await waitIdle(); pr = await api(`/api/projects/${PID}/agent-nodes/${NODE}/prompt`, 'POST', { message: '只回复数字 1，不要其他内容。' }); }
  if (pr.status !== 200) { console.log(`PROMPT-FAIL ${label} status=${pr.status}`, JSON.stringify(pr.json).slice(0, 200)); return NaN; }
  const term = await waitTerminal(before);
  const dur = ((Date.now() - t0) / 1000).toFixed(2);
  console.log(`TURN ${label} term=${term} dur=${dur}s`);
  return Number(dur);
}

(async () => {
  const mode = process.argv[4]; // warm | cold
  const rounds = [];
  for (let i = 0; i < 3; i++) {
    if (mode === 'cold') {
      // 冷组：轮间杀服务器再拉起由外部脚本做；这里仅间隔>30s 让 warmup 冷却过期后…
      // 简化：cold 组在禁用 warmup 的服务器上跑（env FIREFLY_CODEX_WARMUP=off 由启动方设置）
      await new Promise((r) => setTimeout(r, 1000));
    }
    rounds.push(await oneTurn(`${mode}-${i + 1}`));
    await new Promise((r) => setTimeout(r, 2000));
  }
  const avg = (rounds.reduce((a, b) => a + b, 0) / rounds.length).toFixed(2);
  console.log(`AVG ${mode} = ${avg}s`, rounds);
})();
