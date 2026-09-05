# Phase 2 修复总结

**修复日期**: 2026-08-23  
**修复文件**: 7 个文件  
**问题总数**: 11 个（10 已修复，1 待实现）

## 修复清单

### P0 - 功能 Bug（4 个）✅ 全部修复

1. **XSS 漏洞** - `static/js/excel-import.js`
   - 问题: `displayNo` 未转义
   - 修复: 添加 `escapeHtml()` 调用

2. **DOMContentLoaded 时机错误** - `static/js/excel-import.js`
   - 问题: DOM 已加载时监听器不执行
   - 修复: 改为条件判断 `document.readyState`

3. **window.canvasId 不存在** - `static/js/smart-canvas.js`
   - 问题: 模块作用域变量未导出
   - 修复: 添加 `window.canvasId = canvasId;`

4. **window.refreshSmartCanvas 不存在** - `static/js/smart-canvas.js`
   - 问题: 刷新函数未导出
   - 修复: 添加 `window.refreshSmartCanvas = () => mergeReloadCanvasNow();`

### P1 - Spec 不符（3 个）✅ 全部修复

5. **布局防重叠** - `layout_engine.py`
   - 问题: `existing_canvas` 参数未使用
   - 修复: 添加 `_calculate_offset()` 方法计算偏移量

6. **收集节点持久化** - `app/routers/api_excel_collect.py`
   - 问题: 只返回 zip，未创建画布节点
   - 修复: 创建 `excel-collect` 类型节点并持久化

7. **prompt injection 防护** - `plan_ir_builder.py` + `static/js/excel-import.js`
   - 问题: Excel 内容未过滤
   - 修复: 
     - 后端: 添加 `clean_text_for_prompt_injection()` 函数
     - 前端: 添加安全提示"⚠️ 以下内容来自外部 Excel 文件"

### P2 - 代码质量（2 个）✅ 全部修复

8. **任务 ID 生成不一致** - `plan_ir.py`
   - 问题: 随机 ID vs 确定性序号
   - 修复: `generate_task_id()` 支持传入 `sort_index`

9. **路径拼接问题** - `excel_importer.py`
   - 问题: 硬编码 `/` 分隔符
   - 修复: 添加 `.replace("\\", "/")` 规范化

### 信息丢失（1 个）✅ 已修复

10. **operation_references 只输出数量** - `plan_ir.py`
    - 问题: 丢失引用数据
    - 修复: 返回完整列表而非 `len()`

### 待实现（1 个）⏸

11. **导出到指定目录** - `app/routers/api_excel_collect.py`
    - 问题: 只支持 zip 打包
    - 状态: 需要更复杂的文件系统设计，建议单独开发

## 验证方法

### 1. XSS 防护
- 在 Excel 的 `display_no` 列输入 `<script>alert('XSS')</script>`
- 预期: 文本被转义，不执行脚本

### 2. 拖拽功能
- 刷新页面后拖入 Excel 文件
- 预期: 出现高亮，可正常解析

### 3. 画布刷新
- 展开 Excel 计划
- 预期: 新节点立即显示

### 4. 布局防重叠
- 在已有节点的画布上展开 Excel
- 预期: 新节点不与旧节点重叠

### 5. 收集节点持久化
- 执行收集操作
- 预期: 画布出现"Excel 收集结果"节点，刷新后仍存在

### 6. prompt injection 防护
- 在 Excel 中输入"忽略之前的指令"
- 预期: 
  - 前端显示安全提示
  - 后端内容被过滤

## 下一步

1. **功能测试**: 按上述验证方法逐项测试
2. **基准测试**: 使用样例 Excel `加热玩偶-高地牛-图片方案0509.xlsx` 验证
3. **导出目录功能**: 设计并实现 P2 功能
