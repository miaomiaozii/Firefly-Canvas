# 导航索引（Navigation Index）

仓库内可搜索的领域导航入口：把六条发布关键旅程与核心领域术语连接到稳定公共符号、最相关测试与权威 ADR。
维护者和智能体按本文的推荐调查顺序定位代码，不必通读大模块。

## 推荐调查顺序（按此顺序，最后才读整个大模块）

1. **领域文档**：先读根目录 `CONTEXT.md`（领域术语与 _Avoid_ 同义词表）与 `docs/agents/domain.md`（术语规则：issue、测试名、代码设计必须使用 CONTEXT.md 术语）。
2. **ADR**：`docs/adr/` 中与当前工作有关的架构决策；与已有 ADR 冲突必须显式指出并说明重新讨论理由，不得静默覆盖（见 `docs/agents/domain.md`「ADR 冲突」）。
3. **工作树状态**：`git status` / `git diff`。共享脏工作树时，用精确 hunk 隔离本票改动，绝不 reset/clean/checkout。
4. **符号搜索**：从本索引的「稳定公共符号」列出发 grep 符号名，或用 `Select-String -Path <dir>\* -Pattern ...` 限定子目录搜索（仓库根有名为 NUL 的文件，根级 grep 会报错）。
5. **邻近调用链**：`read` 符号周围上下文，沿调用链读，不整文件通读。
6. **领域测试**：跑/读本索引指出的最相关测试（测试名即行为契约；先看红/绿）。
7. 以上都失效时才通读整个大模块。

## 六条发布关键旅程

