# Issue #267 结果（接管波）— fal queue-mode 图片集成 fal-all（topaz/upscale/image 系列）

**日期**：2026-10-29 · **工作区**：`D:/ff-wt/w267b` · **分支**：`issue/267` @ `34829a6`（已 push，且已被总控合并进 `origin/main` @ `33ab97b`）

## 接管说明

- 前一位 worker 死于 140 分钟时箱，**零 commit**——烧穿在通读 main 的绑定机制上。
- 接管时锚点由总控实测直给（fal 基座已在 main：`app/core.py` 的 `'fal'` 请求模式 + `FAL_QUEUE_BASE_URL` 契约注释、`app/services/generation.py`、`tests/test_fal_queue_image_mode.py`、`tools/configure_comfly_fal_topaz.py`；**main 上没有 `app/fal.py`**），未重复勘察，直接进保命线。
- 旧空壳 worktree `D:/ff-wt/w267` 已 `git worktree remove`，按要求改用 `D:/ff-wt/w267b`（`git worktree add -B issue/267 D:/ff-wt/w267b origin/main`，基线 `5e49721`）。

## 红点判定

- **任务书预判的 registry 红点不成立**：`tests/test_provider_contract_registry.py` 在 origin/main 基线即 GREEN——`tests/fixtures/provider_contracts/` 下 openai-mode:fal 家族合同（capability_matrix / parameter_mapping / final_request_golden / source_refs / request_shape / response_shape / execution_tests）已由先前波次随 fal 基座落进 main。保命线 = 验证通过，无需补合同。
- **真实当前红点（origin/main 既有、确定性）**：`tests/test_canvas_core_layer.py::CanvasCoreLayerTests::test_reverse_reference_count_does_not_grow`（约 line 468，棘轮上限漂移）。非本 issue 引入。

## 完成内容（三条熔断线）

1. **保命线 ✅**：registry 测试 GREEN（基线即绿，已验证记录）。
2. **第二条 ✅**（commit `34829a6`，3 文件 +173/-1）：
   - `app/data/model_specs.json`：`image_models` 29→31，登记 `fal-ai/topaz/upscale/image`（Topaz 高清修复）与 `fal-ai/topaz/upscale/image/generative`（Topaz 生成式高清修复）；`api_type: images_generations`、`supports_vision: true`、`async_mode: true`（队列提交→轮询→拉取）；aspect_ratio 选项 11 项与 `tools/configure_comfly_fal_topaz.py` 的 `COMFLY_ASPECT_RATIOS`（能力单一事实源）同集；**不声明** `response_format/size/quality/n/image_size`（fal 官方 schema 严格，多余字段 422）；`_last_updated` → 2026-10-29。
   - `app/services/generation.py`：队列轮询循环接入 **#235 共享终态语义**（复用，未另发明）——`status ∈ IMAGE_TASK_UNKNOWN_STATUSES`（NOT_FOUND/UNKNOWN 等）连续 `IMAGE_TASK_UNKNOWN_TERMINAL_AFTER`(=2) 次 → 502 终态 fail，文案显式带 `request_id`；出现可识别状态即重置计数（单次抖动容忍）；空状态不属于 unknown 集、不计数。
   - `tests/test_fal_queue_image_mode.py` +101 行、7 个新测试：`FalModelSpecRegistrationTests`（登记存在性 / 比例同集 / 无 422 参数）+ `FalQueueUnknownStatusTests`（连续未知终态带 request_id / 单次抖动恢复 / 空状态继续轮询 / 终态判定绑定 #235 共享常量对象）。全 mock，不联网。
3. **第三条 ❌（时间箱，登记尾票）**：routers/前端能力展示接线未做。**尾票**：spec 数据经现有 `routers/model_specs.py`（`/api/model-specs`）+ 前端 `smart-canvas.js` 的 `mergeSpecModels`（additive 合并）路径可自动流通，剩余为显式能力展示接线与端到端验证。

## 质量门

- **fast 门 ✅**：`python tools/quality_gate.py fast` → `{"mode": "fast", "verdict": "passed", "final_exit_code": 0, "duration_seconds": 16.219}`（编辑全部完成之后）。
- **全量 pytest（对照 origin/main 基线）**：1558 passed, 1 skipped, 2 failed——
  1. `tests/test_canvas_core_layer.py::test_reverse_reference_count_does_not_grow`：基线既有棘轮漂移（见红点判定）。
  2. `tests/test_canvas_coupling_report.py::test_total_matches_the_ratchet_ceilings`：同一棘轮漂移族；本 commit 为纯 Python/JSON 改动、未动任何画布 JS，棘轮口径（JS 反向引用计数）按构造不受影响。
  - 全量并行负载下另有一次 `tests/test_canvas_module_units.py::test_units_canvas_generation_tasks` 120s 子进程超时（flake）：该 node 脚本只加载画布 JS、与本 commit 无关，单独直跑全绿。
- **domain 门**：未跑（时间箱不够）——按任务书要求声明。

## 收口状态

- push：`issue/267` 与远端同步（Everything up-to-date）。注：总控"接管波固化"机制在 17:08:03 将工作区快照提交为 `34829a6`（commit message 标注"接管波固化，worker 到点残骸"）并 push，随后并入 `origin/main`（`33ab97b` merge #272 验证波）——本地工作树干净，无未提交内容。
- assign：#267 → `miaomiaozii` ✅。
- 时箱：100 分钟（~16:08 起，17:48 止，17:28 强制收尾）。实际 ~17:31 完成收尾，第三条未进 → 尾票登记如上。
