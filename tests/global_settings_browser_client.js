(function () {
  const results = [];

  function record(name, ok, detail) {
    results.push({ name, ok: !!ok, detail });
  }

  async function waitFor(desc, fn, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 15000);
    while (Date.now() < deadline) {
      try {
        const v = await fn();
        if (v) return v;
      } catch (e) {}
      await new Promise(r => setTimeout(r, 250));
    }
    return null;
  }

  function text(el) {
    return el ? (el.textContent || '').trim() : '';
  }

  async function run() {
    const url = window.location.pathname;
    record('page is global-settings', url.indexOf('global-settings.html') >= 0, url);

    await waitFor('page status', () => {
      const s = document.getElementById('pageStatus');
      return s && s.textContent.trim().length > 0;
    });

    await waitFor('provider cards', () => {
      const list = document.getElementById('providerList');
      return list ? list.querySelectorAll('.provider-card').length : 0;
    }, 20000);

    const sections = ['preferencesTitle', 'providersTitle', 'cliTitle', 'libraryTitle', 'psTitle'];
    const sectionEls = {};
    sections.forEach(id => { sectionEls[id] = document.getElementById(id); });
    record('all five sections present', sections.every(id => !!sectionEls[id]), sections.map(id => id + '=' + !!sectionEls[id]).join(' '));

    const theme = document.getElementById('themeSelect');
    const lang = document.getElementById('languageSelect');
    record('preference selects populated', !!theme && !!lang && theme.options.length >= 2 && lang.options.length >= 2, 'theme=' + (theme ? theme.options.length : 0) + ' lang=' + (lang ? lang.options.length : 0));

    const providerList = document.getElementById('providerList');
    const providerCards = providerList ? providerList.querySelectorAll('.provider-card, .provider-row, [class*=provider]') : [];
    record('provider list renders', providerCards.length >= 1, 'cards=' + providerCards.length);

    const addProvider = document.getElementById('addProvider');
    record('add provider button present', !!addProvider, 'present');

      const cliGrid = document.getElementById('cliGrid');
      const cliItems = cliGrid ? cliGrid.querySelectorAll('.status-card') : [];
    record('cli grid renders', !!cliGrid && cliItems.length >= 1, 'items=' + cliItems.length);

    const libraryForm = document.getElementById('libraryForm');
    record('library form present', !!libraryForm, 'present');

    const psStatus = document.getElementById('photoshopStatus');
    record('photoshop status area present', !!psStatus, 'present');

    const savePrefs = document.getElementById('savePreferences');
    const saveProviders = document.getElementById('saveProviders');
    record('save buttons present', !!savePrefs && !!saveProviders, 'prefs=' + !!savePrefs + ' prov=' + !!saveProviders);

    const ok = results.every(r => r.ok);
    const payload = {
      pass: ok,
      assertions: results.map(r => ({ name: r.name, pass: r.ok, detail: r.detail })),
      errors: results.filter(r => !r.ok).map(r => r.name + ': ' + r.detail),
      result_id: 'global-settings',
      done: true,
    };
    try {
      await fetch('/__conversation_browser_results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      document.title = 'POST-FAIL:' + String(e);
    }
  }

  run();
})();
