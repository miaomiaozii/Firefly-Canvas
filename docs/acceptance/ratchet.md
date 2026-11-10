# 流程棘轮台账

只向前。一行一条，追加不修改。规则见 `.pi/skills/process-optimizer/SKILL.md`「棘轮规则」。

## 版本行

| 版本 | 日期 | 吸收的卡点 | 新增/收紧的可复跑回归 | 放宽了什么 |
|---|---|---|---|---|
| v1 | 2026-11-10 | P-01 … P-10（来自 #310 演练复盘，补丁由维护者亲自动手——当时还没有流程优化者会话） | 14 项 grep 标记（见下） | 无 |
| v2 | 2026-11-10 | 卡点队列改为流程优化者单写（与 P-04 同类风险，上岗前防）；P-11 入队；P-12/P-13 转 `code-issue` | 三个 skill 的 `version:` 字段 1→2（复跑：`grep -n "^version:" .pi/skills/*/SKILL.md` 须全为 2）；新增 grep 标记 `单写` | 无（只收紧） |

## v2 回归标记

| 标记 | 实际所在文件 |
|---|---|
| `version: 2` | 三个 skill |
| `单写` | `acceptance-supervisor/SKILL.md` + `impl-session-contract/SKILL.md` |

## v1 回归标记（复跑：`grep -rlE -e <标记> .pi/skills AGENTS.md`，缺任一项即棘轮破损；标记按正则匹配）

| 标记 | 实际所在文件 |
|---|---|
| `回执（` | 三个 skill |
| `只读不消费` | `impl-session-contract/SKILL.md` |
| `与基线证据逐条比对` | `impl-session-contract/SKILL.md` |
| `PARALLEL_WORKERS=1` | `acceptance-supervisor/SKILL.md` + `impl-session-contract/SKILL.md` |
| `必带来源` | `impl-session-contract/SKILL.md` |
| `json comments` | `impl-session-contract/SKILL.md` |
| `卡点上报` | 三个 skill + `AGENTS.md` |
| `双盲三查` | `acceptance-supervisor/SKILL.md` + `process-optimizer/SKILL.md` |
| `在途兄弟 Issue` | `acceptance-supervisor/SKILL.md` |
| `上岗前置` | `impl-session-contract/SKILL.md` + `process-optimizer/SKILL.md` |
| `^version:` | 三个 skill 各有 |
| `流程优化者` | 三个 skill + `AGENTS.md` |
| `台账例外` | `acceptance-supervisor/SKILL.md` |
| `已执行` | 三个 skill |

复跑脚本（一条命令看全）：

```bash
cd D:/Firefly-Canvas && for m in "回执（" "只读不消费" "与基线证据逐条比对" "PARALLEL_WORKERS=1" "必带来源" "json comments" "卡点上报" "双盲三查" "在途兄弟 Issue" "上岗前置" "^version:" "流程优化者" "台账例外" "已执行"; do printf '%-24s :: %s\n' "$m" "$(grep -rlE -- "$m" .pi/skills AGENTS.md | tr '\n' ' ')"; done
```

## 棘轮读数（每轮一组，坏指标须持平或下降）

| 轮次 | 日期 | 打回次数 | 停止条件触发 | 卡点数(复现/未复现) | 门禁已执行步骤数 | 双盲自检 | 可见性自检 | 新增回归 | 流程版本 |
|---|---|---|---|---|---|---|---|---|---|
| #310 演练 | 2026-11-10 | 2（监理两次要求更正） | 0 | 13 / 0 | 32 | 未做 | 未做（交付在 `pty:` 游离终端） | 14（v1 补丁） | v0→v1 |
| loop-1（优化者上岗） | — | 未演练 | — | — | — | — | — | — | v1 待跑 |
| loop-2（优化者上岗 v2，#310 轮间） | 2026-11-10 | 0（本轮未开新验收） | 0 | 5 / 1（P-15 P-17 P-18 P-19 pending；P-16 merged→P-15；P-14 unverified） | 未跑门禁（本轮不改代码） | 是（优化者≠监理≠交付：终端/工作区/Run 三条全独立；监理 tabId=cf524ca6-…、交付 tabId=816d78f2-…、我方 tabId=c0d9ff46-…） | 是（我方 tabId UUID、worktree current 有 path、terminal list 可按 handle 找回） | 0（只判定入队，未改契约） | v2（未改 skill，无版本递增） |

## 已核实的事实（带来源，供下轮做差）

- 三轮 trial 各 **32 步**，均无 `tabbit-e2e-evidence` 与 `egress-attribution`：`D:/Firefly-Canvas-evidence/{310-baseline-b2b3399-trial,310-delivery-branch-trial,310-supervisor-20261110-092301-trial}/report.json`（`python` 读 `checks[].name`，`final_exit_code=1`）。
- `docs/acceptance/ledger.md` 只有表头 + 示例行（#310 两条裁定至今未落台账）——**监理自己的写入面实际未执行**，v1 已将其列为停止条件。
- 基线与交付的 trial 红项完全同集（零新增红）；`scenario_keys.sh` 实测 3/3 必挂（不是飘）。
- 上述两条均无未发生的事：本轮只跑过监理 + 交付两个会话，**流程优化者会话尚未上岗**（无对应终端/工作树/Run）。
- （loop-2 实测）`D:/ff-wt/w310`（`issue/310`@`16df89a`）上流程契约为 `b2b3399` 时代旧版：`grep -n "^version:" .pi/skills/*/SKILL.md` exit 1、四标记 grep exit 1 → v1/v2 收紧对被验会话不可见（P-15）。根因：v1 补丁 `cd83733`/`1771944` 晚于 `issue/310` 基线 `b2b3399`。
- （loop-2 实测）三方身份与工作区：监理 `term_6d6c2fb9…`（`D:/Firefly-Canvas`，main@a4e458c，run_12b3ab55bf53）；交付方 `term_b300bceb…`（`D:/ff-wt/w310`，issue/310@16df89a，run_79684022c565）；维护者 `pi_4714c1cd_55fe_4d59_8b81_68ea12b8551b`（run_b203fe4d1bef，pi chat 内嵌会话，无 tabId）；流程优化者 `term_e7ba05f8…`（`C:/Users/Administrator/orca/workspaces/Firefly-Canvas/loop`，loop/ratchet@a4e458c，run_c40189b45d94）。
- （loop-2 实测）`send` 寻址：`--to run:12b3ab55bf53` 报 `run_not_found`；`--to run_12b3ab55bf53` 报 `terminal_not_found`；`--to run:run_12b3ab55bf53` 成功（P-18）。

## 待维护者定的事

- `16df89a`（#310 改动）未进 main 但 Issue 已关闭 → 要不要补合并（P-11）。
- 本机存量红与 `scenario_keys.sh` 3/3 必挂无人认领 → 门禁的组级 fail-fast 会永久遮住尾组（P-12、P-13）。
- 流程文件的合并权在维护者：优化会话的改动走 `loop/*` 分支，监理复核通过后由维护者合并。
