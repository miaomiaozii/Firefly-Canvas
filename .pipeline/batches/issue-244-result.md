# issue-244 结果：/api/healthz 暴露运行态指标（wayfinder:wg-scout-wiring 侦察接线）

## 结论

按 wayfinder 地图结论执行：不建 metrics 子系统，新增单一只读端点 `GET /api/healthz`，把已接线的会话注册表与 agent 进程状态如实投影为少量运行态指标。纯内存只读——不写 ops.db、不建 metrics 持久层、无网络出口、无新依赖。

## 票面偏差记录（重要）

- GitHub #244 票面正文实际是「Feat(W2)：Agent/MCP 工具面 17→~30」（canvas.view/view_media/edit_*/batch_edit），与本工单（healthz 侦察接线）描述不符。本次按派工指令（wayfinder 侦察接线）实施，未动 W2 工具面；票面归属请主控侧澄清。
- 派工预注入锚点与实际仓库有出入，实施时已按真实代码校准：
  - `app/agent_host.py` → 实际路径 `app/services/agent_host.py`（`AgentHostManager` 单例 `agent_host_manager`，含看门狗自动 respawn）。
  - 「既有 /healthz」→ 仓库中原本**不存在**任何 healthz 端点（grep 确认），故按 `api_app_info.py` 的最小只读路由惯例新建 `app/routers/api_health.py`。
  - 「ADR 0021 边界」→ 本仓库 ADR 止于 0019，无 0021 文件；边界要求（不写 ops.db、无持久层）按派工文字执行。

## 改动清单

| 文件 | 改动 |
|------|------|
| `app/routers/api_health.py` | 新增。`GET /api/healthz` 只读投影端点（async def，经 manager 锁内快照） |
| `app/project_sessions.py` | 新增只读投影函数 `open_project_ids() -> list`（`_sessions` 注册表内去重排序，持 `_lock`） |
| `app/services/agent_host.py` | 新增常量 `AGENT_STATUSES = ("stopped","starting","running","busy","waiting_confirm","crashed")`；`AgentHostManager` 新增 `async snapshot_sessions() -> List[dict]`（持 `_lock` 的只读投影，绝不改状态） |
| `main.py` | 在 `api_app_info` 之后注册 `api_health` 路由（2 行） |
| `tests/test_healthz.py` | 新增 4 个测试（见下） |

## 响应形状（稳定契约）

```json
{
  "status": "ok",
  "open_project_count": 2,
  "open_project_ids": ["proj-a", "proj-b"],
  "agents": {
    "total": 1,
    "by_status": {"stopped": 0, "starting": 0, "running": 1, "busy": 0, "waiting_confirm": 0, "crashed": 0},
    "sessions": [
      {"project_id": "proj-a", "node_id": "node-1", "status": "running", "model": "...", "autonomy": "standard", "alive": false}
    ]
  }
}
```

- `open_project_ids` 恒为去重升序列表，`open_project_count == len(open_project_ids)`。
- `agents.by_status` 恒含全部 6 个已知状态键（未知状态防御性追加），`sum(by_status) == total`。
- `agents.sessions[*]` 字段集固定为 `{project_id, node_id, status, model, autonomy, alive}`，`alive = session.is_running`（子进程存活）。
- 快照异常时降级为空列表，端点不 500。

## 测试矩阵

| 用例（tests/test_healthz.py） | 覆盖 | 结果 |
|------|------|------|
| `test_healthz_returns_stable_shape` | 形状稳定性：全字段存在、类型、计数自洽、by_status 键齐全、session 字段集固定 | ✅ 通过 |
| `test_healthz_reports_open_project_sessions` | open 会话如实上报：注册→计数 2、同项目双连接去重、注销→归零 | ✅ 通过 |
| `test_healthz_reports_agent_session_summary` | agent 状态摘要如实上报：total/by_status/sessions 明细一致 | ✅ 通过 |
| `test_open_project_ids_is_a_sorted_unique_projection` | 投影函数单测：排序 + 去重 | ✅ 通过 |

## 全量回归（pytest tests/ -q）

- 本分支：`3 failed, 1041 passed, 351 subtests passed`（4:56）
- 基线 main（c6541df，临时 worktree 验证）：`3 failed, 1037 passed, 351 subtests passed`
- 两侧失败集合**完全相同**，均为 `tests/test_agent_generate_tools.py` 的 3 个既有失败（`test_get_task_status`、`test_trigger_generation`、`test_wait_for_task`，`KeyError: 'task_id'`）——main 上已存在，与本分支无关，未在本工单范围内处理。
- 本分支新增回归：0。

## 过程备注

- 首版测试在全量运行中误报：前序 agent 测试文件向全局 `agent_host_manager.sessions` 泄漏会话，导致精确断言失败。已改为 setUp 快照+清空、tearDown 恢复（测试自身隔离，不依赖执行顺序）；定向复跑（agent 测试文件 + healthz 串联）通过，最终全量 4/4 绿。
- 无新依赖；无 ops.db 写；无持久层；只 commit 未 push。

## commits

- `e571927` feat(#244): /api/healthz 暴露运行态指标（open 会话/agent 状态只读投影）
- `bf0f212` test(#244): healthz 测试隔离全局 agent 会话表，修复全量运行中的前序泄漏误报
