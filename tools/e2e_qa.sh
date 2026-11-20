#!/usr/bin/env bash
# e2e_qa.sh — 真实网页 QA；隔离的 31xx 实例和项目由 e2e_common 管理（#360）。
# 用法: python tests/tabbit_e2e_evidence.py --suite tools/e2e_qa.sh
# SKIP 和源码检查不计入浏览器 PASS。
set -u
export E2E_AGENT_NODE=1
QA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$QA_ROOT/tools/e2e/e2e_common.sh"
e2e_start qa "Firefly E2E QA" 25000

# S1：#317 的 stream 握手应显示就绪，但浏览画布本身不得启动 pi 或发起回合。
e2e_run s1-agent-connected <<'JS'
const aid = 'e2e-a1';
const card = `.image-node[data-id="${aid}"]`;
await page.waitForSelector(`${card} .agent-card-status.agent-tui-status-idle`, {state: 'visible', timeout: 5000});
const health = await page.evaluate(async pid => {
  const resp = await fetch('/api/healthz', {cache: 'no-store'});
  if (!resp.ok) throw new Error('HEALTHZ_HTTP_' + resp.status);
  const json = await resp.json();
  return {status: json.status, sessions: json.agents.sessions.filter(s => s.project_id === pid)};
}, E2E_PID);
const session = health.sessions.find(s => s.node_id === aid);
// 不能仅凭 by_status.crashed=0 通过：没握手或空会话表同样不 crashed。
if (health.status !== 'ok' || !session || session.status !== 'stopped' || session.alive !== false) {
  throw new Error('S1_OFFLINE_SESSION: ' + JSON.stringify(health));
}
if (health.sessions.some(s => s.status === 'crashed' || s.alive)) throw new Error('S1_UNEXPECTED_HOST: ' + JSON.stringify(health));
await page.waitForSelector(`${card} .agent-tui-card [data-agent-tui-input="${aid}"]`, {state: 'visible', timeout: 2500});
return {evidence: 'fixture agent stream ready; server session stopped, no host spawned'};
JS

# S2：只访问 launcher 隔离的模型目录（#285）。空模型池不能作为分类过滤已验证的证据。
e2e_run s2-model-pool <<'JS'
const pool = await page.evaluate(async pid => {
  const resp = await fetch(`/api/projects/${encodeURIComponent(pid)}/agent-nodes/chat-models`, {cache: 'no-store'});
  if (!resp.ok) throw new Error('MODEL_POOL_HTTP_' + resp.status);
  return resp.json();
}, E2E_PID);
if (!Array.isArray(pool.models) || typeof pool.current !== 'string') throw new Error('INVALID_MODEL_POOL');
if (pool.models.some(m => typeof m?.id !== 'string' || !m.id || m.id !== `${m.provider}/${m.model}`)) throw new Error('INVALID_MODEL_IDS');
const leaked = pool.models.map(m => m.id).filter(v => /image|classifier|embedding|ranker/i.test(v));
if (leaked.length) throw new Error('non-chat models leaked: ' + leaked.join(','));
if (!pool.models.length) {
  if (pool.current) throw new Error('EMPTY_POOL_WITH_DEFAULT: ' + pool.current);
  return {skip: 'KNOWN-GAP: 隔离模型池返回合法空列表；无模型夹具，未验证过滤'};
}
return {evidence: `no-store chat-models: ${pool.models.length} entries, no non-chat IDs`};
JS

