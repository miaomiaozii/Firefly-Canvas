# 浏览器功能与 Agent 工具覆盖对照（#478）

## 阅读口径

| 项目 | 静态调查结论 |
|---|---|
| 目的 | 供人逐项选择是否补画布工具；不实施、不排序推荐、不授权终端、不创建实现票。未覆盖不等于应开放。 |
| 基线 | 调查快照为 `86328709dff6f46eec880d51636abe6ecad25668`（#464 默认关闭切片）。下列行号是该快照源码的位置，会随代码过时，不是运行验收证据。 |
| 实际枚举 | `app/services/agent_tools_manifest.py:121` 的 TOOLS：**47 个唯一工具**（展开 PRESENT_TOOL 和布局列表）；`app/routers` **57 个 Python 文件、266 个方法-路径声明**，其中 **264 HTTP、2 WebSocket**；`static/js` 递归 **46 个 JS 文件**，包括翻译及校验脚本。不是照抄票面计数。 |
| 路由方法 | 只用 Python AST 读取 APIRouter prefix、装饰器、add_api_route 及 `main.py:1384`—`main.py:1500` 聚合注册；266 项均找到注册。`app/routers/api_agent_tools.py:656` 循环静态展开为 5 项；`app/routers/api_agent_plan.py:183` 的 operation 参数只有 import_excel/edit_preview/apply 合法。没有未解析的路由声明表达式；参数化路径仍保留参数，不算实际请求数量。 |
| 浏览器判定 | 不以“未在 JS 找到 fetch”排除 HTTP 能力。主表兼收当前 UI、浏览器可用后端接缝及兼容入口；Agent 专用和传输入口另列。路径缩写见附录。JS 索引同时检查 fetch、事件、onclick/onchange、动作分发及委托函数，不把仅本地操作漏掉。 |
| 状态含义 | **已覆盖**只指该行限定的静态业务合同；**部分覆盖**须看参数/副作用差异；**未覆盖**未见共享工具；**有意不给**只用于已有 ADR/票号的明确限制。其余一律未覆盖＋待用户裁定，不能从 read-only、敏感性或内部 URL 推导新禁令。 |
| 成本含义 | 小：已有 executor 加窄包装/参数及合同测试；中：复用后端但需扩 schema、CAS、日志或状态机；大：新服务器业务编排/像素处理/持久对象或跨生命周期事务。均为相对工程估计，非工期承诺；每行分别注明可复用与缺失。 |
| 证据界限 | 未导入应用，未启动服务器、浏览器、模型、CLI 或隔离探针，未访问用户项目/运行数据/凭据。没有真实模型调用、跨会话成功、OS 隔离或视觉可用性的结论。静态未能证明的 UI 可达性标为 unknown。 |

## 复用合同（主表末列中的 U / C / O）

| 合同 | 真正拥有事实的源码与边界 |
|---|---|
| U：Agent 操作撤销 | `app/routers/api_agent_tools.py:183`、`app/services/agent_ops_journal.py:1`、`app/services/agent_ops_store.py:19`、`app/services/agent_ops_undo.py:207`：项目内 before/after 差异，不是画布备份；按 op_id 或 turn_id 撤销，无 redo 工具。生成在途、差异冲突、记账失败可拒绝；归档字节、费用及宿主清理不能靠逆差异撤回。新功能须实际接日志，不能仅复用普通 PATCH 就宣称有 U。 |
| U：人工历史与恢复 | `static/js/canvas-undo.js:435`、`static/js/canvas-core.js:359`、`app/routers/api_project_undo.py:33`：本地 actionDelta/旧快照、原图令牌、设置专用历史是不同分支；人工整栈不是 ops.list。ADR 0029 规定并发与未决对话守卫；ADR 0024 规定删除撤销不恢复原生会话。 |
| C：确认 | `app/services/agent_confirmation.py:170`、`app/services/agent_confirmation.py:244`、`app/services/agent_host.py:453`、ADR 0022（#402/#456）：删除媒体、非本轮自建节点、非自建连线及批量改词有底线；归属豁免只采信服务端日志。花钱批准绑定输入、宿主/generation/原轮并执行前复核；90 秒不是批准有效期。新付费/破坏性入口须接门，不能照搬无门浏览器 API。 |
| O：归属与保护 | `app/services/agent_node_state.py:69`、`app/services/agent_layout.py:163`、`static/js/canvas-core.js:231`、ADR 0029：来源与人工 T/P 保护分开；来源字段不是权限。布局检查自有/保护；runSettings 只准未人工编辑的自有节点。**普通 update_node 的 title/text/prompt/x/y/w/h 在 `app/routers/api_agent_tools.py:1242` 没有相同的通用 ownership 硬门**；默认策略 `app/services/agent_host.py:425` 要求保护人工内容，不应把提示词要求冒充所有 executor 已强制。 |
| 专用 REST/会话 | ADR 0022 #456、`app/routers/api_agent_tools.py:51`、`app/routers/api_agent_tools.py:264`：工具写入口要求 X-Agent-Project 与 Agent 上下文，不是浏览器 PATCH 的别名；`app/routers/api_agent_nodes.py:391` 和 `app/services/agent_node_host_settings.py:1` 拥有宿主事务。设置/绑定不能靠 canvas.update_node 的 runSettings 或普通保存替代。 |

## 一、项目、检索、保存与历史恢复

| ID | 浏览器功能＋源代码引用 | 覆盖状态与工具及参数边界 | 缺口与成本 | undo、confirm、ownership 复用 |
|---|---|---|---|---|
| P01 | 项目列表、新开/打开/返回列表；`static/js/project-list.js:40`、`app/routers/api_project_library.py:2424`、`app/routers/root.py:10` | 未覆盖；canvas.list_nodes 只读当前绑定画布，不是项目列表/页面切换 | 小：复用 list/open 后端；切页需新定向 UI 命令；待用户裁定项目仲裁 | 读不需 U/C；已有项目 ID/活动会话边界可复用，不得暗换工具写项目 |
| P02 | 新建、整项目复制；`static/js/project-list.js:204`、`static/js/project-list.js:339`、`firefly_projects.py:658` | 未覆盖；canvas.create_node 不是 create_project | 中：复用 create/duplicate 与活动任务守卫，需工具结果/新项目归属合同 | 无整项目 ops.undo；已有项目回收可另恢复，不是自动逆操作；需独立 C/O |
| P03 | 导入/导出 .fly；`static/js/project-list.js:200`、`static/js/project-list.js:321`、`app/routers/api_project_library.py:2439`、`app/routers/api_project_library.py:3092` | 未覆盖；canvas.view 是缩略媒体拼图，不是项目包 | 中：复用 multipart/package executor，缺附件/下载交付、目标项目绑定 | 包导入不是 U；导出锁/临时文件清理可复用；新项目与源项目分开，不能宣称跨项目写已获准 |
| P04 | 项目删除、回收站列表/恢复/永久删除；`static/js/project-list.js:222`、`static/js/project-list.js:245`、`app/routers/api_project_library.py:3117` | 未覆盖；canvas.delete_node/media.delete 均不是删项目 | 中：生命周期后端已有，需停止宿主、确认及结果合同 | 复用 trash/restore 和 owner admission；purge 不可逆；Agent C 与日志缺失 |
| P05 | 关闭且静默项目的永久清理预览/执行；`static/js/project-list.js:46`、`app/routers/api_project_library.py:3036` | 未覆盖＋待用户裁定，不以维护性质擅定禁用 | 中/大：已有 preview/execute、漂移与活动守卫，需 Agent 预览令牌和破坏性收据 | 原清理不可撤销；现有精确确认快照可复用，仍缺 Agent C/不可逆记账；不能用 media.delete 模拟 |
| P06 | 版本树列表/diff/恢复、损坏文档备份向导；`static/js/project-list.js:384`、`static/js/smart-canvas.js:6771`、`app/routers/api_project_library.py:4153`、`app/routers/api_canvases.py:343` | 未覆盖；ops.undo 只逆 Agent 操作，不恢复整版本/备份 | 中：复用版本/backup executor；需恢复时会话、未决输入与归属合同 | `firefly_project_versions.py:251` 先存 pre-restore；ADR 0018/0029 不保证永久防重放；无通用 Agent U/C |
| P07 | 跨项目关键词搜索、点击命中导航；`static/js/canvas-search.js:92`、`app/routers/api_search.py:34` | 已覆盖搜索：canvas.search_projects(query,limit)，ADR 0026；不搜索 Agent 会话 | 搜索无缺口；点击打开项目/定位结果是 P01/C15，不包含在搜索工具 | 只读无 U/C；不能由搜索结果扩大外项目文件读取/写入 |
| P08 | 标题、手动保存、自动保存、落盘状态、冲突合并、项目消失后快照导出；`static/js/smart-canvas.js:40`、`static/js/smart-canvas.js:7198`、`static/js/smart-canvas.js:7384`、`app/routers/api_project_library.py:3807` | 未覆盖这些用户动作；单个工具完成写入不等于控制页面保存队列/元数据 | 中：复用 Store flush/status、增量 mutation；需窄元数据/保存 API 与本地 pending 协调，不开放任意 mutation | 普通 PATCH 有 revision，无自动 Agent U/C；已有 pending journal/O 保护可用，不能回放整画布 |

## 二、画布操作、文本及主画布排版

