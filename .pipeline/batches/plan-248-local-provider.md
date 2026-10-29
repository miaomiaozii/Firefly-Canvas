我将按研究清单逐项分析。先读取票面与仓库结构。## #248 实施方案（wayfinder 调研产出 · 只读分析，未改任何文件）

---

# 0. 票面与已签方向（验收对齐）

**票面事实**（`gh issue view 248`）：标题 `Wayfinder：本地模型 provider 接入（只接 API 与规则；独立可选，无自动降级）`，label `wayfinder:map`，2 条评论。

票面「待决问题」四条 → 本方案回答位置：

| 票面待决问题 | 回答 | 见 |
|---|---|---|
| 目标服务形态（vllm? OpenAI-compatible?） | 结论：P0 按 **OpenAI 兼容** 落地（零新协议），P1 用 **per-model request_mode** 承载自封装形态；两种形态共用同一 provider 记录 | §2 结论1/2、§3-P0/P1 |
| 模型注册进 `data/api_providers.json` 的形态 | 复用 `normalize_provider` 现有字段（`protocol` / `image_request_mode` / `model_request_modes` / `*_endpoint` / `*_model_capabilities`），**不新增顶层字段** | §1.1、§3-P0-A |
| 图片/视频能力映射；只接文本时是否同一注册表 | 同一注册表（`chat_models` / `image_models` / `video_models` 三张列表同一 provider 记录）；能力走 `*_model_capabilities` + `app/data/model_specs.json`；视频必须进 `VIDEO_EXECUTION_PROTOCOLS` 才能路由 | §1.2/1.4、§3-P1-C |
| 可用性探测与错误文案 | 复用 `/api/providers/test-connection` + `/api/providers/probe-async` 先例，P2 加 `probe()`/`capabilities()` 二分 + 60s 缓存 + 两条精确文案 | §3-P2 |

**评论 1（调研指令）**：要求对照 ComfyArt 的 `probe()` / `capabilities()` / `ProviderCaps(stateful,tools,attachments)` / `PROBE_CACHE_S=60` → 已在 P2 落实（参考 `docs/reference-comfyart-canvas.md:3224,3307,3336-3350,4617`；注意该文档 `:4477` 与 `:5370` 有「虚构段落」自审，引用只取真实事实段 §88）。

**评论 2（维护者方向，已签）**：模型跑在自有服务器 → 画布只做 API 接入；不做客户端显存/进程管理；**服务器 API 形态未定（可能 OpenAI 兼容、可能自封装）→ 方案必须给出 provider 注册表内的多形态适配层**。

---

# 1. 现状基座（file:line 事实，全部实测）

## 1.1 provider 注册表与配置面

- 注册表文件：`main.py:1106 API_PROVIDERS_FILE = os.path.join(DATA_DIR, "api_providers.json")`；密钥 env 文件 `main.py:1092 API_ENV_FILE = RUNTIME_ROOT/API/.env`。当前内容只有 2 条：`volcengine`（protocol=volcengine）与 `provider-ms5fzxu7`（protocol=openai，base_url `https://ai.comfly.org/v1`，带 `model_request_modes` + `image_model_capabilities[...].fal_request`）——**这就是「一张 OpenAI 兼容记录 + 每模型形态覆盖」的现成活样本**。
- `normalize_provider`（`app/core.py:841-905`）保留字段全集：`id,name,base_url,protocol,image_request_mode,image_generation_endpoint,image_edit_endpoint,video_generation_endpoint,enabled,primary,image_models,image_model_capabilities,chat_models,chat_audio_input,video_models,video_model_capabilities,recommended_models,available_models,model_selection_version,model_names,model_protocols,model_request_modes,ms_loras,rh_apps,volcengine_project_name,volcengine_region`。
- 校验：`PROVIDER_ID_RE = ^[a-zA-Z0-9_-]{2,40}$`（`app/core.py:154`）；`base_url` 只要求 `^https?://` → **回环/私网 http 是合法的**（`app/core.py:846-848`）。
- 协议/形态枚举：`SUPPORTED_PROVIDER_PROTOCOLS = {'openai','apimart','gemini','gemini-cli','volcengine','runninghub','codex'}`（`app/core.py:155`）；`SUPPORTED_IMAGE_REQUEST_MODES = {'openai','openai-json','openai-video-proxy','openai-responses','fal'}`（`:158`）；`PER_MODEL_REQUEST_MODE_OPTIONS = SUPPORTED_IMAGE_REQUEST_MODES`（`:164`）；`REMOVED_PROVIDER_IDS = {'modelscope','runninghub','jimeng'}`（`:156`）→ 新 provider id 不得复用这三个。
- 形态选择链：`effective_image_request_mode(provider, model)`（`app/core.py:746`）= `detect_image_request_mode` 主机/模型名启发式（`:2281`，目前只对 agnes 生效）→ `model_request_modes[model]` 单模型覆盖 → 全局 `image_request_mode`。协议侧同构：`effective_protocol`（`:2255`）读 `model_protocols`（`normalize_model_protocols :2233`），`FIXED_PROTOCOL_PROVIDER_IDS` 旁路。
- 端点覆盖：`provider_endpoint_url(provider, key, default_path)`（`app/services/providers.py:470-483`）支持绝对 URL 或「路径覆盖」（拼 base 的 scheme://host），并对 `/v1`、`/v1beta`、`/api/v3`、`/v2` 前缀去重。**自封装形态只需要三个 key 已覆盖 image/edit/video；任务查询端点没有覆盖位（见 §4 风险 R3）**。
- 密钥：`app/services/secret_store.py:57 encrypt_secret`（DPAPI，`dpapi:` 前缀），落盘加解密 `app/services/providers.py:500/515`；env 通道 `provider_env_key_value`（`:26`）。
- 前端配置面：`static/js/global-settings.js` `providerPayload()` / `providerRequestPayload()` 只暴露 `name / base_url / protocol / enabled / primary / image_models / chat_models / video_models / api_key`（+ `image_request_mode` 透传）。**端点覆盖、`model_request_modes`、能力表目前无 UI，只能改 JSON**（419 行文件，改动面小）。

