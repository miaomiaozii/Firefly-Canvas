# 画布删除机制审读与修改方案

- 日期：2026-08-31
- 范围：`static/js/smart-canvas.js`（23245 行）、`app/routers/api_project_library.py`、`firefly_projects.py` / `firefly_projects_deletion.py` / `firefly_projects_cleanup.py`、`static/js/project-list.js`、ADR 0005（superseded）/0014/0015
- 性质：只读审计 + 修改方案。本文不改任何代码；落地按 §5 分期执行。

## 0. 结论摘要（TL;DR)

1. 系统里同一个「删除」动作实际对应 **6 类入口、16 条路径、4 种媒体结局**（保留/进回收区/硬删/静默系统回收），且 undo 能力三层不一（画布 undo / 回收区恢复 / 不可逆）。用户无法从按钮预测后果——这是「不满意」的结构性根源。
2. 存在 **3 个 P0 前端 bug**（批量删除、未使用清理、回收区恢复把 409 冲突误计为成功，产生虚假成功 toast）。
3. 存在 **1 个 P1 后端 bug**：T9 批修复的 `output_id` 归属判定只落在 `delete_project_media_asset`，`delete_generated_original_project_wide` 与 `delete_history_record_with_media` 仍会把「带 run 戳的拖入图节点」误判为生成节点（写错墓碑类型 + 错误重置节点状态，可能破坏生成中节点）。
4. 后端存在**第 5 种结局：静默孤儿回收**（生成失败/取消/回滚后 `delete_media_if_unreferenced` 直接硬删，不进回收区、无提示、用户无从知晓）。
5. 建议分三期：P0 修复（半天）→ 语义统一（含 4 个需拍板决策点，1-2 天）→ 结构收敛（1 天）。
6. 【撤销/重做联合审计补充】用户要求「删除/撤销/重做不能孤立地看」，补审后发现 **新 P0**：undo/redo 可复活已硬删媒体的悬空引用 → 服务端 `upsert_node` 校验抛 `InvalidMutation`（项目媒体不存在）→ 画布进入**持续保存失败**死锁（P0-4）。另外确认 D2 拍板：节点 × 按钮 = 删图片、留节点和内部提示词，**维持现状**。

## 2.1 撤销/重做体系（联合审计）

机制（smart-canvas.js）：
- `UNDO_LIMIT=40`（:309）；`undoStack`/`redoStack`（:310-311）；`snapshotForUndo()`（:355-362）只拍 **nodes+connections+selection**（不含 logs/历史）
- `pushUndo(extra)`（:363-371）：push + 40 条 FIFO 挤出 + 清 redo（redo 应用期间豁免）
- `performUndo()`（:372-407）：普通快照 → 压 redo + 本地恢复 + render + scheduleSave；**token 分支**（`projectOriginalDelete`）→ `restoreSmartProjectOriginalUndo`（走服务端 `POST /original/restore`），成功后**不压 redo**（即 redo 无法「重删原图」，有意设计）；`permanentUndoFailure` → 弃令牌 continue；其他错误 → 令牌压回 + toast
- `performRedo()`（:409-430）：`if(snap.projectOriginalDelete) continue;`（跳过 token）；应用前 `pushUndo`（redoPushing 豁免清栈）
- `capturePendingUndo/commitPendingUndo/discardPendingUndo`（:345-353）仅用于拖拽类手势（:11801/:13255/:21868 等），与删除无关

