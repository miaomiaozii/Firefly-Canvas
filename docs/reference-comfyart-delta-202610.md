# ComfyArt v1.0.0/v1.0.1 增量拆解 × Firefly-Canvas 优化方案

> 增量范围：ComfyArt `aad271e..515d36e`（27 commits / 233 files / +8631 −2976），即主拆解笔记 `docs/reference-comfyart-canvas.md` 快照（2026-10-18）**之后**的全部更新，含 v1.0.0、v1.0.1 两个版本。
> 对照基线：Firefly-Canvas `main @ 15d4cfd`（2026-10-26），232 个 Issue 全部关闭。
> 证据规则：两侧结论均带 `文件:行`；未读到的实现不写。行号除注明 commit 外均为 ComfyArt HEAD（515d36e）或我方 main HEAD。
> 本文与主笔记 §59 互补：§59 映射的是快照前的差距；**§59.4 已过期——我方 Agent 工具现为 17 个**（`app/agent_extensions/canvas-tools.ts`，canvas×8 + generate×3 + media×3 + viewport×3），非 8 个。

---

## 0. 结论速览

| 判定 | 条目 |
|---|---|
| **我们已有且同等或更优（不抄）** | 任务取消入口（#80/#79）、付费任务重启恢复（ADR 0017，比其 fail-lost 更完整）、挂起保存重放队列（#197，强于其 `while(running)`）、预览磁盘缓存（缓存键设计等同）、批量逐张 partial 写回（`api_project_library.py:721`）、多 Provider 能力路由（ADR 0006）、质量门体系 |
| **缺失，值得抄（按波次）** | 见 §3 行动清单：W1 四项（丢任务终态化 / 停止语义精修 / 缩放自适应缩略图+悬停播视频 / Agent 子进程加固），W2 四项（MCP 对外接入契约 / 工具面扩到 ~30 / 负载感知调度 / 失败收割成品），W3 四项（画布视频编辑 / RunningHub 工作流参数化 / UI 卫生包 / freeSpot 落点） |
| **明确不抄（架构不符）** | ComfyUI 专属端点（`/free`、`/queue delete`、`/interrupt`）、参数按 basename 重写（违反 ADR 0006"路由不改用户参数"）、其项目/聊天数据模型、替换底部历史抽屉（ADR 0013） |

---

## 1. 增量机制级拆解（13 项，ComfyArt 侧证据）

### 1.1 `7eab251` 审计 P0 修复（含金量最高）

**(a) 丢任务终态化**：`src/comfy.ts:239-257` `waitForOutput` 每轮查 history；缺席则查 `inQueue`（`src/comfy.ts:252`）；**连续 2 轮双缺席 → throw `jobs.lost`**（`src/comfy.ts:255`）。轮询不傻睡：`nap(promptId)`（`src/comfy.ts:216-233`）Promise+1.5s 兜底，WebSocket 事件 `execution_success/error/interrupted` 立即唤醒（`src/comfy.ts:213-215`）。删卡片联动：`resumeJobs` 注册 `registerAfterDeleteHandler`（`src/jobs.ts:257-260`），删除运行中卡片即 `cancelPrompt` 真取消后端任务；`settled/active` 双 Set 防重复结算（`src/jobs.ts:27-28`）。
**(b) Agent 命令与进程加固**：`run_command(argv,*,timeout,env)`（`bot/_cli_common.py:42-54`）统一包装：`asyncio.wait_for` 超时后 `finally: proc.kill(); await proc.wait()`——消灭超时僵尸；Windows `taskkill /F /T` 的 killer 进程也 `await killer.wait()`；`run_cli_turn` finally 里 `returncode is None` 则 `kill_process_tree`（`bot/_cli_common.py:313-315`）。**MCP 命令定向下发**：`_pages: dict[client_id→project]` 注册表 + `POST /comfyart/mcp_page`（前端 WS 连上后上报）；`_target(project)` 三级仲裁（指定 project 无 tab → 报错要求打开；多 project 无参数 → 报错要求指定；单 project → 命中）（`api/mcp_commands.py:24-37`）；广播改**单 client 定向**，超时文案区分「tab 已关」与「可能仍在跑，先 get_canvas 再重试」（`api/mcp_commands.py:39-64`）。慢工具单独 `timeout=SLOW_S(90s)`（`api/mcp_tools/canvas.py` view_canvas 等）。
**(c) 聊天同步**：服务端 `remove_chat`（`api/bot.py:124-131`）弹队列+停活跃 turn+删存储+广播；删项目循环清理全部 chats（含 archived）——孤儿 turn 不再烧 token。前端 WS 重连事件 `comfyart:reconnected` → 整表刷新聊天并回原会话（`src/ws.ts`+`src/bot.ts`）。`outputsOf` 不再 JSON.parse tool_result（会炸），改正则抽 `/api/view?...` URL（`src/bot.ts:221-227`）。
**(d) 自动保存串行化**：`src/autosave.ts:23` `if (running) await running` → **`while (running) await running`**——in-flight 期间新到的保存排队，彻底串行。
**(e) 聊天 frame 复用**：`chatFrameOf(editor, chatId, anyPage=true)`（`src/chatFrame.ts:15,26`）查找扩到全 store，不再每页重复建 frame。
**(f) 主题与 Escape**：`initTheme()` 跟随宿主设置+`prefers-color-scheme` 监听+渲染前 await 消除 FOUC（`src/theme.ts:14-24`）。**Escape 栈**（`src/ui/escape.ts` 24 行）：`pushEscape(close)` 压栈，capture 阶段只调 `stack.at(-1)()`，多层弹窗 Esc 只关最顶层；全部 Modal/Popover 统一改用。

