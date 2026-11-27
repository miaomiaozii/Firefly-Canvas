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
