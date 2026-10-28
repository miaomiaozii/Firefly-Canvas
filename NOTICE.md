# 项目声明

萤火画布（Firefly Canvas）由 miaomiaozii 独立开发和维护。

- 项目作者：miaomiaozii
- 项目仓库：https://github.com/miaomiaozii/Firefly-Canvas
- 项目名称：萤火画布（Firefly Canvas）

本项目为独立项目，不隶属于任何第三方画布项目，不暗示任何第三方认可本项目的内容、路线或修改。仓库的 Git 提交历史记录了本项目自身的开发过程。

早期开发阶段曾参考社区开源画布项目的设计思路。随着版本演进，本项目已进行大范围重构，与早期参考来源的产品形态显著分化。

## 第三方代码登记

### ComfyArt — LLM 用量归一/累加逻辑（MIT License）

issue #258 将 ComfyArt 仓库 `bot/_cli_common.py`（约 62-87 行）的 `normalize_usage` / `merge_usage` 逻辑及其字段别名表（`_USAGE_KEY_ALIASES`）移植为本项目的 `firefly_usage.py`：多 provider 字段别名归一（OpenAI 风格 prompt_tokens/completion_tokens 等）与跨 turn 累加。移植方式为按本项目 schema 重写（别名表另增 ACP camelCase 与 pi SDK Usage 短键），未逐行复制原文件。

- 来源文件：`bot/_cli_common.py`（normalize_usage / merge_usage / _USAGE_KEY_ALIASES）
- 许可协议：MIT