### 1.2 `b6e0006` 审计后重构

模块拆分（botCommands 459→284 + 新 botLayout 248；videoGraphs/genOptions 21/localPrefs 7/canvasBlob 3 行小模块）。共享 `Modal`(20 行)/`Chips`(8 行)。**错误 toast 解耦**：模块级 `reportError(text)`，渲染层桥接成 toast（`src/ui/ErrorToasts.tsx:11-16`）——任意非 UI 上下文（jobs/命令总线）可命令式报错；后补 `severity` + `reportSuccess`。**快捷键注册进编辑器 action 管线**而非全局 keydown（`src/overrides.ts:22-88`，含内置键位置空防冲突）。**用测试锁外部资源引用**：`tests/test_workflows.py` 硬编码每个 `workflows/*.api.json` 被 src 引用的节点 id，防工作流文件被改后静默失效。

### 1.3 `285fd93` 生成卡片停止按钮

卡片上圆形 stop 钮（`src/shapes/GeneratorShape.tsx:89-113`，`onPointerDown` stopPropagation 防拖动误触）。`stopJob`（`src/jobs.ts:186-198`）四步：幂等 `stopping` 门 → meta 写回禁用按钮 → `cancelPrompt` 返回 **boolean（是否已从队列即删）**：pending → 队列删除返 true；running → `/interrupt` 返 false 由执行器自己发 interrupted 收尾 → 若 true 用 `dropped` Map（`src/jobs.ts:29`）让 `Promise.race`（`src/jobs.ts:161-163`）**立即 reject 等待者**，不等 history。**stop ≠ error**：catch 里 `stopped` 判定（`src/jobs.ts:172`）后不弹 error toast、文案用 "interrupted"，但仍走 `keepPartial` 保留已产出部分（`src/jobs.ts:173`）。

### 1.4 `95ab202` 画布媒体性能（三件套）

**(a) 按缩放取缩略图**：自定义资产解析钩子按 `steppedScreenScale × dpr` 计算需求边长（`src/App.tsx:125-133`）；`THUMB_SIZES=[256,512,1024]` 向上取档（**只有 3 档 → 缓存命中率高**）（`src/comfy.ts:75-79`）；后端 `api/thumbs.py`：**缓存键 = sha1(src|size|mtime_ns)**、`max(im.size) <= size*1.25` 直接返回原图（免无谓重编码，`:31-33`）、WEBP q85、**`.tmp`+`os.replace` 原子写**（`:38-40`）、CPU 重活丢 `run_in_executor`（`:52`）。
**(b) 悬停播视频**：`preload="none"`（静止零流量）+ poster 用按缩放缩略图 + hover/edit 才 `play()`，`muted loop`（`src/shapes/VideoShape.tsx:17-27`）。
**(c) 探测端点返 204**：「可能不存在的文件」读取返 **204 No Content 而非 404**（`api/userdata.py:15-16`），console/网络面板不再刷错误。

### 1.5 `2f57969`+`79d19d0`+`683c4a0` 多片段视频编辑器

