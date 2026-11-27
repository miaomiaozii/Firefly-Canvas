(() => {
  'use strict';

  const P = FireflyChatProtocol;
  const form = document.querySelector('form');
  const input = document.getElementById('firefly-origin');
  const result = document.getElementById('result');
  const submit = form.querySelector('button');
  input.value = P.DEFAULT_ORIGIN;
  chrome.storage.local.get(P.ORIGIN_KEY).then(stored => {
    if (input.value === P.DEFAULT_ORIGIN && !submit.disabled) input.value = P.originSetting(stored);
  }).catch(() => {});

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!event.isTrusted || submit.disabled) return;
    const origin = P.parseOrigin(input.value.trim());
    if (!origin) {
      result.textContent = '请输入 HTTP 或 HTTPS 服务地址，不含路径、查询参数或账号信息。';
      return;
    }
    submit.disabled = true;
    try {
      const granted = origin === P.DEFAULT_ORIGIN || await chrome.permissions.request({origins: [P.canvasPatterns(origin).permission]});
      const reply = granted ? P.response(await chrome.runtime.sendMessage({action: P.SET_ORIGIN, origin})) : P.failure('PERMISSION_DENIED');
      if (reply.ok) input.value = origin;
      result.textContent = reply.ok ? '已保存，请刷新 Firefly 画布标签页。' : P.errorText(reply.error);
    } catch {
      result.textContent = '设置未保存，请重试。';
    } finally {
      submit.disabled = false;
    }
  });
})();

(() => {
  'use strict';

  const P = FireflyChatProtocol;
  const F = FireflyChatFraming;
  const result = document.getElementById('framing-result');
  const boxes = [...document.querySelectorAll('input[data-framing-site]')];
  const name = box => box.parentElement.textContent.trim();
  let loaded = false;
  boxes.forEach(box => { box.disabled = true; });
  chrome.storage.local.get(F.KEY).then(stored => {
    const enabled = F.flags(stored);
    boxes.forEach(box => { box.checked = enabled[box.dataset.framingSite] === true; });
    loaded = true;
  }).catch(() => { result.textContent = '无法读取面板显示设置。'; }).finally(() => {
    if (loaded) boxes.forEach(box => { box.disabled = false; });
  });

  boxes.forEach(box => box.addEventListener('change', async event => {
    const enabled = box.checked;
    if (!event.isTrusted) {
      box.checked = !enabled;
      return;
    }
    boxes.forEach(item => { item.disabled = true; });
    try {
      const reply = P.response(await chrome.runtime.sendMessage({action: F.SET_FRAMING, site: box.dataset.framingSite, enabled}));
      if (!reply.ok) throw new Error(reply.error);
      result.textContent = enabled ? `已开启 ${name(box)}。在对话面板里重新选择该站点即可显示。` : `已关闭 ${name(box)}，站点原有限制已恢复。`;
    } catch {
      box.checked = !enabled;
      result.textContent = '设置未保存，请重试。';
    } finally {
      boxes.forEach(item => { item.disabled = false; });
    }
  }));
})();
