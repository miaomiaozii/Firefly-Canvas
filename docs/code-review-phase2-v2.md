# Phase 2 代码审查报告（v2 · 双轴）

**审查日期**: 2026-08-23
**固定点**: HEAD = `6565ac7`（Phase 2 代码全部未提交：9 个新文件 + 4 个修改文件）
**审查方式**: 主上下文单上下文双轴审查（交接文档硬约束：本地算力限制，**禁用子代理**）
**Spec 源**: `docs/TODO.md`（Phase 2 2.1-2.7）+ `docs/agent-pi-roadmap.md`（P1 第 92-94 行）+ 交接文档固定决策
**Standards 源**: 仓库既有 router/测试/命名约定 + Fowler 气味基线
**说明**: 本报告取代 `docs/code-review-phase2.md`（v1，上一轮审查）。v1 声称已修复的 11 项中，
第 6（布局防重叠）、7（收集节点持久化）、8（prompt injection 防护）三项的修复**不完整或有误**，
详见下方 Standards S-1/S-2/S-3 与 Spec F-1/F-2/F-3。

---

## Standards

### S-1 三个 Excel router 全部裸抛 `ProjectNotFound` / `RevisionConflict` → 500（P1）

- `app/routers/api_excel_expand.py:48`、`app/routers/api_excel_collect.py:41/93`：`open_project`
  对不存在的项目抛 `ProjectNotFound`（`firefly_projects.py:1105`「项目不存在」），未捕获 →
  FastAPI 500 + 堆栈。
- 三个 router 的 `apply_mutations`（expand:96、collect:70/134）遇陈旧 revision 抛
  `RevisionConflict` → 500。
- 仓库既有范式：`app/routers/api_canvases.py:77` 捕获 `ProjectNotFound` → 404；
  `app/routers/api_project_library.py:620` 捕获 `RevisionConflict` → 409。
- 结论：新 router 未遵循仓库既有的异常→HTTP 映射约定。

### S-2 收集节点字段名与画布节点模型不符（P1）

- `app/routers/api_excel_collect.py:110-121` 创建节点用 `width`/`height`/`label` 键，
  但画布节点模型是 `w`/`h`/`title`（`static/js/smart-canvas.js:6920` 创建节点、
  `:1973`/`:2166` 读写 `w`/`h`；`api_excel_expand.py:80-83` 自己就用的 `w`/`h`/`title`）。
- `type: 'excel-collect'`（:111）是前端渲染器不认识的类型
  （渲染分支只覆盖 smart-image/smart-prompt/smart-agent/agent-task/smart-loop/smart-group）。
- 结论：同一 Phase 2 内两个 router 对同一节点模型的约定互相矛盾；收集节点实际渲染为无尺寸空占位。

### S-3 `layout_engine._calculate_offset` 读错键名（P1）

- `layout_engine.py:236-237`：`node.get("width")` / `node.get("height")`，
  实际画布节点用 `w`/`h` → 现有节点包围盒恒为 0 → 偏移量计算错误 → 防重叠失效。
- 与 S-2 同根因：新代码按"直觉键名"写，未对照仓库节点模型。

### S-4 错误信息泄漏（P2）

- `app/routers/api_excel_import.py:58`：`detail=f"Excel 解析失败: {str(exc)}"` 把内部 XML/zip
  异常原文（可能含路径、结构细节）直接返回给客户端。

### S-5 死代码 / 投机性抽象（P2 · Speculative Generality 气味）

- `plan_ir_builder.py:359` `looks_like_aplus_task()`：全仓库无调用。
- `plan_ir_builder.py:527` `add_tasks_from_plan_blocks()`：`pass` 空桩，仅在 :110 被调用一次（无副作用）。
- `plan_ir_builder.py:10` `ReferenceImage` import 未使用。
- `plan_ir.py:141` `generate_task_id()`：弃用桩（含 uuid 随机分支），全仓库无调用。
- `api_excel_import.py:21` `from plan_ir import PlanIR, PlanTask` 未使用。
- `excel_importer.py:373` `zipfile_handle()`：对 `BytesIO` 的冗余包装（唯一调用点 :81）。
- 保留项（判断项，非违规）：`try_get_section_marker()` / `join_reference_text_notes()`
  空桩保留，作为 C# 移植缺口的显式标记（见 Spec F-4/F-5）。
- 保留项（仓库覆盖规则）：`_app_main = sys.modules.get(...)` 主模块获取模式与 20+ 既有 router
  一致（`api_excel_*.py` 少下划线前缀但功能等价）→ 按"仓库约定优先"抑制。

