# 流程棘轮台账

只向前。一行一条，追加不修改。规则见 `.pi/skills/process-optimizer/SKILL.md`「棘轮规则」。

## 版本行

| 版本 | 日期 | 吸收的卡点 | 新增/收紧的可复跑回归 | 放宽了什么 |
|---|---|---|---|---|
| v1 | 2026-11-10 | P-01 P-02 P-03 P-04 P-05 P-06 P-07 P-08 P-09 P-10 | 12 项 grep 标记（见下） | 无 |

## v1 回归标记（复跑：`grep -n -F -e <标记> .pi/skills/*/SKILL.md`，缺任一项即棘轮破损）

| 标记 | 所在文件 |
|---|---|
| `回执（` | `.pi/skills/impl-session-contract/SKILL.md` |
| `只读不消费` | `.pi/skills/acceptance-supervisor/SKILL.md` + `.pi/skills/impl-session-contract/SKILL.md` |
| `与基线证据逐条比对` | `.pi/skills/acceptance-supervisor/SKILL.md` |
| `PARALLEL_WORKERS=1` | `.pi/skills/acceptance-supervisor/SKILL.md` + `.pi/skills/impl-session-contract/SKILL.md` |
| `必带来源` | `.pi/skills/acceptance-supervisor/SKILL.md` + `.pi/skills/impl-session-contract/SKILL.md` |
| `--json comments` | `.pi/skills/impl-session-contract/SKILL.md` |
| `卡点上报` | `.pi/skills/acceptance-supervisor/SKILL.md` + `.pi/skills/impl-session-contract/SKILL.md` |
| `双盲三查` | `.pi/skills/acceptance-supervisor/SKILL.md` |
| `在途兄弟 Issue` | `.pi/skills/acceptance-supervisor/SKILL.md` |
| `上岗前置` | `.pi/skills/impl-session-contract/SKILL.md` |
| `^version:` | 三个 skill 文件各有 |
| `流程优化者` | `AGENTS.md` |

## 棘轮读数（每轮一组，坏指标须持平或下降）

| 轮次 | 日期 | 打回次数 | 停止条件触发 | 卡点数(复现/未复现) | 门禁已执行步骤数 | 双盲自检 | 可见性自检 | 新增回归 | 流程版本 |
|---|---|---|---|---|---|---|---|---|---|
| #310 演练 | 2026-11-10 | 2 | 0 | 13 / 0 | 32 | 未做 | 未做 | 12（v1 补丁） | v0→v1 |
| loop-1 演练 | 2026-11-10 | 1（监理打回优化会话） | 2（缺回执 / 台账不可写） | 5 入队：2 吸收 1 转交 1 pending 1 未复现 | 31（fast，监理独立复跑） | 是（监理侧已自检通过） | 是（两边 tabId 为 UUID） | 待合并 | v1 |

## 待维护者定的事

- `16df89a`（#310 改动）未进 main 但 Issue 已关闭 → 要不要补合并（P-11）。
- 本机存量红与 `scenario_keys.sh` 3/3 必挂无人认领 → 门禁的组级 fail-fast 会永久遮住尾组（P-12、P-13）。
- 流程文件的合并权在维护者：优化会话的改动走 `loop/*` 分支，监理复核通过后由维护者合并。
