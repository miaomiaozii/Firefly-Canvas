# 流程棘轮台账

只向前。一行一条，追加不修改。规则见 `.pi/skills/process-optimizer/SKILL.md`「棘轮规则」。

## 版本行

| 版本 | 日期 | 吸收的卡点 | 新增/收紧的可复跑回归 | 放宽了什么 |
|---|---|---|---|---|
| v1 | 2026-11-10 | P-01 … P-10（来自 #310 演练复盘，补丁由维护者亲自动手——当时还没有流程优化者会话） | 14 项 grep 标记（见下） | 无 |
| v2 | 2026-11-10 | 卡点队列改为流程优化者单写（与 P-04 同类风险，上岗前防）；P-11 入队；P-12/P-13 转 `code-issue` | 三个 skill 的 `version:` 字段 1→2（复跑：`grep -n "^version:" .pi/skills/*/SKILL.md` 须全为 2）；新增 grep 标记 `单写` | 无（只收紧） |

| v3 | 2026-11-10 | P-15、P-17、P-18、P-19（维护者 12:2x 放行清单）；P-16 保持 merged→P-15；P-20 当场修矛盾并入 P-15/P-19 口径；P-05 追加「已完成运行」口径 | 三个 skill `version:` 2→3；新增 grep 标记 `流程契约取法`、`即停`、`收尾归属固定句`、`run-current` 回读非 null、`已完成运行`、`流程层必须推到 origin`；`origin/main` 可读性判据写进三份契约 | 无（只收紧：P-20 是把交付契约里与「单写」矛盾的「自己往 pain.md 追一行」改成单写口径） |

## v3 回归标记（复跑：`grep -rlE -e <标记> .pi/skills`，缺任一项即棘轮破损）

| 标记 | 实际所在文件 |
|---|---|
| `version: 3` | 三个 skill |
| `流程契约取法` | 三个 skill |
| `即停` | 三个 skill |
| `收尾归属固定句` | `acceptance-supervisor/SKILL.md` |
| `回读非 null` | `acceptance-supervisor/SKILL.md` + `impl-session-contract/SKILL.md` |
| `已完成运行` | `impl-session-contract/SKILL.md` |
| `流程层必须推到 origin` | `process-optimizer/SKILL.md` |
| `git show origin/main:` | 三个 skill |
| `不得用 `git merge main`` | `acceptance-supervisor/SKILL.md` + `impl-session-contract/SKILL.md` |
| `--help 核对`（v3 校准追加） | `impl-session-contract/SKILL.md` |
| `归因带原始出处`（v3 追加，单写者自约束） | `process-optimizer/SKILL.md` |
| `取证含会话JSONL`、`toolCall arguments`（v3 追加，P-23） | `process-optimizer/SKILL.md` |
| `适用面与失效轮次`（v3 追加，P-24） | `process-optimizer/SKILL.md` |
| `台账例外声明`（v3 追加，P-24） | `acceptance-supervisor/SKILL.md` + `impl-session-contract/SKILL.md` |
| `口径表`、`认人认编号`、`认人编号`、`班规@origin`（v3 追加，P-25） | 三份 skill + `docs/acceptance/roster.md` |
| `run-current自上下文`、`gh主工作区跑`（v3 追加，P-26/P-27） | `docs/acceptance/roster.md` §3.3/§3.5 |
| `版本宣布带证据`（v3 追加，P-28）、`check截断用inbox--full`（v3 追加） | `docs/acceptance/roster.md` §3.7/§3.8 |
| `旁证不定主体`（v3 追加） | `process-optimizer/SKILL.md` |

## v2 回归标记

