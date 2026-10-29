# issue-236 结果报告（即梦旧模型规格退役与路由收口 · 第四波收口）

工作区：`D:/ff-wt/wt-236`，分支 `issue/236`（基于 `20b2259` = wave1-live）。
**已 push origin issue/236；已指派 miaomiaozii；未合并、未关票**（留总控集成）。

## 四波接管链

| 波 | 时箱 | 结果 |
|---|---|---|
| 1-3 | 各 ~140m | 均死于时箱，但工作已落盘：7 个主题提交完成**源码层**退役（778271a 删 CLI 脚本+api_jimeng 路由 / 6e0d811 core 常量协议 WSL / 983ac69 model_specs / d18a238 services 层 / 714a82a routers / 4348427 main.py 路由注册 / 0e969e3 VIDEO_CLI_PROTOCOLS 清零+运行时键），前端/测试/工具清理与存量迁移未动 |
| 4（本次） | 140m | 接管收口：存量迁移实现+测试、前端 11 文件清理、CLI/tools/README 删除、测试面 14 文件清理、棘轮收紧+ADR 漂移日志、修复前波误伤（misc.py 常量）、全量测试+双门全绿、push |

## 提交（20b2259..835823f，共 12 个）

前波 7 个（见上）+ 本次 5 个：

1. `2e676f6` 前端移除即梦入口/模型过滤/pending 态与排队 UI + 删 CLI 安装/登录脚本
   （static/js+css+i18n 9 文件；jimeng-pending-* 样式类改名通用 task-pending-*，保留既有取回查询 UI；删 CLI/windows/jimeng/*.bat 与 tools/jimeng_cli_*.ps1）
2. `78edcd8` 存量 jimengPending 节点打开时一次性终态化（幂等迁移+测试）
   （`firefly_projects.ProjectStore._migrate_retired_jimeng_nodes`：写 failedGenerationTasks 带 submit_id、清忙态、补 runFinishedAt、变更即 flush；二次打开幂等；**canvas history 记录不做强制删除**，保留可追溯性；tests/test_jimeng_retirement_migration.py 4 用例）
3. `de4e9c5` 耦合棘轮收紧至 242/959/170/805 + ADR 0020 漂移日志
   （generation-tasks 116→101 / cascade 153→152 / image-editor 89→87；四口径 -5/-8/-4/-7 纯还债）
4. `adb9fb3` 测试面随退役清理（路由/契约/全局设置/浏览器 helper/mjs 单测，14 文件）
5. `835823f` tools 面去即梦（quality_gate/audit/README）+ 恢复 d18a238 误删的 #212 常量

## 关键决策与甄别

- **存量迁移挂钩点**：`open_project` 装载路径（`firefly_projects.py:1235` 附近，`_load_canvas_with_recovery` 之后）——任何打开动作（含 API/CLI/导出前）都会终态化；不改变更路径，避免双写。
- **历史保留语义**：票面"存量迁移不可省略"只针对**节点运行时态**（jimengPending 僵尸态）；canvas history 记录与已落盘生成结果原样保留，测试显式断言 history 不被删。
- **验收"全仓 grep 无 jimeng 残留"按功能性引用执行**：现存引用均为刻意保留——`app/core.py:157 REMOVED_PROVIDER_IDS` 含 'jimeng'（存量配置清除机制，与 modelscope/runninghub 同机制）、`firefly_projects.py` 迁移读遗留键+退役文案、firefly_generation.py:28 说明注释、测试中的退役行为断言、docs/ 历史文档。
- **修复前波误伤**：`d18a238` 批量删即梦函数时误删 `app/services/misc.py` 的 `DEFAULT_LIBRARY_NAME` / `DEFAULT_LIBRARY_LEGACY_NAMES`（#212 默认库命名迁移表，与即梦无关），导致 test_asset_library_naming 3 例 NameError——已恢复定义（835823f）。
- **model_specs 回归**：983ac69 删即梦 CLI ID（seedance2.0 等 4 条）后，tools/test_model_specs_service.py 的 vision 断言失配——断言改指仍存活的统一格式 spec ID `doubao-seedance-2-0-260128`（模型本体经统一格式/火山通道仍在产品内）。
- **视频路由行为变化**：`VIDEO_CLI_PROTOCOLS` 清零后，手动固定 jimeng 协议按"协议不可执行"短路 400（`incompatible_parameters={"protocol"}`），端点层提前返回，不走完整能力矩阵；测试已按实测语义改写。
- **`/api/canvas-video` 测试补丁点**：`main.generate_jimeng_video` 已不存在，改为补丁 router 模块 `app.routers.api_canvas_video.execute_canvas_video`（路由函数经模块全局解析，补丁有效）。

## 验证

| 项 | 结果 |
|---|---|
| `python -m pytest -q -p no:cacheprovider --ignore=tests/e2e` | **1549 passed, 1 skipped, 0 failed**（544.6s） |
| 基线参照（任务书给 wave1-live） | 1594 passed / 10 failed（环境性）；本分支 0 failed，用例数差 = 即梦测试删除（含 mjs node:test 用例）；基线 10 环境性失败本轮未复现 |
| mjs 单测（node --test 逐文件，23 文件） | 全绿（含改写的 generation-tasks 26 例 / image-editor 35 例 / cascade 48 例） |
| `python tools/quality_gate.py fast` | passed，16.25s |
| `python tools/quality_gate.py domain` | passed，138.58s（首过，未触发 chrome 并发重试） |
| 禁区核查 | app/agent_tools.py、agent_host*、static/js/agent/* 零触碰（git diff 20b2259..HEAD 无这些路径） |

## 遗留 / 建议

- 总控集成时注意：本分支棘轮钉值 242/959/170/805 低于 wave1-live 现值 247/967/174/812（纯降），合并方向无冲突预期；若期间 wave1-live 棘轮又动，需对撞 ADR 0020 漂移日志。
- 即梦存量服务商配置由 `REMOVED_PROVIDER_IDS` 在加载时清除（既有机制），无需额外迁移。
