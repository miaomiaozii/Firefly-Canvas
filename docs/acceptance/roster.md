# 流程层口径表与身份登记册（ROSTER）

唯一名册 + 唯一口径表。**单写者：流程优化者（棘轮）**（`单写`）。三方不得写本文件；有异议发邮件给我，由我改。
本文件是三方的称呼、编号、文件指代、版本基准的**唯一真相源**。任何邮件、台账、Issue 评论引用角色或文件，一律按本表写法。

## 1. 口径表（先统一这个，再谈别的）

### 1.1 角色名（规范名 / 唯一别名）

| 规范名（正文只用这个） | 唯一别名 | 职责边界（一句话） | 不得做 |
|---|---|---|---|
| 流程优化者 | 棘轮 | 收卡点、判定入队、把教训固化成可复跑的检查或工具 | 不做验收判定、不改产品代码与测试、不落 triage 标签、不关 Issue、不推 main |
| 监理 | `term_6d6c2fb9-4ac0-4fca-a866-be67febbe86e` | `run_12b3ab55bf53`（verified：对端自证 `run-current` + `run-show --id run_12b3ab55bf53 --full` 的 `coordinator_handle=term_6d6c2fb9…` 持久互证，msg_8602b4bdebcf） | `cf524ca6-5cdf-4f7c-8144-baf8c496d202`（UUID） | `D:/Firefly-Canvas` @ `main` @ `a0a8615`（回执读数；本地 main 会被他会话推进） | 从 `班规@origin` 取：文件存在但 **无 version 行**（`grep -n "^version:"` rc=1）——与棘轮自测一致，见 §3.2 | `docs/acceptance/ledger.md`；GitHub Issue 评论（裁定/记录文书）；证据目录 `D:/Firefly-Canvas-evidence/**`；`/tmp/**` | 产品代码与测试（`src/**`、`tests/**`、`tools/**`）、`.pi/skills/**`、`docs/acceptance/{pain,ratchet,roster}.md`、merge/push `main`、改被验工作树 git 状态、triage 标签以外的 Issue 状态关闭、代交付方写实现 | 回执 `msg_66beb330eb06`，2026-11-10，九项+A/B 齐 |
| 交付方 | `term_b300bceb-cd82-4771-95c8-bb53ba836bca`（terminal list 标题「π - w310」互证） | `run_79684022c565`（verified：`run-show --id run_79684022c565 --full --json` → `coordinator_handle=term_b300bceb…`，msg_9afaeecb83d4） | `816d78f2-ebdc-4fe7-b6e4-42da96fbf03e`（UUID） | `D:/ff-wt/w310` @ `issue/310` @ `16df89a` | 从 `班规@origin` 取：**无 version 行**；对照 `班规@棘轮`（`git show origin/loop/ratchet:.pi/skills/impl-session-contract/SKILL.md`）→ `3:version: 3` | 票面白名单文件（#310：`tests/tabbit_e2e_evidence.py`、`tests/test_quality_gate.py`）；`git commit`（仅 `issue/310`）；Issue 收尾序列评论；证据目录 `D:/Firefly-Canvas-evidence/*`；编排邮件 | 一票否决面（`tools/quality_gate.py`、`tools/e2e/**`、`.github/**`、`package*.json`、白名单外 `tests/**`）碰=先停等裁定；`.pi/skills/**`；`docs/acceptance/**`；merge/rebase/push `main`；删 `issue/310`；建 PR；triage 标签；验收判定 | 回执 `msg_e9194575efd3`/`msg_94832d53d48f`/`msg_905055031248`（三段），2026-11-10 |
| 维护者 | 无别名 | 发任务书、裁定口径解释权、合并与发布 `main` | 不代写监理台账 |

不得用的称呼：「优化会话」「监理会话」「交付会话」「那位」「你们这边」——这些词在归因时产生歧义（实测 P-06 归因两轮错记即源于此）。**认人只认 `HANDLE` 与 `SELF_RUN` 编号（`认人认编号`），不认称呼。**

### 1.2 编号与 ID 的规范写法

