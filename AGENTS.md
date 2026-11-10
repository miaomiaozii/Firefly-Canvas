## Agent skills

### Issue tracker

本项目使用 GitHub Issues 管理需求、规格和开发任务。详见 `docs/agents/issue-tracker.md`。

### Triage labels

分类流程使用五种规范角色，并映射为带中文说明的 GitHub 标签。详见 `docs/agents/triage-labels.md`。

### Domain docs

本项目采用 single-context 领域文档结构：根目录使用 `CONTEXT.md`，架构决策存放在 `docs/adr/`。详见 `docs/agents/domain.md`。

### 显式激活的角色会话

三角色（工人 / 监理 / 流程优化者（棘轮））只在 user 显式建立的会话里生效，且**同一项目工作区同一时间每个角色只允许一个存活会话**（v5 边界令）。未激活的会话按普通会话工作，既不做验收判定，也不受工人契约约束。维护者角色已于 2026-11-10 退役，裁决职责合并给 user 与棘轮。

- 三角色契约：
  - 监理：`.pi/skills/acceptance-supervisor/SKILL.md`——监管工人、判定 Issue 是否完成并指示工人推送与关闭
  - 工人（旧称交付方，文件名不变）：`.pi/skills/impl-session-contract/SKILL.md`
  - 流程优化者（棘轮）：`.pi/skills/process-optimizer/SKILL.md`——收另两方的卡点上报，把教训固化成可复跑的检查或工具
- 通用层（跨项目复用）：`班规@origin:.pi/skills/three-role-loop/SKILL.md`（三权分立、上岗前置、信箱增量、卡点四段、发权、卫生）；项目三份契约只写本项目参数与例外，冲突以项目契约为准。
- 唯一口径与身份册：`docs/acceptance/roster.md`（棘轮单写，另两方只读）。
- 推理档位：`.pi/skills/reasoning-modes/SKILL.md`（两类会话上岗时各带自己的 `{REASON:…}` 组合；换档位必须换会话）

三份都设了 `disable-model-invocation: true`，不会自动加载。激活方式二选一：会话内手敲 `/skill:acceptance-supervisor`、`/skill:impl-session-contract` 或 `/skill:process-optimizer`；或在新会话的启动 prompt 里写明"读上面那个路径并照它执行"。

三个角色会话都必须 Orca 界面里可见、可点开的会话（user 要能抽查现场，直到他宣布某个会话退役）；子代理不可见可以接受。工作树让 Orca 建，不要 `git worktree add`——那样建的树 Orca 不认，会话会退成后台游离句柄（`orca terminal show` 里 `tabId` 以 `pty:` 开头），界面里看不到：

```bash
# 工人（旧称交付方）：先建 Orca 托管工作树（落点在 C:/Users/Administrator/orca/workspaces/Firefly-Canvas/<name>），再在里面起一个可见终端
orca worktree create --name w<issue> --repo path:D:/Firefly-Canvas \
  --base-branch main --branch issue/<issue> --issue <issue> --setup skip --json
orca terminal create --worktree path:<新建工作树> --command "pi.cmd --thinking xhigh" --focus --json

# 监理：主工作区里再起一个可见终端
orca terminal create --worktree path:D:/Firefly-Canvas --command "pi.cmd --thinking xhigh" --focus --json

# 流程优化者：自己的 Orca 工作树（它要改流程文件，不能与另两方同工作区）
orca worktree create --name loop --repo path:D:/Firefly-Canvas \
  --base-branch main --branch loop/ratchet --setup skip --json
orca terminal create --worktree path:<新建工作树> --command "pi.cmd --thinking xhigh" --focus --json
```

可见性自检与启动细节、收尾清理见监理契约的「会话可见性」节。

起来后各自发首条消息：监理 `{REASON:hyper}{REASON:ihyper}` + 上岗验收（监理不得用 007，它会自己改代码，双盲当场失效）；工人 `{REASON:007}{REASON:icode}` + 上岗交付。两个 Run 都在会话自己里建（`orca orchestration run-create`），user 不预建再转交——那会把邮件注入到 user 会话。认人认 handle，`orca terminal rename` 会被 pi 自己的标题刷新覆盖。

监理裁定台账在 `docs/acceptance/ledger.md`，一行一条，供 user 抽查。

自循环与棘轮：三方跑的过程中都要报卡点（四段：现象/现行流程/代价/最小改法），以邮件上报，由棘轮落进 `docs/acceptance/pain.md`（单写）；流程优化者在**轮间**吸收成流程改动，版本与可复跑回归记在 `docs/acceptance/ratchet.md`。硬口径：只收紧不放宽（要放宽只能提议给 user）、每条吸收必须留下可复跑的回归、轮内冻结流程文件、上岗报版本号（≠ `roster.md` §1.4 登记值即停）、优化会话不得改代码/不得做验收判定/不得落 triage 标签；流程层（三份班规 + pain/ratchet/roster）由棘轮写并直推 `origin/main`，**产品代码与测试任何人都不得直推 `main`**（监理判定通过后指示工人 push，merge 归 user）。棘轮不得频繁发消息污染监理与工人的上下文：轮内只受理卡点，轮间一次上架 + 一封生效通知（回执一行 `已读 version:<n> @<sha>`）。

角色会话还要同时给推理档位（`{REASON:…}` 令牌与 harness 的 `--thinking` 两样都要，上面配方里的 `pi.cmd --thinking xhigh` 只解决后者）：

```bash
# 监理：首条消息 {REASON:hyper}{REASON:ihyper} + 上岗验收
# 交付方：首条消息 {REASON:007}{REASON:icode} + 上岗交付

# 别写 --thinking high：这几套模型的 thinkingLevelMap 里 high 与 max 是 null，等于没设
```

会话内 `shift+tab` 循环切档位。档位选型与实测依据见 `reasoning-modes`。
