# batch-1 结果（#117 US-21）：移除旧"先计划后执行"Agent UI 与后端机制

## 完成时间
2026-09-23

## 删除清单

### 前端（static/js/smart-canvas.js）
- `agentTaskNodeBodyHtml`：移除 cancel/retry 按钮，改为只读 legacy 卡片（保留 `.agent-task-card-readonly` class）
- 节点创建死字段：`agentTaskDraft`、`agentTaskDrafts`、`agent_plan`（createAgentNode）
- `agentTaskControlAction` 函数（cancel/retry API 调用）
- `refreshAgentPlanState` 函数
- `bindAgentTaskNodeControls` 简化为仅绑定 result link（移除 cancel/retry 按钮绑定）
- 补回缺失的 `agentTaskStatusText` 函数（legacy 卡片渲染依赖）

### 后端端点（app/routers/api_project_library.py）
- `GET /api/project-library/{project_id}/agent/{node_id}/tasks/{task_id}`（get_project_agent_task）
- `POST /api/project-library/{project_id}/agent/{node_id}/tasks/{task_id}/cancel`（cancel_project_agent_task）
- `POST /api/project-library/{project_id}/agent/{node_id}/tasks/{task_id}/retry`（retry_project_agent_task）

### 后端模型（app/core.py）
- `ProjectAgentPlanRequest` class
- `ProjectAgentExecuteRequest` class
- `PROJECT_AGENT_MAX_TASKS_PER_PLAN` 全局量

### i18n（static/js/i18n/smart-canvas.js）
移除旧 plan-execute UI 专属键：
- agentTaskLabel, agentTaskMeta, agentTaskPrompt
- agentPlanAria, agentPlanTitle, agentPlanId, agentPlanEmpty
- agentSubtitle, agentGoalPlaceholder
- agentTaskType, agentTaskCountLabel, removeTask, remove
- agentTaskPromptPlaceholder, dependentTask, agentTaskDeps, noDependency
- taskLabel, addTask, generatePlan, confirmAndRun

保留 legacy 卡片所需键：agentTaskTitle, agentTaskModelCount, agentTaskParams, agentKindImage/Video/Prompt, viewResultNode, waitingResultNode

### import 链修复（main.py, api_project_library.py）
- main.py：移除 get_project_agent_task、ProjectAgentPlanRequest、ProjectAgentExecuteRequest、PROJECT_AGENT_MAX_TASKS_PER_PLAN 的 import
- api_project_library.py：移除 ProjectAgentPlanRequest、ProjectAgentExecuteRequest 的 import（不再使用）

## 保留的共享符号（及理由）

### _agent_* 函数（core.py）
全部保留，因为被旧流程执行引擎（services/agents.py、services/_base.py、routers/startup.py、api_project_library.py）使用：
- _agent_clean_params, _agent_forbidden_value, _agent_plan_response, _agent_public_task, _agent_task_specs（api_project_library.py 使用）
- _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout（services/agents.py、services/_base.py 使用）
- _agent_int_param, _agent_normalize_candidates, _agent_history_output（services/agents.py、services/_base.py 使用）

### PROJECT_AGENT_* 全局量（core.py）
全部保留（除 PROJECT_AGENT_MAX_TASKS_PER_PLAN），因为被旧流程执行引擎使用：
- PROJECT_AGENT_TASKS, PROJECT_AGENT_TASKS_LOCK, PROJECT_AGENT_FUTURES, PROJECT_AGENT_RUNNING_IDS, PROJECT_AGENT_EXECUTION_LOCKS, PROJECT_AGENT_EXECUTION_LOCKS_GUARD, PROJECT_AGENT_ADMISSION_LOCK, PROJECT_AGENT_MAX_PER_PROJECT（api_project_library.py、services/agents.py 使用）
- PROJECT_AGENT_RECOVERY_LOCK（routers/startup.py 使用）

## 旧项目兼容验证
- legacy agent-task 节点渲染保留（只读卡片，无按钮/交互）
- `agentTaskStatusText` 函数补回（之前意外删除导致 legacy 卡片渲染报错）
- 测试中创建 agent-task 节点的用例（test_project_api.py、test_project_history.py、test_project_canvas_mutation_client.py）原样通过
- 浏览器测试（agent_browser_client.js 等）引用 `.agent-task-node`、`.agent-task-card`、`[data-agent-task-status]`、`.agent-task-meta`、`.agent-task-params`、`[data-agent-result-link]`，这些 CSS 选择器在 legacy 卡片中保留

## 验收标准逐条核对
1. ✅ 前端删除旧交互 UI（cancel/retry 按钮、死字段）
2. ✅ 旧项目兼容（legacy 只读渲染保留，测试通过）
3. ✅ 后端删除旧流程专属端点（plan/execute/cancel/retry/get task）；共享 helper/全局量保留并记录
4. ✅ i18n 删除旧 UI 专属键；通用键（cancelTask/retryTask）保留
5. ✅ 测试：覆盖旧流程的测试用例（创建 agent-task 节点）原样通过；新流程测试未受影响
6. ✅ 全量回归：1001 passed, 12 warnings, 351 subtests passed in 319.99s
7. ✅ 本文件
8. ✅ 未提交 commit

## 测试输出
```
1001 passed, 12 warnings, 351 subtests passed in 319.99s (0:05:19)
```

## 遗留风险
- 旧"先计划后执行"的执行引擎（services/agents.py 等）仍保留，因为新 TUI 流程的工具调用结果仍创建 agent-task 节点。未来如果新流程完全独立，可以进一步清理。
- legacy 卡片是只读的，旧项目的 agent-task 节点无法通过 UI 取消/重试（符合 US-21 要求：旧形态整体移除）。
