# 项目声明

萤火画布（Firefly Canvas）由 miaomiaozii 独立开发和维护。

- 项目作者：miaomiaozii
- 项目仓库：https://github.com/miaomiaozii/Firefly-Canvas
- 项目名称：萤火画布（Firefly Canvas）

本项目为独立项目，不隶属于任何第三方画布项目，不暗示任何第三方认可本项目的内容、路线或修改。仓库的 Git 提交历史记录了本项目自身的开发过程。

早期开发阶段曾参考社区开源画布项目的设计思路。随着版本演进，本项目已进行大范围重构，与早期参考来源的产品形态显著分化。

## 第三方来源登记

### ComfyArt（MIT License）

- 引用范围：ComfyArt `src/elements.ts:33-35` `runOcr` 交互模式——图片节点「提取文字」→ 服务端 OCR → 识别结果落可编辑文本。
- 本项目落点：`static/js/smart-canvas.js` 图片节点右键菜单「提取文字」入口 + `app/routers/api_ocr.py` 服务端 OCR 端点（#256）。
- 说明：仅交互模式参考，实现代码为本项目原创；OCR 引擎选型 rapidocr-onnxruntime（见 `app/routers/api_ocr.py` 头部 ADR-lite 注释）。
