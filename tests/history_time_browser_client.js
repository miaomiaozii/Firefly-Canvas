(function(){
    'use strict';

    const query = new URLSearchParams(location.search);
    const expectedCreatedAt = Number(query.get('createdAt') || 0);
    const expectedOlderCreatedAt = Number(query.get('olderCreatedAt') || 0);
    const result = {
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        pass: false
    };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass:Boolean(pass), actual});
    async function waitFor(predicate, timeout=12000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                if(await predicate()) return true;
            } catch(_) {}
            await sleep(40);
        }
        return false;
    }
    function renderedTime(historyId){
        const card = document.querySelector(`[data-canvas-history-id="${CSS.escape(historyId)}"]`);
        const subline = card?.querySelector('.log-subline');
        const span = subline?.querySelector('span');
        return span?.textContent?.trim() || '';
    }
    async function postResult(){
        result.pass = result.errors.length === 0 && result.assertions.every(item => item.pass);
        await fetch('/__history_time_results', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(result)
        }).catch(error => result.errors.push(`result upload: ${error.message}`));
    }
    async function run(){
        const lang = window.StudioI18n?.lang && window.StudioI18n.lang() === 'en' ? 'en-US' : 'zh-CN';
        const expectedTimeText = new Date(expectedCreatedAt).toLocaleString(lang);
        const expectedOlderTimeText = new Date(expectedOlderCreatedAt).toLocaleString(lang);
        const loaded = await waitFor(() => document.querySelector('#smartHistoryToggle') && document.title.includes('时间历史项目'), 15000);
        assertion('real product page loaded its canvas', loaded, document.title);
        const toggle = document.getElementById('smartHistoryToggle');
        toggle?.click();
        const opened = await waitFor(() => document.getElementById('smartHistoryModal')?.classList.contains('open') && Boolean(document.querySelector('[data-canvas-history-id="history-time-1"]')), 12000);
        assertion('history drawer renders the seeded timestamped record', opened, document.querySelectorAll('.log-item').length);
        if(!opened){ await postResult(); return; }

        const actualTime = renderedTime('history-time-1');
        const olderActualTime = renderedTime('history-time-2');
        assertion('rendered time equals the persisted created_at timestamp, not page-load time', actualTime === expectedTimeText, {rendered:actualTime, expected:expectedTimeText, now:new Date().toLocaleString(lang)});
        assertion('older record renders its own distinct timestamp', olderActualTime === expectedOlderTimeText && olderActualTime !== actualTime, {rendered:olderActualTime, expected:expectedOlderTimeText, first:actualTime});
        const newest = new Date(Date.now() + 86400000).toLocaleString(lang);
        assertion('rendered time is not a future page-load fallback', actualTime !== newest, actualTime);
    }
    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