| 旅程 | 浏览器证据入口 | 证据校验器 | 最相关测试 | 稳定公共符号 | 权威 ADR | 已知不变量 |
| --- | --- | --- | --- | --- | --- | --- |
| 1. 项目往返持久化（T05 #25） | `tests/core_surface_browser.py:763` `run_project_roundtrip` | `tools/quality_gate.py:1996` `validate_native_browser_artifacts`（roundtrip 断言） | `tests/test_project_store.py`、`tests/test_project_api.py`、`tests/test_project_entry_ui.py` | `firefly_projects.py` `ProjectStore`（`create_project`:483、`open_project`:1067、`apply_mutations`:2797、`import_media`:1094）；API 在 `app/routers/api_project_library.py` | 0003、0010、0011 | 重开身份按 `asset_id`（内容 sha256）判定，不靠文件名/URL；revision 单调递增；复制/导出/回收/恢复互不污染；未引用但用户导入的媒体仍归项目 |
| 2. 图片无损组织（T06 #26） | `tests/core_surface_browser.py:160` `run_image_organization` | `tools/quality_gate.py:1996`（image_organization 断言） | `tests/test_core_surface_browser.py`、`tests/test_native_browser.py` | 同旅程 1；媒体字节走 `firefly_projects.py` `import_media`:1094 | 0001、0007、0014 | import/drag/group/resize/ungroup 不重编码、不覆盖、不降质原件（`media_before == media_after` 内容 sha256）；源宽高比保留（容差 0.03）；ungroup 后位置尺寸可解释；五种窗口 `controls_reachable` 且无页面级水平溢出 |
| 3. 精确 provider 路由（T03/T04 #23/#24） | 合同驱动，无独立浏览器旅程 | `tests/provider_contract.py`（`load_provider_contract`、`normalize_captured_request`、`ProviderContractCapture`） | `tests/test_provider_contract_template.py`、`tests/test_provider_contract_registry.py`、`tests/test_provider_contract_families.py`；hard-deny 证明 `tests/test_quality_gate.py:683` | `firefly_generation.py:378` `route_image_generation`；`app/core.py:4244` `route_openai_image_request`；`app/services/generation.py` `generate_ai_image`（provider span wrapper） | 0006、0004 | 路由只选执行模型、不改用户参数；tier alias 发现与使用保留用户比例/尺寸/有序重复 reference；不兼容组合在 executor 执行前 400 `code=no_compatible_image_model`；每族 fixture 记录来源 URL+检索日期+final_request_golden；fixture base_url 用 `.invalid` 域名不可路由 |
| 4. 提示词节点连接输入（T11 #31） | `tests/project_linked_generation_native_browser.py` | `tools/project_linked_generation_evidence.py` `validate_project_linked_generation_artifacts` | `tests/test_project_image_generation.py:1223-1267`（公开端点拒绝项目媒体/跨项目 404） | `app/routers/api_project_library.py` `create_project_image_task`/`run_project_image_task`/`project_image_references`；`app/services/media.py:51` `reference_to_data_url`；`app/services/conversation.py:119` `gemini_reference_part` | 0002、0012 | 连接文本+项目媒体 save/close/reopen 后仍关联同一节点；媒体属于当前项目的校验发生在 provider 边界前；引用转有界 data URL（max_size=1536）；出站请求保留连接文本/参数/有序重复 reference；无项目作用域 handle 继续拒绝 |
| 5. 任务并发/取消/重试/下载（T09 #29） | `tests/generation_task_native_browser.py` | gate 的 `browser-tracer`/`browser-harness-contract` 步骤 | `tests/test_project_history.py`、`tests/test_project_video_generation.py` | `firefly_projects.py` `begin_media_generation`:2398、`_end_media_generation`:2464、`fail_media_generation`:2543、`cancel_media_generation`:2560、`complete_media_generation`:2626；任务路由在 `app/routers/api_project_library.py`（CANVAS_TASKS 内存+durable 恢复） | 0010 | 任务状态卡是活动投影：服务端持久化记录与本地活动任务投影按身份合并，不得整体覆盖瞬态；幂等重放命中 202、不匹配 409 |
| 6. 生成-删除-恢复全生命周期（T08/T10/T15/T16 #28/#30/#35/#36） | `tests/generation_lifecycle_browser.py` + cleanup 证据在 `tests/core_surface_browser.py`（cleanup-preview/cleanup-confirm） | `tools/generation_lifecycle_evidence.py`、`tools/complete_generation_lifecycle_evidence.py`、`tools/quality_gate.py:1996` | `tests/test_generation_log_reuse_ui.py`、`tests/test_history_deletion_isolation.py`、`tests/test_project_store.py`（cleanup 组） | `firefly_projects_deletion.py` `delete_generated_original`:571、`restore_deleted_original`:1080（及 `_project_wide` 变体）、`_ensure_output_occurrence_ids`:53；`firefly_projects.py` `delete_history_record`:2164、`preview_project_cleanup`:1416、`execute_project_cleanup`:1725、`_is_generated_original`:1862 | 0005（已被 0014 取代）、0013、0014 | 生成输出项拥有独立身份（ADR-0014），删除单项不级联下游；清理只移除失败/临时来源且可证明不被引用的孤立媒体，须 `revision+ownership_proof` 双重证明、`CleanupDrift` 时拒绝执行且不撤销；项目范围删除进回收区、七天可恢复 |

## 核心领域条目

### 生成身份（generation identity）

- 稳定符号：`firefly_projects_deletion.py` `_ensure_output_occurrence_ids`:53；`firefly_projects.py` `delete_history_record`:2164、`begin_media_generation`:2398、`complete_media_generation`:2626。
- 最相关测试：`tests/test_history_deletion_isolation.py`（删无关记录保留 ≥2 活动任务卡）、`tests/test_generation_log_reuse_ui.py`、`tests/test_project_store.py`。
- 权威 ADR：0014「生成输出项拥有独立身份」（取代 0005）。
- 不变量：任务状态卡、生成记录、生成任务、结果节点、生成原件各自独立；pendingTasks 按 `task_id` 身份合并，客户端不能捏造或整体覆盖；删除历史记录不触碰节点投影。

### 项目媒体所有权（project media ownership）

- 稳定符号：`firefly_projects.py` `import_media`:1094（内容寻址，`asset_id=sha256(content)`，重复内容返回既有记录）、`media_record`:1216；服务端所有权校验 `app/routers/api_project_library.py` `create_project_image_task`（逐 `reference_asset_id` 验 `media_record`）。
- 最相关测试：`tests/test_project_image_generation.py:1223-1267`（跨项目 asset 404、公开端点拒绝项目媒体 handle）、`tests/test_project_store.py`。
- 权威 ADR：0003「项目是自包含的数据归属边界」。
- 不变量：节点只存 `asset_id`（`_invalid_project_media_value` 拒绝 url/src/path/preview_url 等外部键）；不属于当前项目的 asset_id 一律拒绝且不触发出站请求；媒体字节不落日志（`firefly_diagnostics.py` 脱敏）。

