# #429 核心画布启动链实测（只测量）

2026-11-21；测量源码：`D:/ff-wt/ui-352-447-429`，基线 `1ec5e31`，采样时含 #352 看图工具的未提交后端改动。随后主线改写为 `7779c68`；已核对这两个基线的 tree 均为 `39181b768840d4bdff1abd1e74d41847f16014f4`。本票只交测量文档，不实施优化。以下全部数字来自本轮 JSON，**没有使用旧票评论的 timing 充当当前实测**。

原始证据根目录：`D:/ff-qa/352-447-429/429/`。下文 cycle、JSON、脚本路径均相对于这个证据根目录，而非仓库文档目录。

## 结论

- 三个干净的“新服务进程 + 新页面”样本，首次 canvas **ResourceTiming.startTime = 697.1 / 653.3 / 671.4 ms**；同页 reload 为 **372.9 / 375.3 / 391.2 ms**。中位数分别 **671.4 / 375.3 ms**。这不是浏览器/操作系统全冷启动基准。
- canvas 请求自身从 startTime 到 responseEnd 仅 **13.6 / 8.7 / 4.7 ms**（冷），服务端 ASGI 到响应头 **2.545 / 2.282 / 2.343 ms**。本夹具中“请求发得晚”与“canvas 服务端读得慢”是不同问题，后者不能解释前面的约 0.65–0.70 秒。
- 已证实的依赖关系：**页面 load 后才进入 onload；onload 先 await 模板、偏好、配置/模型规格、素材，之后才调用 loadCanvas**。Network initiator 给出实际调用栈，不仅是请求的先后关系。干净冷样本 loadEventStart 为 **531.6 / 470.2 / 498.1 ms**，再过 **165.5 / 183.1 / 173.3 ms**才启动 canvas fetch。
- 当前 Python 环境中，先决 API 的第一次请求还承担 **FastAPI included-router 路由状态/依赖模型的惰性构建**。独立 cProfile 补充实验直接观察到了这一执行栈；不能把所有首次模板耗时都归因于磁盘或 JSON。
- 未取得可隔离到本页的主线程 trace/CPU profile，**不能精确给出每份 JS 的 parse/compile/初始化耗时，不能将资源 responseEnd→load 的间隔全算解析**。保留了原生 V8CompileDuration、ScriptDuration、布局计数及 Long Animation Frame 证据，局限见下文。

## 环境与隔离口径

- Windows 11 `10.0.26200`，Python `3.12.10`；Tabbit 官方 launcher，任务独占短名 **ff429-startup**，UA `Chrome/156.0.0.0`，固定视口 **1600×1000、DPR 1**。
- 每个 cycle 的服务器/ProjectStore 都使用最小白名单环境；`FIREFLY_RUNTIME_ROOT=<证据根目录>/<cycle>/runtime`、`FIREFLY_PROJECTS_ROOT=<证据根目录>/<cycle>/projects`。HOME/USERPROFILE→home，APPDATA→appdata，LOCALAPPDATA→localappdata，TEMP/TMP→temp，均在同一 cycle 目录下。没有继承凭据环境变量，没有导入真实宿主配置或调用模型。
- 仅监听 **127.0.0.1:3203**。没有操作 3000、3201、用户项目或已有用户页面。官方 Tabbit launcher 继续使用已安装浏览器的正常路由环境；没有改变共享浏览器 HOME、profile、缓存或用户配置。
- 固定 fixture 名 **e2e-tabbit-ff429-startup**，只有一个自建 smart-prompt 节点（固定坐标/尺寸/文本），无媒体、Agent 历史或生成任务。每次冷启动重新用独立 ProjectStore 建立同构项目；UUID 不同。同页 reload 保持同一 fixture。
- 每次冷启动是新 Python 服务进程、全新运行根/项目根、新拥有页面。服务 ready 后才导航；没有全局清浏览器缓存、重启共享浏览器或清 OS 文件缓存；3203 origin 的浏览器 localStorage 也没有在 cycle 间清除（仅本任务 fixture，项目 UUID 每次不同）。服务启动产生新的静态资源版本，冷样本的 JS 实际收到 HTTP 200；暖样本是正常 reload/revalidation，并非所有资源都从内存返回。
- 实际依赖：FastAPI **0.141.1**、Starlette **1.6.0**、Pydantic **2.13.5**、Uvicorn **0.52.4**；**不等于**源码 `requirements.lock` 中的 0.163.0 / 1.10.0 / 2.14.3 / 0.54.0。未安装或修改依赖。框架内部结论限定到这套实际环境，不能直接外推发行包。
- `sources-before.json` / `sources-after.json` 校验 smart-canvas.html 与 37 个顶层 JS 文件的 SHA-256 一致；不是对所有仓库文件的 clean 证明。读取 worktree 的 `git -C ... status` 被子代理边界拒绝，未换写法重试，基线号采用委派提供值。