| ID | 浏览器功能＋源代码引用 | 覆盖状态与工具及参数边界 | 缺口与成本 | undo、confirm、ownership 复用 |
|---|---|---|---|---|
| C01 | 查看节点/连线/组/来源；`static/js/canvas-core.js:82`、`app/routers/api_agent_tools.py:580` | 已覆盖：canvas.list_nodes(group_id,created_by,fields)、canvas.get_node(node_id)；list 无 node_ids；fields=[] 仍有 summary，run 是保存配置非实际请求 | 本行无缺口；不返回浏览器选区，见 C05 | 只读，不修补旧来源；O 投影不是权限 |
| C02 | 新建图片、提示词、对话、Agent、循环、局部编辑/分组；`static/js/canvas-node-ops.js:17`—`static/js/canvas-node-ops.js:151`、`static/js/canvas-node-ops.js:523` | 部分覆盖：canvas.create_node(type,title,text,prompt,x,y) 能建基础节点；不能配置 loop/local-edit/LLM/Agent 宿主等专用字段。type 字符串本身不证明节点可执行 | 中：复用建节点/放置；扩专用初始 schema/校验，不借 core 兼容自由对象绕 manifest | U/O 建立已有；框绑定一起记差异；特殊类型的执行、C 与恢复另接 |
| C03 | 节点标题、图片名、快速重命名、F2 批量编号；`static/js/canvas-node-ops.js:375`、`static/js/canvas-node-ops.js:486`、`static/js/smart-canvas.js:16903` | 部分覆盖：多次 canvas.update_node(node_id,title) 可实现节点标题前缀/补零/起号结果，但没有批次预览/原子提交；images[].name 和图片身份 CAS 未覆盖 | 小/中：复用 mutation 与命名预览；需图片/output 身份参数和批次服务 | U 差异可复用；O 需保护人工改名，不能把 title 当媒体名；新批量确认边界待裁定 |
| C04 | 拖动、尺寸手柄、方向键微移、吸附、整组平移/缩放；`static/js/smart-canvas.js:15724`、`static/js/smart-canvas.js:16971`、`static/js/canvas-snap.js:34` | 部分覆盖：canvas.update_node(x,y,w,h) 仅单节点几何；不是整个组成员原子移动，也不含 snap/scale、拖拽会话 | 中：单点参数已有；整组/吸附需服务器原子 planner。主画布坐标不等于图片裁剪/对象内部布局 | U 可用；update 通用字段 O 硬门不足（见合同表），布局路径保护不能自动套来 |
| C05 | 单选、多选、R/Ctrl 框选、Ctrl+A、清选、选中图片索引；`static/js/smart-canvas.js:1057`、`static/js/smart-canvas.js:15198`、`static/js/smart-canvas.js:16331` | 未覆盖；manifest 没有 get_selection。canvas.view(scope=selection,node_ids) 是调用者显式列表，不读真实选区 | 中：需浏览器定向命令/回执及选区读取后端；纯 UI 状态不应假装 canvas node 字段 | 不持久、不走 U；标签页/项目归属可复用 viewport 传输，缺新命令语义 |
| C06 | Ctrl+C/V、Ctrl+D、Alt 拖复制、素材 inbox 粘贴及内部连线重映射；`static/js/smart-canvas.js:7595`、`static/js/smart-canvas.js:7636`、`static/js/smart-canvas.js:16989` | 部分覆盖基础卡重建：create/get/update/connect 可组合复制受支持字段和简单边；未覆盖完整复制合同（消息/媒体/组/未决状态），也不读取系统剪贴板 | 中/大：需新 clone executor；复用 Store 校验，不能复制原生绑定/待决输入 | ADR 0024/0029 的新身份与 pending 守卫可复用；U/C/O 须按复制结果绑定，不沿源归属 |
| C07 | 端口连线、Ctrl 拖连接、插入循环节点、局部编辑输入角色；`static/js/canvas-connections.js:477`、`static/js/canvas-connections.js:485`、`static/js/smart-canvas.js:10474` | 部分覆盖：canvas.connect(from_node,to_node,kind) 只有通用边；无 local-edit original/references/prompt 的 role、插入重布线事务或前端 inputNodeIds 同步合同 | 中：复用 upsert_edge，补类型/角色校验与复合事务；不是只多传同名 kind | U 可用；删旧边必须 C；O 从日志证明创建，不能 client 自报 |
| C08 | 点击/双击断线、Shift 涂抹断多线；`static/js/canvas-connections.js:262`、`static/js/canvas-connections.js:335` | 部分覆盖：canvas.disconnect(from_node,to_node)；core `app/routers/api_agent_tools.py:851` 取首个端点匹配边，不能精确 edge_id/kind/role 或一次原子多删 | 小/中：已有删边 core，需精确边身份/批次 | U/C 及服务端创建证据可复用；不允许自报 owner 免门 |
| C09 | Delete/Backspace、更多菜单删整个节点；`static/js/canvas-node-ops.js:268`、`static/js/smart-canvas.js:16431`、`app/routers/api_agent_tools.py:681` | 已覆盖整节点删除：canvas.delete_node(node_id)；Agent 宿主清理可能不可逆，不等于 × 清媒体 | 无新功能；浏览器“生成中禁删/级联归档”不可仅凭工具名断言完全相同，媒体后果见 C10/M11 | U 仅恢复可逆差异；会话清理 effects 明示不可逆，ADR 0024；C 底线，O 本轮日志豁免 |
| C10 | 节点 × 清媒体留提示词；多图中删一张、拖出缩略图成为独立节点；`static/js/canvas-node-ops.js:328`、`static/js/smart-canvas.js:12100`、`static/js/smart-canvas.js:15970` | 未覆盖；delete_node 会删节点，media.delete 仅回收文件/索引且保留引用，place_media 只追加，不是删除 output | 中：复用 Store 输出身份/归档及 mutation，需 output_id/image 身份和复合 executor | 浏览器 U/归档可参考 ADR 0016；缺 Agent C、日志与引用后果收据，不能用两次不同删除拼凑 |
| C11 | 顶层选中节点“整理排版”/Shift+A；`static/js/smart-canvas.js:1883`、`static/js/smart-canvas.js:1908`、`static/js/canvas-connections.js:64` | 部分覆盖：canvas.align(node_ids,edge,include_user_nodes)、canvas.distribute(node_ids,axis,gap,include_user_nodes) 是对齐/分布，不等于按连线整理；canvas.arrange_groups 仅显式排版框 | 中：主画布连线布局需复用/移植 JS planner 到服务器，不是图片内部 grid 或旧组 arranger | U/C/O layout pipeline 可复用；受保护/另一 Agent/旧成员会跳过，不能承诺全选必移动 |
| C12 | Ctrl+G/解组、图片吸收入卡片、合并组/拖入拖出、组内网格/缩放；`static/js/smart-canvas.js:1202`、`static/js/smart-canvas.js:1348`、`static/js/smart-canvas.js:15216`、`static/js/smart-canvas.js:15362` | 未覆盖旧分组语义；canvas.group_create/group_arrange 不转换旧组、不吸收图片、不支持嵌套或解组；同名 group 不能算覆盖 | 大：现有 JS 改 images/items/连线并删旧实体，需服务器复合分组 executor；复用底层 Store 不等于已有后端 | 参考人工 actionDelta；需组/成员 O 和原子 U/C，不能仅更新框 x/y 或 title |
| C13 | 显式排版框内独立卡片、框整体排列；`static/js/canvas-core.js:92`、`app/services/agent_layout.py:286`、`app/routers/api_agent_tools.py:1054` | 已覆盖限定排版框：canvas.group_create(title,node_ids,plan_section,anchor,include_user_nodes)、canvas.group_arrange(group_id,layout,sort_by,gap)、canvas.arrange_groups(group_ids,direction,gap) | 本能力无缺口；group_create 同 Agent 同标题复用但不加成员，grid 最多三列；不是 C12 或媒体宫格 | U 已接；C/O planner 默认跳过保护对象；include_user_nodes 只在支持入口点名请求，不能通用接管 |
| C14 | 参考框标记/切换绑定、创建/打开会话框、手工改成员；`static/js/canvas-node-ops.js:167`、`static/js/canvas-agent-tui.js:3817`、`static/js/canvas-agent-tui.js:3861` | 部分覆盖：create_node/place_media 自动会话框落位；无 refFrame/refFrameId/chatFrameId/items 显式编辑；group_id 不是绑定宿主参考框 | 中：复用 agent_frame/已有 UI schema，需专用成员/绑定事务 | 自动链 U/O 已有；人工入口 pushUndo 不代表新工具有 U；需保护成员、旧组/会话框差别及 C |
| C15 | 平移/滚轮缩放、小地图、Z 总览、fit-all、定位；`static/js/smart-canvas.js:2078`、`static/js/smart-canvas.js:2183`、`static/js/smart-canvas.js:2325` | 部分覆盖：viewport.focus(node_id)、viewport.zoom(level=0.1..8)、viewport.state()；state 只记服务器最后命令，不回读手势后的真实视口；focus 存节点左上与 scale=1，不是 fit-all | 中：已有单 tab 定向 WS，但 static/js 静态检索**没有 canvas_viewport_command 消费者**；缺浏览器执行接线、pan/fit/总览及 live 状态回执。`dispatched` 仅说明发送，不证明视口改变 | 临时视口无 U；ADR 0022 定向项目/tab 可复用；默认不移动见 agent_host.py:446 |
| C16 | 提示词/正文编辑、追加、替换、批量改词；`static/js/smart-canvas.js:10014`、`static/js/smart-canvas.js:12559`、`app/routers/api_agent_tools.py:3007` | 部分覆盖：canvas.update_node(text,prompt)、canvas.batch_prompt_edit(apply:false,edits → diff_id → apply:true)；后者最多 50、整批确认；不等于全部富文本 draft/mention/来源结构 | 小/中：纯文字已有；需结构化 draft/引用标号保真 schema，不能仅写 prompt 留旧 draft | U/一次性差异 CAS/C 可复用；单 update 无通用人工保护门，批量改词才有明确整批批准 |
| C17 | 拆分提示词、分隔符、心流模式/展开高度/长内容阅读器、选区与 IME；`static/js/smart-canvas.js:10117`、`static/js/smart-canvas.js:12625`、`static/js/smart-canvas.js:12698`、`static/js/smart-canvas.js:12961` | 未覆盖；text/w/h 不等于 promptSplitEnabled/promptSeparator 或 DOM/阅读状态 | 中：持久结构可复用 mutation；临时心流/选区需 UI 命令，不应写节点伪持久 | 文本历史可用，IME/焦点不是 U 对象；新 O/C 需区分提示词结构与临时视图 |
| C18 | 锚定节点的批注增删改/位置/状态、刷新恢复；`static/js/canvas-annotations.js:96`、`app/routers/api_project_annotations.py:51` | 未覆盖；不是 canvas.update_node.text，也不是 agent.present.question | 中：复用 `firefly_project_annotations.py:102` 的独立 SQL CRUD，需 Agent 归属/日志协议 | 前端 pushUndo 有记录，但不能据此证明 SQL 撤销已持久接通；agent_ops 逆差异仅 node/connection/media，批注 U/C/O 缺 |
| C19 | 工具栏/Ctrl+Z/Y、刷新恢复人工 undo 栈、设置专用 undo；`static/js/canvas-undo.js:361`、`static/js/canvas-undo.js:435`、`static/js/canvas-undo.js:678` | 部分覆盖：ops.list/ops.undo 不是人工整栈、redo 或 /settings 历史。Agent 操作撤销见 A10 | 大：前端栈/原图/设置分支可参考，需新受约束服务器历史事务；不可暴露 PUT 整栈冒充 undo | ADR 0029 pending 守卫和局部合并、ADR 0024 expected 可复用；新 U 的归属与 C 未定义 |

