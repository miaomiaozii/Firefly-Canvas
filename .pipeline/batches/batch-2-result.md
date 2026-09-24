# batch-2 结果（#117 US-27 + US-22）：Agent 会话的项目生命周期 + 模型默认行为确认

## 完成时间
2026-09-24

## 改动清单

### 1. AgentHostManager.cleanup_project(project_id) — 新增
**文件**: `app/services/agent_host.py`
- 新增 `cleanup_project(project_id: str)` 方法
- 收集该项目所有 session（key 前缀 `{project_id}:`）
- 在锁内从 `self.sessions` 移除（防止并发 get_or_create 竞争）
- 在锁外逐个 `stop_session`（避免持锁期间做进程 I/O）
- 幂等：重复调用不报错；无 session 时静默返回

### 2. 项目删除/回收站时调用清理 — 接线
**文件**: `app/routers/api_project_library.py`
- 导入 `agent_host_manager`
- `trash_firefly_project`（DELETE /api/project-library/{project_id}）：trash 成功后调用 `cleanup_project`
- `purge_trashed_project`（DELETE /api/project-library/trash/{project_id}）：purge 成功后调用 `cleanup_project`
- `restore_trashed_project`（POST /api/project-library/trash/{project_id}/restore）：无需清理（会话文件随目录回来，下次交互 --continue 自动恢复）

### 3. 项目导出包含 agent_nodes/ — 导出/导入
**文件**: `firefly_projects.py`
- `export_fly_to`: 遍历 `agent_nodes/{node_id}/sessions/` 下所有文件，加入 zip（路径 `agent_nodes/{relative}`，ZIP_DEFLATED 压缩）并加入 manifest
- 导入验证：允许 `agent_nodes/` 前缀路径（原只允许 fixed_files + `media/`）
- 导入提取：遍历 `agent_nodes/` 文件，解压到项目目录（含路径安全检查）

### 4. 测试 — 新增
**文件**: `tests/test_agent_tui.py`
- `TestCleanupProject` 类，4 个测试：
  - `test_cleanup_project_kills_sessions`: 创建 session → cleanup → 进程被 terminate、session 记录清除
  - `test_cleanup_project_multi_project_isolation`: 删 A 项目不影响 B 项目的 session
  - `test_cleanup_project_idempotent`: 重复调用 cleanup 不报错
  - `test_cleanup_project_no_sessions`: 对无 session 的项目调用 cleanup 不报错
- 使用既有 `FakeAgentProcess`（tests/fake_agent_host.py）

## US-27 验收标准逐条核对

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | 项目永久删除时 kill agent 会话 | ✅ | `purge_trashed_project` 端点调用 `cleanup_project`；测试 `test_cleanup_project_kills_sessions` |
| 2 | 项目进回收站时 kill agent 会话 | ✅ | `trash_firefly_project` 端点调用 `cleanup_project`；session 文件在项目目录内随项目进回收区 |
| 3 | 项目恢复时无需特殊处理 | ✅ | 会话文件随项目目录回来；`spawn_session` 自动检测 `--continue`（agent_host.py:123-128） |
| 4 | 项目导出包含 agent_nodes/ | ✅ | `export_fly_to` 遍历并打包 agent_nodes/ 文件；导入验证+提取已支持 |
| 5 | AgentHostManager 项目级清理方法 | ✅ | `cleanup_project(project_id)` 已实现 |
| 6 | fake_agent_host 单测覆盖 | ✅ | `TestCleanupProject` 4 个测试全过 |
| 7 | 全量回归全绿 | ✅ | 1005 passed, 12 warnings, 351 subtests passed in 316.30s |
| 8 | result.md | ✅ | 本文件 |
| 9 | 不提交 commit | ✅ | 工作区未提交 |

## US-22 确认

**结论**: 设计满足，无需代码改动。

**验证**:
- app 无全局文本模型设置（`grep default_model` 仅找到 `volcengine_default_model_payload`，是图片生成相关）
- `spawn_session(session, model="", continue_session=False)`: 当 `model` 和 `session.model` 都为空时，不传 `--model` 参数给 pi CLI（agent_host.py:131-137）
- pi CLI 不传 `--model` 时使用自身配置的默认模型（~/.pi/config.json 或环境变量）
- 因此"全局默认模型" = pi 的默认模型，行为正确

## 遗留风险
- `cleanup_project` 在 trash/purge 成功后调用；如果 cleanup 本身异常（理论上不会，stop_session 有 try/except），不影响已完成的 trash/purge 操作
- 导出包含 agent_nodes/ 会增加 .fly 包大小（通常很小，JSONL 会话文件）；无大小限制检查（与 media 文件不同，agent 会话文件通常 < 1MB）
- 旧版 .fly 包（无 agent_nodes/）导入时不受影响（agent_nodes/ 是可选的）

## 测试输出
```
1005 passed, 12 warnings, 351 subtests passed in 316.30s (0:05:16)
```