## 1.2 能力声明与模型路由（单一事实来源）

- 能力归一化：`firefly_generation.py:252 normalize_image_model_capabilities`（键：`media_types,sizes,aspect_ratios,resolutions,min_width,max_width,min_height,max_height,max_reference_images,arbitrary_size/arbitrary_aspect_ratio/arbitrary_resolution,fal_request`）；`:286 normalize_video_model_capabilities`（+`qualities,min_duration,max_duration,max_image_references,max_video_references,max_audio_references,adapter_schema`）；未声明时 `_inferred_*_model_capabilities`（`:374`/`:319`）给保守默认。
- 路由：`route_image_generation`（`firefly_generation.py:463`）与 `route_video_generation`（`:566`）返回 `GenerationRoute(provider_id, provider_name, model, protocol, adapter, reason, request)`；失败抛 `ModelRoutingError(code='image_model_not_found' | 'no_compatible_video_model', incompatible_parameters=..., capability_hint=...)`。**视频侧硬门**：`if protocol not in VIDEO_EXECUTION_PROTOCOLS: mismatches.append("protocol")`（`firefly_generation.py:600`；集合定义 `:30 = {"openai","apimart","volcengine","runninghub"}`）→ 新形态不登记就永远路由不过，并给文案 `:633-636`。
- SPEC-FIRST 模型表：`app/data/model_specs.json`（`app/services/model_specs.py:27 _SPECS_PATH`），顶层 `_schema_version/_description/_last_updated/chat_models/image_models/video_models`；条目键 `api_type, async_mode, display_name, notes, parameters{参数: {type,default,options}}, supports_vision`。消费点：`app/core.py:4144 model_spec_for` → `:4157 route_image_from_spec` → `:4180 route_openai_image_request` 的 SPEC-FIRST 分支（`spec['api_type'] == 'images_generations'`）。前端经 `GET /api/model-specs`（`app/routers/api_models.py:53`）读同一表。
- 现有 provider 级主机启发式（**私有服务器必须免疫**）：`is_comfly_provider` 判定在 `app/services/generation.py:1427`（会强制 inline base64 + `response_format=url` + 丢 `resolution`）、`is_apimart_provider app/core.py:2268`、`is_fhl_provider :2272`、`is_yuli/is_agnes/is_lingjing`（按 base_url 子串）、`locked_recommended_provider_rule`（`:777`，按 host 强制 protocol/mode）。

## 1.3 三类生成的 provider 通路（新 provider 要挂的点）

