# 模型 ID → 友好名 映射参考

> 用途：测试 Firefly-Canvas 后端 API 时，确认有效模型 ID 及其 UI 展示名（友好名）。
>
> 来源优先级：
> 1. **后端代码**（权威）— `app/routers/api_models.py`、`app/core.py` 的模型默认值
> 2. **桥豆麻衣酱 exe**（`F:\麻衣画布\Win\桥豆麻衣酱_V.3.4.9-08-02.exe`）— 提取的前端模型配置
> 3. **服务端文档**（`https://gpt-best.apifox.cn/llms.txt`）— 补充缺失项
>
> ⚠️ 友好名为基于模型 ID 的推导展示名，**最终需对照实际 app UI 核对**。
> 标记说明：`✅` = 后端代码确认存在；`📦` = exe 提取确认；`📄` = 服务端文档佐证；`❓` = 需人工核对。

---

## 一、即梦 / Seedance 视频模型（Jimeng）

来源：后端 `JIMENG_DEFAULT_VIDEO_MODELS`（core.py:151）+ exe 的 seedance/jimeng-cli 配置（两者 ID 一致）。

| 模型 ID | 友好名（建议） | 来源 |
|---------|--------------|------|
| seedance2.0_vip | Seedance 2.0（VIP） | ✅📦 |
| seedance2.0fast_vip | Seedance 2.0 Fast（VIP） | ✅📦 |
| seedance2.0fast | Seedance 2.0 Fast | ✅📦 |
| seedance2.0 | Seedance 2.0 | ✅📦 |
| seedance2.0mini | Seedance 2.0 Mini | 📦 |
| seedance1.5pro | Seedance 1.5 Pro | 📦 |
| seedance1.0pro | Seedance 1.0 Pro | 📦 |
| seedance1.0profast | Seedance 1.0 Pro Fast | 📦 |
| seedance1.0fast | Seedance 1.0 Fast | 📦 |
| seedance1.0 | Seedance 1.0 | 📦 |
| 3.5pro / 3.5_pro | 即梦 3.5 Pro | ✅📦 |
| 3.0pro / 3.0_pro | 即梦 3.0 Pro | ✅📦 |
| 3.0 / 3.0fast / 3.0_fast | 即梦 3.0 / 3.0 Fast | ✅📦 |

**Jimeng CLI 变体**（exe 经 WSL 调用 `jimeng-cli`，前缀 `jimeng-cli-`）：
- jimeng-cli-seedance2.0-vip / -fast-vip / -fast / -mini / -2.0
- jimeng-cli-seedance1.5-pro / -1.0 / -1.0-fast

**分辨率档位**（exe）：1k / 2k / 4k / 480P / 720P / 1080P / 4K

---

## 二、豆包 / 火山视频模型（Doubao Seedance / Volcengine）

来源：后端 `_DEFAULT_VIDEO_MODELS`（api_models.py:21-40）。

| 模型 ID | 友好名（建议） | 来源 |
|---------|--------------|------|
| doubao-seedance-2-0-260128 | 豆包 Seedance 2.0 | ✅📄 |
| doubao-seedance-2-0-fast-260128 | 豆包 Seedance 2.0 Fast | ✅📄 |
| doubao-seedance-1-5-pro-251215 | 豆包 Seedance 1.5 Pro | ✅📄 |
| doubao-seedance-1-0-pro-250528 | 豆包 Seedance 1.0 Pro | ✅📄 |
| doubao-seedance-1-0-lite-t2v-250428 | 豆包 Seedance 1.0 Lite（文生视频） | ✅📄 |
| doubao-seedance-1-0-lite-i2v-250428 | 豆包 Seedance 1.0 Lite（图生视频） | ✅📄 |

> 注：日期后缀（如 `-260128`）为模型版本日期。

---

## 三、Google 系视频/图像模型（Veo / Nano Banana）

来源：后端 `_DEFAULT_VIDEO_MODELS`、`RUNNINGHUB_DEFAULT_*`。

| 模型 ID | 友好名（建议） | 类别 | 来源 |
|---------|--------------|------|------|
| veo2 / veo2-fast / veo2-pro | Veo 2 / Fast / Pro | 视频 | ✅ |
| veo3 / veo3-fast / veo3-pro | Veo 3 / Fast / Pro | 视频 | ✅ |
| veo3.1 / veo3.1-fast / veo3.1-quality / veo3.1-lite | Veo 3.1 系列 | 视频 | ✅ |
| nano-banana-pro | Nano Banana Pro | 图像 | ✅📄 |
| nano-banana/text-to-image-official-stable | Nano Banana（文生图） | 图像 | ✅ |
| nano-banana/edit-official-stable | Nano Banana（编辑） | 图像 | ✅ |
| google/veo3.1-fast/text-to-video-channel-low-price | Veo 3.1 Fast（低价通道） | 视频 | ✅ |

