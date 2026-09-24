# batch-2 任务卡（#117 US-27 + US-22）：Agent 会话的项目生命周期 + 模型默认行为确认

## 背景
#117 Agent 节点 v2（Pi 终端 TUI）。batch-1 已移除旧 UI。本批次补齐会话生命周期缺口。

现有机制（`app/services/agent_host.py`，勿改其核心逻辑）：
- 每个 Agent 节点 ↔ 一个 `pi --mode rpc` 长驻子进程，由 `AgentHostManager` 管理
- 会话文件存项目目录内 `agent_nodes/{node_id}/sessions`（Pi 原生 JSONL）
- 已有：`--continue` 自动恢复、崩溃 auto-respawn、`cleanup_all()`（进程退出时）
- 前端/路由：`app/routers/api_agent_nodes.py`（create/delete/status/prompt/steer/follow_up/abort/confirm/stream）

## US-27 缺口（主实现）
项目删除/进回收站时，**运行中的 pi 子进程无人清理**（进程泄漏 + 继续操作已删项目）。会话文件在项目目录内会随项目走（已覆盖），但活进程必须 kill。

验收标准：
1. **项目删除（永久删除/回收站清空）时**：kill 该项目所有 agent 会话子进程并清理 `AgentHostManager` 中的 session 记录。找到项目删除的实现位置（`app/services/projects.py` 或 `app/routers/api_project_library.py` 的删除/回收站端点），在合适位置调用清理
2. **项目进回收站（软删除）时**：同样 kill 子进程（项目内容不可访问，会话进程无意义）；session 文件随项目目录进回收区
3. **项目从回收站恢复时**：无需特殊处理（会话文件随目录回来，下次交互时 `--continue` 自动恢复）——验证此路径成立并在 result.md 记录
4. **项目导出**：确认导出包包含 `agent_nodes/` 目录（会话文件随项目导出）；若导出逻辑是白名单式且漏了该目录，补上
5. 新增 `AgentHostManager` 的项目级清理方法（如 `cleanup_project(project_id)`），kill 进程 + 移除 session 记录
6. **测试**：用既有 fake agent host（`tests/fake_agent_host.py`）写单测——创建 session → 模拟项目删除 → 断言进程被 kill、session 记录清除；多项目隔离（删 A 项目不影响 B 项目的 session）
7. 全量回归 `python -m pytest tests/ -q --ignore=tests/excel_import_browser.py` 全绿（单测+浏览器，命令 10-15 分钟，bash timeout 给 1200s+，只报 passed/failed 数）
8. 写 `.pipeline/batches/batch-2-result.md`（覆盖现有旧文件）：改了什么、US-27 验收 1-6 逐条、US-22 确认、测试输出、遗留风险
9. 不要提交 commit

## US-22 确认（小项）
"Agent 默认跟随全局默认模型"：app 无全局文本模型设置（grep 确认），`spawn_session` 在 model 为空时不传 `--model`，pi 用自身配置的默认模型——即全局默认。验证此行为（读代码即可），在 result.md 记录结论；若发现 app 其实有全局模型设置而 spawn 没接上，则接上并测试。

## 约束
- 不碰 canvas-tools.ts、TUI 前端渲染、旧引擎保留区（services/agents.py）
- 清理方法要幂等（重复调用不报错）
- 项目删除路径可能有多个入口（单个删除/批量/回收站清空），全部覆盖并在 result.md 列清单
- 一次跑不完就在干净边界停下，result.md 写清进度
