/* Photoshop 桥：放置一个画布源，导出一次往返结果（合成图 / 图层 / 选区）。 */
(function () {
  // 普通浏览器可渲染连接器外壳做冒烟测试/文档演示；
  // Photoshop 专属操作在 UXP 宿主之外保持不可用。
  if (typeof require !== 'function') {
    DX.ps = {
      hasDocument: () => false,
      placeSource: async () => { throw new Error('需在 Photoshop UXP 宿主内执行'); },
      exportCurrent: async () => { throw new Error('需在 Photoshop UXP 宿主内执行'); },
    };
    return;
  }
  const photoshop = require('photoshop');
  const app = photoshop.app;
  const core = photoshop.core;
  const action = photoshop.action;
  const imaging = photoshop.imaging;
  const uxp = require('uxp');
  const fs = uxp.storage.localFileSystem;
  const formats = uxp.storage.formats;
  const net = DX.net;

  function hasDocument() { return app.documents.length > 0; }

  function bytesFromBase64(value) {
    const binary = atob(String(value || ''));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  async function temporaryFile(prefix, extension, buffer) {
    const folder = await fs.getTemporaryFolder();
    const file = await folder.createFile(`${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}.${extension}`, { overwrite: true });
    await file.write(buffer, { format: formats.binary });
    return file;
  }

  async function deleteTemporary(file) { try { await file?.delete(); } catch (_) {} }

  function extensionForMime(mime) {
    const value = String(mime || '').toLowerCase().split(';', 1)[0];
    if (value === 'image/jpeg' || value === 'image/jpg') return 'jpg';
    if (value === 'image/webp') return 'webp';
    if (value === 'image/gif') return 'gif';
    return 'png';
  }

  async function placeSource(source) {
    const file = await temporaryFile('firefly_source', extensionForMime(source.mime_type), bytesFromBase64(source.bytes_base64));
    try {
      const token = await fs.createSessionToken(file);
      await core.executeAsModal(async () => {
        if (!app.documents.length) { await app.open(file); return; }
        await action.batchPlay([{ _obj: 'placeEvent', null: { _path: token, _kind: 'local' }, freeTransformCenterState: { _enum: 'quadCenterState', _value: 'QCSAverage' } }], {});
      }, { commandName: '发送到 Photoshop' });
      return app.activeDocument ? app.activeDocument.id : '';
    } finally { await deleteTemporary(file); }
  }

  function selectionBounds(value) {
    const val = item => Math.round(typeof item === 'object' && item !== null ? item._value : item);
    const selection = value && value.selection;
    if (!selection) return null;
    const left = val(selection.left), top = val(selection.top), right = val(selection.right), bottom = val(selection.bottom);
    if (!(right > left && bottom > top)) return null;
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }

  async function readSelection() {
    const [result] = await action.batchPlay([{ _obj: 'get', _target: [{ _property: 'selection' }, { _ref: 'document', _enum: 'ordinal', _value: 'targetEnum' }] }], { synchronousExecution: true });
    return selectionBounds(result);
  }

  function assertDocument(expectedId) {
    const doc = app.activeDocument;
    if (!doc) throw new Error('没有打开的文档');
    if (expectedId && String(doc.id) !== String(expectedId)) throw new Error('当前 Photoshop 文档已切换，请切换回本次来源所在的文档');
    return doc;
  }

  async function exportComposite(expectedId) {
    const doc = assertDocument(expectedId);
    const file = await temporaryFile('firefly_composite', 'png', new ArrayBuffer(0));
    try {
      await core.executeAsModal(async () => { await doc.saveAs.png(file, {}, true); }, { commandName: '导出合成图' });
      return {
        bytes_base64: net.toBase64(await file.read({ format: formats.binary })),
        name: String(doc.name || 'photoshop-edit').replace(/\.[^.]+$/, '') || 'photoshop-edit',
        mime_type: 'image/png',
      };
    } finally { await deleteTemporary(file); }
  }

  async function exportLayer(expectedId) {
    const src = assertDocument(expectedId);
    const layers = src.activeLayers || [];
    if (!layers.length) throw new Error('请先选择一个图层');
    const file = await temporaryFile('firefly_layer', 'png', new ArrayBuffer(0));
    const srcId = src.id;
    let name = layers[0].name || 'layer';
    let temporary = null;
    try {
      await core.executeAsModal(async () => {
        await action.batchPlay([{ _obj: 'make', _target: [{ _ref: 'document' }], name: 'firefly_layer_export', using: { _ref: [{ _ref: 'layer', _enum: 'ordinal', _value: 'targetEnum' }, { _ref: 'document', _enum: 'ordinal', _value: 'targetEnum' }] } }], { synchronousExecution: true });
        temporary = app.activeDocument;
        if (!temporary || temporary.id === srcId) throw new Error('未能复制当前图层');
        await temporary.saveAs.png(file, {}, true);
        await temporary.closeWithoutSaving();
        temporary = null;
      }, { commandName: '导出当前图层' });
      return { bytes_base64: net.toBase64(await file.read({ format: formats.binary })), name, mime_type: 'image/png' };
    } finally {
      if (temporary) { try { await temporary.closeWithoutSaving(); } catch (_) {} }
      await deleteTemporary(file);
    }
  }

  async function exportSelection(expectedId) {
    const doc = assertDocument(expectedId);
    const bounds = await readSelection();
    if (!bounds) throw new Error('当前没有有效选区');
    const pixels = await imaging.getPixels({
      documentID: doc.id,
      sourceBounds: { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom },
      applyAlpha: true,
      colorSpace: 'RGB',
      componentSize: 8,
    });
    try {
      const base64 = await imaging.encodeImageData({ imageData: pixels.imageData, base64: true });
      return { bytes_base64: base64, name: 'selection', mime_type: 'image/jpeg' };
    } finally { try { pixels.imageData.dispose(); } catch (_) {} }
  }

  async function exportCurrent(scope, expectedId) {
    if (scope === 'layer') return exportLayer(expectedId);
    if (scope === 'selection') return exportSelection(expectedId);
    return exportComposite(expectedId);
  }

  DX.ps = { hasDocument, placeSource, exportCurrent };
})();
