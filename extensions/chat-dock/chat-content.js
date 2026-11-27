(() => {
  'use strict';

  const P = FireflyChatProtocol;
  const site = P.chatSite(location.href);
  if (!site || window !== window.top) return;

  const host = document.createElement('div');
  host.id = 'firefly-chat-capture-host';
  const shadow = host.attachShadow({mode: 'open'});
  const style = new CSSStyleSheet();
  style.replaceSync(`
    :host { all: initial !important; display: block !important; position: fixed !important;
      inset: 0 !important; z-index: 2147483647 !important; pointer-events: none !important; }
    [hidden] { display: none !important; }
    #controls, #feedback { position: absolute; color: #fff; background: #202838; direction: ltr;
      box-shadow: 0 2px 10px #0006; border-radius: 8px; font: 14px/1.5 system-ui, sans-serif; }
    #controls { pointer-events: auto; padding: 4px; }
    #feedback { bottom: 16px; right: 16px; max-width: min(320px, calc(100vw - 56px)); padding: 8px 12px; }
    button { display: block; color: inherit; background: #354560; font: inherit; white-space: nowrap;
      padding: 4px 8px; border: 1px solid #647493; border-radius: 5px; cursor: pointer; }
    button:hover:not(:disabled) { background: #4c6288; }
    button:focus-visible { outline: 2px solid #fff; outline-offset: -2px; }
    button:disabled { opacity: .6; cursor: not-allowed; }
    #text-menu { display: grid; gap: 4px; padding-top: 4px; }
    #append-reason { margin: 0; max-width: 14em; font-size: 12px; }
  `);
  shadow.adoptedStyleSheets = [style];
  function element(parent, tag, id, attrs = {}, label = '') {
    const node = document.createElement(tag);
    node.id = id;
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    node.textContent = label;
    parent.append(node);
    return node;
  }
  const controls = element(shadow, 'div', 'controls', {hidden: ''});
  const selectionChip = element(controls, 'button', 'selection-chip', {
    type: 'button', 'aria-expanded': 'false', 'aria-controls': 'text-menu',
  }, '对话采集');
  const menu = element(controls, 'div', 'text-menu', {hidden: '', role: 'group', 'aria-label': '文字采集方式'});
  const append = element(menu, 'button', 'append-prompt', {
    type: 'button', 'aria-describedby': 'append-reason', disabled: '',
  }, '填入生图节点提示词');
  const reason = element(menu, 'p', 'append-reason');
  const newText = element(menu, 'button', 'new-text', {type: 'button'}, '新建文本节点');
  const imageChip = element(controls, 'button', 'image-chip', {type: 'button', hidden: ''}, '复制到画布');
  const feedback = element(shadow, 'div', 'feedback', {role: 'status', hidden: ''});
  document.documentElement.append(host);

  let capture = null;
  let menuOpen = false;
  let canAppend = false;
  let busy = false;
  let selectionFrame = 0;
  let feedbackTimer;

  function dismiss() {
    cancelAnimationFrame(selectionFrame);
    capture = null;
    menuOpen = false;
    canAppend = false;
    selectionChip.setAttribute('aria-expanded', 'false');
    menu.hidden = controls.hidden = true;
  }

  function position() {
    const {left, top, right, bottom} = capture.rect;
    const width = controls.offsetWidth;
    const height = controls.offsetHeight;
    const image = capture.kind === 'image';
    const x = image ? right - width : left;
    const y = image ? top : (top >= height + 8 ? top - height - 4 : bottom + 4);
    controls.style.left = `${Math.max(4, Math.min(x, innerWidth - width - 4))}px`;
    controls.style.top = `${Math.max(4, Math.min(y, innerHeight - height - 4))}px`;
  }

  function show(next) {
    dismiss();
    capture = next;
    selectionChip.hidden = next.kind !== 'text';
    imageChip.hidden = next.kind !== 'image';
    controls.hidden = false;
    position();
  }

  function notify(message) {
    clearTimeout(feedbackTimer);
    feedback.textContent = message;
    feedback.hidden = false;
    feedbackTimer = setTimeout(() => { feedback.hidden = true; feedback.textContent = ''; }, 4200);
  }

  const EDITABLE = 'input,textarea,select,[role="textbox"],[role="searchbox"],[role="combobox"]';
  function editable(node) {
    if (document.designMode.toLowerCase() === 'on') return true;
    let element = node instanceof Element ? node : node?.parentElement;
    while (element) {
      if (element.isContentEditable || element.closest(EDITABLE)) return true;
      element = element.getRootNode().host;
    }
    return false;
  }

  function showSelection() {
    const selection = window.getSelection();
    if (busy || !selection || selection.isCollapsed || selection.rangeCount !== 1 ||
        editable(selection.anchorNode) || editable(selection.focusNode) ||
        selection.anchorNode?.getRootNode() === shadow || selection.focusNode?.getRootNode() === shadow) return;
    const range = selection.getRangeAt(0);
    const container = range.commonAncestorContainer;
    if (editable(container)) return;
    if (container.querySelectorAll) {
      for (const entry of container.querySelectorAll(`${EDITABLE},[contenteditable]`)) {
        if (range.intersectsNode(entry) && editable(entry)) return;
      }
    }
    const text = selection.toString();
    if (!text.trim()) return;
    if (text.length > P.MAX_TEXT) {
      notify('所选文字超过 20,000 字符，请缩小选区。');
      return;
    }
    const rect = range.getBoundingClientRect();
    if (rect.width || rect.height) show({kind: 'text', text, rect});
  }

  const withinControls = event => event.composedPath().includes(host);
  window.addEventListener('pointerdown', event => {
    if (event.isTrusted && !withinControls(event)) dismiss();
  }, true);
  window.addEventListener('pointerup', event => {
    if (!event.isTrusted || event.button !== 0 || withinControls(event) || event.composedPath().some(editable)) return;
    cancelAnimationFrame(selectionFrame);
    selectionFrame = requestAnimationFrame(showSelection);
  }, true);
  window.addEventListener('keyup', event => {
    if (!event.isTrusted || withinControls(event) || event.composedPath().some(editable)) return;
    const selectAll = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a';
    const selectRange = event.shiftKey && /^(Arrow(Up|Down|Left|Right)|Home|End|Page(Up|Down))$/.test(event.key);
    if (selectAll || selectRange) {
      dismiss();
      selectionFrame = requestAnimationFrame(showSelection);
    }
  }, true);
  window.addEventListener('keydown', event => { if (event.isTrusted && event.key === 'Escape') dismiss(); }, true);
  window.addEventListener('resize', dismiss);
  window.addEventListener('scroll', dismiss, true);

  function loadedSource(image) {
    if (!image?.isConnected || !image.complete || !image.naturalWidth || editable(image)) return null;
    try {
      const url = new URL(image.currentSrc);
      if (url.username || url.password) return null;
      if (url.protocol === 'https:' || (url.protocol === 'blob:' && url.origin === location.origin) ||
          (url.protocol === 'data:' && url.href.length <= P.MAX_BYTES * 4 + 1024)) return url.href;
    } catch { /* A source may no longer be loaded after the site rerenders. */ }
    return null;
  }

  window.addEventListener('pointerover', event => {
    if (!event.isTrusted || busy || capture?.kind === 'text' || event.pointerType === 'touch' || withinControls(event)) return;
    const image = event.composedPath().find(node => node instanceof HTMLImageElement);
    const url = loadedSource(image);
    if (url) {
      const rect = image.getBoundingClientRect();
      if (rect.width && rect.height) show({kind: 'image', image, url, rect});
    } else if (capture?.kind === 'image') dismiss();
  }, true);

  controls.addEventListener('pointerdown', event => {
    // Focusing a text control on pointerdown would collapse the selected range on some sites.
    if (event.isTrusted && event.button === 0 && capture?.kind === 'text') event.preventDefault();
    event.stopPropagation();
  });
  controls.addEventListener('click', event => event.stopPropagation());

  async function send(action, fields) {
    try {
      return P.response(await chrome.runtime.sendMessage({channel: P.CHANNEL, action, site, ...fields}));
    } catch {
      return P.failure('CANVAS_NOT_READY');
    }
  }

  selectionChip.addEventListener('click', async event => {
    if (!event.isTrusted || busy || capture?.kind !== 'text') return;
    if (menuOpen) {
      menuOpen = canAppend = false;
      menu.hidden = true;
      selectionChip.setAttribute('aria-expanded', 'false');
      position();
      return;
    }
    const current = {...capture};
    capture = current;
    menuOpen = true;
    append.disabled = true;
    reason.hidden = false;
    reason.textContent = '正在检查画布…';
    menu.hidden = false;
    selectionChip.setAttribute('aria-expanded', 'true');
    position();
    const reply = await send('status');
    if (!menuOpen || capture !== current) return;
    canAppend = reply.ok && reply.canAppend === true;
    append.disabled = !canAppend;
    reason.textContent = canAppend ? '' : P.errorText(reply.ok ? 'SELECT_ONE_IMAGE' : reply.error);
    reason.hidden = canAppend;
    position();
  });

  async function commit(captureWork) {
    busy = true;
    dismiss();
    clearTimeout(feedbackTimer);
    feedback.hidden = true;
    feedback.textContent = '';
    try {
      const reply = await captureWork();
      notify(reply.ok ? '已复制到画布。' : P.errorText(reply.error));
    } catch {
      notify(P.errorText('CAPTURE_FAILED'));
    } finally {
      busy = false;
    }
  }

  function textClick(event, action) {
    if (!event.isTrusted || busy || !menuOpen || capture?.kind !== 'text' ||
        (action === 'append-prompt' && !canAppend)) return;
    const {text} = capture;
    void commit(() => send(action, {text}));
  }
  append.addEventListener('click', event => textClick(event, 'append-prompt'));
  newText.addEventListener('click', event => textClick(event, 'new-text'));

  function imageMime(bytes) {
    const head = String.fromCharCode(...bytes.subarray(0, 12));
    if (head.startsWith('\x89PNG\r\n\x1a\n')) return 'image/png';
    if (head.startsWith('\xff\xd8\xff')) return 'image/jpeg';
    if (head.startsWith('GIF87a') || head.startsWith('GIF89a')) return 'image/gif';
    if (head.startsWith('RIFF') && head.slice(8, 12) === 'WEBP') return 'image/webp';
    return null;
  }

  async function readImage({image, url}) {
    if (loadedSource(image) !== url) return P.failure('IMAGE_UNAVAILABLE');
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 8000);
    try {
      // A cross-origin image host must not receive credentials, even if the browser has a session there.
      const credentials = new URL(url).origin === location.origin ? 'same-origin' : 'omit';
      const reply = await fetch(url, {mode: 'cors', credentials, redirect: 'error', referrerPolicy: 'no-referrer', signal: controller.signal});
      if (!reply.ok || !reply.body) return P.failure('IMAGE_UNAVAILABLE');
      if (Number(reply.headers.get('Content-Length')) > P.MAX_BYTES) return P.failure('INVALID_IMAGE');
      const reader = reply.body.getReader();
      const chunks = [];
      let size = 0;
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        size += value.length;
        if (size > P.MAX_BYTES) return P.failure('INVALID_IMAGE');
        chunks.push(value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      const mime = imageMime(bytes);
      if (!P.supportedMime(mime)) return P.failure('INVALID_IMAGE');
      let encoded = '';
      const chunkSize = 0x6000; // A multiple of three keeps padding exclusive to the final Base64 chunk.
      for (let i = 0; i < bytes.length; i += chunkSize) {
        encoded += btoa(String.fromCharCode(...bytes.subarray(i, i + chunkSize)));
      }
      return send('new-image', {mime, bytes: encoded});
    } catch {
      return P.failure('IMAGE_UNAVAILABLE');
    } finally {
      clearTimeout(deadline);
      controller.abort();
    }
  }

  imageChip.addEventListener('click', event => {
    if (!event.isTrusted || busy || capture?.kind !== 'image') return;
    const image = capture;
    void commit(() => readImage(image));
  });
})();
