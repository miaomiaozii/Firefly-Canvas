# ADR 0025：本地 ComfyUI 作为独立生图协议接入

状态：已接受（2026-11-14，维护者本机 ComfyUI 0.45.2 + ComfyUI-GGUF 实测）
日期：2026-11-14
关联：issue #331、ADR 0004（在线生成不依赖本机运行时）、ADR 0006（模型路由保留用户参数）

## 背景

画布已有 8 个生图协议，都是「一次 HTTP 请求 = 一张图」的 REST 语义（含 #330 接入的 MiniMax H3 云 API）。维护者本机已部署 ComfyUI（`http://127.0.0.1:8188`），装了 MiniMax-H3-Image GGUF 节点包，想用本地算力出图，跳过云 API 的额度。

实测发现两件与既有协议都不同的事实：

1. **传输模型不同**：ComfyUI 是会话式图接口——`POST /prompt` 提交整张工作流图 → `GET /history/{id}` 轮询 → `GET /view` 取图；参考图必须先 `POST /upload/image` 进它的 `input` 目录。没有「prompt+size」这种扁平请求体。
2. **同一模型的参数语义不同**：本地 H3 节点的 `steps/cfg/sampler` 是工作流参数（缺省 6 步、cfg 1.0、euler/simple），没有云 API 的 `quality`（standard/HD）概念；尺寸靠节点自带的 `ResolutionSelector`（1k/2k/4k × 比例，边长贴合 8 的倍数）。

## 决策

**新增独立协议 `comfyui`，不把本地 H3 复用成 `minimax` 协议的第二个端点。**

理由：协议在代码里意味着「请求体怎么拼、响应怎么解析、失败怎么判」。本地 H3 与云 API 除了模型能力相同，传输与参数三层全不同；强行复用会让 `generate_minimax_image` 分叉成两条互不共享的路径，测试也失去边界。新增协议让两边各自演进，将来本地节点包升级不影响云 API 路径。

具体钉：

- **协议固定**：写进 `FIXED_PROTOCOL_PROVIDER_IDS`。用户填的模型名/`model_protocols` 不能把它改成 `openai`；否则「同一服务商填不同模型就换协议」会让本地工作流被当成 REST 端点打。
- **单一地址旋钮**：`base_url` 是 ComfyUI 根地址，所有子端点（`/prompt`、`/history`、`/view`、`/upload/image`、`/queue`）由它拼出。换机器/换容器只改这一个字段，不需要 `image_generation_endpoint` 之类的端点覆盖——这是 #330 云 API 路径留下的兼容口，本地场景没有意义。
- **参考图即插即用**：画布连了参考图就走图生图（图上传后挂到节点 4 的 `images` autogrow：`{'image_1': ['ref1', 0]}`，实测形状）；没连就是纯文生图，不额外暴露开关。参考图来源沿用既有三级（data URL / 应用本地媒体 / http(s)），拿不到就 400 明说，不静默降级。
- **不重试、不自动重提**：本地算力照样有代价（一张 4k 图几分钟），且超时后任务可能仍在跑、产物照旧落盘。超时返回 504 并带上 `prompt_id` 与产物目录，由用户去 ComfyUI 队列自己判断——与 ADR 0017（重连后由服务端真相恢复付费任务）精神一致：状态以执行方为准，不靠客户端猜。
- **参数只承诺画布已有的三项**：比例、面积档、输出格式。`quality` 不进工作流（本地没有该概念，硬映射成 steps 会让用户以为画质档位可用）；`n>1` 由上游循环拆成多任务，适配器内部恒为单张。

## 后果

- 多一个协议要维护，但换来本地/云端两条路径互不牵连。
- 本机 ComfyUI 未启动时任务以 503 快速失败并提示改 `base_url`，不占用重试预算。
- 尺寸表以节点 4 的 `width`/`height` 步长为准：**必须是 32 的倍数**。官方图片节点声明 `step: 32`，传非 32 倍数会被静默向下取整（传 2728 → 实际 2720），所以适配器（`COMFYUI_SIZE_STEP = 32`）直接把 1K/2K/4K = 4/8/16 MP 的结果取整到 32。`docs/MODEL_PARAMETERS.md` 记录的云 API 表与此差值在 1 个步长内，两套各自独立维护。
- 将来要接「远程 ComfyUI 容器」时只需改 `base_url` 并补鉴权头，不需要动工作流与协议。

## 上线后的四项修正（用户报「取不回来 / 参考图传不过去 / 图都不对」后实测得出）

> 其中「参考图缩到 1536」已被官方工作流推翻，见下一节；本节的第 2 条保留仅作历史记录。