与删除的交互（F 系列发现，行号已逐处核实）：
- **F1（新 P0-4，最严重）**：节点/单图删除是**纯本地**快照（无服务端参与）；若之后该媒体在资源库被硬删（ADR 0015），再按 undo/redo，旧快照会复活带**悬空 asset_id** 的节点。链路已全程验证：`importProjectMediaReference`（:977-985）对带 `asset_id` 的项**原样放行不校验** → `materializeProjectMediaReferences` 不剔除 → `buildCoreMutations` 生成 upsert_node → PATCH → 服务端 `_apply_mutation('upsert_node')`（firefly_projects.py:2803-2809）`media_record` 失败 → `raise InvalidMutation(f"项目媒体不存在: {asset_id}")` → **400** → 本地脏状态保留 → **之后每次自动保存都重试同一批 mutation 并失败**，画布进入持续保存失败状态，直到用户手动删掉幽灵节点或刷新页面（悬空节点从未持久化，刷新即恢复）。触发面比想象大：**任何早于硬删时刻、含该 asset 的快照**（不止删除操作自己的快照）都会中招；redoStack 同样受影响。
- **F2（坐实 P0-3）**：`smartTrashRestoreItem`（:6777-6807）409 时 `syncProjectCanvasFromResponse` 同步画布后，仍**无条件** `toast(tr('canvas.originalRestored'))`（:6795-6800）并重开面板——409 = 恢复未生效，却报「已恢复」。
- **F3（修正 P1-7 定性）**：`purgeSmartProjectMediaTrash`（:9848-9869）清全部 `projectOriginalDelete` 令牌（:9858-9860）**逻辑上是正确的**（清空=所有回收区条目已删，所有令牌必然失效，不是误清）；该函数真正的问题是：不 await 请求、400ms 盲等后重开面板显示旧列表（:6769-6772）+ 失败 toast 语义错配。
- **F4（新 P2）**：`smartTrashPurgeItem`（:6810-6824，单项彻底删除，有 confirm）**不清理对应 undo 令牌** → 之后 undo 该项会走 restore API → 404/410 → `permanentUndoFailure` 弃令牌（优雅降级，但用户按 undo 会收到一条报错 toast 且实际无变化）。
- **F5（已核实无问题）**：面板恢复 `restoreSmartProjectOriginal`（:9796-9818）成功后会正确移除匹配的 undo 令牌（assetId+deletionId+projectId）——「面板恢复使 undo 令牌失效」场景已处理。
- **F6（设计边界，记录即可）**：token 分支 undo 不压 redo → redo 无法重删原图；媒体仍在回收区，可从回收区面板操作。
- **F7（低危）**：UNDO_LIMIT=40 FIFO——token 被挤出后媒体留在回收区，仍可从回收区面板恢复，不丢数据。
- **F8（文案）**：`performRedo`（:428）toast 用 `tr('smart.toastUndone')`——**重做弹「已撤销」文案**，无 redo 专属字符串。
- **F9（低风险，已核实有防护）**：生成进行中 undo：服务端 `upsert_node` 对 `pendingTasks` 是 server-owned（firefly_projects.py:2808-2831，活跃任务以服务端为准过滤合并，客户端旧 pendingTasks 被丢弃）→ 不会丢活跃任务；本地 UI 短暂不一致，下次同步自愈。

## 1. 审计口径与已核实事实

- 前端报告来自独立 researcher 审计（含全部行号），后端由主会话逐段核对源码；关键判定（`owns_original` / `owns_generation`）均已在源码中逐行确认。
- 行号以 2026-08-31 工作区为准（main @ 01e364e）。

## 2. 现状全景：删除路径矩阵

媒体结局图例：**保留** = 文件与资源库记录不变（孤儿）；**回收区** = 移入项目 `trash/`，7 天（`MEDIA_TRASH_RETENTION_MS`）可恢复；**硬删** = 文件直接 unlink，无回收区；**静默** = 系统自动硬删，无提示。

