# issue-262-result.md：全局跨项目任务总控台（成本/耗时/筛选/reveal 定位产物）

## 接管说明

前 worker 死于 140m 时箱，**零 commit**；worktree `D:/ff-wt/w262`（分支 `issue/262` @ `origin/main` 30dc6fa）留 4 个脏文件。本 worker 接管后：

1. 审 4 文件 diff → 按主题拆 3 commit 固化（usage 归因 / 路由+main.py 注册 / 测试 8 例）。
2. 审计草稿对票面完成度：数据面草稿完整可用，前端未动 → 本 worker 补全前端。
3. 全量跑 pytest 定位失败，逐条与基线比对（见测试证据）。

## 改动清单（5 commits，均已 push origin issue/262）

| commit | 主题 |
|---|---|
| `3ac2ff6` | feat(usage): `list_usage_by_node`（#258 usage_turns 租户按节点聚合成本，供 #262 成本列归因） |
| `34a656c` | feat(global-tasks): `app/routers/api_global_tasks.py` 新路由 `/api/global-tasks` + main.py 仅加路由注册行 |
| `8e5b121` | test(global-tasks): `tests/test_global_tasks.py` 8 例（跨项目列表/成本归因/取消复用/筛选排序） |
| `29b9af3` | feat(global-tasks): 全局设置页 06 总控台 section（状态/服务商/项目筛选 + 2s 自动刷新 + 逐任务取消）+ `smart-canvas.js` `focusRevealNode` reveal 定位（+22 行最小接线）+ i18n |
| `e8b8501` | fix(global-tasks): smart-canvas.html 全页缓存戳统一（fast 门 cache-buster 不变量要求整页单一 `?v=`） |

### 数据面（`app/routers/api_global_tasks.py`，全只读，不新增 ops.db 租户）

- 在飞：`app/core.py CANVAS_TASKS`（queued/running 实时）；终态：各项目 `canvas.history`（tasks 租户语义随画布快照落盘，#234）；成本：各项目 ops.db `usage_turns`（#258）经 `result_node_id` 归因。
- 行字段：task_id / project(+name) / kind / status / provider(+name) / model / prompt 预览 / created_at / finished_at / **duration_sec** / **cost_usd** / tokens / result_node_id / **cancellable**。
- `status=active` 快捷过滤；`project_id` 过滤；`limit` ≤200；内存行覆盖同名 history 行（内存更新）。
- 取消**不新增端点**：逐任务取消复用 `POST /api/project-library/{pid}/image-generation-tasks/{tid}/cancel`（前端 `controlProjectImageTask` 同一通道）；`cancellable` 仅对内存内飞的 `project-online-image` queued/running 任务为真。

### 前端（全局设置页 06 section，前 worker 勘察方向落实）

- `static/global-settings.html`：新增 section 06「全局任务总控台」（heading-actions：自动刷新 2s 开关 + 手动刷新；filters：状态/服务商/项目；`#taskConsoleList`）。
- `static/js/global-settings.js`：拉 `/api/global-tasks?limit=200`，状态/项目筛选走服务端、服务商筛选走客户端；行=状态徽标+项目·提示词+`kind · provider · model · 耗时 · 成本`+「查看」+（可取消时）「取消」。
- reveal：「查看」= 路由级跳转 `/static/smart-canvas.html?id=<canvas>&project=<pid>&project_mode=1&focus_node=<node>`（对齐 `project-list.js:43` 既有导航格式）；`smart-canvas.js` 画布加载完成后读 `focus_node` → `focusRevealNode(nodeId)` 选中并居中该节点。
- 样式追加 `static/css/global-settings.css` 尾部（`--panel/--line` 既有变量）；i18n 键入 `static/js/i18n/studio.js`（globalSettings.tasks*）。

## 票面验收对照

| 验收 | 结果 |
|---|---|
| 跨项目列出 queued/running/failed（provider、耗时、成本列） | PASS（`tests/test_global_tasks.py` 8 passed；冒烟实测在飞任务带 provider=comfly/model/耗时/项目名） |
| 总控台面板挂全局设置页 | PASS（section 06，2s 自动刷新） |
| 筛选（状态/服务商/项目） | PASS（状态+项目服务端、服务商客户端） |
| reveal=路由级跳转到产物所在项目/画板/节点 | PASS（focus_node 参数 + focusRevealNode 选中居中；跳转格式与 project-list.js 一致） |
| 逐任务取消复用 controlProjectImageTask | PASS（同端点 POST cancel；测试覆盖取消后端点行为与行状态翻转） |
| 范围熔断 | 未触发——票面全量完成，无尾票 |

## 测试证据

- `tests/test_global_tasks.py`：8 passed（含在全量中）。
- 全量 pytest（origin/main 基线 30dc6fa + 本分支 5 commits）：**1552 passed, 6 failed, 1 skipped, 619 subtests**（716.91s）。6 个失败逐条归因：
  1. 3 个 cache-buster 测试（`test_canvas_assets_share_one_cache_buster` / `test_changed_frontend_files_get_a_fresh_cache_buster` / `test_canvas_assets_use_the_current_ui_cache_version`）：全量跑在 `e8b8501` 统一缓存戳**之前**（stale）；`e8b8501` 后复跑 3/3 passed，且 fast 门（含该不变量）passed。
  2. `test_reverse_reference_count_does_not_grow` + `test_total_matches_the_ratchet_ceilings`：**origin/main 基线失败**——`canvas-generation-tasks.js` 反向引用计数 103 > 天花板 101；用 origin/main 版本的 smart-canvas.js + canvas-generation-tasks.js 复算仍为 103，与本分支无关（main 上 #236 相关棘轮未随代码重测，建议 main 侧处理）。
  3. `test_units_canvas_generation_tasks`：node 单测直接运行**全绿但 real 3m0.251s**（轮询等待是真实计时），超 pytest 封装 120s 上限而 timeout；被测文件 `canvas-generation-tasks.js` 与 harness 均与 origin/main 字节一致 → 基线问题（建议调高该 harness 超时或压缩轮询等待）。
- 质量门：`FIREFLY_TEST_MODE=1 python tools/quality_gate.py fast` → **passed**（19.5s，e8b8501 后）；`domain` → **passed**（142.4s，含 javascript-syntax/surface-contract/native-browser-evidence；chrome profile 并发争用本轮未复现，一次通过）。
- 冒烟：uvicorn 起服务后 `global-settings.html` 含 section 06 + 新 JS 戳；`/api/global-tasks?status=active` 返回真实在飞任务（provider/model/耗时/项目名齐全）。

## 遗留观察（非本票范围，登记备查）

- **stale running 记录**：项目「H16加热毯」canvas.history 存有 2 个月前应用被杀时未落终态的 `status=running` 记录（含 runMs/completed_at）。总控台如实显示为 running 且 `cancellable=false`（内存无 live 句柄，正确）。清理/标记 stale 可另开小票。
- main 基线棘轮失败（103>101）与 node 单测 120s 上限：见上，属 origin/main 侧。

## 交付

- push：`origin issue/262`（5 commits，工作区干净）。
- assign：issue #262 assignee 已是 `miaomiaozii`（未改动）。