## 三、图片、LLM、视频与生成编排（不能互相代称）

| ID | 浏览器功能＋源代码引用 | 覆盖状态与工具及参数边界 | 缺口与成本 | undo、confirm、ownership 复用 |
|---|---|---|---|---|
| G01 | 图片服务商/模型/路由/比例/分辨率/自定义尺寸/质量/格式/数量/角度参数；`static/js/smart-canvas.js:2993`、`static/js/smart-canvas.js:3815`、`app/routers/api_models.py:53` | 部分覆盖：canvas.update_node.runSettings 只收 model/provider_id/aspect_ratio/count(1..8)/routingMode。不能设置 resolution/customSize/quality/outputFormat/anglePreset/engine，更不是 llmModel 或 agentHostSettings | 中：`app/services/agent_image_settings.py:138` 已能消费部分保存值，需扩白名单/模型验证及能力发现，不保证任意旧 engine 可用 | U 已有；run patch O 仅未被人工编辑自有节点；不涉及密钥修改；花钱仍过 G02 C |
| G02 | 单次图片生成、多候选逐张写回、同节点重叠任务；`static/js/canvas-generation-tasks.js:148`、`static/js/smart-canvas.js:14888`、`app/routers/api_project_library.py:1706` | 部分覆盖：generate.trigger(node_id) 消费保存图片参数/引用，返回 task_id；`app/routers/api_agent_tools.py:1703` **另建结果卡**，不是浏览器所有“原节点累加/候选池/历史组”语义；非图片 API engine 被拒 | 中：付费任务/归档 executor 已有（`app/services/generation.py:2929`）；需目标结果/叠加策略和候选身份合同 | U 只逆画布，费用/归档不退；C 按张额度与输入快照已有；保留原 op/turn，不能改当前配置阻断已付结果结算 |
| G03 | 多节点同词生成、整批预检/队列/进度/停止未开始项；`static/js/canvas-generation-cascade.js:1031`、`app/routers/api_agent_tools.py:910`、`app/services/agent_batch.py:494` | 部分覆盖：canvas.batch_edit(node_ids≤10,prompt) 强制整批；generate.preflight(node_ids≤200 或 group_id) → generate.request_batch(preflight_id,fallback_models,max_retries) → generate.batch_status(batch_id,wait_seconds) / generate.stop_batch(batch_id)。不是通用前端队列/循环并发 | 中：已有后端批次、共用批准预算的最多两次自动重试及备选模型；额度耗尽会暂停，不自动补额度。缺浏览器各编排模式、持久批次及通用恢复；batch 本身驻内存 | U 子操作日志/C/O 可复用；stop 仅阻止新启动，不取消已启动任务；不要把批次批准当任意后续调用许可 |
| G04 | 图片任务状态、等待、失败重试；`static/js/canvas-generation-tasks.js:596`、`static/js/canvas-generation-tasks.js:948`、`app/routers/api_project_library.py:1624` | 部分覆盖：generate.status(task_id)、generate.wait(task_id,timeout_seconds≤600)、generate.retry(task_id 或 node_id,model,use_snapshot)。retry 仅本 Agent scope 的失败且安全、无产物/无不明上游的任务，最多两次，不是任意浏览器旧任务重放 | 小/中：查询后端已有，扩重试面须保留 lineage/引用撤回规则；不能用状态工具代恢复动作 | retry 每次重新 C/计额；U 不退费，运行中拒撤；`app/services/agent_generation_retry.py:159` 校验原来源/附件/scope |
| G05 | 图片任务取消（节点卡、历史详情、全局控制台）；`static/js/smart-canvas.js:9239`、`static/js/global-settings.js:573`、`app/routers/api_project_library.py:1467` | 未覆盖；generate.stop_batch 不取消已开始任务，Agent abort 也不是图片任务 cancel | 中：复用 cancel_shared_agent_image_task/浏览器 cancel executor；需 scope/终态 CAS 和明确费用结果 | 已下载产物/孤儿回收规则可复用 ADR 0016；不承诺退费或 U；Agent 操作记账/C 需接 |
| G06 | 丢失轮询、手动查询上游已付任务/取回；`static/js/canvas-generation-tasks.js:536`、`app/routers/api_project_library.py:1419`、`app/routers/api_image_task_query.py:100` | 未覆盖恢复动作；status/wait 可查 Agent 本地任务，不提供浏览器 recover=1 或 provider/task_id 手工取回参数 | 中：现有 recover/query 后端可用，需任务归属验证、幂等归档/收据；不是 generate.retry 再付钱 | 原任务/媒体身份可复用；恢复产生写入，不能因 GET 就免 C/O/U 审查；ADR 0017 |
| G07 | 循环次数/起号/串并行/每图分批、变量提示词/计数 token、自引用/上游传递、级联链运行/停止；`static/js/smart-canvas.js:10217`、`static/js/canvas-generation-cascade.js:1050` | 未覆盖编排；create_node(type=smart-loop)、connect 或重复 trigger 均不等价。batch 不能表示每轮动态引用、结果槽和级联顺序 | 大：需把 JS graph/loop planner 与停止/恢复搬到业务后端；底层图片任务可复用 | 每步付费 C/预算、日志 parent/op、O 可参考；缺跨轮编排 U、取消和持久归属，不可统一估小 |
| G08 | 提示词节点内 LLM 开关/模型/system/instruction/运行/拆分输出；`static/js/smart-canvas.js:10147`、`static/js/canvas-generation-tasks.js:37`、`app/routers/api_canvas_llm.py:22` | 未覆盖；canvas.update_node.text 是文字写入，不会执行 LLM；generate.trigger 是图片生成；runSettings.model 不是 llmModel | 中：复用 canvas-llm 的多模态/服务商 executor；缺工具 schema、确认/费用、输出 CAS/来源与恢复 | 人工修改 T/P 可参考；Agent C、LLM op/用量关联需接，不能伪装免费更新 |
| G09 | 独立对话节点：多轮消息、参考输入、发送/等待/恢复/重试、折叠；`static/js/smart-canvas.js:11041`、`app/routers/api_project_library.py:3732`、`app/routers/api_project_library.py:3777` | 未覆盖；chat.history/search **只读 Agent 保留正文**，不读/发送 smart-conversation；创建 conversation 不等于已有发送工具 | 中/大：已有 request_id/outbox/FIFO/持久终态 executor，需 Agent 准入、付费 C、scope 与幂等结果 | ADR 0029 D2 发送不进人工撤销；D3 禁止改写 live 未决意图。不是可用 ops.undo 取消请求 |
| G10 | 对话选中文字提取提示词、图片 OCR 建提示词；`static/js/smart-canvas.js:10758`、`static/js/smart-canvas.js:16863`、`app/routers/api_ocr.py:178` | 部分覆盖文字落节点：canvas.create_node(text,type)；无 source_node_id/source_message_id、OCR 工具、选区读取或来源提取动作 | 小/中：OCR 后端已有；需 provenance/目标位置/图片身份参数；DOM 选区需 UI 接缝 | 建节点 U/O 可用，OCR/来源不连线；不能用 connect 模拟提示词提取 |
| G11 | 视频生成/编辑：视频模型/时长/比例/分辨率/音频、首尾帧/多模态参考、云上传、取消/查询；`static/js/canvas-generation-tasks.js:350`、`static/js/smart-canvas.js:3020`、`app/routers/api_project_library.py:1936` | 未覆盖；generate.* 是图片，smart-video type 不带视频执行；media.upload 也不能导入视频/音频 | 中/大：视频 executor/持久任务已有（`app/services/generation.py:3017`），需视频 schema、付费预算/确认、恢复与 op 生命周期；云上传有外部副作用 | 项目媒体 O 与任务 CAS 可用；图片 Agent lifecycle 不自动适用于视频，U/C/额度与停止需扩 |
| G12 | 视频/音频播放、逐帧前后、首/尾/当前帧导出到画布；`static/js/smart-canvas.js:12424`、`static/js/smart-canvas.js:12463` | 部分覆盖：canvas.view_media(node_id,frames≤16,max_px) 抽帧拼图供模型看；没有 timestamp/当前播放头、帧原件导出、播放控制或音频卡工具 | 中：预览抽帧后端可复用；指定帧归档/节点是新业务接缝，本地播放头另需 UI 回执 | 只读拼图无 U/C；导出帧需新 O/U/C；不能把缩略拼图当无损帧原件 |
| G13 | RunningHub 应用/工作流参数、机器/钱包/随机种子、上传/提交/查询；`static/js/smart-canvas.js:3065`、`static/js/canvas-generation-tasks.js:296`、`app/routers/api_runninghub.py:49` | 未覆盖工作流入口；普通图片模型路由可能使用同 provider，不能据此宣称 webappId/nodeInfoList/instanceType/useWallet 已覆盖 | 中/大：现有 submit/query/upload 后端可复用，需有界业务 schema/费用/任务追踪，不是开放命令行 | 已有上游任务 ID，缺 Agent C/额度与完整 U/O 关联；外部执行不逆 |

## 四、媒体、编辑、历史、导出与资源库