### 删除与恢复域（deletion & recovery domain）

- 稳定符号：`firefly_projects_deletion.py` `ProjectDeletionMixin`（`delete_generated_original`:571、`delete_generated_original_project_wide`、`restore_deleted_original`:1080、`restore_deleted_original_project_wide`、`purge_deleted_media`:1266、`cleanup_expired_deleted_media`、`_safe_project_path`:1391、`_ensure_output_occurrence_ids`:53）。
- 最相关测试：`tests/test_history_deletion_isolation.py`、`tests/test_project_store.py`（cleanup/deletion 组）。
- 权威 ADR：0005（已被 0014 取代）、0013、0014。
- 不变量：删除/恢复按稳定输出项身份（`output_id`+`output_order`+`deletion_id`）fail-closed，缺失/错配拒绝整个事务；项目范围删除/恢复是独立显式操作；仍被引用的原件留活动区，逻辑删除项进七天可恢复记录；模块级 helper/错误/常量经 `_store` 引用以保持测试 patch 契约。

### Provider contract

- 稳定符号：`tests/provider_contract.py`（`load_provider_contract`、`normalize_captured_request`、`ProviderContractCapture`）；fixtures `tests/fixtures/provider_contracts/openai-comfly-banana-image.json`、`supported-image-request-families.json`。
- 最相关测试：`tests/test_provider_contract_template.py`、`tests/test_provider_contract_registry.py`、`tests/test_provider_contract_families.py`。
- 权威 ADR：0006「模型路由不得修改用户生成参数」。
- 不变量：出站前捕获最终 payload 与 final_request_golden 逐字节契约相等（无静默改写）；fixture 记录 provider/model family/请求响应 shape/primary doc URL+检索日期；hard-deny 由 domain gate 的 sitecustomize 证明（非 loopback 与真实 provider 可执行文件全阻断）。

### UI 完整性

- 稳定符号：`tools/quality_gate.py:175` `NATIVE_BROWSER_VIEWPORTS`（1920x1080/1440x900/1280x720/1024x768/800x600）；`tests/ui_integrity_native_browser.py`、`tools/ui_integrity_evidence.py`、`tests/test_ui_integrity_contract.py`、`tests/test_ui_integrity_evidence.py`。
- 最相关测试：上述四个模块 + `tests/native_browser.py`（可信原生输入 harness）。
- 权威 ADR：0001（智能画布产品核心）、0013（历史抽屉）。
- 不变量：五种窗口关键控件可达、无页面级水平溢出；原生输入记录全部 `trusted=true` 且带坐标/owner 链；失败证据自包含且 passed 证据不得残留 first_failure。

### 质量门（quality gates）

- 稳定符号：`tools/quality_gate.py` `MODE_BUDGETS`:92（fast 30s / domain 120s / trial 300s / release 900s）、`MODE_STEPS`:133、`BROWSER_EVIDENCE_STEP_NAMES`:181、`CRITICAL_JOURNEY_STEPS`:198、`browser_step_spec`:1265、`execution_groups`:1347（浏览器证据步并行分组）、`SUPPORT_MATRIX`:1384、`RETRY_POLICY`:1406、`build_severity_findings`:1416（S0/S1/S2/S3 频率分层）、`verdict_reasons`:1478、`provider_contract_summary`:1507、`GUARD_SOURCE`（sitecustomize 非 loopback 拒绝+provider 可执行阻断）、`validate_native_browser_artifacts`:1996、`main`:2383。
- 最相关测试：`tests/test_quality_gate.py`（含 hard-deny、retry 漂白、预算超限判罚契约、severity findings、S2 显式接受 flag、Edge 控制面白名单、并行分组）。#39 起 release 模式额外跑 `tests/edge_compatibility_browser.py`（Windows stable Edge，校验器 `tools/edge_compatibility_evidence.py`）。
- 权威依据：MISSION.md（声明预算）与 issue #21/#39。
- 不变量：证据目录必须在仓库外；任何 guard 阻断事件或 retry 证据事件即判失败；首次失败永久保留不被重试覆盖；预算超限 exit 124 且不能靠超时/跳过/重试制造绿色；dirty worktree 保留并在报告记录。

