# #326：Codex 启动调查（零付费）

基线：`issue/ui-404-405-326`，`1ddfb1c`。依据 reviewer 01a1ef85 在共享 `MESSAGES.md` L3406–3409 的裁定，只读历史 turn 记录及离线无推理调查；不实施优化、不改 ADR、不关闭票。

**所有新模型 turn 测量：未测（按用户要求零付费）。历史数据不是受控的新比较。** 未复制认证文件、访问用户实例/项目或调用图像/视频服务。外部证据根目录：`D:/ff-qa/404-405-326/326/`，入口 `README.md`；原日志不整份复制，过滤件不含消息正文、工具返回图片或凭据。

## 1. 旧票数字不作本次基线

本次重新读取 `gh issue view 326 --repo miaomiaozii/Firefly-Canvas --json number,title,body,comments`：票面 1.3 / 8.1 / 48.3 秒是旧场景记录，不能当本树的分阶段测量；245 秒仍无可核验来源。2026-11-14 评论明确称旧数字过期。评论约 1 秒预热收益、[ADR 0027](../adr/0027-codex-cold-start-warmup.md) 的历史上界与网络归因，均是旧结论，不是此次重测所得，也未由此次证据证实。

## 2. 已有代码与计时边界

- [ADR 0024](../adr/0024-codex-per-turn-host-bridge.md) 保留每轮独立进程、显式 thread ID 续接、可终止和失败隔离；无声自动重发有投递风险。`app/services/agent_host.py:1366–1539` 每轮生成参数、写隔离 MCP 配置、Popen、送 stdin；`app/services/agent_adapters/codex_adapter.py:86–124` 构造 `exec` / `resume` argv。
- `app/services/agent_host.py:1140–1143` **先 spawn/送入，再持久化 user_prompt**；`app/services/agent_host.py:2197–2225` 的事件 `ts` 是宿主接收/持久化时的 `time.time()`，不是浏览器点击或原生事件产生时间。不能把 user_prompt 日志到终态当完整启动耗时。
- `app/services/agent_adapters/codex_adapter.py:157–194` 将 `thread.started` 映射为 `session_started`，`turn.started` 为 `agent_start/turn_start`，`turn.completed` 为终态；文本只在整条 item 完成后映射成 `text_delta`。**首 text_delta 不是模型首 token / TTFT**。
- `app/services/agent_adapters/codex_adapter.py:50–72` 设置 CODEX_HOME、写 MCP TOML；没有原生“配置加载完成”计时点。此次不调用其认证同步函数。
- `app/services/agent_host.py:79–99` 的预热仅运行 `--version`、按节点冷却；挂于 `app/services/agent_host.py:1666` 的退出处理。此次设 `FIREFLY_CODEX_WARMUP=off`，不把历史 warmup 的发起记录当完成记录或收益。
- 现有工具 `duration_ms` 来自 monotonic（`app/services/agent_host.py:887–900`）；`firefly_diagnostics.py:41–79` 的诊断事件/耗时字段不提供原生启动各阶段边界。未读用户运行日志。

## 3. 新读取的历史原始证据

来源均为已接受批次 `D:/ff-qa/352-447-429/`。只读 `live_backend.py`、`live352.py`、`live447.py` 和保存的 JSON/JSONL，**未运行这些历史驱动**。`live_backend.py` L33–45 在 `Popen.__init__` **返回之后**记录 wallclock、PID、argv；不是 launch 前时间戳。`live352.py` L103–141 的 `elapsed_s` 是 monotonic 驱动整段，含请求、轮询、固定等待及状态/画布回读，无可恢复的原始 monotonic 端点。

### 历史索引

以下 E/S 均相对该历史根；`r` 是文件数组或 JSONL **从 1 起的记录位置**，不是 seq。完整 SHA256、原始时间戳、seq、generation、过滤 argv 及每项差值的两端保存在 `historical-source-manifest.json` / `historical-filtered.json`。这里的哈希为前 12 位。

| 标识 | 文件 | SHA256 前缀 |
|---|---|---|
| E1 | `live352/a3/codex-a-events.json` | `faa85f8de544` |
| E2 | `live352/a3/codex-b-events.json` | `64c638ec3d70` |
| E3 | `live447/a2/events.json` | `43b1a477624e` |
| S1 | `live352/a3/spawn.jsonl` | `ca4c478ac5ac` |
| S2 | `live447/a2/spawn.jsonl` | `99026989a840` |