数据模型 `Edit{clips[{src,start,end,speed,volume}],transitions,subtitles,music}`（`src/videoEdit.ts:1-9`）；`layout()` 中 **fade 转场 = 相邻片段重叠 `min(duration,两侧长度)` 折算 offset**（`src/videoEdit.ts:31-42`）。**纯前端 BPM 检测**（`src/beats.ts` 62 行）：OfflineAudioContext 解码 → 256 样本帧 log 能量包络 → 去直流去均值 → **60–200 BPM（步长 0.1）逐一自相关，谐波 k∈{1,2,3,4} 叠加，乘高斯先验 `exp(-0.5(log2(b/120))²)`**（`:36-44`）→ 0.25 帧粒度相位搜索（`:45-50`）；`snap(t, points, within)` 吸附 beat+片段边界（`src/ui/VideoEditor.tsx:131`）。渲染：`spec()` 把 Edit 序列化成 JSON（fade 时长分摊 head/tail，字幕 DOM canvas → PNG overlay 上传，`src/videoRender.ts:5-15`），单节点 `ComfyArtTimeline` 后端逐帧合成（`timeline/__init__.py`：PyAV mp4=h264 crf20/webm=vp9+opus、转场 blend、中断响应、ProgressBar 上报）。`683c4a0` 修预览白屏：effect 依赖加 `!!edit`（`src/ui/VideoEditor.tsx:84-95`）。**顺带重做落点**：`freeSpot` 从 holder 起向右扫描、碰撞则 `x=碰撞盒.maxX+40`、上限 200 次（`src/jobs.ts:231-243`）；`rowSpots` 批量行排（`src/jobs.ts:115-119`）。

### 1.6 `5312394` LAN 多实例 + Auto 调度（教科书级 dispatch）

实例注册表 + CRUD（`api/remote.py:71-98`）；状态并发探测 = 队列长度 + **模型清单**（`api/remote.py:108-120`）。**`_pick(graph)`**（`api/remote.py:176-190`）：候选=本机+全部 enabled 实例 → 并发算 `(load, fits)`；`_fit`（`:141-161`）逐节点核对 class 存在性 + combo 输入值在远端 options 内（文件引用按 **basename 匹配重写**，匹不到=缺模型）→ `fits` 非空取 **load 最小**；无 fits 回退本机；全不可达 → 502 明确报错。输入文件随图自包含：`_upload_inputs` 把图内文件上传目标机并改写引用，**去重哈希 = sha1(path:mtime:size) 前 12 位**（`:193-213`）。提交前先建远端 WS 进度通道再 `POST /prompt`；任务 id 封装 **`srv:<instance>:<id>` 前缀**（`api/remote.py:18,243-268`），cancel/history/progress 全按前缀透明路由透传；前端卡片显示 `runsOn` 标签、`isInstalled(id, runOn)` auto 模式「本机或目标机有即可选」（`src/models.ts:92-99`）。

### 1.7 `37342d7`+`e3834df`+`3f213be`+`ac941c3` 自定义工作流协议

**@title 标记约定**（`src/customMarkers.ts`）：用户在节点 `_meta.title` 加 `@marker`（12 种：`@image @image2 @mask @last @prompt @size @width @height @count @seconds @unet @save`）。`checkWorkflow`（`:113-141`）按 feature 规格校验必填/可选标记 + **widget 形态**（image 必须 LoadImage/LoadImageMask；prompt 要有字符串 widget；size 要有 width+height 数值 widget）；输出 = `@save` 或**唯一 sink**（无入边节点恰好一个，`:79-81`）。12 个 feature 各带 required/optional 声明（`:10-23`）；「给了值但没有对应 marker」也报错（`unusedInput`）。存 `comfyart/workflows/<action>/*.json` + `workflow-config.json` 映射（自动剔除失效文件，`src/customWorkflows.ts:15-20`）；`adapt` 钩子（padForOutpaint/pasteBack/resizeTo/withAlpha）用 `rewire` 全图改连线。MCP 单工具多 action：`custom_workflow {rules|list|check|install|assign}`（`api/mcp_tools/canvas.py:257-266`），guide 要求"先读 rules"。内置 24 个 workflow 全部加 @title 当示例。聊天附件接受 API-format JSON（`src/agent.ts:31`）。`@unet` = LoRA 链挂点（首个 MODEL 输出节点）。`isInstalled` 对注册表外 id 返 true（`:92-99`）。

