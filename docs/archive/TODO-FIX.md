# 萤火画布修复任务清单

> 验收标准：**可用且好用**
> 原则：分拆任务、按序完成、不懂就问、不盲目推进

## 任务总览

| # | 任务 | 状态 | 优先级 |
|---|------|------|--------|
| T1 | 扩图功能修复 | **已修复+验证** | 高 |
| T8 | 生图size/resolution路由报错 | **已修复+验证** | 高 |
| T9 | 删除资源库图片UI不刷新 | **已修复+验证** | 高 |
| T2 | PS 插件完善 | **已验证** | 高 |
| T3 | 局部编辑节点实现 | **已验证** | 高 |
| T4 | 生图历史同步 Bug | **已修复+验证** | 中 |
| T5 | 生图失败取回机制 | **已修复+验证** | 中 |
| T6 | 画布项目重命名 | **已完成+验证** | 中 |
| T7 | 整理按钮移左上角 | **已完成+验证** | 低 |

---

## T1: 扩图功能修复

### 用户反馈
- 扩图不生效
- 白底取消选择一会就没了 → 最好红底，方便提示词移除
- 自动比例不生效

### 已完成的修复
- [x] 白色填充 → 红色 `#ff0044`（`applyImageOutpaint` 函数）
- [x] 提示词更新："Remove red area and fill the scene"
- [x] 编辑器背景色同步为红色（CSS）
- [x] 更新测试用例匹配新提示词

### 根因分析（T1 扩图不生效）—— 已通过静态分析高置信度确认

**核心 Bug：扩图生成时，节点自身的合成图没有被作为参考图发给模型。**

完整链路追踪：
1. `applyImageOutpaint`（smart-canvas.js:15455）：把「红底 + 原图」合成一张 PNG，上传后用 `replaceEditedImage` **替换掉节点当前的图片**，并设置 `node.outpaintSize` 和提示词。
2. 点生成 → `runGeneration`（:19535）→ `buildPromptRequest`（:18066）→ `defaultReferenceImagesFor`（:17549）。
3. `defaultReferenceImagesFor` 只返回 **上游连接节点的图片 + manualInputRefs**，**不包含节点自身当前图片**。
4. `runApiGeneration`（:19905）payload 的 `reference_asset_ids` 全部来自 `refs`，而节点自身合成图不在其中。

**结果**：模型只收到提示词 "Remove red area and fill the scene"，没有参考图，于是**凭空生成一张新图**，完全没有扩展原图 → "扩图不生效"。

**已实施修复（方案A，已 e2e 验证）**：在 `buildPromptRequest`（smart-canvas.js:18090 附近）扩图激活时（`validOutpaintSize(node)` 为真）把节点自身合成图（`node.images` 第一张带 url 的图）并入 refs，并入时若参考图已达 `SMART_REFERENCE_IMAGE_MAX` 上限则移除最早的默认参考图让位（扩图底图必需、默认参考图可选）。node --check 通过。
- 效果：模型收到 prompt + 合成图 → 去红底、扩展场景，而非凭空生成。
- 备选方案B（未采用）：applyImageOutpaint 里把合成图写入 manualInputRefs（会污染其它字段）。

**"自动比例不生效"**：outpaint-ratios.js 的 fitContaining 计算正确（测试 3/3 通过），尺寸计算链路 `applyOutpaintSizeToSmartParams`→`validOutpaintSize`→`sizeForRun` 也通顺。比例"不生效"更可能是**上述根因的连带表现**（没扩展成功，比例自然看起来没生效），或模型端忽略 size。需在 T1 主 Bug 修复后实测验证。

### 关键文件
- `static/js/smart-canvas.js` - 核心逻辑
- `static/js/outpaint-ratios.js` - 比例计算（测试已通过）
- `tests/image_editor_func_browser_client.js` - 功能测试

### 待用户确认
- T1 根因假设是否符合你观察到的现象？（即：扩图生成后得到的是全新无关图，而非原图扩展）
- 方案A 已实现，请实测验证；若不对再调整。

---

## T8: 生图 size/resolution 路由报错（新增）

### 用户反馈
- "生图现在报没有手动固定的模型完整支持本次请求（不兼容参数：size, resolution）"

### 根因
- 扩图时前端发送 `size=''`、`resolution=''`（空字符串）
- 后端路由 `_incompatible_common_parameters` 只对 `None` 视为"未指定"，空字符串会被当作无效值 → 报 size/resolution 不兼容

