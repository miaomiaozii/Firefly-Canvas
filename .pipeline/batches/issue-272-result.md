# Issue #272 收口报告（三波接力）

## 接力链
- 首波（del_mvt2wf9j_cmym，150m 烧穿）：产出 b11343b anno_* 租户+CRUD、bc5219d 前端批注层、836399e undo 接线、6bc620f 模块单测 + 脏棘轮登记；死时在做 pytest 对照。
- 接管波（del_mvt89siv_cs3h）：thinking-only 猝死，零动作（已登记 worker 稳定性问题）。
- 总控收口（本）：固化棘轮登记脏文件、修对照红点、全门过、推送关票。

## 交付
- ops.db anno_* 租户 + 批注 CRUD 端点（b11343b）
- canvas-annotations.js 独立模块 + CSS + i18n；仅依赖 canvas-core(tr/escapeHtml) 与 canvas-undo(pushUndo/undoStack)（bc5219d）
- 批注进 undo 快照：加/删/改可撤销（836399e）
- 模块单测：锚定/刷新恢复/撤销回滚（6bc620f）
- 棘轮登记：canvas-annotations.js 钉 3（字符串字面量误计口径）+ 反向耦合四口径 242/959/170/805 → 244/961/171/806（+2/+2/+1/+1，批注层首次以非核心身份引用 core 名家族，与 #253 quick-edit +2 同模式）

## 门与测试
- fast 16.6s / domain 155.4s：/tmp/gate-272-fast / /tmp/gate-272-domain
- 全量 pytest（去 bun 环境项）：1560 passed, 0 failed, 619 subtests

## 尾票登记
- 批注气泡列表美化/多选批注未做（票面无硬项，范围熔断外）
- ADR 0020 漂移日志行随总控并入 wave1-live（main 无 ADR 0020 文件，测试内注释已自带理由）

## 接管验证波（2026-10-29 ~17:10，独立复核记录）

用户派工接管（前波 150m 烧穿 + 接管波 thinking-only 零动作均已登记于上表）。本波未新增产品代码，逐项独立复核并补齐证据链：

- **RED 复现与归因**：死时日志「1 failed」（/tmp/pytest-mine-272.log）= 棘轮测试族，根因 = `canvas-annotations.js` 未登记进 `REVERSE_REFERENCE_CEILINGS`/`FREE_GLOBAL_REFERENCE_CEILINGS`（实测 3 处全是 `getElementById('world')` 字符串字面量误计，真实自由全局耦合 0）。棘轮登记后 HEAD 复跑两棘轮文件 20 passed（31.75s）。
- **验收对照（票面硬项全在）**：迁移 `firefly_opsdb.py:93`（anno_001，ops_migrations 机制）；CRUD `app/routers/api_project_annotations.py`（GET/POST/PATCH/DELETE，`main.py:1263-1264` 挂载，`firefly_projects.py:202` ProjectStore 混入）；锚定 node_id+归一化 x/y（`static/js/canvas-annotations.js`）；**刷新恢复** `smart-canvas.js:6523` `restorePersistedAnnotations()`（#251 同模式）；**undo** `canvas-undo.js:45/96/145` 快照含 annotations + 模块内 pushUndo/失败回滚。区域级圈注锚定按 #259 维护者已批口径留 runAll 票（本票只预留 status）。
- **全量 pytest 对照 origin/main 基线**（/tmp/pytest-full-mine-272c.log vs /tmp/pytest-full-base-272b.log）：本分支 1567 passed / 1 failed / 1 skipped；基线（58ebce0=origin/main~1，与 origin/main 仅差 1 个 pipeline md）1551 passed / 3 failed。基线 2 个棘轮红 = generation-tasks 101→103 漂移（本分支已合法登记修复）；本分支新增 14 个测试（test_project_annotations.py + test-canvas-annotations.mjs）；剩余唯一红 `test_units_canvas_generation_tasks` = node mjs 120s 超时，**origin/main 树同样复现**（/tmp/gt-base-single.log、/tmp/node-direct-272.log 卡死在 pollSmartCanvasTask 段后）→ main 线存量环境项（总控所称「去 bun 环境项」），非本票回归。
- **门**：fast/domain（16:03/16:30）与 integration/integration-domain（16:46/16:48，最终 commit 3146f38 之后）四份 report.json 全 passed/validated（/tmp/gate-272-*）。
- **收口状态**：origin/issue/272 @ db16270 已推；issue 272 CLOSED；assignee=miaomiaozii。
- 备注：任务书所指「wave2-dispatch-20261028.md 底部环境性失败清单」经核不存在（该文件 47 行无此节），以基线实测对照替代。
