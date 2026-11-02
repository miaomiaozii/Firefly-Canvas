# #273 edit_* 能力选型 spike（erase / matting / outpaint）— 决策方案

时箱 70min · 只读 spike（未改代码、未建 worktree）· 仓库 D:\Firefly-Canvas · 票面 #273 OPEN，labels `ready-for-agent` + `blocked`
硬约束复核：不走 fal.ai；不接 ComfyArt（#273 已 blocked，等本地能力就位）；本地模型既定方向=服务器自托管 + 只接 API 进画布（#248 已闭环 @wave1-live）。

## 0. 结论（决策表）

| 能力 | 推荐路线 | 落点（现有资产） | 能否 B 打底 / A 增强 | 拆票 |
|---|---|---|---|---|
| canvas.edit_erase | **A（包既有 local-edit 链路 + 填真实 adapter）** | `POST /api/project-library/{pid}/local-edit[-tasks]` `app/routers/api_project_library.py:3552-3645`；`local_edit.py` 合成/蒙版模块 | 成立：B 只在纯色/平坦背景可用（实测 PSNR 38-56 dB，纹理背景 25 dB 明显糊）→ 只作降级兜底，不作主路 | 票 A1 |
| canvas.edit_matting | **只能 A（站内零分割能力）** | 无任何分割/透明背景能力；`model_specs.json` 33 模型 `api_type` 全为 `images_generations` | **不成立**：B 只有 cv2.grabCut 级（硬边、无 alpha、需用户先验），达不到「抠图」票面语义 | 票 A2（前置=能力确认票 A0） |
| canvas.edit_outpaint | **B（纯 PIL 复刻站内红底扩图链）+ generate.trigger** | 前端红底合成链 `static/js/canvas-image-editor.js:1944-1987`；服务端只需 PIL pad+paste+`node.outpaintSize` | **不需要 A 增强即成立**：质量上限=站内手工 UX 的既有上限（模型 img2img + 红底提示词），不是「低质量兜底」 | 票 A3（可立即做） |

一句话：outpaint 是纯搬运（今天手工已有，缺 agent 工具面）；erase 缺的是「真实模型 adapter」而不是缺管道；matting 缺的是「能力本身」，必须服务器侧先就位。

## 1. 票面原话与既有意图（`gh issue view 273`）

- 范围 1：`canvas.edit_erase / canvas.edit_matting`＝「包装 local-edit 既有链路（POST /api/project-library/{project_id}/local-edit，mode∈{modify,retain}，app/routers/api_project_library.py:3552）成 agent 工具；复用 `_app_main.local_edit_adapter` 边界；写操作走确认门（#242 语义，`CONFIRMATION_GATED_TOOLS` + `_confirm_gate`）」。
- 范围 2：`canvas.edit_outpaint`＝「包装 static/js/canvas-image-editor.js:1934 applyImageOutpaint 红底合成链（服务端 PIL 复刻合成 + node.outpaintSize + 提示下一步 generate.trigger）」。
- 规范：ADR 0022 声明表双面一致（`api_mcp.py` TOOLS `additionalProperties:false` + `_PARAM_*` 片段 + `TOOL_IMPLS` + `canvas-tools.ts` registerTool）；REST core 放 `api_agent_tools.py`；错误文案带下一步建议；只读不确认、写必确认；TDD stub（mock 网络/adapter）；`tests/test_mcp_endpoint.py:183` 工具面守卫 `assertEqual(len(tools), 22, names)`（**票面写「当前 19」已过时**：`TOOLS` 是 **list**，`api_mcp.py:305`，实测 22 项 = 13 canvas.* + 3 generate.* + 3 media.* + 3 viewport.*；`app/agent_extensions/canvas-tools.ts` 同为 22 → 三项落地后应为 25）。`CONFIRMATION_GATED_TOOLS`（`api_mcp.py:90`）现为 5 项：delete_node / media.delete / edit_crop / edit_rotate / edit_flip。
- 验收原话：「每个新工具 stub 测试；全量 pytest 对照 origin/main 基线零新增回归；fast+domain 过门；edit_* 浏览器证据走 trial（#167/#185 先例）」。
- 【范围收窄】批注原话：「本票只剩能力型三项：erase/matting/outpaint——前置是能力选型 spike（ComfyArt inpaint 工作流 vs 本地 fill），先 spike 再拆票。」→ **本方案把该 spike 的前置选项改写为「自托管 OpenAI 兼容端点 vs 本地 fill」**，ComfyArt 分支作废。

