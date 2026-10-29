(() => {
  const pageStatus = document.getElementById('pageStatus');
  const providerList = document.getElementById('providerList');
  const cliGrid = document.getElementById('cliGrid');
  const libraryPath = document.getElementById('libraryPath');
  const moveExisting = document.getElementById('moveExisting');
  const embedded = window.self !== window.top;
  let state = null;
  let studioApiChannel = null;
  try { studioApiChannel = new BroadcastChannel('studio-api'); } catch(error) {}
  const tr = key => window.StudioI18n?.t?.(key) || key;
  const localText = (zh, en) => window.StudioI18n?.lang?.() === 'en' ? en : zh;
  const librarySummaryText = library => window.StudioI18n?.lang?.() === 'en'
    ? `Current: ${library?.path || ''} · ${library?.project_count || 0} project(s)`
    : `当前：${library?.path || ''} · ${library?.project_count || 0} 个项目`;

  const setStatus = (message, kind = '') => {
    pageStatus.textContent = message || '';
    pageStatus.dataset.kind = kind;
  };
  const splitModels = value => String(value || '').split(/[\n,]+/).map(item => item.trim()).filter(Boolean);
  const uniqueModels = value => [...new Set((Array.isArray(value) ? value : splitModels(value)).map(item => String(item || '').trim()).filter(Boolean))];
  const joinModels = value => uniqueModels(value).join('\n');
  const modelFields = ['image_models', 'chat_models', 'video_models'];
  const modelKinds = {image_models:'image', chat_models:'chat', video_models:'video'};
  const makeId = () => `provider-${Date.now().toString(36)}`;

  function preferredModelForKind(kind, models, recommended=''){
    const available = uniqueModels(models);
    const recommendation = String(recommended || '').trim();
    if(recommendation && available.includes(recommendation)) return recommendation;
    const hints = {
      image: ['nano-banana-pro', 'nano-banana-2.1', 'gemini-nano-banana-2.1', 'nano-banana-2', 'nano-banana', 'gpt-image'],
      chat: ['gpt-5.4', 'chatgpt-5.4', 'gpt-5.'],
      video: [],
    }[kind] || [];
    for(const hint of hints){
      const exact = available.find(model => model.toLowerCase() === hint);
      if(exact) return exact;
      const prefix = available.find(model => model.toLowerCase().startsWith(hint));
      if(prefix) return prefix;
    }
    return kind === 'video' ? '' : (available[0] || '');
  }

  function selectedModels(card, field){
    return splitModels(card.querySelector(`[data-models="${field}"]`)?.value);
  }

  function updateTextareaFromModelPicker(card, field){
    const textarea = card.querySelector(`[data-models="${field}"]`);
    const picker = card.querySelector(`[data-model-picker="${field}"]`);
    if(!textarea || !picker) return;
    card._modelSelectionTouched[field] = true;
    const availableModels = uniqueModels(card._availableModels?.[field]);
    const availableSet = new Set(availableModels);
    // Keep manually entered models that did not come from the latest fetch.
    const customModels = selectedModels(card, field).filter(model => !availableSet.has(model));
    const checkedModels = [...picker.querySelectorAll('[data-model-choice]:checked')].map(input => input.value);
    textarea.value = joinModels([...customModels, ...checkedModels]);
  }

  function syncModelPicker(card, field){
    const selected = new Set(selectedModels(card, field));
    card.querySelectorAll(`[data-model-picker="${field}"] [data-model-choice]`).forEach(input => {
      input.checked = selected.has(input.value);
    });
  }

  function renderModelPicker(card, field){
    const picker = card.querySelector(`[data-model-picker="${field}"]`);
    if(!picker) return;
    const availableModels = uniqueModels(card._availableModels?.[field]);
    picker.replaceChildren();
    picker.hidden = !availableModels.length;
    if(!availableModels.length) return;
    const kind = modelKinds[field];
    const recommended = card._provider?.recommended_models?.[kind] || '';
    const heading = document.createElement('div');
    heading.className = 'model-picker-heading';
    heading.textContent = tr('globalSettings.availableModels');
    const note = document.createElement('div');
    note.className = 'model-picker-note';
    note.textContent = tr('globalSettings.modelSelectionHint');
    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'model-picker-search';
    search.placeholder = tr('globalSettings.searchModels');
    const options = document.createElement('div');
    options.className = 'model-options';
    availableModels.forEach(model => {
      const label = document.createElement('label');
      label.className = 'model-option';
      if(model === recommended) label.dataset.recommended = 'true';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = model;
      input.dataset.modelChoice = 'true';
      input.addEventListener('change', () => updateTextareaFromModelPicker(card, field));
      const text = document.createElement('span');
      text.textContent = model;
      label.append(input, text);
      options.append(label);
    });
    search.addEventListener('input', () => {
      const query = search.value.trim().toLowerCase();
      options.querySelectorAll('.model-option').forEach(option => {
        option.hidden = query && !option.textContent.toLowerCase().includes(query);
      });
    });
    picker.append(heading, note, search, options);
    syncModelPicker(card, field);
  }

  function notifyCanvas(type, payload={}){
    const message = {type, ...payload, source:'global-settings'};
    // Embedded settings already has a direct parent channel. Do not also send
    // the same event through BroadcastChannel: the canvas would refresh twice.
    if(embedded && window.parent && window.parent !== window){
      try { window.parent.postMessage(message, window.location.origin); } catch(error) {}
      return;
    }
    if(studioApiChannel){
      try { studioApiChannel.postMessage(message); return; } catch(error) {}
    }
    try { if(window.opener && !window.opener.closed) window.opener.postMessage(message, window.location.origin); } catch(error) {}
  }
  function waitForCanvasReadyForLibrarySwitch(){
    if(!embedded || !window.parent || window.parent === window) return Promise.resolve(true);
    const requestId = `settings-library-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if(settled) return;
        settled = true;
        clearTimeout(timeout);
        window.removeEventListener('message', onMessage);
        resolve(value);
      };
      const onMessage = event => {
        if(event.origin !== window.location.origin || event.source !== window.parent) return;
        if(event.data?.type === 'settings-project-library-ready' && event.data.requestId === requestId){
          finish(event.data.ok === true);
        }
      };
      const timeout = setTimeout(() => finish(false), 8000);
      window.addEventListener('message', onMessage);
      try { window.parent.postMessage({type:'settings-prepare-project-library', requestId, source:'global-settings'}, window.location.origin); }
      catch(error) { finish(false); }
    });
  }

  function providerCard(provider){
    const card = document.createElement('article');
    card.className = 'provider-card';
    card.dataset.providerId = provider.id || makeId();
    card.innerHTML = `
      <div class="provider-head">
        <div><h3 class="provider-name"></h3><span class="provider-meta"></span></div>
        <button class="remove-provider" type="button">${tr('globalSettings.remove')}</button>
      </div>
      <div class="provider-fields">
        <label>${tr('globalSettings.providerName')}<input data-field="name" maxlength="80"></label>
        <label>${tr('globalSettings.endpoint')}<input data-field="base_url" autocomplete="off"></label>
        <label>${tr('globalSettings.protocol')}<select data-field="protocol"><option value="openai">OpenAI</option><option value="apimart">APIMart</option><option value="gemini">Gemini</option><option value="volcengine">Volcengine</option><option value="codex">OpenAI CLI</option><option value="gemini-cli">Antigravity CLI</option></select></label>
        <label class="wide">${tr('globalSettings.apiKey')}<input data-field="api_key" type="password" autocomplete="new-password"></label>
      </div>
      <div class="model-grid">
        <div class="model-field"><span class="model-field-title">${tr('globalSettings.imageModels')}</span><textarea data-models="image_models"></textarea><div class="model-picker" data-model-picker="image_models" hidden></div></div>
        <div class="model-field"><span class="model-field-title">${tr('globalSettings.chatModels')}</span><textarea data-models="chat_models"></textarea><div class="model-picker" data-model-picker="chat_models" hidden></div></div>
        <div class="model-field"><span class="model-field-title">${tr('globalSettings.videoModels')}</span><textarea data-models="video_models"></textarea><div class="model-picker" data-model-picker="video_models" hidden></div></div>
      </div>
      <div class="provider-actions">
        <button type="button" class="quiet-button" data-provider-action="test">${tr('globalSettings.testConnection')}</button>
        <button type="button" class="quiet-button" data-provider-action="fetch">${tr('globalSettings.fetchModels')}</button>
        <span class="provider-fetch-status" role="status" aria-live="polite"></span>
      </div>
      <div class="provider-recommendations" aria-live="polite"></div>
      <div class="provider-extra-secrets"></div>
      <div class="provider-checks">
        <label class="inline-check"><input data-field="enabled" type="checkbox"> ${tr('globalSettings.enabled')}</label>
        <label class="inline-check"><input data-field="primary" type="checkbox"> ${tr('globalSettings.primary')}</label>
      </div>`;
    card._provider = {...provider, id: card.dataset.providerId};
    card._availableModels = {...(provider.available_models || {})};
    card._modelSelectionTouched = {};
    card.querySelector('.provider-name').textContent = provider.name || tr('globalSettings.newProvider');
    card.querySelector('.provider-meta').textContent = provider.id || card.dataset.providerId;
    ['name', 'base_url', 'protocol'].forEach(field => {
      card.querySelector(`[data-field="${field}"]`).value = provider[field] || (field === 'protocol' ? 'openai' : '');
    });
    const keyInput = card.querySelector('[data-field="api_key"]');
    keyInput.placeholder = provider.key_preview || tr('globalSettings.notConfigured');
    card.querySelector('[data-field="enabled"]').checked = provider.enabled !== false;
    card.querySelector('[data-field="primary"]').checked = provider.primary === true;
    const secretFields = card.querySelector('.provider-extra-secrets');
    if(provider.id === 'runninghub'){
      secretFields.innerHTML = `<label>${tr('globalSettings.walletKey')}<input data-field="wallet_api_key" type="password" autocomplete="new-password"></label>`;
      secretFields.querySelector('[data-field="wallet_api_key"]').placeholder = provider.wallet_key_preview || tr('globalSettings.notConfigured');
    } else if(provider.id === 'volcengine'){
      secretFields.innerHTML = `<label>${tr('globalSettings.accessKey')}<input data-field="volcengine_access_key_id" type="password" autocomplete="new-password"></label><label>${tr('globalSettings.secretKey')}<input data-field="volcengine_secret_access_key" type="password" autocomplete="new-password"></label>`;
      secretFields.querySelector('[data-field="volcengine_access_key_id"]').placeholder = provider.volcengine_access_key_preview || tr('globalSettings.notConfigured');
      secretFields.querySelector('[data-field="volcengine_secret_access_key"]').placeholder = provider.volcengine_secret_key_preview || tr('globalSettings.notConfigured');
    }
    modelFields.forEach(field => {
      card.querySelector(`[data-models="${field}"]`).value = joinModels(provider[field]);
      card.querySelector(`[data-models="${field}"]`).addEventListener('input', () => {
        card._modelSelectionTouched[field] = true;
        syncModelPicker(card, field);
      });
      renderModelPicker(card, field);
    });
    renderRecommendations(card, provider.recommended_models);
    card.querySelector('[data-field="name"]').addEventListener('input', event => {
      card.querySelector('.provider-name').textContent = event.target.value || tr('globalSettings.newProvider');
    });
    card.querySelector('.remove-provider').addEventListener('click', () => card.remove());
    card.querySelector('[data-provider-action="test"]').addEventListener('click', () => testProvider(card));
    card.querySelector('[data-provider-action="fetch"]').addEventListener('click', () => fetchProviderModels(card));
    return card;
  }

  function renderProviders(providers){
    providerList.replaceChildren(...providers.map(providerCard));
  }

  function providerPayload(card, index){
    const original = card._provider || {};
    const value = field => card.querySelector(`[data-field="${field}"]`).value.trim();
    const payload = {
      ...original,
      id: original.id || card.dataset.providerId || `provider-${index + 1}`,
      name: value('name') || `服务商 ${index + 1}`,
      base_url: value('base_url'),
      protocol: value('protocol') || 'openai',
      enabled: card.querySelector('[data-field="enabled"]').checked,
      primary: card.querySelector('[data-field="primary"]').checked,
      image_models: splitModels(card.querySelector('[data-models="image_models"]').value),
      chat_models: splitModels(card.querySelector('[data-models="chat_models"]').value),
      video_models: splitModels(card.querySelector('[data-models="video_models"]').value),
      recommended_models: {...(original.recommended_models || {})},
      available_models: Object.fromEntries(modelFields.map(field => [field, uniqueModels(card._availableModels?.[field])])),
      model_selection_version: 1,
    };
    const key = value('api_key');
    if(key) payload.api_key = key;
    const walletKey = card.querySelector('[data-field="wallet_api_key"]')?.value.trim();
    const accessKey = card.querySelector('[data-field="volcengine_access_key_id"]')?.value.trim();
    const secretKey = card.querySelector('[data-field="volcengine_secret_access_key"]')?.value.trim();
    if(walletKey) payload.wallet_api_key = walletKey;
    if(accessKey) payload.volcengine_access_key_id = accessKey;
    if(secretKey) payload.volcengine_secret_access_key = secretKey;
    ['key_preview', 'key_env', 'has_key', 'wallet_key_preview', 'wallet_key_env', 'has_wallet_key', 'volcengine_access_key_preview', 'volcengine_access_key_env', 'volcengine_secret_key_preview', 'volcengine_secret_key_env', 'has_volcengine_access_key', 'has_volcengine_secret_key'].forEach(keyName => delete payload[keyName]);
    return payload;
  }

  function providerRequestPayload(card){
    const payload = providerPayload(card, [...providerList.querySelectorAll('.provider-card')].indexOf(card));
    return {
      provider_id: payload.id,
      base_url: payload.base_url,
      api_key: payload.api_key || '',
      protocol: payload.protocol,
      image_request_mode: payload.image_request_mode || 'openai',
    };
  }

  function renderRecommendations(card, recommendations){
    const target = card.querySelector('.provider-recommendations');
    if(!target) return;
    const labels = [[tr('globalSettings.imageModels'), recommendations?.image], [tr('globalSettings.chatModels'), recommendations?.chat], [tr('globalSettings.videoModels'), recommendations?.video]];
    const values = labels.filter(([, value]) => value);
    target.textContent = values.length
      ? `${tr('globalSettings.recommendedModels')}：${values.map(([label, value]) => `${label} ${value}`).join(' · ')}`
      : '';
  }

  function applyFetchedModels(card, data){
    card._provider = {...(card._provider || {}), image_request_mode:data.image_request_mode || card._provider?.image_request_mode || 'openai', recommended_models:data.recommended_models || {}};
    card._availableModels = Object.fromEntries(modelFields.map(field => [field, uniqueModels(data[field])]));
    const legacySelection = Number(card._provider?.model_selection_version || 0) < 1;
    modelFields.forEach(field => {
      const kind = modelKinds[field];
      const availableModels = card._availableModels[field];
      const textarea = card.querySelector(`[data-models="${field}"]`);
      const current = selectedModels(card, field);
      const preferred = preferredModelForKind(kind, availableModels, card._provider.recommended_models?.[kind]);
      // A legacy provider often contains the entire fetched list. On its
      // first refresh keep only the preferred image/chat model; video keeps
      // its existing selection because its routing is intentionally unchanged.
      const selected = card._modelSelectionTouched[field]
        ? current
        : legacySelection && field !== 'video_models'
        ? (preferred ? [preferred] : [])
        : (current.length ? current : (preferred ? [preferred] : []));
      if(textarea) textarea.value = joinModels(selected);
      renderModelPicker(card, field);
    });
    renderRecommendations(card, card._provider.recommended_models);
  }

  async function runProviderAction(card, action, url){
    const status = card.querySelector('.provider-fetch-status');
    const button = card.querySelector(`[data-provider-action="${action}"]`);
    if(button) button.disabled = true;
    if(status){ status.textContent = action === 'test' ? tr('globalSettings.testingConnection') : tr('globalSettings.fetchingModels'); status.dataset.kind = ''; }
    try {
      const data = await requestJson(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(providerRequestPayload(card))});
      if(data.ok !== false) applyFetchedModels(card, data);
      if(status){ status.textContent = data.message || tr('globalSettings.modelsReady'); status.dataset.kind = data.ok === false ? 'error' : 'success'; }
      return data;
    } catch(error){
      if(status){ status.textContent = error.message || tr('globalSettings.fetchFailed'); status.dataset.kind = 'error'; }
      return null;
    } finally { if(button) button.disabled = false; }
  }

  function testProvider(card){ return runProviderAction(card, 'test', '/api/providers/test-connection'); }
  function fetchProviderModels(card){ return runProviderAction(card, 'fetch', '/api/providers/fetch-models'); }

  function renderCli(cli){
    const labels = {codex:'OpenAI Codex', gemini:'Antigravity'};
    cliGrid.replaceChildren(...Object.entries(labels).map(([key, label]) => {
      const status = cli?.[key] || {};
      const card = document.createElement('article');
      card.className = 'status-card';
      const installed = status.installed === true;
      const loginText = status.logged_in === true ? tr('globalSettings.loggedIn') : status.logged_in === false ? tr('globalSettings.loggedOut') : tr('globalSettings.loginUnknown');
      card.innerHTML = `<h3></h3><span class="status-pill"></span><p class="status-detail"></p>`;
      card.querySelector('h3').textContent = label;
      const pill = card.querySelector('.status-pill');
      pill.textContent = installed ? `${tr('globalSettings.installed')} · ${loginText}` : tr('globalSettings.notInstalled');
      pill.classList.toggle('ok', installed && status.logged_in !== false);
      card.querySelector('.status-detail').textContent = status.version || status.message || tr('globalSettings.noStatus');
      return card;
    }));
  }

  function render(data){
    state = data;
    renderProviders(data.providers || []);
    renderCli(data.cli || {});
    document.getElementById('themeSelect').value = data.preferences?.theme || 'dark';
    document.getElementById('languageSelect').value = data.preferences?.language || 'zh';
    libraryPath.value = data.project_library?.path || '';
    document.getElementById('librarySummary').textContent = librarySummaryText(data.project_library);
    const ps = data.photoshop || {};
    const psCard = document.getElementById('photoshopStatus');
    psCard.dataset.connected = String(Boolean(ps.connected));
    psCard.textContent = ps.connected ? localText('Photoshop 连接器已连接', 'Photoshop connector connected') : localText('Photoshop 连接器未连接', 'Photoshop connector not connected');
  }

  async function requestJson(url, options){
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
    return data;
  }

  async function load(){
    setStatus(localText('正在读取设置…', 'Loading settings…'));
    try {
      render(await requestJson('/api/global-settings'));
      setStatus(localText('设置已就绪', 'Settings ready'), 'success');
    } catch(error) { setStatus(error.message || localText('设置读取失败', 'Failed to load settings'), 'error'); }
  }

  document.getElementById('backButton').addEventListener('click', () => {
    if(embedded){ notifyCanvas('close-settings'); return; }
    if(window.opener && !window.opener.closed){ window.close(); return; }
    history.length > 1 ? history.back() : location.assign('/');
  });
  document.getElementById('refreshAll').addEventListener('click', load);
  document.getElementById('addProvider').addEventListener('click', () => providerList.appendChild(providerCard({id:makeId(), name:'新服务商', enabled:true, protocol:'openai'})));
  document.getElementById('saveProviders').addEventListener('click', async () => {
    const providers = [...providerList.querySelectorAll('.provider-card')].map(providerPayload);
    if(!providers.length){ setStatus(localText('至少保留一个在线服务商', 'Keep at least one online provider'), 'error'); return; }
    setStatus(localText('正在保存在线服务商…', 'Saving online providers…'));
    try {
      const data = await requestJson('/api/providers', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(providers)});
      state.providers = data.providers || [];
      renderProviders(state.providers);
      notifyCanvas('providers-changed');
      setStatus(localText('在线服务商与模型已保存；Key 已写入 API/.env', 'Providers and models saved; keys were written to API/.env'), 'success');
    } catch(error) { setStatus(error.message || localText('服务商保存失败', 'Failed to save providers'), 'error'); }
  });
  document.getElementById('savePreferences').addEventListener('click', async () => {
    const theme = document.getElementById('themeSelect').value;
    const language = document.getElementById('languageSelect').value;
    setStatus(localText('正在保存偏好…', 'Saving preferences…'));
    try {
      await requestJson('/api/global-settings/preferences', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({theme, language})});
      window.StudioTheme?.set(theme);
      window.StudioI18n?.set(language);
      state.preferences = {theme, language};
      render(state);
      notifyCanvas('preferences-changed', {preferences:state.preferences});
      setStatus(localText('外观与语言已保存', 'Appearance and language saved'), 'success');
    } catch(error) { setStatus(error.message || localText('偏好保存失败', 'Failed to save preferences'), 'error'); }
  });
  document.getElementById('libraryForm').addEventListener('submit', async event => {
    event.preventDefault();
    const path = libraryPath.value.trim();
    const projectCount = Number(state?.project_library?.project_count || 0);
    if(projectCount && !moveExisting.checked){ setStatus(localText('当前库有项目，请勾选显式移动确认', 'The current library has projects; confirm the explicit move'), 'error'); return; }
    setStatus(localText('正在复制并完整校验项目库，请勿关闭应用…', 'Copying and fully verifying the project library; keep the app open…'));
    try {
      if(!(await waitForCanvasReadyForLibrarySwitch())) throw new Error(localText('画布尚有未保存修改，项目库切换已取消', 'The canvas still has unsaved changes; the library switch was cancelled'));
      const result = await requestJson('/api/global-settings/project-library', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path, move_existing:moveExisting.checked})});
      state.project_library = result.project_library;
      libraryPath.value = result.project_library.path;
      moveExisting.checked = false;
      document.getElementById('librarySummary').textContent = librarySummaryText(result.project_library);
      const files = result.migration?.file_count ?? 0;
      setStatus(localText(`项目库已切换，完整校验 ${files} 个文件；原库已保留`, `Project library switched after verifying ${files} file(s); the original library was retained`), 'success');
    } catch(error) { setStatus(`${error.message || localText('项目库迁移失败', 'Project library migration failed')}${localText('；仍使用原项目库', '; the original library remains active')}`, 'error'); }
  });
  load();
})();