## 首次 canvas 请求：起点、实际发送、响应分列

单位 ms，均相对各自 navigation timeOrigin；接口实际为 **GET /api/canvases/{id}**，不是 `/api/project-library/{id}/canvas`。

| 样本 | loadEventStart | fetch startTime | requestStart | responseStart | responseEnd | ASGI 到头 / 到尾 |
|---|---:|---:|---:|---:|---:|---:|
| cold2 冷 | 531.6 | 697.1 | 698.2 | 701.4 | 710.7 | 2.545 / 2.953 |
| cold2 reload | 248.7 | 372.9 | 374.2 | 377.3 | 378.3 | 2.410 / 2.768 |
| cold4 冷 | 470.2 | 653.3 | 658.3 | 661.2 | 662.0 | 2.282 / 2.605 |
| cold4 reload | 224.0 | 375.3 | 377.1 | 379.7 | 380.5 | 2.045 / 2.349 |
| repro1 冷 | 498.1 | 671.4 | 672.6 | 675.4 | 676.1 | 2.343 / 2.641 |
| repro1 reload | 253.8 | 391.2 | 395.9 | 398.6 | 399.5 | 2.059 / 2.419 |

`startTime` 是 fetch 开始/调度的资源时间，**不等于实际 HTTP 发送**；`requestStart` 才是请求发送时间。responseStart 是浏览器收到首字节，responseEnd 是接收完成，均不是 paint。服务端时间由仓库外 ASGI wrapper 以 perf_counter_ns 记录：进入 app→response.start、进入 app→最后一个 body 完成 await send，包含应用/中间件/调度，不包含客户端之前的排队，也不代表对端已接收。记录在内存中，退出时落盘，没有每请求同步日志写入本计时区间。

额外的服务启动时间（seed/import/startup/ready，不包含在上述导航计时里）：cold2/cold4/repro1 分别 **781.4 / 813.7 / 821.0 ms**。这不是桌面启动器或用户点击程序后的完整可见延迟。

## 代码与 Network initiator 证据：请求为什么晚

1. `static/smart-canvas.html:23–29,716–758`：classic script 标签没有 async/defer；主脚本在第 757 行。39 份实际 JS 资源包括 i18n 动态注入的 6 份词典/核心文件。
2. `static/js/smart-canvas.js:17553–17568`：onload 顺序执行：同步本地 presets/groups/overrides → `await loadPromptTemplates()` → i18n.apply → `await loadAutosavePreferences()` → connectAssetLibrarySyncSocket → `await loadConfig()` → `await loadAssetLibrary()` → `await loadCanvas()` → render。
3. `loadPromptTemplates:4188–4209` 请求 `/api/prompt-libraries`；`loadAutosavePreferences:6923–6929` await 共用偏好 promise。`global-preferences.js:5–32` 已在脚本顶层主动加载偏好，**本轮偏好响应都早于 load，不能误算成 onload 后又顺序发一遍偏好请求**。
4. `loadConfig:4111–4128` 内部先请求 `/api/config`，再请求 `/api/model-specs`，之后同步更新参数面板。
5. `loadAssetLibrary:4949–4966` 内部 **Promise.all 并行**请求 asset-library、local-assets、项目 media；三者不是串行请求。
6. `loadCanvas:6806–6809` 发出 `/api/canvases/{id}`；服务端 `app/routers/api_canvases.py:335–337` 调用 load_canvas。
7. 全部成功样本的 CDP Network.requestWillBeSent.initiator.stack 均指向 **loadCanvas（0-based 6808 → 源码 6809）← window.onload（17563 → 17564）**，上方还可见 Tabbit inspector 的 fetch wrapper。源码 await 合同与真实 initiator 合在一起，才是依赖链证据。

