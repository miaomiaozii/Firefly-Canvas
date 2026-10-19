(async () => {
  const resultId = `marked-export-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const params = new URLSearchParams(location.search);
  const markedAssetId = params.get('markedAsset') || '';
  const assertions = [];
  const errors = [];
  const extras = {};

  const check = (name, condition, detail = '') => {
    const item = { name, pass: Boolean(condition) };
    if (detail) item.detail = String(detail);
    assertions.push(item);
    if (!item.pass) errors.push(item);
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(name, predicate, timeout = 30000, interval = 200) {
    const deadline = Date.now() + timeout;
    let last;
    while (Date.now() < deadline) {
      try {
        last = await predicate();
        if (last) return last;
      } catch (error) {
        last = error?.message || String(error);
      }
      await sleep(interval);
    }
    throw new Error(`等待 ${name} 超时: ${last || '无结果'}`);
  }
  const bytesToB64 = bytes => {
    let bin = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(bin);
  };

  // 捕获 /api/excel/export-zip 的请求体与响应（zip 字节 / 目录 JSON），供断言与 Python 侧终验。
  const captured = [];
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    let response;
    try {
      response = await originalFetch(input, init);
    } catch (networkError) {
      if (url.indexOf('/api/excel/export-zip') >= 0) {
        captured.push({
          url,
          request: init && init.body ? JSON.parse(init.body) : null,
          networkError: String(networkError?.message || networkError),
        });
      }
      throw networkError;
    }
    if (url.indexOf('/api/excel/export-zip') >= 0) {
      try {
        const record = {
          url,
          request: init && init.body ? JSON.parse(init.body) : null,
          status: response.status,
          statusText: response.statusText,
          responseUrl: response.url,
          type: response.type,
          redirected: response.redirected,
          headers: Object.fromEntries(response.headers.entries()),
          perf: (performance.getEntriesByName(new URL(url, location.href).href, 'resource') || []).map(e => ({
            name: e.name, status: e.responseStatus, dur: Math.round(e.duration),
            encoded: e.encodedBodySize, decoded: e.decodedBodySize,
          })),
        };
        const contentType = response.headers.get('content-type') || '';
        const clone = response.clone();
        if (contentType.indexOf('application/json') >= 0) {
          record.json = await clone.json();
        } else {
          record.bytesB64 = bytesToB64(new Uint8Array(await clone.arrayBuffer()));
        }
        captured.push(record);
      } catch (error) {
        captured.push({ url, error: String(error?.message || error) });
      }
    }
    return response;
  };
  // 捕获任意新记录（zip 字节 / JSON 响应 / 网络错误），失败时能看到真实原因。
  const waitCapture = (before) => waitFor('导出响应捕获', async () => {
      for (let i = captured.length - 1; i >= before; i--) {
        return captured[i];
      }
      await sleep(150);
      return null;
    }, 30000);

  let zipRecord = null;
  let dirRecord = null;

  try {
    await waitFor('画布加载', () => (typeof canvasLoadReady !== 'undefined' && canvasLoadReady) || null);
    check('收到星标图资源 ID', Boolean(markedAssetId), `markedAsset=${markedAssetId}`);

    // 1) 节点里 4 张图，UI 点击只给目标 1 张打星标
    const starButton = () => document.querySelector(
      `.image-node[data-id="gen-4"] [data-asset-id="${markedAssetId}"] .image-mark`
    );
    check('找到星标按钮（4 图节点中的目标图）', Boolean(starButton()));
    if (!starButton()) throw new Error(`缺少星标按钮 asset=${markedAssetId}`);
    starButton().click();
    await waitFor('星标状态写入', () =>
      (typeof getMarkedAssetIds !== 'undefined' &&
        getMarkedAssetIds().length === 1 &&
        getMarkedAssetIds().includes(markedAssetId)) ? true : null);
    check('星标集合恰含 1 张（点击的那张）', true);
    const badge = document.getElementById('smartMarkedCount');
    check('已标记徽标计数 = 1', Boolean(badge) && !badge.hidden && badge.textContent === '1',
      badge ? `text=${badge.textContent} hidden=${badge.hidden}` : '徽标缺失');

    // 2) 打开「已标记」面板
    document.getElementById('smartMarkedToggle').click();
    await waitFor('已标记面板打开', () => {
      const panel = document.getElementById('smartMarkedPanel');
      return panel && !panel.hidden ? panel : null;
    });
    check('已标记面板已打开', true);

    const exportBtn = document.getElementById('smartMarkedExportBtn');
    const dirInput = document.getElementById('smartMarkedExportDir');

    // 3) 路径 1：浏览器 zip 下载（目录留空）
    await sleep(400); // 让 PerformanceObserver 条目落定
    const beforeZip = captured.length;
    exportBtn.click();
    zipRecord = await waitCapture(beforeZip);
    const zipIdmBlocked = Boolean(
      zipRecord && zipRecord.status === 204 && /IDM/i.test(zipRecord.statusText || '')
    );
    if (!zipIdmBlocked) {
      check('zip 导出返回 200', zipRecord && zipRecord.status === 200,
        zipRecord ? `status=${zipRecord.status} json=${JSON.stringify(zipRecord.json)} netErr=${zipRecord.networkError || ''}` : '未捕获到响应');
      check('zip 导出为 zip 字节响应', Boolean(zipRecord && zipRecord.bytesB64),
        JSON.stringify(zipRecord));
    } else {
      // 本机装有 IDM（Internet Download Manager），其「Advanced Integration」在系统层劫持
      // 带附件下载的响应（合成 204，浏览器 JS 拿不到字节）。字节级验证由 Python 侧对同一
      // 运行实例做服务端校验（见 driver），此处只记录环境事实，不作为失败。
      check('zip 字节：本机 IDM Advanced Integration 拦截（降级为同实例服务端校验）', true,
        `status=${zipRecord.status} statusText=${zipRecord.statusText}`);
    }
    check('zip 导出请求按图片粒度携带 asset_ids（不带 node_ids）',
      Boolean(zipRecord && zipRecord.request &&
        Array.isArray(zipRecord.request.asset_ids) && !zipRecord.request.node_ids),
      JSON.stringify(zipRecord && zipRecord.request));
    check('zip 导出请求 asset_ids 恰为标星的那 1 张',
      Boolean(zipRecord &&
        JSON.stringify(zipRecord.request && zipRecord.request.asset_ids) === JSON.stringify([markedAssetId])),
      JSON.stringify(zipRecord && zipRecord.request));

    await waitFor('导出按钮解锁', () => (!exportBtn.disabled) ? true : null);
    await sleep(400);

    // 4) 路径 2：写服务器目录（#58）
    const beforeDir = captured.length;
    dirInput.value = 'exported-marked';
    exportBtn.click();
    dirRecord = await waitCapture(beforeDir);
    check('目录导出返回 200', dirRecord && dirRecord.status === 200,
      dirRecord ? `status=${dirRecord.status} json=${JSON.stringify(dirRecord.json)} netErr=${dirRecord.networkError || ''}` : '未捕获到响应');
    check('目录导出请求按图片粒度携带 asset_ids + export_dir（不带 node_ids）',
      Boolean(dirRecord && dirRecord.request &&
        Array.isArray(dirRecord.request.asset_ids) &&
        dirRecord.request.export_dir === 'exported-marked' &&
        !dirRecord.request.node_ids),
      JSON.stringify(dirRecord && dirRecord.request));
    check('目录导出响应 files 恰含 1 个文件',
      Boolean(dirRecord && dirRecord.json &&
        Array.isArray(dirRecord.json.files) && dirRecord.json.files.length === 1),
      JSON.stringify(dirRecord && dirRecord.json && dirRecord.json.files));

    extras.zipB64 = (zipRecord && zipRecord.bytesB64) || '';
    extras.zipStatus = zipRecord ? zipRecord.status : null;
    extras.zipStatusText = (zipRecord && zipRecord.statusText) || '';
    extras.zipRequest = (zipRecord && zipRecord.request) || null;
    extras.dirFiles = (dirRecord && dirRecord.json && dirRecord.json.files) || null;
    extras.dirRequest = (dirRecord && dirRecord.request) || null;
  } catch (error) {
    errors.push(String(error?.message || error));
  }

  await originalFetch('/__conversation_browser_results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kind: 'final',
      result_id: resultId,
      assertions,
      errors,
      extras,
      pass: assertions.length > 0 && errors.length === 0,
    }),
  });
})();