### 修复（app/routers/api_project_library.py:214-230 `_normalize_generation_size_params`）
- 归一化逻辑提取为 `_normalize_generation_size_params(payload, *, code, message)` helper，`project_image_request`（:232）/`project_video_request`（:248）两个路由复用（code 分别为 `inconsistent_image_dimensions` / `inconsistent_video_dimensions`）：
  - `size = (payload.size or '').strip() or None`
  - `aspect_ratio = (payload.aspect_ratio or '').strip() or None`
  - `resolution = (payload.resolution or '').strip() or None`
- 空字符串归一化为 None，路由逻辑将其视为"未指定"

### 验证
- [x] 单测：test_firefly_generation (15 tests OK)、test_banana_outpaint_request (16 tests OK)、test_banana_image_size (6 tests OK)、test_provider_contract_families (25 tests OK)
- [x] E2E：模拟扩图请求（size='', resolution='', aspect_ratio='4:3', model=nano-banana）→ 路由成功，task succeeded

---

## T9: 删除资源库图片 UI 不刷新（新增）

### 用户反馈
- "删除资源库图片显示该资源已删除，但是图没消失"

### 根因
- 删除 API 成功（或 404=已删过）后，前端 `renderAssetLibrary()` 从缓存 `projectMediaLibrary.items` 渲染，但缓存中已删项未被移除
- 导致 UI 仍显示已删图片，再次点击 DELETE 返回 404 显示"该资源已删除"

### 修复（static/js/smart-canvas.js）
- 新增 `removeProjectMediaFromCache(assetId)`（:6610）：从 `projectMediaLibrary.items` 过滤掉 `asset_id` 匹配项
- 在删除成功和 404 两个分支都调用该函数后再 `renderAssetLibrary()`

### 二轮加固（2026-08-27 code review 批，见 ADR 0015；追踪 issue #55，后续批量清理见 #56）
- **服务端引用清理**：原删除端点只删文件不清画布引用（节点留着失效引用，T3 局部编辑节点引用会被 UI 完全忽略）。新增 store 方法 `delete_project_media_asset`（firefly_projects_deletion.py:836）：同一事务内移除画布/历史对目标媒体的全部直接引用（`ProjectStore._collect_asset_ids` 收集 + `_remove_asset_references` 清理，覆盖 original_asset_id / reference_asset_ids / images 等全部真实引用字段），受影响节点/历史写入墓碑标记，生成结果节点全部图片被删时回退到无结果状态，画布 revision+1 并广播
- **引用检查端点重写**：`GET /media/{asset_id}/references` 与清理使用同一收集规则（修复原来只扫 `node['images']` 和 4 个顶层字段、漏掉 T3 节点的问题）
- **版本冲突防护**：DELETE 接受可选 `base_revision`，画布已被其他窗口修改时 409 返回最新画布，前端同步后提示重试；成功后前端用响应中的最新画布刷新本地 `nodes`（防止下次保存回写旧引用）
- **对话框 i18n**：7 处硬编码中文改 `tr()`（新增 10 个 `smart.delete*` 键）
- 删除按钮 404 分支补上按钮状态恢复；`focusAndSelectNode` 补清 `selectedIds`/`selectedImage`
- 活跃生成任务引用该媒体时删除被拒绝（409）

### 验证
- [x] node --check 通过
- [x] 浏览器 e2e（fly_fixes_browser T9）：资源库删除按钮 → 引用警告对话框列出引用节点 → 确认删除 → UI 节点图片移除 + 服务端 canvas 引用清理 + 墓碑记录 + 媒体文件不再可访问

---

## T2: PS 插件完善 ✅

### 用户反馈
- PS 插件是壳子货，没做完
- 没同步当前画布的进步

### 排查结论
- **PS 连接器已完整实现**（commit 64ab37b v2.0.0 已在 main 分支）
- 往返机制已验证：创建 round-trip → 获取源图 → 返回结果
- Canvas 发送按钮 `sendSmartNodeImageToPhotoshop` 工作正常

### 验证
- [x] 浏览器 e2e（fly_fixes_browser.py T2）：round-trip 创建成功、源图可访问、发送按钮可用

### 关键文件
- `app/services/photoshop.py` - 后端服务
- `app/routers/api_project_library.py:3163+` - round-trip API
- `tools/photoshop-asset-connector/` - PS 插件（ps.js、app.js）

---

## T3: 局部编辑节点实现 ✅