1. **参考图必须进程内解析**：画布给适配器的参考图是 `/api/project-library/{pid}/media/{aid}`。旧实现对它发 HTTP GET，而项目媒体接口有 `require_project_media_adapter_scope` 适配器作用域网关，进程内直连必定 403，`raise_for_status()` 又把 httpx 异常直接抛出——于是整单崩掉。现在解析顺序是 `path`/`source_path` 字段 → 项目媒体 store 反查 → `/output/`、`/assets/` 本地映射，只有真正的外部外链才下网；解析不到抛 400 一句人话。
2. **参考图必须先缩图**：3166×3173 的原图交给节点 4 的 VAE 编码，在 2048² 工作流上直接 `Allocation on device 0 would exceed allowed memory`（16GB 卡）。现在统一用 PIL 缩到最长边 `COMFYUI_REFERENCE_MAX_SIZE`（默认 1536，env 可调）再上传，data URL 也走同一条缩图路径。
3. **超时/失败必须撤单**：ComfyUI 是单队列，一张卡死的图会把后面所有任务堵住（实测：一单 5472×3072 跑着时，后续任务全部 `queue_position: 1`）。现在 `comfyui_wait_for_images` 在超时、报错、被取消时都会 `POST /queue {"delete": [prompt_id]}`；只有该 prompt 确实在 `queue_running` 里才补一次 `/interrupt`，避免打断用户别的任务。
4. **结果落盘回 URL**：4K PNG 常有 20MB+，base64 再胀 33%，塞进 JSON 就是 27MB，画布端卡死/超时多半来自这里。现在写成 `assets/output/comfyui-<时间戳>-<随机>.png` 并回 `{'type': 'url'}`，与 codex / gemini-cli 这些本地提供方一致；落盘失败才退回 base64。

另一个实测事实：参考图与提示词的绑定方式是在提示词里写 `<Picture 1>`（节点描述："The prompt refers to them as <Picture 1>, <Picture 2>"）。实测不写也能用，但写了更稳；画布不代用户改提示词。

## 图生图改为对齐官方工作流（推翻上面的缩图策略）

用户指出应按官方模板（`Comfy-Org/MiniMax-H3-Image` → `image_minimax_h3_image_edit.json`）。读官方 UI 格式模板后，图生图的正确链路是：

```
LoadImage(image_1)
  → ResizeImageMaskNode('scale total pixels', 4 MP, 'area')   # 节点 56：先把参考图缩/放到 4MP
  → ResizeImageMaskNode('scale to multiple', 32, 'area')       # 节点 48：边长对齐 32
  → MiniMaxH3ImageConditioning.images.image_1                  # 节点 32
  → 同一张图接 GetImageSize → MiniMaxH3ImageConditioning.width/height   # 输出尺寸 = 参考图缩放后的尺寸
```

三条与旧实现的差别（都是实质性的）：

1. **参考图是「缩放到目标像素量」，不是「缩到 1536」**。旧策略 1536 与 latent 尺寸（2048²）对不上，VAE 参考 latent 与画布 latent 不同尺度，模型对参考图的锚定基本失效——这才是「生的图都不对」的真正原因，OOM 只是它的副作用。现在按画布面积档（1K/2K/4K = 4/8/16 MP）缩放，两边齐 32（`comfyui_edit_target_size`，上限 `COMFYUI_EDIT_MAX_MEGAPIXELS`）。
2. **图生图的输出尺寸跟参考图走**，不跟画布比例选择走（官方用 GetImageSize 回填）。画布选的比例只对文生图生效；这一点在 UI 文案里没有暴露，但比“按画布比例强行拉参考图”更接近模型训练分布。
3. **提示词里要点名 `<Picture 1>`**。节点描述写了这个约定，但没强制执行。实测同一张房景图：
   - 不点名 + 模糊指令（「把主体放进客厅场景」）→ 模型直接丢掉参考图，凭空画了个沙发场景；
   - 点名（`Edit <Picture 1>: keep the house ... only change the sky`）→ 构图、窗户、灯串全部延续，只改了天空。
   因此参考图存在且提示词里没有 `<Picture` 时，适配器自动加前缀 `Edit <Picture 1>. `，并在任务数据里标 `prompt_prefixed: true`，不静默改写。

另一条官方事实：模板里的 t2i 用 `ResolutionSelector('1:1 (Square)', 4, 32)`（4 MP / 边长 32），并附注「doesn't work well in low resolution ... Raise ... 16MP」；画布的面积档命名（1K/2K/4K = 4/8/16 MP）与之一致。

官方 edit 模板还串了一层提示词增强（PE）：`TextGenerateV2`（用 `qwen3.5_9b_w4a8_gptq.safetensors` 作 LLM）+ `JsonExtractString('expanded_caption')` + `ComfySwitchNode`（开关节点 61）+ 官方给的 system/task 提示词。

**PE 的两个参数必须改否则会“跑飞”**（实测证据：ComfyUI 日志 `Generating tokens: 43%|███▎| 3533/8196 [18:59<25:04, 2.89it/s]`）：

- `max_length`：官方 8196 → 我们 1536（`COMFYUI_PE_MAX_LENGTH`）。PE 只需要扩写一句画面描述，8196 的上限给了它跑到 15 分钟的空间。
- `thinking`：官方 true → 我们 false（`COMFYUI_PE_THINKING`）。链式推理会先吐几千个推理 token，小卡上 3 token/s，这是 15~23 分钟的真正来源。

