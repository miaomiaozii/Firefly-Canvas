(() => {
  'use strict';

  const P = FireflyChatProtocol;
  const pending = new Map();
  const PROBE_TIMEOUT = 1500;

  const activeChild = sender => Number.isInteger(sender.tab?.id) && sender.frameId > 0 &&
    typeof sender.documentId === 'string' && sender.documentLifecycle === 'active';

  async function shellContext(tabId) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['TAB'], tabIds: [tabId], frameIds: [0], documentUrls: [chrome.runtime.getURL('canvas.html')],
    });
    return contexts.length === 1 ? contexts[0].documentId : null;
  }

  function acceptProof(data, sender) {
    if (!activeChild(sender) || !P.exactKeys(data, ['action', 'role', 'nonce'])) return;
    const attempt = pending.get(data.nonce);
    if (!attempt || sender.tab.id !== attempt.tabId) return;
    if (data.role === 'chat' && sender.documentId === attempt.source && P.chatSite(sender.url) === attempt.site) {
      attempt.chat = true;
    }
    if (data.role === 'canvas' && P.isCanvasURL(sender.url, attempt.origin)) attempt.canvas = sender.documentId;
    if (attempt.chat && attempt.canvas) attempt.finish(attempt.canvas);
  }

  async function target(data, sender, origin) {
    const tabId = sender.tab.id;
    const shell = await shellContext(tabId);
    if (!shell) return null;
    const nonce = crypto.randomUUID();
    // The trusted shell challenges its two real children; a tab URL or an origin alone cannot identify either document.
    let timer;
    try {
      const proof = new Promise(finish => {
        pending.set(nonce, {tabId, site: data.site, source: sender.documentId, origin, finish});
      });
      const cutoff = new Promise(resolve => { timer = setTimeout(() => resolve(null), PROBE_TIMEOUT); });
      const acknowledged = chrome.runtime.sendMessage({action: P.DOCK_PROBE, tabId, site: data.site, origin, nonce});
      const destination = await Promise.race([acknowledged.then(ok => ok === true ? proof : null), cutoff]);
      return destination && (await shellContext(tabId)) === shell ? destination : null;
    } finally {
      clearTimeout(timer);
      pending.delete(nonce);
    }
  }

  globalThis.FireflyDockCaptureRouter = Object.freeze({activeChild, shellContext, acceptProof, target});
})();
