# Agent 终端默认关闭（#464）

## 范围与决定

权威范围是用户的 [#464 最新裁定（comment 7069344553）][scope]：**暂不开放终端**；Codex 关闭 `shell_tool`，Pi 不提供 bash / PowerShell / codemode，并补测试证明 `read-only` 配置下没有终端工具。旧 issue 正文中的自定义 `shell.run`、按节点开启及授权方案不再是本切片的实施依据。

本切片只收紧原生工具注册，不作 #478 覆盖调查结论。是否开放、开启 UI、审批/授权和 OS 边界均须另行裁定，不增加开关或权限档位。本文补充 [ADR 0024](../adr/0024-codex-per-turn-host-bridge.md) 的默认关闭依据，不把工具注册约束等同于 OS 隔离。

## Codex：版本及原生关闭机制

只读取已安装公开元数据 `C:/Users/Administrator/AppData/Roaming/npm/node_modules/@openai/codex/package.json`，实际 `@openai/codex` 版本为 **0.162.0**，并非沿用仓库注释中的版本声明。GitHub [release tag ref][tag-ref] 指向 annotated tag `1f3f93473394b620b35580859b7e6864f7a9f948`，[tag object][tag-object] 再指向 commit **`c1382380de69521303b416720a52f42d51af6248`**。以下源码均钉在该 commit；已安装 CLI 的版本/feature 解析回读见下节；未证明其二进制构建哈希与该源码完全相同。

决定：每轮明确传入 argv 对 **`["-c", "features.shell_tool=false"]`**，保留 `-s read-only`，首轮与显式 ID 续轮共用构造路径。`false` 是 TOML boolean，不应变成字符串 `"false"`；不修改任何用户 home 配置。

| 一手模块 / 符号 | 确认的事实 |
|---|---|
| [`codex-rs/features/src/lib.rs`][features]：`FEATURES`、`Features::apply_map` | `Feature::ShellTool` 的键确为 `shell_tool`，stable，默认 **true**；显式 false 会移除该 feature。不能依赖原生默认关闭。 |
| [`codex-rs/core/src/tools/spec_plan.rs`][spec-plan]：`add_shell_tools` | `!features.enabled(Feature::ShellTool)` 在注册前直接 return，位于 `UnifiedExec` 分支之前；因此同时阻断 `ExecCommandHandler::new` + `WriteStdinHandler`，以及 else 中的 `ExecCommandHandler::one_shot`。这不是让模型自觉不调用，也不是等待沙盒拒绝。 |
| [`codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`][exec-command]：`one_shot`、`tool_name`、`handle_call` | fallback 仍叫 **`exec_command`**，仍会执行 shell 命令，只是到完成/超时为止，不能续接；不是另一个已验证的旧 `shell` 注册器。仅关闭 `unified_exec` 或 TTY 不能当作关闭终端。 |
| [`codex-rs/utils/cli/src/config_override.rs`][config-override]：`CliConfigOverrides`、`parse_overrides` | `-c` / `--config` 接受 dotted key，值按 TOML 解析；该参数是 global。 |
| [`codex-rs/config/src/loader/mod.rs`][loader]：`load_config_layers_state`；[`config/src/merge.rs`][merge]：`merge_toml_values` | `${CODEX_HOME}/config.toml` 用户层在前，CLI `SessionFlags` 层在其后，overlay 覆盖 base。因此显式 false 覆盖普通 home 中的 true；这不等于能压过所有 managed requirements / legacy managed config，源码仍有更高管理层。未读取本机这些配置。 |
| [`codex-rs/cli/src/main.rs`][cli-main]：`Subcommand::Exec`；[`exec/src/lib.rs`][exec-main]：`run_main` | 根 CLI overrides 被传入 exec；exec 解析后传给 `ConfigBuilder::cli_overrides` 及 in-process server 启动参数，再处理首轮或 resume；不是只对首轮生效的提示词。 |

[`app/services/agent_adapters/codex_adapter.py`](../../app/services/agent_adapters/codex_adapter.py) 的 `CodexAdapter.build_argv` 显式加入上述 false，继续保留 `-s read-only`、instructions、MCP 等参数。`build_resume_argv` 复用首轮 argv，只把 `resume <thread_id>` 插入末尾 `--json -` 之前，故新增 override 应留在 **`exec … -c features.shell_tool=false … resume <thread_id> --json -`** 的 options 区，不另造续轮策略。上游 [`exec/src/cli.rs`][exec-cli] 的 `Cli.command` / `Command::Resume` 也确认 resume 是 exec 子命令；这里保留适配器既有排序，不主张新版 CLI 必然拒绝其它排序。

## Pi：已有的无进程工具合同

- [`app/services/agent_adapters/pi_adapter.py`](../../app/services/agent_adapters/pi_adapter.py) 的 `PiAdapter.build_argv` 已保留 `--no-extensions`、显式 `-e params.tools_path`、`--no-builtin-tools`，并通过 `--tools` **仅允许** `canvas.*,generate.*,media.*,viewport.*,agent.*,plan.*,ops.*,chat.*,read,write,edit`。bash、PowerShell、codemode、shell / exec 不在该 allowlist；不得为补文件能力重新启用裸内置工具。
- [`app/agent_extensions/session-file-tools.ts`](../../app/agent_extensions/session-file-tools.ts) 的 `registerSessionFileTools` 只构造/注册 `createReadToolDefinition`、`createWriteToolDefinition`、`createEditToolDefinition`。缺少 `FIREFLY_AGENT_WORKSPACE_ROOT` 不注册；SDK 非 **1.5.1** 或工厂不匹配时报不可用，不降级到裸工具。
- 同模块的 `target` / `inspect` 把 I/O 固定在服务器指定的 `sessions` 根，检查路径别名、越界与稳定链接；禁止工具改写根层 `system-prompt.txt` / `.jsonl` 控制文件。执行上下文也固定 `cwd: root`，不由模型传入 cwd 扩权。入口为 [`app/agent_extensions/canvas-tools.ts`](../../app/agent_extensions/canvas-tools.ts) 默认注册函数。
- 这些是**工具注册/可见性控制与文件工具 I/O 守卫，不是整个 Pi 进程的 OS 沙盒**；不承诺对抗其它 OS 进程并发替换 reparse points。Pi 的目录内 read/write/edit 保留，不能把 Codex 的 `read-only` 误写成 Pi 文件工具全只读。与 [CONTEXT.md「节点工作目录与文件工具（#445）」](../../CONTEXT.md) 一致。

## 已证明的缺口与不作的推断

1. 本次明确证明的原生进程工具遗漏风险是 **`unified_exec=false` 的 one-shot fallback**；`shell_tool=false` 的提前返回覆盖它。`read-only`、`approval_policy=never`、提示词「不准执行」均不是移除工具的机制，不能替代该 flag。
2. `shell_tool` 不是「关闭 Codex 全部工具/全部子进程」开关。[`spec_plan.rs`][spec-plan] 的 `register_code_mode_executors` 按 tool mode 独立注册 JavaScript code-mode 入口，MCP / extension / dynamic 工具也另行加入 registry；不能仅凭名为 `exec` 就认定它是 shell fallback，亦不能声称本 flag 关闭了这些入口。本研究**没有证明这些独立入口存在任意进程执行绕过**，不据此扩围，也不声称已穷尽所有配置/插件的进程入口。
3. 继续遵守 [ADR 0024](../adr/0024-codex-per-turn-host-bridge.md) 的 `read-only` 与配置不回写约束；本文增加的是原生 shell 工具不注册的依据，不放宽写权限。Windows sandbox、ACL/UAC、跨目录隔离及任何 enabling 方案都不是本切片的验收或已解决项。

## 验证范围与限制

- [默认关闭回归](../../tests/test_agent_terminal_default_off_464.py) 覆盖首轮/显式 ID 续轮、有无 MCP、有无 effort、Pi 新会话/已有会话/固定 ID，以及 CLI 不可用时不回退 shell。既有 #424 严格 argv 合同只新增同一 `-c` 配置对，不放宽相等断言。
- **真实 Pi SDK 1.5.1**：用生产 adapter 导出的首轮/固定 ID argv，经真实 parser、扩展 loader 和内存 session registry/activation 核验。即使 settings 默认含 bash/codemode，registered、active、callable 三份列表均不含进程工具，尝试重新激活也不生效；业务工具及受限 read/write/edit 保留。两个 argv 形态分别创建内存 session，**不是原生历史恢复或 CLI RPC 准入回执**。
- **真实 Codex CLI 0.162.0**：只运行 version、features list 和首轮/续轮 `--help` 解析。私有 home 固定 `read-only`、`shell_tool=true` 作控制；生产 false override 使原生解析值变为 false，控制文件未被改写。**未执行 exec 轮次，未采集模型实际工具 schema 或工具调用轨迹**；该注册路径的结论还依赖上面的钉版源码。
- 原生检查的 home、runtime、projects 和临时目录均在独立 QA 根；未读取用户 Codex home/认证、发起模型请求、开放终端、配置 ACL/UAC/Windows sandbox 或修改 Percho。真实模型行为和 OS 隔离仍为 **NOT_RUN**，不把 argv、feature 回显或 SDK registry 证据扩张为这些结论。

[scope]: https://github.com/miaomiaozii/Firefly-Canvas/issues/464#issuecomment-7069344553
[tag-ref]: https://api.github.com/repos/openai/codex/git/ref/tags/rust-v0.162.0
[tag-object]: https://api.github.com/repos/openai/codex/git/tags/1f3f93473394b620b35580859b7e6864f7a9f948
[features]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/features/src/lib.rs
[spec-plan]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/core/src/tools/spec_plan.rs
[exec-command]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs
[config-override]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/utils/cli/src/config_override.rs
[loader]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/config/src/loader/mod.rs
[merge]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/config/src/merge.rs
[cli-main]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/cli/src/main.rs
[exec-main]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/exec/src/lib.rs
[exec-cli]: https://raw.githubusercontent.com/openai/codex/c1382380de69521303b416720a52f42d51af6248/codex-rs/exec/src/cli.rs