## 2. 决定性发现（file:line）

**2.1 局部编辑管道已完整，但模型侧是测试替身——这是 #273 的真阻塞点。**
`app/core.py:339 def _offline_local_edit_adapter(request: Dict[str, Any]) -> bytes` docstring：`"""Deterministic fake retained as an explicit test seam."""`（改左上角一个像素后返回）。全站无生产赋值点（`grep local_edit_adapter =` 仅命中 `tests/*`）。所以「包装 local-edit 成 agent 工具」若直接落地，得到的是**假模型结果**：管道/资产/节点/历史都是真的，画面内容不变。
链路已就绪的部分（`app/routers/api_project_library.py:3552-3645`）：mode 校验 `{'modify','retain','局部修改','局部保留'}`；`ProjectLocalEditRequest`（`original_asset_id / reference_asset_ids / prompt / selection / aspect_ratio / size / resolution / feather_radius / result_node_id / base_revision`）；角色连线校验（`kind=='input'` + `role∈{original,references,prompt}`，原图必须恰好 1 个资源）；资产四件套落库 `local-edit-input.png / selection.json|preserve-mask.png / model-result.png / local-edit-output.png`；`NODE_TYPE_LOCAL_EDIT` 节点 + canvas history 记录 + `RevisionConflict`→409。

**2.2 ADR 0007（`docs/adr/0007-local-image-compositing.md`）锁死了蒙版方向**：「**两种模式都不向模型发送蒙版**，也不改写上游原图节点」；局部修改只发「固定比例矩形选区裁出的局部图」，局部保留发「完整原图」+ 本地自由形状保护蒙版回合成（`255=恢复原图 / 0=保留模型结果`）。→ erase 的合规做法是 **rect 选区裁图→模型重画→本地羽化回拼**（`local_edit.py:134 composite_rect` / `:147 composite_preserve` / `:113 rasterize_preserve_mask` / `:102 rectangular_mask`），**不是**往模型发 mask。若要发 mask，需新 ADR 修订。

**2.3 生成面另有「发 mask」的既有合同（与 ADR 0007 并存、不同通路）**：`app/services/generation.py:1500-1504` 取 `image_edit_endpoint`（默认 `/v1/images/edits`）并按 `role=='mask'` 或文件名 `*_mask.png` 分离 mask 引用；`:1640-1657` 用 multipart 发 `image`（可多个）+ `mask`（**只取 mask_refs[0]**）；前端已能画蒙版并上传：`static/js/canvas-image-editor.js:1992-1997`（`uploadCroppedBlob(blob, `${base}_mask.png`)` → `node.images.push({url, name, role:'mask'})`）。→ **A 路线的传输层已经写完了**：自托管服务只要暴露 OpenAI 兼容 `/v1/images/edits`（multipart `image`+`mask`），注册 provider 即可用，无需新 HTTP 代码。

**2.4 provider 现状（运行时 `data/api_providers.json`）**：3 个 provider——`volcengine`(ark)、`provider-ms5fzxu7`(ai.comfly.org/v1，protocol openai，image_request_mode openai，**25 个 image_models**，含 `gpt-image-1.5`、`gpt-image-2`、`qwen-image-edit`、`flux-kontext-pro`)、`moss`(api.mosi.cn)。**没有任何自托管/localhost provider**，三个 provider 的 `image_edit_endpoint` 全为空（走默认 `/v1/images/edits`）。`app/data/api_providers.json` 只有测试夹具 `secret-test`。

**2.5 能力登记缺 mask/edit 维度**：`app/data/model_specs.json` 33 个 image_models 的 `api_type` 全是 `images_generations`（无 edits/inpaint 分类）；`firefly_generation.py:252-282 normalize_image_model_capabilities` 只认 `media_types/sizes/aspect_ratios/resolutions/min_*_/max_*_width|height/max_reference_images/arbitrary_*/fal_request`。→ 要按「会不会吃 mask」路由，需在此加能力键（如 `supports_mask_edit`）并进 `_incompatible_parameters`/`route_image_generation`（`firefly_generation.py:463`）。同时 ADR 0006 规定路由**不得改写用户参数**，因此 outpaint 的尺寸/比例必须原样送达（已闭 issue #187「扩图分辨率被丢弃」就是这条的地雷）。

