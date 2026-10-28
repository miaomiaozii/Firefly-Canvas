# issue-249 结果报告（W3 UI 卫生包）

**状态：完成**（票面四项卫生包全部落地；验收 `quality_gate fast/domain` + 204 单测 + 全量 pytest，见文末验证表）

分支 `issue/249`（基于 main c6541df），只 commit 未 push。

## Commits

| commit | 内容 |
|---|---|
| `6124289` | #249: 缺失媒体/存储文件读取端点返 204 而非 404（ComfyArt userdata 约定） |
| `97f59fa` | #249: 外部 JSON 资源引用加锁测试（缺失/损坏回归防护） |
| `8dccc9b` | #249: Escape 栈统一管理（全局面板/弹窗，ComfyArt 分层结算模式） |
| `8d8e999` | #249: 一次性审计删自造上限（轮询/重试/超时魔法数写成注释事实） |

## 四项卫生包状态

### 1. Escape 栈统一管理 ✅

- 新增 `static/js/escape-stack.js`：~24 行栈核心（ComfyArt `src/ui/escape.ts` 模式），
  唯一捕获阶段 keydown 监听；Escape 只结算栈顶层并吞事件（stopPropagation 阻止穿透
  旧级联）；输入目标（input/textarea/contentEditable）不拦截；栈空不干预（旧行为兜底）。
- 迁移 4 个全局模态层入栈：图片编辑器（`static/js/canvas-image-editor.js`）、
  设置弹窗/批量任务详情/删除范围对话框（`static/js/smart-canvas.js`），
  移除各自散装 window/document Escape 监听。
- 修复两处既有缺陷：一次 Escape 同时关多层并穿透画布底层；图片编辑器内
  contentEditable 按 Escape 误关整个模态。
- 范围说明：栈核心 + 最高频的全局模态层；`smart-canvas.js` 内其余元素级局部
  Escape 处理（如回收站预览、行内编辑器）是局部作用域行为，未迁移，保持原样。

### 2. 缺失文件读取端点返 204 ✅

- `/api/media-preview`（`app/routers/api_media_preview.py`）与
  `/api/storage-files/{kind}/{rel}`（`app/routers/api_storage_files.py`）：
  目标文件不存在时返回 **204 No Content** 而非 404——这两个是「可能不存在的文件」
  探测型端点（缩略图/预览按候选 URL 探测，媒体删除后引用悬空），204 让
  console/网络面板不再刷 404 错误（ComfyArt `api/userdata.py:15-16` 约定）。
- 越界/非法路径仍 400（安全语义不变）；可生成文件仍 200。

### 3. 一次性审计删自造上限 ✅（注释事实化，数值不变，唯一例外见下）

原则：不发明上游没有的数字；真上限/工程选择写成注释事实（抄 ComfyArt
`H3_MAX_SECONDS=3600/24` 写法）。审计范围=轮询/重试/超时魔法数：

- `app/core.py`：轮询/超时常量逐条标注性质（`*_TIMEOUT`=自设安全阀非 SLA；
  `*_POLL_INTERVAL`=负载平滑；任务级硬上限由
  `image/video_generation_task_timeout_seconds()` 负责）；
  `RESPONSES_POLL_MAX_SECONDS` 从 `1500.0` 改写为 `25*60.0` 并注释设计意图
  （故意短于 30 分钟请求超时，后端抢先报「未完成」502，前端才拿得到明确错误文本）。
- `app/services/generation.py`：**唯一行为变更**——长耗时模式（gpt-image-2/APIMART/
  openai-json 系）read 超时原硬编码 `1800.0`，不跟随 `REQUEST_TIMEOUT` env；
  收敛为 `AI_REQUEST_TIMEOUT` 单一事实来源（默认值不变，默认行为零变化）。
- `static/js/canvas-generation-tasks.js`：即梦 `60s×1440=24h` 标注为自设上限
  （付费任务不能过早判丢，非即梦 SLA）；未知任务终态化属 W1 终态机（#235）。
- `static/js/smart-canvas.js`：`CONVERSATION_MAX_NOT_FOUND_POLLS=30`（1s/次，
  30s 宽限）标注为工程选择。
- `main.py`：`PROJECT_AGENT_TASK_TIMEOUT_SECONDS=15*60` 标注为自设上限（上游 CLI 无 SLA）。

### 4. 外部 JSON 资源引用加锁测试 ✅

- `tests/test_external_json_resources.py`（111 行）：钉住代码引用的外部 JSON 资源
  的存在性/可解析性/关键结构（ComfyArt `tests/test_workflows.py` 思路）：
  - `app/data/model_specs.json`：存在+可解析+关键结构；`load_specs` 损坏→`ValueError`、
    缺失→空结构契约；
  - `static/runninghub/api_providers.json`：存在+可解析；
    `load_static_runninghub_provider` 正常形状契约（注：该 loader 损坏时静默返回
    None，测试钉住「正常形状」，静默行为是既有语义，未改）；
  - 可选 `models_registry.json`：只钉「若存在则可解析」（文件当前不存在属合法态）。

## 测了什么

| 测试文件 | 契约 |
|---|---|
| `tests/test_missing_media_204.py` | 204（缺失）/200（可生成）/400（越界）四条，覆盖两个端点 |
| `tests/test_external_json_resources.py` | JSON 资源存在性/可解析/结构 + loader 损坏契约 |
| `tests/test_escape_stack_contract.py` | escape-stack.js 加载顺序、捕获阶段监听唯一性、四个模态登记、旧散装监听已移除（静态契约，无浏览器依赖） |
| 全量 `pytest tests/ -x -q` | 回归（见验证表） |

## 前置票依赖核查

- 票面第 3 项「对齐本 Wave 终态化后的状态机」：#234（手动取回三层修复）已合入 main
  （merge `6feca94`）；**#235（终态化）未合入 main**（main 上仍为派发态）。
  → 本分支按最小可行方式执行：审计+注释事实化完成，即梦 24h 轮询的未知任务终态
  判定式**不自行发明**，注释中标注待 #235 落地后对齐。
- 即梦剔除（#236）亦未合入 main（`FIXED_PROTOCOL_PROVIDER_IDS` 仍含 jimeng），
  即梦轮询常量保留、仅加注。

## 剩余风险 / 留给总控

1. **Escape 栈迁移范围**：4 个全局模态层；`smart-canvas.js` 其余文档级级联
   （历史灯箱/长文阅读/flow 模式/提示词预设面板等约 8 处）仍走旧级联，
   栈空时不干预、行为与改前一致；如需全量迁入栈，建议独立小票（涉 24k 行大文件多处 open/close 配对，本次时箱内不动）。
2. **generation.py read 超时收敛**：默认行为不变，但若运维环境显式设置
   `REQUEST_TIMEOUT`（非常规），长耗时模式 read 超时随之变化——这是审计意图
   （单一事实来源），非回归。
3. **UI 浏览器基线**：本票为卫生包，票面验收仅 `quality_gate fast/domain` + 204 单测，
   未做浏览器证据 accept；Escape 行为变化（一次只关顶层）如需浏览器证据留总控排期。
4. 未 push、未建 PR；合入后建议跑一次 `trial` 层浏览器证据确认 Escape 交互。

## 验证结果

（pytest / quality_gate 运行后回填）