### 结构化诊断日志（T17 #37）

- 稳定符号：`firefly_diagnostics.py`（`STABLE_LEVELS` 11 个事件名、`emit`、`clear`、`read_events`）。
- 最相关测试：`tests/test_diagnostics.py`。
- 不变量：prompt→prompt_sha256、API key/媒体字节/完整本地路径脱敏；仅本地、100MB/7 天轮转、用户可清空、永不自动上传（模块不导入任何 HTTP 客户端）。

## ADR 索引

| 编号 | 标题 | 一句话 |
| --- | --- | --- |
| 0001 | 以智能画布作为产品核心 | 核心画布是产品演进中心，经典画布仅兼容迁移 |
| 0002 | 对话节点与提示词节点严格分离 | 消息不自动成为生图提示词，提取不建连线 |
| 0003 | 项目是自包含的数据归属边界 | 项目内容共同保存/复制/导出/回收/恢复 |
| 0004 | 保留在线多媒体生成并移除本地运行时 | 生成经远程 provider（HTTP 或 CLI 桥接） |
| 0005 | 删除原图在项目范围内生效 | **已被 ADR-0014 取代** |
| 0006 | 模型路由不得修改用户生成参数 | 路由只选执行模型，参数逐字节保留 |
| 0007 | 局部图片编辑由本地画布完成合成 | 局部修改/局部保留由本地合成拼回 |
| 0008 | Photoshop 返回结果始终创建新节点 | 不覆盖原图，保留来源关系 |
| 0009 | 萤火画布使用独立发布渠道 | 发布节奏与渠道独立 |
| 0010 | 以增量传输和 JSON 快照保存项目 | revision+快照持久化模型 |
| 0011 | 使用 .fly 作为项目交换文件 | 单文件导出/备份/分享，导入解包为新项目 |
| 0012 | Agent 使用独立节点 | 工具调用在画布上产生可观察任务或结果 |
| 0013 | 生成历史使用避让缩略图的底部抽屉 | 可折叠调高的历史抽屉 |
| 0014 | 生成输出项拥有独立身份 | 输出项/记录/任务/原件独立，删除单项不级联 |

## 注释规范

生产代码注释只用于解释以下六类内容，其余意图由命名、测试名与 ADR 表达：

1. **invariant**：必须长期成立的不变量及其破坏后果（例：`apply_mutations` 中 pendingTasks 服务端所有权合并）。
2. **race**：并发/时序竞争条件及其防护（例：`_lock` 保护、`RevisionConflict` 语义）。
3. **compatibility**：兼容性保留理由（例：`begin_image_generation` 委托 `begin_media_generation` 的向后兼容说明）。
4. **external side effect**：外部可观察副作用（例：`execute_project_cleanup` 不可撤销）。
5. **paid boundary**：付费/计费边界（例：真实 provider 调用点）。
6. **非显然 escaped defect cause**：曾经逃逸的缺陷根因（例：`.button` 的 `display:inline-flex` 覆盖 UA `[hidden]` 导致的 CSS 显隐缺陷）。

不在上述范围的一律不写注释；发现无注释难以理解时优先改名或补测试，而不是加注释。

## 模块拆分触发条件与拆分票原则

满足任一条件时，创建**独立 behavior-preserving 拆分票**（不混入功能改动）：

- 单文件 ≥ 约 2000 行，且内部存在清晰、可测试的领域边界（如存储核心按媒体/生成/清理/恢复子域切分）；
- 同一领域职责被 ≥ 3 个调用方以复制粘贴方式重复；
- 稳定公共符号跨领域混用导致本索引无法给出单一入口。

拆分票原则：只移动代码、不改行为、不新增功能、不顺手重构；先有红绿测试保护（领域测试先绿，拆分后仍绿）；每一步可独立 review。生产模块的重构/拆分不得搭车在本类文档票或其他功能票中完成。

## 本票边界声明

本票只新增本文档（`docs/agents/navigation-index.md`），未改动任何生产模块；自动化验证不访问真实或付费 provider。