| ID | 浏览器功能＋源代码引用 | 覆盖状态与工具及参数边界 | 缺口与成本 | undo、confirm、ownership 复用 |
|---|---|---|---|---|
| M01 | 文件/目录拖入、粘贴图片、URL/本机路径导入、媒体归档/进度；`static/js/smart-canvas.js:13507`、`static/js/smart-canvas.js:13692`、`app/routers/api_ai.py:79`、`app/routers/api_project_library.py:2511` | 部分覆盖：media.upload(file_path 或 attachment_id)；file_path **仅图片**且限本项目/配置输入输出/素材根；attachment_id 仅已发送本会话图片、人工确认归档；无任意 URL、目录、视频/音频上传 | 中：浏览器导入/后台归档已有；需附件/批次及媒体类型 schema；不拓宽文件路径根 | 导入归档有不可逆 effects，非 ops.undo 清文件；附件 C/O 已有，浏览器上传不能直接变 Agent 绕门路径 |
| M02 | 项目媒体目录与放到画布/目标/分组；`static/js/smart-canvas.js:4950`、`static/js/smart-canvas.js:13706`、`app/routers/api_agent_tools.py:1122` | 已覆盖限定放置：media.list()、canvas.place_media(asset_id,node_id?,group_id?,title?)。新建 smart-image 媒体卡可承载已归档媒体种类，已有目标只接受 image 类型节点；不支持图片内部顺序/任意目标类型；屏蔽特定 mask 名 | 小/中（若选扩展）：复用 placement，新增精确插入/替换/目标类型需 schema，不等于 C10 删除 | place U/O 已有；media.list 只读；导入与放置分开，不能靠放置读取外部文件 |
| M03 | 参考图选择/启用/排序/角色/替换、@ 图号、手工 refs 与多模态；`static/js/smart-canvas.js:13247`、`static/js/smart-canvas.js:13370`、`static/js/smart-canvas.js:14552` | 部分覆盖：canvas.attach_refs(node_id,refs[source:asset_id 或 node_id/image_index,role,enabled],mode)；canvas.set_ref_order(node_id,order,role_overrides)。仅图片生成目标/Agent 管理 input 边，replace 不删用户边；无手工排除/任意视频音频/局部编辑 role | 中：Agent refs/ReferenceGraph 已有；手工/多模态/局部编辑需独立语义，不能只扩 role enum | U 已有；保留用户 refs，O 来源明确；角色 layout 是参考图角色，**不是主画布布局** |
| M04 | 原图/缩略图/多图导航、缩放平移/对比、长边复制、JPEG 转换；`static/js/canvas-image-editor.js:1625`、`static/js/smart-canvas.js:12276`、`static/js/smart-canvas.js:8296`、`app/routers/api_media_preview.py:37` | 部分覆盖：canvas.view(scope,page/selection,node_ids,max_px) 是媒体 contact sheet；canvas.view_media 先取首个非视频媒体（否则首视频）。不是画布截图/文本节点视觉、任意 image_index、全分辨率下载或当前编辑视图 | 中：媒体加载/缩略图已有；需精确资源/索引/比较/播放视图接缝；本地导航另需 UI 命令 | 查看不需 U；缩略图可能写缓存，不等于 OS 只读保证；无 C，项目媒体边界可用 |
| M05 | 裁剪；工具侧另有旋转/镜像，但固定工具栏未见对应浏览器按钮，不算既有 UI 功能；`static/js/canvas-image-editor.js:1934`、`static/js/smart-canvas.js:9576`、`app/routers/api_agent_tools.py:3250`、`app/routers/api_agent_tools.py:3289` | 部分覆盖：canvas.edit_crop(node_id,rect 原图像素)、canvas.edit_rotate(node_id,angle=90/180/270逆时针)、canvas.edit_flip(node_id,direction)。统一取首个有 asset_id 的非视频，不支持浏览器当前 image_index/output_id、任意角度/交互选区 | 小/中：本地 PIL executor 已有，补精确输出身份/CAS；工具旋转/镜像不能据名字推断 UI 当前索引 | U 记录引用差异，原资源保留，新增归档/费用不逆；C 已接；缺精确图片身份参数 |
| M06 | 红底扩图几何、比例预设、建议提示词及后续在线扩场景；`static/js/canvas-image-editor.js:1953`、`static/js/outpaint-ratios.js:27`、`app/routers/api_agent_tools.py:3342` | 部分覆盖：canvas.edit_outpaint(node_id,outpaint_size,offset) 只本地红底拼图；后续 generate.trigger 另付费；不自动复制 UI 建议 draft/全部比例交互，仍无图片索引 | 小/中：PIL/fitContaining 可复用，需图片身份/明确下一步与参数；不是一次模型扩图工具 | 两步各自 U/C/O，归档/费用不逆；ADR 0007 不向模型发 mask；不能把外区像素准备当生成完成 |
| M07 | 局部编辑节点：原图/参考/提示词输入角色、矩形 modify、自由形状 retain、羽化、尺寸/分辨率、合成到独立节点；`static/js/smart-canvas.js:11165`、`app/routers/api_project_library.py:3837` | 部分覆盖仅狭义修改：canvas.edit_erase(node_id,rect,prompt) 裁矩形送模型、feather=0、无参考图、覆写源图；不支持 retain/polygon/mask/feather_radius/result_node_id/input role | 中/大：modify/retain 业务后端/local_edit 已有，需独立节点 schema、付费门/归属及结果 CAS；不能标作 edit_erase 已全覆盖 | `app/routers/api_agent_tools.py:3400` 的 C/外部 effects 可参考；浏览器 executor 不自带 Agent U；新节点/selection 媒体身份须记账 |
| M08 | 手绘遮罩、笔刷/形状/数字、文字对象样式/拖动、绘图撤销/重做；`static/js/canvas-image-editor.js:205`、`static/js/canvas-image-editor.js:637`、`static/js/canvas-image-editor.js:2012`、`static/js/canvas-image-editor.js:2056` | 未覆盖；edit_erase 不接自由绘制/mask；update_node.text 不是像素叠字 | 大：现有浏览器 canvas 绘制，需新服务器有界图元/字体/合成 API；上传后端可复用 | 本地绘图历史不等于 U；结果归档/节点引用 U 可复用，图元 O、写入 C 缺 |
| M09 | 宫格切图（自定义线/间距）、多图拼接/拖排/输出大小、图片重采样；`static/js/canvas-image-editor.js:2074`、`static/js/canvas-image-editor.js:2145`、`static/js/canvas-image-editor.js:2190` | 部分覆盖简单矩形切片：可先 place_media 复用原件，再多次 edit_crop；没有完整有序 Grid 元数据/整批事务，拼接/重采样未覆盖。group_arrange 是卡片坐标，node w/h 不是原图重采样 | 中/大（完整宫格/拼接/重采样）：需新服务器像素 executor，可复用 local_edit/PIL 与归档；JS 几何可参考 | 新产物 U 只恢复引用，不能自动删字节；需输入身份 CAS、C 和 O |
| M10 | 图层新增/导入笔刷、显隐/上下/拖排/删层、导出合成 PNG；`static/js/canvas-layers.js:231`、`static/js/canvas-layers.js:296`、`static/js/canvas-layers.js:412` | 未覆盖；group 与 runSettings 都不是图层 | 大：图层当前为页面内存像素 store，需新有界服务器层模型/合成/交付；不能靠节点字段绕 schema | 本地图层暂无 Agent U/C/O；归档或导出只是底层可复用，图层顺序历史另定 |
| M11 | 项目资源库单删/批删＋直接引用清理/墓碑；`static/js/smart-canvas.js:5940`、`static/js/smart-canvas.js:6103`、`app/routers/api_project_library.py:3275` | 部分覆盖“删除媒体”但**后果不等价**：media.delete(asset_id) 进回收区并保留画布/历史引用；浏览器资源库删除硬删并清引用（ADR 0015/0016） | 中/大：永久删除后端已有；若人选择需新独立工具/确认文案与不可逆日志，不能改名复用 media.delete 冒充 | 现 media.delete U/C/O 已接；硬删无媒体恢复，普通 U 不可承诺；复用 tombstone/引用分析但缺 Agent 入口合同 |
| M12 | 引用列表、未用媒体检测、无引用归档/永久清理；`static/js/smart-canvas.js:6354`、`static/js/smart-canvas.js:6393`、`app/routers/api_project_library.py:3160` | 未覆盖专用查询/维护；media.list 不返回完整引用报告，media.delete 不检查“仅无引用”用户意图 | 小（查询）/中（写）：复用 references/unused/archive executor；查询与永久维护必须分开 | archive 回收可用，永久清理不可逆；需新 C/O 与日志；浏览器 409 不能当成功 |
| M13 | 项目媒体回收区列表/预览、按身份恢复、单项永久删/清空；`static/js/smart-canvas.js:6218`、`static/js/smart-canvas.js:6238`、`static/js/smart-canvas.js:8600`、`app/routers/api_project_library.py:4077` | 部分覆盖恢复：ops.undo 只能撤相关 Agent media.delete；无通用 trash 列表/预览/任意条目恢复/purge 工具 | 中：原回收事务可复用；需区分 agent_deletion_id/原图令牌/无令牌恢复，只回库不还引用 | #442/ADR 0016 的冲突/过期/占位守卫可用；purge 不可逆且缺 Agent C；GET 可能清过期，不假称无副作用 |
| M14 | 历史抽屉展开/折叠/调高、浏览任务/参数/输出、详情导航、拖回画布/参数复用；`static/js/canvas-media-history.js:1170`、`static/js/canvas-media-history.js:1788`、`static/js/canvas-media-history.js:2615` | 部分覆盖输出复用：media.list/canvas.place_media；无历史记录分页/筛选/参数复用/抽屉命令；generate.status 不是历史列表 | 中：history/媒体后端已有；需只读投影与稳定 history/output 身份；抽屉纯 UI 接缝另计 | 已有历史/输出身份可复用 ADR 0014；放置 U/O 已有；读无 C，重用参数仍受 G01/O |
| M15 | 删除生成历史记录、连原件删除；`static/js/smart-canvas.js:8324`、`app/routers/api_project_library.py:3948`、`firefly_projects.py:2159` | 未覆盖；ops.list 是 Agent 操作日志，delete_node/media.delete 都不等于 history/delete 或 delete-with-media | 中：Store 删除与事务已有；需 history_id、revision、清理范围/后果收据 | 历史删除与媒体恢复不同；现 agent_ops 无 history 实体逆差异，U 需扩；C/归属缺 |
| M16 | 删除/恢复当前生成输出项、项目范围删除/恢复生成原件；`static/js/smart-canvas.js:8410`、`static/js/smart-canvas.js:6671`、`app/routers/api_project_library.py:3987` | 未覆盖；media.delete 无 history_id/output_id/deletion_id，不清引用，不等价任一原图删除 | 中/大：`firefly_projects_deletion.py:581`、`firefly_projects_deletion.py:1349` 事务可复用，需精确输出身份、项目范围和撤销桥 | 原图令牌/7天/冲突恢复已有；现 ops.undo 不能代任意令牌恢复；新 Agent C/日志/O 缺 |
| M17 | 星标单图、curated/tags、批量取消、图片名；`static/js/canvas-media-history.js:624`、`static/js/canvas-media-history.js:695`、`app/routers/api_excel_collect.py:70` | 未覆盖；update_node.title 不是 images[].marked/name、curated/tags | 小/中：tag-node/普通 mutation 已有，需 output/asset 身份与批次；前端星标集合不是媒体删除 | 人工 U/T 可参考；需 Agent 白名单/日志/O，批量 C 是否必要待裁定 |
| M18 | 单图/文件下载、整组 ZIP、选中/星标导出、下载记录、服务器目录导出；`static/js/canvas-download.js:275`、`static/js/smart-canvas.js:15581`、`static/js/smart-canvas.js:15649`、`app/routers/api_excel_collect.py:298` | 未覆盖；canvas.view 低分辨率拼图供模型看，不是源文件 ZIP/.fly/文件夹导出；media.upload 不是反向下载 | 中：现 streaming/ZIP/group-export 后端可复用；缺精确选择/命名/下载交付与受约束目录目标，浏览器记录是本地状态 | 导出外部字节不可 U；可复用项目媒体校验，缺文件交付的 O/C；不建议绕到终端 |
| M19 | 发送 Photoshop、轮询/取源、编辑结果返回生成新项目原件/节点；`static/js/smart-canvas.js:9611`、`app/routers/api_project_library.py:3388` | 未覆盖；media.upload/place_media 不保存 request_id、源/结果血缘和一次消费协议 | 中/大：PS 往返 executor/loopback/TTL/哈希协议已有；工具发送与连接器返回必须分角色 | ADR 0008：不是覆盖原图。项目 O/CAS 可用；Agent U/C 和不可逆 PS 副作用收据需接；返回路由不是给 Agent 任意伪造返回 |
| R01 | 全局资源库/分类 CRUD、添加单/多素材、移动、重命名、删除、批量裁剪；`static/js/smart-canvas.js:5598`、`app/routers/api_asset_library.py:237` | 未覆盖＋待用户裁定；media.list/delete 仅当前项目，edit_crop 不操作库分类/副本 | 中：已有 JSON/file executor；需全局 scope、确认与可逆范围合同。未由“全局/敏感”自行判禁用 | 现 U 不覆盖库/文件元数据；删除硬删；现项目 O/C 不能直接授权全局写 |
| R02 | 全局本地素材上传/URL 导入、文件夹创建/重命名、移动/删项，项目素材显式保存到全局；`static/js/smart-canvas.js:6601`、`app/routers/api_local_assets.py:111`、`app/routers/api_project_library.py:2651` | 未覆盖＋待用户裁定；media.upload 只入当前项目，不是写全局素材目录 | 中：现后端可复用，需显式目的 scope、路径/冲突/交付合同 | 现重命名/删除直接动磁盘且无 U；需新 C/全局 O。ADR 0003 要求用户明确保存，不从当前项目工具自动外推 |
| R03 | 素材分类/描述/编辑 caption、头像审核注册/状态；`app/routers/api_asset_library.py:389`、`app/routers/api_asset_library.py:422`、`app/routers/api_local_assets.py:346` | 未覆盖；canvas.view 只是输入视图，不调用分类器/写 caption/注册上游资产 | 中：已有模型/审核 executor；需受约束资源 ID、付费 C、返回与可撤销边界 | 现 U 不管 caption/分类 sidecar 或上游审核；项目/全局 O 需明确，不能把“只查询 avatar-status”当必无写 |
| R04 | 提示词库/分类/条目 CRUD、搜模板/应用模板、保存预设；`static/js/smart-canvas.js:4189`、`static/js/smart-canvas.js:4585`、`app/routers/api_prompt_libraries.py:134` | 部分覆盖应用文字：canvas.update_node/text 或 batch_prompt_edit；没有库读写、template/preset 绑定、分类/positive/negative 参数 | 小/中：模板库后端已有，需库 scope/字段与来源绑定，不把整个模板退化成一段 prompt | 节点文字 U/C 可用；全局库改动无 U/C/O；未覆盖部分待用户裁定 |
| R05 | 注册/取消共享文件夹、树/文件读取、导入库；`app/routers/api_shared_folders.py:114` | 未覆盖＋待用户裁定；不是 media.upload 已有允许根。static/js 未见消费者，当前页面可达性 **unknown**，保守保留 route 能力 | 中：后端已有 allowlist/媒体扩展过滤，需显式注册/读取范围及归属合同 | 项目 U 不覆盖注册表/全局导入；需 C/O；不通过任意本机路径绕过 |
| R06 | 存储文件列表/分页/打开/删除；`app/routers/api_storage_files.py:35` | 未覆盖＋待用户裁定；media.list 不列 generated/input 等存储目录。static/js 当前消费者可达性 **unknown** | 中：后端已有 kind/rel_path 解析；需用途限定、只读投影及删除收据，非通用文件系统工具 | 删除磁盘无 U；新 O/C 缺，不能因“文件路由”自行设禁止策略 |