### 1.8 `020abfc` 外部 CLI Agent 接入契约（docs-site/guides/mcp.mdx +zh）

单一端点 `http://127.0.0.1:8188/comfyart/mcp?project=<id>`；**streamable-HTTP、纯 JSON 响应（无 SSE）**；客户端工具超时要给到**分钟级（示例 600s）**——因 `wait_generation` 可阻塞 ~3 分钟。7 类客户端配置全部文档化（Claude Code `claude mcp add --transport http` + `MCP_TOOL_TIMEOUT=600000`；Codex config.toml `tool_timeout_sec=600`；Gemini/Qwen settings.json；Cursor mcp.json；VS Code `.vscode/mcp.json`；Claude Desktop 走 `npx mcp-remote` 桥）。**裸 JSON-RPC 契约**（自带 agent 用）：`tools/list`/`tools/call` POST；结果 = 文本块+图片块，`isError:true`=失败。**典型会话协议**：`get_canvas → list_models → generate_*(立即返 ids) → wait_generation(running 就带同样 ids 再调；done 后逐项查) → view_canvas`。

### 1.9 `8e06dae` 删自造上限

删 7 个 UI 假上限（LoRA 3 / 语音 15s / 分镜 6 scene+30s / 颜色 6 / 区域 6 / 渐变 stop 6 / 设计 24），一律改为无上限+让后端报错；唯一真上限写成**模型事实**：`H3_MAX_SECONDS = 3600/24`（MiniMax Hailuo 3 硬限制，注释来源）。原则：**UI 不发明后端没有的数字**。

### 1.10 `8eaf77b`+`e042ab4`+`840cf0d` 分镜韧性

**增量编码修 OOM**：旧方案全 scene 原始帧驻留拼接后一次编码；新方案每 scene 先 `CreateVideo` 各自落盘，`Video Slice` 裁重叠首帧，`ConcatenateVideo` 只拼**已编码短片**（`src/videoGraphs.ts:228-233`）。**失败收割**：每 scene 冗余挂 `keep{i}=SaveVideo`，graph 返回 `Built.partial=['keep0',...]`（`src/workflows.ts:6`）；任务失败时 `keepPartial` 从 history 取这些已保存产物 `freeSpot` 摆回画布，错误文案追加"已保留 N 个部分结果"（`src/jobs.ts:143-156,173-176`）。修分镜忽略分辨率：`VIDEO_AREAS[resolution]` 提前传入 graph（`src/generate.ts:135-138`）。

### 1.11 `515d36e` 卸载模型/清缓存

`POST /free {unload_models:true, free_memory:true}` + 查 `/queue` 决定文案（"已清理" vs "已排队跑完生效"）（`src/comfy.ts:204-209`）。ComfyUI 专属；可抄的只有「清理类操作返回 busy 与否以决定成功文案」。

### 1.12 `10f15f7` Comfy Cloud 后端（provider 归一化模式）

任务 id 前缀 `cloud:` 路由全部后端调用；`_cloud_graph` 翻译（类替换/模型文件替换/文件引用上传/缺失 LoRA 自动从 HF 导入并轮询）（`api/cloud.py:76-88`）；**把云 job 状态/进度/输出适配成原生 history 格式**（`succeeded/failed/cancelled → status.completed+execution_error`，progress 经 send_sync 转发，产物拉回本地 `.part`+`os.replace` 原子落盘，`api/cloud.py:129-155`）——上层轮询/事件/取文件零改动。`isInstalled` cloud 恒 true。

### 1.13 `910d422`/`3a5dcc2`/`ce33d3f` 新模型注册

`IMAGE_MODELS` +FLUX.3 Klein、MOSS-Image（id/label/所需文件/lora 家族/workflow 文件五元组，`src/models.ts:37-44`）；Pixal3D 单图与 **2–4 视角** 3D——缺视角时**按数据流不动点剪枝子图**（把缺输入节点入 gone 集，反复删「输入全来自 gone」的节点至不动点，`src/assetGen.ts:85-120`）。

---

## 2. 对照矩阵（ComfyArt 新动作 × 我们现状）

