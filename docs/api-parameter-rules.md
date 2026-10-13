# Firefly-Canvas 后端 API 传参规则参考（最新格式版）

来源：https://gpt-best.apifox.cn/（llms.txt + 各 api-*.md 结构化文档）+ 桥豆麻衣酱缓存真实数据。
用途：修复 Firefly-Canvas 所有模型的传参，全部采用**最新格式**，保证可用。

Base URL（app 实测）：`https://ai.comfly.org`
鉴权：所有请求 `Authorization: Bearer <API_KEY>`，`Content-Type: application/json`

> 核心决策：Chat/对话模型统一改用 **Responses API**（`/v1/responses`，新格式，
> `input` 字段），不再用老的 Chat Completions（`/v1/chat/completions`，`messages` 字段）。
> Responses 是 OpenAI 最新核心 API，支持更好；部分模型（o3-pro、codex-mini-latest）仅支持 Responses。

---

## 1. 对话 / Chat —— 用 Responses API（新格式）★推荐

```
POST /v1/responses
```
```json
{
  "model": "gpt-4o",
  "input": [
    { "role": "user", "content": "你好" }
  ]
}
```
- 必填：`model`、`input`
- `input[]` 每项：`role`（user/assistant）+ `content`（字符串 或 数组）
- 流式：加 `"stream": true`（返回 SSE `response.*` 事件）
- 响应：`output[].content[].text`（type=output_text）

### 1a. 传图（解决 GPT 提示词节点不能传图）★关键
Responses 用 `input_image` 内容块（OpenAI 官方格式）：
```json
{
  "model": "gpt-4o",
  "input": [
    {
      "role": "user",
      "content": [
        { "type": "input_text", "text": "这张图有什么？" },
        { "type": "input_image", "image_url": "https://... 或 data:image/jpeg;base64,..." }
      ]
    }
  ]
}
```
- 文本块：`{type:"input_text", text:"..."}`
- 图片块：`{type:"input_image", image_url:"<https 或 data URL>", detail:"auto|low|high"}`
- base64 图：`image_url` 直接放 `data:image/...;base64,...`
- 修复要点：去掉"对话模型不支持传图"的拦截，把 prompt 节点图片转成 `input_image` 块

### 1b. 老格式 Chat Completions（备用，仍可用）
若某链路必须兼容，Chat 传图格式：
```json
POST /v1/chat/completions
{
  "model": "gpt-4o",
  "messages": [
    { "role": "user", "content": [
        { "type": "text", "text": "这张图有什么" },
        { "type": "image_url", "image_url": { "url": "https://... 或 data:image/jpeg;base64,..." } }
    ] }
  ]
}
```

## 2. 生图（banana2 尺寸/映射）★关键

banana2 = model `nano-banana-2`（友好名 "Nano Banana Pro"）。

```
POST /v1/images/generations
```
```json
{
  "model": "nano-banana-2",
  "prompt": "一只猫",
  "response_format": "url",
  "aspect_ratio": "4:3",
  "image_size": "4K",
  "image": ["https://参考图url 或 b64_json"]
}
```
- 必填：`model`、`prompt`
- **`aspect_ratio` 10 种**（尺寸不全的根因，补全这些）：
  `4:3` `3:4` `16:9` `9:16` `2:3` `3:2` `1:1` `4:5` `5:4` `21:9`
- **`image_size` 3 种**（仅 nano-banana-2 支持）：`1K` `2K` `4K`
- `response_format`：`url` 或 `b64_json`
- `image`：参考图数组（url/b64），可空
- 映射确认：友好名 "Nano Banana Pro" → `nano-banana-2`

### 2a. nano-banana-2.1（2026-10 新增，服务商新模型）

- 模型 ID：`gemini-nano-banana-2.1`、`gemini-nano-banana-2.1-2k`、`gemini-nano-banana-2.1-4k`（NEW）
- 参数与 banana2 相同：`aspect_ratio`（14 种）、`image_size`（1K/2K/4K）、`response_format`、`image`（参考图数组）
- 后端路由：`app/core.py` `banana_model_family` 已识别 2.1 家族，按分辨率自动路由到 `-2k`/`-4k` 变体（无变体时回退基础模型 + `image_size` 参数）
- 规格已录入 `app/data/model_specs.json`（`api_type: images_generations`，supports_vision/async_mode）

### 图生图/改图（Edits）
```
POST /v1/images/edits   (multipart/form-data)
```
- `model`(nano-banana-2)、`prompt`、`image`(多图/可无)、`response_format`、`aspect_ratio`、`image_size`

## 3. 生图异步任务（remoteTaskId 模式）★生图统一走异步

```
提交: POST /v1/images/generations?async=true   (body 同同步)
  响应: { "code":"...", "message":"...", "data":"<task_id>" }
轮询: GET /v1/images/tasks/{task_id}
  响应: { "code":"success", "data": {
           "task_id":"...", "status":"SUCCESS", "progress":"100%",
           "fail_reason":"",
           "data": { "data":[ {"url":"https://.../output.png", "b64_json":"", "revised_prompt":"..."} ],
                     "model":"nano-banana" } } }
```
- status：`IN_PROGRESS` / `SUCCESS` / `FAILURE`
- 取图：`data.data.data[]` 的 `url` 或 `b64_json`
- 轮询建议：2s→4s→8s 退避，SUCCESS/FAILURE 停，超时(30min)提示

## 4. 视频 —— v2 统一格式接口

```
提交: POST /v2/{scope}/generations   (scope: images|videos|audio)
{
  "prompt":"...", "model":"veo3", "duration":5, "aspect_ratio":"16:9",
  "size":"", "resolution":"780P",
  "images":["url 或 b64"], "videos":["only url"], "watermark":false
}
  响应: { "task_id":"f186f65a-..." }
查询: GET /v2/{scope}/generations/{task_id}
  响应: { "task_id":"veo3:...", "status":"SUCCESS", "progress":"100%",
          "data": { "output":"https://.../xxx.mp4", "outputs":["..."] } }
```
- 有 `images` → 图生视频；有 `videos` → 视频生视频；都无 → 纯文生
- status：`NOT_START` `SUBMITTED` `QUEUED` `IN_PROGRESS` `SUCCESS` `FAILURE`
- 取结果：`data.output` / `data.outputs`

## 5. 模型列表

```
GET /v1/models   (Bearer key)  →  OpenAI 格式 data[].id
```

---

## 6. 对 Firefly-Canvas 的修复要点（按优先级）

1. **【P0】对话统一切 Responses API**：所有 chat/对话模型请求改用
   `POST /v1/responses` + `input` 字段（含图片用 `input_image` 块）。
   彻底移除"对话模型不支持传图"的判断——Responses 原生支持多模态。
2. **【P0】prompt 节点传图**：图片输入转 `{type:"input_image", image_url:<dataUrl>}`。
3. **【P1】banana2 尺寸**：`aspect_ratio` 补全 10 种；`image_size` 补 1K/2K/4K；
   映射 "Nano Banana Pro"→`nano-banana-2`。
4. **【P1】生图流程**：统一异步（提交→轮询→取图），别同步等待。
5. **【P2】视频**：走 v2 统一格式接口。