| # | 入口（前端） | API → 后端实现 | 媒体结局 | Undo | 确认 |
|---|---|---|---|---|---|
| 1 | 画布节点 × 按钮（:13023） | 无后端调用 → `deleteNodeFromButton` :13446 → `clearNodeMediaBeforeDelete` :13422（**清空媒体保节点**） | 保留 | 画布 undoStack | 无 |
| 2 | Delete/Backspace 键（:22286-22293） | 无后端调用 → `deleteNode` :13404（删节点+连接+历史分组） | 保留 | 画布 undoStack | 无（无 toast） |
| 3 | 节点内单图 ×（:13103） | 无后端调用 → `deleteImage` :13625 | 保留 | 画布 undoStack | 无 |
| 4 | 分组吸收（:1810/:1825） | 无后端调用（隐含删被吸收节点） | 保留 | 画布 undoStack | 无 |
| 5 | 历史条目「删记录」 | `POST /history/delete` → `delete_history_record` firefly_projects.py:1779 | 保留 | 无 | 确认（i18n OK） |
| 6 | 历史条目「删记录+媒体」 | `POST /history/delete-with-media` → `delete_history_record_with_media` firefly_projects.py:1808（原子事务+undo 快照） | 生成原图→回收区；输入引用文件保留 | 回收区恢复 | 确认 |
| 7 | 历史卡片/详情「删除当前原图」（:9982/:9286） | `POST /original/delete` → `delete_generated_original` firefly_projects_deletion.py:581（要求 log_id+output_id+output_index 完整身份） | 回收区 + `trash/undo/*.json` 快照 | 前端 `pushUndo({projectOriginalDelete})` :9700 → `POST /original/restore` | confirm（文案含「保留 7 天」，i18n canvas.js:89） |
| 8 | （**前端无入口**） | `POST /original/delete-project-wide` → `delete_generated_original_project_wide` deletion.py:613；恢复 `:restore-project-wide` | 回收区 + 快照 | restore-project-wide | — |
| 9 | 资产库单删（项目内，:6480-6562） | `DELETE /media/{id}` → `delete_project_media_asset` deletion.py:836（ADR 0015） | 硬删 + 引用清理 + 墓碑 | 无 | 引用警告对话框（**未声明不可恢复**） |
| 10 | 资产库单删（全局，:6467-6479） | 同上 | 硬删 | 无 | **无确认** |
| 11 | 本地素材删（`deleteLocalAssetFromPanel` :7092-7112） | 无后端调用 | 仅移除本地缓存条目 | 无 | **无确认** |
| 12 | 资产库批量删除（`smartAssetBatchDelete` :6631-6690） | 逐个 `DELETE /media/{id}` | 硬删 | 无 | 汇总 confirm（声明不可恢复） |
| 13 | 未使用媒体面板「清理」（:6842-6880 / `smartUnusedCleanAll` :6891-6925） | `GET /media/unused`（api:3010）→ 逐个 `DELETE /media/{id}` | 硬删 | 无 | confirm |
| 14 | 回收区：恢复单项（:6777-6807） | `POST /original/restore` / `restore-project-wide` | 回收区→media，重建节点/历史引用（`_restore_deleted_nodes` + `_merge_deleted_asset_references`） | — | 无 |
| 15 | 回收区：彻底删单项（:6812-6826）/清空（`purgeSmartProjectMediaTrash` :9848-9869） | `DELETE /trash/media[?asset_id]` → `purge_deleted_media` deletion.py:1445 | 硬删 | 清空时**同时清掉全部** `projectOriginalDelete` undo 令牌（:9858-9860） | — |
| 16 | 系统自动：7 天过期（`_cleanup_expired_deleted_media_locked` deletion.py:1510；触发点=store 初始化 firefly_projects.py:180 + 所有删除/列表/恢复/清空操作的惰性清理） | 回收区→硬删 | 静默 | 无 | 无 |
| 17 | 系统自动：生成孤儿回收（`cleanup_project_generation_orphans` api:1622-1634；`delete_media_if_unreferenced` cleanup.py:446；其他调用点 firefly_projects.py:1404 Photoshop 回滚、agents.py:500/524、misc.py:320） | 项目无活跃任务时，把孤儿媒体**直接硬删**（不查回收区、不提示） | 静默 | 无 | 无 |
| 18 | 项目删除（独立页 `project-list.js:221-254`，`.project-trash` 目录，api:2973-3001） | 整项目目录移动 | 整项目 | 恢复=原样放回；永久删除=释放磁盘 | 有 |

**概念分层现状**（同一「删除」词覆盖）：
- 画布级 undo（undoStack，可撤销节点/图片移除）
- 回收区（媒体 trash，7 天，可恢复；`GET/DELETE /trash/media`）
- 项目回收站（`.project-trash`，整项目，独立页面）
- 不可逆硬删（资源库路径，ADR 0015）
- 静默系统回收（用户不可见）

## 3. 问题清单（分级）

### P0 — 数据/状态风险