| # | ComfyArt 增量 | 我们现状（证据） | 判定 |
|---|---|---|---|
| 1 | 丢任务终态化（双缺席×2→fail） | **缺**。即梦轮询 `startJimengPoll` 60s×1440=**24h 无未知任务终态**（`canvas-generation-tasks.js`）；`generation.py` 无 unknown/not_found 命中（grep 实证）；ADR 0017 只覆盖**重启**场景 | **抄**（W1） |
| 2 | 事件驱动轮询（ws 唤醒+兜底） | 固定间隔轮询（900×2s / 60s×1440）；已有 WS 广播基础设施（cancel 时 flush+broadcast） | 半抄（并入 1） |
| 3 | stop 按钮 + stop≠error + 即删即 reject | **已有取消**：`controlProjectImageTask` `smart-canvas.js:7992`、`cancelNodeGenerationTask` :8569、批量逐任务取消 :8629-8657；后端 `cancel_requested` 标志 + worker 每轮检查 `api_project_library.py:648,661`。**缺**：即删→立即 reject（我方等 worker 下轮）；stop 与 error 文案未区分 | **精修**（W1） |
| 4 | 失败收割 partial 成品 | **半有**：批量逐张 partial 写回同一 history id（`api_project_library.py:721-778`）；#79 手动「取回」（`ImageTaskRecoverSignal`）。**缺**：失败终态是否保留已产出 outputs（`partial=True` 记录被终态覆盖语义待查）、自动摆回画布 | **精修**（W2） |
| 5 | 删运行中卡片→真取消后端任务 | **哲学不同**：ADR 0016 生成中节点**禁删**+toast（`smart-canvas.js:15170`）。我们的保护更严（付费任务防误删） | **不抄** |
| 6 | 按缩放 256/512/1024 取缩略图 | **半有**：两级 LOD（预览默认 512 + 选中 320ms 升原图，`smart-canvas.js:421-424,~498`）；后端缓存键 sha1(path|mtime_ns|size|width) **已含宽度**（`api_media_preview.py:29-32`）——差距纯在前端不按缩放选档 | **抄**（W1，只改前端） |
| 7 | 视频 preload=none + 悬停播 + poster 缩略 | 无：`preload="metadata"` 首帧占位 + 点击才播（`smart-canvas.js:444-448,7289-7291`） | **抄**（W1） |
| 8 | 探测端点返 204 | 未审计我方探测类请求 | 小抄（W3） |
| 9 | 多实例负载+能力探测调度 | **半有**：能力路由强（ADR 0006 `route_image_generation` `firefly_generation.py:458`，不兼容即停并报具体参数）**缺负载感知**（同能力多 Provider 无"取最闲"）、无健康探测 | **抄算法**（W2） |
| 10 | `srv:`/`cloud:` id 前缀透明路由 | 任务 id 无前缀命名空间；provider task ID 已持久化（`api_project_library.py:963`） | 小抄（并入 9） |
| 11 | MCP 对外接入契约（streamable-HTTP/600s/wait 重发） | **缺 MCP 服务器**（无 `firefly_canvas_mcp.py`，find 实证）；Agent 走 pi RPC sidecar（ADR 0019，`agent_host.py` spawn `--mode rpc`） | **抄**（W2，战略级） |
| 12 | MCP 命令定向下发+三级仲裁 | 确认门走 WS 下发（`confirm_router` `api_agent_tools.py:510`）；**多 tab 仲裁未实现**（同一项目多页开时命令广播给谁？） | **抄**（并入 11） |
| 13 | 工具面 40 个（含 view_canvas 看图） | **17 个**（`canvas-tools.ts`），**无 view 类工具**——Agent 看不到画布 | **抄**（W2） |
| 14 | subprocess 超时必 kill+finally 补杀进程树 | `agent_host.py` 长驻 pi 子进程；**未审计**超时/僵尸/树杀语义 | **抄**（W1，小） |
| 15 | autosave `while(running)` 串行 | **已有更强**：#197 挂起 key 重放 + 服务端按 key 去重（`markAutosaveSaved` `smart-canvas.js:6561`）+ #231 `wait_for_snapshot`（`firefly_projects.py:3312`）+ `_lock` | **不抄** |
| 16 | 聊天 frame 全 store 复用 | 无页概念对应物；Agent 节点结果落点已有规则 | 低优先 |
| 17 | Escape 栈 / Modal·Chips 共享 / reportError→toast | 有 toast（ADR 0016）；Esc 处理分散未审计 | 小抄（W3） |
| 18 | 快捷键注册进编辑器 action 管线 | 全局 keydown 监听为主（smart-canvas.js） | W3 评估 |
| 19 | 测试锁 workflow JSON 节点 id | 我方无外部 JSON 资源引用锁测试类似物 | 小抄（W3） |
| 20 | 多片段视频编辑器（BPM 检测/转场布局/后端时间线渲染） | **完全缺失**；roadmap P4.4 远期；ADR 0004 只保留在线视频/抽帧 | **分期抄**（W3，最大新功能） |
| 21 | @marker 用户工作流参数化 | 有 RunningHub 工作流通道（`runRunningHubGeneration` `canvas-generation-tasks.js`）；无用户自定义工作流参数化 | 条件抄（W3，取决于 RunningHub 战略） |
| 22 | 删自造上限 | 未审计 | 小抄（W3，一次性审计） |
| 23 | 分段编码修 OOM + partial 恢复点 | 我方无分镜/长视频拼接（未命中） | 未来分镜时抄 |
| 24 | provider 状态归一化成原生 history | **已有**：多 provider 适配在 `generation.py`（3028 行）统一状态机 | **不抄** |
| 25 | freeSpot 碰撞扫描落点 | §59.3 已列为缺口，仍未实现 | **抄**（W3） |
| 26 | 新模型 FLUX.3/MOSS/Pixal3D + 视角剪枝 | 模型注册走 `data/api_providers.json` + `firefly_generation.py` 路由；3D/多视角我方无 | 供应商侧决定，非代码抄 |

