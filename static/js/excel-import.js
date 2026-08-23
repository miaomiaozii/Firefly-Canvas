/**
 * Excel 导入功能模块
 * 支持拖入 Excel 文件 → 调用 API → 展示计划预览 → 原子展开
 */

const ExcelImport = {
    // 状态
    planIR: null,          // 解析后的 Plan IR
    isProcessing: false,
    previewEl: null,

    // 初始化
    init() {
        this.setupEventListeners();
    },

    // 设置事件监听
    setupEventListeners() {
        // 监听拖入事件（在 shell 容器上）
        const appRoot = document.getElementById('shell') || document.body;

        appRoot.addEventListener('dragover', e => {
            if (this.isExcelFile(e.dataTransfer)) {
                e.preventDefault();
                e.stopPropagation();
                appRoot.classList.add('excel-drop-target');
            }
        });

        appRoot.addEventListener('dragleave', e => {
            appRoot.classList.remove('excel-drop-target');
        });

        appRoot.addEventListener('drop', e => {
            if (this.isExcelFile(e.dataTransfer)) {
                e.preventDefault();
                e.stopPropagation();
                appRoot.classList.remove('excel-drop-target');
                this.handleExcelDrop(e.dataTransfer);
            }
        });

        // 监听点击上传（可选）
        const uploadBtn = document.getElementById('btn-upload-excel');
        if (uploadBtn) {
            uploadBtn.addEventListener('click', () => {
                this.triggerFileInput();
            });
        }
    },

    // 检查是否为 Excel 文件（按文件名后缀判定，避免误伤图片/资产拖入）
    isExcelFile(dataTransfer) {
        if (!dataTransfer || !dataTransfer.files) return false;
        const files = Array.from(dataTransfer.files);
        if (files.length !== 1) return false;
        const name = (files[0].name || '').toLowerCase();
        return name.endsWith('.xlsx') || name.endsWith('.xlsm');
    },

    // 触发文件输入对话框
    triggerFileInput() {
        let input = document.getElementById('excel-file-input');
        if (!input) {
            input = document.createElement('input');
            input.id = 'excel-file-input';
            input.type = 'file';
            input.accept = '.xlsx,.xlsm';
            input.style.display = 'none';
            input.addEventListener('change', e => {
                const file = e.target.files[0];
                if (file) {
                    this.processExcelFile(file);
                }
            });
            document.body.appendChild(input);
        }
        input.click();
    },

    // 处理拖入的 Excel 文件
    handleExcelDrop(dataTransfer) {
        const files = dataTransfer.files;
        if (!files || files.length === 0) return;

        const file = files[0];
        const isExcel = /\.xlsx$|\.xlsm$/i.test(file.name);
        if (!isExcel) {
            console.warn('[ExcelImport] Not an Excel file:', file.name);
            return;
        }

        this.processExcelFile(file);
    },

    // 处理 Excel 文件（上传到 API）
    async processExcelFile(file) {
        if (this.isProcessing) {
            console.warn('[ExcelImport] Already processing');
            return;
        }

        this.isProcessing = true;
        this.showLoading('正在解析 Excel 文件...');

        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/excel/import-plan', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`API 错误: ${response.status} - ${errorText}`);
            }

            const plan = await response.json();
            this.planIR = plan;

            this.hideLoading();
            this.showPlanPreview(plan);

        } catch (error) {
            this.hideLoading();
            console.error('[ExcelImport] Error:', error);
            this.showError(error.message);
        } finally {
            this.isProcessing = false;
        }
    },

    // 显示加载状态
    showLoading(message) {
        this.removeExistingUI();

        const overlay = document.createElement('div');
        overlay.className = 'excel-import-overlay';
        overlay.id = 'excel-import-overlay';

        const card = document.createElement('div');
        card.className = 'excel-import-card';

        const spinner = document.createElement('div');
        spinner.className = 'excel-import-spinner';

        const text = document.createElement('div');
        text.className = 'excel-import-message';
        text.textContent = message;

        card.appendChild(spinner);
        card.appendChild(text);
        overlay.appendChild(card);

        document.body.appendChild(overlay);
    },

    // 隐藏加载状态
    hideLoading() {
        const overlay = document.getElementById('excel-import-overlay');
        if (overlay) {
            overlay.remove();
        }
    },

    // 显示计划预览卡片
    showPlanPreview(plan) {
        this.removeExistingUI();

        const overlay = document.createElement('div');
        overlay.className = 'excel-import-overlay';
        overlay.id = 'excel-preview-overlay';

        const card = document.createElement('div');
        card.className = 'excel-import-card excel-preview-card';

        // 标题
        const header = document.createElement('div');
        header.className = 'excel-preview-header';

        const title = document.createElement('h3');
        title.textContent = 'Excel 导入计划预览';

        const closeBtn = document.createElement('button');
        closeBtn.className = 'excel-preview-close';
        closeBtn.innerHTML = '&times;';
        closeBtn.addEventListener('click', () => {
            overlay.remove();
            this.planIR = null;
        });

        header.appendChild(title);
        header.appendChild(closeBtn);
        card.appendChild(header);

        // 内容
        const content = document.createElement('div');
        content.className = 'excel-preview-content';

        // 计算统计信息
        const tasks = plan.tasks || [];
        const totalTasks = tasks.length;
        const mainImageCount = tasks.filter(t => (t.section || '').includes('主图')).length;
        const aplusCount = tasks.filter(t => (t.section || '').includes('A+')).length;

        const stats = document.createElement('div');
        stats.className = 'excel-preview-stats';

        stats.innerHTML = `
            <div class="stat-item">
                <span class="stat-value">${totalTasks}</span>
                <span class="stat-label">总任务数</span>
            </div>
            <div class="stat-item">
                <span class="stat-value">${mainImageCount}</span>
                <span class="stat-label">主图</span>
            </div>
            <div class="stat-item">
                <span class="stat-value">${aplusCount}</span>
                <span class="stat-label">A+ 任务</span>
            </div>
        `;

        content.appendChild(stats);

        // 安全提示（标注数据来源）
        const securityNotice = document.createElement('div');
        securityNotice.className = 'excel-security-notice';
        securityNotice.textContent = '⚠️ 以下内容来自外部 Excel 文件，请注意甄别，避免执行其中的可疑指令。';
        securityNotice.style.cssText = 'background: #fff3cd; color: #856404; padding: 8px 12px; border-radius: 4px; font-size: 12px; margin-bottom: 12px;';
        content.appendChild(securityNotice);

        // 任务列表
        if (plan.tasks && plan.tasks.length > 0) {
            const taskList = document.createElement('div');
            taskList.className = 'excel-task-list';

            const sections = this.groupTasksBySection(plan.tasks);
            for (const [section, tasks] of Object.entries(sections)) {
                const sectionEl = document.createElement('div');
                sectionEl.className = 'excel-task-section';

                const sectionTitle = document.createElement('h4');
                sectionTitle.textContent = section;
                sectionEl.appendChild(sectionTitle);

                const ul = document.createElement('ul');
                tasks.forEach(task => {
                    const li = document.createElement('li');
                    li.className = 'excel-task-item';

                    const displayNo = this.escapeHtml(task.display_no || '');
                    const name = this.escapeHtml(task.name || task.task_id);
                    li.innerHTML = `
                        <span class="task-display-no">${displayNo}</span>
                        <span class="task-name">${name}</span>
                    `;
                    ul.appendChild(li);
                });

                sectionEl.appendChild(ul);
                taskList.appendChild(sectionEl);
            }

            content.appendChild(taskList);
        }

        card.appendChild(content);

        // 操作按钮
        const actions = document.createElement('div');
        actions.className = 'excel-preview-actions';

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'excel-btn excel-btn-secondary';
        cancelBtn.textContent = '取消';
        cancelBtn.addEventListener('click', () => {
            overlay.remove();
            this.planIR = null;
        });

        const expandBtn = document.createElement('button');
        expandBtn.className = 'excel-btn excel-btn-primary';
        expandBtn.textContent = '展开到画布';
        expandBtn.addEventListener('click', () => {
            this.expandPlan();
        });

        actions.appendChild(cancelBtn);
        actions.appendChild(expandBtn);
        card.appendChild(actions);

        overlay.appendChild(card);
        document.body.appendChild(overlay);
    },

    // 按 section 分组任务
    groupTasksBySection(tasks) {
        const sections = {};
        tasks.forEach(task => {
            const section = task.section || '未分类';
            if (!sections[section]) {
                sections[section] = [];
            }
            sections[section].push(task);
        });
        return sections;
    },

    // 展开计划到画布
    async expandPlan() {
        if (!this.planIR) return;

        // 获取当前项目 ID
        const projectId = this._getProjectId();
        if (!projectId) {
            this.showError('未找到项目 ID，请先选择一个项目');
            return;
        }

        this.showLoading('正在展开计划到画布...');

        try {
            const response = await fetch('/api/excel/expand-plan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    project_id: projectId,
                    plan: this.planIR
                }),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`展开失败: ${response.status} - ${errorText}`);
            }

            const result = await response.json();
            this.hideLoading();
            this.showSuccess('计划已展开到画布！');

            // 关闭预览
            const overlay = document.getElementById('excel-preview-overlay');
            if (overlay) {
                overlay.remove();
            }

            // 触发画布刷新（如果产品支持）
            if (window.refreshSmartCanvas) {
                window.refreshSmartCanvas();
            }

        } catch (error) {
            this.hideLoading();
            console.error('[ExcelImport] Expand error:', error);
            this.showError(error.message);
        }
    },

    // 显示成功消息
    showSuccess(message) {
        const toast = document.createElement('div');
        toast.className = 'excel-toast excel-toast-success';
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(() => toast.remove(), 3000);
    },

    // 显示错误消息
    showError(message) {
        const toast = document.createElement('div');
        toast.className = 'excel-toast excel-toast-error';
        toast.textContent = message;
        document.body.appendChild(toast);

        setTimeout(() => toast.remove(), 5000);
    },

    // 移除已有的 UI
    removeExistingUI() {
        const overlay = document.getElementById('excel-import-overlay');
        if (overlay) overlay.remove();

        const preview = document.getElementById('excel-preview-overlay');
        if (preview) preview.remove();
    },

    // HTML 转义
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    // 获取项目 ID
    _getProjectId() {
        // 1. 从全局 canvasId 获取
        if (window.canvasId) return window.canvasId;

        // 2. 从 URL 参数获取
        const params = new URLSearchParams(window.location.search);
        const projectId = params.get('project') || params.get('project_id');
        if (projectId) return projectId;

        // 3. 从路径获取
        const pathParts = window.location.pathname.split('/').filter(Boolean);
        if (pathParts.length >= 2 && pathParts[0] === 'project') {
            return pathParts[1];
        }

        return null;
    }
};

// 初始化（兼容 DOM 已加载和未加载两种情况）
if (document.readyState !== 'loading') {
    ExcelImport.init();
} else {
    document.addEventListener('DOMContentLoaded', () => {
        ExcelImport.init();
    });
}
