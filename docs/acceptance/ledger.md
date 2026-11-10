# 验收台账

监理的每一次裁定在此留一行，供维护者抽查。监理不写台账，其裁定视为未完成。

追加方式：表末追加一行，不改历史行。`verdict_reasons` 填门禁输出原文；无则填 `none`。

| 日期（UTC） | Issue | 工作树 | 档位 | 退出码 | verdict_reasons | 打回轮次 | 结论 | 证据目录 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| _示例 2026-11-10_ | _#000_ | _D:/Firefly-Canvas_ | _trial_ | _0_ | _none_ | _1_ | _通过_ | _D:/Firefly-Canvas-evidence/000-…_ |
| 2026-11-10 | #310 | D:/ff-wt/w310 | 验收第二轮 | 0 | none | 0 | PASS_WITH_NOTES | D:/Firefly-Canvas-evidence/run_2026-11-10T09-10-15Z |

> 注（#310 行，监理自落）：裁定发生于流程层 v2/v3 期间（门禁为人工复跑，gate.json 的 verdict_reasons=[] 系字段缺失，已立 P-19）；落笔时生效面读数=v4@origin/main。维护者边界令行头里的 (v3) 为棘轮当时误标，v4 上架后更正。P-11 已裁「只推分支不合并不删分支」：origin/issue/310@16df89a 已在远端，合并归维护者。

> 补记（v7 措辞，监理）：merge-pending 16df89a（#310 分支已推 origin，合并归 user；v5 后「维护者」即 user）。