| 标记 | 实际所在文件 |
|---|---|
| `version: 2` | 三个 skill |
| `单写` | 三个 skill（v3 补齐 `process-optimizer/SKILL.md` 的 `PAIN_QUEUE` 行） |

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
cd D:/Firefly-Canvas && for m in "version: 3" "流程契约取法" "即停" "收尾归属固定句" "回读非 null" "已完成运行" "流程层必须推到 origin" "git show origin/main:"; do printf '%-30s :: %s\n' "$m" "$(grep -rlE -- "$m" .pi/skills | tr '\n' ' ')"; done
# v1+v2 的 15 项标记（缺一即破损）：
cd D:/Firefly-Canvas && for m in "回执（" "只读不消费" "与基线证据逐条比对" "PARALLEL_WORKERS=1" "必带来源" "json comments" "卡点上报" "双盲三查" "在途兄弟 Issue" "上岗前置" "^version:" "流程优化者" "台账例外" "已执行"; do printf '%-24s :: %s\n' "$m" "$(grep -rlE -- "$m" .pi/skills AGENTS.md | tr '\n' ' ')"; done
```

## 棘轮读数（每轮一组，坏指标须持平或下降）

| 轮次 | 日期 | 打回次数 | 停止条件触发 | 卡点数(复现/未复现) | 门禁已执行步骤数 | 双盲自检 | 可见性自检 | 新增回归 | 流程版本 |
|---|---|---|---|---|---|---|---|---|---|
| #310 演练 | 2026-11-10 | 2（监理两次要求更正） | 0 | 13 / 0 | 32 | 未做 | 未做（交付在 `pty:` 游离终端） | 14（v1 补丁） | v0→v1 |
| loop-1（优化者上岗） | — | 未演练 | — | — | — | — | — | — | v1 待跑 |
| loop-2（优化者上岗 v2，#310 轮间） | 2026-11-10 | 0（本轮未开新验收） | 0 | 5 / 1（P-15 P-17 P-18 P-19 pending；P-16 merged→P-15；P-14 unverified） | 未跑门禁（本轮不改代码） | 是（优化者≠监理≠交付：终端/工作区/Run 三条全独立；监理 tabId=cf524ca6-…、交付 tabId=816d78f2-…、我方 tabId=c0d9ff46-…） | 是（我方 tabId UUID、worktree current 有 path、terminal list 可按 handle 找回） | 0（只判定入队，未改契约） | v2（未改 skill，无版本递增） |
| loop-3（v3 吸收轮，维护者放行 P-15/17/18/19） | 2026-11-10 | 0（未开验收；单写者自报更正 1 起见 P-22，不计打回） | 0（未触发；P-14 诱饵拦下，未吸收） | 4 吸收 + 1 当场修（P-20）+ 1 merged→P-05（P-21）；P-16 仍 merged→P-15；P-14 仍 unverified | 未跑门禁（本轮只改流程层） | 是（三方终端/工作区/Run 仍全独立；本侧 `git status` 改动仅 `.pi/skills/**` 与 `docs/acceptance/**`） | 是（同 loop-2 读数） | 9 项 v3 grep 标记 + 复跑脚本已扩（v1+v2 15 项 + v3 8 项） | v2→v3（三份 skill `version: 3`） |

## 已核实的事实（带来源，供下轮做差）

- 三轮 trial 各 **32 步**，均无 `tabbit-e2e-evidence` 与 `egress-attribution`：`D:/Firefly-Canvas-evidence/{310-baseline-b2b3399-trial,310-delivery-branch-trial,310-supervisor-20261110-092301-trial}/report.json`（`python` 读 `checks[].name`，`final_exit_code=1`）。
- `docs/acceptance/ledger.md` 只有表头 + 示例行（#310 两条裁定至今未落台账）——**监理自己的写入面实际未执行**，v1 已将其列为停止条件。
- 基线与交付的 trial 红项完全同集（零新增红）；`scenario_keys.sh` 实测 3/3 必挂（不是飘）。
- 上述两条均无未发生的事：本轮只跑过监理 + 交付两个会话，**流程优化者会话尚未上岗**（无对应终端/工作树/Run）。
- （loop-2 实测）`D:/ff-wt/w310`（`issue/310`@`16df89a`）上流程契约为 `b2b3399` 时代旧版：`grep -n "^version:" .pi/skills/*/SKILL.md` exit 1、四标记 grep exit 1 → v1/v2 收紧对被验会话不可见（P-15）。根因：v1 补丁 `cd83733`/`1771944` 晚于 `issue/310` 基线 `b2b3399`。
- （loop-2 实测）三方身份与工作区：监理 `term_6d6c2fb9…`（`D:/Firefly-Canvas`，main@a4e458c，run_12b3ab55bf53）；交付方 `term_b300bceb…`（`D:/ff-wt/w310`，issue/310@16df89a，run_79684022c565）；维护者 `pi_4714c1cd_55fe_4d59_8b81_68ea12b8551b`（run_b203fe4d1bef，pi chat 内嵌会话，无 tabId）；流程优化者 `term_e7ba05f8…`（`C:/Users/Administrator/orca/workspaces/Firefly-Canvas/loop`，loop/ratchet@a4e458c，run_c40189b45d94）。
- （loop-2 实测）`send` 寻址：`--to run:12b3ab55bf53` 报 `run_not_found`；`--to run_12b3ab55bf53` 报 `terminal_not_found`；`--to run:run_12b3ab55bf53` 成功（P-18）。
- （loop-3 自查实测）交付契约上岗前置第 4 项原文写「同时给 `docs/acceptance/pain.md` 追一行（状态 `pending`）」，与监理契约「队列只由流程优化者单写」矛盾（P-20，已 v3 修平：`单写` 现三个 skill 全含）。
- （loop-2 监理自报，msg_1061adfdfedf）台账行未落地的归因：本轮演练硬约束「不得修改/创建任何文件（仅 /tmp 可写）」由维护者设定且未解除，监理称台账行文本已两次交付（状态邮件正文 + 监理会话报告），等维护者代落或授权；监理承诺下一轮上岗第一件事补行。台账现仍只有表头 + 示例行 —— 事实入册，归因待维护者确认（不记成监理单方执行缺口）。
- （v3 实测，`gh issue close --help` 于 `D:/Firefly-Canvas`）`gh issue close` 支持 `-c/--comment string`、`-r/--reason`、`--duplicate-of`，**无** `-F/--body-file`；`gh issue comment` 支持 `-b/--body`、`-F/--body-file`、`--edit-last/--delete-last`。`docs/agents/issue-tracker.md:26` 的 `gh issue close … --comment "..."` 有效。P-06 原「没有 `--comment-file`」表述属实但易误读，已按「自造 flag 前先 `--help` 核对」改写（P-05 校准注记落在此行）。
- （loop-3 归因取证，P-06/P-22）`--comment-file` 失败的**行为主体无法核实，取证为空**：`gh api repos/miaomiaozii/Firefly-Canvas/issues/310/comments --jq '.[]|select(.body|test("comment-file";"i"))'` → 0 命中；`grep -rIl -- "--comment-file" D:/Firefly-Canvas-evidence D:/ff-wt D:/Firefly-Canvas/docs` → 只命中 `docs/acceptance/pain.md` 与 `ratchet.md`（我们自己的台账）；`gh api repos/miaomiaozii/Firefly-Canvas/issues/310/events` 的 closed 事件 → `actor=miaomiaozii, created_at=2026-11-10T09:33:34Z`（三方共用同一 bot 账号，无法定主体）；`git show cd83733:docs/acceptance/pain.md` 的 P-06 原文未记主体。**台账只写「主体未核实」，不指名任何一方**；我此前那句「监理自造」已删（立 P-22，标记 `归因带原始出处`）。
- （loop-3 旁证登记，P-06）监理 msg_0b6dad4f97dc 递来：**自报级**证据——交付方 msg_e489f891f126（from=term_b300bceb，09:33 后送达）原文「已按契约收尾：结论评论 issuecomment-6763910725 + Issue #310 关闭（reason=completed）」，时间吐合 closed 事件 `2026-11-10T09:33:34Z`。等级不够：自报 + 三方共用 bot 账号 ⇒ 不能定主体，P-06 维持「主体未核实」，本条只作旁证（已写成契约口径 `旁证不定主体`）。交付方另确认 pain.md 零写入（msg_b46d784c771c）。
- （loop-3 取证翻案，P-06）主体由「未核实」改为**交付会话**：`--D--ff-wt-w310--/…_01a1c475-6aaa-….jsonl` L220 ts=09:33:20.303Z toolCall(bash) `arguments.command` = `gh issue close 310 … --comment-file "…close-comment-310.md" …`；L222 ts=09:33:29.958Z thinking 「`--comment-file` might not be a valid flag…」。监理会话 JSONL 的 toolCall `arguments` 命中 = 0（行 160/161/163/166/176/177 全为邮件正文文本，ts 10:56–11:16）——按原始行 grep 会把读邮件误算成敲命令。维护者引的 222 行是 thinking，命令文本在 220 行。
- （loop-3 台账裁定）台账边界归监理：「不得创建/修改文件」属临时口头约束，不覆盖监理自有写入面；豁免条款早在 `12cf55a` 就成文（监理契约「台账行不可豁免」），本轮未落属监理未按成文执行；#310 两行由监理下轮上岗第一件事自落，维护者不代写。
- （维护者 msg_db4b3380d294 执行面）`git merge --ff-only loop/ratchet` 已在维护者侧把本地 main 推到 `a6dfa39`，三份 skill 现 `version: 3`；其独立复跑 v3 的 8 项标记命中一致，v1+v2 的 15 项记我方口径。**`origin/main` 仍未推**（向公共仓发布需维护者本人点头）。

## 待维护者定的事

- `16df89a`（#310 改动）未进 main 但 Issue 已关闭 → 要不要补合并（P-11）。
- 本机存量红与 `scenario_keys.sh` 3/3 必挂无人认领 → 门禁的组级 fail-fast 会永久遮住尾组（P-12、P-13）。
- 流程文件的合并权在维护者：优化会话的改动走 `loop/*` 分支，监理复核通过后由维护者合并。
- **v3 合并状态**：本地 main 已 ff 到 `a6dfa39`（维护者执行）；`origin/main` 未推（发布需维护者点头）。
- **台账硬约束边界：已裁定（维护者 msg_bfff2cd265f6）**——临时口头约束止于该轮代码与产物，不覆盖监理台账写入面；#310 两行由监理下轮上岗第一件事自落，维护者不代写。已入契约（`台账例外声明`、`适用面与失效轮次`）。
- **`git push origin main` 需维护者（人）批准**——已列为待办。推上去之前，下一轮三方按 `流程契约取法` 触发「即停」是预期行为，**不是新卡点，不得重复立项**（维护者 msg_db4b3380d294 明示）。
- **下一轮用哪个 Issue：留空待维护者指定**，流程优化者与另两方不得自行领活。
- #310 台账两行是否由监理自落（其称硬约束未解除）或维护者代落（P-04 面）。
- P-14（诱饵，unverified）：如要覆盖「交完验收后的等待」，须先补证据（命令原文 + 时间戳/日志）再重设计改法，不得采用「轮询 `check`」。