### 用户反馈
- 几乎只是文字占位
- 基本完全没做
- 需要可用且好用

### 排查结论
- **局部编辑节点已完整实现**（commit 4774005 在 origin/feature/issue-15-local-edit，代码已在 main）
- 核心逻辑、UI 交互、生成流程集成均已完成
- 后端 local_edit.py + API 端点 + 前端选区工具均已实现

### 验证
- [x] 浏览器 e2e（fly_fixes_browser.py T3）：节点存在、原图连接、prompt 设置、提交按钮可用、输出图片生成成功
- [x] 单测 test_local_edit (10 tests OK)

### 关键文件
- `local_edit.py` - 后端 RGBA 合成
- `app/routers/api_project_library.py:3522` - create_project_local_edit API
- `static/js/smart-canvas.js:7269+` - 前端节点和选区工具
- `main.py:64` - 在线适配器

---

## T4: 生图历史同步 Bug

### 用户反馈
- 生图节点和画布生图历史不同步
- 刚生成的图不能完全删除
- 刷新或生下一张图才能完全删除

### 已排查
- [x] 找到删除函数 `deleteSmartProjectOriginal` (smart-canvas.js:9257)
- [x] 找到 UI 同步函数 `reconcileSmartOriginalDeletionUi` (smart-canvas.js:9194)
- [x] 之前有相关修复: `40e15c4` (409重试、revision同步、output_id匹配)
- [ ] 用户仍报问题，可能是 UI 缓存或 canvas render 未反映删除

### 根因
- 后端 `complete_image_generation` 内部用 `clean_history = copy.deepcopy(history_record)` 分配 canonical output_id，只改副本
- 返回给前端的 `result['history_record']` 是**无 output_id 的本地变量**
- 前端 `absorbProjectGenerationResult` 用它替换 canvas.logs 的 pending 日志 → 日志输出永无 output_id
- `deleteSmartProjectOriginal` 因 outputId 为空而 toast 提前返回，删除不生效
- 刷新后能删的原因：apply_mutations 的 trusted_by_id/trusted_by_task 用 store 权威记录替换前端上传日志

### 修复（firefly_projects.py `complete_media_generation`）
- `_ensure_output_occurrence_ids` 分配 ID 后，把 canonical output_id/output_order 回写到调用方 history_record.outputs（只填缺失不覆盖）
- partial 逐张写回已固化 ID 不覆盖，final 按位置重算不会与节点镜像错位

### 验证
- [x] 浏览器 e2e（fly_fixes_browser.py T4）：nodeImgOutputId == logOutputId，删除后 logStatus=original_deleted，节点图片移除
- [x] 单测 test_project_image_generation_ui (31 OK)

### 关键文件
- `app/services/generation.py` - 生成服务
- `firefly_projects.py` - 项目数据管理（complete_media_generation 回写）
- `static/js/smart-canvas.js` - 前端删除逻辑（deleteSmartProjectOriginal）

---

## T5: 生图失败取回机制

### 用户反馈
- 生图失败取回机制在前端未生效

### 根因（三层）
1. **providerFailure 误判**：pollSmartCanvasTask 对 task.status==='failed' 抛 Error 无标记；resumeSmartPendingNode 的 isProviderFailure 只按英文关键词(failed/cancelled/timeout/sensitive/FAILURE)匹配，中文错误消息→误判网络错误→3 轮无效重试(5+10+15s)
2. **幂等重放**：api_project_library.py:1931 POST 发现同 idempotency_key 任务直接 202 返回同一 task_id（含已 failed 任务）→ 点重试永远重放同一失败任务
3. **核心层（最终根因）**：后端 `fail_image_generation`→`_end_media_generation` 从 node.pendingTasks 移除失败任务并同步节点（final_node 无 pendingTasks）；前端 `absorbProjectGenerationResult` 应用 final_node 时 `delete resultNode.pendingTasks` 清掉任务并设 `project_node_applied=true`，导致 catch 的 `!e?.projectNodeApplied` 守卫**跳过** failedGenerationTasks 登记 → 任务静默消失，用户永远看不到重试入口

