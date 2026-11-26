# #459 补充证据：终端边界与 helper 对照

**状态：部分完成。** 此处补充 [历史报告](459-shell-api-vs-mcp.md) 的证据，不改写旧结果。基线 `44476dfe78077fe32581d44cb8c58a65a587b2bc`。新增源码核对、仓库回归和合成模型工具选择对照；未启动产品或真实 Agent 宿主，未访问用户项目、会话或配置。下表是能力核对，不是使用频次或需求排序。

## 终端仍默认关闭（#464）

- **Codex**：[适配器](../../app/services/agent_adapters/codex_adapter.py)首轮与显式 ID 续轮共用 `-s read-only -c features.shell_tool=false`，保留 Firefly MCP 接入。关闭的是原生 shell 注册；按 [#464 钉版依据](../design/agent-terminal-default-off-464.md)，同一闸口覆盖交互式与 one-shot 路径。`read-only` 不是工具移除，也不是单命令白名单；该 flag 不代表禁掉独立注册的 code mode、MCP、扩展或动态工具，也未证明它们能绕过限制。
- **Pi**：[适配器](../../app/services/agent_adapters/pi_adapter.py)用 `--no-extensions`、显式 `-e` 加载 Firefly 扩展、`--no-builtin-tools` 加工具 allowlist；新会话及两种续接形态都用这套规则。该注册路径只允许列出的业务命名空间和[受限 `read/write/edit`](../../app/agent_extensions/session-file-tools.ts)，不提供 bash / PowerShell / codemode。文件工具限制在服务器给定的会话工作目录，不是整个进程的 OS 沙盒。
- **证据边界**：[回归用例](../../tests/test_agent_terminal_default_off_464.py)覆盖 argv 与不可用时不回退（见下文实测）；#464 已存证据是 Codex 0.162.0 的解析与上游注册源码、Pi SDK 1.5.1 的解析和内存注册，不等于实际模型轮次的工具清单、Pi CLI RPC 或原生历史续接。更高层托管配置、OS 隔离、宿主严格单命令约束和 shell→loopback 连通性均未在本补充中验证。没有开放终端或新增授权的决定。

## 项目路由与 #393 约束

`P` = `/api/projects/{project_id}`（Agent）；`B` = `/api/project-library/{project_id}`（浏览器项目库）。[应用](../../main.py)注册这些路由；[Pi REST 执行器](../../app/agent_extensions/canvas-tools.ts)与 [MCP 网关](../../app/routers/api_mcp.py)在传输层有不同绑定，节点写入、对话查询和撤销等共用业务实现。`X-Agent-Project` 是上下文标记，不是认证；有的入口只查非空，有的会校验与路径一致。同源检查也不是认证。[专用 Agent 写入口](../../app/routers/api_agent_tools.py)注入操作归属；需要 `agent_node_id` 的写入按 handler 从 body/query 取值，轮次由服务端取原逻辑轮或该节点当前活动轮。外部 MCP 的 URL `turn_id` 是另一套归组契约，不能拿请求 ID 或随填值代替宿主轮次。

- **确认不是改传输就能跳过。** `POST P/agent/confirm` 是请求确认；`POST P/agent-nodes/{node_id}/confirm` 是[人的回应](../../app/routers/api_agent_nodes.py)，不纳入 helper。按[待决实现](../../app/services/agent_confirmation.py)处理收据（即使 HTTP 成功也未执行），不重复提交或代批；宿主调用在原逻辑轮续接。#486 仅未被人工接管或保护、且未锁定的自有节点可直接更新；其他节点的实际改动（包括几何字段）须人工批准，绑定目标快照和补丁，写前重验。无变化不弹确认。#487 的 `generate.request_batch` 三档自治模式均须人工批次选择。Agent `POST P/plan/apply`（`agent_node_id` query、匹配项目头；#475）不是人工 `POST P/plan/previews/{plan_id}/apply`（#484）的替身，反之亦然（[计划入口](../../app/routers/api_agent_plan.py)）。
- **撤销不是全栈回滚。** [#393 的 `agent_ops`](../adr/0021-per-project-sqlite-sidecar-ops-db.md)是项目 `运行数据` 中的非权威差异记录，不代替画布文档。[查询/撤销](../../app/routers/api_agent_ops.py)与 [逆操作](../../app/services/agent_ops_undo.py)按操作或逻辑轮定位、重验写入后的状态；后续编辑可能返回 `409 UNDO_CONFLICT`，生成在途可阻断。报告不可逆效果，撤销不退服务商费用，也不等同浏览器撤销栈或重做。

### 动作 → REST（或无等价 Agent 入口）

参考 [#478 静态覆盖表](../design/agent-tool-coverage.md)的行号；有变更的以本次源码为准，不用浏览器写入口冒充 Agent 合同。

| 动作 / #478 | 对应方法、路径与边界 |
| --- | --- |
| 查节点或详情（C01） | `GET P/canvas/nodes`（`fields` 用重复 query 键；工具 `[]` 映射 `fields=summary`，省略才取全部块）；`GET P/canvas/nodes/{node_id}`。[节点入口](../../app/routers/api_agent_tools.py) |
| 建节点、改标题/正文/位置（C02/C04/C16） | `POST P/canvas/nodes` body `{node, agent_node_id}`；`PATCH P/canvas/nodes/{node_id}` query `agent_node_id`、body 为扁平补丁。当前 Pi executor 只转发 `title/text/prompt`，会丢 REST/MCP 已接收的 `x/y/w/h/runSettings`；这是转发漂移，不是缺路由。[写入口](../../app/routers/api_agent_tools.py) / [Pi executor](../../app/agent_extensions/canvas-tools.ts) |
| 查生成状态/等待，不发起任务 | `GET P/generate/tasks/{task_id}`；`GET P/generate/tasks/{task_id}/wait`（工具 `timeout_seconds` 映射 query `timeout`，1–600 秒）。[生成入口](../../app/routers/api_agent_tools.py) |
| 查差异、撤一项/一轮（A10/C19） | `GET P/ops`（`turn_id`、`limit` 可选）；`POST P/ops/undo`（body 恰一 `op_id` 或 `turn_id`）。人工 `GET B/undo-stack`、`PUT B/undo-stack` 不等价。[Agent 撤销](../../app/routers/api_agent_ops.py) / [人工栈](../../app/routers/api_project_undo.py) |
| 读/搜本项目 Agent 对话（A05） | `GET P/chat/history`、`GET P/chat/search`；只取用户/助手正文，不是工具调用轨迹，也不搜索其他项目或普通对话节点。[对话入口](../../app/routers/api_agent_chat.py) / [投影](../../app/services/agent_chat.py) |
| 定位/缩放/读视口，或读真实选区（C15/C05） | `POST P/viewport/focus`（`node_id`）、`POST P/viewport/zoom`（`level`）、`GET P/viewport/state`；#485 要求唯一就绪项目标签页，取应用回执或实时观测，不写文档撤销。**无真实浏览器选区读取等价入口**；`POST P/canvas/view` 的 `scope=selection` 仅消费显式 `node_ids`。[工具路由](../../app/routers/api_agent_tools.py) / [视口投递](../../app/services/canvas_viewport.py) |
| 对齐/分布、框排版，或按连线整理（C11/C13） | `POST P/canvas/align`、`POST P/canvas/distribute`、`POST P/canvas/group_arrange`、`POST P/canvas/arrange_groups`；**无浏览器按连线整理选中节点的等价 Agent 入口**，不是换一个布局参数。[路由注册](../../app/routers/api_agent_tools.py) / [浏览器整理](../../static/js/smart-canvas.js) |
| 查媒体、回收媒体，或硬删/删除原件（M02/M11/M16） | `GET P/media/list`；`DELETE P/media/{asset_id}` 在确认与 Agent 操作上下文中进项目回收区并保留引用。`DELETE B/media/{asset_id}` 硬删并清引用；`POST B/original/delete` 按输出项，`POST B/original/delete-project-wide` 按资产全项目处理原件，均**无等价 Agent 删除入口**。[Agent 媒体](../../app/routers/api_agent_tools.py) / [浏览器项目库](../../app/routers/api_project_library.py) |

**对照建议不变**：只有另行允许终端，才用现有 shell + 项目内只读 HTTP（先 `nodes`、`media/list`、`ops`）作最小传输对照；宿主若不能表达严格单命令限制，就报告不可行。薄 CLI 可选，不新增 runner、代理或沙箱；计费、删除、计划应用及跨项目不进首轮。保留现有 MCP。在现有 [`TOOLS`](../../app/services/agent_tools_manifest.py) 上补显式 method/path/query/body、服务端绑定身份（不可由模型代填）、示例、错误/恢复和任务索引，由一个定义生成 helper 与 OpenAPI/CLI/MCP 投影；不要整份 OpenAPI 暴露给模型，不拿浏览器通用画布写入替代 Agent 写入。Pi 目前是「生成 schema + 手写 forwarding」，因此验收要比实际转发、待决收据和撤销冲突，不能只比工具名或生成文件是否最新。

## 将来怎样计真实缺口（本次未采集）

- **准入与来源**：先取得数据权利人和参与者的明确授权，约定项目/会话 allowlist、用途、脱敏、访问/保留/销毁范围；另行授权真实宿主或付费。只纳入可核验的完整导出，登记导出来源/方式、宿主与模型版本、提交、采集时段、脱敏版本及获准副本哈希；脱敏后仍应能用一致别名关联逻辑轮、调用/参数/结果、确认与撤销。旧 #356 摘录来源不明且不完整，不追索，不入分母；合成亦不混入真实样本。
- **分母与分类**：先锁定窗口及纳入/排除规则，公布会话数、可标注逻辑用户轮数、用户意图动作数、实际调用尝试数（含失败与重试）、缺失或不可判定数。报「至少一个有证据的能力缺口的唯一轮数 / 可标注逻辑轮」「各动作的受阻数 / 该动作出现数」「各类失败尝试 / 可判定尝试」；同轮重试不加用户轮或意图动作，单独记尝试。分别标记无等价能力、已有能力但选错/参数错、宿主阻断、预期分页/等待、确认待决/拒绝、业务冲突/服务商失败。真实频次与 helper 效果仍待合格证据，不能从静态覆盖外推。

## 合成 helper 对照

**方法（2026-11-26）**：Strata `/v1/chat/completions` 实际运行 `flash-next-turbo-iq3s`；不是 Codex/Pi 的真实宿主调用。6 个中文合成任务，每任务每组一轮，从空上下文开始，交替安排两组先后。两组都收到相同的 7 个只读工具名与结构化 schema；helper 组另收到从清单生成的描述，以及按相同工具名附加的任务索引、合成示例和纠错提示。相同 JSON 输出协议、`temperature=0`、`max_tokens=256`。按预设精确答案判断，失败时给通用提示后最多重试一次；不派发任何工具，不测 REST/CLI/MCP 的实际执行。没有凭据，没有请求重定向或代理；脚本硬上限 24 次请求，实际 15 次模型请求、0 次图像生成。

| 指标 | 仅名和 schema | 加 helper |
| --- | ---: | ---: |
| 严格匹配：首次 / 最终正确 | 4/6、5/6 | 5/6、6/6 |
| 按实际参数语义重评：首次 / 最终正确 | 5/6、6/6 | 6/6、6/6 |
| 实际请求（其中重试） | 8（2） | 7（1） |
| 选错工具 / 严格参数不匹配 / 格式错 | 1 / 2 / 0 | 0 / 1 / 0 |
| 服务端返回总 token（输入 + 输出） | 3972（3830 + 142） | 6770（6645 + 125） |
| 顺序请求耗时之和 | 17.953 s | 19.984 s |

- **有效差异**：两组唯一首选工具差异在「真实选区」。无 helper 首选了 `viewport.state`（视口不是选区）；加 helper 首次返回 `unsupported`。两组最终都识别为不支持。
- **重试不能照表外推**：组摘要首次两组都给 `fields:["summary"]`，精确匹配要求 `fields:[]`。实际上 [`_validate_fields`](../../app/services/agent_node_state.py) 将二者都归一成 `{"summary"}`；两次首选在业务语义上均正确。无 helper 重试又擅自加 `created_by:"agent"`，会缩小范围，但这次重试原本可避免。原始记录保留严格判断，离线重评用语义口径，不抹去实际发生的请求。重评只说明这批合成答案，不是重新跑过的语义判分实验。
- **可得结论**：helper 在这 6 个任务中避免了一次真实选错工具，但最终语义正确数没变，提示开销更大。小样本、一个模型、每臂每任务一次，且两组没有执行工具；不能据此排名三种传输、估计真实误用频率，或承诺节省耗时/token。首期建议仍是保留 MCP，不为 helper 打开终端；先补可共用的能力说明和字段映射，另获授权再做真实宿主 A/B。

**复跑**（从仓库根执行；网络复跑只发合成输入，输出必须是新文件）：

```bash
PYTHONUTF8=1 python -B docs/research/459-evidence/helper_probe.py new-results.jsonl
PYTHONUTF8=1 python -B docs/research/459-evidence/helper_probe.py docs/research/459-evidence/helper-results.jsonl --replay
```

[`helper_probe.py`](459-evidence/helper_probe.py) 和 [`helper-results.jsonl`](459-evidence/helper-results.jsonl) 随文归档。后者保留 15 次合成请求/原始回复、判分和耗时，原始 LF 字节 SHA-256 `7f6ed83ed3be76d4ca553283466bf4cbf8d9ffdec56fa6a0037c20c67d5b62ff`。`--replay` 不联网；服务端采样和延迟不保证复跑完全相同。

## 回归与仍缺的证据

同一隔离命令分别在补充工作树与干净基线运行：

```bash
python -m pytest -q -p no:cacheprovider tests/test_agent_terminal_default_off_464.py tests/test_tool_surface_consistency.py
```

两边都是 `1 failed, 27 passed, 3 warnings, 95 subtests passed`（候选 22.34s，基线 22.81s）。同一 `agent.present` 子测返回 `SERVER_NOT_READY: Presentation could not be persisted`，本次只记录基线复现，不归因到产品根因或修复。真实日志没有取得合格授权样本，频次排名仍为 **NOT_RUN**；真实 Codex/Pi helper A/B、宿主单命令放行与 shell→loopback 仍为 **NOT_RUN**。因此不能据本补充关闭 #459。