| 对象 | 规范写法 | 常见错误写法（实测会失败或歧义） |
|---|---|---|
| Orca 终端 | `term_<uuid>`（handle） | `orca terminal rename` 的标题会被 pi 覆盖，不可用作身份 |
| Orca Run | `run_<12位hex>` | — |
| 发件寻址 | `--to run:run_<12位hex>` | `--to run:<hex 不带 run_>` → `run_not_found`；`--to run_<hex>` → `terminal_not_found` |
| 会话逐字记录 | `C:/Users/Administrator/.pi/agent/sessions/<cwd-slug>/<ts>_<uuid>.jsonl` | 按原始行 grep 会把「读邮件」误算成「敲命令」；只查 `toolCall` 的 `arguments` |
| Issue 评论 | `issuecomment-<databaseId>`，引用前先 `gh issue view --json comments` | 凭记忆写 ID |
| 提交 | `<7位sha>`，并写明所在分支（`main` / `loop/ratchet` / `issue/<N>`） | 只写 sha 不写分支（本地 main 与 origin/main 曾差 4 颗） |

### 1.3 文件的唯一指代（同名文件的三份副本必须区分）

| 代号 | 绝对路径 | 谁读它 | 状态 |
|---|---|---|---|
| `班规@origin` | `origin/main:.pi/skills/{acceptance-supervisor,impl-session-contract,process-optimizer}/SKILL.md` | 三方上岗取版本 | **本轮唯一准绳**（`流程契约取法`）；当前 `origin/main` 停在 `7e03237`，尚无流程层 |
| `班规@本地main` | `D:/Firefly-Canvas/.pi/skills/…`（监理与维护者工作区） | 仅维护者合并前后核对 | 已 ff 到 `a6dfa39`（v3） |
| `班规@棘轮` | `C:/Users/Administrator/orca/workspaces/Firefly-Canvas/loop/.pi/skills/…`（分支 `loop/ratchet`） | 只由棘轮写 | 最新 `e66f850`，三份 `version: 3` |
| `班规@被验树` | `D:/ff-wt/w310/.pi/skills/…`（分支 `issue/310`@`16df89a`） | 交付方工作区自带 | **`b2b3399` 时代旧版，不作数**（P-15） |

流程层文件（同样只有棘轮写，其余两方只读）：`docs/acceptance/{pain,ratchet,roster}.md`；监理自有写入面只有 `docs/acceptance/ledger.md`。

### 1.4 版本基准

- 棘轮侧读数：**v3**（三份 skill `version: 3`）。
- 本轮基准值 = **待维护者宣布**（`即停` 判据的比对目标）。三方上岗回执的 `SKILL_VERSION` 必须写从 `班规@origin` 实际取到的读数或命令报错原文，**不得填 v3**。

## 2. 身份登记册

| ROLE | HANDLE | SELF_RUN | TAB_ID | WORKTREE+分支+HEAD | SKILL_VERSION | 自有写入面 | 禁止面 | 回执时间与来源 |
|---|---|---|---|---|---|---|---|---|
| 流程优化者（棘轮） | `term_e7ba05f8-bf24-49f2-8fdf-ac7edc9ce0f0` | `run_c40189b45d94` | `c0d9ff46-eaf7-4959-a1ca-156c392b4850` | `C:/Users/Administrator/orca/workspaces/Firefly-Canvas/loop` @ `loop/ratchet` @ `e66f850` | 3（自己写的，非从 `班规@origin` 取） | `.pi/skills/**`、`docs/acceptance/{pain,ratchet,roster}.md` | 产品代码与测试、`ledger.md`、triage 标签、Issue 状态、`main` | 自报，2026-11-10 |
| 监理 | `term_6d6c2fb9-4ac0-4fca-a866-be67febbe86e`（旧邮件读数，**待回执确认**） | `run_12b3ab55bf53`（同上） | `cf524ca6-5cdf-4f7c-8144-baf8c496d202`（同上） | `D:/Firefly-Canvas` @ `main` @ 读数待回执 | 待回执（要求从 `班规@origin` 取） | `docs/acceptance/ledger.md` | 产品代码与测试、`pain.md`、`roster.md`、`main` | **待回执**（已发 msg_c7b171dfb7c9） |
| 交付方 | `term_b300bceb-cd82-4771-95c8-bb53ba836bca`（旧邮件读数，**待回执确认**） | `run_79684022c565`（同上） | `816d78f2-…`（同上） | `D:/ff-wt/w310` @ `issue/310` @ `16df89a`（待回执） | 待回执 | 产品代码与测试、Issue 评论（按任务书） | `pain.md`、`ledger.md`、`roster.md`、`.pi/skills/**`、`main` | **待回执**（已发 msg_84e9ac23e3c2） |
| 维护者 | `pi_4714c1cd_55fe_4d59_8b81_68ea12b8551b`（pi chat 内嵌会话，无 tabId） | `run_b203fe4d1bef`（旧读数） | 不适用 | `D:/Firefly-Canvas` @ `main`（待确认） | 不适用（发布方） | `main`（合并与推送）、任务书 | `pain.md`、`roster.md`（走棘轮入册） | 待回执（msg_34d36831998f） |

