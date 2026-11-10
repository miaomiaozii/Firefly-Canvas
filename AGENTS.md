## Agent skills

### Issue tracker

本项目使用 GitHub Issues 管理需求、规格和开发任务。详见 `docs/agents/issue-tracker.md`。

### Triage labels

分类流程使用五种规范角色，并映射为带中文说明的 GitHub 标签。详见 `docs/agents/triage-labels.md`。

### Domain docs

本项目采用 single-context 领域文档结构：根目录使用 `CONTEXT.md`，架构决策存放在 `docs/adr/`。详见 `docs/agents/domain.md`。

### 显式激活的角色会话

验收监理与交付方是两个角色，只在维护者显式激活的会话里生效。未激活的会话按普通会话工作，既不做验收判定，也不受交付契约约束。

- 监理契约：`.pi/skills/acceptance-supervisor/SKILL.md`
- 交付方契约：`.pi/skills/impl-session-contract/SKILL.md`
- 推理档位：`.pi/skills/reasoning-modes/SKILL.md`（两类会话上岗时各带自己的 `{REASON:…}` 组合；换档位必须换会话）

两份都设了 `disable-model-invocation: true`，不会自动加载。激活方式二选一：会话内手敲 `/skill:acceptance-supervisor` 或 `/skill:impl-session-contract`；或在新会话的启动 prompt 里写明"读上面那个路径并照它执行"。

两个角色会话都必须是 Orca 界面里可见、可点开的会话（维护者要能抽查现场）；子代理不可见可以接受。工作树让 Orca 建，不要 `git worktree add`——那样建的树 Orca 不认，会话会退成后台游离句柄（`orca terminal show` 里 `tabId` 以 `pty:` 开头），界面里看不到：

```bash
# 交付方：先建 Orca 托管工作树（落点在 C:/Users/Administrator/orca/workspaces/Firefly-Canvas/<name>），再在里面起一个可见终端
orca worktree create --name w<issue> --repo path:D:/Firefly-Canvas \
  --base-branch main --branch issue/<issue> --issue <issue> --setup skip --json
orca terminal create --worktree path:<新建工作树> --command "pi.cmd --thinking xhigh" --focus --json

# 监理：主工作区里再起一个可见终端
orca terminal create --worktree path:D:/Firefly-Canvas --command "pi.cmd --thinking xhigh" --focus --json
```

可见性自检与启动细节、收尾清理见监理契约的「会话可见性」节。

起来后各自发首条消息：监理 `{REASON:hyper}{REASON:ihyper}` + 上岗验收（监理不得用 007，它会自己改代码，双盲当场失效）；交付方 `{REASON:007}{REASON:icode}` + 上岗交付。两个 Run 都在会话自己里建（`orca orchestration run-create`），维护者不预建再转交——那会把邮件注入到维护者会话。认人认 handle，`orca terminal rename` 会被 pi 自己的标题刷新覆盖。

监理裁定台账在 `docs/acceptance/ledger.md`，一行一条，供维护者抽查。

角色会话还要同时给推理档位（`{REASON:…}` 令牌与 harness 的 `--thinking` 两样都要，上面配方里的 `pi.cmd --thinking xhigh` 只解决后者）：

```bash
# 监理：首条消息 {REASON:hyper}{REASON:ihyper} + 上岗验收
# 交付方：首条消息 {REASON:007}{REASON:icode} + 上岗交付

# 别写 --thinking high：这几套模型的 thinkingLevelMap 里 high 与 max 是 null，等于没设
```

会话内 `shift+tab` 循环切档位。档位选型与实测依据见 `reasoning-modes`。
