# Issue #271 实施结果（P0(#248)：自托管模型 provider 最小接入——OpenAI 兼容形态，假服务器 TDD）

worker: pi (w271b) · 分支 `issue/271` @ origin/main `5e49721` · 2026-10-29

## 范围熔断线完成情况（三条全部完成并各自 commit）

| 线 | 内容 | 状态 | commit |
|---|---|---|---|
| 1 保命线 | api_providers.json 自托管 provider 记录形态 + 假服务器（httpx.MockTransport，OpenAI 兼容 /v1/chat/completions）端到端出**文本** GREEN | ✅ | `ffb0565` |
| 2 | image 通路（假图像端点 /v1/images/generations）端到端出图断言 GREEN | ✅ | `751aa20` |
| 3 | model_specs 能力登记 + 契约族声明表对齐 GREEN | ✅ | `9ba253c` |

## 交付物

- `tests/fixtures/self_hosted_provider.json`（新）—— `api_providers.json` 的**记录形态**
  活样本（id=`self_hosted`、protocol=openai、image_request_mode=openai、
  base_url=`http://127.0.0.1:8318/v1`、`image_model_capabilities` 全量显式声明
  media_types/max_reference_images/arbitrary_size/arbitrary_aspect_ratio/
  arbitrary_resolution），与活样本 provider-ms5fzxu7 同形态，**不新增顶层字段**。
  说明：`data/api_providers.json` 是运行时用户数据（git 不跟踪，仓库 data/ 仅
  asset_library.json），故记录形态以「经真实 `main.py` normalize 管线的 fixture」交付，
  由测试加载进 patched `API_PROVIDERS_FILE` 验证。
- `tests/test_self_hosted_provider.py`（新，7 个测试全绿）——
  1. `test_registry_record_shape_survives_normalize`：记录形态经 normalize_provider 不被改写；
  2. `test_host_heuristics_do_not_rewrite_self_hosted`：启发式免疫（comfly/apimart/fhl/
     locked-rule/detect_image_request_mode 均不命中，effective_protocol/mode 保持 openai）；
  3. `test_unknown_provider_does_not_silently_fall_back`：`get_api_provider_exact`
     未知 id → HTTPException 400 显式报错（#248-B 语义，不回退 primary）；
  4. `test_chat_end_to_end_text_via_fake_server`：假服务器（/v1/responses 404 触发既有
     回退链）→ `resolve_chat_provider → chat_request_latest → chat_text_from_raw`
     端到端出文本，断言 Bearer key、model、URL host=127.0.0.1（零真实外连）；
  5. `test_image_end_to_end_via_fake_server`：`generate_ai_image(preserve_model=True)`
     → /v1/images/generations 出图 URL 断言 + 请求体（model/prompt/size）与 Bearer 断言；
  6. `test_capability_registration_routes_pinned_provider`：`route_image_generation`
     pinned provider → `GenerationRoute(protocol='openai', adapter='http')`；
  7. `test_capability_violation_is_reported_not_silently_passed`：
     reference_count=4 > max_reference_images=3 → `ModelRoutingError`
     报 `reference_count`（能力声明被路由层真实执行，非装饰）。

**产品代码零改动**（core.py / main.py / generation.py 均未动）——印证方案 §2 结论 1：
OpenAI 兼容形态下 P0 近零后端改动，缺口在声明与守卫而非请求通路。

## 验证

- `python tools/quality_gate.py fast`：✅ passed（三次，每次 commit 前）
- 全量 pytest（保命线后只跑一次）：`3 failed, 1562 passed, 1 skipped`，701s——
  失败集与 origin/main 时代基线**逐条一致**（均为既有失败，与本票无关）：
  - `tests/test_canvas_core_layer.py::CanvasCoreLayerTests::test_reverse_reference_count_does_not_grow`
  - `tests/test_canvas_coupling_report.py::CanvasCouplingReportTests::test_total_matches_the_ratchet_ceilings`
  - `tests/test_canvas_module_units.py::CanvasModuleUnitsTests::test_units_canvas_generation_tasks`
  零新增失败；新增 7 个测试全部通过。
- `tests/test_provider_contract_registry.py` + `test_terminology_guard.py` +
  `test_external_json_resources.py`：✅ 12 passed（声明表对齐 + 术语门「自有服务器」命名）
- `python tools/quality_gate.py domain`：首跑 7 项实质检查全部 passed（javascript-syntax / surface-contract / project-contract / provider-contract /
  native-browser-evidence / generation-lifecycle-evidence / egress-attribution），verdict failed 仅因
  worktree-preservation（门运行期间写了本 result.md）；工作区干净后重跑结果见文末。

## TDD 记录（RED→GREEN 真实发生）

1. L1：fixture 缺失 → `FileNotFoundError` RED → 建 fixture GREEN。
2. L2：`generate_ai_image` 默认 openai 分支 + httpx.AsyncClient MockTransport 注入
   直接 GREEN（零产品改动——预期内，验证既有通路）。
3. L3：RED 有真实价值——路由层校验 `resolution=''` 需要 `arbitrary_resolution` 声明，
   fixture 初版漏声明 → `ModelRoutingError(参数不兼容：resolution)` → 补声明 GREEN。
   （能力声明必须真实完整，ADR 0006 语义。）

## 设计决策与边界

- **术语**：代码/测试/f fixture 一律「自有服务器」，不用禁用词「本地生成/本地模型」
  （test_terminology_guard FORBIDDEN_TERMS 门绿）。
- **model_specs.json 不写入私有模型条目**：全局 SPEC-FIRST 表只登记第三方适配模型
  （nano-banana/gpt-image-2 系）；私有模型条目归属属方案 §6 未决问题 3
  （「私有模型能力由谁声明」），待维护者裁定，不在本票擅自扩面。
  provider 侧能力登记走 `image_model_capabilities`（本票已覆盖并路由验证）。
- **声明表对齐**：self_hosted 形态复用既有 `protocol:openai` 契约族 key，不新增
  protocol/mode 枚举 → `test_provider_contract_registry.py` 闭集 `expected` 无需变更
  （跑绿确认）。专属 `openai-provider:self_hosted` 契约族 fixture 属方案 248-D 子票。
- **R11 行为确认**：假服务器 /v1/responses 返回 404 → 既有 `chat_request_latest`
  静默回退链工作正常（多一次 404 往返）；「按 provider 关 prefer_responses」属
  方案 248-I 子票，未在本票改产品代码。

## 尾票（建议拆出，非本票范围）

1. **248-D 契约族**：`openai-provider:self_hosted` 专属契约 fixture
   （capability_matrix/parameter_mapping/final_request_golden + execution_tests）。
2. **248-C 参考图私有可达例外**：带参考图的自有服务器生成会走 ADR 0023 图床阶梯，
   需 ADR 0024 修订后才可放行（隐私边界，先于带参考图私有生成落地）。
3. **248-I 文本形态关 Responses 回退**：私有 vllm 常无 /v1/responses，按 provider/
   探测结果关 prefer_responses（省一次 404 往返 + 暴露真实错误）。
4. **248-E 前端最小配置面**：global-settings.js 暴露 image_request_mode + 端点覆盖。
5. **私有模型 model_specs 归属**（方案 §6 未决问题 3，需维护者裁定）。

## 环境备注

- worktree：`D:/ff-wt/w271b`（`D:/ff-wt/w271` 空壳已清理）；
  origin/main 基线全量日志在 `/d/ff-wt/w271b-baseline-logs/`（前 worker 产物，沿用）。
- core.py 零改动，未触碰 #235 的 IMAGE_TASK_* 区（冲突警示遵守）。
