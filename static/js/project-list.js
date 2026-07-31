(() => {
  const status = document.getElementById('status');
  const emptyState = document.getElementById('emptyState');
  const grid = document.getElementById('projectGrid');
  const dialog = document.getElementById('createDialog');
  const form = document.getElementById('createForm');
  const nameInput = document.getElementById('projectName');
  const createError = document.getElementById('createError');
  const importFile = document.getElementById('importFile');
  const trashPanel = document.getElementById('trashPanel');
  const trashGrid = document.getElementById('trashGrid');
  const tr = key => window.StudioI18n?.t?.(key) || key;
  grid.setAttribute('aria-label', tr('projectList.region'));

  function openProject(project){
    const id = encodeURIComponent(project.id);
    const canvasId = encodeURIComponent(project.canvas_id);
    location.href = `/static/smart-canvas.html?id=${canvasId}&project=${id}&project_mode=1`;
  }
  function showCreate(){ dialog.showModal(); nameInput.focus(); }
  function render(projects){
    status.textContent = '';
    grid.replaceChildren();
    emptyState.hidden = projects.length !== 0;
    projects.forEach(project => {
      const card = document.createElement('article');
      card.className = 'project-card';
      const updated = project.updated_at ? new Date(project.updated_at).toLocaleString(window.StudioI18n?.lang?.() === 'en' ? 'en-US' : 'zh-CN') : tr('projectList.justCreated');
      card.innerHTML = `<button class="project-open" type="button"><span class="canvas-badge"></span><h2></h2><time></time></button><div class="project-actions"><a class="project-action" download></a><button class="project-action danger" type="button"></button></div>`;
      card.querySelector('.canvas-badge').textContent = tr('projectList.canvasBadge');
      card.querySelector('h2').textContent = project.name;
      card.querySelector('time').textContent = updated;
      card.querySelector('.project-open').addEventListener('click', () => openProject(project));
      const exportLink = card.querySelector('a');
      exportLink.href = `/api/project-library/${encodeURIComponent(project.id)}/export`;
      exportLink.textContent = tr('projectList.export');
      const deleteButton = card.querySelector('.danger');
      deleteButton.textContent = tr('projectList.delete');
      deleteButton.addEventListener('click', async () => {
        if(!confirm(tr('projectList.deleteConfirm').replace('{name}', project.name))) return;
        const response = await fetch(`/api/project-library/${encodeURIComponent(project.id)}`, {method:'DELETE'});
        const data = await response.json().catch(() => ({}));
        if(!response.ok){ status.textContent = data.detail || tr('projectList.deleteFailed'); return; }
        await Promise.all([load(), ...(trashPanel.hidden ? [] : [loadTrash()])]);
      });
      grid.appendChild(card);
    });
  }
  function renderTrash(projects){
    trashGrid.replaceChildren();
    if(!projects.length){
      const empty = document.createElement('p');
      empty.className = 'trash-empty';
      empty.textContent = tr('projectList.trashEmpty');
      trashGrid.appendChild(empty);
      return;
    }
    projects.forEach(project => {
      const card = document.createElement('article');
      card.className = 'project-card trash-card';
      const deleted = project.deleted_at ? new Date(project.deleted_at).toLocaleString(window.StudioI18n?.lang?.() === 'en' ? 'en-US' : 'zh-CN') : '';
      card.innerHTML = `<span class="canvas-badge"></span><h2></h2><time></time><div class="project-actions"><button class="project-action restore" type="button"></button><button class="project-action danger purge" type="button"></button></div>`;
      card.querySelector('.canvas-badge').textContent = tr('projectList.trashedBadge');
      card.querySelector('h2').textContent = project.name;
      card.querySelector('time').textContent = deleted;
      card.querySelector('.restore').textContent = tr('projectList.restore');
      card.querySelector('.purge').textContent = tr('projectList.purge');
      card.querySelector('.restore').addEventListener('click', async () => {
        const response = await fetch(`/api/project-library/trash/${encodeURIComponent(project.id)}/restore`, {method:'POST'});
        const data = await response.json().catch(() => ({}));
        if(!response.ok){ status.textContent = data.detail || tr('projectList.restoreFailed'); return; }
        await Promise.all([load(), loadTrash()]);
      });
      card.querySelector('.purge').addEventListener('click', async () => {
        if(!confirm(tr('projectList.purgeConfirm').replace('{name}', project.name))) return;
        const response = await fetch(`/api/project-library/trash/${encodeURIComponent(project.id)}`, {method:'DELETE'});
        const data = await response.json().catch(() => ({}));
        if(!response.ok){ status.textContent = data.detail || tr('projectList.purgeFailed'); return; }
        await loadTrash();
      });
      trashGrid.appendChild(card);
    });
  }
  async function loadTrash(){
    const response = await fetch('/api/project-library/trash');
    const data = await response.json().catch(() => ({}));
    if(!response.ok){ status.textContent = data.detail || tr('projectList.trashFailed'); return; }
    renderTrash(data.projects || []);
  }
  async function load(){
    try {
      const response = await fetch('/api/project-library');
      if(!response.ok) throw new Error(tr('projectList.readFailed'));
      render((await response.json()).projects || []);
    } catch(error) {
      status.textContent = error.message || tr('projectList.readFailed');
    }
  }
  document.getElementById('showCreate').addEventListener('click', showCreate);
  document.getElementById('emptyCreate').addEventListener('click', showCreate);
  document.getElementById('cancelCreate').addEventListener('click', () => dialog.close());
  document.getElementById('importProject').addEventListener('click', () => importFile.click());
  const showTrash = document.getElementById('showTrash');
  const closeTrash = document.getElementById('closeTrash');
  showTrash.addEventListener('click', async () => {
    trashPanel.hidden = false;
    showTrash.setAttribute('aria-expanded', 'true');
    await loadTrash();
    trashPanel.scrollIntoView({behavior:'smooth', block:'start'});
    closeTrash.focus();
  });
  closeTrash.addEventListener('click', () => {
    trashPanel.hidden = true;
    showTrash.setAttribute('aria-expanded', 'false');
    showTrash.focus();
  });
  importFile.addEventListener('change', async () => {
    const file = importFile.files?.[0];
    if(!file) return;
    const hint = document.getElementById('importHint');
    hint.hidden = false;
    hint.textContent = tr('projectList.importing');
    const body = new FormData();
    body.append('file', file, file.name);
    try {
      const response = await fetch('/api/project-library/import', {method:'POST', body});
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(data.detail || tr('projectList.importFailed'));
      openProject(data.project);
    } catch(error) {
      hint.textContent = error.message || tr('projectList.importFailed');
      importFile.value = '';
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    createError.textContent = '';
    const name = nameInput.value.trim();
    if(!name){ createError.textContent = tr('projectList.enterName'); return; }
    try {
      const response = await fetch('/api/project-library', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name}),
      });
      if(!response.ok) throw new Error(tr('projectList.createFailed'));
      openProject((await response.json()).project);
    } catch(error) {
      createError.textContent = error.message || tr('projectList.createFailed');
    }
  });
  load();
})();
