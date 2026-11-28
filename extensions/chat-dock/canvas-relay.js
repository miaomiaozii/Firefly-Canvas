(() => {
  'use strict';

  const P = FireflyChatProtocol;
  if (location.pathname !== '/static/smart-canvas.html' || globalThis.FireflyChatRelayInstalled) return;
  const docked = window !== window.top;
  if (docked && !P.dockChild()) return;
  globalThis.FireflyChatRelayInstalled = true;
  if (docked) P.listenForDockProof('canvas');
  else FireflyChatDockSettingsHello.listenForPage(P.isCanvasURL);

  const RESPONSE_TIMEOUT = 15_000;
  function forward(data) {
    return new Promise(resolve => {
      const {port1, port2} = new MessageChannel();
      const timer = setTimeout(() => finish(P.failure('CANVAS_NOT_READY')), RESPONSE_TIMEOUT);
      function finish(result) {
        clearTimeout(timer);
        port1.close();
        resolve(result);
      }
      port1.onmessage = event => finish(P.response(event.data));
      port1.onmessageerror = () => finish(P.failure('CAPTURE_FAILED'));
      try {
        window.postMessage(data, location.origin, [port2]);
      } catch {
        port2.close();
        finish(P.failure('CAPTURE_FAILED'));
      }
    });
  }

  async function relay(data) {
    const origin = P.originSetting(await chrome.storage.local.get(P.ORIGIN_KEY));
    if (!P.isCanvasURL(location.href, origin)) return P.failure('CANVAS_NOT_READY');
    return forward(data);
  }

  chrome.runtime.onMessage.addListener((data, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || sender.tab || sender.url !== chrome.runtime.getURL('background.js') ||
        !P.validCapture(data)) return;
    relay(data).then(sendResponse, () => sendResponse(P.failure('CAPTURE_FAILED')));
    return true;
  });
})();