| ID | 问题 | 证据 | 影响 |
|---|---|---|---|
| P0-1 | 批量删除把 409 计为成功 | smart-canvas.js:6631-6690：`DELETE /media/{id}` 返回 409（`ProjectStoreError`→409，如「媒体正被生成任务引用」）时仍 `okCount++`、从本地缓存移除、toast「已删除 n 个」 | 虚假成功；资产仍在服务端，下次重渲染复现，用户以为删掉了 |
| P0-2 | 未使用媒体清理同型 409 误计 | smart-canvas.js:6891-6925（`smartUnusedCleanAll` 逐删同样只按 2xx 计数外，409 分支处理不当）；且收尾缺 `renderSmartCanvasLog` + `refreshSmartProjectMediaTrashButton` | 同 P0-1 + 面板状态不刷新 |
| P0-3 | 回收区恢复 409 误报成功 | smart-canvas.js:6795-6800：恢复请求 409 后仍 toast「原图已恢复」 | 用户以为恢复成功，实际画布未变（服务端 revision 已前进） |
| P0-4 | **undo/redo 复活已硬删媒体 → 画布持续保存失败** | 见 §2.1 F1：本地快照含悬空 asset_id → 服务端 `upsert_node` 校验 `InvalidMutation`（firefly_projects.py:2803-2809）→ 400 → 每次自动保存重试失败；触发面=任何早于硬删时刻的快照（undoStack+redoStack） | 画布进入「永远存不上」状态，直到手动删幽灵节点或刷新；D2 拍板后（保留「删图留节点」）该操作频率高，命中概率上升 |

### P1 — 语义不一致 / 行为缺口

| ID | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1-1 | **`output_id` 归属判定未回移到两条批量删除路径** | T9 批在 `delete_project_media_asset` 加入判定：`owns_generation = _has_explicit_generation_metadata(node) and any(img.get("output_id") for img in node.images)`（deletion.py:901-909，修复「前端 run 状态机给所有 smart-image 节点盖 `runStartedAt` 戳 → 拖入图节点被误判为生成节点」）。但：`delete_generated_original_project_wide`（deletion.py:613 内 nodes 循环）仍用 `owns_original = displays_original and (node_id in history_producer_ids or self._has_explicit_generation_metadata(node))`；`delete_history_record_with_media`（firefly_projects.py:1947-1951）同样缺 `output_id` 检查。`_delete_generated_output_occurrence`（:299）基于 occurrence 身份精确删除，无此问题 | 当某普通节点**展示**了被删的生成图（如「使用图片」引用同一 asset）且自身带 run 戳时：写错墓碑类型（`deletedOutputTombstones` 而非 `deletedInputTombstones`）+ `_reset_deleted_original_owner_state` 错误重置该节点状态（若该节点正在生成，可能破坏 pendingTasks/run 状态） |
| P1-2 | 节点删除两入口行为不同（**D2 已拍板：维持现状，降级为设计记录**） | × 按钮 = 清空媒体保节点+内部提示词（:13422，用户确认正确）；Delete 键 = 整节点删除（:13404，提示词随节点移除但画布 undo 可找回） | 非缺陷；需在 ADR 0016 中把这两级语义写成规范（单图 × = 删那张图 / 节点 × = 删图留节点留提示词 / Delete 键 = 删节点） |
| P1-3 | 节点/单图删除对生成中任务无保护 | :22286-22293 / :13446 / :13625 均不检查 `pending`/`pendingTasks`/`running`（后端 `begin_media_generation` 要求节点存在，写回时的具体行为未逐一验证） | 删除生成中节点后任务写回的后果不可预测（孤儿资产或报错） |
| P1-4 | 单删/本地素材删无确认 | 全局库单删 :6467-6479；`deleteLocalAssetFromPanel` :7092-7112（另缺 `btn.disabled` 复位；失败文案错配 `smart.assetAddFail`） | 误点即不可逆（硬删） |
| P1-5 | 资源库单删对话框未声明「不可恢复、不进回收区」 | :6931-6989（批量 confirm 文案有声明） | 与 ADR 0015 语义不符的用户预期 |
| P1-6 | `deleteCanvasLogEntry` 无 409 分支、无 busy 防连点 | :9469-9530（对比删原图流 :9677-9695 有完整 409+canvas 同步+重试×3 模板） | 409 后画布状态过期，后续操作连环冲突 |
| P1-7 | 「清空回收区」面板不 await、400ms 盲等重开显示旧列表；失败 toast 语义错配 | :9848-9869（清全部 undo 令牌本身**逻辑正确**：清空=全部令牌失效，F3 修正） | 列表闪烁/旧数据；用户误判清空结果 |
| P1-11 | 回收区单项彻底删除不清理对应 undo 令牌（F4） | `smartTrashPurgeItem` :6810-6824：之后 undo 该项 → restore 404/410 → `permanentUndoFailure` 弃令牌 | 用户按 undo 收到报错 toast 且实际无变化（优雅降级但体验差） |
| P1-8 | 项目范围删原图端点无前端入口 | `POST /original/delete-project-wide` api:3760 + restore api:3780（后端完整实现+单测） | 死代码路径；或能力缺失（按 ADR 0005 superseded 的意图，该操作应可达） |
| P1-9 | 静默孤儿回收无感知 | `cleanup_project_generation_orphans` api:1622-1634 + `delete_media_if_unreferenced` cleanup.py:446：生成失败/取消/Photoshop 回滚后，无引用媒体**直接硬删**，不进回收区、无 toast、回收区面板不可见 | 用户看到「生成失败」后以为图还在资源库，实际已被删；与 ADR 0015「用户主动删除才硬删」的精神不一致 |
| P1-10 | 硬编码文案（i18n 缺口） | smart-canvas.js:13641（节点单图 title 硬编码 `'Image'`）、:9968 `'已取消'`、:9973 取消/重试钮、:9440 `'未记录提示词'`、:9881 内联中英双语、:6812-6826 `'purge failed'`（英文）、:7108 `'删除失败'`、:7109 本地素材删失败错配 `assetAddFail`、:428 **redo toast 误用 `smart.toastUndone`**（重做弹「已撤销」，F8） | 英文 locale 下中文残留 / 中文 locale 下英文残留；redo/undo 文案混淆 |