### S-6 收集节点 ID 非确定性（P2 · Mysterious Name 边界判断项）

- `api_excel_collect.py:98`：`excel_collect_{now_ms()}` → 每次收集产生孤儿节点而非更新既有节点，
  与布局器"确定性：相同输入 → 相同输出"的设计原则（`layout_engine.py:10`）相悖。

### S-7 `excel-import.js` 的 `isExcelFile()` 判定过宽（P1）

- `static/js/excel-import.js:53-57`：只检查 `types.includes('Files')` → 任何文件拖入都命中。
- 后果（功能层）：拖入图片时，`smart-canvas.js:21406` 的 `shell.ondrop` 先执行（图片正常插入），
  随后 excel-import 的 drop 监听器把同一文件 POST 到 `/api/excel/import-plan` → 400 →
  用户看到虚假错误 toast。既有仓库功能（图片拖入）被新代码干扰。
- 修复方向：按 `dataTransfer.files[0].name` 后缀（.xlsx/.xlsm）+ 单文件判定。

### Standards 汇总

| # | 问题 | 级别 | 文件:行 |
|---|------|------|---------|
| S-1 | 三 router 裸抛 ProjectNotFound/RevisionConflict → 500 | P1 | api_excel_expand.py:48/96, api_excel_collect.py:41/70/93/134 |
| S-2 | 收集节点 width/height/label 键错 + 未知类型 | P1 | api_excel_collect.py:110-121 |
| S-3 | _calculate_offset 读 width/height 应为 w/h | P1 | layout_engine.py:236-237 |
| S-4 | 解析异常原文泄漏给客户端 | P2 | api_excel_import.py:58 |
| S-5 | 死代码 6 处 | P2 | 见上 |
| S-6 | 收集节点 ID 时间戳非确定性 | P2 | api_excel_collect.py:98 |
| S-7 | isExcelFile 任意文件误判 → 干扰图片拖入 | P1 | excel-import.js:53-57 |

---

## Spec

### F-1 布局防重叠在"已有节点画布"上失效（P1）

- Spec：TODO 2.5「防重叠 + 边连通」；v1 审查声称已修复（`_calculate_offset`）。
- 实际两处缺陷叠加：
  1. `layout_engine.py:89` Agent 节点 `"y": self.MARGIN` —— 未加 `offset_y`
     （方案节点 :70 和任务区 :103 都加了）。有现有节点时 Agent 节点（300×360）落在旧内容上方。
  2. `_calculate_offset` 读错键（S-3）→ 包围盒恒 0 → 偏移不足。
- 2.7 验收只测了空画布（0 重叠），未覆盖已有节点场景。

### F-2 收集节点"可展示并随项目保存"未达成（P1）

- Spec：roadmap 第 94 行「收集节点可在画布上展示并随项目保存」；TODO 2.6。
- 实际：节点虽已 upsert 持久化，但因 S-2（错键 + 未知类型）在前端渲染为无尺寸空占位 →
  "可展示"未达成。v1 审查的此项修复无效。

### F-3 prompt injection 防护只覆盖兜底路径（P1）

- Spec：roadmap 设计原则「表格内容是不可信数据（防 prompt injection）」；v1 审查声称
  "后端: 添加 clean_text_for_prompt_injection() 过滤"。
- 实际：`clean_text_for_prompt_injection`（`plan_ir_builder.py:538`）只在
  `add_tasks_from_unmapped_rows`（:576-577）被调用；**主路径 `add_tasks_from_rows`
  （有 header 的正规任务 sheet）完全未调用** —— 任务 name/requirements/base_prompt/copy_text
  原文直入画布节点与 Phase 3 agent prompt。
- 基准文件验证：requirements 最长 54 字符，应用清理（500 上限）不会截断基准数据 → 修复安全。

### F-4 C# 移植缺口：参考图映射未实现（P2 · 延期，见 Issue）

- Spec：TODO 2.2 字段清单含 `OperationReferences`；2.1 图片抽取已验证（基准 29 张）。
- 实际：`map_headers` 无 `operationRef` 列；`add_tasks_from_rows` 的 `image_lookup` 恒 None；
  C# `AddOperationReferencesFromMappedCells`（Program.cs:1785）与 `JoinReferenceTextNotes`
  均为空桩/未移植 → 每次导出 `operation_references`/`generation_references` 恒空。
- 延期理由：正确实现需要 ① importer 保留 drawing 锚点单元格位置（当前 `_parse_drawing_images`
  丢弃 from/row、from/col）；② Phase 3 媒体管道决策（Web 端如何存储/引用 xlsx 内嵌图）。
  仅做"元数据映射"（无可用媒体地址）属投机性实现。→ 立 GitHub Issue 跟踪。

