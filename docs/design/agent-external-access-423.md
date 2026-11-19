# #423：Agent 外部目录访问权限——实测与待审核方案

状态：下方三档方案已经被后续裁定取代，仅保留作调查记录。当前按未验证的退路交付：Codex 保持 `-s read-only`，全局设置只展示只读状态和工作目录写入未验证，Pi 标为不支持；不提供权限切换。权限存储保留 `agent_file_access: read-only`，旧配置在保存时迁移，不改变 Agent 启动参数。

以下结果是本机 Windows、`codex-cli 0.162.0`、隔离目录下的观察，不能推广为其他版本或平台的保证。

## 实测

外部样本目录为 `D:/ff-qa/403-423/codex/external`，工作目录为 `D:/ff-qa/403-423/codex/work`，样本文件只含 `isolated-sentinel-423`。三个 `codex exec` 用 `--ignore-user-config --ephemeral --skip-git-repo-check --json` 启动；提示模型只操作样本文件，不请求放权，不重试。原始 stdout/stderr 的 tee：

| 设置 | 原始记录 | 观察 |
| --- | --- | --- |
| `-s read-only` | `D:/ff-qa/403-423/codex/logs/read-only.log` | 命令被 `blocked by policy` 拒绝，PowerShell 未启动，无法判断读写。 |
| `-s read-only -c 'sandbox_permissions=["disk-full-read-access"]'` | `D:/ff-qa/403-423/codex/logs/read-only-disk-full-read.log` | `sandbox_permissions` 报 unrecognized 并被忽略；读写命令均被策略拒绝、未执行。 |
| `-s workspace-write --add-dir D:/ff-qa/403-423/codex/external` | `D:/ff-qa/403-423/codex/logs/workspace-write-add-dir.log` | 读写命令均被策略拒绝、未执行；不能据此宣称 `--add-dir` 生效或失效。 |

补充：直接运行 `codex sandbox` 而非通过模型发起工具调用，在相同测试目录和本机设置下，`read-only` 和 `workspace-write` 都读到了外部 sentinel；两者对未列为可写根的外部目录写入均返回 `UnauthorizedAccessException`。在 `workspace-write` 的 `sandbox_workspace_write.writable_roots` 中加入该目录后，直接运行的读取和写入均成功。原始输出依次见：

- `D:/ff-qa/403-423/codex/logs/direct-read-only-read.log`
- `D:/ff-qa/403-423/codex/logs/direct-read-only-write.log`
- `D:/ff-qa/403-423/codex/logs/direct-workspace-write-read.log`
- `D:/ff-qa/403-423/codex/logs/direct-workspace-write-write.log`
- `D:/ff-qa/403-423/codex/logs/direct-workspace-added-root.log`

直接沙盒测试不是 `codex exec --add-dir` 的端到端验证。尤其不能将 help 中的配置示例当作当前版本有效；也不能据此声称关闭状态已经阻止外部读取。若要确认三档真实工具调用效果，需要先找出命令被策略拒绝的原因，在不修改用户配置、不放宽既有安全边界的前提下，用隔离账号或受控配置重测。

## 方案（待审）

1. 全局设置增加「Agent 外部文件访问」：**关闭（默认）／只读／读写指定目录**。存放于全局配置，不写入项目，复制项目不携带授权。兼容现存配置和保存主题的路径，避免当前 `save_global_preferences` 覆盖新增字段。迁移应有测试。
2. 从关闭切换到开放权限、添加可写目录时显示中文风险提示，经用户确认才保存；说明外部资料可能被 Agent 读取、指定目录可能被修改，权限会影响后续 Agent 会话。界面显示当前权限和真实适配器支持情况，不支持时明确提示「该 Agent 不支持外部访问」，不得静默降级后声称已授权。
3. 新会话和重启会话时由服务端按设置映射 CLI 参数，原会话使用何时生效须在审核中确定；目录仅接受用户明确选择的有效绝对目录，并对路径规范化及符号链接/重解析点做安全审查。不可使用 `danger-full-access` 冒充只读或限定目录。鉴于本机 `disk-full-read-access` 被忽略及本次端到端测试受阻，Codex 具体映射先保持未定，不在证据不足时实现或承诺隔离范围。
4. Agent 节点头部或输入区显示权限标记，注明授权的是全局设置而非单项目；切换显示要以实际生效值为准。UI 不以隐藏开关替代服务端约束。
5. 画布写入仍走现有 MCP 确认闸，`SAFETY_FLOOR_TOOLS` 不变；文件系统授权不能绕过这些检查。逐适配器定义能力映射，未证明支持时禁用相应选项并说明原因。
6. 后续验收：每种支持的 CLI 在隔离实例、不同档位分别验证工作区和外部目录的读/写、目录外写拒绝、路径别名、重启和切换生效、设置持久化/项目复制、确认闸、UI 风险提示；保留命令、原始输出和版本。Windows 实测之外的平台各自验证，不推断行为。
