# #459：shell 调用内部 API 与生成 MCP 的研究归档

**状态：PARTIAL。不能据本文关闭 #459。** 本文按已裁定的方案 A，仅将既有研究整理为一份 Markdown；不是新产品、实现决策或新实验。真实会话缺口频次、真实 Agent helper A/B、受限宿主的单命令放行与 loopback 连通性仍为 **NOT_RUN / UNKNOWN**。

本轮只归档既有研究，解析原材料、计算 SHA256 并核对源码；没有在新 main 重跑研究 probe、研究生成器、历史类型检查或真实 Agent，没有启动研究服务、浏览器或联网采集。归档质量检查另列交付记录，不计入下述研究实证。Git 已核对 main 基线为 `d4f903d3d667263321bf1246aca1aac9dd5858aa`（#472）；本文不修改产品、宿主配置、领域文档、ADR 或导航。

## 1. 问题、硬约束与建议

票面与两条用户补充见 [#459](https://github.com/miaomiaozii/Firefly-Canvas/issues/459)、[helper 要求](https://github.com/miaomiaozii/Firefly-Canvas/issues/459#issuecomment-7032715250)、[shell 定向](https://github.com/miaomiaozii/Firefly-Canvas/issues/459#issuecomment-7037960624)；历史原文快照为 E08。

- **复用现成 bash / PowerShell**，让 Agent 调现有 HTTP（curl / Invoke-RestMethod）或薄 HTTP 客户端；不造 shell 工具、命令代理、脚本执行服务、正则命令解释器或另一套 sandbox。
- **不论传输方向，helper 必须从同一能力定义生成**，包含用途、参数、示例、常见错误及改法、任务到命令的索引；不能另维护一份手写说明书。真实 Agent 有无 helper 的对照要求仍未完成。
- 撤销、确认、操作归属与服务端权限继续复用既有 Agent core；不能仅包装 UI 路由或给 HTTP 加一层日志就宣称等价。

**工程建议，尚非产品决定：**以现成 shell 直接调 HTTP 为最小对照基线；需要稳定参数、JSON 错误和自助说明时，再考虑薄 firefly CLI。先补齐既有共享工具定义中的请求映射与 helper 元数据，再从同一定义生成客户端、OpenAPI 描述和 MCP。保留现有 MCP 兼容，不直接把全量 OpenAPI 当 Agent 白名单。

| 比较维度 | shell + curl / Invoke-RestMethod | shell + 薄 firefly CLI（提案） | 白名单生成 MCP（提案） |
|---|---|---|---|
| 维护成本 | 无新执行器；URL、body、身份和错误说明仍要维护 | 可生成参数、请求绑定、help；业务 core 与事务测试不会自动产生 | 当前 OpenAPI 不完整，须先补语义定义与绑定；不是导入 schema 即完成 |
| 安全性 | 广泛放行 HTTP 客户端会扩大可请求 URL/方法范围 | 可不暴露 arbitrary URL、header、exec 或脚本参数；客户端限制仍不是鉴权 | 工具白名单更清晰，但不限制同机进程直接访问 REST；annotations 不是权限 |
| 撤销与确认 | 调 Agent 入口可复用；误调 UI 通路不等价 | 同左，不能伪造撤销、自动批准或盲目重试写入 | 必须绑定同一 Agent core、归属与确认入口，不能统一装饰任意路由 |
| 使用特性 | 引号、query/body 和 HTTP 错误需处理；可配生成 helper | 稳定命令及结构化输出可能便于使用，但尚未实现/测量 | 结构化参数发现、图片 content blocks 是既有协议特性；不是本次使用效果排名 |
| 已知限制 | 只存在历史进程内 HTTP 探测，受限 shell 连通性 NOT_RUN | CLI 与真实使用效果 NOT_RUN / UNKNOWN | 现有 MCP 的发现/拒绝有历史证据；新生成器及真实效果 NOT_RUN |

比较依据为 E01–E06、E10、E15–E22 及下文源码；没有测得任何胜率、耗时改善或 token 节省。

## 2. 版本与外部证据的边界

### 2.1 三种版本不能混用

| 对象 | 版本/定位 | 可支持的说法 |
|---|---|---|
| 实际历史探测 | `5ccb7657d83c70dffe01cd4aa80bf4feb0f6e41e`（E01、E06 的 `head`） | §3 的 schema 库存、13 项 ASGI 断言及源码字段发现只属于此快照 |
| 旧来源工树冻结点 | `D:/ff-wt/a-402-442-457`，Git 核对为 `02bf90690165fb6efaeeddd0f74a9bce24ea4caa`，归档前后均干净 | 保留旧分支作为来源、不合入；不表示 probe 在该冻结提交运行 |
| 当前归档目标 | `D:/ff-wt/a-459-markdown-only`，从 main `d4f903d` 新建 | 只新增本文；不把历史库存/测试变成新 main 的实测 |

原报告提示采集期间研究文件曾有未提交变化，因此 `head` 本身不保证“纯净 HEAD 构建”。**历史源码定位和文件身份以外部 E06 `source-snapshot.json` 的 SHA256/符号记录为准**，不能仅靠行号或提交简称。E06 是哈希与符号索引，不包含完整源码正文。

归档时逐项比较 E06 已列源码的 SHA256 与目标工树文件，发现以下两处不同；其余已列文件相等。该比较只是静态文件核对，不是全仓差异证明、schema 重生成或运行结果等价证明。

| 与历史快照不同的目标文件 | 亲见的相关变化 | 对归档的影响 |
|---|---|---|
| [app/routers/api_providers.py](../../app/routers/api_providers.py)，`comfyui_test_connection` | #472 的 Bearer 请求、返回结构/能力检查和分类诊断 | 不将旧 schema/诊断材料称为新 main 验收 |
| [app/services/generation.py](../../app/services/generation.py)，`comfyui_cancel_prompt`、`generate_comfyui_provider_image` 等 | 增加服务商鉴权、响应检查、错误处理及撤单结果区分 | 尤其不能把旧快照推广为当前服务商效果或取消验证 |

本次核对中，§3 所涉共享定义、Pi 手写执行器及 Agent 路由文件与 E06 相等；这支持源码发现仍可定位，**不等于在新 main 重现了 HTTP 回包或真实会话故障**。下文产品链接只指当前仍存在的文件，并在文字中标符号，不沿用原报告可能漂移的 `#L...`。

### 2.2 归档位置与可复核性

唯一入库正文是本文。原材料已另存至 **`D:/ff-qa/459-research/raw/<原文件名>`**，完整旧报告另存 **`D:/ff-qa/459-research/original-report.md`**；完整逐文件清单为 **`D:/ff-qa/459-research/sha256.json`**，来源链为 **`D:/ff-qa/459-research/archive-provenance.json`**。§8 列明每个外部文件、用途及已核验的完整 SHA256。

这些是**外部绝对路径，不随 main 入库**；缺少外部档案时，本仓库不能独立复核原始回包。旧完整报告仍保留历史相对链接，只作为原件，不作为当前可点击证据索引。本文不继续使用旧的相对原材料路径。

已逐字节核对 28 个 raw 文件与旧报告，共 29 份原件；`sha256.json` 包含这些原件及来源链，共 30 项（不包含清单自身及后续 QA 回执）。raw 字节同时等于旧工树文件与对应 Git blob。旧报告保留工树原始 CRLF 字节，与 Git blob 仅有换行规范化差异；未改写原件、旧分支或 `.gitattributes`。

| 外部文件 | SHA256 |
|---|---|
| `D:/ff-qa/459-research/original-report.md` | `44a66f6187f772c33d2507491d2aaa1e47a12f2299c563a44fdc28f94b9b183e` |
| `D:/ff-qa/459-research/sha256.json` | `38a367ecb5f30f026781578408ee0e54f3fc11500f20b2d9acc7542c6ae6dbc1` |
| `D:/ff-qa/459-research/archive-provenance.json` | `4f6d1dc3801b04155128714454d4525cfae471f0f1ae4494d5396224a492776b`；记录旧冻结点、归档基线与原件 Git 身份 |

本轮已 grep 核对 reviewer `01a1ef85` 在 `D:/Firefly-Canvas/.local/agent-work/channel/review-batch/MESSAGES.md:7129–7133、7220–7224` 的方案 A 裁定与开工通知，来源链分别登记。E11 只保存更早的 `6830–6832` 排队授权，**不是本轮裁定的替代证据**。

## 3. 历史方法与实际结果（全部属于 5ccb765 快照）

### 3.1 方法与可重跑限制

已阅读原采集脚本 E07：在导入产品前覆盖 runtime/projects、HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP、Codex/Pi 目录到独占 QA 根；设置 `FIREFLY_TEST_MODE=0`，禁 CLI 探测和预热。审计 hook 安装后拒绝外部连接、子进程及约定目录外写入；脚本在 hook 前曾用 git 读取版本，不应概括成“整个历史研究从未启动研究工具”。不启动 lifespan/迁移，用 `httpx.ASGITransport` 在进程内访问应用。

E01 记录没有 HTTP listener、没有项目 `canvas.json`、Agent 宿主/模型调用为 0。`http://127.0.0.1:3199` 是 ASGI 请求 URL，不是已监听的 Firefly 服务；Windows asyncio 在 3198 使用过内部 wakeup socket，不是产品 HTTP 端口。整体异步探测上限 45 秒、每个请求外层上限 5 秒。它证明有限发现/提前拒绝，不证明业务写入、确认、撤销或真实 shell 可用。

历史 `probe.py`、两个采集器和 `verify_artifacts.py` 仅保留为字节保真证据。它们从 `__file__` 推导原仓库根并依赖 `firefly_json_io`、产品源码、旧报告/材料布局；**不能直接在外部 raw 路径运行**。两个采集器还会联网/调用 gh 或读取旧授权来源，校验器会运行类型检查。将来如获准复跑，须使用匹配 E06 的旧源码和原布局、把副本与输出放入新隔离目录、重新审核写入/网络边界；不得写入冻结旧工树或把外部档案当执行目录。本归档不提供错误的外部路径重跑命令，也不授权复跑；真实 Agent、计费或用户数据实验须另行批准。

E25、E26 以及旧报告中的定向测试、材料链接检查与 typecheck 都是**历史结果**，本次未执行；它们不能升级为当前归档或新 main 的项目测试通过。

### 3.2 库存，不是能力缺口频次

以下数字已只读解析 E02–E05，与 E01 交叉核对。HTTP operation 的单位为 method + path template，语义工具按名字计，不含 WebSocket、静态挂载或未公开在 schema 的路由。

| 5ccb765 历史指标 | 数量与含义 |
|---|---|
| OpenAPI paths / HTTP operations | 229 / 260（包含根页面） |
| `/api/` operations | **259**；GET 85、POST 137、PATCH 15、PUT 3、DELETE 19 |
| MCP/共享定义工具 | **46**；E01 记录 MCP 实现名字集合与共享定义相等 |
| 工具对应的不同 REST operations | **44**（method + E05 `schema_path` 去重）；plan 三动作共享 `POST /plan/{operation}`，不是按展开动作后的请求 `path` 去重 |
| body-method 工具映射缺 `requestBody` | **33 / 33**；按 POST/PATCH/PUT 的语义工具映射计，实际这组为 POST/PATCH，不是 33 个不同路由 |
| 结构化 helper 字段 | 46 个定义均有 description、根对象拒额外字段；结构化 examples、errors、tasks/intents 均为 0 |
| OpenAPI 安全声明 | `securitySchemes` 为空，声明 security 的 operations 为 0；不等于服务端没有守卫 |

**不能用 259−46 算“213 个未暴露能力”**：资源路由、语义工具、共享分派、UI/维护接口不是同一个统计单位。没有合格真实会话分母，就没有需求频率、失败率或能力缺口排行榜。`WRITE_TOOLS=25` 和 `_JOURNALED_TOOLS=15` 只是当时 MCP 物化承载节点与画布记账的实现集合，也不是全 API 的安全/可撤销分类器（E01、E06）。

### 3.3 原始 13 项 ASGI 断言

E10 的请求、状态和回包与 E07 中的断言逐项对应；下表是**历史通过记录**，不是本次执行：

| E10 `label` | 历史结果 |
|---|---|
| `openapi`、`mcp_initialize`、`mcp_tools_list` | 各 200；工具数 46，完整定义另存 E04，不把名字表当完整 wire 回包 |
| `no_tools_describe` | HTTP 200，但 JSON-RPC `-32601`，不是 helper 调用成功 |
| `unknown_tool` | HTTP 200，工具结果 `isError:true`、结构化 `INVALID_ARGUMENT` |
| `mcp_get` | 405，GET 不可用；不据此推断其他传输形式 |
| `host_denied`、`origin_denied`、`null_origin_denied` | 各 403 |
| `preflight_denied` | 跨源 OPTIONS 403，无 CORS 放行头 |
| `missing_write_header`、`missing_agent_identity` | 各 400 `INVALID_ARGUMENT`，创建提前拒绝 |
| `missing_project_read` | 404 `PROJECT_NOT_FOUND` |

这 13 项没有成功业务写入，也没有批准、拒绝、等待超时、重启、撤销或跨项目操作实测。E01 另记专用 dependency 接受任意非空 `X-Agent-Project`，是直接调用 dependency 的观察，不属于跨项目 HTTP 成功证据。

### 3.4 两个生成一致性风险

1. **OpenAPI 缺 body / 手读 query。** E02/E05 中 `canvas.update_node` 的 REST schema 没有 JSON body，而 E04 定义了 `node_id/title/text/prompt/x/y/w/h/runSettings`；`canvas.align` 仅公开 project path 参数。[api_agent_tools.py](../../app/routers/api_agent_tools.py) 的 `update_node`、`_layout_route` 使用 `request.json()`，`list_nodes` 手读 `group_id/created_by/fields`，这些内容不会自动出现在该 OpenAPI。不能只按函数名、operation ID 或 HTTP 方法还原完整语义。
2. **描述生成不等于执行映射正确。** [agent_tools_manifest.py](../../app/services/agent_tools_manifest.py) 的 `TOOLS` 驱动 MCP 与 Pi 描述/schema，[gen_canvas_tools.py](../../tools/gen_canvas_tools.py) 生成 Pi 定义；但 [canvas-tools.ts](../../app/agent_extensions/canvas-tools.ts) 的 `EXECUTORS["canvas.update_node"]` 只将 title/text/prompt 写入 `updates`，未转发已声明的 x/y/w/h/runSettings。E01 为源码字段比较，不是真实调用失败频次；当前静态核对该文件仍与 E06 相等。历史 codegen freshness exit 0 并不能发现漏转发。

E27 的重复 operation ID 告警进一步说明不能把自动 ID 当唯一安全身份。**建议**在现有共享定义上补齐稳定工具标识和 method/path/参数绑定，并逐参数检查转发；不另建一套业务实现。E28 是早期将 plan 动作误当独立 schema path 的失败轨迹，后来脚本改用真实 `/{operation}`；不是最终探测失败，也不是产品故障。

## 4. 语义边界：不是给 UI 路由套一个包装

本节是历史源码/现有合同审阅，不是逐路由业务实测。主要定位为 E06、[api_agent_tools.py](../../app/routers/api_agent_tools.py)、[api_mcp.py](../../app/routers/api_mcp.py) 的 `_tools_call`，以及 [ADR 0022](../adr/0022-mcp-streamable-http-canvas-endpoint.md) 的 #456/#402/#457 修订。

| 区别 | 已有语义与必须保留的限制 |
|---|---|
| 撤销 | [agent_ops_context.py](../../app/services/agent_ops_context.py) 的 `agent_ops_scope` 只分配归属/op_id，不捕获修改、不写日志、不授予权限。Agent mutation 经 `_apply_mutations_from_snapshot`、CAS 和 [AgentOpsJournal](../../app/services/agent_ops_journal.py) 等待权威画布持久化再结算日志；不是任意 UI 请求都可自动撤销 |
| 冲突与外部效果 | [agent_ops_undo.py](../../app/services/agent_ops_undo.py) 的 `undo_ops` 校验 after、活动任务和媒体回收身份，不覆盖人工后续修改；画布逆差异不撤回已付费服务商效果，也不保证撤去媒体导入等外部效果，无跨文件事务承诺 |
| 撤销调用 | [api_agent_ops.py](../../app/routers/api_agent_ops.py) 的 `_validate_args` / `undo_ops_core` 要求 op_id、turn_id 恰传一个；冲突为 409 `UNDO_CONFLICT`，仍在生成则拒绝撤销。HTTP 成功状态不能替代业务结果判断 |
| 确认 | [agent_confirmation.py](../../app/services/agent_confirmation.py) 的 `request` / `_owned_entity`：新默认 standard；autonomous 不免安全底线。删除媒体、删除非本 Agent 本轮创建或已被人工接管的节点、断开非本 Agent 创建的边、批量改词仍须相应批准；豁免只信服务端已提交创建差异 |
| 待决与批准有效性 | `resumable` / `WaitingOperation.check` / `Origin.valid`：花钱门可立即回 `CONFIRM_PENDING`、`awaiting_confirmation`、confirm_id，原执行继续等待；非花钱 90 秒是同步响应窗口，不是自动拒绝/批准有效期。批准后仍检查宿主、generation、用户轮、输入/服务商与 CAS；待执行处理器不承诺重启恢复。客户端不得重提原写入 |
| 同轮归属 | REST `_rest_agent_call` 和 hosted MCP 从服务端取逻辑用户轮；外部 MCP 可传本轮独有 turn_id，但 session/request ID 不是用户轮。系统确认续接仍归原轮，薄 CLI 不能自造轮 ID 冒充 hosted 会话 |
| 项目范围 | 项目路径/绑定负责路由；[api_search.py](../../app/routers/api_search.py) 的 `search_projects` 是现有显式跨项目例外，不能声称全部工具被头限制在本项目。#457 对话读口另校验头与路径一致；不能把某入口校验推广成所有入口授权 |
| 鉴权 | v1 为本机服务 + Host/同源检查，没有 MCP bearer-token 身份认证。`X-Agent-Project` 是上下文，不是 secret/token；`caller=hosted` 也是可伪造归属标签。单命令宿主策略、服务端身份认证、项目路由和业务确认是不同边界 |

[core.py](../../app/core.py) 的 `ensure_agent_same_origin` 与 [main.py](../../main.py) 的 `protect_api_origin` 检查 Host、Origin/Referer；有非法 Origin 或跨源即拒绝，无 Origin 的编程式请求可以通过相应守卫。这不建立同机进程之间的强身份隔离，也不要求 curl 伪造浏览器 Origin。OpenAPI 没有 security 声明不能单独证明该结论；依据是实现、ADR 与有限 E10 拒绝记录。

### 路由适用性（工程建议）

- **优先研究本项目发现/查询**：nodes、media.list、ops.list、viewport.state 与 #457 对话只读入口；POST view 不因方法名就成为画布写入，但视频视图可能调用 ffmpeg，不应混入最小无子进程连通性探测。
- **画布写入须复用 Agent core**：节点/边、参考图、place_media、布局、批量改词、plan.apply 各有归属、预览/批准、CAS 或日志要求；不是开放同名 UI mutation 即等价。
- **媒体/生成需逐项后果模型**：`media.delete` 复用项目回收区身份（7 天、占用/过期/缺失拒绝恢复、不改画布引用），见 [agent_ops_media.py](../../app/services/agent_ops_media.py)、[firefly_projects_deletion.py](../../firefly_projects_deletion.py) 的 `recycle_agent_media` / `prepare_agent_media_restore`。上传、几何编辑、异步生成各有专门生命周期；paid、batch、plan 的绑定/预检不能被通用 POST 重试替代。
- **不直接白名单 UI/维护入口**：[api_project_library.py](../../app/routers/api_project_library.py) 的 `mutate_firefly_canvas` 直接走 store mutation；[api_project_undo.py](../../app/routers/api_project_undo.py) 的 `put_project_undo_stack` 属另一套持久栈。清理/永久删除、全局设置、宿主控制与跨项目搜索不进入建议最小一期。

若将来要求“只允许当前节点、本项目的指定能力”，需评估既有宿主权限和服务端绑定；可讨论短期最小范围凭据，但那是后续安全设计，**本文不引入新鉴权决定或 ADR**。客户端藏参数不是权限实现，环境变量对同权限 shell 也不保密；非 loopback 开放须先满足 ADR 0022 的鉴权修订前提。

## 5. 受限宿主：官方事实不等于本机生效

| 亲见缓存/源码 | 可支持的结论 | 不能推断的结论 |
|---|---|---|
| Codex rules、固定 commit execpolicy（E16/E15） | argv 前缀匹配 allow/prompt/forbidden，最严匹配优先；绝对路径与可选 basename resolution 有区别。官方规则定位为控制 sandbox 外命令 | `allow firefly` 不等于 deny-everything-else，也不证明 sandbox 内只能执行一个程序 |
| Codex shell 拆分说明（E16） | 简单 bash/sh/zsh 线性链可逐条检查；变量、替换、重定向和控制流等复杂脚本按 shell wrapper 整体处理 | 不能为方便放行通用 `bash -c` / PowerShell 前缀；也不是所有 PowerShell 语法的安全证明 |
| Codex 安全/网络说明（E17） | sandbox、approval 和命令网络权限分层；默认命令网络关闭，开启 proxy 不自动授予网络，本地目标另受限制；命令网络控制不自动禁用 MCP | MCP 可用不证明 shell HTTP 可达，不可为实验切 danger-full-access 或无边界开网络 |
| 上游 Pi 安全文档、bash/PowerShell 源码（E18–E20） | Pi 使用启动账户权限；cwd/project trust 不是工具执行隔离。bash 交给已有 shell spawn，PowerShell 复用同一 shell 后端 | 不能从启用 bash 推断单命令白名单；不能把上游行为当成本机部署配置 |
| Pi permission-gate 示例（E21） | 示例只拦少数危险命令正则 | 不是完整 allowlist/sandbox，不可拿来证明只能执行 firefly |
| Firefly 适配器（E06） | [CodexAdapter.build_argv](../../app/services/agent_adapters/codex_adapter.py) 构造 `codex exec -s read-only` 并注入 MCP；[PiAdapter.build_argv](../../app/services/agent_adapters/pi_adapter.py) 使用 `--no-builtin-tools` 与 canvas/generate/media/viewport 工具选择 | 不证明 shell 已启用、用户宿主规则已生效，源码注释中的版本/旧实测也不是本次部署测量 |

**UNKNOWN：**本机实际宿主版本、部署 fork/策略与上游差别、现有权限机制能否严格只放行一个可执行文件、bash/PowerShell 到 Firefly 的 loopback 可达性。本次没有探测 Codex/Pi CLI 版本、读取其个人配置、运行 codex sandbox 或真实 Pi/Codex 会话。

### 官方原始来源（本次只读既有缓存，未联网刷新）

E09 保存抓取 URL、最终 URL、响应、字节数与 SHA256；对应缓存的完整哈希列在 §8。HTML 为抓取时内容，不是固定版本或本机版本。固定 commit 只适用于下列源码 URL。

| 缓存 | 官方原始 URL / 固定版本 |
|---|---|
| E15 `codex-execpolicy.txt` | https://raw.githubusercontent.com/openai/codex/7edf91b13c54f20aab22fece0abfa4a5c28379b3/codex-rs/execpolicy/README.md |
| E16 `codex-rules.html` | https://developers.openai.com/codex/rules/ （E09 最终 URL： https://learn.chatgpt.com/docs/agent-configuration/rules ） |
| E17 `codex-agent-security.html` | https://developers.openai.com/codex/agent-approvals-security/ （E09 最终 URL： https://learn.chatgpt.com/docs/agent-approvals-security ） |
| E18 `pi-security.txt` | https://raw.githubusercontent.com/badlogic/pi-mono/780bf31fcaeb252fa9b68df3964c735eae75bde3/packages/coding-agent/docs/security.md |
| E19 `pi-bash.ts` | https://raw.githubusercontent.com/badlogic/pi-mono/780bf31fcaeb252fa9b68df3964c735eae75bde3/packages/coding-agent/src/core/tools/bash.ts |
| E20 `pi-powershell.ts` | https://raw.githubusercontent.com/badlogic/pi-mono/780bf31fcaeb252fa9b68df3964c735eae75bde3/packages/coding-agent/src/core/tools/powershell.ts |
| E21 `pi-permission-gate.ts` | https://raw.githubusercontent.com/badlogic/pi-mono/780bf31fcaeb252fa9b68df3964c735eae75bde3/packages/coding-agent/examples/extensions/permission-gate.ts |
| E22 `mcp-tools.html` | https://modelcontextprotocol.io/specification/2025-06-18/server/tools |

MCP 规范支持 name/description/inputSchema、tools/list、工具执行错误及图片 content blocks，且要求将非可信服务器 annotations 视为不可信。产品 `_tool_result_content_blocks` 见 [api_mcp.py](../../app/routers/api_mcp.py)。规范和 E10 均不支持把 `tools/describe` 当本产品已实现的 JSON-RPC 方法。

## 6. Helper 与真实 A/B：仅设计提案

### 6.1 同一定义生成的内容

建议扩充现有 `agent_tools_manifest.TOOLS`，而非新增手写帮助注册表；以下字段与产物都是**提案，未实现、未运行**，不新增领域术语或命令执行层：

- 用途、参数 schema：必选、范围、缺省语义、互斥项；不能只复制函数名/docstring。
- method/path 与参数到 path/query/body 的绑定，服务端注入的项目/节点/轮次身份；不能让模型随意提供身份以替代绑定。
- 最小、完整及边界示例：同定义维护并校验 schema，不能把随机合法 JSON 当可用业务例子。
- 每工具错误条件、统一 code 与恢复动作；任务索引说明“想做 X 用什么，先读什么，下一步是什么”。
- 确认/等待收据、paid、项目范围、撤销限制和不可逆效果说明，以及定义版本/哈希；这些元数据不是客户端授权机制。

可由此生成 `firefly help [命令]` 的人读/JSON 自助说明、短版会话索引、CLI/HTTP 映射与 MCP 描述；这里的命令名只是候选界面，不是现有可运行产品。若 MCP 需要按需自助工具，应作为普通 helper tool 设计，不虚构非标准 `tools/describe` 已存在。

例：`canvas.align` 的说明应区分“对齐卡片”与“移动整组”，列出 node_ids、edge 及 left 等枚举/例子，提示检查受保护节点跳过结果，并在组框任务索引指向 group_arrange/arrange_groups。语义依据为现有 [agent_tools_manifest.py](../../app/services/agent_tools_manifest.py) 的 `LAYOUT_SCHEMAS` 与 E04；这不是已建 helper 或成功会话。

公共恢复说明应包括：`CONFIRM_PENDING` 等待原操作，不重复 POST；`CONFIRM_REJECTED` 不原参强重试；`CANVAS_CONFLICT` / `UNDO_CONFLICT` 先读状态；`BUDGET_EXCEEDED` 不自动加预算。具体条件须从 owning handler 和 [agent_errors.py](../../app/services/agent_errors.py) 归一引用；当前统一错误定义并非完整 per-tool 索引，`UNDO_CONFLICT` 在 undo handler 另定义，不能声称已有完整自动生成错误目录。

建议生成检查覆盖每个参数的实际 forwarding、示例合法性、任务/错误索引完整性、结果与确认收据等价及 freshness；仅比较名字或生成文件新鲜度不足。这些检查本归档均未运行。

### 6.2 真实语料资格与实验设计

E12/E13 的旧 #356 摘录只有 **1858 bytes**，有 3 个 user、3 个 assistant、4 个 toolResult 文本标记；两处“画布上没有可查看的媒体节点”，后续又有节点列表和视图元数据。SHA256 为 `d6fb6c0884f81b3cf7c79046101e32e71dd20f94b619955f376e13b5ff1f6290`。

它没有完整 tool call/args/ID、宿主/模型版本或可核验会话 provenance，**真实来源 UNKNOWN，不当完整真实会话，不进入频次或 helper 效果统计**。两处错误可能是已有视图能力遇到无媒体初态，不能计作两个 API 缺口；标记数也不是运行次数或失败率。本次只读归档，不追索个人 sessions、不重连旧项目。

后续若另获授权，可采用以下协议；**不是本次已执行计划或效果报告**：

1. 先取得明确授权、可核验来源的完整 Codex/Pi 会话导出，记录 allowlist、哈希、宿主/模型/时段、脱敏和保留许可。按逻辑用户轮标注请求动作、所选工具、失败原因、等价 REST，分别统计唯一受阻轮数、尝试数及分母；拆分缺能力、选错工具、参数错、宿主阻断、确认待决/拒绝、业务冲突与服务商失败，不把所有 error 算缺口。
2. 先固定传输比较 helper on/off，再固定 helper 比较 shell HTTP/薄 CLI/生成 MCP，避免两种变化混杂。固定模型、宿主版本/策略、输入和独立 QA 初态，fresh 会话、交叉随机顺序，预登记任务与重复次数，不预设胜方。
3. 不触发生图计费的隔离任务可覆盖查询、完整参数读取、创建/移动/对齐、批量改词预览、冲突后停止和撤销；Agent 模型本身的调用预算仍须另批，人工确认/删除须预先安排。不得操作用户项目或默认纳入计费生成。
4. 指标预定义为选错工具、参数/选择纠错后重试、重复待决写入、首次成功率、最终正确率、调用数、耗时及实际 token/成本；公布分母、失败和 NOT_RUN。正常分页、已知待决或用户拒绝不记作“调错工具”。目前没有任何测量值。
5. 宿主先测无副作用正反例：允许命令 QA GET、非白名单程序、绝对路径/别名、shell 组合/替换/重定向、PowerShell 管道、跨项目与伪造头、非 loopback 地址。复用现有权限机制，不放行通用 wrapper、不改个人配置、不自造执行层；严格单命令策略若表达不了，就报告限制。
6. 留存获准脱敏的真实 user turn、tool call/args/result、轮次关联、helper 版本/展示时点、确认/撤销事件、stdout/退出码及独立 QA 结果；原记录 ID 可回溯且不含凭据。真实宿主或花钱实验仍需单独批准。

## 7. 建议的后续门槛与未完成项

**一期仅建议**：先生成同源 helper/请求绑定，验证本项目 nodes、media.list、ops.list 等只读 shell 连通性；再考虑小范围节点/布局与 ops.undo。paid、媒体导入/删除、plan.apply、跨项目和维护功能不默认扩面。先过参数转发、项目边界、人工后续编辑撤销冲突、待决不重提及真实 helper 对照门，再由维护者决定是否实施。

| #459 要求 | 目前状态 |
|---|---|
| schema、工具映射与有限 ASGI 发现/拒绝 | 有 5ccb765 历史证据；不是新 main 或真实使用验收 |
| 真实 Codex/Pi 缺口频率及 REST 对应排名 | **NOT_RUN / UNKNOWN**，旧 #356 摘录不合格 |
| 真实 Agent 有无 helper 的重试/选错工具对照 | **NOT_RUN / UNKNOWN** |
| 受限宿主严格单命令放行、shell loopback 连通性 | **NOT_RUN / UNKNOWN** |
| CLI / 新生成 MCP / 新 helper 产品实现 | **未交付；不在本次归档范围** |
| 逐业务路由确认、撤销、跨项目和鉴权验证 | 有源码/合同分析，缺本票真实业务实测 |

因此只能提交研究归档，**不能关闭 #459**。本文不批准后续产品、配置或安全边界变更，也不以 #472 合入替代本票缺失的实证。

## 8. 外部原件逐项索引与完整 SHA256

以下 **28 个 raw 文件**均已在外部落盘，并核对与只读旧来源及对应 Git blob 字节一致；每行是一对同名归档。原来源统一位于 `D:/ff-wt/a-402-442-457/docs/research/459/`。哈希识别字节，不证明材料中的推论或真实会话来源；E09 自带的官方缓存哈希另已与原文件核对。

| ID | 外部绝对路径 | 支持的结论 / 限制 | SHA256（外部归档字节） |
|---|---|---|---|
| E01 | `D:/ff-qa/459-research/raw/summary.json` | 探测 head、统计、隔离结果、NOT_RUN；PASS 仅为历史探测 | `54e6f62b2f0c4f6ea7069e8d4db9ab30fb31a65ec3129cdc2c1b0b534f201930` |
| E02 | `D:/ff-qa/459-research/raw/openapi.json` | 原 schema、body 缺失和安全声明库存 | `a92371cc5d3e8c0dd5b4cb1c6571ecc077ed3e3e179c632d87594bb640db36ee` |
| E03 | `D:/ff-qa/459-research/raw/routes.json` | method/path、参数/响应与源码符号索引，不是需求排名 | `172745318a0b869435914a79fca9b4d54c4346ca43a2795ddae50586d6fab7f0` |
| E04 | `D:/ff-qa/459-research/raw/tools.json` | 46 个完整共享工具定义、参数和 helper 元数据缺口 | `18ca7863d9adfcf64e31853f765d5a07cb6e4ffa2152f992155f133895afe5cf` |
| E05 | `D:/ff-qa/459-research/raw/tool-rest-twins.json` | 46 工具到 44 operation 的逐项映射、33/33 body 缺失 | `4a7eb34e3a2b856eab5892da97edcfc4844d36b39683cabdb124700d5c94fb63` |
| E06 | `D:/ff-qa/459-research/raw/source-snapshot.json` | 5ccb765 历史源码 SHA256/符号定位权威；不是源码正文 | `0eb354b7e80c892b9b400d06f951c1f91f1b8469e7cdaccadd5abe75bc6ff3c2` |
| E07 | `D:/ff-qa/459-research/raw/probe.py` | 隔离、统计和 13 项断言的方法；不可直接在 raw 路径运行 | `195281cd057c8a1fffcb74ef1f8b4c3853e5374bb3974499757d090e6149bc09` |
| E08 | `D:/ff-qa/459-research/raw/issue-459.json` | 票面、helper 与 shell 两条用户补充；历史票状态 | `34ec862be77bd6f5849455cf40cfef04c7285e5527c9ff671678939a4c701df5` |
| E09 | `D:/ff-qa/459-research/raw/primary-sources.json` | 官方来源 URL、redirect、响应及字节哈希 | `04d530fb47e94dd0ffa7a04745bcede68cec45e42be8b3e4b6e696fa9629706c` |
| E10 | `D:/ff-qa/459-research/raw/asgi-probes.json` | 13 项请求/状态/回包，发现和提前拒绝而非业务验收 | `439b13e022356cfed8326d3a2f6e29a0160388910092c959b4d76763a954ed4a` |
| E11 | `D:/ff-qa/459-research/raw/authorization-excerpt.json` | 旧排队授权 6830–6832；不是本轮方案 A 裁定 | `00d975f2e0f9869f0ab460d6afc97f559d5618726d2c11405e846598a1ac068f` |
| E12 | `D:/ff-qa/459-research/raw/authorized-materials.json` | 旧 #356 标记计数、哈希与不合格语料判定 | `19473da2eaec54f1c41a73a1b42d5836eee3e152afe3f31ed067d849e86a0fda` |
| E13 | `D:/ff-qa/459-research/raw/authorized-356-agent-session.txt` | 来源 UNKNOWN 的旧片段原字节，不作完整真实会话 | `d6fb6c0884f81b3cf7c79046101e32e71dd20f94b619955f376e13b5ff1f6290` |
| E14 | `D:/ff-qa/459-research/raw/collect_authorized_materials.py` | 历史两文件 allowlist 采集方法；本次未执行 | `b9d0e4de7edaee6c11bc8e20690160d4066eb047966ee4766e7177e7b485e556` |
| E15 | `D:/ff-qa/459-research/raw/codex-execpolicy.txt` | 固定 Codex commit 的规则匹配/路径语义 | `b309d14abfb56e673124f61f4b346edd196b6dd661af4229694b1f105e9d87fb` |
| E16 | `D:/ff-qa/459-research/raw/codex-rules.html` | 官方规则与 shell 拆分边界，非本机生效证据 | `087ba5577302254638c2d364aeba57201307722d9ff94a1593b0630adeb9700e` |
| E17 | `D:/ff-qa/459-research/raw/codex-agent-security.html` | 官方 sandbox/approval/命令网络与本地地址限制 | `074911ecf57a0382d069ae3d8e50c22336b2f23f13aaed8b0d7d6abf38b7e10a` |
| E18 | `D:/ff-qa/459-research/raw/pi-security.txt` | 固定 Pi commit 的账户权限与 project trust 边界 | `3b1e40a8d28b17c9eb7e496a7ba88fbe04665e3e6ae02873a58b943e325ad504` |
| E19 | `D:/ff-qa/459-research/raw/pi-bash.ts` | 固定 Pi commit 的现成 shell spawn 后端 | `0210762c8e25d42b6d129577b1f3e6864823f45c3a577163d251135cb8ff0e01` |
| E20 | `D:/ff-qa/459-research/raw/pi-powershell.ts` | 固定 Pi commit 的 PowerShell 后端复用 | `4eb47ec993011120960d73985c4d51ae40a3df91e95d76792100c2f0fe2b434e` |
| E21 | `D:/ff-qa/459-research/raw/pi-permission-gate.ts` | 示例的少量危险正则，不能证明完整命令隔离 | `e32086ba9dd9b4ba0fd308b65241d9affa0b5ecd3f90ef7ea1b9f2b30cab8cc1` |
| E22 | `D:/ff-qa/459-research/raw/mcp-tools.html` | MCP 2025-06-18 工具发现、结果、annotations 信任限制 | `4024845a37e8e76816747dad62d3345fcfd6c124ca0828939f46d778b55e93f5` |
| E23 | `D:/ff-qa/459-research/raw/collect_sources.py` | 历史 URL/gh 采集方法；本次未联网执行 | `ecb47917251ac90cf8e078495ecae9c26b3c9b2e5e5cb3bbda1e7a90b8236e45` |
| E24 | `D:/ff-qa/459-research/raw/verify_artifacts.py` | 旧布局材料校验方法，含类型检查；不能当归档可直接执行入口 | `34194560ad9a1c94d7e0a27c93aedd55b28f9dbfed795707664a46a664e40c53` |
| E25 | `D:/ff-qa/459-research/raw/verification.json` | 旧材料自洽/链接检查结果，不是本轮测试 | `c263037b3a5f629676ae4db6feed2f94512f685ee2d2fd4b78c08059899876c0` |
| E26 | `D:/ff-qa/459-research/raw/typecheck.json` | 旧工树 no-emit 检查记录，不是新 main 结果 | `97280d2e0dbc2de0896c6eb54ed9b6e1335a261f01fcd5ba0436ba08df029c6e` |
| E27 | `D:/ff-qa/459-research/raw/warnings.json` | 历史 requests 依赖与重复 operation ID 告警 | `d90a73e321dbd4a15408899b56cc1f1b9f8f61bfc8c72b5943d854f5417aa0ff` |
| E28 | `D:/ff-qa/459-research/raw/last-failure.txt` | 早期研究脚本 plan path 错误轨迹，不是最终状态 | `8d725e4072eb894c6aeee9fc427f5738cb9703822b95c9156cbbd3058a2e9d00` |
