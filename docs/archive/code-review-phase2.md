# Phase 2 代码审查报告

**审查日期**: 2026-08-23  
**审查范围**: 9 个新文件 + 3 个修改文件  
**固定点**: HEAD = 6565ac7  

## 一、Standards 审查

### 1. XSS 漏洞（高危）✅ 已修复
**文件**: `static/js/excel-import.js:253-256`

**问题**: `displayNo` 直接使用模板字符串插入，未调用 `escapeHtml()`。Excel 表格内容是**不可信数据**（ROADMAP 设计原则 #4），若 `display_no` 含 HTML 标签会被渲染，造成 XSS 攻击。

**修复**: 添加 `escapeHtml()` 转义
```javascript
const displayNo = this.escapeHtml(task.display_no || '');
const name = this.escapeHtml(task.name || task.task_id);
li.innerHTML = `
    <span class="task-display-no">${displayNo}</span>
    <span class="task-name">${name}</span>
`;
```

---

### 2. DOMContentLoaded 时机错误（功能 Bug）✅ 已修复
**文件**: `static/js/excel-import.js:420-422`

**问题**: `excel-import.js` 在 `smart-canvas.html` 末尾加载（第 481 行）。此时 DOM 已解析完毕，DOMContentLoaded 事件可能已触发或未触发。若已触发，监听器永不执行，拖拽监听未绑定，**功能完全失效**。

**修复**: 改为条件判断
```javascript
// 初始化（兼容 DOM 已加载和未加载两种情况）
if (document.readyState !== 'loading') {
    ExcelImport.init();
} else {
    document.addEventListener('DOMContentLoaded', () => {
        ExcelImport.init();
    });
}
```

---

### 3. `window.canvasId` 不存在（功能 Bug）✅ 已修复
**文件**: `static/js/excel-import.js:394`

**问题**: `smart-canvas.js` 中 `canvasId` 是模块作用域变量（第 2 行 `const canvasId = params.get('id') || '';`），**未挂到 window 上**。`window.canvasId` 永远为 undefined。

**修复**: 在 `smart-canvas.js` 顶部添加导出
```javascript
const params = new URLSearchParams(location.search);
const canvasId = params.get('id') || '';
const sourceProjectId = params.get('project') || '';
window.canvasId = canvasId;
```

---

### 4. `window.refreshSmartCanvas` 不存在（功能 Bug）✅ 已修复
**文件**: `static/js/excel-import.js:344-345`

**问题**: `smart-canvas.js` 中无 `window.refreshSmartCanvas` 导出。展开后画布不刷新，**用户看不到新节点**。

**修复**: 在 `smart-canvas.js` 导出刷新函数（第 5942 行）
```javascript
// 导出刷新函数供外部模块调用
window.refreshSmartCanvas = () => mergeReloadCanvasNow();
```

---

### 5. `#app-root` 元素不存在 ✅ 已修复
**文件**: `static/js/excel-import.js:17`

**问题**: `smart-canvas.html` 中无 `#app-root` 元素。回退到 `document.body`，但 body 上已有拖拽监听（smart-canvas.js 第 21400 行 `shell.ondragover = ...`），**可能冲突**。

**修复**: 改为使用 `#shell` 元素
```javascript
// 监听拖入事件（在 shell 容器上）
const appRoot = document.getElementById('shell') || document.body;
```

---

### 6. zip 路径拼接使用硬编码 `/`（潜在 Bug）✅ 已修复
**文件**: `excel_importer.py:234`

**问题**: 在 Windows 上，`zipfile.Path` 可能使用 `\\` 分隔符。硬编码 `/` 可能导致路径匹配失败，图片抽取不完整。

**修复**: 添加路径规范化
```python
drawing_name = drawing_path.replace("\\", "/").split("/")[-1]  # drawing1.xml
rels_path = f"xl/drawings/_rels/{drawing_name}.rels"
```

---

## 二、Spec 审查

