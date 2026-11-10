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

监理裁定台账在 `docs/acceptance/ledger.md`，一行一条，供维护者抽查。

角色会话还要同时给推理档位（`{REASON:…}` 令牌与 harness 的 `--thinking` 两样都要）：

```bash
pi --thinking xhigh   # 监理：首条消息 {REASON:hyper}{REASON:ihyper} + 上岗验收；监理不得用 007，它会自己改代码，双盲当场失效
pi --thinking xhigh   # 交付方：首条消息 {REASON:007}{REASON:icode} + 上岗交付

# 别写 --thinking high：这几套模型的 thinkingLevelMap 里 high 与 max 是 null，等于没设
```

会话内 `shift+tab` 循环切档位。档位选型与实测依据见 `reasoning-modes`。
