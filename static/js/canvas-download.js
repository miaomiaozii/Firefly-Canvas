// #223 下载层：把服务端打包好的 zip 安全落成本地文件。
//
// 现象：批量下载星标原图只得到一个 0 字节 zip，用户以为成功了。服务端同一端点直接请求
// 能返回 200 + 48MB 有效 zip → 故障在浏览器接收/落盘侧。
//
// 旧实现内联在 smart-canvas.js 的 zipDownloadImageItems 里，三个缺陷：
//   1. 固定 setTimeout(revokeObjectURL, 1200)：48MB 还没被浏览器读完就撤销 blob URL，下载被截断。
//   2. 不校验 blob.size、不与 Content-Length 对账：空/截断的 blob 照样落盘，还提示成功。
//   3. 不读服务端回执（#225 的 X-Download-Report）：部分失败时用户不知道少了哪几张。
//
// 本模块把这段逻辑收进可单测的接缝（tests/canvas_module_units/test-canvas-download.mjs），
// 只依赖 canvas-core.js 的 tr / 全局 fetch / document / URL。

// 撤销窗口：blob URL 必须活到浏览器把文件读完。固定 1200ms 会把大包截断成 0 字节。
// 取值：下限 20 秒（小包也留得下浏览器起下载）；体积项按 512 字节/毫秒 ≈ 每 MB 多给 2 秒
// （即 48MB ≈ 116 秒）；上限 10 分钟（再多就是下载已经死了，不如交给用户）。
const CANVAS_DOWNLOAD_REVOKE_BASE_MS = 20000;
const CANVAS_DOWNLOAD_REVOKE_BYTES_PER_MS = 512;
const CANVAS_DOWNLOAD_REVOKE_MAX_MS = 600000;
function canvasDownloadRevokeDelayMs(bytes){
    const size = Math.max(0, Number(bytes) || 0);
    return Math.min(
        CANVAS_DOWNLOAD_REVOKE_MAX_MS,
        CANVAS_DOWNLOAD_REVOKE_BASE_MS + Math.round(size / CANVAS_DOWNLOAD_REVOKE_BYTES_PER_MS),
    );
}

// #225 回执：后端把「打了几个、实际进了几个、哪些被跳过及原因」URL-encode 后放进
// X-Download-Report。这里解析成 {total, packed, skipped:[{name, reason}]}；
// 没有该头（旧后端 / HEAD 预检）时返回 null，绝不抛错。
function canvasDownloadReportFromResponse(response){
    try{
        const raw = response?.headers?.get?.('X-Download-Report');
        if(!raw) return null;
        const parsed = JSON.parse(decodeURIComponent(String(raw)));
        if(!parsed || typeof parsed !== 'object') return null;
        return {
            total: Number(parsed.total) || 0,
            packed: Number(parsed.packed) || 0,
            skipped: Array.isArray(parsed.skipped) ? parsed.skipped.map(s => ({
                name: String(s?.name || ''),
                reason: String(s?.reason || 'unknown'),
            })) : [],
        };
    }catch(e){
        return null;
    }
}

const CANVAS_DOWNLOAD_SKIP_REASON_KEYS = {
    trashed: 'smart.downloadSkippedTrashed',
    missing_file: 'smart.downloadSkippedMissing',
    unresolved_url: 'smart.downloadSkippedUnresolved',
    empty_url: 'smart.downloadSkippedInvalid',
    error: 'smart.downloadSkippedFailed',
};

// 部分失败时给用户的说明：明确「已打包 n/总数」，并列出被跳过的图与原因（#225）。
function canvasDownloadSkipSummary(report){
    if(!report || !Array.isArray(report.skipped) || !report.skipped.length) return '';
    const head = `${tr('smart.downloadPartial')} ${report.packed}/${report.total}`;
    const lines = report.skipped.slice(0, 6).map(s => {
        const label = s.name || tr('smart.downloadSkippedUnnamed');
        const key = CANVAS_DOWNLOAD_SKIP_REASON_KEYS[s.reason];
        return `${label}（${key ? tr(key) : s.reason}）`;
    });
    const rest = report.skipped.length - lines.length;
    if(rest > 0) lines.push(`…${rest}`);
    // toast 只能显一行，用 · 分隔（canvas-core.js:154 的 toast 不处理换行）。
    return `${head} · ${lines.join(' · ')}`;
}

// 触发浏览器下载。撤销只能排进延后定时器：下载还在飞的时候撤销 = 0 字节文件（#223）。
function canvasSaveBlobAsFile(blob, filename, revokeDelayMs){
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = String(filename || 'download.zip');
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => { URL.revokeObjectURL(href); }, Number(revokeDelayMs) || canvasDownloadRevokeDelayMs(blob?.size));
    return href;
}

// 从任意下载响应里取 blob，并做「空 / 截断」校验。#223 的同一类缺陷不止一条路径：
// 星标批量下载走 /api/canvas-assets/download，星标导出走 /api/excel/export-zip，
// 两条都必须能在拿不到完整包时报错，而不是静默落一个 0 字节文件。
async function canvasReceiveBlob(response){
    const report = canvasDownloadReportFromResponse(response);
    if(!response?.ok){
        // body 只能消费一次：先 text()，再从文本里剥 detail。旧写法在 text() 之后再调
        // response.json()，真实 Response 上必抛（body 已被消费）→ 服务端的失败原因永远到不了用户眼前。
        let detail = '';
        try{
            const text = String(await response.text() || '');
            try{
                const parsed = JSON.parse(text);
                detail = String(parsed?.detail ?? parsed?.message ?? '');
            }catch(e){
                detail = text;
            }
        }catch(e){ detail = ''; }
        const error = new Error((detail || tr('smart.batchDownloadFailed')).slice(0, 300));
        error.status = response?.status;
        error.report = report;
        throw error;
    }
    const blob = await response.blob();
    const bytes = Number(blob?.size || 0);
    const declared = Number(response?.headers?.get?.('Content-Length') || 0);
    // 服务端声明了体积、实际字节对不上 = 截断（包括只拿到 0 字节的极端情况）。
    // 必须排在空判定之前，否则 48MB 只收到 0 字节会被误报成「没有内容」。
    if(declared > 0 && bytes !== declared){
        const error = new Error(`${tr('smart.downloadTruncated')} (${bytes}/${declared})`);
        error.reason = 'truncated';
        error.report = report;
        throw error;
    }
    // 0 字节绝不落盘：以前正是这里静默「成功」。
    if(bytes <= 0){
        const error = new Error(tr('smart.downloadEmpty'));
        error.reason = 'empty';
        error.report = report;
        throw error;
    }
    return { blob, bytes, report };
}

// 主入口：POST /api/canvas-assets/download 并把 zip 落成文件。
// 失败一律 throw（带可读原因），绝不静默产出空文件；返回值带 report 供上层提示。
async function downloadCanvasArchive({ filename, urls = [], items = [] } = {}){
    const response = await fetch('/api/canvas-assets/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, urls, items }),
    });
    const { blob, bytes, report } = await canvasReceiveBlob(response);
    const href = canvasSaveBlobAsFile(blob, filename, canvasDownloadRevokeDelayMs(bytes));
    return { filename, bytes, href, report };
}
