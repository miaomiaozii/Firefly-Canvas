# 流程层口径表与身份登记册（ROSTER）

唯一名册 + 唯一口径表。**单写者：流程优化者（棘轮）**（`单写`）。三方不得写本文件；有异议发邮件给我，由我改。
本文件是三方的称呼、编号、文件指代、版本基准的**唯一真相源**。任何邮件、台账、Issue 评论引用角色或文件，一律按本表写法。

## 1. 口径表（先统一这个，再谈别的）

### 1.1 角色名（规范名 / 唯一别名）

| 规范名（正文只用这个） | 唯一别名 | 职责边界（一句话） | 不得做 |
|---|---|---|---|
| 流程优化者 | 棘轮 | 收卡点、判定入队、把教训固化成可复跑的检查或工具 | 不做验收判定、不改产品代码与测试、不落 triage 标签、不关 Issue、不推 main |
| 监理 | 无别名 | 独立复现与验收裁定、写 `ledger.md` | 不改产品代码与测试、不推 main、不替交付方写实现 |
| 交付方 | 无别名 | 按任务书实现并自证、写代码与测试、按契约第 5 步收尾 | 不做自我验收裁定、不推 main、不写 `pain.md`/`ledger.md` |
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