## 五、Agent 会话、方案与人的决定

| ID | 浏览器功能＋源代码引用 | 覆盖状态与工具及参数边界 | 缺口与成本 | undo、confirm、ownership 复用 |
|---|---|---|---|---|
| A01 | 建/恢复宿主会话、发送目标/消息/节点引用、steer/FIFO follow-up、运行状态；`static/js/canvas-agent-tui.js:1434`、`static/js/canvas-agent-tui.js:1569`、`app/routers/api_agent_nodes.py:164`、`app/routers/api_agent_nodes.py:571` | 未覆盖；create_node(smart-agent) 只建画布实体，不 spawn/send；chat.* 不发送 | 中/大：已有 host manager/node 输入准入/FIFO，需明确“Agent 控制另一 Agent”的对象/循环/费用合同；不因路由现成就算小 | 原 request_id/host/turn 守卫可复用；发送不可 U/退费；新 C/O 与跨 Agent 权限待裁定 |
| A02 | 停止当前轮/停止宿主、等待终态/核验重试；`static/js/canvas-agent-tui.js:1583`、`static/js/canvas-agent-tui.js:1605`、`app/routers/api_agent_nodes.py:259`、`app/routers/api_agent_nodes.py:608` | 未覆盖；generate.stop_batch 非宿主停止；delete_node 会删实体且可能清会话，不是 abort | 中：复用 stop/abort/busy/取消待决，需精确会话身份和状态回执 | 不可 U 恢复已终止执行；确认与旧批准失效可用 #402，缺 Agent 控制他者 O/C |
| A03 | 宿主/会话模型/思考/自治档位、能力菜单、恢复另一宿主；`static/js/canvas-agent-tui.js:1796`、`app/routers/api_agent_nodes.py:391`、`app/routers/api_agent_nodes.py:413` | 未覆盖；runSettings 是**图片参数**，无 agentCli/agentHostSettings/effort/autonomy 工具；不把普通 get_node 读到设置当有配置能力 | 中/大：现事务/expected/Pi ACK/CAS/失败补偿可复用，仍需授权/接口裁定；本表不新增配置工具 | ADR 0024 设置历史专用；不能用 ops.undo 回放绑定。全局设置/密钥另见 S01；节点设置是否给 Agent 待用户裁定 |
| A04 | Pi 消息分叉、命名分支/切分支；`static/js/canvas-agent-tui.js:1733`、`static/js/canvas-agent-tui.js:2055`、`app/routers/api_agent_nodes.py:661` | 未覆盖；chat.history 的 seq 不是原生 entry_id/thread，不能据消息 ID 调 fork | 中/大：分支事务已有，仅 Pi、idle 守卫；需 Agent scope/用户选择及不可逆副作用收据 | ADR 0024 原生补偿/绑定可复用；普通画布 U 不保证原生会话恢复；C/O 缺 |
| A05 | 搜本画布 Agent 对话/历史会话、读线程/旧页（含删除节点）；`static/js/canvas-agent-chat.js:67`、`app/routers/api_agent_chat.py:38` | 已覆盖限定正文：chat.history(scope,node_id,host,limit,before_seq)、chat.search(query,scope,node_id,host,limit)。默认 node/canvas 不同；历史缺失节点须显式 host；只用户/助手正文、扫描有界/truncated | 本合同无缺口；不包括 smart-conversation、工具/思考/系统/附件内部、外项目历史（ADR 0022 #457） | 只读无 U/C，不启动/切宿主；项目内共享不是 MCP SID 私有租户；旧 turn 不伪填 |
| A06 | WS 流式回放、events/page、工具/思考/错误/确认卡、另一宿主只读视图；`static/js/canvas-agent-tui.js:779`、`static/js/canvas-agent-tui.js:913`、`app/routers/api_agent_nodes.py:685` | 部分覆盖：chat.history 仅正文投影；没有共享工具获取完整事件/page/live subscription 或 tool/thought 事件 | 小/中：读口/日志已有，需受约束事件投影/分页工具；不是扩 chat.* 返回任意内部字段 | 读无 U/C；host/seq 隔离可复用；只读宿主浏览不会启动进程 |
| A07 | 输入草稿/附件暂存、消息复制/用户消息导航/悬停预览、回最新/未读、思考显隐/工具折叠；`static/js/canvas-agent-navigation.js:324`、`static/js/canvas-agent-tui.js:2816`、`static/js/canvas-agent-tui.js:2910` | 未覆盖页面动作；get_node/chat.history 不读 DOM 阅读锚点或剪贴板 | 中：需定向 UI 命令/读取接缝；纯本地状态不能靠普通 mutation 伪保存 | ADR 0028 明确临时状态不写节点；无 U/C；页/节点/宿主归属和迟到响应守卫可用 |
| A08 | 人点击批准/拒绝费用及破坏性确认；`static/js/canvas-agent-tui.js:2156`、`app/routers/api_agent_nodes.py:623` | **有意不给 Agent 替人批准**：ADR 0022 #402、`app/services/agent_confirmation.py:294`。/agent/confirm 是请求确认，不是自我批准工具；/agent-nodes/.../confirm 是人的回应 | 不估新增自批实现；现确认 UI/handlers 已有。人是否改变政策须另行裁定，本调查不改 | 原操作绑定/CAS/原轮续接可用；不能把 prompt/follow-up/答案当费用许可 |
| A09 | 展示计划/差异/多题、按推荐作答、读取回执；`static/js/canvas-agent-tui.js:2380`、`app/routers/api_agent_nodes.py:539`、`app/services/agent_present.py:1` | 已覆盖呈现/取回：agent.present(action=show/get,kind=plan/question/diff)，diff 必须 op_id/diff_id，plan 可 steps 或 plan_id/generation；**有意不给模型替人作答/批准**（ADR 0022 #406/#482） | 问答完整选择/编辑 UI 仍薄；若人选扩展需中等 UI 成本，不是新增工具能代替人工选择 | present 无 U/执行权；question answers 是真实新用户输入/FIFO，非 C 同轮续接；answered/queued 不证明宿主执行 |
| A10 | 完成卡按轮/按操作差异、刷新、定位、单项/整轮撤销；`static/js/canvas-agent-completion.js:118`、`static/js/canvas-agent-completion.js:174`、`app/routers/api_agent_ops.py:76` | 已覆盖服务端操作查询/撤销：ops.list(turn_id?,limit≤1000)、ops.undo(op_id 或 turn_id 恰一)；不是 redo、人工栈或历史包回滚 | 本行限定合同无缺口；差异标记/跟随开关是 UI，见 C15/A07 | U 保留冲突/在途/不可逆 effects/原轮语义；外 MCP turn_id 不得复用；日志保留有界不是永久备份 |
| A11 | Agent 左/右停靠、取回/拖出、收起/展开、宽度；`static/js/canvas-agent-tui.js:3518`、`static/js/canvas-agent-tui.js:3593` | 部分覆盖 w：canvas.update_node(w)；无 dock/唯一停靠切换与临时 collapse 命令；viewport.focus 不会改变呈现形态 | 中：已有 JS/持久 dock，需定向 UI 接缝或原子 dock executor；不能只写 x/y 模拟停靠 | ADR 0028 同一实体/O；持久改动可用 U，临时收起不保存；IME/替换节点守卫需保留 |
| A12 | 图片/视频/音频/Excel 附件上传、预处理、移除、发送；`static/js/canvas-agent-tui.js:1451`、`static/js/canvas-agent-tui.js:1468`、`app/routers/api_agent_nodes.py:561`、`app/routers/api_chat_media.py:112` | 部分覆盖仅后续图片归档：media.upload(attachment_id) 不上传/发送附件；plan.import_excel 只消费已发 XLSX ID；没有视频 contact sheet/audio card 会话工具 | 中：已有 attachment saver/媒体预处理与准入，需用户选择来源/大小/host/输入绑定；不扩大扫描路径 | 已发送 scope/摘要校验可用；附件与项目媒体不同；新 C/U/O 不能沿归档能力假设 |
| A13 | 外部 CLI 接管片段查看/复制；`static/js/canvas-agent-tui.js:2094`、`app/routers/api_agent_nodes.py:277` | 未覆盖＋待用户裁定；这是为人复制 MCP URL/命令，不是应用内开放 bash；不可据此绕 S06 | 小（只读片段）/中（新 UI 命令）：复用真实基址/node 绑定；不实现自动开终端/执行 | 无 U；MCP 仲裁/归属 ADR 0022/0024 可用；启动外部执行不在该片段合同 |
| X01 | 拖入 XLSX、解析字段/任务/板块/排除/警告并预览；`static/js/excel-import.js:80`、`app/routers/api_excel_import.py:29` | 部分覆盖：plan.import_excel(attachment_id,sheet?) 仅已发当前会话 XLSX；不读个人路径/链接、不识图；不等于浏览器旧 Plan IR 上传任意数据 | 小/中：parser 已有；输入桥与展示对齐可复用；差异需保留而非回退旧路径 | 预览只读缓存，不需 U；attachment scope/O 已有；导入不等于批准/生成 |
| X02 | 人工读/编辑 Agent 方案预览（选择/改名/合并/板块）；`app/routers/api_agent_plan.py:144`、`app/routers/api_agent_plan.py:164` | 部分覆盖：plan.edit_preview(plan_id,generation?,ops) 已有后端编辑，只有 select/rename/merge_into/set_section；无 split/set_refs。**当前 JS 未接这两个人工 REST 入口，不把未实现 UI 算作既有浏览器能力**，`static/js/canvas-agent-tui.js:2380` 只画 steps，真实预览引用不能据此声称完整卡片已实现 | 中（完整 UI）；后端/CAS 已有。是否当前页面有别的动态消费者为 unknown，不冒充 #411 验收 | 预览不写画布、不批准、不转 owner；原 Agent 精确 scope 不变，generation/TTL/CAS 可复用 ADR 0022 #482 |
| X03 | 方案展开到画布/应用勾选任务；`static/js/excel-import.js:313`、`app/routers/api_excel_expand.py:33`、`app/routers/api_agent_plan.py:85` | 部分覆盖：plan.apply(plan_id,generation?,task_ids?,layout=by_section) 建缺失任务/提示词→空图片槽，不生成/不导入或接参考图。浏览器旧 expand-plan 会导入 embedded refs，**不等价** | 中：布局/plan mutation 已有；完整人工卡“应用”要向原 Agent 发真实用户输入；不另绕 scope | U/C/O 原 plan 链已接；#482 编辑不批准；PLAN_NOT_CONFIRMED 超同步窗无通用自动恢复，不承诺 resumable |
| X04 | 任务打标/收集/按板块导出、curated/tags 写回；`app/routers/api_excel_collect.py:70`、`app/routers/api_excel_collect.py:114`、`app/routers/api_excel_collect.py:298` | 未覆盖；plan.apply 只建任务，不能 collect/tag/export；模板板块不等于导出目录 | 中：现 tag/collect/export 后端已有；需精确节点/asset 选择与输出交付 | 普通 Store mutation 可用，缺 Agent U/C/O；服务器目录导出不可用画布 U 删除 |

