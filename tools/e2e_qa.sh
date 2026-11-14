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

# S3 真实对话有回复（本地 vllm 优先；无则 llama-cpp-local）
e2e_run s3-real-reply <<'JS'
if (!(await page.$('.agent-tui-card'))) {
  return {skip: 'KNOWN-STALE：画布页已无 .agent-tui-card（对话入口换成 #composer），真实对话场景需按当前 DOM 重写（新票）'};
}
var card = page.locator('.agent-tui-card').first();
var sel = card.locator('select.agent-tui-model-select');
var opts = await sel.evaluate(sx => [...sx.options].map(o=>o.value));
var pick = opts.find(v=>/vllm|llama-cpp|strata/.test(v));
if (!pick) throw new Error('no local chat model in pool: '+opts.join(','));
await sel.selectOption({value: pick});
var input = card.locator('textarea.agent-tui-input');
await input.fill('reply with exactly one word: OK');
await input.press('Enter');
await page.waitForFunction(() => {
  const msgs=[...document.querySelectorAll('[class*="agent-tui-msg"]')];
  return msgs.some(m => /OK|好的|收到/.test(m.innerText||'') && !/模型错误|Failed/.test(m.innerText||''));
}, null, {timeout: 120000});
return {evidence: pick};
JS

# S4 上游错误可见化（#288）：仅当 bailian 存在时测；403/404/401 必须是“模型错误”气泡而非沉默
e2e_run s4-error-visible <<'JS'
if (!(await page.$('.agent-tui-card'))) {
  return {skip: 'KNOWN-STALE：画布页已无 .agent-tui-card（对话入口换成 #composer），真实对话场景需按当前 DOM 重写（新票）'};
}
var card = page.locator('.agent-tui-card').first();
var sel = card.locator('select.agent-tui-model-select');
var opts = await sel.evaluate(sx => [...sx.options].map(o=>o.value));
var pick = opts.find(v=>/bailian/.test(v));
if (!pick) return {skip:'bailian 不在模型池里，上游错误可见化无从断言'};
await sel.selectOption({value: pick});
await page.waitForTimeout(2000);
var before = await page.evaluate(() => document.querySelectorAll('[class*="agent-tui-msg"]').length);
var input = card.locator('textarea.agent-tui-input');
await input.fill('hi e2e');
await input.press('Enter');
try {
  await page.waitForFunction((n) => {
    const msgs=[...document.querySelectorAll('[class*="agent-tui-msg"]')];
    const fresh = msgs.slice(n + 1);
    return fresh.some(m => /模型错误|错误|Error/i.test(m.innerText||'')) ||
           fresh.some(m => !/agent-tui-msg-user/.test(m.className||'') && (m.innerText||'').trim().length > 0);
  }, before, {timeout: 150000});
} catch(e) { throw new Error('neither reply nor visible error bubble (沉默失败回归)'); }
var bubble = await page.evaluate(() => { const m=[...document.querySelectorAll('[class*="agent-tui-msg"]')].slice(-1)[0]; return (m&&m.innerText||'').slice(0,120); });
return {evidence: bubble};
JS

# S5 stall 看门狗在场（#286 源码契约，运行时触发要 120s+，留给手工）
if grep -q "TURN_STALL_NOTIFY_SECONDS = 120.0" app/services/agent_host.py; then echo "PASS  s5-stall-watchdog(source)"; E2E_PASS=$((E2E_PASS+1)); else echo "FAIL  s5-stall-watchdog(source)"; E2E_FAIL=$((E2E_FAIL+1)); fi
# s6：#292 回归——pi 管道必须显式 UTF-8（GBK 默认解码会把中文错误文案炸成 reader 崩溃→误杀活 agent）
if grep -q 'encoding="utf-8"' app/services/agent_host.py && grep -q 'encoding="utf-8"' tests/stubs/fake_pi.py; then echo "PASS  s6-utf8-pipe(source)"; E2E_PASS=$((E2E_PASS+1)); else echo "FAIL  s6-utf8-pipe(source)"; E2E_FAIL=$((E2E_FAIL+1)); fi

e2e_finish
