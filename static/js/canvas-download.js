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
            // #229：打包改为流式后，某张图尺寸读不准时后端宁可不发 Content-Length。
            // 这两个字段就是「没有 Content-Length 时仍然可用的完整性信号」。
            expectedBytes: Number(parsed.expected_bytes) || 0,
            sizeKnown: Boolean(parsed.size_known),
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
    // #229：流式打包下 Content-Length 可能缺席（某张图尺寸读不准时后端不发，也不发一个错的）。
    // 这时用回执里的素材字节合计做**下界**：STORED（不压缩）zip 必然 ≥ 素材字节之和，
    // 所以只可能偏大不会偏小 —— 收到更少就是被截断。没有回执时不做无根据的判定。
    if(declared <= 0 && report?.sizeKnown && report.expectedBytes > 0 && bytes < report.expectedBytes){
        const error = new Error(`${tr('smart.downloadTruncated')} (${bytes}/${report.expectedBytes})`);
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

// #228 本地下载记录：打包回执只闪一次就没了，用户回头想查「那次到底进了几张」无处可查。
// 存进 localStorage（有上限，最新在前），面板入口读它回查。任何异常都不能影响下载本身，
// 所以读写一律吞掉异常（回查是附加能力，不是主流程）。
const CANVAS_DOWNLOAD_RECORDS_KEY = 'canvasDownloadRecords';
const CANVAS_DOWNLOAD_RECORDS_LIMIT = 50;

function canvasDownloadRecords(){
    try{
        const raw = localStorage.getItem(CANVAS_DOWNLOAD_RECORDS_KEY);
        if(!raw) return [];
        const parsed = JSON.parse(raw);
        if(!Array.isArray(parsed)) return [];
        return parsed.filter(r => r && typeof r === 'object');
    }catch(e){
        return [];
    }
}

// #228 规范第 1 条要求记录里含「请求了哪些素材」。localStorage 只有 5MB 量度，
// 所以清单截断存，但真实条数（requestedCount）不能谎。
const CANVAS_DOWNLOAD_REQUESTED_STORED_LIMIT = 40;

function canvasAssetIdentityFromUrl(url){
    const text = String(url || '').trim();
    if(!text) return '';
    const tail = text.split('?')[0].split('/').filter(Boolean).pop() || text;
    try{ return decodeURIComponent(tail); }catch(e){ return tail; }
}

// items 与 urls 在实际调用里描述的是同一批素材（hub:7581），所以优先用带名字的 items，
// urls 只补 items 没覆盖到的；没名字的素材退化成 url 末段（asset id）当身份。
function canvasRequestedIdentities({ items = [], urls = [] } = {}){
    const out = [];
    const seen = new Set();
    for(const item of Array.isArray(items) ? items : []){
        const url = String(item?.url || '').trim();
        if(url){
            if(seen.has(url)) continue;
            seen.add(url);
        }
        const name = String(item?.name || item?.filename || '').trim();
        const identity = name || canvasAssetIdentityFromUrl(url);
        if(identity) out.push(identity);
    }
    for(const url of Array.isArray(urls) ? urls : []){
        const text = String(url || '').trim();
        if(!text || seen.has(text)) continue;
        seen.add(text);
        const identity = canvasAssetIdentityFromUrl(text);
        if(identity) out.push(identity);
    }
    return out;
}

function canvasRecordDownload({ filename, bytes = 0, report = null, status = 'ok', reason = '', requested = null } = {}){
    const record = {
        at: new Date().toISOString(),
        filename: String(filename || 'download.zip'),
        bytes: Number(bytes) || 0,
        status: String(status || 'ok'),
        total: Number(report?.total) || 0,
        packed: Number(report?.packed) || 0,
        skipped: Array.isArray(report?.skipped) ? report.skipped.slice(0, 12) : [],
    };
    const requestedList = Array.isArray(requested)
        ? requested.map(x => String(x || '').trim()).filter(Boolean)
        : [];
    if(requestedList.length){
        record.requestedCount = requestedList.length;
        record.requested = requestedList.slice(0, CANVAS_DOWNLOAD_REQUESTED_STORED_LIMIT);
    }else if(record.total > 0){
        // 没给清单时至少要存服务端报的请求总数，别把「几张」丢掉。
        record.requestedCount = record.total;
    }
    if(reason) record.reason = String(reason);
    try{
        const list = canvasDownloadRecords();
        list.unshift(record);
        localStorage.setItem(CANVAS_DOWNLOAD_RECORDS_KEY, JSON.stringify(list.slice(0, CANVAS_DOWNLOAD_RECORDS_LIMIT)));
    }catch(e){ /* 存不下就算了，下载结果已经拿到 */ }
    return record;
}

// 面板里给人看的一行：时间 · 文件名 · 进了几张/共几张 · 大小 · 跳过原因。
function canvasFormatDownloadRecord(record){
    if(!record) return '';
    const when = String(record.at || '').replace('T', ' ').slice(5, 16);
    const parts = [when, record.filename];
    if(record.requestedCount > 0){
        // 翻译里漏掉 {n} 占位符时，计数要照旧显示出来，不能被静默吃掉。
        const label = String(tr('smart.downloadRecordsRequested'));
        parts.push(label.includes('{n}')
            ? label.replace('{n}', String(record.requestedCount))
            : `${label} ${record.requestedCount}`);
    }
    if(record.total > 0) parts.push(`${record.packed}/${record.total}`);
    if(Number(record.bytes) > 0) parts.push(`${(Number(record.bytes) / 1024 / 1024).toFixed(1)}MB`);
    if(record.status === 'failed') parts.push(tr('smart.batchDownloadFailed'));
    const reasons = Array.isArray(record.skipped) ? record.skipped.slice(0, 3).map(s => {
        const key = CANVAS_DOWNLOAD_SKIP_REASON_KEYS[s?.reason];
        return `${s?.name || tr('smart.downloadSkippedUnnamed')}（${key ? tr(key) : s?.reason}）`;
    }) : [];
    if(reasons.length) parts.push(reasons.join(' · '));
    return parts.filter(Boolean).join(' · ');
}

// 面板回查用的 HTML（#228）：直接交给 innerHTML，所以全部 escapeHtml。
// 复用现有 .smart-marked-export-files / -file 样式，不新增 CSS。
function canvasDownloadRecordsHtml({ limit = 8 } = {}){
    const records = canvasDownloadRecords().slice(0, Math.max(1, Number(limit) || 8));
    if(!records.length) return `<div class="smart-marked-export-file">${escapeHtml(tr('smart.downloadRecordsEmpty'))}</div>`;
    return records.map(r => {
        const line = canvasFormatDownloadRecord(r);
        // 完整请求清单走 hover：面板一行装不下 21 个名字，但字段必须能查到（#228 第 2 条）。
        const list = Array.isArray(r.requested) ? r.requested : [];
        let title = line;
        if(list.length){
            const more = r.requestedCount > list.length ? ` …${r.requestedCount - list.length}` : '';
            title += ` · ${tr('smart.downloadRequestedList')}: ${list.join(', ')}${more}`;
        }
        return `<div class="smart-marked-export-file" title="${escapeHtml(title)}">` +
            `<i data-lucide="${r.status === 'failed' ? 'triangle-alert' : 'check'}" aria-hidden="true"></i>` +
            `<span>${escapeHtml(line)}</span></div>`;
    }).join('');
}

// 主入口：POST /api/canvas-assets/download 并把 zip 落成文件。
// 失败一律 throw（带可读原因），绝不静默产出空文件；返回值带 report 供上层提示。
// 成功/部分成功/失败都要落一条本地记录（#228），否则回查是空的。
async function downloadCanvasArchive({ filename, urls = [], items = [] } = {}){
    const response = await fetch('/api/canvas-assets/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, urls, items }),
    });
    let received;
    try{
        received = await canvasReceiveBlob(response);
    }catch(error){
        canvasRecordDownload({
            filename,
            bytes: 0,
            report: error?.report || null,
            status: 'failed',
            reason: error?.reason || 'failed',
            requested: canvasRequestedIdentities({ items, urls }),
        });
        throw error;
    }
    const { blob, bytes, report } = received;
    const href = canvasSaveBlobAsFile(blob, filename, canvasDownloadRevokeDelayMs(bytes));
    canvasRecordDownload({
        filename,
        bytes,
        report,
        status: report?.skipped?.length ? 'partial' : 'ok',
        requested: canvasRequestedIdentities({ items, urls }),
    });
    return { filename, bytes, href, report };
}
