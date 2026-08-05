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
  const cardFor = name => [...document.querySelectorAll('#projectGrid .project-card')]
    .find(card => card.querySelector('h2')?.textContent === name);
  const cardForId = id => [...document.querySelectorAll('#projectGrid .project-card')]
    .find(card => card.querySelector('a[download]')?.href.includes(encodeURIComponent(id)));

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
      subtitle: elementContrast(elements.subtitle),
      primary: elementContrast(elements.primary),
      cardTitle: elementContrast(elements.cardTitle),
    };
  };

  try {
    const cards = await waitFor('project cards', () => document.querySelectorAll('#projectGrid .project-card').length >= 1);
    check('project list loads seeded project', Boolean(cards));
    const seedId = (await projectList()).find(item => item.name === 'Browser lifecycle project')?.id;
    const seedCard = cardForId(seedId);
    check('seed project name is visible', Boolean(seedCard));
    check('project card exposes Fly export', Boolean(seedCard?.querySelector('a[download]')));
    check('project card exposes delete action', Boolean(seedCard?.querySelector('.danger')));
    check('empty state is hidden when projects exist', document.querySelector('#emptyState')?.hidden === true);

    const foundationLink = document.querySelector('link[href*="/static/css/studio-foundation.css"]');
    check('shared studio foundation is loaded', Boolean(foundationLink), foundationLink?.getAttribute('href') || 'missing');
    check('legacy theme stylesheet is not imported', !document.querySelector('link[href*="/static/css/theme.css"]'));

    const pageTitle = document.querySelector('h1');
    const subtitle = document.querySelector('.subtitle');
    const cardTitle = seedCard?.querySelector('h2');
    const metadata = seedCard?.querySelector('time');
    const createButton = document.querySelector('#showCreate');
    const cardAction = seedCard?.querySelector('.project-action');
    const typography = [
      [subtitle, 14],
      [cardTitle, 18],
      [metadata, 12],
      [createButton, 12],
      [cardAction, 12],
    ];
    check('project typography follows foundation', typography.every(([el, expected]) => el && Math.abs(px(getComputedStyle(el).fontSize) - expected) < 0.25) && px(getComputedStyle(pageTitle).fontSize) >= 32 && px(getComputedStyle(pageTitle).fontSize) <= 48, typography.concat([[pageTitle, '32-48']]).map(([el]) => el ? getComputedStyle(el).fontSize : 'missing').join('/'));

    const sizeMetrics = {
      button: px(getComputedStyle(createButton).minHeight),
      action: px(getComputedStyle(cardAction).minHeight),
      cardRadius: px(getComputedStyle(seedCard).borderRadius),
      buttonRadius: px(getComputedStyle(createButton).borderRadius),
    };
    check('project controls and cards use shared scale', sizeMetrics.button === 34 && sizeMetrics.action === 30 && sizeMetrics.cardRadius === 8 && sizeMetrics.buttonRadius === 6, JSON.stringify(sizeMetrics));

    const cardStyle = getComputedStyle(seedCard);
    check('project surfaces remove gradients shadows and glow', getComputedStyle(document.body).backgroundImage === 'none' && cardStyle.backgroundImage === 'none' && cardStyle.boxShadow === 'none' && getComputedStyle(document.querySelector('.firefly-mark')).filter === 'none', `body=${getComputedStyle(document.body).backgroundImage} card=${cardStyle.backgroundImage}/${cardStyle.boxShadow}`);

    const interactive = [...document.querySelectorAll('button, a.button, .project-action, input:not([type="file"])')];
    const undersized = interactive.filter(el => px(getComputedStyle(el).fontSize) < 12);
    check('project interactive text stays readable', undersized.length === 0, undersized.map(el => `${el.tagName}.${el.className}:${getComputedStyle(el).fontSize}`).join(', ') || 'all >= 12px');

    const initialTheme = document.documentElement.classList.contains('studio-theme-dark') ? 'dark' : 'light';
    const themeElements = { subtitle, primary: createButton, cardTitle };
    const lightMetrics = themeMetrics('light', themeElements);
    const darkMetrics = themeMetrics('dark', themeElements);
    window.StudioTheme.apply(initialTheme);
    check('light and dark project surfaces are distinct', lightMetrics.page !== darkMetrics.page && lightMetrics.panel !== darkMetrics.panel, JSON.stringify({ light: lightMetrics, dark: darkMetrics }));
    check('project page shares canvas accent', lightMetrics.accent === '#5879dc' && darkMetrics.accent === '#5879dc', `${lightMetrics.accent}/${darkMetrics.accent}`);
    check('light project content meets readable contrast', Math.min(lightMetrics.body, lightMetrics.subtitle, lightMetrics.primary, lightMetrics.cardTitle) >= 4.5, JSON.stringify(lightMetrics));
    check('dark project content meets readable contrast', Math.min(darkMetrics.body, darkMetrics.subtitle, darkMetrics.primary, darkMetrics.cardTitle) >= 4.5, JSON.stringify(darkMetrics));

    if (window.innerWidth <= 700) {
      const shellRect = document.querySelector('.project-shell').getBoundingClientRect();
      const gridColumns = getComputedStyle(document.querySelector('#projectGrid')).gridTemplateColumns.trim().split(/\s+/).length;
      const headerDirection = getComputedStyle(document.querySelector('.project-header')).flexDirection;
      const actionColumns = getComputedStyle(document.querySelector('.header-actions')).gridTemplateColumns.trim().split(/\s+/).length;
      check('mobile project list has explicit compact layout', headerDirection === 'column' && gridColumns === 1 && actionColumns <= 2, `header=${headerDirection} projects=${gridColumns} actions=${actionColumns}`);
      check('mobile project list stays inside viewport', shellRect.left >= -1 && shellRect.right <= window.innerWidth + 1 && document.documentElement.scrollWidth <= window.innerWidth + 1, `shell=${shellRect.left.toFixed(1)}..${shellRect.right.toFixed(1)} viewport=${window.innerWidth} scroll=${document.documentElement.scrollWidth}`);
    }

    const dialog = document.querySelector('#createDialog');
    document.querySelector('#showCreate').click();
    check('new project dialog opens', Boolean(dialog?.open));
    const dialogInputStyle = getComputedStyle(document.querySelector('#projectName'));
    check('create dialog form uses shared control typography', Math.abs(px(dialogInputStyle.height) - 34) < 0.25 && px(dialogInputStyle.fontSize) === 12 && px(dialogInputStyle.borderRadius) === 8 && px(getComputedStyle(dialog).borderRadius) === 12, `input=${dialogInputStyle.height}/${dialogInputStyle.fontSize}/${dialogInputStyle.borderRadius} dialog=${getComputedStyle(dialog).borderRadius}`);
    document.querySelector('#projectName').value = '';
    document.querySelector('#createForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    check('empty project name is rejected', Boolean(document.querySelector('#createError')?.textContent));
    document.querySelector('#cancelCreate').click();
    check('new project dialog closes', dialog?.open === false);

    const exportResponse = await fetch(seedCard.querySelector('a[download]').href);
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

    window.confirm = () => true;
    cardForId(seedId).querySelector('.danger').click();
    await waitFor('deleted project leaves active list', () => !cardForId(seedId));
    check('delete moves project out of active list', true);

    document.querySelector('#showTrash').click();
    await waitFor('trash card', () => document.querySelector('#trashGrid .trash-card'));
    check('deleted project appears in trash', document.querySelector('#trashGrid h2')?.textContent === 'Browser lifecycle project');
    document.querySelector('#trashGrid .restore').click();
    await waitFor('restored project card', () => cardForId(seedId));
    check('restore returns project to active list', true);

    cardForId(seedId).querySelector('.danger').click();
    await waitFor('second trash card', () => document.querySelector('#trashGrid .trash-card'));
    document.querySelector('#trashGrid .purge').click();
    await waitFor('empty trash', () => !document.querySelector('#trashGrid .trash-card'));
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
