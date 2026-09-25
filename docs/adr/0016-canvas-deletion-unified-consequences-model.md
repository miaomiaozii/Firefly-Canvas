---
status: accepted
supersedes: ADR 0005 中「项目范围删除生成原件」的界面可达性部分（保留 ADR 0014 的独立破坏性操作定位与 ADR 0015 的资源库硬删边界）
date: 2026-09-08
---

# 画布删除的统一后果模型

本 ADR 把 Firefly Canvas 中所有「删除」入口收敛为一张用户可预期的后果矩阵：**每个入口在动手前声明三件事**——① 媒体结局（保留 / 进回收区 7 天可恢复 / 永久删除）；② 是否可撤销（画布 undo / 回收区恢复 / 不可逆）；③ 影响的引用（哪些节点/历史条目）。决策 D1–D4（2026-08-30 ~ 09-08 用户拍板）与问题清单见 `docs/deletion-mechanism-review.md`，实现见 issue #59–#78、#83。

## 删除后果矩阵

| 入口 | 媒体结局 | 可撤销性 | 前端确认 |
|---|---|---|---|
| 单图 ×（节点内某张图） | 无其他引用 → 进回收区（D1）；有引用 → 保留 | 画布 undo + 回收区恢复 | 无（轻量操作） |
| 节点 × 按钮 | 清空节点媒体：无其他引用 → 进回收区（D1）；节点与提示词保留 | 画布 undo + 回收区恢复 | 无（D2 拍板维持现状） |
| Delete/Backspace 键 | 整节点删除：同上级联媒体（D1） | 画布 undo + 回收区恢复 | 无（D2 拍板维持现状） |
| 生成中节点（上述三种） | 禁止删除，toast「该节点正在生成中，请先取消任务再删除」 | — | — |
| 资源库单删（全局/项目） | 永久删除（硬删）+ 引用清理 + 墓碑 | 不可撤销（ADR 0015） | 统一确认对话框，声明「永久删除、不进回收区、无法恢复」+ 引用列表 |
| 资源库单删 · 多节点引用的生成原图 | 可选「删除原图及项目中全部引用」（项目范围删原图，D4） | 服务端 restore-project-wide 可恢复 | 对话框内独立按钮 + 二次确认 |
| 本地素材删除 | 磁盘硬删 | 不可撤销 | 确认对话框 |
| 生成原图删除（历史条目 ×） | 进回收区 7 天（ADR 0014 流程，deletion_id 撤销令牌） | 画布 undo + 回收区恢复 | 原流程不变 |
| 生成任务取消/失败/超时 | 已下载媒体保留：写入终态记录 outputs（#83 语义）；无引用孤儿 → 进回收区（D3），不再静默硬删 | 重试取回 + 回收区恢复 | — |
| 未使用媒体清理 / 批量删除 | 永久删除（仅无引用者；被引用 → 409 保留） | 不可撤销 | 批量确认（已有声明文案） |
| 回收区单项彻底删除 / 清空 | 磁盘硬删 | 不可撤销 | 单项二次确认；清空整区确认 |

## 决策记录（D1–D4）

- **D1 级联媒体（✅ 2026-08-30 拍板，选 b）**：节点/单图删除时，媒体若**无其他引用**（画布节点 + 历史记录，规则同 `ProjectStore._collect_asset_ids`）则移入回收区（7 天可恢复）；有引用则保留在库。实现：`ProjectStore.archive_unreferenced_media`（`firefly_projects_cleanup.py`，与生成孤儿回收共用 `_archive_unreferenced_media_locked`）+ `POST /media/{asset_id}/archive`（被引用 → 409，前端保留在库并继续）+ 前端 `scheduleMediaArchive` 包装（内部调用 `cascadeArchiveDeletedMedia`；`deleteNode` / `clearNodeMediaBeforeDelete` / `deleteImage` 三处经它触发）。
- **D2 节点 × 按钮 vs Delete 键（✅ 拍板：维持现状）**：× 按钮 = 删图片、留节点和内部提示词；Delete 键 = 整节点删除（提示词随节点移除，画布 undo 可找回）。本 ADR 将此三级语义（单图 × / 节点 × / Delete 键）定为规范。
- **D3 静默孤儿回收（✅ 拍板，选 a）**：生成失败/取消/超时产生的无引用媒体移入回收区（`reason: "generation-orphan"`），可见可恢复，7 天过期照旧；配合 #83（终态记录保留已下载 outputs，使「被历史引用」成立）。
- **D4 项目范围删原图入口（✅ 拍板，选 a）**：`POST /original/delete-project-wide`（ADR 0014 后端能力）补前端入口——资源库单删对话框中，当该原图被**多个节点**引用时显示「删除原图及全部引用」按钮 + 二次确认。

## 墓碑语义表（deleted*Tombstones）

| 墓碑 | 写入时机 | 语义 |
|---|---|---|
| `deletedInputTombstones` | 删除的媒体是该节点的**输入/展示素材**（非输出所有者） | 「上游素材已删除」——节点保留，可重新选择素材 |
| `deletedOutputTombstones` | 删除的媒体是该节点的**生成输出**（`output_id` 身份或历史 producer 命中） | 「生成结果已被删除」——节点回退无结果状态（`_reset_deleted_original_owner_state`），可重新生成 |
| `deletedReferenceTombstones` | 历史记录 inputs/outputs 中的引用被清理 | 「上游已删除」展示 |

**输出所有者判定**统一使用 `ProjectStore._node_owns_generated_output(node, asset_id, history_producer_ids)`（= 节点 images 展示该 asset 且（历史 producer 命中 或（节点带生成 run 元数据且 images 含 `output_id`））），判定集中在 `_node_owns_generated_output`，由 `delete_history_record_with_media` 节点清理路径与共享助手 `_apply_node_asset_removal`（`delete_project_media_asset` T9 / `delete_generated_original_project_wide` 均经它）统一调用（#63/#65 修复：此前 run 状态机给所有 smart-image 节点盖 `runStartedAt` 戳，裸 run 字段判定会把普通拖入图节点误判为生成节点，写错墓碑类型并错误重置节点状态）。

## 防护规则

- **生成中禁删**（#65）：`smartNodeInFlight`（pending>0 / pendingTasks 有活动项 / running / queued / jimengPending 且无已完成结果）的节点禁止从三个删除入口删除；节点占位区提供「取消任务」按钮（#81）。
- **undo/redo 悬空引用双层防护**（#62）：硬删成功后剔除含该 asset 的 undo/redo 快照；performUndo/performRedo 应用快照时兜底过滤悬空 images（asset_id 不在资源库），保证任何情况下 undo 后画布可保存；保存 400（「项目媒体不存在」）自动 rebase 服务端画布自愈。
- **409 不得计成功**（#59–#61/#68）：批量删除、未使用清理、回收区恢复、历史删除的所有路径，409 = 未成功——不本地移除、分列 toast「成功 n / 失败 m」、携带 `canvas` 时先同步服务端最新画布再继续。
- **回收区恢复 409 不误报成功**：仅 200 提示「已恢复」；409 后重渲列表。

## Consequences

- 删除语义从「各自为政」收敛为一张矩阵；新删除入口必须先在矩阵中定义媒体结局/可撤销性/引用影响。
- 资源库硬删（ADR 0015）与回收区（本 ADR）边界不变：资源库维度删除=终态；节点/任务维度删除=尽量可恢复。
- 孤儿媒体不再静默消失：任何「系统替用户删文件」的路径必须进回收区（可见、7 天、可恢复）。
- 前端 i18n 完整覆盖删除相关文案（#72）：不再允许删除流程中出现硬编码语言。
