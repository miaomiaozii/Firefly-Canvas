(function(){
    const KEY = 'studio_lang';
    const DEFAULT_LANG = 'zh';
    const dict = { zh: {}, en: {} };

    function lang(){
        return localStorage.getItem(KEY) || DEFAULT_LANG;
    }

    function normalizeEntry(key, entry){
        if(entry && typeof entry === 'object' && !Array.isArray(entry) && ('zh' in entry || 'en' in entry)){
            return {
                zh: entry.zh == null ? (entry.en == null ? key : String(entry.en)) : String(entry.zh),
                en: entry.en == null ? (entry.zh == null ? key : String(entry.zh)) : String(entry.en),
            };
        }
        const value = entry == null ? key : String(entry);
        return { zh: value, en: value };
    }

    function register(bundle){
        if(!bundle || typeof bundle !== 'object') return;
        if(bundle.zh || bundle.en){
            Object.assign(dict.zh, bundle.zh || {});
            Object.assign(dict.en, bundle.en || {});
            return;
        }
        Object.entries(bundle).forEach(([key, entry]) => {
            const normalized = normalizeEntry(key, entry);
            dict.zh[key] = normalized.zh;
            dict.en[key] = normalized.en;
        });
    }

    function t(key){
        const current = lang();
        return dict[current]?.[key] || dict[DEFAULT_LANG]?.[key] || key;
    }

    function apply(root=document){
        root.querySelectorAll('[data-i18n]').forEach(el => {
            // 跳过已被 JS 显式清空的元素：业务代码（如项目列表的状态栏）会在 fetch 完成后
            // 清空占位文本，若 i18n 在 DOMContentLoaded 阶段（可能晚于 fetch 返回）重写它，
            // 会出现"正在读取项目库…"残留竞态。
            if(el.textContent === '' && el.hasAttribute('data-i18n')) return;
            el.textContent = t(el.dataset.i18n);
        });
        root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            el.setAttribute('placeholder', t(el.dataset.i18nPlaceholder));
        });
        root.querySelectorAll('[data-i18n-title]').forEach(el => {
            el.setAttribute('title', t(el.dataset.i18nTitle));
        });
        root.documentElement?.setAttribute('lang', lang() === 'en' ? 'en' : 'zh-CN');
        window.dispatchEvent(new CustomEvent('studio-lang-change', { detail:{ lang:lang() } }));
    }

    function set(next){
        localStorage.setItem(KEY, next === 'en' ? 'en' : 'zh');
        apply();
    }

    function toggle(){
        set(lang() === 'en' ? 'zh' : 'en');
    }

    function entries(){
        return JSON.parse(JSON.stringify(dict));
    }

    window.StudioI18n = { t, apply, set, toggle, lang, register, entries };
    document.addEventListener('DOMContentLoaded', () => apply());
})();
