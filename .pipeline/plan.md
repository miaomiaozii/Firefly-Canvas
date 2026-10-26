# Twin Pipeline 计划：画布前端模块化 P2b–P3

> 仓库：D:/Firefly-Canvas（miaomiaozii/Firefly-Canvas）
> 父 issue：#154（画布前端单文件过大，按功能模块化拆分）
> 已完成前置：#156（P0 依赖分析，docs/canvas-frontend-modularization.md）、#157（P1 布局/几何，canvas-layout-utils.js）、#158（P2a 连线，canvas-connections.js）
> 当前主脚本：static/js/smart-canvas.js（24233 行）

## 模型与端点（已预检 200）

- 主会话（编排/审核）：`vllm-remote/qwen3.8-27b-w8a16-mtp2` @ 192.168.10.147:8000
- twin（实现/测试）：`vllm-remote-twin/qwen3.8-27b-twin-ultra-mtp2` @ 192.168.10.147:8006

## 批次划分

| 批次 | Issue | 模块 | 新文件 | 状态 |
|---|---|---|---|---|
| batch-1 | #159 | P2b 撤销/重做 | static/js/canvas-undo.js | pending |
| batch-2 | #160 | P2c 节点操作 | static/js/canvas-node-ops.js | pending |
| batch-3 | #161 | P2d 媒体/历史 | static/js/canvas-media-history.js | pending |
| batch-4 | #162 | P3 转 ES module | （改造全部模块） | pending |

**划分理由**：
- 硬规则「同批 issue 不碰同一文件」：#159/#160/#161 都要从 `static/js/smart-canvas.js` 搬函数（各自不同的簇），必然同文件 → 每批 1 条（2–4 条的吞吐指引让位于文件冲突硬规则）。
- 依赖：#162 明确 Blocked by #159/#160/#161（先完成各 feature 模块拆分）→ 排最后。
- 每个 P2 抽取都是高风险精细活（24K 行文件、跨引用、TDZ、混合函数排除），单批单 issue 让 twin 会话短、KV 前缀稳、审核粒度清晰。

## 分支与合入策略

- 工作分支：`pipeline/canvas-modularization`（从 main 建）。
- 每批测试全绿后：分支上提交（message 引用 issue 编号）→ main 快进合并 → 推 origin main（沿用 #157/#158 的直推模式，无 PR）。
- 全部批次完成后：更新父 issue #154 进度、写 `.pipeline/report.md`。

## 验收标准（默认）

每条 issue 的 Verification 段（照抄见各任务卡）+ 全量回归 `python -m pytest tests/`（基线 1040 passed / 351 subtests，约 5.5 分钟）。

## 已知风险与缓解（来自 P2a 经验，写入各任务卡）

1. 抽离模块只放**纯函数定义**，不放顶层执行代码（DOM 引用等顶层执行留主脚本或确认加载时序）。
2. 混合函数（同时强依赖主脚本核心状态机 + WebSocket 等）不抽，留主脚本。
3. 抽后 grep 确认主脚本无残留引用/重复定义；`node --check` 两文件。
4. 浏览器冒烟（Playwright，Chrome 路径 `C:/Program Files/Google/Chrome/Application/chrome.exe`，参考 tmp/qa/qa17-p2a-extract.js）覆盖该 feature 核心交互；注意测试脚本自身缺陷（如元素遮挡命中点）——用 elementFromPoint 验证命中。
5. 冒烟清理测试数据后显式 `await manualSaveCanvas()` 持久化，避免防抖未 flush。
6. 全量 pytest 回归。
