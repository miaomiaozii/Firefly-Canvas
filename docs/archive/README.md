# docs/archive —— 归档区

放**已完成使命、但仍是历史记录**的文档：交接文档、阶段性 review / 测试报告、修完的 TODO。
归档不等于删除：内容原样保留，只是不再出现在 `docs/` 的活动文档列表里。

| 文件 | 是什么 | 为什么归档 |
| --- | --- | --- |
| `TODO-FIX.md` | 原仓库根目录的待修清单（T1–T9） | 条目已全部修完（#366 从根目录搬入） |
| `HANDOVER-20260802.md` | 2026-08-02 交接文档 | 交接已完成；其中引用的 `docs/clean-room-rewrite-plan.md` 仍在 `docs/` |
| `HANDOFF-20260822.md` | 2026-08-22 交接文档 | 同上 |
| `code-review-phase2.md` / `code-review-phase2-v2.md` | 第 2 阶段代码审查报告（v1 / v2） | v2 已取代 v1，两轮结论都已落到代码与 ADR |
| `fix-summary-phase2.md` | 第 2 阶段修复汇总 | 对应修复已合入 |
| `generation-flow-review.md` | 2026-08-31 生图流程专项审计 | 问题已由 #80/#81/#82 系列修复，结论见 `docs/adr/0017-restart-recovery-repolls-paid-tasks.md` |
| `test-report.md` | 早期测试报告 | 现由质量门与测试套件取代 |
| `issue197_recovery_audit.md` | issue #197 恢复审计 | 该 issue 已关闭 |

归档里的**代码行号是当时的快照**，多数已失效（`python tools/doc_link_check.py` 会把它们报成
line-out-of-range）——读这些文档时按历史材料对待，别照着行号跳。