### P2 — 代码结构

| ID | 问题 | 证据 |
|---|---|---|
| P2-1 | 后端「资产删除」模板重复 4 份且已漂移 | `delete_history_record_with_media`（firefly_projects.py:1808）、`delete_generated_original_project_wide`（deletion.py:613）、`delete_project_media_asset`（deletion.py:836）、`_delete_generated_output_occurrence`（deletion.py:299）：同一「revision 校验→清理 history 节点→墓碑→owner 重置→revision+1→事务提交」骨架，细节差异（墓碑类型/是否事务/归属判定）已漂移（P1-1 即漂移后果） |
| P2-2 | 前端「409+canvas 同步」模板散落 | `deleteSmartProjectOriginal` :9677-9695（完整）、资产单删 :6480-6562（部分）、批量/未使用/历史删除（缺失或不一致）——无统一请求 helper |
| P2-3 | 墓碑写入/引用清理规则与前端断言耦合 | `deletedOutputTombstones`/`deletedInputTombstones`/`deletedReferenceTombstones` 的语义分散在 4 个后端方法 + 前端多处渲染逻辑，无单一文档定义 |

## 4. 修改方案

### A. 语义统一（产品层，含决策点）

**A-0 目标模型**：把「删除」收敛为一张用户可预期的矩阵——每个入口在**动手前**用统一对话框声明三件事：① 媒体结局（保留 / 进回收区 7 天 / 永久删除）；② 是否可撤销（画布 undo / 回收区恢复 / 不可逆）；③ 影响的引用（n 个节点/历史条目）。

**决策点（需拍板，默认建议见括号）**：

- **D1 节点/单图删除是否级联处理媒体**（现状=不处理，媒体变孤儿堆资源库）
  - a) 维持现状（最小改动，孤儿由「未使用清理」面板兜底）
  - b) **（建议）** 删除节点时若其图片在项目中**无其他引用**，一并移入回收区（进面板可见、可恢复）；有引用则保留
  - c) 级联硬删（危险，不建议）
- **D2 节点 × 按钮与 Delete 键**（**已拍板：维持现状**）：× 按钮 = 删图片、留节点和内部提示词（用户确认正确）；Delete 键 = 整节点删除（提示词随节点移除，画布 undo 可找回）。需在 ADR 0016 写成三级规范（单图 × / 节点 × / Delete 键）；节点删除对生成中任务的保护（P1-3）仍要做
- **D3 静默孤儿回收（P1-9）是否改走回收区**
  - a) **（建议）** 生成失败/取消产生的孤儿也进回收区（`reason: "generation-orphan"`），用户可见可恢复；7 天过期照旧
  - b) 维持静默硬删，但加 toast 提示