## 六、全局设置、诊断与非浏览器入口

| ID | 浏览器功能＋源代码引用 | 覆盖状态与工具及参数边界 | 缺口与成本 | undo、confirm、ownership 复用 |
|---|---|---|---|---|
| S01 | 全局服务商/模型/API Key、连接/协议探测、分类提示词、主题/语言/UI 缩放偏好、存储与项目库切换；`static/js/global-settings.js:328`、`static/js/global-settings.js:422`、`static/js/theme.js:203`、`app/routers/api_global_settings.py:91`、`app/routers/api_providers.py:209` | **有意不给修改设置/密钥**：#406 默认策略 `app/services/agent_host.py:416` 明确不改设置或密钥，ADR 0003 明确全局不属项目；#342 的 token 接口仅配置标志。没有会话配置/密钥工具；get_node 不代表允许配置 | 不估开放密钥；若人另裁定窄只读模型/偏好查询，小/中可复用 public payload。连接测试可能调用上游，不能伪称只是 list | 现全局写无项目 U/O；项目库迁移完整性/显式移动可复用但非 Agent 已获准。read-only 本身不是禁用依据 |
| S02 | 版本/健康/数据诊断、错误 toast/重试提示；`app/routers/api_app_info.py:33`、`app/routers/api_health.py:21`、`app/routers/api_config.py:39`、`static/js/global-settings.js:387` | 未覆盖＋待用户裁定；不能因内部/诊断自行列有意不给 | 小：已有只读/脱敏 payload，可做窄查询；页面错误呈现不等于新工具 | 无 U/C；只返回现有限定诊断，不能扩到运行目录/认证文件读取 |
| S03 | 全局任务控制台、项目/状态/服务商过滤、自动刷新/队列位置；`static/js/global-settings.js:466`、`app/routers/api_global_tasks.py:162`、`app/routers/api_queue_status.py:10` | 未覆盖；generate.batch_status 只查本批，不是全局任务/队列；取消另见 G05 | 中：现聚合后端已有，需明确跨项目只读合同；不从 canvas.search_projects 外推所有全局查询权限 | 无自动 U；查询可能跨项目，O/披露边界待裁定；取消必须按任务归属另过门 |
| S04 | 项目用量/按天/按模型聚合；`app/routers/api_project_usage.py:19` | 未覆盖；任务状态/ops 不是 usage 汇总；JS 当前专用消费者可达性 unknown（后端接缝保留） | 小：复用 usage_summary 投影与 days 参数 | 只读无 U/C；限定本项目 O；无凭据/运行日志输出 |
| S05 | Codex/Antigravity 状态、固定命令 --help；`static/js/global-settings.js:347`、`app/routers/api_codex.py:21`、`app/routers/api_gemini_cli.py:22` | 未覆盖＋待用户裁定；既不是 generate 模型参数，也不是通用终端工具。源码 status 会 spawn，help 仅固定 allowlist＋--help，不能推断可执行任意命令 | 中：已有受限后端，若人选需窄 schema/输出脱敏/生命周期；不作为开放 shell 建议 | CLI side effects 非 U；现同源守卫可用，Agent C/O 缺；本调查未执行任何此类入口 |
| S06 | 原生终端/工作目录文件边界（不是浏览器缺口替代方案）；`docs/adr/0024-codex-per-turn-host-bridge.md`、`CONTEXT.md` 的 #445/#464 | **有意不给终端**：#464；Codex shell_tool=false/read-only，Pi 不注册 bash/PowerShell/codemode。Pi 受限 read/write/edit 不在这 47 个业务工具内，不是全局终端/文件授权 | 不实施、不估偷偷开终端的替代成本；是否开放由用户在调查后另裁定；受限文件 SDK/目录内能力不能代替产品功能 | 文件工具不接 ops.undo；工具注册约束不是 OS 沙盒；不保证抵抗进程/重解析点并发攻击 |
| I01 | MCP JSON-RPC/Pi REST、确认请求、WS 项目注册/画布更新/PS 通知；`app/routers/api_mcp.py:891`、`app/routers/api_agent_tools.py:41`、`app/routers/ws_stats.py:25` | 单列 **Agent/传输入口，不是遗漏的用户功能**。POST /api/mcp 分发 47 工具，GET 返回不支持 SSE；/agent/confirm 请求门；/ws/stats 收 ping/注册并转事件，不另算业务工具 | 无“把内部 route 默认开放”选项；各业务缺口在上表。主画布通知不是可执行任意 JS 的工具 | ADR 0022 同源/Host/项目仲裁、#456 专用写入口可复用，头不是密码鉴权；人工 confirm 回答 A08 |
| I02 | 已退役写入口/经典画布兼容；`app/routers/api_projects.py:27`、`app/routers/api_canvases.py:295`、`app/routers/api_canvases.py:304` | 明确 out-of-scope **兼容/410**，不是有意不给新 Agent。旧 /api/projects 新改删与经典创建 410；核心画布拒旧 meta/touch/PUT/history 写；旧画布 trash/restore/purge 仍是兼容合同 | 不计新增核心能力；若要迁移需另裁定。不能将兼容 HTTP route 数当现行按钮数 | ADR 0001/0003 的核心画布/项目归属；兼容删除不是 current project 生命周期，不能接错 U/C/O |