**2.6 B 路线依赖与实测上限（只读实验，见 §8）**：`import cv2` **可用**（cv2 **4.13.0**，`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\...\site-packages\cv2`，Python 3.12.10），但 `requirements.txt` **未声明 opencv**（只有 `pillow` + `rapidocr-onnxruntime==1.4.4`，注释明确它「非 ADR 0004 的生成推理运行时」）；离线 wheel 缓存 `packages/` 里也**没有 opencv 的 cp314 wheel**（缓存为 cp314，说明部署侧 Python 与本机 3.12 不同）→ 用 OpenCV 必须补依赖声明+目标版本 wheel。onnxruntime **1.27.0**（仅 `CPUExecutionProvider`）+ numpy 2.3.5 已在（rapidocr 带入），但仓库内 **无任何 .onnx 权重**。PIL **12.2.0**，无 inpaint/分割 API。
实测（512×512 合成、含真值）：`cv2.inpaint` 擦除 PSNR（越高越好，>30 近乎无痕）— 纯色背景 TELEA 38.81 / NS ∞；渐变背景 TELEA 38.31 / NS 56.45；**纹理背景 TELEA 25.02 / NS 24.93（meanAbsErr≈11，明显涂抹）**。`cv2.grabCut` 抠图 IoU=1.000、0.21s @512² CPU，但**仅硬边二值输出（无 alpha）、需用户先验（rect/笔画）**，且本环境 cv2 4.13.0 的模型缓冲必须是 `(1,65) float64`（文档值 39 会报 `GMM::GMM` 断言）；笔画初始化若不留背景样本会报 `!bgdSamples.empty() && !fgdSamples.empty()`。合成图 IoU=1.0 不代表真实照片可用（发丝/半透明必然掉）。
ADR 侧红线：`docs/adr/0004-online-generation-without-local-runtime.md`「不再保留 ComfyUI、**本地模型运行**或工作流执行能力」「新的生成能力必须通过在线服务商接入……允许直接 HTTP 接口或服务商 CLI 适配器……前提是**模型推理发生在远程服务中**」。→ grabCut/inpaint 属经典图像处理（与 ADR 0007 的本地合成同类），**不触发** 0004；但「本地跑 u2net/birefnet ONNX 抠图」**触发** 0004，需先立 ADR 修订票，否则 B 路线的 matting 直接违宪。

**2.7 前端已有蒙版笔刷与红底扩图 UX，outpaint 无需新 UX**：`applyImageOutpaint`（`static/js/canvas-image-editor.js:1944-1987`）＝ 显示坐标→原图分数坐标换算（`editScaleRectToNatural`，#255 骨架 `static/js/canvas-geometry.js:1-4`）→ 新建 `outW×outH` 画布 → **`#ff0044` 纯色填充扩图区**（注释：红色在真实场景少见，提示词「remove the red background/area」更稳）→ 贴原图 → 上传 `${base}_outpaint_${timestamp}.png` → `importProjectMediaReference` 换 `asset_id` → `applyOutpaintSizeToSmartParams(outW,outH)` + 提示词草稿 `Remove red area and fill the scene`；生图时 `buildPromptRequest` 把合成图当参考图发给模型。`validOutpaintSize` `:85-91`、`withOutpaintDisplaySettings` `:93-113`（`outpaintResolutionLocked`）。**注意：站内 outpaint 从不发 mask，纯靠红底 keying + 提示词**，与 ADR 0007 一致。

**2.8 测试门会阻断真实网络**：`tools/quality_gate.py` MODE_BUDGETS（fast 30s / domain 120s / trial 300s / release 900s），domain 门由 `sitecustomize` 硬阻断「非 loopback 与真实 provider 可执行文件」（`docs/agents/navigation-index.md:55,66`）。→ A 路线的三项工具**必须**能以 mock adapter / mock httpx 通过 fast+domain；真实自托管调用只能在 trial 或人工验证里出现。测试命名沿用 `tests/test_mcp_canvas_edit_266.py`、`tests/test_mcp_canvas_edit2_266.py`、`tests/test_local_edit.py`（`:103,141,167,180,214` 用 `patch.object(main, "local_edit_adapter", fake)`）。

