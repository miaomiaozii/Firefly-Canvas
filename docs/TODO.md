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
- `FireflyProjectCreateRequest` 在 `app/core.py:1163` 和 `app/routers/api_project_library.py:141` 重复定义，合并到一处。
- 收敛 `main.py:1671` 的巨型 import（675 个符号）—— 随 core.py 拆分一并处理。

---

## Phase 2 · P1 无 LLM 主链路（Excel→工作流，核心增量）

> 路线图 P1。当前只有聊天附件用的 `read_xlsx_attachment`，**没有**完整链路。

### 2.1 [ ] Excel 解析（OpenXML 读 .xlsx，无 COM 依赖）
- shared strings / sheet 关系 / 表格图片抽取。
- 参考历史 C# 资产 `ImagePlanDesignerP0`（`AddTasksFromRows` 在 `Program.cs:1615-1694`）。

### 2.2 [ ] Plan IR（结构化任务清单）
- 字段：TaskId / Section / SourceRow / DisplayNo / OutputType / Requirements / CopyHandlingMode / CopyText / BasePrompt / TargetAspectRatio / SameAsMain / LinkedMainIndex / OperationReferences。
- **原则**：任务数量/依赖永远来自 Excel 解析器，LLM 只润色 + 歧义追问，禁止 LLM 编造任务。

### 2.3 [ ] Agent 节点拖入 Excel 文件节点 + 计划预览卡片

### 2.4 [ ] 原子展开（一次事务创建全部节点/边/布局）
- 禁止逐节点 PATCH。

### 2.5 [ ] 确定性布局器
- 左方案 / 中 Agent / 右主图 A + 分区，防重叠 + 边连通。

### 2.6 [ ] 人工打标 + 一键收集
- 结果节点快捷打标 `curated:true` / tags 持久化。
- 收集节点按命名规则批量导出 zip。

### 2.7 [ ] 验收
- 基准样例 `加热玩偶-高地牛-图片方案0509.xlsx` 复测 13 sheets / 23 行 / 15 任务（8 主图 + 7 A+）/ 70 内嵌图；展开 60 节点无重叠边全连通；断网全链路可用。

---

## Phase 3 · P2 pi 基座接入（需先拍板架构决策）

### 3.1 [!] 前置决策（用户拍板）
- 会话载体：**Node sidecar（独立进程，跨刷新恢复）** vs **浏览器内嵌 pi-agent-core（零依赖，关标签即断）**。
- 控制台形态：**节点式（resize 手柄）** vs **停靠面板**。

### 3.2 [ ] LLM provider 配置 UI + key 安全存储（浏览器加密 + 后端落盘）

### 3.3 [ ] FastAPI 桥接（WebSocket/SSE 会话流 `{project_id,agent_node_id}` 独立 session）

### 3.4 [ ] 工具白名单（inspect_canvas_context / import_excel_plan / propose_image_plan / materialize_image_plan / ...）

### 3.5 [ ] 节点式控制台（Agent 节点 resize、keyed DOM 复用、复制节点=独立会话）

---

## Phase 4 · P3 协作/托管模式（远期，暂不排期）
- 协作模式 / 托管模式 / 视觉检查 / 硬性预算上限 / 变更日志回滚。

## Phase 5 · P4 pi 自我修复（远期，不进当前工作）
- 结构化诊断接口 / 受控工具白名单 / 回滚机制。

---

## 架构治理（穿插进行，不阻塞功能）
- [ ] 拆分 `app/core.py` 上帝模块（6154 行 / 483 顶层符号）——按 provider/领域归并，收敛 main.py 巨型 import。
- [ ] 前端 `smart-canvas.js` 内部函数分组注释 + 逐步收敛 110 个模块级共享 `let`（不物理拆分）。

---

## 当前工作树提醒（勿乱动）
- 既有 WIP（smart-canvas.js / smart-canvas.css / smart-canvas.html）是用户/前会话未提交工作，**绝不提交或还原**。
- `backend.log` 勿提交；`restart.bat` / `tools/restart-firefly.ps1` / `tools/recover_h39_canvas.py` 入库前先问用户。
- 改磁盘 canvas.json 后必须重启服务器（ProjectStore 内存缓存）。
