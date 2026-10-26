# 20. 画布前端继续拆分：以可测性为目标，不以反向引用数为目标

日期：2026-10-19
状态：已接受

关联：#154（拆分起点）、#173（核心下沉层，建立棘轮守卫）、#162（已否决 ES 模块改造）、
#194（模块独立单测）、`docs/adr/0001-smart-canvas-as-product-core.md`。

## 背景

#154 起把画布代码从 `static/js/smart-canvas.js` 往外拆，#173 又建了「核心下沉层」
`static/js/canvas-core.js`，并用 `tests/test_canvas_core_layer.py` 锁住每个模块对
`smart-canvas.js` 的反向引用处数（只许降不许升）。

反向引用从 1836 降到 606。但**这个指标开始误导**：它把「模块调用共享服务」和
「模块寄居在核心里的自己的代码」算成同一种东西，于是「继续降 606」听起来像进展，
实际可能只是在改数字。

`tests/test_canvas_core_layer.py:62` 早就记下这个待决项：

> 剩下的主要是 render / scheduleSave / shell / world 这类行为枢纽与 DOM 节点，
> 它们本来就该被模块调用；要继续降，得先判明哪些该注入、哪些该留在核心。

本 ADR 用 `tools/canvas_coupling_report.py`（本决策同时签进仓库）把构成测清楚，
据此定拆分策略。

## 实测构成（`python tools/canvas_coupling_report.py`，总数 606，与棘轮上限之和完全一致）

按核心里的声明形态：

| 形态 | 处数 | 占比 |
|---|---|---|
| `function` | 505 | 83.3% |
| `const` | 37 | 6.1% |
| `dom-ref` | 34 | 5.6% |
| `let` | 30 | 5.0% |

按「这个核心名字被几个模块引用」：

| | 名字数 | 处数 | 占比 |
|---|---|---|---|
| 只被 1 个模块引用 | 138 | 138 | 22.8% |
| 被 ≥2 个模块引用 | 39 | 468 | 77.2% |

按「这个核心名字被几个模块引用」（`--names` 输出；这是比声明形态更可行动的切分）：

| | 名字数 | 处数 | 占比 |
|---|---|---|---|
| 只被 1 个模块引用（该模块自己的代码寄居在核心） | 161 | 269 | 44.4% |
| 被 ≥2 个模块引用（真共享服务） | 39 | 337 | 55.6% |

只被 1 个模块引用的 161 个名字里：

- **71 个核心自己不调用** → 可以纯搬家，不造新依赖，共 122 处。分布：
  `canvas-media-history.js` 21、`canvas-image-editor.js` 19、`canvas-generation-tasks.js` 10、
  `canvas-generation-cascade.js` 7、`canvas-connections.js` 6、`canvas-node-ops.js` 6、
  `canvas-agent-tui.js` 2。
- **90 个核心自己也调用** → 搬走会造出「核心 → 模块」的反向依赖。
  核心内调用最多的：`renderAssetLibrary` 20、`activeComposerNode` 17、`renderDynamicParams` 14、
  `screenToWorld` 13、`selectedNodeIds` 9、`smartPreviewImgHtml` 9、`positionComposerForNode` 9、
  `savePromptDraftForCurrent` 9。

被 ≥2 个模块引用的 39 个共享服务里，`render` 62 处、`scheduleSave` 46 处，两个就占 108 处（17.8%）。
这 39 个已全部在 `CATEGORY_BY_NAME` 里人工分类（`hub` 327 处 / `state` 58 / `dom` 45 / `util` 2）；
剩下 174 处未分类的全是 solo 名字——它们不需要人工分类，`scope` 字段已经客观判出归属。

## 决策

### 目标重述：不是「降 606」，是「每个模块能独立测」+「核心不反向依赖模块」

理由：#194 已实测证明**独立可测性取决于模块要桩多少个不同的外部名字，不取决于引用处数**。
`canvas-undo.js` 29 个依赖名 → 15 个手写桩就能测，且有真实可观察行为；
`canvas-media-history.js` 210 个依赖名 → 就算处数降一半也测不动。
所以拿 606 当 KPI 会优化错的东西。

### 轨道 1（最高杠杆）：把 39 个共享服务变成显式接口

`canvas-core.js` 暴露一个明确的接口对象（例如 `canvasCore`），模块调
`canvasCore.render()` 而不是抓全局 `render`。

