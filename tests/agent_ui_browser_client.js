(async function () {
  const query = new URLSearchParams(location.search);
  window.__agentUiPid = query.get('project') || '';
  const runId = Date.now() + Math.random().toString(36).slice(2);
  const results = [];
  const errors = [];

  function record(name, ok, detail) {
    results.push({ name, ok: Boolean(ok), detail: detail == null ? '' : String(detail).slice(0, 200) });
  }

  async function post() {
    try {
      await fetch('/__conversation_browser_results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pass: errors.length === 0, assertions: results, errors, result_id: 'agent-ui-' + runId }),
      });
    } catch (e) {
      errors.push('post failed: ' + String(e));
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitFor(name, pred, timeout) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const value = pred();
      if (value) return value;
      await sleep(250);
    }
    return null;
  }

  function evalFn(code) {
    try {
      return (0, eval)(code);
    } catch (e) {
      return 'ERR:' + String(e);
    }
  }

  const agentEl = () => document.querySelector('.agent-smart-node[data-id="agent-ui"]');
  const taskCards = () => document.querySelectorAll('.agent-task-card');
  const planStatuses = () => document.querySelectorAll('.agent-plan-task-status[data-status]');
  const nodeMessages = () => document.querySelectorAll('.agent-node-message');
  const toastCatch = evalFn(`(() => {
    if (window.__agentUiToasts) return true;
    window.__agentUiToasts = [];
    const orig = window.toast;
    window.toast = function (msg) {
      window.__agentUiToasts.push(String(msg));
      if (typeof orig === 'function') return orig.apply(this, arguments);
    };
    return true;
  })()`);

  try {
    const canvasReady = await waitFor('canvas ready', () => {
      return evalFn('(typeof canvas !== "undefined" && canvas && Array.isArray(canvas.nodes) && canvas.nodes.length > 0) ? canvas.nodes.length : 0');
    }, 20000);
    record('canvas loaded', Boolean(canvasReady), 'agent node present; nodes=' + canvasReady + ' ids=' + evalFn('(canvas && canvas.nodes) ? canvas.nodes.map(n=>n.id).join(",") : "none"'));
    const agentNode = await waitFor('agent node rendered', () => {
      return document.querySelector('.agent-smart-node[data-id="agent-ui"]') ? true : false;
    }, 10000);
    if (!agentNode) throw new Error('agent node not rendered');
    const goal = agentEl()?.querySelector('[data-agent-goal]');
    if (!goal) throw new Error('goal input missing');
    const firstDraft = agentEl()?.querySelector('[data-agent-draft-index="0"]');
    const draftControls = firstDraft ? [...firstDraft.querySelectorAll('select,input')] : [];
    const agentStyle = getComputedStyle(agentEl());
    const goalStyle = getComputedStyle(goal);
    const draftStyle = firstDraft ? getComputedStyle(firstDraft) : null;
    record('agent node uses the studio panel surface', agentStyle.backgroundColor !== 'rgba(0, 0, 0, 0)' && Number.parseFloat(agentStyle.borderTopWidth) >= 1, {background:agentStyle.backgroundColor, border:agentStyle.borderTopWidth});
    record('agent goal uses readable form typography and compact geometry', Number.parseFloat(goalStyle.fontSize) >= 13 && goal.getBoundingClientRect().height >= 58 && goal.getBoundingClientRect().height <= 90, {font:goalStyle.fontSize, height:goal.getBoundingClientRect().height});
    record('agent draft is a bounded compact card', Boolean(firstDraft) && Number.parseFloat(draftStyle?.borderTopWidth || '0') >= 1 && firstDraft.getBoundingClientRect().width <= agentEl().getBoundingClientRect().width, firstDraft ? {draft:firstDraft.getBoundingClientRect().toJSON(), node:agentEl().getBoundingClientRect().toJSON()} : 'missing');
    const referenceFieldStyle = draftControls.length ? getComputedStyle(draftControls[0]) : null;
    record('agent draft fields use themed controls instead of browser defaults', draftControls.length >= 4 && draftControls.every((el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.backgroundColor === referenceFieldStyle?.backgroundColor
        && style.color === referenceFieldStyle?.color
        && style.backgroundColor !== 'rgb(255, 255, 255)'
        && Number.parseFloat(style.fontSize) >= 12
        && rect.height >= 30
        && rect.height <= 34;
    }), draftControls.map((el) => ({tag:el.tagName, font:getComputedStyle(el).fontSize, height:el.getBoundingClientRect().height, background:getComputedStyle(el).backgroundColor})));
    goal.value = '测试取消与重试';
    goal.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(150);

    const add = agentEl()?.querySelector('[data-agent-draft-add]');
    add?.click();
    await sleep(300);
    const draftCount = agentEl()?.querySelectorAll('[data-agent-draft-index]').length || 0;
    record('draft cards added', draftCount >= 2, 'drafts=' + draftCount);

    const p0 = agentEl()?.querySelector('[data-agent-draft-index="0"] [data-agent-draft-prompt]');
    const p1 = agentEl()?.querySelector('[data-agent-draft-index="1"] [data-agent-draft-prompt]');
    if (!p0 || !p1) throw new Error('draft prompt inputs missing');
    p0.value = 'slow: 生成一张慢图';
    p0.dispatchEvent(new Event('input', { bubbles: true }));
    p1.value = 'fail: 生成一张失败图';
    p1.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(150);

    agentEl()?.querySelector('[data-agent-plan]')?.click();
    const planned = await waitFor('plan awaiting', () => {
      const st = agentEl()?.querySelector('[data-agent-plan-status]');
      return st && /确认|awaiting|confirm/i.test(st.textContent || '');
    }, 15000);
    record('plan reaches awaiting confirmation', Boolean(planned), 'status text=' + String(agentEl()?.querySelector('[data-agent-plan-status]')?.textContent || '').slice(0, 80));
    const planTasks = agentEl()?.querySelectorAll('[data-agent-plan-task]').length || 0;
    record('plan panel lists tasks', planTasks >= 2, 'tasks=' + planTasks);

    agentEl()?.querySelector('[data-agent-confirm]')?.click();

    const cancelBtnAppears = await waitFor('cancel button appears', () => {
      const btn = [...document.querySelectorAll('.agent-task-cancel[data-agent-cancel-task]')]
        .find((b) => b.closest('.agent-task-card')?.querySelector('.agent-task-status')?.getAttribute('data-status') === 'running');
      return btn || null;
    }, 5000);
        const cardsBefore = evalFn(`(() => { const cards = [...document.querySelectorAll('.agent-task-node [data-agent-task-card]')].map((el) => ({ tid: el.getAttribute('data-agent-task-card'), status: el.querySelector('.agent-task-status')?.getAttribute('data-status'), prompt: (el.querySelector('.agent-task-prompt')?.textContent || '').slice(0, 20) })); return JSON.stringify(cards); })()`);
        record('diag cards before cancel', true, String(cardsBefore).slice(0, 300));
    record('cancel button visible while running', Boolean(cancelBtnAppears), 'cancel btn present');

    if (cancelBtnAppears) {
      evalFn(`(() => {
        const b = [...document.querySelectorAll('.agent-task-cancel[data-agent-cancel-task]')]
          .find((x) => x.closest('.agent-task-card')?.querySelector('.agent-task-status')?.getAttribute('data-status') === 'running');
        if (b) b.click();
        return Boolean(b);
      })()`);
      const cancelled = await waitFor('task cancelled', () => {
        return [...taskCards()].some((el) => el.querySelector('.agent-task-status')?.getAttribute('data-status') === 'cancelled');
      }, 45000);
      const cancelledActual = [...taskCards()].map((el) => el.querySelector('.agent-task-status')?.getAttribute('data-status')).join(',');
      record('task reaches cancelled', Boolean(cancelled), 'status=' + cancelledActual);
      if (!cancelled) {
        const dump = await evalFn(`(async () => {
          try {
            const pid = window.__agentUiPid || '';
            const r = await fetch('/api/project-library/' + pid).then(x => x.json());
            const tasks = (r.canvas?.nodes || []).filter(n => n.type === 'agent-task');
            return JSON.stringify({ pid: pid, tasks: tasks.map(t => ({ id: t.id, status: t.status })) });
          } catch (e) { return 'ERR:' + String(e); }
        })()`);
        record('post-cancel frontend dump', true, JSON.stringify(Array.from(document.querySelectorAll('.agent-task-card')).map((card) => ({ tid: card.getAttribute('data-agent-task-card'), status: card.querySelector('.agent-task-status')?.getAttribute('data-status') || 'none' }))));
      }
    } else {
      record('task reaches cancelled', false, 'no cancel button');
    }

    const retryBtnAppears = await waitFor('retry button appears', () => {
      const btn = document.querySelector('.agent-task-retry[data-agent-retry-task]');
      return btn ? btn : null;
    }, 25000);
    record('retry button visible for failed task', Boolean(retryBtnAppears), 'retry btn present');

    if (retryBtnAppears) {
      evalFn(`(() => { const b = document.querySelector('.agent-task-retry[data-agent-retry-task]'); if (b) b.click(); return Boolean(b); })()`);
      const retried = await waitFor('retried task succeeds', () => {
        return [...taskCards()].some((el) => el.querySelector('.agent-task-status')?.getAttribute('data-status') === 'succeeded');
      }, 45000);
      const retriedActual = [...taskCards()].map((el) => el.querySelector('.agent-task-status')?.getAttribute('data-status')).join(',');
      record('retried task succeeds', Boolean(retried), 'status=' + retriedActual);
      if (!retried) {
        record('retry debug dom', true, JSON.stringify({
          planRows: document.querySelectorAll('.agent-plan-task').length,
          planStatuses: [...document.querySelectorAll('.agent-plan-task-status')].map((el) => (el.getAttribute('data-agent-task-status') || '').slice(-8) + ':' + (el.getAttribute('data-status') || '')),
          taskCards: document.querySelectorAll('.agent-task-card').length,
          cardStatuses: [...document.querySelectorAll('.agent-task-card .agent-task-status')].map((el) => (el.closest('.agent-task-card')?.getAttribute('data-agent-task-card') || '').slice(-8) + ':' + (el.getAttribute('data-status') || '')),
          agentNodes: [...document.querySelectorAll('.agent-smart-node')].map((el) => (el.getAttribute('data-id') || '').slice(-8)),
        }));
      }
    } else {
      record('retried task succeeds', false, 'no retry button');
    }

    const toasts = evalFn('window.__agentUiToasts ? window.__agentUiToasts.slice() : []');
    record('no fatal toast', !toasts.some((t) => /未适配|崩溃|Error:/.test(String(t))), 'toasts=' + JSON.stringify(toasts).slice(0, 150));

    await sleep(800);
    const saved = await evalFn(
      '(async () => { try { const r = await saveCoreCanvasIncrementally(canvasForStorage()); return JSON.stringify(r); } catch (e) { return "ERR:" + String(e); } })()'
    );
    record('save settles', !String(saved).startsWith('ERR') && String(saved).includes('"saved":true'), String(saved).slice(0, 110));

    const nodeCount = evalFn('(canvas && canvas.nodes) ? canvas.nodes.length : -1');
    record('nodes alive', nodeCount > 0, 'count=' + nodeCount);
  } catch (e) {
    errors.push(String(e));
    record('unexpected error', false, String(e));
  }

  await post();
})();