以 cold2 为例（startTime→responseEnd）：

| 阶段 | 时间 ms |
|---|---:|
| loadEventStart | 531.6 |
| prompt-libraries | 532.0→611.6 |
| config | 650.7→665.1 |
| model-specs | 666.7→675.3 |
| asset-library / local-assets / project media 并行包络 | 684.5→695.2 |
| canvas | 697.1→710.7 |

因此 load→canvas 的 165.5 ms 可按**观察边界**分成：0.4（到模板开始）+79.6（模板 fetch）+39.1（模板结束→config 开始）+16.0（config→specs 开始）+17.8（specs→素材开始）+10.7（素材包络）+1.9（素材结束→canvas）。这些是相邻边界，不是逐函数 CPU 计时；39.1 等空隙可能包含 JS、promise/消息调度、样式布局和观测影响，不能仅凭时序指定某个函数为根因。所有样本的完整边界在 `summary.json`。

## 静态脚本：排队、下载与执行不能混为一谈

`smart-canvas.js` 源文件/冷响应体 **978,906 bytes**：

| 干净冷样本 | fetch startTime | requestStart | responseStart | responseEnd | start→request / request→end |
|---|---:|---:|---:|---:|---:|
| cold2 | 32.6 | 264.1 | 268.5 | 282.1 | 231.5 / 18.0 |
| cold4 | 33.8 | 236.1 | 240.7 | 254.2 | 202.3 / 18.1 |
| repro1 | 39.9 | 253.8 | 258.1 | 282.6 | 213.9 / 28.8 |

所以“主脚本花约 220–250 ms 下载”不准确：其中大段在 requestStart 之前。Network/Resource Timing 显示 HTTP/1.1、静态资源并发/排队；没有通过改变连接数或资源顺序去证明每毫秒的队列原因。

每次冷导航 39 个 JS 资源，合计 decodedBodySize **2,151,105 B**、transferSize **2,162,805 B**。暖 reload 仍有39个，合计 transferSize **189,364 B**：CDP 中主静态脚本为 **304**，动态 i18n 为 **200**。`static/js/i18n.js:4–14` 用 Date.now 拼版本并 document.write，因此 reload 并不是全部 JS 零传输。ResourceTiming.responseStatus 对 revalidated 文件显示有效 200，而 CDP Network.responseReceived.status 为 304；报告缓存状态采用 CDP，原始两套数据都保留。不能把暖 decodedBodySize 的小数值当成浏览器只执行/解析了那么多源码。

页面在脚本接收完成之后还要执行、处理样式/布局及其它 load 依赖。主脚本 responseEnd 不是执行完成，更不是 paint。

原生 CDP Performance 计数（**截至 DOM 检查后 networkidle 的快照，包含 canvas 返回后的工作，不是精确 pre-canvas 分段**）：

| 指标 ms | cold2 冷 / 暖 | cold4 冷 / 暖 | repro1 冷 / 暖 |
|---|---:|---:|---:|
| ScriptDuration | 91.426 / 58.488 | 80.484 / 48.464 | 89.273 / 54.364 |
| V8CompileDuration | 1.953 / 0.359 | 1.786 / 0.326 | 1.928 / 0.404 |
| LayoutDuration | 165.687 / 23.089 | 133.529 / 28.628 | 145.894 / 25.059 |
| RecalcStyleDuration | 57.804 / 30.590 | 49.928 / 26.086 | 52.291 / 32.766 |

计数在导航后重置，**没有将 warm-after 减 cold-before 得到错误负数**。V8CompileDuration 只按该原生命名解释，不当成完整 parse 时长。异步请求开始事件触发的额外 Performance 快照也保留了自己的 Timestamp：例如 repro1 冷快照为673.592 ms，落后 canvas CDP 起点2.177 ms；cold4 冷落后20.041 ms且已在响应后，不能冒充“发送瞬间”计数。

### 补充的主线程关联与限制

