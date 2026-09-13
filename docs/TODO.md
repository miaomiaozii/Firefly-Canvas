# 萤火画布 · 推进完成 Todo

> 依据：2026-08-22 前后端系统走查 + `docs/agent-pi-roadmap.md`（P0-P4 总路线图）+ H39 恢复交接文档。
> 状态图例：`[ ]` 待办 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻塞/需用户决策
> 验收标准（MISSION.md）：好用、美观、无大 bug；S0/S1 必须修，发布门全绿。

## 背景判断（一句话版）
- 画布**基础盘（P0）扎实**，真正漏风的是**上层功能**：P1 只搭壳、P2/P3 未动。
- 后端 `app/core.py` 是上帝模块需拆分；前端单文件维持现状（W2 已评估不拆）。
- 有几个**真实 S0/S1 bug** 要先止血，其中一个是**会导致用户数据丢失**的服务端缺陷。

---

## Phase 1 · 止血 + 质量门恢复（最高优先级）

### 1.1 [x] 修复空状态 autosave 数据丢失（S0 · 最高优先）✅
- **问题**：前端标签页加载/重连失败时本地节点为空 → autosave 定时器触发 → 空画布 PATCH 覆盖服务端数据，导致用户节点丢失（H39 事故根因）。
- **已实施（双层防护）**：
  - **服务端（根本防护）** `firefly_projects.py` `apply_mutations`：加 `force_empty` 参数，mutation 后若节点全空且未声明 `force_empty` → 抛 `RevisionConflict` → 前端已有 409 rebase 逻辑自动恢复用户节点。
  - **前端** `smart-canvas.js`：加 `canvasLoadReady` 标志，`send()` 里 `force_empty = canvasLoadReady && 本地空`；`saveCanvas` 里加载未完成时空状态直接拒存。
  - 路由 `CanvasMutationBatch` 加 `force_empty` 字段（`app/routers/api_project_library.py` + `app/core.py` 两处）。
- **测试**：新增 `tests/test_empty_canvas_guard.py`（4 用例）；修正 3 个合法清空场景的既有测试补 `force_empty=True`。相关 80 tests 全绿。
- 来源：H39 恢复交接文档第二节。

### 1.5 [x] 修复 3 个预存契约测试失败（S1 测试基线全绿）✅
- 全量测试初跑 733 tests 有 3 个**预存失败**（代码演进后契约测试未同步，非 1.1 引起，git HEAD 就挂）。已修复，**全量 733 tests → 0 失败**。
- **T1** `test_canvas_assets_use_the_current_ui_cache_version`：版本同步测试写死了 `?v=2026.08.13.10`，但 `tools/restart-firefly.ps1` 的 `Bump-AssetVersions` 每次重启都重刷版本号（**有意的 cache-bust，必须保留**）。改为版本无关断言：只校验 css/js 都有 `?v=` 且两者相等（Bump 设同值），不锁死具体值。
- **T2** `test_history_card_has_one_image_overlay_action`：`selectedIndex` 重构为 IIFE（支持多输出内单选特定 output），更新断言串。
- **T3** `test_generation_has_persistent_pending_feedback...`：`generationPlaceholderHtml` 签名扩展 `completedImgs`/`candidatePhases`（渐进完成+候选阶段），更新断言串。

### 1.2 [x] 排查项目名编码损坏根源（S0 数据安全）✅
- **排查结论**（2026-08-23，已排查）：
  - 当前代码**无编码 bug**，全链路 UTF-8 安全：`_write_json_atomic`(ensure_ascii=False)、`set_canvas_meta` mutation、.fly 导出/导入（zip 内 UTF-8）均验证通过。
  - 全数据扫描（D:\Firefly-Canvas-Projects 所有 JSON）：无 U+FFFD、无罕见生僻字、无 decode 失败。
  - 全代码库无可疑编码调用（无 gbk/cp936/latin-1/getfilesystemencoding 滥用）。
  - 实证复现测试：create→rename→flush→export→import 全链路 UTF-8 无损坏。
- **根源判定**：历史乱码来自旧代码库（本仓库独立导入前）或一次性事故；当前 8 个项目名全部正常（H39重力理疗垫、H35理疗垫等），无需恢复。
- **防回归**：加 `test_utf8_roundtrip_preserves_chinese_project_names`（tests/test_project_store.py），覆盖 create→set_canvas_meta→flush→export→import 全链路字节级验证。