### 1. `existing_canvas` 参数未使用（Spec 不符）✅ 已修复
**文件**: `layout_engine.py:58`

**ROADMAP 要求**: "左方案 / 中 Agent / 右主图 A + 分区，**防重叠** + 边连通"

**问题**: 布局器未检查现有画布节点，若画布已有内容，新节点**可能与旧节点重叠**。

**修复**: 添加 `_calculate_offset()` 方法，计算偏移量
```python
def _calculate_offset(self, existing_canvas: Dict[str, Any] = None) -> Tuple[float, float]:
    """计算布局偏移量，避免与现有节点重叠。"""
    if not existing_canvas or not existing_canvas.get("nodes"):
        return 0.0, 0.0
    
    nodes = existing_canvas["nodes"]
    if not nodes:
        return 0.0, 0.0
    
    # 计算现有节点的最大边界
    max_x = max_y = 0.0
    for node in nodes:
        x = float(node.get("x", 0))
        y = float(node.get("y", 0))
        w = float(node.get("width", 0))
        h = float(node.get("height", 0))
        max_x = max(max_x, x + w)
        max_y = max(max_y, y + h)
    
    # 在现有节点边界外放置新内容
    offset_x = max(max_x + self.ZONE_GAP, self.MARGIN)
    offset_y = max(max_y + self.ZONE_GAP, self.MARGIN)
    
    return offset_x, offset_y
```

---

### 2. 收集节点未在画布持久化（Spec 不符）✅ 已修复
**文件**: `api_excel_collect.py:58-85`

**ROADMAP 要求**: 
- "收集节点可在画布上展示并**随项目保存**"
- "打标数据随项目/Fly 导出迁移"

**问题**: `collect_nodes` 只返回 zip 文件，**未在画布创建"收集节点"**。ROADMAP 要求收集节点是画布上的特殊节点，随项目保存，而非临时下载。

**修复**: 创建收集节点并持久化到画布
```python
# 创建收集节点（持久化到画布）
collect_node_id = f"excel_collect_{int(_app_main.now_ms())}"
collect_node = {
    'id': collect_node_id,
    'type': 'excel-collect',
    'x': 0,
    'y': 0,
    'width': 300,
    'height': 200,
    'label': 'Excel 收集结果',
    'extra_data': {
        'node_ids': node_ids,
        'collected_at': _app_main.now_ms(),
        'include_unselected': payload.include_unselected
    }
}

# 应用 mutation
mutations = [
    {
        'op': 'upsert_node',
        'node': collect_node
    }
]

await asyncio.to_thread(
    _app_main.project_store_call,
    'apply_mutations',
    project_id,
    mutations,
    base_revision=base_revision
)
await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)
```

---

### 3. 不支持"导出到指定目录"（Spec 不符）⏸ 待实现
**文件**: `api_excel_collect.py:68`

**ROADMAP 要求**: "按命名规则批量**导出到指定目录**或打包 zip"

**问题**: `collect_nodes` 只支持 zip 打包，不支持指定导出目录。用户无法在服务器端批量导出节点到磁盘。

**状态**: 此功能需要更复杂的实现（文件系统设计、权限控制等），建议单独开发。

---

### 4. `plan_ir.py` 中 `operation_references` 只输出数量（信息丢失）✅ 已修复
**文件**: `plan_ir.py:58-59`

**ROADMAP 要求**: Plan IR 需包含完整引用信息，供前端渲染参考图。

**问题**: 只输出数量，**丢失实际引用数据**。前端无法显示参考图缩略图或操作细节。

**修复**: 返回完整列表
```python
"operation_references": [
    {
        "asset_id": r.asset_id,
        "source": r.source,
        "url": r.url
    }
    for r in t.operation_references
],
"generation_references": [
    {
        "asset_id": r.asset_id,
        "source": r.source,
        "url": r.url
    }
    for r in t.generation_references
],
```

---

### 5. 任务 ID 生成不一致 ✅ 已修复
**文件**: `plan_ir.py:95-96` vs `plan_ir_builder.py:186`