> 服务端文档佐证：Nano-banana-2 / Nano-banana-3.1-Flash / Nano-banana-3.1-Flash-Lite、gemini-3-pro-image-preview、gemini-2.5-flash-image(-preview)。

---

## 四、OpenAI / Sora / GPT 系

来源：后端 `_DEFAULT_VIDEO_MODELS`、`_DEFAULT_CHAT_MODELS`、`CODEX_DEFAULT_*`。

| 模型 ID | 友好名（建议） | 类别 | 来源 |
|---------|--------------|------|------|
| sora-2 / sora-2-pro | Sora 2 / Sora 2 Pro | 视频 | ✅📄 |
| sora-2/text-to-video-official-stable | Sora 2（文生视频） | 视频 | ✅ |
| gpt-4o-mini | GPT-4o mini | 对话 | ✅ |
| gpt-5.4 | GPT-5.4 | 对话 | ✅ |
| gpt-image-2 | GPT Image 2 | 图像 | ✅📄 |
| gpt-image-2.0/text-to-image-channel-low-price | GPT Image 2.0（低价文生图） | 图像 | ✅ |
| gpt-image-2.0/edit-channel-low-price | GPT Image 2.0（低价编辑） | 图像 | ✅ |
| gpt-image-2/text-to-image-official-stable | GPT Image 2（官方文生图） | 图像 | ✅ |
| gpt-image-2/image-to-image-official-stable | GPT Image 2（官方图生图） | 图像 | ✅ |

---

## 五、阿里 / 万相 / 通义（Wan / Qwen）

来源：后端 `_DEFAULT_VIDEO_MODELS`、`MODELSCOPE_DEFAULT_*`、qwen视频模型文档。

| 模型 ID | 友好名（建议） | 类别 | 来源 |
|---------|--------------|------|------|
| wan2.6-t2v / wan2.6-i2v | Wan 2.6（文生/图生视频） | 视频 | ✅📄 |
| wan2.5-t2v-preview / wan2.5-i2v-preview | Wan 2.5 Preview（文生/图生） | 视频 | ✅ |
| wan2.2-t2v-plus / wan2.2-i2v-plus / wan2.2-i2v-flash | Wan 2.2 系列 | 视频 | ✅📄 |
| Qwen/Qwen-Image-2512 | 通义千问图像 2512 | 图像 | ✅ |
| Qwen/Qwen-Image-Edit-2511 | 通义千问图像编辑 2511 | 图像 | ✅ |
| Qwen/Qwen3-235B-A22B | 通义千问 3 235B（MoE） | 对话 | ✅ |
| Qwen/Qwen3-VL-235B-A22B-Instruct | 通义千问 3 VL 235B | 对话 | ✅ |

---

## 六、其它服务商

| 模型 ID | 友好名（建议） | 服务商 | 来源 |
|---------|--------------|--------|------|
| agnes-video-v2.0 | Agnes Video v2.0 | Agnes | ✅ |
| seedance-2.0-global/text-to-video | Seedance 2.0 Global（文生视频） | RunningHub | ✅ |
| seedance-2.0-global/image-to-video | Seedance 2.0 Global（图生视频） | RunningHub | ✅ |
| jimeng-image-2k / jimeng-image-4k | 即梦图像 2K / 4K | Jimeng | ✅ |
| jimeng-video-720p / jimeng-video-1080p | 即梦视频 720P / 1080P | Jimeng | ✅ |
| auto | 自动 | Gemini CLI | ✅ |

> 服务端文档佐证（尚未在后端默认值中）：MidJourney、FLUX.1 / flux-pro / flux-kontext / flux-2-pro、Runway、Pika、pixverse、Higgsfield、vidu、Ideogram、Suno。

---

## 七、映射机制说明

后端通过 `api_providers.json` 的 `model_names` 字段（`{模型ID: 展示名}`）存储友好名映射，当前为占位空对象 `{}`。

- 规整逻辑：`normalize_model_name_map`（core.py:2145）— 仅保留 `label != model` 的有意义标签
- 前端：`static/js/smart-canvas.js:2941 DEFAULT_VIDEO_MODELS`，`/api/models` 提供生效列表
- 填充方式：在 `model_names` 中按 `{ "seedance2.0_vip": "Seedance 2.0（VIP）", ... }` 添加

## 待办
- [ ] 对照实际 桥豆麻衣酱 app UI 核对友好名（尤其 3.x 即梦版本的中文展示）
- [ ] 用有效 API Key 调用 `/v1/models` 获取服务端完整模型清单，补全缺失项
- [ ] 确认 jimeng-cli- 前缀模型是否需要单独映射（CLI 内部 ID vs 展示 ID）
