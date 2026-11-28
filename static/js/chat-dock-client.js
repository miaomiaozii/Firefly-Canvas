(() => {
  'use strict';

  const channel = 'firefly.chat-dock.lifecycle.v1';
  // No receipt means "not detected": an absent extension and one that is not yet listening look the same.
  function request(action, timeout) {
    return new Promise(resolve => {
      const {port1, port2} = new MessageChannel();
      const timer = timeout ? setTimeout(() => finish(null), timeout) : null;
      function finish(value) {
        clearTimeout(timer);
        port1.close();
        resolve(value);
      }
      port1.onmessage = ({data}) => finish(data && typeof data.ok === 'boolean' ? data : null);
      port1.onmessageerror = () => finish(null);
      try {
        window.postMessage({channel, action}, location.origin, [port2]);
      } catch {
        port2.close();
        finish(null);
      }
    });
  }

  async function hello() {
    const data = await request('hello', 750);
    return data?.ok === true && typeof data.version === 'string' && typeof data.supported === 'boolean'
      ? {ok: true, version: data.version, supported: data.supported} : null;
  }

  // Uninstall waits on the browser's confirmation dialog, so it has no timeout.
  const act = action => request(action, action === 'uninstall' ? 0 : 5000).then(data => data?.ok === true);

  window.FireflyChatDockClient = Object.freeze({
    hello,
    reload: () => act('reload'),
    uninstall: () => act('uninstall'),
    options: () => act('options'),
    enterDock: () => act('enter-dock'),
    openDirect: () => act('open-direct'),
    toggleDock: () => act('toggle-dock'),
    leaveDock: () => act('leave-dock'),
  });
})();
