(() => {
  'use strict';

  const H = FireflyChatDockSettingsHello;
  if (location.pathname !== H.path || globalThis.FireflyChatDockDetectorInstalled) return;
  globalThis.FireflyChatDockDetectorInstalled = true;
  H.listenForPage(H.isSettingsPage);
})();
