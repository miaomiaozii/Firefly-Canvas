(() => {
  'use strict';

  const names = Object.freeze({
    'chatgpt.com': 'ChatGPT',
    'gemini.google.com': 'Gemini',
    'chat.deepseek.com': 'DeepSeek',
    'chat.qwen.ai': '通义',
    'www.doubao.com': '豆包',
    'www.kimi.com': 'Kimi',
    'yuanbao.tencent.com': '元宝',
  });
  // A regular website URL is not evidence of permission to embed it.
  const approvedPages = Object.freeze({});

  function mount({view, link, unavailable, heading, note}) {
    let activeFrame;
    return function show(site) {
      activeFrame?.remove();
      activeFrame = null;
      if (!Object.hasOwn(names, site)) return;
      const name = names[site];
      link.href = `https://${site}/`;
      heading.textContent = name;
      const url = approvedPages[site];
      const allowed = FireflyChatProtocol.chatSite(url) === site;
      unavailable.hidden = allowed;
      note.hidden = !allowed;
      // Cross-origin iframe load is also fired for browser-blocked documents.
      // Keep the ordinary-tab path available without claiming the site loaded.
      if (!allowed) return;
      const frame = document.createElement('iframe');
      frame.title = `${name} 对话`;
      frame.referrerPolicy = 'no-referrer';
      frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms');
      frame.src = url;
      view.prepend(frame);
      activeFrame = frame;
    };
  }

  globalThis.FireflyChatEmbed = Object.freeze({names, mount});
})();