- **D4 项目范围删原图（P1-8）**
  - a) **（建议）** 补前端入口（资源库单删对话框中，当该原图被多节点引用时提供「删除该项目中此原图的全部引用」选项）
  - b) 删除端点保持后端-only（写文档标注为高级 API）

**A-1 统一删除确认对话框**（落地 D1-D4 后）：
- 资源库单删/全局单删/本地素材删：统一走引用警告对话框，文案三要素（P1-4/P1-5）；本地素材删补 busy 复位与正确失败文案。
- 节点删除（按 D2）：生成中（`pending>0` / `pendingTasks` 非空 / `running`）节点禁止删除并提示（P1-3），或先取消任务再删。
- 历史删除（record/media）：补 409 分支与 busy 防护（P1-6），复用 A-2 的 helper。

### B. P0 修复（无争议，立即做）

- B-1 批量删除：409 → 不计入 `okCount`、不本地移除、收集失败原因；全部完成后 toast 分列「成功 n / 失败 m」；409 且响应带 `canvas` 时先 hydrate 画布再继续（P0-1）。
- B-2 未使用清理：同 B-1；收尾补 `renderSmartCanvasLog()` + `refreshSmartProjectMediaTrashButton()`（P0-2）。
- B-3 回收区恢复：409 → 不 toast 成功；hydrate 返回画布后重渲染回收区列表；仅 200 才提示「已恢复」（P0-3）。
- B-4 三条路径补 busy 防连点（按钮 disabled + 请求结束复位，统一模式）。
- B-5 **P0-4 悬空引用修复**（联合审计新增）：双层防护——
  1. 硬删成功（`DELETE /media/{id}` 返回 200 + canvas）时，遍历 undoStack/redoStack，移除（或在应用时过滤）含该 asset_id 的快照，并 toast「已删除的媒体无法通过撤销找回」；
  2. `performUndo`/`performRedo` 应用快照时兜底过滤悬空 images（asset_id 不在 `projectMediaLibrary` 中的条目剔除）+ 有剔除时 toast 提示，保证任何情况下 undo 后画布可保存。

### C. 一致性加固

- C-1 **`output_id` 判定回移**（P1-1）：抽 `_node_owns_generated_output(node, asset_id)`（= `displays in images` + (`history_producer_ids` 命中 或 `run 快照` + `images 含 output_id`)），`delete_generated_original_project_wide`、`delete_history_record_with_media`、`delete_project_media_asset` 三处共用。补单测：拖入图节点带 `runStartedAt` 展示同 asset → 走 `deletedInputTombstones` 且不被 reset。
- C-2 清空回收区（P1-7）：只清与**本次被清条目** `deletion_id` 匹配的 `projectOriginalDelete` 令牌；面板操作 await 完成后重渲染（去掉 400ms 盲等）。
- C-3 前端统一 `projectCanvasRequest` helper（P2-2）：封装「请求→409 时 hydrate `detail.canvas`（含 nodes 重映射/`normalizeLegacySmartNode`/`materializeProjectMediaReferences`/snapshot）→可选重试×N→失败统一错误文案」，`deleteSmartProjectOriginal`/资产单删/历史删除/回收区恢复/批量删全部改走 helper。
- C-4 **保存 400 自愈**（配合 P0-4）：`saveCoreCanvasIncrementally` 捕获 `InvalidMutation`（「项目媒体不存在」）类 400 时，不再无限重试——自动 rebase 到服务端画布（hydrate+render）+ 一次性 toast「部分内容引用的媒体已被删除，已同步到最新状态」，避免「持续保存失败」死锁（B-5 生效后此路径仅作兜底）。

### D. i18n / 文案（P1-10）

- 新增 i18n 键并替换硬编码：`smart.nodeImageTitle`（替 :13641 'Image'）、`canvas.logCancelled`（:9968）、取消/重试钮（:9973）、`canvas.logNoPrompt`（:9440）、`canvas.deleteLogInline`（:9881 双语拆键）、`smart.trashPurgeFailed`（:6826）、`smart.assetDeleteFail`（:7108，并修正本地素材删错配的 `assetAddFail`）、**`smart.toastRedone`**（替 :428 redo 误用的 toastUndone，F8）。
- 统一删除文案三要素模板（结局/可撤销性/引用数），`smart.delete*` 键族集中维护，跑 `static/js/i18n/validate-i18n.js`。