39 个自由全局名字 → 1 个接口对象。单测桩数量因此与「名字数」解耦：
以前每个自由名字要一个桩，现在一个接口对象就够。

**关键：这个改进在现有棘轮上几乎不可见**（还是 606 处，只是写法变成 `canvasCore.render`）。
所以必须同时给守卫加一个新指标：**自由全局引用数 vs 接口引用数**，
并让前者只许降。否则做完了看不出进展，会被误判为白干。

### 轨道 2（先做，建立机制）：搬走 71 个安全寄居名字

核心不调用、只被一个模块用 → 纯搬家，行为不变，不造新依赖。共 122 处（20.1%）。
**价值不在数字，在于建立「搬家 + 守卫验证」的流程和信心**，为轨道 3 铺路。

搬家顺序必须是「先确认目标模块在引用方之前加载」——classic script 的顶层
`function` 声明在该脚本执行时才建立全局绑定，跨脚本调用发生在运行期，
所以只要定义方脚本已加载就安全；但**加载期就使用**的情况要先查。

### 轨道 3（真正的结构债，长期）：解掉「核心也调用」的寄居函数

这些说明**核心不是叶子层**：它调用着本该属于模块的功能。
逐个判定，不能批量做：

- 是核心职责 → 留在核心（并把名字进 `CATEGORY_BY_NAME` 标 `hub`）；
- 是模块职责 → 先让核心改走事件或接口，再搬。

#### 轨道 3 的前提被实测推翻（2026-10-19，轨道 2 进行中）

本 ADR 初稿写的是「90 个 / 206 处」。**错**，两个原因：

1. 当时把「核心内部调用数 > 0」等同于「核心也调用」，没排除**模块自己调用自己定义的东西**；
2. 轨道 2 已把 40 个名字搬走，数字在变。

轨道 2 进行中重测（总数 542 / 161 名字）：

| 类 | 名字 | 引用 | 占比 |
|---|---|---|---|
| 真共享服务（≥2 模块引用，核心不调用） | 40 | 352 | 64.9% |
| 真共享服务（核心也调用） | 1 | 1 | 0.2% |
| 合法寄居（单 owner，核心不调用） | 31 | 49 | 9.0% |
| **真结构债（单 owner，核心也调用）** | **17** | **40** | **7.4%** |
| 已搬走 | 40 | 82 | 15.1% |
| 未分类 | 32 | 18 | 3.3% |

**结论：轨道 3 的规模是 17 个名字 / 40 处，不是 90 / 206。** 真结构债只占 7.4%。

#### 但上面的 17 / 40 无法从工具重现，轨道 3 的实际范围已重测（2026-10-21）

后来人（包括我）拿着「17 / 40」去排期时发现：**跑 `python tools/canvas_coupling_report.py` 得不出这两个数**。
查下去：

- `coupling()` / `summarize()` 的 `owners` 语义是「某模块引用了**声明在核心里**的名字」——方
  向刚好跟轨道 3 相反。它的 `blocked`（当前 108 个名字）全部 `declared_in_core_as: function`，
  即「核心自己的函数恰好被 1 个模块用了一次」——**#202 已经据此判完并关闭了轨道 3**
  （结论：全部留在核心，搬走会造出核心→模块正向依赖）。
- 本 ADR 那句「17 个已逐个列在 #202 评论里」是**空指针**：#202 评论里只有 97 / 160 和
  40 / 352 两组数，从没有 17 / 40。这两个数来自当时一个已丢的临时脚本。

重测（新增 `tools/canvas_coupling_report.py:reverse_coupling()`，已钉进
`tests/test_canvas_coupling_report.py:test_reverse_coupling_is_measurable_and_pinned`）：

| 口径 | 名字 | 核心侧引用处 |
|---|---|---|
| 核心引用模块声明的名字（反向总面） | **237** | **941** |
| 其中核心用得比 owner 模块自己还多（「搬早了」信号） | **168** | **791** |

（从 231/933/163/784 上漂：全部来自 #223 新建的 `canvas-download.js`——下载层从 smart-canvas.js
抽成模块后，核心改为按名调它的 4 个函数（downloadCanvasArchive / canvasReceiveBlob /
canvasSaveBlobAsFile / canvasDownloadSkipSummary）。这是 #154 要求的「核心调模块」方向，不是新债，
但口径就是口径，上漂就必须同步这里。）