## 3. 逐项能力评估

| 项 | B（本地 PIL/OpenCV）能做到 | A（自托管经 #248）需要服务器侧具备 | 合同要点 | 主要风险 |
|---|---|---|---|---|
| erase | 平坦/渐变背景可用（38-56 dB）；纹理背景 25 dB 涂抹；无「语义」擦除，只能填色/邻域扩散 | 二选一：(a) 指令式编辑模型（comfly 已有 `qwen-image-edit`/`flux-kontext-pro`/`gpt-image-*`，走 `image_edit_endpoint`，**不发 mask**，与 ADR 0007 兼容）；(b) 真 inpaint（SD-inpaint / anyrel 类）需自托管服务实现 `/v1/images/edits` 的 `image`+`mask` multipart | 复用 `local_edit_adapter(request_data)`（`request_data` 见 `api_project_library.py:3616`，含 `project_id/input_asset_id/mode/image/references/prompt/selection/aspect_ratio/size/resolution/source_size`）→ 返回 PNG bytes 即可，**合成/落库/节点由既有代码完成** | 默认 adapter 是假 stub（§2.1）；`modify` 只支持矩形选区，自由形状 erase 需走 `retain` 且语义反直觉（保护周围而非标记对象） |
| matting | 只有 `grabCut` 级：硬边、二值、要用户 rect/笔画先验；无 alpha/发丝 → **达不到票面语义** | 需服务器侧具备**分割/抠图服务**，两种形态：(a) OpenAI 兼容 `/v1/images/edits` 返回 RGBA（模型侧透明背景）；(b) 独立分割端点（返回 mask PNG），站内再 `putalpha` | local-edit 两模式都不产透明通道；但 `local_edit.py` 全程 RGBA、`encode_png`（`:21`）保 alpha → **输出 RGBA 的通道是通的，缺的只是 mask 来源** | 唯一无现成落点的一项；本地 ONNX 撞 ADR 0004（§2.6）→ 必须先 A0 定形态 |
| outpaint | **完整够用**：PIL `new(outW,outH,(255,0,68))` + `paste` 复刻红底链，登记资产、写 `node.outpaintSize`，再 `generate.trigger` | 不需要（红底 keying 已是产品级 UX）；将来换真 outpaint 模型属增强票 | 服务端复刻须守 ADR 0007 坐标合同（整数半开区间 `[left,right)×[top,bottom)`，视口只换算一次）+ ADR 0006（不得改用户 size/aspect；已闭 #187 是地雷）| 红底可能被模型当物体而非扩图区；`outpaintSize` 与 `size/resolution` 取舍已有规则在 `static/js/canvas-generation-tasks.js:167-179`，服务端复刻不得反向破坏 |

## 4. mask 传递合同（三套并存，#273 只能选一套做主线）

| 合同 | 位置 | 形态 | 用途 |
|---|---|---|---|
| ① local-edit selection（**不向模型发蒙版**） | `ProjectLocalEditRequest.selection` + `local_edit.py:47 validate_rect` / `:113 rasterize_preserve_mask` / `:102 rectangular_mask` | 原图像素坐标 rect，或 `{type:'hand|lasso|curve', points, width}` 自由形状；持久化为 `selection.json` / `preserve-mask.png` | erase（modify/retain）及一切 ADR 0007 兼容编辑 → **建议 #273 主线** |
| ② 生成面 role mask | `app/services/generation.py:1502-1504, 1653-1655`；前端 `static/js/canvas-image-editor.js:1992-1997` | 项目媒体里 `role:'mask'` 或 `*_mask.png` 引用；multipart 字段名 `mask`，**只取 mask_refs[0]**；URL 必须能被 `app/services/storage.py:69 output_file_from_url` 解析（仅 `/output/`、`/assets/`、`/api/storage-files/`、project-media-adapter scheme） | A 路线真 inpaint（若走 images/edits） |
| ③ agent 工具入参（现 edit_crop 基准） | `app/routers/api_mcp.py:317 _PARAM_RECT`；`app/routers/api_agent_tools.py:1009 crop_node_image_core` | `rect {x,y,width,height}` 原图像素；图像按 `asset_id` 从项目库取（`_load_node_image:925`） | 新 edit_* 的入参形状基准 |