### 修复
1. failed/cancelled/timeout 三分支的 error 加 `error.providerFailure = true;`；isProviderFailure 改为 `e && (e.providerFailure || 关键词匹配)`
2. retryFailedGeneration 第 2 分支重试前换新的幂等键：`submission.idempotencyKey = regen-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
3. **关键**：resumeSmartPendingNode 的 isProviderFailure catch 分支中，把 failedGenerationTasks 登记移出 `!e?.projectNodeApplied` 守卫——provider 失败无论节点状态是否已被服务端同步都必须登记重试 UI

### 验证
- [x] 浏览器 e2e（fly_fixes_browser.py T5）：fake_image 首次抛错 → 节点显示"重试"按钮 → 点击后取回/重生成图片成功
- [x] 单测 test_project_image_generation_ui (31 OK)

### 关键文件
- `static/js/smart-canvas.js` - pollSmartCanvasTask、resumeSmartPendingNode、absorbProjectGenerationResult、retryFailedGeneration
- `app/routers/api_project_library.py` - 失败路径 fail_image_generation、幂等重放
- `firefly_projects.py` - _end_media_generation 清理 node.pendingTasks

---

## T6: 画布项目重命名 ✅

### 状态
已完成

### 实现内容
- HTML: 标题区改为可编辑结构（span + 编辑按钮）
- CSS: 标题样式、编辑按钮、输入框样式
- JS: 
  - `setSmartTitleText()` - 更新标题文本
  - `openSmartTitleEdit()` - 打开编辑模式
  - `setupSmartTitleEdit()` - 绑定事件
  - 支持 Enter/blur 保存、Escape 取消
- 保存机制：调用 `scheduleSave()` 走增量保存流程

### 验证
- [x] 需要实际测试重命名功能 —— 已由 fly_fixes_browser T6 场景自动验证（点编辑按钮 → 输入新标题 → Enter 保存 → 恢复原标题，断言 canvas.title 变更与恢复）

---

## T7: 整理按钮移左上角 ✅

### 状态
已完成

### 实现内容
- HTML: 按钮从 `.minimap-wrap` 移到 `.smart-top-left`
- CSS: 删除旧位置样式，复用工具栏按钮样式
- JS: 两处 `smartArrangeBtn` 引用更新

### 验证
- [x] 需要实际测试按钮位置和点击效果 —— 已由 fly_fixes_browser T7 场景自动验证（断言 `#smartArrangeBtn` 位于 `.smart-top-left` 工具栏内）

---

## 测试验证结果

### 单元测试
- test_project_image_generation_ui: 31 tests OK
- test_provider_contract_families: 25 tests OK
- test_firefly_generation: 15 tests OK
- test_banana_outpaint_request: 16 tests OK
- test_banana_image_size: 6 tests OK
- test_local_edit: 10 tests OK
- test_project_store / test_project_history / test_project_api / test_history_deletion_isolation 等（2026-08-27 批）
- **2026-08-27 批：7 模块 161 tests OK**

### 浏览器 e2e 测试（fly_fixes_browser.py）
- T1 扩图: 12/12 通过
- T2 PS 插件: 3/3 通过
- T3 局部编辑: 5/5 通过
- T4 历史同步: 5/5 通过
- T5 失败取回: 3/3 通过
- T6 重命名: 4/4 通过（2026-08-27 批新增，含二次重命名回归）
- T7 按钮位置: 2/2 通过（2026-08-27 批新增）
- T9 资源库删除: 10/10 通过（2026-08-27 批新增：资产在库 / 删除按钮 / 引用警告对话框 / 警告列出引用节点 / 资产库移除 / DELETE 响应 200 / UI 节点清理 / 服务端引用清理 / 墓碑键断言 / 媒体 404）
- **2026-08-27 批总计: 44/44 通过**

---

## 2026-08-27 Code Review 批（T1-T9 复核 + 13 项修复）

对 T1-T9 做双轴 code review（Standards + Spec），按用户拍板落地 9 项决策 + e2e 新发现 4 项修复：