## 路由 → feature 索引

| 约定 | 含义 |
|---|---|
| 路径缩写 | `P=/api/project-library/{project_id}`；`A=/api/projects/{project_id}`；`N=A/agent-nodes`；`B=/api/canvases/{canvas_id}`。花括号保留原参数；`∅` 代表无后缀。逗号列举仅是多个真实声明的合并，不生成笛卡尔积。 |
| 核查口径 | 下表覆盖所有 266 个声明。原件逐条保留 file/line/method/path、生成来源与 main 注册位置；同一方法-路径可映射多个 feature。浏览器/浏览器可用 210、Agent 接入 43、兼容 12、浏览器＋连接器传输 1；分类不等于授权。 |

| 路由文件（均在 app/routers/） | 方法＋完整路径或上方前缀的后缀 | feature |
|---|---|---|
| app/routers/root.py:10 | GET / | P01 |
| app/routers/api_agent_chat.py:38 | GET A/chat/history；GET A/chat/search | A05 |
| app/routers/api_agent_nodes.py:164 | POST N；GET N/{node_id}/status；POST N/{node_id}/prompt、/steer、/follow_up；WS N/{node_id}/stream（内含 prompt/steer/follow_up/abort/confirm 命令） | A01；WS 子动作另 A02/A08 |
| app/routers/api_agent_nodes.py:259 | DELETE N/{node_id}；POST N/{node_id}/abort | A02 |
| app/routers/api_agent_nodes.py:277 | GET N/external-cli-snippet | A13 |
| app/routers/api_agent_nodes.py:329 | GET N/capabilities、/chat-models；POST N/{node_id}/settings、/model、/autonomy | A03 |
| app/routers/api_agent_nodes.py:539 | GET N/{node_id}/questions/{presentation_id}；POST 同路径 /answers | A09 |
| app/routers/api_agent_nodes.py:561 | POST N/{node_id}/attachments | A12 |
| app/routers/api_agent_nodes.py:623 | POST N/{node_id}/confirm | A08 |
| app/routers/api_agent_nodes.py:661 | GET N/{node_id}/fork-messages；POST N/{node_id}/fork、/switch-branch | A04 |
| app/routers/api_agent_nodes.py:685 | GET N/{node_id}/events、/events/page | A06 |
| app/routers/api_agent_ops.py:76 | GET A/ops；POST A/ops/undo | A10 |
| app/routers/api_agent_plan.py:144 | GET A/plan/previews/{plan_id}；POST 同路径 /edits | X02（人工接缝） |
| app/routers/api_agent_plan.py:183 | POST A/plan/{operation}（仅 import_excel/edit_preview/apply） | X01/X02/X03，Agent 接入 |
| app/routers/api_agent_tools.py:61 | POST A/agent/present | A09，Agent 接入 |
| app/routers/api_agent_tools.py:570 | GET A/canvas/nodes、/nodes/{node_id} | C01，Agent 接入 |
| app/routers/api_agent_tools.py:608 | POST A/canvas/nodes；PATCH A/canvas/nodes/{node_id}；DELETE 同路径 | C02；C03/C04/C16/G01；C09，Agent 接入 |
| app/routers/api_agent_tools.py:627 | POST A/canvas/place_media、/attach_refs、/set_ref_order | M02；M03，Agent 接入 |
| app/routers/api_agent_tools.py:658 | POST A/canvas/group_create、/group_arrange、/arrange_groups、/align、/distribute（循环注册） | C13/C11，Agent 接入 |
| app/routers/api_agent_tools.py:832 | POST A/canvas/edges；DELETE A/canvas/edges/{from_node}/{to_node} | C07/C08，Agent 接入 |
| app/routers/api_agent_tools.py:872 | POST A/canvas/view、/view_media | M04，Agent 接入 |
| app/routers/api_agent_tools.py:886 | POST A/canvas/edit_crop、/edit_rotate、/edit_flip；POST A/canvas/edit_outpaint；POST A/canvas/edit_erase | M05；M06；M07，Agent 接入 |
| app/routers/api_agent_tools.py:897 | POST A/canvas/batch_prompt_edit；POST A/canvas/batch_edit | C16；G03，Agent 接入 |
| app/routers/api_agent_tools.py:1350 | POST A/generate/trigger；POST A/generate/retry | G02/G04，Agent 接入 |
| app/routers/api_agent_tools.py:1383 | POST A/generate/preflight、/request_batch、/batch_status、/stop_batch | G03，Agent 接入 |
| app/routers/api_agent_tools.py:2215 | GET A/generate/tasks/{task_id}、/tasks/{task_id}/wait | G04，Agent 接入 |
| app/routers/api_agent_tools.py:2279 | POST A/media/upload；GET A/media/list；DELETE A/media/{asset_id} | M01/M02/M11，Agent 接入 |
| app/routers/api_agent_tools.py:2547 | POST A/viewport/focus、/zoom；GET A/viewport/state；POST A/agent/confirm | C15；A08，Agent 接入 |
| app/routers/api_ai.py:79 | POST /api/ai/upload、/upload-base64、/import-local-image | M01（base64 含连接器消费者） |
| app/routers/api_app_info.py:33；app/routers/api_health.py:21 | GET /api/app-info；GET /api/healthz | S02 |
| app/routers/api_asset_classification_prompt.py:19 | GET、PATCH /api/asset-classification-prompt | S01 |
| app/routers/api_asset_library.py:237 | GET /api/asset-library；POST /api/asset-library/libraries；PATCH、DELETE /api/asset-library/libraries/{library_id}；POST /api/asset-library/categories；PATCH、DELETE /api/asset-library/categories/{category_id} | R01 |
| app/routers/api_asset_library.py:322 | POST /api/asset-library/items、/items/batch；PATCH、DELETE /api/asset-library/items/{item_id}；POST /api/asset-library/items/delete、/items/move、/items/crop | R01 |
| app/routers/api_asset_library.py:389 | POST /api/asset-library/items/classify、/items/{item_id}/register-avatar、/items/{item_id}/avatar-status | R03 |
| app/routers/api_canvas_assets.py:162 | GET /api/canvas-assets；POST /api/canvas-assets/check、/download | M18 |
| app/routers/api_canvas_image_tasks.py:50；app/routers/api_online_image.py:18 | POST /api/canvas-image-tasks；GET /api/canvas-image-tasks/{task_id}；POST /api/online-image | G02/G04 |
| app/routers/api_canvas_llm.py:22；app/routers/api_canvas_video.py:18 | POST /api/canvas-llm；POST /api/canvas-video | G08/G11 |
| app/routers/api_canvases.py:282 | GET /api/canvases；GET B、B/meta | P01 |
| app/routers/api_canvases.py:289 | GET /api/canvases/trash；POST /api/canvases；POST B/meta、B/touch；PUT B；POST B/history/delete；DELETE B；POST B/restore；DELETE B/purge | I02（部分为核心 410，不能当新功能） |
| app/routers/api_canvases.py:343 | GET B/recovery/backups；POST B/recovery/restore | P06 |
| app/routers/api_chat_media.py:112 | POST /api/chat-media/video-contact-sheet、/audio-card | A12 |
| app/routers/api_cloud_video.py:20；app/routers/api_temp_sh.py:20 | POST /api/cloud-video/upload；POST /api/temp-sh/upload | G11 |
| app/routers/api_codex.py:21；app/routers/api_gemini_cli.py:22 | GET /api/codex/status、/api/gemini-cli/status；POST /api/codex/help、/api/gemini-cli/help | S05 |
| app/routers/api_config.py:21 | GET /api/config、/api/config/token、/api/config/data-diagnostics | S01/S02 |
| app/routers/api_download_output.py:27 | GET /api/download-output | M18 |
| app/routers/api_excel_collect.py:70 | POST /api/excel/tag-node、/collect-nodes、/export-zip | X04/M17/M18 |
| app/routers/api_excel_import.py:29；app/routers/api_excel_expand.py:33 | POST /api/excel/import-plan、/api/excel/expand-plan | X01/X03 |
| app/routers/api_global_settings.py:91 | GET /api/global-settings；GET、PATCH /api/global-settings/preferences；POST /api/global-settings/project-library | S01 |
| app/routers/api_global_tasks.py:162；app/routers/api_queue_status.py:10 | GET /api/global-tasks；GET /api/queue_status | S03 |
| app/routers/api_image_jpeg.py:22；app/routers/api_media_preview.py:37；app/routers/api_view.py:19 | GET /api/image-jpeg、/api/media-preview、/api/view | M04 |
| app/routers/api_image_params.py:35；app/routers/api_models.py:44 | GET /api/image-params、/api/models、/api/model-specs | G01 |
| app/routers/api_image_task_query.py:100；app/routers/api_ocr.py:178 | POST /api/image-task-query；POST /api/ocr | G06/G10 |
| app/routers/api_local_assets.py:111 | POST /api/local-assets/upload、/import-urls；GET /api/local-assets；POST、PATCH /api/local-assets/folders；PATCH /api/local-assets/items；POST /api/local-assets/delete、/move | R02 |
| app/routers/api_local_assets.py:346 | POST /api/local-assets/caption、/classify；PATCH /api/local-assets/caption | R03 |
| app/routers/api_mcp.py:891 | POST、GET /api/mcp | I01（Agent；GET 405，无 SSE） |
| app/routers/api_project_annotations.py:51 | GET、POST P/annotations；PATCH、DELETE P/annotations/{anno_id} | C18 |
| app/routers/api_project_library.py:1419 | GET P/image-generation-tasks/{task_id}；POST 同路径 /cancel、/retry；POST P/image-reference-resolution；POST P/image-generation-tasks | G06；G05；G04；G01/M03；G02 |
| app/routers/api_project_library.py:1936 | POST P/video-generation-tasks；GET P/video-generation-tasks/{task_id}；POST 同路径 /cancel | G11 |
| app/routers/api_project_library.py:2424 | GET /api/project-library；POST /api/project-library；POST /api/project-library/import；GET P | P01/P02/P03 |
| app/routers/api_project_library.py:2511 | POST P/media/import；GET P/media/import/{task_id}；GET P/media；GET P/media/{asset_id}/preview；POST P/media/{asset_id}/copy-to-global | M01；M02；M04；R02 |
| app/routers/api_project_library.py:3036 | GET P/cleanup-preview；POST P/cleanup-execute；POST P/duplicate；GET P/export | P05/P02/P03 |
| app/routers/api_project_library.py:3117 | GET /api/project-library/trash；POST /api/project-library/trash/{project_id}/restore；DELETE /api/project-library/trash/{project_id}；DELETE P | P04 |
| app/routers/api_project_library.py:3160 | GET P/media/unused；POST P/media/{asset_id}/archive；GET P/media/{asset_id}；GET P/media/{asset_id}/references；DELETE P/media/{asset_id} | M12；M04；M11 |
| app/routers/api_project_library.py:3388 | POST P/photoshop/round-trips、P/photoshop-round-trips；GET 两别名的 /{request_id}、/{request_id}/source；POST 两别名的 /{request_id}/return | M19（8 个声明；取源/返回供连接器） |
| app/routers/api_project_library.py:3732 | POST P/conversation/{node_id}/messages；GET P/conversation/{node_id}/messages/{request_id} | G09 |
| app/routers/api_project_library.py:3807 | PATCH P/canvas | P08；C03/C04/C06/C07/C10/C12/C14/C16/C17；G01/G07/G08/G09；A11（人工保存，不是通用 Agent 工具） |
| app/routers/api_project_library.py:3837 | POST P/local-edit、P/local-edit-tasks | M07 |
| app/routers/api_project_library.py:3934 | POST P/flush；GET P/save-status | P08 |
| app/routers/api_project_library.py:3948 | POST P/history/delete、P/history/delete-with-media | M15 |
| app/routers/api_project_library.py:3987 | POST P/original/delete、/restore、/delete-project-wide、/restore-project-wide | M16 |
| app/routers/api_project_library.py:4077 | GET P/trash/media、P/trash/media/{asset_id}/preview；POST P/trash/media/restore；DELETE P/trash/media | M13 |
| app/routers/api_project_library.py:4153 | GET P/versions、P/versions/{version_id}/diff；POST P/versions/{version_id}/restore | P06 |
| app/routers/api_project_undo.py:33；app/routers/api_project_usage.py:19 | GET、PUT P/undo-stack；GET P/usage | C19/S04 |
| app/routers/api_projects.py:22 | GET /api/projects；POST /api/projects、/api/projects/{project_id}；DELETE /api/projects/{project_id} | P01；I02（三个写入口 410） |
| app/routers/api_prompt_libraries.py:134 | GET、POST /api/prompt-libraries；PATCH、DELETE /api/prompt-libraries/{library_id}；POST /api/prompt-libraries/items；PATCH、DELETE /api/prompt-libraries/items/{item_id}；POST /api/prompt-libraries/items/delete；POST /api/prompt-libraries/categories；PATCH、DELETE /api/prompt-libraries/categories/{category_id} | R04 |
| app/routers/api_providers.py:205 | GET、PUT /api/providers；POST /api/providers/test-connection、/probe-async、/fetch-models；GET /api/providers/{provider_id}/fetch-models | S01 |
| app/routers/api_runninghub.py:49 | GET /api/runninghub/app-info、/query；POST /api/runninghub/submit、/upload-asset | G13 |
| app/routers/api_search.py:34 | GET /api/search/projects | P07 |
| app/routers/api_shared_folders.py:114 | GET、POST /api/shared-folders；DELETE /api/shared-folders/{folder_id}；GET /api/shared-folders/{folder_id}/tree、/file；POST /api/shared-folders/import | R05 |
| app/routers/api_smart_canvas.py:26 | GET /api/smart-canvas/prompt-templates；POST /api/smart-canvas/group-export | R04/M18 |
| app/routers/api_storage_files.py:35；app/routers/api_storage_settings.py:49 | GET /api/storage-files、/api/storage-files/{kind}/{rel_path:path}；POST /api/storage-files/delete；GET、PATCH /api/storage-settings | R06/S01 |
| app/routers/ws_stats.py:25 | WS /ws/stats | I01（浏览器/PS 通知、项目注册；非业务操作工具） |
| app/routers/http.py:8；app/routers/shutdown.py:8；app/routers/startup.py:27；app/routers/unknown.py:8 | 无 HTTP/WS 声明；startup/unknown 为生命周期/异常支撑，http/shutdown 空 router；未虚构 feature | I01，非业务范围 |

