# 项目声明

萤火画布（Firefly Canvas）由 miaomiaozii 独立开发和维护。

- 项目作者：miaomiaozii
- 项目仓库：https://github.com/miaomiaozii/Firefly-Canvas
- 项目名称：萤火画布（Firefly Canvas）

本项目为独立项目，不隶属于任何第三方画布项目，不暗示任何第三方认可本项目的内容、路线或修改。仓库的 Git 提交历史记录了本项目自身的开发过程。

早期开发阶段曾参考社区开源画布项目的设计思路。随着版本演进，本项目已进行大范围重构，与早期参考来源的产品形态显著分化。

## 第三方来源登记

### ComfyArt（MIT License）——交互模式引用（#256）

- 引用范围：ComfyArt `src/elements.ts:33-35` `runOcr` 交互模式——图片节点「提取文字」→ 服务端 OCR → 识别结果落可编辑文本。
- 本项目落点：`static/js/smart-canvas.js` 图片节点右键菜单「提取文字」入口 + `app/routers/api_ocr.py` 服务端 OCR 端点（#256）。
- 说明：仅交互模式参考，实现代码为本项目原创；OCR 引擎选型 rapidocr-onnxruntime（见 `app/routers/api_ocr.py` 头部 ADR-lite 注释）。

### ComfyArt（MIT）——代码移植登记（#255）

`static/js/canvas-vector-path.js` 与 `static/js/canvas-layers.js` 中的像素/路径纯函数移植自 [ComfyArt](https://github.com/ComfyArt/ComfyArt)（参考版本 v1.1.0 @ 13396a8，本地审计版本 v1.0.1 @ 515d36e）：

```
MIT License

Copyright (c) 2026 Terry Jia

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

移植范围（源 → 我方）：

| 源（ComfyArt） | 目标（Firefly-Canvas） | 说明 |
| --- | --- | --- |
| `src/vectorPath.ts` `scalePath` | `static/js/canvas-vector-path.js` `window.CanvasVectorPath.scalePath` | 逐式移植（含指令解析怪癖：含 e 的 number token 被指令判断吞掉，见单测） |
| `src/vectorPath.ts` `nodesToPath` | `window.CanvasVectorPath.nodesToPath` | 逐式移植（PenNode → 归一化 0..1 三次贝塞尔路径） |
| `src/layers.ts` `measure` | `static/js/canvas-layers.js` `window.CanvasLayers.measureLayer` | 去 media 依赖，cover/solid/diff 语义保留（OPAQUE=128 / SOLID=200 / 999 哨兵） |
| `src/layers.ts` `overlap` | `window.CanvasLayers.layerOverlap` | IoU（alpha>128）逐式保留 |
| `src/layers.ts` `cropLayer` bbox+裁剪 | `window.CanvasLayers.layerAlphaBBox` / `cropLayerData` | 上传部分不迁，改返回像素缓冲；alpha<=8 视为空 |
| `src/layers.ts` `separateRegions` 像素循环 | `window.CanvasLayers.splitLayerByBoxes` | 分数坐标 box → 区域像素，消费像素清零 |
| `src/layers.ts` `regionSplit` 掩膜合成 | `window.CanvasLayers.composeWithMask` | 忠实原版：掩膜判断用 m[p]（灰度图 RGBA 的 R 通道），mask>=128 保留原像素、alpha 置 255、union 累积 |

未移植（依赖 tldraw / comfy 后端 / paper.js / DOM）：`registerPlacer` 系、`grow`（canvas blur）、`canvasOf`/`pixels`、`loadPaper`/`normalized`。点-多边形命中不在 ComfyArt 仓库内（票面所引 `utils.ts:250-268` 于参考版本中不存在），改用标准 even-odd 射线法自研实现于 `static/js/canvas-geometry.js` `pointInPolygon`。



### ComfyArt — LLM 用量归一/累加逻辑（MIT License）

issue #258 将 ComfyArt 仓库 `bot/_cli_common.py`（约 62-87 行）的 `normalize_usage` / `merge_usage` 逻辑及其字段别名表（`_USAGE_KEY_ALIASES`）移植为本项目的 `firefly_usage.py`：多 provider 字段别名归一（OpenAI 风格 prompt_tokens/completion_tokens 等）与跨 turn 累加。移植方式为按本项目 schema 重写（别名表另增 ACP camelCase 与 pi SDK Usage 短键），未逐行复制原文件。

- 来源文件：`bot/_cli_common.py`（normalize_usage / merge_usage / _USAGE_KEY_ALIASES）
- 许可协议：MIT