# S3/S4：只有 launcher 明确提供隔离的本地对话夹具时才提交指令；不选用户模型或远端模型。
# E2E_LOCAL_CHAT_FIXTURE 可选：reply/error 分别保证该场景默认路由到本地成功/失败夹具。
e2e_run s3-real-reply <<'JS'
const aid = 'e2e-a1';
const input = `.image-node[data-id="${aid}"] .agent-tui-card [data-agent-tui-input="${aid}"]`;
await clearSelection();
await page.keyboard.press('Control+0');
// 第一次点 Agent 卡会选中并可能重建 TUI，再找一次当前的输入框。
await clickNode(aid);
await page.waitForFunction(id => canvasState.selectedId === id, aid, {timeout: 3000});
await clickAt(page.locator(input));
await page.waitForFunction(selector => document.querySelector(selector) === document.activeElement, input, {timeout: 3000});
await page.keyboard.type('reply with exactly one word: OK');
const draft = await page.evaluate(selector => {
  const input = document.querySelector(selector);
  return {value: input?.value, focused: input === document.activeElement};
}, input);
if (draft.value !== 'reply with exactly one word: OK' || !draft.focused) throw new Error('S3_DRAFT_INPUT: ' + JSON.stringify(draft));
if (typeof E2E_LOCAL_CHAT_FIXTURE === 'undefined' || E2E_LOCAL_CHAT_FIXTURE?.reply !== true) {
  return {skip: 'KNOWN-GAP: 已实测草稿输入；无本地对话回复夹具，未发送指令，未验证新增 assistant 消息'};
}
const selector = `.image-node[data-id="${aid}"] .agent-tui-msg-assistant .agent-tui-msg-text`;
const before = await page.evaluate(s => document.querySelectorAll(s).length, selector);
await page.keyboard.press('Enter');
await page.waitForFunction(({selector, before}) => [...document.querySelectorAll(selector)].slice(before).some(el =>
  !el.closest('.agent-tui-msg-streaming') && /^OK[.!]?$/i.test(el.innerText.trim())), {selector, before}, {timeout: 8000});
return {evidence: 'local fixture returned OK in a new completed assistant message'};
JS

# S4：没有回合结果不算通过；只认新增的可见非用户气泡。
e2e_run s4-error-visible <<'JS'
const card = '.image-node[data-id="e2e-a1"]';
await page.waitForSelector(`${card} .agent-tui-messages[aria-live="polite"]`, {state: 'visible', timeout: 2500});
if (typeof E2E_LOCAL_CHAT_FIXTURE === 'undefined' || E2E_LOCAL_CHAT_FIXTURE?.error !== true) {
  return {skip: 'KNOWN-GAP: 无本地对话失败夹具；未发送指令，未验证新增的错误气泡或 assistant 回复'};
}
const selector = `${card} .agent-tui-msg:not(.agent-tui-msg-user) .agent-tui-msg-text`;
const before = await page.evaluate(s => document.querySelectorAll(s).length, selector);
await page.keyboard.press('Control+0');
await clickAt(page.locator(`${card} [data-agent-tui-input="e2e-a1"]`));
await page.keyboard.type('hi e2e');
await page.keyboard.press('Enter');
const visibleResult = ({selector, before}) => [...document.querySelectorAll(selector)].slice(before).some(el => {
  if (!el.getClientRects().length || getComputedStyle(el).visibility !== 'visible') return false;
  const text = el.innerText.trim();
  return /模型错误|错误|Error/i.test(text) || (el.closest('.agent-tui-msg-assistant') && text.length > 0);
});
await page.waitForFunction(visibleResult, {selector, before}, {timeout: 8000});
return {evidence: 'local fixture turn produced a visible error or assistant reply'};
JS

# S5/S6 只做契约源码检查，不参与浏览器结果与退出码。
if grep -q 'TURN_STALL_NOTIFY_SECONDS = 120.0' "$QA_ROOT/app/services/agent_host.py"; then
  echo 'SOURCE-ONLY OK s5-stall-watchdog'
else
  echo 'SOURCE-ONLY FAIL s5-stall-watchdog'
fi
if grep -q 'encoding="utf-8"' "$QA_ROOT/app/services/agent_host.py" && grep -q 'encoding="utf-8"' "$QA_ROOT/tests/stubs/fake_pi.py"; then
  echo 'SOURCE-ONLY OK s6-utf8-pipe'
else
  echo 'SOURCE-ONLY FAIL s6-utf8-pipe'
fi

e2e_finish