## 47 工具反向索引

| manifest 工具（精确名） | feature／参数事实来源 |
|---|---|
| agent.present | A09/X02；`app/services/agent_present.py:56`；manifest 引用 PRESENT_TOOL |
| chat.history | A05/A06；`app/services/agent_tools_manifest.py:123` |
| chat.search | A05；`app/services/agent_tools_manifest.py:125` |
| plan.import_excel | X01；`app/services/agent_tools_manifest.py:127` |
| plan.edit_preview | X02；`app/services/agent_tools_manifest.py:129` |
| plan.apply | X03；`app/services/agent_tools_manifest.py:134` |
| ops.list | A10/C19；`app/services/agent_tools_manifest.py:137` |
| ops.undo | A10/C19/M13；`app/services/agent_tools_manifest.py:138` |
| canvas.list_nodes | C01；`app/services/agent_tools_manifest.py:139` |
| canvas.get_node | C01；`app/services/agent_tools_manifest.py:144` |
| canvas.create_node | C02/G10；`app/services/agent_tools_manifest.py:147` |
| canvas.update_node | C03/C04/C16/G01/A03/A11；`app/services/agent_tools_manifest.py:157`（后两项只有对照边界，不表示配置/停靠已覆盖） |
| canvas.delete_node | C09/C10；`app/services/agent_tools_manifest.py:168` |
| canvas.connect | C07；`app/services/agent_tools_manifest.py:170` |
| canvas.disconnect | C08；`app/services/agent_tools_manifest.py:175` |
| canvas.place_media | M02/M14；`app/services/agent_tools_manifest.py:176` |
| canvas.attach_refs | M03；`app/services/agent_tools_manifest.py:184` |
| canvas.set_ref_order | M03；`app/services/agent_tools_manifest.py:198` |
| canvas.group_create | C13/C12；`app/services/agent_tools_manifest.py:80` |
| canvas.group_arrange | C13/C12；`app/services/agent_tools_manifest.py:84` |
| canvas.arrange_groups | C11/C13；`app/services/agent_tools_manifest.py:86` |
| canvas.align | C11；`app/services/agent_tools_manifest.py:88` |
| canvas.distribute | C11；`app/services/agent_tools_manifest.py:90` |
| canvas.search_projects | P07；`app/services/agent_tools_manifest.py:210` |
| canvas.view | M04/M18；`app/services/agent_tools_manifest.py:212` |
| canvas.view_media | M04/G12；`app/services/agent_tools_manifest.py:213` |
| canvas.edit_crop | M05；`app/services/agent_tools_manifest.py:215` |
| canvas.batch_prompt_edit | C16；`app/services/agent_tools_manifest.py:216` |
| canvas.batch_edit | G03；`app/services/agent_tools_manifest.py:235` |
| canvas.edit_rotate | M05；`app/services/agent_tools_manifest.py:237` |
| canvas.edit_flip | M05；`app/services/agent_tools_manifest.py:238` |
| canvas.edit_outpaint | M06；`app/services/agent_tools_manifest.py:239` |
| canvas.edit_erase | M07；`app/services/agent_tools_manifest.py:240` |
| generate.trigger | G02；`app/services/agent_tools_manifest.py:241` |
| generate.retry | G04/G06；`app/services/agent_tools_manifest.py:242` |
| generate.preflight | G03；`app/services/agent_tools_manifest.py:243` |
| generate.request_batch | G03；`app/services/agent_tools_manifest.py:244` |
| generate.batch_status | G03；`app/services/agent_tools_manifest.py:245` |
| generate.stop_batch | G03/G05；`app/services/agent_tools_manifest.py:246` |
| generate.status | G04/G06；`app/services/agent_tools_manifest.py:247` |
| generate.wait | G04；`app/services/agent_tools_manifest.py:248` |
| media.upload | M01/A12；`app/services/agent_tools_manifest.py:249` |
| media.list | M02；`app/services/agent_tools_manifest.py:250` |
| media.delete | M11/M13/M16；`app/services/agent_tools_manifest.py:251` |
| viewport.focus | C15；`app/services/agent_tools_manifest.py:252` |
| viewport.zoom | C15；`app/services/agent_tools_manifest.py:253` |
| viewport.state | C15；`app/services/agent_tools_manifest.py:254` |

## JS 文件范围、排除与未决项

| 范围 | 说明 |
|---|---|
| 主用户动作 | `static/js/smart-canvas.js`：项目/画布、参数、素材/模板、图片/视频、输入引用、选区/快捷键/拖拽、保存/恢复/导出；其动作委托至 node-ops/connections/generation/image-editor/media-history/undo，已分别对应主表，不把 w/h、内部 grid、主画布排版混为一类。 |
| Agent 文件 | canvas-agent-tui/activity/completion/chat/navigation 分别覆盖 A01—A13、C14/C15、X02 接缝差异；agent-navigation 仅页面阅读。没有静态调用人工 plan/previews REST 的完整编辑界面，不能用后端通过代替 UI 验收。 |
| 其余用户入口 | canvas-annotations/quick-edit/search/download/layers、excel-import、project-list、global-settings/preferences、image-preview、theme/touch-mouse/escape-stack/ui-dialog/ui-toast；纯本地按钮、键盘、复制粘贴也入表。 |
| 支撑非独立功能 | canvas-core/state/node-types/layout-utils/geometry/placement/snap/vector-path、project-canvas-mutations、outpaint-ratios：状态/几何/身份/合并/布局支撑，无额外独立 HTTP 授权；snap 的手势功能见 C04。i18n.js/i18n-core.js 与 i18n/ 字典只是本地化支撑；i18n/validate-i18n.js 是 Node 校验脚本，不是用户动作。 |
| 明确范围外 | 不把依赖库、工具脚本、原生宿主受限文件工具计入 static/js 或 47 工具；静态 HTML 内嵌脚本不作为本次 JS 文件枚举的额外全集。对应 router 能力仍保守入表，未因缺 JS 消费者删除。没有分析用户数据或运行目录来补可达性。 |
| unknown / 待裁定 | R05/R06/S04 当前页面直接可达性、X02 完整动态 UI 消费者无法由本范围证明；不声称全 UI 实测覆盖。节点设置/会话控制、全局库/查询/维护、导出目标等是否提供给 Agent，均留人选择；不由本调查创设禁止或授权。 |
