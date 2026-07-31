# Clean-Room 重写计划（脱离上游继承）

> 状态：草案 v1
> 目标：把继承自上游 Infinite-Canvas 的代码逐模块干净重写为自有实现，许可证完全归属本项目，同时为 pi 化改造铺平模块化地基。
> 关联：`docs/agent-pi-roadmap.md`（五段路线图）、`LICENSE`（萤火画布自有许可证）

## 1. 判定标准与总原则

**哪些模块必须 clean-room 重写**：代码源自上游继承（或架构承袭上游）的大单体。

**哪些模块只需保留/重构**：完全自研的新层（2026-07 起新增），是重写后的新地基，不动。

**Clean-room 的含义**：不参考上游源码，仅以「现有测试 + 浏览器回归 + 功能规格」为行为规格，重新实现同等功能。因此：

1. **测试即规格**：现有 303 项单元测试 + P0 浏览器回归（4 mode × 桌面/移动）就是每轮重写的验收基准；每重写一个模块，全量测试必须保持全绿。
2. **先拆后重**：大单体先做物理拆分（保行为），再逐个替换实现（clean-room），避免一次性重写 2 万行导致不可验证。
3. **保留层即地基**：`firefly_projects.py` / `firefly_generation.py` / `project-canvas-mutations.js`（自研存储与生成层）作为重写后的服务端/前端底座，main.py 与 smart-canvas.js 重写为调用它们的新壳。
4. **顺序绑定路线图**：本计划是 pi 路线图 P0 的一部分，不新开并行赛道。

## 2. 现状盘点（继承度证据）

| 文件 | 规模 | 首次提交 | 判定 |
|---|---|---|---|
| `main.py` | 20427 行 / 175 路由 | 2026-05-11（自导入日） | **继承上游**，服务端唯一单体 |
| `static/js/smart-canvas.js` | 18847 行 / 1119 函数 | 2026-05-24 | **继承上游**，前端唯一单体 |
| `static/js/i18n/*` | 333+293+207+186 行 | 2026-05-25 | **继承上游**文案 |
| `static/js/theme.js` | 251 行 | 2026-05-22 | 继承上游，小模块 |
| `static/js/image-preview.js` | 139 行 | 2026-05-22 | 继承上游，小模块 |
| `static/smart-canvas.html` | — | 2026-05-20 | 继承上游模板 |
| `static/js/touch-mouse.js` | 118 行 | 2026-07-04 | 自研（交互层） |
| `static/js/global-settings.js` + `global-settings.html` | 407 行 | 2026-07-21 | **自研** |
| `static/js/project-list.js` | 150 行 | 2026-07-21 | **自研** |
| `static/js/project-canvas-mutations.js` | 360 行 | 2026-07-23 | **自研**（durable snapshot/rebase） |
| `firefly_projects.py` | 2695 行 | 2026-07-21 | **自研**（项目库存储层） |
| `firefly_generation.py` | 529 行 | 2026-07-22 | **自研**（生成编排） |
| `local_edit.py` | 105 行 | 未进 git | **自研**小工具 |

**结论**：真正需要 clean-room 的只有两个大单体（main.py、smart-canvas.js）+ 三个小件（i18n 文案、theme、image-preview、html 模板、prompt templates 内容）。自研层已覆盖存储、生成、快照、设置、项目列表——这正是重写后的新骨架。

## 3. 重写优先级（W1→W4，每轮必须全量测试全绿）

### W1 — 服务端 main.py 拆分为模块化路由包（最高优先）

**原因**：175 条路由集中在 2 万行单体，是 pi 接入（WebSocket 桥接、工具白名单权限层）的最大障碍；拆包后每个功能域可独立 clean-room。

**步骤**：

1. **物理拆分（保行为）**：新建 `app/` 包，按路由域把 main.py 拆成独立 router 文件，全部 `include_router` 挂回，路由前缀与行为零变化：
   - `app/routers/project_library.py`（45 条，最大域）
   - `app/routers/asset_library.py`（17）
   - `app/routers/canvases.py`（12）
   - `app/routers/prompt_libraries.py`（11）
   - `app/routers/local_assets.py`（11）
   - `app/routers/jimeng.py`（7）、`providers.py`（6）、`shared_folders.py`（6）
   - `app/routers/runninghub.py`（4）、`global_settings.py`（4）、`conversations.py`（4）、`projects.py`（4）
   - `app/routers/ai_chat.py`（ai 3 + chat 3 + canvas-llm 1 + canvas-chat 相关）、`storage_files.py`（3）、`smart_canvas.py`（2）、`generation.py`（canvas-image-tasks 2 + image-task-query + queue_status + download-output 等生成域）、`codex_gemini.py`（codex 2 + gemini-cli 2）、`misc.py`（其余零散）
2. **共享逻辑提取**：鉴权/Origin 信任、媒体导入、参数清洗等抽到 `app/core/`（`deps.py`、`media.py`、`params.py`、`security.py`），main.py 只留 app 装配 + 静态托管 + 健康检查。
3. **逐个 clean-room 重写**（按路由域，从下往上）：
   - 先重写 `misc / config / app-info / models / view` 等无状态小域（练手 + 验证流程）
   - 再重写 `conversations / history / smart_canvas` 中等域
   - 最后重写 `project_library` 域（业务最重，依赖 firefly_projects 存储层，重写为纯路由壳 + 校验层）