### 采信规则

1. 未回执的行只作「旧读数」参考，**不得据以定行为主体**（定主体按 P-23 四项取证清单）。
2. 三方上岗回执缺任一项（`ROLE`/`HANDLE`/`SELF_RUN`/`TAB_ID`/`WORKTREE`/`SKILL_VERSION`/`MY_WRITE_SURFACES`/`MY_FORBIDDEN`/`PEERS`）→ 按停止条件处理，不带身份裂缝开工。
3. 回执与我记录的旧读数不一致 → 以回执为准并记一行变更原因。
4. 每轮上岗第一件事：对册；轮内冻结本文件。

## 3. 回执核验记录（2026-11-10 loop-3）

### 3.1 三方九项回执状态

三方回执**齐**：监理 1 封（九项+A/B）、交付方 3 封（1/3 项1-5、2/3 项6-9、3/3 两问）、维护者身份由名册记录。`PEERS` 三方互证一致（handle ↔ run ↔ 工作区，无冲突）。

### 3.2 棘轮自我更正①：`班规@origin` 的真实状态（三方读数互证 + 棘轮复测）

- 复测（棘轮，2026-11-10）：`git rev-parse --short origin/main` = `7e03237`；`git ls-tree -r --name-only origin/main | grep -c "^\.pi/skills"` = **4**；`MSYS_NO_PATHCONV=1 git show 'origin/main:.pi/skills/acceptance-supervisor/SKILL.md' | grep -n "^version:"` → **rc=1（无版本行）**。
- 监理回执（项 6）与交付方回执（项 6）独立给出同一读数：**文件存在、无 version 行**。
- 因此此前「`origin/main` 上没有 `班规@origin`」的措辞**不准**，已改为：**`origin/main` 有旧版班规（无 `version:` 字段），但没有 `docs/acceptance/{pain,ratchet,roster}.md`，也没有 v2/v3 条款**。`流程契约取法` 的 `即停` 判据据此成立：三方从 `班规@origin` 取到的读数（无版本行）≠ 维护者将宣布的值（棘轮侧 v3）→ 三方在流程层上架前不得按 v3 开工。
- git-bash 踩坑（三方各踩一次，命令原文可引）：`git show origin/main:.pi/...` 不带 `MSYS_NO_PATHCONV=1` 时冒号路径被转义 → `fatal: ambiguous argument` / `fatal: invalid object name`。**规范写法**：`MSYS_NO_PATHCONV=1 git show 'origin/main:<path>'`（引号包住）。

### 3.3 棘轮自我更正②：`run-current` 是「按调用方上下文」绑定的，不能用来核实对端绑定（`run-current自上下文`）

