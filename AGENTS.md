## Agent skills

### Issue tracker

本项目使用 GitHub Issues 管理需求、规格和开发任务。详见 `docs/agents/issue-tracker.md`。

### Triage labels

分类流程使用五种规范角色，并映射为带中文说明的 GitHub 标签。详见 `docs/agents/triage-labels.md`。

### Domain docs

本项目采用 single-context 领域文档结构：根目录使用 `CONTEXT.md`，架构决策存放在 `docs/adr/`。详见 `docs/agents/domain.md`。

### 显式激活的角色会话

两角色（工人 / 监理）只在按 `自举顺序` 建立的会话里生效（user 只起根会话，默认工人；监理由根会话建），且**同一项目工作区同一时间每个角色只允许一个存活会话**。未激活的会话按普通会话工作，既不做验收判定，也不受工人契约约束。维护者角色已于 2026-11-10 退役，裁决职责合并给 user。旧称「交付方」（口语变体「交付者」）不是第三个角色（`旧称不是角色`）。

第三个角色「流程优化者（棘轮）」及其卡点队列（`pain.md`）、身份名册（`roster.md`）、版本门与可复跑回归脚本已于 **2026-11-12 由 user 整体废除**（判定：太难用）。要查旧内容只在 git 历史里：`git log --all -- docs/acceptance/`。

- 两角色契约：
  - 监理：`.pi/skills/acceptance-supervisor/SKILL.md`——监管工人、判定 Issue 是否完成并指示工人推送与关闭
  - 工人（旧称交付方，文件名不变）：`.pi/skills/impl-session-contract/SKILL.md`
- 通用层（跨项目复用）：`班规@origin:.pi/skills/two-role-loop/SKILL.md`（两权分立、上岗前置与 `种子公式`、信箱增量、流程摩擦、卫生）；项目两份契约只写本项目参数与例外，冲突以项目契约为准。
- 身份登记：`docs/acceptance/ledger.md` 的头部（监理单写）——上岗时把双方 `handle / Run / 工作树` 写进去。**没有名册文件**，引用会话按 `认人编号`：`规范名 + term_<handle> + run_<id>`。
- 推理档位：`.pi/skills/reasoning-modes/SKILL.md`（两个角色上岗各带自己的 `{REASON:…}` 组合，并随种子一起投进首条消息（`种子带档位`）；换档位必须换会话）
- **没有版本号、没有版本门**：引用班规一律按 `班规@origin` 从 `origin/main` 取原文（`git show "origin/main:<路径>"`，git-bash 要 `MSYS_NO_PATHCONV=1` + 引号），并当场报出「文件 + 节名 + 取法命令」。自己工作区里那份叫 `班规@被验树`，不作数。

两份都设了 `disable-model-invocation: true`，不会自动加载。激活方式二选一：会话内手敲 `/skill:acceptance-supervisor` 或 `/skill:impl-session-contract`；或在新会话的启动 prompt 里写明"读上面那个路径并照它执行"。

两个角色会话都必须 Orca 界面里可见、可点开的会话（user 要能抽查现场，直到他宣布某个会话退役）；子代理不可见可以接受。**每角色一棵专属工作树（`同层两树`），监理不得落在主仓根。** 工作树让 Orca 建，不要 `git worktree add`——那样建的树 Orca 不认，会话会退成后台游离句柄（`orca terminal show` 里 `tabId` 以 `pty:` 开头），界面里看不到。

**user 只跑第一组（工人根会话）；第二组（监理）由工人照通用层 §3 的 `种子公式` 执行（`自举顺序` + `种子自举`，首条消息随建随投，不建空会话再补投）。**

> `种子公式`：`{推理令牌} 上岗：<角色>：读 <该角色契约的完整路径> 并照它执行。` —— 除令牌与这一句之外不得有任何内容，尤其不得夹带任务指令。被建方上岗第一事就是核对种子是否只有这两样，多出来的即停上报 user。

```bash
# ① 工人（根会话，user 跑）：建专属树 + 起会话，种子当 --command 的一部分带进去
orca worktree create --name w<issue> --repo path:D:/Firefly-Canvas \
  --base-branch main --branch issue/<issue> --issue <issue> --setup skip --json
orca terminal create --focus --json \
  --worktree path:C:/Users/Administrator/orca/workspaces/Firefly-Canvas/w<issue> \
  --command "pi.cmd --thinking xhigh \"{REASON:007}{REASON:icode} 上岗：工人：读 .pi/skills/impl-session-contract/SKILL.md 并照它执行。\""

# ② 监理（工人建）：专属 review/<issue> 树，不得放主仓根
orca worktree create --name rv<issue> --repo path:D:/Firefly-Canvas \
  --base-branch main --branch review/<issue> --issue <issue> --setup skip --json
orca terminal create --json \
  --worktree path:C:/Users/Administrator/orca/workspaces/Firefly-Canvas/rv<issue> \
  --command "pi.cmd --thinking xhigh \"{REASON:hyper}{REASON:ihyper} 上岗：监理：读 .pi/skills/acceptance-supervisor/SKILL.md 并照它执行。\""
```

退路（需 Orca 托管 agent 记录时）：`orca agent launch --agent pi --worktree path:<专属树> --surface terminal --prompt "<种子>" --json`——它能带种子但带不了 `--thinking`，两样都要就用上面那组。

可见性自检与启动细节、收尾清理见监理契约的「会话可见性」节。

首条消息不用手打：种子已随建随投（见上）。建完会话后的补投一律走 `发送判据`：`orca terminal send --terminal <handle> --text "<正文>" --enter --wait-submit 20 --json`，再看 `orca terminal read --terminal <handle>` 的 `draft`——**`draft` 非空就是没发出去，不得重发**（实测 2026-11-11：往输入框里手打长正文会触发 pi 自动补全，回车变成确认补全，Escape 直接 `Operation aborted`）。监理不得用 `007`，它会自己改代码，双盲当场失效。两个 Run 都在会话自己里建（`orca orchestration run-create`），user 不预建再转交——那会把邮件注入到 user 会话。认人认 handle，`orca terminal rename` 会被 pi 自己的标题刷新覆盖。监理上岗后把双方 handle / Run / 工作树登记进台账头部。

监理裁定台账在 `docs/acceptance/ledger.md`，一行一条，供 user 抽查。

流程摩擦：工人和监理踩到「不是代码问题，是流程没覆盖或与实际不符」，就在自己会话里直接对 user 说一句（现象 / 现行流程 / 代价 / 想怎么改），由 user 决定改不改、让谁代笔——不再排队、不再轮间换版、不再有专门角色改班规。**产品代码与测试任何人都不得直推 `main`**（监理判定通过后指示工人 push，merge 归 user）；班规（`.pi/skills/**`）与 `AGENTS.md` 归 user 写，工人和监理不得自己改。

角色会话还要同时给推理档位（`{REASON:…}` 令牌与 harness 的 `--thinking` 两样都要；上面配方里的 `pi.cmd --thinking xhigh "<种子>"` 一次解决两样：位置参数就是初始 prompt）：

```bash
# 监理：{REASON:hyper}{REASON:ihyper}（输出预算紧时改 {REASON:socrates}{REASON:ihouse}）
# 工人：{REASON:007}{REASON:icode}

# 别写 --thinking high：这几套模型的 thinkingLevelMap 里 high 与 max 是 null，等于没设
# 令牌名字拼错静默无效（实测 `{REASON:strak}`），上岗后看输出的声口确认吃了没
```

会话内 `shift+tab` 循环切档位。档位选型与实测依据见 `reasoning-modes`。
