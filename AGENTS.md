## Agent skills

### Issue tracker

本项目使用 GitHub Issues 管理需求、规格和开发任务。详见 `docs/agents/issue-tracker.md`。

### Triage labels

分类流程使用五种规范角色，并映射为带中文说明的 GitHub 标签。详见 `docs/agents/triage-labels.md`。

### Domain docs

本项目采用 single-context 领域文档结构：根目录使用 `CONTEXT.md`，架构决策存放在 `docs/adr/`。详见 `docs/agents/domain.md`。

### 显式激活的角色会话

三角色（工人 / 监理 / 流程优化者（棘轮））只在按 `自举顺序` 建立的会话里生效（user 只起根会话，默认工人；同层另两方由根会话建），且**同一项目工作区同一时间每个角色只允许一个存活会话**（v5 边界令）。未激活的会话按普通会话工作，既不做验收判定，也不受工人契约约束。维护者角色已于 2026-11-10 退役，裁决职责合并给 user 与棘轮。旧称「交付方」（口语变体「交付者」）不是第四个角色（`旧称不是角色`）。

- 三角色契约：
  - 监理：`.pi/skills/acceptance-supervisor/SKILL.md`——监管工人、判定 Issue 是否完成并指示工人推送与关闭
  - 工人（旧称交付方，文件名不变）：`.pi/skills/impl-session-contract/SKILL.md`
  - 流程优化者（棘轮）：`.pi/skills/process-optimizer/SKILL.md`——收另两方的卡点上报，把教训固化成可复跑的检查或工具
- 通用层（跨项目复用）：`班规@origin:.pi/skills/three-role-loop/SKILL.md`（三权分立、上岗前置、信箱增量、卡点四段、发权、卫生）；项目三份契约只写本项目参数与例外，冲突以项目契约为准。
- 唯一口径与身份册：`docs/acceptance/roster.md`（棘轮单写，另两方只读）。
- 推理档位：`.pi/skills/reasoning-modes/SKILL.md`（三类会话上岗时各带自己的 `{REASON:…}` 组合，并随种子一起投进首条消息（`种子带档位`）；换档位必须换会话）
- 版本：门集五份 `version: 13`（`acceptance-supervisor`、`impl-session-contract`、`process-optimizer`、`three-role-loop`、`reasoning-modes`）；上岗第一读 `docs/acceptance/roster.md` §0 状态锚，**版本读数 ≠ 13 即停**（上报棘轮）。

三份都设了 `disable-model-invocation: true`，不会自动加载。激活方式二选一：会话内手敲 `/skill:acceptance-supervisor`、`/skill:impl-session-contract` 或 `/skill:process-optimizer`；或在新会话的启动 prompt 里写明"读上面那个路径并照它执行"。

三个角色会话都必须 Orca 界面里可见、可点开的会话（user 要能抽查现场，直到他宣布某个会话退役）；子代理不可见可以接受。**每角色一棵专属工作树（`同层三树`），监理与棘轮不得落在主仓根。** 工作树让 Orca 建，不要 `git worktree add`——那样建的树 Orca 不认，会话会退成后台游离句柄（`orca terminal show` 里 `tabId` 以 `pty:` 开头），界面里看不到。

**user 只跑第一组（工人根会话）；后两组由工人按名册 §1 上岗种子逐字执行（`自举顺序` + `种子自举`，首条消息随建随投，不建空会话再补投）。种子全文以 `docs/acceptance/roster.md` §1 为单一事实源。**

