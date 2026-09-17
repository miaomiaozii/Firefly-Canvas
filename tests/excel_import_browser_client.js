(async () => {
  const resultId = `excel-import-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const params = new URLSearchParams(location.search);
  const projectId = params.get('project') || '';
  const xlsxBase64 = params.get('xlsx') || '';
  const assertions = [];
  const errors = [];

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
  async function postResult() {
    await fetch('/__conversation_browser_results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: 'final',
        result_id: resultId,
        assertions,
        errors,
        pass: assertions.length > 0 && errors.length === 0,
      }),
    });
  }

  window.addEventListener('error', event => {
    if (event?.error?.message) errors.push(event.error.message);
  });
  window.addEventListener('unhandledrejection', event => errors.push(String(event.reason?.message || event.reason || 'unhandled rejection')));

  const b64ToBytes = b64 => Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const makeFile = (bytes, name, type) => new File([bytes], name, { type });
  const dropFile = file => {
    const dt = new DataTransfer();
    dt.items.add(file);
    (document.getElementById('shell') || document.body).dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  };
  const overlay = () => document.getElementById('excel-preview-overlay');
  const excelNodeIds = () => Array.from(document.querySelectorAll('.image-node[data-id^="excel_"]')).map(e => e.getAttribute('data-id'));
  const serverExcelNodes = async () => {
    const response = await fetch(`/api/canvases/${encodeURIComponent(projectId)}`);
    if (!response.ok) return null;
    const data = await response.json();
    const canvas = (data && (data.canvas || data)) || {};
    return (canvas.nodes || []).filter(n => n && n.excel_task_id).map(n => n.id);
  };

  try {
    await waitFor('画布加载', () => (typeof canvasLoadReady !== 'undefined' && canvasLoadReady) || null);
    check('页面加载完成', true);

    // --- 异常路径（不产生画布内容）---
    // 1) 非 xlsx 文件：不打开预览
    dropFile(makeFile(new TextEncoder().encode('not excel'), 'notes.txt', 'text/plain'));
    await sleep(1200);
    check('非 xlsx 文件不打开预览浮层', overlay() === null);

    // 2) 损坏的 xlsx：UI 不打开预览
    dropFile(makeFile(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00, 0x01]), 'corrupt.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'));
    await sleep(1500);
    check('损坏 xlsx 不打开预览浮层', overlay() === null);

    // 3) 损坏 xlsx 走 API：400 且提示解析失败
    const badResponse = await fetch('/api/excel/import-plan', {
      method: 'POST',
      body: (() => { const fd = new FormData(); fd.append('file', makeFile(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00, 0x01]), 'corrupt.xlsx'), 'corrupt.xlsx'); return fd; })(),
    });
    const badText = await badResponse.text();
    check('损坏 xlsx API 返回 400', badResponse.status === 400, `status=${badResponse.status} text=${badText.slice(0, 80)}`);
    check('损坏 xlsx API 提示解析失败', /Excel 解析失败/.test(badText), badText.slice(0, 120));

    // 4) 非 xlsx 文件名走 API：400
    const wrongNameResponse = await fetch('/api/excel/import-plan', {
      method: 'POST',
      body: (() => { const fd = new FormData(); fd.append('file', makeFile(new TextEncoder().encode('x'), 'data.csv'), 'data.csv'); return fd; })(),
    });
    const wrongNameText = await wrongNameResponse.text();
    check('非 xlsx 文件名 API 返回 400', wrongNameResponse.status === 400, `status=${wrongNameResponse.status} text=${wrongNameText.slice(0, 80)}`);

    // 5) 空任务 sheet：200 且 tasks=[]（UI 侧展开被后端拒绝：400）
    const emptySheetXlsx = (() => {
      // 用页面已有能力构造不了新 xlsx，直接通过服务端返回的 plan 校验展开空计划
      return null;
    })();
    const emptyExpandResponse = await fetch('/api/excel/expand-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, plan: { project_id: projectId, tasks: [] } }),
    });
    check('空任务计划展开被拒绝 400', emptyExpandResponse.status === 400, `status=${emptyExpandResponse.status}`);

    // --- 正常路径 ---
    const xlsxBytes = xlsxBase64 ? b64ToBytes(xlsxBase64) : new Uint8Array(0);
    check('收到 xlsx 夹具', xlsxBytes.length > 0, `len=${xlsxBytes.length}`);

    // 先自行调 import-plan 校验 Plan IR 结构
    const planResponse = await fetch('/api/excel/import-plan', {
      method: 'POST',
      body: (() => { const fd = new FormData(); fd.append('file', makeFile(xlsxBytes, 'fixture.xlsx'), 'fixture.xlsx'); return fd; })(),
    });
    check('import-plan 200', planResponse.status === 200, `status=${planResponse.status}`);
    const plan = await planResponse.json();
    const taskIds = (plan.tasks || []).map(t => t.task_id).sort();
    check('Plan IR 含 3 个任务 excel_1..excel_3', JSON.stringify(taskIds) === JSON.stringify(['excel_1', 'excel_2', 'excel_3']), JSON.stringify(taskIds));
    const sectionNames = new Set((plan.tasks || []).map(t => t.section));
    check('Plan IR 含主图方案与 A+ 分组', sectionNames.has('主图方案') && [...sectionNames].some(s => s.startsWith('高级A+')), JSON.stringify([...sectionNames]));

    // UI：drop → 预览浮层
    dropFile(makeFile(xlsxBytes, 'fixture.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'));
    await waitFor('预览浮层出现', () => (overlay() ? overlay() : null));
    const overlayText = overlay().innerText;
    check('预览浮层展示任务统计', /总任务/.test(overlayText) && /3/.test(overlayText), overlayText.slice(0, 120).replace(/\n/g, '|'));

    // UI：点击展开
    const expandButton = overlay().querySelector('.excel-btn-primary');
    check('预览浮层有展开按钮', Boolean(expandButton));
    if (expandButton) expandButton.click();

    await waitFor('展开完成：浮层消失且 6 个节点渲染', () => {
      const ids = excelNodeIds();
      const want = ['excel_1-prompt', 'excel_1-image', 'excel_2-prompt', 'excel_2-image', 'excel_3-prompt', 'excel_3-image'];
      return want.every(id => ids.includes(id)) ? ids : null;
    }, 60000);
    check('画布渲染全部 6 个展开节点', true);

    // 与服务器落盘一致性
    const serverIds = await waitFor('服务器画布含 excel 节点', async () => {
      const ids = await serverExcelNodes();
      return Array.isArray(ids) && ids.length === 6 ? ids : null;
    }, 30000);
    check('服务器画布 6 个 excel 节点与 UI 一致', JSON.stringify(serverIds.sort()) === JSON.stringify(['excel_1-prompt', 'excel_1-image', 'excel_2-prompt', 'excel_2-image', 'excel_3-prompt', 'excel_3-image'].sort()), JSON.stringify(serverIds));
  } catch (error) {
    errors.push(String(error?.message || error));
  }
  await postResult();
})();
