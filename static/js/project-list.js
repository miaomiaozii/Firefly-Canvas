(() => {
  const status = document.getElementById('status');
  const emptyState = document.getElementById('emptyState');
  const searchEmpty = document.getElementById('searchEmpty');
  const search = document.getElementById('projectSearch');
  const summary = document.getElementById('projectSummary');
  const library = document.getElementById('projectLibrary');
  const grid = document.getElementById('projectGrid');
  const gridCreate = document.getElementById('gridCreate');
  const header = document.querySelector('.project-header');
  const dialog = document.getElementById('createDialog');
  const form = document.getElementById('createForm');
  const nameInput = document.getElementById('projectName');
  const createError = document.getElementById('createError');
  const importFile = document.getElementById('importFile');
  const trashPanel = document.getElementById('trashPanel');
  const trashGrid = document.getElementById('trashGrid');
  const trashCount = document.getElementById('trashCount');
  const trashEmpty = document.getElementById('trashEmpty');
  const showTrash = document.getElementById('showTrash');
  const closeTrash = document.getElementById('closeTrash');
  const cleanupDialog = document.getElementById('cleanupPreviewDialog');
  const cleanupProject = document.getElementById('cleanupPreviewProject');
  const cleanupCount = document.getElementById('cleanupCandidateCount');
  const cleanupTotalBytes = document.getElementById('cleanupTotalBytes');
  const cleanupList = document.getElementById('cleanupCandidateList');
  const cleanupError = document.getElementById('cleanupPreviewError');
  const cleanupConfirmArea = document.getElementById('cleanupConfirmArea');
  const cleanupConfirmNote = document.getElementById('cleanupConfirmNote');
  const cleanupConfirmError = document.getElementById('cleanupConfirmError');
  const cleanupConfirmButton = document.getElementById('confirmCleanup');
  const versionsDialog = document.getElementById('versionsDialog');
  const versionsProject = document.getElementById('versionsProject');
  const versionsList = document.getElementById('versionsList');
  const versionsError = document.getElementById('versionsError');
  const versionsDiff = document.getElementById('versionsDiff');
  const versionsDiffSummary = document.getElementById('versionsDiffSummary');
  const versionsDiffList = document.getElementById('versionsDiffList');
  const versionsDiffMore = document.getElementById('versionsDiffMore');
  const versionsConfirmArea = document.getElementById('versionsConfirmArea');
  const versionsRestoreButton = document.getElementById('versionsRestore');
  const versionsRestoreError = document.getElementById('versionsRestoreError');
  let versionsProjectId = null;
  let versionsDiffVersionId = null;
  let cleanupConfirmProjectId = null;
  let cleanupConfirmState = null;
  let projects = null;
  let projectRequest = 0;
  let trashRequest = 0;
  let openMenu = null;
  let cleanupInvoker = null;
  const tr = key => window.StudioI18n?.t?.(key) || key;
  document.getElementById('projectTools').setAttribute('aria-label', tr('projectList.tools'));

  function openProject(project){
    const id = encodeURIComponent(project.id);
    const canvasId = encodeURIComponent(project.canvas_id);
    location.href = `/static/smart-canvas.html?id=${canvasId}&project=${id}&project_mode=1`;
  }
  function showCreate(){ dialog.showModal(); nameInput.focus(); }
  async function showCleanupPreview(project){
    cleanupProject.textContent = project.name;
    cleanupCount.textContent = '…';
    cleanupTotalBytes.textContent = '';
    cleanupError.textContent = '';
    cleanupConfirmError.textContent = '';
    cleanupList.replaceChildren();
    cleanupConfirmProjectId = null;
    cleanupConfirmState = null;
    cleanupConfirmButton.hidden = true;
    cleanupConfirmArea.hidden = true;
    cleanupDialog.showModal();
    try {
      const response = await fetch(
        `/api/project-library/${encodeURIComponent(project.id)}/cleanup-preview`,
        {method:'GET'},
      );
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(data.detail || tr('projectList.cleanupFailed'));
      const preview = data.preview || {};
      const candidates = Array.isArray(preview.candidates) ? preview.candidates : [];
      cleanupCount.textContent = String(preview.candidate_count || 0);
      cleanupTotalBytes.textContent = ` · ${Number(preview.total_bytes || 0).toLocaleString()} B`;
      if(!candidates.length){
        const empty = document.createElement('p');
        empty.className = 'cleanup-empty';
        empty.textContent = tr('projectList.cleanupEmpty');
        cleanupList.appendChild(empty);
      } else {
        cleanupConfirmProjectId = project.id;
        cleanupConfirmState = {
          revision: Number(preview.revision || 0),
          ownership_proof: String(preview.ownership_proof || ''),
          candidate_asset_ids: candidates.map(item => String(item.asset_id || '')),
        };
        cleanupConfirmArea.hidden = false;
        cleanupConfirmButton.hidden = false;
        cleanupConfirmButton.disabled = false;
      }
      candidates.forEach(candidate => {
        const item = document.createElement('article');
        item.className = 'cleanup-candidate';
        const thumbnail = document.createElement(candidate.kind === 'video' ? 'video' : 'img');
        thumbnail.src = candidate.thumbnail_url;
        thumbnail.setAttribute('aria-label', candidate.name || candidate.asset_id || '');
        if(candidate.kind === 'video'){
          thumbnail.preload = 'metadata';
          thumbnail.muted = true;
        } else {
          thumbnail.alt = '';
          thumbnail.loading = 'lazy';
        }
        const details = document.createElement('div');
        const name = document.createElement('strong');
        name.textContent = candidate.name || candidate.asset_id;
        const source = document.createElement('span');
        source.textContent = tr(`projectList.cleanupSource.${candidate.source_category}`);
        const size = document.createElement('span');
        size.textContent = `${Number(candidate.size || 0).toLocaleString()} B`;
        details.append(name, source, size);
        item.append(thumbnail, details);
        cleanupList.appendChild(item);
      });
    } catch(error) {
      cleanupCount.textContent = '0';
      cleanupError.textContent = error.message || tr('projectList.cleanupFailed');
      cleanupConfirmProjectId = null;
      cleanupConfirmState = null;
      cleanupConfirmButton.hidden = true;
      cleanupConfirmArea.hidden = true;
    }
  }
  cleanupConfirmButton.addEventListener('click', async () => {
    const state = cleanupConfirmState;
    const projectId = cleanupConfirmProjectId;
    if(!state || !projectId){
      cleanupConfirmError.textContent = tr('projectList.cleanupRepreview');
      return;
    }
    cleanupConfirmButton.disabled = true;
    cleanupConfirmButton.textContent = tr('projectList.cleanupConfirming');
    cleanupConfirmError.textContent = '';
    try {
      const response = await fetch(
        `/api/project-library/${encodeURIComponent(projectId)}/cleanup-execute`,
        {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify(state),
        },
      );
      const data = await response.json().catch(() => ({}));
      if(!response.ok){
        const detail = String(data.detail || '');
        const expired = detail.includes('重新预览');
        cleanupConfirmError.textContent = expired
          ? tr('projectList.cleanupRepreview')
          : (detail || tr('projectList.cleanupConfirmFailed'));
        // 确认已失效：保持禁用，用户必须重新预览。
        cleanupConfirmButton.disabled = expired;
        return;
      }
      const result = data.result || {};
      if(result.complete){
        cleanupConfirmProjectId = null;
        cleanupConfirmState = null;
        cleanupConfirmButton.hidden = true;
        cleanupConfirmArea.hidden = true;
        cleanupCount.textContent = '0';
        cleanupTotalBytes.textContent = '';
        cleanupList.replaceChildren();
        const done = document.createElement('p');
        done.className = 'cleanup-empty';
        done.textContent = tr('projectList.cleanupDone')
          .replace('{count}', String(result.deleted_count || 0))
          .replace('{bytes}', Number(result.deleted_bytes || 0).toLocaleString());
        cleanupList.appendChild(done);
        await load();
        return;
      }
      cleanupConfirmError.textContent = tr('projectList.cleanupPartial')
        .replace('{count}', String(result.deleted_count || 0));
      cleanupConfirmButton.disabled = false;
    } catch(error) {
      cleanupConfirmError.textContent = error.message || tr('projectList.cleanupConfirmFailed');
      cleanupConfirmButton.disabled = false;
    } finally {
      if(!cleanupConfirmButton.hidden) cleanupConfirmButton.textContent = tr('projectList.cleanupConfirm');
    }
  });
  function addProjectCover(thumb, project, inTrash){
    const url = project.cover_thumbnail_url;
    const prefix = `/api/project-library/${inTrash ? 'trash/' : ''}${encodeURIComponent(project.id)}/cover/`;
    // 只加载卡片所属项目在当前列表中的封面路由。
    if(typeof url !== 'string' || !url.startsWith(prefix)) return;
    const assetId = url.slice(prefix.length);
    if(!/^[a-zA-Z0-9._-]+$/.test(assetId) || assetId === '.' || assetId === '..') return;

    const cover = document.createElement('img');
    cover.className = 'project-cover';
    cover.alt = '';
    cover.loading = 'lazy';
    cover.decoding = 'async';
    cover.addEventListener('load', () => cover.classList.add('is-loaded'), {once:true});
    cover.addEventListener('error', () => cover.remove(), {once:true});
    cover.src = url;
    thumb.appendChild(cover);
  }
  function cardBase(project, inTrash = false){
    const card = document.createElement('article');
    card.className = 'project-card';
    card.dataset.projectId = project.id;
    card.innerHTML = `
      <div class="project-thumb" aria-hidden="true">
        <svg class="project-thumb-mark" viewBox="0 0 96 64">
          <path class="project-thumb-frame" d="M29 12H16v11m51-11h13v11M16 41v11h13m51-11h13V41"/>
          <path d="M29 39c9 0 10-11 19-11s10 8 19 8"/>
          <circle cx="29" cy="39" r="4"/>
          <circle cx="48" cy="28" r="6"/>
          <circle cx="67" cy="36" r="4"/>
        </svg>
      </div>
      <div class="project-meta"><h2></h2><time hidden></time></div>`;
    addProjectCover(card.querySelector('.project-thumb'), project, inTrash);
    const title = card.querySelector('h2');
    title.textContent = project.name;
    title.title = project.name;
    const updated = new Date(project.updated_at);
    if(project.updated_at && !Number.isNaN(updated.getTime())){
      const time = card.querySelector('time');
      time.dateTime = updated.toISOString();
      time.textContent = formatVersionsTime(project.updated_at);
      time.hidden = false;
    }
    return card;
  }
  function closeProjectMenu(restoreFocus = false){
    if(!openMenu) return;
    const {trigger, menu} = openMenu;
    openMenu = null;
    trigger.setAttribute('aria-expanded', 'false');
    menu.hidden = true;
    if(restoreFocus) trigger.focus({preventScroll:true});
  }
  function menuItems(menu){
    return [...menu.querySelectorAll('.project-action:not(:disabled)')];
  }
  function showProjectMenu(trigger, menu, last = false){
    closeProjectMenu();
    openMenu = {trigger, menu};
    trigger.setAttribute('aria-expanded', 'true');
    menu.hidden = false;
    menu.classList.remove('opens-above');
    const rect = menu.getBoundingClientRect();
    const below = window.innerHeight - rect.top;
    const top = Math.max(0, header.getBoundingClientRect().bottom);
    const above = trigger.closest('.project-card').getBoundingClientRect().top - top;
    const flip = menu.scrollHeight > below && above > below;
    menu.classList.toggle('opens-above', flip);
    menu.style.setProperty('--menu-available', `${Math.max(trigger.offsetHeight, flip ? above : below)}px`);
    const items = menuItems(menu);
    (last ? items.at(-1) : items[0])?.focus();
  }
  function wireProjectMenu(trigger, menu){
    trigger.addEventListener('click', () => {
      if(openMenu?.trigger === trigger) closeProjectMenu(true);
      else showProjectMenu(trigger, menu);
    });
    trigger.addEventListener('keydown', event => {
      if(event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      event.preventDefault();
      showProjectMenu(trigger, menu, event.key === 'ArrowUp');
    });
    // 保留原生 Tab 顺序与下载链接语义；方向键提供菜单内的快捷导航。
    menu.addEventListener('keydown', event => {
      const items = menuItems(menu);
      if(!items.length) return;
      const index = items.indexOf(document.activeElement);
      let next;
      if(event.key === 'ArrowDown') next = items[(index + 1) % items.length];
      else if(event.key === 'ArrowUp') next = items[(index + items.length - 1) % items.length];
      else if(event.key === 'Home') next = items[0];
      else if(event.key === 'End') next = items.at(-1);
      else if(event.key === ' ' && event.target.matches('a[download]')){
        event.preventDefault();
        event.target.click();
        return;
      } else return;
      event.preventDefault();
      next.focus();
    });
    menu.addEventListener('click', event => {
      if(event.target.closest('.project-action')) closeProjectMenu(true);
    }, true);
  }
  function dismissOutsideMenu(event){
    if(openMenu && !openMenu.trigger.contains(event.target) && !openMenu.menu.contains(event.target)){
      closeProjectMenu();
    }
  }
  document.addEventListener('click', dismissOutsideMenu);
  document.addEventListener('focusin', dismissOutsideMenu);
  document.addEventListener('keydown', event => {
    if(openMenu && event.key === 'Escape'){
      event.preventDefault();
      closeProjectMenu(true);
    }
  });
  window.addEventListener('resize', () => closeProjectMenu(true));

  function projectCard(project){
    const card = cardBase(project);
    const id = encodeURIComponent(project.id);
    const title = card.querySelector('h2');
    title.id = `project-name-${id}`;
    const time = card.querySelector('time');
    time.id = `project-time-${id}`;
    card.insertAdjacentHTML('beforeend', `
      <button class="project-open" type="button"></button>
      <button class="tool-button tool-icon project-more" type="button" aria-expanded="false">⋯</button>
      <div class="project-menu" role="group" hidden>
        <button class="project-action project-versions" type="button"></button>
        <button class="project-action project-duplicate" type="button"></button>
        <a class="project-action project-export" download></a>
        <button class="project-action project-cleanup-preview" type="button"></button>
        <hr>
        <button class="project-action danger project-trash" type="button"></button>
      </div>`);
    const open = card.querySelector('.project-open');
    open.setAttribute('aria-labelledby', title.id);
    if(!time.hidden) open.setAttribute('aria-describedby', time.id);
    open.title = project.name;
    open.addEventListener('click', () => openProject(project));
    const menu = card.querySelector('.project-menu');
    const more = card.querySelector('.project-more');
    more.id = `project-more-${id}`;
    more.setAttribute('aria-label', tr('projectList.more').replace('{name}', () => project.name));
    menu.id = `project-menu-${id}`;
    menu.setAttribute('aria-labelledby', more.id);
    more.setAttribute('aria-controls', menu.id);
    wireProjectMenu(more, menu);
    const versionsButton = card.querySelector('.project-versions');
    versionsButton.textContent = tr('projectList.versions');
    versionsButton.addEventListener('click', () => showVersions(project));
    const cleanupButton = card.querySelector('.project-cleanup-preview');
    if(project.cleanup_preview_available){
      cleanupButton.textContent = tr('projectList.cleanupPreview');
      cleanupButton.addEventListener('click', () => {
        cleanupInvoker = project.id;
        showCleanupPreview(project);
      });
    } else {
      cleanupButton.remove();
    }
    const exportLink = card.querySelector('.project-export');
    exportLink.href = `/api/project-library/${encodeURIComponent(project.id)}/export`;
    exportLink.textContent = tr('projectList.export');
    const duplicateButton = card.querySelector('.project-duplicate');
    duplicateButton.textContent = tr('projectList.duplicate');
    duplicateButton.addEventListener('click', async () => {
      duplicateButton.disabled = true;
      try {
        await requestProjectMutation(`/api/project-library/${encodeURIComponent(project.id)}/duplicate`, 'POST', 'projectList.duplicateFailed');
        await load();
      } catch(error) {
        status.textContent = error.message;
      } finally {
        duplicateButton.disabled = false;
      }
    });
    const deleteButton = card.querySelector('.project-trash');
    deleteButton.textContent = tr('projectList.delete');
    deleteButton.addEventListener('click', async () => {
      deleteButton.disabled = true;
      try {
        // #347：软删除可撤销；只有永久删除需要二次确认。
        await requestProjectMutation(`/api/project-library/${id}`, 'DELETE', 'projectList.deleteFailed');
        await refreshLibrary();
        window.ffToast?.info(tr('projectList.deletedToast').replace('{name}', () => project.name), {
          timeoutMs: 8000,
          action: {
            label: tr('common.undo'),
            run: async () => {
              try {
                await restoreProject(project);
                window.ffToast?.success(tr('projectList.restoredToast').replace('{name}', () => project.name));
              } catch(error) {
                window.ffToast?.error(error.message);
              }
            }
          }
        });
      } catch(error) {
        status.textContent = error.message;
      } finally {
        deleteButton.disabled = false;
      }
    });
    return card;
  }
  function focusCard(container, id, selector, fallback){
    const card = container.querySelector(`[data-project-id="${CSS.escape(id)}"]`);
    (card?.querySelector(selector) || fallback).focus({preventScroll:true});
  }
  function render(){
    if(!projects) return;
    const query = search.value.trim().toLocaleLowerCase();
    const visible = projects.filter(project => project.name.toLocaleLowerCase().includes(query));
    const active = document.activeElement;
    const focusedCard = active.closest('#projectGrid .project-card');
    closeProjectMenu();
    grid.querySelectorAll('.project-card').forEach(card => card.remove());
    grid.append(...visible.map(projectCard));
    summary.textContent = tr('projectList.summary').replace('{count}', String(visible.length));
    emptyState.hidden = projects.length !== 0;
    searchEmpty.hidden = !query || visible.length !== 0 || !emptyState.hidden;
    if(focusedCard) focusCard(grid, focusedCard.dataset.projectId, active.matches('.project-open') ? '.project-open' : '.project-more', gridCreate);
  }
  async function requestProjectMutation(url, method, failureKey){
    const response = await fetch(url, {method}).catch(() => { throw new Error(tr(failureKey)); });
    const data = await response.json().catch(() => ({}));
    if(!response.ok) throw new Error(data.detail || tr(failureKey));
  }
  async function restoreProject(project){
    await requestProjectMutation(`/api/project-library/trash/${encodeURIComponent(project.id)}/restore`, 'POST', 'projectList.restoreFailed');
    await refreshLibrary();
  }
  function trashCard(project){
    const card = cardBase(project, true);
    card.classList.add('trash-card');
    card.insertAdjacentHTML('beforeend', `<div class="trash-actions"><button class="project-action restore" type="button"></button><button class="project-action danger purge" type="button"></button></div>`);
    const restore = card.querySelector('.restore');
    const purge = card.querySelector('.purge');
    restore.textContent = tr('projectList.restore');
    purge.textContent = tr('projectList.purge');
    let busy = false;
    const act = callback => async () => {
      if(busy) return;
      busy = true;
      status.textContent = '';
      try {
        await callback();
      } catch(error) {
        status.textContent = error.message;
      } finally {
        busy = false;
      }
    };
    restore.addEventListener('click', act(() => restoreProject(project)));
    purge.addEventListener('click', act(async () => {
      if(!(await window.ffDialog?.ask({
        title: tr('common.confirmTitle'),
        message: tr('projectList.purgeConfirm').replace('{name}', () => project.name),
        confirmText: tr('projectList.purge'),
        cancelText: tr('common.cancel'),
        danger: true,
      }))) return;
      await requestProjectMutation(`/api/project-library/trash/${encodeURIComponent(project.id)}`, 'DELETE', 'projectList.purgeFailed');
      await loadTrash();
    }));
    return card;
  }
  async function loadTrash(){
    const request = ++trashRequest;
    try {
      const response = await fetch('/api/project-library/trash');
      const data = await response.json().catch(() => ({}));
      if(request !== trashRequest) return;
      if(!response.ok) throw new Error(data.detail || tr('projectList.trashFailed'));
      const trashed = data.projects || [];
      const active = document.activeElement;
      const focusedCard = active.closest('#trashGrid .trash-card');
      trashCount.textContent = String(trashed.length);
      trashCount.hidden = false;
      trashGrid.replaceChildren(...trashed.map(trashCard));
      trashEmpty.hidden = trashed.length !== 0;
      if(focusedCard) focusCard(trashGrid, focusedCard.dataset.projectId, active.matches('.purge') ? '.purge' : '.restore', closeTrash);
    } catch(error) {
      if(request !== trashRequest) return;
      trashCount.hidden = true;
      if(!trashPanel.hidden) status.textContent = dataError(error, 'projectList.trashFailed');
    }
  }
  function dataError(error, fallback){
    return error instanceof TypeError ? tr(fallback) : error.message || tr(fallback);
  }
  async function load(){
    const request = ++projectRequest;
    try {
      const response = await fetch('/api/project-library');
      if(!response.ok) throw new Error(tr('projectList.readFailed'));
      const data = await response.json();
      if(request !== projectRequest) return;
      projects = data.projects || [];
      status.textContent = '';
      render();
    } catch(error) {
      if(request === projectRequest) status.textContent = dataError(error, 'projectList.readFailed');
    }
  }
  function refreshLibrary(){
    return Promise.all([load(), loadTrash()]);
  }
  document.getElementById('showCreate').addEventListener('click', showCreate);
  gridCreate.addEventListener('click', showCreate);
  document.getElementById('cancelCreate').addEventListener('click', () => dialog.close());
  document.getElementById('closeCleanupPreview').addEventListener('click', () => cleanupDialog.close());
  cleanupDialog.addEventListener('close', () => {
    if(cleanupInvoker && !library.hidden && document.activeElement === document.body){
      focusCard(grid, cleanupInvoker, '.project-more', gridCreate);
    }
    cleanupInvoker = null;
  });
  document.getElementById('importProject').addEventListener('click', () => importFile.click());
  function closeTrashPanel(){
    trashPanel.hidden = true;
    library.hidden = false;
    showTrash.setAttribute('aria-expanded', 'false');
  }
  showTrash.addEventListener('click', () => {
    if(!trashPanel.hidden){ closeTrashPanel(); return; }
    closeProjectMenu();
    trashPanel.hidden = false;
    library.hidden = true;
    showTrash.setAttribute('aria-expanded', 'true');
    window.scrollTo(0, 0);
    closeTrash.focus({preventScroll:true});
    loadTrash();
  });
  closeTrash.addEventListener('click', () => {
    closeTrashPanel();
    showTrash.focus();
  });
  search.addEventListener('input', () => {
    if(!trashPanel.hidden) closeTrashPanel();
    render();
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
  // ── 版本历史 / diff 确认 / 回滚（issue #257；先存当前再回滚，ADR 0018 语义）────────
  function versionsApiUrl(projectId, suffix){
    return `/api/project-library/${encodeURIComponent(projectId)}/versions${suffix || ''}`;
  }
  function versionsLabel(label){
    if(label === 'auto') return tr('projectList.versionsLabelAuto');
    if(label === 'pre-restore') return tr('projectList.versionsLabelPreRestore');
    return String(label || '');
  }
  function formatVersionsTime(value){
    if(!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(window.StudioI18n?.lang?.() === 'en' ? 'en-US' : 'zh-CN');
  }
  function resetVersionsDialog(project){
    versionsProjectId = project.id;
    versionsProject.textContent = project.name;
    versionsError.textContent = '';
    versionsList.replaceChildren();
    versionsDiff.hidden = true;
    versionsDiffSummary.textContent = '';
    versionsDiffList.replaceChildren();
    versionsDiffMore.hidden = true;
    versionsConfirmArea.hidden = true;
    versionsRestoreError.textContent = '';
    versionsRestoreButton.hidden = true;
    versionsRestoreButton.disabled = false;
    versionsRestoreButton.textContent = tr('projectList.versionsRestoreConfirm');
    versionsDiffVersionId = null;
  }
  function showVersions(project){
    resetVersionsDialog(project);
    versionsDialog.showModal();
    loadVersionsList();
  }
  async function loadVersionsList(){
    if(!versionsProjectId) return;
    versionsError.textContent = '';
    try {
      const response = await fetch(versionsApiUrl(versionsProjectId), {method:'GET'});
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(String(data.detail || tr('projectList.versionsFailed')));
      renderVersionsList(Array.isArray(data.versions) ? data.versions : []);
    } catch(error){
      versionsList.replaceChildren();
      versionsError.textContent = error.message || tr('projectList.versionsFailed');
    }
  }
  function renderVersionsList(versions){
    versionsList.replaceChildren();
    if(!versions.length){
      const empty = document.createElement('p');
      empty.className = 'versions-empty';
      empty.textContent = tr('projectList.versionsEmpty');
      versionsList.appendChild(empty);
      return;
    }
    versions.forEach(version => {
      const item = document.createElement('article');
      item.className = 'versions-item';
      const info = document.createElement('div');
      info.className = 'versions-item-info';
      const title = document.createElement('strong');
      title.textContent = `#${version.id} · ${versionsLabel(version.label)}`;
      const meta = document.createElement('span');
      meta.className = 'versions-item-meta';
      meta.textContent = tr('projectList.versionsNodes').replace('{count}', String(version.node_count || 0)) + ' · ' + formatVersionsTime(version.created_at);
      info.append(title, meta);
      const actions = document.createElement('div');
      actions.className = 'versions-item-actions';
      const diffButton = document.createElement('button');
      diffButton.type = 'button';
      diffButton.className = 'button secondary';
      diffButton.textContent = tr('projectList.versionsCompare');
      diffButton.addEventListener('click', () => showVersionDiff(version.id, {withConfirm: false}));
      const restoreButton = document.createElement('button');
      restoreButton.type = 'button';
      restoreButton.className = 'button primary';
      restoreButton.textContent = tr('projectList.versionsRestore');
      restoreButton.addEventListener('click', () => showVersionDiff(version.id, {withConfirm: true}));
      actions.append(diffButton, restoreButton);
      item.append(info, actions);
      versionsList.appendChild(item);
    });
  }
  async function showVersionDiff(versionId, {withConfirm = false} = {}){
    if(!versionsProjectId) return;
    versionsError.textContent = '';
    versionsDiff.hidden = false;
    versionsDiffSummary.textContent = '…';
    versionsDiffList.replaceChildren();
    versionsDiffMore.hidden = true;
    if(!withConfirm){
      versionsConfirmArea.hidden = true;
      versionsRestoreButton.hidden = true;
    }
    versionsDiffVersionId = null;
    try {
      const response = await fetch(versionsApiUrl(versionsProjectId, `/${encodeURIComponent(versionId)}/diff?base=current`), {method:'GET'});
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(String(data.detail || tr('projectList.versionsDiffFailed')));
      renderVersionDiff(versionId, data, withConfirm);
    } catch(error){
      versionsDiffSummary.textContent = error.message || tr('projectList.versionsDiffFailed');
      if(withConfirm){
        versionsConfirmArea.hidden = true;
        versionsRestoreButton.hidden = true;
      }
    }
  }
  function renderVersionDiff(versionId, data, withConfirm){
    versionsDiffVersionId = versionId;
    const added = Array.isArray(data.added) ? data.added : [];
    const removed = Array.isArray(data.removed) ? data.removed : [];
    const changed = Array.isArray(data.changed) ? data.changed : [];
    versionsDiffSummary.textContent = tr('projectList.versionsDiffSummary')
      .replace('{from}', String((data.stats && data.stats.nodes_from) || 0))
      .replace('{to}', String((data.stats && data.stats.nodes_to) || 0))
      .replace('{added}', String(added.length))
      .replace('{removed}', String(removed.length))
      .replace('{changed}', String(changed.length));
    const list = versionsDiffList;
    list.replaceChildren();
    const MAX = 20;
    let shown = 0;
    let hiddenCount = 0;
    const appendEntry = (kind, entry) => {
      if(shown >= MAX){ hiddenCount += 1; return; }
      const line = document.createElement('p');
      line.className = `versions-diff-item versions-diff-${kind}`;
      const mark = kind === 'added' ? '+' : (kind === 'removed' ? '−' : '~');
      line.textContent = `${mark} ${entry.label || entry.id} (${entry.id})`;
      list.appendChild(line);
      shown += 1;
    };
    removed.forEach(entry => appendEntry('removed', entry));
    added.forEach(entry => appendEntry('added', entry));
    changed.forEach(entry => appendEntry('changed', entry));
    if(added.length + removed.length + changed.length === 0){
      const same = document.createElement('p');
      same.className = 'versions-diff-item';
      same.textContent = tr('projectList.versionsNoDiff');
      list.appendChild(same);
    }
    versionsDiffMore.hidden = hiddenCount === 0;
    versionsDiffMore.textContent = tr('projectList.versionsMore').replace('{count}', String(hiddenCount));
    if(withConfirm){
      versionsConfirmArea.hidden = false;
      versionsRestoreButton.hidden = false;
      versionsRestoreButton.disabled = false;
    }
  }
  versionsRestoreButton.addEventListener('click', async () => {
    const projectId = versionsProjectId;
    const versionId = versionsDiffVersionId;
    if(!projectId || !versionId) return;
    versionsRestoreButton.disabled = true;
    versionsRestoreButton.textContent = tr('projectList.versionsRestoring');
    versionsRestoreError.textContent = '';
    try {
      const response = await fetch(versionsApiUrl(projectId, `/${encodeURIComponent(versionId)}/restore`), {method:'POST'});
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(String(data.detail || tr('projectList.versionsRestoreFailed')));
      const preId = data.pre_restore_version_id;
      versionsConfirmArea.hidden = true;
      versionsRestoreButton.hidden = true;
      const done = document.createElement('p');
      done.className = 'versions-diff-summary';
      done.textContent = typeof preId === 'number'
        ? tr('projectList.versionsRestoreDone').replace('{id}', String(preId))
        : tr('projectList.versionsRestoreDoneNoPre');
      versionsDiffList.replaceChildren(done);
      await Promise.all([loadVersionsList(), load()]);
    } catch(error){
      versionsRestoreError.textContent = error.message || tr('projectList.versionsRestoreFailed');
    } finally {
      if(!versionsRestoreButton.hidden){
        versionsRestoreButton.disabled = false;
        versionsRestoreButton.textContent = tr('projectList.versionsRestoreConfirm');
      }
    }
  });
  document.getElementById('closeVersions').addEventListener('click', () => versionsDialog.close());
  versionsDialog.addEventListener('close', () => {
    if(versionsProjectId && !library.hidden && document.activeElement === document.body){
      focusCard(grid, versionsProjectId, '.project-more', gridCreate);
    }
  });
  refreshLibrary();
})();