（再到 236/940/167/790：#224 让素材抽屉的名字格改调 `canvas-media-history.js` 的
`markedItemDisplayName`（smart-canvas.js 两处：可见文本 + title）。同样是「核心调模块」方向，
目的是避免两处显示名逻辑分叉（回填后素材名全是 generated-N 这类默认名，直接拼 item.name 会一排重复）。）

（再到 237/941/168/791：#228 给已标记面板加「下载记录」入口，核心调 `canvas-download.js` 的
`canvasDownloadRecordsHtml`——记录存在下载模块那一侧，核心只负责把面板打开。方向不变。）

owner 分布：`canvas-media-history.js` 59、`canvas-image-editor.js` 54、`canvas-connections.js` 21、
`canvas-layout-utils.js` 16、`canvas-undo.js` 15、`canvas-generation-cascade.js` 15、
`canvas-node-ops.js` 13、`canvas-node-types.js` 8、`canvas-agent-tui.js` 7、`canvas-generation-tasks.js` 24、
`canvas-download.js` 5。
（`canvas-core.js` 已排除：核心调它是合法下层依赖。）

**两个口径都不是「债」的判定，只是证据。** 核心是 UI 编排层，调模块功能本身合法（正常组合关系）。
典型误判例：`NODE_TYPE_*` 常量核心用 24-28 处、owner 模块用 2 处——常量本来就是给全仓用的，
不是债。所以 167 这个数不能直接当工作量。

**轨道 3 现行结论：保持 #202 的判定（不搬）。** 若将来要重开，必须先逐名过一遍上表的
 `core_dominant_rows`，并区分「常量/工具——应共享」与「行为——可能真搬错了」。

其中 17 个有 12 个（27 处）集中在 `canvas-media-history.js`，而该模块有 210 个外部依赖名、
ADR 已判定不该写单测——**所以轨道 3 不能排在补测前面**。
（这句里的 17 / 12 / 27 同属下面被推翻的那组数，保留只为记录当时的推理。）

**修订后的顺序**：轨道 1（39-40 个共享服务显式化，占 65%）→ 轨道 4（补测）→ 轨道 3（17 个）。
轨道 3 的 17 个已逐个列在 #202 评论里，含建议分类。
（这句指针是空的——见下一节。#202 评论里没有 17 这个数字，而 #202 本身已关闭。）

### 轨道 1 完成后的实测结果：口径必须分清（2026-10-19）

**接口化不改变反向引用总数。** 调用还在，只是从裸全局名改成走 `canvasCore` 接口。
所以拿「反向引用总数」衡量轨道 1 会得到 0 收益的错误结论——这个坑我踩过一次。

`tools/canvas_coupling_report.py` 实测（`FREE_GLOBAL_REFERENCE_CEILINGS` 之和 = `free_total`，
`REVERSE_REFERENCE_CEILINGS` 之和 = `grand_total`，两个守卫测试强制对齐）：

| 口径 | 轨道 1 前 | 轨道 1 后 |
|---|---|---|
| 反向引用总数（`grand_total`） | 539 | **539（不变，设计如此）** |
| 自由全局引用（`free_total`） | 539 | **269（−50.1%）** |
| 接口引用（`interface_total`） | 0 | 270 |

按模块的自由侧变化：`canvas-generation-cascade.js` 147→42、`canvas-media-history.js` 83→36、
`canvas-image-editor.js` 87→41、`canvas-node-ops.js` 60→17、`canvas-connections.js` 41→12。

#### 轨道 1 完成：`canvas-generation-tasks.js`（2026-10-20，#199）

它是最后一个「自由侧 == 总数」的模块，即轨道 1 收益为 0 的唯一一块。实测：

| 口径 | 接口化前 | 接口化后 |
|---|---|---|
| 反向引用总数 | 113 | **113（不变，设计如此）** |
| 自由全局引用 | 113 | **35** |
| 独立外部自由名 | 42 | **22** |

20 个共享名字 / 78 处改走 `canvasCore`（提交 `c94f7c5`→`07836bf`）。棘轮 `FREE_GLOBAL_REFERENCE_CEILINGS["canvas-generation-tasks.js"]` 降到实测 35。
一个名字 `reducedAspectRatio` **回退保持裸引用**（`07836bf`）：接口化它触发了 `tests/test_project_image_generation_ui.py:97` 的源码契约断言（该断言钉死字面量 `return reducedAspectRatio(parsed.width, parsed.height)`），全量 1189 中 1 个失败。 worker 当时文件白名单不包含该测试文件，所以选择回退而不是跨范围改契约——**这是正确的边界行为，不是技术失败**。棘轮因此是实测 35 而非 34。
后续应按 `99453da`（#201 `isApiLikeEngine` 契约改成 `(?:canvasCore.)?` 前缀）的既有模式放宽该契约，再把这个名字接口化。轨道 1 的**技术**停止条件（“必须复制产品逻辑才能接口化就停”）**未命中**：21 个名字全部纯接口化，零逻辑副本。

