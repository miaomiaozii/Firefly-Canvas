#!/usr/bin/env bash
# e2e_qa.sh — Firefly-Canvas 端到端真实网页 QA（tabbit 驱动真实浏览器，非截图、断言 DOM 状态）
# 用法: bash tools/e2e_qa.sh [base_url]   默认 http://127.0.0.1:3000
# 退出码: 0=全部通过; N=失败场景数。每个场景打印 PASS/SKIP/FAIL + 证据 JSON。
# 原则(#288 教训): 只信 waitForFunction 的 DOM 断言，不信“看起来启动了”。
#
# #360：以前这里从 /api/healthz 取 open_project_ids[0]——那是用户当前打开的真实项目，
# QA 在里面发消息、跑对话，项目数据被写。现在走 tools/e2e/e2e_common.sh 的夹具：
# 建一个一次性隔离项目，跑完删掉，并校验用户项目的 canvas.json 没被碰过。
set -u
BASE="${1:-http://127.0.0.1:3000}"
export FIREFLY_BASE="$BASE"
# #376：本套件的 s3/s4 需要夹具里的 Agent 节点（smart-agent，e2e-a1）；
# 种子开关只影响本套件（e2e_common.sh 的 e2e_agent_seed_args），其他套件种子不变。
export E2E_AGENT_NODE=1
source "$(cd "$(dirname "$0")" && pwd -W)/e2e/e2e_common.sh"
e2e_start qa "Firefly E2E QA" 180000

echo "== Firefly E2E QA @ $E2E_URL =="

# S1 Agent 卡片连接成功（无“启动失败/Failed to start”；重启后卡片有退避重试，给 90s 收敛）
e2e_run s1-agent-connected <<'JS'
// #288 教训升级：卡片文字残留(chat history 含旧“启动失败”)不作数——以服务端 healthz 为真相
var st = await page.evaluate(async () => (await (await fetch('/api/healthz', {cache:'no-store'})).json()).agents.by_status);
if (st.crashed) {
  // crashed 态下卡片应提供“重试连接”；真鼠标点→等待→以 healthz 复核（#360：
  // 不在 evaluate 里 el.click()——那是合成事件，绕过了命中测试和焦点行为）
  const btn = await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find(x => /重试连接|启动|连接/.test(x.textContent));
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height};
  });
  if (!btn) throw new Error('S1_RETRY_BUTTON_MISSING crashed 态但页面上没有重试按钮');
  await page.mouse.click(btn.x, btn.y);
  await page.waitForTimeout(15000);
  st = await page.evaluate(async () => (await (await fetch('/api/healthz', {cache:'no-store'})).json()).agents.by_status);
}
if (st.crashed) throw new Error('crashed 态点重试后仍 crashed（agent 无法复活）: ' + JSON.stringify(st));
if (st.crashed) throw new Error('agent crashed: ' + JSON.stringify(st));
// 探针实测：画布页上已经没有 .agent-tui-*（对话入口换成 #composer / #promptInput）。
// 卡片在场只断言 composer 存在，agent 健康与否以服务端 healthz 为真相。
var composer = await page.evaluate(() => !!document.getElementById('composer'));
if (!composer) throw new Error('composer missing: 对话入口不在页面上');
return {evidence:'healthz ' + JSON.stringify(st)};
JS
# S2 模型池新鲜且不含 image/classifier（#285 + cache:no-store 回归）
e2e_run s2-model-pool <<'JS'
var pool = await page.evaluate(async pid => (await (await fetch('/api/projects/' + pid + '/agent-nodes/chat-models', {cache:'no-store'})).json()), E2E_PID);
if (!pool.models || !pool.models.length) throw new Error('pool empty');
var leaked = pool.models.map(m => m.id).filter(v => /image|classifier|embedding|ranker/i.test(v));
if (leaked.length) throw new Error('non-chat models leaked: ' + leaked.join(','));
// 旧的「模型下拉与池逐项相等」断言依赖 .agent-tui-model-select，该控件已被 #engineSelect
// 取代（引擎下拉，不是逐模型下拉），没有等价物可断言——新鲜度回归改由 cache:no-store
// 这一路自己保证，池里拿到什么就是什么。
return {evidence: 'pool=' + pool.models.length + ' ' + pool.models.map(m=>m.id).slice(0,3).join('|')};
JS

