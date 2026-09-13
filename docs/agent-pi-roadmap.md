# Firefly Canvas × pi 智能基座路线图

> 状态：草案（v1，待评审）
> 关联：`docs/adr/0012-agent-is-a-separate-node.md`（Agent 独立节点约束）

## 1. 目标

把画布升级为可迭代的智能工作台：

1. **运营图片方案 Excel → 拆封为要求 → 展开为完整节点工作流**（需求、提示词、生成节点、比例、模型全部预设排好）。
2. **人工手动打标 + 一键收集**：快速挑选要用的图片，直接收集到输出目录；打标数据同时成为 pi 自我迭代验收的 ground truth。
3. **pi 作为 Agent 节点的智能内核**：节点式、可自由移动/缩放/复制出独立会话，充当画布内中控台。
4. **远期**：画布基础功能扎实后，pi 可以在画布内自我修复画布 bug。

核心判断：**pi 是放大器，不是 bug 生成器**。画布基础功能的稳健性是 pi 接入的前提，因此本路线图按 P0→P4 五段执行，前一阶段验收未达标准不进下一阶段。

## 2. 设计原则（所有阶段强制）

1. **确定性优先**：任务清单、数量、依赖关系永远来自 Excel 解析器；LLM 只做提示词润色和歧义追问，禁止 LLM 编造任务（防止幻觉删任务）。
2. **无 LLM 可用时功能不瘫痪**：Excel→方案→生成的主链路不依赖 pi；缺 key / 断网时自动降级到确定性链路。
3. **Agent 只写自己管的东西**：所有 Agent 创建的节点记录 `agent_owner_id / session_id / plan_id / plan_version`；用户锁定的字段（比例、模型、手动改过的提示词）Agent 不得静默覆盖。
4. **表格内容是不可信数据**：Excel 单元格、文件名当数据不当指令；解析出的提示词标注来源（sheet/行号），防 prompt injection。
5. **默认协作模式**：解析、布局、优化提示词自动做；首次生成与批量重生成必须用户确认。托管模式按节点单独开启。
6. **不依赖实验性协议**：pi 的 `pi-server` / `pi-protocol` 官方标注 experimental、API 不稳定，Firefly 自行实现稳定桥接层。

## 3. 优先级总览

| 段 | 内容 | 进入条件 |
|---|---|---|
| **P0** | 画布基础功能完善（稳定性/一致性/性能/数据安全/测试地基） | 当前 |
| **P1** | 无 LLM 主链路：Excel→拆解→展开→手动生成 + 打标收集 | P0 验收通过 |
| **P2** | pi 基座接入：provider/key 管理、运行时、会话、节点控制台 | P1 验收通过 |
| **P3** | 协作/托管模式：自动搭建、视觉检查、受限自主迭代 | P2 验收通过 |
| **P4** | 远期：pi 自我修复画布 bug | P3 稳定后另立方案 |

## 4. 前置决策（✅ 2026-09-13 已拍板，详见 `docs/adr/0019-pi-sidecar-runtime-deep-integration.md`）

| 决策 | 选择 | 理由（ADR 0019） |
|---|---|---|
| pi 运行时 | **A：Node sidecar（独立进程），一步到位** | 会话跨刷新/重启持久，支撑 P3 托管与自迭代；不做内嵌两段式 |
| 控制台形态 | **A：节点内展开（节点式）** | 画布自由坐标系，复制节点=新会话 |

> ADR 0019 追加的设计约束：接口/扩展点先行（工具插拔）、迁移期兼容旧 plan-first Agent、pi 可读应用结构化日志、pi 拥有维护/清理工具、`app/core.py` 拆分并入 P2、Excel 导出暴露为 pi 工具。北极星：自适应、自迭代、自修复。

## 5. P0 — 画布基础功能完善（当前）

**目标**：让画布成为 pi 的可信基座。

### 任务

- [ ] 稳定性
  - [ ] 保存链收敛：无重复 PATCH/flush 请求、无 dirty 卡死（现状：`smart-canvas.js:5843-6084` 保存链，`hasDurableCanvasChanges`/`saveCanvas` 已修复一轮，需持续回归）
  - [ ] 异常恢复：崩溃后项目可读；异步图片测量（`measureSmartNodeImages`，`smart-canvas.js:8699-8766`）不再制造 late durable change
  - [ ] 多入口保存并发保护（自动保存 vs 手动保存 vs 恢复保存）
- [ ] 一致性
  - [ ] 撤销/重做、历史抽屉、局部编辑、原图删除/恢复的编辑保留（`restoreSmartProjectOriginalUndo` 三方 rebase 已实现）
  - [ ] 计划 hash 校验、输入变更后计划失效