**轨道 1 到此结束。** 剩下的自由引用集中在 `canvas-generation-cascade.js`(50) 和 `canvas-media-history.js`(51)，两者已在轨道 2/201 处理过，剩余部分是 ADR 判定不该搬的宿主耦合。

**轨道 1 真正的收益是「单测要桩的独立外部名字数」下降**，它由自由侧的独立名字数驱动，
不由总数驱动。棘轮因此拆成两套：`REVERSE_REFERENCE_CEILINGS`（总数）+
`FREE_GLOBAL_REFERENCE_CEILINGS`（自由侧），后者才是轨道 1 的执行机制。

### 轨道 4：按依赖面从小到大补测

`canvas-undo.js`(29，已完成) → `canvas-core.js`(已完成) → `canvas-connections.js`(已完成) → `canvas-generation-tasks.js`(#219，已完成) → `canvas-node-ops.js`(已完成) → `canvas-agent-tui.js`(已完成) → `canvas-image-editor.js`(纯计算核心，已完成)。

**轨道 1 完成后被解锁的下一块**：`canvas-generation-tasks.js` 现在独立外部自由名 = **22**，已经低于两个已判定「可测」的模块（`canvas-undo.js` 29 → 15 个手写桩、`canvas-connections.js` 28 → 15 个手写桩）。按本 ADR 的判据它现在进入可测范围，而它是 #197（刷新后恢复）与 #198（重放失败丢用户动作）两个真 bug 的所在地，当前无单测。

#### 该块已完成（2026-10-21，#219）

`tests/canvas_module_units/test-canvas-generation-tasks.mjs`：`node --test` → **tests 27 / pass 27 / fail 0 / skipped 0**；
全量 `Ran 1190 tests, OK`；门 `trial` → `verdict: passed`。
**有效性证据（不是「写了测试」就算）**：在 worktree 里把 `handlePendingActionReplayFailure` 的写回
分支禁用（`if(node)` → `if(false && node)`）→ **恰好 1 个 #198 用例变红**（`undefined !== 1`），
其余 26 个绿。这说明该用例真在盯那个行为，不是自我断言。
桩预算（文件头有清单）：13 个外部自由名由加载真实模块满足；26 个手写桩（20 零逻辑记录器 /
6 单行契约镜像）。
明确判为单测盖不住（没写假测试）：`runPromptLLMNode` / `runRunningHubGeneration` /
`runApiVideoGeneration`（依 smart-canvas.js 13 个未桩自由名，靠浏览器 runner 兜）；
`startJimengPoll` / `pollSmartCanvasTask` 的分钟级长轮询循环本身（只覆盖单次终态迁移）；
服务端 `idempotency_key` 去重（API 层负责）。

#### 该块已完成（2026-10-21，#199 轨道 4）：`canvas-agent-tui.js`

（顺序说明：它在 `canvas-node-ops.js` 之后做，不是按“从小到大”排；选它的理由就是下面那个
实测自由面——当时未测模块里最小的一块。）

选它的依据是实测自由面 **9 名 / 12 处**（全部是 `smart-canvas.js` 顶层声明），是当时全部未测
画布模块里最小的一块（对比 `canvas-node-ops.js` 17/14、`canvas-connections.js` 12/9）。

`tests/canvas_module_units/test-canvas-agent-tui.mjs`：`node --test` → **tests 54 / pass 54 / fail 0 / skipped 0**；
`tests.test_canvas_module_units` → **Ran 13 tests, OK**（该 runner 按 `test-*.mjs` 动态发现，新增文件
不用登记，以 12→13 的计数验证）；全量 **`Ran 1193 tests, OK`**。提交 `af2a862`/`1922d79`/`7849882`/`a790ef9`/`48bc7ac`。

**有效性证据（不是「写了测试」就算）**：worktree 里把 `handleAgentEvent` 的 `process_exited` 分支
`state.status = 'crashed'` 改成 `'dead'` → **恰好 1 个用例红**，其余 53 绿。另有 4 处独立变异，
各自只打红对应用例：删 `agentCardStatusHtml` 的 `disconnected` 分支 → 1 红；去掉
`cleanupAgentTuiState` 的 `agentTuiState.delete(nodeId)` → 2 红（两个 cleanup 用例）；
`message_update` 丢掉 `last.streaming` 判定 → 1 红；`renderMarkdown` 协议白名单去掉 `mailto:` → 1 红。

**桩预算：9 个外部自由名全部手写，复制产品逻辑的个数 = 0 → 停止条件未命中。**
1 个值传递记录器（`trf`）、2 个状态（`canvasId`/`sourceProjectId`）、4 个零逻辑记录器
（`imageLayout`/`nodeBodyHtml`/`render`/`centerViewportOnWorldPoint`）、2 个数据驱动
（`chatApiProviders`/`providerChatModels`）。`NODE_TYPE_AGENT`/`NODE_TYPE_TASK`、`canvas`/`tr`/
`escapeHtml`/`escapeAttr`/`nodeScale`、`canvasState` 由**加载真实模块**满足；`WebSocket`/`fetch`/
`prompt`/`console` 算浏览器表面积桩，不计入依赖面。
`trf` 故意不用 `smart-canvas.js:314-316` 的逐字镜像：假 `StudioI18n.t` 返回 `'i18n:'+key`、
里面没有 `{kind}` 占位符，逐字版会把代入值丢掉，所以做成记录器（把被代入的值拼回返回值），
这样才断言得出调用方真的传了 `kind`。

**顺带查出 3 个产品缺陷，已建单（本块只钉现状，未修）**：
**#220** `agentCardStatusHtml` 只分支 busy/connecting/error/disconnected，`retrying`/`crashed` 落到默认
「空闲」，而同文件 `agentNodeBodyHtml` 显示「重试中/已崩溃」——同一节点头部与正文互相矛盾；
**#221** `sendAgentCommand` 的 HTTP 回退路径 `fetch(...); return;`，`onSuccess`/`renderAgentNode` 只在
WS 路径执行 → WS 断开时点 abort，请求发出去了但 `state.status` 仍是 `busy`、控制按钮保持 `disabled`；
**#222** `turn_end` 从不把 `streaming: true` 的消息收尾（该标记只在追加增量时写入、无处清除）→
相邻两轮回复粘连（实测 `He`+`llo`+空 content 的 turn_end+`Next` ⇒ 仍是 1 条 `HelloNext`），
或同段文本被 `turn_end` 再推一条造成重复。

**单测盖不住（没写假测试）**：WebSocket 真实服务端行为（握手/断线重连/服务端事件时序）；
`showForkPicker` 的完整选择器 UI（真实实现就是 `window.prompt`，源码注释自陈 "In a full
implementation, this would be a UI picker"）；fork / switch-branch / model / autonomy / prompt 端点的
真实网络往返（fetch 是数据驱动桩，只验请求形态与本地状态）；渲染 HTML 的实际布局与 CSS。

**并发教训（流程层面）**：本块被两个 agent 各写了一遍。判活要看
`acp-delegate/del_*.activity` 的 mtime，**不能看 `.out`** —— `.out` 只在进程退出时写，我因此把一个
仍在跑的 worker 误判成零交付、自己写了一份，两边在同一文件上撞了一次（`af2a862` 一度把另一版
缩到 3 个用例）。最终由 worker 把那版 41 用例合并为基、补到 54，并逐个 `test(` 名比对确认零丢失。
以后同一模块的单测只派一个 worker。另外 worker 纠正了我两处误判：`trf` 桩的分类描述与实现不符；
我误判 `bindAgentNodeControls` 「单测盖不住」——`harness.makeElement` 支持按实例覆写 `querySelector`，
`test-canvas-connections.mjs` 的 `wireWorldQueries` 早就证了同法可行。

#### 该块已完成（2026-10-21）：`canvas-node-ops.js`

选它的依据是实测自由面 `17 处 / 14 名`（`canvas-node-ops.js 60 / 27 自由 17 / 14 可注入 11`），
低于已证可测的 `canvas-connections.js`，但它是唯一没单测的核心行为模块。

`tests/canvas_module_units/test-canvas-node-ops.mjs`（807 行）：`node --test` → **tests 25 / pass 25 / fail 0 / skipped 0**；
`tests.test_canvas_module_units + test_canvas_core_layer + test_canvas_coupling_report` → **Ran 32 tests, OK**；
全量 **`Ran 1192 tests, OK`**；门 `trial` → **`verdict: passed`, `final_exit_code: 0`**（200.1s）。
提交 `8242e81` / `a88b5ce` / `8700a7e`。

**有效性证据**：worktree 里移除 `deleteNodeFromButton` 的 #65「生成中禁删」守卫
→ **恰好 1 个用例红**（`deleteNodeFromButton: 生成中节点禁止删除（#65）`），其余 24 个绿。
浏览器 runner `node_flow_browser.py` / `node_e2e_browser.py` 均 EXIT=0（两者不收命令行参数，
内部用 `tempfile.TemporaryDirectory`，天然全新目录）。

**停止条件：数值阈值越线、实质判据未越线。** 我派的 brief 写了「桩数明显超过
`canvas-connections.js` 的 15 个就停」，实测 **20 个手写桩**，超了；但它们**全部是零逻辑
记录器（9）/ 数据驱动（8）/ 单行契约镜像（3），复制产品逻辑的个数 = 0**。7 个外部自由名
由**加载真实模块**满足（包含 `canvas-undo.js` 真实深快照撤销栈、`canvas-media-history.js`
的 #217 真实归档登记、`canvas-agent-tui.js` 真实 Map 清理）。所以 ADR 的实质判据（
「桩要开始重新实现产品逻辑就停」）**未命中**，本块保留。以后写 brief 时不要把桩个数当硬阈，
实质判据是「有没有第二套实现」。

**`canvas-core.js` 的「54」是错的**（本 ADR 初稿口径有误，#203 实测纠正）：按「不同外部自由名」
口径实测为 **18**，且对最近 4 个历史版本实测均为 14–18，从未到过 54。其中 9 个是 JS 内建、
6 个是 `harness.mjs` 已提供的浏览器全局、3 个是可直接加载真实模块的跨模块名——
**手写函数桩 0 个**（`043ab0a`，18 个 `node:test` 用例）。它是全部画布模块里最适合单测的一个。

#### 该块已完成（2026-10-21，#199 轨道 4）：`canvas-image-editor.js` —— 只测纯计算核心

**这块的难点不是自由名，是渲染。** 自由面只有 22 名（全部未测模块里最小的），但模块真实规模是
**2301 行 / 139 个顶层 function**（不是耦合报告里的 `89 / 39`——那是引用数/名字数，我一开始把它
当行数写进 brief，导致第一个 worker 按「小模块」做，烧掉 300 分钟零提交）。
脚本实测：**65 个顶层函数的自身函数体不碰 DOM/canvas**；真正的障碍是大量
`document.getElementById` + canvas 2D context + `ImageData` + `getBoundingClientRect`。

**因此本块主动限定范围，而不是去造假 DOM。** 给一个 2300 行的编辑器伪造 DOM + canvas 2D +
`ImageData`，那份脚手架本身就是「第二套假核心」——跟下面 `canvas-media-history.js` 不做单测是
同一个理由。渲染/DOM/像素路径写进「盖不住清单」，由浏览器 runner 兜。

`tests/canvas_module_units/test-canvas-image-editor.mjs`（1239 行）：`node --test` →
**tests 34 / pass 34 / fail 0 / skipped 0**；`tests.test_canvas_module_units` → **Ran 14 tests, OK**；
全量 **`Ran 1194 tests in 519.533s, OK`**；门 `trial` → **`verdict: passed`, `final_exit_code: 0`,
28 步全绿, 206.0s**。提交 `91ac16b`/`bdf081f`/`9e3576a`/`2e0ea51`/`df6c5d3`。

测的是参数整形/钳位（`validOutpaintSize`、`withOutpaintDisplaySettings`、
`stripOutpaintDisplaySettings`、`applyOutpaintSizeToSmartParams`、`clampCrop`/`clampOutpaint`/
`fitCropRectToAspect`/`cropRatioFromPreset`/`clampAspectCropToBounds`/`resizeCropFromDrag`/
`clampImageResizeScale`/`exceedsFourKStandard`/`textItemFont`）、宫格数学（`groupImageGridLayout`、
`gridJoinAutoDims`/`NaturalSize`/`BaseCellSize`/`ItemDisplaySize`/`VisualOrder`/`DragTarget`、
`gridSplitRects`、`gridLayoutFromRects`）、文本项与历史栈（`createEditTextItem`、`setBrushTool`、
`pushEditDrawHistory` 的 `EDIT_DRAW_HISTORY_MAX = 40` 截断、undo/redo 栈转移）。
其中 `groupImageGridLayout` 源码注释自陈「完美网格优先：整除且行数不超可见上限（8→4×2 / 2×4、
6→3×2、9→3×3），避免 3×2+2 这类缺角布局」——**这条注释之前没有任何测试锁着**。

**有效性证据**：4 处独立变异，每处只打红预期用例（红后回滚复绿）：评分向量去掉 `perfect` 项 →
1 红（完美网格用例）；`gridJoinDragTarget` 的 `*0.55` 改成 `*0.99` → 1 红；
`EDIT_DRAW_HISTORY_MAX` 40→41 → 1 红；`validOutpaintSize` 的 `w <= 0` 改成 `w < 0` → 2 红
（null 门 + 下游）。

**桩预算：`canvasCore.*` 等 hub 名字全是零逻辑记录器 → 复制产品逻辑个数 = 0，停止条件未命中。**
`reducedAspectRatio` / `isApiLikeEngine` / `MEDIA_GROUP_MAX_VISIBLE_ROWS` / `imageForDisplay` /
`mediaKindForItem` 定义在 hub `smart-canvas.js`（harness 加载不了 hub），所以用
`(w,h) => \`R(${w},${h})\`` 这类记录器断言「确实调了它 + 传的是取整后的 w/h」，
**不断言化简结果**；白名单正则 `/^\d+\s*:\s+\d+$/`（去空格）是模块自己的逻辑，直接断言
`'4 : 3'` → `'4:3'`；`cloneSmartSettings` 在真模块 `canvas-core.js` 里，加载真模块拿。

**顺带修了一个 harness 自己骗人的地方（正是本 ADR 要防的那类）**：`harness.mjs` 的
`classList.toggle` 不支持 DOM 标准的 `force` 参数，产品代码 `toggle('primary', active)` 被静默
当成单参翻转——假桩与真核心行为不一致。已按 DOM 语义补齐（`toggle(c)` 翻转、
`toggle(c, force)` 强制设置），全套件回归绿。

#### 不做 `canvas-media-history.js`（204 个跨模块自由标识符，实测）

**口径对账（重要，两个数字不是矛盾）**：耦合报告行里的 `自由 51 / 34 名字` 是**可注入自由名**
这个窄口径；210 这个老数字是**去重后由 hub 或其他 canvas 模块定义的自由标识符数**，
今天实测为 **204**（同文件另测：去重自由标识符总面 734）——所以旧数字站得住，
只是必须写清口径。本块的“不做”判断用的是 204 那个宽口径。
它自己需要维护——**真正要付出的代价是一份 200 多个名字、需要跟人一起维护的桩文件，
而且假核心跟真核心行为不一致时测试会骗人**（本块已经实际撞上一次：`harness.mjs` 的
`classList.toggle` 不支持 `force` 参数，产品代码被静默当成单参翻转）。
这才是真正的代价，不是做不到。

## 明确不做

- **ES 模块改造**：已由 #162 否决并实测理由——这套前端的协作完全建立在 classic script
  共享单一全局词法作用域之上（`canvas-core.js` 声明 `let settings`，
  `canvas-media-history.js` 直接读）。1502 个顶层声明全部停止共享，
  169 处跨文件类型比较 + 654 处反向引用 + 191 处正向引用都要改成 import/export，
  加载顺序语义同时变。真正想要的「依赖方向」已经用低成本拿到并有守卫锁住。
- **把 606 当 KPI 去降**：见上。55.6% 是对 39 个真共享服务的合法调用。
- **为了降数字把共享服务复制进各模块**：会造出多份实现，行为漂移不可测。

## 后果

- `tools/canvas_coupling_report.py` 成为拆分工作的入口工具；它的 `CATEGORY_BY_NAME`
  是人给的分类决定，工具会列出所有未分类名字，所以分类不完整时**可见**，不会悄悄退化。
  新增名字进核心时必须在这里补一行。
- 棘轮守卫保留，但**不再是唯一指标**；轨道 1 必须配一个新指标才有效。
- 「拆得越多越好」这个隐含假设被否掉：拆分的收益边界由「该模块能否独立测」判定。
