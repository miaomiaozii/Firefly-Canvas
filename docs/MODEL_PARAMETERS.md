# 模型参数规范 (MODEL_PARAMETERS)

> 本文档从 `app/data/model_specs.json` 自动生成，请勿手动编辑。
> 最后更新: 2026-08-25 17:44:37

## 概述

本系统支持三类模型：

| 类别 | 用途 | API 类型 |
|------|------|----------|
| 对话模型 | 文本对话、图片分析 | responses / chat_completions |
| 图像模型 | 图像生成、图像编辑 | images_generations |
| 视频模型 | 视频生成 | v2_generations |

---

## 对话模型 (Chat Models)

| 模型 ID | 显示名称 | API 类型 | 支持视觉 |
|---------|----------|----------|----------|
| gpt-4o-mini | GPT-4o mini | responses | ✅ |
| gpt-4o | GPT-4o | responses | ✅ |
| gpt-4.1 | GPT-4.1 | responses | ✅ |
| gpt-4.1-mini | GPT-4.1 mini | responses | ✅ |
| gpt-5 | GPT-5 | responses | ✅ |
| gpt-5.4 | GPT-5.4 | responses | ✅ |
| claude-sonnet-4-5 | Claude Sonnet 4.5 | responses | ✅ |
| claude-opus-4-1 | Claude Opus 4.1 | responses | ✅ |
| gemini-3.1-flash-image-preview-2k | Gemini 3.1 Flash Image 2K | responses | ✅ |

### 对话模型参数详情

#### GPT-4o mini
- **模型 ID**: `gpt-4o-mini`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)
  - `max_tokens`: integer (默认: None)
- **备注**: OpenAI Responses API 原生支持图片输入

#### GPT-4o
- **模型 ID**: `gpt-4o`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### GPT-4.1
- **模型 ID**: `gpt-4.1`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### GPT-4.1 mini
- **模型 ID**: `gpt-4.1-mini`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### GPT-5
- **模型 ID**: `gpt-5`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### GPT-5.4
- **模型 ID**: `gpt-5.4`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### Claude Sonnet 4.5
- **模型 ID**: `claude-sonnet-4-5`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### Claude Opus 4.1
- **模型 ID**: `claude-opus-4-1`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

#### Gemini 3.1 Flash Image 2K
- **模型 ID**: `gemini-3.1-flash-image-preview-2k`
- **API 类型**: responses
- **支持视觉**: 是
- **参数**:
  - `stream`: boolean (默认: False)
  - `temperature`: number (默认: 1.0)

---

## 图像模型 (Image Models)

| 模型 ID | 显示名称 | API 类型 | 支持视觉 | 异步模式 |
|---------|----------|----------|----------|----------|
| nano-banana-pro | Nano Banana Pro | images_generations | ✅ | ✅ |
| nano-banana-2 | Nano Banana 2 | images_generations | ✅ | ✅ |
| gpt-image-2 | GPT Image 2 | images_generations | ✅ | ✅ |

### 图像模型参数详情

#### Nano Banana Pro
- **模型 ID**: `nano-banana-pro`
- **API 类型**: images_generations
- **支持视觉**: 是
- **异步模式**: 是
- **参数**:
  - `aspect_ratio`: 枚举类型 (默认: 1:1)
    - 可选值: 1:1, 2:3, 3:2, 3:4, 4:3, 9:16, 16:9, 4:5, 5:4, 21:9
  - `image_size`: 枚举类型 (默认: 1K)
    - 可选值: 1K, 2K, 4K
  - `response_format`: 枚举类型 (默认: url)
    - 可选值: url, b64_json
  - `image`: array (默认: N/A)
- **备注**: Nano-banana Pro，支持参考图输入

#### Nano Banana 2
- **模型 ID**: `nano-banana-2`
- **API 类型**: images_generations
- **支持视觉**: 是
- **异步模式**: 是
- **参数**:
  - `aspect_ratio`: 枚举类型 (默认: 1:1)
    - 可选值: 4:3, 3:4, 16:9, 9:16, 2:3, 3:2, 1:1, 4:5, 5:4, 21:9, 1:4, 4:1, 8:1, 1:8
  - `image_size`: 枚举类型 (默认: 1K)
    - 可选值: 1K, 2K, 4K
  - `response_format`: 枚举类型 (默认: url)
    - 可选值: url, b64_json
  - `image`: array (默认: N/A)
- **备注**: Nano-banana 2 (对应 Nano-banana-3.1-Flash)，支持参考图输入

