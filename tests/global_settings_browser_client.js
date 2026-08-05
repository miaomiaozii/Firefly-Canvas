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

  function px(value) {
    return Number.parseFloat(value) || 0;
  }

  function colorParts(value) {
    const match = String(value || '').match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?/i);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])] : null;
  }

  function luminance(parts) {
    const channels = parts.slice(0, 3).map(value => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  }

  function contrast(foreground, background) {
    const fg = colorParts(foreground);
    const bg = colorParts(background);
    if (!fg || !bg) return 0;
    const lighter = Math.max(luminance(fg), luminance(bg));
    const darker = Math.min(luminance(fg), luminance(bg));
    return (lighter + 0.05) / (darker + 0.05);
  }

  function elementBackground(el) {
    let node = el;
    while (node) {
      const value = getComputedStyle(node).backgroundColor;
      const parts = colorParts(value);
      if (parts && parts[3] >= 0.98) return value;
      node = node.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  }

  function elementContrast(el) {
    return contrast(getComputedStyle(el).color, elementBackground(el));
  }

  function themeMetrics(theme, elements) {
    window.StudioTheme.apply(theme);
    const rootStyle = getComputedStyle(document.documentElement);
    return {
      page: rootStyle.getPropertyValue('--page').trim(),
      panel: rootStyle.getPropertyValue('--panel').trim(),
      accent: rootStyle.getPropertyValue('--accent').trim().toLowerCase(),
      body: elementContrast(document.body),
      subtitle: elementContrast(elements.subtitle),
      primary: elementContrast(elements.primary),
      input: elementContrast(elements.input),
    };
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

    const foundationLink = document.querySelector('link[href*="/static/css/studio-foundation.css"]');
    record('shared studio foundation is loaded', !!foundationLink, foundationLink?.getAttribute('href') || 'missing');

    const section = document.querySelector('.settings-section');
    const sectionTitle = document.querySelector('.section-heading h2');
    const providerCard = document.querySelector('.provider-card');
    const providerTitle = providerCard?.querySelector('.provider-name');
    const providerLabel = providerCard?.querySelector('label');
    const providerInput = providerCard?.querySelector('input:not([type="checkbox"]):not([type="radio"])');
    const providerSelect = providerCard?.querySelector('select');
    const providerTextarea = providerCard?.querySelector('textarea');
    const providerCheckbox = providerCard?.querySelector('input[type="checkbox"]');
    const subtitle = document.querySelector('.subtitle');
    const pageTitle = document.querySelector('h1');

    const typographyOk = [
      [sectionTitle, 20],
      [providerTitle, 16],
      [providerLabel, 13],
      [providerInput, 12],
      [providerSelect, 12],
    ].every(([el, expected]) => el && Math.abs(px(getComputedStyle(el).fontSize) - expected) < 0.25);
    record('settings typography follows foundation', typographyOk && px(getComputedStyle(pageTitle).fontSize) >= 32 && px(getComputedStyle(pageTitle).fontSize) <= 48, [sectionTitle, providerTitle, providerLabel, providerInput, providerSelect, pageTitle].map(el => el ? getComputedStyle(el).fontSize : 'missing').join('/'));

    const inputHeight = providerInput ? px(getComputedStyle(providerInput).height) : 0;
    const selectHeight = providerSelect ? px(getComputedStyle(providerSelect).height) : 0;
    const textareaHeight = providerTextarea ? px(getComputedStyle(providerTextarea).minHeight) : 0;
    const checkboxStyle = providerCheckbox ? getComputedStyle(providerCheckbox) : null;
    record('form control heights preserve native checks and textarea', Math.abs(inputHeight - 34) < 0.25 && Math.abs(selectHeight - 34) < 0.25 && textareaHeight >= 90 && px(checkboxStyle?.width) <= 20 && px(checkboxStyle?.height) <= 20, `input=${inputHeight} select=${selectHeight} textareaMin=${textareaHeight} checkbox=${checkboxStyle?.width || 'missing'}x${checkboxStyle?.height || 'missing'}`);

    const radii = {
      section: section ? px(getComputedStyle(section).borderRadius) : 0,
      card: providerCard ? px(getComputedStyle(providerCard).borderRadius) : 0,
      input: providerInput ? px(getComputedStyle(providerInput).borderRadius) : 0,
      button: savePrefs ? px(getComputedStyle(savePrefs).borderRadius) : 0,
    };
    record('settings radius scale is mapped', radii.section === 12 && radii.card === 8 && radii.input === 8 && radii.button === 6, JSON.stringify(radii));

    const sectionStyle = section ? getComputedStyle(section) : null;
    record('settings removes decorative gradients and heavy cards', getComputedStyle(document.body).backgroundImage === 'none' && sectionStyle?.backgroundImage === 'none' && sectionStyle?.boxShadow === 'none', `body=${getComputedStyle(document.body).backgroundImage} section=${sectionStyle?.backgroundImage}/${sectionStyle?.boxShadow}`);

    const interactive = [...document.querySelectorAll('button, select, textarea, input:not([type="checkbox"]):not([type="radio"]), .model-option')];
    const undersized = interactive.filter(el => px(getComputedStyle(el).fontSize) < 12);
    record('interactive text stays readable', undersized.length === 0, undersized.map(el => `${el.tagName}.${el.className}:${getComputedStyle(el).fontSize}`).join(', ') || 'all >= 12px');

    const initialTheme = document.documentElement.classList.contains('studio-theme-dark') ? 'dark' : 'light';
    const themeElements = { subtitle, primary: savePrefs, input: providerInput };
    const lightMetrics = themeMetrics('light', themeElements);
    const darkMetrics = themeMetrics('dark', themeElements);
    window.StudioTheme.apply(initialTheme);
    record('light and dark token surfaces are distinct', lightMetrics.page !== darkMetrics.page && lightMetrics.panel !== darkMetrics.panel, JSON.stringify({ light: lightMetrics, dark: darkMetrics }));
    record('canvas accent is shared in both themes', lightMetrics.accent === '#5879dc' && darkMetrics.accent === '#5879dc', `${lightMetrics.accent}/${darkMetrics.accent}`);
    record('light theme content meets readable contrast', Math.min(lightMetrics.body, lightMetrics.subtitle, lightMetrics.primary, lightMetrics.input) >= 4.5, JSON.stringify(lightMetrics));
    record('dark theme content meets readable contrast', Math.min(darkMetrics.body, darkMetrics.subtitle, darkMetrics.primary, darkMetrics.input) >= 4.5, JSON.stringify(darkMetrics));

    if (window.innerWidth <= 760) {
      const oneColumn = ['.preference-grid', '.provider-fields', '.model-grid', '.status-grid'].every(selector => {
        const el = document.querySelector(selector);
        return el && getComputedStyle(el).gridTemplateColumns.trim().split(/\s+/).length === 1;
      });
      const shellRect = document.querySelector('.settings-shell').getBoundingClientRect();
      const headingDirection = getComputedStyle(document.querySelector('.section-heading')).flexDirection;
      record('mobile settings uses explicit single-column layout', oneColumn && headingDirection === 'column', `columns=${oneColumn} heading=${headingDirection}`);
      record('mobile settings stays inside viewport', shellRect.left >= -1 && shellRect.right <= window.innerWidth + 1 && document.documentElement.scrollWidth <= window.innerWidth + 1, `shell=${shellRect.left.toFixed(1)}..${shellRect.right.toFixed(1)} viewport=${window.innerWidth} scroll=${document.documentElement.scrollWidth}`);
    }

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
