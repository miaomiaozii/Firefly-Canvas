# issue-242 结果报告（#242 ADR 0022 + #245 ADR 0006 解释行）

工作区：`D:/ff-wt/wt-242`，分支 `issue/242`（基于 `pipeline/canvas-modularization`）。未 push、未关票、未合并。

## #242：ADR(W2) 画布 MCP 端点（streamable-HTTP，外部 agent 具现为 Agent 节点）

**状态：完成**

### 改动文件

| 文件 | 变化 |
|---|---|
| `docs/adr/0022-mcp-streamable-http-canvas-endpoint.md` | 新建（ADR 0022，`status: accepted`，date 2026-10-28） |

### ADR 内容（全部按 #242 票面 + #233 拷问签署方向成文）

- **整画布 MCP 端点**：streamable-HTTP、纯 JSON、无 SSE；复用现有 17 个 agent 工具（project-scoped HTTP），外部 agent（Claude Code/Codex/Cursor/VS Code）直接操作画布。
- **外部 MCP 会话具现为画布上的 Agent 节点（Q12-a）**：复用节点绑定的确认门与画布可观察性；ADR 0012/0019 不修订、只引用。
- **安全边界（v1）**：仅 loopback 绑定 + Host 校验（现服务已绑 127.0.0.1）；token 鉴权留接口不实现；同源守卫为前置票 #240。
- **确认门阻塞语义**：烧钱/破坏性工具阻塞等待画布确认，无 fire-and-forget 变体。
- **会话→项目绑定**：spawn-time env 绑定不适用于外部连接 → `?project=` + client_id 注册表 + 三级仲裁（①显式 project 无 tab→报错提示打开；②未指定且多项目 tab→报错提示指定；③未指定且单 tab→命中）+ 命令定向下发单 tab；仲裁形态抄 ComfyArt `api/mcp_commands.py` 的 `_target(project)`（经 #233 签署）。
- **超时契约**：wait 类工具可阻塞数分钟（`generate.wait` 默认 120s、上限 600s），外部客户端超时 600s，写进文档 + 客户端配置示例。
- 另附「已知缺口（#243 顺带修）」小节：假工具 `get_selection` 恒空、viewport 空 dict、`_apply_mutations` 不广播、`generate.wait` timeout 发在 GET body 被忽略。

### 证据核对结果（票面引用逐项核实）

| 票面引用 | 核实结果 |
|---|---|
| `app/routers/api_agent_tools.py:20-28` 五个 project-scoped 路由 | ✅ 属实（canvas/generate/media/viewport/agent 五个 APIRouter，20/22/24/26/28 行） |
| 17 个 agent 工具 | ✅ 属实——`app/agent_extensions/canvas-tools.ts`（395 行）注册 17 个工具；文件共 18 个端点，第 18 个是 confirm 门支持端点（非工具） |
| `app/agent_extensions/canvas-tools.ts`「packages 内」 | ⚠️ 位置偏差：实际在 `app/agent_extensions/`，不在 packages/（packages/ 目录存在但无此文件）；ADR 按实际路径引用 |
| `api_agent_tools.py:541-579` `request_confirmation` 强制 node_id | ⚠️ 行号偏差：函数定义在 `:510-511`，node_id 必填校验在 `:532-533`，阻塞等待在 `:579-580`（541-579 覆盖 autonomy 检查+事件下发+等待段）；ADR 按实际行号引用 |
| `app/services/agent_host.py:164-166` spawn-time env | ✅ 属实（`FIREFLY_AGENT_API_BASE`/`FIREFLY_AGENT_PROJECT_ID`/`FIREFLY_AGENT_NODE_ID`） |
| wait 超时 | ✅ `api_agent_tools.py:357` `Query(default=120, ge=1, le=600)`；票面「~3 分钟」按签署契约保留，代码事实（120/600）同写 |
| 三级仲裁出处 | ⚠️ 仓库内无定义：精确语义（`_target(project)` 三级）来自 #233 引用的 `docs/reference-comfyart-delta-202610.md`——该文件**未入库**（主工作区 untracked），ADR 不引用该文件（防死链），改为内联描述 + 引 #233 |
| `requirements.txt` 现无 mcp 包 | ✅ 属实（ADR 作新依赖后果记录） |
| `main.py:1318` loopback 绑定 | ✅ 属实（uvicorn `host="127.0.0.1"` port=3000） |
| #243 提到的 `static/js/dual_tab_sync_client.js` | ⚠️ 不存在：本分支与主工作区均无此文件，ADR 不引用，仅以「多 tab 契约测试（#243 前置）」表述 |

### ADR 编号与死链

- 编号 **0022**（0021 已被 #250 ops.db sidecar 预占，票面确认；`docs/adr/` 现只有 0001-0020，无冲突）。
- 死链核查：ADR 内 3 个 ADR 交叉引用（0012/0016/0019）全部存在；9 处代码 file:line 引用全部在文件实际行宽内（脚本核查通过）。

### 验证

| 项 | 结果 |
|---|---|
| `python -m pytest tests/ -q -k "adr or docs"` | 1 passed, 1301 deselected |
| ADR 交叉引用存在性 | 3/3 EXISTS |
| 代码 file:line 行宽核查 | 全部 OK |
| `git status` | 干净（提交后） |

### 提交

- `525467b` `docs(adr): 0022 MCP streamable-HTTP 画布端点 (#242)`

---

## #245：Docs ADR 0006 补一行「路由可选 provider、不改用户参数」边界解释

**状态：完成**

### 改动文件

| 文件 | 变化 |
|---|---|
| `docs/adr/0006-model-routing-preserves-user-parameters.md` | 在正文段落后补一行独立边界解释（票面原句），其余内容零改动（diff：+2 行） |

补充行（票面原文，逐字）：

> 路由/调度层可以**选择** provider,但不得**修改**用户提交的生成参数（含 basename 重写类映射）；参数重写即违反本 ADR。

### 备注

- 票面正文在 GitHub 上本身截断于「背景：ComfyArt 的 `5312394` 调」（issue 保存不完整），但要求补充的行完整无歧义，按票面原文落地。
- 该行与 #233「明确不做」清单中的「basename 重写」一致（#233 明确把 ComfyArt 的 basename 重写列入不做项）。

### 验证

| 项 | 结果 |
|---|---|
| diff 范围 | 仅 +1 行边界解释（+1 空行），无其他改动 |
| `python -m pytest tests/ -q -k "adr or docs"` | 1 passed（同 #242 批次运行） |

### 提交

- `0d5fb41` `docs(adr): 0006 补路由可选 provider 边界解释 (#245)`

---

## 阻塞与偏离

- **无阻塞**。两张票均完成并提交。
- 偏离（均为票面/引用与代码实况不符，ADR 已按实况纠正并在此记录）：
  1. `canvas-tools.ts` 实际在 `app/agent_extensions/` 而非 packages/；
  2. `request_confirmation` 实际 `:510-511`（node_id 校验 `:532-533`），非票面 `:541-579`；
  3. 「三级仲裁」精确定义只存在于未入库的 delta 文档，ADR 改为内联描述并引 #233，不引用未入库文件；
  4. #243 引用的 `static/js/dual_tab_sync_client.js` 在两个工作区均不存在，ADR 未引用。
- 未执行：push、关票、合并（按工作规则禁止）。#242 票面标注 ADR 需维护者签署（ready-for-human）——本 ADR `status: accepted` 是按「决策已签（拷问会话，用户确认 Q3/Q12=a）」直接成文；如维护者流程要求先 `proposed` 再签，可单行改 frontmatter。