**图片（画布/项目共用）**
1. `app/services/generation.py:1409 _generate_ai_image_impl` —— 特例协议早退（`:1421 modelscope`、`:1422 codex`、`:1424 gemini-cli`、`:1426 runninghub`、`:1428 protocol==gemini`、`:1430 volcengine`）。
2. 之后走 OpenAI 家族：`route_openai_image_request`（`:1441/1444`）→ 形态分支 `:1502 fal` / `:1511 openai-video-proxy` / `:1550 openai-responses` / `:1568 openai-json` / `:1578 apimart` / `:1589+ gpt-image-2 generations|edits`。
3. 超时：`:1473`（`AI_REQUEST_TIMEOUT = env REQUEST_TIMEOUT` 默认 1800，`app/core.py:216`；`IMAGE_TASK_TIMEOUT` `:220`）。
4. 任务查询/轮询：`wait_for_image_task :14`、`image_task_url_for_provider :657`、`fetch_image_task_payload :665`；终态集合 `IMAGE_TASK_SUCCESS_STATUSES` / `IMAGE_TASK_FAILED_STATUSES`（`app/core.py:247-248`）。
5. 任务 worker 与历史：`run_project_image_task :1919`（成功记录 `:1979`、失败记录 `:1993`）、`run_canvas_image_task :2051`、归档 `:1803/:1853`。
6. 路由文件：`app/routers/api_online_image.py`、`api_canvas_image_tasks.py`、`api_image_task_query.py:53`、`api_project_library.py`、`api_queue_status.py`。

**视频**：`app/services/generation.py:2612 execute_canvas_video`（先过 `video_adapter_incompatible_parameters :2377` 与 `video_adapter_label app/core.py:2709`；再按 `is_runninghub / is_kling / is_agnes / is_lingjing / is_yuli / is_apimart / volc proxy` 分支）；端点候选 `app/services/media.py:699 video_api_root`、`:709 video_submit_url_candidates`；项目侧 `generation.py:451 generate_project_video`、`:468 execute_project_video_generation`、`:485 preflight_project_video_providers`、`:530 project_video_adapter_incompatible_parameters`；resume 缺口在 `app/routers/api_project_library.py:1764-1779`（#235 已列）。

**文本/对话**：`app/services/conversation.py:169 resolve_chat_provider`（protocol→base：`/v1beta`、`/api/v3`、`RUNNINGHUB_LLM_BASE_URL`、否则 `/v1`；返回 `(base, hdrs, model)`）、`:219 chat_request_latest`（**Responses 优先、失败静默回退 `chat/completions`**，`:246`）、`:251 chat_text_from_raw`、`raw_usage` 回传 `:468`；入口 `app/routers/api_canvas_llm.py:67`。

## 1.4 契约族声明表 = 新形态的强制 TDD 锚点

`tests/test_provider_contract_registry.py:17-27` 断言：
`expected = {f"protocol:{p}" for p in SUPPORTED_PROVIDER_PROTOCOLS} ∪ {f"openai-mode:{m}" for m in SUPPORTED_IMAGE_REQUEST_MODES if m != 'openai'} ∪ {"runninghub-mode:ai-app","openai-provider:comfly","openai-model:gpt-image-2","openai-model:gemini-image","openai-model:nano-banana"}`，且每个 key 必须在 `tests/fixtures/provider_contracts/*.json` 里有一个契约（`capability_matrix{resolutions,aspect_ratios,references:"ordered-with-duplicates"}`、`parameter_mapping`、`final_request_golden`、`request_shape`、`response_shape`、`source_refs` 且 `sources[].retrieved_at` 为 ISO 日期），并在 `execution_tests` 里指向可导入的真实测试（同文件 `:47-72`）；`excluded_families` 必须精确等于 `REMOVED_PROVIDER_IDS`。

现成模板：`tests/fixtures/provider_contracts/supported-image-request-families.json`（`family_keys` 14 项 + `execution_tests` 映射到 `tests/test_provider_contract_families.py` 的具名测试），捕获器 `tests/provider_contract.py:47 ProviderContractCapture` / `:16 normalize_captured_request`（method/url/header_names/json/data/files+sha256）。

**含义**：给 #248 新增任何 `image_request_mode` 或 `protocol` 值，会**自动强制**一份契约族声明 + 一个真实执行测试。这是 P1 的红-绿锚点，不是负担。

**门**：`tools/quality_gate.py:459 fast` / `:460 domain`（含 `provider-contract` `:410-416`、`project-contract`、`generation-lifecycle-evidence`、`egress-attribution` `:444-450`）/ `:469 trial`。

## 1.5 用量计量（成本挂接点）

`firefly_usage.py:192 UsageTenantMixin.record_usage_turn(project_id, *, provider, model='', node_id='', usage=None, duration_seconds=None)` → 写 ops.db `usage_turns`（列：`input_tokens,output_tokens,cache_read_input_tokens,cache_creation_input_tokens,total_tokens,cost_usd,duration_seconds,created_at`，`firefly_usage.py:216-241`）；归一化 `:81 normalize_usage` / `:125 usage_has_data`；聚合 `:246 list_usage_summary`；API `app/routers/api_project_usage.py:19`。现有调用点：`app/services/agent_host.py:375`（pi sidecar）、`app/services/conversation.py:375 _record_codex_usage`；HTTP 生成侧只把 `raw_usage` 塞进结果（`app/services/misc.py:167`、`conversation.py:468`），**图片/视频生成没有 usage 落库**。

