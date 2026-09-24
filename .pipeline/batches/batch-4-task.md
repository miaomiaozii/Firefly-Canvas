# batch-4 任务卡：#140 节点级模型覆盖 + #141 自治级别

## 背景
Agent 节点（Pi 终端 TUI，#117 已完成 M1-M3）当前：所有节点跟随 pi 全局默认模型；确认门是固定硬门（破坏性操作 canvas.delete_node / media.delete 永远需用户确认，系统提示词约定"多步任务先给计划"不可配置）。本批次给节点加两个节点级设置。

## 范围（只做这两件事，不碰 fork/并行）

### A. #140 节点级模型覆盖
1. **节点属性持久化**：agent 节点 dict 加可选 `model` 字段（空串 = 跟随全局默认）。存项目数据（`project['canvas']['nodes']` 里的节点 dict），刷新/重载后保留。
2. **spawn 透传**：`create_agent_node` 端点已接受 `model` Body 参数并传给 `get_or_create_session`/`spawn_session`。改为：优先用显式传入的 model，否则读节点 dict 的 `model` 属性；为空则不传（走全局默认）。
3. **运行中切换**：新增端点 `POST /api/projects/{pid}/agent-nodes/{node_id}/model`，body `{"model": "..."}`。运行中会话走 pi RPC 原生 `set_model` 命令（agent_host 加 `set_model` 方法，经 `send_command` 发 `{"command":"set_model","provider":...,"model":...}`——先查 `tmp/pi_rpc_smoke.py` 和 pi 文档确认 set_model 的确切参数名）；同时更新节点 dict 的 `model` 属性持久化。未运行会话只更新属性（下次 spawn 生效）。
4. **TUI 模型选择**：`agentNodeBodyHtml` 的 header 区加模型下拉（`<select data-agent-model>`），选项来自 `/api/models` 的 `chat_models`（前端已有加载逻辑，复用）。空选项 = "默认"。`agentTuiState` 加 `model` 字段（已存在）+ 变更时调新端点。

### B. #141 自治级别（三档）
1. **节点属性持久化**：agent 节点 dict 加 `autonomy` 字段，取值 `cautious`（谨慎，默认）/ `standard`（标准）/ `autonomous`（自主）。未设置 = cautious（向后兼容）。
2. **确认门范围**（改 `app/routers/api_agent_tools.py` 的 `/confirm` 端点）：
   - 端点读该 node 的 `autonomy` 属性。
   - `cautious` / `standard`：维持现状（总是发 confirm 事件等用户）。
   - `autonomous`：破坏性工具（canvas.delete_node / media.delete）**自动批准**（直接返回 `{"approved": true}`，不发 confirm 事件）；**安全底线例外**：项目级删除等真正不可逆操作仍需确认——当前 canvas-tools 没有项目级删除工具，所以 autonomous 下两个破坏性工具都自动批准，但代码里要留安全底线判断结构（如 `SAFETY_FLOOR_TOOLS` 常量集合，autonomous 下该集合内的工具仍走确认），并在注释说明。
3. **系统提示词按级别注入**（改 `app/services/agent_host.py` 的 DEFAULT_SYSTEM_PROMPT 或 spawn 时拼接）：
   - `cautious`：现状（"多步任务先给简短计划等确认"）。
   - `standard`：破坏性操作仍需确认（工具层硬门不变），但"多步任务直接执行，边做边报"。
   - `autonomous`：直接执行，仅安全底线操作会触发确认（工具层已处理）。
   - 级别在 spawn 时注入；级别变更对运行中会话**即时生效**——改属性后，下一轮工具调用起生效（系统提示词部分：若 pi RPC 有 set_system_prompt 类命令则用之，否则文档说明"提示词部分下次 spawn 生效，确认门部分即时生效"，以实际能力为准并在 result.md 说明）。
4. **TUI 级别选择**：`agentNodeBodyHtml` header 区加级别下拉（`<select data-agent-autonomy>`），三档中文标签（谨慎/标准/自主）。变更时调新端点 `POST /api/projects/{pid}/agent-nodes/{node_id}/autonomy`，body `{"autonomy": "..."}`，更新节点 dict 持久化。

## 关键文件
- `app/services/agent_host.py`：spawn_session model 透传（已有）、set_model 方法、系统提示词按级别拼接
- `app/routers/api_agent_nodes.py`：create 读节点属性、新增 /model 和 /autonomy 端点
- `app/routers/api_agent_tools.py`：/confirm 端点读 autonomy 决定门范围
- `app/agent_extensions/canvas-tools.ts`：可能无需改（扩展总是调 /confirm，服务端决定）；若需传 tool 名判断安全底线，确认现有 body 已含 tool 字段（已含）
- `static/js/smart-canvas.js`：agentNodeBodyHtml header 加两个下拉、agentTuiState、变更处理、事件绑定
- `static/css/smart-canvas.css`：下拉样式（复用既有 agent-tui 样式）

## 约束
- 不改确认门机制本身（事件流/超时/批准回传），只改"哪些工具进门"
- 不改 pi 会话文件格式
- 模型覆盖值原样透传，不做路由层改写（ADR 0006）
- 默认行为必须与现状完全一致（未设 model/autonomy 的节点回归无变化）
- 前端文案走 i18n（tr()），中文为主

## 测试要求（TDD，fake agent host 接缝，不真调 pi/模型）
- **单测**（app/services 或 tests/ 既有 agent 测试文件）：
  - #140：带/不带节点 model 属性创建会话 → 宿主实际使用的 model；/model 端点更新属性 + 运行中发 set_model
  - #141：三档级别下 /confirm 端点行为（cautious/standard 发 confirm 事件等用户；autonomous 对 canvas.delete_node/media.delete 自动批准）；安全底线工具集合内工具在 autonomous 下仍走确认；系统提示词按级别含对应指令
  - 默认回归：未设属性的节点行为与现状一致
- **浏览器测试**（tests/ 既有 agent 浏览器测试模式）：TUI 两个下拉渲染 + 选择后节点属性持久化（刷新保留）+ autonomous 下破坏性操作不弹确认
- 跑：`python -m pytest tests/ -q`（单测全绿）+ 新增浏览器测试

## 交付
1. 实现 + 单测 + 浏览器测试
2. `.pipeline/batches/batch-4-result.md`：改动清单（文件:行）、测试命令与结果、set_model RPC 确切参数（查 pi 文档/冒烟确认）、系统提示词即时生效能力的结论、遗留问题
3. 不要 git commit（主会话审查后提交）

## 参考
- issue：gh issue view 140 / 141 --repo miaomiaozii/Firefly-Canvas
- 既有测试模式：tests/ 下 agent 相关测试、tmp/pi_rpc_smoke.py（pi RPC 冒烟）
- 既有 TUI：static/js/smart-canvas.js agentNodeBodyHtml / agentTuiState
