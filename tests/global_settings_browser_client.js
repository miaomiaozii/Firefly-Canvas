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
      return s?.dataset.kind === 'success';
    });

    await waitFor('provider cards', () => {
      const list = document.getElementById('providerList');
      return list ? list.querySelectorAll('.provider-card').length : 0;
    }, 20000);

    const categories = [...document.querySelectorAll('#settingsNav button')];
    const panelFor = button => document.getElementById(button.getAttribute('aria-controls'));
    const visiblePanels = () => [...document.querySelectorAll('[data-settings-panel]')].filter(panel => getComputedStyle(panel).display !== 'none');
    const groups = [...document.querySelectorAll('.settings-nav-group')].map(group => group.querySelectorAll('button').length);
    const widthFits = () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1;
    record('eight categories, grouped 3/2/3, with Appearance selected initially', categories.length === 8 && groups.join() === '3,2,3' && visiblePanels()[0]?.id === 'preferencesPanel', groups);
    let categoryChecks = true;
    for (const button of categories) {
      button.click();
      const panel = panelFor(button);
      categoryChecks &&= visiblePanels().length === 1 && visiblePanels()[0] === panel && widthFits()
        && button.getAttribute('aria-current') === 'true' && document.activeElement === panel.querySelector('h2');
    }
    record('each category displays only its own panel, focuses its title, and fits the viewport', categoryChecks, innerWidth);
    categories[0]?.click();
    const issue = document.querySelector('#settingsIssueActions button');
    if (issue) {
      issue.click();
      record('issue action opens the affected section', visiblePanels()[0]?.id === issue.dataset.issuePanel, issue.dataset.issuePanel);
      categories[0]?.click();
    }

    const theme = document.getElementById('themeSelect');
    // #365：语言下拉已从设置页拿掉（英文选项实际用不到），主题下拉还在。
    const lang = document.getElementById('languageSelect');
    record('preference selects populated', !!theme && theme.options.length >= 2, 'theme=' + (theme ? theme.options.length : 0));
    record('language control removed', !lang, 'languageSelect=' + (lang ? 'present' : 'missing'));

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

    const section = document.querySelector('#preferencesPanel .settings-card');
    const sectionTitle = document.querySelector('.section-heading h2');
    const providerCard = document.querySelector('.provider-card');
    const providerTitle = providerCard?.querySelector('.provider-name');
    const providerLabel = providerCard?.querySelector('label');
    const providerInput = providerCard?.querySelector('input:not([type="checkbox"]):not([type="radio"])');
    const providerSelect = providerCard?.querySelector('select');
    const providerTextarea = providerCard?.querySelector('textarea');
    const providerCheckbox = providerCard?.querySelector('input[type="checkbox"]');
    const subtitle = document.querySelector('.subtitle');
    const typographyOk = [
      [sectionTitle, 20],
      [providerTitle, 14],
      [providerLabel, 13],
      [providerInput, 12],
      [providerSelect, 12],
    ].every(([el, expected]) => el && Math.abs(px(getComputedStyle(el).fontSize) - expected) < 0.25);
    record('settings typography follows foundation', typographyOk, [sectionTitle, providerTitle, providerLabel, providerInput, providerSelect].map(el => el ? getComputedStyle(el).fontSize : 'missing').join('/'));

    const inputHeight = providerInput ? px(getComputedStyle(providerInput).height) : 0;
    const selectHeight = providerSelect ? px(getComputedStyle(providerSelect).height) : 0;
    const textareaHeight = providerTextarea ? px(getComputedStyle(providerTextarea).minHeight) : 0;
    const checkboxStyle = providerCheckbox ? getComputedStyle(providerCheckbox) : null;
    record('form control heights preserve native checks and textarea', Math.abs(inputHeight - 34) < 0.25 && Math.abs(selectHeight - 34) < 0.25 && textareaHeight >= 90 && px(checkboxStyle?.width) <= 20 && px(checkboxStyle?.height) <= 20, `input=${inputHeight} select=${selectHeight} textareaMin=${textareaHeight} checkbox=${checkboxStyle?.width || 'missing'}x${checkboxStyle?.height || 'missing'}`);

    const radii = {
      section: section ? px(getComputedStyle(section).borderRadius) : 0,
      editor: providerCard ? px(getComputedStyle(providerCard.querySelector('.provider-editor')).borderRadius) : 0,
      input: providerInput ? px(getComputedStyle(providerInput).borderRadius) : 0,
      button: savePrefs ? px(getComputedStyle(savePrefs).borderRadius) : 0,
    };
    record('settings radius scale is mapped', radii.section === 12 && radii.editor === 8 && radii.input === 8 && radii.button === 6, JSON.stringify(radii));
    const buttonHeights = [...categories, savePrefs, saveProviders].map(el => px(getComputedStyle(el).height));
    record('settings uses 30px toolbar buttons', buttonHeights.every(height => height === 30), buttonHeights);

    const sectionStyle = section ? getComputedStyle(section) : null;
    record('settings uses the dotted workspace and solid grouped cards', getComputedStyle(document.body).backgroundImage.startsWith('radial-gradient(') && sectionStyle?.backgroundImage === 'none', getComputedStyle(document.body).backgroundImage);

    const interactive = [...document.querySelectorAll('button, select, textarea, input:not([type="checkbox"]):not([type="radio"]), .model-option')];
    const undersized = interactive.filter(el => px(getComputedStyle(el).fontSize) < 12);
    record('interactive text stays readable', undersized.length === 0, undersized.map(el => `${el.tagName}.${el.className}:${getComputedStyle(el).fontSize}`).join(', ') || 'all >= 12px');

    const initialTheme = document.documentElement.classList.contains('studio-theme-dark') ? 'dark' : 'light';
    const themeElements = { subtitle, primary: savePrefs, input: providerInput };
    const lightMetrics = themeMetrics('light', themeElements);
    const darkMetrics = themeMetrics('dark', themeElements);
    window.StudioTheme.apply(initialTheme);
    record('light and dark token surfaces are distinct', lightMetrics.page !== darkMetrics.page && lightMetrics.panel !== darkMetrics.panel, JSON.stringify({ light: lightMetrics, dark: darkMetrics }));
    record('canvas accent is shared in both themes', lightMetrics.accent === '#865500' && darkMetrics.accent === '#edbd62', `${lightMetrics.accent}/${darkMetrics.accent}`);
    record('light theme content meets readable contrast', Math.min(lightMetrics.body, lightMetrics.subtitle, lightMetrics.primary, lightMetrics.input) >= 4.5, JSON.stringify(lightMetrics));
    record('dark theme content meets readable contrast', Math.min(darkMetrics.body, darkMetrics.subtitle, darkMetrics.primary, darkMetrics.input) >= 4.5, JSON.stringify(darkMetrics));

    if (window.innerWidth <= 760) {
      const nav = document.querySelector('#settingsNav');
      const sidebar = document.querySelector('.settings-sidebar').getBoundingClientRect();
      const content = document.querySelector('.settings-main').getBoundingClientRect();
      record('mobile categories scroll within the header above the content', getComputedStyle(nav).overflowX === 'auto' && sidebar.bottom <= content.top + 1 && widthFits(), `navigation=${nav.clientWidth}/${nav.scrollWidth}`);
      categories[1]?.click();
      providerCard?.querySelector('.provider-toggle')?.click();
      record('expanded provider editor fits the narrow screen', widthFits(), document.documentElement.scrollWidth);
      providerCard?.querySelector('.provider-toggle')?.click();
      categories[0]?.click();
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