- [ ] 性能
  - [ ] 数百节点级渲染/缩放/平移流畅（`render()` 重建策略优化）
  - [ ] 大项目导入导出不卡死
- [ ] 数据安全
  - [ ] 导出/导入往返无损；回收站；损坏文件修复入口
  - [ ] 运行中任务纳入项目删除/移动/导出保护
- [ ] 测试地基（pi 接入后唯一的"可信基准"）
  - [ ] 浏览器回归（桌面 `1408,710` + 移动 `500,749`）持续全绿
  - [ ] 全量单元测试全绿（当前基线 303 tests + 1 skipped）

### 验收标准

- 全量测试 + 浏览器回归全绿。
- 连续高强度操作无崩溃；保存/恢复往返无数据差异。
- 性能指标达标（如 500 节点交互延迟可接受）。
- 运行中任务在项目删除时被中止，无孤儿任务/半成品媒体残留。

## 6. P1 — 无 LLM 主链路（先证明核心价值）

**目标**：把历史 C# 工具（`ImagePlanDesignerP0`）的成熟能力搬进 Firefly，全程不依赖 LLM。

### 任务

- [ ] Excel 解析（移植到 Python）
  - [ ] OpenXML `.xlsx` 读取（无 Excel COM 依赖）：shared strings、sheet 关系、表格图片 + anchored drawing 图片抽取为项目资产
  - [ ] 表头 / 无表头 / 板块标记识别；主图 / A+ 任务拆解；比例、文案、提示词、同主图依赖识别
  - [ ] 参考图角色：运营展示 / 生图池
- [ ] Plan IR（迁移自 `ImagePlanDesignerP0` 的 `ImageTask` 字段）
  - `TaskId / Section / SourceRow / DisplayNo / OutputType / Requirements / CopyHandlingMode / CopyText / BasePrompt / TargetAspectRatio / SameAsMain / LinkedMainIndex / OperationReferences`
- [ ] Agent 节点支持拖入 / 连接 Excel 文件节点
- [ ] 计划预览卡片：任务清单、参考图角色、比例与模型预设
- [ ] **原子展开**：一次事务创建全部节点、边和布局（禁止逐节点 PATCH）
- [ ] 确定性布局器：左侧方案/素材区、中间 Agent、右侧主图/A+ 分区；行内顺序 需求→提示词→生成→结果；防重叠 + 边连通（生成节点经边接收上游引用）+ 视口定位
- [ ] 展开后走现有手动生成链（选中生成 / 批量生成）
- [ ] **人工打标与一键收集**
  - [ ] 结果节点快捷打标（✓ / 星标 / 自定义标签），持久化到节点 meta（`curated: true, tags: [...]`），随项目 / Fly 导出迁移
  - [ ] 一键收集：收集节点引用选中图片 asset_id，按命名规则批量导出到指定目录或打包 zip；收集节点可在画布上展示并随项目保存
  - [ ] 打标数据成为 P3 视觉检查的 ground truth 数据源

### 验收标准

- 基准样例 `加热玩偶-高地牛-图片方案0509.xlsx` 复测：13 sheets / 23 行 / 15 任务（8 主图 + 7 A+）/ 70 张内嵌图，与历史烟测（`ImagePlanDesignerP0/bin/excel-import-smoke.json`）一致。
- 展开 60 节点级方案：原子事务成功、无重叠、边全部连通。
- 断网 / 无 LLM key 时全链路可用。
- 打标 → 收集 → 导出往返无损，收集节点可保存/加载。

## 7. P2 — pi 基座接入（Node sidecar，ADR 0019）

**目标**：补齐 pi 运行前置条件，接通智能层；深度底座与画布产品融合（接口先行、插拔、自诊断地基）。

### 任务

- [ ] LLM provider 配置 UI + key 安全存储（浏览器端加密 + 后端落盘；参考旧 C# 工具 DPAPI 思路的 Web 实现）
- [ ] **Node sidecar 运行时**（ADR 0019，一步到位）：sidecar 进程启动/守护/崩溃自愈/僵尸回收、与 FastAPI 的版本一致性校验、锁定并 vendor pi-agent-core 版本（当前 `@earendil-works/pi-agent-core` / `@earendil-works/pi-web-ui@0.75.3`）；Windows 分发包含 Node 运行时（渠道见 `0009-independent-release-channel.md`）
- [ ] FastAPI 桥接层：WebSocket/SSE 会话流；`{project_id, agent_node_id}` 独立 session 存储（不写 canvas.json）；**工具注册/插拔架构**（新能力不硬编码进桥接层）
- [ ] **pi 日志访问工具**（`inspect_app_logs`）：结构化日志按级别/时间/模块过滤；不暴露 key/凭证明文
- [ ] **pi 维护工具**（清理孤儿资产/半成品任务、项目完整性校验），为 P3 定期自清理/自修复铺路
- [ ] 会话纳入 Fly 导出/导入与项目复制/删除清理
- [ ] 工具白名单（只给高层工具，不给 PATCH canvas / 文件系统 / Shell）：
  `inspect_canvas_context` / `import_excel_plan` / `propose_image_plan` / `materialize_image_plan` / `update_managed_prompt` / `configure_generation_nodes` / `run_generation_batch` / `inspect_generation_results` / `revise_failed_tasks` / `layout_agent_plan` / `export_collection`（issue #58 导出能力，首要消费者是 pi）