4. **每域验收**：该域浏览器回归 + 全量单元测试全绿；`git diff` 确认无上游注释/命名残留。

**W1 完成标志**：main.py 降到 <1000 行（仅装配），`app/` 包全部为自有实现；175 路由路径/参数/行为完全一致；测试全绿。

### W2 — 前端 smart-canvas.js 拆分与核心域重写

**原因**：1119 个函数的 1.9 万行单体，`render()` 重建整个 DOM 是性能与 pi 控制台（节点式会话）的直接障碍。

**步骤**：

1. **物理拆分（保行为）**：按职责拆为 ES modules（仓库无 bundler，用原生 `<script type="module">` 或保留 script 加载顺序 + 命名空间导出）：
   - `static/js/canvas/state.js`（节点/边/项目状态与撤销栈）
   - `static/js/canvas/renderer.js`（渲染引擎、视图变换、节点 DOM 复用 keyed 策略）
   - `static/js/canvas/save.js`（保存链：durable snapshot / PATCH / flush，依赖 project-canvas-mutations.js）
   - `static/js/canvas/media.js`（媒体引用、归档、import）
   - `static/js/canvas/history.js`（历史抽屉、原图删除/恢复）
   - `static/js/canvas/agents.js`（Agent 节点：计划/执行/会话）
   - `static/js/canvas/generation.js`（生成节点、批量、参数 schema）
   - `static/js/canvas/chat.js`、`edit.js`（局部编辑）、`clipboard.js`、`layout.js`、`import_export.js`
   - `static/js/canvas/api.js`（fetch 封装）
2. **逐个 clean-room 重写**：
   - 先 `api.js` → `state.js`（纯数据，最好测）
   - 再 `save.js`（已有 P0 回归护住：编辑不丢失 / 保存收敛 / 媒体渲染 / 生成 flush）
   - 再 `media.js`、`history.js`（同样有 P0 回归）
   - 再 `renderer.js`（P0 回归 + 性能验收：500 节点交互延迟）
   - 最后 `agents.js`（对接 pi 时顺便重写）
3. **每域验收**：P0 浏览器回归全绿 + 交互冒烟（画布可拖拽/缩放/连线/撤销）。

**W2 完成标志**：smart-canvas.js 拆完，主入口 <500 行；renderer 支持 keyed DOM 复用（为 pi 控制台挂载铺路）；P0 全绿。

### W3 — 继承小件重写（低风险，可与 W2 并行）

- `static/js/i18n/*`：文案全部重写为自有措辞（结构保留：validate-i18n.js 校验各语言 key 一致），去掉上游措辞残留。
- `static/js/theme.js`：重写为自有主题系统（深浅色 token）。
- `static/js/image-preview.js`：重写预览逻辑。
- `static/smart-canvas.html` / `static/global-settings.html`：重写模板结构（语义化、去上游类名）。
- `static/system-prompts/firefly-canvas-prompt-templates.md`：内容重写为自有提示词模板体系（10 个模板，端点 `/api/smart-canvas/prompt-templates` 行为不变）。

**W3 完成标志**：全库 grep 无上游标识（Infinite / hero8152 等）残留；validate-i18n 通过；页面视觉回归通过。

### W4 — 保留层：自研模块仅重构不重写

`firefly_projects.py`、`firefly_generation.py`、`project-canvas-mutations.js`、`global-settings.js`、`project-list.js`、`local_edit.py`、`touch-mouse.js`：**不做 clean-room**。仅按需补充测试覆盖与模块边界整理（它们本身就是 2026-07 自研的新地基，W1/W2 重写以它们为底座）。

## 4. 与 pi 路线图的对齐

| 重写轮次 | 服务 pi 路线图的意义 |
|---|---|
| W1 | pi 桥接层（WebSocket/SSE 会话流、工具白名单权限层）落在 `app/core/security.py` 与独立 router，不再塞进 2 万行单体 |
| W2 | renderer keyed 复用 → pi 节点式控制台（iframe 挂载或 keyed 子树）可行；save 域已稳定 → 托管模式生成可回滚 |
| W3 | 自有文案 → 许可证完全独立 |
| W4 | firefly_projects 存储层 → pi 会话按 `{project_id, agent_node_id}` 独立持久化 |

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 2 万行单体拆分后行为漂移 | 先物理拆分零行为变化并全量回归，再逐域重写；每域测试全绿才进下一域 |
| clean-room 重写期间 pi 路线图停摆 | W1 拆包本身不阻塞 P0 其余任务；重写与测试并行推进 |
| 测试覆盖面不足的域（如 jimeng/gemini-cli） | 拆分期先补浏览器冒烟用例（含 fake adapter），再重写 |
| 前端无 bundler，模块化改造成本 | 用原生 ES modules 或保留 script 顺序加载 + IIFE 命名空间，两种方案先做小实验选型 |

## 6. 里程碑

- **M1**（W1 完成）：`app/` 包成立，main.py <1000 行，175 路由行为一致，测试全绿。
- **M2**（W2 完成）：smart-canvas.js 拆分完成，renderer keyed 化，P0 浏览器回归全绿。
- **M3**（W3 完成）：全库无上游残留，许可证完全独立。
- **M4**（W4 收尾）：保留层测试补齐，进入 pi 路线图 P0 验收 → P1。
