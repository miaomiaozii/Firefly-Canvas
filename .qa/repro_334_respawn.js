// #334 复现：双 pi 节点 → 暗号轮 → kill 宿主 → 自动 respawn → 判定上下文/串线
// 前置：:8791 已跑（scratch fly_h 盘）。用法：node .qa/repro_334_respawn.js
const BASE = "http://127.0.0.1:8791";
const HDRS = { "content-type": "application/json", origin: BASE };

async function j(method, url, body) {
  const r = await fetch(BASE + url, { method, headers: HDRS, body: body === undefined ? undefined : JSON.stringify(body) });
  const t = await r.text();
  let data; try { data = JSON.parse(t); } catch { data = t; }
  if (!r.ok) throw new Error(`${method} ${url} -> ${r.status} ${t.slice(0, 200)}`);
  return data;
}

async function promptAndWait(pid, nodeId, text, timeoutMs = 180000) {
  const p = await j("POST", `/api/projects/${pid}/agent-nodes/${nodeId}/prompt`, { message: text });
  const start = p.total || 0;
  const t0 = Date.now();
  let endSeen = null;
  while (Date.now() - t0 < timeoutMs) {
    const ev = await j("GET", `/api/projects/${pid}/agent-nodes/${nodeId}/events?since=${start}`);
    for (const e of ev.events || []) {
      const ty = e.event && e.event.type;
      if (ty === "message_end" && e.event.message && e.event.message.role === "assistant") endSeen = e;
      if (ty === "agent_end" || ty === "agent_settled") endSeen = endSeen || e;
    }
    if (endSeen) return endSeen;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error(`prompt on ${nodeId} timed out`);
}

(async () => {
  const lib = await j("GET", "/api/project-library");
  let project = (lib.projects || lib || []).find((x) => x.name === "REPRO334" || x.title === "REPRO334");
  if (!project) {
    const created = await j("POST", "/api/project-library", { name: "REPRO334" });
    project = created.project || created;
  }
  const pid = project.id;
  console.log("PID", pid);

  const nodes = [
    { id: "p334-e", word: "MANGO" },
    { id: "p334-f", word: "KIWI" },
  ];
  const mutations = nodes.map((n) => ({ op: "upsert_node", node: { id: n.id, type: "agent", kind: "agent", x: 100, y: 100 + nodes.indexOf(n) * 500, w: 480, h: 360, agentCli: "pi", autonomy: "cautious", title: `334 ${n.id}` } }));
  mutations.push({ op: "upsert_node", node: { id: "p334-r2", type: "agent", kind: "agent", x: 100, y: 1100, w: 480, h: 360, agentCli: "pi", autonomy: "cautious", title: "334 probe2" } });
  let baseRev = 0;
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await fetch(BASE + `/api/project-library/${pid}/canvas`, { method: "PATCH", headers: HDRS, body: JSON.stringify({ mutations, base_revision: baseRev }) });
    if (r.ok) break;
    const t = await r.text();
    if (r.status === 409) { baseRev = (JSON.parse(t).detail || {}).canvas?.revision ?? baseRev + 1; continue; }
    throw new Error(`canvas PATCH ${r.status} ${t.slice(0, 150)}`);
  }
  for (const n of [...nodes, { id: "p334-r2", word: "" }]) {
    const created = await j("POST", `/api/projects/${pid}/agent-nodes`, { node_id: n.id });
    console.log("CREATED", n.id, JSON.stringify(created));
    const before = await j("GET", `/api/projects/${pid}/agent-nodes/${n.id}/status`);
    console.log("STATUS-BEFORE", n.id, JSON.stringify({ session_id: before.session_id, session_file: before.session_file, state: before.state || before.status }));
    const ans = await promptAndWait(pid, n.id, `记住暗号 ${n.word}。只需回复 OK。`);
    console.log("TURN1-END", n.id, ans.event.type);
    const mid = await j("GET", `/api/projects/${pid}/agent-nodes/${n.id}/status`);
    console.log("STATUS-MID", n.id, JSON.stringify({ session_id: mid.session_id, session_file: mid.session_file }));
  }

  const pids = process.argv.slice(2).map(Number).filter(Boolean);
  if (!pids.length) {
    console.log("KILL-SKIP: 未传 pi PID；请外部 taskkill 后二次运行带 --resume-check");
  }
  if (process.argv.includes("--resume-check")) {
    for (const n of nodes) {
      const st = await j("GET", `/api/projects/${pid}/agent-nodes/${n.id}/status`);
      console.log("STATUS-AFTER-RESPAWN", n.id, JSON.stringify({ session_id: st.session_id, session_file: st.session_file, state: st.state || st.status }));
      const ans = await promptAndWait(pid, n.id, "之前让你记住的暗号是什么？只回答那个词。");
      const content = (ans.event.message && ans.event.message.content || []).map((c) => c.text || "").join("");
      console.log("RESUME-ANSWER", n.id, JSON.stringify(content.slice(0, 120)), "expected", n.word, content.toUpperCase().includes(n.word) ? "PASS" : "FAIL");
    }
    // 钉②：完整提示词到达探针（行为准则第三条在首行之后，截断 bug 下从未送达）
    const probe = await promptAndWait(pid, "p334-r2", "你的行为准则里关于破坏性操作的那一条是什么？原样复述那一行。");
    const ptext = (probe.event.message && probe.event.message.content || []).map((c) => c.text || "").join("");
    console.log("PROMPT-PROBE", JSON.stringify(ptext.slice(0, 200)), /破坏性/.test(ptext) ? "PASS" : "FAIL");
  }
})().catch((e) => { console.error("ERR", e.message); process.exit(1); });