- [ ] 工具执行全部走服务端权限层：plan hash 确认、同项目输入隔离、字段锁定校验；**兼容期旧 plan-first Agent（#14）链路继续可用**，与 pi 工具共享权限层
- [ ] 模型可用性校验：预设模型消失时执行前报错并提示可选模型
- [ ] **`app/core.py` 结构化重组**（ADR 0019）：请求模型/异常/multipart 解析器分家，53 个 import 源一次迁移，一次 820+ 测试回归
- [ ] **节点式控制台**
  - [ ] Agent 节点支持自由缩放（resize 手柄，替代当前固定 382×360，见 `smart-canvas.js:6217 createAgentNode`）
  - [ ] 解决 `render()`（`smart-canvas.js:8569`）重建销毁问题：Agent 子树 keyed DOM 复用，或独立 iframe 挂载（画布重渲染时 iframe 不动，会话状态零丢失）
  - [ ] 复制 Agent 节点 = 新建独立会话（带"复制会话配置"选项）
  - [ ] 控制台 UI：聊天、流式消息、思考块、工具调用卡片、附件、模型/思考级别选择（复用 pi-web-ui 组件：`ChatPanel` / `AgentInterface` / `MessageList` / `ToolMessage` / `ThinkingBlock` / `StreamingMessageContainer` / `AttachmentTile` / `ModelSelector`）

### 验收标准

- 配置 key 后 pi 会话可启动、工具可调用、事件流可展示；key 失效有明确降级提示。
- 无 key / 网络失败时，P1 链路不受影响。
- 节点重渲染/缩放/拖拽后，控制台滚动位置与输入不丢；刷新页面/重启 sidecar 后会话可恢复（sidecar 持久性验收）。
- sidecar 崩溃自动恢复，无僵尸进程；版本不一致有明确提示。
- pi 通过工具读取到应用日志（不含凭证明文）、执行一次孤儿资产清理且画布数据无损。
- Fly 导出导入后会话随项目完整迁移。
- `app/core.py` 重组后 820+ 测试全绿，无新双轨。

## 8. P3 — 协作 / 托管模式

**目标**：方案里的"导入即拆封排好" + 受限自主迭代。

### 任务

- [ ] 协作模式
  - [ ] pi 读取解析结果 + 画布上下文，产出 Plan IR（任务数量以解析器为准）
  - [ ] 自动布局 + 提示词优化（保留版本历史），生成前必须用户确认
  - [ ] 用户锁定字段：手动改过的提示词/比例/模型打锁，Agent 只能建议不能覆盖
  - [ ] 输入变更 → 计划自动失效 → 提示重新规划
- [ ] 托管模式（默认关闭，按节点开启）
  - [ ] 批量生成 + 结果检查（视觉模型评估是否符合要求；无视觉模型则跳过）
  - [ ] 迭代循环：失败/不合格任务修订提示词重跑，保留每一版提示词与旧图
  - [ ] 硬性预算：每会话步数上限、生成次数上限、模型范围、费用上限，达到即停
  - [ ] 变更日志：Agent 所有操作可回溯，支持"回滚到本轮之前"
- [ ] 验收数据闭环
  - [ ] 人工标注（P1 打标）作为 ground truth：pi 视觉检查结果与人工标注对齐，输出人机一致率报告
  - [ ] 阈值校准：根据一致率调整视觉检查通过/失败阈值

### 验收标准

- 同一 Excel 连续规划两次，任务集合一致（确定性断言）。
- 用户锁定字段后，Agent 重规划不改动该字段（有测试覆盖）。
- 确认前不产生任何生成请求；取消后画布无残留。
- 预算耗尽/超时后自动停止，无后台续跑。
- 迭代后旧版本提示词与图片均可找回。

## 9. P4 — 远期：pi 自我修复画布

**目标**：画布基础扎实后，让 pi 在画布内诊断并修复画布自身 bug。

### 前置条件（写进愿景，不进当前工作）

