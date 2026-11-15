# Issue #197 调查记录：四道保存关卡「请求已发出」分支的刷新恢复路径审计

本审计回答：关卡请求**已发出**、任务已在服务端创建时刷新页面，现有恢复路径能否接管并显示？
结论先说：四道均可被既有路径接管，无需后端改动；Photoshop 有一个客户端瞬态状态的已知缺口（结果不丢）。

## 审计结论（逐道，含证据）

| 关卡 | 已发出后刷新能否接管 | 证据（file:line） |
|---|---|---|
| 视频 | **能** | 服务端把任务持久化在节点 `pendingTasks` + 画布 `history`，按 task_id 可查（`app/routers/api_project_library.py:1640-1679` 的 durable 查询；`#82` 重启重轮询 `_resume_project_video_generation` `:1682`，不重复提交）。客户端加载钩子 `static/js/smart-canvas.js:6610 resumeSmartPendingTasks()` → `static/js/canvas-generation-tasks.js:1141-1148` 遍历带 `pendingTasks` 的节点 → `resumeSmartPendingNode`（`:876`）轮询 `GET /video-generation-tasks/{task_id}`（URL 映射 `projectImageTaskUrl` `:445-456`，`project_vid_` 前缀走项目端点）。刷新后任务重新轮询并显示。 |
| 局部编辑 | **能** | 服务端在处理 `/local-edit` 时直接把 `output_asset_id` / `images` 写回节点（`app/routers/api_project_library.py:3420` `node.update({... 'output_asset_id': ..., 'images': [...]})`）。节点状态随画布持久化，刷新后渲染。 |
| Photoshop | **能（有缺口）** | round-trip 记录持久化在服务端（创建 `app/routers/api_project_library.py:2977-3001`，存储读回 `ProjectStore.load_photoshop_round_trips`）；PS 结果经 `/round-trips/{id}/return`（`:3048`）→ `complete_photoshop_round_trip` 更新源节点（`:3117`），节点更新持久化，刷新后显示最终产物。**缺口**：「等待 PS 返回」是客户端瞬态状态，刷新后不恢复该等待 UI；服务端记录与最终节点更新不丢。 |
| 对话 | **能** | 未发消息以 `status:'pending'` + `request_id` 的本地 user 消息持久化在 `node.messages`（`static/js/smart-canvas.js:9951-9961 ensureLocalPendingConversationMessage`）。加载钩子 `resumeConversationTasks()`（`smart-canvas.js:10027-10032`）对 pending user 消息恢复 `startConversationTaskPolling`（`:9978`）→ `GET /conversation/{node}/messages/{request_id}` 取服务端快照。服务端按 `request_id` 去重（`app/routers/api_project_library.py:3277-3282`，命中已有快照直接返回），即使双发也只一个任务。 |

## 由此确定的持久化设计（照 issue 票；传输层已修正）

**传输层修正（勘误）**：增量保存（autosave 主路径）走 mutation 协议，
`snapshotFromCanvas` 的 meta 白名单只有 `{title, icon, history}`
（`static/js/project-canvas-mutations.js:147-156`）→ **顶层 canvas 新字段不会落盘**；
节点则整节点 `JSON.stringify` 进 `upsert_node` mutation，服务端 `_apply_mutation`
（`firefly_projects.py:3215-3245`）deep-copy 全字段合并 → **节点任意字段可落盘**
（agent 服务已靠此模式持久化完整节点字典）。因此标记锚定在**节点字段**
`node.pendingActionResumes = [{actionId, kind, payload, enqueuedAt}]`（服务端零改动）。

- 标记语义 = **「尚未发出」**：关卡失败时把 `{actionId, kind, payload, enqueuedAt}` 写入
  锚定节点字段（内存 Map 为权威，同步镜像到节点字段，随下一次成功保存的
  `upsert_node` mutation 落盘）。
- **fetch 发出的那一刻清除**：实现上更严格——先把「该条目已移除」的节点状态
  落盘成功（persister：改节点字段 + `saveCanvas({automatic:false})` await 落定），
  才 fire fetch；落盘失败则 `releasing=false` 留在队列，等下一次
  `markAutosaveSaved` 重试（请求不丢）。并发保护：`entry.releasing` 标记，
  另一轮 resume 跳过正在清位的条目，杜绝双发。
- 刷新后：节点字段存在 → 从未发出 → 从字段重建内存队列（replay 闭包丢失，
  用注册表 `smartPendingActionRebuilderFor(kind)` 从 payload 重建）→ 安全复放（恰好一次）；
  字段不存在 → 已发出 → **不复放**，由上表既有路径接管。重复发送在结构上不可能，
  不依赖服务端去重。
- 各道载荷字段（payload，可序列化）：
  - video: `{prompt, refs, runSettings}`（rebuild 重调 `runApiVideoGeneration` + 结果镜像）
  - photoshop: `{projectId, assetId}`（rebuild 直发 round-trip POST，不依赖闭包变量）
  - local-edit: `{}`（全部状态在节点字段上，`submitLocalEditRequest(live)` 从节点读）
  - conversation: `{}`（`sendConversationRequest(live)` 从节点读 text/request_id/refs；
    服务端按 request_id 去重兜底）

## 残留缺口（最终报告需如实列出）

1. 「请求已发出但响应未回」刷新：三道无服务端去重的关卡（视频/局部编辑/Photoshop）由
   「先落盘清除、后发 fetch」关闭同标签页刷新窗口；**多标签页同时打开同一画布**不在 #189 语义内，
   理论上仍可双发——不在本 issue 修（需服务端去重，报告代价，不擅自改
   `app/routers/api_project_library.py`）。
2. video 载荷 `refs` 若含未 asset 化的 `blob:` URL，刷新后 URL 失效（与服务端行为一致，记录之）。
3. Photoshop 在途轮询的客户端瞬态状态刷新后不恢复（结果与记录不丢）。

## 分层守卫约束（已实测）

`tests/test_canvas_core_layer.py` 反向引用棘轮：`canvas-generation-tasks.js` 当前 **106/106 零余量**
（构成：render 22、scheduleSave 17、canvasId 6、其余 1 处 × 41 个名字）。`canvas`（`static/js/canvas-core.js:63`）
与 `canvasState`（`static/js/canvas-state.js:3`）均**不是** smart-canvas.js 顶层名 → 从 tasks 模块引用零成本。
新增持久化代码只用 `canvas.*` 与从调用方传入的参数（persistNow 等），不新增任何 smart-canvas.js
顶层名的文本出现；共享注册表（`registerSmartPendingActionRebuilder` / `smartPendingActionRebuilderFor`）
下沉到 `canvas-core.js`（该层上限 0，注册表自身不引用 smart-canvas.js 任何名字）。