实测（2048²，同一提示词）：max_length 8196 + thinking → 93s（顺利时）/ 958s、1413s（跑飞时）；1536 + 无 thinking → 38s（t2i）/ 84s（i2i 带参考图），PE 输出仍是合格的 `expanded_caption`（含 `<Picture 1>` 指代），token 生成本身只要 2~3 秒（日志：`128/1536 [00:02<00:22, 62it/s]`）。

还有一格保险：`COMFYUI_PE_MAX_MEGAPIXELS`（默认 8）——超过 8MP（即 4K 档）自动跳过 PE。因为 4K 时真正的代价不是 PE 的 token，而是它那个 7GB 编码器把 11GB 主模型挤出显存，两边来回读盘：实测 5472×3072 图生图 PE 开 516s / PE 关 138s（热）。1K/2K 档 PE 代价可控（1K 图生图 84s），所以只在 4K 关。

## 追加（2026-11-14 深夜，维护者实测反馈后）

### 参考图必须写成扁平键 `images.image_0`
节点 4 的 `images` 是 `COMFY_AUTOGROW_V3`。API 图里**必须**把它写成扁平键：

```json
"4": {"inputs": {"images.image_0": ["ref0", 0], "prompt": "...", "width": 2720, "height": 1536}}
```

写成嵌套的 `"images": {"image_0": [...]}` 会被后端**静默丢弃**：不报错、不警告，节点拿到的 `images` 是空的，输出尺寸回落到默认 2048，整单退化成文生图。第一版适配器就是嵌套写法，导致画布上"参考图传不过去"——图能出，但跟参考图毫无关系。

判定方法（不留悬念）：把节点 `width`/`height` 设成 0，参考图被消费时输出尺寸等于参考图缩放后的尺寸（4:3 → 2336×1760），被忽略时是 2048×2048。

序号从 **0** 开始，与前端 `autogrowOrdinalToName`（`comfyui_frontend`：`${group}.${prefix}${ordinal}`）一致，所以提示词里的 `<Picture 1>` 对应 `images.image_0`。UI 工作流文件里也是这个键名——写成 `images.image_1` 会导致打开工作流时那条连线丢失（面板上只剩一个空的 `image_0` 插槽）。

### 显存不足自动腾一次
显存碎片导致 OOM 很常见（同一张图前一次能跑、后一次 OOM）。适配器把 OOM 单独归类：收到 `out of memory` / `exceed allowed memory` 时先 `POST /free {"unload_models": true, "free_memory": true}`，再同图重跑**一次**；第二次仍 OOM 才把错误抛给画布（并提示降档）。这不是"自动重试"——只针对显存这一种确定性可恢复的失败，且上限一次。

### 提示词增强（PE）已接入：与官方模板同构

维护者对照官方 `image_minimax_h3_image_edit.json`（27 节点）后要求「按官方模板重建，含 PE」（2026-11-14，选项 A）。适配器现在发出的图在节点集与连线上与官方一致，只有三处**必须**的差异：

| 官方模板 | 适配器 |
|---|---|
| `UNETLoader`/`CLIPLoader`（safetensors） | `UnetLoaderGGUF`/`CLIPLoaderGGUF`（本机只有 GGUF 权重） |
| `GetImageSize` 把参考图尺寸回填给节点 4 | 尺寸取画布的比例 + 面积档（画布必须能指定比例，官方网页也支持比例与参考图不同） |
| `ImageCompare` 对比面板 | 不进 API 图（`compare_view` 是纯前端自定义类型，后端会 `required_input_missing`） |

PE 链（节点号与官方一致，便于对照）：`43 PrimitiveStringMultiline(用户提示词)` → `40 StringReplace(task_prompt 里的 {input text})` → `34 TextGenerateV2(clip=35 CLIPLoader qwen3.5_9b_w4a8_gptq, system_prompt=36, images.image1=参考图)` → `44 JsonExtractString(expanded_caption)` → `41 ComfySwitchNode(switch=61 PrimitiveBoolean)` → `37 PreviewAny` → 节点 4 的 `prompt`。关 PE 就把节点 61 置 false，提示词直连；适配器用 `pe_enabled=False` 表达这件事。

官方 system/task 提示词逐字落库在 `app/services/comfyui_pe_prompts.py`（脚本从模板导出，不手抄）。

实测代价（4070 Ti SUPER 16GB，4030×4030 参考图 + 4MP 输出）：

| 路径 | 纯 H3 | 加 PE |
|---|---|---|
| 文生图 1K 16:9 | 30s | 90s（冷启动）/ 30s（PE 模型已常驻） |
| 图生图 1K 1:1 | 35s | 60–175s（首跑要 175s：PE 9B + 主模型 + Qwen3-VL-32B 同时抢显存） |

代价换来的东西是实打实的：PE 会把「把参考图里的毯子放进客厅」扩写成带 `<Picture 1>`、光照、镜头、材质描述的完整提示词（实测 714 字符），H3 因此更愿意保留产品外观。
