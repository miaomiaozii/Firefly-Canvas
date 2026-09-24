# batch-6 结果：#143 并行 Agent（多节点并发执行验证 + 修复）

## 验证矩阵

| 并发行为 | 验证方式 | 结果 | 备注 |
|---------|---------|------|------|
| 多 Agent 节点可同时运行任务 | 创建 2 个独立 session，各自独立 FakeAgentProcess，并发 send_prompt | ✅ 通过 | 每个节点独立子进程，互不阻塞 |
| 并发画布变更经 revision 机制串行化 | 4 线程 × 5 次 upsert_node 并发，验证 revision 单调递增 | ✅ 通过 | revision 从初始值 +20，无丢失 |
| 多 Agent 并行生成任务共存 | 并发创建 2 个 CANVAS_TASKS，验证独立状态 | ✅ 通过 | 任务状态卡、node_id 各归其位 |
| 各节点 TUI 事件流互不串扰 | 注册独立 event handler，分别 dispatch 事件 | ✅ 通过 | A 节点事件不泄漏到 B 节点 |
| 并发 prompt 请求互不阻塞 | asyncio.gather 并发 send_prompt，验证耗时 <1s | ✅ 通过 | 两个 prompt 都正确送达各自进程 |
| 并发画布变更 retry 机制 | 4 线程并发 _apply_mutations，触发 revision conflict | ✅ 通过（修复后） | 发现并修复了 retry 不生效的 bug |
| 多节点共存于同一 manager | 创建 3 个 session，并发 send_prompt | ✅ 通过 | manager.sessions 正确跟踪 3 个 session |

## 发现的并发 Bug 清单 + 修复说明

### Bug #1：`_apply_mutations` retry 机制对 `RevisionConflict` 不生效

**文件**: `app/routers/api_agent_tools.py:45-58`

**问题**: `_apply_mutations` 的 retry 循环检查 `"revision" in str(e).lower()` 来判断是否重试，但 `RevisionConflict` 异常的默认消息是 `"画布已在其他窗口更新"`（中文），不包含 "revision" 字样。因此在并发场景下，revision conflict 时 retry 永远不会触发，直接抛出 409 HTTPException。

**修复**: 改为按异常类型判断，直接 `except RevisionConflict`：

```python
from firefly_projects import RevisionConflict

def _apply_mutations(project_id: str, mutations: list):
    store = _app_main.FIREFLY_PROJECT_STORE
    for attempt in range(8):
        try:
            canvas = store.canvas(project_id)
            revision = int(canvas.get("revision") or 0)
            result = store.apply_mutations(project_id, mutations, base_revision=revision)
            return result
        except RevisionConflict:
            if attempt < 7:
                continue
            raise HTTPException(status_code=409, detail="Canvas mutation failed after retries")
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e))
```

**影响**: 所有通过 Agent 工具（canvas.create_node、canvas.update_node、canvas.delete_node、canvas.connect、canvas.disconnect）修改画布的操作，在并发场景下都会因 revision conflict 而失败（之前静默失败，现在正确 retry）。

## 测试命令与结果

```bash
# 新增并发测试
python -m pytest tests/test_agent_concurrency.py -v
# 结果：9 passed in 0.91s

# 全量回归
python -m pytest tests/ -q
# 结果：1039 passed, 12 warnings, 351 subtests passed in 321.45s (0:05:21)
```

## 新增测试文件

`tests/test_agent_concurrency.py` — 9 个测试用例：

1. `test_session_isolation` — 每个 Agent 节点有独立 session
2. `test_concurrent_prompts_do_not_block` — 并发 prompt 互不阻塞
3. `test_event_stream_isolation` — 事件流按节点隔离
4. `test_concurrent_canvas_mutations_revision_monotonic` — revision 单调递增
5. `test_concurrent_canvas_mutations_with_retry` — revision conflict 时 retry 生效
6. `test_concurrent_generation_tasks` — 并发生成任务独立
7. `test_stress_concurrent_structural_changes` — 压力测试：4 线程 × 5 次变更
8. `test_concurrent_status_updates` — 并发状态更新互不干扰
9. `test_multiple_nodes_same_session_manager` — 多节点共存于同一 manager

## 并发上限/资源结论

- **Agent 节点数量**: 无显式上限，受限于系统进程数（每个节点一个 pi 子进程）
- **并发画布变更**: 通过 revision + retry 机制串行化，无上限但高并发下可能有 retry 开销
- **并发生成任务**: 复用既有生成管线限制（`CANVAS_TASKS` dict），无 Agent 专属限制
- **资源问题**: 未发现需要加 Agent 专属并发上限的问题

## 遗留问题

- 浏览器 E2E 测试（两个 Agent 节点在真实浏览器中并行运行）未在本批次实现——需要 Playwright/Selenium 环境，建议作为后续批次
- 高并发下（>10 个 Agent 同时操作同一画布）retry 开销可能增加，但未观察到实际性能问题
- `RevisionConflict` 消息不包含 "revision" 字样是一个设计缺陷，其他依赖字符串匹配的代码也可能有类似问题（建议后续统一改为按异常类型判断）
