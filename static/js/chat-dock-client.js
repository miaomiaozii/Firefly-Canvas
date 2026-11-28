(() => {
  'use strict';

  const channel = 'firefly.chat-dock.lifecycle.v1';
  function hello() {
    return new Promise(resolve => {
      const {port1, port2} = new MessageChannel();
      const timer = setTimeout(() => finish(null), 750);
      function finish(value) {
        clearTimeout(timer);
        port1.close();
        resolve(value);
      }
      port1.onmessage = ({data}) => finish(data?.ok === true && typeof data.version === 'string' &&
        typeof data.supported === 'boolean' ? {ok: true, version: data.version, supported: data.supported} : null);
      port1.onmessageerror = () => finish(null);
      try {
        window.postMessage({channel, action: 'hello'}, location.origin, [port2]);
      } catch {
        port2.close();
        finish(null);
      }
    });
  }

  window.FireflyChatDockClient = Object.freeze({hello});
})();