## 1.6 探测先例（P2 复用）

`app/routers/api_providers.py`：`:205 GET /api/providers`、`:209 PUT /api/providers`、`:429 POST /api/providers/test-connection`（`:352 url = f'{root}/chat/completions'`）、`:501 POST /api/providers/probe-async`（**用假 task_id `GET /v1/tasks/healthcheck_probe_do_not_submit`，靠 400/401/403/404 区分「端点存在/Key 无效/不支持异步」**）、`:647/:654 fetch-models`（上游 `:419` 端点标签 `/v1/models`、`/v1beta/models`、`/api/v3/models`、`/openapi/v2/models`）。探测原语：`:340 openai_compat_root_for_probe`、`:348 probe_openai_compat_bearer_endpoint`、`:366 probe_openai_models_endpoint`（`app/core.py:4970/4978/4996` 有镜像实现）。

---

# 2. 决定方案形状的六条结论

1. **P0 可以近乎零后端改动**：一台暴露 `/v1/models`、`/v1/images/generations|edits`、`/v1/chat/completions` 的自有服务器，用现有 `protocol=openai` + `base_url=http://<lan>/v1` + 端点覆盖 + `image_model_capabilities` 就能接入并路由通过。真正的缺口不在「怎么发请求」，而在**参考图通道（ADR 0023 会把图上传第三方图床）、usage 计量、健康/能力探测、契约族覆盖**。
2. **多形态适配层的正确维度是 `image_request_mode`（可 per-model 覆盖），不是新 `protocol`**。`protocol` 承载鉴权基座与固定协议语义（`FIXED_PROTOCOL_PROVIDER_IDS`），`mode` 承载请求/响应形状（已有 5 种形态互相印证）。自封装的 submit/poll 语义 → 新增一个 mode（如 `openai-async`）+ 任务查询端点覆盖位。
3. **视频形态必须登记进 `firefly_generation.py:30 VIDEO_EXECUTION_PROTOCOLS`**，否则 `route_video_generation` 永远报 `no_compatible_video_model`（`:600/:633`）。
4. **术语与 ADR 是硬约束**：`tests/test_terminology_guard.py:108-124` 把 `本地生成`、`本地模型` 列入 `FORBIDDEN_TERMS`（`test_no_forbidden_terms_in_code` :315 扫 code/i18n/tests）。票名可以叫「本地模型」，**代码/i18n/测试文案必须叫「自有服务器」/「私有部署服务商」**（CONTEXT.md `在线生成` 词条 `_Avoid_: 本地生成`）。ADR 0004 只允许「新的生成能力必须通过在线服务商接入 + 统一生成请求与结果模型」→ #248 与 ADR 0004 一致的前提是**推理在服务器侧**（已签）。ADR 0006 要求路由不改用户参数 → 能力声明必须真实，不能用 `_inferred_*` 的宽松默认冒充私有模型能力。
5. **不自动降级已在路由层成立，但有两处「静默换 provider」漏洞必须钉死**：`get_api_provider`（`app/services/providers.py:536-540`，id 不存在时静默回退 primary）与 `pick_chat_image_provider`（`app/services/conversation.py:395-406`，按候选列表自动挑）。私有 provider 必须走 `get_api_provider_exact`（`:548`）语义。
6. **长任务语义直接复用 #235**：私有服务器最常见的失败模式正是「提交成功、任务号存在、查询返回未知/404/自定义 code」。#235 已开处方（连续 N 次未知 → 终态 fail、404 → 结构化 not-found、resume 显式 not-found）。#248 不重复发明，只需保证新 mode 复用同一套状态机与文案（ADR 0017 不重复计费）。

---

# 3. 分阶段实施计划

## P0 — 一种形态的最小接入（OpenAI 兼容自有服务器：文本 + 图片）

目标：不新增协议/形态枚举，把「自有服务器」变成一条可保存、可选用、可测的 provider 记录；把隐私边界先修好。

