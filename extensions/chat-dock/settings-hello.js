(() => {
  'use strict';

  const channel = 'firefly.chat-dock.lifecycle.v1';
  const path = '/static/global-settings.html';
  const P = FireflyChatProtocol;

  function isSettingsPage(value, origin) {
    if (typeof value !== 'string' || !URL.canParse(value)) return false;
    const url = new URL(value);
    return url.origin === origin && url.pathname === path && !url.username && !url.password;
  }

  function isHello(data) {
    return P.exactKeys(data, ['channel', 'action']) && data.channel === channel && data.action === 'hello';
  }

  async function getSettingsHello(data, sender, getOrigin) {
    if (!isHello(data) || sender.id !== chrome.runtime.id || !Number.isInteger(sender.tab?.id) ||
        !Number.isInteger(sender.frameId)) return null;
    const origin = await getOrigin();
    if (sender.origin !== origin || !isSettingsPage(sender.url, origin)) return null;
    return {ok: true, version: chrome.runtime.getManifest().version, supported: typeof chrome.runtime.getContexts === 'function'};
  }

  globalThis.FireflyChatDockSettingsHello = Object.freeze({channel, path, isSettingsPage, isHello, getSettingsHello});
})();
