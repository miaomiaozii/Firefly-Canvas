# #476｜Pi SDK 切换研究：上游发行、fork 备选与验收门

核查时间：2026-11-27 UTC。Firefly 基线：[52ee9463804e343ec29ec862ccf5f0c120397035][ff]，工作树 `D:/ff-wt/a-476-sdk-switch-research`。下文 Firefly 相对路径均以该目录为根；fork 源码本地根为 `D:/ff-wt/pi-firefly-rpc`，只读。**这是切换前研究，不是版本变更、部署回执或关票依据。**

## 结论

- **官方已有修复发行版。** `earendil-works/pi` 首个包含该修复的正式发行是 **v1.6.0**；**v1.6.1** 也含修复，且是本次观测到的官方最新正式发行和 npm `latest`。官方 npm 包是 **`@earendil-works/pi-coding-agent`**，不是 Firefly 报错中建议的 `@earendil-works/pi`，也不是 `pi-stable`。已核对 tag 祖先、发行对象、npm 实物与其来源声明。
- **等价仅限原生 RPC 生命周期。** 上游 [#12230][pr] 覆盖 fork [8d7ad63f2][switchfix] 的一次 `switch_session` → 一次 `session_start(resume)`，还修复 #479/#480/#481，并改变订阅时序。官方不带 fork 的 `pi-stable` 扩展别名、同样的视频输入/读取路径；不能直接替换现有 CLI 或把版本字符串改成 `1.6.1`。
- **候选有条件分流。** 若明确不需要 fork 专有能力，并批准扩展导入迁移，优先验收官方精确 **1.6.1**；若要保留 `video-input` 行为，或官方候选未达到已定兼容合同，验收 fork **`f20d22cd925555d5a821512dcf27275d5b1509e4`** 的独立产物。两者都必须用真实选中的 CLI 过下述门。两者都不合格就保留当前部署，不在宿主/UI 层按 reason/path 去重。按 [#476][i476] 及两条 [owner 评论][i476fix]、[分支评论][i476branch]，Firefly 实际切换并集成回归后才关票。

## 1. 现在究竟 pin 了什么

| 环节 | 固定源码和实际行为 |
| --- | --- |
| Firefly 安装/打包 | [README.md:18–34][ffreadme] 的桌面入口是「萤火画布」快捷方式，开发入口是 `run.bat` 或 Python 3.12 `python main.py`。[安装依赖.bat:52–85][ffinstall] 按 `requirements.lock` 装 Python 依赖（本地 wheels 优先），然后由 [`install-firefly-shortcuts.ps1`][ffinstalllinks] 在桌面和开始菜单创建快捷方式；[tools/build_portable.py:24–42,96–112,146–176][ffportable] 打包应用、Python/依赖及 FFmpeg，剪掉 `node_modules`，不随包交付 Node/Pi；生成的 `启动Firefly.bat` 把便携 `env` 前置到 `PATH`，再用 bundled Python 跑 `main.py`，Pi 仍需独立提供。根 [package.json][ffpkg] / [package-lock.json][fflock] 是 Playwright/TS 开发依赖，没有 Pi SDK 锁；[CLI/README.md][ffcli] 的 Windows 安装脚本只列 Codex/Gemini。Pi 是外部先决条件，不是 Firefly 安装器自动安装的 1.5.1。 |
| 服务启动 → 子进程 | [run.bat:14–56][ffrun] 选择/检查 Python，再由 [tools/launch-firefly.ps1:95–108][fflaunch] 启动 `tools/run-server.py`；桌面路径 [`New-FireflyDesktopLink`][fflink] → `wscript.exe` + [tools/open-firefly-desktop.vbs:1–13][ffvbs] → 隐藏 PowerShell [tools/firefly-desktop.ps1 `Start-FireflyDesktopService`][fftray] → [tools/FireflyDesktopProcess.cs:15,175–189][ffdesktop] 复制父环境并启动 `tools/run-server.py --desktop`。该 [Python 入口][ffserver] 最终运行 `main.py`。应在**启动服务的环境**设 CLI，不能假定修改终端或用户 PATH 会影响已运行的托盘/服务。 |
| 选择与启动 | [app/services/agent_host.py:142–223][ffresolve] 的 `resolve_pi_cli` 先返回非空 `FIREFLY_PI_CLI` 原值，否则 `shutil.which(PI_CLI or PI_CLI_FALLBACK_NAME)`（`PI_CLI` 导入时读取环境，通常为 `pi`）。无版本/包校验；未配置且 PATH 检索失败才返回 `None`。[`build_pi_launch_command`][ffbuildargv] 在 Windows 对 `.cmd/.bat` 调用 `resolve_npm_cmd_wrapper`，把已识别的 npm shim 或 `CALL "%~dp0…cmd" %*` 转发链变成 `[node.exe, 绝对脚本, …]`；优先 shim 同目录 `node.exe`，其次 `which("node")`；不支持的包装器拒绝，不走 `cmd /c`。其他入口按单个可执行文件透传；不能把 `node <js>` 当成一个环境变量值，也不能在 Windows 把裸 `.js` 当作 `.exe`。 |
| 启动参数与绑定 | [app/services/agent_adapters/pi_adapter.py:23–65][ffadapter] 的 `PiAdapter.build_argv` 用 `--mode rpc --no-extensions -e <canvas-tools.ts> --no-builtin-tools --tools canvas.*,generate.*,media.*,viewport.*,agent.*,plan.*,ops.*,chat.*,read,write,edit`、`--session-dir`、`--name`、模型/推理级别、`--system-prompt <file>`。优先 `--session-id`，兼容无明确绑定时的 `--continue`；[app/services/agent_host.py:2726–2752][ffbinding] 的 `_pi_session_binding` 优先已知 ID 对应的文件后缀，旧路径才选最新后缀。`spawn_session` [L706–881][ffspawn] 不允许新映射节点误发现旧会话；[`_pi_workspace_path`][ffroot] 校验 `$projects/<project>/agent_nodes/<node>/sessions`，将其同时设为 `cwd`、`--session-dir`、服务端覆盖的 `FIREFLY_AGENT_WORKSPACE_ROOT`；Popen UTF-8 stdio，`get_state` 就绪后读回 native ID/file，首次保存绑定。缺 CLI 的安装文案在 L795–798，既没固定版本，包名也不对应本次查到的官方包。 |
| 1.5.1 真正的闸 | [app/agent_extensions/canvas-tools.ts:412–425][ffext] 先注册业务工具，仅有根时加载文件扩展，捕获加载/注册异常写 stderr。[app/agent_extensions/session-file-tools.ts:1–7,55–63,86–121][fftools] 的 `registerSessionFileTools(pi)` 从 `pi-stable` 导入 `VERSION` 和三原生工厂：无根不注册；`VERSION !== "1.5.1"` 或工厂不为函数 → 拒绝文件工具，不用裸内置工具补位。读保留核对后绝对路径的原生媒体/流式后端；写/改以自定义 `operations` 在实际 I/O 复核，保护宿主控制文件。**`get_state` 成功并不证明 `read/write/edit` 已注册。** [tests/agent-session-file-tools-445.mjs:13–19][ff445] 的 `--sdk-entry <dist/index.js>` 也绑定 `1.5.1`，是合规测试入口，不是产品选 CLI 的机制。[CONTEXT.md:230][ffcontext] 明示升级须复验原生工厂、路径和媒体。 |

**本机取样不是产品部署证明。** `where.exe pi` 命中 `%APPDATA%/npm/pi.cmd`，内容转发到同目录 `pi-stable.cmd`，后者指向 `%APPDATA%/npm/node_modules/pi-stable/dist/bundle/cli.js`；该包目录 realpath 指向本机的 `pi-stable-video/packages/coding-agent` 源码目录，`package.json` 报 `@earendil-works/pi-coding-agent` / `1.6.1`，并非 npm 正版身份。公开 [npm `pi-stable` 元数据][npmstable] 的 `latest` 是 `0.83.8`、仓库是 `ranxianglei/pi-stable`、bin 名为 `pi-stable`；公开 registry 查询 `@earendil-works/pi` 返回 HTTP 404。fork 与官方 tarball 的薄入口 `dist/bundle/cli.js` 甚至有同一 SHA256 `e79626f2dd6f94aa45d30f3fa63cd84319a6eefcd150b353cfaf274366926774`：它只加载 `cli-runtime.js`（[构建脚本 L246–255][bundle]），**不能凭 shim 名、版本、`repository` 字段或单入口哈希认产物**。也未查询/启动当前部署的服务进程；真实选择须在验收时留证。

## 2. 官方发行和等价范围

[上游 PR #12230][pr] 于 2026-11-16 20:27:47Z 合入 `earendil-works/pi:main`，合并提交 [beffcde92dea5253fecefc052320e8c8e97082fb][upfix]。用 GitHub 公共 tags/releases API 解析 tag 到完整 SHA，并以该合并提交作 base 查询 Compare API。下表四行均有 GitHub 正式 Release（非 draft、非 prerelease）；npm 发布时间取公开 registry，UTC。

| 官方 tag / Release | tag 对应提交 | 相对修复提交的祖先结果 | `@earendil-works/pi-coding-agent` 发布 |
| --- | --- | --- | --- |
| [v1.5.1][rel151] | `dd30101cfc5b0609411dd80b6f93adbc14db14cf` | [behind 314][cmp151]，不含 | [1.5.1][npm151] · 11-09 23:45:13.428Z |
| [v1.5.2][rel152] | `b4afdddf44e059c67ccf5097e5317d913521b470` | [behind 292][cmp152]，不含 | [1.5.2][npm152] · 11-10 12:24:55.226Z |
| [v1.6.0][rel160] | `faac585884ae2c013a6b1400f3e9e77f10b34bac` | [ahead 76、behind 0][cmp160]，merge base 即修复提交 | [1.6.0][npm160] · 11-20 11:09:15.529Z |
| [v1.6.1][rel161] | `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe` | [ahead 191、behind 0][cmp161]，merge base 即修复提交 | [1.6.1][npm161] · 11-25 13:41:09.982Z |

- **包与源码闭环。** 官方 [README.md:25–43][officialinstall] 指向 `@earendil-works/pi-coding-agent`；[v1.6.1 `packages/coding-agent/package.json`][officialpkg] 和实际 npm tarball 都是该包名，`bin.pi = dist/bundle/cli.js`、Node `>=22.19.0`。[官方 `packages/coding-agent/src/config.ts:603–607`][officialversion] 的 `PACKAGE_NAME` / `VERSION` 读包 manifest，版本是自述而非来源证明。注册表版本条目未给 `gitHead`；本次解码四版 npm [SLSA 声明][att161]，`buildDefinition.externalParameters.workflow` 指向 `https://github.com/earendil-works/pi` 的 `.github/workflows/build-binaries.yml` 和对应 `refs/tags/v…`；`resolvedDependencies[].digest.gitCommit` 与上表四个 SHA 一一相符。v1.6.1 对应 [Actions run 45621597215][run161]，`push`、`success`、同一 tag/SHA；[固定发布工作流 L380–386][officialworkflow] 有 provenance 校验与 npm 发布步骤。
- **核过实际发布字节，不只看声明。** 只读下载 1.5.1/1.6.0/1.6.1 tarball 到内存，SHA512 同时匹配 registry `dist.integrity` 和声明 `subject` 的 digest，读取归档内 manifest、bin 和 `package/dist/modes/rpc/rpc-mode.js`，未安装/执行。1.5.1 的 JS 有四处 handler 二次绑定，1.6.0/1.6.1 只余 runtime 回调与首启绑定。候选 npm 1.6.1 SRI：`sha512-+VSb95N4PB+G7TLceg7wkbJT31BEeIBnUT9500g9a1rOsCx78CUw1YFGtLcRhS6jj8pdfm0fFwrakrMLu6lVAQ==`；本次**未**独立验 Sigstore 证书/透明日志签名。官方 GitHub 两个 1.6.x Release 另有源包、安装锁、`SHA256SUMS` 和 Windows x64/arm64 等二进制；这里只核了发行资产元数据，没验证独立 exe 实物。若走 npm，固定 [官方 install-lock][officiallock] 或独立 consumer lock（直接 pin 包版本不等于 lock 传递依赖）；若走 exe，另核平台、对应 release 校验和及真实 CLI 回归，不能与 npm 结果互代。
- **同一根因，不是重连/显示去重。** [8d7ad63f2][switchfix] 基于历史 fork `6b5ea86b7383582c3f3e1bf3e6a6a225a47b15e5`，只去掉 `switch_session` 成功处理分支中多余的 `await rebindSession()`，保留初始绑定、取消/失败语义。上游 [#12230 的 diff][upfix] 同时移除四个 handler 的多余绑定；[v1.6.0][rpc160] / [v1.6.1][rpc161] 的 `runRpcMode` 首启显式 bind，替换仅由 `setRebindSession` 回调绑定。`AgentSessionRuntime.finishSessionReplacement` 调该回调；`switchSession` preflight/旧会话 teardown 后创建 `reason: "resume"` 的新会话（[v1.6.1 runtime][runtime161]；本地 fork 同名文件 L206–242）。新进程用已保存 ID 恢复历史，和同进程发 `switch_session` 是两条不同路径：前者新进程只应有 `startup`，后者单次成功后累计 `startup,resume`。两次成功后 `startup,resume,resume` 是正确结果，必须按 RPC 逐次断言，不能看最终单个字符串判重。
- **上游还提前订阅。** `session.subscribe` 和 backpressure 订阅先于 `bindExtensions`，首启和替换时扩展追加的 `entry_appended` 可进入 stdout，可能早于相应 ACK；Firefly reader/事件持久化/前端投影需要验。官方 [固定版回归][reg161] 的五个用例断言 startup 追加事件、四种替换各一次 `session_start` 和一次对应 `entry_appended`；不等价于 Firefly 集成回执。官方 [与 fork 8d7ad63f2 的 Compare][cmpforktoofficial] 显示分叉（官方未包含该 fork 提交本身），等价来自 handler 实现而非 fork cherry-pick。

### 为什么官方不能直接代替 `video-input`

1. **扩展解析：** Firefly `app/agent_extensions/session-file-tools.ts:1–4` 是运行时 `pi-stable` 导入，`app/agent_extensions/canvas-tools.ts:1` 也是此类型 specifier，且业务扩展使用 `typebox`。官方 [loader `getAliases`][loader161]、[Bun/Node bundle `VIRTUAL_MODULES`][virtual161] 支持 `@earendil-works/pi-coding-agent`，没有 `pi-stable`；fork 同两表 [loader L126][loaderfork]、[virtual L51][virtualfork] 明确增加别名。即使更新 `VERSION` 判断，官方也不保证解析原样扩展；若碰巧解析到宿主环境里别的 `pi-stable`，工厂也可能来自**另一**运行时。官方路径要审核迁移到同进程的正牌导入，并验证两种分发形态，不能安装一个同名依赖来掩盖问题。
2. **视频与原生工具：** [官方 v1.6.1 ↔ fork 对比][cmpofficialfork] 显示 RPC `prompt/steer/follow_up` 的 `command.videos` 转交属于 fork；官方 [`createReadToolDefinition`][read161] 为文本/图片，fork [同工厂][readfork] 有视频 MIME 探测、50 MiB 上限、`VideoContent` 和不支持视频模型时的提示。[d6f9c5761][videofix] 还补回视频 MIME 分支，修 `packages/ai/src/api/openai-completions.ts` 的 `getCompat` 转传 `compat.videoPartFormat`（`video_url`/`input_video`），以及 durable 图像处理保留视频 item。没验证这些行为就不能声称媒体/历史或供应商适配等价。当前 Firefly [agent_host.py:1207–1295][ffimages] prompt 主要发送 `images`，视频画布引用会跳过而不是自动进入 fork `videos` 形态；仍须评估**工作区原生 read**、已存视频历史及实际产品承诺，不以“能生成视频节点”推断需要或不需要视频输入。
3. **工具语义和安全边界：** 命令行同名 flag、工厂仍导出或 UI 起得来，都不能证明 `--tools` 仍过滤实际注册/激活/调用、工厂 `operations` 替换/路径回退、图片缩放/文本流/abort 等与 1.5.1 合同一致。若改变扩展、加载器或发布形式，按下面矩阵重测。不扩大权限，不绕过受限工具，不把原有的稳定路径检查宣称为并发重解析点/OS sandbox。

## 3. fork 备选：构建、固定、部署与回退（均未执行）

**来源。** `miaomiaozii/pi` 是 `earendil-works/pi` 的 fork。GitHub 公共 refs 核得 `video-input` → `d6f9c57610c3cc721ce369292751db335e308a55` 同时包含 [8d7ad63f2][forkancestry] 和 [beffcde92][upancestry]；[`fix/firefly-sdk-rpc-lifecycle`][forkbranch] → `f20d22cd925555d5a821512dcf27275d5b1509e4`，相对 `video-input` [ahead 2、behind 0][forktwo]。顺序为 [a0792b927401ebcafec378438f88bd4fe2b46a52][p2fix] → [f20d22cd925555d5a821512dcf27275d5b1509e4][forkfix]。前者独立修 p2 `apps/p2/src/web/transcript.ts` 的 `imagesOf(readonly UserContent[])` 类型窄化（只选图片，视频不显示；基线两处 TS2345），后者只改 README、CI/发行 npm 和 `packages/coding-agent/test/rpc-prompt-response-semantics.test.ts` 夹具，未改生产 RPC 或依赖锁。#483 [发布确认][i483pushed] 已说明 push 到新分支，并非改 `video-input`；fork 公共 Releases API 返回 `[]`。本地 `D:/ff-wt/pi-firefly-rpc` 的 HEAD 分支及共享 packed ref 同 SHA。其 manifest 也写 `1.6.1` / `@earendil-works/pi-coding-agent` / 上游 repository，**不能据此认作官方 npm，不能向官方包名发布，不能 pin 浮动分支**。

1. **独立构建。** 另开隔离构建目录，固定完整 fork SHA/来源，记录 clean 源树、`package-lock.json` SHA256（本次只读样本 `f05391f49b31146692ff2ac76239d78a52fe72d0d694a1624e8580a6ea8fec41`）、Windows 架构、Node 与 npm 版本。历史同线使用 Node `24.14.1` / npm `11.17.0`；后者为 #483 源码工具链首个识别 `.npmrc` `min-release-age=2` / `min-release-age-exclude[]=@earendil-works/*` 例外的版本，不是已安装 CLI 的运行条件。按 [fork README][forkreadme]、[根 package.json][forkpkg] 用锁 `npm ci --ignore-scripts`（离线前需备齐并核验缓存），固定且预水合公共模型**目录数据**（不是模型调用）；归档并核所有 catalog 文件及 `.manifest.json`，不要依赖在线 `latest`。`packages/ai/package.json:72–77` 的 `build:offline` 会 `check:model-data`，无匹配目录则停。历史输入可参考 `D:/ff-qa/pi-firefly-rpc/catalog.json` 的 44 个文件散列（含 manifest），以及 fork [目录 revision pin][catalogpin]，不能用两者的存在代替新构建的输入核验。
2. **产物。** 根 `npm run build:offline` 走整个工作区；入口是 `packages/coding-agent/dist/bundle/cli.js`，还必须有其 `cli-runtime.js`、chunks、workers/assets 和所需外部依赖。候选部署可用独立 staging 的完整本地包集：`npm run pack:packages -- --offline-model-data --out <隔离目录>`；用 [scripts/use-local-packages.mjs][uselocal] 将 manifest 中的 `@earendil-works/pi-coding-agent` 及 Pi siblings 绑定到 consumer 的内容寻址 `file:` + overrides，再锁 consumer、复查解析树。不要只打包 CLI、让 `@earendil-works/pi-ai` 等相同 semver 误落官方包。保存 manifest、锁、完整 tarball SHA256/512 和入口/运行时哈希、依赖树、工具链/catalog、构建/测试收据；用此组合标识 `fork@f20d22cd…`，非 `VERSION=1.6.1`。不使用共享 SDK worktree/VCS 管理目录或全局 npm 目录当发布路径。
3. **显式切换。** 并排保留已验 1.5.1 与单一候选，配置服务进程 `FIREFLY_PI_CLI=<绝对隔离入口>`。使用核准的 npm 生成 shim（确认能被当前解析器直启且 Node 也固定）或另外已验的独立 exe；不要用普通批脚本拼命令、改全局 PATH、或把目录/多参数值传进去。受限工具的确切版本、工厂/导入和启动自检改动如需做，须另开审查，**与 CLI 路径、包来源和锁一起交付**；不允许放宽为任意 `1.x`、落回裸工具或两套代码同 semver 静默混搭。负例：同版本错误产物、未知版本、未找到 shim/Node 或扩展加载错，必须拒收；必要时补显式产品 readiness 错误，不能仅靠 `get_state`。
4. **回退。** 切换前记录原 Firefly 修订、guard/导入、服务启动环境与实际旧 argv、1.5.1 完整产物/哈希、每节点绑定/分支映射和 JSONL 快照（保留原件）；先排空 busy 轮和队列，按正常生命周期停止旧树，再在隔离 canary 试。出错停候选并确认 reader/子树退出，**配对恢复**旧 Firefly/guard 和精确旧 CLI/Node/依赖及启动环境，重启后读回绑定、历史和工具。别只回 `PATH` 或只回版本字符串。用副本提前演练 `1.5.1 → 候选 → 1.5.1` 的读取、分支和媒体；若格式/视频条目逆向不兼容，保留候选新增数据，按备份明确恢复兼容副本和绑定，不静默截断或覆盖用户新轮。恢复旧版也会恢复 #476 已知缺陷，不应据回退关票。

**历史证据，不是本轮复跑。** `D:/ff-qa/a-476-resume-notice/sdk-native-{switch,new-session,fork,clone}-red-01/result.json` 的历史 1.5.1 fork 单次操作只在 `exact_lifecycle` 红；仅修 switch 的 `sdk-native-switch-candidate-01/result.json` 得到精确 `startup,resume`，当时是历史候选源码而非最终 freeze。旧 Firefly `fda070e072ea3f9536ae268a02aa7b8bab4c26b8` 的 `D:/ff-qa/a-476-resume-notice/s3-product-445-candidate-02/result.json` 为 **142 pass / 0 fail / 0 unavailable**；`initial-process/provider.jsonl` 与 `restarted-process/provider.jsonl` 分别是 `startup,resume`、`startup`，真实产物 + 生产扩展 + 离线确定性 provider，含文件权限与明确绑定；本地夹具 `D:/ff-qa/a-476-resume-notice/s3-candidate/native_445.py:115–127,321–388` **直接 Popen SDK**，不是当前 Firefly HTTP/WS/真实部署 launcher。

`D:/ff-qa/pi-firefly-rpc/DELIVERY.md`、`D:/ff-qa/pi-firefly-rpc/DELIVERY-BLOCKED.md`（后者仅历史阻塞）、`D:/ff-qa/pi-firefly-rpc/build-base/started.json` 和 `D:/ff-qa/pi-firefly-rpc/final-native-*/result.json`：**d6f9c57** 实际 `npm.cmd run build:offline` / bundled CLI 原生矩阵 **12/12**，包括同/不同 ID、重复操作、取消/失败、工具/后续 prompt、严格 JSONL 和清理，无生产 Firefly 扩展，不是 f20 新 bundle 的证明。最终 **f20d22cd9** 的收据 `D:/ff-qa/pi-firefly-rpc/listeners-post-p2/`、`D:/ff-qa/pi-firefly-rpc/rpc-all-post-p2/` 分别 **25 passed**、**36 passed / 18 skipped**，stderr 0 字节；p2 check exit 0、transcript 5 passed（需要 Chromium 的 suite 有跳过）。#483 [issue][i483] / [交付评论][i483pushed]：npm Unknown project/env 配置 **7+6 → 0**，RPC 夹具精确移除自己注册的 signal/stdin listeners，MaxListeners **11 → 0**，未静音、未调大上限；这是源码工具链/测试治理，不是生产生命周期新修复。

## 4. 后续端到端验收矩阵（本轮均未执行）

同一固定 Firefly 基线先做身份可证的 **A：部署旧 1.5.1** 原样对照。预期单次 `switch_session` 重复 `resume`；保存对照原始结果。若实际旧部署未复现，先核对产物身份、触发路径和记录探针，再单列历史 fork RED 作复现对照，不能把未知来源 `1.5.1` 当成旧部署。随后 **B：官方精确 1.6.1 + 经审查的导入/guard**、**C：fork 精确 f20d22cd9 + 经审查的 guard**，按选择的发布形式分别跑。候选各用私有 HOME/agent dir、runtime/projects/temp、loopback 端口、无真实凭据，明确仅离线确定性 provider（含 fail-on-network 守卫）；不把 QA 额外 provider/记录扩展和 `--offline` 写进生产常规 argv。记录未跑/跳过，不计 PASS。测试路径均为固定基线中的已有起点，不能用 fake/stub/Python mock 证明真实 CLI。

| 层/入口 | 必须观察的严格 oracle 与主要风险 |
| --- | --- |
| 安装与启动身份 | 在干净 Windows 用户/部署环境核「萤火画布」桌面快捷方式、支持的开发启动路径与便携入口（若分发便携包），使用独立 npm prefix 的绝对 shim；若选择 exe，再单独验证该形态。记父进程传入的 `FIREFLY_PI_CLI`、真实 `[node.exe, script, …]`/exe、架构、包/完整产物散列、cwd/env 键名（不记秘密）。模拟 PATH 上更早的 `pi-stable`、缺 CLI、坏 shim、错版本、无根；保持可诊断、无错误回退。参照 `tests/test_agent_cli_resolution.py`、`tests/test_agent_spawn_argv_guard_343.py`；另跑真实 Node/launcher，不只 mock Popen。 |
| 原生 #476 `switch_session` | 用 bundled CLI + 生命周期记录扩展，先无 switch 精确 `startup`；成功一次为 `startup,resume`，同/不同 ID、连续两次成功逐操作只一次（累计 `startup,resume,resume`），取消/坏 path/不可用 cwd 在该操作无额外 start，原绑定不漂。核 `session_shutdown`/`previousSessionFile`、严格 JSONL、RPC 请求 `id`/ACK/cancel、`get_state`、native JSONL header、历史、切换后 prompt+工具结果；首启/替换的 `entry_appended` 只进一次、顺序可追踪，不把它假定在 ACK 之后。回归不改为 `includes`、reason 去重、skip 或延长等待来洗绿。 |
| 原生相关票 [#479][i479] / [#480][i480] / [#481][i481] | 首次 `new_session` → `startup,new`；`fork`（`get_fork_messages` 的用户 `entryId`）/`clone`（当前 leaf）→ `startup,fork`；两次成功各累计 `startup,new,new` / `startup,fork,fork`。取消与可构造失败均只 `startup`（无效 fork entry、未保存/无 leaf clone；历史 new 无独立失败收据，额外能构造则测，不把未跑写 PASS）。核父会话、fork 裁剪/clone 保留到 leaf、原/新文件/ID 及后续工具；这三票已闭不豁免回归，也不要求给 Firefly 新增 `new_session` / `clone` 路由。 |
| Firefly 真宿主 + HTTP/WS | 必须是实际 `AgentHostManager.spawn_session` + 所选 CLI + 生产 `canvas-tools.ts`：[`api_agent_nodes.py`][ffroutes] `/api/projects/{project_id}/agent-nodes/{node_id}/{prompt,fork,switch-branch,events,stream}`。首轮、续轮、stop/restart（固定 `--session-id`、放更新 mtime 诱饵）、分支 A→B→A、同一节点 WS 断线重连/历史重放；核真正的子进程/命令次数、WS/持久化不重复、ID/file/history/请求关联、无事件串代。`AgentHostTransaction` 的 [`native_branch` / `branch`][ffbranch] 要求 ACK + readback 后才 CAS，取消/ACK 错/ID path 不一致/并发/回退失败不虚假落盘；正常分支继承仍可后续对话。参照 `tests/test_agent_fork.py`、`tests/test_agent_host_settings_421_424.py`、`tests/test_agent_events.py`。 |
| #445/#464 扩展与能力 | 真实 loader、真实已激活且 provider 实际可调用的集合：业务命名空间 + 仅受限 `read/write/edit`，首启、替换、进程重启和 reload 一致。用确定性工具调用验证合法读写精改、图片 MIME/缩放、长文本偏移/截断/stream、abort，宿主管理的 prompt/JSONL 不可经工具修改；跨项目、父目录、稳定链接/junction/hardlink/Windows 别名拒绝，外部哨兵未变；shell/PowerShell/Codemode 不注册、不激活、不能调用。保持 #343/#445 边界；参考 `tests/agent-session-file-tools-445.mjs`、`tests/test_agent_workspace_445.py`、`tests/test_agent_terminal_default_off_464.py`，并复验业务 callback 的 loopback、节点绑定和确认语义。 |
| 媒体及存量兼容 | 两候选都须验证文本/图像输入与文件工具结果、历史回放/写读以及工具事件进 Firefly 的投影。产品若依赖视频：使用小型合成视频/视频历史验证 read→原生内容→fake provider、非视频模型提示/限制、`prompt/steer/follow_up` 传递及本地假 endpoint 的 `video_url`/`input_video` wire 形态；确认是否真走宿主现有路径，不能用 SDK 独立结果宣布 UI 支持。官方缺此能力若属必需即不准上线；非必需也须明确处理存量，不能默默降级成乱码文本、丢历史或扩大 I/O。 |
| 生命周期、退出与回退 | 平稳/超时/取消/故障时 stdout 纯 JSONL、UTF-8 不误杀、stderr 诊断归属、`agent_settled`/事件有序、reader 与子树正常回收 `stop_session` 无残留、无重复 provider/工具副作用；参考 `tests/test_agent_host_kill.py`。复验 #483 的贡献者检查与监听器夹具，不要求吞一般 stderr。以会话副本演练完整回退，复核旧工具和绑定；只在选定精确候选通过真实 Firefly 集成并保存对照收据后，才复审部署/关 #476。 |

## 核查方法与限度

只读 `read/grep`、`git status --short` / `git rev-parse HEAD`（主目录），并读取研究工树 `.git` 指向的 HEAD/ref 验证指向 [固定 Firefly 提交][ff]；关键本地源码与该 SHA 的 GitHub raw 内容逐字节（CRLF 归一）相符。`where.exe` 与文件 metadata/哈希只做 PATH 身份取样；Python `urllib` 对 GitHub REST/raw 和 npm registry 做匿名 GET，解析 tag/release/compare/commit、Issue 评论、provenance 与公开归档；历史 QA 只读。独立复核命令 `py -3.12 -B D:/ff-qa/a476-sdk-switch-research/verify-release.py` 退出 0，`D:/ff-qa/a476-sdk-switch-research/public-release-verification.json` 保存四版祖先结果和官方 1.6.1 tarball 的完整 SRI、包身份及 RPC 绑定点数。未安装、构建或运行 SDK，未启动 Firefly 服务、调用模型、读写凭据、push 或写入 Firefly main、SDK 工树；仓库只新增本报告。尚无最终候选整包的 Firefly E2E、生产实际解析路径或回退回执；先由维护方定视频/兼容合同及选哪个产物，再另行授权执行。

[ff]: https://github.com/miaomiaozii/Firefly-Canvas/commit/52ee9463804e343ec29ec862ccf5f0c120397035
[ffreadme]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/README.md#L18-L34
[ffinstall]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/%E5%AE%89%E8%A3%85%E4%BE%9D%E8%B5%96.bat#L52-L85
[ffinstalllinks]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/install-firefly-shortcuts.ps1#L6-L13
[ffportable]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/build_portable.py#L24-L176
[ffpkg]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/package.json
[fflock]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/package-lock.json
[ffcli]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/CLI/README.md
[ffrun]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/run.bat#L14-L56
[fflaunch]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/launch-firefly.ps1#L95-L108
[fflink]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/firefly-desktop-links.ps1#L16-L36
[ffvbs]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/open-firefly-desktop.vbs#L1-L13
[fftray]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/firefly-desktop.ps1#L54-L62
[ffdesktop]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/FireflyDesktopProcess.cs#L175-L189
[ffserver]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tools/run-server.py#L15-L30
[ffbuildargv]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_host.py#L339-L357
[ffresolve]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_host.py#L142-L223
[ffroot]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_host.py#L291-L304
[ffadapter]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_adapters/pi_adapter.py#L23-L65
[ffbinding]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_host.py#L2726-L2752
[ffspawn]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_host.py#L706-L881
[ffext]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/agent_extensions/canvas-tools.ts#L412-L425
[fftools]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/agent_extensions/session-file-tools.ts#L55-L121
[ff445]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/tests/agent-session-file-tools-445.mjs#L13-L19
[ffcontext]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/CONTEXT.md#L230
[ffimages]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_host.py#L1207-L1295
[ffroutes]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/routers/api_agent_nodes.py#L569-L816
[ffbranch]: https://github.com/miaomiaozii/Firefly-Canvas/blob/52ee9463804e343ec29ec862ccf5f0c120397035/app/services/agent_node_host_settings.py#L396-L520
[i476]: https://github.com/miaomiaozii/Firefly-Canvas/issues/476
[i476fix]: https://github.com/miaomiaozii/Firefly-Canvas/issues/476#issuecomment-7210975356
[i476branch]: https://github.com/miaomiaozii/Firefly-Canvas/issues/476#issuecomment-7211428346
[i479]: https://github.com/miaomiaozii/Firefly-Canvas/issues/479
[i480]: https://github.com/miaomiaozii/Firefly-Canvas/issues/480
[i481]: https://github.com/miaomiaozii/Firefly-Canvas/issues/481
[i483]: https://github.com/miaomiaozii/Firefly-Canvas/issues/483
[i483pushed]: https://github.com/miaomiaozii/Firefly-Canvas/issues/483#issuecomment-7211423879
[pr]: https://github.com/earendil-works/pi/pull/12230
[upfix]: https://github.com/earendil-works/pi/commit/beffcde92dea5253fecefc052320e8c8e97082fb
[rel151]: https://github.com/earendil-works/pi/releases/tag/v1.5.1
[rel152]: https://github.com/earendil-works/pi/releases/tag/v1.5.2
[rel160]: https://github.com/earendil-works/pi/releases/tag/v1.6.0
[rel161]: https://github.com/earendil-works/pi/releases/tag/v1.6.1
[cmp151]: https://api.github.com/repos/earendil-works/pi/compare/beffcde92dea5253fecefc052320e8c8e97082fb...dd30101cfc5b0609411dd80b6f93adbc14db14cf?per_page=1
[cmp152]: https://api.github.com/repos/earendil-works/pi/compare/beffcde92dea5253fecefc052320e8c8e97082fb...b4afdddf44e059c67ccf5097e5317d913521b470?per_page=1
[cmp160]: https://api.github.com/repos/earendil-works/pi/compare/beffcde92dea5253fecefc052320e8c8e97082fb...faac585884ae2c013a6b1400f3e9e77f10b34bac?per_page=1
[cmp161]: https://api.github.com/repos/earendil-works/pi/compare/beffcde92dea5253fecefc052320e8c8e97082fb...ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe?per_page=1
[npm151]: https://registry.npmjs.org/@earendil-works/pi-coding-agent/1.5.1
[npm152]: https://registry.npmjs.org/@earendil-works/pi-coding-agent/1.5.2
[npm160]: https://registry.npmjs.org/@earendil-works/pi-coding-agent/1.6.0
[npm161]: https://registry.npmjs.org/@earendil-works/pi-coding-agent/1.6.1
[npmstable]: https://registry.npmjs.org/pi-stable/0.83.8
[att161]: https://registry.npmjs.org/-/npm/v1/attestations/@earendil-works%2fpi-coding-agent@1.6.1
[run161]: https://github.com/earendil-works/pi/actions/runs/45621597215
[officialinstall]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/README.md#L25-L43
[officialpkg]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/package.json
[officialversion]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/src/config.ts#L603-L607
[officiallock]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/install-lock/package-lock.json
[officialworkflow]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/.github/workflows/build-binaries.yml#L380-L386
[rpc160]: https://github.com/earendil-works/pi/blob/faac585884ae2c013a6b1400f3e9e77f10b34bac/packages/coding-agent/src/modes/rpc/rpc-mode.ts#L318-L644
[rpc161]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/src/modes/rpc/rpc-mode.ts#L318-L643
[runtime161]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/src/core/agent-session-runtime.ts#L206-L242
[reg161]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/test/suite/regressions/10470-rpc-session-replacement-start-once.test.ts#L231-L262
[cmpforktoofficial]: https://api.github.com/repos/earendil-works/pi/compare/8d7ad63f280310a0f4e03831eda03c54b106abc6...ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe?per_page=1
[loader161]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/src/core/extensions/loader.ts#L72-L132
[virtual161]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/src/core/extensions/virtual-modules.ts#L15-L42
[read161]: https://github.com/earendil-works/pi/blob/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe/packages/coding-agent/src/core/tools/read.ts#L229-L317
[cmpofficialfork]: https://github.com/miaomiaozii/pi/compare/ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe...f20d22cd925555d5a821512dcf27275d5b1509e4
[switchfix]: https://github.com/miaomiaozii/pi/commit/8d7ad63f280310a0f4e03831eda03c54b106abc6
[videofix]: https://github.com/miaomiaozii/pi/commit/d6f9c57610c3cc721ce369292751db335e308a55
[forkancestry]: https://api.github.com/repos/miaomiaozii/pi/compare/8d7ad63f280310a0f4e03831eda03c54b106abc6...d6f9c57610c3cc721ce369292751db335e308a55?per_page=1
[upancestry]: https://api.github.com/repos/miaomiaozii/pi/compare/beffcde92dea5253fecefc052320e8c8e97082fb...d6f9c57610c3cc721ce369292751db335e308a55?per_page=1
[forkbranch]: https://api.github.com/repos/miaomiaozii/pi/git/ref/heads/fix/firefly-sdk-rpc-lifecycle
[forktwo]: https://github.com/miaomiaozii/pi/compare/d6f9c57610c3cc721ce369292751db335e308a55...f20d22cd925555d5a821512dcf27275d5b1509e4
[p2fix]: https://github.com/miaomiaozii/pi/commit/a0792b927401ebcafec378438f88bd4fe2b46a52
[forkfix]: https://github.com/miaomiaozii/pi/commit/f20d22cd925555d5a821512dcf27275d5b1509e4
[loaderfork]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/packages/coding-agent/src/core/extensions/loader.ts#L107-L137
[virtualfork]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/packages/coding-agent/src/core/extensions/virtual-modules.ts#L33-L52
[readfork]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/packages/coding-agent/src/core/tools/read.ts#L245-L332
[forkreadme]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/README.md#L102-L179
[forkpkg]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/package.json#L13-L33
[bundle]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/scripts/build-coding-agent-bundle.mjs#L246-L255
[catalogpin]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/nix/model-catalog.json
[uselocal]: https://github.com/miaomiaozii/pi/blob/f20d22cd925555d5a821512dcf27275d5b1509e4/scripts/use-local-packages.mjs
