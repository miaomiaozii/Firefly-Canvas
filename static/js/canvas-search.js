// 跨项目搜索（fullnode 票 F / #324, ADR-0026）
// 顶栏按钮开合面板；输入防抖 250ms 调 GET /api/search/projects；
// 结果按项目分组。当前项目命中=就地 focusAndSelectNode；其他项目=跳转其画布。
(function () {
    'use strict';
    if (window.__canvasSearchMounted) return;
    window.__canvasSearchMounted = true;

    const DEBOUNCE_MS = 250;

    function currentProjectId() {
        try {
            const params = new URLSearchParams(window.location.search);
            return (
                (window.canvasState && window.canvasState.projectId) ||
                params.get('project') ||
                params.get('project_id') ||
                ''
            );
        } catch (_e) {
            return '';
        }
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function projectUrl(projectId) {
        return `/static/smart-canvas.html?id=${encodeURIComponent(projectId)}&project=${encodeURIComponent(projectId)}&project_mode=1`;
    }

    function mount() {
        const toggle = document.getElementById('smartSearchToggle');
        const panel = document.getElementById('canvasSearchPanel');
        const input = document.getElementById('canvasSearchInput');
        const results = document.getElementById('canvasSearchResults');
        const meta = document.getElementById('canvasSearchMeta');
        const closeBtn = document.getElementById('canvasSearchClose');
        if (!toggle || !panel || !input || !results) return;

        let timer = 0;
        let requestId = 0;

        function setOpen(open) {
            panel.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) input.focus();
        }

        toggle.addEventListener('click', () => setOpen(panel.hidden));
        closeBtn && closeBtn.addEventListener('click', () => setOpen(false));
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !panel.hidden) {
                event.stopPropagation();
                setOpen(false);
            }
        });

        function renderHits(data) {
            const here = currentProjectId();
            if (!data || !Array.isArray(data.projects) || data.projects.length === 0) {
                results.innerHTML = '<div class="canvas-search-empty">无结果</div>';
                meta.hidden = true;
                return;
            }
            const parts = data.projects.map((project) => {
                const hits = (project.hits || []).map((hit) => {
                    const isHere = project.project_id === here;
                    const action = isHere && hit.node_id
                        ? `<button type="button" class="canvas-search-hit-open" data-node="${escapeHtml(hit.node_id)}">定位</button>`
                        : `<a class="canvas-search-hit-open" href="${escapeHtml(projectUrl(project.project_id))}">打开</a>`;
                    return `<div class="canvas-search-hit" data-project="${escapeHtml(project.project_id)}">`
                        + `<span class="canvas-search-hit-kind">${escapeHtml(hit.kind || '')}·${escapeHtml(hit.field || '')}</span>`
                        + `<span class="canvas-search-hit-snippet">${escapeHtml(hit.snippet || '')}</span>`
                        + action
                        + '</div>';
                }).join('');
                const current = project.project_id === here ? ' <em>(当前项目)</em>' : '';
                return `<div class="canvas-search-project"><div class="canvas-search-project-name" data-project-link="${escapeHtml(project.project_id)}">${escapeHtml(project.name || project.project_id)}${current}</div>${hits}</div>`;
            });
            results.innerHTML = parts.join('');
            const flags = [];
            if (data.truncated) flags.push('结果已达上限（截断）');
            if (Array.isArray(data.skipped_large) && data.skipped_large.length) flags.push(`已跳过超大画布 ${data.skipped_large.length} 个`);
            meta.hidden = flags.length === 0;
            meta.textContent = flags.join('；');
        }

        async function runSearch() {
            const query = input.value.trim();
            if (!query) { results.innerHTML = ''; meta.hidden = true; return; }
            const id = ++requestId;
            try {
                const res = await fetch(`/api/search/projects?q=${encodeURIComponent(query)}`);
                if (id !== requestId) return;
                if (!res.ok) { results.innerHTML = `<div class="canvas-search-empty">搜索失败（HTTP ${res.status}）</div>`; return; }
                renderHits(await res.json());
            } catch (_e) {
                if (id === requestId) results.innerHTML = '<div class="canvas-search-empty">搜索失败（网络）</div>';
            }
        }

        input.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(runSearch, DEBOUNCE_MS);
        });

        results.addEventListener('click', (event) => {
            const openBtn = event.target.closest('.canvas-search-hit-open[data-node]');
            if (openBtn) {
                event.preventDefault();
                if (typeof window.focusAndSelectNode === 'function') window.focusAndSelectNode(openBtn.dataset.node);
                setOpen(false);
                return;
            }
            const projectLink = event.target.closest('[data-project-link]');
            if (projectLink && !event.target.closest('.canvas-search-hit-open')) {
                const pid = projectLink.getAttribute('data-project-link');
                const here = currentProjectId();
                if (pid && pid !== here) window.location.href = projectUrl(pid);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }
})();
