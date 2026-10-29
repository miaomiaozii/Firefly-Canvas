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