---

## 3. 行动清单（最优方案，按 ROI 排序）

### Wave 1 — 生成可靠性与画布体验（P0，合计 ~2-4 天）

1. **Provider 未知任务终态化**（抄 1.1a）：轮询状态机加「N 次（建议 2）provider 明确返回未知/不存在 → 终态 fail，错误文案带 task ID」，对齐 ADR 0017 的"不重复计费+显式重试"文案哲学。落点：`app/routers/api_project_library.py` 任务 worker + `app/services/generation.py` 各 provider 查询函数。**先验证即梦 `POST /api/jimeng/query-media` 对未知 id 的实际返回**再定判定式。验收：新单测（stub provider 连续 unknown → 终态化）+ generation-lifecycle 门步骤。
2. **停止语义精修**（抄 1.3）：① 取消端点返回「是否已即时移除」，是则前端**立即**结束等待/重挂，不等 worker 下轮；② 用户主动取消的终态记录与失败区分文案（interrupted vs error），取消不弹 error toast；③ 取消时保留已产出的部分成品（见 W2-4 联动）。落点：`api_project_library.py:1212,1876`（cancel 返回值）、`smart-canvas.js:7992-8725`。
3. **缩放自适应缩略图 + 悬停播视频**（抄 1.4a/b）：前端按 `节点长边 × zoom × dpr` 向上取 [256,512,1024] 档请求 `api_media_preview`（端点与缓存**已支持 w 参数**，零后端改动）；视频节点改 `preload="none"` + poster=缩略图 + hover 才 play（muted loop）。落点：`smart-canvas.js:421-448`、`canvas-media-history.js:74,127`。验收：perf500 长浏览器测试 + 新增悬停浏览器证据。
4. **Agent 子进程加固**（抄 1.1b）：`app/services/agent_host.py` 补 `run_command` 语义（`wait_for` 超时 → finally kill+wait；退出路径 `returncode is None` → 树杀；Windows taskkill killer 进程 await）。与我们 #230 进程树归属门呼应。验收：新增单测（假 CLI 挂死 → 必杀无残留）。

### Wave 2 — Agent 平台化（P1，合计 ~1-2 周）

