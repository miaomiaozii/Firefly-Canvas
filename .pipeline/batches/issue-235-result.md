# issue-235 结果报告（provider 未知任务终态化 + resume 显式 not-found）

**状态：完成（provider-agnostic 部分）**。票面 RunningHub 专属段经维护者裁定「RunningHub 不接入」，该段验收作废（已在票上签字）：未改动 RunningHub 任何既有产品逻辑，测试文件也不保留 RunningHub 专属测试。

分支 `issue/235`，基于 `origin/main` @ `4d8132a`（wave1 十七票已合主线，`v2026.10.wave1`）。已 ff 到 origin/main 后接管（前 worker 仅留下未跟踪的契约测试草稿，无已提交代码）。

## Commits

| commit | 内容 |
|---|---|
| 见 git log `fix(provider): #235 ...` | 产品代码 3 文件 + 新契约测试 1 文件（11 用例） |
| 见 git log `chore(pipeline): #235 结果报告` | 本报告 |

## 票面要求落地（provider-agnostic）

### 处方 1：轮询状态机「连续 N 次未知/不存在 → 终态 fail，文案显式带 task ID」

- `app/core.py`：新增 `IMAGE_TASK_UNKNOWN_STATUSES = {'NOT_FOUND','NOTFOUND','NOT_FOUND_ERROR','TASK_NOT_FOUND','UNKNOWN'}` 与 `IMAGE_TASK_UNKNOWN_TERMINAL_AFTER = 2`（紧随既有 SUCCESS/FAILED 状态集）。
- `app/services/generation.py` `wait_for_image_task`：新增 `unknown_streak` 计数——连续 2 次 provider 明确返回未知/不存在状态 → 抛 `HTTPException(502, '生图任务不存在或状态未知（task_id=…，连续 N 次查询返回 …），请确认任务 ID 后显式重试')`，不再空转到 504 超时。单次抖动容忍：出现任何可识别状态（成功/失败/进行中/空状态）即重置计数。
- 项目任务 worker（`app/routers/api_project_library.py` / `app/services/generation.py` 的 `run_project_image_task` / `run_project_video_task`）无需改动：其失败路径提取 `HTTPException.detail` 落任务 error 字段，显式 task ID 文案随 detail 自然传递。

### 处方 2：上游 404 → 结构化 not-found 载荷，不再裸抛

- `app/services/generation.py` `fetch_image_task_payload`：`response.status_code == 404` → 返回 `{'status': 'NOT_FOUND', 'task_id': …, 'message': '生图任务不存在（task_id=…）'}`；非 404 维持 `raise_for_status()` 原行为。
- `app/routers/api_image_task_query.py` `query_upstream_image_task`（/api/image-task-query 与项目任务主动取回共用）：未知状态（含 404 映射出的 NOT_FOUND）→ 返回 `{'status': 'failed', 'error': '服务商侧任务不存在或状态未知（task_id=…，最后状态：…）'}`，不得当 running；进行中状态（如 PROCESSING）保持 running。

### 处方 4：视频 resume 显式「服务商侧任务不存在」

- `app/services/generation.py` `wait_for_video_task`：候选 URL 全部 404 → 抛 `HTTPException(502, '服务商侧视频任务不存在（task_id=…），请确认任务 ID 后显式重试')`，不再裸抛 `httpx.HTTPStatusError`（task ID 不再只"顺带"出现在 URL 里）；存在任何非 404 错误时保持旧行为抛最后一个错误。
- 视频 resume（`app/routers/api_project_library.py` `_resume_project_video_generation`）无需单独加分支：其 except 提取 `exc.detail` 写入任务终态错误，显式 not-found 文案（含 task ID）自然落画布。

### RunningHub 段（验收作废）

- 原处方 3（RunningHub 未知 code → 终态 fail）未实现；`wait_for_runninghub_image_task` / `wait_for_runninghub_openapi_task` 与 `query_upstream_image_task` 的 RunningHub 分支保持 main 现状不动。
- 前 worker 草稿测试中的 RunningHub 专属用例（router 层 2 例 + 轮询层 3 例）已删除，未进入测试文件。

## 测试

`tests/test_provider_unknown_terminal.py`（新，11 用例，全绿）：

- `WaitImageTaskUnknownTerminalTests`（4）：连续 NOT_FOUND → 502 且 detail 含 task_id；单次 NOT_FOUND 后 SUCCESS 仍成功；PROCESSING/PENDING 照常轮询至成功；NOT_FOUND/UNKNOWN ∈ 未知集。
- `FetchImageTaskPayload404Tests`（2）：404 → 结构化 NOT_FOUND 载荷（含 task_id）；500 仍裸抛 `httpx.HTTPStatusError`。
- `ImageTaskQueryUnknownTerminalTests`（2，TestClient 真 app）：未知状态 → failed 结果；PROCESSING → running。
- `VideoTaskNotFoundTests`（3）：全部候选 404 → 502 显式「不存在」+ task_id（3 个候选全试）；成功载荷正常返回；404+500+500 混合仍抛最后一个 500。

测试桩说明：视频/取载荷用例打桩 `main.provider_env_key_value → 'test-key'`（`api_headers` 对无 Key 的 provider 在发请求前抛 400，与产品行为一致）。

## 验证证据

| 项 | 结果 |
|---|---|
| `pytest tests/test_provider_unknown_terminal.py` | **11 passed**（~10s） |
| main 基线全量 pytest（`4d8132a` 干净工作区） | 3 failed, 1540 passed, 1 skipped |
| `issue/235` 全量 pytest | 3 failed, **1551 passed**（= 基线 +11 本票新用例）, 1 skipped；失败集合与基线逐条一致（canvas JS 体积棘轮 `canvas-generation-tasks.js: 103 > 101`、node 子进程 120s 超时、耦合棘轮——均为 main 既有债务，与本票无关） |
| `python tools/quality_gate.py fast` | **passed**（16.2s） |
| `python tools/quality_gate.py domain` | **passed**（131.8s，一次通过无需重试；含票面要求的 `generation-lifecycle-evidence` 与 `provider-contract` 步骤） |

## 已知边界

- Agnes 专属视频查询 `wait_for_agnes_video_task` 保持裸异常行为——票面处方针对通用轮询路径（`wait_for_video_task`），未点名 Agnes，保持最小面。
- 空状态（payload 无 status 字段）不计入未知计数，维持既有"轮询到超时兜底"行为——避免误杀不报状态的慢速 provider；仅 provider **明确**返回未知/不存在状态才终态化。
- RunningHub 既有逻辑未动（裁定不接入，验收作废）。