| 子票 | 内容 | 红测试（先写） | 涉及文件 |
|---|---|---|---|
| **248-A** 私有 provider 注册样板与守卫 | 在 `data/api_providers.json` 增加一条 `self_hosted`（id 满足 `PROVIDER_ID_RE`，不撞 `REMOVED_PROVIDER_IDS`）记录：`protocol=openai`、`base_url=http://<lan>:<port>/v1`、`chat_models/image_models`、`image_model_capabilities` 全量显式声明（禁 `_inferred` 兜底冒充）；写「启发式免疫」断言：不得命中 `is_comfly_provider`（`generation.py:1427`）、`is_apimart/fhl/yuli/agnes/lingjing`、`locked_recommended_provider_rule`（`core.py:777`） | 新增 `tests/test_self_hosted_provider_registration.py`：断言 `normalize_provider` 后 protocol/mode 未被主机启发式改写；断言 `effective_image_request_mode == 'openai'`；断言 `route_image_generation` 在 pinned provider 下返回 `GenerationRoute(protocol='openai', adapter='http')` | `data/api_providers.json`、`app/core.py:841`（只读校验）、`tests/test_self_forbidden_*` 新测试 |
| **248-B** 不自动降级钉死 | provider 解析强制 exact 语义；私有 provider 不参与 `pick_chat_image_provider` 候选池 | 单测：传入未知 provider id → `HTTPException 400 未找到 API 平台`，**不得**回退 primary（`providers.py:538-540`）；单测：私有 provider 被 disabled 时不得被 `conversation.py:400` 自动替换 | `app/services/conversation.py:169,395`、`app/services/generation.py:1419`（`preserve_model` 已用 exact，另一分支待评估）、`app/services/providers.py:536` |
| **248-C** 参考图通道私有可达例外（ADR 0023 修订） | 引入「私有可达」阶梯：若 provider `base_url` host 为回环/RFC1918（复用 `app/services/media.py:508 is_loopback_host` 与 ADR 0023 的私网判定），参考图走 **站内直连/base64 内联（按上游 schema）**，**禁止**上传第三方图床；公网 provider 行为不变 | 契约式单测（用 `tests/provider_contract.py:47 ProviderContractCapture`）：私有 base_url 下捕获请求里**不含** catbox/litterbox 域名、且 `PUBLIC_MEDIA_BASE_URL` 未被使用；公网 base_url 下阶梯顺序不变（回归） | `app/services/media.py:508,580 fal_reference_public_url`、`app/services/generation.py:1418 require_project_media_values_scope`、`docs/adr/0023-*.md` + 新 ADR 0024 |
| **248-D** 私有 provider 契约族（OpenAI 兼容私有形态） | 为「OpenAI 兼容私有部署」补一份契约族声明（挂在既有 `protocol:openai` 之下，用 `openai-provider:self_hosted` 这类新 key 与 `openai-provider:comfly` 同构），含 `capability_matrix / parameter_mapping / final_request_golden / request_shape / response_shape / sources(retrieved_at) / execution_tests` | 先跑 `python -m unittest tests.test_provider_contract_registry` 确认新 key 缺失即红，再补 fixture + `tests/test_provider_contract_families.py` 里新增具名执行测试 | `tests/fixtures/provider_contracts/self-hosted-provider.json`（新）、`tests/test_provider_contract_families.py`、`tests/test_provider_contract_registry.py:17`（expected 集合需加 `openai-provider:self_hosted`） |
| **248-E** 前端最小配置面（可选，但建议） | `global-settings.js` 暴露 `image_request_mode` + 三个端点覆盖输入框（`image_generation_endpoint / image_edit_endpoint / video_generation_endpoint`），文案用「自有服务器」；协议下拉无需新增值 | 现有前端门：`tests/global_settings_browser.py` + `tests/canvas_boundaries_browser.py`（棘轮）；新增断言：保存后 PUT payload 含端点覆盖字段 | `static/js/global-settings.js:228-279 providerPayload/providerRequestPayload`、i18n 文案文件、对应 browser 测试 |

P0 门：`python tools/quality_gate.py fast` + `domain`（`provider-contract` 必须绿）。

## P1 — 多形态适配层（自封装 submit/poll；形态注册表）

目标：让「服务器 API 形态未定」成为**配置问题而不是代码分支膨胀**。设计原则：**一个 protocol（openai）× N 个 request_mode**，每个 mode 有契约族、有 capability 参数表、有终态化状态机。