### 可重建的区间（秒，wallclock 差，显示至毫秒）

“首映射”只指 `session_started`；“首文本”指完成的文本 item。表中各 E 端点顺序为 **user_prompt / 首映射 / 首文本 / agent_end**；S 为 post-Popen 记录。

| 历史场景 | 原始记录端点 | user_prompt→end | post-Popen→首映射 | post-Popen→首文本 | post-Popen→end |
|---|---|---:|---:|---:|---:|
| #352 首轮 exec | E1 r1/3/6/12；S1 r2，PID 34104 | 17.348 | 3.204 | 10.391 | 17.349 |
| #352 显式 resume | E2 r3/4/7/13；S1 r4，PID 31180 | 12.347 | 0.531 | 6.969 | 12.348 |
| #447 medium 首轮 exec | E3 r1/3/6/9；S2 r3，PID 96276 | 10.622 | 2.839 | 10.410 | 10.623 |
| #447 xhigh resume | E3 r10/11/14/17；S2 r6，PID 117644 | 7.229 | 0.443 | 6.986 | 7.230 |
| #447 刷新后 xhigh resume | E3 r18/19/22/25；S2 r8，PID 44580 | 5.963 | 0.505 | 5.922 | 5.964 |

#352 provenance commit 为 `1ec5e3107df2dc94e4d9b8587ba9b2144c9f5099`；#447 为 `d39c46dcbd91845cba20549e42562baddd313a58`，均取各自 `isolation.json`。原 argv 的 launcher 是显式 `C:/Program Files/nodejs/node.EXE` 加 npm 安装内的 `@openai/codex/bin/codex.js`；模型 `gpt-6.1-sol`。#352 是只读视觉工具场景，#447 是无工具回复/设置/刷新场景；effort 与上下文不同，不能合并成冷/热 A/B。元数据探测、预热也可能影响缓存；首轮不等于 OS 真冷启动。历史未保存二进制哈希，不能由当前版本反推历史二进制身份。

未隐藏缺口：

- `live352/a2/codex-a-events.json`（`020b2b507a7d`）r1–7 含 error、没有 agent_end；`codex-a-result.json`（`4766f59abe47`）`/elapsed_s=15.969`、done/passed=false，不计作完整 turn。
- `live447/a1/events.json`（`ff031c762a01`）事件数组为空，不能计时。
- #352 a3 两个 result 的 `/elapsed_s` 分别为 18.110 / 17.625 秒（哈希 `98ba0fa667da` / `18c829b82241`）；属于驱动口径，不与表内 wallclock 差相减来拆阶段。
- `447/*send.receipt.json` 有浏览器驱动 elapsedMs，但没有可配对的 native 起止；medium 驱动还标 failed。过滤件保留这些事实，不把自动化任务耗时、超时或 succeeded 标记冒充模型耗时/成功率。

## 4. 本次离线测量

最终完整执行为 `offline-run04/`：固定路径、同一已安装 `codex-cli 0.162.0`，原生 exe 与历史 receipt 同路径的 Node+JS wrapper 各测 `--version`、`--help`，每组有限重复。`identity.json` 保存 Node/JS/package/native exe 的路径、SHA256 和版本；不执行 PATH fallback。原生路径为 npm 包内 `node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe`，从已读取的 launcher 源码解析规则确认。

每条命令新建空 home/config，预先设置 runtime/projects、HOME/USERPROFILE、APPDATA/LOCALAPPDATA、TEMP/TMP、Pi/Codex 私有根；环境按白名单重建，不带凭据。无 prompt、exec、resume、app-server 或认证操作。Python audit 对非精确允许 argv 和网络连接 fail-closed；应用协议阶段不允许任何子进程。两项拒绝自检通过。原生 help/version 子进程不在 Python socket audit 内：私有无认证环境、拒绝代理是附加保护，不宣称操作系统级出站沙箱。

