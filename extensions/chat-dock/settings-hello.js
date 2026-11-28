(() => {
  'use strict';

  const channel = 'firefly.chat-dock.lifecycle.v1';
  const path = '/static/global-settings.html';
  const P = FireflyChatProtocol;
  // Each Firefly page may only ask for its own lifecycle actions; the canvas cannot reload or uninstall the extension.
  const ACTIONS = Object.freeze({
    settings: Object.freeze(['hello', 'reload', 'uninstall', 'options']),
    canvas: Object.freeze(['hello', 'open-dock', 'enter-dock', 'open-direct']),
    docked: Object.freeze(['toggle-dock', 'leave-dock']),
  });
  const KNOWN = new Set([...ACTIONS.settings, ...ACTIONS.canvas, ...ACTIONS.docked]);
  const SHELL_PARAMS = Object.freeze(['id', 'project', 'project_mode']);
  const ID = /^[A-Za-z0-9_-]{1,128}$/;

  function isSettingsPage(value, origin) {
    if (typeof value !== 'string' || !URL.canParse(value)) return false;
    const url = new URL(value);
    return url.origin === origin && url.pathname === path && !url.username && !url.password;
  }

  function isLifecycle(data) {
    return P.exactKeys(data, ['channel', 'action']) && data.channel === channel && KNOWN.has(data.action);
  }

  function isHello(data) {
    return isLifecycle(data) && data.action === 'hello';
  }

  function pageOf(sender, origin) {
    if (isSettingsPage(sender.url, origin)) return 'settings';
    // A child is only a candidate here. Mutating the shell additionally requires its direct-frame proof.
    if (P.isCanvasURL(sender.url, origin)) return sender.frameId === 0 ? 'canvas' : 'docked';
    return null;
  }

  async function lifecycleRequest(data, sender, getOrigin) {
    if (!isLifecycle(data) || sender.id !== chrome.runtime.id || !Number.isInteger(sender.tab?.id) ||
        !Number.isInteger(sender.frameId)) return null;
    const origin = await getOrigin();
    if (sender.origin !== origin) return null;
    const page = pageOf(sender, origin);
    return page && ACTIONS[page].includes(data.action) ? {page, action: data.action} : null;
  }

  function helloReceipt() {
    return {ok: true, version: chrome.runtime.getManifest().version, supported: typeof chrome.runtime.getContexts === 'function'};
  }

  async function getSettingsHello(data, sender, getOrigin) {
    const request = isHello(data) ? await lifecycleRequest(data, sender, getOrigin) : null;
    return request ? helloReceipt() : null;
  }

  function shellQuery(canvasURL) {
    if (typeof canvasURL !== 'string' || !URL.canParse(canvasURL)) return null;
    const source = new URL(canvasURL).searchParams;
    const query = new URLSearchParams();
    for (const name of SHELL_PARAMS) {
      const values = source.getAll(name);
      if (!values.length) continue;
      if (values.length !== 1) return null;
      const [value] = values;
      if (name === 'project_mode' ? value !== '1' : !ID.test(value)) return null;
      query.set(name, value);
    }
    return query.has('id') ? query : null;
  }

  function shellCanvasPath(search) {
    const query = shellQuery(`http://shell.invalid/${search}`);
    return query ? `${P.CANVAS_PATH}?${query}` : '/';
  }

  // Content-script side: forward one page request to the background and answer on the page's own port.
  function listenForPage(isPage) {
    window.addEventListener('message', async event => {
      if (event.source !== window || event.origin !== location.origin || event.ports.length !== 1 || !isLifecycle(event.data)) return;
      const port = event.ports[0];
      try {
        const origin = P.originSetting(await chrome.storage.local.get(P.ORIGIN_KEY));
        if (!isPage(location.href, origin)) return;
        const receipt = await chrome.runtime.sendMessage(event.data);
        if (typeof receipt?.ok === 'boolean') port.postMessage(receipt);
      } catch {
        // Reload can invalidate the content script; the caller treats an absent receipt as undetected.
      } finally {
        port.close();
      }
    });
  }

  globalThis.FireflyChatDockSettingsHello = Object.freeze({
    channel, path, isSettingsPage, isLifecycle, isHello, lifecycleRequest, helloReceipt, getSettingsHello,
    shellQuery, shellCanvasPath, listenForPage,
  });
})();
