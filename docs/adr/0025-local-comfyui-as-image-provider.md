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
- 尺寸表以节点 `ResolutionSelector` 的实测表为准（1k/2k/4k = 4/8/16 MP，边长 8 的倍数）；`docs/MODEL_PARAMETERS.md` 记录的云 API 表与此差值在 1 个步长内，两套各自独立维护。
- 将来要接「远程 ComfyUI 容器」时只需改 `base_url` 并补鉴权头，不需要动工作流与协议。

## 上线后的四项修正（用户报「取不回来 / 参考图传不过去 / 图都不对」后实测得出）

1. **参考图必须进程内解析**：画布给适配器的参考图是 `/api/project-library/{pid}/media/{aid}`。旧实现对它发 HTTP GET，而项目媒体接口有 `require_project_media_adapter_scope` 适配器作用域网关，进程内直连必定 403，`raise_for_status()` 又把 httpx 异常直接抛出——于是整单崩掉。现在解析顺序是 `path`/`source_path` 字段 → 项目媒体 store 反查 → `/output/`、`/assets/` 本地映射，只有真正的外部外链才下网；解析不到抛 400 一句人话。
2. **参考图必须先缩图**：3166×3173 的原图交给节点 4 的 VAE 编码，在 2048² 工作流上直接 `Allocation on device 0 would exceed allowed memory`（16GB 卡）。现在统一用 PIL 缩到最长边 `COMFYUI_REFERENCE_MAX_SIZE`（默认 1536，env 可调）再上传，data URL 也走同一条缩图路径。
3. **超时/失败必须撤单**：ComfyUI 是单队列，一张卡死的图会把后面所有任务堵住（实测：一单 5472×3072 跑着时，后续任务全部 `queue_position: 1`）。现在 `comfyui_wait_for_images` 在超时、报错、被取消时都会 `POST /queue {"delete": [prompt_id]}`；只有该 prompt 确实在 `queue_running` 里才补一次 `/interrupt`，避免打断用户别的任务。
4. **结果落盘回 URL**：4K PNG 常有 20MB+，base64 再胀 33%，塞进 JSON 就是 27MB，画布端卡死/超时多半来自这里。现在写成 `assets/output/comfyui-<时间戳>-<随机>.png` 并回 `{'type': 'url'}`，与 codex / gemini-cli 这些本地提供方一致；落盘失败才退回 base64。

另一个实测事实：参考图与提示词的绑定方式是在提示词里写 `<Picture 1>`（节点描述："The prompt refers to them as <Picture 1>, <Picture 2>")。实测不写也能用，但写了更稳；画布不代用户改提示词。
