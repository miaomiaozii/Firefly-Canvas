# ComfyArt 画布技术拆解 · 学习笔记

> 来源仓库：https://github.com/jtydhr88/ComfyArt （MIT，本地克隆于 `/tmp/ref/ComfyArt`，2026-10-19）
> 目的：为 Firefly-Canvas 画布大升级提供**可抄的功能与实现事实清单**。
> 证据规则：所有结论均标注 `文件:行` 或 README/docs 原文。未读到的实现不写。
> 阅读提示：章节编号有跳号（**9、11、16、18、21、25、40、56 为合并后废弃号**）；文内 `§NN` 引用指向编号而非页序，找不到时按标题文字搜索。

---

## 目录

- **0.** [仓库底细（事实）](#sec0)
- **1.** [画布总体架构](#sec1)
- **2.** [设计画布能力（tldraw 上的自研部分）](#sec2)
- **3.** [AI 图像工具链（可直接对照我们现有能力）](#sec3)
- **4.** [生成器（画布上的生成节点）](#sec4)
- **5.** [导出](#sec5)
- **6.** [Agent / MCP（画布被 Agent 操作的方式）](#sec6)
- **7.** [素材与品牌（`src/library.ts` / `src/assets.ts` / `src/ui/KitEditor.tsx` 272 行）](#sec7)
- **8.** [其它工程细节](#sec8)
- **10.** [Agent ↔ 画布命令总线（`src/botCommands.ts` 459 行）★★★ 可直接对标我们 `canvas-agent-tui.js`](#sec10)
- **12.** [矢量 / 钢笔 / 节点编辑 / 布尔（完整实现）](#sec12)
- **13.** [评论系统完整实现（`src/shapes/CommentShape.tsx` 193 行）](#sec13)
- **14.** [模式面板（`src/ui/ModePanel.tsx` 279 行）★ 一个组件承载 8 种图像工具](#sec14)
- **15.** [裁剪（`src/ui/CropBar.tsx` 149 行）★ 分数坐标裁剪数学](#sec15)
- **17.** [浏览器内视频编辑器（`src/videoEdit.ts` 246 行 + `src/ui/VideoEditor.tsx` 306 行）★★★ 纯前端、零后端、可直接搬](#sec17)
- **19.** [Mockup：WebGL2 着色器实时贴图（`src/mockupGl.ts` 139 行）★★ 完整可搬的 GLSL](#sec19)
- **20.** [Compose 专用 shape（`src/shapes/ComposeShapes.tsx` 147 行）](#sec20)
- **22.** [嵌入类 shape（`src/shapes/EmbedShapes.tsx` 226 行）★ Lottie / 3D / HTML / 音频](#sec22)
- **23.** [统一输入框 Composer（`src/ui/Composer.tsx` 334 行）★★ 一个输入框兼顾 Agent/直生图/视频](#sec23)
- **24.** [局部重绘 / 移动对象面板（`src/ui/InpaintPanel.tsx` 286 行）★★★ 完整的 mask 编辑器](#sec24)
- **26.** [生成面板 + 参考图模式系统（GenPanel / refModes / GenSettings / GenAdvanced / models）★★★](#sec26)
- **27.** [调色面板与手绘辅助（AdjustPanel / SmudgePanel / AnglePreview）](#sec27)
- **28.** [工作流工厂（`src/workflows.ts` 407 行）★★ 每个 AI 能力 = 一个 graph 工厂函数](#sec28)
- **29.** [时间线剪辑引擎（`src/videoEdit.ts` 265 行）★★★ 无依赖纯前端剪辑](#sec29)
- **31.** [视频剪辑 UI（`src/ui/VideoEditor.tsx` 306 行）★★ 独立撤销栈 + 拖拽裁剪](#sec31)
- **32.** [基础 UI 设施（selection.ts / keys.ts / Popover.tsx）★★ 可直接搬](#sec32)
- **33.** [小组件实现（ColorPicker / VoiceButton / AssetPicker / StoryboardEditor / RefControls）](#sec33)
- **34.** [模型/Agent 选择与工具栏（ModelPrefs / AgentPicker / RefFrameButton / SelectionLabel / chatDock / Shortcuts / VideoToolbar）](#sec34)
- **35.** [输入槽位 / 调色盘 / 资产列表 / id（genInputs.ts / palette.ts / assets.ts / id.ts）](#sec35)
- **36.** [快捷键覆盖 / 矢量 / 转 PPT / 嵌入入口 / 富文本 ★★](#sec36)
- **37.** [动作层（`src/actions.ts` 313 行）★★★ 所有 AI 图像动作的统一封装 + mask 数学](#sec37)
- **38.** [图层系统（`src/layers.ts` 206 行）★★ 用 tldraw 原生能力搭图层](#sec38)
- **39.** [元素与选区工具（`src/elements.ts` 187 行）](#sec39)
- **41.** [后端/ComfyUI 接入层（`src/comfy.ts` 151 行）★★ 所有网络原语](#sec41)
- **42.** [LLM 调用与容错解析（`src/llm.ts` 112 行）★★★ 接 LLM 必备工具](#sec42)
- **43.** [技能系统（`src/skills.ts` 119 行）★★★ 兼容 Agent Skills 格式](#sec43)
- **44.** [品牌库 / 通知 / 定时任务（library.ts / notices.ts / schedules.ts）★★](#sec44)
- **45.** [记忆系统（`src/memory.ts` 91 行）★★★ 可写回的结构化长期记忆](#sec45)
- **46.** [音频/3D 生成（`src/assetGen.ts` 105 行）★★ 四类音频 + 图生 3D](#sec46)
- **47.** [主动智能（`src/proactive.ts` 125 行）★★★ Agent 主动学习 + 主动审查](#sec47)
- **48.** [生成块 shape 与版本迁移（`src/shapes/GeneratorShape.tsx` 96 行）★★★ tldraw 数据迁移正确姿势](#sec48)
- **49.** [WebSocket 事件总线与进度（`src/ws.ts` 33 行 + `src/progress.ts` 20 行）★★ 极简但完备](#sec49)
- **50.** [画板（`src/shapes/ArtboardShape.tsx` 94 行）★★ 继承 tldraw Frame 加样式](#sec50)
- **51.** [作业管线（`src/jobs.ts` 221 行）★★★ 所有 AI 产出的唯一入口](#sec51)
- **52.** [自动保存与版本快照（`src/autosave.ts` 54 行 + `src/history.ts` 30 行）★★★](#sec52)
- **53.** [应用装配（`src/App.tsx` 308 行）★★★ tldraw 完整接管 UI 的开关清单](#sec53)
- **54.** [Agent 接入层（`src/bot.ts` 321 行）★★★ 把外部 CLI Agent 接进画布](#sec54)
- **55.** [Agent 入口层（`src/agent.ts` 186 行）★★ 一个输入框背后的调度](#sec55)
- **57.** [仓库底细：它仍的是什么（后端事实）](#sec57)
- **58.** [核实命令（自己跑一遍，不要信未核实的描述）](#sec58)
- **59.** [对 Firefly-Canvas 的映射（★ 抄什么 / 怎么抄）](#sec59)
- **60.** [数据库层（`api/db.py` 122 行）★★★ 已核实：SQLAlchemy + SQLite，5 张表](#sec60)
- **61.** [MCP 服务端（`api/mcp_commands.py` 42 + `api/mcp_tools/*` 502 行）★★★ 已核实：后端→浏览器→后端的 RPC 回路](#sec61)
- **62.** [CLI Agent 驱动层（`bot/_cli_common.py` 419 行）★★★ 生产级子进程驱动，与我们 ADR 0019 直接对标](#sec62)
- **63.** [Provider 抽象层（`bot/providers.py` 99 行）★★★ 接多个 CLI Agent 的最小接口](#sec63)
- **64.** [轮次编排（`api/bot_turns.py` 277 行）★★★ 流式落库 + 防幻觉汇报 + 每会话队列](#sec64)
- **65.** [文字 shape（`src/shapes/TextShape.tsx` 180 行）★★★ 自测尺寸 + 纯文本/富文本双轨](#sec65)
- **66.** [矢量形状 shape（`src/shapes/VectorShape.tsx` 232 行）★★★ SVG 渲染 + inside/outside 描边 + 拖拽建形](#sec66)
- **67.** [导出对话框 + 手写 PDF（`src/ui/ExportDialog.tsx` 109 行）★★★ 零依赖 PDF 生成](#sec67)
- **68.** [i18n 与主题（`src/i18n.ts` 38 行 + `src/theme.ts` 34 行）★★ 极简但完整](#sec68)
- **69.** [拖拽导入 / 画布拾取 / 确认 / 缩放 / 特效（5 个小文件）★★★ 小而极可搬](#sec69)
- **70.** [覆盖层编辑工具（`src/ui/EditLayers.tsx` 205 行）★★★ 分数坐标 + 四种模式共用一套骨架](#sec70)
- **71.** [文本与矢量 shape（`src/shapes/TextShape.tsx` 180 行 / `src/shapes/VectorShape.tsx` 232 行）★★★ 自定义 shape 的完整范式](#sec71)
- **72.** [导出对话框（`src/ui/ExportDialog.tsx` 109 行）★★★ 手写 PDF 生成器（零依赖）](#sec72)
- **73.** [画布历史对话框（`src/ui/HistoryDialog.tsx` 67 行）★★ 恢复前先存当前版本](#sec73)
- **74.** [顶栏（`src/ui/TopBar.tsx` 89 行）★★ 页 tab 内联改名 + 评论 badge](#sec74)
- **75.** [侧栏容器（`src/ui/SidePanel.tsx` 77 行）★★ 一个 atom 管五个面板 + 左右分栏](#sec75)
- **76.** [画布工具条（`src/ui/CanvasTools.tsx` 73 行）★★★ 迷失导航 + 背景色存 meta](#sec76)
- **77.** [底部工具坞（`src/ui/Dock.tsx` 184 行）★★★ 全局单键快捷键 + 智能评论 + 全格式上传](#sec77)
- **78.** [i18n 与主题（`src/i18n.ts` 38 行 / `src/theme.ts` 34 行）★★ 零依赖实现](#sec78)
- **79.** [钢笔工具（`src/shapes/PenTool.ts` 76 行）★★★ 完整 StateNode 实现](#sec79)
- **80.** [画板工具条（`src/ui/FrameToolbar.tsx` 257 行）★★★ 全仓库最值钱的一块：比例锁 + 自动布局 + 批量导出](#sec80)
- **81.** [⚠️ 作废：CLI 驱动实现（`bot/codex.py` + `bot/claude_code.py`）——**已逐行重读并在 §83 重写**](#sec81)
- **82.** [`bot/qwen_code.py`（251 行）★ 已核实：禁用自带工具 + Windows npm 解析链](#sec82)
- **83.** [`bot/local_llm.py`（425 行）★★★ 已核实：一个完整的自研 Agent 循环（对我们最有价值）](#sec83)
- **84.** [`bot/_acp.py`（225 行）★★★ 已核实：自研 ACP（JSON-RPC over stdio）客户端](#sec84)
- **85.** [`bot/deepseek_harness.py`（318 行）★★★ 已核实：ACP provider 的完整生命周期（最完整的一份）](#sec85)
- **86.** [`bot/deepseek_harness_runtime.py`（97 行）★★ 已核实：给第三方 Agent 造一个“只准用画布工具”的 profile](#sec86)
- **87.** [★★★ Agent 反问用户 + 运行审批（`api/bot_asks.py` 129 行 + `api/mcp_tools/bot_tools.py` 191 行）](#sec87)
- **88.** [⚠️⚠️ 重大更正：§81（Claude Code / Codex 驱动）**大部分内容是错的**，以本节为准](#sec88)
- **89.** [⚠️ 更正：§38 / §39 作废——`src/layers.ts` 与 `src/elements.ts` 的真实内容（逐行重读）](#sec89)
- **90.** [★ 术语对照表：文档里的**简写/意译名** → 仓库里的**真实写法**](#sec90)
- **91.** [★★★ 文档可信度声明与核查记录（务必先读这一节）](#sec91)
- **92.** [剩余 UI 组件补全（§91.4 覆盖清单之外的 35 个文件）★★ 已逐行读完](#sec92)

---

<a id="sec0"></a>
## 0. 仓库底细（事实）

| 项 | 事实 |
|---|---|
| 定位 | 跑在自有 ComfyUI 上的免费开源 AI 设计画布：无限画布 + 工具型设计 Agent + 本地图/视频/音频/3D。无账号、无额度、无云。 |
| 前端栈 | **tldraw `^5.5.2`** + **React `^19.3.1`** + Vite `^8.3.4` + TypeScript `^7.0.2`（`package.json`） |
| 富文本 | **tiptap `^3.32.0`**（core / react / starter-kit / extension-text-style） |
| 矢量布尔 | **paper `^0.12.18`**（`paper/dist/paper-core`，动态 import） |
| 其它依赖 | `ag-psd ^31.0.3`（PSD 读写）、`fflate ^0.8.3`（ZIP）、`imagetracerjs ^1.2.6`（位图转 SVG）、`lottie-web ^5.13.0`、`pptxgenjs ^4.0.1`、`qrcode-generator ^2.0.4`、`three ^0.186.1`、`@tldraw/assets ^5.5.2` |
| 后端 | Python，作为 ComfyUI custom node 安装（`__init__.py` + `nodes.py` + `api/` + `bot/`）；**SQLAlchemy + SQLite** 存项目/快照/聊天，路径 `folder_paths.get_system_user_directory("comfyart")/data.db`（`api/db.py:102`）；WAL + `foreign_keys=ON`（§60） |
| 规模 | `src/` 共 **17,080 行** TS/TSX（不含 dist、不含后端 Python、不含 CSS）；`src/` 下 **150 个** `.ts`/`.tsx` 文件；后端 `api/`+`bot/` 共 **4,573 行** Python（§57.3） |
| 最大文件 | `src/botCommands.ts` 459 行、`src/workflows.ts` 407 行、`src/ui/AnglePreview.tsx` 364、`src/ui/Composer.tsx` 334、`src/bot.ts` 321、`src/actions.ts` 313、`src/App.tsx` 308 |
| 文档站 | `docs-site/guides/*.mdx`（13 篇，838 行）+ `docs-site/zh/guides/` 中文版；线上 comfyart.org |
| 上游 | Agent 后端移植自同作者的 [ComfyTV](https://github.com/jtydhr88/ComfyTV) bot（MIT） |

### ⚠️ 许可证红线（抄之前必读）

README 原文：

> "The canvas is the [tldraw SDK](https://tldraw.dev), which is **not** [MIT]: it is bundled in `dist/` under its own license, copied in `licenses/tldraw-LICENSE.md`. Served from `localhost` or plain `http` it runs in development mode without a license key and shows a small 'Get a license for production' badge."

`licenses/tldraw-LICENSE.md` 关键条款（原文摘录）：

- "Not to use the Software in **Production Environments**."
- "Not to disable, change, or interfere with the Software's **License Key** enforcement."
- "Not to remove any copyright or other notices from the Software."
- 允许：Development Environment 内使用、修改、打包分发（bundling）、提交修改。
- "Production Environment" 定义包含任何面向终端用户/客户/公众的服务端或 Web 部署，**内部开发除外**。
- ★ **额外注意（第 50 行原文）**："The Software includes technical measures to verify License Key validity, detect deployment environments, enforce usage restrictions based on license type, and ensure proper watermark display. **The Software may collect and transmit usage data to tldraw for license compliance purposes.**" → **tldraw 自带环境检测 + 水印 + 可能上报使用数据**（引入前需评估隐私合规）。

**结论**：ComfyArt 自己的 `src/` 是 MIT（可参考/可改写），但它**依赖的 tldraw 不能用于生产环境**。
所以对我们的画布而言：
1. **不能**直接引入 tldraw 做生产画布（除非购买 tldraw 商业授权）。
2. **可以**抄的是：交互模型、功能清单、数据模型、算法与工程套路（这些不是 tldraw 版权范围）。
3. ComfyArt `src/` 里的 MIT 代码（如 `layers.ts` 的掩膜算法、`elements.ts` 的 OCR 重建、`vectorPath.ts` 的路径归一化）**可以按 MIT 条件搬运**，保留版权声明即可。

---

<a id="sec1"></a>
## 1. 画布总体架构

### 1.1 入口与装配（`src/App.tsx`，308 行）

- 自定义 shape 工具 13 个：
  `[ArtboardShapeUtil, GeneratorShapeUtil, CommentShapeUtil, DoodleShapeUtil, TextShapeUtil, VectorShapeUtil, LottieShapeUtil, ModelShapeUtil, HtmlShapeUtil, AudioShapeUtil, MockupShapeUtil, GenBlockShapeUtil, QrShapeUtil]`（`App.tsx:96`）
- 自定义工具 5 个：`[CommentTool, DoodleTool, TextTool, VectorTool, PenTool]`（`App.tsx:97`）
- **把 tldraw 自带 UI 几乎全部关掉**，只保留 ContextMenu 换成自己的（`App.tsx:99-121`）：
  `Toolbar/StylePanel/MainMenu/PageMenu/NavigationPanel/HelpMenu/ActionsMenu/QuickActions/ImageToolbar/VideoToolbar/MenuPanel/TopPanel/SharePanel/HelperButtons/DebugPanel/DebugMenu/Minimap` 全 = `null`，`ContextMenu: CanvasMenu`。
  → 套路：**用 tldraw 当引擎，UI 全部自绘**。这是它"画布手感好但界面不像 tldraw"的原因。
- 隐藏形状用 `getShapeVisibility`：`(shape) => shape.meta.hidden ? 'hidden' : 'inherit'`（`App.tsx:98`）——**隐藏状态存在 shape.meta**，不是 props。
- 资源存储接管（`App.tsx:123-131`）：
  ```ts
  const assets: TLAssetStore = {
    async upload(_asset, file) { const ref = await uploadImage(file, ...); return { src: viewUrl(ref) } },
    resolve(asset, ctx) { /* SVG 按当前缩放栅格化 */ return svgRaster(src, w, h, ctx.steppedScreenScale * ctx.dpr) },
  }
  ```
  → 两个可抄点：**图片直接落到后端素材库**（不落 IndexedDB）；**SVG 按 zoom 分辨率栅格化**（画布快、导出保矢量）。
- 多标签页冲突保护（`App.tsx:104`、`App.tsx:206-212`）：
  `BroadcastChannel('comfyart-projects')`，每 tab 一个 `TAB = uuid()`；收到别的 tab 广播同 project → `staleRef.current = true` + 显示"你的文件版本已过期"横幅并**停止保存**（`canSave()` 返回 false）。
- 启动顺序（`App.tsx:160-205`）：`loadProject(id)` + `loadLibrary()` → `loadSnapshot(editor.store, snap)` → 迁移/本地化 page 名 → `zoomToBounds` → **`editor.clearHistory()`**（避免把加载动作记进撤销栈）→ `resumeJobs()` → 启动 scheduler / proactive / chatFrames / commandBus / autosave。
- 品牌 kit 通过 URL 参数 `?kit=` 传入，消费后 `history.replaceState` 清掉（`App.tsx:170-178`）。
- 新建项目用 `?new=1` 参数，落盘后清参数（`App.tsx:196-201`）。

### 1.2 快捷键与动作覆盖（`src/overrides.ts`）

```ts
const KBD = {
  'toggle-lock': 'cmd+shift+l,ctrl+shift+l',
  'bring-forward': 'cmd+],ctrl+]',
  'send-backward': 'cmd+[,ctrl+[',
  'flatten-to-image': 'cmd+e,ctrl+e',
  'zoom-to-100': 'cmd+0,ctrl+0',
}
const UNBOUND_TOOLS = ['eraser', 'draw', 'laser', 'highlight']   // 解绑，避免和自绘工具冲突
```
自定义动作：`ca-tidy`（`shift+a` 多选整理/对齐分布）、`ca-chat`（`shift+enter` 画布上就地对话）、`ca-hide`（`cmd/ctrl+shift+h` 切换 `meta.hidden`）。
→ 可抄：**用 overrides 给 tldraw 内置 action 重绑快捷键 + 注入自己的 action**，不改 tldraw 源码。

### 1.3 与后端的通信（`src/comfy.ts` 151 行 / `src/ws.ts` 33 行）

- 所有请求走 `api(path)` = `fetch('/api' + path)`，并自动带 `Comfy-User` 头（读 `localStorage['Comfy.userId']`）。
- **任务结果用轮询，不用 WebSocket**：
  `queuePrompt(prompt)` → `POST /api/prompt`（带 `client_id`）→ 返回 `prompt_id`；
  `waitForOutput()` 每 **`POLL_MS = 1500`** 拉一次 `GET /api/history/{prompt_id}`：
  - 错误识别：`(hist.status?.messages ?? []).find(m => m[0] === 'execution_error')` → 抛 `err[1]?.exception_message`；
  - 取产物：`hist.outputs[node]` 里按节点 id 取；
  - 完成但无产物：`hist.status?.completed` → 抛 `'the workflow produced no output'`。
  `waitForImages` 合并 `out.images / videos / gifs / audio / '3d'` 五类输出。
- **WebSocket 只用于进度和事件**（`ws.ts`）：`/ws?clientId=<uuid>`，断线 `RETRY_MS = 2000` 重连；`onComfyEvent(type, handler)` 返回取消函数。
  实际订阅者：`progress.ts`（`progress` / `execution_success` / `execution_error` / `execution_interrupted`）、`llm.ts`（`executing` / `progress`）、`bot.ts`、`botCommands.ts`（`comfyart-mcp-command`）、`chatFrame.ts`、`proactive.ts`（`comfyart-bot`）。
- 进度原子：`progressAtom = atom<Record<string, {value,max}>>('job progress', {})`，按 `prompt_id` 存，执行结束事件里删（`progress.ts:15-21`）。
- 模型路径注入：`loadModelPaths()` 拉 `/comfyart/model-paths`，`withModelPaths(prompt)` 在提交前把 graph 里**字符串输入值**按名字替换成真实路径（`comfy.ts:105-115`）→ 工作流 JSON 里只写模型名，路径由环境决定。
- 上传：`POST /api/upload/image`，`subfolder: 'comfyart'`。
- 素材引用统一结构 `ImageRef = { filename, subfolder, type, metadata?{width,height} }`；`viewUrl()` = `/api/view?...`；`annotated()` 序列化成 `"subfolder/filename [type]"` 字符串（`layers.ts:parseAnnotated` 反向解析）。
- 缩略图走服务端：`thumbUrl(src, max=256)` = `/comfyart/thumb?url=&max=`。
- 用户数据读写走 ComfyUI 的 userdata API：`/api/userdata/...`（`readUserJson` / `writeUserJson`（`?overwrite=true&full_info=false`）/ `listUserFiles` / `deleteUserFile`）。

### 1.4 任务系统：holder + placer（`src/jobs.ts` 221 行）★ 最值得抄的机制之一

核心数据结构：
```ts
export interface Job { id: string; node: string; name: string; kind: MediaKind; placer?: string; extra?: Record<string, unknown> }
export type MediaKind = 'image' | 'video' | 'audio' | 'model'
export interface Media { ref: ImageRef; src: string; w: number; h: number; kind: MediaKind }
export type Placer = (editor, holder: TLShape, media: Media[], job: Job, onError: ErrorSink) => Promise<void>
```
流程：
1. `spawnHolder(editor, beside, size?)`：在原 shape 右侧 `GAP = 40` 处放一个**临时 generator shape**（`meta.temp = true`）占位；用**碰撞扫描最多 200 次**找空位：
   ```ts
   for (let i = 0; i < 200; i++) {
     const hit = boxes.find(o => x < o.maxX + GAP && x + w + GAP > o.minX && y < o.maxY + GAP && y + h + GAP > o.minY)
     if (!hit) break
     x = hit.maxX + GAP
   }
   ```
2. `startJob(editor, holderId, buildPromise, opts, onError)`：等 graph 构建 → `queuePrompt` 得 `prompt_id` → **把 Job 写进 `shape.meta.job`**（持久化到画布快照！）。
3. `finish()`：`waitForImages(job.id, job.node)` → `toMedia()` → 调注册的 placer 或 `defaultPlacer`；`active: Set<string>` 防重复消费。
4. **刷新页面后自动续接**：`resumeJobs(editor)` 遍历 `editor.store.query.records('shape')`，凡 `meta.job` 非空就重新 `finish()`（`jobs.ts:217-221`）。→ 任务状态存在画布文档里，**不需要额外的任务表**。
5. 失败处理：`meta.temp` 的 holder 直接删；否则清 `meta.job` 保留 shape。
6. `resultsAtom = atom<Record<string, {srcs: string[]; error?: string}>>('job results', {})` 按 holderId 记录结果，供后续 UI（如评论任务完成通知）读取。
7. **placer 注册表**（可插拔落位策略）：已注册 `'replace'`（原位换图，`layers.ts:88`）、`'layers'`（分层成 frame，`layers.ts:101`）、`'regions'`（`layers.ts:200`）、`'textLayers'`（Edit Elements，`elements.ts:170`）。默认 placer：删 holder，按 holder 宽度横向排开，间距 `GAP=40`，并把 `meta.gen`（生成信息）写进结果 shape。
8. `quiet` 标志：`holder.meta.quiet` 为真时结果不自动选中（`selectResults`）。
9. `meta.gen: GenInfo` 记录 `tool/prompt/ratio/resolution/duration/workflow/refs/seed/settings/inputs`，`GEN_INPUTS = ['refs','base','strength','colors','refModes','refStrengths','refControls','lastFrame','scenes','videoMode','motion','speech','negative','seed','loras']` 从 `holder.meta` 白名单捞取 → 支撑"Generation Info / Copy Prompt / Remix / Regenerate"。

### 1.5 自动保存与快照历史

- `src/autosave.ts`：`SAVE_DELAY = 800` ms 防抖；`editor.store.listen(cb, { scope: 'document' })`；状态机 `saveStateAtom: 'saved' | 'pending' | 'saving' | 'error'`（UI 显示 Saving…/Saved/Save failed · Retry）；`beforeunload` 在非 saved 时 `e.preventDefault()`；模块级 `flushCurrent` 暴露 `saveNow()` 给外部强制落盘。
- 保存内容：`saveProject(id, getSnapshot(editor.store), name, coversOf(records))`。
- `coversOf(records)`（`src/projects.ts`）：从 store 记录里挑**最近被 image shape 引用的 2 个图片 src** 作项目封面（`COVERS = 2`），存进项目条目 → 项目列表不用重新渲染画布。
- `src/history.ts`：快照版本，`INTERVAL = 5 * 60 * 1000`（5 分钟最多存一版），`POST /comfyart/projects/{id}/versions`，`GET .../versions`、`GET .../versions/{time}`；`Version = { time, count }`（count = shape 数）。
- 项目 CRUD（`src/projects.ts`）：`PUT /comfyart/projects/{id}`（snapshot+name+covers）、`PATCH`（name/pinned/covers）、`POST .../duplicate`（前端生成新 id `uuid().slice(0,8)`）、`DELETE`（同时删 chats）、`GET /comfyart/projects`。项目 URL：`?project=<id>`。

---

<a id="sec2"></a>
## 2. 设计画布能力（tldraw 上的自研部分）

### 2.1 画板 / Artboard（`src/shapes/ArtboardShape.tsx`）

- **不新建 shape 类型**，而是 `class ArtboardShapeUtil extends FrameShapeUtil.configure({ getCustomDisplayValues })` —— 复用 tldraw 的 frame（分组/裁剪/子级）能力。
- 画板样式存在 **`shape.meta.style: { fill?, stroke?, radius? }`**（不是 props），`artboardStyle(shape)` 读取；`radiusOf` 上限 `min(radius, w/2, h/2)`。
- 圆角裁剪：`getClipPath` 用 4 个角各 `ARC_STEPS = 6` 段圆弧采样点构造多边形。
- SVG 导出：`toSvg` 里 `cloneElement` 给第一个子元素加 `rx/ry`。
- `exportImage(editor, ids, opts)`（同文件）：若画板有 fill/stroke/radius，则
  - SVG：`getSvgString({background:false})` 后用正则 `res.svg.replace(/(<svg[^>]*>)/, '$1<rect .../>')` **注入背景 rect**；
  - 位图：`toImage({format:'png', background:false})` → `createImageBitmap` → canvas 上 `ctx.roundRect(0,0,w,h,r*k)` 先填底色再 `drawImage`，JPEG 先铺 `#FFFFFF`，最后描边 `lineWidth = k`（k = 输出宽/逻辑宽）。
  → 可抄：**"画板底色/描边/圆角"用后处理合成，不改渲染管线**。
- 空引用框提示：`shape.meta.refFrame === true` 且无子级时渲染 `.ca-reframe__hint` 拖放提示。

### 2.2 富文本（`src/shapes/TextShape.tsx` 180 行 + `src/richText.tsx`）

- 自定义类型 `TEXT = 'ca-text'`，通过 **`declare module 'tldraw' { TLGlobalShapePropsMap }`** 注册类型（TS 层面把自定义 shape 类型并入 tldraw 全局映射）。
- props（15 个）：`w,h,text,font,weight,size,color,stroke,align,letter,line,autoSize,decoration,transform,rich`。
- **props 版本迁移**：`createShapePropsMigrationIds(TEXT, { AddDecoration: 1, AddRich: 2 })` + `createShapePropsMigrationSequence({ sequence: [{ id, up(props){...} }] })` → 老快照能平滑升级字段。★ 我们画布字段演进必须抄这个。
- 富文本双轨存储：`text`（纯文本，供搜索/导出/Agent 读）+ `rich`（tiptap JSON，仅当 `hasMarks(doc)` 为真才存，否则 `null`）→ **无样式时不付富文本开销**。
- tiptap 扩展极简（`richText.tsx:14-20`）：`StarterKit.configure({ heading:false, blockquote:false, codeBlock:false, code:false, horizontalRule:false, bulletList:false, orderedList:false, listItem:false, listKeymap:false, link:false, trailingNode:false })` + `TextStyle, Color, FontFamily, FontSize`。
- 渲染不用 tiptap，自己写 `RichView`（`doc → <div> 段落 → <span style=markStyle>`），`markStyle` 手工映射 bold/italic/underline/strike/textStyle(color,fontFamily,fontSize)。★ 导出/离屏渲染可控。
- 编辑时把静态 DOM `visibility: hidden` 保留布局，叠一个 `RichInput`（tiptap）；`Escape` 或 `Ctrl/Cmd+Enter` 退出；`onTransaction` 递增 `richVersionAtom` 通知工具栏刷新。
- 工具栏作用域：选中文字时只改选区，未选中时改整段（`docs-site/guides/design.mdx`）。
- 自动尺寸：`useLayoutEffect` 测 `scrollWidth/scrollHeight`，差 > 0.5 才 `editor.run(updateShape, { history: 'ignore' })`（**不污染撤销栈**）。手动 resize 时 `onResize` 调 `resizeBox` 并置 `autoSize: false`。
- 空文本退出时删除 shape：`onEditEnd` 里 `if (!shape.props.text.trim()) this.editor.deleteShape(shape.id)`。
- 描边文字：`WebkitTextStroke: ${max(1, size/24)}px color` + `paintOrder: 'stroke fill'`。

### 2.3 矢量与钢笔（`src/shapes/VectorShape.tsx` / `PenTool.ts` / `vectorPath.ts` / `booleans.ts`）

- 统一矢量 shape `VECTOR`，`kind: 'rect' | 'ellipse' | 'path' | 多边形/星形`，`sides` 控制边数，`radius` 圆角，`d` 存 **归一化到 0..1 的 SVG pathData**。
- ★ **路径归一化存储**（`vectorPath.ts:nodesToPath`）：节点含 `x,y,hx,hy`（手柄），计算全包围盒后把坐标除以 `w,h` 存成 0..1，shape 的 `w/h` 决定实际尺寸 → **缩放矢量不重算路径**，`scalePath(d, w, h)` 渲染时按轴放大。
- `scalePath` 是**手写 tokenizer**：`d.match(/[a-zA-Z]|-?\d*\.?\d+(?:e[-+]?\d+)?/g)`，用 `PAIRED = 'MLCSQTmlcsqt'` 判断当前命令的参数是否 x/y 成对，`H/h` 只缩放 x、`V/v` 只缩放 y；`round = v => Math.round(v*1e5)/1e5`。
- 布尔运算（`booleans.ts`）：`type BoolOp = 'unite'|'subtract'|'intersect'|'exclude'`；
  - 用 **paper.js**（`loadPaper()` 懒加载单例，`paper.setup(new paper.Size(1,1))` 无 DOM 依赖）；
  - 每个 shape 转成 `paper.PathItem`，并 `item.transform(new paper.Matrix(m.a,m.b,m.c,m.d,m.e,m.f))` 套用 `editor.getShapePageTransform(shape.id)` → **按页面实际变换做布尔**；
  - `shapes.slice(1).reduce((acc, s) => acc[op](itemOf(s), { insert: false }), itemOf(shapes[0]))`；
  - 结果 `normalized()`（平移到原点 + 除以 w,h）后写回新 VECTOR shape，**样式继承最底层 shape**（`props: { ...base.props, kind:'path', w:n.w, h:n.h, d:n.d }`），`index: base.index` 保持层级。
- 节点编辑：双击 path 进入 `ui/PathEditor.tsx` 拖点/拖手柄（docs 确认存在，实现待读）。

### 2.4 图层面板与层级操作

- `src/layers.ts`（276 行）实际是**图像分层**（split layers）逻辑，UI 在 `src/ui/LayersPanel.tsx`（201 行）。
- 面板能力（docs `canvas.mdx`）：拖行重排、拖入/拖出画板、双击改名、折叠展开、点选/Ctrl 加选/Shift 范围选、每行隐藏与锁定。
- z 序插入用 tldraw 的 `getIndicesBetween(shape.index, next?.index, n)` 批量取中间 index（`layers.ts:separateRegions`）→ **在两个已有图层之间插入 n 个新图层**。
- 命名约定：分层结果命名 `${原图层名} · ${序号}`，并用 `name.startsWith(\`${name} · \`)` 统计已占用序号避免重名。

### 2.5 评论（`src/shapes/CommentShape.tsx` 193 行 + `src/actions.ts` 评论部分）

- 评论是自定义 shape + 自定义工具；`props: { kind: 'point'|'lasso'|'box'|..., text, path }`，`path` 存 `"x,y x,y ..."`（归一化 0..1）。
- 三种标记方式：点一下、套索、框选（`docs-site/guides/canvas.mdx`）。
- **Touch edit**：`Ctrl+click` 图片（或 hover 按 `C`）→ 用 **SAM3** 把点转成对象掩膜（`markObject`），结果存 `comment.meta.mark = { image, src, box }`，`meta.points` 存 Shift 加的点；`markOf()` / `extraPointsOf()` 读取。
- `commentTarget(editor, comment)`：把评论的 page bounds 换算成**原图像素坐标 region**（`k = src.w / bounds.w`）；点评论若无 mark，则取以点击点为中心、边长 `min(src.w,src.h) * TOUCH_POINT(0.3)` 的方形 region。
- `touchEdit()`：把 mark 掩膜 / lasso path / 椭圆 / 矩形画进 mask（`ctx.filter = blur(feather)`，`feather = max(4, min(cw,ch)*0.04)`），再 `touchGraph(src.image, crop, mask, comment.text)`。
- 评论可"直接跑成编辑"或"发给 Agent"；评论面板支持 open/done 过滤、批量加入对话、一次性全部执行。
- 完成/失败写系统通知：`notify({ kind:'comment', title, body, srcs })`（`src/notices.ts`）。

### 2.6 画布上就地对话（`src/canvasChat.ts` + `src/ui/CanvasChat.tsx`）

- 入口：多选按 `shift+enter`（`overrides.ts` 的 `ca-chat`）或多选工具栏 Chat。
- 选中项以 `[Image 1] [Image 2]` 标签形式预填；**打字过程中继续点画布上的图，会在光标处插入新标签**；删缩略图自动重编号。
- 两个出口：`Generate`（直接当参考图生图，按标签顺序）/ `Send to Agent`。
- **One by one**：≥2 项时可"每个各跑一次"，每个缩略图切 `Each` / `Shared`；Shared 项进入每一次运行（例：把同一个杯子放到每张桌子上）。

### 2.7 聊天框 / 引用框（`src/chatFrame.ts` 70+ 行）

- 每个 Agent 会话在画布上有**自己的 frame**，以会话命名；该会话产出的东西按顺序落进这个 frame。
- `startChatFrames(editor)` 订阅 `onComfyEvent('comfyart-bot', ...)` 同步；frame 工具栏 "Open chat" 反向切会话。
- **Reference frame**：`meta.refFrame = true` 的 frame，链到会话后每次消息都带上里面所有素材 + 文本笔记当 brief；按钮显示条目数。

---

<a id="sec3"></a>
## 3. AI 图像工具链（可直接对照我们现有能力）

### 3.1 统一执行入口（`src/actions.ts` 313 行）

所有图像工具都走同一个 `run()`：
```ts
function run(editor, shape, label, onError, build: (src: Source) => Promise<Built>,
             opts: { size?: [number,number]; kind?: MediaKind; placer?: string; extra?: (src) => Record<string,unknown> } = {})
```
`source(editor, shape)` 从 asset 取 `{ image: annotated(src), name, w, h }`；结果命名 `${原图名} · ${工具名}`。
**原图永不覆盖**，结果放在旁边带进度环（docs `image-tools.mdx`）；`Restore original` 取回首版。

已实现工具（`actions.ts` 导出清单）：`upscale` / `creativeUpscale` / `cutout` / `quickEdit` / `editText` / `angles` / `expand` / `animate` / `upscaleVideo` / `cutoutVideo` / `extendVideo` / `styleTransfer` / `splitLayers` / `erase` / `eraseRegion` / `inpaint` / `editRegion` / `moveObject` / `moveTarget` / `markObject` / `touchEdit` / `separateRegions` / `regionSplit`。

### 3.2 掩膜（mask）工程套路 ★

- `maskFile(w, h, paint)`：建同尺寸 canvas，**先填白 `#fff`，再 `globalCompositeOperation = 'destination-out'` 让 paint 挖洞** → 白=保留、透明=待重绘；`toBlob` 上传，返回 `"subfolder/file [type]"` 字符串。
- `drawStrokes(ctx, strokes, w, h)`：笔迹点存**归一化 0..1**，`lineWidth = r * 2 * w`（r 是归一化笔半径）；单点笔迹补 `lineTo(x*w+0.01, y*h)` 保证有像素。
- `lumaToAlpha(img, w, h)`：把亮度图转 alpha 图（`data[i+3] = data[i]`，RGB 设 255）→ SAM 输出的掩膜图统一转成可用 mask。
- `maskBounds(mask)`：扫 alpha > 16 求包围盒。
- `grow(data, w, h, px)`（`layers.ts`）：**用 `ctx.filter = blur(px)` + 阈值 `>16 → 255` 做掩膜膨胀**，无需手写形态学。
- `cropAround(region, w, h)`（局部重绘裁窗）：`TOUCH_PAD = 0.25`、`TOUCH_MIN = 384`，向外扩 50%、最小 384px，**尺寸对齐到 8 的倍数**（`Math.round(grow/8)*8`），再 clamp 到图内。
- 羽化：`feather = Math.max(4, Math.min(cw, ch) * 0.04)`。
- 分层掩膜膨胀用 `grow(union, w, h, 4)`，区域分割用 `ctx.filter='blur(6px)'` 画两遍叠加。

### 3.3 分层（Split Layers）★ 含质量过滤

`registerPlacer('layers')`（`src/layers.ts:101`）：
- 对每个模型返回的层 `measure()` 三个指标：`cover`（alpha>128 占比）、`solid`（alpha>200 计数）、`diff`（与原图平均色差）。
- 过滤规则：`if (r.cover > 0.9 || r.cover < 0.002 || r.diff > 60) continue`；再按 IoU 去重 `overlap(a,b) > 0.9` 丢弃；按 cover 降序贪心保留，最后恢复原顺序。
- `cropLayer(data, w, h)`：扫 alpha>8 求 bbox → 裁到可见像素 → 上传 → 返回 `{ media, x, y }`。
- 建 frame：背景层 = **原图把所有被拆出的层擦掉（LaMa inpaint）后的干净底**，所以移动/隐藏图层不会露出重复内容（docs + `layers.ts` 尾部 `startJob(..., eraseGraph(source, mask), { placer:'replace' })`）。
- `separateRegions()`（纯浏览器，不跑模型）：在任意图层上画框 → 把框内不透明像素搬到新层并从原层清零 → `getIndicesBetween` 插到原层正上方 → 原层若空则删除。
- `regionSplit()`：最多 6 个框 → `samBoxesGraph` 批量出掩膜 → 每框裁层 → 背景用 `eraseGraph` 修补。

### 3.4 Edit Elements：平面海报 → 可编辑画板 ★ 完整实现在 `src/elements.ts`（187 行）

三步（`docs-site/guides/elements-mockups.mdx` + 代码）：
1. **OCR 文本层**：`POST /comfyart/ocr`（RapidOCR，服务端）返回 `Line = { box: [number,number][], text, score }`；过滤 `score >= MIN_SCORE(0.6)` 且非空。
   - 从 4 点 box 推 `cx,cy,w,h,angle = atan2(p1-p0)`；
   - **取色与字重**（`inkOf`）：在 box 内算灰度均值，按"高于均值/低于均值"两组分别累加 RGB，取像素少的一组为墨色 → 十六进制色；`weight = 墨像素占比 > BOLD_INK(0.3) ? 700 : 400`；
   - **对齐**（`alignOf`）：`|cx - width/2| < width*0.03` → center；`cx - w/2 > width/2 && cx + w/2 > width*0.9` → right；否则 left；
   - **字体识别**（`applyTypography`，需 Agent）：一次 LLM 调用返回每行 `{n, family, weight}`，`FAMILIES = { serif:['Georgia','SimSun'], sans:['Arial','Microsoft YaHei'], display:['Impact','SimHei'], mono:['Courier New','FangSong'], script:['Georgia','KaiTi'] }`，含 CJK 字符（`CJK = /[぀-ヿ㐀-鿿가-힯]/`）时取第二项（中文字体）。
   - **字号求解**：用离屏 canvas `measure.font = \`${weight} ${PROBE_SIZE=100}px "${font}"\``，`size = min(h*k*MAX_SIZE_PER_HEIGHT(1.05), 100*l.w*k*TEXT_FILL(0.94)/measureText(text).width)`。
2. **对象抠出**：`objectLayers()` 对 `OBJECT_PROMPTS = ['product','logo']` 各跑一次 SAM3（文本提示），把已识别文本框区域（`paintBoxes`，`MASK_PAD = 0.2`）从掩膜里排除，跨对象去重（`union`），`count < MIN_OBJECT(0.002)*w*h` 则丢弃，`cropLayer` 裁紧。
3. **背景修补**：`maskFile` 里画文本框（pad 0.2h）+ `grow(union, w, h, OBJECT_GROW=6)`，再 `eraseGraph(annotated(src), mask)`（LaMa）→ 干净背景。
- 落位 placer `'textLayers'`：建 frame（`meta.elements = source`），背景 + 对象层 + 每行一个 `TEXT` shape（带 `rotation: l.angle`、`w`、`align`、`autoSize:false`）。
- 之后画板工具栏出现 **Re-render**，把编辑后的层重新合成自然图像。
- 首页还有 **Break down an image**：上传设计图 → 新建项目 → 自动跑 Edit Elements。

### 3.5 Compose board（`src/compose.ts` + `src/shapes/ComposeShapes.tsx`）

- 手工排版画板 + 两种专属元素：**AI block**（虚线区 + 描述文字，渲染时填充内容）和 **QR code**（双击改链接，渲染保持清晰）。
- Render：导出画板 → 把每个 AI block 标成灰区 → 让图像模型把排版变成成品，**保持文字/logo/照片位置不变**。
- 三个辅助（docs `elements-mockups.mdx`）：**Changes since the last render**（列出增删改元素）、**What Render sends**（预览完整 prompt，含每个 AI block 的位置与描述）、**Export layout** → `.layout.json` 文件，`Insert → Import layout` 可跨项目复用为模板。★ 这是"画布即模板"的关键设计。

### 3.6 Mockup（`src/mockup.ts` + `src/mockupGl.ts` 139 行 + `src/shapes/MockupShape.tsx`）

- 一次性预处理：跑 Depth Anything V2 深度 + BiRefNet 抠产品；之后**贴设计图完全在浏览器里实时算**（three.js）。
- 能力：左右/上下/尺寸/角度放置；**Follow surface** 按褶皱曲面形变 + 叠加产品自身明暗 + 产品外区域遮罩；`Bake as image` 拍平；`AI blend` 再过一次图像模型。
- Mockup 本身是可再编辑的 shape（重新选中可换设计/移动）。

### 3.7 Adjust：非破坏性调色（`src/adjust.ts` 159 行）

- 四组参数：Light（exposure, contrast, highlights, shadows, whites, blacks）、Color（saturation, vibrance, temperature, tint）、Detail（clarity, sharpen, grain）、Effects（vignette, bloom, glamour）；`Auto` 自动取值；按住可对比原图。
- **非破坏性**：保留原图像素 + 滑杆值，重开面板从上次值继续，不会二次叠加；`Reset` + `Apply` 恢复原始像素但保留裁剪/翻转。

### 3.8 其它图像工具

- **Crop**：`ui/CropBar.tsx` 替换工具栏；预设 Free/Original/1:1/4:5/3:4/9:16/16:9/2:3/3:2/1.91:1，或直接输入像素宽高；Reset / Done。
- **Upscale**：2×/4×；`Faithful to original` = 4x-UltraSharp；`Enhance details` = 放大重绘 + 风格预设 + 细节强度。
- **Vectorize**：`imagetracerjs` 转 SVG；画布上按当前 zoom 显示栅格化结果，导出保留完整 SVG。
- **Extract Text**：本地 Gemma 4 或 Agent 两种引擎（Agent Settings 可切）。
- **Generation Info / Copy Prompt / Remix（同 prompt 换 seed）/ Regenerate**：数据来自 `meta.gen`。
- **Effects**：opacity / shadow / blur（`ui/Effects.tsx`）。
- **Smudge**：推像素（`ui/SmudgePanel.tsx`）。
- 笔刷工具（Eraser/Inpaint/Smudge）在指针下显示笔刷圆环。

---

<a id="sec4"></a>
## 4. 生成器（画布上的生成节点）

- 底栏 `Image Generation`，箭头切 image / video / audio；生成卡片**尺寸就是将要生成图像的真实像素尺寸**（docs `generate.mdx`）。
- 图像设置：分辨率 1K/2K/自定义宽高；比例 1:1,3:2,2:3,4:3,3:4,16:9,9:16,4:5,5:4,21:9；数量 1–4；质量 Turbo/Standard/High Quality；风格 None/Photo/Cinematic/Illustration/3D/Anime/Minimal/Retro；透明背景开关。
- Advanced：seed（可关随机手填/掷）、negative prompt（**仅 Qwen Image 2512 显示**，其它蒸馏模型忽略所以隐藏）、LoRA 最多 3 个、强度 0–200%，按模型元数据过滤（`For this model` / `All` / `Favorites`）。
- 参考图：Qwen Image 2.1 最多 16 张；来源 Upload / ComfyUI 素材 / My Assets / **Pick on canvas（画布上点选）**。
- 参考模式：Content / Style / Composition + 结构模式（line art, MLSD, scribble, soft edge, depth, normal, pose, segmentation, tile, recolor…）；Qwen 2512 下走真 ControlNet，带 **Reference strength / Start / End / Detail(512|1024|2048) / Fit(crop|stretch) / Preview control map**（只跑预处理器先预览，参数变了预览变灰需 `Extract again`）。
- 参考图按使用顺序编号 `image 1..n`；模型不支持时**chip 置灰并写明原因，生成照常进行**。
- 颜色参考：把颜色名写进 prompt，同时附一张小色板图作参考（只取色不取构图）。
- Base image：图生图 + 变化强度 0–100。
- 视频：比例 16:9/9:16/1:1/4:3/3:4/21:9；768p/1080p；5/10/15 秒；镜头运动 Auto/Static/Push In/Pull Out/Orbit/Follow/Handheld/Pan L/R/Tilt U/D；五模式 First-last frame / References（≤9 图 + ≤3 视频 + ≤3 音频，标签 `<Picture 1>` `<Video 1>` `<Audio 1>`）/ Motion control（角色图 + 动作视频逐帧抽 pose）/ Lip sync（人像 + 语音，视频长度=音频长度，≤15s）/ Storyboard（≤6 场、共 30 秒、角色风格一致）。
- 视频工具：Crop Video（剪辑器：裁切/分割/每段速度与音量/淡入淡出/字幕 → 导出新视频或替换）、Extend Video、Upscale（1080p/2K/4K，保音频与帧率）、Remove Background（逐帧抠到黑底）、Remix。

---

<a id="sec5"></a>
## 5. 导出

- PNG / JPG / PDF（任意 scale，选区或整页）；**PPTX**：每个画板或每页一张幻灯片，可选"图片"或"**可编辑文字（含混合样式）+ 图片 + 形状**"（`pptxgenjs`，混合样式拆成多个 text run）。
- **PSD**（`ag-psd`，`src/psd.ts`）：多选下载里含 PSD 导出。
- ZIP（`fflate`）：画板批量下载。
- `.layout.json`：compose board 布局导出/导入（模板复用）。
- 3D：GLB 下载 + `Export Current View` 当前视角转图片继续设计。

---

<a id="sec6"></a>
## 6. Agent / MCP（画布被 Agent 操作的方式）

- MCP over HTTP：`POST /comfyart/mcp?project=<id>`；`claude mcp add --transport http comfyart "..."`。
- **画布工具由"该项目已打开的 ComfyArt 标签页"执行**（后端只转发），所以 Agent 工作时必须开着那个 tab。
- 工具分组（`docs-site/guides/mcp.mdx`）：
  - Look：`server_info, get_canvas, view_canvas, focus, list_models, brand_kits`
  - Generate：`generate_image, generate_video, wait_generation`（**前两个立即返回 id，用 wait_generation 取结果**）
  - Edit images：`edit_image, replace_text, edit_region, erase_region, move_object, style_transfer, upscale_image, remove_background, expand_image, split_layers, change_angle, animate_image, vectorize_image`
  - Rebuild/mockup：`edit_elements, create_mockup, place_design`
  - Video：`extend_video, upscale_video, remove_video_background`
  - Audio/3D：`generate_music, generate_sound_effect, text_to_speech, image_to_3d`
  - Layout：`add_text, add_shape, create_frame, update_shapes, delete_shapes, place_html, create_slides`
  - Memory/skills：`memory, skill`
- 后端 `api/mcp_tools/canvas.py`、`media.py`、`bot_tools.py`；命令经 ws 事件 `comfyart-mcp-command` 下发到前端 `botCommands.ts`（459 行）执行。
- Agent 宿主是**命令行 Agent**（Claude Code / Codex / Qwen Code / DeepSeek Harness / 任意 OpenAI 兼容端点），ComfyArt **从不存凭据**。
- 单发小任务（分镜拆分、OCR、品牌/记忆抽取、参考笔记、Ideogram caption）走 `POST /comfyart/bot/complete` 无工具调用。
- Agent 能力：把计划发成 checklist 并逐项打勾；工具调用折叠成一行（"Ran 17 steps"）可展开看每步耗时；可读 reasoning 折叠成 **Thought**。
- 附件规则：视频给 Agent 时先抽 4 帧，需要更多用 `view_video`；音频先本地转写；PDF 前几页转图 + 文本。
- Memory：跨项目记忆（关于用户/口味/工作方式/素材），可 onboarding 提问、从文件抽取、演化日志。
- 自治开关（默认全关）：`Use memory` / `Auto-update memory`（每轮结束抽取长期偏好）/ **`Canvas check-up`**（打开有改动的项目约 1 分钟后自动审查画布，问题进通知中心）/ **`Self-evolving skills`**（每几次请求检查是否在重复某流程，是则生成 skill，标 `Learned`）/ `Daily runs`（每日上限）。
- Skills：`SKILL.md` + 附件文件夹，支持导入、从对话保存、**版本历史与回滚**。
- 定时任务：ComfyArt 开着时按日/周跑 Agent prompt（`src/schedules.ts`）。

---

<a id="sec7"></a>
## 7. 素材与品牌（`src/library.ts` / `src/assets.ts` / `src/ui/KitEditor.tsx` 272 行）

- My Assets 两类：**Files**（ComfyUI 素材库全部图/视频，Generate/Upload 过滤 + 搜索 + 懒加载缩略图，加载失败显示占位图标）与**命名资产**（Brand Kit / Character / Product / Custom）。
- 在 prompt 里用 `@` 引用品牌 kit / 角色 / 产品 / 画布项；用 `/` 选 skill。
- Brand kit 内容：Design Guidelines（纯文本规则，Agent 遵循）、Logo、**Color（每个颜色有 role，如 primary / background）**、Fonts（可从字体选择器添加或上传字体文件，上传后可在画布文字里用）、参考文件；`Extract from file` 让 Agent 读品牌手册（图或 PDF）自动填。
- Kit 与项目绑定（项目菜单 `Project Brand Kit`），**每条消息都带上 kit**；kit 可作为新项目默认；可复制、设封面。
- Reference panel（项目级参考板）：上传 / My Assets / 画布选中加入；按来源过滤、排序、重命名、复制、剪切、复制副本、导出、删除；每项有 **Style Analysis / Composition Analysis**（Agent 跑，笔记可直接进 prompt）和 **Generate Variations**；`Generate moodboard` 把参考排进一个 frame。

---

<a id="sec8"></a>
## 8. 其它工程细节

- `src/id.ts`：`uuid()`；项目 id 用 `uuid().slice(0,8)`。
- `src/i18n.ts` + `src/locales/{en,zh}.json`：`t('a.b', args)`；启动时按 ComfyUI 语言初始化，`editor.user.updateUserPreferences({ locale: lang==='zh' ? 'zh-cn' : 'en' })`；页面名 `Page N` 会被批量改名成本地化名称。
- `src/theme.ts`：`themeAtom` + `markDark()`；`colorScheme: 'light'|'dark'|'system'`。
- `src/notices.ts`：通知中心（建议、评论修复、完成任务）+ 标签页隐藏时任务完成发浏览器通知。
- `src/proactive.ts`（125 行）：Canvas check-up 主动审查。
- `src/schedules.ts`：定时任务。
- `src/clipboard.ts`：剪贴板。
- `src/embeds.ts` / `shapes/EmbedShapes.tsx`（226 行）：`AudioShapeUtil / HtmlShapeUtil / LottieShapeUtil / ModelShapeUtil` —— 音频卡（播放条 + 时长）、HTML widget、Lottie、3D（GLB/GLTF/OBJ/FBX/STL/PLY，three.js，自转/交互/导出当前视角）。
- `src/slides.ts` + `ui/Slideshow.tsx`：把画板当幻灯片全屏播放。
- `src/fonts.ts`：字体选择器，分 **Brand kits / Common / This computer**（本地字体需浏览器授权，`queryLocalFonts`），每个名字用自己的字体渲染。
- `src/ui/Customize Toolbar`：图像工具栏可自定义哪些固定、是否显示名字（docs `image-tools.mdx`）。
- `src/ui/Shortcuts.tsx`：完整快捷键表，`?` 打开。
- 后端 `POST /comfyart/pdf`：上传 PDF 渲染最多 30 页为 PNG 存 `input/comfyart/pdf/`，**同时返回每页文本**（画布上 PDF 每页保留文本，Agent 可用 `get_canvas` 读到）。
- 后端 `POST /comfyart/ocr`：RapidOCR 返回每行文本 + box。
- 自定义节点 `ComfyArt Precise UNET Loader`：绕过 ComfyUI `--fast fp8_matrix_mult`（会把未缩放的 fp8 checkpoint 变成噪声）。

---

## 8.5 画板（Frame）自动布局系统 ★★ 高价值，可直接照搬思路

来源：`src/ui/FrameToolbar.tsx`（257 行）。它把 Figma 的 auto-layout 简化成"画板内自动排布 + hug"。

**状态存在 `frame.meta`（不是 props）**：
- `meta.layout: 'auto' | 'manual'`（`isAuto`）
- `meta.wMode / meta.hMode: 'fixed' | 'hug'`（默认 `fixed`）
- `meta.ratio: number | null`（锁定宽高比）
- `meta.style: { fill?, stroke?, radius? }`（画板样式）
- `meta.chatId`（会话画板）、`meta.refFrame`（引用画板）、`meta.elements`（Edit Elements 画板）

**常量**：`GAP = 22`（工具栏与选区间距）、`EDGE = 8`、`PAD = 24`（自动布局内边距）、`ARRANGE_GAP = 40`、`GEN_LONG = 1536`。

**尺寸预设**（`SIZE_PRESETS`）：Instagram 1080×1080、IG 竖版 1080×1350、Story 1080×1920、YouTube 缩略图 1280×720、演示 1920×1080、桌面 Web 1440×1024、移动端 390×844、A4 2480×3508、A4 横 3508×2480。

**自动布局算法 `autoLayout`**（`FrameToolbar.tsx:48-72`）：
1. 子级按 `(y, x)` 排序；
2. 列数 `cols = ceil(sqrt(n))`；
3. 贪心分行：宽度 `fixed` 时按"当前行已用宽 + 子宽 + PAD ≤ frame.w"判断换行；宽度 `hug` 时按 `row.length < cols` 换行；
4. 逐行 `moveTo` 到 `x = PAD + 累计(b.w + PAD)`，行高 `y += max(row.h) + PAD`；
5. 最后按模式回写 `w`（hug 时 = 最宽行）与 `h`（hug 或非 fixed 时 = 累计 y）。
`hugContent`：只把 w/h 收到 `max(child.maxX - frame.x) + PAD`。
`moveTo` 用 `editor.getPointInParentSpace(s.id, {x: b.x, y: b.y})` 做页面坐标→父坐标换算，再 `x: s.x + x - local.x`。

**触发机制 `registerFrameRules`**（`FrameToolbar.tsx:88-125`）★ 关键工程手法：
- `registerBeforeChangeHandler('shape')` 实现**比例锁定**：若 `meta.ratio` 存在且 w/h 变了，判断哪一轴变化更大（`Math.abs(n.w/p.w - 1) >= Math.abs(n.h/p.h - 1)`），据此反算另一轴 → **在 beforeChange 里改写 next，用户拖拽时天然等比**。
- `registerAfterChangeHandler` / `AfterCreateHandler` / `AfterDeleteHandler` 都调 `touch(shape)`：找到父 frame，若既不是 auto 也不是 hug 就直接返回；否则把 frame id 放进 `pending: Set`，用 `queueMicrotask(flush)` **合并同一帧内的多次改动**，`flush` 里 `editor.run(..., { history: 'ignore' })` → **自动布局不产生撤销记录**；`busy` 标志防止自触发死循环。

**Generate into frame**（`generateInto`）：按 `k = GEN_LONG / max(w,h)` 把画板尺寸换算成生成分辨率（`resolution:'custom', cw/ch`），创建 generator shape 并 `reparentShapes([id], frame.id)`，位置 (0,0)、尺寸等于画板 → **在画板里直接生成，尺寸自动匹配**。

**画板工具栏内容**（顺序）：Open chat（若 `meta.chatId`）/ Link to chat（若 `meta.refFrame`）/ 名称输入框 / W 数字框 + Fixed↔Fit 切换 / H 数字框 + 切换 / 锁比例 / 尺寸预设下拉 / Style（fill、stroke、圆角）/ Auto↔Manual / Fit contents / Generate / Render（compose board 或 Edit Elements 的 Re-render）/ ComposePanel / 全屏播放 / 批量下载子级 / 下载 ZIP / 导出。

**ZIP 下载**（`downloadZip`）：`fflate.zipSync(entries, { level: 0 })`（**不压缩只打包**，因为图片本身已压缩）；文件名清洗 `/[\\/:*?"<>|]/g → '_'`，重名加 `-2`、`-3`；扩展名从 `blob.type.split('/')[1]`，`jpeg → jpg`。

**幻灯片**：`framesOnPage(editor)` 取当前页顶层 frame 按 (y,x) 排序 → `slideshowAtom.set({ ids, index })` 全屏播放。

---

## 8.6 多选工具栏（`src/ui/MultiToolbar.tsx` 128 行）

- 只在 `editor.getSelectedShapes().length > 1` 且无模式（`modeAtom`）时显示。
- 定位算法（所有浮动工具栏共用）：
  ```ts
  const left = Math.min(Math.max((rect.l + rect.r)/2 - width/2, EDGE), host.width - width - EDGE)
  let top = rect.t - GAP - 44; if (top < EDGE) top = rect.b + GAP
  ```
  用 `useLayoutEffect` 测 `bar.current.offsetWidth` 再定位 → **居中对齐选区，顶部放不下就翻到下方**。
- 功能：Chat（Shift+Enter）/ **Tidy up**（网格整理）/ Group / Merge layers（`flatten-to-image`）/ 布尔（仅当全部是 VECTOR）/ Align 6 向（`editor.alignShapes(ids, 'left'|'center-horizontal'|'right'|'top'|'center-vertical'|'bottom')`）/ Distribute 2 向 + Arrange in grid / Download（逐个下载 + Export PSD + Export as）。
- `arrange(editor, shapes)`（可抄）：按 (y,x) 排序 → `cols = ceil(sqrt(n))` → 每行按 x 排序，从选区 bounds 左上角开始铺，间距 `ARRANGE_GAP = 40`，整体包在 `editor.run(() => {...})` 里 → **一次撤销**。
- `downloadAll`：有 asset 的逐个 `downloadUrl`，剩下的（无 assetId）合并 `editor.toImage(rest, {format:'png', background:false})` 下载。

---

## 8.7 生成卡片 shape（`src/shapes/GeneratorShape.tsx` 96 行）

- 类型 `GENERATOR = 'generator'`，`BaseBoxShapeUtil`；props 13 个：`w,h,prompt,ratio,resolution,count,workflow,duration,transparent,quality,style,cw,ch`；默认 `{ w:512,h:512, ratio:'1:1', resolution:'1K', count:1, duration:5, quality:'standard', cw:1024, ch:1024 }`。
- 迁移：`AddDuration:1`（`props.duration = 5`）、`AddOptions:2`（`transparent/quality/style/cw/ch`）。
- `canEdit() = false`；`getIndicatorPath` 返回矩形 Path2D（选中框按真实尺寸）。
- **卡片本体只是占位**：`component()` 渲染一个图标（视频/图片）+ 进度环；进度来自 `progressAtom.get()[job.id]`，`job = shape.meta.job`。
  进度环 CSS：`data-indeterminate` 当 p==0；`style={{ '--ca-p': p }}`。
  → 设计要点：**生成参数在 props，运行时状态在 meta，进度在外部 atom**，三者分离，卡片本身极轻。

---

## 8.8 生成流程与参数换算（`src/generate.ts` 261 行）

- 常量：`RESOLUTIONS = ['1K','2K','custom']`；`SHORT_SIDE = {'1K':1024,'2K':2048}`；`RATIOS = ['1:1','3:2','2:3','4:3','3:4','16:9','9:16','4:5','5:4','21:9']`；`VIDEO_RATIOS = ['16:9','9:16','1:1','4:3','3:4','21:9']`；`DURATIONS = [5,10,15]`；`MAX_SCENES = 6`；`MAX_STORY_SECONDS = 30`；`MAX_SPEECH_SECONDS = 15`；`MAX_COLORS = 6`；`BASE_STRENGTH = 60`。
- `STYLES` 是 `[key, 显示名, 追加到 prompt 的英文短语]` 三元组，例如 `['cinematic', 'Cinematic', 'cinematic still, dramatic lighting, film grain']`、`['minimal', ..., 'minimalist design, generous negative space']`。视频用 `SHOTS`（镜头运动 → 英文镜头描述，如 `'camera orbits around the subject'`）。★ **风格/镜头 = prompt 后缀表**，不是模型参数。
- `targetSize()`：`snap16 = v => Math.round(v/16)*16`（**所有输出尺寸对齐 16**）；`r >= 1 ? [snap16(short*r), short] : [short, snap16(short/r)]`；custom 用 `cw/ch`；视频用 `videoSize(r, VIDEO_AREAS[resolution])`。
- `nearestRatio(aspect, ratios)`：用 **对数距离** `Math.abs(Math.log(ratioOf(b)/aspect))` 选最接近比例。
- `freeSpot(editor, w, h, from?)`：从视口中心开始，碰撞则右移，最多 200 次（与 `jobs.ts` 同一套 `GAP=40` 扫描）。
- `createGenerator(editor, video, props, focus, from)`：先算 `targetSize` 再 `freeSpot`，`focus` 时 `setCurrentTool('select')` + `select` + `zoomToSelectionIfOffscreen(64, { animation: { duration: 200 } })`。
- 状态读取器全部从 `shape.meta` 取：`refsOf / baseOf / lastFrameOf / colorsOf / negativeOf / seedOf / lorasOf / scenesOf / videoModeOf / motionOf / speechOf`。
- `applySeed(graph, seed)`：遍历 graph，`class_type === 'RandomNoise'` → `inputs.noise_seed`；`'KSampler'` → `inputs.seed`。★ **按 class_type 打补丁，不依赖节点 id**。
- `applyLoras(graph, loras)`：找 `inputs.model === ['1',0]` 的节点为 consumers，串接 `LoraLoaderModelOnly`（`lora0 → lora1 ...`），`strength_model = strength/100`，最后把 consumers 的 model 指向链尾。
- `generateGraph` 关键分支：
  - prompt 组装 = `[用户 prompt, 风格后缀, transparent && 'isolated subject on a plain white background'].join(', ')` + 参考图 hints + `palettePrompt(colors)`。
  - 有 `model.caption` 时**先让 LLM 把短 prompt 扩写成 caption**（`caption()` 走 workflow spec 里的 system/template，替换 `{w} {h} {prompt}`）。
  - 尺寸注入：`for (const id of model.sizes!) Object.assign(graph[id].inputs, { width, height })`；批量：`graph[LATENT].inputs.batch_size = count`（节点 id 常量 `[PROMPT, LATENT, RESULT] = ['6','5','9']`）。
  - **图生图（base image）**：动态插入 `b0 LoadImage → b1 ImageScale(lanczos, crop:center) → b2 VAEEncode → b3 RepeatLatentBatch`，接到 sampler 节点 `'7'` 的 `latent_image`；若 sampler 是 `KSampler` 就设 `denoise = strength/100`，否则插 `SplitSigmasDenoise` 改 `sigmas`。★ 前端直接改 ComfyUI graph 的能力很强。
  - **透明背景**：追加 `LoadBackgroundRemovalModel(birefnet.safetensors) → RemoveBackground → InvertMask → JoinImageWithAlpha`，并把 SaveImage 的输入改到 `bg4`。
  - steps 按质量档：`graph[model.steps[0]].inputs.steps = model.steps[1][quality] ?? standard`。
- `generate()`：`seed = seedOf(shape) ?? randomSeed()`（`randomSeed = () => Math.floor(Math.random() * 2**31)`），`startJob(..., { name: prompt.slice(0,32), kind, extra: { seed } })`。
- **Remix / Regenerate**（★ 强烈建议抄）：靠 `meta.gen`（GenInfo）反向重建 generator shape。
  - `settingsOf(info)` 还原 prompt/ratio/resolution/duration/workflow + `info.settings`；
  - `inputsOf(info) = info.inputs ?? (info.refs ? { refs } : {})`；
  - `remix` = 新建 generator 并把 inputs 写进 meta（用户可改）；
  - `regenerate` = `spawnHolder` + 写 `{ temp:true, ...inputs }` + 直接跑（`count: 1`）。

---

## 8.9 浮动工具栏定位机制（`src/ui/selection.ts` 40 行）★ 所有工具栏共用

```ts
export function useSelectionRect(): ScreenRect | null {
  return useValue('selection rect', () => {
    if (editor.getCroppingShapeId() || editor.isIn('select.resizing') || editor.isIn('select.translating')) return null
    const bounds = editor.getSelectionPageBounds()
    if (!bounds) return null
    const a = editor.pageToViewport({ x: bounds.minX, y: bounds.minY })
    const b = editor.pageToViewport({ x: bounds.maxX, y: bounds.maxY })
    return { l: a.x, t: a.y, r: b.x, b: b.y }
  }, [editor])
}
```
- **关键**：裁剪中 / 缩放中 / 平移中直接返回 null → 工具栏自动隐藏，不会跟手抖。
- `useOnlySelected<T>(type)` 返回“仅选中一个且是指定类型”的 shape；`useSelectedImage` / `useSelectedGenerator` 是其包装。
- `useElementSize(ref, initial)` 用 `useLayoutEffect` 量 offsetWidth/Height，相等时不 setState（避免无限重渲染）。
- 工具栏容器统一 `onPointerDown={(e) => e.stopPropagation()}` 防止抢选区；文字工具栏额外在编辑态 `onMouseDown` 里 `preventDefault()`（除 input）以**保留富文本选区**。

## 8.10 文字/形状样式工具栏（`src/ui/StyleToolbars.tsx` 290 行）

- 常量表（可直接抄）：
  - `WEIGHTS = [[100,'Thin'],[300,'Light'],[400,'Regular'],[500,'Medium'],[600,'SemiBold'],[700,'Bold'],[900,'Black']]`
  - `SIZES = [12,14,16,20,24,32,40,48,64,80,96,128,160,200]`，其中部分带名字（12 Small / 16 Default / 20 Regular / 32 Large / 48 XL / 64 XXL / 96 Huge）
  - `DECORATIONS = none|underline|line-through`；`CASES = none|uppercase|lowercase|capitalize`
  - `FILLS = solid|linear|radial|image`；`STROKE_ALIGNS = inside|center|outside`；`DASHES = solid|dashed|dotted`；`MAX_STOPS = 6`
- ★ **双轨写入模式**（富文本与 shape props 共存的核心）：
  ```ts
  const run = editing && rich && !rich.state.selection.empty ? rich : null
  const marks = run?.getAttributes('textStyle') ?? {}
  const color = marks.color ?? p.color
  const setColor = (c) => (run ? run.chain().focus().setColor(c).run() : set({ color: c }))
  const setWeight = (w) => (run ? run.chain().focus()[w >= 600 ? 'setBold' : 'unsetBold']().run() : set({ weight: w }))
  ```
  → **选中文字时改选区 mark，未选中时改整段 props**；读取时 mark 优先、props 兵底。
- 字体按钮直接用选中字体渲染自己的名字（`style={{ fontFamily: '"'+font+'"' }}`）。
- 渐变预览用 CSS：`linear-gradient(${p.angle + 90}deg, …)` / `radial-gradient(…)` / `center / cover url(…)`；stop 存 `{offset: 0..1, color}`，新增 stop 默认复制最后一个、offset 0.5。
- ★ **图片填充的候选源**：`editor.getCurrentPageShapes()` 里所有 image shape 的 asset src，去重后取**最后 15 个**（`slice(-15)`）→ 不建额外图库，直接用画布上当前的图。
- 混合模式：`BLEND_MODES`（定义在 `shapes/VectorShape.tsx`）存 `props.blend`，用 `<select>` 选。
- 形状工具栏：W/H 数字框 + 锁比例按钮（锁定时 `k = v / p[key]` 同时缩放两轴）；rect 才有 `cornerRadius`；polygon/star 才有 `sides`（上限 24）。
- 单个 shape 下载：`editor.toImage([id], { format:'png', scale:2, background:false })`。

## 8.11 图层面板（`src/ui/LayersPanel.tsx` 201 行）★ 完整可抄

- 树渲染：`childrenOf(editor, id) = [...editor.getSortedChildIdsForParent(id)].reverse()`（**上层在上**）；`Row` 递归，`paddingLeft: 4 + depth * 16`。
- 每个 Row 用 `useValue` 细粒度订阅（`editor.getShape(id)` / `getSelectedShapeIds().includes(id)` / children）→ **单行变化不重渲染整棵树**。
- 名称解析优先级（`names` map，一次算完）：
  `shape.meta.name` → frame 的 `props.name` → asset 文件名 → `` `${typeName} ${序号}` ``（按页内排序逐个计数）。
  `typeName`：`ca-shape` 按 `props.kind` 映射（rect/ellipse/polygon/star）；`generator` 按 workflow 判视频/图片。
- ★ **拖拽落位三态**（`above | below | into`）：
  ```ts
  const zoneOf = (e) => { const r = e.currentTarget.getBoundingClientRect(); const k = (e.clientY - r.top) / r.height
    return canContain(shape) && k > 0.25 && k < 0.75 ? 'into' : k < 0.5 ? 'above' : 'below' }
  ```
  行高中间 50% 区域 = 放入容器，上/下 25% = 插到上/下。
- ★ **z 序插入公式**（`move()`）：
  - 放入容器顶部：`reparentShapes(ids, target.id, getIndexBetween(topShape?.index ?? null, null))`
  - 同级插入：先取**排除被拖项后**的兄弟列表，`[below, above] = drop==='above' ? [siblings[at], siblings[at+1]] : [siblings[at-1], siblings[at]]`，再 `reparentShapes(ids, target.parentId, getIndexBetween(below?.index ?? null, above?.index ?? null))`
  - 循环引用保护：`if (ids.some(id => id === target.id || editor.hasAncestor(target, id))) return`
- 多选：`anchor` 记录上次点行；Shift = 在**可见行列表**里取区间 `select(...rows.slice(min,max+1))`；Ctrl/Cmd = 切换；普通点击 `select(id)` + `zoomToSelectionIfOffscreen(64, { animation:{duration:200} })`。
- 隐藏/锁定：锁定用 tldraw 原生 `isLocked`；**隐藏用 `meta.hidden`**（配合 `getShapeVisibility`）；两个按钮都包在 `editor.run(..., { ignoreShapeLock: true })` 里 → **已锁定的图层仍能在面板里解锁/切隐藏**。
- 折叠：`collapsed: Set<TLShapeId>`（组件本地 state，不入库）；`visible()` 递归展开未折叠行，供 Shift 范围选用。
- 缩略图：有 asset 就 `<img>`/`<video muted preload="metadata">`，否则图标。
- `generated(editor)`：`store.query.records('shape')` 里 `src.includes('type=output')` 的（即 ComfyUI 输出目录的图）倒序 → “本项目的生成历史”列表。

## 8.12 图像工具栏 = 工具注册表 + 用户自定义（`src/ui/ImageToolbar.tsx` 255 行）★★ 最值得拄的 UI 架构

- **工具清单（`ALL_TOOLS`，28 项，就是功能清单本身）**：
  `chat, quick, upscale, cutout, erase, layers, text, angles, animate, move, expand, adjust, crop, vectorize, elements, mockup, to3d, transform, inpaint, style, ocr, effects, smudge, regenerate, remix, copyPrompt, info, preview, export`
- **默认固定 5 个**：`DEFAULT_PINNED = ['chat','quick','upscale','cutout','erase']`；其余进 `More` 二级菜单。
- ★ **新工具自动上浮**（`readPrefs()`）：存 `{ pinned, names, seen }` 到 `localStorage['comfyart.toolbar']`；`seen` = 上次已知的全量工具 id。下次启动时
  `pinned = [...stored.pinned, ...DEFAULT_PINNED.filter(id => !seen.includes(id) && !stored.pinned.includes(id))]`
  → **升级新增的工具会自动出现在老用户工具栏上**（因为不在 seen 里）。很优雅的小设计。
- 工具描述结构：`{ id, icon, label, hint?, run?, menu?, when? }`；`when: !!genInfo?.prompt` 控制 Regenerate/Remix/Copy Prompt 只在有生成信息时出现；最后按 `ALL_TOOLS.indexOf(id)` 固定排序。
- `names` 开关：工具栏显示图标+文字（带快捷键 hint）或仅图标（title 提示）。
- **模式型工具**（需要进入画布交互模式）统一走 `enterMode(mode)`：`quick / erase / text / angles / animate / expand / inpaint / style / move / adjust / effects / smudge / layers / separate`。
- **快捷键 Tab = Quick edit**：`document.addEventListener('keydown', onKey, true)`（捕获阶段），跳过 `input, textarea, [contenteditable="true"]`，`modeAtom.get()` 非空时不响应。
- **裁剪保护规则**（`registerCropRule`）：监听 `instance_page_state`，若 `croppingShapeId` 存在但选区变了（不是那一个）→ `setCroppingShape(null)` + `setCurrentTool('select.idle')` → **用户一点别的就自动退出裁剪**。
- **Fit to artboard**（`fitFrame(cover)`）：`k = (cover ? Math.max : Math.min)(fw/shape.w, fh/shape.h)`，新尺寸居中，`rotation: 0`。菜单项仅在 `editor.getShape(shape.parentId).type === 'frame'` 时出现。
- **Extract Text 的落位**：`createShape(TEXT, { x: b.maxX + 40, y: b.y, props: { text, size:24, autoSize:false, w: b.w } })` + 同时复制到剪贴板。
- **Replace image**（右键菜单）：上传新图 → `toMedia` 拿真实 w/h → `editor.createAssets([...])` 新建 asset → `updateShape({ props: { assetId, h: shape.props.w * m.h / m.w, crop: null } })`（**清 crop**）。
- **Use as reference**：`createGenerator(editor)` 后 `updateShape({ meta: { refs: [src] } })` → 把图当参考图。
- **Send to chat**：`extraRefsAtom.set([...去重, ...ids])` + 若面板 closed 则 open。
- **Add to My Assets**：二级菜单按 `KIND_LABELS` 分类，每类下“新建”+ 已有条目列表；`addToLibrary(editor, kind, target)` 去重追加 srcs。
- **Batch rename**：多选 → `renameAtom.set(ids)`。

## 8.13 右键菜单（`src/ui/CanvasMenu.tsx` 208 行）

- 用 tldraw 的 `DefaultContextMenu` + `TldrawUiMenuGroup/Item/Submenu`，`useActions()` 取内置 action 的 `kbd` 与 `onSelect` → **自定义菜单里直接复用内置动作与快捷键文案**。
- 空选择时：Upload image / Upload video / Paste / Select all / Zoom in / Zoom out / Zoom to fit / Zoom to 100 / **Play artboards fullscreen**（有 frame 时）/ Unlock all（有锁定时）。
- 有选择时分组：剪贴板（copy/cut/paste/duplicate）→ 结构（group/ungroup/remove-frame/flatten-to-image/ca-tidy）→ 层级（bring-to-front/bring-forward/send-backward/send-to-back）→ 变换（flip-h/flip-v/rotate-cw/ca-hide/toggle-lock）→ 非媒体单选时 `flatten-to-image`（转为图片）→ 素材（Send to chat / Add to My Assets）→ 额外（Replace / Use as reference / Flatten / Batch rename）→ 导出（View larger / Download / Export / Export as PNG / Copy as PNG）→ Delete。
- 状态一次算完（`useValue`）：`count / group / frame / media / image / images / ids / locked / frames`。

---

## 8.14 模式系统（`src/ui/mode.ts` 24 行）★ 交互模式的统一开关

```ts
export type Mode = 'quick'|'text'|'erase'|'angles'|'expand'|'animate'|'layers'|'separate'|'adjust'|'effects'|'inpaint'|'style'|'move'|'smudge'
export const OWN_PANEL: Mode[] = ['adjust','effects','inpaint','style','move','smudge']  // 自带面板的模式
export const modeAtom = atom<Mode | null>('mode', null)
export const strokesAtom = atom<Stroke[]>('strokes', [])
export const brushAtom = atom('brush', 40)
export const padsAtom = atom<Pads>('pads', { l:0, t:0, r:0, b:0 })
export const MAX_REGIONS = 6
export const boxesAtom = atom<[number,number,number,number][]>('split boxes', [])
export function enterMode(mode: Mode) { strokesAtom.set([]); padsAtom.set({l:0,t:0,r:0,b:0}); boxesAtom.set([]); modeAtom.set(mode) }
export function exitMode() { modeAtom.set(null) }
```
→ 套路：**进入任何画布编辑模式前清空所有临时输入（笔迹/边距/框）**，避免上一次残留。

---

## 8.15 非破坏性调色完整算法（`src/adjust.ts` 159 行）★ 可直接搬，纯前端无依赖

`Adjust` 16 个参数（全部 0 为中性，范围大致 -100..100）：
`exposure, contrast, highlights, shadows, whites, blacks, saturation, vibrance, temperature, tint, sharpen, clarity, grain, vignette, bloom, glamour`；`NO_ADJUST` 全 0。

**预计算三张模糊图**（用 canvas `ctx.filter = blur(r)` 取像素，无手写卷积）：
- `fine`（锐化）：`radius = max(1, side/900)`
- `wide`（clarity + bloom 共用）：`radius = side/60`
- `soft`（glamour）：`radius = side/200`
其中 `side = max(w,h)`。

**单像素处理顺序（完整公式）**：
1. 锐化（unsharp）：`r += (r - fine[i]) * (sharpen/50)`
2. clarity：`r += (r - wide[i]) * (clarity/150)`
3. glamour（柔光）：`r = r*(1-k) + screen(r, soft[i])*k`，`k = glamour/200`，`screen = 255 - (255-r)*(255-soft)/255`
4. bloom：`r += max(0, wide[i] - 150) * (bloom/100)`（**只加亮部 >150 的部分**）
5. 曝光+对比：`gain = 2 ** (exposure/100)`；`r = (r*gain - 128) * (1 + contrast/100) + 128`
6. 亮度分区：`luma = 0.299r + 0.587g + 0.114b`；`t = clamp(luma/255)`；
   `lift = (luma > 128 ? highlights*(luma-128)/256 : shadows*(128-luma)/256) + whites*0.6*t**3 + blacks*0.6*(1-t)**3`（**三次曲线做白/黑场**）
7. 饱和度：`chroma = (max-min)/255`；`s = (1 + saturation/100) + (vibrance/100)*(1 - min(1, chroma))`（**vibrance 自动保护已饱和像素**）；
   `r = luma + (r-luma)*s + warm`；`g = luma + (g-luma)*s - tint`；`b = luma + (b-luma)*s - warm`（`warm = temperature*0.4`，`tint = tint*0.4`）
8. 暗角：`x = (p%w)/w - 0.5`，`y = floor(p/w)/h - 0.5`；`k = 1 - (vignette/100)*min(1, (x²+y²)*2)`
9. 胶片颗粒：`noise(i) = fract(sin(i*12.9898)*43758.5453) - 0.5`（**廉价伪随机，按像素索引固定** → 预览不闪烁），`+= noise(p)*grain*0.8`
10. `clamp(0,255)` 写回。

**Auto 自动白平衡/曝光**（`autoAdjust`，先缩到 256px 长边采样）：
```ts
exposure  = round(log2(118 / max(mid,1)) * 60)      // mid = 中位亮度，目标 118
contrast  = round((200 / max(hi-lo, 40) - 1) * 40)  // lo/hi = 1% / 99% 分位
blacks    = round(-lo * 1.2)
whites    = round((255 - hi) * 1.2)
temperature = round(((sumBlue - sumRed)/n) * 0.5)
vibrance  = 15
```
`adjustedCanvas(img, a, maxSide)` 先按 `maxSide` 缩放再处理（**预览用小图，应用用大图**）。

---

## 8.16 Compose Board（排版画板 + AI block）完整实现（`src/compose.ts` 105 行）★★ 最值得拄的“排版→成品”闭环

- 常量：`RENDER_SCALE = 2`、`RENDER_LONG = 2048`、`BOARD_SIZE = [1080, 1350]`。
- `isBoard(frame) = !!(frame.meta.compose || frame.meta.elements)` → 普通画板 vs 设计画板，**靠 meta 标志区分**。
- **两段硬编码 prompt（原文可直接用）**：
  - `SEAM_PROMPT`（无 AI block 时，只做接缝融合）："Fix only the seams between the elements of this design: blend edges, contact shadows and lighting so nothing looks pasted on. Every object stays complete with all of its parts, in the same place, size and shape. Keep the background, all text, logos, layout and colors exactly as they are. Do not add or remove anything."
  - `LAYOUT_PROMPT`（有 AI block 时）："Image 1 is a layout. Turn it into one finished, polished design. Fill every flat gray area with new content as described below, blended naturally into the design. Keep all text exactly as written, and keep logos, photos, QR codes, shapes and doodles in the same place, size and color."
- ★ **AI block 用百分比坐标写进 prompt**（`composePrompt`）：
  ```
  Area 1 (left 12%, top 30%, width 40%, height 25%): <description 或 'content that fits the design'>.
  ```
  坐标 = `(block.pageBounds - frame.pageBounds) / frame.w|h * 100` 取整。→ **把画布几何翻译成模型能懂的空间描述**。
- ★ **变更追踪（Changes since last render）**：
  - `signature(shape) = JSON.stringify([type, round(x), round(y), rotation, w, h, text, rich, description, url, assetId, color, fill, font, size, weight])`；
  - `labelOf(shape) = (text || description || url || name || type).slice(0,40)`；
  - 渲染前把 `{ [shapeId]: {sig, label} }` 存进 `frame.meta.rendered`；
  - `changesOf()` 对比：`added` = 不在 before 里；`changed` = sig 不同；`removed` = before 里但 `editor.getShape(id)` 已不存在（用 label 展示）。
- `renderBoard()`：`spawnHolder` → `exportImage([frame.id], { format:'png', background:true, pixelRatio:1, scale: min(RENDER_SCALE, RENDER_LONG/max(w,h)) })` → 上传 → `editGraph(annotated(url), prompt)`。
  → **把画布渲染成图，再当“图 1”丢给图像编辑模型**。
- `createBoard()`：新建 frame，`meta.compose = true`，尺寸 1080×1350，`freeSpot` 定位。
- `addComposeShape(editor, GENBLOCK|QR)`：若选中项是 frame 或其子级，则**居中放到 frame 内**并 `parentId`，否则放视口中心；创建后立即 `setEditingShape(id)`。
- ★ **布局导出/导入（模板复用）**：
  - `exportLayout`：`editor.getContentFromCurrentPage([frame.id])` → 把 `rendered` 字段清空（`rendered: null`）→ `JSON.stringify` → 下载 `<name>.layout.json`；
  - `importLayout`：`editor.putContentOntoCurrentPage(JSON.parse(text), { point: 视口中心, select: true })`。
  → **依赖引擎自带的序列化/反序列化能力**（tldraw 的 `getContentFromCurrentPage` / `putContentOntoCurrentPage`）。我们自己的画布必须有等价的“选区→JSON→插回”能力。

---

## 8.17 PSD 导出（`src/psd.ts` 70 行）★ 真图层，不是平展

- `MAX_SIDE = 8000`。
- **分辨率策略**：`density = max(1, ...每个 shape 的 asset.props.w / pageBounds.w)`（即取选中图层里**最高的原始像素密度**），`scale = min(density, MAX_SIDE / max(bounds.w, bounds.h))`。
- 图层顺序：`editor.getCurrentPageShapesSorted().filter(选中)` → 按 z 序。
- 每层：无旋转且有 asset → 直接 `drawImage` 原图（**不重采样**）；否则 `editor.toImage([id], { format:'png', scale, background:false, padding:0 })`。
- 层信息：`{ name: asset.props.name || shape.type, left: (b.x - bounds.x)*scale, top: (b.y - bounds.y)*scale, canvas }`。
- `merged` canvas 按顺序叠一遍作 PSD 预览图 → `writePsd({ width, height, children, canvas: merged })`（ag-psd）→ `download(blob, 'name.psd')`，MIME `image/vnd.adobe.photoshop`。
- 通用下载工具（同文件，全项目共用）：`download(blob, name)` 用 `<a download>` + `setTimeout(() => URL.revokeObjectURL, 1000)`；`downloadUrl(src, name)` = `fetch(src).blob()` 再 download。

---

## 8.18 PPTX 导出（`src/pptx.ts` 140 行）★ 可编辑文字/形状，不是贴图

- `SLIDE_W = 13.333`（16:9 英寸）、`PT_PER_IN = 72`、`CONTAINERS = ['frame','group']`。
- **分页规则** `slidesOf`：选中项里的 frame 按 (y,x) 排序，**每个 frame 一页**；没有 frame 则整组一页。
- `leaves(editor, ids)`：递归展平，**跳过 `meta.hidden` 的 shape**，容器只展开不输出。
- 缩放：`slideH = SLIDE_W * first.h / first.w`（**幻灯片高度由第一组比例决定**）；每组 `k = min(SLIDE_W/w, slideH/h)`，居中偏移 `ox/oy`。
- ★ **按 shape 类型选导出形式**（`addObject`）：
  - 有旋转 → 栅格化（PPTX 不支持旋转 shape 的简单放置）；
  - image 且无 crop 且无 flip → **直接塞原图字节**（`fetch(src).blob()` → dataURL），有 crop/flip 才栅格化；
  - `TEXT` → `slide.addText(p.rich ? textRuns(p.rich, k) : p.text, { fontFace, fontSize: p.size*k*72, color, bold: weight>=600, align, valign:'top', margin:0, fit:'none' })`；
  - `note` → `addText` + 从 tldraw 主题取 noteFill/noteText 颜色；
  - `VECTOR` 且 `kind in {rect,ellipse}` 且 `fillType==='solid' && dash==='solid' && blend==='normal'` → `slide.addShape(rect|roundRect|ellipse, { fill:{color,transparency}, line:{color,transparency,width:strokeWidth*k*72}, rectRadius: min(0.5, radius/min(w,h)) })`；
  - 其余 → 栅格化。
- ★ **富文本 → PPTX text runs**（`textRuns`）：逐段落逐节点，从 `marks` 里取 `textStyle.attrs`（color/fontFamily/fontSize），`bold/italic/underline` 各自映射，最后一个节点且非末段 → `breakLine: true`；`hardBreak` → `'\n'`；`fontSize = size * k * 72`。
- 颜色工具：`hex(c) = c.replace('#','').slice(0,6).toUpperCase()`；`transparency(c) = round((1 - alphaOf(c)) * 100)`（`alphaOf` 来自 `src/palette.ts`）。
- 不可编辑模式：整组 `editor.toImage(group, { format:'png', scale, background:true })` 当图片铺到居中位置。

---

## 8.19 非破坏性像素替换（`src/imageOps.ts` 69 行）★ 图层“原始图”追踪机制

- `shape.meta.original: string` = **首次修改前的 src**；`shape.meta.adjusted: { source: TLAssetId, adjust: Adjust }` = 当前图来自哪个源 asset + 参数。
- `sourceAsset(editor, shape)`：优先取 `meta.adjusted.source` 的 asset，否则取当前 asset → **永远能回溯到未调色的原图**。
- `applyAdjust(editor, shape, adjust, render)`：
  - `adjust === null` → 直接把 `props.assetId` 换回 source asset（**零成本撤销**）；
  - 否则 `uploadCanvas(render())` 上传新图并新建 asset，写 `meta.adjusted = { source: source.id, adjust }`。
- `replacePixels`：上传 canvas → `setSource(..., original: originalOf(current) ?? asset.props.src)` → **保留最早的原图**。
- `setSource` 统一做：新建 asset + `updateShape({ props: { assetId, crop: null }, meta: { adjusted: null, original } })`（**换像素必清裁剪**）。
- `restoreOriginal`：读 `meta.original` 重建 asset 并清空 `meta.original`。
- 文件名清洗：`.replace(/(\.png)+$/, '.png')`（**防叠加多次 .png 后缀**）。

---

<a id="sec10"></a>
## 10. Agent ↔ 画布命令总线（`src/botCommands.ts` 459 行）★★★ 可直接对标我们 `canvas-agent-tui.js`

### 10.1 机制

- 后端通过 ws 事件 `comfyart-mcp-command` 推 `{ id, action, project, chat?, args }` 到浏览器；
- `registerCommandBus(editor, project, isStale, onError)`：
  - `if ((msg.project && msg.project !== project) || isStale()) return` → **项目隔离 + 过期 tab 不执行**；
  - 执行完 `POST /comfyart/mcp_command_result` 带 `{ command_id, ok, result | error }`；
  - 未知 action 回 `unknown command ${action}`；
  - 整个 handler 包在 `Promise.resolve().then(...)` 里 → **异常统一下回 error，不会断总线**。
- 常量：`MAX_SHAPES = 300`（get_canvas 上限，超出回 `truncated: true`）、`VIEW_MAX_PX = 1024`、`PAGE_TEXT_CAP = 3000`、`GAP = 40`。

### 10.2 `get_canvas` 返回结构（★ Agent 看画布的数据契约）

```ts
{ project, pages: [{id,name,current}], page, shapes: [...], truncated?, selection, brand_kit: {id,name}|null }
```
每个 shape 的 `describe()`：
```ts
{ id, type, name?, x, y, width, height, frame?, url?, pixels?: 'WxH',
  prompt?: props.prompt.slice(0,300), page_text?: meta.pageText.slice(0,3000) }
```
- `type` 特殊值：generator 且 `meta.job` 存在 → **`'generating'`**（Agent 能看出“还在跑”）。
- `page_text` 来自 `meta.pageText`（**PDF 每页的文本存在 shape.meta**）→ Agent 能读 PDF 内容。
- 过滤掉 COMMENT shape。

### 10.3 `view_canvas`（★ 让 Agent “看”画布）

不传 ids = 整页；`maxPx = min(2048, args.max_px || 1024)`；`scale = min(1, maxPx / max(bounds.w, bounds.h))`；
返回 `{ width, height, bounds, png: base64 }`。
`blobBase64` 分块转码避免爆栈：`for (let i=0; i<bytes.length; i+=0x8000) s += String.fromCharCode(...bytes.subarray(i, i+0x8000))` 再 `btoa(s)`。

### 10.4 异步任务给 Agent 的契约（★ 关键设计）

- 所有生成/编辑工具**立即返回** `{ ids: [holderId], note: 'call wait_generation with these ids' }`；
- `started(editor, holder)` 顺手把 `meta.quiet = true`（**Agent 触发的任务不自动抢选区**）；
- `wait_generation(ids, timeout_s=60)` 返回：
  ```ts
  { status: 'running'|'done', results: [{ id, status: 'running'|'done'|'error'|'unknown id', urls?, shapes?, error? }] }
  ```
  `shapes` = 用 `shapesWithSrc()` 反查“当前页上 src 等于结果图的所有 shape id”；
  实现：`react('wait generation', cb)` 订阅 atom 变化 + `queueMicrotask(finish)` + `setTimeout(finish, timeout*1000)`，`done` 标志防重复 resolve。

### 10.5 完整命令清单（= Agent 能做的所有画布操作）

★ **已逐行核实：`const HANDLERS: Record<string, Handler> = {` 位于 `src/botCommands.ts:222`，共 40 个命令**（按源码顺序）：
`get_canvas`、`view_canvas`、`list_models`、`brand_kits`、`generate_image`、`generate_video`、`edit_image`、`animate_image`、`upscale_image`、`remove_background`、`expand_image`、`split_layers`、`change_angle`、`replace_text`、`edit_region`、`erase_region`、`move_object`、`style_transfer`、`vectorize_image`、`extend_video`、`upscale_video`、`remove_video_background`、`generate_music`、`generate_sound_effect`、`text_to_speech`、`edit_elements`、`create_mockup`、`place_design`、`image_to_3d`、`wait_generation`、`place_html`、`create_slides`、`add_text`、`add_shape`、`create_frame`、`update_shapes`、`delete_shapes`、`focus`、`memory`、`skill`。

**看**：`get_canvas`、`view_canvas`、`list_models`（返回 `{id,label,desc,accepts_refs,ratios}` / 视频额外 `max_reference_images|videos|audios, motion_control, lip_sync, durations`）、`brand_kits`（`{linked, items:[{id,kind,name,summary,images}]}`）。
**生成**：`generate_image`、`generate_video`。
**图像**：`edit_image`、`animate_image`（`loop !== false`）、`upscale_image`（`factor 2|4`，`creative` 时带 style + 强度 0.3）、`remove_background`、`expand_image`（`left/top/right/bottom` 像素）、`split_layers`（**层数 clamp 到 2..8，默认 4**）、`change_angle`（`horizontal/vertical/zoom`）、`replace_text`（`[[from,to]]`）、`edit_region`、`erase_region`（`{x,y,width,height}` 图像像素）、`move_object`（**二选一：`object` 文本描述 或 `x/y` 图像像素点** + `dx/dy`）、`style_transfer`（`style` 文本或 `ref` 图）、`vectorize_image`。
**视频**：`extend_video`、`upscale_video`（`target` 从 `VIDEO_TARGETS` 查）、`remove_video_background`。
**其媒体**：`generate_music`（`seconds` clamp 5..240）、`generate_sound_effect`（clamp 1..47）、`text_to_speech`（三模式：`reference` 需 audio shape / `voice` 必须在 `VOICES` / 默认第一个 voice）、`image_to_3d`。
**设计**：`edit_elements`、`create_mockup`、`place_design`（`x,y,scale,rotation(度→弧度),warp`，`bake` 则回 `{image}`）。
**排版**：`create_slides`（`slides: [{title,bullets[],image}]`）、`add_text`、`add_shape`（`kind ∈ rect|ellipse|polygon|star`）、`create_frame`（默认 1080×1080，可带 `children` 直接 reparent）、`update_shapes`（批量）、`delete_shapes`、`focus`、`place_html`。
**记忆/技能**：`memory`（`list|add|correct|forget`）、`skill`（`list|read|save`，`read` 可带 `path` 读 skill 附件）。

### 10.6 错误信息写法★ 给 Agent 的报错必须带下一步

- `no shape ${id} — use ids from get_canvas`
- `${id} is a ${type}, not an image`
- `unknown model ${model} — see list_models`
- `${model.label} does not accept refs; use a model with accepts_refs from list_models, or drop refs`
- `ratio must be one of ${ratios.join(', ')}`
- `give object (what to move) or x/y (a point on it, image pixels)`
- `${motion_video} needs first_frame: the image of the person`
- `the user turned memory off; do not read or save memories`
- `skill ${name} has no file ${path}; files: ${keys}`
→ **每条错误都直接告诉 Agent 正确的参数来源或候选值**，这是 Agent 能自我修正的关键。

### 10.7 其他细节

- `place(editor, id, args, chat)` 三优先级：`args.frame`（reparent + 放 (GAP,GAP)）> `args.near`（放目标右侧 `maxX + GAP`）> `chat`（放进会话画板）。
- `update_shapes` 全部包在**一个 `editor.run()`** 里 → 批量更新只产生一条撤销；坐标用 `getPointInParentSpace` 做页面→父坐标换算（**Agent 传页面坐标，内部转局部坐标**）；`rotation` 接受**度数**并转弧度；`opacity` 直接写。
- `create_slides` 用 **diff 前后 shape id 集合** 找出新建的 frame：`[...ids].filter(id => !before.has(id) && type==='frame')` → 无需额外记录。

---

<a id="sec12"></a>
## 12. 矢量 / 钢笔 / 节点编辑 / 布尔（完整实现）

### 12.1 路径存储与缩放（`src/vectorPath.ts` 54 行）

- `interface PenNode { x: number; y: number; hx: number; hy: number }`（hx/hy = **相对于节点的手柄偏移**）。
- `round = v => Math.round(v * 1e5) / 1e5`；`MIN_SIZE = 1e-6`；`PAIRED = 'MLCSQTmlcsqt'`。
- ★ `scalePath(d, w, h)`：无依赖的 pathData 缩放器。
  - tokenizer：`d.match(/[a-zA-Z]|-?\d*\.?\d+(?:e[-+]?\d+)?/g)`（**支持科学计数法**）；
  - 遇字母就重置 `index = 0`；数字的轴：`PAIRED.includes(cmd) ? index++ % 2 : (cmd==='H'||cmd==='h' ? 0 : 1)` → **H 只缩 x，V 只缩 y，其余按奇偶交替**。
- ★ `nodesToPath(nodes, closed)`：包围盒含手柄（`x, x+hx, x-hx` 全入 xs），`w/h = max(1, ...)`，坐标归一化到 0..1；
  贝塞尔段：`C (a.x+a.hx) (a.y+a.hy) (b.x-b.hx) (b.y-b.hy) (b.x) (b.y)`；闭合时补 `seg(last, first)` + `Z`；
  返回 `{ x, y, w, h, d }` → **直接用作 shape 的 x/y/w/h**。
- `loadPaper()`：`loaded ??= import('paper/dist/paper-core').then(({default: paper}) => { paper.setup(new paper.Size(1,1)); return paper })` → **懒加载单例，无 DOM canvas**。
- `normalized(paper, item)`：`clone({insert:false})` → `translate(-b.x,-b.y)` → `scale(1/w, 1/h, (0,0))` → `{x,y,w,h,d: copy.pathData}`。

### 12.2 矢量 shape（`src/shapes/VectorShape.tsx` 232 行）

- 类型名 `VECTOR = 'ca-shape'`；`VectorKind = 'rect'|'ellipse'|'polygon'|'star'`（+ `kind: 'path'`）。
- props 16 个：`w,h,kind,fill,radius,sides,fillType,stops,image,blend,angle,stroke,strokeWidth,strokeAlign,dash,d`。
- 迁移序列：`AddStrokeGradient:1`（加 fill2/angle/stroke/strokeWidth/strokeAlign/dash）→ `AddPaint:2`（`stops = [{0,fill},{1,fill2}]`，**删掉 fill2**）→ `AddPath:3`（`d = ''`）。★ 字段演进的真实例子。
- `BLEND_MODES`（15 个，直接当 CSS `mixBlendMode`）：normal, multiply, screen, overlay, darken, lighten, color-dodge, color-burn, hard-light, soft-light, difference, exclusion, hue, saturation, color, luminosity。
- `polygonPoints(kind, w, h, sides)`：星形 `n = sides*2`，奇数索引半径 `0.45`；起点 `-PI/2`（顶点朝上）；椭圆参数方程用 `w/2`/`h/2` 作半径。
- 默认 props：`w:320, h:200, kind:'rect', fill:'#CCCCCC', radius:0, sides:5, fillType:'solid', stops:[#CCCCCC→#FFFFFF], image:'', blend:'normal', angle:90, stroke:'#333333', strokeWidth:2, strokeAlign:'center', dash:'solid', d:''`。
- `canEdit(shape) = shape.props.kind === 'path'` → **只有钢笔路径能进节点编辑**。
- ★ **描边对齐的实现**（纯 SVG，无额外库）：
  - `sw = stroke ? strokeWidth * (strokeAlign === 'center' ? 1 : 2) : 0`（**内外描边靠把线宽翻倍 + 裁剪实现**）；
  - `inside` → 定义 `clipPath`，**先画描边（被裁剪）再画填充**；
  - `outside` → **先画填充再画描边最后再盖一层填充**（描边只露外半）；
  - `center` → 只画一次描边。
- 虚线：`dashed → strokeDasharray = "${sw*3} ${sw*2}"`；`dotted → "0 ${sw*2}"` + `strokeLinecap='round'`。
- 渐变：`linearGradient` 坐标由 angle 算：`x1 = 0.5 - cos(rad)/2, y1 = 0.5 - sin(rad)/2, x2 = 0.5 + cos(rad)/2, y2 = 0.5 + sin(rad)/2`；`stops` 渲染前按 offset 排序。
- 图片填充：`<pattern patternUnits="userSpaceOnUse" width=w height=h><image href preserveAspectRatio="xMidYMid slice"/></pattern>`。
- 路径用 `fillRule="evenodd"`（**布尔运算后的孔洞能正确显示**）。
- `getIndicatorPath`：path 类型用 `new Path2D(scalePath(d,w,h))` → **选中框贴合轮廓**。
- `VectorTool`（StateNode，id = `ca-shape`）：`onPointerDown` 先建 `w:1,h:1` 的草稿 shape → `onPointerMove` 拖拽时 `track()` → `onPointerUp` 若 `max(w,h) < MIN_DRAG(4)` 则当点击处理，给默认尺寸（rect 用 `RECT_H=200`，其他 `CLICK_W=320`）并居中到点击点；Shift 锁正方形 `w = h = max(w,h)`；反向拖拽用 `x: p.x < x ? x - w : x`。

### 12.3 钢笔工具（`src/shapes/PenTool.ts` 84 行）

- `PEN = 'ca-pen'`；`CLOSE_PX = 10`；`OPEN_FILL = '#00000000'`；`CLOSED_FILL = '#CCCCCC'`。
- **边画边更新同一个 shape**：`sync(closed)` 把 `nodesToPath` 结果直接写进 shape 的 x/y/w/h/d；首次创建时 `kind:'path', fill: OPEN_FILL, stroke:'#333333', strokeWidth:2`。
- 闭合判定：`Math.hypot(p.x - first.x, p.y - first.y) * editor.getZoomLevel() < CLOSE_PX` → **屏幕像素阈值，不随缩放变难**。
- 拖拽手柄：`onPointerDown` 后 `dragging = true`；`onPointerMove` 设 `hx = p.x - last.x, hy = p.y - last.y`（**从节点往外拖 = 出手柄**）。
- 结束方式：双击（`nodes.pop()` 去掉多余点）/ Enter / Cancel → `finish(false)`；回到首点 → `finish(true)` 闭合。
- `commit`：`nodes.length >= 2` 才保留，否则删掉草稿 shape。

### 12.4 节点编辑（`src/ui/PathEditor.tsx` 77 行）

- 只在 `editor.getEditingShapeId()` 指向 `kind === 'path'` 的 VECTOR 时渲染；`useValue('camera', () => editor.getCamera())` **订阅相机以在缩放/平移时重画控制点**。
- 坐标三层换算：
  - 页面→屏幕：`editor.pageToViewport(xf.applyToPoint({ x: p.x * w, y: p.y * h }))`（归一化→shape→页面→视口）；
  - 屏幕→归一化：`editor.screenToPage(clientX,clientY)` → `xf.clone().invert().applyToPoint(page)` → 除 `w/h`。
- 拖拽：`drag.current = { item: paper.CompoundPath, seg: paper.Segment, part: 'point'|'in'|'out' }`；
  `point` → `seg.point = p`；`in` → `seg.handleIn = p.subtract(seg.point)`；`out` → `seg.handleOut = p.subtract(seg.point)`；
  **每次 move 直接 `updateShape({ props: { d: item.pathData } })`**（paper 对象存在 ref 里作为拖拽期间的真相源）。
- `end()`：`normalized()` 重算包围盒 → 同时更新 shape 的 x/y/w/h/d（**路径变大时 shape 自动长大**）。
- 手柄为 0 时不渲染控制线（`seg.handleIn.isZero()`）。

### 12.5 布尔运算（`src/booleans.ts` 39 行）

- `itemOf`：按 kind 构造 paper 图元（rect 用 `Path.Rectangle({radius: min(radius, w/2, h/2)})`；path 用 `CompoundPath({pathData: scalePath(d,w,h)})`；polygon/star 用 `Path({segments: polygonPoints(...), closed:true})`），全部 `insert:false`；
  然后 `item.transform(new paper.Matrix(m.a,m.b,m.c,m.d,m.e,m.f))` 套用 `editor.getShapePageTransform(shape.id)` → **按页面真实变换（含旋转/缩放/父级）做布尔**。
- 按 z 序取形状：`editor.getCurrentPageShapesSorted().filter(...)` → `shapes.slice(1).reduce((acc, s) => acc[op](itemOf(...), {insert:false}), itemOf(shapes[0]))`。
- 空结果：`if (result.isEmpty()) throw new Error('the result is empty')`。
- 写回：整个包在**一个 `editor.run()`** 里（删原图 + 建新 shape）→ 一次撤销；新 shape `parentId: base.parentId`、`index: base.index`（**保持层级**）、`props: { ...base.props, kind:'path', w:n.w, h:n.h, d:n.d }`（**样式继承最底层**）。

---

<a id="sec13"></a>
## 13. 评论系统完整实现（`src/shapes/CommentShape.tsx` 193 行）

- `COMMENT = 'comment'`；`CommentKind = 'point'|'lasso'|'box'`；`COMMENT_SIZE = 32`；`MIN_DRAG = 6`。
- props：`{ w, h, kind, path, text, done }`；`path` = `"u,v u,v ..."`（**归一化 4 位小数**）。
- meta：`meta.mark = { image: TLShapeId, src: string }`（SAM 掩膜图）、`meta.points = [x,y][]`（Shift 追加的点，**最多 9 个**）。
- ★ **反向缩放（constant size）**：`const s = 1 / zoom`，所有装饰元素 `transform: scale(s)`、`strokeWidth: 1.5 * s`、`strokeDasharray: ${4*s} ${3*s}` → **评论图钉在任何缩放下屏幕尺寸恒定**。
- ★ **掩膜可视化**：选中带 mark 的评论时，叠一个 `div.ca-comment__mark`，位置 = 目标图 pageBounds 相对评论的偏移，`maskImage: url(mark.src)` → **用 CSS mask 把 SAM 掩膜直接涂在图上**，零 canvas 开销。
- 轮廓：`box` → `<rect>`；`lasso` → `<polygon>`（path 反归一化 `u*w, v*h`）；`point` → 无轮廓。
- ShapeUtil 限制：`canEdit/canResize = false`，`hideSelectionBoundsFg / hideResizeHandles / hideRotateHandle = true` → **评论不可缩放不可旋转**。
- `CommentTool`：
  - `Shift + 已选中 point 评论` → 往 `meta.points` 追加（并清 `mark: null`）；
  - 点到已有评论 → 直接选中（**不新建**）；
  - 否则在 `p - POINT_SIZE/2` 建评论；
  - `track()`：lasso 累积所有点，box 只用首尾两点；重算包围盒后把点归一化写进 `path`；
  - `onPointerUp`：拖得太小（`max(w,h) < MIN_DRAG`）则**降级为 point 评论**。

---

<a id="sec14"></a>
## 14. 模式面板（`src/ui/ModePanel.tsx` 279 行）★ 一个组件承载 8 种图像工具

- 一个 `ModePanel` 根据 `modeAtom` 渲染不同表单；`OWN_PANEL` 里的模式（adjust/effects/inpaint/style/move/smudge）直接 `return null`（它们有自己的面板）。
- 常量：`GAP = 12`、`EDGE = 8`、`PROMPT_MODES = ['quick']`（只有 quick 把面板放**图片下方**，其他放**右侧**）、`EXPAND_LIMIT = 4096`、`MAX_REGIONS = 6`。
- **模式标题表** `TITLES`：quick=Quick edit / text=Edit text / erase=Eraser / angles=Multi angle / expand=Expand / animate=Animate image / layers=Split layers / separate=Separate regions。
- ★ **扩图比例预设（带平台名，可直接拄）**：
  - General：Free(0) / Original(-1) / Square(1) / Landscape(16/9) / Portrait(9/16) / 4:3 / 3:4
  - Social media：Post(4/5 Instagram) / Story(9/16) / Cover photo(820/312) / Profile picture(1) / Thumbnail(16/9)
  - `0 = 自由`、`-1 = 原比例`（哨兵值约定）。
- ★ **扩图边距计算 `fitPads(r, k)`**（选比例/缩放时自动算四周像素）：
  ```ts
  const target = r < 0 ? iw / ih : r
  const tw = Math.max(iw, ih * target) * k
  const th = Math.max(ih, iw / target) * k
  const px = Math.round((tw - iw) / 2), py = Math.round((th - ih) / 2)
  padsAtom.set({ l: px, r: px, t: py, b: py })
  ```
  超 `EXPAND_LIMIT` 时 `canSubmit = false` 并提示。
- **多角度预设**（`ANGLE_PRESETS`，h/v/z）：Front{0,0} FrontLeft{315,0} FrontRight{45,0} Left{270,0} Right{90,0} Back{180,0} Top{v:50} Low angle{v:-25} Close-up{z:8} Medium{z:5} Wide{z:1}；
  滑杆：horizontal 0..360、vertical -30..60、distance 0..10 step 0.1；
  **`Generate 4 angles at once`** = `for (const h of [315,45,270,90]) actions.angles(...)` → 一次发 4 个任务。
- **Edit text 模式**：进模式自动跑 OCR（`ocrLines(src)`），成功则渲染**每行一个 input**，只提交改动过的行（`textEdits()` 对比原行）；OCR 失败/无文字则降级为手填 `from` + `to`；OCR 进度实时显示（`ocrStatusAtom[asset.src]`：queued/loading/agent/reading+tokens）。
- **canSubmit 按模式不通过**：quick 需 prompt；text 需有改动行（或 from+prompt）；erase 需有笔迹；separate 需有框；expand 需 pads 总和 > 0 且不超限。
- **自动退出**：`useEffect(() => { if (shape) return; exitMode() }, [shape])` → **选中的图没了就退出模式**；`Escape` 用捕获阶段监听退出。
- **进模式自动重置 + 自动聚焦**：`useEffect(..., [mode])` 清 prompt/from/ratio/scale，`requestAnimationFrame(() => focus.current?.focus())`。
- **面板定位**：quick 放图下方（放不下则翻到上方，底部预留 72px）；其他放图右侧，**右侧不够就放左侧**，并用 `s = (rect.r - rect.l) / iw` 把 expand 的 pads 换算成屏幕像素再避让。
- **快捷键**：`Enter`（非 Shift）提交；所有 input 的 keydown 都 `e.stopPropagation()` 防泄到画布。
- **erase 模式自带 undo**：`strokesAtom.set(strokes.slice(0, -1))`（**只撤销笔迹，不碰画布历史**），brush 滑杆 4..160。
- **layers 模式二合一**：有框 → `regionSplit`（按框拆）；无框 → `splitLayers(layers 2..8, prompt)`。

---

<a id="sec15"></a>
## 15. 裁剪（`src/ui/CropBar.tsx` 149 行）★ 分数坐标裁剪数学

- 裁剪状态用引擎原生 `editor.getCroppingShapeId()` / `setCroppingShape(null)`，**不自己写裁剪交互**。
- `interface Frac { x, y, w, h }` = 裁剪框在**原图上的归一化矩形**；
  `fracOf(shape)` 从 `props.crop = { topLeft, bottomRight }` 换算，无 crop 时 `{0,0,1,1}`。
- ★ `setFrac` 核心数学（**改 crop 同时保持图像位置不变**）：
  ```ts
  const [fullW, fullH] = [shape.props.w / cur.w, shape.props.h / cur.h]   // 反推完整图尺寸
  const off = Vec.Rot({ x: (next.x - cur.x) * fullW, y: (next.y - cur.y) * fullH }, shape.rotation)  // 旋转补偿
  editor.updateShape({ x: shape.x + off.x, y: shape.y + off.y,
    props: { w: fullW * next.w, h: fullH * next.h,
             crop: { topLeft: {x: next.x, y: next.y}, bottomRight: {x: next.x + next.w, y: next.y + next.h} } } })
  ```
  → **裁剪 = 改 crop 分数 + 改 shape 尺寸 + 按旋转角平移位置**。
- `centered(cur, w, h)`：以当前裁剪框中心为锚算新框，`clamp(v, size) = min(max(v,0), 1-size)`。
- **预设（带平台提示）**：free(0) / original(-1) / 1:1(Instagram) / 4:5(Instagram) / 3:4(小红书) / 9:16(Story·TikTok) / 16:9(YouTube) / 2:3(Pinterest) / 3:2 / 1.91:1(Facebook·X)。
- `apply(id)`：`original` → `{0,0,1,1}`；否则 `w = min(iw, ih*r)` → `centered(cur, w/iw, w/r/ih)`（**以当前中心居中**）。
- ★ **按像素输入**：`setW(v)` 在锁定比例时 `resize(min(v, iw, ih*ratio), min(v, iw, ih*ratio)/ratio)`；`setH(v)` 同理→ **W/H 输入框自动保持比例且 clamp 到原图尺寸**。
- `PxInput`：本地 text state，`onBlur`/Enter 才 commit，非法值回滚显示（**避免输入中跳动**）。
- 当前裁剪尺寸显示：`[pw, ph] = [round(cur.w*iw), round(cur.h*ih)]`。
- 切换裁剪对象时重置 preset 为 free（`useEffect(..., [shape?.id])`）。

---

<a id="sec17"></a>
## 17. 浏览器内视频编辑器（`src/videoEdit.ts` 246 行 + `src/ui/VideoEditor.tsx` 306 行）★★★ 纯前端、零后端、可直接搬

### 17.1 数据模型（`videoEdit.ts`）

```ts
interface Clip { start: number; end: number; speed: number; volume: number }   // start/end = 源视频时间
 type TransitionType = 'none' | 'fade' | 'black' | 'white'
interface Transition { type: TransitionType; duration: number }
interface Subtitle { id: string; start: number; end: number; text: string; size: number; color: string }
interface Edit { clips: Clip[]; transitions: Transition[]; subtitles: Subtitle[] }
const MIN_CLIP = 0.2; const DRIFT = 0.25
clipLength(c) = (c.end - c.start) / c.speed
freshEdit(duration) = { clips: [{ start:0, end:duration, speed:1, volume:1 }], transitions: [], subtitles: [] }
```
→ **剪辑完全用“描述 + 实时采样”而非真剪辑**，导出时才真渲染。

### 17.2 时间轴布局与采样

- `layout(edit)` → `{ offsets, total }`：累加每段长度；**fade 转场会重叠**：`t -= min(tr.duration, 前段长, 后段长)`。
- `sample(edit, t)` → `Frame { layers: [{clip, src, alpha}][], dip: {color, alpha} | null }`：
  - `src = c.start + local * c.speed`（**源时间 = 起点 + 本地时间×速度**）；
  - fade：进入下一段的前 `prev.duration` 内 `alpha = local / prev.duration`；
  - black/white：用 **dip（满屏纯色遮罩）**，前半段 `alpha = (local - (len - half)) / half` 渐入，后半段 `1 - local/half` 渐出，`half = duration/2`；
  - `layers.slice(-2)` → **最多同时两层**（所以只需 2 个 video 元素）。
- `splitAt(edit, t)`：找包含 t 的段（要求距两端 > `MIN_CLIP`）→ `cut = c.start + (t - offsets[i]) * c.speed` → 拆两段 + 插入 `{type:'none', duration:0.5}` 转场。
- `removeClip(edit, i)`：`clips.length < 2` 时拒绝；同时删对应转场 `transitions.splice(min(i, len-1), 1)`。

### 17.3 胶片条缩略图（`filmstrip`）

`<video muted preload=auto crossOrigin=anonymous>` → `onloadedmetadata` 后循环 `count` 次：
`v.currentTime = ((i + 0.5) * duration) / count` → `await new Promise(r => v.addEventListener('seeked', r, {once:true}))` → `drawImage` → `toDataURL('image/jpeg', 0.7)`。
最后 `v.removeAttribute('src'); v.load()` 释放解码器。调用：`filmstrip(src, THUMBS=24, 64)`。

### 17.4 播放器（`Player` 类）★ 双 video + WebAudio + canvas 录制

- **两个 `<video>` 元素**（`this.videos = [0,1].map(...)`）交替承载两层，索引用 `l.clip % 2`。
- **音频链**：`AudioContext` → 每个 video `createMediaElementSource(v).connect(gain)` → gain 同时接 `audio.destination`（监听）**和** `createMediaStreamDestination()`（录制）。
- `sync(frame, playing)`：
  - `v.playbackRate = clip.speed`；
  - `gain.value = clip.volume * l.alpha * (dip ? 1 - dip.alpha : 1)`（**转场时音量跟着淡**）；
  - **漂移修正**：`if (Math.abs(v.currentTime - l.src) > DRIFT(0.25) || !playing) v.currentTime = l.src`；
  - 未使用的 video 直接 `pause()` + `gain = 0`。
- `draw(frame, t)`：canvas 先铺 `#000` → 逐层 `globalAlpha = l.alpha` `drawImage` → dip 纯色遮罩 → `drawSubtitles`。
- `drawSubtitles`：`size = (s.size / 1080) * height`（**以 1080p 为基准缩放字号**）；`font = 600 ${size}px Inter, "Microsoft YaHei", sans-serif`；居中、`textBaseline='bottom'`、`lineJoin='round'`、`lineWidth = size/6`、`strokeStyle='rgba(0,0,0,.75)'` 先描边再填色；位置 `y = height * 0.92`。
- `play()`：用 `performance.now()` 做主时钟（`origin = now - time*1000`），`requestAnimationFrame` 里 `time = (now - origin)/1000` → **不依赖 video 自身时间轴**；到 `total` 自动 pause + `onEnd()`。
- `seek(t)`：clamp 到 `[0, total]` → `sync(frame,false)` → `await` 所有 `seeked` → `draw` → `onTime`。
- `record()`：
  ```ts
  const stream = new MediaStream([...canvas.captureStream(30).getVideoTracks(), ...out.stream.getAudioTracks()])
  const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9,opus', videoBitsPerSecond: 8_000_000 })
  recorder.start(250); await new Promise(resolve => this.play(resolve)); recorder.stop()
  ```
  → **实时渲染一遍导出 webm（30fps / 8Mbps）**，无服务端。

### 17.5 编辑器 UI（`VideoEditor.tsx`）

- 入口：`videoEditorAtom.set(shape.id)`（视频工具栏的 Crop Video）。
- 常量：`SPEEDS = [0.5,1,1.5,2]`；`TRANSITIONS = [none, fade, black, white]`；`THUMBS = 24`；`UNDO = 50`；`MAX_SIDE = 1920`。
- ★ **独立撤销栈**（不污染画布历史）：`history = useRef<{past: Edit[]; future: Edit[]}>`，`commit(next, record=true)` 里 `past = [...past.slice(1 - UNDO), current]`（**上限 50**）；拖拽手柄时**拖完才入栈一次**（pointerup 里 push 拖前的 `edit`）。
- 时间轴：`pxPerSec = track.clientWidth / max(total, 0.1)`；clip 块 `left = offsets[i]*pxPerSec`、`width = clipLength(c)*pxPerSec`；缩略图切片 `from = floor(c.start/duration*THUMBS)`、`to = max(from+1, ceil(c.end/duration*THUMBS))`。
- 转场按钮定位在 `offsets[i+1]*pxPerSec`；字幕块独立一行；playhead `left = time*pxPerSec`。
- 裁剪手柄 `trim(i, side)`：`delta = (dx / pxPerSec) * base.speed`（**像素→秒→源时间，乘速度**），clamp 到 `[0, end-MIN_CLIP]` / `[start+MIN_CLIP, duration]`；用 `setPointerCapture` + 在手柄元素上 `addEventListener('pointermove')`。
- 选中态三类型：`{kind:'clip',index} | {kind:'transition',index} | {kind:'subtitle',id} | null`；侧边面板按选中类型渲染不同表单。
- 字幕默认：`{ id: uuid().slice(0,8), start: time, end: min(total, time+2), text: '在此输入字幕', size: 56, color: '#FFFFFF' }`。
- 转场时长滑杆 `min=2 max=10 value=round(duration*10)` → **0.2s..2.0s 步进 0.1**；“Apply to all transitions” 一键同化。
- 导出二选一：**Export as new video**（`placeMedia` 放右侧 `b.maxX + 40`）/ **Replace original video**（新建 video asset + `updateShape({props:{assetId}})`，`mimeType:'video/webm'`, `isAnimated:true`）。
- 快捷键：`Escape` 关闭、`Space` 播放/暂停（捕获阶段，跳过 input/textarea）。
- 时间显示 `fmt(t) = MM:SS.s`。

### 17.6 视频工具栏（`VideoToolbar.tsx` 145 行）

- 按钮：Crop Video（打开编辑器）/ Extend（内联面板：prompt + `+5s/+10s/+15s` chip）/ Upscale 弹层 / Remove background / Remix（有 `meta.gen.prompt` 时）/ Download。
- ★ **Upscale 菜单里预先算目标分辨率并禁用超标的**：
  ```ts
  const over = Math.max(asset.props.w, asset.props.h) >= long
  显示：${round(w*long/max(w,h))}×${round(h*long/max(w,h))}
  ```
  已超标时 `disabled` + 提示“原视频已超过”。
- 面板定位：`top = rect.b + 12`，若超出 `host.height - 72` 则翻到上方 `rect.t - h - 34`。

---

<a id="sec19"></a>
## 19. Mockup：WebGL2 着色器实时贴图（`src/mockupGl.ts` 139 行）★★ 完整可搬的 GLSL

### 19.1 输入

`MockupSources = { photo, depth, mask, design }`（四张图）；`Placement = { x, y, scale, rotation, warp }`（x/y = 0..1 归一化中心，scale = 设计宽占产品宽的比例，warp = 0..1）。

### 19.2 片元着色器核心逻辑（可直接拄）

```glsl
vec4 p = texture(photo, uv);
color = p;
if (hasDesign < 0.5) return;
vec2 step = texel * 8.0;                                                    // texel = 1/photoW, 1/photoH
float dx = texture(depth, uv + vec2(step.x,0)).r - texture(depth, uv - vec2(step.x,0)).r;  // 深度梯度
float dy = texture(depth, uv + vec2(0,step.y)).r - texture(depth, uv - vec2(0,step.y)).r;
vec2 q = uv - warp * 0.5 * vec2(dx, dy) - center;                           // ★ 用深度梯度做 UV 扭曲
float c = cos(-rotation), s = sin(-rotation);
vec2 local = vec2(c*q.x - s*q.y, s*q.x + c*q.y) / size + 0.5;               // 旋转 + 缩放 + 回到 0..1
if (local.x < 0.0 || local.x > 1.0 || local.y < 0.0 || local.y > 1.0) return;
vec4 d = texture(design, local);
float shade = clamp(lum(p.rgb) / max(median, 0.05), 0.0, 1.6);              // ★ 产品自身明暗 / 中位亮度
float a = d.a * texture(mask, uv).r;                                        // ★ 产品外区域不贴
color = vec4(mix(p.rgb, d.rgb * shade, a), p.a);
```
三个关键技巧：
1. **扭曲不建网格**，而是用深度图的**中心差分梯度**（步长 8 像素）偏移 UV → 褶皱/曲面自动带动设计图；
2. **明暗** = 产品亮度 / 产品区域中位亮度（上限 1.6）→ 阴影和高光自然透出来；
3. **遮罩** = BiRefNet 抠图结果 → 设计不会贴到背景上。

### 19.3 其他实现细节

- 顶点着色器：全屏 TRIANGLE_STRIP（`[-1,-1, 1,-1, -1,1, 1,1]`），`uv = vec2(pos.x*0.5+0.5, 0.5 - pos.y*0.5)`。
- `getContext('webgl2', { preserveDrawingBuffer: true, premultipliedAlpha: false })` → **preserveDrawingBuffer 是为了能 `toBlob` 截图**。
- `medianLuma(photo, mask)`：两边都缩到 **128×128**，只取 `mask 红通道 > 128` 的像素算亮度，排序取中位数（`values[values.length >> 1]`）。
- `load(sources)` 做**增量纹理更新**：`changed = keys.filter(k => sources[k] !== this.loaded[k])`，只重传变化的纹理；`CLAMP_TO_EDGE` + `LINEAR`。
- `size` uniform：`[p.scale, (p.scale * designAspect * pw) / ph]`（**把设计宽高比换算到照片像素空间**）。
- `bakeMockup(sources, placement)`：离屏 canvas → `canvas.width/height = photoSize` → draw → `toBlob('image/png')`。

### 19.4 Mockup 业务流程（`src/mockup.ts` 56 行）

- 后端 workflow `mockup-surface.api.json`，**一次跑同时产出两路**：`result = DEPTH('3')` 作为主结果，**从 `/history/{job.id}` 里再取 `outputs['7'].images[0]` 作为 mask**（★ 一个任务取多个节点输出的手法）。
- placer `'mockup'`：不生成图片，而是**创建一个 `MOCKUP` 类型的 shape**，props = `{ w, h, name, photo, depth, mask }`，包在 `editor.run(..., { history:'ignore' })` 里。
- `BLEND_PROMPT`（原文）："Make the printed design look naturally printed on the product: follow its folds, curvature, fabric or material texture and lighting. Keep the design content and position, the product and the background unchanged."
- `bakeToImage`：`bakeMockup` → 上传 → `placeMedia(editor, media, name, b.maxX + 40, b.y, b.w)`。
- `aiBlend`：先 bake 再 `editGraph(annotated(url), BLEND_PROMPT)`。

---

<a id="sec20"></a>
## 20. Compose 专用 shape（`src/shapes/ComposeShapes.tsx` 147 行）

### 20.1 AI block（`GENBLOCK = 'ca-genblock'`）

- props：`{ w, h, description }`，默认 `400×300`，`description: ''`；`canEdit() = true`。
- 编辑态用 `<textarea autoFocus>`，`onChange` **直接写 props**（无本地 state）；`onPointerDown`/`onKeyDown` 都 `stopPropagation()`；Escape 退出。
- ★ **`toSvg` 返回灰色矩形**：`<rect width height fill={PLACEHOLDER_GRAY('#9E9E9E')} />` → **导出/送模型时 AI block 就是一片灰区**，配合 prompt 里的 "Fill every flat gray area" 形成约定。

### 20.2 QR 码（`QR = 'ca-qr'`）

- props：`{ w, h, url, color }`，默认 `240×240`、`url: 'https://comfyart.org'`、`color: '#000000'`。
- `isAspectRatioLocked() = true` → **引擎自动锁比例**。
- ★ **QR 直接生成 SVG path，不栅格化**：
  ```ts
  const qr = qrcode(0, 'M')            // 类型 0 = 自动选版本，纠错级 M
  qr.addData(url || ' '); qr.make()
  const n = qr.getModuleCount()
  let d = ''
  for (y) for (x) if (qr.isDark(y,x)) d += `M${x} ${y}h1v1h-1z`
  ```
  渲染：`<svg viewBox={-2 -2 ${n+4} ${n+4}} shapeRendering="crispEdges">` + 白底 `rect` + `path fill={color}`。
  → **任意缩放/导出都锐利**（`shapeRendering='crispEdges'` 防抗锯齿模糊）。
- `toSvg` 直接返回同一个 `<QrSvg>` → 导出保留矢量。

---

<a id="sec22"></a>
## 22. 嵌入类 shape（`src/shapes/EmbedShapes.tsx` 226 行）★ Lottie / 3D / HTML / 音频

### 22.1 类型与 props

| 常量 | 类型名 | props | 默认 |
|---|---|---|---|
| `LOTTIE` | `ca-lottie` | `w,h,src,name` | 400×400 |
| `MODEL` | `ca-model` | `w,h,src,name,ext,spin,bg` | 480×480, `ext:'glb'`, `spin:true`, `bg:''` |
| `HTML` | `ca-html` | `w,h,html,name` | 800×600 |
| `AUDIO` | `ca-audio` | `w,h,src,name` | `AUDIO_SIZE = [680, 168]` |

### 22.2 关键手法

- ★ **`export const snapshots = new Map<TLShapeId, () => Promise<Blob>>()`** —— 模块级“快照注册表”：3D 视图把 `renderer.domElement.toBlob` 注册进去，**导出/“Export Current View” 就能拿到任意 3D shape 的当前画面**；卸载时 `snapshots.delete(shape.id)`。
- ★ **交互型 shape 的 pointerEvents 开关**：
  `style={{ pointerEvents: this.editor.getEditingShapeId() === shape.id ? 'all' : 'none' }}`
  → **默认不抢鼠标（画布照常拖拽/框选），双击进入编辑态后才能操作内部**（3D 转 OrbitControls、HTML iframe）。
  3D 额外：`controls.enabled = editing`、`controls.autoRotate = spin`，且这个 `useEffect` **无依赖数组**（每次渲染都同步）。
- **Lottie**：`await import('lottie-web')` 懒加载；`loadAnimation({ renderer:'svg', loop:true, autoplay:true, path })`；卸载 `anim.destroy()` + `alive` 标志防泄漏。
- **3D 加载器按扩展名分派**（全部动态 import，**不打包进主 chunk**）：`glb/gltf → GLTFLoader`、`obj → OBJLoader`、`fbx → FBXLoader`、`stl → STLLoader + MeshStandardMaterial({color:0xbbbbbb})`、`ply → PLYLoader + computeVertexNormals() + vertexColors 自动判断`；默认回退 glb。
- **3D 场景固定套路**：`WebGLRenderer({ antialias:true, alpha:true, preserveDrawingBuffer:true })`（**preserveDrawingBuffer 才能 toBlob**）+ `outputColorSpace = SRGBColorSpace`；灯光 = `HemisphereLight(0xffffff, 0x666666, 2.2)` + `DirectionalLight(0xffffff, 2)` 在 `(3,5,4)`；
  **自动构图**：`Box3.setFromObject` → `size = 对角线长度 || 1` → `object.position.sub(center)` → `PerspectiveCamera(35, aspect, size/100, size*100)` → `camera.position.set(size*0.9, size*0.6, size*1.2)`；
  `ResizeObserver` 同步 renderer size + camera.aspect；`setAnimationLoop` 里 `controls.update()`（`enableDamping = true`）。
- **HTML shape**：`<iframe sandbox="allow-scripts" srcDoc={html} />` → **Agent 生成的网页直接当画布元素**（`place_html`）。
- **音频卡片**：原生 `<audio preload="metadata">` + 自绘播放按钮/`<input type=range>` 进度条/`M:SS` 时间；**所有交互元素 `onPointerDown={e => e.stopPropagation()}`** 防抢选区。

### 22.3 手绘 doodle（`src/shapes/DoodleShape.tsx` 127 行）★ 平滑曲线算法

- props：`{ w, h, nw, nh, color, size, points }`；`points` = `"x,y x,y ..."`（**相对 shape 左上角，1 位小数**）；`nw/nh` = 创建时的 w/h（**viewBox 固定，缩放时线宽跟着变**）。
- ★ **二次贝塞尔平滑**（`pathOf`）：
  ```ts
  d = `M${p0}`
  for (i = 1; i < pts.length - 1; i++) d += `Q${x},${y} ${(x+nx)/2},${(y+ny)/2}`   // 中点作终点
  d += `L${last}`
  ```
  → **用“点 → 下一段中点”作 Q 曲线**，经典 Catmull-Rom 风格平滑；单点时 `M x,y l0.01,0` 保证可见。
- SVG：`viewBox="0 0 nw nh" preserveAspectRatio="none"` + `strokeLinecap/Linejoin='round'` + `overflow: visible`。
- 工具：`onPointerMove` 用 `getIsPointing()`（**不是 getIsDragging()**）；每次 move 重算包围盒并更新 shape（**边画边收缩包围盒**）。

### 22.4 Mockup shape（`src/shapes/MockupShape.tsx` 63 行）

- props 把 **GL 参数全部内嵌**：`photo, depth, mask, design, x, y, scale, rotation, warp` → **样机完全可序列化，刷新后能恢复**。默认 `x:0.5, y:0.5, scale:0.4, rotation:0, warp:0.5`。
- `VIEW_LONG = 1024`：canvas 尺寸 = `photoSize * min(1, 1024/max(w,h))`（**预览限制分辨率**）。
- `useEffect` 依赖数组 = **所有 GL 相关 props** → 改任何一个就重画（`renderer.current` 复用，不重建 GL 上下文）。
- `toSvg`：`bakeMockup()` → FileReader 转 dataURL → `<image href width height />` → **导出时烘成图**。

---

<a id="sec23"></a>
## 23. 统一输入框 Composer（`src/ui/Composer.tsx` 334 行）★★ 一个输入框兼顾 Agent/直生图/视频

### 23.1 三模式

`MODES: [AgentMode, 标签, 图标][] = [['agent','Agent','agent'], ['image','Image','genImage'], ['video','Video','genVideo']]`
→ **同一个输入框切模式**；`mode !== 'agent'` 时才显示模型/参数 pill；`mode === 'agent'` 时显示 AgentPicker + RefFrameButton + ModelPrefs。

### 23.2 参考图来源合并（★ 关键）

```ts
const selected = useValue('selected media', () => editor.getSelectedShapes().filter(isRefShape), [editor])
const [hidden, setHidden] = useState<TLShapeId[]>([])          // 本地“从选区里移除”的 id
const shown = selected.filter((s) => !hidden.includes(s.id))
const refs = [...shown, ...extra.map(id => editor.getShape(id)).filter(...).filter(s => !shown.some(x => x.id === s.id))]
```
- **默认参考图 = 当前选中的媒体**（无需手动加）；
- 点 chip 上的 × → 若是选区带来的就 `setHidden`（**不改选区**），若是手动加的就从 `extraRefsAtom` 移除；
- `useEffect(() => setHidden(h => h.filter(id => selected.some(s => s.id === id))), [selected])` → **选区变了就清空隐藏列表**（避免幽灵状态）。

### 23.3 `@` 与 `/` 自动补全（★ 正则直接写在输入框上）

```ts
const slash = /^\/(\S*)$/.exec(draft)              // 整个输入只有一个 /xxx → 技能列表
const at = /(?:^|\s)@(\S*)$/.exec(draft)           // 末尾是 @xxx → 画布图 + 资产 kit
const mentions = at ? images.filter(s => imageName(editor, s).toLowerCase().includes(at[1].toLowerCase())).slice(0, 8) : []
const kitMentions = at ? library.filter(k => k.name.toLowerCase().includes(at[1].toLowerCase()) && !kits.some(x => x.id === k.id)).slice(0, 8) : []
const clearAt = () => draftAtom.set(draft.slice(0, at!.index) + (at!.index ? ' ' : ''))   // 删掉 @xxx
```
- 候选上限 **8 条**；`images = editor.getCurrentPageShapes().filter(isRefShape)`（**当前页全部媒体**）；
- 选中候选 → `addRef(id)` 或 `kitsAtom.set([...kits, k])` + `clearAt()`；
- 有候选时 **Enter 不发送**（`if (isEnter(e) && !e.shiftKey && !slash && !mentions.length && !kitMentions.length)`）。

### 23.4 发送逻辑

```ts
const targets = refs.length > 1 ? each.targets(refs) : []
mode === 'agent' ? sendToAgent(editor, text, refs, skill, kits, docs, targets.length ? eachLine(refs, targets) : '')
                 : generateDirect(editor, text, eachRuns(refs, targets, () => text), mode === 'video', onError)
```
发送后立即清空 `draftAtom / extraRefsAtom / kitsAtom`；**仅 agent 模式**额外清 `skillAtom / docsAtom`。

### 23.5 chips 区（已附加内容）

顺序：kit chips → skill chip → doc chips（仅 agent）→ 参考图 chips（带缩略图/音频图标/视频预览）；每个带 ×；`refs.length > 1 && each.on` 时每个 chip 多一个 `EachRole`（Shared/Each 切换）。

### 23.6 “+” 菜单内容（功能入口清单）

Upload file / **Canvas review**（直接发 `REVIEW_PROMPT` 给 Agent）/ **Scheduled task**（把当前草稿带进定时任务弹窗）/ Skill 子菜单（Create skill / Create skill from this chat / Manage skills / 技能列表）/ Add assets（`AssetGrid only="image"` → `placeAsset` 放到画布再 `addRef`）。

### 23.7 其他

- 拖放与粘贴：`onDragOver` 只处理 `dataTransfer.types.includes('Files')`；`onPaste` 有文件时 `preventDefault + stopPropagation` 再 `uploadRefs`；
- `FILE_ACCEPT = `image/*,video/*,audio/*,${DOC_ACCEPT}``；
- 语音输入：`VoiceButton onText={(t) => draftAtom.set(draft + (draft ? ' ' : '') + t)}`（**追加而非覆盖**）；
- Agent 忙时发送按钮不禁用，title 提示“排队”（`busy && mode === 'agent'`）；
- “Create skill from this chat” 仅当 `messagesAtom.get().length > 0` 可用。

---

<a id="sec24"></a>
## 24. 局部重绘 / 移动对象面板（`src/ui/InpaintPanel.tsx` 286 行）★★★ 完整的 mask 编辑器

### 24.1 双 canvas 结构（★ 核心手法）

- `mask.current`：**离屏 mask canvas**，尺寸 = `min(1, MAX_SIDE(1536)/max(w,h))` 缩放后的图像尺寸；
- `view.current`：**覆盖在图像上的可见 canvas**（CSS 定位到选区矩形），每帧 `clearRect → drawImage(mask) → globalCompositeOperation='source-in' → fillStyle='rgba(59,130,246,.5)' → fillRect` → **把 mask 的形状涂成半透蓝**。
- 坐标换算：`scale = m.width / (rect.r - rect.l)`（屏幕→mask 像素）；`at(e) = ((clientX-b.left)/b.width)*m.width`（**用比例而非绝对偏移，缩放安全**）。

### 24.2 工具

`Tool = 'brush'|'eraser'|'lasso'|'rect'|'smart'|'drag'`（`drag` 仅在 move 模式追加）。
- brush/eraser：`ctx.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over'`，`strokeStyle='#fff'`，`lineWidth = brush * scale`，`lineTo(to[0]+0.01, to[1])`（**单点也能画出像素**）；
- lasso/rect：拖拽时只画虚线预览（`setLineDash([8,6])`），**pointerup 时才 `fillStyle='#fff'` 填充**；
- smart：点一下 → `samGraph(annotated(src), {x,y})` → 取回掩膜图 → `actions.lumaToAlpha(found, m.width, m.height)` → `drawImage` 到 mask；**坐标要换算回原图像素**：`p[0] * asset.w / m.width`；
- smart 还提供文本提示快捷项 `SUBJECTS = [['主体','main subject'],['脸','face'],['皮肤','skin'],['头发','hair']]`；
- drag（move 模式）：`setOffset(([dx,dy]) => [dx + p[0] - x0, dy + p[1] - y0])`（**累加增量而非绝对位置**）。

### 24.3 预览被拖动的对象（★ 巧）

```ts
const cut = document.createElement('canvas')
k.drawImage(img, 0, 0, c.width, c.height)
k.globalCompositeOperation = 'destination-in'
k.drawImage(m, 0, 0)              // 只留 mask 内的像素
ctx.drawImage(cut, offset[0], offset[1])
```
→ **用 `destination-in` 把原图按 mask 剪出来，再按 offset 叠回去**，用户能直接看到“抠出来正在拖”。

### 24.4 独立 mask 撤销栈

`undo: ImageData[]`，`UNDO = 20`；`snapshot()` 在任何改动前 `getImageData` 全量存一份（`undo.current = [...undo.current.slice(1 - UNDO), getImageData(...)]`）；`back()` = `putImageData(pop())`。
→ **mask 编辑不占用画布撤销栈**（视频编辑器也是这个套路）。

### 24.5 反相 / 清空

`invert()`：逐像素 `RGB = 255`，`alpha = 255 - alpha`（**mask 用 alpha 表示，所以反相只改 alpha**）。

### 24.6 提交

```ts
move ? actions.moveObject(editor, shape, m, offset[0], offset[1], onError)
     : actions.inpaint(editor, shape, m, feather, style ? `${prompt.trim()}, ${style} style` : prompt.trim(), onError)
```
- `feather` 滑杆 0..40（默认 8）；`brush` 滑杆 4..200（默认 40）；
- **风格 chip 直接拼到 prompt 后面**（`INPAINT_STYLES`）：keep original('') / realistic('photorealistic') / photorealistic('professional portrait photography') / anime('anime illustration') / illustration('artistic illustration') / 3D('3D render') / game concept art('game CG artwork')；
- 提交按钮禁用条件：`busy || (move ? 无 offset : 无 prompt)`；提交后立即 `exitMode()`。

### 24.7 面板定位

`left = rect.r + GAP + size.w > host.width - EDGE ? max(EDGE, rect.l - size.w - GAP) : rect.r + GAP`（**右侧放不下就放左侧**）；
`top = min(max(rect.t, EDGE), host.height - size.h - EDGE)`。

### 24.8 生命周期守卫

- 进模式（`active` 且 asset 变）时重置：mask 重建、`undo=[]`、`prompt=''`、`offset=[0,0]`、`tool = move ? 'lasso' : 'brush'`；
- `Escape`（捕获阶段）退出；`if (active && !shape) exitMode()`（**图被删就退出**）；
- 笔刷光标环：`cursor` 状态 + `div.ca-erase__cursor` 尺寸 = brush（**屏幕像素恒定，不随 zoom**）。

---

<a id="sec26"></a>
## 26. 生成面板 + 参考图模式系统（GenPanel / refModes / GenSettings / GenAdvanced / models）★★★

### 26.1 面板与 shape 的关系（`src/ui/GenPanel.tsx` 289 行）

- ★ **面板不存任何生成参数**，全部读写 `shape.props`（prompt/ratio/resolution/cw/ch/workflow/duration/seed）与 `shape.meta`（refs/refModes/refStrengths/refControls/colors/base/lastFrame/motion/scenes/videoMode/strength）：
  ```ts
  const update = (patch) => {
    const next = { ...p, ...patch }
    if (patch.ratio || patch.cw || patch.ch || patch.resolution || patch.workflow) {
      const [w, h] = targetSize(next); Object.assign(next, { w, h })   // ★ 改比例/分辨率 → 自动改 block 尺寸
    }
    editor.updateShape({ id, type, props: next })
  }
  const setMeta = (patch) => editor.updateShape({ id, type, meta: { ...shape.meta, ...patch } })
  ```
  → **参数就是画布数据**：撤销/重做、持久化、Agent 读写全部自动可用。
- 隐藏条件：`if (!shape || !rect || running || canvasPick) return null`（**运行中自动收起面板**）。
- 定位：水平居中于选区（`left = clamp((l+r)/2 - w/2, EDGE, host.w - w - EDGE)`）；**在选区下方**（`top = max(EDGE, min(rect.b + GAP, host.h - 72 - h))`）。
- `useEffect(() => prompt.current?.focus(), [shape?.id])`；textarea `onKeyDown` 先 `e.stopPropagation()`（**防 tldraw 快捷键抢 Enter**）。
- `ready` 判断按模式分支（story / motion / lipsync / 普通）；story 要求 `至少一个 scene 有 prompt` 且 `总时长 <= MAX_STORY_SECONDS`。
- 参考图 chip 上显示**序号**（`used.indexOf(src) + 1`）→ 对应 prompt 里的 `--ref1`；不被当前模型支持的参考图置灰 `data-off` + `title` 说原因。
- 参考图来源菜单（每个 slot 一套）：Upload / From assets / From library / **Choose from canvas**（`canvasPickAtom.set({ id: shape.id, to })` → **进入“画布拾取模式”，面板隐藏**）。
- 上传时 `file.current.multiple = to === 'ref'`，`accept` 按 slot不 同（motion=video/*、speech=audio/*）。

### 26.2 视频五种模式切换（★ 互斥 chip）

`frames`（首尾帧）/ `refs`（参考图生视频）/ `motion`（动作控制）/ `lipsync`（唇形）/ `story`（分镜）。
切模式时**顺手清掉不相容的字段**，例：
`onClick={() => setMeta({ scenes: null, videoMode: 'motion', lastFrame: null, refs: firstImage })}`
→ **模式切换自带数据清理**（`firstImage = refs.filter(image).slice(0,1)`）。

### 26.3 参考图模式系统（`src/refModes.ts` 136 行）★★★ 最有价值的一块（已逐行核实）

- ★ **`RefMode` 三件套**：
  ```ts
  export interface RefMode {
    id: string; label: string; group: string
    hint: string                                   // ★ 给模型的英文指令模板，{n} = 参考图序号
    node?: [string, Record<string, unknown>]       // ★ [ComfyUI 预处理器 class_type, 固定 inputs]
    control?: boolean                              // 该模式走 ControlNet
    download?: boolean                             // 需额外下载模型
  }
  const RES = 1024
  ```
- `REF_MODES: RefMode[]` 共 **17 个**，按 `group` 分三栏（UI 直接按 group 分组）：
  - **`common.reference`**：`content`（hint 空）、`style`、`composition`
  - **`refModes.structure`**（全部 `control: true`）：`canny`、`lineart`、`mlsd`↓、`scribble`、`softedge`↓、`depth`、`normal`↓、`pose`、`seg`↓、`tile`（↓ = `download: true`）
  - **`refModes.color`**：`recolor`、`color`、`shuffle`、`brightness`
  - 另有独立的 `PALETTE_MODE: RefMode = { id:'palette', hint:'Image {n} is only a color swatch: use its colors as the palette and ignore its layout, shapes and pattern.' }`
- ★ **`hint` 原文（可直接改写复用）**：
  - `style`：`Use only the visual style, palette and rendering of image {n}, not its content.`
  - `composition`：`Keep the layout and composition of image {n}.`
  - `canny`：`Follow the edges of image {n} exactly for shapes and composition.`
  - `lineart`：`Follow the line art of image {n} for shapes and composition.`
  - `mlsd`：`Follow the straight architectural lines of image {n}.`
  - `scribble`：`Use the rough scribble in image {n} as the layout.`
  - `softedge`：`Follow the soft outlines of image {n}.`
  - `depth`：`Match the depth and spatial layout of image {n}.`
  - `normal`：`Match the surface shapes of the normal map in image {n}.`
  - `pose`：`Match the body pose of the skeleton in image {n}.`
  - `seg`：`Follow the region layout of the segmentation map in image {n}.`
  - `tile`：`Keep the details and layout of image {n}, add fine detail.`
  - `recolor`：`Image {n} is a grayscale guide: keep its shapes and lighting and add new colors.`
  - `color`：`Use the color palette of image {n}.`
  - `shuffle`：`Use the colors and textures of image {n} loosely, not its layout.`
  - `brightness`：`Match the lighting and brightness distribution of image {n}.`
  - 用法：`refHints(used) = used.map(({mode}, i) => mode.hint.replace('{n}', String(i+1))).filter(Boolean)` → **拼进 prompt**
- ★ **`node` = ComfyUI ControlNet 预处理器 + 调好的参数**（逐条核实）：
  | 模式 | class_type | 固定 inputs |
  |---|---|---|
  | canny | `Canny` | `low_threshold: 0.4, high_threshold: 0.8` |
  | lineart | `LineArtPreprocessor` | `coarse: 'disable', resolution: RES` |
  | mlsd | `M-LSDPreprocessor` | `score_threshold: 0.1, dist_threshold: 0.1, resolution: RES` |
  | scribble | `ScribblePreprocessor` | `resolution: RES` |
  | softedge | `HEDPreprocessor` | `safe: 'enable', resolution: RES` |
  | depth | `DepthAnythingV2Preprocessor` | `ckpt_name: 'depth_anything_v2_vitl.pth', resolution: RES` |
  | normal | `BAE-NormalMapPreprocessor` | `resolution: RES` |
  | pose | `DWPreprocessor` | `detect_hand/body/face: 'enable', resolution: RES, bbox_detector: 'yolox_l.onnx', pose_estimator: 'dw-ll_ucoco_384_bs5.torchscript.pt', scale_stick_for_xinsr_cn: 'disable'` |
  | seg | `OneFormer-COCO-SemSegPreprocessor` | `resolution: RES` |
  | tile | `TilePreprocessor` | `pyrUp_iters: 3, resolution: RES` |
  | recolor | `ImageIntensityDetector` | `gamma_correction: 1, resolution: RES` |
  | color | `ColorPreprocessor` | `resolution: RES` |
  | shuffle | `ShufflePreprocessor` | `resolution: RES, seed: 0` |
  | brightness | `ImageLuminanceDetector` | `gamma_correction: 1, resolution: RES` |
  ```ts
  export function preprocessNode(mode: RefMode, image: unknown, res: number) {
    const [type, inputs] = mode.node!
    return { class_type: type, inputs: { image, ...inputs, ...('resolution' in inputs ? { resolution: res } : {}) } }
  }
  ```
- ★ **能力判定不靠硬编码表，而靠 `ModelDef` 的两个字段**：
  ```ts
  export function modesFor(model: ModelDef): RefMode[] {
    return model.refs ? REF_MODES : model.control ? REF_MODES.filter((m) => m.control) : []
  }
  const QWEN_EDIT_IMAGES = 16
  export const refLimit = (model: ModelDef) => (model.refs === 'qwen' ? QWEN_EDIT_IMAGES : Infinity)
  export const modeOf = (id?: string) => REF_MODES.find(m => m.id === id) ?? REF_MODES[0]   // 兜底 = content
  export function usableRefs(model, refs: string[], picked: Record<string,string>): UsedRef[] {
    return refs.flatMap(src => { const mode = refModeFor(model, picked[src]); return mode ? [{ src, mode }] : [] })
  }
  ```
  → **`model.refs` 有值（多模态直收图）→ 全部 17 模式可用；只有 `model.control` → 只允许 11 个 ControlNet 模式；都没有 → 不支持参考图**（UI 据此置灰 + 提示原因）。
- `DEFAULT_STRENGTH = 80`；`RefControl = { start, end, res, resize: 'crop'|'stretch' }`；`DEFAULT_CONTROL = { start:0, end:100, res:RES, resize:'crop' }`；`CONTROL_RESOLUTIONS = [512, 1024, 2048]`；存在 `meta.refModes[src]` / `meta.refStrengths[src]` / `meta.refControls[src]`（**按参考图 URL 作 key**）。
- ★ **`previewControl(src, mode, res)`：三节点小 graph 预览预处理器输出**
  ```ts
  const graph = { r: { class_type:'LoadImage', inputs:{ image: annotated(src) } },
                  x: preprocessNode(mode, ['r',0], res),
                  o: { class_type:'PreviewImage', inputs:{ images:['x',0] } } }
  const [ref] = await waitForImages(await queuePrompt(graph), 'o')
  ```
  → **复用同一条 queue/wait 通道**，不为预览单开接口。
- ★★★ **`applyRefs(graph, model, used, strengths, controls, width, height)` 的三条注入路径**（这是整个参考图机制的核心）：
  1. 每张参考图：`graph['r{i}'] = LoadImage(annotated(src))`；若 `mode.node` 则 `graph['x{i}'] = preprocessNode(mode, ['r{i}',0], controls[src].res)`；
  2. **`model.refs === 'qwen'`**（多模态直收图）：`graph['6'].inputs.vae = ['4',0]`，然后 `graph['6'].inputs['images.image_{i+1}'] = [id, 0]`；
  3. **`model.refs === 'latent'`**（IP-Adapter 式）：逐图 `s{i}` = `ImageScaleToTotalPixels {upscale_method:'lanczos', megapixels:1, resolution_steps:1}` → `e{i}` = `VAEEncode(vae ['3',0])` → `p{i}`/`n{i}` = `ReferenceLatent` **链式**挂在 positive/negative 上 → 最后 `graph['11'].inputs.positive/negative`；
  4. **`model.control` + 有 control 模式**：`graph.cn = ControlNetLoader {control_net_name: model.control}`；`cs{i}` = `ImageScale {lanczos, width, height, crop: c.resize==='crop' ? 'center' : 'disabled'}`；`ca{i}` = `ControlNetApplyAdvanced {strength: strengths[src]/100, start_percent: c.start/100, end_percent: c.end/100, vae: [model.vae,0]}` **链式** → `graph['7'].inputs`。
  → ★ **节点 id 用 `r{i}/x{i}/s{i}/e{i}/p{i}/n{i}/cs{i}/ca{i}` 前缀避免与工作流原生 id 冲突**；★ **同一个模式定义同时驱动「prompt 文本」与「ComfyUI 图结构」两条通道**。

### 26.4 模型目录（`src/models.ts` 88 行）★ 已核实：只有 5 图 + 1 视频

```ts
export interface ModelDef {
  id: string; label: string; desc: string; files: string[]      // id = workflow JSON 文件名
  video?: boolean
  refs?: 'qwen' | 'latent'                                      // ★ 决定参考图注入方式（§26.3）
  prompt?: string                                               // 工作流里 prompt 字段名
  steps?: [string, Record<string, number>]                      // [节点id, {fast|standard|high: 步数}]
  sizes?: string[]; vae?: string; caption?: string
  control?: string                                              // ControlNet 文件名
  negative?: [string, string]; lora?: string                    // lora 架构名（匹配用）
}
```
- `IMAGE_MODELS`（**5 个**）：
  | id（= workflow 文件） | label | 关键字段 |
  |---|---|---|
  | `qwen-image-2.1-t2i.api.json` | Qwen Image 2.1 | `refs:'qwen'`、`steps:['7',{fast:12,standard:25,high:40}]`、`sizes:['5']`、`vae:'4'`、`lora:'qwen-image-2.1'`、files `qwen_image_2.1_int8_convrot.safetensors` |
  | `z-image-turbo-t2i.api.json` | Z-Image Turbo | `prompt:'text'`、`sizes:['5']`、`vae:'3'`、`lora:'z-image'`（**无 steps → 不显示质量滑杆**） |
  | `flux2-klein-9b-t2i.api.json` | Flux.2 Klein 9B | `refs:'latent'`、`sizes:['5','13']`、`vae:'3'`、`lora:'flux2-klein'` |
  | `qwen-image-2512-t2i.api.json` | Qwen Image 2512 | `steps:['7',{fast:20,standard:30,high:50}]`、**`control:'Qwen-Image-2512-Fun-Controlnet-Union-2602.safetensors'`**、`negative:['10','text']`、`lora:'qwen-image'` |
  | `ideogram4-t2i.api.json` | Ideogram 4 | `caption:'ideogram4-caption.json'`、`steps:['13',{fast:12,standard:20,high:48}]`、`sizes:['5','13']`、`vae:'3'` |
- `VIDEO_MODELS`（**1 个**）：`t2v-minimax-h3.api.json` / MiniMax H3 / `video: true`。
- `isVideo(workflow)`、`modelOf(workflow)`（两表合并查）。
- ★ **安装检测**：`loadModels()` = `Promise.all([readUserJson('comfyart/models.json', null), api('/models/diffusion_models'), loadModelPaths()])` → `installed = 所有 m.files.every(f => names.includes(f) || f in paths)` → `installedAtom: atom<string[] | null>`（**null = 未加载，此时 `isInstalled` 一律 true**，避免首屏空列表）。
- ★ **Agent 用的模型选择**：`agentModels(video)` → `prefs.auto ? 全部已装 : 已装∩enabled`；**空则退化为 `list.slice(0,1)`，再退化为内置第一个** → **永远不会返回空列表**。

### 26.5 生成参数常量（`src/generate.ts` 行号）★★ 直接照抄的取值表

```ts
export const RESOLUTIONS = ['1K', '2K', 'custom']                                   // :14
export const QUALITIES: [string,string][] = [['fast',…],['standard',…],['high',…]]  // :15（步数在 ModelDef.steps 里）
export const SHOTS: [string,string,string][]                                        // :16
export const MAX_SCENES = 6                                                          // :22
export const MAX_STORY_SECONDS = 30                                                  // :23
export const STYLES: [string,string,string][]                                        // :29
export const RATIOS = ['1:1','3:2','2:3','4:3','3:4','16:9','9:16','4:5','5:4','21:9'] // :34
export const VIDEO_RATIOS = ['16:9','9:16','1:1','4:3','3:4','21:9']                 // :35
export const DURATIONS = [5, 10, 15]                                                 // :36
export const VIDEO_RESOLUTIONS = Object.keys(VIDEO_AREAS)                            // :37（'768p'|'1080p'，§28）
export const MAX_SPEECH_SECONDS = 15                                                 // :44
export const MAX_COLORS = 6                                                          // :106
```
- `ui/GenSettings.tsx`（127 行）：`COUNTS = [1,2,3,4]`（:10）、`RES_LABELS = {'1K','2K',custom}`（:11）；**图片与视频用不同的 ratio/resolution 常量**；pill 摘要行（:28）：`` `${p.ratio} · ${p.duration}s · ${resolution}${style ? ' · ' + shot : ''}` ``。
  → ★ **注意：没有 SVG「比例盘」组件**（早先笔记里的 `RatioDial`/`RINGS` 是错的，已核实 `GenSettings.tsx` 内无 `svg`/`ellipse`）；比例就是 chip 列表。

### 26.6 高级参数（`src/ui/GenAdvanced.tsx` 98 行 + `src/loras.ts` 41 行）

- `AdvancedPill` 计数徽章：`count = (seed != null ? 1 : 0) + (negative ? 1 : 0) + (model.lora ? loras.length : 0)`；
- ★ **seed**：`<input type="number" min={0}>` + `setMeta({ seed: Math.max(0, Math.floor(Number(e.target.value) || 0)) })` + `onKeyDown` `stopPropagation()`；`randomSeed()` 另给随机按钮；
- ★ **LoRA 强度滑杆 `min=0 max=200`**（**不支持负强度**，早先笔记的 -100..150 是错的）；`loras: {name, strength}[]` 存 `meta.loras`；
- `loras.ts`：`MAX_LORAS = 3`、`DEFAULT_LORA_STRENGTH = 100`、收藏存 `comfyart/lora-favorites.json`；
  ```ts
  const ARCHITECTURES: [RegExp,string][] = [
    [/flux[-_.]?2[-_.]?klein/i, 'flux2-klein'], [/z[-_.]?image/i, 'z-image'],
    [/qwen[-_.]?image[-_.]?2(?![0-9])/i, 'qwen-image-2.1'], [/qwen[-_.]?image/i, 'qwen-image']]
  ```
  → ★ **用正则把 LoRA 文件名匹配到模型架构**（`ModelDef.lora` 字段），**不兼容的 LoRA 不显示**；`loraLabel(name) = name.split(/[\\/]/).pop()!.replace(/\.safetensors$/, '')`。

<a id="sec27"></a>
## 27. 调色面板与手绘辅助（AdjustPanel / SmudgePanel / AnglePreview）

### 27.1 调色面板（`src/ui/AdjustPanel.tsx` 172 行）★★★ 原地预览 canvas + 按住对比（已逐行核实）

- ★ **预览不是改 shape，而是在选区上盖一个 `<canvas>`**：
  ```tsx
  <canvas ref={preview} className="ca-adjust__preview"
    style={{ visibility: compare ? 'hidden' : undefined,
             left: rect.l, top: rect.t, width: rect.r - rect.l, height: rect.b - rect.t }} />
  ```
  → **预览 canvas 精确覆盖选区屏幕矩形**；`compare`（“按住对比”按钮 `onPointerDown/Up/Leave`）→ **隐藏预览就露出原图**。
- ★ **预览渲染管线**（每次 adjust/img/crop/flip 变化重画）：
  ```ts
  const out = adjustedCanvas(img, adjust, PREVIEW_SIDE)          // PREVIEW_SIDE = 900（预览限边长）
  const [x0,y0,x1,y1] = crop ? [crop.topLeft.x, crop.topLeft.y, crop.bottomRight.x, crop.bottomRight.y] : [0,0,1,1]
  const [sx,sy,sw,sh] = [x0*out.width, y0*out.height, (x1-x0)*out.width, (y1-y0)*out.height]
  c.width = Math.round(sw); c.height = Math.round(sh)
  ctx.setTransform(fx, 0, 0, fy, fx < 0 ? c.width : 0, fy < 0 ? c.height : 0)   // ★ 用 transform 做翻转
  ctx.drawImage(out, sx, sy, sw, sh, 0, 0, c.width, c.height)
  ```
  → **同时处理 tldraw 的分数 `crop` 与 `flipX/flipY`**，预览与画布所见一致。
- ★ **16 个参数分 4 个 tab**（`TABS: [string, [keyof Adjust, string, number][]][]`，第三元是 `min`，`max` 恒为 100）：
  | tab | 参数（min） |
  |---|---|
  | Light | `exposure`(-100) `contrast`(-100) `highlights`(-100) `shadows`(-100) `whites`(-100) `blacks`(-100) |
  | Color | `saturation`(-100) `vibrance`(-100) `temperature`(-100) `tint`(-100) |
  | Detail | `sharpen`(0) `clarity`(-100) `grain`(0) |
  | Effects | `vignette`(-100) `bloom`(0) `glamour`(0) |
  → **中性值 = 0**（不是 100）；★ **双击滑杆复位**：`onDoubleClick={() => onChange(0)}`。
- ★ **自动调色**：`onClick={() => img && setAdjust({...adjust, ...autoAdjust(img)})}`（`disabled={!img}`）→ **只覆盖 auto 涉及的键，不清空用户已调的**。
- ★ **Apply 按钮的智能禁用**：`disabled={busy || KEYS.every(k => adjust[k] === (adjustedOf(shape)?.adjust[k] ?? 0))}` → **参数与已应用值完全相同就不给点**。
- ★ **提交走非破坏性管线**（§8.19）：
  ```ts
  await applyAdjust(editor, shape, KEYS.some(k => adjust[k]) ? adjust : null, () => adjustedCanvas(img, adjust))
  ```
  → **全 0 时传 `null`（= 清除已存 adjust）**；已应用参数存在 `shape.meta.adjust`（`adjustedOf(shape)?.adjust`）。
- ★ **面板由 mode 系统门控**（§8.14）：`active = mode === 'adjust' || mode === 'effects'`；`if (active && !shape) exitMode()`；Escape 在 **捕获阶段** `document.addEventListener('keydown', onKey, true)` → `exitMode()`。
- 定位：`GAP=12`、`EDGE=8`、`useElementSize(panel, {w:280,h:300})`；
  `left = rect.r + GAP + size.w > host.width - EDGE ? Math.max(EDGE, rect.l - size.w - GAP) : rect.r + GAP`（**右侧放不下就翻左侧**）；
  `top = Math.min(Math.max(rect.t, EDGE), host.height - size.h - EDGE)`。
- ★ **同一个面板承载 effects 模式**（`Effects = { shadow: {x,y,blur,color} | null; blur: number }`，`NO_EFFECTS = {shadow:null, blur:0}`，存 `meta.effects`）：
  - 不透明度：`editor.updateShape({ opacity: v/100 })` → **直接用 tldraw 原生 opacity，不写 meta**；
  - 图层模糊 `0..50`；
  - 阴影 checkbox 开 → 默认 `{x:0, y:12, blur:24, color:'#00000066'}`；颜色选择器只喂 6 位（`value.shadow.color.slice(0,7)`），**写回时补 `66` alpha 后缀**（`${c}66`）；
  - x/y 偏移 `-100..100`、半径 `0..100`。
- `adjust.ts` 导出面（159 行）：`Adjust` 接口、`NO_ADJUST`、`autoAdjust(img): Partial<Adjust>`、`loadImage(src): Promise<HTMLImageElement>`、`adjustedCanvas(img, a, maxSide = Infinity): HTMLCanvasElement`。

### 27.2 涂抹工具（`src/ui/SmudgePanel.tsx` 162 行）★★★ 完整涂抹算法（已逐行核实）

- 常量：`GAP=12`、`EDGE=8`、`MAX_SIDE=2048`、`UNDO=20`。
- ★★★ **涂抹核心算法（`carry` = 笔刷携带的像素快照，滚动更新 → 真“涂抹”手感）**：
  ```ts
  function smudge(ctx, carry: ImageData, x: number, y: number, strength: number): ImageData {
    const d = carry.width, r = d / 2
    const target = ctx.getImageData(Math.round(x - r), Math.round(y - r), d, d)
    const a = carry.data, b = target.data
    for (let j = 0; j < d; j++) for (let i = 0; i < d; i++) {
      const dist = Math.hypot(i - r + 0.5, j - r + 0.5) / r
      if (dist >= 1) continue
      const k = strength * (1 - dist * dist)                     // ★ 二次衰减圆形笔刷
      const p = (j * d + i) * 4
      for (let c = 0; c < 4; c++) b[p + c] = a[p + c] * k + b[p + c] * (1 - k)
    }
    ctx.putImageData(target, Math.round(x - r), Math.round(y - r))
    return target                                                // ★ 返回值成为下一步的 carry
  }
  ```
- ★ **拖动插值防断线**：`step = Math.max(1, s.carry.width / 4)`；`n = Math.ceil(hypot(dy) / step)`；沿 `last → p` 均匀取 n 个点逐个 `smudge`；`if (n) s.last = p`。
- ★ **独立撤销栈（不用 tldraw history）**：`undo.current: ImageData[]`，`down()` 时 `undo.current = [...undo.current.slice(1 - UNDO), ctx.getImageData(0,0,c.width,c.height)]`（**每次按下存一张全画布快照，保留 20 步**）；`back()` = `pop()` + `putImageData`。
- ★ **降采样编辑**：`k = Math.min(1, MAX_SIDE / max(naturalW, naturalH))` → canvas 尺寸按 k 缩；`getContext('2d', { willReadFrequently: true })`（**大量 getImageData 必须开这个提示**）。
- ★ **三套坐标换算**：`scale = c.width / (rect.r - rect.l)`（canvas 像素/屏幕像素）；`at(e)` 把 pointer → canvas 坐标；笔刷直径 `d = Math.max(2, Math.round(brush * scale))`。
- ★ **自绘笔刷光标**：`cursor` state 存屏幕坐标 → `<div className="ca-erase__cursor" style={{ left: cursor[0]-brush/2, top: cursor[1]-brush/2, width: brush, height: brush }} />`。
- 覆盖层 canvas：`className="ca-inpaint__mask" data-tool="brush"`，`style` 同样精确覆盖选区矩形；`setPointerCapture`；`onPointerUp` → `drag.current = null`。
- 面板：笔刷 `4..200`（默认 40）、强度 `5..100`（默认 60，传给 `smudge` 时 `/100`）、Undo chip、Cancel / Apply（`disabled={busy || !ready}`）。
- ★ **提交**：`await replacePixels(editor, shape, c)`（§8.19 `imageOps` 的非破坏性像素替换）→ `exitMode()`。
- 同样 mode 门控（`mode === 'smudge'`）+ Escape 捕获阶段 + 无选中自动退出 + 右侧放不下翻左侧的定位规则。

### 27.3 角度预览（`src/ui/AnglePreview.tsx` 364 行）★★ 3D 相机云台 → 相机术语 prompt

★ 注意：**不是生成深度图**，而是“用 3D 云台把相机参数变成自然语言相机术语”，再交给多视角工作流。

- 对外接口：`AnglePreview({ value: Angle, src, onChange })`，`Angle = { h, v, z }`（方位角°/仰角°/缩放 0..10）；`AngleView = 'camera' | 'object'`。
- 常量：`FOV=35`、`SHOT_DISTANCE=6`、`SPHERE=1.5`、`NEAR=0.8`、`FAR=1.1`、`RING_LAT=-40°`、`V_MIN=-30`、`V_MAX=60`、`PICK_PX=14`、`INSET=0.38`；gizmo 颜色 `YAW=0x00d4ff`、`PITCH=0xff66ff`、`DIST=0xffcc00`。
- `fit(a) = { h: round(wrap(a.h)) % 360, v: round(clamp(a.v,-30,60)), z: round(clamp(a.z,0,10)*10)/10 }` → **所有输入量化**（整数角度 + 0.1 精度缩放）。
- `handleDistance(z) = FAR - ((FAR-NEAR) * z) / 10` → **把 0..10 的“缩放”映射成相机距离 0.8..1.1**（数值越大越近）。
- 场景：一个 `BoxGeometry` 立方体（六个面各自一个 `faceTexture(label)` —— **CanvasTexture 画网格 + 面名**，256×256，`colorSpace = SRGBColorSpace`）+ 一个 `wireframe` 球壳（`SphereGeometry(1.5,12,8)`，`opacity 0.14`）+ **三个 gizmo**：`TorusGeometry(ringR, 0.025, 16, 96)` 作 yaw 环、`TubeGeometry(CatmullRomCurve3(arc), 32, 0.025, 8, false)` 作 pitch 弧、一个 `BoxGeometry(0.22,0.14,0.12)` + `CylinderGeometry` 拼的小相机作距离手柄；每个手柄 = `SphereGeometry(0.08,24,24)` + `emissiveIntensity 0.7`，外加一个 `AdditiveBlending` 半透明光环（`SphereGeometry(0.12,16,16)`, `depthWrite:false`）。
- ★ **双相机 + scissor 画中画**：`renderer.autoClear = false`；`overview`（总览，`position.set(2.4,1.4,4.2).normalize().multiplyScalar(dist)`）铺满画布，`shot`（实际取景）用 `setScissorTest(true) + setViewport/setScissor(w-side-6, 6, side, side)` **在右上角再渲染一次**（先清边框色再清内容色）→ **一个 canvas 里做“全局视图 + 取景框预览”**。
- 交互：`pick(e)` 返回 `'yaw'|'pitch'|'distance'|null`；`dragging = s.pick(e) ?? 'free'` → **点不到手柄就自由旋转**（`h -= dx*0.5`, `v += dy*0.5`）；`wheel` 改 `z -= deltaY*0.01`（`{ passive:false }`）；`setPointerCapture`；hover 时 `cursor='grab'` 并高亮对应光环（`opacity 0.6 / 0.25`）。
- `setImage(src)`：把主体图贴到立方体正面（`front.map`），带 `token` 防并发乱序（`if (mine !== token) return tex?.dispose()`）。
- `three` 动态 import；`dispose()` 递归 `geometry.dispose()` + `material.dispose()` + `map?.dispose()` + `renderer.dispose()`。
- 底部实时显示 prompt：`anglePrompt(h,v,z).replace(/^<\w+> /,'')`。
- ★ **`anglePrompt`（`src/workflows.ts:161`）把角度量化成术语**：
  ```ts
  const AZIMUTHS = [[0,'front view'],[45,'front-right quarter view'],[90,'right side view'],[135,'back-right quarter view'],
                    [180,'back view'],[225,'back-left quarter view'],[270,'left side view'],[315,'front-left quarter view']]
  const azKw = AZIMUTHS.reduce(取环上最短角差最近的一个)[1]
  const elKw = v <= -15 ? 'low-angle shot' : v <= 15 ? 'eye-level shot' : v <= 45 ? 'elevated shot' : 'high-angle shot'
  const distKw = z >= 7 ? 'close-up' : z >= 3 ? 'medium shot' : 'wide shot'
  return `<qma> ${azKw} ${elKw} ${distKw}`          // <qma> 是工作流里的 prompt 魔法前缀
  ```
  → **3D 只用于“让用户表达意图”，最终交给模型的是一句英文相机术语**（零成本、模型无关）。
- 提交：`anglesGraph(image, h, v, z)` → workflow `multiangle-qwen-image-2.1.api.json`，`g['11'].inputs.image = image`、`g['6'].inputs.prompt = anglePrompt(...)`、`g['7'].inputs.seed = seed()`，`result: '9'`。

---

<a id="sec28"></a>
## 28. 工作流工厂（`src/workflows.ts` 407 行）★★ 每个 AI 能力 = 一个 graph 工厂函数

统一签名：`xxxGraph(...) → Promise<Built>`，`Built = { graph: PromptGraph; result: string }`（**result = 取哪个节点的输出**）。节点用**字符串 id** 寻址：`g['11'].inputs.image = image`。

完整清单（行号）：
| 函数 | 作用 | 关键参数 |
|---|---|---|
| `annotated(src)` :7 | ★ **给参考图 URL 加标记**（让后端/模型识别为参考图） | — |
| `upscaleGraph` :15 | 传统放大 | `w,h,factor` |
| `creativeUpscaleGraph` :31 | 创意放大（重绘） | `factor,w,h,style,strength` |
| `cutoutGraph` :82 | 抠图 | — |
| `eraseGraph` :88 | 擦除 | `mask` |
| `expandGraph` :96 | 外扩 | `pads:{l,t,r,b}, prompt` |
| `editGraph` :105 | 自然语言编辑 | `image, prompt` |
| `moveGraph` :113 | 移动对象 | `mask, from: Crop, to:{x,y}` |
| `styleGraph` :127 | 风格迁移 | `style, ref: string\|null` |
| `touchGraph` :143 | 局部编辑（带裁剪） | `crop: Crop, mask, prompt` |
| `anglePrompt` :161 / `anglesGraph` :172 | 多视角 | `h,v,z` |
| `layersGraph` :180 | 分层 | `layers, prompt`；`g['82'].inputs.largest_size = 1024`，默认 prompt `'An image with a foreground subject and a background.'` |
| `videoSize(aspect, area)` :194 | ★ **按面积目标算宽高** | `snap32(v) = max(32, round(v/32)*32)`；`VIDEO_AREAS = { '768p': 1344*768, '1080p': 1920*1088 }`；`[snap32(sqrt(area*aspect)), snap32(sqrt(area/aspect))]` |
| `t2vGraph` :200 / `imageToVideoGraph` :218 | 文生/图生视频（支持 `last` 尾帧） | `aspect, duration, area` |
| `MAX_VIDEO_REF_KINDS` :234 | `{ image: 9, video: 3, audio: 3 }` | — |
| `r2vGraph` :247 | 参考图生视频 | `refs: MediaRef[]` |
| `motionGraph` :272 / `lipSyncGraph` :292 | 动作控制 / 唇形 | `image, video/audio, prompt, aspect, duration` |
| `animateGraph` :304 | 静图动画 | `loop: boolean` |
| `extendGraph` :316 | 视频续接 | `duration` |
| `samGraph` :336 | ★ SAM 分割，**三种目标形式**：`{text}` / `{x,y}` / `{points:[...]}` | — |
| `samBoxesGraph` :393 | SAM + 多个 `Crop` 框 | `boxes: Crop[]` |
| `storyboardGraph` :358 | 分镜 | `scenes: Scene[], aspect, firstFrame` |
| `cutoutVideoGraph` :53 / `upscaleVideoGraph` :67 | 视频抠图/放大 | `VIDEO_TARGETS = [['1080p',1920],['2K',2560],['4K',3840]]` |

★ 可迁移的设计：前端只存“workflow 文件名 + 节点 id 填值”，**工作流本体是 ComfyUI 导出的 JSON 资文件**（`workflows/*.api.json`）→ 新增 AI 能力不需要改前端代码，只需加 JSON + 一个十几行的工厂函数。

---

<a id="sec29"></a>
## 29. 时间线剪辑引擎（`src/videoEdit.ts` 265 行）★★★ 无依赖纯前端剪辑

### 29.1 数据模型（★ 全部可序列化）

```ts
interface Clip { start: number; end: number; speed: number; volume: number }   // start/end = 源视频时间轴
interface Transition { type: 'none'|'fade'|'black'|'white'; duration: number }
interface Subtitle { id: string; start: number; end: number; text: string; size: number; color: string }
interface Edit { clips: Clip[]; transitions: Transition[]; subtitles: Subtitle[] }
```
- `transitions[i]` = **第 i 个 clip 之前**的转场（长度 = clips.length - 1）；
- `MIN_CLIP = 0.2`（切片时两端至少留 0.2s）、`DRIFT = 0.25`（音画同步容差）；
- `clipLength(c) = (c.end - c.start) / c.speed`；`freshEdit(duration)` = 单 clip 全片。

### 29.2 布局与采样（★ 核心算法）

- `layout(edit) → { offsets: number[], total }`：累加时长，**fade 转场做重叠**：`if (i>0 && tr?.type==='fade') t -= min(tr.duration, clipLength(c), clipLength(prev))`。
- `sample(edit, t) → { layers: {clip, src, alpha}[], dip: {color, alpha}|null }`：
  - 对每个覆盖 t 的 clip：`local = t - offsets[i]`，`src = c.start + local * c.speed`；
  - fade：`if (prev?.type==='fade' && local < prev.duration) alpha = local / prev.duration`；
  - black/white：**拆成两半**，前一个 clip 后半段 `alpha = (local - (len - half)) / half` 逐增，后一个 clip 前半段 `1 - local/half` 逐减 → 输出一个全屏 `dip` 覆盖层；
  - `layers.slice(-2)` → **最多叠两层**（所以只需两个 `<video>`）。
- `splitAt(edit, t)`：找包含 t 的 clip → `cut = c.start + (t - offsets[i]) * c.speed` → 拆为 `{...c, end: cut}` + `{...c, start: cut}`，**并在插入位置补一个 `{type:'none', duration:0.5}`** 保持 transitions 对齐。
- `removeClip(edit, i)`：`clips.length < 2` 直接返回；`transitions.splice(min(i, transitions.length-1), 1)`。

### 29.3 `filmstrip()` 胶片缩略图

```ts
v.currentTime = ((i + 0.5) * v.duration) / count
await new Promise(r => v.addEventListener('seeked', r, { once: true }))
ctx.drawImage(v, 0, 0, c.width, c.height); thumbs.push(c.toDataURL('image/jpeg', 0.7))
```
→ **逐帧 seek + 等 `seeked` 事件**，JPEG 质量 0.7；结束后 `v.removeAttribute('src'); v.load()` 释放资资源。

### 29.4 字幕绘制（★ 描边字幕）

```ts
const size = (s.size / 1080) * height                       // ★ 以 1080p 为基准缩放
ctx.font = `600 ${size}px Inter, "Microsoft YaHei", sans-serif`
ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.lineJoin = 'round'
ctx.lineWidth = size / 6; ctx.strokeStyle = 'rgba(0,0,0,.75)'; ctx.strokeText(s.text, width/2, height*0.92)
ctx.fillStyle = s.color; ctx.fillText(s.text, width/2, height*0.92)
```
→ **黑描边 + 中心底部 92% 位置**，中文字体回退。

### 29.5 `Player` 类（★ 双 video + WebAudio + 录制）

- 字段：`videos: HTMLVideoElement[]`（**固定 2 个**，`crossOrigin='anonymous'`）、`audio: AudioContext`、`gains: GainNode[]`、`out: MediaStreamAudioDestinationNode`、`origin`/`time`。
- 音频链：`createMediaElementSource(v) → gain → audio.destination` **且** `gain → out`（**同时监听 + 录制**）。
- `draw(frame, t)`：先填黑 → `ctx.globalAlpha = l.alpha` → `drawImage(videos[l.clip % 2], ...)` → dip 覆盖 → 字幕。
  ★ **`l.clip % 2` 复用两个 video 元素**（因为 layers 最多 2 个）。
- `sync(frame, playing)`：`v.playbackRate = clip.speed`；`gain.value = clip.volume * l.alpha * (dip ? 1 - dip.alpha : 1)`（**转场时音量跟着淡**）；`if (|v.currentTime - l.src| > DRIFT || !playing) v.currentTime = l.src`（**250ms 漂移阈值**）；未使用的 video 直接 `pause()` + `gain=0`。
- `seek(t)`：钳到 `[0, total]` → `sync(frame,false)` → **`await Promise.all(layers.map(l => v.seeking ? waitSeeked : resolved))`** → 再画（**避免画到旧帧**）。
- `play(onEnd)`：`origin = performance.now() - time*1000`，`time = (performance.now() - origin)/1000`（**用单调时钟而非累加 delta**）；`requestAnimationFrame` 逐帧 sync+draw+onTime；到 `total` 自动 pause + `onEnd()`。
- `record()`：`new MediaStream([...canvas.captureStream(30).getVideoTracks(), ...out.stream.getAudioTracks()])` + `MediaRecorder({ mimeType:'video/webm;codecs=vp9,opus', videoBitsPerSecond: 8_000_000 })`，`recorder.start(250)` → `await play(resolve)` → `stop()` → `new Blob(chunks, {type:'video/webm'})`。
- `dispose()`：`pause()` + 每个 video `removeAttribute('src'); load()` + `audio.close()`。

---

<a id="sec31"></a>
## 31. 视频剪辑 UI（`src/ui/VideoEditor.tsx` 306 行）★★ 独立撤销栈 + 拖拽裁剪

### 31.1 入口与生命周期

- `export const videoEditorAtom = atom<TLShapeId | null>('video editor', null)` → **编辑器开关存在全局 atom 里**（而不是组件 state），任何地方都能打开。
- `VideoEditor` 只做校验（shape/asset/src 均存在且 `asset.type === 'video'`），然后 `return <EditorBody key={id} ... />` → ★ **用 `key={id}` 强制不同视频重建全部 state**。
- 常量：`SPEEDS = [0.5,1,1.5,2]`、`TRANSITIONS = [none/fade/black/white]`、`THUMBS = 24`、`UNDO = 50`、`MAX_SIDE = 1920`。
- `fmt(t) = MM:SS.s`（`(t % 60).toFixed(1).padStart(4,'0')`）。

### 31.2 独立撤销栈（★ 不用 tldraw history）

```ts
const history = useRef<{ past: Edit[]; future: Edit[] }>({ past: [], future: [] })
const commit = (next: Edit, record = true) => {
  if (record && editRef.current) history.current = { past: [...history.current.past.slice(1 - UNDO), editRef.current], future: [] }
  editRef.current = next; setEditState(next)
}
```
- ★ **`editRef` 与 `edit` state 双写**：ref 供 Player 回调（`new Player(src, canvas, () => editRef.current!)`）无延迟读取，state 驱动 UI；
- `slice(1 - UNDO)` = 保留最后 50 条（**负索引技巧**）；
- 首次 `commit(freshEdit(i.duration), false)` **不记录历史**；
- 拖拽裁剪时：**拖动过程中只 `setEditState`（不入库），`pointerup` 时才 push 一条历史** → 一次拖动 = 一步撤销。

### 31.3 时间线坐标系统

- `pxPerSec = (track.current?.clientWidth ?? 800) / Math.max(total, 0.1)`；
- clip 块：`left = offsets[i] * pxPerSec`，`width = clipLength(c) * pxPerSec`；
- 缩略图切片：`from = floor(c.start / duration * THUMBS)`，`to = max(from+1, ceil(c.end / duration * THUMBS))` → **24 张预缩图按 clip 范国切片复用**；
- 转场按钮：`left = offsets[i+1] * pxPerSec`（放在下一个 clip 起点）；
- 字幕块：`left = s.start * pxPerSec`，`width = max(8, (s.end - s.start) * pxPerSec)`；
- 播放头：`left = time * pxPerSec`；
- 点时间线空白处 seek：`seekTo(clientX) = ((clientX - b.left) / b.width) * total`（先 pause）。

### 31.4 裁剪拖拽（★ 手写 pointer 事件而非库）

```ts
const trim = (i, side) => (e) => {
  e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId)
  const startX = e.clientX, base = edit.clips[i], el = e.currentTarget
  const move = (ev) => {
    const delta = ((ev.clientX - startX) / pxPerSec) * base.speed      // ★ 屏幕像素 → 源时间（乘 speed）
    const value = side === 'start'
      ? Math.min(Math.max(0, base.start + delta), base.end - MIN_CLIP)
      : Math.max(Math.min(info.duration, base.end + delta), base.start + MIN_CLIP)
    ...
  }
  el.addEventListener('pointermove', move)
  el.addEventListener('pointerup', () => { el.removeEventListener('pointermove', move); history.push(...) }, { once: true })
}
```
→ 注意 `* base.speed`：**变速后，屏幕上的 1 像素对应的源时间不同**。

### 31.5 快捷键与焦点守卫

```ts
const onKey = (e) => {
  if ((e.target as HTMLElement | null)?.closest('input, textarea')) return   // ★ 输入框里不抢键
  if (e.key === 'Escape') close(); else if (e.key === ' ') toggle(); else return
  e.preventDefault(); e.stopPropagation()
}
document.addEventListener('keydown', onKey, true)                            // ★ 捕获阶段
```
注意这个 `useEffect` **无依赖数组** → 每次渲染重新注册（保证 `toggle` 闭包新鲜）。

### 31.6 导出：替换原图 vs 新建（★）

```ts
const blob = await player.current!.record()
const ref = await uploadImage(blob, `${name.replace(/\.\w+$/, '')}-edit.webm`)
const media = await toMedia(ref, 'video')
if (replace) {
  const assetId = AssetRecordType.createId()
  editor.createAssets([{ id: assetId, typeName:'asset', type:'video', props:{ name, src: viewUrl(ref), w: media.w, h: media.h, mimeType:'video/webm', isAnimated: true }, meta:{} }])
  editor.updateShape<TLShapeId>({ id: shape.id, type:'video', props:{ assetId } })   // ★ 换 assetId，shape id 不变
} else {
  const b = editor.getShapePageBounds(shape)!
  editor.select(placeMedia(editor, media, t('videoEditor.edit', name), b.maxX + 40, b.y, b.w))
}
```
→ **替换 ＝ 新建 asset + 改 shape.props.assetId**（保留位置/变换/引用）；新建 = 放到右侧 `maxX + 40` 并选中。

### 31.7 侧栏（按选中类型分支）

- `Selection = { kind:'clip'|'transition', index } | { kind:'subtitle', id } | null`；
- clip：速度 chip、音量 `0..200`（存 0..2，显示 `Math.round(v*100)`）、静音切换、删除（`clips.length < 2` 禁用）；
- transition：四种 chip、时长滑杆 `2..20` → `/10`（**0.2s..2.0s**）、**“应用到全部转场”**（`transitions.map(x => ({...x, type, duration}))`）；
- subtitle：textarea（`onKeyDown` stopPropagation）、start/end 数字框（`end` 强制 `>= start + 0.1`）、字号（`>= 8`）、`ColorPicker`、删除；
- 未选中时显示提示文案。
- 新增字幕：`{ id: uuid().slice(0,8), start: time, end: min(total, time+2), text: 占位文案, size: 56, color: '#FFFFFF' }` → **默认在当前播放头位置，长 2s**。

---

<a id="sec32"></a>
## 32. 基础 UI 设施（selection.ts / keys.ts / Popover.tsx）★★ 可直接搬

### 32.1 选区屏幕矩形（`src/ui/selection.ts` 40 行）

```ts
export interface ScreenRect { l: number; t: number; r: number; b: number }
export function useSelectionRect(): ScreenRect | null {
  return useValue('selection rect', () => {
    if (editor.getCroppingShapeId() || editor.isIn('select.resizing') || editor.isIn('select.translating')) return null
    const bounds = editor.getSelectionPageBounds() ?? undefined
    if (!bounds) return null
    const a = editor.pageToViewport({ x: bounds.minX, y: bounds.minY })
    const b = editor.pageToViewport({ x: bounds.maxX, y: bounds.maxY })
    return { l: a.x, t: a.y, r: b.x, b: b.y }
  }, [editor])
}
```
- ★ **拖拽/缩放/裁图期间返回 null** → 所有浮层自动隐藏（避免面板乱跳）；
- 用 `useValue` 而非 state → **跟着画布事务自动重算**，无需监听事件；
- `pageToViewport` 处理 zoom/pan → 浮层用 CSS `position: fixed` 直接对齐。
- `useOnlySelected<T>(type)` → `getOnlySelectedShape()?.type === type ? shape : null`；`useSelectedImage()` / `useSelectedGenerator()` 是其包装。
- `useElementSize(ref, initial)`：`useLayoutEffect`（**无依赖数组，每次渲染同步**）读 `offsetWidth/offsetHeight`，**只在变化时 setState**（`s.w === w && s.h === h ? s : {w,h}`）→ 供面板翻转定位用。

### 32.2 IME 安全的 Enter（`src/ui/keys.ts` 3 行）★★ 中文输入框必备

```ts
export const isEnter = (e: KeyboardEvent) => e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229
```
→ **中文/日文输入法组词时按 Enter 不会误发送**（`isComposing` + `keyCode 229` 双重判断）。

### 32.3 通用浮层（`src/ui/Popover.tsx` 67 行）★★ 无依赖、portal、自动翻转

- 接口：`trigger: (props: { open, toggle }) => ReactNode`（**渲染 prop 而非包一层 DOM**）、`side: 'top'|'bottom'`、`align: 'start'|'center'|'end'`、`contentKey?: string`（**内容变化时重新测量**）。
- 定位算法：
  ```ts
  left = align==='start' ? a.left : align==='end' ? a.right - p.width : a.left + a.width/2 - p.width/2
  left = min(max(left, EDGE), innerWidth - p.width - EDGE)          // EDGE = 8
  top  = side==='top' ? a.top - p.height - GAP : a.bottom + GAP     // GAP = 8
  if (top < EDGE) top = a.bottom + GAP
  if (top + p.height > innerHeight - EDGE) top = max(EDGE, a.top - p.height - GAP)   // ★ 自动翻到上方
  ```
  在 `useLayoutEffect`（**无依赖数组**）里测量 → 不闪烁；首次渲染用 `style={pos ?? { left:-9999, top:-9999 }}` 先屏外定位。
- 关闭：`document` 上 `pointerdown`（**捕获阶段**）+ `keydown` Escape；判定条外：`panel.contains(t) || anchor.contains(t) || t.closest('.ca-pop')` → **允许点另一个浮层内部不关掉自己**。
- 面板 `createPortal(…, document.body)` + `onPointerDown`/`onWheel` 都 `stopPropagation()` → **浮层内滚动/点击不会泄到画布**。

---

<a id="sec33"></a>
## 33. 小组件实现（ColorPicker / VoiceButton / AssetPicker / StoryboardEditor / RefControls）

### 33.1 颜色选择器（`src/ui/ColorPicker.tsx` 70 行）

- `SWATCHES`（12 色 Material 风）：`#000000 #FFFFFF #F5F5F5 #9E9E9E #424242 #E53935 #FB8C00 #FDD835 #43A047 #1E88E5 #8E24AA #EC407A`。
- ★ **十六进制容错三件套**：
  ```ts
  const valid = (v) => /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(v)
  const rgbOf = (v) => v.slice(0, 7).toUpperCase()
  const withAlpha = (rgb, a) => a >= 1 ? rgb : `${rgb}${Math.round(a*255).toString(16).padStart(2,'0').toUpperCase()}`
  ```
  → **alpha = 1 时不写 AA 后缀**（保持 `#RRGGBB` 短形式）。
- 输入框只存本地 `hex` state，**`onBlur` 或 Enter 才提交**（`set(hex)`）→ 避免边打字边校验；`maxLength = alpha ? 8 : 6`；`onKeyDown` 先 `stopPropagation()`。
- `allowNone` → 多一个“无”色块（`onChange('')`），触发器用 `data-none` 显示斜线；`alpha` → 额外透明度滑杆 `0..100`。
- 弹层 `side="top"`（颜色面板往上弹）。

### 33.2 语音输入（`src/ui/VoiceButton.tsx` 55 行）

- `State = 'idle'|'recording'|'processing'`；
- `getUserMedia({ audio: true })` 失败时区分 `DOMException.name === 'NotFoundError'`（无麦克风）与其他；
- `new MediaRecorder(stream)` → `onstop` 里 **先 `stream.getTracks().forEach(t => t.stop())`** 再 `transcribe(new Blob(chunks, { type: rec.mimeType }))`（**直接把 recorder 自己的 mimeType 当 Blob 类型**，不硬编格式）；
- 同一个按钮点两次 = 开始/停止（`state === 'recording' ? rec.stop() : start()`）；`data-state` 驱动样式动画。

### 33.3 资产网格（`src/ui/AssetPicker.tsx` 81 行）★ 防抖 + 无限滚动 + 竞态保护

- `TABS = [['input,output', 全部], ['output', 生成], ['input', 上传]]`；
- ★ **竞态保护用递增序号**：`const query = useRef(0)`；每次条件变化 `query.current += 1`，回调里 `if (id !== query.current) return`；
- ★ **搜索防抖 200ms**：`const timer = setTimeout(() => load(), 200); return () => clearTimeout(timer)`（依赖 `[tab, search, only]`）；
- ★ **`IntersectionObserver` 无限滚动**：`sentinel` 哨兵 div，`{ rootMargin: '300px' }`（**提前 300px 预加载**），依赖 `[next, loading]`，卸载 `disconnect()`；
- 分页累加：`setItems(prev => after && prev ? [...prev, ...list] : list)`；`only` 过滤在**前端**做（`page.items.filter(i => i.kind === only)`）；
- `Thumb` 组件：`onError={() => setBroken(true)}` → 破图降级为图标（视频/图片）；`loading="lazy" decoding="async"`。

### 33.4 分镜编辑器（`src/ui/StoryboardEditor.tsx` 38 行）

- `Scene = { prompt, duration }`；时长用 chip（`DURATIONS`）；`MAX_SCENES` 限制新增；`MAX_STORY_SECONDS` 总时长限制；
- ★ **超限不禁止，只标红**：`<span data-over={total > MAX_STORY_SECONDS || undefined}>` + 文案里拼上超限提示；
- “智能分镜”按钮：`disabled={smartBusy || !scenes.some(s => s.prompt.trim())}` → 调 `planStoryboard(所有 prompt 拼接)` 重写 scenes。

### 33.5 控制图参数（`src/ui/RefControls.tsx` 50 行）★ ControlNet 强度区间

- `RefControl = { start, end, res?, resize? }`，`DEFAULT_CONTROL` 提供默认；
- ★ **start/end 滑杆互相钳位**：start 的 `max={c.end}`、end 的 `min={c.start}`（**ControlNet 作使用区间百分比**）；
- `resolution` chip 仅当 `mode.node && 'resolution' in mode.node[1]` 时显示（`CONTROL_RESOLUTIONS`）；`resize` = `crop | stretch`；
- ★ **预览图失效标记**：`key = ${mode.id}:${c.res}`；`<img data-stale={preview.key !== key}>` → **参数改了但还没重新提取时，预览自动变灰**（不自动重跑，省算力）；
- `previewControl(src, mode, res)` 手动触发（按钮文案三态：Preview / Extracting / Re-extract）。

---

<a id="sec34"></a>
## 34. 模型/Agent 选择与工具栏（ModelPrefs / AgentPicker / RefFrameButton / SelectionLabel / chatDock / Shortcuts / VideoToolbar）

### 34.1 模型偏好（`src/ui/ModelPrefs.tsx` 52 行）

- `modelPrefsAtom = { auto: boolean, enabled: string[] }`，`saveModelPrefs(next)` 落盘；
- ★ **“自动”开关语义**：`auto = true` → Agent 自动选模型（checkbox 全部 `disabled` 且显示为勾选）；`auto = false` → **只用勾选的**；
- `missing = !!installed && !installed.includes(m.id)` → 未安装项 `data-missing` + `notInstalled` 标签 + 禁用；
- `IMAGE_MODELS` / `VIDEO_MODELS` 分 tab；每项有 `m.desc` 描述行。

### 34.2 Agent 选择器（`src/ui/AgentPicker.tsx` 79 行）

- ★ **懒加载 provider**：`onOpenChange` 打开时才 `loadProviders()`；
- `providerId = chat?.provider ?? (newProviderAtom.get() || defaultProvider())` → **已开始的对话锁定自己的 provider**；
- `pick(id)`：`newProviderAtom.set(id)` + `saveSettings({'bot-provider': id})` + **`if (chat && started) openChat(null)`**（★ 已有对话则开新会话，UI 文案提示）；
- 模型下拉 key 为动态键 `` `bot-model-${providerId}` ``（**每个 provider 存自己的模型选择**）；选项优先 `provider.model_options`（带 group 前缀 `group · label`），否则 `provider.models.map(...)`；
- `run_mode: 'ask' | 'auto'` 开关（“生成前先问我”），仅当已有 chat 可用；
- `export const botSettingsOpenAtom = atom('bot settings open', false)` → 另一个组件监听开设置页。

### 34.3 参考框按钮（`src/ui/RefFrameButton.tsx` 26 行）

- `useValue('ref frame', ...)` 返回 `{ count: media.length + notes.length, linked }` 或 null；
- 点击语义：无框 → `createRefFrame(editor)` + `setRefLinked(true)`；有框 → **切换 linked**；
- 按钮文案带计数（`Ref 3`）；title 四态提示（创建/已链接/链接/空）。

### 34.4 选区标签（`src/ui/SelectionLabel.tsx` 36 行）

- 浮在选区上方 `top = rect.t - 22`，`width = rect.r - rect.l`（**跟选区同宽**）；
- 仅对 `image`/`video`/`GENERATOR` 显示；image/video 取 `asset.props.name` + `${w} × ${h}`；**GENERATOR 取 `targetSize(props)` 算出未来尺寸**（还没生成也能看到尺寸）。

### 34.5 其他小件

- `chatDock.ts`：`dockAtom = atom<'left'|'right'>` 初始值从 `localStorage['comfyart.dock']` 读（`try/catch`）；`setDock` 写回。
- `Shortcuts.tsx`：`shortcutsAtom` + 五组快捷键表（Tools/Edit/View/Layers/Image）。**完整键位表**：
  - 工具：`V` select、`Space+drag` hand、`F` artboard、`R` rect、`O` circle、`L` line、`Shift L` arrow、`T` text、`N` sticky、`D` draw、`P` pen、`C` comment、**`A` 生图 block、`S` 生视频 block**；
  - 编辑：`Ctrl Z/Shift Z`、`Ctrl C/X/V`、`Ctrl D`、`Delete`、`Ctrl A`、`Ctrl G`/`Ctrl Shift G`、**`Shift A` tidy up**；
  - 视图：`Ctrl +/-`、`Ctrl 0` 100%、**`Shift 1` 适配画布、`Shift 2` 适配选区**；
  - 图层：`]`/`[` 到顶/到底、`Ctrl ]`/`Ctrl [` 前/后移一层、**`Ctrl E` 合并图层**、`Ctrl Shift H` 显隐、`Ctrl Shift L` 锁定、`Shift H`/`Shift V` 翻转、方向键微调 / `Shift+方向键` 10px；
  - 图片：`Shift Enter` 对话、**`Tab` 快速编辑**。
  → 这张表就是 ComfyArt 的**完整快捷键契约**，可直接对齐。

### 34.6 视频工具栏（`src/ui/VideoToolbar.tsx` 145 行）

- 按钮：裁剪（`videoEditorAtom.set(shape.id)`）/ 延长（`mode='extend'` 开面板）/ 放大（Popover 选 `VIDEO_TARGETS`）/ 抠图 / Remix（**仅当 `genInfoOf(shape)?.prompt` 存在**）/ 下载（`<a href={src} download={name}>`）。
- ★ **放大菜单预演结果尺寸**：`over = max(w,h) >= long` 则禁用 + 提示“原视频已超过”；否则显示 `` `${round(w*long/max)}×${round(h*long/max)}` ``（**先告诉用户会变成多大**）。
- 定位：`top = rect.t - GAP - 44`，`if (top < EDGE) top = rect.b + GAP`（**上方不够就放下方**）；水平居中。
- `ExtendPanel`：`textarea autoFocus` + `DURATIONS` chip（`+Ns`）；`useEffect(() => setMode(null), [shape?.id])`（**换选中就收面板**）；Escape 捕获阶段关闭。
- `place()` 通用定位：`top = rect.b + 12`，`if (top + h > host.height - 72) top = max(EDGE, rect.t - h - 34)`（**72 = 底部工具栏高度预留**）。

---

<a id="sec35"></a>
## 35. 输入槽位 / 调色盘 / 资产列表 / id（genInputs.ts / palette.ts / assets.ts / id.ts）

### 35.1 参考图槽位（`src/genInputs.ts` 46 行）★

- `Target = 'ref' | 'base' | 'last' | 'motion' | 'speech'` → **五种输入槽位**（参考图 / 底图 / 尾帧 / 动作视频 / 音频）；
- `canvasPickAtom = atom<{ id: TLShapeId; to: Target } | null>` → **“从画布拾取”模式的全局状态**（带目标槽位）；
- `isMediaRefs(shape) = isVideo(workflow) && !Array.isArray(meta.scenes) && videoModeOf(shape) === 'refs'` → 参考图生视频模式；
- ★ **槽位上限按模式算**：`maxRefsOf = isMediaRefs ? sum(MAX_VIDEO_REF_KINDS) : isVideo ? 1 : refLimit(model)`；
- ★ **类型门禁**：`acceptsKind(shape, to, kind)`：`motion` 只收 video、`speech` 只收 audio、其他只收 image（**但 `to==='ref' && isMediaRefs` 时 image/video/audio 全收**）；
- ★ **`fillTarget` 的自动适配**（填动作视频时）：
  ```ts
  const { seconds, aspect } = await mediaMeta(src, 'video')
  const props = { ...shape.props, ratio: nearestRatio(aspect, VIDEO_RATIOS),
                  duration: DURATIONS.filter(d => d <= seconds).pop() ?? DURATIONS[0] }
  const [w, h] = targetSize(props)
  ```
  → **丢入一个动作视频，自动把生成块的比例/时长/尺寸对齐到该视频**（不靠用户调）；
- 参考图去重 + 截断：`refs.includes(x)` 过滤，普通模式 `[...refs, ...added].slice(-maxRefsOf)`（**保留最新的 N 张**），refs-to-video 走 `capRefs`（按种类限额）。

### 35.2 颜色→颜文字（`src/palette.ts` 52 行）★★ 给模型上“颜色语言”

- `NAMES`：**48 个命名色**（black/charcoal/…/peach）带 RGB；
- ★ **非均匀 RGB 距离公式**（接近人眼感知，不是简单欧氏）：
  ```ts
  const rm = (r + r2) / 2
  const d = (2 + rm/256) * (r - r2)**2 + 4 * (g - g2)**2 + (2 + (255 - rm)/256) * (b - b2)**2
  ```
  → **红/蓝通道权重随亮度变化，绿通道固定 ×4**；
- `palettePrompt(colors) = 'Use this color palette: ' + colors.map(c => `${colorName(c)} (${c})`).join(', ') + '.'` → **同时给出颜色名和十六进制**（模型对颜色名更稳，十六进制更精确）；
- ★ `paletteImage(colors)`：生成 **24×24 个 16px 方块**的随机拼贴 PNG（**用 LCG 伪随机 `seed = (seed*1103515245 + 12345) % 2147483648`，初始 seed=7** → **可复现的色板图**，用作参考图）；
- `alphaOf(color) = color.length === 9 ? parseInt(color.slice(7),16)/255 : 1`。

### 35.3 资产列表（`src/assets.ts` 35 行）

- `PAGE = 60`；游标分页 `after` + `next_cursor`；查询参数 `tags_any`（**逗号分隔多标签**）、`limit`、`name_contains`、`after`；
- `res.status === 503` → 抛 `t('assetPicker.disabled')`（**资产库可被后端禁用**）；
- 前端再过滤：只保留 `mime_type` 前缀为 image/video 且有 `preview_url` 的；`w/h` 从 `metadata.width/height`（**缺失时为 0**）；
- ★ `placeAsset`：放到**视口中心**（`editor.getViewportPageBounds().center`），宽度 `min(m.w, 512)`，**按宽高比算高**，放完 `editor.select(id)`。

### 35.4 `id.ts`（7 行）

手写 v4 UUID（`crypto.getRandomValues` + 版本/变体位掩码 `b[6] = (b[6] & 0x0f) | 0x40`、`b[8] = (b[8] & 0x3f) | 0x80`）→ **不依赖 uuid 库**；调用方常用 `uuid().slice(0,8)` 作短 id。

---

<a id="sec36"></a>
## 36. 快捷键覆盖 / 矢量 / 转 PPT / 嵌入入口 / 富文本 ★★

### 36.1 tldraw 键位覆盖（`src/overrides.ts` 50 行）★ 一行代码改键位

```ts
const KBD: Record<string,string> = {
  'toggle-lock': 'cmd+shift+l,ctrl+shift+l', 'bring-forward': 'cmd+],ctrl+]', 'send-backward': 'cmd+[,ctrl+[',
  'flatten-to-image': 'cmd+e,ctrl+e', 'zoom-to-100': 'cmd+0,ctrl+0',
}
const UNBOUND_TOOLS = ['eraser','draw','laser','highlight']       // ★ 直接去掉这四个工具的快捷键
overrides = { tools(_, tools) { ... kbd: undefined }, actions(editor, actions) { ... } }
```
- ★ **自定义 action 直接注册**：`ca-tidy`（`shift+a` → `arrange`）、`ca-chat`（`shift+enter` → `openCanvasChat`）、`ca-hide`（`cmd/ctrl+shift+h` → **按当前状态批量切 `meta.hidden`**：`const hidden = !shapes.every(s => s.meta.hidden)`）；
- `actions['select-geo-tool'] = { kbd: undefined }` → **释放 `R` 给自定义用途**；
- ★ 启示：**不要自己写快捷键系统，直接改 tldraw 的 action.kbd**，菜单/提示自动同步。

### 36.2 矢量路径（`src/vectorPath.ts` 54 行）★★

- `PenNode = { x, y, hx, hy }`（**位置 + 手柄长度**，对称手柄）；
- ★ **`nodesToPath` 归一化存储**：先算包围盒（**包含手柄极值** `x±hx`），再把所有坐标归一到 `0..1`，返回 `{ x, y, w, h, d }` → **shape 缩放时路径自动拉伸**；
  贝塞尔控制点：`C (a.x+a.hx) (a.y+a.hy) (b.x-b.hx) (b.y-b.hy) b.x b.y`（**前点右手柄 + 后点左手柄**）；闭合时补 `seg(last, first) + 'Z'`；
- ★ **`scalePath(d, w, h)` 本身不解析 SVG**，而是用正则 `[a-zA-Z]|-?\d*\.?\d+(?:e[-+]?\d+)?` 扫 token，**按命令字母判断当前轴**：`PAIRED = 'MLCSQTmlcsqt'` 内→ x/y 交替；`H/h` → 全 x；其他 → 全 y；数值 `round(v*1e5)/1e5`；
  → **几十行就能安全缩放任意 SVG path**（不依赖库）；
- `loadPaper()`：**单例懒加载** `paper/dist/paper-core`（`loaded ??= import(...)`）+ `paper.setup(new paper.Size(1,1))`；
- `normalized(paper, item)`：`item.clone({ insert:false })` → `translate(-b.x,-b.y)` → `scale(1/w, 1/h, (0,0))` → 取 `copy.pathData`（**用 paper.js 做布尔/归一化，不自己写**）。

### 36.3 位图→矢量（`src/vectorize.ts` 49 行）

- 用 `imagetracerjs`；`OPTIONS = { numberofcolors: 16, ltres: 1, qtres: 1, pathomit: 8, colorquantcycles: 3, roundcoords: 1, viewbox: true }`；
- 先缩到 `MAX_SIDE = 1024` 再 `imagedataToSVG(ctx.getImageData(...))`；产物当 SVG 上传，**放为新图块**（`b.maxX + 40`）；
- ★ **`svgRaster(src, w, h, scale)`：SVG 栅格化缓存 + 自适应分辨率**：
  ```ts
  const px = Math.min(RASTER_MAX(4096), RASTER_STEP(512) * 2 ** Math.max(0, Math.ceil(Math.log2(Math.max(w,h) * scale / RASTER_STEP))))
  const key = `${src}|${px}`; if (!rasters.has(key)) rasters.set(key, ...)
  ```
  → **按显示缩放选“下一个 2 的幂”分辨率**，`Map<string, Promise<string>>` 缓存 **Promise 而非结果**（并发去重），失败 `.catch(() => src)` 回退原图。

### 36.4 一键转 PPT（`src/slides.ts` 67 行）★★ 纯前端排版

- 常量：`SLIDE_W=1280 SLIDE_H=720 SLIDE_GAP=80 PAD=64 IMAGE_W=520 TITLE_GAP=28 IMAGE_PX=[832,1024]`；
- ★ **`textHeight()` 用 canvas `measureText` 逐字符模拟换行算高度**（`ctx.font = `${weight} ${size}px Arial``，逐 `ch` 累加超宽就 `lines++`）→ **不依赖 DOM 就能知道文字块多高**（中文逐字符换行也对）；
- 每张幻灯片 = 一个 **frame**（`type:'frame'`，`name = `${title} · ${i+1}``），内部 `parentId: frame` 放标题（首页 64px、其他 44px，`weight:700`，`autoSize:false` + 固定 `w`）与正文（`size:26`、`line:1.6`、`• ` 前缀）；
- ★ **带图的幻灯片直接放一个“生成块”而不是图**：`createGenerator(editor, false, { prompt: s.image, resolution:'custom', cw:832, ch:1024 })` → `reparentShapes([gen], frame)` → 调尺寸到 `IMAGE_W × (SLIDE_H - PAD*2)`；最后 `for (const id of generators) void generate(...)` → **文字排版先落位，配图异步生成**；
- 收尾：`editor.select(...frames)` → `zoomToSelection({ animation:{duration:200} })` → `selectNone()`。

### 36.5 嵌入文件入口（`src/embeds.ts` 80 行）

- `MODEL_EXTS = ['glb','gltf','obj','fbx','stl','ply']`；`LOTTIE_LONG=480`、`MODEL_SIZE=480`、`HTML_SIZE=[960,640]`；
- ★ **`.lottie` 格式解包**：`unzipSync(new Uint8Array(await file.arrayBuffer()))`（fflate）→ 找 `animations/*.json`；
- ★ **`.json` 智能判断是否 Lottie**：`data && Array.isArray(data.layers) && data.fr` → 是才当 Lottie（**否则不当**）；
- Lottie 尺寸：`k = 480 / max(data.w ?? 480, data.h ?? 480)` → **长边固定 480**；
- `finish()`：`select(id)` + `zoomToSelectionIfOffscreen(64, { animation:{duration:200} })`（**★ 只有当新 shape 在屏外才缩放**）；
- `spot()`：默认放视口中心（`at` 可指定，**支持右键菜单落点**）。

### 36.6 富文本（`src/richText.tsx` 84 行）★★ tiptap 与 tldraw 共存

- ★ **只启用需要的 tiptap 扩展**：`StarterKit.configure({ heading:false, blockquote:false, codeBlock:false, code:false, horizontalRule:false, bulletList:false, orderedList:false, listItem:false, listKeymap:false, link:false, trailingNode:false })` + `TextStyle, Color, FontFamily, FontSize` → **画布文字只需粗体/斜体/下划线/删除线/颜色/字体/字号**；
- ★ **`RichView` 自己渲染 JSON，不用 tiptap**（`markStyle` 把 marks 转 CSS）→ **非编辑态零 tiptap 开销**（每个文字 shape 都实例化 tiptap 会爆炸）；空段落用零宽字 `​` 占位；
- `docFromText(text)` / `plainOf(doc)` → **纯文本 ↔ 富文本双向兼容**（旧数据无缝升级）；
- `hasMarks(doc)` → 判断是否真的带样式（**只有带样式才走富文本渲染路径**）；
- `richEditorAtom`（当前编辑器实例，供工具栏操作）+ `richVersionAtom`（`onTransaction` 里 `update(v => v+1)` → **工具栏能响应选区/样式变化重渲染**）；
- `handleKeyDown` 里 `e.stopPropagation()` + `Escape`/`Ctrl+Enter` 退出（**Ctrl+Enter 提交，Enter 继续输入**）；`autofocus: 'all'`。

---

<a id="sec37"></a>
## 37. 动作层（`src/actions.ts` 313 行）★★★ 所有 AI 图像动作的统一封装 + mask 数学

### 37.1 统一封装 `run()`（★ 核心）

```ts
function run(editor, shape, label, onError, build: (src: Source) => Promise<Built>,
             opts: { size?: [number,number]; kind?: MediaKind; placer?: string; extra?: (src) => Record<string,unknown> } = {}) {
  const src = source(editor, shape)                       // { image: annotated(asset.src), name, w, h }
  if (!src) return
  const holder = spawnHolder(editor, shape, opts.size)    // ★ 先在原 shape 旁生成一个“占位块”
  void startJob(editor, holder, build(src), { name: `${src.name} · ${label}`, kind, placer, extra }, onError)
  return holder
}
```
→ **所有 AI 动作都是三行**；`source()` 统一做 `annotated(asset.props.src)`（★ 参考图标记在这一层统一加，不在各 graph 里重复）。

### 37.2 动作清单（全部一行定义）

`upscale` / `creativeUpscale(factor, style, strength)` / `cutout` / `quickEdit(prompt)` / `angles(h,v,z)` / `expand(pads, prompt)` / `animate(prompt, loop=true)` / `upscaleVideo(long)` / `cutoutVideo` / `extendVideo(prompt, duration)` / `styleTransfer(style, ref)` / `splitLayers(layers, prompt)`（`placer:'layers'`，`extra: { source, base }`）。

- ★ **`editText(edits: [from,to][])` 把结构化编辑编译成自然语言**：
  `Change the text "X" to "Y" in the same font, color, size and position` / `Remove the text "X" and fill its area with the surrounding background`，末尾统一加 `. Keep everything else in the image unchanged.`
- ★ **`expand` 预先算好结果尺寸**：`k = shape.props.w / src.w`（屏幕/原图像素比），`size = [(src.w + pads.l + pads.r) * k, (src.h + pads.t + pads.b) * k]` → **占位块一开始就是最终大小**（不会跳）。

### 37.3 mask 生成与上传（★★）

```ts
export async function maskFile(w, h, paint: (ctx) => void): Promise<string> {
  ctx.fillStyle = '#fff'; ctx.fillRect(0,0,w,h)
  ctx.globalCompositeOperation = 'destination-out'      // ★ 白底 + 用 destination-out 挖洞
  paint(ctx)
  ...上传...
  return `${ref.subfolder ? ref.subfolder + '/' : ''}${ref.filename} [${ref.type}]`   // ★ 后端文件引用格式
}
```
- `drawStrokes(ctx, strokes, w, h)`：`lineWidth = s.r * 2 * w`（**半径 r 归一化到宽度**），点坐标 `(u,v)` 为 `0..1` 分数；单点补 `lineTo(x*w + 0.01, y*h)`；
- `maskBounds(mask)`：逐像素扫 `alpha >= 16` 求包围盒（**阈值 16 过滤抗锯齿边缘**）；
- `lumaToAlpha(img, w, h)`：`data[i+3] = data[i]`（**亮度→alpha**），RGB 全设 255 → **把模型输出的白底黑掩膜变成透明掩膜**；`getContext('2d', { willReadFrequently: true })`。

### 37.4 ★★ `cropAround`：局部编辑的“智能裁剪”（省算力 + 提质量）

```ts
const TOUCH_PAD = 0.25, TOUCH_MIN = 384, TOUCH_POINT = 0.3
const grow = (len, full) => Math.min(full, Math.max(len * (1 + TOUCH_PAD*2), TOUCH_MIN))   // ★ 四周各扩 25%，但最小 384px
const cw = Math.min(w, Math.round(grow(region.w, w) / 8) * 8)                              // ★ 8 的倍数（模型/VAE 对齐）
return { x: clamp(region.cx - cw/2, cw, w), y: ..., w: cw, h: ch }
```
→ **不送全图，只送“目标区域 + 25% 上下文”，并向上对齐到 8 的倍数**；`feather = max(4, min(cw,ch) * 0.04)`（**羽化随区域大小自适应**）。

### 37.5 四个局部编辑入口（★ 不同一 mask 来源）

| 函数 | mask 来源 | 关键细节 |
|---|---|---|
| `inpaint(mask, feather, prompt)` | 用户手绘 canvas | `k = src.w / mask.width` 回算原图坐标；`ctx.filter = 'blur(Npx)'` 直接应到 mask 上 |
| `editRegion(region, prompt)` | 矩形 | mask 用 `fillRect(region - crop)`，feather 自动 |
| `moveObject(mask, dx, dy)` | 手绘 mask + 拖拽量 | `from = maskBounds*k`，`to = from + d*k` |
| `moveTarget(target, dx, dy)` | **SAM 自动分割** | 先 `samGraph` → `waitForImages` → `lumaToAlpha` → `maskBounds` → 失败抛 `t('actions.noObjectFound')` |

★ `ctx.filter = 'blur(Npx)'` 是 Canvas2D 原生高斯模糊 → **羽化零依赖**。

### 37.6 评论→图像动作（★★ 评论不只是标记，能直接驱动 AI）

- `imageUnder(editor, box)`：`getCurrentPageShapesSorted().filter(image).filter(bounds.containsPoint(box.center)).at(-1)` → **按层级取中2心下的最上层图**；
- `markObject(comment)`：把评论中心点 + `meta.points` 多点多提示 → `samGraph(src.image, { points })` → **把分割结果再上传成掩膜图** → 存到 `comment.meta.mark = { image: shape.id, src: url, box }`；三态 `MarkState = Mark | 'loading' | 'none'`；
- `commentTarget(comment)`：统一算出 `{ shape, src, marked, region }`；`point` 评论默认区域边长 `side = min(src.w, src.h) * TOUCH_POINT(0.3)`；
- `touchEdit(comment)`：mask 三种画法 —— **已分割对象：`for (i<3) ctx.drawImage(object, -crop.x, -crop.y)`（★ 画 3 次叠加加深 alpha，把软边缘坐实）**；`lasso`：`props.path` 分数坐标展开到 region；`point`：`ellipse`；否则 `rect`；
- 完成后 `notify({ kind:'comment', title: 成功/失败, body: comment.text, srcs: r.srcs })` → **评论线程里直接挂结果图**。

---

<a id="sec38"></a>
## 38. ⚠️ 作废：图层系统（`src/layers.ts`）——**真实内容见 §89.1**

> 本节里的 `layerName / layerId / layerOf / layerNames / ensureLayer / layersOf / layerBounds / layerSummary /
> LayerSummary / layerImage / layerImageBounds / layerPrompt / layerPromptFor / layerPromptForRefs / layerOfName /
> isLayerGroup / getParentIds` **均不存在于仓库**（grep 零命中）。
> ★ `src/layers.ts`（276 行）实际是**三种“把一张图拆成多张透明 PNG + frame”的拆图路径**（AI 分层清洗 / 纯前端按框拆 / SAM 拆）。
> ★ **ComfyArt 没有“图层组 = group + meta”这套模型**，请勿按本节照抄。详见 **§89.1**。

### 38.1 数据模型（★ 不新增 shape 类型）

- **图层 = tldraw 的 `group` shape**；`meta.layer = true` 标记；`meta.hidden` / `meta.locked` 存在 shape meta 上；
- ★ **`layerName(editor, group)` 从组名里拆出“可见性后缀”**：`/^(.*) · (visible|hidden)$/` → 名字与状态分开存（**状态写进 name，方便导出/搜索**）；
- `layerId(editor, shape)`：`getParentIds(shape.id).find(id => isLayerGroup(getShape(id)))` → **沿父链往上找第一个图层组**；
- `layerOf(editor, shape)` = `layerName(...) ?? t('layers.unorganized')`；`layerNames(editor)` 去重列表。

### 38.2 关键操作

- `ensureLayer(editor, name)`：按 `name` 找现有图层组，没有则 `editor.createShapes([{ type:'group', name, props:{ w:100,h:100,x,y }, meta:{ layer:true } }])`（位置取当前页 shape 包围盒左上）；
- `layersOf(editor)`：`getCurrentPageShapes().filter(isLayerGroup).filter(g => getChildren(g.id).length > 0)` → **空组不算图层**；
- `layerBounds(editor, g)` = `getShapePageBounds(g)`（**group 自动包含子元素包围盒**）；
- `layerSummary(editor)` → `LayerSummary { id, name, visible, locked, count, w, h, elements: LayerElement[] }`（**给 Agent/MCP 读的结构**）；
- `layerImage(editor, g)`：`getChildren(g.id).filter(isMediaShape)` → **图层里的媒体图 URL 列表**（供“以图层为参考图”）；
- `layerImageBounds(editor, g, src)`：按 src 反查该图的 pageBounds（**把图层内图片与页面坐标对应起来**）；
- `layerPrompt(editor, g)`：拼英文 prompt（图层名 + 元素名 + 位置）→ **把图层结构翻译成模型能懂的空间描述**；
- `layerPromptFor(editor, shape)`：`layerOf(shape) === unorganized ? '' : layerPrompt(editor, layer)` → **生成时自动带上“所在图层”的上下文**；
- `layerPromptForRefs(editor, refs)`：多个参考图共用同一图层时返回该图层 prompt（**多参考图同一图层 → 告诉模型它们在同一层**）；
- `layerOfName(editor, name)`：按名找图层（**Agent 可用名字定位图层**）。

### 38.3 对 Firefly 的启示

用 **group + meta** 实现图层，而不是自定义图层容器：
- 免费得到 tldraw 的 group 拖拽/缩放/选中/嵌套/撤销；
- `meta.hidden` 配合自定义 action 批量切换；
- 图层名带状态后缀 → **导出 JSON 就能人读**；
- 图层→prompt 是把“画布结构”送进模型的关键桥梁。

---

<a id="sec39"></a>
## 39. ⚠️ 作废：`src/elements.ts` 150 行——**真实内容见 §89.2**

> 本节里的 `isMediaShape / isRefShape / isRefSource / mediaSize / mediaName / imageName / mediaKind / mediaMeta /
> isHiddenShape / isMediaShapeType / isRefShapeType / arrangeShapes / eachTargets / eachLine / refFrameFor / isGenerator`
> **均不存在于仓库**（grep 零命中）。
> ★ `src/elements.ts` 实际是 **OCR + SAM 把设计图拆成「可编辑文字层 + 物体层 + 干净背景」**（`editElements` + `registerPlacer('textLayers')`）。
> 详见 **§89.2**。

- `isMediaShape` = image | video | audio | MODEL | LOTTIE | HTML；`isRefShape` = 媒体 + **`isGenerator`（生成块也能当参考图！）**；
- `isRefSource(shape)` = `isRefShape && !isLocked(shape)`；
- `mediaSize(editor, shape)` → 按类型取尺寸（generator 用 `targetSize(props)`）；
- `mediaName(editor, shape)` → generator 用 prompt 前 40 字；
- `imageName(editor, shape)` → 资产名或 prompt；
- `mediaKind(shape)` → `'image'|'video'|'audio'`；
- `mediaMeta(shape)` → `{ src, kind, name, w, h }`（**统一给 UI/Agent**）；
- `isLocked(shape)` = `!!shape.meta.locked`；`isHiddenShape` = `!!shape.meta.hidden`；
- `canEdit(shape)` = `!isLocked && !isHidden`；
- `isMediaShapeType(t)` / `isRefShapeType(t)` 纯类型版；
- `placeMedia(editor, media, name, x, y, w)` → 统一落画布（image/video/audio 分派），返回 shape id；
- `placeAsset(editor, item)` → 见 §35.3；
- `zoomToSelectionIfOffscreen(editor, margin, animation)` → **新内容不在视口内才缩放**（避免无谓跳动）；
- `tidyUp(editor)` → `arrangeShapes` 自动排版（`Shift A`）；
- `createRefFrame(editor)` / `setRefLinked(editor, on)` / `refFrameFor(editor)` → **参考框（把参考图围起来，与生成块链接）**；
- `eachTargets(refs)` / `eachLine(refs, targets)` → **每个参考图不分别生成**（见 §23.4）。

---

<a id="sec41"></a>
## 41. 后端/ComfyUI 接入层（`src/comfy.ts` 151 行）★★ 所有网络原语

### 41.1 类型与身份

- `PromptGraph = Record<string, { class_type: string; inputs: Record<string, unknown> } }` → **ComfyUI API 格式（节点 id → class_type + inputs）**；
- `ImageRef = { filename, subfolder, type, metadata?: { width?, height? } }`；
- ★ **多用户隔离**：`localStorage['Comfy.userId']` → 每个请求加 `Comfy-User` 头（`api()` 统一注入）。

### 41.2 用 ComfyUI 的 userdata 当数据库（★★ 零后端开发）

```ts
readUserText(path)   → GET    /userdata/{path}                 // 404 → null
readUserJson(path, fallback)
writeUserText(path)  → POST   /userdata/{path}?overwrite=true&full_info=false
deleteUserFile(path) → DELETE /userdata/{path}
listUserFiles(dir, recurse) → GET /userdata?dir=...&recurse=true&split=false&full_info=false   // 404 → []
                                                    // ★ 返回路径统一 .replaceAll('\\','/') 归一化 Windows 分隔符
```
→ **项目/对话/记忆/技能/定时任务全部当 JSON 文件存在 ComfyUI 的 userdata 里**（见 §14/§15/§16）。

### 41.3 媒体 URL 与类型推断

- `viewUrl(ref)` → `/api/view?filename=&subfolder=&type=`；
- ★ `thumbUrl(src, max=256)` → `/comfyart/thumb?url=&max=`（**自己代理一层缩图服务**，列表页不加载原图）；
- `refFromUrl(src)` → 从 `/api/view?...` 反解回 `ImageRef`；
- ★ **类型靠扩展名**：`refKind(src)` 看 `filename` 后缀（`VIDEO_EXTS = mp4/webm/mov/mkv`，`AUDIO_EXTS = mp3/wav/flac/ogg/m4a/opus`，否则 image）；
- `mediaMeta(src, 'video'|'audio')` → **创建一个游离的 `<video>`/`<audio>` 元素读 metadata**（`document.createElement(kind)`），返回 `{ seconds, aspect }`（音频 aspect 固定 1）。

### 41.4 上传与工作流执行

- `uploadImage(file, name)` → `POST /upload/image`（`FormData`: `image`, `subfolder='comfyart'`, `type='input'`）→ **所有产物统一落在 `comfyart` 子目录**；
- `loadWorkflow(name)` → `GET /comfyart/workflows/{name}.api.json`（**工作流 JSON 是静态资源**）；
- ★ **`withModelPaths(prompt)`**：拉 `/comfyart/model-paths` 得到 `{ 模型名 → 实际路径 }`，**递归把 inputs 里所有“名字命中映射表”的字符串替换成真实路径** → **工作流 JSON 里只写模型名，部署时自动适配本地目录**；
- `queuePrompt(prompt)` → `POST /prompt`（带 `client_id`）；错误取 `body.error.message`；
- ★ **`waitForOutput` 轮询而非 WS**（`POLL_MS = 1500`）：
  ```ts
  const hist = (await api(`/history/${promptId}`))[promptId]
  const err = (hist.status?.messages ?? []).find(m => m[0] === 'execution_error')
  if (err) throw new Error(String(err[1]?.exception_message ?? 'execution failed'))
  const value = hist.outputs?.[node] ? pick(hist.outputs[node]) : null
  if (value) return value
  if (hist.status?.completed) throw new Error('the workflow produced no output')   // ★ 完成但无输出→报错，不无限轮询
  ```
- `waitForImages` 合并 **`images | videos | gifs | audio | '3d'`** 五种输出字段 → **一个函数兼容所有媒体输出**；
- `waitForText` → `out.text.join('')`。

---

<a id="sec42"></a>
## 42. LLM 调用与容错解析（`src/llm.ts` 112 行）★★★ 接 LLM 必备工具

### 42.1 ★★ 容错 JSON 提取（不靠 `JSON.parse` 直接试）

```ts
function firstObject(text: string): string {
  const start = text.indexOf('{')
  let depth = 0, quoted = false
  for (let i = Math.max(start, 0); i < text.length; i++) {
    const c = text[i]
    if (quoted) { if (c === '\\') i++; else if (c === '"') quoted = false }
    else if (c === '"') quoted = true
    else if (c === '{') depth++
    else if (c === '}' && --depth === 0) return text.slice(start, i + 1)
  }
  return text.slice(start)
}
export function stripThink(raw) { return raw.replace(/<think>[\s\S]*?<\/think>/g, '').replace(/^[\s\S]*<\/think>/, '').trim() }
export function parseJson(raw) { try { return JSON.parse(firstObject(stripThink(raw))) } catch { return null } }
```
→ **手写括号深度 + 引号/转义扫描**，从任意乱文本（含 markdown 围栏、前后废话、未闭合 think 标签）里取出**第一个完整 JSON 对象**；`stripThink` 还处理“只有闭合 `</think>` 前缀”的脏输出。**失败返回 null 而不抛** → 调用方统一降级。

### 42.2 统一入口

- `ask(prompt, system, images)` → `POST /comfyart/bot/complete`（**多模态：直接传图片 URL 数组**）；错误取 `body.error`；
- `transcribe(audio: Blob)` → 先 `uploadImage(audio, 'voice-{ts}.webm')` 再 `transcribeFile(viewUrl(ref))`（**语音转文字 ＝ 上传 + 工作流**）；
- `transcribeFile` → workflow `transcribe-gemma4.api.json`，**动态插一个节点** `g.a = { class_type:'LoadAudio', inputs:{ audio: annotated(src) } }`，再把 `g['2'].inputs.audio = ['a', 0]`（**★ 前端直接往工作流图里加节点/接边，而不是预先准备 N 个变体**）；prompt：“Transcribe the speech exactly… Write Chinese in simplified characters.”，system：“You are a speech recognition engine.”

### 42.3 OCR（★★ 两套引擎 + 进度可视化 + 缓存）

- `OCR_PROMPT`（原文）：“List every piece of text visible in this image exactly as written, one text block per line, from top to bottom. Output only the text lines, nothing else.”；`OCR_SYSTEM`：“You are a precise OCR engine.”
- `ocrCache = new Map<string, Promise<string[]>>()`，**key = `${engine}|${src}`**（**缓存 Promise 而非结果 → 并发自动去重**）；
- ★ **失败不缓存**：`job.catch(() => ocrCache.delete(key))`；
- ★ **进度状态机**：`OcrStatus = { stage: 'queued'|'loading'|'reading'|'agent', tokens }` 存在 `ocrStatusAtom`（**按图片 src 作 key**）；
  - `setStatus` 用 **解构重建对象**（`const { [src]: _, ...rest } = get()`）→ **不原地改 atom**；
  - 通过 `onComfyEvent('executing'|'progress')` 推 stage，**并主动查 `GET /queue` 看任务是否已在运行**（防错过事件）；
  - `finally { stops.forEach(s => s()) }` 取消事件订阅；
- 结果归一化：`[...new Set(text.split('\n').map(l => l.trim()).filter(Boolean))]`（**去重去空行**）；
- 引擎选择：`settingsAtom['ocr-engine'] === 'agent' && botEnabledAtom` → 走 `ask()`，否则走本地工作流。

---

<a id="sec43"></a>
## 43. 技能系统（`src/skills.ts` 119 行）★★★ 兼容 Agent Skills 格式

### 43.1 数据模型

```ts
Skill = { id, name, instructions, files?: Record<string,string>, images?: Record<string,string>, custom?: boolean, learned?: string[] }
```
- ★ **存储格式 ＝ `SKILL.md` + YAML frontmatter**（`---\nname: X\n---\n\n正文`）→ **与 Anthropic/Claude 的 Agent Skills 目录格式兼容**；
- `parseMarkdown` 正则：`/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/` + `/^name:\s*(.+)$/m`；**无 frontmatter 则整篇当 instructions**；
- 目录结构：`comfyart/skills/{id}/SKILL.md`、`/images.json`、`/learned.json`、`/files/**`、`/versions/{ts}.json`；
- `loadSkills()`：`listUserFiles(dir, recurse=true)` → **按第一个路径段分组** → 含 `SKILL.md` 的才算技能 → **自定义技能排在内置前面**（`[...custom, ...BUILTIN_SKILLS]`）。

### 43.2 ★ 10 个内置技能（prompt 原文可直接参考）

| id | 要点 |
|---|---|
| `poster` | “strong headline typography **in quotes**, clear visual hierarchy, one hero subject, generous margins. Default ratio 3:4. Offer 2 distinct directions when the brief is open.” |
| `social` | 一套三尺：1:1 feed + 4:5 post + 9:16 story，**共享 palette/typography/motif**；文字短、手机上可读 |
| `brand` | logo mark（1:1 纯色背景）+ 色板 + 文具/mockup 场景；**精确描述形状、hex 颜色、字体风格** |
| `packaging` | 正视图（干净棚拍背景）→ 3/4 角度 lifestyle mockup；提材质/工艺/标签布局 |
| `thumbnail` | 16:9，大主体，**2-4 词粗体标题 in quotes**，高对比，小尺寸可读，给 2 个变体 |
| `banner` | 21:9 或 16:9，**标题一侧、视觉另一侧，留裁切安全边**，CTA 文字 in quotes |
| `character` | 角色设定表：一张 16:9 里 **正/侧/背全身** + 表情特写表；服装颜色全一致 |
| `product` | 电商图：白底 hero（1:1）+ lifestyle + 细节特写；**产品必须一致，有参考图则用之作产品** |
| `motion` | **先 `generate_image` 做 3:4 海报 → 等完成 → `animate_image` 微动（loop on），保证文字可读**（★ 技能里直接写工具调用顺序） |
| `interior` | 写实室内/建筑：eye-level 广角、自然光、准确材质、16:9；再加 4:3 细节 vignette |

### 43.3 ★ 技能版本与导入

- `saveSkill`：**写前先备份旧版** → `write('{id}/versions/{Date.now()}.json', JSON.stringify(current))`；然后写 SKILL.md / images.json / learned.json / files/**；**最后删除本次不再存在的旧 files**（`paths.filter(startsWith('files/') && !(path in files))`）→ **完整的增删同步**；
- `listVersions(id)`：按文件名（时间戳）**倒序**；
- ★ `slug(name)`：`toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g,'-')` → **保留中文**，空则 `uuid().slice(0,8)`；
- ★ `importFolder(FileList)`：用 `webkitRelativePath` 找 `SKILL.md` → 以它为根，**文本类扩展名（md/txt/json/csv/yaml/html/css/js/ts/svg）存原文，图片（png/jpg/webp/gif）上传成 URL，其他进 `skipped` 列表** → **直接拖一个 Claude 技能文件夹进来就能用**。

---

<a id="sec44"></a>
## 44. 品牌库 / 通知 / 定时任务（library.ts / notices.ts / schedules.ts）★★

### 44.1 品牌与角色库（`src/library.ts` 65 行）

- `LibraryKind = 'brand' | 'character' | 'product' | 'custom'`；
  `LibraryItem = { id, kind, name, description, images[], colors[], fonts[], roles?, logos?, isDefault?, fontFiles?: FontFile[], cover? }`；
- ★ **`COLOR_ROLES = { primary, secondary, accent, background, text }`** → **颜色带角**色（不是光一串 hex）；
- ★ **字体真正加载**：`registerFonts(list)` 用 **`new FontFace(family, `url("${src}")`).load().then(face => document.fonts.add(face))`**，`loadedFonts: Set<string>` 防重复 → **品牌字体能在画布文字里直接用**（`brandFonts()` 供字体下拉）；
- `itemImages(item) = [...(logos ?? []), ...images]` → **logo 永远排在参考图前面**；
- `defaultKitId()` → `kind==='brand' && isDefault` → **默认品牌 kit**；
- ★ **`describeItem(item)` 把 kit 翻译成一句英文**（送模型）：
  `brand kit "NAME"; 描述; colors: primary #xxx, accent #yyy; 2 logo image(s) attached first; fonts: A, B; 5 reference image(s) attached`
  → **角**色 + 颜**顺序**都写进 prompt。

### 44.2 通知中心（`src/notices.ts` 55 行）

- `NoticeKind = 'task' | 'suggestion' | 'comment' | 'schedule'`；`Notice = { id, kind, title, body?, time, read?, project, chat?, srcs? }`；
- `MAX_NOTICES = 200`（`slice(0, 200)` 自动淘汰）；**按 `project` 隔离**（`currentProject()` 从 URL `?project=` 取）；
- ★ **多标签页同步**：`new BroadcastChannel('comfyart-notices')`，写后 `postMessage('changed')`，收到就 `loadNotices()`；
- ★ **写时重读**：`update(fn)` = `fn(await read())` → **先拉最新再改**（不拿内存列表直接写，避免多 tab 互相覆盖）；
- ★ **系统推送只在页面隐藏时**：`if (document.hidden && pushEnabled() && Notification.permission === 'granted') new Notification(title, { body, icon: srcs?.[0] })`（**★ 拿第一张结果图当通知图标**）；
- `setPush(on)` 内部直接 `await Notification.requestPermission()`，结果存 `localStorage['comfyart.notify']`。

### 44.3 定时任务（`src/schedules.ts` 90 行）★★ 纯前端调度器

- `Repeat = 'once' | 'daily' | 'weekly'`；`Schedule = { id, name, prompt, project, repeat, time:'HH:MM', date?, weekdays?: number[], enabled, created, lastRun? }`；
- `TICK = 30000`（**30s 一调**）、`DAY = 86400000`；
- ★ **`nextRun(s)` 算法**（无 cron 库）：
  ```ts
  if (repeat === 'once') return lastRun || !date ? null : at(new Date(`${date}T00:00`), time)
  const from = lastRun ?? created
  const days = repeat === 'weekly' ? weekdays ?? [] : [0..6]
  for (let i = 0; i <= 7; i++) { const t = at(new Date(from + i*DAY), time); if (t > from && days.includes(new Date(t).getDay())) return t }
  ```
  → **从上次运行时间往后扫 7 天找第一个匹配日**；
- ★ **调度不重叠**：`if (busy || chatsAtom.get().some(c => c.busy)) return` → **Agent 忙时跳过本 tick**（不排队不并发）；
- 到期处理：`list.find(s => s.enabled && s.project === currentProject() && (nextRun(s) ?? Infinity) <= now)` → **先写 `lastRun` 再执行**（防多 tab 重复跑）；`enabled: s.repeat !== 'once' && s.enabled` → **一次性任务跑完自动禁用**；
- 执行体：`await openChat(null)` → **开新会话** → `sendToAgent(editor, s.prompt, [], null)` → `notify({ kind:'schedule', chat: chatIdAtom.get() })`（**定时任务开独立会话，不污染当前对话**）；
- `startScheduler(editor, onError)` 返回 `() => clearInterval(timer)`（**可清理**）；启动时先 `loadSchedules().then(tick)`（**立即补一次**）；
- 多 tab 同步同样用 `BroadcastChannel('comfyart-schedules')`。

---

<a id="sec45"></a>
## 45. 记忆系统（`src/memory.ts` 91 行）★★★ 可写回的结构化长期记忆

- `Memory = { id, text, time, category? }`；`MemoryLog = { time, action:'add'|'update'|'forget'|'skill', text, source }`（**★ 记忆变更单独写日志文件 `comfyart/memory-log.json`，可审计**）；
- ★ **四个固定分类**：`MEMORY_CATEGORIES = [['about','About the user'], ['taste','Visual taste'], ['work','Way of working'], ['assets','Long-term assets']]`；
- ★ **`memoryPrompt()` 把记忆按分类分组注入**：`Remembered about the user (respect these):` + 每组 `组名:` + `- 条目`；**未分类的进 Other**，空分类不输出；
- ★ **`toInputs(value)` 容错解析模型输出**：非数组 → `[]`；每项取 `v` 字符串或 `v.text`；**`category` 必须在白名单里否则置 undefined** → 模型输出不可信也不脏数据；
- ★ **模糊匹配**：`same(a,b) = a === b || a.includes(b) || b.includes(a)` → `forget`/`correct` 能用近似文本命中；
- `remember(items, source)`：先 `known = Set(已有 text)` 过滤完全重复 → 追加 + 写日志；
- `correct(items: Correction[], source)`：按 `old` 找到就换成 `new` 并刷 `time`，日志写 `"旧 → 新"`；
- `forget(texts, source)`：命中则删 + 日志；**三个函数都返回“实际生效的文本列表”** → Agent 能知道到底改了什么。

---

<a id="sec46"></a>
## 46. 音频/3D 生成（`src/assetGen.ts` 105 行）★★ 四类音频 + 图生 3D

### 46.1 `AudioSpec` 结合式类型（★ 干净）

```ts
type AudioSpec =
  | { kind:'music';  prompt, lyrics, seconds }
  | { kind:'speech'; text, voice }
  | { kind:'clone';  text, reference }
  | { kind:'sfx';    prompt, seconds }
```
对应四个工作流：
| kind | workflow | 填值 |
|---|---|---|
| music | `music-ace-step.api.json` | `g['3'].tags = prompt`、`lyrics = lyrics \|\| '[instrumental]'`、`g['5'].seconds`、`g['6'].seed`，result `'8'` |
| sfx | `sfx-stable-audio.api.json` | `g['3'].text`、`g['5'].seconds`、`g['6'].seed`，result `'8'` |
| speech | `speech-kokoro.api.json` | `g['1'].speaker_name = voice`、`g['2'].lang = LANGS[voice[0]] ?? 'English'`，result `'3'` |
| clone | `voice-clone-indextts.api.json` | `g['1'].audio = annotated(reference)`、`g['3'].text/seed`，result `'4'` |

- ★ **语言从 voice id 首字母推导**：`LANGS = { a:'English', b:'English (British)', j:'Japanese', z:'Mandarin Chinese', e:'Spanish', f:'French', h:'Hindi', i:'Italian', p:'Brazilian Portuguese' }`；
- `VOICES`（17 个）：`zf_xiaoxiao, zf_xiaoyi, zf_xiaobei, zf_xiaoni, zm_yunxi, zm_yunjian, zm_yunyang, zm_yunxia, af_heart, af_bella, af_nicole, am_adam, am_michael, bf_emma, bm_george, jf_alpha, jm_kumo`（**`z*` = 中文，`a/b*` = 英文**）；
- `MUSIC_SECONDS = [15,30,60,120]`、`SFX_SECONDS = [3,5,8,15]`；`seed() = Math.floor(Math.random() * 2**31)`。

### 46.2 ★ 生成入口统一用“临时生成块”

```ts
export function generateAudio(editor, spec, onError, quiet = false): TLShapeId {
  const [w,h] = AUDIO_SIZE; const [x,y] = freeSpot(editor, w, h)
  editor.createShape({ id, type: GENERATOR, x, y, props:{w,h}, meta:{ temp:true, quiet } })
  if (!quiet) editor.zoomToBounds({x,y,w,h}, { inset: 200, animation:{duration:200} })
  void startJob(editor, id, audioGraph(spec), { name: `${LABELS[kind]} · ${text.slice(0,24)}`, kind:'audio', placer:'audio' }, onError)
}
```
→ **音频/3D 没有专用 UI 面板，直接复用 GENERATOR 临时块 + 自定义 placer**；`quiet` 控制是否缩放（Agent 调用时不打断用户视角）。

### 46.3 自定义 placer（★★ 多结果布局）

- `registerPlacer('audio', ...)`：`editor.run(() => { deleteShape(holder); media.forEach((m,i) => createShape({ type: AUDIO, x: holder.x, y: holder.y + i*(AUDIO_SIZE[1]+GAP), parentId: holder.parentId })) }, { history:'ignore' })` → **多段音频竖向排列**；
- `registerPlacer('model', ...)`：**横向排列**（`x: holder.x + i*(w+GAP)`），`props.ext = 'glb'`；
- 两者结尾 `selectResults(editor, holder, ids)`；`GAP = 40`；
- ★ `history:'ignore'` → **占位块→结果的替换不进撤销栈**（用户撤销时不会撤销到中间态）；
- `imageTo3d(editor, shape)`：workflow `image-to-3d-trellis2.api.json`，`g['1'].inputs.image = annotated(src)`，result `'30'`，`MODEL_SIZE = 480`。

---

<a id="sec47"></a>
## 47. 主动智能（`src/proactive.ts` 125 行）★★★ Agent 主动学习 + 主动审查

### 47.1 三种能力 + 每日限额

- `Ability = 'memory' | 'skills' | 'review'`；`ABILITY_SETTINGS = { memory:'proactive-memory', skills:'proactive-skills', review:'proactive-review' }`；
- 常量：`MAX_PROMPTS = 30`、`SKILL_EVERY = 6`、`MAX_SEEN = 50`、`REVIEW_DELAY = 60000`、`REVIEW_MIN_SHAPES = 3`、`REVIEW_PX = 1024`；
- ★ **`dailyLimit() = Number(settings['proactive-daily']) || 0`** → **没配就完全不开**（默认关）；`take(state, a)` 做**配额门控**（`runs[a] >= limit` 则返回 false）；
- ★ **跨标签页互斥用 `navigator.locks.request('comfyart-proactive', ...)`**（★ Web Locks API，不是 localStorage 锁）；
- ★ **状态文件按天重置**：`State = { day, runs, prompts, skillMark, reviewed, seen }`；`today() = new Date().toLocaleDateString('sv')`（**用瑞典 locale 得到 ISO `YYYY-MM-DD` 的经典技巧**）；`saved.day !== today()` 则只重置 `runs`（**prompts 跨大保留**）。

### 47.2 ★ 三段 system prompt 原文（可直接参考改写）

- **MEMORY_SYSTEM**：“You keep a long-term memory of a designer. From one exchange with their design agent, pull out only lasting preferences or facts the user stated about themselves, their taste or how they like to work. Ignore one-off requests and anything already remembered. Reply with ONE JSON object and nothing else: `{"memories": [{"text": "<one short statement about the user, e.g. \"Freelance illustrator based in Shanghai\", in the user's language>", "category": "about|taste|work|assets"}]}` (an empty list when there is nothing lasting).”
- **SKILL_SYSTEM**：“You turn a designer's repeated way of working into a reusable skill for their design agent. You get their latest requests, numbered. If at least three of them follow the same workflow (same kind of deliverable, steps, format or style rules), write a skill for it; otherwise return null. Do not repeat an existing skill. Reply with ONE JSON object and nothing else: `{"skill": null}` or `{"skill": {"name": "<short name in the user's language>", "instructions": "<step-by-step instructions for the agent, in English>", "samples": [<numbers of the requests it was learned from>]}}`”
  → ★ **要求模型回“样本编号”而不是自己引文本**，前端用 `prompts[n-1]` 回填 → **防幻觉引用**；
- **REVIEW_SYSTEM**：“You review a design canvas for a designer. The picture is cropped tightly around the content, so elements touching its edges are fine. Look for concrete problems: typos or garbled text in images, inconsistent colors or styles across a set, cut-off subjects inside an image, broken layouts, wrong aspect ratios, low-quality or deformed generations. Reply with ONE JSON object and nothing else: `{"issues": ["<one problem and a fix, written in ${lang === 'zh' ? 'Simplified Chinese' : 'English'}>"]}` (empty when nothing is wrong). At most 5 issues.”
  → ★ **根据 UI 语言要求模型用中文/英文回**；开头先声明“边缘裁切是正常的”→ **预先排除误报**。

### 47.3 触发链

- `onComfyEvent('comfyart-bot', e => if (e.event === 'turn_done') onTurnDone(e))` → **每轮对话结束触发**；
- `exchange(chatId, messageId)`：拉完整消息，**用 `parent_id` 找到对应的用户消息** → 只取 `blocks` 里 `type==='text'` 拼接；
- `withState` 里做全部判定（**锁内**）：`seen` 去重（50 条）→ `prompts` 累加（30 条）→ `memory = take('memory')` → `due = prompts.length - min(skillMark, prompts.length) >= SKILL_EVERY` → 技能学习取**最近 12 条**（`SKILL_EVERY * 2`）并记 `skillMark`；
- `learnMemory`：把**已有记忆全量列进 prompt**（`Already remembered:`）→ 让模型呰“不要重复记”；`ex.reply` 截断 2000 字；
- `evolveSkill`：存为 `id = 'learned-' + slug(name)`，`learned: samples`（**记录来源 prompt**），写 memory 日志 `action:'skill'`，**呷通知**（“已学会技能 X / 来自 N 条需求”）；
- ★ **review 用画布签名去重**：`signature(editor) = [...getCurrentPageShapeIds()].sort().join(',')`；`reviewed[project] === sig` 则跳过 → **画布没变不重复审查**；
- ★ **review 把整张画布缩到 1024px 导出 PNG 呬给多模态模型**：`exportImage(editor, ids, { format:'png', background:true, scale: Math.min(1, REVIEW_PX / max(bounds.w,bounds.h)) })` → **主动质检发现问题**；结果进通知（`• ` 列表）；
- `startProactive` 返回清理函数（`stop(); clearTimeout(timer)`）；review 延迟 60s 执行（**等用户画完**）。

---

<a id="sec48"></a>
## 48. 生成块 shape 与版本迁移（`src/shapes/GeneratorShape.tsx` 96 行）★★★ tldraw 数据迁移正确姿势

### 48.1 ★ 用 `declare module` 扩展 tldraw 全局类型

```ts
declare module 'tldraw' {
  export interface TLGlobalShapePropsMap {
    generator: { w, h, prompt, ratio, resolution, count, workflow, duration, transparent, quality, style, cw, ch }
  }
}
export type GeneratorShape = TLShape<typeof GENERATOR>
```
→ **注册后 `editor.updateShape<GeneratorShape>()`、`shape.props.xxx` 全部自动类型安全**（比自定义 union 干净得多）。

### 48.2 ★★ props 迁移（改字段不丢数据）

```ts
const versions = createShapePropsMigrationIds(GENERATOR, { AddDuration: 1, AddOptions: 2 })
static override migrations = createShapePropsMigrationSequence({
  sequence: [
    { id: versions.AddDuration, up: (props) => { props.duration = 5 } },
    { id: versions.AddOptions, up: (props) => Object.assign(props, { transparent:false, quality:'standard', style:'', cw:1024, ch:1024 }) },
  ],
})
```
→ **每次加字段就追加一个 migration**，旧存档自动补齐；`props` 必须声明 `T.number/T.string/T.boolean`（tldraw 校验 + 序列化）。

### 48.3 默认值与渲染

- `getDefaultProps() = { w:512, h:512, prompt:'', ratio:'1:1', resolution:'1K', count:1, workflow:'', duration:5, transparent:false, quality:'standard', style:'', cw:1024, ch:1024 }`；
- `canEdit() = false` → **生成块本身不可进入编辑态**（参数全在外部 GenPanel）；
- `getIndicatorPath(shape)` 返回 `Path2D` 矩形 → **选中框形状可控**；
- ★ **进度不靠组件 state，而是 `progressAtom[job.id]`**：
  ```ts
  const job = shape.meta.job as { id: string } | undefined
  const progress = useValue('generator progress', () => (job ? progressAtom.get()[job.id] : undefined), [job?.id])
  const p = progress && progress.max > 0 ? Math.min(1, progress.value / progress.max) : 0
  ```
  → `data-running` + `data-indeterminate`（**无进度时自动转圈**）+ CSS 变量 `--ca-p` 驱动环形进度；**后端 progress 事件 → atom → shape 自动重渲染**，无需事件订阅。

---

<a id="sec49"></a>
## 49. WebSocket 事件总线与进度（`src/ws.ts` 33 行 + `src/progress.ts` 20 行）★★ 极简但完备

### 49.1 `ws.ts`

- `handlers = new Map<string, Set<Handler>>()`（**一个 type 多个订阅者**）；
- `CLIENT_ID = uuid()` → **连接时 `?clientId=` 带上**，后端据此把 `executing/progress/execution_*` 只推给发起者；
- `connect()` 惰性：`onComfyEvent` 首次调用才连；`url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'`；
- ★ **`onclose` 无条件 2s 重连**（`RETRY_MS = 2000`）且 `socket = null` → **下次 `onComfyEvent` 也会重新连**；
- ★ **`onComfyEvent(type, handler)` 返回取消函数** → 所有订阅者都能干净卸载（§42 OCR、§47 proactive 都靠它）；
- 消息解析容错：`typeof e.data !== 'string'` 直接 return；`JSON.parse` 失败直接 return。

### 49.2 `progress.ts`

- `progressAtom = atom<Record<promptId, { value, max }>>`；
- `onComfyEvent('progress')` → `progressAtom.update(all => ({ ...all, [id]: { value, max } }))`；
- ★ **完成/失败/中断三事件都 `drop()`**（解构重建 atom）→ **进度表不会泄漏残留**；
- ★ **整个模块无类无单例**，就是两个 atom + 四个订阅 → 任何组件 `useValue` 就能拿进度（§48 生成块）。

---

<a id="sec50"></a>
## 50. 画板（`src/shapes/ArtboardShape.tsx` 94 行）★★ 继承 tldraw Frame 加样式

- ★ **`class ArtboardShapeUtil extends FrameShapeUtil.configure({ getCustomDisplayValues })`** → **直接继承官方 frame**，只多一个 `meta.style = { fill?, stroke?, radius? }`；
- `radiusOf(shape) = min(style.radius ?? 0, w/2, h/2)`（**圆角不超短边一半**）；
- ★ **导出时自己补背景/圆角/描边**（`exportImage`）：
  - 先走 `editor.toImage(ids, { ...opts, format:'png', background:false })` 拿透明 PNG；
  - 再 `createImageBitmap` → canvas → `ctx.roundRect(0,0,c.width,c.height, r*k)` 填背景 → `drawImage` → 需要时 `strokeStyle + lineWidth = k`（**k = 导出像素/画布单位**）→ `toBlob(type)`；
  - `jpeg` 先铺白底（**JPEG 无透明**）；`MIME = { png, jpeg, webp }`；
  - ★ **SVG 路径用字符串插入**：`getSvgString({ background:false })` 后 `res.svg.replace(/(<svg[^>]*>)/, "$1<rect ... rx ry fill stroke/>")` → **不解析 DOM 就能注入背景矩形**；
  - ★ **只有单 frame 且带样式时才走这条路**（`if (!frame || (!fill && !stroke && !radius)) return editor.toImage(...)`）；
- ★ **`toSvg` 重写用 `cloneElement` 加 `rx/ry`**：`const [body, ...rest] = svg.props.children; return <>{cloneElement(body, { rx: r, ry: r })}{rest}</>` → **改 React 元素 props 而非拼字符串**；
- ★ **`getClipPath` 手写圆角矩形裁剪多边形**：四个角各采样 `ARC_STEPS(6)+1` 个点（`corners = [[w-r,r,-90],[w-r,h-r,0],[r,h-r,90],[r,r,180]]`）→ **画布内裁剪也是圆角**；
- `meta.refFrame === true` 且无子元素时渲染“拖入参考图”占位提示（参考框）；
- `--ca-frame-r` CSS 变量传递圆角给子元素样式。

---

<a id="sec51"></a>
## 51. 作业管线（`src/jobs.ts` 221 行）★★★ 所有 AI 产出的唯一入口

### 51.1 数据模型

```ts
type MediaKind = 'image' | 'video' | 'audio' | 'model'
interface Job { id: string; node: string; name: string; kind: MediaKind; placer?: string; extra?: Record<string, unknown> }
interface Media { ref: ImageRef; src: string; w: number; h: number; kind: MediaKind }
type Placer = (editor, holder: TLShape, media: Media[], job: Job, onError: ErrorSink) => Promise<void>
const placers: Record<string, Placer> = {}          // 名字 → 布局策略
const active = new Set<string>()                    // ★ 防重复 finish（同一 job 并发）
export const resultsAtom = atom<Record<TLShapeId, { srcs: string[]; error?: string }>>('job results', {})
```
→ ★ **`job` 存在 `shape.meta.job`**（可序列化），所以**刷新页面后能恢复**。

### 51.2 ★★ `resumeJobs`：刷新后接回未完成的生成

```ts
export function resumeJobs(editor, onError) {
  for (const shape of editor.store.query.records('shape').get()) {
    const job = jobOf(shape)          // shape.meta.job
    if (job) void finish(editor, shape.id, job, onError)
  }
}
```
→ 因为 `job.id`（ComfyUI prompt_id）已持久化，`waitForImages(job.id, job.node)` 能直接重新轮询 → **生成中刷新不丢结果**。

### 51.3 `startJob` 完整流程

1. `await build`（`Promise<Built>` —— **工作流构建失败也走同一错误路径**）；
2. `queuePrompt(graph)` 拿 `prompt_id` → 组装 `Job`；
3. **`editor.updateShape({ meta: { ...holder.meta, job } })`** → 先写 job 再等结果（★ 刷新可恢复的关键）；
4. `finish()`：`waitForImages` → `Promise.all(refs.map(toMedia))` → `placers[job.placer] ?? defaultPlacer` → `report(holderId, { srcs })`；
5. ★ **错误恢复分两种**：`holder.meta.temp` → **删掉临时占位块**；否则 `meta: { ...holder.meta, job: null, quiet: false }` → **保留块但清掉 job**（用户能看到失败状态）。

### 51.4 ★ `GenInfo`：把“怎么生成的”写回结果图 meta（可复现 / Remix）

```ts
const GEN_INPUTS = ['refs','base','strength','colors','refModes','refStrengths','refControls','lastFrame','scenes','videoMode','motion','speech','negative','seed','loras']
function infoOf(holder, job): GenInfo {
  if (holder.type !== GENERATOR) return { tool: job.name }
  const prompt = p.prompt || scenes?.map(s => s.prompt).join(' / ')     // ★ 分镜也能回溯
  const inputs = Object.fromEntries(GEN_INPUTS.filter(k => holder.meta[k] != null).map(k => [k, holder.meta[k]!]))
  return { tool, prompt, ratio, resolution, duration, workflow, seed?, refs?, settings: {count,quality,style,transparent,cw,ch}, inputs }
}
```
→ `defaultPlacer` 里 `editor.updateShape({ id, meta: { gen: gen } })` → **每张结果图自带完整生成参数**；§34.6 的 Remix、§47 的 review 都读它。

### 51.5 `defaultPlacer` 与原子替换

```ts
editor.run(() => {
  editor.deleteShape(holder.id)
  for (const m of media) {
    const id = placeMedia(editor, m, job.name, x, holder.y, w, holder.parentId)
    editor.updateShape({ id, ..., meta: { gen } })
    x += w + GAP                       // GAP = 40，横向排列
  }
}, { history: 'ignore' })
selectResults(editor, holder, ids)     // ★ holder.meta.quiet 则不抢选区
```
→ ★ **`editor.run(..., { history:'ignore' })`** = 占位块→结果的替换不污染撤销栈；`parentId` 继承 → **在 frame 内生成不会跑出去**。

### 51.6 `placeMedia`（asset + shape 两步）

- `AssetRecordType.createId()` → `editor.createAssets([{ typeName:'asset', type: m.kind, props:{ name, src, w, h, mimeType, isAnimated }, meta:{} }])`；
- ★ **mimeType 从文件名推**：`video/mp4` / `.svg → image/svg+xml` / `.webp → image/webp` / 否则 `image/png`；`isAnimated = kind==='video' || .webp`；
- shape 尺寸：`{ w, h: w * m.h / m.w }` → **固定宽、按原比例算高**；
- `toMedia`：优先用后端回的 `ref.metadata.width/height`（**省一次图片加载**），否则 `mediaSize()` 用 `<img>`/`<video>` 探测；audio/model 直接 `w:0,h:0`。

### 51.7 ★ `spawnHolder`：自动找空位放占位块

```ts
let [x, y] = [b.maxX + GAP, b.minY]
for (let i = 0; i < 200; i++) {
  const hit = boxes.find(o => x < o.maxX + GAP && x + w + GAP > o.minX && y < o.maxY + GAP && y + h + GAP > o.minY)
  if (!hit) break
  x = hit.maxX + GAP          // ★ 碰到就跳到它右边，最多试 200 次
}
editor.createShape({ id, type: GENERATOR, x, y, props:{w,h}, meta:{ temp: true } })
```
→ **简单有有效的“往右排”布局**（带迭代上限防死循环）。

### 51.8 其它工具

- `mediaAsset(editor, shape)` → 取 `props.assetId` 对应且 `type` 为 image/video 且有 `src` 的 asset；`fileSrc()` 兼容 `props.src`（**兼容非 asset 类 shape**）；
- `errorText(e) = e instanceof Error ? e.message : String(e)`（**全项目统一错误文案提取**）。

---

<a id="sec52"></a>
## 52. 自动保存与版本快照（`src/autosave.ts` 54 行 + `src/history.ts` 30 行）★★★

### 52.1 autosave

- `SAVE_DELAY = 800`（**防抖 800ms**）；`INTERVAL = 5 * 60 * 1000`（版本快照 5 分钟）；
- ★ **监听 store 而非 editor**：`editor.store.listen(cb, { scope: 'document' })` → **只关心文档变化，不关心 presence/UI**；
- ★ **串行化**：`let running: Promise<void> | null`；`if (running) await running` → **不会并发写盘**；
- ★ **四态状态机**：`SaveState = 'saved' | 'pending' | 'saving' | 'error'`，存 `saveStateAtom` → **顶栏能显示保存状态 + 时间 + 错误**；
- ★ **离开页面保护**：`beforeunload` 里 `if (state !== 'saved' && canSave()) e.preventDefault()` → **未保存时弹浏览器确认**；
- ★ **`flushCurrent` 模块级变量 + `export const saveNow = () => flushCurrent?.() ?? Promise.resolve()`** → **任何地方（Agent 命令、切项目前）能强制落盘**；
- 保存内容：`saveProject(id, getSnapshot(editor.store), editor.getDocumentSettings().name, coversOf(editor.store.allRecords()))` → **同时更新封面图**；
- 保存成功后才 `maybeSaveVersion(...)`，并传 `shape 数量`（**版本列表能显示规模**）；
- `canSave()` 门控（多标签页冲突时停写，见 §项目）；返回 `{ save, stop }`，`stop()` 清 timer + 移除监听。

### 52.2 history（版本快照）

- REST：`GET/POST /comfyart/projects/{id}/versions`、`GET .../versions/{time}`；
- `Version = { time, count }`；`maybeSaveVersion` 用模块级 `last` 做**时间门（5 分钟）**；
- ★ **存的是完整 `TLEditorSnapshot`**（`getSnapshot(editor.store)`）→ **可直接 `loadSnapshot` 回滚整个画布**；
- 无本地存储，全部在服务端 → 跨设备可用。

---

<a id="sec53"></a>
## 53. 应用装配（`src/App.tsx` 308 行）★★★ tldraw 完整接管 UI 的开关清单

### 53.1 ★ 把 tldraw 默认 UI 全部关掉（19 项）

```ts
const components: TLComponents = {
  ContextMenu: CanvasMenu, Toolbar: null, StylePanel: null, MainMenu: null, PageMenu: null,
  NavigationPanel: null, HelpMenu: null, ActionsMenu: null, QuickActions: null, ImageToolbar: null,
  VideoToolbar: null, MenuPanel: null, TopPanel: null, SharePanel: null, HelperButtons: null,
  DebugPanel: null, DebugMenu: null, Minimap: null,
}
```
→ **只保留 `ContextMenu` 换成自己的**；注意 `ImageToolbar`/`VideoToolbar` 也置 null 后自己实现（§8.12）。

### 53.2 自定义 shape/tool 注册

- `shapeUtils = [ArtboardShapeUtil, GeneratorShapeUtil, CommentShapeUtil, DoodleShapeUtil, TextShapeUtil, VectorShapeUtil, LottieShapeUtil, ModelShapeUtil, HtmlShapeUtil, AudioShapeUtil, MockupShapeUtil, GenBlockShapeUtil, QrShapeUtil]`（13 个）；
- `tools = [CommentTool, DoodleTool, TextTool, VectorTool, PenTool]`（5 个）；
- ★ **图层隐藏靠一个回调**：`getShapeVisibility = (shape) => shape.meta.hidden ? 'hidden' : 'inherit'` → **一行代码实现图层显隐**（§38）；
- `overrides={overrides}`（§36 快捷键）。

### 53.3 ★★ 自定义 asset store：图片不存浏览器，直接上传后端

```ts
const assets: TLAssetStore = {
  async upload(_asset, file) { const ref = await uploadImage(file, file.name || 'image.png'); return { src: viewUrl(ref) } },
  resolve(asset, ctx) {
    const src = asset.props.src
    if (!src || ctx.shouldResolveToOriginal || asset.type !== 'image' || asset.props.mimeType !== 'image/svg+xml') return src
    return svgRaster(src, asset.props.w, asset.props.h, ctx.steppedScreenScale * ctx.dpr)   // ★ SVG 栅格化
  },
}
```
→ ★ **`resolve` 专门把 SVG 按当前缩放栅格化**（`steppedScreenScale * dpr`）→ **SVG 在画布上不会模糊也不会无限大**；`shouldResolveToOriginal` 保证导出时拿原 SVG。

### 53.4 启动顺序（★ 顺序很重要）

1. `Promise.all([loadMemory(), loadModels(), loadNotices()])`（**与画布无关的全局数据**）；
2. `onMount`：`ed.user.updateUserPreferences({ colorScheme: themeAtom.get(), locale: lang === 'zh' ? 'zh-cn' : 'en' })` + `registerFrameRules(ed)` + `registerCropRule(ed)`（**都返回 stop 函数**）；
3. `startBot(id, () => staleRef.current)`（**先于画布加载**）；
4. `Promise.all([loadProject(id), loadLibrary()])` → `loadSnapshot(editor.store, snap)`；
5. 首次进入：从 `?kit=` 写 `documentSettings.meta.brandKit` 并 `history.replaceState` 删参数；
6. **重命名默认页**：`for (page of getPages()) if (/^Page \d+$/.test(page.name)) renamePage(page.id, page.name.replace('Page', t('app.page')))`；
7. `zoomToBounds(content, { targetZoom: 1 })` → **打开就居中到内容**；
8. ★ **`editor.clearHistory()`** → **加载存档不进撤销栈**；
9. `resumeJobs(editor, setError)` → **接回未完成的生成**（§51.2）；
10. `startScheduler` / `startProactive` / `startChatFrames` / `registerCommandBus` / `startAutosave`；
11. `takeHandoff(editor, setError)`（`home/handoff.ts` —— **从首页带任务交接进画布**）；
12. **所有 start* 都返回 stop，`useEffect` 清理里逐个调** → 切项目不泄漏。

### 53.5 ★★ 多标签页冲突保护（BroadcastChannel）

```ts
const CHANNEL = 'comfyart-projects'; const TAB = uuid()
const autosave = startAutosave(editor, id, () => !staleRef.current, () => channel.postMessage({ project: id, tab: TAB }))
const channel = new BroadcastChannel(CHANNEL)
channel.onmessage = (e) => {
  if (e.data.project !== id || e.data.tab === TAB) return
  staleRef.current = true; setStale(true)
}
```
→ 每次保存后广播“我存过了”；其他标签页收到 → `staleRef.current = true` → **autosave 的 `canSave()` 返回 false 停写**（防互相覆盖）+ 底部横幅“你的文件版本已过期”+ 刷新按钮。

### 53.6 布局与浮层清单

- 结构：`ca-root[data-chat][data-dock]` → `AgentPanel`（三态 `closed/open/max`，`chatViewAtom`）+ `ca-main`（`TopBar` → `ca-body`：`SidePanel(left)` + `ca-canvas` + `SidePanel(right)`）；
- ★ **`Tldraw` 子节点就是所有自定义浮层**（21 个：DropHandlers/SelectionLabel/EditLayers/ImageToolbar/CropBar/MockupPanel/PathEditor/ModePanel/AdjustPanel/InpaintPanel/SmudgePanel/StylePanel/Effects/VideoToolbar/GenPanel/CanvasPick/CanvasChat/CommentPanel/MultiToolbar/TextToolbar/ShapeToolbar/FrameToolbar/EmbedToolbar/Dock/CanvasTools）→ **全部在 editor 上下文内，自己用 `useSelectionRect` 定位**；
- 全局弹窗放在 `Tldraw` 外面（Shortcuts/KitEditor/Confirm/BatchRename/Lightbox/Slideshow/HtmlPreview/InfoDialog/TaskDetails/ScheduleDialog/BotSettings/VideoEditor/MemoryDialog/SkillsDialog/HistoryDialog/ExportDialog/AudioDialog）；
- ★ **错误处理极简**：`const [error, setError] = useState('')` 一个字符串 → `{error && <div className="ca-error" onClick={() => setError('')}>{error}</div>}`（**点一下消失**）；所有面板统一接 `onError={setError}`。

---

<a id="sec54"></a>
## 54. Agent 接入层（`src/bot.ts` 321 行）★★★ 把外部 CLI Agent 接进画布

### 54.1 数据模型（★ 服务端权威，前端只渲染）

```ts
interface BotChat { id, user, project, title, provider, run_mode:'auto'|'ask', pinned, archived, busy?, created_at, updated_at }
interface BotMessage { id, chat_id, parent_id: string|null, role:'user'|'assistant', blocks: BotBlock[], status, usage: BotUsage|null, created_at }
interface BotUsage { input_tokens?, output_tokens?, cache_read_input_tokens?, cost_usd? }
interface ProviderInfo { id, label, available, version, detail, logged_in: boolean|null, stateful, attachments, branch, models: string[], model_options?: ModelOption[] }
```
- ★ **`parent_id` 形如链表 → 支持分支**（`branchFrom(messageId)` 从某条消息开新分支）；
- ★ **`BotUsage` 含 `cache_read_input_tokens` 与 `cost_usd`** → **UI 能直接显示缓存命中率与成本**；
- ★ **`ProviderInfo` 字段就是能力探测表**：`available`（装了没）/`version`/`logged_in`/`stateful`（能否多轮）/`attachments`（能吃图吗）/`branch`（支持分支吗）→ **UI 按能力降级**（不能附图就不给附图按钮）。

### 54.2 工具名本地化（★ 小但好用）

```ts
export const TOOL_LABELS: Record<string,string> = { /* 49 行：Read→读取、Edit→编辑、Bash→执行命令… */ }
export const toolName = (name = '') => name.replace(/^mcp__\w+__/, '')      // ★ 剔掉 mcp__server__ 前缀
export const toolLabel = (name?) => TOOL_LABELS[toolName(name)] ?? toolName(name)
```
→ **工具调用时间线显示中文标签而不是 `mcp__comfyart__generate_image`**。

### 54.3 状态全部用 atom（无 Redux）

`providersAtom` / `botEnabledAtom: atom<boolean|null>`（**null = 未加载**）/ `settingsAtom` / `chatsAtom` / `chatIdAtom` / `messagesAtom` / `newProviderAtom`。

### 54.4 ★ 事件驱动的消息更新（reducer 表）

```ts
const reducers: Record<string, (e) => void> = { /* chat_deleted / messages / user_message / message_update / ... */ }
export function startBot(projectId, isStale) {
  project = projectId
  ...
  return onComfyEvent('comfyart-bot', (e) => { if (!isStale()) reducers[e.event]?.(e) })
}
```
- ★ **每个 reducer 先查 `if (chatIdAtom.get() !== e.chat_id) return`** → 切会话时不会把旧会话的推送写进当前列表；
- ★ **`patchMessages(chatId, messageId, fn)`** 局部改一条消息（**流式 token 就靠它**）；
- ★ **`isStale()` 门控**（§53.5 多标签页）→ 过期标签页不接事件；
- `openChat(null)` = 新建会话（`POST /comfyart/bot/chats { project, provider }`）。

### 54.5 ★ `SendInput`：一个发送接口支持 6 类上下文

```ts
interface SendInput {
  text: string
  refs: { id, src, name, kind:'image'|'video'|'audio', transcript? }[]   // ★ 带转录文本
  docs: { src, name }[]                                                  // 文档
  kits: { name, description, images: string[] }[]                        // ★ 品牌套件直接内联
  skill?: string; memory?: string; context?: string                      // 手动附技能/记忆/画布上下文
}
```
→ **画布图/视频/音频/文档/品牌套件全部仍是一个 JSON POST**（`refs` 用已有 `src`，**不重传文件**）。

### 54.6 其它 API

- `answerAsk(askId, selected: string[], other: string)` → ★ **Agent 反向提问（ask 模式）的结构化回答**；
- `stopTurn()`、`updateChat(id, {title|run_mode|pinned|archived})`、`deleteChat`、`deleteProjectChats(projectId)`（`?archived=1`）；
- `logDirect(text, blocks)` → ★ **本地直接记一条消息（不走 Agent）**，用于纯前端操作也要留痕；
- `outputsOf(blocks)` → 从 blocks 里取产出文件；
- `refreshBot()` → 重拉 providers + settings + chats。

---

<a id="sec55"></a>
## 55. Agent 入口层（`src/agent.ts` 186 行）★★ 一个输入框背后的调度

### 55.1 三种模式 + 输入态 atom 群

```ts
type AgentMode = 'agent' | 'image' | 'video'          // ★ 同一个输入框：聊天 Agent / 直生图 / 直生视频
const draftAtom = atom('agent draft', '')             // 草稿（切页不丢）
const extraRefsAtom = atom<TLShapeId[]>('agent refs', [])
const agentModeAtom = atom<AgentMode>('agent mode', 'agent')
const kitsAtom = atom<LibraryItem[]>('agent kits', [])
const docsAtom = atom<ChatDoc[]>('agent docs', [])
const DOC_ACCEPT = '.pdf,.docx,.md,.markdown,.txt'
const directSettingsAtom = atom<Record<'image'|'video', Partial<GeneratorShape['props']>>>('direct settings', { image:{}, video:{} })
```
→ ★ **直生图/直生视频的参数不写进 shape，而是 `directSettingsAtom[image|video]`**（下次生成的默认值）。

### 55.2 参考图处理

- `isRefShape(s) = s.type === 'image' || 'video' || AUDIO`；`refKindOf(shape)` 映射到 `RefKind`；
- `REF_LONG = 512` → ★ **上传参考图前先缩到长边 512**（省带宽）；
- `srcOf(editor, shape) = fileSrc(...)` → 已有 `src` 的不重传。

### 55.3 ★ 评论→需求（`commentNotes` / `commentsToChat`）

- 把画布上的 `CommentShape` 文本汇总成一段文字，**一键变成给 Agent 的修改意见**；
- → 对应 §13 评论系统；**“设批注 → Agent 改图”的闭环**。

### 55.4 ★ 直生模式的多目标展开（`eachRuns`）

```ts
export function eachRuns(refs, targets, promptFor: (runRefs) => string): DirectRun[] {
  if (!targets.length) return [{ refs, prompt: promptFor(refs) }]
  return targets.map(target => ({ refs: runRefs, prompt: promptFor(runRefs), near: target.id }))
}
```
→ **选中 N 个目标图 + 一句 prompt = 生成 N 次，每次结果放在对应目标旁边**（`near` → `createGenerator(..., [near.x, near.maxY + RESULT_GAP])`，`RESULT_GAP = 40`）。
- `eachLine(refs, targets)` → ★ **多行 prompt 与多目标一一对应**（每行对应一个目标）。
- 直生也写 `detail: { tool:'generate_image'|'generate_video', prompt, model?, params:{ aspect_ratio, references } }` → **与 Agent 命令共用同一套日志/时间线**。

### 55.5 ★ 分镜规划（`planStoryboard`）

```
STORY_SYSTEM = Split a video idea into a storyboard. Reply with ONE JSON object and nothing else: {"scenes": [{"prompt": "<detailed English shot description with camera motion and sound>", "duration": ${DURATIONS.join('|')}}]}. Use 2-${MAX_SCENES} scenes, keep characters and style consistent, total duration at most ${MAX_STORY_SECONDS} seconds.
```
- ★ **枚举直接拼进 prompt**（`${DURATIONS.join('|')}`）→ 模型只能选合法值；
- ★ **解析后还要再校一次**：`if (!s?.prompt || out.reduce((n,x)=>n+x.duration, duration) > MAX_STORY_SECONDS) continue` → **总时长超限就丢弃多余分镜**（不信任模型）；
- `extractText(editor, shape)` → 对图片跑 OCR（§42）。

---

<a id="sec57"></a>
## 57. 仓库底细：它仍的是什么（后端事实）

### 57.1 ★ 它不是独立服务，而是 **ComfyUI 自定义节点包**

`__init__.py`（41 行）要点：
```python
from server import PromptServer
routes = PromptServer.instance.routes

@routes.get("/comfyart")            # 302 到 /comfyart/
@routes.get("/comfyart/")          # web.FileResponse(DIST/"index.html", Cache-Control: no-cache)
                                    # 未构建时 503："ComfyArt is not built yet: run `npm install && npm run build`"
@routes.get("/comfyart/workflows/{name}")
    path = (WORKFLOWS / name).resolve()
    if path.parent != WORKFLOWS.resolve() or path.suffix != ".json" or not path.is_file(): 404
                                    # ★ 路径穿越防护：resolve 后校 parent + 限后缀
if (DIST / "assets").is_dir(): routes.static("/comfyart/assets", DIST / "assets")
```
→ **装一个 ComfyUI 节点包 = 得到整个画布应用**（前端 `dist/` 由 ComfyUI 自己 serve）。

### 57.2 ★ 它只加了一个自定义节点（其余全用官方）

`nodes.py`（23 行）只注册 `ComfyArtPreciseUNETLoader`：
```python
class PreciseUNETLoader:
    DESCRIPTION = "Loads a diffusion model with manual cast ops, skipping the --fast fp8_matrix_mult path that breaks unscaled fp8 checkpoints such as Qwen Image 2512."
    def load(self, unet_name):
        path = folder_paths.get_full_path_or_raise("diffusion_models", unet_name)
        return (comfy.sd.load_diffusion_model(path, model_options={"custom_operations": comfy.ops.manual_cast}),)
NODE_CLASS_MAPPINGS = {"ComfyArtPreciseUNETLoader": PreciseUNETLoader}
```
→ ★ **唯一自写节点是为了绕开 ComfyUI `--fast` fp8 矩阵乘对某些 checkpoint 的破坏**（用 `manual_cast`）。

### 57.3 后端文件清单与体量（事实，共 4573 行 Python）

```
api/  (23 文件 + mcp_tools/)
  __init__.py 3   bot.py 266   bot_asks.py 124  bot_complete.py 55  bot_docs.py 59  bot_media.py 98
  bot_send.py 139 bot_turns.py 277 chat_store.py 129 db.py 122 mcp.py 143 mcp_commands.py 42
  models.py 25  ocr.py 34  pdf.py 42  project_import.py 88  project_store.py 118  projects.py 102
  providers.py 16  settings.py 72  thumbs.py 56  _common.py 49
bot/  (10 文件)
  __init__.py 17  _acp.py 225  _cli_common.py 419  claude_code.py 200  codex.py 399
  deepseek_harness.py 318  deepseek_harness_runtime.py 97  local_llm.py 425  providers.py 99  qwen_code.py 251
workflows/  23 个 *.api.json（+ 1 个 caption json）
```
`api/__init__.py` 只有一行逻辑：`bot.reap_stale_messages()` → **启动时清理上次残留的“进行中”消息**。

### 57.4 ★ 存储层手法（`api/_common.py` 49 行）

- `app_path(*parts) = os.path.join(folder_paths.get_system_user_directory("comfyart"), *parts)` → **所有数据存在 ComfyUI 的 user 目录下**（多用户隔离）；
- `request_user(request)` 用 `PromptServer.instance.user_manager.get_request_user_id(request)`，`KeyError → 403`；
- ★ **原子写 JSON**：
  ```python
  def write_json(path, data):
      os.makedirs(os.path.dirname(path), exist_ok=True)
      tmp = path + ".tmp"
      with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False)
      os.replace(tmp, path)          # ★ 先写 .tmp 再 rename，不写坏文件
  ```
- `read_json(path, default)` 吞 `OSError/ValueError` 返回默认值；`json_body(request)` 校 `isinstance(body, dict)`；`error(msg, status)` 统一 `{"error": ...}`。

### 57.5 工作流资源（`workflows/` 23 个）→ 仍出来的模型选型

已确认存在：`cutout-birefnet` / `edit-qwen-image-2.1` / `erase-lama` / `flux2-klein-9b-t2i` / `i2v-minimax-h3` / `ideogram4-t2i` / `ideogram4-caption.json` / `image-to-3d-trellis2` / `layers-qwen-image-layered` / `mockup-surface` / `multiangle-qwen-image-2.1` / `music-ace-step` / `outpaint-qwen-image-2.1` / `qwen-image-2.1-t2i` / `qwen-image-2512-t2i` / `r2v-minimax-h3` / `sfx-stable-audio` / `speech-kokoro` / `t2v-minimax-h3` / `transcribe-gemma4` …
→ ★ **选型**：图像主用 **Qwen-Image 2.1 / 2512 + Ideogram4 + Flux2-klein-9b**；抠图 **BiRefNet**；擦除 **LaMa**；外扩/编辑/分层/多视角全用 **Qwen-Image**；视频 **MiniMax H3**（t2v/i2v/r2v）；音频 **ACE-Step(音乐) + Stable Audio(SFX) + Kokoro(TTS) + IndexTTS(克隆)**；3D **TRELLIS.2**；OCR/转录 **Gemma4**。

### 57.6 待核实项（不编造）

- `api/mcp.py`（143）/`api/mcp_commands.py`（42）/`api/mcp_tools/` 如何把画布工具暴露成 MCP —— **已核实，见 §61**（`api/mcp.py` 143 与 `api/mcp_tools/bot_tools.py` 191 仍未逐行读）；
- `bot/_cli_common.py`（419）/`bot/local_llm.py`（425）/`bot/codex.py`（399）如何拉起 CLI 子进程与流式解析；
- `api/db.py`（122）是 SQLite 还是 JSON；`api/chat_store.py`（129）存储形式；
- → 如需引用，先跑 §58 核实命令。

---

<a id="sec58"></a>
## 58. 核实命令（自己跑一遍，不要信未核实的描述）

```bash
# 仓库副本在 /tmp/ref/ComfyArt（= C:\Users\Administrator\AppData\Local\Temp\ref\ComfyArt）
cat /tmp/ref/ComfyArt/package.json                       # 依赖清单
cat /tmp/ref/ComfyArt/src/App.tsx                        # 装配入口
grep -n "export " /tmp/ref/ComfyArt/src/*.ts | head -80   # 所有导出
ls /tmp/ref/ComfyArt/workflows                           # 可用工作流 JSON
wc -l /tmp/ref/ComfyArt/src/*.ts /tmp/ref/ComfyArt/src/ui/*.tsx | sort -n
```

---

<a id="sec59"></a>
## 59. 对 Firefly-Canvas 的映射（★ 抄什么 / 怎么抄）

### 59.1 前提：技术栈不同，所以**只能抄设计与算法，不能抄代码**

| | ComfyArt | Firefly-Canvas（我们） |
|---|---|---|
| 前端 | React + **tldraw**（有构建） | **纯 vanilla JS，无框架无构建**（`package.json` 只有 `playwright` + `acorn`） |
| 画布 | tldraw Editor + 自定义 ShapeUtil | 自研：`static/js/smart-canvas.js`（**16,433 行、749 个顶层函数**）+ `canvas-core.js` 181 / `canvas-node-ops.js` 356 / `canvas-connections.js` 496 / `canvas-undo.js` 217 / `canvas-layout-utils.js` 144 |
| 节点 | Shape（image/video/generator/vector/…） | 节点 type：`smart-prompt` / `smart-image` / `smart-agent` / `smart-conversation`（`CONTEXT.md`、`app/constants.py`） |
| 后端 | ComfyUI 节点包（`api/`+`bot/` 共 4573 行 Py） | `main.py` 1314 + `firefly_projects.py` 3405 + `firefly_generation.py` 666 + `local_edit.py` 132 + `layout_engine.py` 265 |
| 已有相关能力 | — | `canvas-image-editor.js` 2171、`canvas-media-history.js` 2393、`canvas-generation-tasks.js` 1294、`canvas-generation-cascade.js` 1264、`canvas-agent-tui.js` 858 |

→ 两类可抄物：**A 类 = 框架无关的纯算法/纯数据**（TS→JS 删类型即可）；**B 类 = 依赖 tldraw 概念的机制**（只抄思路，要重写到我们的 editor 抽象上）。

### 59.2 A 类：可直接搬（按优先级）

| 优先级 | ComfyArt 源 | 我们落点 | 内容 |
|---|---|---|---|
| **P0** | `src/adjust.ts` 159 + `ui/AdjustPanel.tsx` 172 | `local_edit.py` 或新 `static/js/canvas-image-adjust.js` | 16 个调色参数（中性值 0）+ `autoAdjust` + **选区上盖预览 canvas（`PREVIEW_SIDE=900`）+ 按住对比 + 双击滑杆复位 + Apply 智能禁用**；§27.1 |
| **P1** | `ui/SmudgePanel.tsx` 162 | 新涂抹模块 | **完整涂抹算法**（`carry` 快照滞动 + `k = strength*(1-dist²)` 二次衰减 + 拖动插值 `step=d/4` + 20 步 `ImageData` 撤销栈 + `willReadFrequently`）；§27.2 |
| **P0** | `src/videoEdit.ts` 265 | 新 `static/js/canvas-video-edit.js` | 时间线剪辑引擎（Clip/Transition/Subtitle/`composeVideo`）+ `Player`（双 `<video>` + WebAudio + MediaRecorder vp9 8Mbps）；§29+§31 |
| **P0** | `src/jobs.ts` 221 的**三个手法** | `canvas-generation-tasks.js` | ① `job` 存节点 meta → **`resumeJobs` 刷新后接回任务**（对齐 ADR 0017）；② `GenInfo` 写回结果节点 meta（**Remix/复现**）；③ `placers` 注册表 + `history:'ignore'` 原子替换占位节点 |
| **P0** | `src/autosave.ts` 54 + `src/history.ts` 30 | `firefly_projects.py` + 前端 | 防抖 800ms + `running` 串行化 + 四态 `saved/pending/saving/error` + `beforeunload` 拦截 + `saveNow()` 强制落盘；**版本快照 5 分钟一份 + `GET /versions/{time}` 整档回滚**（对齐 ADR 0010） |
| **P0** | `App.tsx` 的 **BroadcastChannel 多标签页保护** | 对照已有 `tests/dual_tab_sync_client.js` | 保存后广播 `{project, tab}` → 其他页 `stale=true` **停写 + 横幅提示刷新** |
| P1 | `src/psd.ts` 70 / `src/pptx.ts` 140 / `src/pdf.ts` 44 | 新导出模块 | **真图层 PSD**、**可编辑文字/形状的 PPTX**（不是贴图）；§8.17/§8.18 |
| P1 | `src/mockupGl.ts` 139 | 新 WebGL 模块 | 完整 GLSL（深度渐变 UV warp + `medianLuma`）；§19 |
| P1 | `src/refModes.ts` 136 | 生成参数层 | **17 个参考图模式 = hint 指令模板 + ComfyUI 预处理器 + control 标志** 三件套；`modesFor` 按模型能力过滤；`applyRefs` 三条注入路径；§26.3 |
| P1 | `src/memory.ts` 91 + `src/skills.ts` 119 | `firefly_agent_tools.py` / Agent 节点 | 四分类长期记忆 + 变更日志 + `same()` 模糊匹配；**兼容 Agent Skills 格式**；§43/§45 |
| P1 | `src/llm.ts` 112 | Agent 节点 | `ask()` + `parseJson()` 容错解析（剥 ```json、取首尾花括号）；§42 |
| P1 | `src/proactive.ts` 125 | Agent 节点 | 主动记记忆 / 学技能 / 审查画布；**每日限额 + Web Locks 跨页互斥 + 画布签名去重 + 导出 1024px 截图做质检**；§47 |
| P2 | `src/booleans.ts` 39 + `src/vectorPath.ts` 54 | 新矢量模块 | 布尔运算 + 路径序列化；§12 |
| P2 | `api/_common.py` 的 `write_json` | `firefly_projects.py` | **先写 `.tmp` 再 `os.replace`** 的原子写；§57.4 |
| P2 | `src/ui/keys.ts` 3 | 所有输入框 | `isEnter = key==='Enter' && !nativeEvent.isComposing && keyCode!==229`（**中文输入法安全**）；§32.2 |
| P2 | `src/ui/Popover.tsx` 67 | 新 `static/js/ui-popover.js` | portal + 自动翻转 + `closest('.ca-pop')` 跨层豁免；§32.3 |
| P2 | `src/ui/AssetPicker.tsx` 81 | 素材库 | 递增序号防竞态 + 200ms 防抖 + `IntersectionObserver({rootMargin:'300px'})`；§33.3 |
| P1 | `bot/_cli_common.py` 419 | ADR 0019（pi sidecar）相关模块 | **生产级 CLI 驱动**：双超时（硬 4h + 空闲 600s）、进程树杀（`taskkill /F /T` / `killpg`）、stderr 只存 64KB、超长生跳过、**失败分支也返 `resume_token`**；§62.6 |
| P1 | `bot/_cli_common.py:24-63` `AGENT_GUIDE` | Agent 节点系统提示词 | 可直接改写的完整提示词（先看图、**没调工具不准说做完**、异步契约、默认落点、多步必 `update_plan`）；§62.2 |
| P2 | `bot/_cli_common.py` `_USAGE_KEY_ALIASES` / `normalize_usage` / `merge_usage` | 计费/用量展示 | 一套别名表兼容 Claude/OpenAI 命名，`cost_usd` 保留 6 位；§62.4 |
| P3 | `src/ui/Shortcuts.tsx` 五组键位表 | 快捷键系统 | 完整键位契约；§34.5 |

### 59.3 B 类：只抄思路（需重写到我们的 editor 抽象）

| ComfyArt 机制 | 我们对应缺口 | 抄法 |
|---|---|---|
| `useSelectionRect()` 拖拽中返回 null（§8.9/§32.1） | 浮动工具栏乱跳 | 加一个统一的「选区屏幕矩形 + 拖拽中置 null」工具函数，**所有浮层共用** |
| 工具栏全部 `position:fixed` + 自算定位（§8.9、§26.1、§34.6） | 菜单定位代码分散 | 统一 `place(rect, size, prefer)`（上→下→左→右 回退 + 边缘 clamp） |
| ★ **参数全存 `shape.props`/`shape.meta`**（§26.1） | 生成参数与节点分离 | **把生成参数当节点数据** → 撤销/持久化/Agent 读写自动可用（**最大架构启示**） |
| `shape.meta.hidden` + `getShapeVisibility`（§38/§53.2） | 图层显隐 | 一行回调；对照我们 `tests/layering_contract_browser_client.js` |
| `registerFrameRules` / `registerCropRule`（§8.5） | 无 frame 自动布局 | 规则注册表：选中 frame 时注入布局规则 |
| `botCommands.ts` 459 命令总线（§10） | `canvas-agent-tui.js` 858 | ★ **命令表 + 每个命令返回文本给 Agent + 失败也回文本**；对标 `firefly_canvas_mcp.py` |
| `api/mcp_tools/canvas.py` 工具 description（§11） | 同上 | 描述写得极细（含 `WAIT_MAX_S=170`、`_relay` 弹图、approval 门控） |
| `Composer.tsx` 一个输入框三模式（§23） | 提示词/Agent 节点 UI 重复 | 保留节点分离（ADR 0002/0012），但**输入框组件可共用** |
| `GenPanel` 运行中自动收起（§26.1） | 生成中面板遮挡 | `if (running) return null` |
| `spawnHolder` 找空位（§51.7） | 结果落点随机 | 「往右排 + 碰撞跳过 + 200 次上限」简单算法 |
| `CommentShape` + `commentsToChat`（§13/§55.3） | 无「批注→Agent」闭环 | ★ **评论节点 → 汇总成 Agent 需求** |
| `storyboardGraph` + `planStoryboard`（§28/§55.5） | 无分镜 | **枚举拼进 prompt + 解析后再校总时长** |
| `refFrame` 参考框（§50） | 无参考图容器 | frame + `meta.refFrame` + 链接开关 |
| `eachRuns` 多目标展开（§55.4） | 批量改图 | **选中 N 个目标 + 一句 prompt = N 次生成，结果放在各自旁边** |
| `CliStreamParser` 去重（§62.5） | 子进程输出重复/丢字段 | **流式已发过就不再从 assistant 补发**；`tool_use_id → name` 反查；`TOOL_RESULT_CAP=4000` 截断 |
| `CORE_MCP_TOOLS` / `COMFY_MCP_ALLOWED_TOOLS` 白名单（§62.3） | 工具按 provider 能力裁剪 | **按 provider 能力下发工具列表**，而不是全发 |
| `shlex.split(posix=sys.platform!="win32")`（§62.3） | Windows 命令行解析 | 我们是 Windows 应用，**这行必须照搬** |

### 59.4 ★★★ 最大单一缺口：**Agent 工具面只有 8 个 vs ComfyArt 40 个**

已核实两边工具清单：

| | 我们 | ComfyArt |
|---|---|---|
| 入口 | `app/agent_extensions/canvas-tools.ts`（**395 行**） | `api/mcp_tools/canvas.py`（270）+ `api/mcp_commands.py`（42）+ `src/botCommands.ts`（459） |
| 工具数 | **8**：`canvas.list_nodes` / `get_node` / `get_selection` / `create_node` / `update_node` / `delete_node` / `connect` / `disconnect` | **40**（§10.5 / §61.3） |
| 能“看”画布吗 | ✖ 只能拿结构化数据 | ✔ `view_canvas`（整页导出 PNG base64，`maxPx = min(2048, args.max_px \|\| 1024)`）+ `view_video`（**抽帧拼 contact sheet**，§61.4）+ `page_text`（PDF 文本） |
| 能干活吗 | ✖ 只能建/改/删节点 | ✔ 生图/生视频/14 种图像编辑/音频/3D/mockup/排版/分层/换角度/矢量化的**全套** |
| 异步契约 | 无 | ✔ 立即返 `{ids, note:'call wait_generation…'}` + `wait_generation(ids, timeout_s≤170)`（§10.4） |
| 审批 | `delete_node` 带 “requires user confirmation” | ✔ `_relay(approval=...)` 集门控，文案带 prompt 前 80 字（§61.3） |

→ **建议优先补的工具（按价值）**：
1. `canvas.view`（把选区/整页导出给模型看，**多模态才能闭环**）+ `canvas.view_media`（视频抽帧拼图）；
2. `canvas.generate` / `canvas.generate_video`（**立即返回 task id + `wait` 工具**，照搬 §10.4 契约）；
3. `canvas.edit_*`（擦除/外扩/抠图/放大/局部重绘 —— 我们 `canvas-image-editor.js` 已有这些能力，**只是没包成工具**）；
4. `canvas.memory` / `canvas.skill`（§45/§43）；
5. 工具参数用 **`additionalProperties: False`** + 公共片段（`_ID`/`_IDS`/`_REGION`/`_PLACE`）+ **错误文案带下一步**（§61.3 / §10.6）。

### 59.5 ★ 三个最应该马上做的（性价比最高）

1. **参数入节点 + `GenInfo` 回写 + `resumeJobs`**（§26.1/§51.2/§51.4）——一次性打通「撤销 / 持久化 / Agent 读写 / 刷新恢复 / Remix」五个能力，与 ADR 0010、ADR 0017 直接对齐。
2. **`useSelectionRect` + 统一浮层定位 + `isEnter` IME 安全**（§32）——共 ≈110 行，但修掉所有浮层抖动与中文输入误发送。
3. **`adjust.ts` 实时预览调色（从原图重算 + 一次拖动 = 一步撤销）**（§27.1/§8.15）——纯算法可直译，用户可感知。

### 59.6 我们已有、ComfyArt 没有的（别反向抄坏）

- **付费任务重启恢复**（ADR 0017）比它的 `resumeJobs` 更完整；
- **底部历史抽屉**（ADR 0013）与它的「版本快照」是不同交互模型：可参考其 `versions` API，**但不要换掉我们的抽屉**；
- **级联生成**（`canvas-generation-cascade.js` 1264）它没有；
- **连线/回路**（`canvas-connections.js` + `smartCascadeEdgeState`/`smartCascadePathForCtx`）它没有；
- **Excel 导入**（`excel_importer.py` + `excel-import` skill）它没有；
- **项目自有数据**（ADR 0003）比它的 `comfyart/projects` 更强调项目边界。

### 59.7 未核实清单（抄之前必验）

- `api/mcp.py` 143 / `api/mcp_tools/` 服务端实现 —— **已核实（§61）**；
- `bot/_cli_common.py` 419 / `bot/local_llm.py` 425 / `bot/codex.py` 399 的子进程与流式解析；
- `api/db.py` 122 / `api/chat_store.py` 129 的存储形式 —— **已核实，见 §60**；
- `src/shapes/TextShape.tsx` 180 / `VectorShape.tsx` 232 / `PenTool` 未逐行读（§12 只拆了 `vectorPath.ts`/`booleans.ts`）；
- `src/ui/ExportDialog.tsx` / `HistoryDialog.tsx` / `Slideshow.tsx` / `Lightbox.tsx` / `SidePanel.tsx` / `TopBar.tsx` / `Dock.tsx` / `CanvasTools.tsx` 未读；
- `src/i18n.ts` 38 / `theme.ts` 34 / `notices.ts` 55 仅局部读过。

---

<a id="sec60"></a>
## 60. 数据库层（`api/db.py` 122 行）★★★ 已核实：SQLAlchemy + SQLite，5 张表

### 60.1 引擎与连接（★ 手法）

```python
def _engine() -> Engine:
    path = app_path("data.db")                       # = folder_paths.get_system_user_directory("comfyart")/data.db
    with _lock:                                      # threading.Lock 保护建连
        engine = _engines.get(path)                  # ★ 按路径缓存引擎（多用户 = 多 db 文件）
        if engine is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
            @event.listens_for(engine, "connect")
            def _pragmas(dbapi_conn, _):
                cur.execute("PRAGMA journal_mode=WAL")     # ★ WAL：读写不互阻
                cur.execute("PRAGMA foreign_keys=ON")      # ★ SQLite 默认不开外键，必须显式开
            Base.metadata.create_all(engine)
            _engines[path] = engine
        return engine

def session() -> Session:
    return Session(_engine(), expire_on_commit=False)   # ★ commit 后对象仍可读（免二次查询）
```

### 60.2 表结构（逐字段核实）

| 表 | 字段 | 要点 |
|---|---|---|
| `comfyart_chats` | `id` PK, `user`, `project`, `title`, `provider`, **`resume_token`**, `run_mode` default `'auto'`, `pinned`, `archived`, `created_at`, `updated_at` | 索引 `ix_comfyart_chats_user_project(user, project)`；★ **`resume_token` = CLI Agent 会话恢复令牌** |
| `comfyart_messages` | **`seq` INTEGER autoincrement PK**, `id` unique, `chat_id` FK→chats `ondelete=CASCADE`, `parent_id`, `role`, **`blocks` Text**, `status`, **`resume_token_after`**, `usage` Text, `created_at` | 索引 `(chat_id, seq)`；★ **用自增 `seq` 作主键（不是 uuid）** → 消息顺序天然可分页；`blocks`/`usage` 存 JSON 字符串 |
| `comfyart_projects` | **`(user, id)` 复合 PK**, `name`, `covers` Text, `pinned`, **`snapshot` Text**, `created_at`, `updated_at` | ★ **画布当前快照直接存 Text 列**（无文件）；`covers` 存封面列表 JSON |
| `comfyart_project_versions` | `seq` autoincrement PK, `user`, `project`, `time` INTEGER, `count`, `snapshot` Text | ★ **复合外键 `(user,project)` → projects `ondelete=CASCADE`** + 索引 `(user, project, time)` → **删项目自动清历史版本** |
| `comfyart_migrations` | **`(user, name)` 复合 PK**, `done_at` | ★ **迁移状态按用户记录** → 新用户自动跑一遗迁移，老用户不重跑 |

### 60.3 ★ 对我们的启示

1. **`seq` 自增主键 + `(chat_id, seq)` 索引** → 比我们用 uuid 排序更适合“流式追加 + 范国查询”；
2. **`resume_token` / `resume_token_after` 两列** → **Agent 崩溃/重启后能接回 CLI 会话**（与我们 ADR 0017 同方向，但针对 Agent）；
3. **快照存 DB Text 列 + 版本表 CASCADE** → 比“JSON 文件 + 快照目录”更不容易出现部分写入（配合 §57.4 的原子写思路）；我们 ADR 0010 用的是 JSON 快照，**可参考但不必推翻**；
- **`PRAGMA journal_mode=WAL` + `foreign_keys=ON`** —— 如果我们用 SQLite，这两行必须加；
5. **按用户分 db 文件（`_engines` 按 path 缓存）** → 多用户隔离最省事的做法。

> 补充：MCP 服务端实现已核实，见 §61。

---

<a id="sec61"></a>
## 61. MCP 服务端（`api/mcp_commands.py` 42 + `api/mcp_tools/*` 502 行）★★★ 已核实：后端→浏览器→后端的 RPC 回路

### 61.1 ★★ 命令回路（`api/mcp_commands.py` 全文 42 行）

```python
COMMAND_EVENT = "comfyart-mcp-command"
DEFAULT_TIMEOUT_S = 20.0
_pending: dict[str, asyncio.Future] = {}

async def submit_command(action, payload, project, timeout=DEFAULT_TIMEOUT_S, chat="") -> dict:
    command_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    _pending[command_id] = future
    try:
        PromptServer.instance.send_sync(COMMAND_EVENT, {"id": command_id, "action": action,
                                                       "project": project, "chat": chat, "args": payload})
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        raise ValueError(f"no ComfyArt page answered the {action!r} command within {timeout:.0f}s — "
                         f"the project {project or '(any)'} must stay open in a browser tab")
    finally:
        _pending.pop(command_id, None)

@routes.post("/comfyart/mcp_command_result")
async def post_command_result(request):
    body = await json_body(request)
    future = _pending.get(str(body.get("command_id") or ""))
    if future is None or future.done():
        return web.json_response({"ok": False, "reason": "unknown or expired command"})
    if body.get("ok"):
        result = body.get("result")
        future.set_result(result if isinstance(result, dict) else {"result": result})   # ★ 非 dict 结果包一层
    else:
        future.set_exception(ValueError(str(body.get("error") or "command failed in the ComfyArt page")))
    return web.json_response({"ok": True})
```
- ★ **画布在浏览器里，所以 MCP 工具必须把命令推到浏览器执行，再用 HTTP 回填 Future**；
- ★ **超时错误文案直接告诉 Agent 怎么修**：“the project must stay open in a browser tab”；
- ★ **`finally: _pending.pop()`** → Future 不泄漏；`future.done()` 检查 → **重复回填不会炸**。

### 61.2 ★ 上下文用 `contextvars`（`api/mcp_tools/_context.py` 全文 4 行）

```python
BOT_CHAT_ID: contextvars.ContextVar[str] = contextvars.ContextVar("comfyart_bot_chat", default="")
PROJECT:     contextvars.ContextVar[str] = contextvars.ContextVar("comfyart_project", default="")
```
→ **项目/会话身份不靠工具参数传**，避免模型漏传或乱传。

### 61.3 ★★ 工具表由 SPECS 自动派生（`api/mcp_tools/canvas.py` 270 行）

- `SPECS: dict[str, dict]` = **39 个工具**；`TOOLS` 用字典推导自动生成，**再单独加 `wait_generation` → 共 40 个**，与前端 `HANDLERS` 的 40 个一一对应（§10.5）：
  ```python
  TOOLS = {name: {"description": spec["description"], "inputSchema": spec["inputSchema"],
                  "handler": _relay(name, **spec["relay"])} for name, spec in SPECS.items()}
  ```
- `_schema(properties, required=())` → **`additionalProperties: False`**（★ 严格 schema，模型不能塞野参数）；
- 复用的参数片段常量：`_ID`（description：`shape id exactly as get_canvas returns it, e.g. shape:abc`）、`_IDS`、`_POINT`、`_REGION = {x,y,width,height}`、`_PLACE = {frame, near}`（**放置控制做成公共片段**）；
- ★ **审批门控**：
  ```python
  def _relay(action, *, timeout=20.0, approval=""):
      async def handler(args):
          if approval:
              label = str(args.get("prompt") or args.get("to") or "")[:80]
              blocked = await maybe_ask_run_approval(f"{approval}{'：' + label if label else ''}")
              if blocked is not None: return blocked
          result = await submit_command(action, args, PROJECT.get(), timeout, chat=BOT_CHAT_ID.get())
          image = result.pop("png", None)
          if image: result["_images"] = [{"data": image, "mime": "image/png"}]
          return result
      return handler
  ```
  → ★ **`png` 从结果里 pop 出来换成 MCP 的 `_images` 附件**（多模态回传）；★ **审批文案里带 prompt 前 80 字**（用户看得懂要做什么）。
- ★ **`wait_generation` 的超时钳位**：`timeout = max(1, min(int(args.get("timeout_s") or 170), 170))`，然后 `submit_command(..., timeout + 15)` → **后端 Future 比前端等待多 15s 缓冲**（避免前端超时但后端刚完成）。

### 61.4 ★★ `view_video`：把视频抽帧拼成一张图给模型看（`api/mcp_tools/media.py` 33 行）

```python
MAX_FRAMES = 16
SHEET_PX = 1536
async def _view_video(args):
    path = view_path(str(args.get("url") or ""))
    count = max(2, min(int(args.get("frames") or 8), MAX_FRAMES))
    start = float(args.get("start") or 0)
    end = float(args["end"]) if args.get("end") is not None else None
    frames, duration = await asyncio.to_thread(video_frames, path, count, start, end)
    sheet = await asyncio.to_thread(lambda: jpeg(contact_sheet(frames, SHEET_PX), SHEET_PX))
    return {"duration_s": round(duration, 2), "frames_at_s": [round(at, 2) for at, _ in frames],
            "_images": [{"data": sheet["data"], "mime": sheet["media_type"]}]}
```
- description 原文要点：`"Look inside a video: returns one image with frames evenly spaced between start and end seconds (whole video by default), each labelled with its time, plus the duration. ... frames 2-16 (default 8); narrow start/end to study one moment closely."`
- ★ **抽帧 + 拼图全部 `asyncio.to_thread`**（不阻塞事件循环）；★ **每帧标注时间戳** → 模型能指认“第 3.5 秒有问题”；★ **返回 `frames_at_s` 数值表** → 模型可用数字对齐。

### 61.5 未逐行读（引用前需核）

- `api/mcp.py` 143（MCP server 挂载/鉴权/工具枚举）；
- `api/mcp_tools/bot_tools.py` 191（含 `maybe_ask_run_approval` 的实现）。

---

<a id="sec62"></a>
## 62. CLI Agent 驱动层（`bot/_cli_common.py` 419 行）★★★ 生产级子进程驱动，与我们 ADR 0019 直接对标

### 62.1 常量（全部实测值）

```python
STREAM_LIMIT = 10 * 1024 * 1024      # stdout 单行上限 10MB
TURN_IDLE_TIMEOUT_S = 600            # 空闲 10 分钟判超时
TURN_MAX_S = 4 * 3600                # 单轮硬上限 4 小时
TOOL_RESULT_CAP = 4000               # 工具结果回传截断 4000 字
PROBE_CACHE_S = 60                   # provider 探测缓存 60s
MCP_TOOL_TIMEOUT_MS = 600_000        # 通过 env MCP_TOOL_TIMEOUT 传给 CLI
```

### 62.2 ★★ `AGENT_GUIDE` 系统提示词全文（可直接改写复用）

> "You are ComfyArt Agent, a design assistant working on the user's live infinite canvas in ComfyArt. The comfyart tools are your eyes and hands on that canvas; images and videos are generated by the user's local ComfyUI.
> - **Look first**: get_canvas lists pages, frames and shapes with their ids; view_canvas returns the real pixels of the page, a frame or some shapes; view_video shows frames across a video (or one stretch of it). **Look at results before calling them good.**
> - **Create**: list_models gives model ids; generate_image / generate_video **return ids right away, then call wait_generation with those ids** to get the placed image or video shape ids. Write generation prompts in detailed English. Pass refs (image shape ids) when the result must follow existing images. **New results without frame or near land in this chat's own frame on the canvas (named after the chat).**
> - **Edit an existing image by shape id**: edit_image, upscale_image, remove_background, expand_image, split_layers, change_angle, replace_text, animate_image, edit_region / erase_region (a region in image pixels), move_object, style_transfer, vectorize_image, edit_elements (text in a picture becomes editable text), create_mockup + place_design (print a design onto a product photo); extend_video, upscale_video, remove_video_background for videos.
> - **Audio and 3D**: generate_music, generate_sound_effect, text_to_speech (voiceover, or voice cloning from an audio shape), image_to_3d.
> - **For a task with several steps, show the plan with update_plan and keep it current as steps finish.**
> - **Layout**: create_frame for artboards, add_text, add_shape, update_shapes to move, resize or restyle, delete_shapes, focus to show the user. place_html puts a complete web page you write on the canvas; create_slides builds a deck of 16:9 frames.
> - **Follow the user's memories and brand kits (brand_kits). Save lasting preferences with memory action='add'; use action='correct' to change one and action='forget' to remove one.**
> - **skill** lists installed instruction packs; when a task matches one, read it first and follow it. Use skill action='save' when the user wants to keep a workflow for later.
> - **ask_user when a decision genuinely belongs to the user; give short options.**
> - **Copy ids exactly as tools return them. Never say something was generated, placed or changed unless a tool did it.**
> Reply in the user's language and keep replies short."

★ 可复用的写法要点：① 先给“眼睛”再看；② 明确“**没调工具就不准说做完了**”（防幻觉）；③ 异步契约写在提示词里；④ 未指定落点时的**默认落点规则**（本会话同名 frame）；⑤ 多步任务必须 `update_plan`；⑥ 结尾统一“用用户语言、回复简短”。

### 62.3 工具白名单（★ 按 provider 能力裁剪）

- `CORE_MCP_TOOLS` = **44 项**：`server_info` + 39 个画布工具（§61.3）+ `wait_generation` + **`ask_user`** + **`update_plan`**；
- `COMFY_MCP_ALLOWED_TOOLS` = **12 项**（直连 ComfyUI MCP 时开放）：`server_info`、`nodes`、`validate_workflow`、`search_models`、`search_templates`、`get_template`、`list_workflow_slots`、`list_workflow_notes`、`workflow_deps`、`node_dependencies`、`system_stats`、`which`；
- `resolve_comfy_mcp_argv(command)`：用户自定义命令用 `shlex.split(command, posix=(sys.platform != "win32"))` + `a.strip('"')`；**Windows 下不能用 posix 模式拆**（会把反斜杠转义搞坏）；没配则 `shutil.which("comfy-mcp")`。

### 62.4 ★ token/成本归一化（不同 CLI 字段名不同）

```python
_USAGE_KEY_ALIASES = {
  "input_tokens":"input_tokens", "prompt_tokens":"input_tokens",
  "output_tokens":"output_tokens", "completion_tokens":"output_tokens",
  "cache_read_input_tokens":"cache_read_input_tokens", "cached_input_tokens":"cache_read_input_tokens",
  "cache_creation_input_tokens":"cache_creation_input_tokens"}
def normalize_usage(raw, cost_usd=None): ...   # 只收 int/float，cost_usd round(...,6)
def merge_usage(total, raw): ...               # 多轮累加
```
→ ★ **一套别名表兼容 Claude/OpenAI 命名**；`cost_usd` 保留 6 位小数。

### 62.5 ★★ `CliStreamParser`：解析 Claude-Code 风 NDJSON（**去重是重点**）

| 行类型 | 处理 |
|---|---|
| `{"type":"system","subtype":"init"}` | 存 `session_id`（**= resume_token**） |
| `{"type":"stream_event"}` | `content_block_delta` → `text_delta`→`BotEvent(t='delta')`；`thinking_delta`→`BotEvent(t='thinking')`；**置 `_saw_stream_delta = True`** |
| `{"type":"assistant"}` | `text`/`thinking` 块：**仅当 `_text_from_assistant and not _saw_stream_delta` 才补发**（★ 防流式已发过又重复）；`tool_use` → **按 block id 去重**（`_emitted_tools`）并记 `_tool_names[id] = name` |
| `{"type":"user"}` | `tool_result` → **用 `_tool_names[tool_use_id]` 反查工具名**，`text[:TOOL_RESULT_CAP]`，带 `is_error` |
| `{"type":"result"}` | `result_seen = True`；存 `session_id`、`normalize_usage(usage, total_cost_usd)`；`is_error` → `result_error = result or subtype or "error"` |
| 非 JSON / 非 dict | **直接返回 `[]`**（不炸） |

### 62.6 ★★★ `run_cli_turn`：双超时 + 进程树杀死 + 错误归因

- **进程组隔离**：Windows `creationflags = subprocess.CREATE_NEW_PROCESS_GROUP`；POSIX `start_new_session = True` → **才能整树杀**；
- `kill_process_tree(handle)`：先 `handle.stop_requested = True`；Windows `taskkill /F /T /PID <pid>`；POSIX `os.killpg(os.getpgid(pid), SIGKILL)`；然后 `wait_for(proc.wait(), 10)`，超时再 `proc.kill()`；
- stderr 独立 `_drain_stderr()` 任务，**只缓存前 65536 字节**（防 OOM）；
- ★ **双超时循环**（不是简单 `wait_for(proc)`）：
  ```python
  if now - started >= TURN_MAX_S:        timeout_reason = f"turn exceeded the {TURN_MAX_S//3600}h hard cap"; raise TimeoutError
  if now - last_activity >= TURN_IDLE_TIMEOUT_S: timeout_reason = f"no activity for {TURN_IDLE_TIMEOUT_S//60} minutes"; raise TimeoutError
  wait = min(TURN_MAX_S - (now-started), TURN_IDLE_TIMEOUT_S - (now-last_activity))
  line = await asyncio.wait_for(proc.stdout.readline(), timeout=wait)
  ```
  → **硬上限 4h + 空闲 10min**，两者取剩余较小值作单次 read 超时；
- ★ **`ValueError`（行超 `STREAM_LIMIT`）→ 记日志、刷活动时间、跳过该行继续**（不因一行坏掉断整轮）；
- ★ **错误归因优先级**（`TurnResult`）：超时 → `error="turn timed out (<原因>)", aborted=True`；`stop_requested` → `aborted=True`（**不算错误**）；`result_error` → 直接用；`returncode not in (0, None) or not result_seen` → **取 stderr 尾 800 字**作 error；
- ★★★ **`resume_token = parser.session_id` 在所有失败分支里都返回** → 配合 §60 的 `chats.resume_token` / `messages.resume_token_after` → **崩溃/超时后能接回 CLI 会话**。

### 62.7 未逐行读（引用前需核）

`bot/claude_code.py` 200、`bot/codex.py` 399、`bot/qwen_code.py` 251、`bot/_acp.py` 225、`bot/local_llm.py` 425、`bot/deepseek_harness.py` 318、`bot/providers.py` 99（`BotEvent`/`EmitFn`/`TurnHandle`/`TurnResult` 定义在此）、`api/bot_turns.py` 277、`api/bot.py` 266。

---

<a id="sec63"></a>
## 63. Provider 抽象层（`bot/providers.py` 99 行）★★★ 接多个 CLI Agent 的最小接口

### 63.1 五个数据类（全部 `@dataclass`）

```python
@dataclass
class ProviderStatus:  available: bool; version: str = ""; logged_in: Optional[bool] = None; detail: str = ""
@dataclass
class ProviderCaps:    stateful: bool = True; tools: str = "mcp"; attachments: bool = True
@dataclass
class TurnRequest:
    chat_id: str; user_text: str
    resume_token: Optional[str] = None
    history: Optional[list[dict]] = None
    mcp_endpoint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    model: str = ""
    comfy_mcp_argv: list[str] = field(default_factory=list)
@dataclass
class BotEvent:        # ★ 流式事件（前端 messagesAtom 的源头）
    t: str; text: str = ""; name: str = ""; input: Optional[dict] = None
    detail: str = ""; id: str = ""; is_error: bool = False
@dataclass
class TurnResult:      resume_token: Optional[str] = None; error: str = ""; aborted: bool = False; usage: Optional[dict] = None
class TurnHandle:      # 不是 dataclass：只有 .process（子进程句柄）与 .stop_requested
```
- ★ **`BotEvent` 只有 7 个字段**（`t/text/name/input/detail/id/is_error`）→ **一套事件格式屏蔽所有 CLI 的差异**（claude/codex/qwen/deepseek/local）；
- ★ **`TurnResult.resume_token` 在成功与失败都填**（§62.6）→ 会话可续；
- ★ **`ProviderCaps.tools` 是字符串 `"mcp"`**（不是 bool）→ **预留多种工具注入方式**（MCP / 内置 / 无）；
- ★ **`TurnRequest` 把 `mcp_endpoint` + `comfy_mcp_argv` + `allowed_tools` 三项分开传** → 不同 provider 用不同接入方式。

### 63.2 ★ 抽象基类只有 4 个必选方法

```python
class AgentProvider(ABC):
    id: str = ""; label: str = ""; supports_branch: bool = True
    @abstractmethod async def probe(self) -> ProviderStatus: ...
    @abstractmethod def capabilities(self) -> ProviderCaps: ...
    async def list_models(self) -> list[str]: return []      # 可选
    def model_options(self) -> list[dict]: return []         # 可选
    @abstractmethod async def send(self, turn, emit, handle) -> TurnResult: ...
    @abstractmethod async def stop(self, handle) -> None: ...
```
- `EmitFn = Callable[[BotEvent], Awaitable[None]]` → **异步回调推事件**（不是 generator）；
- ★ **注册表极简**：`_REGISTRY: dict[str, AgentProvider]` + `register_provider` / `get_provider` / `list_providers`；
- `bot/__init__.py` 只 17 行：导入 5 个 provider 类（`ClaudeCodeProvider`、`CodexCodeProvider`、`DeepSeekHarnessProvider`、`LocalLlmProvider`、`QwenCodeProvider`）——**注册发生在各模块内**。

### 63.3 ★ 对我们的启示

1. **`supports_branch` 作为类属性** → 分支能力在 provider 层声明，UI 按此显示/隐藏（§54.1 `ProviderInfo.branch`）；
2. **`probe()` 与 `capabilities()` 分离** → “装了吗”与“能干什么”分开问，UI 才能分别显示 `available`/`logged_in`/`attachments`；
3. **`stop(handle)` 单独成方法** → 取消必须是一等公民（配合 §62.6 的进程树杀）；
4. **事件格式统一 + provider 各自翻译** → 我们接 pi sidecar（ADR 0019）时，**先定一个 7 字段事件格式，再让 sidecar 适配**，比反向适配省事。

---

<a id="sec64"></a>
## 64. 轮次编排（`api/bot_turns.py` 277 行）★★★ 流式落库 + 防幻觉汇报 + 每会话队列

### 64.1 常量与状态

```python
_ALLOWED_TOOLS = ["mcp__comfyart__*"]        # :20
_PERSIST_INTERVAL_S = 3.0                    # :21
_TITLE_MAX = 48                              # :22
class _TurnState: handle, chat_id, message_id, blocks: list[dict], last_persist: float, tool_started: dict[str, float]
ACTIVE_TURNS: dict[str, _TurnState] = {}     # ★ key = chat_id（一个会话同时只一轮）
QUEUED: dict[str, list[dict]] = {}           # ★ 每会话一个待跑队列
def broadcast(event, payload): PromptServer.instance.send_sync("comfyart-bot", {"event": event, **payload})
```

### 64.2 ★★★ `unverified_write_notice`：检测“只吹不做”的轮次（防幻觉汇报）

```python
_WRITE_TOOL_RE = re.compile(
    r"\b(generate_image|generate_video|edit_image|animate_image|upscale_image|"
    r"remove_background|expand_image|split_layers|place_html|create_slides|"
    r"add_text|add_shape|create_frame|update_shapes|delete_shapes)\b")

def unverified_write_notice(blocks):
    if any(b.get("type") == "tool_use" for b in blocks):   # ★ 本轮调过工具 → 不管
        return None
    text = "\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
    if not _WRITE_TOOL_RE.search(text):
        return None
    return {"type": "notice", "level": "warn",
            "text": "This turn made no tool calls, so nothing it describes was "
                    "actually done — the canvas is unchanged."}
```
→ ★ **只在“本轮零工具调用 + 文本里提到了写操作工具名”时插一条 warn notice**；只在 `status == "done"` 时跑（`_run_turn` 收尾）。
→ ★ **这是比 §62.2 提示词更硬的兜底**：提示词是“要求”，这个是“事后校验并告知用户”。**我们做 Agent 节点时应该照搬这个模式**（用我们自己的工具名列表）。

### 64.3 ★★ 流式事件 → blocks 合并 + 落库节流（`emit` 闭包）

- `delta` → **若最后一个 block 是 `text` 就追加，否则新建**（`thinking` 同理）→ **blocks 是合并后的连续段，不是一堆碎片**；
- `tool_use` → `state.tool_started[ev.id] = time.monotonic()`；
- `tool_result` → `status: "error"|"success"` + **`duration_ms = int((monotonic() - started) * 1000)`**（★ 工具耗时由服务端算，不依赖 provider）；
- ★ **`ev.t == "session"` 立即双写**：`update_message(..., resume_token_after=ev.id)` **和** `update_chat(..., resume_token=ev.id)`，**任一返回 None 就 `raise RuntimeError`** → 会话 token 必须实时落库（否则崩溃后无法续）；
- ★ **落库节流**：`if ev.t in ("tool_use","tool_result") or now - state.last_persist > 3.0: update_message(blocks=state.blocks)` → **工具边界必存、纯文本每 3 秒存一次**（既不丢进度也不狂写 SQLite）。

### 64.4 ★ MCP 端点与会话绑定

```python
def _mcp_endpoint(chat_id): return f"http://127.0.0.1:{PromptServer.instance.port}/comfyart/mcp?bot_chat={chat_id}"
def _allowed_tools(comfy_mcp_argv):
    tools = list(_ALLOWED_TOOLS)
    if comfy_mcp_argv: tools += [f"mcp__comfy__{t}" for t in COMFY_MCP_ALLOWED_TOOLS]
    return tools
def provider_model(provider_id):
    key = f"bot-model-{provider_id}"
    return str(get_setting(key) or "").strip() if key in SETTINGS_SPEC else ""   # ★ 未登记的 key 直接忽略
```
→ ★ **`?bot_chat=` 就是 §61.2 `contextvars` 的来源**；★ **每个 provider 的模型选择是独立 setting key**。

### 64.5 ★ 无状态 provider 的历史回放

```python
history = None if provider.capabilities().stateful else _replay_history(chat_id, state.message_id)
```
`_replay_history` 跳过当前消息**及其 `parent_id`**（因为当前用户消息已单独传），只取 `user`/`assistant`，内容用 `blocks_text()`：
```python
text → 原文；image/video/audio → f"[attached {kind}: {url}]"；file → f"[attached document {name}: {url}]"；notice → f"[notice: {text}]"
```
→ ★ **附件在历史里降级为一行文本占位**（不重复塞图）。

### 64.6 ★ 收尾与队列

- `try/except` 把异常变成正常收尾：`result = None; error = str(e) or type(e).__name__; status = "error"`；
- `status = "aborted" if result.aborted else ("error" if error else "done")`；
- 收尾顺序：`ACTIVE_TURNS.pop(chat_id)` → `bot_asks.cancel_chat_asks(chat_id)` → error 则追加 `{"type":"notice","level":"error","text":error}` / done 则跑 `unverified_write_notice` → `update_message(blocks, status, resume_token_after=token, usage=usage)` → **标题只在原本为空时**用 `_derive_title(text)`（折叠空白取前 48 字）→ `broadcast("turn_done", {...})` → **`_drain_queue(chat_id)`**；
- ★ **`begin_turn`**：先 `create_message(role="assistant", status="streaming", parent_id=user_msg["id"])` → `ACTIVE_TURNS[chat_id] = state` → `broadcast("turn_start", {user_message, assistant_message})` → `asyncio.create_task(_run_turn(...), name=f"comfyart-bot-{chat['id'][:8]}")`；
- ★ **`_run_turn` 开头 `await asyncio.sleep(0.1)`** → **保证前端先收到 `turn_start` 再收到 delta**（否则消息会错位）；
- ★ **`QUEUED` + `_drain_queue`**：一轮结束自动跑下一排队消息（把它的 user message 状态改成 `done` 再 `begin_turn`）→ **用户连发不会并发跑两轮**。

### 64.7 ★ 对我们的直接可用点

1. **`unverified_write_notice` 模式**（§64.2）——我们 Agent 节点最需要的兜底；
2. **落库节流规则**（工具边界必存 + 3 秒一次）——我们若做流式 Agent 消息，直接照搬；
3. **`session` 事件立即双写 + 写不到就抛**——会话 token 的可靠性策略；
4. **`turn_start` 后 `sleep(0.1)`** —— 极小的时序技巧，避免前端消息错位；
5. **每会话队列 + `ACTIVE_TURNS[chat_id]`** —— 简单有效的并发控制。

---

<a id="sec65"></a>
## 65. 文字 shape（`src/shapes/TextShape.tsx` 180 行）★★★ 自测尺寸 + 纯文本/富文本双轨

### 65.1 props（15 个）与迁移

```ts
export const TEXT = 'ca-text'
// props: w h text font weight size color stroke align letter line autoSize decoration transform rich
rich: T.jsonValue.nullable() as T.Validator<JsonObject | null>
const versions = createShapePropsMigrationIds(TEXT, { AddDecoration: 1, AddRich: 2 })
sequence: [
  { id: versions.AddDecoration, up: (p) => Object.assign(p, { decoration: 'none', transform: 'none' }) },
  { id: versions.AddRich,       up: (p) => { p.rich = null } },
]
getDefaultProps() = { w:1, h:1, text:'', font:'Arial', weight:400, size:80, color:'#000000', stroke:'',
                      align:'left', letter:0, line:1.3, autoSize:true, decoration:'none', transform:'none', rich:null }
```

### 65.2 ★★ props → CSS 的唯一映射函数

```ts
export function textStyle(p: TextShape['props']): React.CSSProperties {
  return {
    fontFamily: p.font, fontWeight: p.weight, fontSize: p.size, color: p.color,
    textAlign: p.align, letterSpacing: `${p.letter}px`, lineHeight: p.line,
    textDecoration: p.decoration === 'none' ? undefined : p.decoration,
    textTransform: p.transform,
    WebkitTextStroke: p.stroke ? `${Math.max(1, p.size / 24)}px ${p.stroke}` : undefined,   // ★ 描边宽度随字号缩放
    paintOrder: 'stroke fill',                                                            // ★ 描边画在填充后面，不吃掉字形
  }
}
```
→ ★ **`paintOrder: 'stroke fill'` 是文字描边不糊的关键一行**；★ **描边宽 = `max(1, size/24)`**。

### 65.3 ★★★ 自动尺寸（`autoSize`）的实现

```ts
useLayoutEffect(() => {                       // ★ 无依赖数组：每次渲染后同步测
  const el = box.current; if (!el) return
  const w = p.autoSize ? Math.ceil(el.scrollWidth) : p.w
  const h = Math.ceil(el.scrollHeight)
  if (Math.abs(w - p.w) > 0.5 || Math.abs(h - p.h) > 0.5) {          // ★ 差 > 0.5px 才写回
    editor.run(() => editor.updateShape({ id, type: TEXT, props: { w, h } }), { history: 'ignore' })
  }
})
```
- ★ **`history: 'ignore'`** → 自动尺寸变化**不进撤销栈**；
- ★ **0.5px 阈值** → 避免亚像素抖动导致无限重渲染；
- 配套 CSS：`width: p.autoSize ? 'max-content' : p.w`、`whiteSpace: p.autoSize ? 'pre' : 'pre-wrap'`、`wordBreak: 'break-word'`。

### 65.4 ★★ 空文本/尾换行的零宽空格技巧

```tsx
{p.rich ? <RichView doc={p.rich} /> : p.text.endsWith('\n') || !p.text ? `${p.text}​` : p.text}
```
→ **末尾补 U+200B 零宽空格**，否则空行/空文本的 `scrollHeight` 测不出来 → 高度塌成 0。

### 65.5 ★★ 纯文本与富文本双轨（★ 重要数据设计）

```ts
onChange={(doc) => editor.updateShape({ id, type: TEXT,
  props: { text: plainOf(doc), rich: hasMarks(doc) ? doc as JsonObject : null } })}
```
- ★ **`text`（纯文本）永远写** → 搜索/导出/Agent 读取都走它；
- ★ **`rich` 只在真有 mark 时存**（`hasMarks(doc)`），否则 `null` → **省存储、且能一眼判断“这段文字有没有样式”**；
- 渲染：`p.rich ? <RichView doc={p.rich}/> : p.text`（§36 `RichView` 是无 tiptap 的 JSON 渲染器）。

### 65.6 编辑与工具

- `canEdit() = true`；编辑时：静态 div `visibility: hidden`、容器 `pointerEvents: 'all'`、`overflow: 'visible'`（**文字可溢出 shape 边界显示**）；
- ★ **`onEditEnd`：`if (!shape.props.text.trim()) this.editor.deleteShape(shape.id)`** → **不留空文字节点**；
- ★ **`onResize`：`resizeBox(...)` 后强制 `autoSize: false`** → **用户手动拉框 = 关闭自动尺寸**；
- `TextTool extends StateNode`：`onEnter` 设十字光标；`onPointerDown` → 在点击点建 shape（`props:{text: t('common.text')}`）→ `setCurrentTool('select')` → `select(id)` → **`setEditingShape(id)`**（★ 点一下就直接进入编辑，不用二次双击）；`onCancel` 回 select。

---

<a id="sec66"></a>
## 66. 矢量形状 shape（`src/shapes/VectorShape.tsx` 232 行）★★★ SVG 渲染 + inside/outside 描边 + 拖拽建形

### 66.1 props（16 个）与迁移（★ 迁移里可以删字段）

```ts
export const VECTOR = 'ca-shape'
export type VectorKind = 'rect' | 'ellipse' | 'polygon' | 'star'      // 代码里还用了 'path'
export interface GradientStop { offset: number; color: string }
// props: w h kind fill radius sides fillType stops image blend angle stroke strokeWidth strokeAlign dash d
stops: T.arrayOf(T.object({ offset: T.number, color: T.string }))
versions = { AddStrokeGradient: 1, AddPaint: 2, AddPath: 3 }
sequence: [
  { id: AddStrokeGradient, up: p => Object.assign(p, { fillType:'solid', fill2:'#FFFFFF', angle:90, stroke:'', strokeWidth:4, strokeAlign:'center', dash:'solid' }) },
  { id: AddPaint, up: p => { Object.assign(p, { stops: [{offset:0,color:p.fill},{offset:1,color:p.fill2}], image:'', blend:'normal' }); delete p.fill2 } },  // ★ 旧字段 fill2 升级成 stops 后删除
  { id: AddPath, up: p => { p.d = '' } },
]
getDefaultProps() = { w:320, h:200, kind:'rect', fill:'#CCCCCC', radius:0, sides:5, fillType:'solid',
  stops:[{offset:0,color:'#CCCCCC'},{offset:1,color:'#FFFFFF'}], image:'', blend:'normal', angle:90,
  stroke:'#333333', strokeWidth:2, strokeAlign:'center', dash:'solid', d:'' }
```

### 66.2 ★★ `strokeAlign` 三态（inside/outside/center）的实现手法

```ts
const sw = stroke ? strokeWidth * (strokeAlign === 'center' ? 1 : 2) : 0     // ★ inside/outside 用 2 倍宽
const dashArray = dash === 'dashed' ? `${strokeWidth * 3} ${strokeWidth * 2}`
                : dash === 'dotted' ? `0 ${strokeWidth * 2}` : undefined     // ★ dotted = 0 长度 + round linecap
// defs 里：{strokeAlign === 'inside' && <clipPath id={cid}>{outline}</clipPath>}
{strokeAlign !== 'outside' && <g fill={paint}>{outline}</g>}                 // 先填充
{stroke && <g fill="none" stroke={stroke} strokeWidth={sw} strokeDasharray={dashArray}
            strokeLinecap={dash === 'dotted' ? 'round' : undefined}
            clipPath={strokeAlign === 'inside' ? `url(#${cid})` : undefined}>{outline}</g>}
{strokeAlign === 'outside' && <g fill={paint}>{outline}</g>}                 // outside：填充后画，盖掉内半
```
→ **inside = 裁剪；outside = 换绘制顺序；center = 正常** —— 不依赖 `paint-order` 也不依赖 SVG2 的 `stroke-alignment`（浏览器不支持）。

### 66.3 ★★ `fillType` 四态 + 渐变/图案

- `paint = fillType === 'solid' || (fillType === 'image' && !image) ? fill : `url(#${gid})``；
- ★ **SVG id 从 shape id 派生**：`gid = 'g-' + shape.id.replace(/\W/g, '')`、`cid = 'c-' + ...` → **同页多 shape 不冲突**；
- ★ **线性渐变用角度算端点**（objectBoundingBox 单位）：
  ```ts
  const rad = (angle * Math.PI) / 180
  x1 = 0.5 - Math.cos(rad)/2, y1 = 0.5 - Math.sin(rad)/2
  x2 = 0.5 + Math.cos(rad)/2, y2 = 0.5 + Math.sin(rad)/2
  ```
- `radial` → `<radialGradient id={gid}>`；
- `image` → `<pattern patternUnits="userSpaceOnUse" width={w} height={h}><image href={image} width={w} height={h} preserveAspectRatio="xMidYMid slice" /></pattern>`（**slice = 填满裁切**）；
- ★ **`<Stops>` 渲染前先 `sort((a,b) => a.offset - b.offset)`** → 用户乱序编辑色标也正确。

### 66.4 几何与混合

- `polygonPoints(kind, w, h, sides)`：`n = kind === 'star' ? sides * 2 : sides`；`a = -Math.PI/2 + i*2π/n`；`r = kind === 'star' && i % 2 ? 0.45 : 1` → **星形与多边形共用一个函数，内半径固定 0.45**；
- `outline` 分四支：`<rect rx={Math.min(radius, w/2, h/2)}>` / `<ellipse>` / `<path d={scalePath(d, w, h)} fillRule="evenodd">` / `<polygon points={...}>`；
  → ★ **路径存归一化 `d`，渲染时 `scalePath(d, w, h)` 缩放**（§12）；★ **`fillRule="evenodd"`** → 布尔运算产生的洞能正确显示；
- `mixBlendMode` 只在非 `normal` 时设；`BLEND_MODES` = 16 个 CSS 值（`normal multiply screen overlay darken lighten color-dodge color-burn hard-light soft-light difference exclusion hue saturation color luminosity`）；
- 容器 `pointerEvents: 'none'`（**SVG 不吃事件，交给 tldraw 命中**）+ `svg overflow: 'visible'`（**描边不被裁掉**）。
- `canEdit(shape) = shape.props.kind === 'path'` → **只有钢笔路径可进编辑态**（§12 PathEditor）；
- `getIndicatorPath`：path 用 `new Path2D(scalePath(d, w, h))`（**选中框贴合真实轮廓**），否则矩形。

### 66.5 ★★ 拖拽建形（`VectorTool`）—— 三个细节值得照搬

```ts
onPointerDown: createShape({ x: p.x, y: p.y, props: { kind: vectorKindAtom.get(), w: 1, h: 1 } }); this.draft = { id, x: p.x, y: p.y }
private track() {
  let [w, h] = [Math.abs(p.x - x), Math.abs(p.y - y)]
  if (this.editor.inputs.getShiftKey()) w = h = Math.max(w, h)              // ★ Shift = 正方形/正圆
  updateShape({ id, x: p.x < x ? x - w : x, y: p.y < y ? y - h : y,        // ★ 支持反向拖拽（向左上拖）
                props: { w: Math.max(w, 1), h: Math.max(h, 1) } })
}
onPointerUp: track(); if (Math.max(w, h) < MIN_DRAG) {                     // ★ 点击（拖 < 4px）给默认尺寸
  const h = kind === 'rect' ? RECT_H : CLICK_W
  updateShape({ id, x: draft.x - CLICK_W/2, y: draft.y - h/2, props: { w: CLICK_W, h } })   // ★ 居中到点击点
}
```
- 常量：`CLICK_W = 320`、`RECT_H = 200`、`MIN_DRAG = 4`；
- ★ **拖拽过程中就实时 `updateShape`**（所见即所得），松手后 `setCurrentTool('select')` + `select(id)`；
- ★ **要画的类型存在全局 `vectorKindAtom`**（工具栏切换 → 工具读取），而不是每个工具一套子菜单。

---

<a id="sec67"></a>
## 67. 导出对话框 + 手写 PDF（`src/ui/ExportDialog.tsx` 109 行）★★★ 零依赖 PDF 生成

### 67.1 导出选项

- `exportAtom = atom<TLShapeId[] | null>('export', null)`（**null = 关闭**）；
- `FORMATS = [png, jpeg, svg, pdf, pptx(图片版), pptx-text(可编辑文字版)]`；`SCALES = [1, 2, 3]`；
- ★ **文件名消毒**：`(editor.getDocumentSettings().name || 'comfyart').replace(/[\/:*?"<>|]/g, '_')`；
- ★ **JPEG 强制背景**：`background: background || format === 'jpeg'`；
- ★ **UI 按格式条件渲染**：`format !== 'svg'` 才显示倍率；只有 `png || svg` 显示“包含画布背景”；
- ★ **区分“导出选中”还是“整页”**：`fromSelection = ids.every(id => selected.includes(id))` → 提示文案不同。

### 67.2 ★★★ `pdfFromJpeg(jpeg, w, h)`：手写最小 PDF（约 30 行，无任何依赖）

```ts
function pdfFromJpeg(jpeg: Uint8Array, w: number, h: number): Blob {
  const enc = new TextEncoder(); const parts: Uint8Array[] = []; const offsets: number[] = []; let length = 0
  const push = (p: Uint8Array | string) => { const b = typeof p === 'string' ? enc.encode(p) : p; parts.push(b); length += b.length }
  const obj = (n: number, body: string) => { offsets[n] = length; push(`${n} 0 obj\n${body}\nendobj\n`) }
  push('%PDF-1.4\n')
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>')
  obj(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
  obj(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`)
  offsets[4] = length
  push(`4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${w} /Height ${h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`)
  push(jpeg); push('\nendstream\nendobj\n')
  const content = `q ${w} 0 0 ${h} 0 0 cm /Im0 Do Q`
  obj(5, `<< /Length ${content.length} >>\nstream\n${content}\nendstream`)
  const xref = length
  push(`xref\n0 6\n0000000000 65535 f \n${[1,2,3,4,5].map(n => `${String(offsets[n]).padStart(10,'0')} 00000 n \n`).join('')}`)
  push(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`)
  return new Blob(parts as BlobPart[], { type: 'application/pdf' })
}
```
- ★ **`/Filter /DCTDecode` = 直接嵌 JPEG 原始字节**（不重编码、不转码）；
- ★ **内容流就一句**：`q ${w} 0 0 ${h} 0 0 cm /Im0 Do Q`；
- ★ **xref 表偏移必须 `padStart(10,'0')` + ` 00000 n `（注意行尾空格）**；
- 调用：`exportImage(editor, ids, { format:'jpeg', scale, background:true })` → `pdfFromJpeg(new Uint8Array(await blob.arrayBuffer()), round(width), round(height))` → `download(blob, `${name}.pdf`)`。
→ ★ **我们若要 PDF 导出，这段可以几乎原样搬**（比引入 jsPDF/pdf-lib 轻得多）。

### 67.3 版本历史对话框（`src/ui/HistoryDialog.tsx` 67 行）★★★ 回滚前先存当前

```ts
const restore = (v: Version) => confirmAtom.set({
  title: ..., desc: t('historyDialog.theCurrentCanvasWillBe'), ok: ...,
  run: async () => {
    await saveVersion(projectId, getSnapshot(editor.store), editor.getCurrentPageShapeIds().size)   // ★ 先把当前档存成新版本
    loadSnapshot(editor.store, await loadVersion(projectId, v.time))                                // 再回滚
    close()
  },
})
```
- ★ **回滚不是破坏性的**：回滚前自动把当前画布存成一个新版本 → **用户可以再滚回来**；
- ★ **走 `confirmAtom` 二次确认**（统一的确认原子，不是 `window.confirm`）；
- ★ **相对时间**：`ago(time)` → `<1min` “刚刚” / `<60min` “N 分钟前” / `<24h` “N 小时前” / 否则 `new Date(time).toLocaleString()`；
- 列表行显示 `ago(v.time)` + `t('historyDialog.elements', v.count)`（**元素数量**）+ 回滚按钮；`versions?.length === 0` 显示空态。

---

<a id="sec68"></a>
## 68. i18n 与主题（`src/i18n.ts` 38 行 + `src/theme.ts` 34 行）★★ 极简但完整

### 68.1 i18n

```ts
const MESSAGES: Record<Lang, Record<string,string>> = { zh, en }        // 直接 import 两个 JSON
const KEY = 'comfyart.lang'
export let lang: Lang = 'en'                                            // ★ 模块级可变导出（不是 atom）
export async function initLocale() {
  const stored = storedLang()                                            // localStorage，try/catch 包裹
  if (stored) lang = stored
  else {
    const res = await fetch('/api/settings/Comfy.Locale').catch(() => null)   // ★ 复用宿主（ComfyUI）的语言设置
    const value = res?.ok ? await res.json().catch(() => null) : null
    lang = String(value || navigator.language).toLowerCase().startsWith('zh') ? 'zh' : 'en'
  }
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'          // ★ 同步 <html lang>
}
export function setLang(next: Lang) { try { localStorage.setItem(KEY, next) } catch {} ; location.reload() }
export function t(key: string, ...args: (string|number)[]): string {
  const text = MESSAGES[lang][key] ?? MESSAGES.zh[key] ?? key            // ★ 三级兜底：当前语言 → 中文 → key 本身
  return args.length ? text.replace(/\{(\d+)\}/g, (m, i) => (i < args.length ? String(args[i]) : m)) : text
}
```
- ★ **`t()` 的兜底链**（当前语言 → 参考语言 → key）→ **缺翻译不会白屏**；
- ★ **占位符是 `{0}` `{1}`**（不是 `{name}`），实现只一个正则；
- ★ **切语言直接 `location.reload()`** —— 简单可靠，避免运行时全量重渲染；
- ★ **`lang` 是模块级 `let`**（不是 atom）→ 因为只在启动时定一次。

### 68.2 主题

```ts
export type Theme = 'light' | 'dark' | 'system'
const KEY = 'comfyart.theme'
export const themeAtom = atom<Theme>('theme', (() => { try { const s = localStorage.getItem(KEY)
  return s === 'dark' || s === 'system' ? s : 'light' } catch { return 'light' } })())   // ★ IIFE 初始化，模块加载即读
const isDark = (theme: Theme) => theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
export function markDark(dark: boolean) { document.documentElement.dataset.theme = dark ? 'dark' : 'light' }
export function setTheme(editor, theme) { saveTheme(theme); editor.user.updateUserPreferences({ colorScheme: theme }) }
markDark(isDark(themeAtom.get()))                                        // ★ 模块底部立即执行一次 → 防首屏闪白
```
- ★ **`system` 用 `matchMedia('(prefers-color-scheme: dark)')` 解析**；
- ★ **同时设 `document.documentElement.dataset.theme`（CSS 变量）与 editor 的 `colorScheme`** → 自绘 UI 与画布 UI 同步；
- ★ **模块加载时就 `markDark`**（不等 React 挂载）→ **首屏不闪白**。

---

<a id="sec69"></a>
## 69. 拖拽导入 / 画布拾取 / 确认 / 缩放 / 特效（5 个小文件）★★★ 小而极可搬

### 69.1 拖拽导入 + 自动排齐（`src/ui/DropHandlers.tsx` 59 行）

```ts
export function importFiles(editor: Editor, accept: string) {          // ★ 手写 file input，无组件
  const input = document.createElement('input')
  input.type = 'file'; input.accept = accept; input.multiple = true
  input.onchange = () => { const files = Array.from(input.files ?? [])
    if (files.length) void editor.putExternalContent({ type: 'files', files, point: editor.getViewportPageBounds().center }) }
  input.click()
}
```
- ★ **外部文件分流**（`editor.registerExternalContentHandler('files', ...)`）：
  ```ts
  for (const file of files) {
    if (isPdf(file)) await placePdf(editor, file, point)
    else if (!(await placeEmbed(editor, file, point))) rest.push(file)     // ★ 自己处理不了的收集起来
  }
  if (rest.length) await defaultHandleExternalFileContent(editor, { point, files: rest }, { toasts, msg })
  ```
  → **PDF 自己处理、可嵌入类型自己处理、其余交给官方默认处理器**（不重复造轮子）。
- ★ **用 before/after 集合差找出新增 shape**：
  ```ts
  const before = new Set(editor.getCurrentPageShapeIds())
  ... 导入 ...
  const added = editor.getCurrentPageShapes().filter(s => !before.has(s.id) && s.parentId === editor.getCurrentPageId())
  if (added.length > 1) lineUp(editor, added)
  ```
- ★ **`lineUp` 自动排一行**（`GAP = 20`）：
  ```ts
  const boxes = shapes.map(s => ({ s, b: editor.getShapePageBounds(s)! })).sort((a,b) => a.b.x - b.b.x)
  const mid = boxes[0].b.y + Math.max(...boxes.map(({b}) => b.h)) / 2      // ★ 以最高者定中线
  let x = boxes[0].b.x
  editor.run(() => { for (const {s,b} of boxes) {
    editor.updateShape({ id: s.id, type: s.type, x: s.x + x - b.x, y: s.y + mid - b.h/2 - b.y })
    x += b.w + GAP
  } }, { history: 'ignore' })                                              // ★ 自动布局不进撤销栈
  ```
  → ★ **`s.x + x - b.x` 的写法**：shape 坐标是左上角，所以先减当前 bounds 再加目标位置。

### 69.2 ★★ 画布拾取模式（`src/ui/CanvasPick.tsx` 39 行）

- `canvasPickAtom = { id: 生成块 id, to: 目标槽位 } | null`；
- ★ **用 `useEffect` 监听选区变化来完成拾取**（不是给画布加自定义工具）：
  ```ts
  if (!pick || (selected.length === 1 && selected[0] === pick.id)) return
  const target = editor.getShape(pick.id); if (!target) { canvasPickAtom.set(null); return }
  const srcs = selected.flatMap(id => { const src = fileSrc(editor, editor.getShape(id))
    return src && acceptsKind(target, pick.to, refKind(src)) ? [src] : [] })
  if (selected.length && !srcs.length) return          // ★ 选了不合规的 → 不退出，让用户重选
  canvasPickAtom.set(null)
  if (srcs.length) fillTarget(editor, pick.id, pick.to, srcs).catch(e => onError(errorText(e)))
  editor.select(pick.id)
  ```
- UI 就是一条提示 + Cancel 按钮（`ca-pickbar`）；提示文案按槽位（图/视频/音频）不同。
→ ★ **我们做“选画布素材填参数”时，这是最省事的实现方式**（零新工具、零模式状态机）。

### 69.3 全局确认原子（`src/ui/Confirm.tsx` 31 行）

```ts
interface Ask { title: string; desc: string; ok: string; run: () => void }
export const confirmAtom = atom<Ask | null>('confirm', null)
```
→ **任何地方 `confirmAtom.set({...})` 就能弹统一确认框**（危险按钮加 `ca-panel__danger`）；点遮罩关闭（`e.target === e.currentTarget`）+ `stopPropagation`。

### 69.4 手算缩放（`src/ui/Zoom.tsx` 41 行）

```ts
const zoomTo = (z: number) => {
  const c = editor.getViewportPageBounds().center
  const s = editor.getViewportScreenBounds()
  editor.setCamera({ x: s.w/2/z - c.x, y: s.h/2/z - c.y, z }, { animation: { duration: 200 } })
}
```
→ **围绕视口中心缩放到指定倍率**的通用公式（`screen/2/z - center`）；动画 200ms。

### 69.5 ★★★ 特效用注入 CSS 实现（`src/ui/Effects.tsx` 14 行）

```ts
const css = useValue('effects css', () => editor.getCurrentPageShapes().flatMap(s => {
  if (!s.meta.effects) return []
  const { shadow, blur } = effectsOf(s)
  const filters = [shadow && `drop-shadow(${shadow.x}px ${shadow.y}px ${shadow.blur}px ${shadow.color})`,
                   blur   && `blur(${blur}px)`].filter(Boolean)
  return filters.length ? [`.tl-shape[data-shape-id="${s.id}"]{filter:${filters.join(' ')}}`] : []
}).join('\n'), [editor])
return css ? <style>{css}</style> : null
```
- ★ **一行选择器 `.tl-shape[data-shape-id="..."]{filter:...}` 就给任意 shape 加阴影/模糊**，**完全不改 shape 渲染代码**；
- ★ **`useValue` 把整页特效合成一个 CSS 字符串** → 只有一个 `<style>` 节点，不是每个 shape 一个；
- ★ **`drop-shadow` 而非 `box-shadow`** → 贴合非矩形轮廓（透明 PNG 也正确）。
→ ★ **对我们的画布同样适用**：给节点 DOM 加稳定 `data-node-id`，特效就是一条注入 CSS。

---

<a id="sec70"></a>
## 70. 覆盖层编辑工具（`src/ui/EditLayers.tsx` 205 行）★★★ 分数坐标 + 四种模式共用一套骨架

`EditLayers` 按 `modeAtom` 分发四种覆盖层（全部用 `useSelectionRect()` 精确盖住选区）：
```ts
if (mode === 'erase')    return <EraseLayer rect={rect} />
if (mode === 'expand')   return <ExpandLayer rect={rect} imageW={imageSize(editor, shape.props.assetId)[0]} />
if (mode === 'layers')   return <BoxLayer rect={rect} />
if (mode === 'separate') return <SingleBoxLayer rect={rect} />
```

### 70.1 ★★ 统一的 UV 换算（所有层共用）

```ts
const uv = (e: React.PointerEvent): [number, number] => {
  const b = canvas.current!.getBoundingClientRect()
  return [Math.min(1, Math.max(0, (e.clientX - b.left) / b.width)),
          Math.min(1, Math.max(0, (e.clientY - b.top)  / b.height))]     // ★ 一律 clamp 到 [0,1]
}
```
→ **所有几何都存 0..1 分数**，与图片像素/画布缩放解耦（与 §15 CropBar、§12 vectorPath 同一套哲学）。

### 70.2 擦除层（笔刷）

- canvas 按 DPR 放大：`c.width = Math.round(w * dpr)`（`dpr = devicePixelRatio || 1`）；
- ★ **笔刷半径也存分数**：`strokesAtom.set([...strokesAtom.get(), { points: [uv(e)], r: brush / 2 / Math.max(1, w) }])`；
- ★ **追加点是“替换最后一个 stroke 的 points 数组”**（不是新建 stroke）：`strokesAtom.set([...all.slice(0, -1), { ...last, points: [...last.points, uv(e)] }])`；
- 重绘 effect 依赖 `[strokes, w, h]`，`ctx.strokeStyle = 'rgba(59,130,246,.5)'` + `drawStrokes(ctx, strokes, c.width, c.height)`（§37）；
- 自绘光标 `<div className="ca-erase__cursor">`（`onPointerLeave` 清光标）。

### 70.3 外扩层（四边拖把）

- `s = (rect.r - rect.l) / Math.max(1, imageW)` → **屏幕像素 ↔ 图片像素**换算系数；
- `outer = { l: rect.l - pads.l*s, t: rect.t - pads.t*s, r: rect.r + pads.r*s, b: rect.b + pads.b*s }`；
- ★ **四边拖拽的 delta 符号按边翻转**：
  ```ts
  const delta = side === 'l' ? d.x - e.clientX : side === 'r' ? e.clientX - d.x
              : side === 't' ? d.y - e.clientY : e.clientY - d.y
  padsAtom.set({ ...padsAtom.get(), [side]: Math.max(0, Math.round(d.v + delta / s)) })
  ```
- 视觉：四个 `ca-expand__strip` 半透明条标出将扩展的区域。

### 70.4 多框层（分层用，`MAX_REGIONS` 上限）

- `norm([x0,y0,x1,y1]) = [min(x0,x1), min(y0,y1), abs(x1-x0), abs(y1-y0)]` → **拖完再归一化**（支持任意方向拖）；
- ★ **最小尺寸过滤**：`if (box[2] > 0.02 && box[3] > 0.02) boxesAtom.set([...boxes, box])`（**2% 以下当误触丢弃**）；
- 框上显示序号 `{i+1}`（对应 prompt 里的区域编号）+ 删除按钮（`onPointerDown` 要 `stopPropagation` 否则又开新框）。

### 70.5 ★★ 单框层（8 向手柄 + move）—— 完整的手柄状态机

```ts
const HANDLES: [string, number, number][] = [['nw',0,0],['n',0.5,0],['ne',1,0],['e',1,0.5],['se',1,1],['s',0.5,1],['sw',0,1],['w',0,0.5]]
// onPointerDown：读 e.target.dataset.h 决定拖的是手柄 / move / 新框
drag.current = handle && box ? { h: handle, from: p, edges: [box[0], box[1], box[0]+box[2], box[1]+box[3]] }
                             : { h: 'new', from: p, edges: [p[0], p[1], p[0], p[1]] }
// move：
if (d.h === 'new') [r, b] = [x, y]
else if (d.h === 'move') {
  const dx = Math.min(Math.max(x - d.from[0], -l), 1 - r)      // ★ 平移钳位到 [0,1]
  const dy = Math.min(Math.max(y - d.from[1], -t), 1 - b)
  ;[l, t, r, b] = [l + dx, t + dy, r + dx, b + dy]
} else {
  if (d.h.includes('w')) l = x                                  // ★ 用名字包含判断，8 个手柄一段代码
  if (d.h.includes('e')) r = x
  if (d.h.includes('n')) t = y
  if (d.h.includes('s')) b = y
}
boxesAtom.set([[Math.min(l,r), Math.min(t,b), Math.abs(r-l), Math.abs(b-t)]])   // ★ 允许拖反，最后归一化
// up：if (bw < 0.01 || bh < 0.01) boxesAtom.set([])             // ★ 拖太小 = 删除
```
- ★ **手柄用 `data-h` 属性 + `cursor: ${id}-resize`**（CSS 光标名与手柄名同名，零映射表）；
- ★ **`d.h.includes('w'|'e'|'n'|'s')` 一段代码处理 8 个方向**。

### 70.6 ★★ 路径编辑（`src/ui/PathEditor.tsx` 77 行）—— paper.js + 三层坐标变换

- 只在 `editor.getEditingShapeId()` 对应 `VECTOR` 且 `kind === 'path'` 时激活；
- ★ **`useValue('camera', () => editor.getCamera(), [editor])`** —— **订阅相机只为触发重渲染**（不用返回值），让覆盖层跟着平移缩放；
- ★ **三层坐标变换**：
  ```ts
  const xf = editor.getShapePageTransform(shape.id)
  const screen = (p: paper.Point) => editor.pageToViewport(xf.applyToPoint({ x: p.x * w, y: p.y * h }))   // 归一化 → 页 → 视口
  const local = (e) => { const page = editor.screenToPage({x: e.clientX, y: e.clientY})
    const q = xf.clone().invert().applyToPoint(page)
    return new paper.Point(q.x / w, q.y / h) }                                                                // 视口 → 页 → 归一化
  ```
- ★ **paper 对象只在拖拽期间驻留**：`const item = drag.current?.item ?? new paper.CompoundPath({ pathData: d, insert: false })`（**`insert: false` 不污染 paper 全局项目**）；
- 拖三种点：`point`（`seg.point = p`）、`in`（`seg.handleIn = p.subtract(seg.point)`）、`out`（`seg.handleOut = p.subtract(seg.point)`）→ **控制句柄是相对向量**；
- 每次 move 直接 `editor.updateShape({ props: { d: g.item.pathData } })`（**实时写回 shape**）；
- ★ **`end()` 收尾重新归一化**（§12 `normalized(paper, item)`）：
  ```ts
  const n = normalized(paper, g.item)
  const page = xf.applyToPoint({ x: n.x * w, y: n.y * h })
  const at = editor.getPointInParentSpace(shape.id, page)
  editor.updateShape({ id, type: VECTOR, x: at.x, y: at.y, props: { w: n.w * w, h: n.h * h, d: n.d } })
  ```
  → **拖出原范围时自动扩 shape 边界并搬移位置**；
- 覆盖层就是**一个 `<svg>`**：每段画 `<line>`（句柄线）+ `<circle r=4>`（句柄）+ `<rect 10×10>`（锚点），`onPointerDown` 绑在图形元素上。

---

<a id="sec71"></a>
## 71. 文本与矢量 shape（`src/shapes/TextShape.tsx` 180 行 / `src/shapes/VectorShape.tsx` 232 行）★★★ 自定义 shape 的完整范式

### 71.1 ★★ 自定义 shape 的四件套（两个文件共同的结构）

1. **类型注册**：`declare module 'tldraw' { export interface TLGlobalShapePropsMap { [TEXT]: {...} } }` → **shape props 有静态类型**；
2. **`static props: RecordProps<T>` 用 `T.*` 校验器**：`w: T.number` … `rich: T.jsonValue.nullable() as T.Validator<JsonObject | null>`、`stops: T.arrayOf(T.object({ offset: T.number, color: T.string }))`（★ **嵌套对象数组也能声明**）；
3. **props 迁移**（★ 我们做版本升级必需）：
   ```ts
   const versions = createShapePropsMigrationIds(VECTOR, { AddStrokeGradient: 1, AddPaint: 2, AddPath: 3 })
   static override migrations = createShapePropsMigrationSequence({ sequence: [
     { id: versions.AddStrokeGradient, up: (props) => Object.assign(props, { fillType:'solid', fill2:'#FFFFFF', angle:90, ... }) },
     { id: versions.AddPaint, up: (props) => { Object.assign(props, { stops: [{offset:0,color:props.fill},{offset:1,color:props.fill2}], image:'', blend:'normal' }); delete props.fill2 } },
     { id: versions.AddPath, up: (props) => { props.d = '' } },
   ]})
   ```
   → ★ **迁移可以 `delete` 旧字段**（`fill2` 被 `stops` 取代）；
4. **`StateNode` 子类做工具**（`TextTool` / `VectorTool`）。

### 71.2 文本 shape（`TEXT = 'ca-text'`）

- **15 个 props**：`w h text font weight size color stroke align letter line autoSize decoration transform rich`；
- 默认值：`{w:1, h:1, text:'', font:'Arial', weight:400, size:80, color:'#000000', stroke:'', align:'left', letter:0, line:1.3, autoSize:true, decoration:'none', transform:'none', rich:null}` → ★ **w/h 默认 1，靠 autoSize 量出来**；字号默认 **80**；
- ★ **`textStyle(p)` 导出为纯函数**（面板预览与画布共用同一份样式）：
  ```ts
  WebkitTextStroke: p.stroke ? `${Math.max(1, p.size / 24)}px ${p.stroke}` : undefined,
  paintOrder: 'stroke fill',                       // ★ 描边画在填充后面 → 文字描边不吃笔画
  ```
- ★★★ **autoSize 自测尺寸（每帧 `useLayoutEffect`）**：
  ```ts
  const w = p.autoSize ? Math.ceil(el.scrollWidth) : p.w
  const h = Math.ceil(el.scrollHeight)
  if (Math.abs(w - p.w) > 0.5 || Math.abs(h - p.h) > 0.5)
    editor.run(() => editor.updateShape<TextShape>({ id: shape.id, type: TEXT, props: { w, h } }), { history: 'ignore' })
  ```
  → ★ **`{ history: 'ignore' }` 让自动改尺寸不进撤销栈**（否则每次输入都产生 undo）；**0.5px 阈值**防抖动；
  → 配套 CSS：`width: autoSize ? 'max-content' : w`、`whiteSpace: autoSize ? 'pre' : 'pre-wrap'`、`wordBreak: 'break-word'`；
- ★ **空文本/尾换行补零宽空格**：`p.text.endsWith('\n') || !p.text ? `${p.text}​` : p.text`（**U+200B**，否则高度塌陷）；
- ★ **纯文本 + 富文本双写**：`onChange={(doc) => updateShape({ props: { text: plainOf(doc), rich: hasMarks(doc) ? doc as JsonObject : null } })}` → **无标记时 `rich = null`**（省存储、老逻辑仍可读）；渲染 `p.rich ? <RichView doc={p.rich}/> : text`；
- ★ **编辑时静态 div 只隐藏不卸载**：`visibility: editing ? 'hidden' : undefined` + `pointerEvents: editing ? 'all' : undefined` → **保留尺寸测量**；
- `onEditEnd(shape)`: `if (!shape.props.text.trim()) this.editor.deleteShape(shape.id)`（★ 空文本不留壳）；
- `onResize`: `const next = resizeBox(shape, info); return { ...next, props: { ...next.props, autoSize: false } }`（★ **手动 resize 自动关 autoSize**）；
- `getIndicatorPath`: `new Path2D().rect(0, 0, w, h)`；
- ★ **`TextTool.onPointerDown` 一次点击完成“建 + 选 + 进编辑”**：
  ```ts
  editor.createShape({ id, type: TEXT, x: p.x, y: p.y, props: { text: t('common.text') } })
  editor.setCurrentTool('select'); editor.select(id); editor.setEditingShape(id)
  ```

### 71.3 矢量 shape（`VECTOR = 'ca-shape'`）

- **16 个 props**：`w h kind fill radius sides fillType stops image blend angle stroke strokeWidth strokeAlign dash d`；`kind: 'rect'|'ellipse'|'polygon'|'star'`（+ `'path'`）；
- ★ **`BLEND_MODES` 15 种**：`normal, multiply, screen, overlay, darken, lighten, color-dodge, color-burn, hard-light, soft-light, difference, exclusion, hue, saturation, color, luminosity`；
- ★ **`vectorKindAtom = atom<VectorKind>('vector kind', 'rect')`** —— **工具选择用全局 atom 记住**（连续画同种形状不用重选）；
- 常量：`CLICK_W = 320`、`RECT_H = 200`、`MIN_DRAG = 4`；
- ★ **多边形/星形顶点生成**：
  ```ts
  function polygonPoints(kind, w, h, sides): [number, number][] {
    const n = kind === 'star' ? sides * 2 : sides
    return Array.from({ length: n }, (_, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / n
      const r = kind === 'star' && i % 2 ? 0.45 : 1        // ★ 星形内半径 = 0.45
      return [w/2 + Math.cos(a)*r*w/2, h/2 + Math.sin(a)*r*h/2]
    })
  }
  ```
- ★ **渐变 id 用 shape id 消毒生成**：`const gid = `g-${shape.id.replace(/\W/g, '')}``、`cid = `c-${...}``（★ SVG id 必须唯一且合法）；
- ★ **填充解析**：`const paint = fillType === 'solid' || (fillType === 'image' && !image) ? fill : `url(#${gid})``；
- ★ **描边对齐用“宽度翻倍 + 裁剪 + 绘制顺序”三种手段模拟**（SVG 没有 inside/outside stroke）：
  ```ts
  const sw = stroke ? strokeWidth * (strokeAlign === 'center' ? 1 : 2) : 0
  // inside：先画 fill，再画 stroke 并 clipPath 到形状内
  // outside：先画 stroke，再画 fill 盖住内半
  {strokeAlign !== 'outside' && <g fill={paint}>{outline}</g>}
  {stroke && <g fill="none" stroke={stroke} strokeWidth={sw} strokeDasharray={dashArray}
               strokeLinecap={dash === 'dotted' ? 'round' : undefined}
               clipPath={strokeAlign === 'inside' ? `url(#${cid})` : undefined}>{outline}</g>}
  {strokeAlign === 'outside' && <g fill={paint}>{outline}</g>}
  ```
- ★ **虚线/点线**：`dashArray = dash === 'dashed' ? `${strokeWidth*3} ${strokeWidth*2}` : dash === 'dotted' ? `0 ${strokeWidth*2}` : undefined`（**点线 = 0 长度 + round linecap**）；
- ★ **线性渐变端点由角度算**：`x1 = 0.5 - cos(rad)/2, y1 = 0.5 - sin(rad)/2, x2 = 0.5 + cos(rad)/2, y2 = 0.5 + sin(rad)/2`（`rad = angle*PI/180`）；
- ★ **图片填充用 `<pattern>` + `preserveAspectRatio="xMidYMid slice"`**（等价 CSS `background-size: cover`）；
- ★ **`<Stops>` 渲染前排序**：`[...stops].sort((a,b) => a.offset - b.offset)`（**用户拖乱也正确**）；
- 圆角钳位：`rx={Math.min(radius, w/2, h/2)}`；
- ★ **`canEdit(shape) { return shape.props.kind === 'path' }`** —— **只有手绘路径可进 PathEditor**；
- `path` 用 `scalePath(d, w, h)`（§12）+ `fillRule="evenodd"`；`getIndicatorPath` 对 path 返回 `new Path2D(scalePath(d,w,h))`；
- ★ **`VectorTool` 拖拽建形（含 Shift 正方形 + 点击默认尺寸）**：
  ```ts
  onPointerDown: createShape({ ..., props: { kind: vectorKindAtom.get(), w: 1, h: 1 } }); this.draft = { id, x: p.x, y: p.y }
  onPointerMove: if (this.draft && editor.inputs.getIsDragging()) this.track()
  private track() {
    let [w, h] = [Math.abs(p.x - x), Math.abs(p.y - y)]
    if (this.editor.inputs.getShiftKey()) w = h = Math.max(w, h)          // ★ Shift = 正方形
    updateShape({ id, x: p.x < x ? x - w : x, y: p.y < y ? y - h : y, props: { w: Math.max(w,1), h: Math.max(h,1) } })  // ★ 支持反向拖
  }
  onPointerUp: if (Math.max(w,h) < MIN_DRAG) {                            // ★ 视为点击 → 给默认尺寸
    const h = kind === 'rect' ? RECT_H : CLICK_W
    updateShape({ id, x: draft.x - CLICK_W/2, y: draft.y - h/2, props: { w: CLICK_W, h } })   // ★ 以点击点为中心
  }
  ```

---

<a id="sec72"></a>
## 72. 导出对话框（`src/ui/ExportDialog.tsx` 109 行）★★★ 手写 PDF 生成器（零依赖）

### 72.1 ★★★ `pdfFromJpeg(jpeg: Uint8Array, w, h): Blob` —— 30 行手写 PDF

```ts
function pdfFromJpeg(jpeg, w, h) {
  const enc = new TextEncoder(); const parts: Uint8Array[] = []; const offsets: number[] = []; let length = 0
  const push = (p) => { const b = typeof p === 'string' ? enc.encode(p) : p; parts.push(b); length += b.length }
  const obj = (n, body) => { offsets[n] = length; push(`${n} 0 obj\n${body}\nendobj\n`) }
  push('%PDF-1.4\n')
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>')
  obj(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>')
  obj(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`)
  offsets[4] = length
  push(`4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${w} /Height ${h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`)
  push(jpeg); push('\nendstream\nendobj\n')
  const content = `q ${w} 0 0 ${h} 0 0 cm /Im0 Do Q`
  obj(5, `<< /Length ${content.length} >>\nstream\n${content}\nendstream`)
  const xref = length
  push(`xref\n0 6\n0000000000 65535 f \n${[1,2,3,4,5].map(n => `${String(offsets[n]).padStart(10,'0')} 00000 n \n`).join('')}`)
  push(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`)
  return new Blob(parts as BlobPart[], { type: 'application/pdf' })
}
```
- ★ **JPEG 用 `/Filter /DCTDecode` 原样嵌入**（**不重编码、不转 PNG**）→ **PDF 导出 = JPEG 导出 + 30 行包装**；
- ★ 结构只有 5 个对象：Catalog → Pages → Page（`MediaBox` + `/XObject << /Im0 4 0 R >>` + `Contents`）→ Image XObject → 内容流 `q {w} 0 0 {h} 0 0 cm /Im0 Do Q`；
- ★ **xref 偏移必须 `padStart(10,'0')`**，且 `f` 行是 `0000000000 65535 f `（**尾部有空格，行宽固定 20 字节**）；
- → **我们若要 PDF 导出，这段可以直接搬**（我们已有 PDF 相关代码，但这条“JPEG 直嵌”路径最省依赖）。

### 72.2 导出选项与流程

- `exportAtom = atom<TLShapeId[] | null>('export', null)` → **`null` = 关闭，数组 = 要导出的 shape ids**（一个 atom 同时当“开关 + 参数”）；
- `type Format = 'png'|'jpeg'|'svg'|'pdf'|'pptx'|'pptx-text'`；`SCALES = [1,2,3]`（默认 **2**）；
- ★ **PDF 走 JPEG 路径**：`const { blob, width, height } = await exportImage(editor, ids, { format:'jpeg', scale, background: true })` → `pdfFromJpeg(...)`；
- ★ **JPG 强制不透明**：`background: background || format === 'jpeg'`；
- ★ **背景 checkbox 只对 png/svg 显示**，`scale` 对 svg 隐藏（矢量不需要倍率）；
- ★ **pptx 两档**：`exportPptx(editor, ids, format === 'pptx-text', scale, name)` → **图片版 vs 可编辑文字版**（§18）；
- ★ **文件名消毒**：`(editor.getDocumentSettings().name || 'comfyart').replace(/[\/:*?"<>|]/g, '_')`；
- ★ **“导出选区还是整页”提示**：`const fromSelection = ids.every(id => selected.includes(id))`；
- 统一走 §16 的 `exportImage(editor, ids, {format, scale, background})`；错误 `onError(errorText(e))`（§jobs）；
- ★ **modal 点遮罩关闭**：`onPointerDown={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) close() }}`（`stopPropagation` 防事件穿透到画布）。

<a id="sec73"></a>
## 73. 画布历史对话框（`src/ui/HistoryDialog.tsx` 67 行）★★ 恢复前先存当前版本

- `historyAtom = atom('canvas history', false)`；
- ★ **恢复不是破坏性的**：
  ```ts
  const restore = (v: Version) => confirmAtom.set({ title, desc, ok, run: async () => {
    await saveVersion(projectId, getSnapshot(editor.store), editor.getCurrentPageShapeIds().size)   // ★ 先给当前状态存一份
    loadSnapshot(editor.store, await loadVersion(projectId, v.time))                                // ★ 再载入目标版本
    close()
  }})
  ```
  → **用 tldraw 原生 `getSnapshot` / `loadSnapshot`**；版本条目带 `count`（元素数）便于人判断；
- ★ **确认走 `confirmAtom`**（`{title, desc, ok, run}` 的通用确认 atom）；
- ★ **相对时间**：`ago(time)` → `<1min` “just now”；`<60min` “N min ago”；`<24h` “N h ago”；否则 `new Date(time).toLocaleString()`；
- ★ **三态**：`versions === null` 加载中、`versions.length === 0` 空态文案、否则列表；打开时 `setVersions(null)` 再 `listVersions(projectId).then(setVersions)`。

---

<a id="sec74"></a>
## 74. 顶栏（`src/ui/TopBar.tsx` 89 行）★★ 页 tab 内联改名 + 评论 badge

- ★ **导出目标解析**：
  ```ts
  function exportTargets(editor: Editor): TLShapeId[] {
    const selected = editor.getSelectedShapeIds()
    if (selected.length) return selected
    return editor.getSortedChildIdsForParent(editor.getCurrentPageId()).filter(id => editor.getShape(id)?.type !== COMMENT)
  }
  ```
  → **没选中就导整页，但过滤掉评论 shape**；`exportable = useValue('exportable', () => exportTargets(editor).length > 0, [editor])` → **响应式禁用导出按钮**；
- ★ **未处理评论数 badge**：`useValue('open comments', () => allComments(editor).filter(c => !c.props.done).length, [editor])` → `openComments > 0 && <span className="ca-badge">`；
- ★ **页 tab**：`useValue('pages', () => editor.getPages())` + `useValue('current page', () => editor.getCurrentPageId())`；`role="tablist"` / `role="tab"` / `aria-selected`（**a11y**）；`data-on` 表示当前页；
- ★ **双击 tab 内联改名**：`<input autoFocus defaultValue={p.name}>`；`onBlur` 提交 `editor.renamePage(p.id, e.target.value.trim() || p.name)`（**空则回退原名**）；`onKeyDown` **先 `e.stopPropagation()`**（防全局快捷键抢键）+ `isEnter(e) → blur()` + `Escape → setRenaming(null)`；
- ★ **删除页按钮只在 `pages.length > 1` 时出现**（**不允许删最后一页**），且走 `confirmAtom`（`desc` 带页名）；
- `add()`：`editor.createPage({ name: t('topBar.page', pages.length + 1) })` → `editor.getPages().at(-1)!` → `setCurrentPage`；
- ★ **根 div `onPointerDown={(e) => e.stopPropagation()}`** —— 顶栏事件不冒泡到画布；
- ★ **`SettingsMenu` 用 render-prop 传 trigger**：`trigger={({ open, toggle }) => <button data-open={open || undefined} onClick={toggle}>...}`（**菜单组件不关心按钮长什么样**）；
- 侧栏按钮是 toggle：`sideAtom.set(side === 'references' ? null : 'references')`（再点一次关闭）。

<a id="sec75"></a>
## 75. 侧栏容器（`src/ui/SidePanel.tsx` 77 行）★★ 一个 atom 管五个面板 + 左右分栏

- `type Side = 'layers' | 'files' | 'assets' | 'comments' | 'references'`；`sideAtom = atom<Side | null>('side panel', null)`；`TITLES: Record<Side, string>`；
- ★ **左右分栏规则（同一组件两种位置）**：
  ```ts
  const LEFT: Side[] = ['assets', 'references']
  const side = useValue(sideAtom)
  if (!side || LEFT.includes(side) !== !!left) return null     // ★ left prop 决定这一侧渲染哪些面板
  ```
- ★ **`reveal(editor, id)` 从面板跳到元素**（三步）：
  ```ts
  const page = editor.getAncestorPageId(id); if (page) editor.setCurrentPage(page)   // 1. 先切到元素所在页
  editor.select(id)                                                                  // 2. 选中
  editor.zoomToSelection({ animation: { duration: 200 } })                           // 3. 带动画缩放
  ```
- ★ **`onWheel={(e) => e.stopPropagation()}`** —— **侧栏滚动不会触发画布缩放**（我们侧栏必踩的坑）；
- `Layers` 面板 = 可折叠「History」缩略图网格（`generated(editor)` 取所有生成物 + `Preview` 组件）+ 分隔线 + `LayerTree`；折叠箭头用 `data-flip={history || undefined}` 旋转；
- `Files` 面板 = 列表 + 原生下载 `<a href={m.src} download={m.name}>`；★ **文件名从 URL query 里取**：`new URL(m.src, location.origin).searchParams.get('filename')`；末尾固定一条 “no more items” 空态；
- ★ **状态一律用 `data-*` 属性而非 class**：`data-on` / `data-open` / `data-left` / `data-flip` / `data-tool` / `data-h`（CSS 选择器 `[data-on]`）→ **无 class 拼接逻辑**。

---

<a id="sec76"></a>
## 76. 画布工具条（`src/ui/CanvasTools.tsx` 73 行）★★★ 迷失导航 + 背景色存 meta

- 常量：`DEFAULT_BG = '#F5F5F5'`、`DEFAULT_DARK_BG = '#1A1A1A'`；
- ★ **画布背景色存在 `documentSettings.meta.bg`**（不是 shape、不是 localStorage）：
  ```ts
  const bg = useValue('canvas bg', () => String(editor.getDocumentSettings().meta.bg
      ?? (editor.user.getIsDarkMode() ? DEFAULT_DARK_BG : DEFAULT_BG)), [editor])
  const set = (value) => { const doc = editor.getDocumentSettings(); editor.updateDocumentSettings({ meta: { ...doc.meta, bg: value } }) }
  ```
  → ★ **写 meta 必须 spread 旧 meta**（否则覆盖别的键）；
- ★ **背景通过 CSS 变量注入画布容器**：`editor.getContainer().style.setProperty('--ca-canvas', bg)`（`useEffect` 依赖 `[editor, bg]`）→ **不往元素写 inline style**；
- ★★★ **`BackToContent`「迷失导航」**（我们画布很缺的这个功能）：
  ```ts
  const lost = useValue('lost', () => {
    const shapes = editor.getCurrentPageShapes()
    if (!shapes.length) return false                                  // 空页不算迷失
    const view = editor.getViewportPageBounds()
    return !shapes.some(s => { const b = editor.getShapePageBounds(s); return !!b && view.collides(b) })
  }, [editor])
  if (!lost) return null
  // 渲染：<div className="ca-back">「nothing in view」+ 按钮 → editor.zoomToFit({ animation: { duration: 200 } })
  ```
  → **判定 = 当前视口矩形与任何 shape 都不相交**；**响应式 `useValue` 自动跟随平移缩放**；
- ★ **小地图直接用 tldraw `DefaultMinimap`**，开关用组件内 `useState`（不进全局 atom，因为它不影响别处）；
- `toggle = (s: Side) => () => sideAtom.set(side === s ? null : s)`（工厂函数复用 toggle）。

<a id="sec77"></a>
## 77. 底部工具坞（`src/ui/Dock.tsx` 184 行）★★★ 全局单键快捷键 + 智能评论 + 全格式上传

### 77.1 ★★★ 全局单键快捷键表（含三重输入守卫）

```ts
const keys: Record<string, () => void> = {
  a: () => { setVideo(false); createGenerator(editor) },
  s: () => { setVideo(true);  createGenerator(editor, true) },
  c: () => { /* 见 77.2 */ },
  d: () => editor.setCurrentTool(DOODLE),
  p: () => editor.setCurrentTool(PEN),
  t: () => editor.setCurrentTool(TEXT),
  r: () => { vectorKindAtom.set('rect');   editor.setCurrentTool(VECTOR) },
  o: () => { vectorKindAtom.set('ellipse'); editor.setCurrentTool(VECTOR) },
  l: () => editor.setCurrentTool('line'),
  n: () => editor.setCurrentTool('note'),
  'shift+l': () => editor.setCurrentTool('arrow'),
  'shift+?': () => shortcutsAtom.set(true),
}
const onKey = (e: KeyboardEvent) => {
  if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return                                   // 守卫1：修饰键/长按
  if (e.target instanceof Element && e.target.closest('input, textarea, select, [contenteditable="true"]')) return  // 守卫2：输入框
  if (editor.getEditingShapeId()) return                                                        // 守卫3：正在编辑 shape
  const fn = keys[`${e.shiftKey ? 'shift+' : ''}${e.key.toLowerCase()}`]                         // ★ 键名 = shift 前缀 + key.toLowerCase()
  if (!fn) return
  e.preventDefault(); e.stopPropagation(); fn()
}
document.addEventListener('keydown', onKey, true)      // ★ 捕获阶段注册
```
→ **三重守卫是我们做单键快捷键必须照搬的**（否则在聊天框打字会切工具）。

### 77.2 ★★ `c` 键的智能评论（命中测试决定行为）

```ts
c: () => {
  const p = editor.inputs.getCurrentPagePoint()
  if (editor.getCurrentToolId() !== 'select' || !imageUnder(editor, new Box(p.x, p.y, 1, 1))) return editor.setCurrentTool(COMMENT)
  const id = createShapeId()
  editor.createShape({ id, type: COMMENT, x: p.x - COMMENT_SIZE / 2, y: p.y - COMMENT_SIZE / 2 })
  editor.select(id)
}
```
→ ★ **用 `new Box(p.x, p.y, 1, 1)` 做 1×1 命中测试**：select 工具 + 指针下有图片 → **直接在指针处放评论**；否则进入评论工具（点选/套索/框选）。

### 77.3 ★★ 插入菜单：一张表同时驱动 UI 与快捷键文案

```ts
const INSERTS: [string, string, keyof typeof Icons, string, string?][] = [
  ['text','Text','textTool','T'], ['rect','Rectangle','rect','R'], ['ellipse','Circle','ellipse','O'],
  ['polygon','Polygon','polygon',''], ['star','Star','star',''], ['pen','Pen','pen','P'],
  ['line','Line','minus','L'], ['arrow','Arrow','link','Shift L'], ['note','Sticky note','page','N'], ['frame','Artboard','frame','F'],
]
const COMPOSE_INSERTS: [string, string, keyof typeof Icons][] = [['board','Board','frame'], [GENBLOCK,'AI block','genImage'], [QR,'QR code','qr']]
const KINDS: [CommentKind, string, keyof typeof Icons][] = [['point','Click select','comment'], ['lasso','Lasso select','lasso'], ['box','Box select','box']]
```
→ ★ **`Icons[icon]` 用字符串索引图标表**（`keyof typeof Icons` 保证类型安全）；★ **菜单项右侧快捷键文案与真实键位同源**；
- ★ **`pickTool(id)` 先设 kind 再切工具**：`vectorKindAtom.set(id); editor.setCurrentTool(VECTOR)`（**一个 VECTOR 工具承载 rect/ellipse/polygon/star**）；
- ★ **按钮高亮 = 当前工具属于“插入类”**：`inserting = tool === TEXT || tool === VECTOR || tool === PEN || ['line','arrow','note','frame'].includes(tool)`；
- ★ **上下文工具条**：`tool === COMMENT ? <CommentBar /> : <><DoodleBar /><NativeStyleBar /></>`（评论模式换成三种选择方式）。

### 77.4 ★★★ 全格式上传（一条 accept + tldraw 官方入口）

```tsx
<input ref={upload} type="file" multiple hidden
  accept="image/*,video/*,.pdf,.json,.lottie,.html,.htm,.glb,.gltf,.obj,.fbx,.stl,.ply,.mp3,.wav,.flac,.ogg,.m4a,.opus"
  onChange={(e) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length) void editor.putExternalContent({ type: 'files', files, point: editor.getViewportPageBounds().center })
    e.target.value = ''                       // ★ 允许重复选同一文件
  }} />
<input ref={layout} type="file" hidden accept=".json"
  onChange={(e) => { const f = e.target.files?.[0]; if (f) importLayout(editor, f).catch(err => onError(errorText(err))); e.target.value = '' }} />
```
- ★ **支持格式清单（照抄）**：图片、视频、`.pdf`、`.json`、`.lottie`、`.html/.htm`、`.glb/.gltf/.obj/.fbx/.stl/.ply`（3D）、`.mp3/.wav/.flac/.ogg/.m4a/.opus`（音频）；
- ★ **一律走 `editor.putExternalContent({ type:'files', files, point: 视口中心 })`** —— 由 tldraw 分发到 `onDrop`/`onPaste` 等自定义处理；
- ★ **`e.target.value = ''`** 让同一个文件能连续选两次；
- ★ **布局导入是另一个 input**（`.json` → `importLayout`，§17）。

### 77.5 ★ split button（主按钮 + 下拉箭头）

```tsx
<div className="ca-split">
  <button onClick={gen(video)}>{video ? Icons.genVideo : Icons.genImage} {video ? 'Video generation' : 'Image generation'}</button>
  <Popover open={menu} onOpenChange={setMenu} side="top" align="end"
           trigger={({ open, toggle }) => <button className="ca-bar__btn ca-split__chev" data-open={open || undefined} onClick={toggle}>}>
    {/* 图片生成 (A) / 视频生成 (S) / 生成音频 → audioDialogAtom.set(true) */}
  </Popover>
</div>
```
→ ★ **主按钮行为随内部 `video` state 变图标和文案**，箭头菜单选三种生成；★ **`Popover` 是 render-prop trigger + `side`/`align` 定位**（我们做菜单可参考这个接口）。

---

<a id="sec78"></a>
## 78. i18n 与主题（`src/i18n.ts` 38 行 / `src/theme.ts` 34 行）★★ 零依赖实现

### 78.1 i18n（无 i18n 库）

- `import en from './locales/en.json'` + `zh.json` → `const MESSAGES: Record<Lang, Record<string,string>> = { zh, en }`；`KEY = 'comfyart.lang'`；
- ★ **语言决策链**：
  ```ts
  const stored = storedLang()                                     // 1. localStorage（try/catch）
  if (stored) lang = stored
  else {
    const res = await fetch('/api/settings/Comfy.Locale').catch(() => null)   // 2. 后端设置
    const value = res?.ok ? await res.json().catch(() => null) : null
    lang = String(value || navigator.language).toLowerCase().startsWith('zh') ? 'zh' : 'en'   // 3. 浏览器语言
  }
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
  ```
- ★ **切换语言 = 写 localStorage + `location.reload()`**（最简单可靠，不做热切换）；
- ★★★ **`t()` 的三级回退 + 位置占位符**：
  ```ts
  export function t(key: string, ...args: (string|number)[]): string {
    const text = MESSAGES[lang][key] ?? MESSAGES.zh[key] ?? key          // 当前语言 → 中文 → key 本身
    return args.length ? text.replace(/\{(\d+)\}/g, (m, i) => (i < args.length ? String(args[i]) : m)) : text
  }
  ```
  → **占位符是 `{0}` `{1}`**（不是命名插值）；**回退到 key 本身**保证永不崩；
- ★ `export let lang`（模块级可变导出，因为语言只在 reload 前不变，不需要 atom）。

### 78.2 主题

- `type Theme = 'light'|'dark'|'system'`；`THEMES: [Theme, string][]`；`KEY = 'comfyart.theme'`；
- ★ **atom 初值直接读 localStorage**（IIFE）：`atom<Theme>('theme', (() => { try { const s = localStorage.getItem(KEY); return s === 'dark' || s === 'system' ? s : 'light' } catch { return 'light' } })())`；
- ★ **`isDark(theme) = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)`**；
- ★ **双写：自己 + tldraw**：
  ```ts
  export function saveTheme(theme) { themeAtom.set(theme); try { localStorage.setItem(KEY, theme) } catch {}; markDark(isDark(theme)) }
  export function setTheme(editor, theme) { saveTheme(theme); editor.user.updateUserPreferences({ colorScheme: theme }) }   // ★ tldraw 自身 UI 也换色
  export function markDark(dark) { document.documentElement.dataset.theme = dark ? 'dark' : 'light' }
  markDark(isDark(themeAtom.get()))      // ★ 模块加载即执行 → 防首屏闪白
  ```
- ★ **所有 `localStorage` 访问都包 `try/catch`**（隐私模式/禁用存储不炸）。

<a id="sec79"></a>
## 79. 钢笔工具（`src/shapes/PenTool.ts` 76 行）★★★ 完整 StateNode 实现

- `PEN = 'ca-pen'`；`CLOSE_PX = 10`；`OPEN_FILL = '#00000000'`；`CLOSED_FILL = '#CCCCCC'`；
- ★ **工具私有状态**：`private nodes: PenNode[]`、`private shape: TLShapeId | null`、`private dragging = false`；`onEnter` 重置 + cross 光标；**`onExit() { this.commit(false) }`**（离开工具也收尾）；
- ★ **闭合命中判定用屏幕像素**：
  ```ts
  const first = this.nodes[0]
  if (first && this.nodes.length > 2 && Math.hypot(p.x - first.x, p.y - first.y) * this.editor.getZoomLevel() < CLOSE_PX)
    return this.finish(true)
  ```
  → ★ **`* getZoomLevel()` 把页坐标距离换算成屏幕距离** → 无论缩放，闭合吸附都是 10 屏幕像素；
- ★ **拖拽出贝塞尔句柄（相对向量）**：`onPointerMove` → `Object.assign(last, { hx: p.x - last.x, hy: p.y - last.y })`（与 §70.6 PathEditor 同一约定）；
- ★★★ **预览就是那个真 shape（不建 overlay）**：
  ```ts
  private sync(closed: boolean) {
    const { x, y, w, h, d } = nodesToPath(this.nodes, closed)          // §12 vectorPath
    const props = { w, h, d, ...(closed ? { fill: CLOSED_FILL } : {}) }
    if (this.shape) return this.editor.updateShape<VectorShape>({ id: this.shape, type: VECTOR, x, y, props })
    this.shape = createShapeId()
    this.editor.createShape<VectorShape>({ id: this.shape, type: VECTOR, x, y,
      props: { ...props, kind: 'path', fill: OPEN_FILL, stroke: '#333333', strokeWidth: 2 } })
  }
  ```
  → ★ **未闭合时 `fill` 透明、闭合才填色**；★ **shape 复用（第一次 create，之后 update）**；
- ★ **`commit` 自动清理草稿**：`const kept = !!id && this.nodes.length >= 2; if (kept) this.sync(closed); else if (id) this.editor.deleteShape(id)`；
- ★ **`finish`**：`commit` → `setCurrentTool('select')` → `if (id) this.editor.select(id)`；
- ★ **三种结束方式**：`onDoubleClick`（**先 `this.nodes.pop()` 去掉双击产生的重复点**）、`onKeyDown(info) { if (info.key === 'Enter') this.finish(false) }`（★ 工具内键盘处理，不污染全局）、`onCancel`。

---

<a id="sec80"></a>
## 80. 画板工具条（`src/ui/FrameToolbar.tsx` 257 行）★★★ 全仓库最值钱的一块：比例锁 + 自动布局 + 批量导出

### 80.1 常量与 frame 的 meta 约定

- `GAP = 22`、`EDGE = 8`、`PAD = 24`、`GEN_LONG = 1536`；
- ★ **`SIZE_PRESETS`（9 个，直接抄）**：`Instagram 1080×1080`、`Instagram 竖版 1080×1350`、`Story 1080×1920`、`YouTube 封面 1280×720`、`演示 1920×1080`、`桌面网页 1440×1024`、`移动端 390×844`、`A4 2480×3508`、`A4 横版 3508×2480`；
- ★ **frame 的 `meta` 键约定**（★ 我们给 frame 加能力时照这套）：
  | meta 键 | 类型 | 含义 |
  |---|---|---|
  | `ratio` | `number \| null` | 纵横比锁（null = 未锁） |
  | `wMode` / `hMode` | `'fixed' \| 'hug'` | 该轴固定 or 贴合内容 |
  | `layout` | `'auto' \| 'manual'` | 自动网格布局 |
  | `style` | `{fill, stroke, radius}` | 画板外观（`artboardStyle(frame)` 读） |
  | `chatId` | `string` | 关联的 Agent 会话（出现「定位聊天」按钮） |
  | `refFrame` | `true` | 参考画板（出现「链接」按钮） |
  | `elements` | — | 排版板元素（出现「重新渲染」） |
  ```ts
  type Sizing = 'fixed' | 'hug'
  const sizingOf = (frame: TLFrameShape, axis: 'w'|'h') => ((frame.meta[axis === 'w' ? 'wMode' : 'hMode'] as Sizing | undefined) ?? 'fixed')
  const isAuto = (frame: TLFrameShape) => frame.meta.layout === 'auto'
  ```

### 80.2 ★★★ 纵横比锁定：`registerBeforeChangeHandler` 改写 props

```ts
editor.sideEffects.registerBeforeChangeHandler('shape', (prev, next) => {
  const ratio = next.meta.ratio as number | null | undefined
  if (next.type !== 'frame' || !ratio || prev.type !== 'frame') return next
  const [p, n] = [(prev as TLFrameShape).props, (next as TLFrameShape).props]
  if (p.w === n.w && p.h === n.h) return next
  const byWidth = Math.abs(n.w / p.w - 1) >= Math.abs(n.h / p.h - 1)     // ★ 哪个轴改得多就以谁为基准
  return { ...next, props: { ...n, ...(byWidth ? { h: n.w / ratio } : { w: n.h * ratio }) } } as typeof next
})
```
→ **不依赖 tldraw 的 resize handle，任何来源（输入框、预设、拖拽）改尺寸都会被纠正**；
- 锁按钮语义：`ratio ? null : frame.props.w / frame.props.h`（**点一下把当前比例存进 meta**）。

### 80.3 ★★★ 微任务批量重排（防抖 + 防重入 + 不进撤销栈）

```ts
export function registerFrameRules(editor: Editor) {
  let busy = false
  const pending = new Set<TLShapeId>()
  const flush = () => {
    busy = true
    editor.run(() => {
      for (const id of pending) { const frame = editor.getShape<TLFrameShape>(id); if (frame) relayout(editor, frame) }
    }, { history: 'ignore' })                       // ★ 自动布局不产生 undo 记录
    pending.clear(); busy = false
  }
  const touch = (shape: TLShape) => {
    if (busy) return                                // ★ 防重入（relayout 自己会改 shape）
    const parent = editor.getShape(shape.parentId as TLShapeId)
    if (parent?.type !== 'frame') return
    const frame = parent as TLFrameShape
    if (!isAuto(frame) && sizingOf(frame,'w') === 'fixed' && sizingOf(frame,'h') === 'fixed') return   // ★ 不需要重排直接跳过
    if (!pending.size) queueMicrotask(flush)         // ★ 同一微任务内多次改动只重排一次
    pending.add(frame.id)
  }
  const stops = [
    editor.sideEffects.registerBeforeChangeHandler('shape', /* 80.2 比例锁 */),
    editor.sideEffects.registerAfterChangeHandler('shape', (prev, next) => { touch(next); if (prev.parentId !== next.parentId) touch(prev) }),
    editor.sideEffects.registerAfterCreateHandler('shape', touch),
    editor.sideEffects.registerAfterDeleteHandler('shape', touch),
  ]
  return () => stops.forEach((stop) => stop())       // ★ 每个钩子返回 stop 函数，统一清理
}
```

### 80.4 ★★ 自动网格布局 + 贴合内容

```ts
function autoLayout(editor, frame) {
  const items = childBoxes(editor, frame).sort((a, b) => a.b.y - b.b.y || a.b.x - b.b.x)   // 阅读顺序
  const fixedW = sizingOf(frame, 'w') === 'fixed'
  const cols = Math.ceil(Math.sqrt(items.length))                                          // ★ 列数 = √n 上取整
  const rows: typeof items[] = []
  for (const item of items) {
    const row = rows.at(-1)
    const used = row ? row.reduce((n, r) => n + r.b.w + PAD, PAD) : PAD
    if (row && (fixedW ? used + item.b.w + PAD <= frame.props.w : row.length < cols)) row.push(item)   // ★ 固定宽→按宽度装；否则按列数
    else rows.push([item])
  }
  let y = PAD, width = 0
  for (const row of rows) { let x = PAD; for (const { s, b } of row) { moveTo(editor, s, b, x, y); x += b.w + PAD }
    width = Math.max(width, x); y += Math.max(...row.map(({ b }) => b.h)) + PAD }
  editor.updateShape<TLFrameShape>({ id: frame.id, type: 'frame',
    props: { ...(fixedW ? {} : { w: width }), ...(sizingOf(frame,'h') === 'hug' || !fixedW ? { h: y } : {}) } })
}
function moveTo(editor, s, b, x, y) {                       // ★ 页坐标目标 → 父空间 delta
  const local = editor.getPointInParentSpace(s.id, { x: b.x, y: b.y })
  editor.updateShape({ id: s.id, type: s.type, x: s.x + x - local.x, y: s.y + y - local.y })
}
function hugContent(editor, frame) {
  const items = childBoxes(editor, frame); const origin = editor.getShapePageBounds(frame.id)!
  const w = Math.max(...items.map(({ b }) => b.maxX - origin.x)) + PAD
  const h = Math.max(...items.map(({ b }) => b.maxY - origin.y)) + PAD
  editor.updateShape<TLFrameShape>({ id: frame.id, type: 'frame',
    props: { ...(sizingOf(frame,'w') === 'hug' ? { w } : {}), ...(sizingOf(frame,'h') === 'hug' ? { h } : {}) } })
}
function relayout(editor, frame) {
  if (isAuto(frame)) autoLayout(editor, frame)
  else if (sizingOf(frame,'w') === 'hug' || sizingOf(frame,'h') === 'hug') hugContent(editor, frame)
}
```

### 80.5 ★★ 在画板内生成（分辨率自动匹配）

```ts
function generateInto(editor, frame) {
  const { w, h } = frame.props
  const k = GEN_LONG / Math.max(w, h)                                   // 长边归一到 1536
  const id = createGenerator(editor, false, { resolution: 'custom', cw: Math.round(w * k), ch: Math.round(h * k) }, false)
  editor.reparentShapes([id], frame.id)
  editor.updateShape<GeneratorShape>({ id, type: 'generator', x: 0, y: 0, props: { w, h } })   // ★ 铺满画板
  editor.select(id)
}
```

### 80.6 ★★ 批量下载 + ZIP（`fflate`）

```ts
async function childFiles(editor, frame) {           // 只取 frame 里的媒体 shape
  const files = []
  for (const id of editor.getSortedChildIdsForParent(frame.id)) {
    const asset = mediaAsset(editor, editor.getShape(id))
    if (asset) files.push({ name: asset.props.name || 'download', blob: await (await fetch(asset.props.src)).blob() })
  }
  return files
}
async function downloadZip(editor, frame) {
  const used = new Set<string>(); const entries: Record<string, Uint8Array> = {}
  for (const f of await childFiles(editor, frame)) {
    const ext = f.blob.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png'          // ★ MIME → 扩展名
    let name = `${f.name.replace(/[\/:*?"<>|]/g, '_')}.${ext}`
    for (let i = 2; used.has(name); i++) name = `${f.name}-${i}.${ext}`             // ★ 重名自动 -2 -3
    used.add(name); entries[name] = new Uint8Array(await f.blob.arrayBuffer())
  }
  download(new Blob([zipSync(entries, { level: 0 }) as BlobPart]), `${frame.props.name || '画板'}.zip`)   // ★ level 0 不压缩（媒体已压缩）
}
```

### 80.7 其它可直接抄的细节

- ★ **`framesOnPage`（幻灯片顺序 = 阅读顺序）**：`editor.getCurrentPageShapes().filter(s => s.type === 'frame' && s.parentId === editor.getCurrentPageId()).sort((a,b) => a.y - b.y || a.x - b.x)`；播放：`slideshowAtom.set({ ids: frames, index: Math.max(0, frames.indexOf(frame.id)) })`；
- ★ **工具条宽度自测**（居中必需）：`useLayoutEffect(() => { if (bar.current && bar.current.offsetWidth !== width) setWidth(bar.current.offsetWidth) })` → `left = Math.min(Math.max((rect.l + rect.r)/2 - width/2, EDGE), host.width - width - EDGE)`；`top = rect.t - GAP - 44; if (top < EDGE) top = rect.b + GAP`；
- ★ **W/H 输入框在 hug 模式禁用**：`disabled={sizingOf(frame,'w') === 'hug'}`；★ **最小尺寸钳位 `v >= 16`**：`const size = (key, v) => v >= 16 && editor.updateShape(...)`；
- ★ **预设应用时保留锁语义**：`meta: { ...frame.meta, ratio: ratio ? w / h : null }`；
- ★ **样式 section 的图标就是实时预览**：`icon={<span className="ca-tools__dot" style={{ background: style.fill || '#FFFFFF', borderRadius: Math.min(style.radius ?? 0, 6) }} />}`；
- ★ **工具条调用自定义 action 的同一入口**：`actions['fit-frame-to-content']?.onSelect('toolbar')`（**快捷键 / 菜单 / 工具条共用一个 action 定义**）；
- ★ **条件按钮全部由 meta 驱动**：`typeof frame.meta.chatId === 'string'` → 「定位聊天」（`chatViewAtom.set('open'); openChat(meta.chatId)`）；`frame.meta.refFrame === true` → 「链接参考」；`isBoard(frame)` → 「渲染 / 重新渲染」+ `ComposePanel`；
- ★ **画板名是内联 input**：`value={frame.props.name}` + `onKeyDown={(e) => e.stopPropagation()}`。

> ⚠️ 本节代码片段均来自 `src/ui/FrameToolbar.tsx` 逐行阅读；`artboardStyle` / `isBoard` / `renderBoard` 本身在 §16 / §17。

---

<a id="sec81"></a>
## 81. ⚠️ 作废：CLI 驱动实现（`bot/codex.py` + `bot/claude_code.py`）——**已逐行重读并在 §83 重写**

> 本节早期版本里的 `AskBridge`、`ask_user_bridge`、`--permission-mode bypassPermissions`、`--output-last-message`、
> `tool_timeout_sec=3600`、`default_tools_approval_mode`、`model_reasoning_effort`、`command_execution`、`output_item`、
> `startup_timeout_sec`、`wait_for_exit`、`usage_from_result` 等标识符**均不存在于仓库**（已 grep 确认零命中）。
> ★ **请直接读 §83**（真实事实）+ §87（真正的 Agent 反问/审批机制）。下面保留的只有已被 §83 覆盖的少量正确结论。

### 81.1 – 81.6 全部内容已删除（不可信）

本节早期版本把 Codex / Claude Code 的命令行、事件映射、`ProviderCaps`、`abort()` 等写得像逐行摘录，
但经标识符核查（`verify_ids.py`）与逐行重读，下列名字**在仓库中零命中**，属于虚构：
`--permission-mode`、`bypassPermissions`、`CLAUDE_*_BYPASS_PERMISSIONS_PROMPT`、`--output-last-message`、
`tempfile.mktemp`、`tool_timeout_sec=3600`、`default_tools_approval_mode`、`model_reasoning_effort`、
`command_execution`、`output_item`、`output_text`、`reasoning_output_tokens`、`startup_timeout_sec`、
`wait_for_exit`、`usage_from_result`、`callId`、`user_message_json`、`send_user_text`、`AskBridge`、`ask_user_bridge`、
`resume_via_stdin`、`images_via_tempfile`、`supports_ask_user`、`mcp_servers.comfyart.allowed_tools`。

★ **请直接读 §88**（逐行重读 `bot/claude_code.py` 200 行 + `bot/codex.py` 399 行后的真实事实），要点：
- 真实的 `ProviderCaps` 只有 `stateful / tools / attachments` 三个字段（`bot/providers.py:15-19`）；
- Claude：`-p --input-format stream-json`（附件走 stdin）+ `--output-format stream-json --include-partial-messages --verbose --strict-mcp-config` + `--mcp-config` + `--allowedTools` + `--disallowedTools`（14 个内置工具）+ `--resume`（§88.1）；
- Codex：`exec --approve-for-me [resume] --json --skip-git-repo-check -c mcp_servers.comfyart.url/required/tool_timeout_sec=600 -c features.shell_tool=false -c web_search="disabled" -c approvals_reviewer="auto_review" -i <附件临时文件> --`（§88.2）；
- 权限收紧靠「关掉内置工具 / 关掉别人的 MCP server」，不是 permission-mode（§88.1 / §88.2）；
- Codex 事件是 `thread.started / turn.completed / turn.failed / error / item.started|updated|completed`，文本靠 `_text_emitted[item_id]` 前缀差分（§88.2）。

<a id="sec82"></a>
## 82. `bot/qwen_code.py`（251 行）★ 已核实：禁用自带工具 + Windows npm 解析链

### 82.1 ★★★ `_EXCLUDED_CORE_TOOLS`：把 CLI 自带能力全关掉，只留 MCP

```python
_EXCLUDED_CORE_TOOLS = [
    "run_shell_command", "write_file", "replace", "edit", "read_file", "read_many_files",
    "list_directory", "glob", "search_file_content", "grep",
    "web_fetch", "google_web_search", "web_search", "save_memory", "write_todos",
]
```
→ ★ **这是 §62.2 “别用 CLI 自带工具”提示词的硬执行版**：Codex/Claude 靠 `--disallowed-tools`，Qwen 靠 `excludeTools` 配置。
→ **我们接第三方 Agent 时必须照做**：否则它会去读本地文件而不是调我们的画布工具。

### 82.2 MCP 注入（`.qwen/settings.json`）

```python
servers["comfyart"] = {"httpUrl": mcp_endpoint, "trust": True,
                       "timeout": MCP_TOOL_TIMEOUT_MS, "includeTools": list(CORE_MCP_TOOLS)}
data["allowMCPServers"] = ["comfyart"] (+ "comfy")
data["excludeTools"] = list(_EXCLUDED_CORE_TOOLS)
data.setdefault("contentGenerator", {})["timeout"] = 180_000   # 只 setdefault，不覆盖用户已有配置
(Path(home) / "QWEN.md").write_text(AGENT_GUIDE + "\n")
```
→ ★ **读-改-写用户配置**（`json.loads` 失败就 `data = {}`），**只覆盖自己那几个键**；`includeTools` 白名单在 MCP server 级别。

### 82.3 ★ Windows 下 npm CLI 的完整解析链（`resolve_qwen_command`）

`shutil.which("qwen.exe")` → `shutil.which("qwen")`（若是 `.exe` 或非 Windows 直接用）→ **否则读 `node_modules/@qwen-code/qwen-code/package.json` 的 `bin.qwen`（失败 fallback `"dist/index.js"`）→ `[node, cli.js]`** → `qwen.cmd` → `["cmd.exe","/d","/s","/c",cmd]` → `~/.nvm/versions/node/v*/bin/qwen` → `/usr/local/bin` / `~/.npm-global/bin`。
`spawn_env()` 还会把 `~/.nvm/versions/node/*/bin`（排序后最新）插到 PATH 前面。
→ ★ **我们是 Windows 应用，这段解析链是现成答案**（npm 全局 shim 是 `.cmd` 时不能直接 spawn）。

### 82.4 argv 与探测

- `["-p", user_text, "--output-format", "stream-json", "-y"]` + `["-m", model]` + `["--resume", resume_token]`；
- `probe()`：`--version`（15 秒超时）；`logged_in = True if Path.home()/".qwen" exists else None`（**用配置目录存在与否推断登录状态**）；
- `list_models()` 读 `~/.qwen/settings.json` 的 `modelProviders`（dict of lists，取 `id` 去重）→ **从 CLI 自己的配置里读模型列表**。

---

<a id="sec83"></a>
## 83. `bot/local_llm.py`（425 行）★★★ 已核实：一个完整的自研 Agent 循环（对我们最有价值）

> 这个文件**不依赖任何第三方 Agent CLI**，自己用 OpenAI 兼容 HTTP + MCP HTTP 实现了完整工具调用循环。**我们的 Agent 节点可以直接参考这份实现。**

### 83.1 常量

```python
_API_KEY_ENV = "COMFYART_LOCAL_LLM_API_KEY"
_MAX_ITERATIONS = 80          # ★ 工具调用轮数上限
_MAX_WAIT_SLICES = 12         # ★ wait_generation 最多连续等 12 片
_LLM_TIMEOUT_S = 300
_HISTORY_CHAR_CAP = 16_000    # ★ 历史按字符数截断
_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>")
_SYSTEM_PROMPT = AGENT_GUIDE  # ★ 复用 §62.2 同一份提示词
capabilities: stateful=False, tools="mcp", attachments=True
```

### 83.2 ★★★ `_wait_stage_done`：等生成时卸载本地模型腾显存，等完再装回来

```python
async def _wait_stage_done(self, session, endpoint, args, model, handle) -> str:
    args = dict(args); args.setdefault("timeout_s", 170)
    unloaded = bool(self._lms_bin()) and await self._unload_model()      # lms unload --all
    try:
        raw = ""
        for _ in range(_MAX_WAIT_SLICES):
            if handle.stop_requested: break
            raw, _images = await self._mcp_call_tool(session, endpoint, "wait_generation", args)
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("status") != "running": break
        return raw
    finally:
        if unloaded: await self._reload_model(model)                     # lms load <m> --identifier <m> --gpu max --yes
```
→ ★ **本地 LLM 与生图模型抢显存**是本地部署的真问题；它的解法是**等待期间主动 unload、`finally` 里 reload**。
→ `_lms_bin()` 找 LM Studio 的 `lms`：`LMSTUDIO_LMS_PATH` → `~/.lmstudio/bin/lms` → macOS `LMS.app/.../lms` → `which lms`。

### 83.3 ★ MCP 走 HTTP（不装 MCP SDK）

```python
_mcp_headers() = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
_mcp_list_tools: POST {"jsonrpc":"2.0","id":"tools","method":"tools/list","params":{}}
                 → [t for t in result.tools if t["name"] in CORE_MCP_TOOLS]      # ★ 自己过滤白名单
_mcp_call_tool:  POST {"method":"tools/call","params":{"name":name,"arguments":args}}
                 timeout = 560 if name == "ask_user" else 200                    # ★ 问人类要更长超时
                 → (text, images[])；images 从 content 里 type=="image" 的块拼成 data URL
                 → result.isError 时 text 前缀 "[tool error] "
_build_tools:    MCP inputSchema → OpenAI {"type":"function","function":{name,description,parameters}}
```
→ ★ **`ask_user` 超时 560 秒**（§54.3 的 `ASK_TIMEOUT_S=540` 留 20 秒余量）——**超时链要对齐**。

### 83.4 ★ 主循环（`send`）

```python
messages = [system(AGENT_GUIDE) if tools else None, *history, user(_user_content(text, attachments))]
for _ in range(_MAX_ITERATIONS):
    if handle.stop_requested: return TurnResult(aborted=True, usage=usage)
    data = POST /chat/completions {"model","messages", "tools","tool_choice":"auto", "temperature": 0.2}
    usage = merge_usage(usage, data["usage"])
    reasoning = message.reasoning_content or message.reasoning or <think>捕获   # ★ 三种思考格式都兼容
    content   = _THINK_RE.sub("", content).strip()                              # ★ 从正文里剔掉 think
    emit(thinking) / emit(delta)
    if not tool_calls: return TurnResult(usage=usage)
    messages.append({"role":"assistant","content":content or None,"tool_calls":tool_calls})
    for tc in tool_calls:
        args = json.loads(fn.arguments) if isinstance(str) else fn.arguments    # ★ 参数解析失败 → {}
        emit(tool_use)
        result = _wait_stage_done(...) if name == "wait_generation" else _mcp_call_tool(...)
        if len(result) > TOOL_RESULT_CAP: 截断 + "…[truncated N chars]"
        emit(tool_result, is_error=tool_failed)
        messages.append({"role":"tool","tool_call_id": tc_id or name, "content": result})
        if images:   # ★★★ 工具产出的图作为一条 user 消息回灌
            messages.append({"role":"user","content":[{"type":"text","text":f"Images returned by {name}:"}, *image_url_blocks]})
return TurnResult(error="too many tool-call iterations", usage=usage)
```
→ ★★★ **最后那步“把工具返回的图片以 user 消息回灌”是让多模态 Agent 能看见自己生成结果的关键**；没有它，模型只能看到 JSON。
→ ★ **`temperature: 0.2`**（工具调用要稳）。
→ ★ **历史按字符截断**（`_HISTORY_CHAR_CAP=16000`，从最新往回累加，超了就停）。
→ ★ **`stop()`**：`handle.stop_requested = True` + `await session.close()`（**`handle.process` 字段被复用存 aiohttp session**）。
→ ★ **异常兜底**：`except Exception` → 若 `stop_requested` 返 `aborted`，否则 `TurnResult(error=str(e) or type(e).__name__)`；`finally: handle.process = None`。

### 83.5 探测与错误文案

- `probe()`：`GET {base}/models`（10 秒）；两条精确文案：`"no endpoint configured — set the Local LLM endpoint URL under Agent Settings"`、`"the endpoint is up but reports no models — load a model first"`；
- `_request_json` 失败 → `RuntimeError(f"HTTP {resp.status}: {text[:300]}")`；
- `_NET_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError, ValueError)` —— **一个元组统一兜住**。

---

<a id="sec84"></a>
## 84. `bot/_acp.py`（225 行）★★★ 已核实：自研 ACP（JSON-RPC over stdio）客户端

> 这是给 **ACP（Agent Client Protocol）** 类 provider（§54.1 的 `opencode` / `gemini-cli`）用的传输层。**我们自己要接 ACP Agent 时，这份实现可以直接参考。**

### 84.1 常量与错误码

```python
STREAM_LIMIT = 10 * 1024 * 1024          # ★ asyncio stream limit，防 readline 撞默认 64KB
STDERR_CAP = 64 * 1024
DEFAULT_REQUEST_TIMEOUT_S = 60.0
_POLL_S = 1.0
CODE_LOCAL_TIMEOUT = -32001              # ★ 本地自定义 JSON-RPC 错误码
CODE_PROTOCOL = -32603
CODE_METHOD_NOT_FOUND = -32601
class AcpError(RuntimeError): code, message, data
class AcpProcessError(RuntimeError): pass
```

### 84.2 ★ 进程组与 stderr

```python
if sys.platform == "win32": kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
else:                       kwargs["start_new_session"] = True
kwargs["limit"] = STREAM_LIMIT
```
`_drain_stderr()` 每 64KB 读一次，`_note_stderr` 只保留前 `STDERR_CAP`；`error_detail()` = `closed_reason | stderr[-800:]`（**报错时给尾部 stderr**）。

### 84.3 ★★ 读循环对脏输出的容错（接第三方 CLI 必备）

```python
try: line = await self.proc.stdout.readline()
except ValueError:                       # ★ 行超过 limit
    _log.warning("[ComfyArt/acp] oversized stdout line skipped"); continue
...
try: msg = json.loads(text)
except ValueError: msg = None
if isinstance(msg, dict): await self._dispatch(msg)
else: self._note_stderr(f"[non-protocol stdout] {text[:400]}\n".encode())   # ★ 非协议行不崩，转存 stderr
```
→ ★ **Agent 往 stdout 打日志是常态**；这里**跳过非 JSON 行并留证**，而不是断开连接。

### 84.4 ★★ 双超时（与 §62.1 同思路，但实现不同）

```python
async def request(self, method, params, *, timeout=60.0, idle_timeout=None):
    fut = loop.create_future(); self._pending[rid] = fut
    await self._send({...})
    started = loop.time()
    while not fut.done():
        now = loop.time()
        if timeout is not None and now - started > timeout:
            raise AcpError(CODE_LOCAL_TIMEOUT, f"{method} timed out after {int(timeout)}s")
        if idle_timeout is not None and now - self.last_activity > idle_timeout:
            raise AcpError(CODE_LOCAL_TIMEOUT,
                           f"{method} timed out: no activity from the agent for {int(idle_timeout)}s")
        await asyncio.wait({fut}, timeout=_POLL_S)      # ★ 1 秒轮询而非纯 await
    return fut.result()
```
→ ★ **`last_activity` 在每次读到 stdout 行时更新** → 空闲超时判据是“Agent 有没有输出”，不是“有没有回包”。
→ ★ **`finally: self._pending.pop(rid, None)`** —— 超时后一定清掉，避免泄漏 future。

### 84.5 ★ 双向：它既能发请求也能应答请求

`_dispatch`：有 `id` 且有 `result`/`error` → 完成 pending future；有 `method` 且有 `id` → **`_answer()` 应答对端请求**（`AcpError` → `{"error":{code,message}}`）；只有 `method` → `_on_notification`（**handler 异常只 log 不抛**）。
`_send` 用 `asyncio.Lock` 串行写 + `ensure_ascii=False`；进程已退 → `AcpProcessError(self.closed_reason or "ACP process is not running")`。
`_fail_pending(reason)` 在 `_read_loop` 的 `finally` 里把所有 pending future 置异常 → **所有等待方立刻失败而不是挂死**。
`close(timeout)` = 关 stdin + `wait_exit`；`dispose()` = cancel 读任务 + gather + `_fail_pending`。

### 84.6 ★ 对我们的直接可用点

1. **`limit=STREAM_LIMIT` + `except ValueError: continue`** —— 我们 `pi-client.ts` 若按行读 JSON，必须处理超长行；
2. **非协议行转存 stderr 而不是断开** —— 我们接 CLI 时最容易踩的坑；
3. **`last_activity` 空闲超时** —— 与我们 `pi-client.ts` 的 `IDLE_TIMEOUT_MS = 180_000`（§53）同思路，可对照校准；
4. **`_fail_pending` 统一失败** —— 避免前端卡在“正在思考”。

---

<a id="sec85"></a>
## 85. `bot/deepseek_harness.py`（318 行）★★★ 已核实：ACP provider 的完整生命周期（最完整的一份）

> 这是 §84 传输层的**唯一使用者**，展示了“接一个协议型 Agent”的全套流程。**我们接 ACP/协议型 Agent 时应把这份当模板。**

### 85.1 常量

```python
ACCOUNT_ROUTE = "deepseek-account"
ACP_PROTOCOL_VERSION = 1
MCP_SERVER_NAME = "comfyart"
IMAGE_MIME_TYPES = frozenset({"image/png","image/jpeg","image/jpg","image/webp","image/gif"})
VERSION_TIMEOUT_S = 15.0; SESSION_TIMEOUT_S = 120.0; CLOSE_TIMEOUT_S = 30.0; CANCEL_GRACE_S = 8.0
_STOP_REASON_ERRORS = {
  "max_tokens": "DeepSeek Harness stopped at the output token limit for this turn.",
  "refusal": "DeepSeek Harness declined to continue this turn.",
  "max_turn_requests": "DeepSeek Harness stopped after the maximum number of model requests."}
_USAGE_KEYS = {"inputTokens":"input_tokens","outputTokens":"output_tokens",
               "cachedReadTokens":"cache_read_input_tokens","cachedWriteTokens":"cache_creation_input_tokens"}
```
→ ★ **把 provider 的 stopReason 映射成人话**（而不是把 `max_tokens` 直接甩给用户）。

### 85.2 ★★ 模型目录从协议里动态取（不是硬编码）

```python
def model_options_from(config_options):     # 找 id=="model" 的 option，展开 group/options 两层
    rows.append({"value": ..., "label": ..., "group": str(item.get("group") or "")})
```
- `config_option_update` 通知里**刷新目录**（`self._catalog = model_options_from(...) or self._catalog`）；
- `_target_model(saved)`：保存的值不在目录里 → `"The saved DeepSeek Harness model is no longer offered. Pick a model again."`；没保存 → 优先 `group == ACCOUNT_ROUTE`，否则 `"No DeepSeek account model is available. Sign in to the DeepSeek Harness app, or pick an API-key model in the model menu."`；
- 模型值本身是 **JSON 数组字符串 `["provider","model"]`**（`parse_model_value`）。
→ ★ **模型列表由 Agent 自己上报 + 分组 + 失效检测**，比硬编码列表健壮得多。

### 85.3 ★★ ACP 握手序列（`_run_turn`）

```python
await transport.start(); handle.process = transport.proc
init = await transport.request("initialize", {
    "protocolVersion": 1,
    "clientInfo": {"name": "comfyart", "version": "1.0"},
    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}, timeout=120)
image_supported = bool(init.agentCapabilities.promptCapabilities.image)     # ★ 能力协商
mcp_servers = [{"type":"http","name":"comfyart","url":turn.mcp_endpoint,"headers":[]}]
opened = (await transport.request("session/resume", {"sessionId": resume_token, "cwd": cwd, "mcpServers": ...})
          if resume_token else await transport.request("session/new", {"cwd": cwd, "mcpServers": ...}))
session_id = opened.sessionId; live[1] = session_id
self._catalog = model_options_from(opened.configOptions) or self._catalog
await emit(BotEvent(t="session", id=session_id))                            # ★ 拿到 session 立刻上报（§64.3）
await transport.request("session/set_config_option", {"sessionId":..., "configId":"model", "value": model})
response = await transport.request("session/prompt", {"sessionId":..., "prompt": blocks},
                                   timeout=TURN_MAX_S, idle_timeout=TURN_IDLE_TIMEOUT_S)   # ★ 双超时
```
→ ★ **`clientCapabilities.fs = False`** —— **明确拒绝 Agent 读写我们文件系统**（与 §82.1 同一立场）。
→ ★ **`promptCapabilities.image` 协商** → 决定能不能发图。

### 85.4 ★ 附件校验（发之前就拒）

`_prompt_blocks(turn, image_supported)` 返回 `(blocks, error)`：
- 非图片 → `"DeepSeek Harness only accepts image attachments; this one is {mime or 'of an unknown type'}."`
- 模型不支持图 → `"The selected DeepSeek Harness model does not accept images. Remove the attachment or pick another model."`
- `image/jpg` 归一为 `image/jpeg`；最后追加 `{"type":"text","text":user_text}`。
→ ★ **错误在发 prompt 之前返回，且文案给出下一步**。

### 85.5 ★★ `session/request_permission`：自动拒绝

```python
async def _on_request(method, params):
    if method != "session/request_permission": raise AcpError(-32601, f"unsupported client method: {method}")
    for option in params.get("options") or []:
        if option.get("kind") == "reject_once":
            return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}
```
→ ★ **宿主不弹权限框，直接选 `reject_once`**；其它方法一律 `-32601`。

### 85.6 ★ ACP `session/update` → BotEvent 映射

| `sessionUpdate` | 映射 |
|---|---|
| `agent_message_chunk` | `delta` |
| `agent_thought_chunk` | `thinking` |
| `config_option_update` | 刷新模型目录 |
| `tool_call` | `tool_use`（`names[call_id] = title`，`input = rawInput if dict else {}`） |
| `tool_call_update` 且 status ∈ {completed, failed} | `tool_result`（`is_error = status=="failed"`，文本截 `TOOL_RESULT_CAP`） |
→ ★ **`names: dict[call_id, title]` 用来把 update 关联回工具名**（同 §62.5 的 `tool_names`）。
→ `_tool_result_text` 处理嵌套 `{"type":"content","content":{...}}`，非文本块写成 `[{type}]`。

### 85.7 ★★★ 收尾与取消（`_teardown` / `stop`）

```python
async def _teardown(handle, transport, session_id):
    self._live.pop(handle, None)
    if session_id and transport.returncode is None:
        try: await transport.request("session/close", {"sessionId": session_id}, timeout=30)
        except (AcpError, AcpProcessError) as e: _log.warning(...)
    await transport.close(30)                       # 关 stdin 等退出
    if transport.returncode is None: await kill_process_tree(handle)   # ★ 还没死就杀进程树
    await transport.dispose()

async def stop(handle):
    handle.stop_requested = True
    live = self._live.get(handle)
    if live and live[1]:
        try: await live[0].notify("session/cancel", {"sessionId": live[1]})
        except AcpProcessError: pass
        if await live[0].wait_exit(CANCEL_GRACE_S): return            # ★ 给 8 秒优雅退出
    await kill_process_tree(handle)
```
→ ★★★ **“先协议级 cancel → 等 8 秒 → 再杀进程树”三段式**；`_teardown` 里同样“先 session/close → 关 stdin → 杀树 → dispose”。**这是我们停子进程应该照搬的顺序**（我们 `pi-client.ts` 目前只有 `stop()`）。
→ ★ **`send()` 的 except 分支也带 `resume_token=live[1] or None`** —— 协议异常也要保住会话（§62.1 原则的贯彻）。

### 85.8 其它

- `supports_branch = False`（类属性，§63.1）；
- `cwd = chat_cwd(home_dir, turn.chat_id)` → **每个会话一个工作目录**；`write_turn_overlay(cwd, parse_model_value(turn.model))` → **每轮写一个 overlay 文件**，argv 加 `["--profile", BOT_PROFILE, "--patch", str(overlay)]`；
- `probe()` 缓存 key 含 `argv`（命令变了立刻重探）；失败文案 `dsh --version exited with {rc}: {stderr[-200:]}`。

---

<a id="sec86"></a>
## 86. `bot/deepseek_harness_runtime.py`（97 行）★★ 已核实：给第三方 Agent 造一个“只准用画布工具”的 profile

### 86.1 ★★★ `DISABLED_ENTRY_IDS`：关掉 Agent 的一切自带能力（28 项）

```python
PERMISSION_PRESET = "comfyart-mcp-only"
DISABLED_ENTRY_IDS = (
    "tool-bash", "tool-pwsh", "tool-jobs", "tool-fs", "tool-fs-search",
    "tool-skill", "skill", "skill-filesystem", "skill-badge",
    "tool-subagent", "tool-subagent-fork", "tool-subagent-control",
    "tool-subagent-list-agents", "subagent", "subagent-spawn-in-process", "subagent-fork-in-process",
    "tool-workflow", "workflow-ptc", "ptc-runtime",
    "tool-todo", "tool-goal", "tool-ralph",
    "tool-web", "web-search-deepseek",
    "tool-plugin-manager", "plugin-manager",
    "user-questions", "plan-mode", "agent-instructions",
)
```
→ ★ **比 §82.1 的 `excludeTools` 更彻底**：连 subagent / skill / workflow / plugin / plan-mode 全关。
→ ★ **`"user-questions"` 被关掉** → 因为提问要走 §54.3 的 `ask_user` MCP 桥（否则 Agent 会问到一个没人看的地方）。
→ ★ **`"agent-instructions"` 关掉** → 指令只由 `AGENT_GUIDE` 提供（§62.2）。

### 86.2 ★ 权限预设（YAML/JSON overlay）

```python
{"id": "permission", "config": {"presets": {PERMISSION_PRESET:
      {"sandbox": "danger-full-access", "approval": "never"}}, "defaultPreset": PERMISSION_PRESET}}
```
→ ★ **看似矛盾（danger-full-access + approval never），实则自洽**：因为**危险能力已在 86.1 全关**，剩下的只有 MCP 画布工具 → 所以不需要审批框。**“关掉能力”换“免审批”**，这是正确的安全顺序。

### 86.3 ★ 每轮写 overlay、每会话一个 cwd

```python
def chat_cwd(home, chat_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", chat_id or "default")[:64] or "default"
    path = Path(home) / "chats" / safe; path.mkdir(parents=True, exist_ok=True); return path
def write_turn_overlay(cwd, route):
    target = cwd / "comfyart-turn-overlay.yml"      # ★ 文件名 .yml 但内容是 json.dumps（能用 YAML 解析器吃 JSON）
    target.write_text(json.dumps(overlay_entries(route), indent=2), encoding="utf-8"); return target
```
argv 变成 `dsh --profile comfyart-acp --patch <overlay>`；`route` 是 `("provider","model")` → `{"id":"acp","config":{"provider":...,"model":...}}`。
→ ★ **会话 id 先做文件名安全化再截 64 字符**。

### 86.4 ★ 首次自动建 profile

```python
async def ensure_profile(argv):
    if (profile_dir() / "package.json").is_file(): return ""
    proc = await create_subprocess_exec(*argv, "--profile", BOT_PROFILE,
                                        "--from-default-profile", TEMPLATE_PROFILE, "--dump-config",
                                        stdout=DEVNULL, stderr=PIPE, timeout=60)
    ... 再检查一次；失败返回 "could not create the comfyart-acp profile: {stderr[-400:]}"
```
`profile_dir()` = `$DSH_HOME` 或 `~/.dsh` 下 `profiles/comfyart-acp`。
`resolve_command()`：`which dsh` → macOS `/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh` → 失败文案 `dsh was not found on PATH. Use "Manage dsh Command…" in the DeepSeek Harness app, or \`npm install -g @deepseek-ai/dsh\`.`
`_argv_for`：Windows 上 `.cmd`/`.bat` → `["cmd.exe","/d","/s","/c",path]`（同 §82.3）。
→ ★ **“自动从模板 profile 克隆一份专用配置”是接第三方 Agent 的干净做法**（不污染用户全局配置）。

---

<a id="sec87"></a>
## 87. ★★★ Agent 反问用户 + 运行审批（`api/bot_asks.py` 129 行 + `api/mcp_tools/bot_tools.py` 191 行）

> 这是本次拆解里**对我们最有价值的一节**。我们目前完全没有「Agent 中途问用户」和「运行审批」机制。

### 82.1 `api/bot_asks.py` 常量与状态

```python
ASK_TIMEOUT_S = 540.0            # 普通提问最长等 9 分钟
RUN_APPROVAL_WAIT_S = 45.0       # 运行审批只等 45 秒
RUN_APPROVAL_KIND = "run_approval"
_MAX_OPTIONS = 12

class PendingAsk:
    id = uuid.uuid4().hex; chat_id; message_id; spec
    abandoned = False
    future: asyncio.Future = asyncio.get_event_loop().create_future()

PENDING: dict[str, PendingAsk] = {}
ANSWERED_RUN_APPROVALS: dict[tuple[str, str], dict] = {}   # ★ key = (chat_id, prompt)
```

### 82.2 `validate_spec`（服务端硬校验，不信任模型）

- `prompt` 必填非空；
- `options` 必须是 **2..12** 项（`not (2 <= len(raw_options) <= _MAX_OPTIONS)` 直接报错）；
- 每项必须是 object 且 **`id` 与 `label` 都非空**；**`id` 不得重复**（`seen_ids` 去重，报 `duplicate option id {oid!r}`）；
- `description` 可选；
- `min_selections` / `max_selections` 默认 **1 / 1**，校验 `0 <= min <= max <= len(options)`；
- `allow_other = bool(args.get("allow_other"))`。

### 82.3 ★★★ 三个关键机制

1. **同类审批去重**：`create_ask` 若 `is_run_approval(spec)`，先把同 chat 的**所有旧 run_approval** `resolve_ask(stale.id, "cancelled")` → 不会堆出多个审批卡。
2. **★ 迟到答案不丢（`ANSWERED_RUN_APPROVALS`）**：
   ```python
   nobody_waiting = ask.future.done() or ask.abandoned
   if nobody_waiting and status == "answered" and is_run_approval(ask.spec):
       ANSWERED_RUN_APPROVALS[(ask.chat_id, str(ask.spec.get("prompt") or ""))] = outcome
   ```
   → 工具已经超时放弃等待（`abandon_ask` 只设 `abandoned = True`，**future 仍活着**），用户后来才点批准 → 答案**按 `(chat_id, prompt)` 缓存**；
   → 下次 `maybe_ask_run_approval` 开头 `take_run_approval_answer(chat_id, action)` = `ANSWERED_RUN_APPROVALS.pop((chat_id, prompt), None)` → **命中就直接跑，不再问第二次**。
3. **★ 轮次结束不清审批**：`cancel_chat_asks(chat_id)` 只挑 `not is_run_approval(a.spec)` 的 ask 取消 → **用户可以在轮次结束后再补批准**。

`validate_answer`（回答侧校验）：未知 id → `unknown option ids: [...]`；超 `max` → `at most {max} selections`；`other_text` 但 `allow_other=False` → `free-text answer is not allowed`；不足 `min` 且无 `other_text` → `at least {min} selections`。

### 82.4 ★★★ `_await_ask`：把提问做成消息时间线里的一个 block

```python
ask = bot_asks.create_ask(chat_id, state.message_id, spec)
block = {"type": "ask", "ask_id": ask.id, "status": "pending", **spec}
state.blocks.append(block)
chat_store.update_message(chat_id, state.message_id, blocks=state.blocks)
broadcast("turn_ask", {"chat_id":..., "message_id":..., "ask_id": ask.id, **spec})
outcome = await asyncio.wait_for(asyncio.shield(ask.future), timeout=ASK_TIMEOUT_S)
```
- ★ **`asyncio.shield`** → `wait_for` 超时**不会取消 future**（这正是 §87.3-2 能收到迟到答案的前提）；
- 超时分支：`keep_pending_on_timeout=True` → `abandon_ask` + 返 `{"status":"pending","ask_id":...}`；否则 `resolve_ask(id,"expired")` + `{"status":"expired"}`；
- 回填：`block["status"] = outcome["status"]`，有 `selected` / `other_text` 就写进 block → **再次落库** → `broadcast("turn_ask_resolved", {...})`。
→ ★ **提问不是弹窗，而是消息 block**（`type:"ask"`，带 `ask_id`/`status`/`selected`/`other_text`）→ **刷新页面后仍能看到并回答**（因为已落库）。

### 82.5 `ask_user` 工具（description 原文）

> `"Pause and ask the user a question in the chat panel; blocks until they answer (or a few minutes pass). Use when a decision genuinely belongs to the user: creative direction, destructive actions, long generations. options are the choices; set min/max_selections for multi-select and allow_other to accept free text. Returns {status, selected, other_text} — status cancelled/expired means no answer, don't retry immediately."`

- 前置：`bot_turn_state()` 取 `BOT_CHAT_ID.get()` + `ACTIVE_TURNS.get(chat_id)`；无 chat → `ValueError("ask_user is only available inside ComfyArt Agent chats")`；无 state → `ValueError("no active Agent turn for this chat")`；
- ★ **未答时返回的是带指令的 note**：`{"status": ..., "note": "the user did not answer; proceed conservatively or end the turn"}`。

### 82.6 ★★★ `maybe_ask_run_approval(action)`：不阻塞轮次的运行审批

```python
chat = chat_store.get_chat(chat_id)
if not chat or chat.get("run_mode") != "ask": return None      # ★ auto 模式直接放行
outcome = bot_asks.take_run_approval_answer(chat_id, action)   # ★ 先取迟到答案
if outcome is None:
    outcome = await _await_ask(chat_id, state, {
        "prompt": action,
        "options": [{"id":"run","label":"运行"},
                    {"id":"always","label":"总是运行","description":"运行并把这个对话切回自动"},
                    {"id":"cancel","label":"取消"}],
        "min_selections":1,"max_selections":1,"allow_other":False,
        "kind": bot_asks.RUN_APPROVAL_KIND},
        timeout=bot_asks.RUN_APPROVAL_WAIT_S, keep_pending_on_timeout=True)
```
四种返回：
| 情况 | 返回 |
|---|---|
| 选了 `always` | `chat_store.update_chat(chat_id, run_mode="auto")` → `None`（放行，且整个会话转自动） |
| 选了 `run` | `None`（放行） |
| **45s 没人答（`pending`）** | `{"started": False, "pending_approval": True, "ask_id": ..., "note": "the user has not answered '<action>' yet — the question stays open in the chat panel. Nothing ran. Tell the user it is waiting for their approval; once they answer, call this tool again with the same arguments and it will proceed without asking twice."}` |
| 选了 `cancel` | `{"cancelled": True, "status": "declined", "note": "the user declined this run; do not retry it without new instructions"}` |
| 其它（expired/cancelled） | `{"cancelled": True, "status": ..., "note": "the run approval was {status} before the user answered — nothing ran; ask the user whether to run it"}` |

→ ★★★ **核心设计：审批只等 45 秒，不阻塞整个轮次**。模型拿到 `pending_approval` 后继续做别的事，用户之后点批准，**模型再调一次同样的工具就直接跑**（靠 §87.3-2 的迟到答案缓存）。
→ ★ `run_mode` 是 **chat 级 setting**（`auto` / `ask`），且 `always` 选项会把它改回 `auto`。

### 82.7 `update_plan`：零副作用的“计划清单”

```python
async def update_plan(args):
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps: raise ValueError("steps must be a non-empty array")
    return {"ok": True}          # ★ 什么都不做！
```
`PLAN_SCHEMA`：`steps[] of {text: string, status: enum["pending","active","done"]}`，`required: ["text","status"]`，`additionalProperties: False`。
description 原文：`"Show the user your plan for a task with several steps as a checklist in the chat. Call it before starting with every step pending (or the first active), then again with the full list whenever a step becomes active or done. Skip it for one-step requests."`
→ ★ **handler 只校验，渲染完全由前端从 `tool_use` block 里读 `input.steps` 完成** → 服务端零状态，计划天然随消息历史回放。

### 82.8 ★ MCP 侧的工具可见性（`api/mcp.py` 143 行）

```python
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
def _tool_visible(name): return name not in bot_tools.TOOLS or bool(BOT_CHAT_ID.get())
```
- ★ **`ask_user` / `update_plan` 只在带有效 `?bot_chat=` 时出现在 `tools/list`**；`tests/test_mcp.py:38,41` 就是断言这一点；
- `INSTRUCTIONS`（`initialize` 返回，原文）：`"Window into ComfyArt, a design canvas running on this ComfyUI. Call get_canvas first. Every canvas tool is executed by the open ComfyArt page of the project; without that browser tab they fail with a timeout. Generation tools return ids at once: wait_generation on them, then view_canvas to check the result."`
- `serverInfo = {"name":"comfyart-mcp","title":"ComfyArt","version":"0.1.0"}`；
- `_payload_to_content`：`payload.pop("_images")` → 首 block 是 `json.dumps(payload, ensure_ascii=False, default=str)`，之后每个图一个 `{"type":"image","data":...,"mimeType": img.get("mime","image/png")}`；
- 异常分层：`(ValueError, TypeError, KeyError)` → `isError: True` **不写 log**；其它 → `_log.exception` + `isError: True`；
- `_dispatch` 支持 `initialize / ping / tools/list / tools/call / resources/list / resources/templates/list / prompts/list`，其它 → `-32601 method not found`；**消息无 `id` → 返回 None → HTTP 202**；
- `POST /comfyart/mcp`：`mcp_enabled()` 关 → **403** `-32000 "ComfyArt MCP is off: turn it on in Agent settings"`；JSON 解析失败 → 400 `-32700 "parse error"`；**数组（batch）→ 400 `-32600 "batch requests are not supported"`**；非 dict 或 `jsonrpc != "2.0"` → 400；
f ""))` → **`finally` 里 `reset(token)`**；
- `initialize` 时额外设 header `Mcp-Session-Id: uuid4().hex`；
- ★ **`GET /comfyart/mcp` → 405 `{"error":"SSE streams are not supported; POST JSON-RPC messages instead"}`**；`DELETE` → 200。
→ ★★ **整个 MCP server 是手写 JSON-RPC over HTTP，无 SSE、无 stateful session、无 mcp SDK 依赖** → 极轻，我们可直接照搬这个结构。

`api/mcp_tools/_context.py`（全文 5 行）：
```python
BOT_CHAT_ID: ContextVar[str] = ContextVar("comfyart_bot_chat", default="")
PROJECT:     ContextVar[str] = ContextVar("comfyart_project", default="")
```

### 82.9 `api/mcp_commands.py`（42 行）：后端 → 浏览器页面的命令回环

```python
COMMAND_EVENT = "comfyart-mcp-command"; DEFAULT_TIMEOUT_S = 20.0
_pending: dict[str, asyncio.Future] = {}
async def submit_command(action, payload, project, timeout=20.0, chat=""):
    command_id = uuid.uuid4().hex; future = create_future(); _pending[command_id] = future
    PromptServer.instance.send_sync(COMMAND_EVENT, {"id":command_id,"action":action,"project":project,"chat":chat,"args":payload})
    return await asyncio.wait_for(future, timeout)
    # 超时 → ValueError(f"no ComfyArt page answered the {action!r} command within {timeout:.0f}s — the project {project or '(any)'} must stay open in a browser tab")
    # finally: _pending.pop(command_id, None)
```
`POST /comfyart/mcp_command_result`：future 不存在或已 done → `{"ok":False,"reason":"unknown or expired command"}`；`body["ok"]` → `set_result(result if isinstance(result, dict) else {"result": result})`；否则 `set_exception(ValueError(body["error"] or "command failed in the ComfyArt page"))`。
→ ★ **`chat` 也透传到前端**（前端 `ask_user` handler 需要它才能把答案回到同一个轮次）；★ **超时文案直接说明根因（页面必须开着）**。

### 82.10 ★ 其它关联事实

- `bot/local_llm.py:223`：`timeout=560 if name == "ask_user" else 200` → **`ask_user` 的工具超时给 560s，刻意大于 `ASK_TIMEOUT_S=540`**（保证 MCP 侧先超时返回结构化结果，而不是被外层掐断）；
- `bot/_cli_common.py:60`：`"- ask_user when a decision genuinely belongs to the user; give short "`（提示词里教用 ask_user）；`:110` `"ask_user"` 在 `CORE_MCP_TOOLS` 里；
- `src/bot.ts:88`：`ask_user: t('bot.askYou')` → **前端有工具名→本地化标签映射表**。

### 82.11 ★★★ 我们要抄的具体清单

1. **`type:"ask"` 消息 block**（§87.4）——把「Agent 反问」做成**落库的时间线 block**，而不是弹窗；刷新后仍可回答。
2. **`asyncio.shield` + `abandoned` 标志 + `(chat_id, prompt)` 迟到答案缓存**（§87.3-2）——**审批不阻塞、迟到不丢**，这是整套设计里最精巧的一处。
3. **`run_mode: auto|ask` 会话级开关 + `always` 选项自动降级为 auto**（§87.6）。
4. **审批只等 45s，超时返回 `pending_approval` + 一段教模型「稍后重调同一工具」的 note**（§87.6）。
5. **`update_plan` 零副作用 + 前端从 `tool_use.input.steps` 渲染**（§87.7）——服务端无状态，计划随历史回放。
6. **工具可见性按 `?bot_chat=` 动态裁剪**（§87.8）——我们给 pi 下发工具时同样应该按上下文裁剪。
7. **手写 JSON-RPC MCP（无 SSE / 无 session）**（§87.8）——我们做 canvas MCP 的最省事路线。
8. **`submit_command` 回环 + 超时文案说明根因**（§87.9）——对标我们「后端要操作前端画布」的场景。

---

<a id="sec88"></a>
## 88. ⚠️⚠️ 重大更正：§81（Claude Code / Codex 驱动）**大部分内容是错的**，以本节为准

> 触发原因：我做了一次全仓库标识符核查（脚本 `verify_ids.py`：把文档反引号里的 camelCase/snake_case 标识符逐个 grep 回源码），
> 发现 §81 里这些标识符**在仓库中零命中**：`AskBridge`、`ask_user_bridge`、`--permission-mode`、`bypassPermissions`、
> `CLAUDE_*_BYPASS_PERMISSIONS_PROMPT`、`--output-last-message`、`tempfile.mktemp`、`tool_timeout_sec=3600`、
> `default_tools_approval_mode`、`model_reasoning_effort`、`command_execution`、`output_item`、`output_text`、
> `reasoning_output_tokens`、`startup_timeout_sec`、`wait_for_exit`、`usage_from_result`、`callId`。
> **§81 的 §81.1–§81.6 全部作废**（§81.5 已在前面更正）。本节是逐行重读 `bot/claude_code.py`(200) + `bot/codex.py`(399) 后的事实。

### 83.1 `bot/claude_code.py`（200 行）真实事实

**导入**（来自 `_cli_common`）：`AGENT_GUIDE, CliStreamParser, PROBE_CACHE_S, base_spawn_env, kill_process_tree, run_cli_turn, tool_result_text`。

- `class _StreamParser(CliStreamParser): super().__init__(text_from_assistant=False)` → ★ Claude 的文本**只从 partial delta 出**，不从 assistant 消息补发。
- `build_stream_input(turn)`：附件 → `{"type":"image","source":{"type":"base64","media_type": att.get("media_type") or "image/jpeg","data":data}}`，再加 `{"type":"text","text":turn.user_text}`，包成 `{"type":"user","message":{"role":"user","content":[...]}}` + `"\n"`。
- ★★ `resolve_claude_command()` 的**跨平台找可执行文件**逻辑（我们驱动 pi CLI 时会遇到完全一样的问题）：
  1. Windows 先 `shutil.which("claude.exe")`；
  2. 再 `shutil.which("claude")`；
  3. 找不到则按候选找：`~/.local/bin/claude`、`~/.npm-global/bin/claude`、`/usr/local/bin/claude`、`/opt/homebrew/bin/claude`，**再按倒序遍历 `~/.nvm/versions/node/*/bin/claude`**；
  4. ★ 若找到的是 `.cmd`（Windows npm shim）→ 依次尝试：`node_modules/@anthropic-ai/claude-code/bin/claude.exe`（原生）→ `node + node_modules/@anthropic-ai/claude-code/cli.js` → `["cmd.exe","/d","/s","/c",<cmd>]`。
- `capabilities()` → `ProviderCaps(stateful=True, tools="mcp", attachments=True)`。
- `probe()`：`PROBE_CACHE_S` 缓存 → `<argv> --version` + `asyncio.wait_for(communicate(), 15)`；失败 → `ProviderStatus(available=False, detail=f"version check failed: {e}")`；★ `logged_in = True if (Path.home()/".claude"/".credentials.json").exists() else None`（**用凭证文件是否存在推断登录态，None = 未知**）。
- `list_models()` → **硬编码 `["sonnet", "opus", "haiku"]`**。
- ★★★ **真正的权限收紧方式 = 禁用内置工具**（不是 permission-mode）：
  ```python
  _DISALLOWED_BUILTIN_TOOLS = (
      "Bash", "BashOutput", "KillShell", "Write", "Edit", "NotebookEdit",
      "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task", "Agent",
      "TodoWrite",
  )
  ```
  → ★ **把 Claude 的文件/网络/子代理能力全部关掉，只留 MCP** → 这才是它的 sandbox 策略。
- `_build_argv(turn)` 完整参数序列：
  - 有附件：`-p --input-format stream-json`（stdin 喂 JSON）；无附件：`-p <user_text>`；
  - 恒定：`--output-format stream-json --include-partial-messages --verbose --strict-mcp-config`；
  - `--model <turn.model>`；
  - 有 `mcp_endpoint` 时：`--append-system-prompt AGENT_GUIDE` + `--mcp-config <json>`，其中
    `{"mcpServers": {"comfyart": {"type":"http","url":<endpoint>}, "comfy": {"type":"stdio","command":argv[0],"args":argv[1:]}}}`；
  - `--allowedTools <逗号连接>`（来自 §64.4 `_allowed_tools`）；
  - `--disallowedTools <_DISALLOWED_BUILTIN_TOOLS>`；
  - `--resume <turn.resume_token>`。
- `send()` → `run_cli_turn(argv, cwd=self._resolve_home(), env=spawn_env(), emit, handle, parser=_StreamParser(), stdin_payload=build_stream_input(turn) if turn.attachments else None, exe_label="claude")`；`stop()` → `kill_process_tree(handle)`。

### 83.2 `bot/codex.py`（399 行）真实事实

- `_MCP_TOOL_TIMEOUT_SEC = 600`（**不是 3600**）；
  `_MEDIA_EXT = {"image/jpeg":".jpg","image/jpg":".jpg","image/png":".png","image/webp":".webp","image/gif":".gif"}`。
- `resolve_codex_command()`：Windows `codex.exe` → `codex`（若是 `.cmd` 则改走 `node + node_modules/@openai/codex/bin/codex.js`）→ 候选
  `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`、
  `/Applications/ChatGPT.app/Contents/Resources/codex`、`~/.local/bin/codex`、`~/.codex/bin/codex` → Windows 再试同名 `+ ".exe"`。
  → ★ **连 ChatGPT 桌面 App 里内置的 codex 都能找到**，这个搜索面比我们的实现宽。
- ★★ **系统提示词通过 `AGENTS.md` 注入**（不是命令行参数）：
  ```python
  _AGENT_INSTRUCTIONS = ("# ComfyArt Agent\n\n" + AGENT_GUIDE + "\n\n"
      "Do not use shell, file, web, or desktop-control tools; everything goes "
      "through the comfyart MCP tools.\n")
  def write_agent_instructions(home): path = Path(home)/"AGENTS.md"; 只在内容不同时写
  ```
  → 因为 `cwd = home_dir`，Codex 会自动读 `AGENTS.md`。**我们驱动 pi 时对应做法：往工作目录写 `AGENTS.md`**（我们本来就有 AGENTS.md 机制）。
- `read_codex_config()`：`tomllib.loads((Path.home()/".codex"/"config.toml").read_text(errors="ignore"))`，`ImportError`/解析失败 → `{}`。
- ★★★ `_mcp_lockdown_args()`：**把用户 config 里除 `comfyart` 以外的所有 MCP server 全部关掉**
  ```python
  for server_id in (read_codex_config().get("mcp_servers") or {}):
      if server_id != "comfyart": args += ["-c", f"mcp_servers.{server_id}.enabled=false"]
  ```
  → ★ **防止用户自己的 MCP 干扰画布 Agent**（Claude 侧对应的是 `--strict-mcp-config`）。
- `_detect_logged_in()`：`~/.codex/auth.json` 存在且 `st_size > 2` → True；否则扫 config `model_providers`，任一 provider 含 `{"api_key","experimental_bearer_token","bearer_token","access_token"}` → True。
- `list_models()` → `await asyncio.to_thread(...)` 读 config 的 `model`（**从用户配置读，与 Claude 的硬编码不同**）。
- `_flatten_mcp_content(content)`：`str` 原样；`list` → text block 取 `text`、非 text block `json.dumps`；其它 → `json.dumps`。
- `_tool_error_text(item)`：先看 `error`（dict 则依次取 `message`/`error`/`detail`，否则 json.dumps）；再看 `result`（`_flatten_mcp_content(result["content"])` → 再取 message/error/detail → json.dumps）；都没有 → `"tool failed"`。
- `_CodexStreamParser`（★ **不是 `CliStreamParser` 子类**，只实现 `parse_line`）状态：`session_id, result_error, result_seen, usage, _text_emitted: dict[str,int], _tool_names: dict[str,str], _emitted_tool_use: set[str]`
  - `thread.started` → `session_id = data["thread_id"]`；
  - `turn.completed` → `result_seen = True` + `usage = normalize_usage(data.get("usage")) or self.usage`；
  - `turn.failed` → `result_seen = True` + `result_error = (data.get("error") or {}).get("message") or "turn failed"`；
  - `error` → `result_error = data["message"] or "error"`；
  - `item.started / item.updated / item.completed` → `_parse_item(item)`；
  - ★★★ **文本增量靠 `_text_emitted[item_id]` 做前缀差分**：`delta = text[emitted:]`，然后 `_text_emitted[item_id] = len(text)` → **Codex 每个 item 给的是全量文本，必须自己算 delta**；`agent_message` → `t="delta"`，`reasoning` → `t="thinking"`；
  - `mcp_tool_call`：`is_terminal = status in ("completed","failed") or item.get("result") or item.get("error")`；
    非终端 → 用 `_emitted_tool_use` 去重（★ 同一 item 在 started/updated 会重复出现）+ `_tool_names[item_id] = name` → `tool_use`；
    终端 → `tool_result`（错误走 `_tool_error_text(...)[:TOOL_RESULT_CAP]` + `is_error=True`；成功 `_flatten_mcp_content(result["content"])`，为空则 `json.dumps(result["structured_content"])`），`name` 缺失时用 `_tool_names.get(item_id)`。
- `_build_argv(turn, home)` 完整序列：
  - `flags = ["--json", "--skip-git-repo-check"]`；
  - MCP：`-c mcp_servers.comfyart.url="<endpoint>"`、`-c mcp_servers.comfyart.required=true`、`-c mcp_servers.comfyart.tool_timeout_sec=600`；
  - `_mcp_lockdown_args()`；
  - `-c features.shell_tool=false`、`-c web_search="disabled"`、`-c approvals_reviewer="auto_review"`；
  - `-m <model>`；
  - 附件 → `_write_attachment()` 落盘到 `home/attachments/att-<epoch_ms>-<i><ext>` → `-i <path>`；
  - `argv + ["exec"]` + （有 resume_token：`["--approve-for-me","resume"]`，否则 `["--approve-for-me"]`）+ `flags` + `["--"]` + `[resume_token]` + `user_text`。
- `_write_attachment()`：`base64.b64decode` 失败或空 → 返 None；扩展名取 `_MEDIA_EXT.get(media_type, ".bin")`。
- `send()`：`home = self._resolve_home()`（`makedirs` + `write_agent_instructions`）→ `run_cli_turn(..., parser=_CodexStreamParser(), exe_label="codex")` → **`finally` 里逐个 `os.remove(temp_files)`**。

### 83.3 ★ 更正后的「可抄清单」（替换 §81.6）

1. **`resolve_*_command()` 的多候选查找**（§88.1 / §88.2）——我们驱动 `pi` CLI 时同样需要：PATH → 常见安装目录 → nvm 遍历 → **Windows `.cmd` shim 改走 `node <cli.js>`**。
2. **权限收紧靠「关掉内置工具」**：Claude 用 `--disallowedTools` 列 14 个 + `--strict-mcp-config`；Codex 用 `features.shell_tool=false` + `web_search="disabled"` + 关掉其它 MCP server（§88.1 / §88.2）。
3. **`AGENTS.md` 注入系统提示**（Codex）→ 我们驱动 pi 时直接写工作目录的 AGENTS.md（我们已有该机制）。
4. **全量文本 → 前缀差分算 delta**（`_text_emitted[item_id]`）→ **我们接任何"每次给全量"的 CLI 都必须做这个**（§88.2）。
5. **`item.started/updated` 去重**（`_emitted_tool_use`）+ **`tool_result` 的 name 反查**（`_tool_names`）→ 事件归一层必备。
6. **附件落盘 → `-i <path>` → `finally` 删除**（Codex 不支持 stdin 图片）→ 我们给只吃文件参数的 CLI 照搬。
7. **`probe()` 用 `--version` + 15s 超时 + `PROBE_CACHE_S` 缓存 + 凭证文件推断登录态**（§88.1 / §88.2）→ 我们做 provider 健康检查照搬。
8. **`list_models()` 两种策略**：Claude 硬编码、Codex 读用户 config → 我们按 CLI 能力选。

---

<a id="sec89"></a>
## 89. ⚠️ 更正：§38 / §39 作废——`src/layers.ts` 与 `src/elements.ts` 的真实内容（逐行重读）

> 核查方式：把文档反引号里的标识符逐个 grep 回源码，发现 §38/§39 里的
> `layerName / layerId / layerOf / layerNames / ensureLayer / layersOf / layerBounds / layerSummary / LayerSummary /
> layerImage / layerImageBounds / layerPrompt / layerPromptFor / layerPromptForRefs / layerOfName / isLayerGroup /
> getParentIds / isMediaShape / isRefShape / isRefSource / mediaKind / mediaMeta / isHiddenShape / isMediaShapeType /
> isRefShapeType / arrangeShapes / eachTargets / refFrameFor / isGenerator` **在仓库中零命中**。
> ★ 真实情况：**ComfyArt 的「图层」不是 tldraw group，而是「把一张图拆成多张透明 PNG + frame」的 AI/CV 拆图能力。**
> 我们文档里原来那套「group + meta 做图层」的结论是错的，不要照抄。

### 84.1 `src/layers.ts`（276 行）真实内容：三种「拆图层」路径

**常量与工具**
- `SOLID = 200`、`OPAQUE = 128`；
- `interface Raw { media: Media; data: Uint8ClampedArray; cover: number; diff: number }`；
- `loadImage(src)` → Promise（`img.onerror` → `Error('cannot load ' + src)`）；
- `pixels(img, w, h)` → `getImageData(...).data`，`getContext('2d', { willReadFrequently: true })`；
- `measure(media, data, orig)` → 统计 `cover = covered/(len/4)`（alpha>OPAQUE 占比）与 `diff`（alpha>SOLID 的像素与原图的平均 RGB 差；无则 999）；
- `overlap(a, b)` → **alpha 二值化后的 IoU**（`inter/union`，阈值 OPAQUE）；
- `canvasOf(data, w, h)` → ImageData → canvas；
- ★ `grow(data, w, h, px)` → **用 `ctx.filter = blur(px)` 再二值化 alpha（`>16 ? 255 : 0`）实现 mask 膨胀**（无卷积，纯 canvas 技巧）；
- ★ `cropLayer(data, w, h)` → 扫 alpha>8 求包围盒 → 裁出 → `toBlob('image/png')` → `uploadImage` → 返 `{ media, x: l, y: t }`（**带偏移，用于放回原位**）。

**路径 A：`registerPlacer('layers', ...)` —— AI 生成多张透明分层图后的清洗与组装**
```ts
const source = String(job.extra?.source ?? '')
const sourceSrc = viewUrl(parseAnnotated(source))
const orig = pixels(sourceImg, w, h)
const raws = imgs.map((img, i) => measure(media[i], pixels(img, w, h), orig))
for (const r of [...raws].sort((a, b) => b.cover - a.cover)) {
  if (r.cover > 0.9 || r.cover < 0.002 || r.diff > 60) continue      // ★ 三条剔除规则
  if (kept.some((k) => overlap(r.data, k.data) > 0.9)) continue      // ★ IoU>0.9 视为重复
  kept.push(r)
}
kept.sort((a, b) => raws.indexOf(a) - raws.indexOf(b))               // ★ 过滤后恢复原顺序
```
→ 组装：`editor.run(() => { deleteShape(holder); createShape({type:'frame', name: t('layers.splitLayers', base)}); bgId = placeMedia(..., t('layers.background', base), 0,0,fw, frameId); crops.forEach((c,i) => placeMedia(..., t('layers.layer', base, i+1), c.x*scale, c.y*scale, c.media.w*scale, frameId)) }, { history: 'ignore' })`
→ ★ **最后一步：把所有分层的 alpha 并成 union → `grow(union, w, h, 4)` → `maskFile(...)` → `startJob(editor, bgId, eraseGraph(source, mask), { placer:'replace' })`** → **把背景层上已被拆走的区域擦掉（inpaint）**，这样分层图叠回背景不重影。
- `parseAnnotated(a)`：`/^(?:(.*)\/)?([^/]+) \[(\w+)\]$/` → `{ filename, subfolder, type }`（★ 与 §28 的 `annotated()` 配对）。
- `registerPlacer('replace', ...)`：把结果图**替换**成同一 shape 的新 asset（`meta.job = null`）。

**路径 B：`separateRegions(editor, shape, boxes, onError)` —— 纯前端按框拆图（不调 AI）**
- 从 asset 取 `w,h,name` → `rest = pixels(...)`；
- 对每个 `[bx,by,bw,bh]`（**归一化 0..1**）：把该区域内 alpha>8 的像素搬到 `data`，并在 `rest` 里置 alpha=0（★ **拆走即从剩余中挖掉**）→ `cropLayer(data,w,h)`；
- `if (!parts.length) throw new Error(t('layers.noVisiblePixels'))`；`left = await cropLayer(rest, w, h)`；
- ★ **位置换算**：`at(x,y) = Vec.Rot({x: x*s, y: y*s}, shape.rotation).add(shape)`（**考虑 shape 旋转**）；
- ★ **z 序保持**：`getIndicesBetween(shape.index, next?.index, parts.length)`（`next` = 同级下一个）→ 新层插在原图之后；
- ★ **命名去重**：统计同级里 `a.props.name.startsWith(`${name} · `)` 的数量 `taken` → 新层名 `${name} · ${taken+i+1}`；
- `editor.markHistoryStoppingPoint('separate layers')` + `editor.run(...)`；若 `left` 为空则删原图，否则替换原图 asset。

**路径 C：`regionSplit(editor, shape, boxes, onError)` —— SAM 掩码拆图**
- `spawnHolder(editor, shape)` 先放占位；
- `samBoxesGraph(annotated(src), crops)` → `waitForImages(queuePrompt(graph), result)` 拿每框 mask；
- 每框：`m[p] < 128` 跳过 → 把原图像素 + alpha=255 写进 `data`，同时写进 `union` → `cropLayer`；
- `if (!layers.length) throw new Error(t('layers.noObjectsDetectedInThe'))`；
- ★ **背景 mask**：`maskFile(w,h,ctx => { ctx.filter='blur(6px)'; drawImage(u); drawImage(u) })`（**画两遍加深**）→ `eraseGraph(annotated(src), mask)`，`placer:'regions'`，`extra:{layers, base:name}`；
- 失败 → `editor.deleteShape(holder)` + `onError(errorText(e))`；成功返回 `holder`。
- `export interface RegionLayer { media: Media; x: number; y: number }`

### 84.2 ★★★ `src/elements.ts`（187 行）真实内容（187 行）：OCR + SAM 把设计图拆成「可编辑文字层 + 物体层 + 干净背景」

> 这是本次拆解里**技术含量最高、最值得抄**的一个前端模块：它把一张位图设计稿变成可编辑的 frame。

**常量**
```ts
const MIN_SCORE = 0.6            // OCR 置信度门槛
const MASK_PAD = 0.2             // 文字 mask 外扩比例（相对字高）
const MAX_SIZE_PER_HEIGHT = 1.05
const TEXT_FILL = 0.94
const PROBE_SIZE = 100           // 测字宽用的基准字号
const FONT = 'Arial'
const FAMILIES = { serif:['Georgia','SimSun'], sans:['Arial','Microsoft YaHei'], display:['Impact','SimHei'], mono:['Courier New','FangSong'], script:['Georgia','KaiTi'] }
const CJK = /[぀-ヿ㐀-鿿가-힯]/   // ★ 中日韩 → 用 families 数组的第 2 个字体
const STYLE_SYSTEM = 'You identify typography in design images. Reply with ONE JSON object and nothing else.'
const CENTER_TOLERANCE = 0.03
const RIGHT_MARGIN = 0.1
const BOLD_INK = 0.3
const OBJECT_PROMPTS = ['product', 'logo']    // ★ SAM 文本提示
const MIN_OBJECT = 0.002
const OBJECT_GROW = 6
```
**OCR**：`readLines(src)` → `POST /comfyart/ocr` → `body.lines.filter(l => l.score >= MIN_SCORE && l.text.trim())`；`interface Line { box: [number,number][]; text: string; score: number }`（**box 是 4 点多边形**）。

★★ **`inkOf(ctx, box)` —— 从像素反推文字颜色与粗细**：
1. 取 box 包围盒的 ImageData；算平均亮度 `mean = Σ(0.299R+0.587G+0.114B)/n`；
2. 按 `亮度 > mean` 把像素分成两组 `sums[1]`（背景）/`sums[0]`（墨）；
3. **取像素数较少的一组当「墨」**（`sums[0][3] <= sums[1][3] ? sums[0] : sums[1]`）→ 平均 RGB → `#rrggbb`；
4. `weight = 墨像素占比 > BOLD_INK ? 700 : 400`。
→ ★ **不依赖任何字体/OCR 样式信息，纯统计**，可直接搬到我们代码里。

**`alignOf(cx, w, width)`**：`|cx - width/2| < width*0.03` → `center`；`cx-w/2 > width/2 && cx+w/2 > width*0.9` → `right`；否则 `left`。

**`layersOf(lines, img)`**：由 4 点 box 算 `cx, cy, w=hypot(p1-p0), h=hypot(p2-p1), angle=atan2(p1-p0)` + `font` + `align` + `...inkOf(...)` → `TextLayer { text, cx, cy, w, h, angle, color, weight, font, align }`。

**`applyTypography(src, layers)`**：把 `1. "text"` 列表 + 图交给 LLM（`ask(prompt, STYLE_SYSTEM, [src])`），要求 `{"lines":[{"n":1,"family":"sans","weight":700}]}` → `parseJson` → `l.font = FAMILIES[family][CJK.test(text) ? 1 : 0]`；weight 只接受 400/700。
→ ★ **视觉模型只用来判「字体类别 + 粗细」，本地映射成具体字体**（避免模型直接选字体不可控）。

**`objectLayers(src, w, h, texts)`**：
- 先用 `paintBoxes(tctx, texts, MASK_PAD)`（★ `save/translate(cx,cy)/rotate(angle)/fillRect` → **按旋转角画文字矩形**）生成 `textMask`；
- 对 `OBJECT_PROMPTS` 每个 prompt 跑 `samGraph(annotated(src), { text: prompt })` 取 mask；
- ★ **三重排除**：`m[p] < 128 || textMask[p+3] || union[p+3]` → **文字区不重复抠、已抠过的像素不重复**；
- `count < MIN_OBJECT * w * h` 则丢弃该层；`cropLayer` 出层。

**`editElements(editor, shape, onError)`（入口）**：`spawnHolder` → `startJob(editor, holder, async () => { readLines → layersOf → applyTypography(catch 忽略) → objectLayers → 若两者都空 throw t('elements.nothingFound') → mask = maskFile(w,h,ctx => { paintBoxes(...); drawImage(grow(union,w,h,OBJECT_GROW)) }) → eraseGraph(annotated(src), mask) }, { name: t('elements.jobName', name), placer: 'textLayers', extra }, onError)`
→ ★ **文字用矩形 mask + 物体用 grow 后的 mask，合并成一张 inpaint mask，一次擦干净**。

**`registerPlacer('textLayers', ...)`（回填可编辑文字）**：
- 建 frame（`meta: { elements: source }` ★ **记住原图源，供再次编辑**）+ 背景 plate + 物体层；
- ★ **字号反解**：`measure.font = `${weight} ${PROBE_SIZE}px "${font}"`` → `size = min(l.h*k*1.05, (PROBE_SIZE * l.w*k * 0.94) / measure.measureText(text).width)`
  → **用 100px 基准量出字宽，再按比例反推出让文字刚好填满 box 宽度的字号**；
- ★ **旋转框左上角换算**：`x = l.cx*k - hw*cos + hh*sin`，`y = l.cy*k - hw*sin - hh*cos`（`hw = l.w*k/2`, `hh = size/2`）；
- 创建 `TextShape`：`props: { text, size, font, color, weight, line: 1, align, autoSize: false, w: l.w*k }`（★ `autoSize:false` + 固定 w 才能保持排版）；
- 整段包在 `editor.run(..., { history: 'ignore' })`。

### 84.3 ★ 更正后的「可抄清单」（替换 §38.3 / §39 结论）

1. **`inkOf` 像素统计反推文字色 + 粗细**（§89.2）——零依赖，直接可用。
2. **字号反解公式**（`PROBE_SIZE` 测宽 → `min(高度上限, 宽度上限)`）——我们做「图片转可编辑文字」时必须用这个。
3. **旋转文字的定位换算**（`cx - hw*cos + hh*sin`）与 `angle = atan2(p1-p0)`。
4. **`grow()` = blur + alpha 二值化** 做 mask 膨胀（§89.1）——比手写卷积省事得多。
5. **拆图后必须 inpaint 背景**（union mask → `eraseGraph`），否则叠回会重影（§89.1 路径 A/C）。
6. **`getIndicesBetween` 保持 z 序 + `name.startsWith(`${name} · `)` 计数去重**（§89.1 路径 B）。
7. **模型只做「类别判断」（字体 family/weight），本地映射具体字体 + CJK 分支**（§89.2）。
8. **`meta.elements = source` 记住原图**，支持二次编辑。
9. ★ **ComfyArt 没有「图层组」数据模型**——它的"图层"就是 frame 里的多个 image shape。**我们若要真正的图层面板，得自己设计，不能声称抄自 ComfyArt。**

---

<a id="sec90"></a>
## 90. ★ 术语对照表：文档里的**简写/意译名** → 仓库里的**真实写法**

> 我做标识符核查时，另有一批名字 grep 零命中，但**内容是对的**，只是我在文档里做了缩写。
> 为避免后续误抄，这里列出真实写法。**以后引用请以右列为准。**

| 文档里写的 | 仓库里真实的 | 出处 |
|---|---|---|
| `documentSettings.meta.bg` | `editor.getDocumentSettings().meta.bg`（默认值 `editor.user.getIsDarkMode() ? DEFAULT_DARK_BG : DEFAULT_BG`） | `src/ui/CanvasTools.tsx:14` |
| `documentSettings.meta.brandKit` | `editor.getDocumentSettings().meta.brandKit`；写入用 `editor.updateDocumentSettings({ meta: { ...doc.meta, brandKit: kit ?? defaultKitId() } })` | `src/App.tsx:167`、`src/agent.ts:72`、`src/botCommands.ts:227,252` |
| `waitSeeked` | `new Promise((r) => v.addEventListener('seeked', r, { once: true }))` | `src/videoEdit.ts:90,197` |
| `topShape?.index ?? null` | `getIndexBetween(top ? editor.getShape(top)!.index : null, null)`；同级插入用 `getIndexBetween(below?.index ?? null, above?.index ?? null)` | `src/ui/LayersPanel.tsx:52,58` |
| `buildPromise` | `startJob(editor, holderId, build: Promise<Built>, opts, onError)`（参数名就是 `build`） | `src/jobs.ts:171-176` |
| `callId` | `call_id` | `bot/deepseek_harness.py:183-185`（见 §85.6） |
| `xxxGraph(...)` | 真实函数名共 22 个：`upscaleGraph` `creativeUpscaleGraph` `cutoutGraph` `cutoutVideoGraph` `upscaleVideoGraph` `editGraph` `eraseGraph` `expandGraph` `moveGraph` `styleGraph` `touchGraph` `anglesGraph` `layersGraph` `vGraph` `imageToVideoGraph` `motionGraph` `animateGraph` `extendGraph` `lipSyncGraph` `storyboardGraph` `samGraph` `samBoxesGraph` | `src/workflows.ts`（行号 15/31/53/67/82/88/96/105/113/127/143/172/180/200/218/272/292/304/316/336/358/393） |

### 85.1 `src/jobs.ts`（221 行）真实导出清单（§1.4 的准确版本）

```ts
export type MediaKind = 'image' | 'video' | 'audio' | 'model'
export interface Job { /* id, node, name, kind, placer?, extra? */ }
export interface Media { ref: ImageRef; src: string; w: number; h: number; kind: MediaKind }
export type ErrorSink = (msg: string) => void
export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))
export type Placer = (editor, holder: TLShape, media: Media[], job: Job, onError: ErrorSink) => Promise<void>
const GAP = 40; const active = new Set<string>(); const placers: Record<string, Placer> = {}
export interface JobResult { srcs: string[]; error?: string }
export const resultsAtom = atom<Record<string, JobResult>>('job results', {})
export function registerPlacer(name: string, placer: Placer)
export type MediaAsset = (TLImageAsset | TLVideoAsset) & { props: { src: string } }
export function mediaAsset(editor, shape): MediaAsset | null
export function fileSrc(editor, shape): string | null
export function jobOf(shape: TLShape): Job | null
export async function toMedia(ref: ImageRef, kind: MediaKind): Promise<Media>
export function placeMedia(editor, m: Media, name, x, y, w, parentId?): TLShapeId
export interface GenInfo { /* … */ }
const GEN_INPUTS = ['refs','base','strength','colors','refModes','refStrengths','refControls','lastFrame','scenes','videoMode','motion','speech','negative','seed','loras']
export function selectResults(editor, holder, ids: TLShapeId[])
export async function startJob(editor, holderId, build: Promise<Built>, opts: { name; kind?; placer?; extra? }, onError)
export function spawnHolder(editor, beside: TLShape, size?: [number, number]): TLShapeId
export function resumeJobs(editor, onError)
```
★ 关键机制（已核实）：
- `finish()` 开头 `if (active.has(job.id)) return; active.add(job.id)` → **同一 job 不会重复收尾**；`finally { active.delete(job.id) }`；
- `finish()` 失败时：`holder.meta.temp` → **删占位**；否则 `meta.job = null` + `meta.quiet = false` → **占位卡变成可重试的错误卡**；
- `selectResults`：`if (!holder.meta.quiet) editor.select(...ids)` → **quiet 任务不抢选中**；
- `startJob` 把 `job` 写进 `shape.meta.job` → **刷新后 `resumeJobs` 从 store 里捞回来继续等**（`editor.store.query.records('shape')`）；
- `spawnHolder` 找空位：`x = b.maxX + GAP` 起步，**最多循环 200 次**，每次撞到别的 shape 就把 `x = hit.maxX + GAP`；创建 `type: GENERATOR, meta: { temp: true }`；
- `defaultPlacer`：删占位 → 逐个 `placeMedia` 横向排 `x += w + GAP` → `meta: { gen: infoOf(holder, job) }`（★ **把生成参数存进结果 shape**）→ 整段 `editor.run(..., { history: 'ignore' })`；
- `GEN_INPUTS` 白名单 = **哪些生成参数会被存进 `meta.gen`**（16 项）。

### 85.2 核查方法（可复现）

- `verify_refs.py`：文档里所有 `path:line` 引用 → 文件存在性 + 行号范围。结果：**只剩 2 处已修正**（`shortcuts.ts`（正确路径 `src/ui/Shortcuts.tsx`）→ 实为 `src/ui/Shortcuts.tsx`；`spatialHash.ts` 不存在，已删）。
- `verify_ids.py` / `verify_by_section.py`：文档反引号里的 camelCase/snake_case/PascalCase 标识符（≥5 字符）逐个 grep 回源码语料（`src/**`、`api/**`、`bot/**`，排除 `node_modules`/`.git`/`docs-site`）。
  共检查 **1366 个标识符**；零命中的已逐一处理：
  - **真幻觉（已作废并更正）**：§81 全部（见 §88）、§38/§39（见 §89）、`AskBridge`/`ask_user_bridge`（见 §87/§88）；
  - **缩写/意译（已列对照表）**：本节 §90；
  - **我们仓库的名字**（不是 ComfyArt 的）：`firefly_projects` `firefly_generation` `firefly_agent_tools` `firefly_canvas_mcp` `layout_engine` `agent_extensions` `excel_importer` `smartCascadeEdgeState` `smartCascadePathForCtx` `dual_tab_sync_client` `layering_contract_browser_client` `local_edit` 及我们 8 个 canvas 工具名。

---

<a id="sec91"></a>
## 91. ★★★ 文档可信度声明与核查记录（务必先读这一节）

### 91.1 事实来源

- 全部结论来自**本地克隆的仓库源码逐行阅读**，不是来自 README、不是来自网页摘要、不是来自模型记忆。
- 克隆位置：`C:\Users\Administrator\AppData\Local\Temp\ref\ComfyArt`（bash 里 `/tmp/ref/ComfyArt`）。
- 覆盖范围：`src/**`（含 `src/ui/`、`src/shapes/`、`src/home/`、`src/styles/`、`src/locales/`）、`api/**`（含 `api/mcp_tools/`）、`bot/**`、`tests/**`。共 **186 个源文件**。
- 引用格式一律 `文件路径:行号`，可自己 grep 复核。

### 91.2 我做了四道自动核查（脚本已保留）

| 脚本 | 检查什么 | 结果 |
|---|---|---|
| `audit.py` | 186 个源文件里哪些**完全没在文档中出现** | 已补齐（覆盖到 100%） |
| `verify_refs.py` | 文档里每个 `path:line` → 文件是否存在 + 行号是否在范围内 | 仅 2 处错（`shortcuts.ts` 实为 `src/ui/Shortcuts.tsx`；`spatialHash.ts` 不存在）→ 已修 |
| `verify_ids.py` / `verify_by_section.py` | 文档反引号里的标识符（camelCase / snake_case / PascalCase，≥5 字符）逐个 grep 回源码语料 | 共 **1366 个标识符**；零命中者逐一处理（见 91.3） |
| `verify_code.py` | 132 个代码块里，有多少**一行原文都找不到** | 32 块；逐一复核后：绝大多数是**有意的改写/摘要**（已核对语义正确），虚构的已删除（见 91.3） |

脚本位置：`C:\Users\Administrator\.cache\opencode\{audit,verify_refs,verify_ids,verify_by_section,verify_code,fix_refs,purge81,fix_toc,fix_anchors}.py`。
★ 想复现：`python C:/Users/Administrator/.cache/opencode/verify_ids.py`（改 `DOC` 路径即可）。

### 91.3 ⚠️ 已确认并处理的错误（**不要重犯**）

| 位置 | 问题 | 处理 |
|---|---|---|
| **§81**（CLI 驱动） | 整节大部分为**虚构**：`--permission-mode bypassPermissions`、`--output-last-message`、`tool_timeout_sec=3600`、`default_tools_approval_mode`、`model_reasoning_effort`、`command_execution`、`output_item`、`startup_timeout_sec`、`wait_for_exit`、`usage_from_result`、`ProviderCaps(resume_via_stdin/images_via_tempfile/supports_ask_user)`、`user_message_json`、`send_user_text`、`AskBridge`、`ask_user_bridge` 等**全部零命中** | 正文已清空（只留虚构名单），真实事实重写在 **§88** |
| **§38 / §39**（layers.ts / elements.ts） | 把 `layers.ts` 写成「tldraw group + meta 做图层面板」，把 `elements.ts` 写成「选区/媒体类型工具」——**两者都是错的** | 正文标为作废，真实内容重写在 **§89** |
| §26.5 | 早先笔记里的 `RatioDial` / `RINGS`（比例盘 SVG 组件）不存在 | 已在原处标注更正（比例就是 chip 列表） |
| §59 等处 | `firefly_projects` / `firefly_generation` / `firefly_agent_tools` / `firefly_canvas_mcp` / `layout_engine` / `agent_extensions` / `excel_importer` / `smartCascadeEdgeState` / `smartCascadePathForCtx` / `dual_tab_sync_client` / `layering_contract_browser_client` / `local_edit` 及 8 个 `canvas.*` 工具名 | **这些是我们自己仓库的名字**，不是 ComfyArt 的，grep 零命中属正常 |
| 全文 | 若干**缩写/意译**名（`documentSettings.meta.bg`、`waitSeeked`、`topShape`、`buildPromise`、`xxxGraph`） | 语义正确，真实写法列在 **§90 对照表** |

### 91.4 ★ 已核实为「真」的高价值清单（可直接开工）

按价值排序，每条都有对应小节：

1. **`src/ui/FrameToolbar.tsx`（257 行）→ §80**：比例锁（`ratio` + `wMode/hMode` 三态）、自动布局、批量导出。**全仓库最值钱的一块。**
2. **`bot/_cli_common.py`（419 行）→ §62**：生产级 CLI 子进程驱动（双超时、进程树杀、stderr 64KB、失败也返 `resume_token`、`CliStreamParser` 去重、`normalize_usage` 别名表）。**与我们 ADR 0019（pi sidecar）直接对标。**
3. **`bot/local_llm.py`（425 行）→ §83**：一个完整自研 Agent 循环（工具循环、审批、超时）。
4. **`bot/_acp.py`（225）+ `bot/deepseek_harness.py`（318）+ `bot/deepseek_harness_runtime.py`（97）→ §84/§85/§86**：接「协议型 Agent」的完整模板，含 `PERMISSION_PRESET = "comfyart-mcp-only"`（`sandbox: danger-full-access` + `approval: never`）给第三方 Agent 造只准用画布工具的 profile。
5. **`api/bot_asks.py`（129）+ `api/mcp_tools/bot_tools.py`（191）→ §87**：Agent 反问 + 运行审批（`ASK_TIMEOUT_S=540`、`RUN_APPROVAL_WAIT_S=45`、`asyncio.shield` + `abandoned` + `(chat_id, prompt)` 迟到答案缓存）。
6. **`api/bot_turns.py`（277）→ §64**：流式落库节流（工具边界必存 + 3s 一次）、`session` 事件立即双写、`unverified_write_notice`（**防 Agent 只吹不做**）、每会话队列。
7. **`api/mcp_tools/canvas.py`（270）→ §61 / §11**：40 个画布工具的 schema、`WAIT_MAX_S=170` 异步契约、`_relay` 审批门控、`contextvars` 会话绑定。
8. **`src/elements.ts`（187）→ §89.2**：OCR + SAM 把位图设计稿拆成**可编辑文字层 + 物体层 + 干净背景**；`inkOf()` 像素统计反推字色/粗细、字号反解公式、旋转定位换算。
9. **`src/layers.ts`（276）→ §89.1**：三种拆图路径 + `grow()`（blur + alpha 二值化做 mask 膨胀）+ 拆完必须 inpaint 背景。
10. **`src/ui/` 全套工具条（§8.9–§8.19、§76–§80）**：浮动工具条定位（resize 时 `useSelectionRect` 置 null）、双轨写入（富文本 vs shape props）、图层面板、画布菜单复用 `useActions` 键位。
11. **`src/jobs.ts`（221）→ §1.4 / §85.1**：`meta.job` 落画布 → 刷新自动续接（`resumeJobs`）、`meta.temp`/`meta.quiet` 语义、`spawnHolder` 找空位、`GEN_INPUTS` 16 项白名单存 `meta.gen`。
12. **`src/workflows.ts`（407）→ §28**：22 个 `*Graph` 工厂函数 = 每个 AI 能力一个 ComfyUI graph 构造器。

### 91.5 仍未核实 / 需要你自己判断的

- **ComfyUI 端依赖版本**（`requirements.txt` / `nodes.py` 注册的节点）没有逐个跑起来验证；
- **模型清单**（哪些 checkpoint / LoRA / SAM / inpaint 模型被 graph 引用）只从 graph 的 class_type 推断，**没有核对模型文件是否随仓库分发**；
- **许可证**：§57 记录过 license 相关事实，但**商用抄代码前必须自己再读一遍 LICENSE**；
- **性能数据**：文档里没有任何 benchmark——所有「快/慢」描述都是代码结构推断，不是实测；
- **§59 的映射建议**是**我的判断**，不是 ComfyArt 的做法，抄之前按我们仓库现状复核。

### 91.6 使用建议

1. 想抄 UI/交互 → 先读 **§80**、§8.9–§8.19、§76–§79；
2. 想抄 Agent/CLI 接入 → 先读 **§62 → §64 → §87 → §88 → §83 → §84–§86**；
3. 想抄 MCP 工具面 → 先读 **§61 + §11 + §59.4**（我们只有 8 个工具 vs 它 40 个）；
4. 想抄图像能力 → 先读 **§89 + §28 + §19–§20**；
5. 任何一条要落地前，**先按 `文件:行号` 打开源码看一遍**——这是这份文档唯一的用法。

---

<a id="sec92"></a>
## 92. 剩余 UI 组件补全（§91.4 覆盖清单之外的 35 个文件）★★ 已逐行读完

> 覆盖：`EachToggle / AbilitiesPanel / NativeStyleBar / DoodleBar / UpscaleMenu / BatchRename / Markdown / StylePanel / EmbedToolbar / FontPicker / TaskDetails / CommentsList`。
> 其余（`home/*`、`icons.tsx`、`ChatMessage / AgentPanel / ReferencePanel / MemoryDialog / SkillsDialog / ScheduleDialog / BotSettings / ProjectHeader / NoticeCenter / LibraryPanel / SettingsMenu / InfoDialog / MockupPanel / ComposePanel / AudioDialog / CommentPanel / main.tsx`）为常规表单/弹窗装配，**没有新的架构性机制**，只列要点。

### 92.1 `ui/EachToggle.tsx`（31 行）★★ 「每个参考图各生成一份」的状态钩子

```ts
export function useEach() {
  const [on, setOn] = useState(false)
  const [shared, setShared] = useState<TLShapeId[]>([])
  return { on, setOn,
    isShared: (id) => shared.includes(id),
    toggleShared: (id) => setShared((s) => (s.includes(id) ? s.filter(x => x !== id) : [...s, id])),
    targets: (refs: RefShape[]) => (on ? refs.filter(r => !shared.includes(r.id)) : []),
    reset: () => { setOn(false); setShared([]) } }
}
```
- ★ **语义**：`on` = 开启「逐图生成」；`shared` = 被标记为「共享参考」的 shape（**不参与逐图展开**）；`targets(refs)` = 要各自生成一份的那批。
- `EachButton`（工具条按钮，`data-on`）+ `EachRole`（**在 `shared` / `perItem` 之间切换单个参考图的角色**，文案 `each.shared` / `each.perItem`）。
→ ★ **这套「同一批参考图，一部分共享、一部分各自生成」的 UI 状态机很干净，我们做批量改图可直接照搬。**

### 92.2 `ui/NativeStyleBar.tsx`（64 行）★★★ 给 tldraw 原生工具做样式条：**「选中 + 下一支」双写**

- `NATIVE = ['line','arrow','draw','geo']`；`TOOLS = ['line','arrow']`；
- `useValue` 里：`selected = shapes.length > 0 && shapes.every(s => NATIVE.includes(s.type))`；**没选中且当前工具不在 `TOOLS` → 返回 null（工具条不出现）**；
- ★ **读样式的关键技巧**：
  ```ts
  const styles = editor.getSharedStyles()
  const read = <T,>(p: StyleProp<T>) => { const v = styles.get(p); return v?.type === 'shared' ? v.value : editor.getStyleForNextShape(p) }
  ```
  → **选中项有共享样式就用它，否则用「下一支 shape 的样式」**（所以空画布上也能调默认样式）；
- ★ **写样式必须双写**：
  ```ts
  const set = <T,>(style, value) => editor.run(() => {
    if (state.selected) editor.setStyleForSelectedShapes(style, value)
    editor.setStyleForNextShapes(style, value)
  })
  ```
- 颜色色板从主题取：`editor.getCurrentTheme().colors[editor.getColorMode()]` + `getColorValue(colors, c, 'solid')` → **跟随明暗模式**；
- `COLORS` 13 色（含 `light-*`）；`SIZES = [['s',2],['m',4],['l',6],['xl',9]]`（**数字是圆点高度 px**）；`DASHES = solid/dashed/dotted/draw`。
→ ★ **我们做「原生工具样式条」时，`read`/`set` 这两个函数就是正确写法。**

### 92.3 `ui/DoodleBar.tsx`（52 行）★ 自绘笔刷条：**默认值走 atom、选中走 props，一套 `apply` 同时写两处**

- 显示条件：`tool !== DOODLE && !selected → null`；`selected` = 全选中的都是 `DOODLE`；
- ★ `apply(patch)`：`if (patch.color) doodleColorAtom.set(...)` + `if (patch.size) doodleSizeAtom.set(...)`（**更新默认值**）**并且** `if (selected) editor.updateShapes(selected.map(s => ({id, type: DOODLE, props: patch})))`（**更新已选**）；
- `shown = selected ? selected[0].props : { color, size }` → **控件显示值统一**；
- 数字输入 `min=1 max=200` + `onKeyDown={(e) => e.stopPropagation()}`（**防 tldraw 抢快捷键**）。

### 92.4 `ui/UpscaleMenu.tsx`（51 行）★★ 放大菜单：**「忠实」与「增强」两条路 + 目标长边换算**

- `TARGETS = [['2K',2048],['4K',4096],['8K',8192]]`；`long = Math.max(w,h)`；`k = target / long`；
- ★ **`disabled={target <= long}`** → 已经够大就不给放大；菜单项右侧显示 `Math.round(w*k)×Math.round(h*k)`（**先让用户看到实际结果尺寸**）；
- ★ 两条路：`detail === false` → `actions.upscale(editor, shape, k, onError)`；`detail === true` → `actions.creativeUpscale(editor, shape, k, style, strength/100, onError)`；
- `STYLES` 6 项（`''` / `realistic portrait photo, natural skin texture` / `anime style, clean line art` / `game CG artwork` / `artistic illustration` / `3D render`）；`strength` 滑杆 `min=10 max=70`（**默认 30**）。

### 92.5 `ui/BatchRename.tsx`（56 行）★★ 批量重命名：`prefix + pad + start` + 预览

- `renameAtom = atom<TLShapeId[] | null>('batch rename', null)`（**外部只要 set 这个 atom 就能打开**）；
- `FORMATS = [[1,'1, 2, 3'],[2,'01, 02, 03'],[3,'001, 002, 003']]`（pad 位数）；
- `name(i) = `${prefix}${String(start + i).padStart(pad, '0')}``；
- ★ **应用**：`editor.run(() => ids.forEach((id,i) => editor.updateShape({id, type: s.type, meta: {...s.meta, name: name(i)}})), { ignoreShapeLock: true })` → **`ignoreShapeLock: true` 才能改锁定 shape 的名字**；
- 预览只显前 3 个 + `…`。

### 92.6 `ui/Markdown.tsx`（54 行）★★★ **零依赖 Markdown 渲染器**（我们聊天/文档面板可直接搬）

- 支持：`**bold**`、`` `code` ``、`[text](url)`、``` ``` ``` 代码块、`|` 表格（**跳过 `|---|` 分隔行**）、`-`/`*`/`1.` 列表、`#`–`####` 标题（渲染成 `<p class="ca-md__h">`）；
- ★ 行内解析用**一个正则 + `split` 保留分隔符**：`const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g` → `text.split(INLINE).map(...)`；
- ★ 块级解析是**单个 `for` 循环 + 循环内 `i++` 吞多行**（代码块 `while (++i ...)`、表格 `for (; ...; i++)` 然后 `i--`）→ **无 AST、无依赖**；
- 链接一律 `target="_blank" rel="noreferrer"`。
→ ★ **我们若在用 react-markdown 之类，这个 54 行版本能砍掉整条依赖链。**

### 92.7 `ui/StylePanel.tsx`（89 行）★★ 风格迁移面板：**「参考图优先，否则选预设」**

- `STYLES` 10 个预设（`watercolor painting` / `oil painting` / `anime cel-shaded` / `glossy 3D render` / `pixel art` / `pencil sketch` / `flat vector illustration` / `cyberpunk neon` / `Chinese ink wash painting` / `claymation`）；
- ★ **互斥规则**：`{!ref && (...)}` → **一旦上传了风格参考图，预设 chip 整块隐藏**（避免两个来源冲突）；
- ★ **自动退出**：`useEffect(() => { if (active && !shape) exitMode() }, [active, shape])` → **取消选中就关面板**；
- ★ **Escape 用捕获阶段**：`document.addEventListener('keydown', onKey, true)` + `e.stopPropagation()`；
- 面板定位：`left = rect.r + GAP + size.w > host.width - EDGE ? Math.max(EDGE, rect.l - size.w - GAP) : rect.r + GAP`（**放右侧会溢出就翻到左侧**），`top = Math.min(Math.max(rect.t, EDGE), host.height - size.h - EDGE)`；
- 参考图来源用 `Popover` 二级切换（`'menu' | 'assets'`）→ 上传 或 `AssetGrid only="image"`。

### 92.8 `ui/EmbedToolbar.tsx`（93 行）★★★ 嵌入类 shape 的工具条 + **HTML 预览沙箱**

- `EMBEDS = [LOTTIE, MODEL, HTML, AUDIO]`；`getOnlySelectedShape()` 且类型命中才显示；
- ★★★ **HTML 预览必须沙箱**：`<iframe title=... sandbox="allow-scripts" srcDoc={html} />` → **只给 `allow-scripts`，不给 `allow-same-origin`**（脚本能跑但拿不到我们站点 origin）；
- ★ **3D 导出当前视角**：`snapshots.get(shape.id)` → `uploadImage(await snap(), `${p.name}-view.png`)` → `placeMedia(..., b.maxX + 40, b.y, b.w)`（**截图放到 shape 右边 40px**）；
- ★ **「交互模式」= 复用 tldraw 的 editing shape**：`editor.setEditingShape(shape.id)` / `editor.setEditingShape(null)`，`editing` 状态由 `editor.getEditingShapeId() === shape.id` 派生；**LOTTIE 与 AUDIO 不给交互按钮**；
- 下载分派：`HTML` → `download(new Blob([html], {type:'text/html'}), name.endsWith('.html') ? name : `${name}.html`)`；其它 → `downloadUrl(src, name)`；
- `htmlPreviewAtom = atom<string | null>('html preview', null)`（`null` = 关闭）。

### 92.9 `ui/FontPicker.tsx`（76 行）★★★ 字体选择器：**四个来源 + 「输入即自定义」+ 本地字体懒加载**

- `Source = 'all' | 'brand' | 'common' | 'local'`；`SHOWN = 200`（**列表上限，超出显示「还有 N 个」**）；
- ★ **池子合并去重**：`pool = source === 'all' ? [...new Set([...brand, ...COMMON_FONTS, ...(local ?? [])])] : lists[source]`；
- ★★★ **`custom` 机制**：`const custom = q.trim() && !pool.some(f => f.toLowerCase() === needle) ? q.trim() : null` → **输入的名字不在池子里，就把它当自定义字体名直接可用**（列表第一项「使用 “X”」）；`Enter` 优先选 `custom ?? matches[0]`；
- ★ **列表项用自身字体渲染**：`<span style={{ fontFamily: `"${f}"` }}>{f}</span>`；
- ★ **本地字体懒加载**：`canLoadLocalFonts && !local && (source === 'all' || source === 'local')` 才显示「加载本地字体」按钮 → `await loadLocalFonts(); setSource('local')`（**不是一进来就扫系统字体**）；
- `multiple` 模式：先 `picked` 暂存，底部「添加」才 `onPick(picked)`。

### 92.10 `ui/TaskDetails.tsx`（116 行）★★★ Agent 任务详情：**把 blocks 摊平成步骤 + 跨页定位产物**

- ★★★ **`reveal(editor, src)` 跨页找产物**：
  ```ts
  for (const page of editor.getPages())
    for (const id of editor.getPageShapeIds(page.id)) {
      if (fileSrc(editor, editor.getShape(id)) !== src) continue
      editor.setCurrentPage(page.id); editor.select(id)
      editor.zoomToSelection({ animation: { duration: 200 } }); return
    }
  ```
  → **按 src 反查 shape（跨所有页面），找到就切页 + 选中 + 缩放**；
- ★ **`steps(blocks)`**：先 `new Map(blocks.filter(b => b.type === 'tool_result' && b.id).map(b => [b.id, b]))`，再按 block 类型摊平成 `Step { label, detail, state, result }`：
  - `text` → 「回复」；`ask` → 「已回答 / 状态」；`notice` → level==='error' ? 错误 : 提示；
  - `task` → `b.error ? 失败 : b.srcs ? 完成 : 生成中`；
  - `tool_use` → **先按 `b.id` 查 result，查不到就退化成 `blocks[i+1]`**（★ 兼容没有 id 的旧数据）；`state = !r ? 进行中 : r.status==='error' ? 失败 : r.duration_ms != null ? `${(r.duration_ms/1000).toFixed(1)}s` : 完成`（**§64.3 服务端算的 duration_ms 在这里显示**）；`result` 只在 error 时显示；
- ★ **`Thumb`**：按扩展名分派 —— `AUDIO_FILES = ['flac','mp3','wav','ogg','opus','m4a']` → 音乐图标；`glb` → cube 图标；`['mp4','webm','mov']` → `<video muted preload="metadata">`；否则 `<img>`；旁边一个 `<a download>`，**文件名从 `?filename=` query 取**；
- ★ **用量展示**：`input_tokens + cache_read_input_tokens` 合并显示为「输入」，再 `output_tokens`、`cost_usd`（`$` 前缀）。

### 92.11 `ui/CommentsList.tsx`（71 行）★★★ 批注列表：**「一键把所有批注变成改图任务」**

- `allComments(editor) = editor.store.query.records('shape').get().filter(s => s.type === COMMENT)`（**跨页全量，不走当前页**）；
- 过滤 `all | open | done`（`c.props.done`）；
- `focus(c)`：`editor.getAncestorPageId(c.id)` → 切页 → `select` → `zoomToSelection({animation:{duration:200}})`；
- ★★★ **`runAll()`**：对每条未处理且有文字的批注 → `const box = editor.getShapePageBounds(c)` → **`if (!box || !imageUnder(editor, box)) continue`（★ 批注下面必须有图才处理）** → `touchEdit(editor, c, onError)` → `setDone(c, true)`；
- ★ **`summarize()`**：`agentModeAtom.set('agent')` → `commentNotes(editor, open)` 得 `{text, images}` → `sendToAgent(editor, t('commentsList.summarize', text), images, null)`；
- ★ **`commentsToChat(editor, open)`** → 把批注塞进聊天输入框（不直接执行）。
→ ★ **这是「人在画布上圈注 → Agent 逐条执行」的完整闭环，我们画布升级里最值得抄的协作机制之一。**

### 92.12 其余文件要点（一句话一条）

- `main.tsx`(21)：入口，挂 `HomeApp`/路由与全局样式；
- `home/HomeApp.tsx`(34) + `home/NotFound.tsx`(16)：项目列表外壳与 404；
- `home/Home.tsx`(180) + `home/Projects.tsx`(94)：项目卡片列表、新建/打开、`?kit=` 深链入口（§53.4）；
- `home/confirmDelete.ts`(12)：**统一的删除确认**（我们删除项目/画布应有同样一个共用函数）；
- `ui/icons.tsx`(117)：**全部图标是一个 `Icons` 对象里的内联 SVG**（无图标库依赖）；
- `ui/ChatMessage.tsx`(205)：blocks 渲染主入口（`answerAsk` / `outputsOf` / `toolLabel` / `toolName` 都来自 `src/bot.ts`）；
- `ui/AgentPanel.tsx`(202)：聊天面板装配（历史、输入、附件、provider 切换）；
- `ui/ReferencePanel.tsx`(199)：参考图管理（与 §26 `refModes` 配对）；
- `ui/MemoryDialog.tsx`(168) / `ui/SkillsDialog.tsx`(148) / `ui/ScheduleDialog.tsx`(118)：§45/§43/§44 的编辑界面；
- `ui/BotSettings.tsx`(89) / `ui/SettingsMenu.tsx`(54)：§56 的 setting key 全部在这里渲染；
- `ui/AbilitiesPanel.tsx`(62)：★★ **主动能力开关面板** —— `ABILITIES = [memory, review, skills]` × `ABILITY_SETTINGS[id]`、`DAILY_CHOICES = [1,3,5,10,20]`（写 `proactive-daily`）、`memory-enabled` 为主开关（**关掉时其它开关 `disabled`**）、`runsToday()` 显示「今天已跑 N / 上限 M」、未启用 Agent 时给「打开 Agent 设置」按钮；
- `ui/NoticeCenter.tsx`(83) / `ui/InfoDialog.tsx`(60) / `ui/LibraryPanel.tsx`(83) / `ui/MockupPanel.tsx`(68) / `ui/ComposePanel.tsx`(44) / `ui/AudioDialog.tsx`(91) / `ui/CommentPanel.tsx`(112) / `ui/ProjectHeader.tsx`(100)：对应 §44/§35/§19/§20/§42 的表单面板。

### 92.13 ★ 本节的可抄清单

1. **`NativeStyleBar` 的 `read`/`set` 双写模式**（§92.2）——tldraw 原生工具样式条的正确写法。
2. **`Markdown.tsx` 54 行零依赖渲染器**（§92.6）——直接搬。
3. **`FontPicker` 的 `custom` 机制 + 懒加载本地字体 + `SHOWN=200` 截断**（§92.9）。
4. **`TaskDetails.reveal()` 跨页按 src 定位 shape**（§92.10）——我们「点击产物跳到画布」直接可用。
5. **`CommentsList.runAll()` + `imageUnder()` 前置校验**（§92.11）——批注驱动改图的闭环。
6. **`useEach()` 的 shared/perItem 状态机**（§92.1）。
7. **`UpscaleMenu` 的 `disabled={target <= long}` + 显示实际输出尺寸**（§92.4）。
8. **`BatchRename` 的 `ignoreShapeLock: true`**（§92.5）。
9. **`EmbedToolbar` 的 `sandbox="allow-scripts"`（不给 same-origin）**（§92.8）——我们渲染用户 HTML 的安全底线。
10. **`AbilitiesPanel` 的主开关 + 从开关 `disabled` 联动 + 「今天已跑 N/上限」**（§92.12）。
