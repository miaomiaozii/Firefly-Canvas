'use strict';

importScripts('protocol.js', 'framing.js');

(() => {
  const P = FireflyChatProtocol;
  const F = FireflyChatFraming;
  // Serialize settings writes with grant-revocation reconciliation.
  let settings = Promise.resolve();
  function updateSettings(work) {
    const job = settings.then(work);
    settings = job.catch(() => {});
    return job;
  }

  async function storedOrigin() { return P.originSetting(await chrome.storage.local.get(P.ORIGIN_KEY)); }

  function permitted(origin) {
    return chrome.permissions.contains({origins: [P.canvasPatterns(origin).permission]});
  }

  async function syncRelay(origin) {
    const [relay] = await chrome.scripting.getRegisteredContentScripts({ids: [P.RELAY_ID]});
    const match = origin === P.DEFAULT_ORIGIN ? null : P.canvasPatterns(origin).match;
    if (relay && match && relay.matches.length === 1 && relay.matches[0] === match) return;
    if (relay) await chrome.scripting.unregisterContentScripts({ids: [P.RELAY_ID]});
    if (match) {
      await chrome.scripting.registerContentScripts([{
        id: P.RELAY_ID, matches: [match], js: ['protocol.js', 'canvas-relay.js'], runAt: 'document_idle',
        allFrames: false, persistAcrossSessions: true, world: 'ISOLATED',
      }]);
    }
  }

  async function setOrigin(origin) {
    const previous = await storedOrigin();
    if (!(await permitted(origin))) return P.failure('PERMISSION_DENIED');
    try {
      await syncRelay(origin);
      await chrome.storage.local.set({[P.ORIGIN_KEY]: origin});
    } catch (error) {
      await syncRelay(await permitted(previous) ? previous : P.DEFAULT_ORIGIN).catch(() => {});
      throw error;
    }
    // Release grants for former custom hosts. Chrome's required default grant cannot be removed.
    const oldGrant = P.canvasPatterns(previous).permission;
    if (previous !== P.DEFAULT_ORIGIN && oldGrant !== P.canvasPatterns(P.DEFAULT_ORIGIN).permission &&
        oldGrant !== P.canvasPatterns(origin).permission) {
      await chrome.permissions.remove({origins: [oldGrant]}).catch(() => {});
    }
    return {ok: true};
  }

  async function applyFraming(enabled) {
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: [...F.RULE_IDS],
      addRules: Object.keys(enabled).map(site => F.rule(site, chrome.runtime.id)),
    });
  }

  async function syncFraming() {
    await applyFraming(F.flags(await chrome.storage.local.get(F.KEY)));
  }

  async function setFraming(site, enabled) {
    const previous = F.flags(await chrome.storage.local.get(F.KEY));
    const next = {...previous};
    if (enabled) next[site] = true;
    else delete next[site];
    // Rules first: the panel reloads the site on the storage change, and must not load it before the rule exists.
    await applyFraming(next);
    try {
      await chrome.storage.local.set({[F.KEY]: next});
    } catch (error) {
      await applyFraming(previous).catch(() => {});
      throw error;
    }
    return {ok: true};
  }

  function reconcile() {
    updateSettings(async () => {
      const origin = await storedOrigin();
      // Do not silently redirect captures to a different machine if a grant is revoked.
      await syncRelay(await permitted(origin) ? origin : P.DEFAULT_ORIGIN);
    }).catch(() => {});
    updateSettings(syncFraming).catch(() => {});
  }
  chrome.runtime.onInstalled.addListener(reconcile);
  chrome.runtime.onStartup.addListener(reconcile);
  chrome.permissions.onRemoved.addListener(reconcile);

  async function route(data) {
    await settings;
    const origin = await storedOrigin();
    if (!(await permitted(origin))) return P.failure('PERMISSION_DENIED');
    const {id, error} = P.chooseCanvas(await chrome.tabs.query({url: [P.canvasPatterns(origin).match]}), origin);
    if (id === undefined) return P.failure(error);
    if (origin !== await storedOrigin()) return P.failure('CANVAS_NOT_READY');
    try {
      return P.response(await chrome.tabs.sendMessage(id, data, {frameId: 0}));
    } catch {
      return P.failure('CANVAS_NOT_READY');
    }
  }

  chrome.runtime.onMessage.addListener((data, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || (sender.frameId !== 0 && sender.frameId !== undefined)) return;
    if (sender.url === chrome.runtime.getURL('options.html') && data?.action === P.SET_ORIGIN) {
      const origin = P.exactKeys(data, ['action', 'origin']) && P.parseOrigin(data.origin);
      if (!origin) return;
      updateSettings(() => setOrigin(origin)).then(sendResponse, () => sendResponse(P.failure('CAPTURE_FAILED')));
      return true;
    }
    if (sender.url === chrome.runtime.getURL('options.html') && data?.action === F.SET_FRAMING) {
      if (!F.validCommand(data)) return;
      updateSettings(() => setFraming(data.site, data.enabled)).then(sendResponse, () => sendResponse(P.failure('CAPTURE_FAILED')));
      return true;
    }
    const site = P.chatSite(sender.url);
    if (sender.frameId !== 0 || !Number.isInteger(sender.tab?.id) || !site || !P.validCapture(data, site)) return;
    route(data).then(sendResponse, () => sendResponse(P.failure('CAPTURE_FAILED')));
    return true;
  });
})();
