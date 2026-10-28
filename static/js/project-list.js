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
  const tr = key => window.StudioI18n?.t?.(key) || key;
  grid.setAttribute('aria-label', tr('projectList.region'));

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
  function render(projects){
    status.textContent = '';
    grid.replaceChildren();
    emptyState.hidden = projects.length !== 0;
    projects.forEach(project => {
      const card = document.createElement('article');
      card.className = 'project-card';
      const updated = project.updated_at ? new Date(project.updated_at).toLocaleString(window.StudioI18n?.lang?.() === 'en' ? 'en-US' : 'zh-CN') : tr('projectList.justCreated');
      card.innerHTML = `<button class="project-open" type="button"><span class="canvas-badge"></span><h2></h2><time></time></button><div class="project-actions"><button class="project-action project-versions" type="button"></button><button class="project-action project-cleanup-preview" type="button"></button><button class="project-action project-duplicate" type="button"></button><a class="project-action" download></a><button class="project-action danger" type="button"></button></div>`;
      card.querySelector('.canvas-badge').textContent = tr('projectList.canvasBadge');
      card.querySelector('h2').textContent = project.name;
      card.querySelector('time').textContent = updated;
      card.querySelector('.project-open').addEventListener('click', () => openProject(project));
      const versionsButton = card.querySelector('.project-versions');
      versionsButton.textContent = tr('projectList.versions');
      versionsButton.addEventListener('click', () => showVersions(project));
      const cleanupButton = card.querySelector('.project-cleanup-preview');
      if(project.cleanup_preview_available){
        cleanupButton.textContent = tr('projectList.cleanupPreview');
        cleanupButton.addEventListener('click', () => showCleanupPreview(project));
      } else {
        cleanupButton.remove();
      }
      const exportLink = card.querySelector('a');
      exportLink.href = `/api/project-library/${encodeURIComponent(project.id)}/export`;
      exportLink.textContent = tr('projectList.export');
      const duplicateButton = card.querySelector('.project-duplicate');
      duplicateButton.textContent = tr('projectList.duplicate');
      duplicateButton.addEventListener('click', async () => {
        duplicateButton.disabled = true;
        try {
          const response = await fetch(`/api/project-library/${encodeURIComponent(project.id)}/duplicate`, {method:'POST'});
          const data = await response.json().catch(() => ({}));
          if(!response.ok){
            status.textContent = data.detail || tr('projectList.duplicateFailed');
            return;
          }
          await load();
        } catch(error) {
          status.textContent = tr('projectList.duplicateFailed');
        } finally {
          duplicateButton.disabled = false;
        }
      });
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
  document.getElementById('closeCleanupPreview').addEventListener('click', () => cleanupDialog.close());
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
      await loadVersionsList();
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
  load();
})();
