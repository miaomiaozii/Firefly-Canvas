// @ts-check
(() => {
    const storageKey = 'firefly.backendRestartNotice.v1';
    /** @type {'stale' | 'legacy' | null} */
    let restart = null;
    /** @type {HTMLElement | null} */
    let notice = null;
    let checking = false;
    // The UI is Chinese-only (#365); entries avoids t()'s storage write when storage is denied.
    const i18n = /** @type {{StudioI18n: {entries: () => {zh: Record<string, string>}}}} */ (/** @type {unknown} */ (window)).StudioI18n;
    const messages = i18n.entries().zh;
    const tr = (/** @type {string} */ key) => messages[key];
    try {
        const saved = localStorage.getItem(storageKey);
        if (saved === 'stale' || saved === 'legacy') restart = saved;
    } catch (error) {
        // #348: storage denial must not hide an in-memory service warning.
        console.warn('[service-version] Cannot read saved notice', error);
    }

    function render(/** @type {boolean} */ unknown = false) {
        if (!restart && !unknown) {
            if (notice) notice.hidden = true;
            return;
        }
        if (!notice) {
            notice = document.createElement('section');
            notice.id = 'serviceVersionNotice';
            notice.className = 'service-version-notice';
            notice.setAttribute('role', 'alert');
            document.body.appendChild(notice);
        }
        const title = document.createElement('strong');
        title.textContent = tr(restart ? 'serviceVersion.restartTitle' : 'serviceVersion.unknownTitle');
        const message = document.createElement('span');
        message.textContent = tr(restart === 'stale' ? 'serviceVersion.stale' : restart === 'legacy' ? 'serviceVersion.legacy' : 'serviceVersion.unknown');
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.textContent = tr('serviceVersion.retry');
        retry.addEventListener('click', () => { void check(); });
        // Only localized text is rendered; endpoint/error/storage content is never HTML.
        notice.replaceChildren(title, message, retry);
        notice.hidden = false;
    }

    function persist() {
        try {
            if (restart) localStorage.setItem(storageKey, restart);
            else localStorage.removeItem(storageKey);
        } catch (error) {
            // #348: retain the visible notice even without browser storage.
            console.warn('[service-version] Cannot persist service notice', error);
        }
    }

    function validId(/** @type {unknown} */ value) {
        return typeof value === 'string' && /^sha256:[0-9a-f]{64}$/.test(value);
    }

    async function check() {
        if (checking) return;
        checking = true;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 4000);
        try {
            const response = await fetch('/api/app-info', {cache: 'no-store', signal: controller.signal});
            if (response.status === 404) {
                restart = 'legacy';
            } else {
                if (!response.ok) throw new Error('service_version_unavailable');
                const info = await response.json();
                if (!info || typeof info !== 'object' || Array.isArray(info)) throw new Error('service_version_invalid');
                if (!('backend' in info)) restart = 'legacy';
                else {
                    const backend = info.backend;
                    if (!backend || backend.schema !== 1 || !validId(backend.loaded) || !validId(backend.source)
                            || typeof backend.restart_required !== 'boolean') throw new Error('service_version_invalid');
                    restart = backend.loaded !== backend.source || backend.restart_required ? 'stale' : null;
                }
            }
            persist();
            render();
        } catch (error) {
            // #348: an unavailable check is not evidence of changed code; keep prior evidence.
            console.warn('[service-version] Backend version unverified', error);
            render(true);
        } finally {
            clearTimeout(timeout);
            checking = false;
        }
    }

    function start() {
        render();
        void check();
        window.addEventListener('focus', () => { void check(); });
        setInterval(() => { void check(); }, 30000);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
})();