| 子票 | 内容 | 红测试 | 涉及文件 |
|---|---|---|---|
| **248-F** 形态注册表化（去 if 链） | 把 `_generate_ai_image_impl`（`generation.py:1502-1600`）的 if/elif 形态分支收敛为 handler 注册表 `{mode: handler}`；新增 mode 只需注册 handler + 契约族。禁止再在 dispatch 里加 provider 特判 | 结构守卫单测：`IMAGE_REQUEST_HANDLERS.keys() == SUPPORTED_IMAGE_REQUEST_MODES`；既有的 5 个 mode 的 `final_request_golden` 契约不变（回归即绿） | `app/services/generation.py:1409-1735`、新 `app/services/provider_adapters.py`（handler 模块，需 `app/services/__init__.py:6-16` 风格 re-export）、`app/core.py:158,164,746` |
| **248-G** 新增 `openai-async`（或 `custom-submit-poll`）形态 | 语义：`POST {submit}` → `{task_id,status}` → `GET {task_endpoint}/{task_id}` → 结果媒体（url 或 b64）。任务查询端点新增覆盖位 `image_task_endpoint`（`normalize_endpoint_override` 复用）；状态映射走 `IMAGE_TASK_SUCCESS_STATUSES/FAILED_STATUSES`（`core.py:247-248`）；未知态按 #235 终态化 | 契约族 + 执行测试（submit→status→result 三段请求序列全部捕获比对，`ProviderContractCapture` 支持多响应脚本 `provider_contract.py:53`）；未知 code/404 → 终态 fail 且不重复提交 | `app/core.py:158,164`（枚举 + `normalize_image_request_mode`/`normalize_model_request_modes`）、`app/core.py:841`（新端点位）、`app/services/generation.py:14,657,665,1473`、`app/services/providers.py:470` |
| **248-H** 能力映射与模型表 | 私有模型写进 `app/data/model_specs.json`（`api_type`、`parameters{...}`、`supports_vision`、`async_mode: true`）+ provider 侧 `image_model_capabilities`（`max_reference_images`、`sizes/aspect_ratios/resolutions` 或 `arbitrary_*`）；SPEC-FIRST 分支（`core.py:4180`）生效验证 | `tests/test_aspect_ratio_validation.py:261`（能力单一来源覆盖 14 比例）+ `tests/test_external_json_resources.py:30-47`（specs 结构守卫）已有；新增：私有模型条目 → `route_image_from_spec` 参数风格断言；`route_image_generation` 在超参数请求下抛 `ModelRoutingError` 带 capability_hint | `app/data/model_specs.json`、`app/services/model_specs.py:27`、`firefly_generation.py:252,374,463` |
| **248-I** 文本形态（vllm 主路） | 私有 provider 的对话：`resolve_chat_provider` 走 `/v1`；**关掉 Responses 优先**（私有 vllm 常无 `/v1/responses`，`conversation.py:219` 现在是「试 Responses，异常静默吞掉再回退」——一次无谓往返 + 掩盖错误）。做法：provider/mode 级 `prefer_responses=False` 或按探测结果缓存 | 单测：私有 provider 下 `chat_request_latest` 只发 `chat/completions`（捕获请求数 == 1）；既有 Responses 行为回归不变 | `app/services/conversation.py:169,219`、`app/routers/api_canvas_llm.py:67` |
| **248-J** 视频形态（可延后到 P1b） | 若要接私有视频：`firefly_generation.py:30 VIDEO_EXECUTION_PROTOCOLS` 登记 + `execute_canvas_video`（`generation.py:2612`）注册 handler + `video_model_capabilities.adapter_schema` 承载参数表（fal 的 `fal_request` 同构先例 `firefly_generation.py:277-279`）；`video_submit_url_candidates`（`media.py:709`）支持覆盖 | `tests/test_video_generation_routing.py` 新增：未登记 protocol → `code='no_compatible_video_model'`；登记后路由通过 | `firefly_generation.py:30,286,566,600`、`app/services/generation.py:2377,2612`、`app/services/media.py:699,709` |

P1 门：`domain` 必绿；新增 mode 后 `trial` 至少跑 `generation-lifecycle-evidence`。

## P2 — 能力探测 / 健康检查 / 成本计量