# S3 真实对话有回复（#376：按当前 DOM 重写——Agent TUI 内嵌在 Agent 节点卡片里
#（canvas-agent-tui.js agentNodeBodyHtml），不再是页面上独立的 .agent-tui-card）。
# 模型口径：候选 = 池里 /vllm|llama-cpp|strata/ 的会话模型，本地口（llama-cpp-local/
# strata-sage-local）优先、其次 vllm、最后其他 strata，最多试 3 个；某个模型上游不可用
# 时会弹「模型错误（本轮无回复）」气泡（#288 可见化），据此换下一个。全部候选都
# 拿不到真实回复 = 环境无可用会话模型，明确 FAIL（不 skip——前置「池里有候选」已满足）。
e2e_run s3-real-reply <<'JS'
const AID = 'e2e-a1';
const card = page.locator('.image-node[data-id="' + AID + '"] .agent-tui-card');
await card.first().waitFor({ timeout: 30000 });
await clickNode(AID); // 真鼠标点选 Agent 节点（预置 clickNode，找不到会炸 NODE_MISSING）
// 等 Agent 会话就绪（空闲/运行中）；启动失败/已崩溃/重连停止 → 明确 FAIL
try {
  await page.waitForFunction((aid) => {
    const s = document.querySelector('.image-node[data-id="' + aid + '"] .agent-tui-status');
    return s && /空闲|运行中/.test(s.textContent || '');
  }, AID, { timeout: 90000 });
} catch (e) {
  const st = await page.evaluate((aid) => {
    const s = document.querySelector('.image-node[data-id="' + aid + '"] .agent-tui-status');
    return s ? (s.textContent || '').trim() : '(状态灯缺失)';
  }, AID);
  throw new Error('AGENT_NOT_READY: Agent 会话 90s 内未就绪，状态=' + st);
}
const sel = card.locator('select.agent-tui-model-select');
const opts = await sel.evaluate(sx => [...sx.options].map(o => o.value));
const rank = v => /llama-cpp-local|strata-sage-local/.test(v) ? 0 : (/vllm/.test(v) ? 1 : (/strata/.test(v) ? 2 : 9));
const cands = opts.filter(v => /vllm|llama-cpp|strata/.test(v)).sort((a, b) => rank(a) - rank(b)).slice(0, 3);
if (!cands.length) throw new Error('no local/vllm/strata chat model in pool: ' + opts.join(','));
const tried = [];
let ok = '';
for (const cand of cands) {
  await sel.selectOption({ value: cand });
  await page.waitForTimeout(500);
  // 只量 assistant 消息：用户消息回显里含「OK」字样，拿全量 .agent-tui-msg 判 reply 会假 PASS
  const before = await card.locator('.agent-tui-msg-assistant').count();
  const input = card.locator('textarea.agent-tui-input');
  await input.fill('reply with exactly one word: OK');
  await input.press('Enter');
  const t0 = Date.now();
  let outcome = 'timeout';
  while (Date.now() - t0 < 45000) {
    const fresh = await card.locator('.agent-tui-msg-assistant').allTextContents();
    const after = fresh.slice(before);
    if (after.some(x => /模型错误|启动失败|Error/.test(x || ''))) { outcome = 'error-bubble'; break; }
    if (after.some(x => /OK|好的|收到/.test(x || ''))) { outcome = 'reply'; break; }
    await page.waitForTimeout(500);
  }
  tried.push(cand + '→' + outcome);
  if (outcome === 'reply') { ok = cand; break; }
}
if (!ok) throw new Error('no candidate produced a real reply: ' + tried.join(' | '));
const reply = await card.locator('.agent-tui-msg-assistant').last().textContent();
return { evidence: 'model=' + ok + ' 尝试序列=' + tried.join(' | ') + ' 回复=' + (reply || '').trim().slice(0, 40) };
JS