5. **MCP 对外接入端点**（抄 1.8+1.1b 仲裁，战略级）：新建 streamable-HTTP MCP（纯 JSON、无 SSE），复用 `api_agent_tools.py` 端点与 `canvas-tools.ts` 工具语义；URL 带 `?project=`，**client_id 注册表 + 三级仲裁 + 命令定向下发单 tab**；超时契约（wait 工具可阻塞 ~3 分钟，客户端超时 600s）写进 docs；给出 Claude Code/Codex/Cursor/VS Code 等配置示例。对齐 ADR 0019 深度融合要求与 TODO P2 3.6。
6. **工具面 17 → ~30**（抄 1.1a 慢工具超时 + §59.4）：加 `canvas.view`（整页/选区导出图片喂模型，慢操作独立 90s 超时）、`canvas.view_media`（视频抽帧拼图，复用 `generate_video_preview_image`）、`canvas.edit_*` 包装（擦除/外扩/抠图等 `canvas-image-editor.js` 已有能力未成工具）、`canvas.batch_edit`（eachRuns：选中 N 个+一句 prompt=N 次生成，结果放各自旁边，§59.3）。工具参数 `additionalProperties:false` + 公共片段 + 错误文案带下一步。
7. **负载感知 Provider 调度**（抄 1.6 算法，ADR 0006 红线内）：同能力候选 Provider 并发探测（队列/近失败率），能力过滤后取最闲；任务 id 加 provider 前缀命名空间透明路由；不可用 Provider 明确报"缺什么"。**禁止抄 basename 参数重写**（违反"路由不改用户参数"）。落点：`firefly_generation.py:458-666`。
8. **失败收割成品自动化**（抄 1.10 partial 语义）：批量/级联任一子任务失败时，已产出 outputs 保留在终态记录并（可选）自动落画布空位（联动 W3 freeSpot）；确认现有 `partial=True` history 覆盖语义（`api_project_library.py:721`）是否丢失成品。付费生成场景 ROI 高。

### Wave 3 — 画布编辑与卫生（P2，按 roadmap 排期）

9. **画布视频编辑（P4.4 提级候选）**：三期——M1 数据模型+时间轴 UI（抄 `videoEdit.ts` Clip/Transition/fade 重叠 offset 布局）；M2 纯前端 **BPM 自相关检测**（62 行算法整套可移植：谐波叠加+高斯先验+相位搜索）+ beat 吸附 + 音乐轨；M3 后端渲染用 **ffmpeg 时间线**（我方已有抽帧 ffmpeg 链路；不抄 PyAV 节点绑定）。工程量最大，建议独立 wayfinder。
10. **RunningHub 用户工作流参数化**（条件性，抄 1.7 标记协议）：`@marker` 标题约定 + checkWorkflow 槽位/widget 形态校验 + 唯一 sink 推断输出；聊天/附件接受工作流 JSON。仅当 RunningHub 是战略通道时立项。
11. **UI 卫生包**：Escape 栈（24 行，`static/js/`）；「可能不存在的文件」探测端点返 204；一次性审计删自造上限（真上限写成注释事实，抄 `H3_MAX_SECONDS=3600/24` 写法）；给外部 JSON 资源引用加锁测试（抄 `tests/test_workflows.py` 思路）。
12. **freeSpot 落点扫描**（§59.3 遗留）：右移扫描+碰撞跳过+200 次上限（`src/jobs.ts:231-243` 算法直译），替换现有随机/固定偏移落点。

### 明确不做

- ComfyUI 专属：`/free`、`/queue delete`、`/interrupt`、`/object_info`（ADR 0004 已移除本地 ComfyUI）；
- 参数 basename 重写（违反 ADR 0006）；
- 替换底部历史抽屉（ADR 0013）与项目自持模型（ADR 0003）；
- 生成中节点禁删策略改「删即取消」（ADR 0016 保护更优）。
- 负载感知 Provider 调度 / 自动 failover（拷问决议：单供货商、无多实例池；本地模型为独立可选项、无自动降级；仅保留报错文案改进）。
- RunningHub `@marker` 工作流参数化（W3-10，拷问决议：不学工作流 JSON 底层，以 API 调用为主，不引入本地推理运行时）。
- 失败成品自动摆回画布（W2-8 的自动落点部分，手动取回修复 #234 取代；freeSpot 独立留 W3 #246）。
- 【全链路复核新增】Slideshow、自由文本节点+FontPicker（无落点）、模型安装检测（无本地模型）、多用户头路由（单机定位）、ComfyUI 节点打包（非插件）、TS 构建门禁（前端规模不足）、workflow 引用锁测试（无该概念）、ProgressBar 宿主中断（ComfyUI 产物）、聊天 bot 平台接入（产品形态不同）。

---

## 4. 风险与验证缺口

