# batch-5 任务卡：#142 会话分支（fork）

## 背景
Agent 节点会话是单线历史。某轮走错方向后只能继续说"重做"（上下文被污染）或删除节点重来（丢全部上下文）。PiX 有会话分支：从历史某轮分叉重做。pi RPC 原生支持 fork（已实测）。

## pi RPC 事实（已查 docs/rpc.md）
- `get_fork_messages`：`{"type":"get_fork_messages"}` → `data.messages[]` 每项 `{entryId, text}`（可分叉的用户消息列表）
- `fork`：`{"type":"fork","entryId":"abc123"}` → `data:{text, cancelled}`（从该用户消息分叉出新分支）
- `clone`：`{"type":"clone"}` → 复制当前分支到当前位置
- `switch_session`：切换到指定会话（分支间切换用）
- fork 后 pi 进程：`session_shutdown`（旧）→ reload/rebind 扩展 → `session_start {reason:"fork", previousSessionFile}`（新会话文件）
- 会话文件：Pi 原生 JSONL，存 `agent_nodes/{node_id}/sessions/`；一个分支 = 一个会话文件

## 范围
1. **宿主边界**（app/services/agent_host.py）：
   - `get_fork_messages(session)`：发 RPC，返回可分叉消息列表
   - `fork(session, entry_id, name?)`：发 fork RPC，pi 切到新会话文件；捕获 `session_start{reason:fork}` 拿到新 sessionFile/sessionId，注册为新分支
   - `switch_branch(session, branch)`：发 switch_session RPC 切到目标分支会话文件
   - 分支状态：节点 dict 加 `branches`（列表，每项 `{id, name, session_file, fork_from_summary, created_at}`）+ `current_branch`（引用）。未设置 = 单分支（向后兼容，行为与现状一致）
2. **API**（app/routers/api_agent_nodes.py）：
   - `GET .../agent-nodes/{node_id}/fork-messages` → 可分叉消息列表
   - `POST .../agent-nodes/{node_id}/fork` body `{entry_id, name?}` → 创建新分支（默认名 = 分叉轮次摘要），返回分支列表
   - `POST .../agent-nodes/{node_id}/switch-branch` body `{branch_id}` → 切换当前分支
   - 分支列表随节点属性持久化（存项目数据节点 dict）
3. **项目导出/导入/回收站**：分支文件（sessions/ 目录全部 JSONL）随项目导出/导入/回收——扩展现有 agent_nodes/ 目录机制，含 zip-slip 防护（参考 #117 batch-2 的导出 manifest 模式）
4. **TUI**（static/js/smart-canvas.js agentNodeBodyHtml）：
   - header 或输入行加分叉按钮（⑂ 或 🌿）
   - 点分叉 → 拉 fork-messages → 轻量列表（下拉或侧栏，显示分支名 + 分叉点摘要）→ 选一条 → 可改名 → 确认分叉
   - 分支列表（当前分支高亮，可切换）
   - 分叉/切换后 TUI 消息区重置为新分支历史
   - 不做分支树可视化 / 分支 diff（Out of Scope）

## 约束
- 用 pi RPC 原生 fork，不自研历史切分
- 会话与分支 1:1（一个分支 = 一个 Pi 会话文件）
- 单分支节点行为与现状完全一致（回归无变化）
- 导出/导入含 zip-slip 防护
- 前端文案走 i18n

## 测试要求（TDD，fake agent host，不真调 pi）
- **宿主边界单测**：fork → 新会话文件生成 → 新分支注册 → 新分支可继续对话；switch_branch 切换；get_fork_messages 返回
- **API 单测**：fork/switch-branch/fork-messages 端点 + 分支属性持久化
- **浏览器测试**：TUI 分叉入口 + 分支切换 + 分叉后消息区重置
- **导出/导入**：分支文件不丢、恢复后可切换
- 跑 `python -m pytest tests/ -q` 全绿 + 新增浏览器测试

## 交付
1. 实现 + 单测 + 浏览器测试
2. `.pipeline/batches/batch-5-result.md`：改动清单、测试命令与结果、fork 后 session 切换的处理方式、分支文件导出/导入方案、遗留问题
3. 不要 git commit

## 注意
- batch-4 会先改 TUI header（加 model/autonomy 下拉）——本批次的分叉按钮/分支列表放不同区域，注意与 batch-4 的 TUI 结构兼容（读 batch-4 后的 smart-canvas.js 现状）
- fork 后 pi 进程 session 切换的时序（session_shutdown → session_start）要正确处理，避免事件串扰