- PerformanceObserver longtask/paint，以及 cold3/cold4/repro1 的 Long Animation Frame 原始条目都已保留。部分长任务归因仅为 `smartSettingsFrame` 的同源空 iframe，长帧的 scripts 数组也常为空；不能据此说设置页真实加载或某个 app 函数占了整个长帧。
- **仅供解释的 cold3** 有可定位 LoAF：主 smart-canvas classic-script 条目 9.9 ms；global-preferences.js 的 Response.json.then continuation（sourceCharPosition 445→约第10行）41.8 ms，其中 forcedStyleAndLayoutDuration 34.9 ms；smart-canvas.js 的 restoreDynamicParamsScroll 内 apply（sourceCharPosition149756→第2944行；映射保留源码CRLF并按UTF-16位置计数）7.6 ms，其中 forced style/layout7.5 ms。它证明存在同步布局工作，不证明这就是全部启动瓶颈。
- 本机 Tabbit owned-page CDP 的 `Profiler.enable` 返回 method-not-found；Schema.getDomains 也没有实现，Tracing.getCategories 返回空数组。**没有收集浏览器全局 trace**：无法事先限定 renderer/process、也无法保证不记录/干扰其他用户页或已有 trace。没有关闭别人浏览器或绕过共享锁。故无法交付可信的逐脚本 parse/execute 主线程 trace。

## 服务端先决 API 的真实耗时与补充根因证据

干净冷样本中 `/api/prompt-libraries` 的 ASGI 到响应头为 **71.885 / 76.552 / 73.236 ms**；同页 reload 为 **1.934 / 1.681 / 1.760 ms**。偏好 GET 首次为 **40.250 / 46.546 / 42.513 ms**，reload 为 **1.192 / 0.907 / 1.274 ms**。模板先决 API 确实有服务端冷路径，不能只归因于网络调度。

独立 `profile1` 新服务/新页面实验只给这两个请求加 cProfile（不改 repo，不加入延时；**有 profiler 开销，排除在主表统计之外**）：

| profile1 请求 | ASGI 到响应头 | _populate_api_route_state 次数 / 累积 ms | effective_candidates 累积 ms | 业务处理累积 ms |
|---|---:|---:|---:|---:|
| 首次 preferences | 106.874 | 130 / 92.991 | 97.067 | load_global_preferences 0.238 |
| 首次 prompt-libraries | 77.450 | 101 / 64.288 | 66.505 | get_prompt_libraries 6.101 |
| reload preferences | 2.806 | 0 | 0.074 | load_global_preferences 0.276 |
| reload prompt-libraries | 4.174 | 0 | 0.120 | get_prompt_libraries 0.715 |

这些是**嵌套 cumulative 时长，不能相加**。首次模板写默认 JSON 的 write_json_atomic 累积只有 **2.973 ms**，不足以解释该 profile 中整个77 ms。记录包括 ASGI middleware 和该事件循环线程在 await 期间的工作，不声称是线程独占 CPU。

代码证据在实际安装的 `C:/Users/Administrator/AppData/Local/Programs/Python/Python312/Lib/site-packages/fastapi/routing.py`：`_IncludedRouter.effective_candidates:1601` 检查缓存版本，缺失时逐项 `_build_effective_context:1653`，调用 `from_api_route:1426`→`_populate_api_route_state:961`，构造依赖与 Pydantic fields。版本命中后返回已有 candidates。四份原始 profile 与该文件 SHA-256/依赖版本保存在 `server-profile-summary.json` 及 `profile1/server-profile-{1..4}.json`。LoAF 源码位置另由 `map_loaf_sources.py` 重算到 `loaf-source-locations.json`。**这支持本机冷先决请求确实执行框架惰性构建，不等于证明主表每个71–77 ms都完全来自它，更不能外推 lock 的另一版本。**

## 不混淆 paint 与 DOM

真正的浏览器 PaintTiming/FCP（仅整页首次内容，不能说是画布节点首绘）：cold2/cold4/repro1 冷 **468 / 432 / 448 ms**，reload **244 / 200 / 236 ms**；都早于 canvas 请求。显然这些 FCP 只能覆盖页面壳等先出现的内容。

固定提示词 textbox 的 Playwright visible/value 等待完成观察时刻：冷 **802.8 / 734.1 / 718.8 ms**，暖 **442.7 / 438.0 / 454.9 ms**。这是 DOM/可见性与多次自动化往返之后的观察，**不是 paint，不是节点首次 DOM 插入时刻，也不是视觉可用性精确上界测量**。没有获取可信的“canvas 内容首次实际呈现”指标，因此没有为它编造性能目标。

## 失效/排除样本与假设闭环