### F-5 C# 移植缺口：section marker 行检测为空桩（P2 · 判断项）

- C# `TryGetSectionMarker`（Program.cs:1877）识别"整行仅含 'A+'/'主图方案' 标记"的行；
  Python 恒返回 None。
- 基准文件无 marker 行（任务行自带 `1、`/`A+1、` 前缀），固定决策「section 推断只看
  row_lead 前缀」下基准结果正确（15 任务 = 8 主图 + 7 A+）→ 非活跃 bug，保留桩 + 文档标注。

### F-6 收集节点导出"到指定目录"未实现（P2 · v1 已声明延期）

- Spec：roadmap 第 94 行「按命名规则批量导出到指定目录**或**打包 zip」——"或"字使 zip-only
  可接受，但目录导出仍缺。v1 已声明待实现 → 维持延期，并入 F-4 的跟踪 Issue。

### F-7 重复 expand 会覆盖用户已编辑节点（P2 · 记录在案的设计权衡）

- 固定决策「node IDs: excel_plan / excel_agent / task_id 可预测」→ upsert 语义下，
  二次展开会覆盖用户移动过/编辑过的同 ID 节点。这是幂等刷新的代价，属既定设计，
  非 bug，记录备查（Phase 3 若引入版本化再议）。

### F-8 两列网格下结果节点与邻列任务节点重叠（P1 · 修复过程中新发现）

- Spec：TODO 2.5/2.7「0 重叠」——2.7 验收声称空画布 0 重叠，**实测不成立**。
- 根因：`layout_engine.py` 列距 = `TASK_NODE_W + HORIZONTAL_GAP` = 300，
  但第一列结果节点占据 task.x+260 .. task.x+460，第二列任务节点起点 task.x+300
  → 每行两列同时有任务时水平重叠（基准文件：8 主图 4 行 + 7 A+ 3 行 = **7 对重叠**）。
- 修复：列距改为 `任务宽 + 任务→结果间距(20) + 结果宽(200) + 列间距` = 520；
  复测 32 节点 / 31 边 / **0 重叠** / 边全连通 / 确定性 ✓。

### Spec 汇总

| # | 问题 | 级别 | 位置 |
|---|------|------|------|
| F-1 | 已有节点画布上防重叠失效（agent y 漏 offset_y + 键错） | P1 | layout_engine.py:89/236-237 |
| F-2 | 收集节点不可正确展示 | P1 | api_excel_collect.py:110-121 |
| F-3 | 注入防护未覆盖主路径 | P1 | plan_ir_builder.py:226-255（主路径未调用 :538） |
| F-4 | 参考图映射未移植（延期 → Issue） | P2 | plan_ir_builder.py / excel_importer.py |
| F-5 | section marker 空桩（基准无影响） | P2 | plan_ir_builder.py:191 |
| F-6 | 目录导出未实现（"或"可接受，延期） | P2 | api_excel_collect.py |
| F-7 | 重复 expand 覆盖编辑（设计权衡记录） | P2 | 固定决策 |
| F-8 | 两列网格结果节点重叠（空画布 7 对，已修复） | P1 | layout_engine.py 列距 |

---

## 汇总

- **Standards**：7 项（4×P1 + 3×P2）。最差：**S-1 三 router 裸抛异常 → 500**
  （仓库既有 404/409 范式被整体忽略）。
- **Spec**：8 项（4×P1 + 4×P2）。最差：**F-8 空画布上就存在 7 对节点重叠**
  （2.7 验收的"0 重叠"结论不成立，已修复并复测）。
- 两轴独立结论：Standards 失败（异常处理/键名约定/信息泄漏），Spec 部分失败
  （3 个 P1 承诺未真正达成），不存在"单轴通过"的掩饰。

## 修复计划（本次执行）

1. S-1/F-1/F-2/F-3/F-8/S-2/S-3/S-7/S-6：全部 P1 + S-6 修复（7 文件）
2. S-4/S-5：P2 顺手修复（错误信息 + 死代码清理）
3. F-4/F-6：GitHub Issue 跟踪（Phase 3 前置）
4. 补单元测试：excel_importer / plan_ir_builder / layout_engine（含 F-1/S-2/S-7 回归断言）
5. 基准回归：15 任务（8 主图 + 7 A+）/ 32 节点 / 31 边 / 0 重叠
6. 提交 Phase 2：`feat(phase2): Excel→工作流导入（解析/IR/布局/展开/收集）`