- 事实：监理与交付方回执均称「`run-current` 回读 = 我的 run」。但 `run-current` 只能由会话**在自己的终端里**执行才有效；三方互发的「回读非 null」若由收信方代跑，读到的是**收信方自己**绑定的 run。棘轮此前对监理「`run-current` 为 null」的断言也来自同类跨上下文读数，已作废。
- 结论：**对端绑定状态只能凭对端在自己终端里的自证 + `run-list`/`run-show` 的持久字段互证**，不得由第三方代跑 `run-current` 得出。已并入 P-23 取证清单。

### 3.4 棘轮自我更正③：监理写入面含 Issue 评论

监理回执项 7 列 `GitHub Issue 评论`。棘轮此前只把 `ledger.md` 记为监理自有写入面，属登记不全。已补入名册（Issue 评论属监理职权边界第 3 条：只评不改代码）。

### 3.5 gh 在棘轮工作区不可用（`gh主工作区跑`）

`gh issue view 310 --repo zhao1477/Firefly-Canvas` 在 `C:/Users/Administrator/orca/workspaces/Firefly-Canvas/loop` 下报 `GraphQL: Could not resolve to a Repository`；`gh api repos/…/issues/comments` 报 404。同一命令在 `D:/Firefly-Canvas` 下可用（P-20 取证即在该处完成）。规范：**审计类 gh 命令一律在 `D:/Firefly-Canvas` 执行**（`cd D:/Firefly-Canvas && gh …`），或在棘轮侧改用只读网页/`git` 取证。

### 3.6 监理绑定复核结果（P-26 关闭）

监理补证（`msg_8602b4bdebcf`，2026-11-10）：两条命令均在监理自己终端执行——①`orca orchestration run-current --json` → `runId=run_12b3ab55bf53`；②`orca orchestration run-show --id run_12b3ab55bf53 --full` → `coordinator_handle=term_6d6c2fb9-4ac0-4fca-a866-be67febbe86e`。**新可互证字段**：`run-show` 的 `coordinator_handle` 是服务端持久字段，跨会话查它不受「自上下文」限制 → 今后核绑定一律用 `run-show` 的 `coordinator_handle`（`run-current` 只用于自证）。监理行状态：verified。交付方行仍待同类 `coordinator_handle` 互证（已发 msg 询问）。

### 3.7 交付方绑定复核结果 + 读数截断恢复口径（2026-11-10）

- 交付方补证（`msg_9afaeecb83d4`）：`run-show --id run_79684022c565 --full --json` → `coordinator_handle=term_b300bceb-cd82-4771-95c8-bb53ba836bca`（与回执 HANDLE 一致），另附 `created_at=2026-11-10T07:56:21Z`、`updated_at=2026-11-10T08:08:11Z`、`consumer_generation=2`、`legacy=0`。交付方行状态：**verified**。三方绑定全部持久互证完毕。
- 读数口径（`check截断用inbox--full`，交付方自报）：`orca orchestration check` 的正文会被截断（`truncated; --full`），管道处理时易丢全文；**要全文一律 `orca orchestration inbox --full` 或 `check --full`**。`inbox --json` 在含中文正文时曾出现解析中断，解析失败时改用 `inbox --full` 纯文本 + 分段截取。

### 3.8 版本宣布格式（`版本宣布带证据`，P-28）

维护者「同意推流程层 + 同意宣布版本号」≠ 口径已生效。复测（棘轮 2026-11-10，批准当刻）：`git rev-parse --short origin/main` = `7e03237`；`git ls-tree -r --name-only origin/main | grep docs/acceptance` 只有 `ledger.md`；`MSYS_NO_PATHCONV=1 git show 'origin/main:.pi/skills/process-optimizer/SKILL.md' | grep -n "^version:"` → rc=1。合并面 = `origin/loop/ratchet@5820225`，与 `origin/main` 的差异只有 8 个文件：`.pi/skills/{acceptance-supervisor,impl-session-contract,process-optimizer,reasoning-modes}/SKILL.md`、`AGENTS.md`、`docs/acceptance/{pain,ratchet,roster}.md`（零产品代码）。

