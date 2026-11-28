(() => {
  'use strict';

  const P = FireflyChatProtocol;
  const H = FireflyChatDockSettingsHello;
  const probe = 'firefly.chat-dock.direct-proof';
  const request = 'firefly.chat-dock.direct-command';
  const actions = new Set(['toggle-dock', 'leave-dock']);
  const pending = new Map();

  function isDirect(search) {
    const values = new URLSearchParams(search).getAll('direct');
    return values.length === 1 && values[0] === '1';
  }

  function shellQuery(value) {
    const query = H.shellQuery(value);
    if (!query || !query.has('project') || query.get('project_mode') !== '1') return null;
    return query;
  }

  function isShellURL(value) {
    return typeof value === 'string' && value.split(/[?#]/, 1)[0] === chrome.runtime.getURL('canvas.html');
  }

  async function shellContext(tabId, project) {
    const contexts = await chrome.runtime.getContexts({contextTypes: ['TAB'], tabIds: [tabId], frameIds: [0]});
    const matching = contexts.filter(({documentUrl}) => isShellURL(documentUrl) &&
      isDirect(new URL(documentUrl).search) && shellQuery(documentUrl)?.toString() === project);
    return matching.length === 1 ? matching[0].documentId : null;
  }

  function acceptProof(data, sender) {
    if (!P.exactKeys(data, ['action', 'nonce'])) return;
    const attempt = pending.get(data.nonce);
    if (attempt && sender.tab?.id === attempt.tabId && sender.documentId === attempt.source &&
        sender.frameId === attempt.frameId && sender.documentLifecycle === 'active' && sender.origin === attempt.origin &&
        P.isCanvasURL(sender.url, attempt.origin) && shellQuery(sender.url)?.toString() === attempt.project) attempt.finish(true);
  }

  async function command(action, sender, origin, stillPermitted) {
    const project = shellQuery(sender.url)?.toString();
    const tabId = sender.tab.id;
    if (!actions.has(action) || !project || !Number.isInteger(sender.frameId) || sender.frameId <= 0 ||
        typeof sender.documentId !== 'string' || sender.documentLifecycle !== 'active') return false;
    const documentId = await shellContext(tabId, project);
    if (!documentId) return false;
    const nonce = crypto.randomUUID();
    let timeout;
    try {
      // A same-origin sibling or a stale navigation must not be able to operate the shell.
      const proof = new Promise(finish => pending.set(nonce, {
        finish, tabId, origin, project, source: sender.documentId, frameId: sender.frameId,
      }));
      const expiry = new Promise(resolve => { timeout = setTimeout(() => resolve(false), 1200); });
      const acknowledged = chrome.runtime.sendMessage({action: probe, tabId, documentId, origin, nonce});
      if (!(await Promise.race([acknowledged.then(ok => ok === true ? proof : false), expiry])) ||
          !(await stillPermitted()) || await shellContext(tabId, project) !== documentId) return false;
      return await chrome.runtime.sendMessage({action: request, tabId, documentId, origin, nonce, command: action}) === true;
    } finally {
      clearTimeout(timeout);
      pending.delete(nonce);
    }
  }

  function listenInCanvas() {
    window.addEventListener('message', event => {
      if (!event.isTrusted || event.source !== window.parent || event.origin !== `chrome-extension://${chrome.runtime.id}` ||
          !P.exactKeys(event.data, ['action', 'nonce']) || event.data.action !== probe || typeof event.data.nonce !== 'string') return;
      chrome.runtime.sendMessage(event.data).catch(() => {});
    });
  }

  globalThis.FireflyChatDockDirect = Object.freeze({probe, request, actions, isDirect, shellQuery, isShellURL, command, acceptProof, listenInCanvas});
})();
