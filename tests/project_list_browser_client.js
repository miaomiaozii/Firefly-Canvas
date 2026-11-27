(async () => {
  const resultId = `project-list-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const assertions = [];
  const errors = [];
  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = detail;
    assertions.push(item);
    if (!item.pass) errors.push(item);
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(name, predicate, timeout = 15000) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
      try {
        last = await predicate();
        if (last) return last;
      } catch (error) {
        last = error?.message || String(error);
      }
      await sleep(100);
    }
    throw new Error(`等待 ${name} 超时: ${last || '无结果'}`);
  }
  const projectList = async () => (await (await fetch('/api/project-library')).json()).projects || [];
  const cardForId = (id, container = '#projectGrid') => [...document.querySelectorAll(`${container} .project-card`)]
    .find(card => card.dataset.projectId === id);
  const clickAction = (id, selector) => {
    const card = cardForId(id);
    card.querySelector('.project-more').click();
    card.querySelector(selector).click();
  };
  const press = (element, key) => element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

  const px = value => Number.parseFloat(value) || 0;
  const colorParts = value => {
    const match = String(value || '').match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?/i);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])] : null;
  };
  const luminance = parts => {
    const channels = parts.slice(0, 3).map(value => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const contrast = (foreground, background) => {
    const fg = colorParts(foreground);
    const bg = colorParts(background);
    if (!fg || !bg) return 0;
    const lighter = Math.max(luminance(fg), luminance(bg));
    const darker = Math.min(luminance(fg), luminance(bg));
    return (lighter + 0.05) / (darker + 0.05);
  };
  const elementBackground = el => {
    let node = el;
    while (node) {
      const value = getComputedStyle(node).backgroundColor;
      const parts = colorParts(value);
      if (parts && parts[3] >= 0.98) return value;
      node = node.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  };
  const elementContrast = el => contrast(getComputedStyle(el).color, elementBackground(el));
  const themeMetrics = (theme, elements) => {
    window.StudioTheme.apply(theme);
    const rootStyle = getComputedStyle(document.documentElement);
    return {
      page: rootStyle.getPropertyValue('--page').trim(),
      panel: rootStyle.getPropertyValue('--panel').trim(),
      accent: rootStyle.getPropertyValue('--accent').trim().toLowerCase(),
      body: elementContrast(document.body),
      summary: elementContrast(elements.summary),
      metadata: elementContrast(elements.metadata),
      primary: elementContrast(elements.primary),
      cardTitle: elementContrast(elements.cardTitle),
    };
  };

  try {
    const cards = await waitFor('project cards', () => document.querySelectorAll('#projectGrid .project-card').length >= 1);
    check('project list loads seeded project', Boolean(cards));
    const seed = (await projectList()).find(item => item.name === 'Browser lifecycle project');
    const seedId = seed.id;
    const seedCard = cardForId(seedId);
    check('seed project name is visible', seedCard?.querySelector('h2')?.textContent === seed.name);
    check('create is the first tile', document.querySelector('#projectGrid').firstElementChild.id === 'gridCreate');
    check('empty state is hidden when projects exist', document.querySelector('#emptyState')?.hidden === true);
    check('card shows the real modified timestamp', seedCard.querySelector('time').dateTime === new Date(seed.updated_at).toISOString());

    const more = seedCard.querySelector('.project-more');
    const menu = seedCard.querySelector('.project-menu');
    check('more controls a closed menu', menu.hidden && more.getAttribute('aria-controls') === menu.id && more.getAttribute('aria-expanded') === 'false');
    more.focus();
    press(more, 'ArrowDown');
    check('down arrow opens menu and focuses version history', !menu.hidden && more.getAttribute('aria-expanded') === 'true' && document.activeElement.matches('.project-versions'));
    press(document.activeElement, 'ArrowDown');
    check('down arrow moves to duplicate', document.activeElement.matches('.project-duplicate'));
    press(document.activeElement, 'End');
    check('end moves to move-to-trash action', document.activeElement.matches('.project-trash'));
    press(document.activeElement, 'Escape');
    check('escape closes menu and restores focus', menu.hidden && document.activeElement === more);
    more.click();
    const menuRect = menu.getBoundingClientRect();
    check('open menu fits inside viewport', menuRect.left >= 0 && menuRect.right <= window.innerWidth + 1 && menuRect.top >= 0 && menuRect.bottom <= window.innerHeight + 1);
    document.querySelector('#projectsTitle').click();
    check('outside click closes menu', menu.hidden);
    clickAction(seedId, '.project-versions');
    check('version history opens without entering the canvas', menu.hidden && document.querySelector('#versionsDialog').open);
    document.querySelector('#closeVersions').click();

    const search = document.querySelector('#projectSearch');
    const searchToggle = document.querySelector('#toggleProjectSearch');
    searchToggle.click();
    check('dock search opens and receives focus', search === document.activeElement && searchToggle.hidden);
    search.value = 'no project has this name';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    check('search reports no matches and hides create tile', !document.querySelector('#searchEmpty').hidden && document.querySelector('#gridCreate').hidden && document.querySelector('#projectSummary').textContent.includes(search.value));
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    check('Escape resets search and restores button focus', !search.value && searchToggle === document.activeElement && !document.querySelector('#gridCreate').hidden);
    const visibleSeed = cardForId(seedId);

    const foundationLink = document.querySelector('link[href*="/static/css/studio-foundation.css"]');
    check('shared studio foundation is loaded', Boolean(foundationLink), foundationLink?.getAttribute('href') || 'missing');
    check('legacy theme stylesheet is not imported', !document.querySelector('link[href*="/static/css/theme.css"]'));

    const pageTitle = document.querySelector('#projectsTitle');
    const summary = document.querySelector('#projectSummary');
    const cardTitle = visibleSeed.querySelector('h2');
    const metadata = visibleSeed.querySelector('time');
    const createButton = document.querySelector('#showCreate');
    const cardAction = visibleSeed.querySelector('.project-action');
    const rootStyle = getComputedStyle(document.documentElement);
    const tokenPx = token => px(rootStyle.getPropertyValue(token));
    const typography = [
      [pageTitle, '--font-section-title'],
      [summary, '--font-body'],
      [cardTitle, '--font-card-title'],
      [metadata, '--font-caption'],
      [createButton, '--font-control'],
      [cardAction, '--font-control'],
    ];
    check('project typography follows foundation', typography.every(([el, token]) => px(getComputedStyle(el).fontSize) === tokenPx(token)));
    const dock = document.querySelector('#projectDock');
    const dockBounds = dock.getBoundingClientRect();
    const headerStyle = getComputedStyle(dock.parentElement);
    check('dock uses canvas inset, padding, and utility height', px(headerStyle.paddingLeft) === tokenPx('--space-16') && px(headerStyle.paddingTop) === tokenPx('--space-16') &&
      px(getComputedStyle(dock).paddingTop) === tokenPx('--space-3') &&
      dock.offsetHeight === tokenPx('--height-utility') + 2 * (tokenPx('--space-3') + tokenPx('--space-1')) &&
      [...dock.querySelectorAll(':scope > .tool-button')].every(el => el.offsetHeight === tokenPx('--height-utility')));
    check('project grid aligns with dock', document.querySelector('#projectGrid').getBoundingClientRect().left === dockBounds.left);
    check('cards use the shared panel radius', px(getComputedStyle(visibleSeed).borderRadius) === tokenPx('--radius-panel'));
    check('placeholder is a decorative 16:10 dot matrix', (() => {
      const thumb = visibleSeed.querySelector('.project-thumb');
      const bounds = thumb.getBoundingClientRect();
      return thumb.getAttribute('aria-hidden') === 'true' && getComputedStyle(thumb).backgroundImage.startsWith('radial-gradient') && Math.abs(bounds.width / bounds.height - 1.6) < 0.01;
    })());

    const initialTheme = document.documentElement.classList.contains('studio-theme-dark') ? 'dark' : 'light';
    const themeElements = { summary, metadata, primary: createButton, cardTitle };
    const lightMetrics = themeMetrics('light', themeElements);
    const darkMetrics = themeMetrics('dark', themeElements);
    window.StudioTheme.apply(initialTheme);
    check('light and dark project surfaces are distinct', lightMetrics.page !== darkMetrics.page && lightMetrics.panel !== darkMetrics.panel, JSON.stringify({ light: lightMetrics, dark: darkMetrics }));
    check('project page shares canvas amber accent', lightMetrics.accent === '#865500' && darkMetrics.accent === '#edbd62', `${lightMetrics.accent}/${darkMetrics.accent}`);
    for (const [theme, metrics] of [['light', lightMetrics], ['dark', darkMetrics]]) {
      const { body, summary, metadata, primary, cardTitle } = metrics;
      check(`${theme} project content meets readable contrast`, Math.min(body, summary, metadata, primary, cardTitle) >= 4.5, JSON.stringify(metrics));
    }

    check('project list stays inside viewport', document.documentElement.scrollWidth <= window.innerWidth + 1, `viewport=${window.innerWidth} scroll=${document.documentElement.scrollWidth}`);
    if (window.innerWidth <= 520) {
      const gridColumns = getComputedStyle(document.querySelector('#projectGrid')).gridTemplateColumns.trim().split(/\s+/).length;
      check('narrow project list uses two columns and icon-only create', gridColumns === 2 &&
        getComputedStyle(document.querySelector('#projectCreateLabel')).display === 'none' &&
        createButton.getAttribute('aria-labelledby') === 'projectCreateLabel');
    }

    const dialog = document.querySelector('#createDialog');
    document.querySelector('#gridCreate').click();
    check('create tile opens the new project dialog', Boolean(dialog?.open));
    const dialogInputStyle = getComputedStyle(document.querySelector('#projectName'));
    check('create dialog form uses shared control typography', Math.abs(px(dialogInputStyle.height) - 34) < 0.25 && px(dialogInputStyle.fontSize) === 12 && px(dialogInputStyle.borderRadius) === 8 && px(getComputedStyle(dialog).borderRadius) === 12, `input=${dialogInputStyle.height}/${dialogInputStyle.fontSize}/${dialogInputStyle.borderRadius} dialog=${getComputedStyle(dialog).borderRadius}`);
    document.querySelector('#projectName').value = '';
    document.querySelector('#createForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    check('empty project name is rejected', Boolean(document.querySelector('#createError')?.textContent));
    document.querySelector('#cancelCreate').click();
    check('new project dialog closes', dialog?.open === false);

    const exportResponse = await fetch(visibleSeed.querySelector('.project-export[download]').href);
    const exportBytes = await exportResponse.arrayBuffer();
    check('Fly export returns a package', exportResponse.ok && exportBytes.byteLength > 100, `${exportResponse.status}/${exportBytes.byteLength}`);
    check('Fly export has download filename', /\.fly/i.test(exportResponse.headers.get('content-disposition') || ''));

    const importBody = new FormData();
    importBody.append('file', new File([exportBytes], 'browser-roundtrip.fly', { type: 'application/zip' }));
    const importResponse = await fetch('/api/project-library/import', { method: 'POST', body: importBody });
    const importedPayload = await importResponse.json();
    check('Fly import succeeds in browser', importResponse.ok && Boolean(importedPayload.project?.id), String(importResponse.status));
    check('Fly import creates a separate project', importedPayload.project?.id !== importedPayload.project?.canvas_id ? false : importedPayload.project?.id !== '');
    const importedId = importedPayload.project?.id;
    if (importedId) {
      const imported = await (await fetch(`/api/project-library/${encodeURIComponent(importedId)}`)).json();
      check('imported project canvas is readable', imported.canvas?.nodes?.some(node => node.id === 'lifecycle-prompt'));
    }

    const trashCard = () => cardForId(seedId, '#trashGrid');
    const trashCount = () => document.querySelector('#trashCount').textContent;
    clickAction(seedId, '.project-trash');
    const undo = await waitFor('move to trash shows undo and updates the count', () =>
      !cardForId(seedId) && trashCount() === '1' && document.querySelector('.ff-toast-action'));
    undo.click();
    await waitFor('undo restores the project and count', () => cardForId(seedId) && trashCount() === '0');

    clickAction(seedId, '.project-trash');
    await waitFor('move to trash refreshes the grid and count', () => !cardForId(seedId) && trashCount() === '1');
    document.querySelector('#showTrash').click();
    await waitFor('trash card', trashCard);
    check('deleted project has the same card treatment', trashCard().querySelector('h2').textContent === seed.name && trashCard().querySelector('.project-thumb') && !trashCard().querySelector('.project-open'));
    check('trash is a separate view with safety copy and active count', document.querySelector('#projectLibrary').hidden &&
      !document.querySelector('.trash-note').hidden && document.querySelector('#showTrash').getAttribute('aria-expanded') === 'true' &&
      document.querySelector('#projectTrashLabel').textContent.includes('1') && document.querySelector('#trashSummary').textContent.includes('1'));
    trashCard().querySelector('.restore').click();
    await waitFor('restore refreshes the list and trash count', () => cardForId(seedId) && !trashCard() && trashCount() === '0');
    document.querySelector('#closeTrash').click();
    check('close returns to the grid and restores focus', !document.querySelector('#projectLibrary').hidden && document.activeElement.id === 'showTrash');

    clickAction(seedId, '.project-trash');
    await waitFor('second move to trash', () => !cardForId(seedId) && trashCount() === '1');
    document.querySelector('#showTrash').click();
    await waitFor('second trash card', trashCard);
    trashCard().querySelector('.purge').click();
    const confirmation = document.querySelector('.ff-dialog-card[role="alertdialog"]');
    check('purge asks before irreversible deletion', confirmation?.querySelector('.ff-dialog-message')?.textContent.includes(seed.name) && !!trashCard());
    confirmation.querySelector('.ff-dialog-btn:not(.ff-dialog-btn-danger)').click();
    check('cancel keeps the trash entry', !!trashCard());
    await sleep(0);
    trashCard().querySelector('.purge').click();
    document.querySelector('.ff-dialog-btn-danger').click();
    await waitFor('confirmed purge empties trash', () => !trashCard() && trashCount() === '0');
    check('purge permanently removes project from trash', true);
    check('imported project remains after lifecycle actions', (await projectList()).some(item => item.id === importedId));
  } catch (error) {
    errors.push({ name: 'project list browser flow', pass: false, detail: error?.stack || String(error) });
  }

  const payload = {
    kind: 'project-list-browser-result',
    result_id: resultId,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    assertions,
    errors,
    pass: errors.length === 0 && assertions.every(item => item.pass),
  };
  await fetch('/__conversation_browser_results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
})();
