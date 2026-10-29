# Batch-5 结果：#142 会话分支（fork）

> 实施：twin（run 684ba280，30min 超时但实现完整）；对抗审查 + 修复 + 测试补全：主会话（qwen3.8-27b-twin-ultra-mtp2:max）

## 实现

### 后端（`app/services/agent_host.py`）
- `get_fork_messages(session)`：读 session_file 的 JSONL，返回 `[{entry_id, role, preview}]`（过滤 meta，截断 preview 到 120 字符）
- `fork(session, entry_id, name, timeout=60)`：发 `fork` 命令 → 等 fork 响应 → `get_session_state` 拿新 sessionFile（审查修复：pi 的 fork 响应不返回 sessionFile，必须用 get_state 查询）→ 更新 session.session_file/session_id
- `switch_branch(session, session_file, session_id, timeout=60)`：发 `switch_branch` 命令（审查修复：参数名是 `sessionPath` 不是 `sessionFile`）
- 新增 `_wait_for_command_count` 辅助（审查修复：避免 read_response 在事件流中误读）

### 后端（`app/routers/api_agent_nodes.py`）
- `GET .../fork-messages`：返回可 fork 的历史轮次列表
- `POST .../fork`：fork 到指定轮次，创建新 branch 并切换；**首次 fork 时把当前分支标记为"原始"**（审查修复：twin 原实现丢失原始分支）
- `POST .../switch-branch`：切换到指定分支

### 前端（`static/js/smart-canvas.js`）
- `agentTuiHtml`：分支选择器（`node.branches` 有值时渲染）+ fork 按钮（🌿 从历史轮次分叉）
- `bindAgentNodeControls`：绑定 fork 按钮（调 `showForkPicker`）+ 分支选择器 change（调 `switchAgentBranch`）
- `showForkPicker(nodeId, projectId)`：拉 fork-messages → 原生 prompt 选择轮次 + 命名 → 调 fork API
- `switchAgentBranch(nodeId, projectId, branchId)`：调 switch-branch API → 刷新节点

## 审查发现并修复（主会话对抗审查）
1. **`switch_branch` 参数名错误**：twin 用 `sessionFile`，pi 期望 `sessionPath` → 已修
2. **`fork` 拿不到新 sessionFile**：twin 假设 fork 响应含 sessionFile（实际不含）→ 改为 fork 后调 `get_session_state` 查询；加防御检查（拿不到新文件时报错，避免产生坏分支）
3. **首次 fork 丢失原始分支**：twin 只在 fork 时 push 新分支，原始 session_file 丢失 → 首次 fork 时把当前分支标记为"原始"
4. **单测误报**：twin 单测 `test_fork` 假设 fork 响应含 sessionFile（与真实 pi 不符）→ 改为两段响应（fork + get_state）；`test_switch_branch` 检查 `sessionPath`；`test_fork_endpoint`/`test_switch_branch_endpoint` 同步修正

## 测试
- **单测**（`tests/test_agent_fork.py`，13 个）：全绿
  - fork 逻辑（6）：正常 fork、无 session_file、fork 被取消、拿不到新文件报错、switch_branch 正常、switch_branch 无 session
  - API 层（4）：fork-messages、fork（含原始分支）、switch-branch、fork 取消
  - 数据模型（3）：分支数据持久化、切换分支更新 current_branch、fork 创建新分支
- **浏览器**（`tests/agent_fork_browser.py` + `_client.js`，13 断言）：全绿
  - fork 按钮渲染、分支选择器渲染（原始+分叉）、fork 按钮 click handler 绑定、分支选择器 change handler 绑定、模型/自治选择器回归
- **全量回归**：1030 passed, 351 subtests passed, 0 失败（5m20s）

## 已知限制
- fork 选择器用原生 `prompt()`（简单但不够优雅，后续可换自定义弹窗）
- 真实 pi 的 fork/switch_branch 未在真机验证（需 pi 环境），逻辑已按 pi rpc.md 规范实现
- 切换分支后前端需手动刷新节点（未自动重连 WS）

## 文件清单
- `app/services/agent_host.py`（+110：fork/switch_branch/get_fork_messages/_wait_for_command_count）
- `app/routers/api_agent_nodes.py`（+108：3 个 endpoint）
- `static/js/smart-canvas.js`（+93：分支选择器 + fork 按钮 + showForkPicker + switchAgentBranch）
- `tests/test_agent_fork.py`（新建，13 单测）
- `tests/agent_fork_browser.py` + `tests/agent_fork_browser_client.js`（新建，浏览器测试）