- [ ] 结构化诊断接口：健康检查、错误快照、状态导出（`/api/.../diagnostics`），pi 才能"看到"bug
- [ ] 修复走受控工具：与生成一样白名单化，pi 只能提交修复计划，人工确认或限定自动范围
- [ ] 回滚机制：每次修复有版本记录，可一键还原（依赖 P0 数据安全能力）
- [ ] 复用骨架：P4 的"诊断 + 补丁"工具集与 P3 的"画布编辑"工具集是同一套 pi 会话骨架

### 验收标准（远期草案）

- pi 在沙箱环境（fake runtime + 注入故障）能定位并修复指定故障类，修复可回滚。
- 修复操作不触碰用户数据，全部记录审计日志。

## 10. 技术现状锚点（实现时对照）

| 主题 | 位置 |
|---|---|
| Agent 节点创建 | `static/js/smart-canvas.js:6217`（默认 382×360）、`:7943 agentNodeBodyHtml` |
| Agent 节点交互 | `:9607 planAgentNode`、`:9652 executeAgentNode`、`:9674 bindAgentNodeControls` |
| 画布渲染（重建 Agent DOM） | `:8569 render()` |
| 保存链 / dirty 状态 | `:5843-6084`（`hasDurableCanvasChanges`、`saveCanvas`、`saveProjectCanvasUntilSettled`） |
| 异步图片测量 | `:8699-8766 measureSmartNodeImages` |
| 原图恢复三方 rebase | `restoreSmartProjectOriginalUndo` |
| durable snapshot | `static/js/project-canvas-mutations.js`（`durableNode` / `durableMediaItem`） |
| Agent 后端计划 API | `main.py:14773 create_project_agent_plan`；API `POST /api/project-library/{project}/agent/{node}/plan`、`POST /execute`、GET plan、GET task |
| 输入接入点（pi 接入位置） | `main.py:13433 project_agent_connected_inputs()` 之后、`_agent_normalize_task()` 之前 |
| 参数 schema / 清洗 | `main.py:13510` / `:13569 _agent_clean_params` |
| 执行前重验 | `main.py:14946 _agent_admit_execution_sync`、`:15144 execute_project_agent_plan` |
| Agent 持久字段（canvas 内） | `agentGoal`、`agentTaskDraft`、`agent_plan`、`agent_plans`；项目默认 `D:\Firefly-Canvas-Projects` |
| 历史 C# 领域资产 | `F:\Users\Administrator\Documents\Codex\2026-05-07\new-chat\ImagePlanDesignerP0`（`Program.cs`、`README.md`、`PRODUCT_QA.md`、`bin/excel-import-smoke.json`；`AddTasksFromRows` 在 `Program.cs:1615-1694`） |
| 浏览器回归基建 | `tests/p0_regressions_browser.py` + `p0_regressions_browser_client.js`（4 mode × 桌面/移动）；`tests/conversation_browser.py`（`ConversationBrowserServer`、`load_production_app`、CLIENT_SCRIPT 注入） |
| pi Web UI 组件 | `@earendil-works/pi-web-ui@0.75.3`（`ChatPanel`、`AgentInterface`、`MessageList`、`ToolMessage`、`ThinkingBlock`、`StreamingMessageContainer`、`AttachmentTile`、`ModelSelector`） |
| pi 能力边界 | `pi-agent-core`：有状态 loop、工具调用、事件流、`beforeToolCall` 阻断、abort、steer/followUp、图片输入；**无内建权限系统**（继承宿主进程权限） |

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| pi 快速迭代（0.75.x）API 变动 | 锁定版本 + vendor 打包；不依赖 experimental 的 pi-server/pi-protocol |
| 仓库零 Node 依赖，双运行时分发成本 | 前置决策明确；接受关标签页即断则选浏览器内嵌 |
| LLM 幻觉删任务 | 任务清单确定性来源，LLM 只润色（原则 1） |
| Excel 内容 prompt injection | 表格内容当数据不当指令，提示词标注来源（原则 4） |
| 渲染重建丢会话状态 | keyed 子树或 iframe 挂载（P2） |
| 真 LLM 测试 flaky/成本高 | fake pi-agent-core + 录制 session 回放 + 现有浏览器 harness |
| 多 Agent 并发互相干扰 | P3 收尾：单项目互斥、资源上限、会话剪枝 |
| 用户字段被 Agent 覆盖 | 锁定字段 + 服务端校验（原则 3） |

## 12. 建议的 P0 最小可交付

**P0 安全加固 + P1 完整无 LLM 链路**。独立于 pi 决策、无需 LLM key、价值立即可见（Excel 拖进来 → 拆好排好 → 手动生成 + 打标收集），且是后续所有阶段的地基。pi 是叠加层，不是依赖。