1. **T9 资源库删除对齐 ADR**（方案 A：硬删除 + 引用清理 + ADR 0015）：服务端同事务清理画布/历史直接引用并写墓碑、引用检查端点与清理同规则（覆盖 T3 局部编辑节点）、base_revision 冲突防护 + 成功广播 + 前端同步 nodes、对话框 i18n、404 分支按钮状态、focusAndSelectNode 选中态清理。新增 `docs/adr/0015-user-initiated-deletion-of-project-media-asset.md`
2. **资源库批量清理 + 回收区入口**：记录为下一次方向（GitHub Issue）
3. **删除 core.py:5305/:5326 旧版 `project_image_request`/`project_video_request` 副本**（无 T8 归一化的死代码陷阱；同步清理 api_project_library.py:20 与 main.py:1677 两处 import）
4. **T5 重试连点防护**：`retryFailedGeneration` 拆分防重入外层（`node._retryInFlight` 同步标志）+ `retryFailedGenerationInner`
5. **删除调试残留**：smart-canvas.js:15440 console.log、conversation.py 3 处 print
6. **对话框硬编码中文 → i18n**（10 个 `smart.delete*` 键，zh/en）
7. **T6/T7 补自动 e2e**（不再依赖人工）
8. **本批记录 GitHub Issue**
9. **其余可选项全修**：T8 归一化提取 helper 消除两份重复；`outpaintForAspect2` → `outpaintForWriteback`；T1 底图并入加 `SMART_REFERENCE_IMAGE_MAX` 上限防护；删 `query_task_status` 死代码
10. **T6 二次重命名 bug（e2e 新发现）**：`smartTitleText` 为顶层 `const`，`finish()` 用新 span 替换输入框后引用仍指向已脱离文档的旧节点 → 第二次重命名时 `replaceWith` 无效、输入框永远进不了 DOM（第一次重命名后按钮永久失效）。改为 `let` + `finish()` 内更新引用。这正是人工测试（“待用户确认”从未实测）抓不到、自动 e2e 抓到的真实 bug
11. **e2e 测试驱动加固**：`tests/fly_fixes_browser.py` 增加 Windows GBK 控制台 UTF-8 重配置（断言文本含 ⚠️ 等字符时打印不再崩溃）
12. **T9 DELETE 500 bug（e2e 新发现）**：路由对同步函数 `project_store_call` 误用 `await`（未走 `asyncio.to_thread`），store 逻辑已完整执行后请求层抛 TypeError 变 500，前端靠 409 合并路径“兑底”同步了状态才没出事。已修为与 /original/delete 一致的 `asyncio.to_thread` 调用；e2e 增加 DELETE 响应码=200 与资产库移除断言，确保请求层故障不再被掩盖
13. **T9 生成输出归属误判 bug（e2e 新发现）**：前端 run 状态机给所有 smart-image 节点盖 `runStartedAt` 戳并回写，仅凭 run 字段会把「用户拖入图片的普通节点」误判为生成输出节点（写错墓碑键 `deletedOutputTombstones` 并触发输出归属回退，本应写 `deletedInputTombstones`）。归属判定加严：需同时满足 run 快照 + images 条目带 `output_id`（complete_media_generation 固化的生成输出身份）

---

## 修改文件清单

### 核心修复
- `app/routers/api_project_library.py` - T8 路由参数归一化 + T5 幂等键处理
- `firefly_projects.py` - T4 output_id 回写 + T5 _end_media_generation
- `static/js/smart-canvas.js` - T5 providerFailure + T9 缓存失效 + T1 扩图修复；2026-08-27 批：T9 删除流重写（base_revision/409/成功同步）+ 对话框 i18n + T5 重试防重入 + 参考图上限 + outpaintForWriteback 改名
- `app/routers/api_project_library.py` - T8 路由参数归一化 + T5 幂等键处理；2026-08-27 批：归一化提取 helper + 引用检查端点重写 + ProjectMediaDeleteRequest/delete 路由升级
- `firefly_projects_deletion.py` - 2026-08-27 批：新增 `delete_project_media_asset`（硬删 + 引用清理 + 墓碑 + 广播前置数据）
- `app/core.py` / `main.py` - 2026-08-27 批：删除旧版路由构造函数副本及对应 import
- `app/services/conversation.py` - 2026-08-27 批：删 3 处调试 print
- `static/js/i18n/smart-canvas.js` - 2026-08-27 批：10 个删除对话框键（zh/en）
- `docs/adr/0015-user-initiated-deletion-of-project-media-asset.md` - 2026-08-27 批：资源库媒体删除决策
- `static/css/smart-canvas.css` - T7 整理按钮样式
- `static/smart-canvas.html` - T6 重命名 + T7 按钮位置
- `CONTEXT.md` - 2026-08-27 批：新增「扩图（Outpainting）」领域词条

### 测试文件
- `tests/fly_fixes_browser.py` - 浏览器 e2e 测试驱动
- `tests/fly_fixes_browser_client.js` - 浏览器 e2e 测试客户端
- `tests/test_project_image_generation_ui.py` - T5 providerFailure 测试；2026-08-27 批：outpaint 时不覆盖显式 resolution 断言
- `tests/image_editor_func_browser_client.js` - 2026-08-27 批：T1 红底措辞（Remove red area and fill the scene）