建议：三项 edit_* 工具入参一律走 **③**（rect/strokes + asset_id 取图，与 #266 一致、测试可 mock），内部转成 **①**；只有「真 inpaint 增强票」才引入 **②**，并同时补能力位（§2.5）。

## 5. 拆票建议（4 票；「B 打底 + A 增强」只对 erase/outpaint 成立）

| 票 | 标题建议 | 内容要点 | 前置 | 验收 |
|---|---|---|---|---|
| **A0**（能力确认，先立） | Spike/Decision(W2)：edit_matting 能力形态与 ADR 0004 边界 | 定「远程分割服务 vs images/edits 出 RGBA」；若考虑本地 ONNX 则写 ADR 修订；顺带定 erase 的真实 adapter 目标（comfly `qwen-image-edit`/`flux-kontext-pro` 指令式，或自托管 inpaint 服务） | 无 | 结论评论 + `docs/adr/` 新增/修订 |
| **A1** | Feat(W2)：canvas.edit_erase（包 local-edit + 真实 adapter 落位） | `api_agent_tools.py` 加 `erase_node_image_core`（复用 `_node_image_for_edit:999`、`_load_node_image:925`、`_replace_node_image:939`），经 `_app_main.local_edit_adapter`；`api_mcp.py` TOOLS + TOOL_IMPLS + `_confirm_gate` + `CONFIRMATION_GATED_TOOLS:90`；`app/agent_extensions/canvas-tools.ts` 双面一致；**并装上 `_offline_local_edit_adapter` 之外的真实 adapter**（provider→`image_edit_endpoint`，走 `route_image_generation`，不改用户参数） | A0（erase 分支） | stub 测试（patch adapter，命名沿用 `tests/test_mcp_canvas_edit_266.py` 风格）+ 守卫 22→23（`tests/test_mcp_endpoint.py:183`）+ fast/domain + trial 浏览器证据 |
| **A2** | Feat(W2)：canvas.edit_matting（透明背景输出） | 形态取决于 A0：新增 mask/RGBA adapter；输出 RGBA PNG（`encode_png` 已保 alpha）；B 侧 grabCut 只可作「硬边快速抠图」可选项，票面须写明上限与 cv2 `(1,65)` 坑 | **A0 关闭** | 同上，守卫 →24 + 透明通道断言（沿用 ADR 0007 的 `0→156` / `64→0` 探针风格） |
| **A3**（无 A 依赖，可立即开） | Feat(W2)：canvas.edit_outpaint（服务端 PIL 红底扩图） | 服务端复刻 `applyImageOutpaint`：PIL 填 `#ff0044` + paste → 资产入库 → 写 `node.outpaintSize`/比例 → 返回「下一步调 generate.trigger」；不自动消耗生成 | 无 | stub 测试 + 守卫 →25 + fast/domain + trial 浏览器证据；断言 size/resolution 未被改写（#187 回归）；写操作（入库/写节点）必须进 `CONFIRMATION_GATED_TOOLS` |

## 6. 最大技术风险（按严重度）

