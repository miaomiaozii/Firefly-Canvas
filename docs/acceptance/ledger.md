# 验收台账

监理的每一次裁定在此留一行，供 user 抽查。监理不写台账，其裁定视为未完成。

追加方式：表末追加一行，不改历史行。`verdict_reasons` 填门禁输出原文；无则填 `none`。

## 身份登记（`认人编号`；监理单写，上岗时更新，退役标「已退役」不删行）

| 角色 | 终端 handle | Run | 工作树 path | 状态 |
| --- | --- | --- | --- | --- |
| 工人 | `（待登记）` | `（待登记）` | `（待登记）` | — |
| 监理 | `term_3aaf3449` | `run_99a68361aed4` | `C:/Users/Administrator/orca/workspaces/Firefly-Canvas/rv307` | 上岗记录来自旧名册 §2（名册已废），实测在列前只作历史 |

> 旧名册 `docs/acceptance/roster.md` 已于 2026-11-12 废除，本节是替代它的唯一登记表。

| 日期（UTC） | Issue | 工作树 | 档位 | 退出码 | verdict_reasons | 打回轮次 | 结论 | 证据目录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| _示例 2026-11-10_ | _#000_ | _D:/Firefly-Canvas_ | _trial_ | _0_ | _none_ | _1_ | _通过_ | _D:/Firefly-Canvas-evidence/000-…_ |
| 2026-11-10 | #310 | D:/ff-wt/w310 | 验收第二轮 | 0 | none | 0 | PASS_WITH_NOTES | D:/Firefly-Canvas-evidence/run_2026-11-10T09-10-15Z |
| 2026-11-11 | #307 | C:/Users/Administrator/orca/workspaces/Firefly-Canvas/rv307（流程A：本次未验交付物） | trial（本轮拟用） | n/a（流程A 无门禁运行） | none | 0 | 可实施（ready-for-agent（可由智能体处理））；同场 #306 与 ADR 0004 冲突已评注 | 无（流程A 无门禁证据；上岗自检证据见 msg_49d832d7f3c9） |

> 注（#310 行，监理自落）：裁定发生于流程层 v2/v3 期间（门禁为人工复跑，gate.json 的 verdict_reasons=[] 系字段缺失，已立 P-19）；落笔时生效面读数=v4@origin/main。user 边界令行头里的 (v3) 为棘轮当时误标，v4 上架后更正。P-11 已裁「只推分支不合并不删分支」：origin/issue/310@16df89a 已在远端，合并归 user。

> 补记（v7 措辞，监理）：merge-pending 16df89a（#310 分支已推 origin，合并归 user；v5 后「维护者」即 user）。
> 注（#307 行，监理 term_3aaf3449 / run_99a68361aed4）：本行为流程 A（审 Issue）裁定留痕，非流程 B 验收，故退出码记 n/a、无门禁证据目录。上岗前置四条 + 种子逐字比对（MATCH_VERBATIM=True）+ 版本门五行 version: 13 @origin/main=39658b2（门 sha 2066823）已当场复跑，九项回执见 msg_49d832d7f3c9。流程 B 尚未开始：任务书以工人在线回执为前置（监理契约「握手」第 3 步），已发 probe msg_7d97ce694630。台账称呼同径：头部无「维护者」称呼，历史行内引文按「不改历史行」保留。

> 注（2026-11-12）：上面历史行里的 `P-xx`、「流程层 vN」读数、`version: 13` 门读数、「棘轮」指已废除的卡点队列与流程优化者角色，原文见 `git log --all -- docs/acceptance/`；只作历史，不当活口径。