### 1.3 [x] 修复质量门浏览器测试失败（S1 发布阻塞）✅
- **已修复**（commit 93c6e7a）：
  - **拖拽起点动态探针**：`core_surface_browser.py` 多图节点拖拽改用几何探测（避开浮动菜单遮挡），5 视口全过。
  - **group resize 证据**：改测 `node.w/h`（模型层）而非固定缩略格 DOM（resize handler 真正修改的是节点边界）。
  - **double_click 适配**：`native_browser.py` 支持 click_count=2；`native_input_evidence.py` + `quality_gate.py` journey order/pointer 验证适配 dblclick。
  - **domain budget** 120→180s（实测 129.6s，原 120s 不足）。
- **验证**：domain 质量门 6/6 步骤通过（129.6s，commit 93c6e7a）。

### 1.4 [ ] 清理重复定义 / 死代码（S2 维护性）
- **2026-09-10 复核更新**：请求模型重复定义不止 1 处——`app/core.py` 有 **65 个**类被 `app/routers/*.py` 本地同名类 shadow（clean-room W1 拆分时机械复制未清 core 副本；api_project_library 14 个、api_asset_library 10 个、api_local_assets 7 个等），core 版全部是死代码。收敛工作暂缓，待专门排期（先清完再评估 core.py 拆分收益）。
- 收敛 `main.py:1677` 的巨型 import（单行 17,468 字符 / ~675 符号 re-export core；main.py 仍 1731 行）—— 随 core.py 拆分一并处理。

---

## Phase 2 · P1 无 LLM 主链路（Excel→工作流，核心增量）

> 路线图 P1。当前只有聊天附件用的 `read_xlsx_attachment`，**没有**完整链路。

### 2.1 [x] Excel 解析（OpenXML 读 .xlsx，无 COM 依赖）✅
- **已实施**：`excel_importer.py`（纯 stdlib，zipfile + ElementTree）
- 功能：shared strings / sheet 关系 / 表格图片抽取
- 命名空间坑已解决：r:id 用 `{ns_rel}id` 全名
- **测试结果**：基准 Excel 13 sheets，29 张图片 ✓

### 2.2 [x] Plan IR（结构化任务清单）✅
- **已实施**：
  - `plan_ir.py`：PlanIR / PlanTask / ReferenceImage 数据模型（22 字段）
  - `plan_ir_builder.py`：任务提取器（移植 C# AddTasksFromRows）
  - 字段完整：TaskId / Section / SourceRow / DisplayNo / OutputType / Requirements 等
- **测试结果**：15 任务 = 8 主图 + 7 A+ ✓
- **原则**：任务数量/依赖永远来自 Excel 解析器，LLM 只润色 + 歧义追问，禁止 LLM 编造任务。

### 2.3 [x] Agent 节点拖入 Excel 文件节点 + 计划预览卡片
- **已实施**：
  - 前端模块 `static/js/excel-import.js`（ExcelImport 对象）
  - 拖入检测：#app-root 监听 dragover/dragleave/drop
  - 计划预览卡片：显示统计（任务数/主图/A+）+ 任务列表
  - 项目 ID 获取：_getProjectId() 方法（canvasId → URL 参数 → 路径）
  - expandPlan()：调用 /api/excel/expand-plan（传入 project_id + plan）
  - 成功/错误提示：toast 通知
  - 版本号：v=20260823.000002

### 2.4 [x] 原子展开（一次事务创建全部节点/边/布局）✅
- **已实施**：
  - API 端点 `POST /api/excel/expand-plan`（app/routers/api_excel_expand.py）
  - 接收 `project_id` + `plan`（Plan IR）
  - 调用 `open_project` 获取画布 + `base_revision`
  - 使用 `DeterministicLayout.generate_canvas_operations()` 生成节点/边操作
  - 原子性应用：`apply_mutations`（`upsert_node` + `upsert_edge`）+ `flush`
  - **测试结果**：32 节点（1 方案 + 1 Agent + 15 任务 + 15 结果），31 边
- **禁止逐节点 PATCH**：所有节点/边在单次 `apply_mutations` 调用中创建。

### 2.5 [x] 确定性布局器
- **已实施**：`layout_engine.py`（DeterministicLayout 类）
- **三分区布局**：
  - 左区：方案节点（smart-prompt，280×200）
  - 中区：Agent 节点（smart-agent，300×360）
  - 右区：任务节点（agent-task 240×160 + smart-image 200×160），按 section 分组（2 列网格）