1. ~~即梦 `query-media` 对未知任务的实际返回语义未验证~~ → **作废**：拷问决议即梦整体剔除（#236），终态化判定式只需覆盖通用 provider 与 RunningHub。
2. `partial=True` history 记录被终态覆盖的确切语义未读全（`api_project_library.py:721-778` 只读了 grep 层）→ W2-8 立项前精读该段。
3. 我方多 tab 场景现状未审计（dual_tab_sync_client.js 已存在）→ W2-5 仲裁设计前先跑该契约测试确认边界。
4. `agent_host.py` 现有进程管理语义未精读 → W1-4 立项时先读全文件。
5. ComfyArt 行号基于 515d36e；其仓库在持续演化（本次 fetch 即 27 commits），引用时以本文为准。

---

## 5. 拷问决议与拆票映射（spec #233，票 #234–#249）

- **W1 全收**：#234 手动取回修复（审计证实对 Firefly/RunningHub 图片任务三层断裂，实质未实现——升级为 P0）→ #235 终态化（含 RunningHub 未知 code、resume not-found 显式分支）→ #236 即梦整体剔除（含存量 `jimengPending` 一次性终态化）；并行 #237 停止语义、#238 三档缩略图+悬停播放、#239 agent_host 超时必杀、#240 agent-tools 同源守卫、#241 TUI confirm HTTP fallback 假路由 bug。
- **W2**：#242 新 ADR（画布 MCP 端点：外部会话具现为 Agent 节点、loopback+Host 校验、确认门阻塞、`?project=` 三级仲裁、600s 契约）→ #243 MCP 实现（Blocked by #240/#242；顺带清理假工具 `get_selection`/viewport、补 `_apply_mutations` 广播、修 wait timeout 参数）；并行 #244 工具面 17→~30、#245 ADR 0006 解释行。
- **W3**：#246 freeSpot、#249 UI 卫生包。
- **Wayfinder**：#247 画布视频编辑（原 W3-9 移出）、#248 本地模型 provider（1cat vllm 方向，只接 API 与规则）。
- 定性记录：学 ComfyArt 的**画布交互形式**，不学工作流 JSON 底层架构；以 API 调用为主，不引入本地推理运行时。

---

## 6. 全链路复核（器官表反扫，#250–#262）

第一轮拆票以我方痛点为轴；第二轮以 ComfyArt 模块表反扫（前端/UI、交互/工具、后端/架构三路审计），补 13 票：

- **#250 ADR 0021 运行数据 sidecar**（Q17-A 已签）：每项目 `ops.db`（sqlite3 标准库零依赖，WAL+foreign_keys，`Migration` done-row 幂等标记）；租户仅四个：tasks（#234 第一个入住）/undo（#251）/版本树（#257）/usage（#258）。**ADR 0010 不动**：画布文档保持 JSON；节点/连线永不进库，媒体永不进 BLOB。关键洞察：ComfyArt 的画布文档本身也是 JSON 大对象存进 SQLite 信封（`api/project_store.py:80-90`）。
- 缺陷：#251 undo 栈刷新即失忆（`undoStack` 不在 `buildSavePayload:727-738`）；#252 IME 裸回车（`smart-canvas.js:15091`）+ Markdown 围栏 bug。
- 交互/工具：#253 套件（右键菜单/吸附 tidy/快捷键/Tab）；#254 逐图生成（两份审计独立点名的最大空白）；#255 分层 MIT 移植（layers.ts 276L + vectorPath.ts 431L + 分数坐标骨架）；#256 OCR（elements.ts MIT）；#260 小工具包；#262 全局任务中心（成本/耗时/reveal）。
- 数据/协作：#257 版本树（KEEP_VERSIONS+回滚前存）；#258 用量遥测（normalize_usage 直接移植）；#259 批注闭环（主笔记 §59.3 亲标「最值得抄」而第一轮漏票——本轮复核的直接成果）。
- 工程：#261 docs 死链/失效 file:line 门禁进 `.pipeline`。
- 既有票追加验收（见评论区）：#234 ops.db 首个租户；#239 重启孤儿终态化+错误五元组+stderr 尾截断；#243 MCP 多协议版本/batch-400/脏标志节流；#244 工具错误=操作指引+style/seed；#247 BPM snap/转场/字幕/contact-sheet；#248 probe()/能力位三态。
- 反向登记（我方更强）：job 准入配额（MAX_PENDING=32/PER_PROJECT=8 vs 其单活跃 FIFO）、DPAPI 凭据、165+41 测试体系、批量详情弹窗逐任务取消、分组拖入+自动整理、触屏 127 行、设置严格校验同构。
