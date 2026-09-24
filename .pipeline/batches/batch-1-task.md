# batch-1 任务卡（#117 US-21）：移除旧"先计划后执行"Agent UI 与后端机制

## 背景
#117 把 Agent 节点重做为 Pi 终端 TUI 形态。新流程已上线（smart-agent 节点 + TUI 面板）：
- 后端: `app/routers/api_agent_nodes.py` + `app/services/agent_host.py`
- 扩展: `app/agent_extensions/canvas-tools.ts`
- 前端: `static/js/smart-canvas.js` 中 `agentTuiState`/`connectAgentWebSocket`/`agentNodeBodyHtml` 等（约 11331 行起）

旧"先计划后执行"流程（用户填任务类型/数量/提示词/依赖 → 生成计划 → 确认并执行 → agent-task 任务节点）的前端**创建入口已移除**，但残留未清：
- 前端渲染: `isAgentTaskNode`（1326 行）、`agentTaskNodeBodyHtml`（11699 行起，含 cancel/retry 按钮）、节点创建时的死字段 `agentTaskDraft`/`agentTaskDrafts`/`agent_plan`（7975-7977 行）、尺寸/标题分支（22928-22929、12666 行）、2370 行
- 后端机制: `app/routers/api_project_library.py` 中 `ProjectAgentPlanRequest`/`ProjectAgentExecuteRequest`、`project_agent_cancel_task`（1397 行）、`project_agent_retry_task`（1410 行）、`get_project_agent_task`（296 行）、`project_has_active_agent_task`（2482 行）等端点；`app/core.py` 中 `_agent_plan_*`/`_agent_task_*`/`_agent_normalize_task` 等 helper 与 `PROJECT_AGENT_*` 全局量（PROJECT_AGENT_TASKS、PROJECT_AGENT_EXECUTION_LOCKS、PROJECT_AGENT_FUTURES、PROJECT_AGENT_RUNNING_IDS、PROJECT_AGENT_MAX_PER_PROJECT、PROJECT_AGENT_MAX_TASKS_PER_PLAN、PROJECT_AGENT_ADMISSION_LOCK 等）
- i18n: `agentTaskTitle`/`agentTaskModelCount`/`agentTaskParams`/`agentTaskStatus*` 等旧键（`static/js/i18n/` 下）
- 测试: `tests/test_project_api.py`、`tests/test_project_history.py`、`tests/agent_*_browser_client.js`、`tests/test_project_canvas_mutation_client.py` 中覆盖旧流程的部分

## 目标
旧形态整体移除（spec 决断："旧的'先计划后执行'任务列表 UI 整体移除；'计划'变成对话中的自然步骤"），Agent 节点只保留 TUI 一种范式。

## 验收标准
1. 前端删除旧交互 UI：`agentTaskNodeBodyHtml` 中的计划/任务卡片交互（cancel/retry 按钮、任务列表）、节点创建时的死字段 `agentTaskDraft`/`agentTaskDrafts`/`agent_plan`
2. **旧项目兼容**：画布 JSON 里已有 `agent-task` 节点的项目加载不报错、不白屏。保留最小只读 legacy 渲染（任务卡片只读展示，无任何按钮/交互入口）。这是硬约束，必须有浏览器测试或单测证明
3. 后端删除旧流程专属端点（plan/execute/cancel/retry/get task）与专属 helper/全局量。**删除前必须 grep 验证**：新流程文件（api_agent_nodes.py、agent_host.py、canvas-tools.ts）不引用任何待删符号；被新流程或无关模块共享的符号**保留**并在 result.md 记录
4. i18n 删除旧 UI 专属键（先 grep 确认无其他 UI 共享；`cancelTask`/`retryTask` 这类通用键若被其他 UI 用则保留）
5. 测试：删除或改写覆盖旧流程的测试（test_project_api.py、test_project_history.py、agent_*_browser_client.js、test_project_canvas_mutation_client.py 中的相关用例）；新流程测试（test_agent_tui.py、test_agent_*_tools.py、agent_ui_browser 等）原样通过
6. 全量回归 `python -m pytest tests/ -q` 全绿（单测 + 浏览器测试；命令较慢，给 bash 足够 timeout 或分批跑，只报 passed/failed 数）
7. 写 `.pipeline/batches/batch-1-result.md`：删了什么（文件/符号清单）、保留了什么共享符号及理由、旧项目兼容验证证据、验收 1-6 逐条、测试输出、遗留风险
8. 不要提交 commit

## 约束
- 不碰新流程：api_agent_nodes.py、agent_host.py、canvas-tools.ts、smart-agent TUI 渲染
- 不碰对话节点（ADR 0002）
- 删除是主体工作：宁可少删（保留并记录）不可多删（删掉共享符号导致回归挂）
- 如果一次跑不完：在干净边界停下（如"前端删完、后端未动"），result.md 写清进度，不留半删状态