- **布局参数**：MARGIN=80, ZONE_GAP=100, HORIZONTAL_GAP=60, VERTICAL_GAP=30
- **防重叠**：确定性计算坐标，相同输入 → 相同布局
- **边连通**：Plan→Agent→Task→Result 全连接

### 2.6 [x] 人工打标 + 一键收集
- **已实施**：
  - `POST /api/excel/tag-node`（app/routers/api_excel_collect.py）
    - 参数：`project_id`, `node_id`, `curated`, `tags`
    - 更新节点 `curated` + `tags` 字段，持久化到画布
  - `POST /api/excel/collect-nodes`
    - 参数：`project_id`, `node_ids[]`, `include_unselected`
    - 收集 curated 节点（或所有节点），导出 `nodes.json` zip 文件
- **测试结果**：打标成功，收集返回 762 字节 zip ✓

### 2.7 [x] 验收
- **验收结果**：
  - 基准 Excel：13 sheets ✓
  - 任务提取：15 任务 = 8 主图 + 7 A+ ✓
  - 原子展开：32 节点（1 方案 + 1 Agent + 15 任务 + 15 结果），31 边 ✓
  - 布局验证：0 重叠 ✓
  - 边连通：Plan→Agent→Task→Result 全连接 ✓
- **断网可用**：所有功能纯本地实现（stdlib + FastAPI），无需外部依赖 ✓

---

## Phase 3 · P2 pi 基座接入（✅ 决策已拍板，ADR 0019）

### 3.1 [x] 前置决策（2026-09-13 已拍板，见 `docs/adr/0019-pi-sidecar-runtime-deep-integration.md`）
- 会话载体：**Node sidecar（独立进程，跨刷新恢复），一步到位** ✅
- 控制台形态：**节点式（resize 手柄）** ✅
- 附加约束：接口/扩展点先行、插拔架构、兼容旧 plan-first Agent、pi 日志访问、pi 维护/自修复工具、`app/core.py` 重组并入 P2、Excel 导出暴露为 pi 工具。

### 3.2 [ ] LLM provider 配置 UI + key 安全存储（浏览器加密 + 后端落盘）

### 3.3 [ ] Node sidecar 运行时（启动/守护/崩溃自愈/僵尸回收、版本一致性校验、Windows 分发含 Node、锁定 vendor pi-agent-core 版本）

### 3.4 [ ] FastAPI 桥接（WebSocket/SSE 会话流 `{project_id,agent_node_id}` 独立 session）+ 工具注册/插拔架构

### 3.5 [ ] pi 日志访问工具 `inspect_app_logs`（结构化过滤，不暴露凭据）+ 维护工具（孤儿资产清理、完整性校验）

### 3.6 [ ] 工具白名单（inspect_canvas_context / import_excel_plan / propose_image_plan / materialize_image_plan / ... / export_collection；与旧 plan-first Agent 共享权限层）

### 3.7 [ ] `app/core.py` 结构化重组（请求模型/异常/multipart 分家，53 个 import 源一次迁移）

### 3.8 [ ] 节点式控制台（Agent 节点 resize、keyed DOM 复用、复制节点=独立会话）

---

## Phase 4 · P3 协作/托管模式（远期，暂不排期）
- 协作模式 / 托管模式 / 视觉检查 / 硬性预算上限 / 变更日志回滚。

## Phase 5 · P4 pi 自我修复（远期，不进当前工作）
- 结构化诊断接口 / 受控工具白名单 / 回滚机制。

---

## 架构治理（穿插进行，不阻塞功能）
- [ ] 拆分 `app/core.py` 上帝模块（2026-09-10 复核：6261 行 / 74 个顶层类）——按 provider/领域归并，收敛 main.py 巨型 import。注意：`app/services/_base.py` 的 `_svc._main('...')` 动态绑定机制是巨型 import 的根因，拆分需连带设计。
- [ ] 前端 `smart-canvas.js` 内部函数分组注释 + 逐步收敛模块级共享声明（2026-09-10 实测：378 个模块级声明，其中 let 185 个；不物理拆分，W2 已评估）。

---

## 当前工作树提醒（勿乱动）
- 2026-09-10 起：此前 WIP（#84-#89 UI 批次 + 星标持久化）已由用户全部提交（7bfd80f / c58bc0b / 0f9f6cf）；工作树保持干净，新改动随各自 issue 提交。
- `backend.log` 勿提交；`restart.bat` / `tools/restart-firefly.ps1` / `tools/recover_h39_canvas.py` 入库前先问用户。
- 改磁盘 canvas.json 后必须重启服务器（ProjectStore 内存缓存）。
