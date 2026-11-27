(() => {
  'use strict';

  const SETTINGS = 'firefly.chat-dock.layout.v1';
  const P = FireflyChatProtocol;
  const embed = FireflyChatEmbed;
  const byId = id => document.getElementById(id);
  const shell = byId('dock-shell');
  const panel = byId('chat-dock-panel');
  const divider = byId('dock-divider');
  const move = byId('move-panel');
  const resize = byId('resize-panel');
  const hide = byId('hide-panel');
  const reveal = byId('show-panel');
  const sitePicker = byId('chat-site');
  const modeButtons = [...panel.querySelectorAll('button[data-mode]')];
  const sites = Object.keys(embed.names);
  let stored;
  try { stored = JSON.parse(localStorage.getItem(SETTINGS) ?? 'null'); }
  catch { stored = null; }
  const layouts = new Map();
  const lastSite = stored?.site;
  let site = sites.includes(lastSite) ? lastSite : sites[0];
  let drag;

  function layoutFor(key) {
    if (layouts.has(key)) return layouts.get(key);
    const previous = stored?.layouts?.[key];
    const number = (field, fallback) => Number.isFinite(previous?.[field]) ? previous[field] : fallback;
    const value = {
      mode: ['left', 'right', 'float'].includes(previous?.mode) ? previous.mode : 'right',
      dockWidth: number('dockWidth', 420), x: number('x', 84), y: number('y', 24),
      width: number('width', 460), height: number('height', 590), collapsed: previous?.collapsed === true,
    };
    layouts.set(key, value);
    return value;
  }

  function persist() {
    try { localStorage.setItem(SETTINGS, JSON.stringify({site, layouts: Object.fromEntries(layouts)})); }
    catch { /* The layout still works if persistence is disabled. */ }
  }

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

  function bounds(value) {
    const w = shell.clientWidth;
    const h = shell.clientHeight;
    const maxDock = Math.max(0, w - divider.offsetWidth - Math.min(144, Math.floor(w * .39)));
    const minDock = Math.min(232, maxDock);
    const margin = Math.min(8, Math.floor(Math.min(w, h) / 4));
    const fw = clamp(value.width, Math.min(256, w - 2 * margin), w - 2 * margin);
    const fh = clamp(value.height, Math.min(220, h - 2 * margin), h - 2 * margin);
    return {
      minDock, maxDock, dockWidth: clamp(value.dockWidth, minDock, maxDock),
      x: clamp(value.x, margin, w - fw - margin), y: clamp(value.y, margin, h - fh - margin),
      width: fw, height: fh,
    };
  }

  function render() {
    const value = layoutFor(site);
    const floating = value.mode === 'float';
    shell.dataset.mode = value.mode;
    shell.toggleAttribute('data-collapsed', value.collapsed);
    panel.hidden = value.collapsed;
    divider.hidden = value.collapsed || floating;
    reveal.hidden = !value.collapsed;
    reveal.setAttribute('aria-expanded', String(!value.collapsed));
    move.hidden = resize.hidden = !floating;
    const b = bounds(value);
    for (const [key, px] of Object.entries({
      'dock-width': b.dockWidth, 'float-width': b.width, 'float-height': b.height, 'float-x': b.x, 'float-y': b.y,
    })) shell.style.setProperty(`--${key}`, `${px}px`);
    divider.setAttribute('aria-valuemin', String(b.minDock));
    divider.setAttribute('aria-valuemax', String(b.maxDock));
    divider.setAttribute('aria-valuenow', String(b.dockWidth));
    modeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === value.mode)));
  }

  const mounted = embed.mount({
    view: byId('chat-view'), link: byId('open-chat'), unavailable: byId('chat-unavailable'),
    heading: byId('chat-site-heading'), note: byId('chat-frame-note'), framingHint: byId('chat-framing-hint'),
  });
  let framed = {};
  const showSite = key => mounted(key, framed);
  sites.forEach(key => sitePicker.add(new Option(embed.names[key], key)));
  sites.forEach(layoutFor);
  sitePicker.value = site;
  showSite(site);
  chrome.storage.local.get(FireflyChatFraming.KEY).then(stored => {
    framed = FireflyChatFraming.flags(stored);
    if (framed[site] === true) showSite(site);
  }, () => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !Object.hasOwn(changes, FireflyChatFraming.KEY)) return;
    const next = FireflyChatFraming.flags({[FireflyChatFraming.KEY]: changes[FireflyChatFraming.KEY].newValue});
    const changed = (framed[site] === true) !== (next[site] === true);
    framed = next;
    // Reloading an unaffected site would discard the conversation the user is looking at.
    if (changed) showSite(site);
  });
  sitePicker.addEventListener('change', () => {
    if (!sites.includes(sitePicker.value)) return;
    site = sitePicker.value;
    showSite(site);
    render();
    persist();
  });
  modeButtons.forEach(button => button.addEventListener('click', () => {
    layoutFor(site).mode = button.dataset.mode;
    render();
    persist();
  }));

  function collapse(value, returnFocus) {
    layoutFor(site).collapsed = value;
    render();
    persist();
    returnFocus.focus();
  }
  hide.addEventListener('click', () => collapse(true, reveal));
  reveal.addEventListener('click', () => collapse(false, hide));

  function applyDelta(kind, value, start, dx, dy) {
    if (kind === 'dock') value.dockWidth = start.dockWidth + (value.mode === 'left' ? dx : -dx);
    if (kind === 'move') Object.assign(value, {x: start.x + dx, y: start.y + dy});
    if (kind === 'resize') Object.assign(value, {width: start.width + dx, height: start.height + dy});
    const b = bounds(value);
    if (kind === 'dock') value.dockWidth = b.dockWidth;
    else if (kind === 'move') Object.assign(value, {x: b.x, y: b.y});
    else Object.assign(value, {width: b.width, height: b.height, x: b.x, y: b.y});
    render();
  }

  function bindGesture(handle, kind) {
    handle.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0 || drag) return;
      const value = layoutFor(site);
      drag = {id: event.pointerId, value, original: {...value}, start: bounds(value), x: event.clientX, y: event.clientY};
      handle.setPointerCapture(event.pointerId);
      shell.setAttribute('data-dragging', '');
    });
    handle.addEventListener('pointermove', event => {
      if (drag?.id !== event.pointerId) return;
      applyDelta(kind, drag.value, drag.start, event.clientX - drag.x, event.clientY - drag.y);
    });
    function end(event, cancel) {
      if (drag?.id !== event.pointerId) return;
      if (cancel) Object.assign(drag.value, drag.original);
      else persist();
      drag = null;
      shell.removeAttribute('data-dragging');
      render();
    }
    handle.addEventListener('pointerup', event => end(event, false));
    handle.addEventListener('pointercancel', event => end(event, true));
    handle.addEventListener('lostpointercapture', event => end(event, true));
    handle.addEventListener('keydown', event => {
      const delta = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
      if (!delta || (kind === 'dock' && !delta[0]) || event.altKey || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      const value = layoutFor(site);
      const step = event.shiftKey ? 40 : 10;
      applyDelta(kind, value, bounds(value), delta[0] * step, delta[1] * step);
      persist();
    });
  }
  bindGesture(divider, 'dock');
  bindGesture(move, 'move');
  bindGesture(resize, 'resize');

  const color = matchMedia('(prefers-color-scheme: dark)');
  const setTheme = () => document.documentElement.classList.toggle('studio-theme-dark', color.matches);
  color.addEventListener('change', setTheme);
  window.addEventListener('resize', render);
  setTheme();
  render();

  function probeCapture(data) {
    const chatFrame = byId('chat-view').querySelector('iframe');
    const canvasFrame = byId('firefly-frame');
    // The shell owns exactly these two direct frames. Do not pick a destination if another frame appears.
    if (!P.dockCaptureSite(site) || data.site !== site || window.length !== 2 ||
        !chatFrame?.contentWindow || !canvasFrame.contentWindow) return false;
    for (const [role, frame, origin] of [
      ['chat', chatFrame, `https://${site}`], ['canvas', canvasFrame, data.origin],
    ]) {
      frame.contentWindow.postMessage({action: P.DOCK_PROBE, role, nonce: data.nonce}, origin);
    }
    return true;
  }

  chrome.tabs.getCurrent().then(tab => {
    if (!tab || window !== window.top) return;
    chrome.runtime.onMessage.addListener((data, sender, respond) => {
      if (sender.id !== chrome.runtime.id || sender.tab || sender.url !== chrome.runtime.getURL('background.js') ||
          data?.action !== P.DOCK_PROBE || data.tabId !== tab.id) return;
      respond(probeCapture(data));
    });
  });

  async function openFirefly() {
    const origin = P.originSetting(await chrome.storage.local.get(P.ORIGIN_KEY));
    if (!(await chrome.permissions.contains({origins: [P.canvasPatterns(origin).permission]}))) return false;
    const frame = byId('firefly-frame');
    // Canvas clipboard and fullscreen support belongs only to the configured origin.
    frame.allow = ['clipboard-read', 'clipboard-write', 'fullscreen'].map(feature => `${feature} ${origin}`).join('; ');
    frame.src = new URL('/', origin).href;
    frame.hidden = false;
    return true;
  }
  openFirefly().then(opened => { byId('firefly-problem').hidden = opened; }, () => { byId('firefly-problem').hidden = false; });
})();
