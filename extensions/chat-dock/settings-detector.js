(() => {
  'use strict';

  const P = FireflyChatProtocol;
  const H = FireflyChatDockSettingsHello;
  if (location.pathname !== H.path || globalThis.FireflyChatDockDetectorInstalled) return;
  globalThis.FireflyChatDockDetectorInstalled = true;

  window.addEventListener('message', async event => {
    if (event.source !== window || event.origin !== location.origin || event.ports.length !== 1 || !H.isHello(event.data)) return;
    const port = event.ports[0];
    try {
      const origin = P.originSetting(await chrome.storage.local.get(P.ORIGIN_KEY));
      if (!H.isSettingsPage(location.href, origin)) return;
      const receipt = await chrome.runtime.sendMessage(event.data);
      if (receipt?.ok === true) port.postMessage(receipt);
    } catch {
      // Reload can invalidate the content script; the caller treats an absent receipt as undetected.
    } finally {
      port.close();
    }
  });
})();