| 子票 | 内容 | 红测试 | 涉及文件 |
|---|---|---|---|
| **248-K** `probe()` / `capabilities()` 二分 + 60s 缓存 | 对照 ComfyArt：`probe()` = `GET {base}/models`（10s 超时）→ `{available, version, logged_in}`；`capabilities()` 读声明表（不发网络）；`PROBE_CACHE_S=60` 等价缓存（key 含 base_url+api_key 指纹，配置变即重探）。两条精确文案（照 ComfyArt 语义）：「未配置服务器地址」/「地址可连但没有模型 —— 服务器未加载模型」；复用 `probe_openai_models_endpoint`（`api_providers.py:366`）与 `probe-async`（`:501`）的 400/401/403/404 判别 | 单测：stub 上游 → `available=True, models=[]` 必须返回「已连上但无模型」文案；不可达 → 「未配置/无法连接」两条不同文案；缓存命中不重复外连（计数器）；`/api/providers/probe` 新端点或复用 `test-connection` 返回结构断言 | `app/routers/api_providers.py:340-400,429,501`、`app/core.py:4970-5010`、`static/js/global-settings.js`（状态条，复用现有 `.provider-fetch-status`）、`tests/global_settings_browser.py` |
| **248-L** usage / 成本计量挂接 | 把私有服务器响应里的 `usage` 归一化后落 ops.db：在图片/视频/文本完成处调用 `record_usage_turn(project_id, provider=..., model=..., node_id=..., usage=raw['usage'], duration_seconds=...)`（`firefly_usage.py:192`）。私有模型常无 token 字段 → 允许只记 `duration_seconds` 或自定义 `cost_usd`（`normalize_usage` `:81`）；全 0 用量不记（`:125`）；遥测 best-effort 不得影响生成 | 单测（模板 `tests/test_codex_usage.py`、`tests/test_firefly_usage.py`）：私有响应带 usage → ops.db `usage_turns` 出现一行且租户正确；无 usage → 无行且生成成功；`GET /api/project-library/{id}/usage`（`api_project_usage.py:19`）聚合可见 | `firefly_usage.py:81,125,192`、`app/services/generation.py`（完成路径）、`app/services/misc.py:167`、`app/services/conversation.py:375,468` |
| **248-M** 长任务语义复用 #235 | 新 mode 必须复用同一轮询状态机与终态化：连续 2 次未知/404 → fail（文案带 task id）；不重复提交（ADR 0017）；resume 走「待恢复任务」词条 | 复用 #235 的红测试（stub provider 连续 unknown → 终态；404 → 结构化 not-found），把私有 mode 加进同一参数化矩阵 | `app/services/generation.py:14,657,665`、`app/core.py:247-248`、`app/routers/api_image_task_query.py:53`、`app/routers/api_project_library.py:1764-1779` |
| **248-N** 外连归属与安全边界 | 私有地址（回环/私网）在门证据里必须可归属；不得因为「本机服务」绕过 egress 守卫 | `python -m unittest tests.test_product_egress_attribution`（`tests/test_product_egress_attribution.py:24-25` 用 `.invalid` 探针，勿改成真实域名） | `app/core.py fetch_remote_media_bytes`、`tools/quality_gate.py:444-450` |

---

# 4. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | **超时语义不一致**：`generation.py:1473` 只对 `is_gpt2/is_apimart/{openai-json,openai-video-proxy,openai-responses,fal}` 收敛 read 超时到 `AI_REQUEST_TIMEOUT`；私有大模型同步等待极易超过默认 1800s（`core.py:216`），后端会抢先 502（`core.py:250` 注释已钉这条） | 新 mode 必须进 `:1473` 的长耗时白名单；`IMAGE_TASK_TIMEOUT`（`:220`）作为轮询截止单一来源；私有服务器建议走异步形态（P1-G）而非同步长连接 |
| R2 | **长任务/未知态**：私有服务最常出现「查询返回自定义 code / 404」，落进 #235 描述的「未知态空转到 deadline → 504」 | 248-M 复用 #235 状态机与文案；不要为新 mode 单写一套轮询。**依赖关系：248-M 应在 #235 落地后或与其并行同矩阵** |
| R3 | **任务查询端点无覆盖位**：`normalize_provider` 只有 image/edit/video 三个端点覆盖（`core.py:841` 起），自封装的 `GET /tasks/{id}` 无法配置 | P1-G 一并加 `image_task_endpoint`（+ `video_task_endpoint`），并同步进契约族 `parameter_mapping` |
| R4 | **隐私泄漏（最严重）**：ADR 0023 阶梯第 2 级会把私有模型的输入图上传 catbox/litterbox 第三方图床；第 3 级 `PUBLIC_MEDIA_BASE_URL` 会把局域网画布暴露成公网 | 248-C 必须先于任何带参考图的私有生成落地；ADR 需要新决策（建议 ADR 0024「私有可达上游的参考图阶梯例外」），因为 ADR 0023 明文「不做 base64 内联」是针对 fal schema 的理由，对自有 vllm 不成立 |
| R5 | **契约族门强制成本**：任何新 protocol/mode 都会让 `tests/test_provider_contract_registry.py:17` 立刻红 | 把「fixture + 执行测试」当成 P0/P1 子票的 DoD，而不是后置补丁；fixture 的 `sources[].url` 用服务器自己的 OpenAPI/文档地址，`retrieved_at` 必须 ISO 日期 |
| R6 | **主机启发式误伤 / 静默改形态**：`is_comfly_provider`（`generation.py:1427`）、`locked_recommended_provider_rule`（`core.py:777`）、`detect_image_request_mode`（`:2281`）都能按 host 改写行为 | 248-A 的「启发式免疫」守卫测试；私有 provider 的 id 与 name 不进 `LOCKED_RECOMMENDED_PROVIDER_RULES`（`core.py:777-789` 按 host/name/base_url 全等匹配，需断言不命中） |
| R7 | **违反「不自动降级」**：`get_api_provider` 未知 id 静默回退 primary（`providers.py:538-540`）、`pick_chat_image_provider` 自动挑候选（`conversation.py:395`） | 248-B；私有 provider 一律走 `get_api_provider_exact`（`:548`）；路由失败保持 `ModelRoutingError` 直抛，不做 failover |
| R8 | **术语门会打回命名**：`本地生成`/`本地模型` 是 `FORBIDDEN_TERMS`（`tests/test_terminology_guard.py:108-124`，扫描 test `:315`） | 统一命名：**领域词「自有服务器」/「私有部署服务商」**；provider id 用 `self_hosted`；票面/ADR 里描述背景时可用「本地模型」但代码与 UI 文案不用 |
| R9 | **前端耦合棘轮**：前端反向引用棘轮已收紧到 242/959/170/805（commit `de4e9c5`，ADR 0020 `docs/adr/0020-*.md`） | 前端只做 `global-settings.js` 内字段扩展 + i18n 文案，不新增对 `smart-canvas.js`/`canvas-core.js` 的反向引用；`tests/test_canvas_core_layer.py` 守卫 |
| R10 | **成本不可见**：私有模型无账单，若 usage 不落库则「用了多少」完全无感（ops.db usage 租户是 ADR 0021 白名单四类之一） | 248-L：至少记 `duration_seconds`；服务器若回 token 则走 `normalize_usage`；`cost_usd` 允许由用户在 provider 配置里自定义单价（后续票） |
| R11 | **文本形态的隐藏回退**：`chat_request_latest`（`conversation.py:219-249`）先试 `/responses`、异常吞掉再 `/chat/completions` → 私有 vllm 每次多一次失败往返且掩盖真实错误 | 248-I：按 provider/探测结果关掉 `prefer_responses`；把「Responses 不支持」记为可观测状态而不是静默 |

