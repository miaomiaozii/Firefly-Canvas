#!/usr/bin/env bash
# e2e_qa.sh — Firefly-Canvas 端到端真实网页 QA（tabbit 驱动真实浏览器，非截图、断言 DOM 状态）
# 用法: bash tools/e2e_qa.sh [base_url]   默认 http://127.0.0.1:3000
# 退出码: 0=全部通过; N=失败场景数。每个场景打印 PASS/FAIL + 证据 JSON。
# 原则(#288 教训): 只信 waitForFunction 的 DOM 断言，不信“看起来启动了”。
set -u
BASE="${1:-http://127.0.0.1:3000}"
TABBIT="$LOCALAPPDATA/Tabbit/LocalAgent/bin/tabbit-cli.exe"
TABBIT="${TABBIT:-$HOME/.local/Tabbit/LocalAgent/bin/tabbit-cli.exe}"
PROJ="${FIREFLY_QA_PROJECT:-}"
FAILS=0
if [ ! -x "$TABBIT" ]; then echo "FATAL: tabbit-cli 不存在: $TABBIT"; exit 99; fi

# 项目 id：healthz 第一个 open project
PID=$(curl -s -m 5 "$BASE/api/healthz" | python -c 'import sys,json;d=json.load(sys.stdin);ids=d.get("open_project_ids") or [];print(ids[0] if ids else "")' 2>/dev/null)
[ -z "$PID" ] && { echo "FATAL: $BASE 上没有打开的项目（先打开一个画布再跑 QA）"; exit 98; }
URL="$BASE/static/smart-canvas.html?id=$PID&project=$PID&project_mode=1"

run(){ # run <name> <场景 JS 从 stdin 进入>；tab 偶发 about:blank，失败自动重跑一次
  local name="$1" out i js
  js=$(cat)
  for i in 1 2; do
    out=$("$TABBIT" nodejs --task "Firefly E2E QA" --request-id "qa-$name-$i" --timeout-ms 180000 <<JS 2>&1
await page.goto("$URL", {waitUntil: "domcontentloaded"});
if (!page.url().includes('smart-canvas')) throw new Error('NAV_FAIL: ' + page.url());
await page.waitForSelector('.agent-tui-card, [class*="agent-tui"]', {timeout: 30000}).catch(()=>{});
await page.waitForTimeout(4000);
$js
JS
)
    if echo "$out" | grep -q '"status":"ok"'; then echo "PASS  $name  $(echo "$out" | grep -o '"evidence":"[^"]*"' | head -1)"; return; fi
  done
  echo "FAIL  $name"; echo "$out" | tail -5; FAILS=$((FAILS+1))
}

echo "== Firefly E2E QA @ $URL =="

# S1 Agent 卡片连接成功（无“启动失败/Failed to start”；重启后卡片有退避重试，给 90s 收敛）
run s1-agent-connected <<'JS'
// #288 教训升级：卡片文字残留(chat history 含旧“启动失败”)不作数——以服务端 healthz 为真相
var st = await page.evaluate(async () => (await (await fetch('/api/healthz', {cache:'no-store'})).json()).agents.by_status);
if (st.crashed) {
  // crashed 态下卡片应提供“重试连接”；点击→等待→以 healthz 复核
  await page.evaluate(() => { const b=[...document.querySelectorAll('button')].find(x=>/重试连接|启动|连接/.test(x.textContent)); b&&b.click(); });
  await page.waitForTimeout(15000);
  st = await page.evaluate(async () => (await (await fetch('/api/healthz', {cache:'no-store'})).json()).agents.by_status);
}
if (st.crashed) throw new Error('crashed 态点重试后仍 crashed（agent 无法复活）: ' + JSON.stringify(st));
if (st.crashed) throw new Error('agent crashed: ' + JSON.stringify(st));
var msgs = await page.evaluate(() => [...document.querySelectorAll('[class*="agent-tui"]')].length);
if (msgs < 3) throw new Error('agent tui elements missing');
return {status:'ok', evidence:'healthz ' + JSON.stringify(st)};
JS
# S2 模型池新鲜且不含 image/classifier（#285 + cache:no-store 回归）
run s2-model-pool <<JS
var pool = await page.evaluate(async pid => (await (await fetch('/api/projects/' + pid + '/agent-nodes/chat-models', {cache:'no-store'})).json()), "$PID");
var sel = await page.evaluate(() => [...(document.querySelector('select.agent-tui-model-select')||{options:[]}).options].map(o=>o.value));
if (!sel.length) throw new Error('model select empty/absent');
if (!pool.models || !pool.models.length) throw new Error('pool empty');
var leaked = sel.filter(v => /image|classifier|embedding|ranker/i.test(v));
if (leaked.length) throw new Error('non-chat models leaked: ' + leaked.join(','));
if (sel.join('|') !== '|' + pool.models.map(m=>m.id).join('|')) throw new Error('select stale vs pool (cache bug)');
return {status:'ok', evidence: pool.models.map(m=>m.id).slice(0,3).join('|')};
JS

# S3 真实对话有回复（本地 vllm 优先；无则 llama-cpp-local）
run s3-real-reply <<'JS'
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
return {status:'ok', evidence: pick};
JS

# S4 上游错误可见化（#288）：仅当 bailian 存在时测；403/404/401 必须是“模型错误”气泡而非沉默
run s4-error-visible <<JS
var card = page.locator('.agent-tui-card').first();
var sel = card.locator('select.agent-tui-model-select');
var opts = await sel.evaluate(sx => [...sx.options].map(o=>o.value));
var pick = opts.find(v=>/bailian/.test(v));
if (!pick) return {status:'ok', evidence:'bailian absent, skip'};
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
return {status:'ok', evidence: bubble};
JS

# S5 stall 看门狗在场（#286 源码契约，运行时触发要 120s+，留给手工）
if grep -q "TURN_STALL_NOTIFY_SECONDS = 120.0" app/services/agent_host.py; then echo "PASS  s5-stall-watchdog(source)"; else echo "FAIL  s5-stall-watchdog(source)"; FAILS=$((FAILS+1)); fi
# s6：#292 回归——pi 管道必须显式 UTF-8（GBK 默认解码会把中文错误文案炸成 reader 崩溃→误杀活 agent）
if grep -q 'encoding="utf-8"' app/services/agent_host.py && grep -q 'encoding="utf-8"' tests/stubs/fake_pi.py; then echo "PASS  s6-utf8-pipe(source)"; else echo "FAIL  s6-utf8-pipe(source)"; FAILS=$((FAILS+1)); fi

echo "== 失败数: $FAILS =="
# 标签卫生（用户实测反馈：E2E 遗留标签挤占浏览器资源）：套件退出前销毁自己的标签；
# 不碰非本任务名下的用户标签。
"$TABBIT" finish --task "Firefly E2E QA" --discard >/dev/null 2>&1 || true
exit $FAILS
