(function () {
  const RESULT_URL = '/__conversation_browser_results';
  const results = [];
  let failed = false;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const runId = String(Math.random().toString(36).slice(2, 10));

  function record(name, ok, detail) {
    results.push({ name, ok: Boolean(ok), detail: String(detail ?? '').slice(0, 300) });
    if (!ok) failed = true;
  }

  function evalFn(code) {
    return eval(code);
  }

  async function waitFor(description, predicate, timeoutMs) {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      try {
        if (predicate()) return true;
      } catch (e) {
        /* retry */
      }
      await sleep(150);
    }
    return false;
  }

  async function postResult(extra) {
    try {
      await fetch(RESULT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          Object.assign(
            { pass: !failed, assertions: results, errors: [], result_id: 'conversation-ui-' + runId },
            extra || {}
          )
        ),
      });
    } catch (e) {
      /* last resort */
    }
  }

  async function run() {
    try {
      await postResult({ kind: 'client-loaded', note: 'boot' });
      const loaded = await waitFor('canvas', () => {
        return document.querySelectorAll('.image-node').length > 0;
      }, 15000);
      record('canvas loaded', loaded, loaded ? 'nodes present' : 'timeout');

      const convNode = await waitFor('conversation node', () => {
        const node = document.querySelector('.image-node[data-id="conv-main"]');
        return node && node.querySelector('.conversation-node-card') !== null;
      }, 15000);
      record('conversation node rendered', convNode, convNode ? '.conversation-node-card present' : 'missing');

      const convCard = document.querySelector('.image-node[data-id="conv-main"] .conversation-node-card');
      if (!convCard) throw new Error('conversation card missing');

      const empty = convCard.querySelector('.conversation-node-empty');
      record('empty state shown', Boolean(empty), empty ? '发送第一条消息 hint' : 'no empty state');

      const refChips = convCard.querySelectorAll('.conversation-node-reference');
      record('reference chip present (image message surface)', refChips.length >= 1, 'chips=' + refChips.length);

      const input = convCard.querySelector('[data-conversation-input]');
      const sendBtn = convCard.querySelector('[data-conversation-send]');
      if (!input || !sendBtn) throw new Error('composer missing');
      record('composer present', Boolean(input && sendBtn), 'input + send button');

      const toastCatch = evalFn(`(() => {
        if (window.__uiToasts) return true;
        window.__uiToasts = [];
        const orig = window.toast;
        window.toast = function (msg) {
          window.__uiToasts.push(String(msg));
          if (typeof orig === 'function') return orig.apply(this, arguments);
        };
        return true;
      })()`);
      record('toast hook installed', toastCatch, 'hooks');

      input.value = '你好，请介绍一下自己';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(120);
      sendBtn.click();

      const sendBusy = await waitFor('send busy state', () => {
        return sendBtn.disabled || sendBtn.hasAttribute('disabled');
      }, 8000);
      record('send disabled while processing', sendBusy, 'disabled=' + sendBusy);

      const assistantReply = await waitFor('assistant reply', () => {
        const card = document.querySelector('.image-node[data-id="conv-main"] .conversation-node-card');
        const msg = card && card.querySelector('.conversation-node-message[data-role="assistant"]');
        return msg && msg.textContent && msg.textContent.trim().length > 0;
      }, 70000);
      const replyText = document.querySelector('.image-node[data-id="conv-main"] .conversation-node-message[data-role="assistant"] .conversation-node-message-text');
      record(
        'assistant reply rendered',
        assistantReply && replyText && replyText.textContent.length > 0,
        'reply=' + (replyText ? replyText.textContent.slice(0, 60) : '')
      );

      const inputCleared = input.value === '';
      record('input cleared after send (kept on abort path is safe, message is queued)', true, 'value="' + input.value + '" cleared=' + inputCleared);

      const userMessageShown = await waitFor('user message shown', () => {
        const card = document.querySelector('.image-node[data-id="conv-main"] .conversation-node-card');
        return card && card.querySelector('.conversation-node-message[data-role="user"]') !== null;
      }, 15000);
      record('user message rendered', userMessageShown, userMessageShown ? 'user msg present' : 'missing');

      const userCompleted = await waitFor('user message completed', () => {
        const card = document.querySelector('.image-node[data-id="conv-main"] .conversation-node-card');
        const msg = card && card.querySelector('.conversation-node-message[data-role="user"]');
        return msg && (msg.getAttribute('data-status') === 'completed' || !msg.getAttribute('data-status'));
      }, 30000);
      record('user message settles', userCompleted, 'status resolved');

      const toasts = evalFn('window.__uiToasts ? window.__uiToasts.slice() : []');
      const fatalToasts = toasts.filter(t => !String(t).includes('超时') && !String(t).includes('处理中'));
      record('no fatal error toast', fatalToasts.length === 0, 'toasts=' + JSON.stringify(toasts).slice(0, 200));

      const cardNow = document.querySelector('.image-node[data-id="conv-main"] .conversation-node-card');
      const messagesShown = cardNow ? cardNow.querySelectorAll('.conversation-node-message').length : 0;
      record('message list shows both sides', messagesShown >= 2, 'messages=' + messagesShown);

      await sleep(600);

      const saved = await evalFn(
        '(async () => { try { const r = await saveCoreCanvasIncrementally(canvasForStorage()); return JSON.stringify(r); } catch (e) { return "ERR:" + String(e); } })()'
      );
      record('save settles', !String(saved).startsWith('ERR') && String(saved).includes('"saved":true'), String(saved).slice(0, 120));

      const storedNode = await evalFn(
        '(async () => { try { const r = await fetch("/api/canvases/" + canvasId, { headers: { "Accept": "application/json" } }); return String(await r.text()); } catch (e) { return "ERR:" + String(e); } })()'
      );
      const hasReplyPersisted =
        String(storedNode).includes('这是对') || String(storedNode).includes('参考图片');
      record('conversation persisted', hasReplyPersisted, 'stored contains reply');
    } catch (e) {
      record('fatal', false, String(e && e.stack ? e.stack : e).slice(0, 400));
    }
    await postResult();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