### E. 结构收敛（P2）

- E-1 后端抽公共删除骨架：`_delete_asset_core(project_id, asset_ids, *, sink: "trash"|"hard", reason, tombstone_policy, require_identity)`，四个入口方法退化为参数化调用 + 各自的事务/快照差异（`_delete_generated_output_occurrence` 保留 occurrence 身份逻辑，仅共用引用清理与墓碑段）。目标：P1-1 类漂移在结构上不可能复发。
- E-2 在 ADR 0015 或新 ADR 中固化「墓碑类型语义表」（P2-3）：`deletedOutputTombstones`（节点拥有生成输出，输出被删）/`deletedInputTombstones`（节点仅输入引用）/`deletedReferenceTombstones`（历史条目引用）+ 归属判定唯一事实源（C-1 的 helper）。

## 5. 分期执行计划

| 期 | 内容 | 预计 | 验证 |
|---|---|---|---|
| 1（无争议修复） | B-1~B-5（P0 四 bug + 悬空引用双层防护 + busy 防护）；C-1（output_id 回移 + 单测）；C-4（保存 400 自愈）；D（i18n 补齐含 redo 文案） | 1 天 | ✅ **已完成（2026-08-31）**：#59/#60/#61/#62/#63/#72/#73 + #83（生成历史累加合并）+ #79（孤儿进回收区 + 通用恢复端点 `POST /trash/media/restore`）全部落地；新增 7 单测，全量 **792 tests OK**（基线 732），e2e **44/44 无回归**，validate-i18n.js 通过 |
| 2（语义统一，依赖 D1-D4 拍板） | A-0~A-1（统一删除对话框 + 级联策略 + 节点删除统一 + 静默回收可见化 + project-wide 入口）；C-2（undo 令牌精确清理） | 1-2 天 | 新 e2e 断言：节点删除级联进回收区、生成中节点禁删、孤儿回收可见、project-wide 入口全链路；i18n validate 通过；zh/en 双 locale 文案抽查 |
| 3（结构收敛） | C-3（前端 helper）；E-1/E-2（后端骨架 + ADR） | 1 天 | 全量单测 + 44/44 e2e；helper 覆盖的 5 条路径 409 注入测试（可扩 e2e 用 fetch wrapper 注入 409） |

每期独立提交、可独立验收；第 2 期开始前需要你对 D1-D4 拍板（不拍板则按建议项 a/b 标注执行）。

## 6. ADR / 文档影响

- 新增 **ADR 0016「画布删除的统一后果模型」**（第 2 期落地时写）：定义删除后果矩阵、级联策略（D1）、静默回收策略（D3）、墓碑语义表（E-2）；supersede ADR 0005 中「项目范围删除生成原件」的部分边界（保留 0014 的「独立破坏性操作」定位）。
- ADR 0015（用户主动删除 = 硬删）不受影响；D3 若选 a（孤儿进回收区），需补一句「系统回收默认进回收区，用户主动删除维持硬删」。
- `TODO-FIX.md` 增补本清单条目，按分期勾选。

## 7. 附：待拍板决策点（速览）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 节点/单图删除是否级联媒体 | ✅ **已拍板（2026-08-30）：级联**——节点/单图删除时，媒体若无其他引用则移入回收区（7 天可恢复），有引用则保留 |
| D2 | 节点 × 按钮 vs Delete 键 | ✅ **已拍板：维持现状**（× 按钮删图留节点留提示词；Delete 键整删节点）；写进 ADR 0016 三级规范 |
| D3 | 生成失败孤儿静默硬删 | ✅ **已拍板（2026-08-31）：选 a**——孤儿进回收区（`reason: generation-orphan`），用户可见可恢复，7 天过期照旧。已实现（#79，第 1 期）；`restore_trash_media` + `POST /trash/media/restore` 提供通用恢复 |
| D4 | project-wide 删原图入口 | ✅ **已拍板（2026-08-30）：补前端入口**（多引用时的显式选项） |

另：用户要求**删除/撤销/重做作为一个整体审视**（2026-08-31），已完成联合审计，见 §2.1（F1-F9）；由此新增 P0-4（悬空引用→持续保存失败）并修正 P1-2/P1-7 定性。