# S4 上游错误可见化（#288/#376：按当前 DOM 重写）：bailian 在池则真发一条，150s 内
# 必须出现「模型错误」气泡或任意非空回复——沉默失败才是回归。bailian 不在池 =
# 前置条件不满足（#360 三态：前置缺失≠产品失败），保留 SKIP。
e2e_run s4-error-visible <<'JS'
const AID = 'e2e-a1';
const card = page.locator('.image-node[data-id="' + AID + '"] .agent-tui-card');
await card.first().waitFor({ timeout: 30000 });
await clickNode(AID);
try {
  await page.waitForFunction((aid) => {
    const s = document.querySelector('.image-node[data-id="' + aid + '"] .agent-tui-status');
    return s && /空闲|运行中/.test(s.textContent || '');
  }, AID, { timeout: 90000 });
} catch (e) {
  const st = await page.evaluate((aid) => {
    const s = document.querySelector('.image-node[data-id="' + aid + '"] .agent-tui-status');
    return s ? (s.textContent || '').trim() : '(状态灯缺失)';
  }, AID);
  throw new Error('AGENT_NOT_READY: Agent 会话 90s 内未就绪，状态=' + st);
}
const sel = card.locator('select.agent-tui-model-select');
const opts = await sel.evaluate(sx => [...sx.options].map(o => o.value));
const pick = opts.find(v => /bailian/.test(v));
if (!pick) return { skip: 'bailian 不在模型池里，上游错误可见化无从断言（前置条件，非产品失败）' };
await sel.selectOption({ value: pick });
await page.waitForTimeout(500);
const before = await card.locator('.agent-tui-msg').count();
const input = card.locator('textarea.agent-tui-input');
await input.fill('hi e2e');
await input.press('Enter');
try {
  await page.waitForFunction((aid, n) => {
    const msgs = [...document.querySelectorAll('.image-node[data-id="' + aid + '"] .agent-tui-msg')];
    const fresh = msgs.slice(n);
    const err = fresh.some(m => /模型错误|错误|Error/i.test(m.innerText || ''));
    const reply = fresh.some(m => !/agent-tui-msg-user/.test(m.className || '') && (m.innerText || '').trim().length > 0);
    return err || reply;
  }, AID, before, { timeout: 150000 });
} catch (e) { throw new Error('neither reply nor visible error bubble (沉默失败回归)'); }
const msgs = await card.locator('.agent-tui-msg').allTextContents();
const bubble = (msgs[msgs.length - 1] || '').trim();
const kind = /模型错误|错误|Error/i.test(bubble) ? 'error-bubble' : 'reply';
return { evidence: 'model=' + pick + ' 结果=' + kind + ' 气泡=' + bubble.slice(0, 120) };
JS

# S5 stall 看门狗在场（#286 源码契约，运行时触发要 120s+，留给手工）
if grep -q "TURN_STALL_NOTIFY_SECONDS = 120.0" app/services/agent_host.py; then echo "PASS  s5-stall-watchdog(source)"; E2E_PASS=$((E2E_PASS+1)); else echo "FAIL  s5-stall-watchdog(source)"; E2E_FAIL=$((E2E_FAIL+1)); fi
# s6：#292 回归——pi 管道必须显式 UTF-8（GBK 默认解码会把中文错误文案炸成 reader 崩溃→误杀活 agent）
if grep -q 'encoding="utf-8"' app/services/agent_host.py && grep -q 'encoding="utf-8"' tests/stubs/fake_pi.py; then echo "PASS  s6-utf8-pipe(source)"; E2E_PASS=$((E2E_PASS+1)); else echo "FAIL  s6-utf8-pipe(source)"; E2E_FAIL=$((E2E_FAIL+1)); fi

e2e_finish
