# #444：Agent 外部文件读取隔离（桌面研究）

**结论：保留 #423 的写入合同，不宣称现有 Agent 已限制外部读取。** Firefly 目前以 Windows 本地运行为主。下一轮如获批准，先用同一组合成哨兵对照 **无宿主共享的 Windows VM（隔离基准）**和 **AppContainer / LPAC（原生兼容性候选）**。独立账号只作为降权对照，不作为严格模式。没有候选通过验证，就不提供该模式。

后续结果见 [Windows 合成读取隔离验证记录](444-agent-read-isolation-lab.md)：两种原生候选均在启动阶段受阻，VM 未运行。

本文依据 [#444 票面](https://github.com/miaomiaozii/Firefly-Canvas/issues/444)、`ea23602` 源码和文中链接的厂商/上游资料；不实施、不配置环境、不运行探针。#444 转述用户 2026-11-19 的 #423 裁定：权限档按「能写哪里」定义，真正阻止读取是独立议题。[#423 历史记录](../design/agent-external-access-423.md#历史实测423)与外部归档 `D:/ff-qa/403-423/BLOCKER-423.md` 记载：本机 Windows 的 Codex CLI 0.162.0 **直接**沙箱测试中，`read-only`、`workspace-write` 都读到了外部合成哨兵；前者拒绝工作目录写入，后者允许；当时未授权的外部写入被拒。模型发起的 `codex exec` 命令被 `blocked by policy` 拦下、没有执行，不能据此判断读取。旧归档里 Pi 未绑定 cwd 的观察不是当前行为。

## 1. 要守住什么

- **对象是 Agent 运行时和所有子孙进程**，不是只有模型可见的工具；包含启动器、解释器、辅助程序及首次/续接/重试路径。默认不读取或枚举未批准的宿主文件；只给服务端绑定的节点工作区和明确列出的运行依赖（例如客体系统、运行时、证书、专用临时目录），不授权整个用户目录、项目库或配置目录。工作区可写性仍由现有合同独立决定。
- **不只检查路径。** 未授权字节也不能经继承句柄、共享内存、远程共享、宿主 API 或代为启动的进程到达 Agent。用户明确提交的附件及已授权画布数据是单独的数据授权，不等于放行同目录或整个存储根。测试按明确的 OS 版本和配置范围验收；不保证抵抗宿主管理员、内核/虚拟化漏洞或所有侧信道，也不保证已经授权的数据不会被模型提供方收到。

## 2. 当前 Firefly：源码证据，不是 OS 隔离验收

| 路径（`ea23602`） | 已有边界与缺口 |
| --- | --- |
| Pi 原生文件：[启动宿主](../../app/services/agent_host.py) L634–638、L740–828，[Pi adapter](../../app/services/agent_adapters/pi_adapter.py) L24–65，[文件工具](../../app/agent_extensions/session-file-tools.ts) L11–115 | `cwd` 和 `FIREFLY_AGENT_WORKSPACE_ROOT` 绑定当前节点 `agent_nodes/{node_id}/sessions`；宿主用 stdio RPC，复制服务端环境。禁用 Pi 自带扩展加载及内建工具，仅显式加载 Firefly 扩展及 allowlist，不开放 shell/Codemode。SDK 1.5.1 限定 `read/write/edit`：检查根、稳定链接/目录联接、别名及普通文件，缺配置或不兼容时文件工具不裸回退；不保证并发重解析点替换，也不限制进程内任意其他 OS I/O。 |
| Codex 原生进程：[每轮启动](../../app/services/agent_host.py) L1543–1611，[Codex adapter](../../app/services/agent_adapters/codex_adapter.py) L22–69、L83–129 | 首轮/续轮显式 `-s read-only`、`-c features.shell_tool=false`；当前工作目录不是读取 allowlist。进程继承环境；默认 `CODEX_HOME` 在运行目录下，可能复制用户登录文件，显式 `CODEX_HOME` 优先。参考图 `-i` 直接传路径；切边界后不能继续假定进程能打开这些宿主路径。 |
| Firefly 工具桥：[Pi HTTP 调用](../../app/agent_extensions/canvas-tools.ts) L4–22，[Codex MCP URL](../../app/services/agent_host.py) L1543–1551，[MCP 分派](../../app/routers/api_mcp.py) L705–846 | Pi 扩展调用 REST，Codex 经 HTTP MCP。工具白名单、画布写确认和 hosted 活跃轮次约束是业务边界；`caller=hosted`、project/node、MCP 会话标识不是强身份。仅隔离 CLI 而仍允许它访问有宿主读取能力的任意 REST/MCP 路由，不满足 #444。 |
| 代读路径：[媒体导入及预览](../../app/routers/api_agent_tools.py) L2326–2451、L2768–2877，[会话附件归档](../../app/routers/api_agent_plan.py) L464–515 | `media.upload(file_path)` 由宿主读图，范围包括当前项目以及配置的输入/输出/上传目录，不是节点工作区；预览按项目素材记录取内容。`attachment_id` 路径核对提交记录、会话/代际，归档仍需确认。须逐项明确哪些内容能返回；归档确认不是读取隔离。 |
| 生命周期：[停止/回收](../../app/services/agent_host.py) L2195–2267 | Windows `taskkill /T /F` 和 POSIX 进程树回收/首进程退路是事后清理，不是启动前限制，也不证明所有后代已受同一读策略约束。 |

## 3. 方案矩阵：文档事实与工程判断

成本为相对判断，非本机测量；「候选」不表示已有可发布实现。

| 方案 | OS 边界与缺口（上游依据） | Firefly 取舍与成本 |
| --- | --- | --- |
| **Windows：专用非管理员 SID + NTFS ACL** | [DACL 检查](https://learn.microsoft.com/en-us/windows/win32/secauthz/how-dacls-control-access-to-an-object#dacls)看进程用户及启用的组 SID；给专用 SID 授权工作区，不撤销 `Users`/`Everyone` 等对外部对象的读授权。低完整性默认 [禁止向上写，不禁止向上读](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control#mandatory-policy)。 | 可隔开原用户的私有 ACL，适合作降权和对照；不能在普通宿主上充当全盘读取 allowlist，不建议重写宿主全盘 ACL。进程启动负担较低，但创建/轮换账号、目录授权、组成员和新目录巡检有管理成本。 |
| **Windows：AppContainer / LPAC** | [启动与 ACL 模型](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer#appcontainer-overview)：与宿主共享内核，可启动非打包进程；受保护对象要求常规用户/组权限与包/能力权限取交集。普通 AppContainer 默认可访问部分系统文件、注册表和 COM；LPAC 默认范围更窄，非「什么都读不到」。[能力隔离](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)不防应用显式交付数据/句柄。 | 原生候选，不需客体镜像。先盘点可读默认资源并对照依赖清单；核实 Node/Pi/Codex、加载器/证书、临时文件、所需 IPC 和子进程。兼容性不能靠挂整个用户目录或开启泛能力解决。需要维护启动器、ACL、能力清单及每个支持版本的回归；冷启动收益待测。 |
| **Windows：Windows Sandbox** | [独立内核、一次性实例](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)，不继承已装应用；文档不支持并发实例。[支持与许可](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/#windows-edition-and-licensing-requirements)包含 Pro/Enterprise/Education 等指定版本，不含 Home；另核对虚拟化条件。[配置](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file#configuration-options)默认开网络/剪贴板/音频输入；映射目录的只读标记默认关闭，主动设只读也仍暴露内容。 | 有合格机器和现成客体材料时，可作离线基准候选；明确关闭重定向，只给最小合成暂存数据。桌面会话、启动/关停、应用注入及状态恢复造成摩擦；不先假定适合作为无头、多 Agent 产品后端。 |
| **Windows：受管 VM（Windows 或 Linux 客体）** | 客体不直接看到未挂载的宿主盘，但共享目录、虚拟磁盘导入、设备及网络都是信任边界。Hyper-V 核对[客户端版本与硬件](https://learn.microsoft.com/en-us/virtualization/hyper-v-on-windows/reference/hyper-v-requirements#operating-system-requirements)，关闭 Windows 客体 [增强会话的驱动器/设备等重定向](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/enhanced-session-mode#share-drives-and-devices)。宿主功能可用不等于已获 Windows 客体权利，按实际渠道核对 [Microsoft 许可概览及条款入口](https://learn.microsoft.com/en-us/windows/whats-new/windows-licensing)。 | 跨平台都能研究「专用客体磁盘 + 最小数据通道」这一架构，而非同一运行包。Windows 客体可保留 Windows CLI 语义；Linux 客体需处理路径、二进制与附件传输。镜像构建/更新、管理员或 IT 介入、磁盘和内存、冷启动及撤销/销毁成本通常最高，仍须实测。 |
| **Windows：WSL2 上的 Linux 容器 / Docker Desktop** | [WSL 默认自动挂载固定盘并启用 Windows 程序互操作](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#automount-settings)（[互操作设置](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#interop-settings)）；裸 WSL 不合格。Linux 容器隔的是客体进程/可见挂载；[Desktop 后端及集成](https://docs.docker.com/desktop/features/wsl/#wsl-2-security-in-docker-desktop)、[宿主 bind mount](https://docs.docker.com/engine/storage/bind-mounts/#considerations-and-constraints)和管理接口必须另审。 | 仅用受控 Linux 镜像、无宿主盘/互操作/管理套接字、显式暂存数据才有资格验证。需测 Windows/Linux 路径和 I/O、后台 VM/引擎、镜像与更新；按[后端、Windows 版本/架构和虚拟化条件](https://docs.docker.com/desktop/setup/install/windows-install/#system-requirements)分别核实，不承诺覆盖 Home。Docker Desktop [订阅条件](https://docs.docker.com/subscription/desktop-license/)与 Engine/Moby 开源分发条款不同：文档列个人、教育、非商业 OSS 及小企业（<250 人且年收入 <1000 万美元）免费范围，较大机构的专业用途及政府等须付费；按用途核对。 |
| **Linux：命名空间 + Landlock / OCI 容器** | [挂载命名空间](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html#DESCRIPTION)起始复制挂载表，仅新建命名空间不会隐藏宿主根。需要裁剪挂载和传播范围，并约束用户、PID、网络/IPC、设备及权限。Landlock [规则](https://docs.kernel.org/userspace-api/landlock.html#landlock-rules)可限制文件读取、目录列举和执行，叠加并[继承给后代](https://docs.kernel.org/userspace-api/landlock.html#inheritance)，不撤销[已打开的文件描述符](https://docs.kernel.org/userspace-api/landlock.html#rights-associated-with-file-descriptors)。不能把上游文档所列 [ABI](https://docs.kernel.org/userspace-api/landlock.html#landlock-abi-versions) 当成已部署能力。 | 建议 Linux 时评估受控根文件系统 + Landlock，严格模式缺所需 ABI 即拒启；两层均须在工作负载前就绪。容器共享一个 Linux 内核；[rootless](https://docs.docker.com/engine/security/rootless/#how-it-works)降低引擎权限，不应交付[引擎管理套接字](https://docs.docker.com/engine/security/#docker-daemon-attack-surface)。通常无需启动独立 VM，但有内核/发行版差异、镜像更新、用户命名空间配置和运维成本。 |
| **macOS：备用账号 / App Sandbox** | [标准账号](https://support.apple.com/guide/mac-help/add-a-user-or-group-mchl3e281fc9/mac)是降权，共享和其他可读文件仍由原权限决定；[隐私控制](https://support.apple.com/guide/security/controlling-app-access-to-files-secddd1d86a6/web)针对受保护数据类别，不是任意文件 allowlist。[App Sandbox](https://developer.apple.com/documentation/xcode/configuring-the-macos-app-sandbox#Overview)是内核执行的应用 entitlement 模型，不代表现有原生 CLI 及全部 helper 已在该沙盒中。 | 备用账号不是严格候选；App Sandbox 如要保留 macOS 原生 CLI，需另审签名、用户选择授权、各 helper/子进程及服务调用，不能用低启动成本替代边界证据。 |
| **macOS：VM / Linux 容器** | Apple [Virtualization.framework](https://developer.apple.com/videos/play/wwdc2022/10002/)支持 Linux 客体、Apple silicon 上的 macOS 客体，也提供可选共享目录。[常规 Docker Desktop](https://docs.docker.com/desktop/features/networking/#overview)容器共享 Linux 客体内核；Apple [`container`](https://github.com/apple/container/blob/main/docs/technical-overview.md#how-does-container-run-my-container)每容器用轻量 Linux VM，仍须审挂载与网络；[支持条件](https://github.com/apple/container#requirements)为 Apple silicon、macOS 26+，项目 Apache-2.0 不等于客体/镜像授权。 | Linux 容器不能直接运行 macOS 原生 CLI。需要 macOS 客体时核对硬件、对应 [macOS SLA](https://www.apple.com/legal/sla/)及部署用途；Docker Desktop 许可另算。需要测启动、共享 I/O、镜像磁盘及并发开销；不从厂商性能描述推算本产品容量。 |

## 4. 无论选择哪一层，仍需单独验证的边界（提案）

1. **数据授权和文件桥。** 显式清单绑定项目、节点、当次会话代际和允许的操作；只传获授权的附件/素材、工作区内容或生成物，避免映射共享项目库。`-i` 与只接受宿主路径的能力需改为受控暂存或禁用。路径授权要落实到实际打开的对象：覆盖 junction/reparse point、symlink、hardlink、大小写与路径别名、挂载边界和检查/打开间的替换。不把字符串前缀、现有 `realpath` 预检或只读挂载当作保证；不能保证对象一致性时，不执行代读。
2. **凭证及 IPC。** 从最小环境和专用状态开始，禁止搬运用户登录态，排除密钥库、SSH agent、宿主会话以及已有的句柄/FD/套接字；只显式交付必要通道。Windows [句柄继承](https://learn.microsoft.com/en-us/windows/win32/sysinfo/handle-inheritance)可携带文件、映射、令牌等，不能靠更换 token 撤销。专用、可撤销的模型访问能力或受控代理另审；不把宿主凭证暴露给运行时。远端模型数据处理另有边界。
3. **网络及代读代理。** 默认无网络；需要上线时再独立评估最小模型出口及绑定身份的 Firefly 能力入口。不能直接暴露宿主 REST/MCP/UI、任意 URL 代理、容器/虚拟机控制接口、命名管道/COM 等；按操作和对象授权，不按调用方自报的 project/node。拒绝未批准 UNC/SMB（包括已映射盘）、本机及局域网文件服务；断开共享盘不等于封住网络和 IPC。保留画布确认闸，不能用它代替读鉴权。
4. **先约束后执行。** 全部本地子孙进程必须继承且不能放宽有效策略；服务/计划任务等代启动的进程须另有限权，否则不能开放。Windows [Job Object](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects#managing-processes)可辅助进程树归属/回收；要核实 breakaway 和外部代启动，不提供文件读限制。会话终止、崩溃或撤销后须回收工作负载并断开旧授权，不能只杀 CLI。

## 5. 后续验证与放行门槛

**先审批准入，再验证。** 先确定可用的 Windows 版本/架构、许可和既有测试材料；不满足则该候选未验证。获单独批准后，仅使用隔离环境和生成的唯一标记，先以离线固定读取探针确认 OS 边界，再验证同策略下 Pi/Codex 的启动、续轮/重试和受控 Firefly 桥。用本地假模型/假服务，不接真实登录态或用户文件。无共享 VM 是基准，标准账号是对照，AppContainer/LPAC 按实际依赖比较；Linux/macOS 在明确支持需求后另立同等验收，不由 Windows 外推。

| 合成哨兵组 | 必须观察到的结果 |
| --- | --- |
| 正对照：已授权工作区、运行依赖、明确提交的附件和已授权画布数据 | 固定读取、列表、必要运行和合法工具操作成功；按 #423/#445 核实原有写入与确认行为不变。宿主用测试身份确认外部哨兵确实存在且对照可读。 |
| 负对照：工作区外同级目录、模拟用户目录、模拟公共可读目录/另一项目、未授予的临时目录 | Agent、直接子进程及孙进程在新会话、续接、重启时都不返回外部标记或文件列表；专用账号可能读到公共可读对照，应据此淘汰其严格模式，而非改测试权限。 |
| 对象边界：合成的 junction/symlink/hardlink、路径别名、挂载及并发目录变更 | 边界内的普通文件仍可用；所有指向边界外的对象不得返回字节。只用测试环境生成的对象，不枚举真实主机。 |
| 信任通道：假凭证/环境、可遗留句柄、假 UNC/SMB、剪贴板/共享、回环与 IPC、假代启动服务 | 无未批准标记经任何通道进入 Agent；闭网和受控出口两种配置各自验证，批准的传输仍正常。 |
| Firefly 桥：路径式导入、附件/参考图、预览/生成、跨项目与过期绑定 | 非授权或过期请求不返内容、不给数据副作用；允许项可用，破坏性写仍受现有确认约束。检查结果、日志和已生成资产中无未批准标记。 |
| 故障：不支持 OS/ABI、策略探测失败、临时目录/挂载错误、桥鉴权失败、子进程归属或清理失败 | 不启动未受限进程、不放行代读，已启动的受限会话撤销桥授权并回收；不回退到普通 `Popen`、裸 WSL 或更宽 profile。 |

**验收判据：** 记录 OS/edition/build、架构、后端/内核/ABI、CLI 版本、有效 token/挂载/网络策略及脱敏的结果；正例成功、全部负例在规定层真正执行并被拒，才能算通过。工具不可用、未启动、命令未执行、无输出或探针出错均不算读取被拒；任何外部标记泄露、未经验证的可达代读通道或未知策略状态都阻止宣称严格隔离。撤权不能保证收回已经交付的数据。

**选型门槛：** 最小权限的原生候选通过才考虑优先采用；否则只评估通过验收且满足授权、运维和交互要求的受管 VM。若仅有 Windows Sandbox 通过离线对照，不据此推荐生产后端。

**仍未知：** 原生 CLI 在最小 LPAC 策略下能否运行，最小客体与附件桥的兼容性，模型访问如何避免传递宿主凭证，以及实际覆盖版本、管理权限、许可和运维负担。后续用冷/热启动、稳态与峰值内存/磁盘、并发、桥 I/O 和回收时延实测并记录机器条件；不承诺固定资源上限或性能。本文不据文档判定任何配置已通过。