---

# 5. 明确不做（对齐已签方向）

- 不做客户端显存/进程/模型加载管理；不引入本地推理运行时或工作流执行（ADR 0004）。
- 不做自动降级 / failover / 自动换 provider（票面已签；见 R7 的反向加固）。
- 不新增第二套 provider 注册表：文本、图片、视频共用 `data/api_providers.json` 的三张模型列表 + 两张能力表；`model_specs.json` 仍是模型能力单一事实来源。
- 不在代码里写死私有服务器地址、端口或任务端点（全部走 `*_endpoint` 覆盖位 + `*_model_capabilities` 参数表）。

---

# 6. 建议拆票顺序（可直接建票）

```
248-A 私有 provider 注册与启发式免疫守卫      [P0]
248-B 不自动降级：exact provider 解析钉死      [P0]
248-C 参考图私有可达阶梯 + ADR 0024            [P0，前置 C 需签 ADR]
248-D 私有 provider 契约族（openai 兼容形态）  [P0]
248-E 全局设置：形态与端点覆盖字段             [P0，可并行]
248-F 图片形态 handler 注册表化（去 if 链）    [P1]
248-G openai-async 自封装 submit/poll 形态     [P1，依赖 248-M 状态机]
248-H 私有模型进 model_specs.json + 能力表     [P1]
248-I 私有文本形态（关 Responses 回退）        [P1]
248-J 私有视频形态（VIDEO_EXECUTION_PROTOCOLS）[P1b，可延后]
248-K probe()/capabilities() + 60s 缓存        [P2]
248-L usage 租户挂接（成本/时长计量）          [P2]
248-M 长任务终态化矩阵复用 #235                [P2，Blocked by #235]
248-N 外连归属与安全边界                       [P2]
```

**未决问题（需维护者裁定，建议在票评论里签）**
1. 自封装形态的 submit/poll 具体报文（需服务器 OpenAPI 或一段真实响应样例）——决定 `openai-async` 契约族的 `final_request_golden`。
2. ADR 0023 的私有可达例外是否立项为 ADR 0024（不签则 248-C 无法通过）。
3. 私有模型能力由谁声明：手工写 `image_model_capabilities` / `model_specs.json`，还是 P2 从服务器 `/v1/models` + `/v1/models/{id}` 自动探测回填（ComfyArt 的 `basename→相对子路径` 回传模式，见 `docs/reference-comfyart-canvas.md:4617`）。