**生效判据（三方上岗必须逐项核验，缺任一件即停）**：
1. `git fetch origin main && git rev-parse --short origin/main` 读数 ≠ `7e03237`（即流程层已并入）；
2. `MSYS_NO_PATHCONV=1 git show 'origin/main:.pi/skills/<自己契约名>/SKILL.md' | grep -n "^version:"` 有输出且 = 维护者宣布值；
3. 维护者的版本宣布邮件里同时给出上述两条的命令原文与输出（只有「按 v3 干」这类无证据宣布 → 视为未生效，即停上报）。

**名册自身的一致性**：本轮流程层版本号以「`origin/main` 上实际读到的 `version:` 行」为准，不以棘轮支头读数为准；`班规@棘轮@5820225` 现读数 `version: 3`。

### 3.9 交付方「班规@origin 已=3」读数不成立（`读数贴错路径`，2026-11-10）

三方工作区（`D:/ff-wt/w310`、`D:/Firefly-Canvas`、棘轮 loop）的 `origin` 全部 = `https://github.com/miaomiaozii/Firefly-Canvas.git`，`refs/remotes/origin/main` 全部 = `7e03237`（committerdate 2026-11-10 15:45:52 +0800）。三处 `git show 'origin/main:.pi/skills/<契约名>/SKILL.md' | grep -n "^version:"` 均 **无匹配**；`7e03237:.pi/skills/impl-session-contract/SKILL.md` 前 8 行是 `---/name/disable-model-invocation/description/---`，**根本没有 version 行**。
唯一含 `3:version: 3` 的读数是 `a0a8615`（= `D:/Firefly-Canvas` 的**本地 main**，含棘轮推的流程层）。所以交付方 msg_7042f14d178b 报的「origin/main 已=3」是把 `班规@本地main` 当成了 `班规@origin`。
**规范写法（三方必须照抄，不得简写）**：
1. `git fetch origin main`
2. `git rev-parse --short origin/main` → 当前必须是 `7e03237`（v3 未上架前的读数），≠ 宣布值即停
3. `MSYS_NO_PATHCONV=1 git show 'origin/main:.pi/skills/<契约名>/SKILL.md' | grep -n "^version:"` → 未上架前 rc=1
4. 报告里必须同时写第 2 步的 sha。只有 grep 输出没有 sha 的读数一律不采信。

**流程层仍未上架**（截至本轮）：`origin/main` = `7e03237`，`docs/acceptance/**` 只有 `ledger.md`；`loop/ratchet` = `85bdbfd` 是 `origin/main` 的非祖先，合并面 = `git diff --name-only origin/main origin/loop/ratchet` 的 8 个文件。

### 3.10 监理独立复读互证 + 假读数影响面（2026-11-10）

监理四步照抄独立复读（`msg_31214dba38e2`）：`git fetch origin main` → `origin/main=7e03237`；`grep "^version:"` 于 `origin/main:.pi/skills/acceptance-supervisor/SKILL.md` → rc=1；本地 main `a0a8615` 含 `version: 3`（仅本地生效面）。**与棘轮三处互证完全一致 → §3.9 结论 confirmed（两人独立、命令与 sha 齐）**。监理立场：以 `班规@origin` 为准，读不到宣布版本号的对应读数即停，不用本地 main 开工。
影响面核查（按 P-22 归因链要求）：监理声明其全部裁定发生在该假读数之前且依据均带 sha/评论 ID → 假读数未污染任何在途裁定，不需追级。交付方假读数只影响「是否可按 v3 开工」这一条待决判断，已被本轮预警拦下。

### 3.11 消息类型与 Dispatch 权限（`类型需Dispatch`）

`--type escalation`（及 `worker_done`）要求发送方有 active Dispatch，否则报 `error: sender_not_assignee: No active Dispatch belongs to this message sender.`；`--type status` / `question` 无此要求（实测：同一正文 escalation 失败 → status 成功，`msg_ed891ec8b7f3`）。合法取值：`status, dispatch, worker_done, merge_ready, escalation, handoff, decision_gate, question, heartbeat`。**给维护者/跨角色的一般上报一律用 `status`；`escalation` 只在有 Dispatch 的 worker 会话里用**。