1. **自托管模型可用性时点（A 路线的总开关）**：`data/api_providers.json` 里没有任何自托管/localhost provider（只有 volcengine / comfly / moss），且默认 adapter 是测试替身（`app/core.py:339`）。若三项都押「等自托管起来」，#273 全部悬空。缓解：A3 先落（零模型依赖）；A1 先用已注册 provider 的指令式编辑模型跑通（符合 ADR 0004「推理在远程」），自托管到位后**只换 adapter 配置、不改工具面**。
2. **mask 合同三套并存**（§4）+ `mask_refs[0]` 只取一张 + mask URL 必须落在 `app/services/storage.py:69 output_file_from_url` 可解析前缀。agent 侧传任意 URL 会**静默丢 mask**。缓解：工具只收 rect/strokes/asset_id，服务端自己栅格化 mask（①→②），不可解析时显式 400 带下一步建议。
3. **能力登记缺 mask/edit 维度**（`firefly_generation.py:252-282` 只认 media_types/sizes/aspect_ratios/resolutions/dims/max_reference_images/arbitrary_*/fal_request；33 个模型 `api_type` 全是 `images_generations`）→ 路由无法判断模型吃不吃 mask，会 400 或静默忽略。缓解：加能力键（如 `supports_mask_edit`）并入 `_incompatible_parameters` / `route_image_generation`（`firefly_generation.py:463`）；缺能力时按 ADR 0006 停请求而非降级。
4. **违宪风险**：往模型发蒙版与 ADR 0007 现文「两种模式都不向模型发送蒙版」冲突；本地 ONNX 分割与 ADR 0004「不再保留本地模型运行」冲突。缓解：A0 先补/修 ADR，再写实现票。
5. **B 路线依赖未声明**：cv2 只存在于本机 Python 3.12 环境，`requirements.txt` 无 opencv，离线 wheel 缓存 `packages/` 为 cp314 且无 opencv → 上线即缺包。缓解：任何用 grabCut/inpaint 的票，验收须含「依赖声明 + 目标 Python wheel + domain 120s 预算内跑通」。
6. **local-edit 结果节点前置**：端点要求 `result_node_id` 已存在且角色连线齐（`app/routers/api_project_library.py:3560-3585`，边须 `kind=='input'` + `role∈{original,references,prompt}`）。agent 工具必须先 `upsert_node` + `upsert_edge`（写法参考 `app/services/agents.py:697-698`），否则 400「局部编辑结果节点必须先存在于当前项目」。

## 7. 明确不做

- 任何 fal.ai 形态（`image_request_mode:'fal'`、`fal_request` 参数表）不进 #273 候选。
- ComfyArt / ComfyUI 工作流：ADR 0004 已删该能力，#273 的 blocked 语义=等本地模型能力就位。
- 不在画布进程内跑生成式本地推理运行时（ONNX 分割等），除非 A0 立 ADR 修订。
- 不改写用户 size/resolution/aspect（ADR 0006；#187 已闭回归）。
- 附带发现（不在 #273 范围，建议评论里提一句）：`canvas.batch_edit` 已随 #266 落地（`app/routers/api_mcp.py:319,485`、`app/routers/api_agent_tools.py:236`、`app/agent_extensions/canvas-tools.ts:286-296`），但它**消耗生成任务却不在 `CONFIRMATION_GATED_TOOLS`（`api_mcp.py:90`）**，与票面「涉及生成，写操作走确认门」的意图不一致 → 属 #266 遗留，另开小票更稳妥。

## 8. 只读实验记录（未触碰产品代码；探针 `tmp/spike273_b_probe.py`）

- `import cv2` → **4.13.0**（Python 3.12.10 全局 site-packages）；`cv2.inpaint`、`cv2.grabCut` 存在；无 `cv2.ximgproc`。
- 512² 合成真值实验：`cv2.inpaint` 擦除 PSNR —— 纯色 38.81(TELEA)/∞(NS)、渐变 38.31/56.45、**纹理 25.02/24.93（meanAbsErr≈11，明显涂抹）**；`cv2.grabCut` rect 初始化 IoU=1.000、0.21s@512² CPU、**仅二值输出**，模型缓冲须 `(1,65) float64`（文档值 39 触发 `GMM::GMM` 断言），笔画初始化缺背景样本触发 `!bgdSamples.empty() && !fgdSamples.empty()`。合成高对比图 IoU=1.0 **不代表**真实照片（发丝/半透明会显著下降）。
- 依赖面：`requirements.txt` 仅 `pillow` + `rapidocr-onnxruntime==1.4.4`（注释标其「非 ADR 0004 的生成推理运行时」）；已装 onnxruntime 1.27.0（仅 `CPUExecutionProvider`）、numpy 2.3.5、PIL 12.2.0；仓库内无 `.onnx` 权重。
- 配置面：`data/api_providers.json` 3 provider 的 `image_edit_endpoint` 全为空（走默认 `/v1/images/edits`）；`app/data/model_specs.json` 33 个 image_models 全 `api_type=images_generations`。