| 测量范围 | n | 最小 / 中位 / 最大（ms） |
|---|---:|---|
| 原生 exe `--version` | 5 | 334.583 / 357.772 / 365.647 |
| 原生 exe `--help` | 5 | 326.157 / 354.770 / 360.271 |
| receipt wrapper `--version` | 5 | 474.551 / 499.974 / 521.127 |
| receipt wrapper `--help` | 5 | 487.272 / 503.219 / 513.337 |
| 实际应用 ASGI MCP initialize | 5 | 1.393 / 1.771 / 59.563 |
| 实际应用 ASGI MCP tools/list | 5 | 1.808 / 1.845 / 2.128 |

计时为 `perf_counter_ns` 启止，不与历史 wallclock 相混。CLI 证据 `offline-run04/commands.json`（`efef93e8cb5e`）按执行序号分组：version/native r1,5,9,13,17；help/native r2,6,10,14,18；version/wrapper r3,7,11,15,19；help/wrapper r4,8,12,16,20。每条另有 stdout/stderr 原文、字节数、exit=0、实际 argv/根目录/原始计时；Popen 返回耗时也单列，但不是 ready 边界。

协议证据 `offline-run04/asgi-protocol.json`（`7810b72f1f80`）：initialize r1,4,7,10,13；tools/list r3,6,9,12,15；其间为 initialized 通知（202）。初始化/列表均 200，工具数来自真实端点。执行 `httpx.ASGITransport(main.app)`，调用实际 JSON-RPC 初始化、会话头和工具列表（`app/routers/api_mcp.py:652–678`、`app/routers/api_mcp.py:852–883`），**无 TCP 应用监听、无 lifespan、无原生宿主**。3195 仅为 ASGI 基址；Windows asyncio 内部短暂 loopback socketpair 是单独记录并随 loop 关闭的 IPC，不是 MCP 服务。首个 initialize 的大样本保留，未拆分归因。

另一次 `main` import 为 804.285ms（`offline-run04/asgi-import.json`，`f9657269cccf`，`/elapsed_ms`），包含 Python 导入及空运行时配置装配（`main.py:1296–1337`），**不是 Codex 配置阶段**。未做 TOML 微基准。所有 OS 缓存未清空；help/version 可能完全绕过原生配置、MCP 和会话初始化，不能代替 exec 启动，也不能据此给首轮上界或预热收益。原生 MCP 握手、配置阶段、网络等待、模型 TTFT：**未测**。

外部 README 保留探针调试失败及清理记录：早期 Windows audit 参数形状和 asyncio self-pipe 被拒，未以失败运行充当协议通过；其已成功的 CLI 原始样本也未删除。

## 5. 证据与假设、后续候选

**证据**：这些历史事件区间真实存在；首轮与后轮的 post-Popen→首映射不同；离线 help/version 的 launcher 范围小于上述历史整段；实际应用初始化/工具列表可在不启动宿主下完成。**不能推出**：差额属于配置、MCP、网络、thread 初始化或推理；不能把首映射或整条文本 item 当 TTFT；不能由少量异质样本认定根因、统计上界或预热因果收益。

仅提出，不实施：

1. 未来获准后补齐 launch-before/after、stdin 完成、原生事件接收、退出及 MCP initialize/list 边界，统一时钟、关联 PID/generation；需控制正文/凭据泄露与测量开销。模型实验另等费用授权。
2. 调查可失效的 CLI 路径解析/静态工具元数据缓存、launcher 开销；需守住安装更新、宿主切换和能力真实性，不凭本次 wrapper/exe 差异直接替换产品启动链。
3. 对现有 `--version` 预热按明确空闲策略做将来的对照；额外进程/I/O、缓存失效及调度竞争有代价，本次没有收益数据，不改默认。
4. 常驻宿主、提前启动执行进程、跨轮复用执行 thread/协议连接，改变 ADR 0024 的进程生命周期与失败隔离（区别于已支持的显式 ID 会话 resume），须**另行 ADR 讨论/批准**，不能借性能调查直接实现。ADR 0027 的上界/归因若需修订也须独立审查，本次原文不动。

清理：全部新建私有工作根已删除；未创建项目、未启动应用监听/模型宿主；仅等待自己启动的 help/version 进程正常退出，未暂停/停止其他进程。完整性与仓库文档检查见外部 `verification.json`；整批测试另行记录，不以局部文档核验代替全量。本次不宣称 #326 已关闭或模型性能验收通过。
