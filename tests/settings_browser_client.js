(function(){
    'use strict';

    const query = new URLSearchParams(location.search);
    const projectId = query.get('settingsProject') || query.get('project') || '';
    const switchPath = query.get('settingsSwitchPath') || '';
    const result = {
        kind:'final',
        result_id:`settings-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
        clientScriptLoaded:true,
        viewport:{width:innerWidth, height:innerHeight},
        assertions:[],
        errors:[],
        pass:false
    };
    let posted = false;
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
    async function waitFor(predicate, timeout=15000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try { if(predicate()) return true; } catch(_) {}
            await sleep(50);
        }
        return false;
    }
    async function postResult(){
        if(posted) return;
        posted = true;
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__conversation_browser_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    function settingsFrame(){ return document.querySelector('#smartSettingsFrame'); }
    function settingsDocument(){ return settingsFrame()?.contentDocument || null; }
    function setting(selector){ return settingsDocument()?.querySelector(selector) || null; }
    function selectCategory(panel){ setting(`#settingsNav [aria-controls="${panel}"]`)?.click(); }
    function setValue(element, value, eventName='input'){
        element.value = value;
        element.dispatchEvent(new Event(eventName, {bubbles:true}));
    }
    async function fetchSettings(){
        const response = await fetch('/api/global-settings');
        return response.ok ? response.json() : {};
    }
    async function run(){
        const canvasReady = await waitFor(() => document.querySelector('#smartTitle')?.textContent.includes('Settings browser project'));
        assertion('temporary project canvas loads in Chrome', canvasReady, document.querySelector('#smartTitle')?.textContent || '');
        const link = document.querySelector('#smartSettingsLink');
        assertion('canvas exposes the global settings control', Boolean(link), link?.outerHTML || '');
        if(!link) return postResult();

        link.focus();
        link.click();
        const modalOpen = await waitFor(() => document.querySelector('#smartSettingsModal')?.classList.contains('open'));
        assertion('settings opens as an accessible canvas overlay', modalOpen, {
            open:document.querySelector('#smartSettingsModal')?.className || '',
            hidden:document.querySelector('#smartSettingsModal')?.getAttribute('aria-hidden')
        });
        const frameReady = await waitFor(() => {
            const doc = settingsDocument();
            return doc?.querySelector('#pageStatus')?.dataset.kind === 'success' && Boolean(doc.querySelector('#providerList .provider-card'));
        });
        const frameDoc = settingsDocument();
        assertion('embedded settings iframe loads its real provider and CLI state', frameReady, {
            status:frameDoc?.querySelector('#pageStatus')?.textContent || '',
            providers:frameDoc?.querySelectorAll('#providerList .provider-card').length || 0,
            cli:frameDoc?.querySelectorAll('#cliGrid .status-card').length || 0
        });
        const initialKey = setting('[data-field="api_key"]');
        assertion('provider key is write-only in the browser', !!initialKey?.placeholder && initialKey.value === '' && !initialKey.placeholder.includes('super-secret-value'), initialKey?.placeholder || '');
        assertion('CLI status renders both supported CLI cards', frameDoc?.querySelectorAll('#cliGrid .status-card').length === 2, frameDoc?.querySelectorAll('#cliGrid .status-card').length || 0);
        if(!frameReady) return postResult();

        selectCategory('providersPanel');
        const card = setting('#providerList .provider-card');
        card?.querySelector('.provider-toggle').click();
        const key = card?.querySelector('[data-field="api_key"]');
        setValue(key, 'browser-key-rotated');
        selectCategory('libraryPanel');
        setValue(setting('#libraryPath'), switchPath);
        selectCategory('preferencesPanel');
        setValue(setting('#themeSelect'), 'light', 'change');
        setting('#savePreferences').click();
        const preferencesSaved = await waitFor(() => setting('#pageStatus')?.dataset.kind === 'success' && setting('#pageStatus')?.dataset.resting === 'false');
        const savedSettings = await fetchSettings();
        assertion('valid theme preference saves from the iframe with Chinese as the only language', preferencesSaved && savedSettings.preferences?.theme === 'light' && savedSettings.preferences?.language === 'zh', {
            status:setting('#pageStatus')?.textContent || '',
            preferences:savedSettings.preferences,
            languageSelect:setting('#languageSelect') ? 'present' : 'missing'
        });
        assertion('preference change reaches the hosting canvas', document.documentElement.classList.contains('studio-theme-dark') === false, document.documentElement.className);

        selectCategory('providersPanel');
        assertion('section switches and preference save preserve hidden provider and library drafts', setting('#providerList .provider-card') === card && key.value === 'browser-key-rotated' && setting('#libraryPath').value === switchPath && !card.querySelector('.provider-editor').hidden, frameDoc.activeElement?.id);
        const name = card?.querySelector('[data-field="name"]');
        const imageModels = card?.querySelector('[data-models="image_models"]');
        const testProvider = card?.querySelector('[data-provider-action="test"]');
        const fetchModels = card?.querySelector('[data-provider-action="fetch"]');
        testProvider?.click();
        const tested = await waitFor(() => card?.querySelector('.provider-fetch-status')?.dataset.kind === 'success' && card?.querySelector('.model-option input[value="nano-banana-pro"]'));
        assertion('provider test action fetches and groups models through the UI', tested, {
            status:card?.querySelector('.provider-fetch-status')?.textContent || '',
            models:[...card?.querySelectorAll('.model-option input')].map(item => item.value)
        });
        fetchModels?.click();
        const fetched = await waitFor(() => card?.querySelector('.provider-fetch-status')?.dataset.kind === 'success' && card?.querySelector('.model-option input[value="veo-browser"]'));
        assertion('provider fetch action refreshes the model picker', fetched, card?.querySelector('.provider-fetch-status')?.textContent || '');
        setValue(name, 'Browser Provider Updated');
        setValue(imageModels, 'nano-banana-pro\ncustom-image');
        const addProvider = setting('#addProvider');
        const providerCount = frameDoc.querySelectorAll('#providerList .provider-card').length;
        addProvider.click();
        const added = await waitFor(() => frameDoc.querySelectorAll('#providerList .provider-card').length === providerCount + 1);
        frameDoc.querySelector('#providerList .provider-card:last-child .remove-provider')?.click();
        const removed = await waitFor(() => frameDoc.querySelectorAll('#providerList .provider-card').length === providerCount);
        assertion('provider add and remove controls keep the registry editable', added && removed, frameDoc.querySelectorAll('#providerList .provider-card').length);
        setting('#saveProviders')?.click();
        const providersSaved = await waitFor(() => setting('#pageStatus')?.dataset.kind === 'success');
        const providersAfterSave = await fetchSettings();
        const savedProvider = (providersAfterSave.providers || []).find(item => item.id === 'secret-test');
        const serializedProviderResponse = JSON.stringify(providersAfterSave);
        assertion('provider edits and model selections persist without leaking the key', providersSaved && savedProvider?.name === 'Browser Provider Updated' && savedProvider?.image_models?.includes('custom-image') && !serializedProviderResponse.includes('browser-key-rotated'), {
            status:setting('#pageStatus')?.textContent || '',
            provider:savedProvider,
            leaked:serializedProviderResponse.includes('browser-key-rotated')
        });

        selectCategory('libraryPanel');
        const pathInput = setting('#libraryPath');
        const moveExisting = setting('#moveExisting');
        const libraryForm = setting('#libraryForm');
        setValue(pathInput, switchPath);
        libraryForm.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
        const blockedSwitch = await waitFor(() => setting('#pageStatus')?.dataset.kind === 'error');
        assertion('non-empty project library switch requires explicit confirmation', blockedSwitch && /confirm|确认/i.test(setting('#pageStatus')?.textContent || ''), setting('#pageStatus')?.textContent || '');
        moveExisting.checked = true;
        libraryForm.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
        const switched = await waitFor(() => setting('#pageStatus')?.dataset.kind === 'success' && (setting('#librarySummary')?.textContent || '').includes(switchPath), 20000);
        const afterSwitch = await fetchSettings();
        assertion('confirmed project library migration completes and updates the summary', switched && afterSwitch.project_library?.path === switchPath, {
            status:setting('#pageStatus')?.textContent || '',
            summary:setting('#librarySummary')?.textContent || '',
            path:afterSwitch.project_library?.path
        });

        frameDoc.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true, cancelable:true}));
        const escaped = await waitFor(() => !document.querySelector('#smartSettingsModal')?.classList.contains('open'));
        assertion('Escape inside settings closes the overlay', escaped && document.activeElement === link, document.activeElement?.id);
        link.focus();
        link.click();
        await waitFor(() => document.querySelector('#smartSettingsModal')?.classList.contains('open'));
        setting('#backButton')?.click();
        const closed = await waitFor(() => !document.querySelector('#smartSettingsModal')?.classList.contains('open'));
        await sleep(150);
        assertion('embedded settings back action closes the overlay and returns focus', closed && document.querySelector('#smartSettingsLink')?.getAttribute('aria-expanded') === 'false' && document.activeElement === link, {
            closed,
            expanded:document.querySelector('#smartSettingsLink')?.getAttribute('aria-expanded'),
            activeId:document.activeElement?.id || ''
        });
    }
    run().catch(error => result.errors.push(error.stack || error.message || String(error))).finally(postResult);
})();
