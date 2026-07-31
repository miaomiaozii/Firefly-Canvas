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

  try {
    const cards = await waitFor('project cards', () => document.querySelectorAll('#projectGrid .project-card').length >= 1);
    check('project list loads seeded project', Boolean(cards));
    const seedId = (await projectList()).find(item => item.name === 'Browser lifecycle project')?.id;
    const seedCard = cardForId(seedId);
    check('seed project name is visible', Boolean(seedCard));
    check('project card exposes Fly export', Boolean(seedCard?.querySelector('a[download]')));
    check('project card exposes delete action', Boolean(seedCard?.querySelector('.danger')));
    check('empty state is hidden when projects exist', document.querySelector('#emptyState')?.hidden === true);

    const dialog = document.querySelector('#createDialog');
    document.querySelector('#showCreate').click();
    check('new project dialog opens', Boolean(dialog?.open));
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