#### GPT Image 2
- **模型 ID**: `gpt-image-2`
- **API 类型**: images_generations
- **支持视觉**: 是
- **异步模式**: 是
- **参数**:
  - `size`: string (默认: auto)
  - `quality`: 枚举类型 (默认: auto)
    - 可选值: low, medium, high, auto
  - `response_format`: 枚举类型 (默认: b64_json)
    - 可选值: url, b64_json
  - `image`: array (默认: N/A)
- **备注**: OpenAI 原生生图模型。支持任意分辨率（满足约束），支持图生图

---

## 视频模型 (Video Models)

| 模型 ID | 显示名称 | API 类型 | 支持视觉 | 异步模式 |
|---------|----------|----------|----------|----------|
| doubao-seedance-2-0-260128 | Seedance 2.0 | jimeng_video | ✅ | ✅ |
| doubao-seedance-2-0-fast-260128 | Seedance 2.0 Fast | jimeng_video | ✅ | ✅ |
| doubao-seedance-2.0-mini | Seedance 2.0 Mini | jimeng_video | ✅ | ✅ |

### 视频模型参数详情

#### Seedance 2.0
- **模型 ID**: `doubao-seedance-2-0-260128`
- **API 类型**: jimeng_video
- **支持视觉**: 是
- **异步模式**: 是
- **参数**:
  - `duration`: 整数类型 (默认: 5，范围: 4-15，单位: seconds)
  - `ratio`: 枚举类型 (默认: 16:9)
    - 可选值: 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, 9:21, adaptive
  - `resolution`: 枚举类型 (默认: 480p)
    - 可选值: 480p, 720p, 1080p
  - `watermark`: boolean (默认: False)
  - `seed`: 整数类型 (默认: None，范围: 0-2147483647)
  - `return_last_frame`: boolean (默认: False)
  - `generate_audio`: boolean (默认: False)
  - `images`: array (默认: N/A)
  - `videos`: array (默认: N/A)
  - `audios`: array (默认: N/A)
  - `tools`: array (默认: N/A)
- **备注**: 即梦 Seedance 2.0 统一格式接口。支持文生视频、图生视频、多模态参考生视频

#### Seedance 2.0 Fast
- **模型 ID**: `doubao-seedance-2-0-fast-260128`
- **API 类型**: jimeng_video
- **支持视觉**: 是
- **异步模式**: 是
- **参数**:
  - `duration`: 整数类型 (默认: 5，范围: 4-15，单位: seconds)
  - `ratio`: 枚举类型 (默认: 16:9)
    - 可选值: 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, 9:21, adaptive
  - `resolution`: 枚举类型 (默认: 480p)
    - 可选值: 480p, 720p, 1080p
  - `watermark`: boolean (默认: False)
  - `seed`: 整数类型 (默认: None，范围: 0-2147483647)
  - `return_last_frame`: boolean (默认: False)
  - `generate_audio`: boolean (默认: False)
  - `images`: array (默认: N/A)
  - `videos`: array (默认: N/A)
  - `audios`: array (默认: N/A)
  - `tools`: array (默认: N/A)
- **备注**: 即梦 Seedance 2.0 Fast 快速版

#### Seedance 2.0 Mini
- **模型 ID**: `doubao-seedance-2.0-mini`
- **API 类型**: jimeng_video
- **支持视觉**: 是
- **异步模式**: 是
- **参数**:
  - `duration`: 整数类型 (默认: 5，范围: 4-15，单位: seconds)
  - `ratio`: 枚举类型 (默认: 16:9)
    - 可选值: 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, 9:21, adaptive
  - `resolution`: 枚举类型 (默认: 480p)
    - 可选值: 480p, 720p
  - `watermark`: boolean (默认: False)
  - `seed`: 整数类型 (默认: None，)
  - `images`: array (默认: N/A)
  - `videos`: array (默认: N/A)
  - `audios`: array (默认: N/A)
- **备注**: 即梦 Seedance 2.0 Mini 轻量版

---

## 使用说明

### 新增模型
1. 编辑 `app/data/model_specs.json`，在相应类别下添加模型条目
2. 运行 `python tools/generate_model_docs.py` 重新生成本文档
3. 如需代码引用，使用 `app.services.model_specs` 模块

### 代码引用示例
```python
from app.services.model_specs import get_model_spec, is_vision_model, get_aspect_ratios

# 获取模型规格
spec = get_model_spec('nano-banana-2')

# 检查是否支持视觉
if is_vision_model('gpt-4o'):
    # 可以发送图片
    pass

# 获取支持的宽高比
ratios = get_aspect_ratios('nano-banana-2')
```
