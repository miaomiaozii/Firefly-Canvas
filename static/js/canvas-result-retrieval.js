// #493: private queue DTOs describe retrieval, never media assets or generation inputs.
var canvasResultRetrieval = (function () {
    'use strict';
    let services = {}, project = '', epoch = 0, version = 0, timer = null, loading = null;
    let groups = new Map(), pending = new Set(), chains = new Map(), notices = new Map();
    let listError = '', panelOpen = false, paintedKey = '';
    const working = new Set(['downloading', 'retrying', 'recovering']);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const en = () => window.StudioI18n?.lang() === 'en';
    const text = (zh, english) => en() ? english : zh;
    const task = log => String(log?.task_id || log?.taskId || log?.request?.task_id || '');
    const owner = log => String(log?.result_node_id || log?.nodeId || '');
    const key = (history, id) => `${history}:${id}`;
    const current = (p, e) => services.getProjectId?.() === p && project === p && epoch === e;
    function historyGroup(log) {
        const group = groups.get(String(log?.id || ''));
        if (!group || group.project_id !== project || group.task_id !== task(log) || group.node_id !== owner(log)) return null;
        const deleted = new Set((log.deletedOutputTombstones || []).map(item => item.deleted_output_id || item.output_id).filter(Boolean));
        return deleted.size ? {...group, items:group.items.filter(item => !deleted.has(item.output_id))} : group;
    }
    function valid(group) {
        if (!group || group.project_id !== project || typeof group.history_id !== 'string' || !group.task_id || !group.node_id || !Array.isArray(group.items)) return false;
        const ids = new Set(), indexes = new Set();
        return group.items.every(item => item && typeof item.id === 'string' && item.id && !ids.has(item.id) && ids.add(item.id)
            && Number.isInteger(item.index) && item.index >= 0 && !indexes.has(item.index) && indexes.add(item.index)
            && typeof item.can_retrieve === 'boolean' && typeof item.status === 'string');
    }
    function listedGroups() {
        const logs = services.getHistory?.() || [];
        return [...groups.values()].map(group => {
            const log = logs.find(entry => entry.id === group.history_id);
            return log ? historyGroup(log) : group;
        }).filter(Boolean);
    }
    function nodeGroup(node) {
        const tasks = node?.pendingTasks || [];
        // Canonical reloads drop pendingTasks; the trusted listing and canonical history own the result.
        // A still-present task list must agree, so an in-flight replacement cannot inherit old actions.
        return (services.getHistory?.() || []).map(historyGroup).find(group => group && group.node_exists !== false && group.node_id === node?.id
            && (!tasks.length || tasks.some(item => item.taskId === group.task_id && item.download?.history_id === group.history_id))) || null;
    }
    const actionable = group => group.items.filter(item => item.status !== 'succeeded' && item.can_retrieve && !working.has(item.status) && !pending.has(key(group.history_id, item.id)));
    const missing = group => group.items.filter(item => item.status !== 'succeeded');
    const terminal = item => item.status === 'unavailable' || (item.status === 'expired' && !item.can_retrieve);
    const remaining = group => missing(group).filter(item => !terminal(item) && (item.can_retrieve || working.has(item.status) || pending.has(key(group.history_id,item.id))));
    const unavailable = group => missing(group).filter(item => !remaining(group).includes(item) && ['unavailable','expired'].includes(item.status));
    function ttl(item) {
        if (!Number.isFinite(item.remaining_seconds)) return text('有效期未知', 'Expiry unknown');
        const seconds = Math.max(0, Math.floor(item.remaining_seconds));
        if (seconds < 60) return text('即将过期', 'Expiring soon');
        const minutes = Math.ceil(seconds / 60);
        return seconds >= 3600
            ? text(`剩余约 ${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`, `About ${Math.floor(minutes / 60)} hours ${minutes % 60} minutes remaining`)
            : text(`剩余 ${minutes} 分钟`, `${minutes} minute${minutes === 1 ? '' : 's'} remaining`);
    }
    function phase(group, item) {
        if (pending.has(key(group.history_id, item.id)) && item.status !== 'succeeded' && !terminal(item) && !working.has(item.status)) return text('取回中', 'Retrieving');
        return ({pending:text('待取回','Pending retrieval'),failed:text('取回失败，可重试','Retrieval failed'),downloading:text('下载中','Downloading'),retrying:text('重试下载中','Retrying download'),recovering:text('查询原结果中','Recovering original'),expired:item.can_retrieve ? text('链接已过期，仍可查询原结果','Link expired; original recoverable') : text('链接已过期','Link expired'),unavailable:text('已确认无法取回','Confirmed unavailable'),succeeded:text('已保存','Saved')})[item.status] || text('等待状态更新','Awaiting status');
    }
    function actionHtml(button) {
        return `<span class="retrieval-action">${button}<span class="retrieval-no-charge">${text('不扣费','No charge')}</span></span>`;
    }
    function button(group, item) {
        return actionHtml(`<button type="button" data-retrieval-history="${esc(group.history_id)}" data-retrieval-item="${esc(item.id)}" ${actionable(group).includes(item) ? '' : 'disabled'} title="${text('只取回原结果，不会重新生成、不扣费','Retrieve original only; no generation or charge')}"><svg class="ico" aria-hidden="true"><use href="#i-download"/></svg>${text('重新取回','Retrieve again')}</button>`);
    }
    function slotHtml(group, item, style = '') {
        const mb = value => `${(Number(value) / (1024 * 1024)).toFixed(1)} MB`;
        const bytes = Number.isFinite(item.downloaded_bytes) ? `${mb(item.downloaded_bytes)}${Number.isFinite(item.total_bytes) ? ` / ${mb(item.total_bytes)}` : ''}` : '';
        return `<div class="retrieval-slot" style="${esc(style)}" data-retrieval-slot="${esc(item.id)}" role="group" aria-label="${esc(text('原结果 ', 'Original result ') + (item.index + 1))}"><span role="status">${esc(phase(group,item))}</span>${terminal(item) || item.status === 'succeeded' ? '' : button(group,item)}<small>${esc(ttl(item))}</small>${bytes ? `<small>${esc(bytes)}</small>` : ''}${item.error ? `<details><summary>${text('失败说明','Error details')}</summary><span>${esc(item.error)}</span></details>` : ''}</div>`;
    }
    function batchHtml(group) {
        const n = remaining(group).length;
        return n >= 2 ? actionHtml(`<button type="button" data-retrieval-history="${esc(group.history_id)}" data-retrieval-all="1" ${actionable(group).length ? '' : 'disabled'} title="${text('只取回原结果，不会重新生成、不扣费','Retrieve original only; no generation or charge')}">${text('全部取回','Retrieve all')}（${n}）</button>`) : '';
    }
    function generatedText(group) {
        return remaining(group).length ? text('原图已生成，待取回','Original generated; awaiting retrieval') : text('原图已生成','Original generated');
    }
    function summaryHtml(group) {
        if (!group || !missing(group).length) return '';
        const n = remaining(group).length, terminal = unavailable(group).length;
        const saved = group.items.length - missing(group).length, unresolved = missing(group).length - n - terminal;
        return `<div class="retrieval-summary" role="status"><strong>${esc(text('原图已生成','Original generated'))} · ${[n ? `${n} ${text('张待取回','awaiting retrieval')}` : '', saved ? `${saved} ${text('张已保存','saved')}` : '', terminal ? `${terminal} ${text('张无法取回','unavailable')}` : '', unresolved ? `${unresolved} ${text('张等待状态确认','awaiting status')}` : ''].filter(Boolean).join(' · ')}</strong>${batchHtml(group)}<small>${text('只取回原结果，不会重新生成、不扣费','Original results only; no generation or charge')}</small>${notices.has(group.history_id) ? `<span role="alert">${esc(notices.get(group.history_id))}</span>` : ''}</div>`;
    }
    function slots(group, outputs) {
        const used = new Set();
        const result = group.items.flatMap(item => {
            const index = outputs.findIndex((output, i) => !used.has(i) && (item.output_id ? output.output_id === item.output_id : Number.isInteger(output.output_order) && output.output_order === item.index));
            if (index >= 0) { used.add(index); return [{index:item.index, output:outputs[index], outputIndex:index}]; }
            return [{index:item.index, item}];
        });
        outputs.forEach((output, index) => { if (!used.has(index)) result.push({index:Number.isInteger(output.output_order) ? output.output_order : result.length, output, outputIndex:index}); });
        return result.sort((a,b) => a.index - b.index);
    }
    function nodeHtml(node, layout, renderSaved) {
        const group = nodeGroup(node);
        if (!group || !missing(group).length) return '';
        const rows = slots(group, node.images || []);
        // A singleton shares the image's inner viewport, not the padded outer node box (#493).
        const width = layout.thumb;
        const height = layout.batchThumbHeight || Math.round(width * 3 / 4);
        const style = layout.single ? 'width:100%;height:100%' : `--thumb-w:${width}px;--thumb-h:${height}px;width:${width}px;height:${height}px`;
        return `<div class="retrieval-node pending-batch-stack${layout.single ? ' retrieval-single' : ''}${layout.batchThumbHeight ? ' retrieval-frame' : ''}">${summaryHtml(group).replace('class="retrieval-summary"','class="retrieval-summary batch-statusbar"')}<div class="thumb-grid" data-thumb-scroll="1" style="--thumb-cols:${layout.cols};--thumb-size:${width}px;--thumb-max-height:${layout.gridHeight || height}px;gap:${layout.batchGap ?? 12}px">${rows.map(row => row.output ? renderSaved(row.output,row.outputIndex,style) : slotHtml(group,row.item,style)).join('')}</div></div>`;
    }
    function batchHeight(node, grid) {
        const group = nodeGroup(node);
        // Retrieval replaces generation's status area; cached generation height must not reserve it twice.
        return group && missing(group).length && group.items.length > 1
            ? grid.gridH + SMART_GROUP_LAYOUT.PADDING * 2 + BATCH_STATUSBAR_H : 0;
    }
    function viewKey(list) {
        return JSON.stringify([...list.values()].map(group => ({...group, remaining_seconds:undefined,
            items:group.items.map(item => ({...item, remaining_seconds:ttl(item)}))})));
    }
    function groupHeading(group, index) {
        const log = (services.getHistory?.() || []).find(entry => entry.id === group.history_id);
        const label = String(log?.displayPrompt || log?.prompt || log?.title || '').trim();
        return label && label !== group.history_id ? label : `${text('生成记录', 'Generation record ')}${index + 1}`;
    }
    function paint() { paintedKey = viewKey(groups); paintToolbar(); services.repaint?.(); }
    function panelIdentity(control) {
        const history = control.closest('[data-retrieval-group]')?.dataset.retrievalGroup ?? '';
        const item = control.closest('[data-retrieval-slot]')?.dataset.retrievalSlot ?? '';
        const kind = control.hasAttribute('data-retrieval-close') ? 'close'
            : control.hasAttribute('data-retrieval-refresh') ? 'refresh'
            : control.hasAttribute('data-retrieval-all') ? 'all' : control.tagName;
        // Item IDs are only unique within a history; tuple encoding also avoids delimiter collisions.
        return JSON.stringify([history, item, kind]);
    }
    function paintToolbar() {
        const badge = document.getElementById('smartRetrievalToggle');
        if (!badge) return;
        const n = listedGroups().reduce((sum,group) => sum + remaining(group).length, 0);
        badge.hidden = !groups.size && !listError;
        const label = listError ? text('取回列表读取失败','Retrieval list unavailable') : `${n} ${text('张待取回','awaiting retrieval')}`;
        let count = document.getElementById('smartRetrievalCount') || badge.querySelector('.retrieval-count');
        if (!count) {
            count = document.createElement('span');
            count.className = 'retrieval-count';
            badge.appendChild(count);
        }
        count.textContent = label;
        badge.title = label; badge.setAttribute('aria-label',label);
        badge.setAttribute('aria-expanded', String(panelOpen));
        badge.onclick = () => { panelOpen = !panelOpen; paintToolbar(); if(panelOpen) void refresh(); };
        const panel = document.getElementById('smartRetrievalPanel');
        if (!panel) return;
        const focused = !panel.hidden && document.activeElement && panel.contains(document.activeElement) ? panelIdentity(document.activeElement) : null;
        panel.hidden = !panelOpen;
        if (!panelOpen) return;
        const expanded = new Set([...panel.querySelectorAll('details')].filter(details => details.open).map(panelIdentity));
        const scrollTop = panel.scrollTop;
        panel.innerHTML = `<header><strong>${text('本项目待取回','Project retrievals')}</strong><button type="button" data-retrieval-close aria-label="${text('关闭','Close')}">×</button></header>${listError ? `<p role="alert">${esc(listError)}</p><button type="button" data-retrieval-refresh>${text('重新读取','Reload')}</button>` : ''}${listedGroups().filter(group => missing(group).length).map((group,index) => `<section data-retrieval-group="${esc(group.history_id)}"><h3>${esc(groupHeading(group,index))}</h3>${summaryHtml(group)}<div class="retrieval-project-slots">${group.items.map(item => slotHtml(group,item)).join('')}</div></section>`).join('') || `<p>${text('没有待取回原件','No pending originals')}</p>`}`;
        panel.querySelector('[data-retrieval-close]').onclick = () => { panelOpen=false; paintToolbar(); badge.focus(); };
        const retry = panel.querySelector('[data-retrieval-refresh]');
        if(retry) retry.onclick = () => void refresh();
        panel.onkeydown = event => { if(event.key === 'Escape') {panelOpen=false; paintToolbar(); badge.focus();} };
        bind(panel, true);
        panel.querySelectorAll('details').forEach(details => { details.open = expanded.has(panelIdentity(details)); });
        // Polls may replace controls, but must not take focus from outside or revive a closed panel.
        if (focused) {
            const target = [...panel.querySelectorAll('button, summary')].find(control => panelIdentity(control) === focused && !control.disabled);
            (target || panel.querySelector('[data-retrieval-close]')).focus({preventScroll:true});
        }
        panel.scrollTop = scrollTop;
    }
    async function request(url, options = {}) {
        const response = await (services.fetch || window.fetch)(url, {credentials:'same-origin', ...options});
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`);
        return data;
    }
    function base(p) { return `/api/project-library/${encodeURIComponent(p)}/image-result-downloads`; }
    async function refresh() {
        syncProject();
        if (!project) return;
        if (loading) return loading;
        const p=project, e=epoch, v=version;
        const run = (async () => {
            try {
                const data = await request(base(p));
                if (!current(p,e) || version !== v) return;
                if (!Array.isArray(data.groups) || !data.groups.every(valid)) throw new Error(text('取回列表身份或结构无效','Invalid retrieval identity or schema'));
                const incoming = new Map(data.groups.map(group => [group.history_id,group]));
                const archiveChanged = [...groups.values()].some(before => {
                    const after = incoming.get(before.history_id);
                    return !after || after.items.some(item => item.status === 'succeeded' && before.items.some(old => old.id === item.id && old.status !== 'succeeded'));
                });
                if(archiveChanged && !await services.adoptCanonical?.(p)) throw new Error(text('原件状态已更新，画布同步未确认','Original state updated; canvas synchronization not confirmed'));
                if(!current(p,e) || version !== v) return;
                const changed = listError || paintedKey !== viewKey(incoming);
                groups = incoming; listError='';
                if (changed) paint();
                else if (!document.getElementById('smartRetrievalToggle')?.onclick) paintToolbar();
            } catch(error) {
                if(current(p,e)) {listError=String(error.message || error); paintToolbar();}
            } finally { if(current(p,e)) {loading=null; schedule();} }
        })();
        loading=run; return run;
    }
    function schedule() {
        clearTimeout(timer);
        timer=setTimeout(() => {timer=null; void refresh();}, [...groups.values()].some(group => group.items.some(item => working.has(item.status))) || pending.size ? 1000 : 10000);
    }
    function syncProject() {
        const p=services.getProjectId?.() || '';
        if(p === project) return;
        project=p; epoch++; version++; groups=new Map(); pending=new Set(); chains=new Map(); notices=new Map(); loading=null; listError=''; panelOpen=false; paintedKey=''; clearTimeout(timer); timer=null; paintToolbar();
    }
    function sync() { syncProject(); if(project && !loading && !timer) void refresh(); }
    async function retrieve(historyId, ids, projectList = false) {
        syncProject();
        const log=(services.getHistory?.() || []).find(log => log.id === historyId);
        const group=log ? historyGroup(log) : groups.get(historyId), p=project, e=epoch;
        // Server-listed groups outlive the client's bounded history window; canonical tombstones still veto every surface.
        if(!group || !valid(group) || (!projectList && !log)) return;
        const allowed=new Set(actionable(group).map(item => item.id));
        const selected=[...new Set(ids)].filter(id => allowed.has(id));
        if(!selected.length) return;
        selected.forEach(id => pending.add(key(historyId,id))); version++; paint(); schedule();
        const previous=chains.get(historyId) || Promise.resolve();
        const run=previous.then(async () => {
            if(!current(p,e)) return;
            try {
                const result=await request(`${base(p)}/${encodeURIComponent(historyId)}/redownload`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item_ids:selected})});
                if(!current(p,e)) return;
                if(result.history_id !== historyId || (result.items && (!valid(result) || result.task_id !== group.task_id || result.node_id !== group.node_id))) throw new Error(text('取回响应身份不匹配','Retrieval response identity mismatch'));
                if(!await services.adoptCanonical?.(p)) throw new Error(text('原件状态已更新，画布同步未确认；请稍后刷新','Original state updated; canvas synchronization not confirmed'));
                if(!current(p,e)) return;
                if(result.items) groups.set(historyId,result);
                else if(result.status === 'succeeded') groups.delete(historyId);
                else throw new Error(text('取回响应缺少逐项状态','Retrieval response lacks item states'));
                notices.delete(historyId);
            } catch(error) { if(current(p,e)) notices.set(historyId,String(error.message || error)); }
            finally { if(current(p,e)) {selected.forEach(id => pending.delete(key(historyId,id))); version++; paint(); schedule();} }
        });
        chains.set(historyId,run); return run;
    }
    function bind(root, projectList = false) {
        root?.querySelectorAll?.('.retrieval-slot, .retrieval-summary').forEach(element => {
            element.onpointerdown = event => event.stopPropagation();
            element.ondblclick = event => event.stopPropagation();
        });
        root?.querySelectorAll?.('[data-retrieval-history]').forEach(button => {
            button.onpointerdown = event => event.stopPropagation();
            button.onclick = event => {
                event.stopPropagation();
                const group=groups.get(button.dataset.retrievalHistory);
                if(group) return retrieve(group.history_id, button.dataset.retrievalAll ? actionable(group).map(item => item.id) : [button.dataset.retrievalItem], projectList);
            };
        });
    }
    return {configure(next) {services={...services,...next};}, sync, refresh, retrieve, bind, historyGroup, nodeGroup, nodeHtml, batchHeight, slotHtml, slots, summaryHtml, generatedText};
})();
