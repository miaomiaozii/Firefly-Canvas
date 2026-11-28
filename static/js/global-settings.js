(() => {
  const pageStatus = document.getElementById('pageStatus');
  const providerList = document.getElementById('providerList');
  const cliGrid = document.getElementById('cliGrid');
  const libraryPath = document.getElementById('libraryPath');
  const moveExisting = document.getElementById('moveExisting');
  const embedded = window.self !== window.top;
  const settingsNav = document.getElementById('settingsNav');
  const navButtons = [...settingsNav.querySelectorAll('button')];
  const issueBanner = document.getElementById('settingsIssues');
  const issueSummary = document.getElementById('settingsIssueSummary');
  const issueActions = document.getElementById('settingsIssueActions');
  const refreshAll = document.getElementById('refreshAll');
  const themeSelect = document.getElementById('themeSelect');
  const upstreamDirect = document.getElementById('upstreamDirect');
  const addProvider = document.getElementById('addProvider');
  const saveProviders = document.getElementById('saveProviders');
  const savePreferences = document.getElementById('savePreferences');
  const libraryForm = document.getElementById('libraryForm');
  const switchLibrary = libraryForm.querySelector('button');
  const writeButtons = [saveProviders, savePreferences, switchLibrary];
  const pendingWrites = new Map();
  let state = null;
  let providerBaseline = '';
  let lockSettings = false;
  let lockedControls = [];
  let loading = false;
  let currentIssuePanels = '';
  let cliNeedsAttention = false;
  let photoshopDisconnected = false;
  let studioApiChannel = null;
  if(typeof BroadcastChannel === 'function'){
    try { studioApiChannel = new BroadcastChannel('studio-api'); } catch(error) {
      // #348: If the API is restricted, standalone settings can still use the opener.
      console.warn('[global-settings] 无法开启画布通知通道', error);
    }
  }
  const tr = key => window.StudioI18n?.t?.(key) || key;
  const localText = (zh, en) => window.StudioI18n?.lang?.() === 'en' ? en : zh;
  const librarySummaryText = library => window.StudioI18n?.lang?.() === 'en'
    ? `Current: ${library?.path || ''} · ${library?.project_count || 0} project(s)`
    : `当前：${library?.path || ''} · ${library?.project_count || 0} 个项目`;

  const setStatus = (message, kind = '', resting = false) => {
    pageStatus.textContent = message || '';
    pageStatus.dataset.kind = kind;
    pageStatus.dataset.resting = String(resting);
  };

  function selectPanel(panelId){
    // Keep form elements mounted so even unsaved secrets and model picks survive navigation.
    for(const button of navButtons){
      const id = button.getAttribute('aria-controls');
      const selected = id === panelId;
      document.getElementById(id).hidden = !selected;
      if(selected){
        button.setAttribute('aria-current', 'true');
        if(settingsNav.scrollWidth > settingsNav.clientWidth){
          const rail = settingsNav.getBoundingClientRect();
          const item = button.getBoundingClientRect();
          if(item.left < rail.left) settingsNav.scrollLeft += item.left - rail.left;
          else if(item.right > rail.right) settingsNav.scrollLeft += item.right - rail.right;
        }
      } else button.removeAttribute('aria-current');
    }
    document.getElementById(panelId).querySelector('h2').focus({preventScroll:true});
    window.scrollTo(0, 0);
    if(panelId === 'chatDockPanel') void pollChatDock();
  }

  for(const button of navButtons){
    button.addEventListener('click', () => selectPanel(button.getAttribute('aria-controls')));
    button.addEventListener('keydown', event => {
      const index = navButtons.indexOf(button);
      const step = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1
        : event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : 0;
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? navButtons.length - 1
        : step ? (index + step + navButtons.length) % navButtons.length : -1;
      if(next < 0) return;
      event.preventDefault();
      navButtons[next].focus();
    });
  }

  function setNavDot(panel, tone, description = ''){
    const button = navButtons.find(item => item.getAttribute('aria-controls') === panel);
    const dot = button?.querySelector('.settings-nav-dot');
    if(!dot) return;
    dot.hidden = !tone;
    dot.dataset.tone = tone;
    dot.id = `${panel}-status`;
    if(tone) button.setAttribute('aria-describedby', dot.id);
    else button.removeAttribute('aria-describedby');
    if(dot.textContent === description) return;
    const label = document.createElement('span');
    label.className = 'visually-hidden';
    label.textContent = description;
    dot.replaceChildren(label);
  }

  function renderIssues(){
    const providerFailed = !lockSettings && [...providerList.querySelectorAll('.provider-card')].some(card => card._testFailed);
    const issues = [
      ['providersPanel', providerFailed, 'problem', 'providerIssue'],
      ['cliPanel', cliNeedsAttention, 'warning', 'cliIssue'],
      ['photoshopPanel', photoshopDisconnected, 'problem', 'photoshopDisconnected'],
    ];
    for(const [panel, present, tone, key] of issues) setNavDot(panel, present ? tone : '', present ? tr(`globalSettings.${key}`) : '');
    const visible = issues.filter(([, present]) => present);
    const issuePanels = visible.map(([panel]) => panel).join(' ');
    if(currentIssuePanels === issuePanels) return;
    const previousFocus = document.activeElement?.dataset.issuePanel;
    currentIssuePanels = issuePanels;
    issueBanner.hidden = !visible.length;
    issueSummary.textContent = visible.map(([, , , key]) => tr(`globalSettings.${key}`)).join(' ');
    const actions = visible.map(([panel]) => {
      const label = navButtons.find(button => button.getAttribute('aria-controls') === panel).querySelector('span').textContent;
      const action = document.createElement('button');
      action.className = 'quiet-button';
      action.type = 'button';
      action.dataset.issuePanel = panel;
      action.textContent = visible.length > 1 ? `${tr('globalSettings.viewIssue')} ${label}` : tr('globalSettings.viewIssue');
      action.setAttribute('aria-label', `${tr('globalSettings.viewIssue')} ${label}`);
      action.addEventListener('click', () => selectPanel(panel));
      return action;
    });
    issueActions.replaceChildren(...actions);
    if(previousFocus) (actions.find(action => action.dataset.issuePanel === previousFocus) || actions[0] || refreshAll).focus();
  }
  const splitModels = value => String(value || '').split(/[\n,]+/).map(item => item.trim()).filter(Boolean);
  const uniqueModels = value => [...new Set((Array.isArray(value) ? value : splitModels(value)).map(item => String(item || '').trim()).filter(Boolean))];
  const joinModels = value => uniqueModels(value).join('\n');
  const providerSecretFields = ['api_key', 'wallet_api_key', 'volcengine_access_key_id', 'volcengine_secret_access_key'];
  const modelFields = ['image_models', 'chat_models', 'video_models'];
  const modelKinds = {image_models:'image', chat_models:'chat', video_models:'video'};
  const makeId = () => `provider-${Date.now().toString(36)}`;
  // #330: 各协议的 Base URL 默认值/占位提示。MiniMax 国内站默认 api.minimax.cn
  // （官方文档值；实测 api.minimaxi.com 也能认证，作备选提示）。
  const PROTOCOL_BASE_URL_DEFAULTS = {minimax:'https://api.minimax.cn'};
  const PROTOCOL_BASE_URL_HINTS = {minimax:'https://api.minimax.cn（国内站默认；https://api.minimaxi.com 也可用）'};
  function syncProtocolBaseUrl(card){
    const protocolSelect = card.querySelector('[data-field="protocol"]');
    const baseInput = card.querySelector('[data-field="base_url"]');
    if(!protocolSelect || !baseInput) return;
    baseInput.placeholder = PROTOCOL_BASE_URL_HINTS[protocolSelect.value] || '';
    const defaultUrl = PROTOCOL_BASE_URL_DEFAULTS[protocolSelect.value];
    if(defaultUrl && !String(baseInput.value || '').trim()) baseInput.value = defaultUrl;
  }

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
    // An embedded page has one channel: also broadcasting would refresh the canvas twice.
    if(embedded && window.parent && window.parent !== window){
      try { window.parent.postMessage(message, window.location.origin); } catch(error) {
        // #348: The canvas may be closing; a notification failure does not undo a saved setting.
        console.warn('[global-settings] 通知画布失败（parent 通道）', error);
      }
      return;
    }
    if(studioApiChannel){
      try { studioApiChannel.postMessage(message); return; } catch(error) {
        // #348: A failed broadcast can still fall back to the opener.
        console.warn('[global-settings] 通知画布失败（BroadcastChannel 通道）', error);
      }
    }
    try { if(window.opener && !window.opener.closed) window.opener.postMessage(message, window.location.origin); } catch(error) {
      // #348: The opener may close between the check and delivery; the save receipt still stands.
      console.warn('[global-settings] 通知画布失败（opener 通道）', error);
    }
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
        <span class="provider-mark" aria-hidden="true"></span>
        <div><h3 class="provider-name"></h3><span class="provider-meta"></span></div>
        <span class="settings-chip" hidden></span>
        <button class="quiet-button provider-toggle" type="button" aria-expanded="false"></button>
      </div>
      <div class="provider-editor" hidden>
      <div class="provider-fields">
        <label>${tr('globalSettings.providerName')}<input data-field="name" maxlength="80"></label>
        <label>${tr('globalSettings.endpoint')}<input data-field="base_url" autocomplete="off"></label>
        <label>${tr('globalSettings.protocol')}<select data-field="protocol"><option value="openai">OpenAI</option><option value="apimart">APIMart</option><option value="gemini">Gemini</option><option value="volcengine">Volcengine</option><option value="minimax">MiniMax</option><option value="codex">OpenAI CLI</option><option value="gemini-cli">Antigravity CLI</option><option value="comfyui">ComfyUI（本地）</option></select></label>
        <label class="wide">${tr('globalSettings.apiKey')}<input data-field="api_key" type="password" autocomplete="new-password"></label>
      </div>
      <div class="model-grid">
        <div class="model-field"><span class="model-field-title">${tr('globalSettings.imageModels')}</span><textarea data-models="image_models"></textarea><div class="model-picker" data-model-picker="image_models" hidden></div></div>
        <div class="model-field"><span class="model-field-title">${tr('globalSettings.chatModels')}</span><textarea data-models="chat_models"></textarea><div class="model-picker" data-model-picker="chat_models" hidden></div></div>
        <div class="model-field"><span class="model-field-title">${tr('globalSettings.videoModels')}</span><textarea data-models="video_models"></textarea><div class="model-picker" data-model-picker="video_models" hidden></div></div>
      </div>
      <div class="provider-models-hint">${tr('globalSettings.modelsHint')}</div>
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
        <button class="quiet-button danger-button remove-provider" type="button">${tr('globalSettings.remove')}</button>
      </div>
      </div>`;
    card._provider = {...provider, id: card.dataset.providerId};
    card._availableModels = {...(provider.available_models || {})};
    card._modelSelectionTouched = {};
    card._testRevision = 0;
    const editor = card.querySelector('.provider-editor');
    const toggle = card.querySelector('.provider-toggle');
    editor.id = `provider-editor-${card.dataset.providerId}`;
    toggle.setAttribute('aria-controls', editor.id);
    toggle.addEventListener('click', () => setProviderExpanded(card, editor.hidden));
    ['name', 'base_url', 'protocol'].forEach(field => {
      card.querySelector(`[data-field="${field}"]`).value = provider[field] || (field === 'protocol' ? 'openai' : '');
    });
    syncProtocolBaseUrl(card);
    card.querySelector('[data-field="protocol"]').addEventListener('change', () => syncProtocolBaseUrl(card));
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
    for(const field of ['base_url', 'protocol', 'enabled', ...providerSecretFields]){
      const input = card.querySelector(`[data-field="${field}"]`);
      input?.addEventListener(input.type === 'checkbox' || field === 'protocol' ? 'change' : 'input', () => invalidateProviderTest(card));
    }
    card.querySelector('[data-field="name"]').addEventListener('input', () => updateProviderHead(card));
    card.querySelector('.remove-provider').addEventListener('click', () => {
      const next = card.nextElementSibling || card.previousElementSibling;
      card.remove();
      renderIssues();
      (next?.querySelector('.provider-toggle') || document.getElementById('addProvider')).focus();
    });
    card.querySelector('[data-provider-action="test"]').addEventListener('click', () => testProvider(card));
    card.querySelector('[data-provider-action="fetch"]').addEventListener('click', () => fetchProviderModels(card));
    updateProviderHead(card);
    return card;
  }

  function updateProviderHead(card){
    const provider = card._provider;
    const name = card.querySelector('[data-field="name"]').value.trim() || tr('globalSettings.newProvider');
    const protocol = card.querySelector('[data-field="protocol"]').value;
    const enabled = card.querySelector('[data-field="enabled"]').checked;
    const editor = card.querySelector('.provider-editor');
    const toggle = card.querySelector('.provider-toggle');
    const baseUrl = card.querySelector('[data-field="base_url"]').value;
    const host = URL.canParse(baseUrl) ? new URL(baseUrl).hostname : '';
    card.querySelector('.provider-name').textContent = name;
    card.querySelector('.provider-mark').textContent = Array.from(name)[0].toLocaleUpperCase();
    card.querySelector('.provider-meta').textContent = host || provider.id;

    const usesKey = ['openai', 'apimart', 'gemini', 'volcengine', 'minimax'].includes(protocol);
    const chip = card.querySelector('.settings-chip');
    const keyMissing = usesKey && provider.has_key === false;
    const statusKey = !enabled ? 'providerDisabled' : card._testFailed ? 'providerTestFailed'
      : usesKey && provider.has_key === true ? 'keyConfigured' : keyMissing ? 'keyNotConfigured' : '';
    chip.hidden = !statusKey;
    chip.textContent = statusKey ? tr(`globalSettings.${statusKey}`) : '';
    if(card._testFailed){
      if(card._testFailureStatus) chip.textContent += ` · HTTP ${card._testFailureStatus}`;
      chip.dataset.tone = 'problem';
    }
    else if(statusKey === 'keyConfigured') chip.dataset.tone = 'ok';
    else delete chip.dataset.tone;
    const action = tr(editor.hidden ? keyMissing ? 'globalSettings.addKey' : 'globalSettings.editProvider' : 'globalSettings.collapseProvider');
    toggle.textContent = action;
    toggle.setAttribute('aria-label', `${action} · ${name}`);
  }

  function setProviderExpanded(card, expanded){
    card.querySelector('.provider-editor').hidden = !expanded;
    card.querySelector('.provider-toggle').setAttribute('aria-expanded', String(expanded));
    updateProviderHead(card);
  }

  function invalidateProviderTest(card, updateIssues = true){
    card._testRevision += 1;
    card._testFailed = false;
    card._testFailureStatus = null;
    const status = card.querySelector('.provider-fetch-status');
    status.textContent = '';
    status.dataset.kind = '';
    updateProviderHead(card);
    if(updateIssues) renderIssues();
  }

  function renderProviders(providers){
    const expandedIds = new Set([...providerList.querySelectorAll('.provider-card')]
      .filter(card => !card.querySelector('.provider-editor').hidden).map(card => card.dataset.providerId));
    providerList.replaceChildren(...providers.map(provider => {
      const card = providerCard(provider);
      if(expandedIds.has(card.dataset.providerId)) setProviderExpanded(card, true);
      return card;
    }));
    providerBaseline = providerDraft();
  }

  function readProviders(){
    return [...providerList.querySelectorAll('.provider-card')].map(providerPayload);
  }

  function providerDraft(){
    const fields = [...providerList.querySelectorAll('input, select, textarea')];
    return JSON.stringify([readProviders(), fields.map(input => [input.value, input.checked])]);
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
    // #493：代理开关延期，旧页面数据中的实验字段不能随 original 展开重新保存。
    delete payload.image_bypass_system_proxy;
    for(const field of providerSecretFields){
      // An empty field preserves the saved key; neither a preview nor a receipt is an input value.
      delete payload[field];
      delete payload[`clear_${field === 'api_key' ? 'key' : field === 'wallet_api_key' ? 'wallet_key' : field}`];
      const value = card.querySelector(`[data-field="${field}"]`)?.value.trim();
      if(value) payload[field] = value;
    }
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
    if(lockSettings || !state || pendingWrites.has(saveProviders)) return;
    if(action === 'test') invalidateProviderTest(card);
    const revision = card._testRevision;
    const payload = providerRequestPayload(card);
    const hideKey = text => payload.api_key ? String(text).split(payload.api_key).join('••••••••') : text;
    button.disabled = true;
    card._pendingActions = (card._pendingActions || 0) + 1;
    status.textContent = action === 'test' ? tr('globalSettings.testingConnection') : tr('globalSettings.fetchingModels');
    status.dataset.kind = '';
    try {
      const data = await requestJson(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      if(lockSettings || !providerList.contains(card) || revision !== card._testRevision) return;
      if(data.ok !== false) applyFetchedModels(card, data);
      if(action === 'test' && data.ok === false && card.querySelector('[data-field="enabled"]').checked){
        // Only an explicit test receipt describes this endpoint draft, not provider health.
        card._testFailed = true;
        card._testFailureStatus = Number.isInteger(data.status) && data.status >= 400 && data.status <= 599 ? data.status : null;
        updateProviderHead(card);
        renderIssues();
      }
      const fallback = data.ok === false ? tr(action === 'test' ? 'globalSettings.providerTestFailed' : 'globalSettings.fetchFailed') : tr('globalSettings.modelsReady');
      status.textContent = hideKey(data.message || fallback);
      status.dataset.kind = data.ok === false ? 'error' : 'success';
    } catch(error){
      if(lockSettings || !providerList.contains(card) || revision !== card._testRevision) return;
      status.textContent = hideKey(error.message || tr('globalSettings.fetchFailed'));
      status.dataset.kind = 'error';
    } finally {
      if(lockSettings) lockedControls.push(button);
      else button.disabled = false;
      card._pendingActions -= 1;
    }
  }

  function testProvider(card){ return runProviderAction(card, 'test', '/api/providers/test-connection'); }
  function fetchProviderModels(card){ return runProviderAction(card, 'fetch', '/api/providers/fetch-models'); }

  function renderCli(cli){
    const labels = {codex:'OpenAI Codex', gemini:'Antigravity'};
    cliNeedsAttention = false;
    cliGrid.replaceChildren(...Object.entries(labels).map(([key, label]) => {
      const status = cli?.[key] || {};
      const card = document.createElement('article');
      card.className = 'status-card';
      const installed = status.installed === true;
      // The API also uses installed:false on crashes. Only its explicit not-found receipt confirms absence.
      const notFound = status.installed === false && !status.path && status.message === `未找到 ${label} CLI，请先安装。`;
      const signedOut = installed && status.raw?.login_status_checked === true && status.logged_in === false;
      const signedIn = installed && status.raw?.login_status_checked !== false && status.logged_in === true;
      cliNeedsAttention ||= notFound || signedOut;
      const login = signedIn ? 'loggedIn' : signedOut ? 'loggedOut' : 'loginUnknown';
      card.innerHTML = `<h3></h3><span class="settings-chip status-pill"></span><p class="status-detail"></p>`;
      card.querySelector('h3').textContent = label;
      const pill = card.querySelector('.status-pill');
      pill.textContent = installed ? `${tr('globalSettings.installed')} · ${tr(`globalSettings.${login}`)}`
        : tr(notFound ? 'globalSettings.notInstalled' : 'globalSettings.statusUnknown');
      if(notFound || signedOut) pill.dataset.tone = 'warning';
      else if(signedIn) pill.dataset.tone = 'ok';
      card.querySelector('.status-detail').textContent = status.message || status.version || tr('globalSettings.noStatus');
      if(status.version && status.message) card.querySelector('.status-detail').textContent += ` · ${status.version}`;
      return card;
    }));
  }

  const savedTheme = preferences => preferences?.theme || 'dark';
  const savedDirect = preferences => preferences?.upstream_direct !== false;
  const savedLibraryPath = library => library?.path || '';

  function applySettings(data){
    const previous = state;
    state = data;
    const cards = [...providerList.querySelectorAll('.provider-card')];
    if(!previous || (!cards.some(card => card._pendingActions) && providerDraft() === providerBaseline)){
      renderProviders(data.providers || []);
    } else if(savedDirect(previous.preferences) !== savedDirect(data.preferences)){
      cards.forEach(card => invalidateProviderTest(card, false));
    }
    renderCli(data.cli);
    if(!previous || themeSelect.value === savedTheme(previous.preferences)) themeSelect.value = savedTheme(data.preferences);
    if(!previous || upstreamDirect.checked === savedDirect(previous.preferences)) upstreamDirect.checked = savedDirect(data.preferences);
    if(!previous || libraryPath.value === savedLibraryPath(previous.project_library)) libraryPath.value = savedLibraryPath(data.project_library);
    document.getElementById('librarySummary').textContent = librarySummaryText(data.project_library);
    renderPhotoshop(data.photoshop);
    renderIssues();
  }

  function renderPhotoshop(status){
    const connected = status?.connected === true;
    photoshopDisconnected = status?.connected === false;
    const card = document.getElementById('photoshopStatus');
    card.dataset.connected = connected ? 'true' : photoshopDisconnected ? 'false' : '';
    card.textContent = tr(connected ? 'globalSettings.photoshopConnected'
      : photoshopDisconnected ? 'globalSettings.photoshopDisconnected' : 'globalSettings.statusUnknown');
  }

  async function requestJson(url, options){
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
    return data;
  }

  const chatDockUrl = '/api/global-settings/chat-dock';
  const chatDockFetch = {mode:'same-origin', credentials:'same-origin', referrerPolicy:'same-origin'};
  const chatDockUi = Object.fromEntries([
    'Panel', 'Title', 'State', 'Versions', 'DetectionNote', 'FolderStatus',
    'Guide', 'Installed', 'ManagerUrl', 'FolderPath', 'ReloadNote', 'ActionFeedback',
    'CopyManager', 'OpenFolder', 'CopyFolder', 'Reload', 'Options', 'Uninstall',
  ].map(name => [name, document.getElementById(`chatDock${name}`)]));
  let chatDockFolder;
  let chatDockReceipt;
  let chatDockPolling = false;
  let chatDockReloadedFrom = null;
  let chatDockActionBusy = false;
  // Web pages cannot open chrome:// or edge:// URLs, so the guide copies the address instead.
  const chatDockManagerUrl = /\bEdg\//.test(navigator.userAgent) ? 'edge://extensions' : 'chrome://extensions';

  const dockText = suffix => tr(`globalSettings.chatDock${suffix}`);
  const versionTuple = version => typeof version === 'string' && /^\d+(?:\.\d+)*$/.test(version) ? version.split('.').map(Number) : null;
  function compareChatDockVersions(installed, bundled){
    const a = versionTuple(installed), b = versionTuple(bundled);
    if(!a || !b) return null;
    for(let i = 0; i < Math.max(a.length, b.length); i++){
      const delta = (a[i] ?? 0) - (b[i] ?? 0);
      if(delta) return Math.sign(delta);
    }
    return 0;
  }

  function setDockText(element, text){
    if(element.textContent !== text) element.textContent = text;
  }

  function setDockVisible(element, visible){
    if(!visible && !element.hidden && !chatDockUi.Panel.hidden && element.contains(document.activeElement)){
      chatDockUi.Title.focus({preventScroll:true});
    }
    element.hidden = !visible;
  }

  function dockFeedback(element, key, kind = ''){
    setDockText(element, key ? dockText(key) : '');
    element.dataset.kind = kind;
  }

  function updateChatDockControls(){
    const available = chatDockFolder?.available === true;
    chatDockUi.OpenFolder.disabled = !available || chatDockActionBusy;
    chatDockUi.CopyFolder.disabled = !available;
    for(const button of [chatDockUi.Reload, chatDockUi.Options, chatDockUi.Uninstall]) button.disabled = chatDockActionBusy;
  }

  function renderChatDock(){
    const receipt = chatDockReceipt;
    const available = chatDockFolder?.available === true;
    const comparison = available ? compareChatDockVersions(receipt?.version, chatDockFolder.version) : null;
    const outdated = receipt?.supported === true && comparison === -1;
    const status = receipt === undefined ? 'Checking' : !receipt ? 'NotDetected' : !receipt.supported ? 'Unsupported' : outdated ? 'Outdated'
      : comparison === null ? 'Installed' : 'Current';
    setDockText(chatDockUi.State, dockText(status));
    const versions = [receipt && `${dockText('RuntimeVersion')} ${receipt.version}`,
      available && `${dockText('FolderVersion')} ${chatDockFolder.version}`].filter(Boolean).join(' · ');
    setDockText(chatDockUi.Versions, versions);
    setDockVisible(chatDockUi.Versions, Boolean(versions));
    setDockText(chatDockUi.DetectionNote, receipt === null ? dockText('NotDetectedNote') : '');
    setDockVisible(chatDockUi.DetectionNote, receipt === null);
    const folderMessage = chatDockFolder === null ? 'FolderUnknown' : chatDockFolder?.available === false ? 'FolderMissing' : '';
    setDockText(chatDockUi.FolderStatus, folderMessage ? dockText(folderMessage) : '');
    setDockVisible(chatDockUi.FolderStatus, Boolean(folderMessage));
    setDockVisible(chatDockUi.Guide, receipt === null);
    setDockVisible(chatDockUi.Installed, Boolean(receipt));
    setDockText(chatDockUi.ManagerUrl, chatDockManagerUrl);
    setDockText(chatDockUi.FolderPath, available ? chatDockFolder.directory : '');
    setDockVisible(chatDockUi.FolderPath, available);
    chatDockUi.Reload.className = outdated ? 'primary-button' : 'quiet-button';
    // reload() only rereads the folder the browser already remembers; still behind afterwards means another folder.
    const staleAfterReload = outdated && chatDockReloadedFrom !== null && chatDockReloadedFrom === receipt.version;
    setDockText(chatDockUi.ReloadNote, staleAfterReload ? dockText('ReloadStillOutdated') : '');
    setDockVisible(chatDockUi.ReloadNote, staleAfterReload);
    updateChatDockControls();
  }

  async function loadChatDockFolder(){
    try {
      const folder = await requestJson(chatDockUrl, chatDockFetch);
      chatDockFolder = folder?.available === false || (folder?.available === true && versionTuple(folder.version)
        && typeof folder.directory === 'string' && folder.directory) ? folder : null;
    } catch { chatDockFolder = null; }
    renderChatDock();
  }

  async function pollChatDock(){
    if(chatDockPolling || document.hidden || chatDockUi.Panel.hidden) return;
    chatDockPolling = true;
    try {
      const result = await window.FireflyChatDockClient?.hello();
      chatDockReceipt = result?.ok === true && typeof result.supported === 'boolean' && versionTuple(result.version) ? result : null;
      renderChatDock();
    } finally { chatDockPolling = false; }
  }

  // #345：只报文件名，不展示可能含密钥的配置内容。
  async function dataFaultText(){
    const data = await requestJson('/api/config/data-diagnostics');
    const items = Array.isArray(data.items) ? data.items : [];
    if(!items.length) return '';
    const names = [...new Set(items.map(item => String(item.file || '').split(/[\\/]/).pop()).filter(Boolean))];
    return localText(
      `配置文件读取失败：${names.join('、')}。原始内容已保留；请恢复有效配置后再保存。`,
      `Could not read configuration: ${names.join(', ')}. Original data was preserved; restore a valid configuration before saving.`);
  }

  function updateWriteControls(){
    refreshAll.disabled = loading || pendingWrites.size > 0;
    const blocked = lockSettings || !state || loading;
    for(const button of writeButtons) button.disabled = blocked || pendingWrites.has(button);
    addProvider.disabled = lockSettings || !state || pendingWrites.has(saveProviders);
    updateChatDockControls();
  }

  function startWrite(button){
    if(button.disabled) return false;
    pendingWrites.set(button, document.activeElement === button);
    updateWriteControls();
    return true;
  }

  function finishWrite(button){
    const restoreFocus = pendingWrites.get(button);
    pendingWrites.delete(button);
    updateWriteControls();
    if(restoreFocus && document.activeElement === document.body && !button.closest('[data-settings-panel]').hidden){
      button.focus({preventScroll:true});
    }
  }

  async function load(){
    if(loading || pendingWrites.size) return;
    const restoreFocus = document.activeElement === refreshAll;
    loading = true;
    updateWriteControls();
    setStatus(tr('globalSettings.loading'));
    try {
      const data = await requestJson('/api/global-settings');
      if(data.storage_recovery){
        lockSettings = true;
        for(const control of document.querySelectorAll('[data-settings-panel] :is(button, input, select, textarea)')){
          if(!control.disabled){
            lockedControls.push(control);
            control.disabled = true;
          }
        }
        renderCli();
        renderPhotoshop();
        renderIssues();
        updateTaskDot();
        setStatus(data.storage_recovery.message, 'error');
        return;
      }
      lockSettings = false;
      lockedControls.forEach(control => { control.disabled = false; });
      lockedControls = [];
      applySettings(data);
      const faults = await dataFaultText().catch(() => '');
      if(faults){ setStatus(faults, 'error'); return; }
      setStatus(localText('设置已就绪', 'Settings ready'), 'success', true);
    } catch(error) {
      renderCli();
      renderPhotoshop();
      renderIssues();
      setStatus(error.message || localText('设置读取失败', 'Failed to load settings'), 'error');
    } finally {
      loading = false;
      updateWriteControls();
      if(restoreFocus && document.activeElement === document.body) refreshAll.focus({preventScroll:true});
    }
  }

  const backButton = document.getElementById('backButton');
  function returnToCanvas(){
    if(embedded){ notifyCanvas('close-settings'); return; }
    if(window.opener && !window.opener.closed){ window.close(); return; }
    history.length > 1 ? history.back() : location.assign('/');
  }
  backButton.addEventListener('click', returnToCanvas);
  if(embedded) document.addEventListener('keydown', event => {
    if(event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return;
    event.preventDefault();
    returnToCanvas();
  });
  refreshAll.addEventListener('click', async () => {
    await load();
    void loadChatDockFolder();
    void pollChatDock();
    void refreshConsoleTasks();
    startConsoleTimer();
  });
  addProvider.addEventListener('click', () => {
    const card = providerCard({id:makeId(), enabled:true, protocol:'openai', has_key:false});
    providerList.append(card);
    setProviderExpanded(card, true);
    card.querySelector('[data-field="name"]').focus();
  });
  saveProviders.addEventListener('click', async () => {
    const providers = readProviders();
    if(!providers.length){ setStatus(localText('至少保留一个在线服务商', 'Keep at least one online provider'), 'error'); return; }
    if(!startWrite(saveProviders)) return;
    providerList.inert = true;
    [...providerList.querySelectorAll('.provider-card')].forEach(card => invalidateProviderTest(card, false));
    renderIssues();
    setStatus(localText('正在保存在线服务商…', 'Saving online providers…'));
    try {
      const data = await requestJson('/api/providers', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(providers)});
      state.providers = data.providers || [];
      renderProviders(state.providers);
      notifyCanvas('providers-changed');
      setStatus(localText('在线服务商与模型已保存；Key 已写入 API/.env', 'Providers and models saved; keys were written to API/.env'), 'success');
    } catch(error) { setStatus(error.message || localText('服务商保存失败', 'Failed to save providers'), 'error'); }
    finally { providerList.inert = false; finishWrite(saveProviders); }
  });
  savePreferences.addEventListener('click', async () => {
    if(!startWrite(savePreferences)) return;
    setStatus(localText('正在保存偏好…', 'Saving preferences…'));
    try {
      const theme = themeSelect.value;
      const body = {theme, upstream_direct: upstreamDirect.checked === true};
      const saved = await requestJson('/api/global-settings/preferences', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      if(savedDirect(state.preferences) !== savedDirect(saved.preferences)){
        [...providerList.querySelectorAll('.provider-card')].forEach(card => invalidateProviderTest(card, false));
        renderIssues();
      }
      window.StudioTheme?.set(theme);
      state.preferences = saved.preferences;
      notifyCanvas('preferences-changed', {preferences:state.preferences});
      setStatus(localText('外观已保存', 'Appearance saved'), 'success');
    } catch(error) { setStatus(error.message || localText('偏好保存失败', 'Failed to save preferences'), 'error'); }
    finally { finishWrite(savePreferences); }
  });
  async function copyChatDockText(text){
    try {
      await navigator.clipboard.writeText(text);
      dockFeedback(chatDockUi.ActionFeedback, 'Copied');
    } catch { dockFeedback(chatDockUi.ActionFeedback, 'CopyFailed', 'error'); }
  }
  async function runChatDockAction(work, failed){
    if(chatDockActionBusy) return;
    chatDockActionBusy = true;
    updateChatDockControls();
    dockFeedback(chatDockUi.ActionFeedback, '');
    try {
      if(!(await work())) dockFeedback(chatDockUi.ActionFeedback, failed, 'error');
    } catch { dockFeedback(chatDockUi.ActionFeedback, failed, 'error'); }
    finally {
      chatDockActionBusy = false;
      updateChatDockControls();
    }
  }
  chatDockUi.CopyManager.addEventListener('click', () => copyChatDockText(chatDockManagerUrl));
  chatDockUi.CopyFolder.addEventListener('click', () => {
    if(chatDockFolder?.available === true) void copyChatDockText(chatDockFolder.directory);
  });
  chatDockUi.OpenFolder.addEventListener('click', () => runChatDockAction(async () => {
    await requestJson(`${chatDockUrl}/open-folder`, {...chatDockFetch, method:'POST'});
    return true;
  }, 'OpenFolderFailed'));
  chatDockUi.Options.addEventListener('click', () => runChatDockAction(() => window.FireflyChatDockClient.options(), 'ActionFailed'));
  chatDockUi.Reload.addEventListener('click', () => runChatDockAction(async () => {
    const from = chatDockReceipt?.version ?? null;
    if(!(await window.FireflyChatDockClient.reload())) return false;
    chatDockReloadedFrom = from;
    chatDockReceipt = undefined;
    dockFeedback(chatDockUi.ActionFeedback, 'Reloading');
    renderChatDock();
    // The content script dies with the old runtime; wait for the reloaded one to answer.
    for(let attempt = 0; attempt < 10 && !chatDockReceipt; attempt++){
      await new Promise(resolve => setTimeout(resolve, 600));
      await pollChatDock();
    }
    dockFeedback(chatDockUi.ActionFeedback, chatDockReceipt ? 'Reloaded' : 'ReloadPending');
    return true;
  }, 'ActionFailed'));
  chatDockUi.Uninstall.addEventListener('click', () => runChatDockAction(async () => {
    // Cancelling the browser dialog is not an error; the receipt simply stays.
    await window.FireflyChatDockClient.uninstall();
    chatDockReceipt = undefined;
    await new Promise(resolve => setTimeout(resolve, 600));
    await pollChatDock();
    return true;
  }, 'ActionFailed'));

  libraryForm.addEventListener('submit', async event => {
    event.preventDefault();
    const draft = libraryPath.value;
    const path = draft.trim();
    const move = moveExisting.checked;
    const projectCount = Number(state?.project_library?.project_count || 0);
    if(projectCount && !move){ setStatus(localText('当前库有项目，请勾选显式移动确认', 'The current library has projects; confirm the explicit move'), 'error'); return; }
    if(!startWrite(switchLibrary)) return;
    setStatus(localText('正在复制并完整校验项目库，请勿关闭应用…', 'Copying and fully verifying the project library; keep the app open…'));
    try {
      if(!(await waitForCanvasReadyForLibrarySwitch())) throw new Error(localText('画布尚有未保存修改，项目库切换已取消', 'The canvas still has unsaved changes; the library switch was cancelled'));
      const result = await requestJson('/api/global-settings/project-library', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path, move_existing:move})});
      state.project_library = result.project_library;
      if(libraryPath.value === draft && moveExisting.checked === move){
        libraryPath.value = result.project_library.path;
        moveExisting.checked = false;
      }
      document.getElementById('librarySummary').textContent = librarySummaryText(result.project_library);
      const files = result.migration?.file_count ?? 0;
      setStatus(localText(`项目库已切换，完整校验 ${files} 个文件；原库已保留`, `Project library switched after verifying ${files} file(s); the original library was retained`), 'success');
    } catch(error) { setStatus(`${error.message || localText('项目库迁移失败', 'Project library migration failed')}${localText('；仍使用原项目库', '; the original library remains active')}`, 'error'); }
    finally { finishWrite(switchLibrary); }
  });

  const taskConsoleList = document.getElementById('taskConsoleList');
  const consoleStatus = document.getElementById('consoleStatus');
  const consoleRefresh = document.getElementById('consoleRefresh');
  const consoleStatusFilter = document.getElementById('consoleStatusFilter');
  const consoleProviderFilter = document.getElementById('consoleProviderFilter');
  const consoleProjectFilter = document.getElementById('consoleProjectFilter');
  const consoleAutoRefresh = document.getElementById('consoleAutoRefresh');
  let consoleProjects = [];
  let consoleRows = [];
  let consoleTimer = null;
  let consoleBusy = false;
  let consoleRefreshQueued = false;
  let consoleUnfiltered = false;

  const consoleStatusText = status => ({
    queued: localText('排队', 'Queued'),
    running: localText('运行中', 'Running'),
    succeeded: localText('成功', 'Succeeded'),
    failed: localText('失败', 'Failed'),
    cancelled: localText('已取消', 'Cancelled'),
    uncertain: localText('不确定', 'Uncertain'),
  }[status] || status || localText('未知', 'Unknown'));
  const consoleKindText = kind => ({
    image: localText('图片', 'Image'),
    video: localText('视频', 'Video'),
    'local-edit': localText('本地编辑', 'Local edit'),
  }[kind] || kind || '');
  const formatConsoleDuration = sec => {
    if(sec == null) return '—';
    const total = Math.max(0, Number(sec) || 0);
    if(total < 60) return `${total < 10 ? total.toFixed(1) : Math.round(total)}s`;
    return `${Math.floor(total / 60)}m${Math.round(total % 60)}s`;
  };
  const formatConsoleCost = task => task.cost_usd == null ? '—' : `$${Number(task.cost_usd).toFixed(4)}`;
  const consoleRevealHref = task => {
    const project = (consoleProjects.find(item => item.id === task.project_id) || {});
    const canvasId = encodeURIComponent(project.canvas_id || project.id || task.project_id);
    const focus = task.result_node_id ? `&focus_node=${encodeURIComponent(task.result_node_id)}` : '';
    return `/static/smart-canvas.html?id=${canvasId}&project=${encodeURIComponent(task.project_id)}&project_mode=1${focus}`;
  };

  const makeConsoleRow = task => {
    const row = document.createElement('div');
    row.className = 'task-console-row';
    row.dataset.taskId = task.task_id;
    row.setAttribute('role', 'listitem');
    const statusClass = task.status === 'succeeded' ? 'ok' : (task.status === 'failed' || task.status === 'uncertain') ? 'err' : ((task.status === 'running' || task.status === 'queued') ? 'busy' : '');
    row.innerHTML = `
      <span class="settings-chip task-console-status ${statusClass}"></span>
      <div class="task-console-main">
        <p class="task-console-title"></p>
        <p class="task-console-meta"></p>
      </div>
      <div class="task-console-actions"></div>`;
    row.querySelector('.task-console-status').textContent = consoleStatusText(task.status);
    const title = row.querySelector('.task-console-title');
    title.textContent = `${task.project_name || task.project_id || '—'} · ${task.prompt || localText('（无提示词）', '(no prompt)')}`;
    if(task.error) title.title = task.error;
    row.querySelector('.task-console-meta').textContent = `${consoleKindText(task.kind)} · ${task.provider_name || task.provider || '—'}${task.model ? ` · ${task.model}` : ''} · ${formatConsoleDuration(task.duration_sec)} · ${formatConsoleCost(task)}`;
    const actions = row.querySelector('.task-console-actions');
    const reveal = document.createElement('a');
    reveal.href = consoleRevealHref(task);
    reveal.className = 'quiet-button';
    reveal.textContent = localText('查看', 'View');
    reveal.title = localText('跳转到产物所在画布并定位节点', 'Open the canvas that owns this result and locate its node');
    actions.append(reveal);
    if(task.cancellable){
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'quiet-button';
      cancel.textContent = localText('取消', 'Cancel');
      cancel.addEventListener('click', () => { void cancelConsoleTask(task, cancel); });
      actions.append(cancel);
    }
    return row;
  };

  const renderConsoleRows = () => {
    const providerFilter = consoleProviderFilter.value;
    const visible = consoleRows.filter(task => !providerFilter || task.provider === providerFilter);
    const focused = document.activeElement;
    const taskId = focused?.closest('.task-console-row')?.dataset.taskId;
    const rows = visible.map(makeConsoleRow);
    taskConsoleList.replaceChildren(...rows);
    if(taskId){
      // Polling must not send keyboard focus back to the top of the page.
      const row = rows.find(item => item.dataset.taskId === taskId);
      const target = row?.querySelector(focused.tagName.toLowerCase()) || row?.querySelector('a') || consoleRefresh;
      target.focus({preventScroll:true});
    }
    if(!visible.length){
      const empty = document.createElement('p');
      empty.className = 'task-console-empty';
      empty.textContent = localText('暂无匹配任务', 'No matching tasks');
      taskConsoleList.append(empty);
    }
  };

  const syncConsoleFilters = () => {
    const fill = (select, options, placeholder) => {
      const current = select.value;
      const first = document.createElement('option');
      first.value = '';
      first.textContent = placeholder;
      select.replaceChildren(first, ...options.map(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        return option;
      }));
      if(current && [...select.options].some(option => option.value === current)) select.value = current;
    };
    fill(consoleProviderFilter, [...new Set(consoleRows.map(task => task.provider).filter(Boolean))].sort().map(provider => [provider, provider]), localText('全部', 'All'));
    fill(consoleProjectFilter, consoleProjects.map(project => [project.id, project.name || project.id]), localText('全部', 'All'));
  };

  function setConsoleError(message = ''){
    consoleStatus.hidden = !message;
    consoleStatus.textContent = message;
    consoleStatus.dataset.kind = message ? 'error' : '';
  }

  function updateTaskDot(){
    // The API caps the list at 200 and has no total; a dot asserts presence only.
    const hasActive = !lockSettings && consoleUnfiltered && consoleRows.some(task => task.status === 'queued' || task.status === 'running');
    setNavDot('tasksPanel', hasActive ? 'activity' : '', hasActive ? tr('globalSettings.tasksActive') : '');
  }

  async function cancelConsoleTask(task, button){
    button.disabled = true;
    try {
      await requestJson(`/api/project-library/${encodeURIComponent(task.project_id)}/image-generation-tasks/${encodeURIComponent(task.task_id)}/cancel`, {method:'POST'});
      task.status = 'cancelled';
      task.cancellable = false;
      task.error = localText('任务已取消', 'Task cancelled');
      renderConsoleRows();
      updateTaskDot();
    } catch(error) {
      button.disabled = false;
      setConsoleError(error.message || localText('任务取消失败', 'Failed to cancel task'));
    }
  }

  async function refreshConsoleTasks(queue = false){
    if(!state || lockSettings) return;
    if(consoleBusy){ consoleRefreshQueued ||= queue; return; }
    consoleBusy = true;
    const status = consoleStatusFilter.value;
    const project = consoleProjectFilter.value;
    try {
      const query = new URLSearchParams({limit: '200'});
      if(status) query.set('status', status);
      if(project) query.set('project_id', project);
      const data = await requestJson(`/api/global-tasks?${query.toString()}`);
      if(lockSettings || status !== consoleStatusFilter.value || project !== consoleProjectFilter.value) return;
      consoleProjects = data.projects || [];
      consoleRows = data.tasks || [];
      consoleUnfiltered = !status && !project;
      setConsoleError();
      syncConsoleFilters();
      renderConsoleRows();
    } catch(error) {
      if(lockSettings || status !== consoleStatusFilter.value || project !== consoleProjectFilter.value) return;
      consoleUnfiltered = false;
      consoleRows = [];
      renderConsoleRows();
      setConsoleError(error.message || localText('任务总控台读取失败', 'Failed to load global tasks'));
    } finally {
      consoleBusy = false;
      updateTaskDot();
      if(consoleRefreshQueued){ consoleRefreshQueued = false; void refreshConsoleTasks(); }
    }
  }

  const startConsoleTimer = () => {
    if(consoleTimer){ clearInterval(consoleTimer); consoleTimer = null; }
    if(state && !lockSettings && consoleAutoRefresh.checked) consoleTimer = setInterval(() => { void refreshConsoleTasks(); }, 2000);
  };

  const changeConsoleFilter = () => {
    consoleUnfiltered = false;
    updateTaskDot();
    void refreshConsoleTasks(true);
  };
  consoleRefresh.addEventListener('click', () => { void refreshConsoleTasks(true); });
  consoleStatusFilter.addEventListener('change', changeConsoleFilter);
  consoleProjectFilter.addEventListener('change', changeConsoleFilter);
  consoleProviderFilter.addEventListener('change', renderConsoleRows);
  consoleAutoRefresh.addEventListener('change', startConsoleTimer);
  renderChatDock();
  void loadChatDockFolder();
  if(new URLSearchParams(location.search).get('panel') === 'chat-dock') selectPanel('chatDockPanel');
  else void pollChatDock();
  if(embedded) window.addEventListener('message', event => {
    if(event.source === window.parent && event.origin === location.origin && event.data?.type === 'settings-open-chat-dock'){
      selectPanel('chatDockPanel');
    }
  });
  window.addEventListener('focus', () => { void pollChatDock(); });
  document.addEventListener('visibilitychange', () => { void pollChatDock(); });
  setInterval(() => { void pollChatDock(); }, 2000);
  void load().then(() => {
    void refreshConsoleTasks();
    startConsoleTimer();
  });
})();