**问题**: 两套 ID 生成方式不一致。`plan_ir_builder` 使用确定性序号，但 `plan_ir.generate_task_id()` 生成随机 ID。若前端或后端误调用 `generate_task_id()`，ID 格式会不一致。

**修复**: 修改 `generate_task_id()` 函数，支持传入 `sort_index`
```python
def generate_task_id(sort_index: int = None) -> str:
    """生成任务 ID（excel_N 格式，N 为序号）。"""
    if sort_index is None:
        import warnings
        warnings.warn(
            "generate_task_id() 已弃用，请传入 sort_index 参数",
            DeprecationWarning,
            stacklevel=2
        )
        return f"excel_{uuid.uuid4().hex[:8]}"
    return f"excel_{sort_index}"
```

---

## 三、安全审查

### 1. Excel 表格内容未做 prompt injection 防护 ✅ 已修复
**文件**: `api_excel_import.py`, `plan_ir_builder.py`

**ROADMAP 要求**: 表格内容是**不可信数据**（防 prompt injection），需在 UI 中标注来源。

**修复**: 

#### 前端：添加安全提示
```javascript
// 安全提示（标注数据来源）
const securityNotice = document.createElement('div');
securityNotice.className = 'excel-security-notice';
securityNotice.textContent = '⚠️ 以下内容来自外部 Excel 文件，请注意甄别，避免执行其中的可疑指令。';
securityNotice.style.cssText = 'background: #fff3cd; color: #856404; padding: 8px 12px; border-radius: 4px; font-size: 12px; margin-bottom: 12px;';
content.appendChild(securityNotice);
```

#### 后端：添加内容过滤
```python
def clean_text_for_prompt_injection(text: str, max_length: int = 500) -> str:
    """清理文本以防止 prompt injection。"""
    if not text:
        return ""
    # 限制长度
    text = text[:max_length]
    # 移除控制字符（保留换行、制表符、空格）
    text = ''.join(c for c in text if c.isprintable() or c in '\n\t\r')
    # 移除可疑的模式（简化版，可根据需要扩展）
    suspicious_patterns = [
        r'(?i)(ignore|忽略).{0,50}(instruction|指令|指令)',
        r'(?i)(new|new)\s*instruction',
        r'(?i)(ignore|忽略)\s*all\s*(previous|prior|上述)',
    ]
    for pattern in suspicious_patterns:
        text = re.sub(pattern, '[filtered]', text)
    return text
```

---

## 四、汇总

| 类别 | 严重度 | 数量 | 状态 |
|------|--------|------|------|
| 功能 Bug（XSS、DOMContentLoaded、window 变量、#app-root） | 高 | 4 | ✅ 全部修复 |
| Spec 不符（布局重叠、收集节点、导出目录、信息丢失） | 中 | 4 | 3 已修复，1 待实现 |
| 安全问题（prompt injection） | 中 | 1 | ✅ 已修复 |
| 代码质量（ID 不一致、路径拼接） | 低 | 2 | ✅ 全部修复 |

## 五、修复总结

### 已完成（P0 + P1 大部分）
1. ✅ XSS 漏洞 - 添加 `escapeHtml()` 转义
2. ✅ DOMContentLoaded 时机 - 改为条件判断
3. ✅ window.canvasId 不存在 - smart-canvas.js 导出
4. ✅ window.refreshSmartCanvas 不存在 - smart-canvas.js 导出
5. ✅ #app-root 不存在 - 改为 #shell
6. ✅ 布局防重叠 - 添加 `_calculate_offset()`
7. ✅ 收集节点持久化 - 创建画布节点
8. ✅ prompt injection 防护 - 前端提示 + 后端过滤
9. ✅ 任务 ID 生成 - 统一为 `excel_{sort_index}`
10. ✅ 路径拼接 - 添加路径规范化

### 待实现（P2）
- 导出到指定目录 - 需要更复杂的文件系统设计
