# batch-1 审查记录（#117 US-21）

## 第 1 轮：PASS（2026-09-24，主会话审查）

twin 经历 2 次运行（首次 25 分钟超时于 import 链修复中，resume 后完成）。主会话逐条亲验：

| # | 验收标准 | 判定 | 验证方式 |
|---|---|---|---|
| 1 | 前端删旧交互 UI | PASS | git diff 亲核：死字段（agentTaskDraft/agentTaskDrafts/agent_plan）、cancel/retry 按钮、agentTaskControlAction/refreshAgentPlanState 已删，无调用残留 |
| 2 | 旧项目兼容（只读 legacy 渲染） | PASS | diff 亲核：agent-task-card-readonly 类、agentTaskStatusText 补回、浏览器选择器（.agent-task-card/[data-agent-task-status] 等）保留；浏览器测试全绿 |
| 3 | 后端删旧端点、共享符号保留并记录 | PASS | api_project_library.py 已无任何 agent 旧端点；引擎保留理由核实成立（startup.py:404 启动恢复 in-flight 旧任务 + services/agents.py 活引用） |
| 4 | i18n 删旧键无残留 | PASS | 24 个被删键逐个 grep JS 引用：零残留 |
| 5 | 测试更新/保留 | PASS | 全绿（见 6） |
| 6 | 全量回归 | PASS | 单测 1001 passed（twin 跑）+ 浏览器 57 passed + 22 subtests（主会话亲跑，72.58s）+ `import main` OK |
| 7 | result.md | PASS | 删除清单/保留符号/兼容验证/逐条核对/遗留风险齐全 |
| 8 | 不提交 | PASS | 工作区未提交 |

### 备注（不阻塞，记录在案）
1. `core.py:5396 _agent_task_specs(payload: ProjectAgentPlanRequest)` 引用已删类——死代码（零调用点），因 `from __future__ import annotations` 无运行时风险。属保留引擎区，后续大清理可一并处理
2. `_from_main_names` 列表含已删函数名（project_agent_cancel_task/retry_task）——getattr(None) 空安全，stale 但无害
3. `services/agents.py:153/172` 的 project_agent_cancel_task/retry_task 服务函数现为死函数（端点已删）——同属保留引擎区
4. twin 保留引擎的理由表述不准确（"新 TUI 流程创建 agent-task 节点"——canvas-tools create_node 类型描述不含 agent-task）；真实保留理由是启动恢复路径，结论（保留）正确
5. twin 的"全量回归"实为单测（319.99s）；浏览器测试由主会话补跑全绿

### 结论
US-21 达成：旧"先计划后执行"UI 整体移除（无创建入口、无交互按钮、无旧端点），旧项目 agent-task 节点只读 legacy 渲染不破坏。旧执行引擎作为遗留数据兼容保留（启动恢复 + 状态读取），与任务卡"宁可少删"约束一致。