```bash
# ① 工人（根会话，user 跑）：建专属树 + 起会话，种子当 --command 的一部分带进去
orca worktree create --name w<issue> --repo path:D:/Firefly-Canvas \
  --base-branch main --branch issue/<issue> --issue <issue> --setup skip --json
orca terminal create --focus --json \
  --worktree path:C:/Users/Administrator/orca/workspaces/Firefly-Canvas/w<issue> \
  --command "pi.cmd --thinking xhigh \"<工人种子全文：{REASON:007}{REASON:icode} 开头>\""

# ② 监理（工人建）：专属 review/<issue> 树，不得放主仓根
orca worktree create --name rv<issue> --repo path:D:/Firefly-Canvas \
  --base-branch main --branch review/<issue> --issue <issue> --setup skip --json
orca terminal create --json \
  --worktree path:C:/Users/Administrator/orca/workspaces/Firefly-Canvas/rv<issue> \
  --command "pi.cmd --thinking xhigh \"<监理种子全文：{REASON:hyper}{REASON:ihyper} 开头>\""

# ③ 棘轮（工人建；它要改流程文件，不能与另两方同工作区；名册 §2 存活列已存活则不重建）
orca worktree create --name loop --repo path:D:/Firefly-Canvas \
  --base-branch main --branch loop/ratchet --setup skip --json
orca terminal create --json \
  --worktree path:C:/Users/Administrator/orca/workspaces/Firefly-Canvas/loop \
  --command "pi.cmd --thinking xhigh \"<棘轮种子全文：{REASON:iteacher}{REASON:ihouse} 开头>\""
```

退路（需 Orca 托管 agent 记录时）：`orca agent launch --agent pi --worktree path:<专属树> --surface terminal --prompt "<种子>" --json`——它能带种子但带不了 `--thinking`，两样都要就用上面那组。

可见性自检与启动细节、收尾清理见监理契约的「会话可见性」节。

首条消息不用手打：种子已随建随投（见上）。建完会话后的补投一律走 `发送判据`：`orca terminal send --terminal <handle> --text "<正文>" --enter --wait-submit 20 --json`，再看 `orca terminal read --terminal <handle>` 的 `draft`——**`draft` 非空就是没发出去，不得重发**（实测 2026-11-11：往输入框里手打长正文会触发 pi 自动补全，回车变成确认补全，Escape 直接 `Operation aborted`）。监理不得用 `007`，它会自己改代码，双盲当场失效。三个 Run 都在会话自己里建（`orca orchestration run-create`），user 不预建再转交——那会把邮件注入到 user 会话。认人认 handle，`orca terminal rename` 会被 pi 自己的标题刷新覆盖。三方上岗后各自把 handle/Run/工作树登记进名册 §2。

监理裁定台账在 `docs/acceptance/ledger.md`，一行一条，供 user 抽查。

自循环与棘轮：三方跑的过程中都要报卡点（四段：现象/现行流程/代价/最小改法），以邮件上报，由棘轮落进 `docs/acceptance/pain.md`（单写）；流程优化者在**轮间**吸收成流程改动，版本与可复跑回归记在 `docs/acceptance/ratchet.md`。硬口径：只收紧不放宽（要放宽只能提议给 user）、每条吸收必须留下可复跑的回归、轮内冻结流程文件、上岗报版本号（≠ `roster.md` §1.4 登记值即停）、优化会话不得改代码/不得做验收判定/不得落 triage 标签；流程层（三份班规 + pain/ratchet/roster）由棘轮写并直推 `origin/main`，**产品代码与测试任何人都不得直推 `main`**（监理判定通过后指示工人 push，merge 归 user）。棘轮不得频繁发消息污染监理与工人的上下文：轮内只受理卡点，轮间一次上架 + 一封生效通知（回执一行 `已读 version:<n> @<sha>`）。

角色会话还要同时给推理档位（`{REASON:…}` 令牌与 harness 的 `--thinking` 两样都要；上面配方里的 `pi.cmd --thinking xhigh "<种子>"` 一次解决两样：位置参数就是初始 prompt）：

```bash
# 监理：{REASON:hyper}{REASON:ihyper}（输出预算紧时改 {REASON:socrates}{REASON:ihouse}）
# 工人：{REASON:007}{REASON:icode}
# 棘轮：{REASON:iteacher}{REASON:ihouse}

# 别写 --thinking high：这几套模型的 thinkingLevelMap 里 high 与 max 是 null，等于没设
# 令牌名字拼错静默无效（实测 `{REASON:strak}`），上岗后看输出的声口确认吃了没
```

会话内 `shift+tab` 循环切档位。档位选型与实测依据见 `reasoning-modes`。
