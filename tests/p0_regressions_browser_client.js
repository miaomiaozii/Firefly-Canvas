(async () => {
  'use strict';

  const query = new URLSearchParams(location.search);
  const projectId = query.get('id') || query.get('project') || '';
  const mode = query.get('p0Mode') || '';
  const result = {
    kind: 'p0-regression-browser-result',
    result_id: `p0-${mode}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    mode,
    viewport: { width: innerWidth, height: innerHeight },
    assertions: [],
    errors: [],
    pass: false,
  };
  let postResult = true;

  const check = (name, condition, actual = '') => {
    const item = { name, pass: Boolean(condition) };
    if (actual !== '') item.actual = actual;
    result.assertions.push(item);
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(name, predicate, timeout = 15000, interval = 100) {
    const deadline = Date.now() + timeout;
    let last = '';
    while (Date.now() < deadline) {
      try {
        const value = await predicate();
        if (value) return value;
        last = 'predicate false';
      } catch (error) {
        last = error?.message || String(error);
      }
      await sleep(interval);
    }
    throw new Error(`等待 ${name} 超时: ${last}`);
  }
  async function canvasSnapshot() {
    const response = await fetch(`/api/canvases/${encodeURIComponent(projectId)}`);
    if (!response.ok) throw new Error(`canvas GET ${response.status}`);
    const payload = await response.json();
    return payload.canvas || payload;
  }
  async function mediaStatus(assetId) {
    return fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}`)
      .then(response => response.status)
      .catch(() => 0);
  }
  async function trashHasAsset(assetId) {
    const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/trash/media`);
    if (!response.ok) return false;
    const payload = await response.json();
    return Array.isArray(payload.items) && payload.items.some(item => String(item?.asset_id || '') === assetId);
  }
  function nodeFrom(canvas, id) {
    return (canvas?.nodes || []).find(node => node.id === id) || null;
  }
  function dispatchTextInput(element, value) {
    element.value = value;
    element.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertText',
      data: value,
    }));
  }
  function requestPath(input) {
    if (typeof input === 'string') return input;
    return input?.url || input?.toString?.() || '';
  }

  async function restorePreservesEdit() {
    window.confirm = () => true;
    await waitFor('restore fixture', () => document.querySelector('.image-node[data-id="p0-editable-prompt"]'));
    document.getElementById('smartHistoryToggle')?.click();
    await waitFor('history restore card', () => document.querySelector('[data-canvas-history-id="p0-restore-history"] [data-history-delete-original]'));
    const deleteButton = document.querySelector('[data-canvas-history-id="p0-restore-history"] [data-history-delete-original]');
    check('restore fixture exposes an original-delete action', Boolean(deleteButton));
    deleteButton?.click();
    const beforeDelete = await canvasSnapshot();
    const restoreAssetId = beforeDelete.history?.find(log => log.id === 'p0-restore-history')?.outputs?.[0]?.asset_id || '';
    check('restore fixture exposes the original asset id', Boolean(restoreAssetId), restoreAssetId || 'missing');
    await waitFor('original moved to project trash', async () => Boolean(restoreAssetId) && await trashHasAsset(restoreAssetId), 15000);

    const prompt = document.querySelector('.image-node[data-id="p0-editable-prompt"] .prompt-node-text');
    const editedText = 'edited while original is deleted';
    check('editable prompt exists during original deletion', Boolean(prompt));
    if (!prompt) return;
    check('LLM-enabled prompt output remains directly editable', prompt.readOnly === false, {readOnly: prompt.readOnly});
    dispatchTextInput(prompt, editedText);
    await sleep(250);
    const beforeRestore = await canvasSnapshot();
    check('edit is still visible before restore', prompt.value === editedText, prompt.value);
    check('edit is unsaved before restore', nodeFrom(beforeRestore, 'p0-editable-prompt')?.text !== editedText, nodeFrom(beforeRestore, 'p0-editable-prompt')?.text || 'missing');

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
    await waitFor('original restore request', async () => {
      const canvas = await canvasSnapshot();
      return !(await trashHasAsset(restoreAssetId)) && canvas.history?.some(log => log.id === 'p0-restore-history' && log.outputs?.length);
    }, 15000);
    try {
      await waitFor('restore save settles', () => document.getElementById('projectSaveState')?.dataset.state === 'saved', 20000);
    } catch (error) {
      const serverAfterRestore = await canvasSnapshot().catch(() => ({}));
      const serverPrompt = nodeFrom(serverAfterRestore, 'p0-editable-prompt')?.text || '';
      const saveButton = document.getElementById('smartSaveButton');
      throw new Error(`${error.message}; state=${document.getElementById('projectSaveState')?.dataset.state || 'missing'}; stateText=${document.getElementById('projectSaveState')?.textContent || ''}; busy=${saveButton?.dataset.busy || '0'}; serverPrompt=${serverPrompt}; livePrompt=${prompt.value}`);
    }
    const afterRestore = await canvasSnapshot();
    const restoredPrompt = nodeFrom(afterRestore, 'p0-editable-prompt');
    check('restoring an original preserves the unsaved edit in the editor', prompt.value === editedText, prompt.value);
    check('restoring an original persists the unsaved edit', restoredPrompt?.text === editedText, restoredPrompt?.text || 'missing');
    check('restore returns the project to a saved state without losing the edit', document.getElementById('projectSaveState')?.dataset.state === 'saved', document.getElementById('projectSaveState')?.dataset.state || 'missing');
  }

  async function urlSaveConverges() {
    let mutationRequests = 0;
    let flushRequests = 0;
    let importRequests = 0;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const path = requestPath(input);
      const method = String(init.method || input?.method || 'GET').toUpperCase();
      if (path.includes('/canvas') && method === 'PATCH') mutationRequests += 1;
      if (path.includes('/flush') && method === 'POST') flushRequests += 1;
      if (path.includes('/media/import') && method === 'POST') importRequests += 1;
      return originalFetch(input, init);
    };
    await waitFor('URL-only media fixture', () => document.querySelector('.image-node[data-id="p0-url-image"]'));
    const saveButton = document.getElementById('smartSaveButton');
    check('URL-only fixture exposes the manual save control', Boolean(saveButton));
    saveButton?.click();
    await waitFor('URL-only media archive', async () => {
      const canvas = await canvasSnapshot();
      return Boolean(nodeFrom(canvas, 'p0-url-image')?.images?.[0]?.asset_id);
    }, 20000);
    const firstCanvas = await canvasSnapshot();
    const firstAsset = nodeFrom(firstCanvas, 'p0-url-image')?.images?.[0]?.asset_id || '';
    await waitFor('URL-only save settles', () => document.getElementById('projectSaveState')?.dataset.state === 'saved', 20000);
    const firstRequestCount = mutationRequests + flushRequests;
    await sleep(1500);
    const passiveRequestCount = mutationRequests + flushRequests;
    check('URL-only media is archived as a project asset', Boolean(firstAsset), firstAsset || 'missing');
    check('URL-only save stops issuing background save requests', passiveRequestCount === firstRequestCount, { firstRequestCount, passiveRequestCount, importRequests });

    check('URL-only media save converges to the saved state', document.getElementById('projectSaveState')?.dataset.state === 'saved', document.getElementById('projectSaveState')?.dataset.state || 'missing');
    check('URL-only save imports the source only once', importRequests === 1, importRequests);
    const stableRequestCount = mutationRequests + flushRequests;
    saveButton?.click();
    await sleep(1000);
    check('repeating a converged URL-only save issues no extra requests', mutationRequests + flushRequests === stableRequestCount, { stableRequestCount, mutationRequests, flushRequests });
  }

  async function mediaRendersAfterSave() {
    const nodeSelector = '.image-node[data-id="p0-render-image"]';
    const waitForRenderedImage = () => waitFor('rendered project media', () => {
      const image = document.querySelector(`${nodeSelector} .node-img`);
      return image?.complete && image.naturalWidth > 0 ? image : null;
    }, 15000);
    await waitFor('media render fixture', () => document.querySelector(nodeSelector));
    const initialImage = await waitForRenderedImage();
    check('asset-backed media renders before saving', Boolean(initialImage), { complete: initialImage?.complete, naturalWidth: initialImage?.naturalWidth });
    const saveButton = document.getElementById('smartSaveButton');
    saveButton?.click();
    await waitFor('media save settles', () => document.getElementById('projectSaveState')?.dataset.state === 'saved', 15000);
    document.querySelector('.image-node[data-id="p0-render-anchor"]')?.click();
    document.querySelector(nodeSelector)?.click();
    let afterSave = null;
    let renderError = '';
    try {
      afterSave = await waitForRenderedImage();
    } catch (error) {
      renderError = error?.message || String(error);
    }
    check('asset-backed media remains renderable after an immediate save', Boolean(afterSave), renderError || { complete: afterSave?.complete, naturalWidth: afterSave?.naturalWidth });
  }

  async function generationFlushReference() {
    let taskRequests = 0;
    let canvasPatches = 0;
    let flushRequests = 0;
    let failedFlushRequests = 0;
    let failedCanvasPatches = 0;
    const canvasPatchStatuses = [];
    const canvasPatchErrors = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const path = requestPath(input);
      const method = String(init.method || input?.method || 'GET').toUpperCase();
      if (path.includes('/image-generation-tasks') && method === 'POST') taskRequests += 1;
      if (path.includes('/canvas') && method === 'PATCH') canvasPatches += 1;
      if (path.includes('/flush') && method === 'POST') flushRequests += 1;
      const response = await originalFetch(input, init);
        if (path.includes('/canvas') && method === 'PATCH') {
          canvasPatchStatuses.push(response.status);
          if (response.status >= 400) {
            failedCanvasPatches += 1;
            canvasPatchErrors.push(await response.clone().text().catch(() => ''));
          }
      }
      if (path.includes('/flush') && method === 'POST' && response.status >= 400) failedFlushRequests += 1;
      return response;
    };
    await waitFor('generation reference fixture', () => document.querySelector('.image-node[data-id="p0-reference-generator"]'));
    document.querySelector('.image-node[data-id="p0-reference-generator"]')?.click();
    await waitFor('generation composer', () => document.querySelector('#composer.open'));
    const referenceThumb = document.querySelector('#inputThumbsRow .input-thumb');
    check('generation composer shows the connected project reference', Boolean(referenceThumb), referenceThumb?.outerHTML || 'missing');
    const promptInput = document.querySelector('#promptInput');
    const runButton = document.querySelector('#runBtn');
    check('generation composer exposes prompt and run controls', Boolean(promptInput && runButton));
    if (!promptInput || !runButton) return;
    promptInput.textContent = 'generate with the connected project reference';
    promptInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: 'generate with the connected project reference' }));
    await waitFor('generation run button enabled', () => !runButton.disabled, 12000);
    runButton.click();
    await waitFor('generation request attempt', async () => {
      const state = document.getElementById('projectSaveState')?.dataset.state;
      return canvasPatches > 0 || taskRequests > 0 || state === 'error';
    }, 20000, 250).catch(() => null);
    await waitFor('generation result or error', async () => {
      const state = document.getElementById('projectSaveState')?.dataset.state;
      if (failedCanvasPatches > 0 || failedFlushRequests > 0 || state === 'error') return true;
      const snapshot = await canvasSnapshot();
      const output = nodeFrom(snapshot, 'p0-reference-generator');
      return taskRequests > 0 && ((snapshot.history || []).some(log => log.result_node_id === 'p0-reference-generator' && ['succeeded', 'failed'].includes(log.status)) || Boolean(output?.images?.length));
    }, 20000, 250).catch(() => null);
    const canvas = await canvasSnapshot();
    const node = nodeFrom(canvas, 'p0-reference-generator');
    const success = (canvas.history || []).some(log => log.result_node_id === 'p0-reference-generator' && log.status === 'succeeded');
    check('generation pre-flight canvas save succeeds', canvasPatches > 0 && failedCanvasPatches === 0, { state: document.getElementById('projectSaveState')?.dataset.state, canvasPatches, failedCanvasPatches, canvasPatchStatuses, canvasPatchErrors });
    check('generation pre-flight flush completes before task submission', flushRequests > 0 && failedFlushRequests === 0, { state: document.getElementById('projectSaveState')?.dataset.state, canvasPatches, flushRequests, failedFlushRequests });
    check('reference-backed generation task is submitted', taskRequests > 0, { taskRequests, canvasPatches, flushRequests });
    check('reference-backed generation produces a persisted result', Boolean(success && node?.images?.length), { success, node: node || null });
    check('reference-backed generation leaves the project saved', document.getElementById('projectSaveState')?.dataset.state === 'saved', document.getElementById('projectSaveState')?.dataset.state || 'missing');
  }

  try {
    if (mode === 'restore-preserves-edit') await restorePreservesEdit();
    else if (mode === 'url-save-converges') await urlSaveConverges();
    else if (mode === 'media-renders-after-save') await mediaRendersAfterSave();
    else if (mode === 'generation-flush-reference') await generationFlushReference();
    else throw new Error(`unknown P0 mode: ${mode}`);
  } catch (error) {
    result.errors.push(error?.stack || String(error));
  } finally {
    result.pass = postResult && result.errors.length === 0 && result.assertions.length > 0 && result.assertions.every(item => item.pass);
    if (postResult) {
      await fetch('/__conversation_browser_results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(result),
      }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
  }
})();