- cold1 首次运行误等不存在的 project-library canvas 路径而超时；页面其实正常加载。保留 `failed-waiter-observed.json` 和失败 receipt，**不计入统计**。修正后反馈环校验真实 canvas GET200、ResourceTiming 起点/响应及固定 textbox 值；主表6个导航全部通过。
- cold3 虽为新服务、新页面，但本任务上一个 cold2 页面在服务器切换时尚未关闭，向新的3203实例发了一次**旧自建 fixture 的 meta GET404**，位于本次 HTML 之前。这可能预热框架路由，违背“首个 API 冷”的口径。因此 cold3 冷667.2→672.9 ms、reload370.3→381.0 ms完整保留，**整对排除主统计**。这也是其首次偏好/模板服务时间显著较短的一个线索，但不单靠这个先后关系断言因果。最终可复现脚本已在测量结束关闭自己页面，后续三个干净实例无旧 fixture 请求。
- cold3 初次试 CPU Profiler 在导航前报 method-not-found；随后关闭了那张自己的 blank 页并重新测量，不混用失败执行的计数。
- 采到 loop 后、补充 profiling 前记录的排名假设：①onload先决依赖推迟发送；②静态资源及初始化阻塞load；③canvas服务端读取慢；④主线程布局/调度/观测造成请求间隙。结果：①有源码+initiator直接支持；②资源/布局计数支持“有成本”但未完成逐脚本归因；③在本夹具不支持；④观察到布局及间隙，但未做反事实干预，未定量归因。额外 server profile 识别了先决API里的框架冷构建。按“仅测量”要求不执行 skill 的修复/回归阶段。

## 原始证据、复现与清理

证据根目录内：
- `<cycle>/cold.json,warm.json`：完整 Navigation/Resource/Paint Timing、Observer、CDP Network initiator/response/finish、Performance snapshots、UA/视口/fixture；`network-all.json` 是 Network 原始事件（去除了请求/响应 headers 与 postData，避免敏感数据）。
- `<cycle>/fixture.json,server-timing.json,server.log`：隔离环境、ready时间、服务器边界计时、日志。`observed.json` 只是有截断的辅助 DOM 快照，不用于完整性或 paint 主张。
- `summary.json`：8个导航的重算结果；primary为 cold2/cold4/repro1，cold3明确标记排除。profile1另列。
- `*.receipt.json`、`capabilities.json`：Tabbit 成败/能力证据；`reproduce-finish.json`、`cleanup.json`：结束与回收证明。

已实际运行并通过的完整复现入口（需要官方 launcher 可用；cycle名称不能复用）：

```text
python -B D:/ff-qa/352-447-429/429/reproduce.py repro1
python -B D:/ff-qa/352-447-429/429/reproduce.py profile1
python -B D:/ff-qa/352-447-429/429/analyze.py
python -B D:/ff-qa/352-447-429/429/profile_analysis.py
python -B D:/ff-qa/352-447-429/429/verify_cleanup.py
```

再次取样应使用新名称，如 `reproduce.py repro2 repro3 repro4`；以 `profile` 开头的新名称（如 `profile2`）启用同一服务端 cProfile 补充实验，不纳入主样本统计。`measure.js` 不调用内部应用函数、不改业务 state；只有导航/reload与只读性能观察。`cli.py` 通过稳定 launcher + UTF-8脚本文件 + CMD stdin重定向调用，Windows 不经过 PowerShell 字符串改写。已全文读取 Tabbit recovery.md，但安装版本缺少 SKILL.md 所链接的 windows-multiline-invocation 锚点；此调用方式已实跑成功。

所有自己启动的6个服务进程均已退出，所有6套测试项目根已删除；生成的runtime/home/appdata/temp树也在导出证据后删除。3203不再接受连接；没有按端口/名字杀进程。任务创建页全部关闭，原始15个用户页未被获取/操作，最后一次finish返回 **finished:true、forcedCleanup:false、issues:[]**。第一次finish所列旧fixture meta错误属于上述已披露的cold3污染，不是用户项目请求。

本票没有产品改动，不把整批的 pytest/typecheck/build 检查包装成性能验证。已经验证的是实际浏览器反馈环、原始样本一致性/排除口径、server profile、复现入口及清理。未完成项是准确的逐脚本解析/执行trace、真实canvas内容paint和锁定依赖环境复测；本报告不提出优化或承诺延迟能降低多少。
