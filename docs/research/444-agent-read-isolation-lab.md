# #444：Windows 合成读取隔离验证记录

**结论：本轮没有候选通过严格读取隔离验收。** AppContainer、LPAC 均在启动阶段受阻；无共享 Windows VM 未运行。保留 [#423 写入合同](../design/agent-external-access-423.md)，不改产品、不开放严格模式。本文记录[桌面研究](444-agent-read-isolation.md)后的独立实测，不把研究中的候选判断当作运行结果。

## 环境与范围

- Windows 11 专业工作站版，`10.0.26200`，x64；Firefly 源码基线 `f23a8fb09a664b07e9e8bf66f4adb5270372a31c`；固定探针用 Go `1.26.2 windows/amd64` 离线构建。
- 文件、路径、假凭证、继承句柄、命名事件和回环服务均由测试生成。探针未读取真实项目、用户目录中的数据或真实凭证。未创建账号或 AppContainer 配置文件，未启用宿主功能、修改全局安全设置、重启或调用模型；ACL 和完整性标签只改在本轮创建的夹具内。
- 两个宿主对照和两个候选使用同一个探针。启动器用自有 Job 管理进程树，测试父、子、孙三层；数据和日志仅记录预定义哨兵是否可见，不扫描宿主。AppContainer 使用新派生的包 SID、无能力列表；LPAC 另外请求退出 `ALL APPLICATION PACKAGES` 默认访问。两者均未观察到有效 token，以下结果不代表这些策略已生效。

## 实测结果

| 验证项 | 实际观察 | 判定 |
| --- | --- | --- |
| 宿主正对照 | 三层退出码均为 0；都读到工作区、同级、模拟用户目录和公共读取对照目录的标记并列出测试文件；从工作区内的 junction 和 hardlink 读到目标标记，打开已触发的同会话命名事件。假环境变量三层可见；显式交付的假文件句柄仅首层可读。 | 探针和哨兵对照有效；候选 ACL 在宿主对照完成后才设置，宿主也不是降权账号。 |
| 宿主最小环境、不继承句柄 | 三层退出码均为 0；假环境变量均缺失，句柄检查均为 `WIN32_6`。其他文件、枚举、链接和 IPC 观察同正对照。两个宿主对照各有 3 次回环允许请求（HTTP 200、有标记）、3 次回环拒绝请求（HTTP 403、无标记）。 | 清理环境与继承句柄减少了两条具体暴露通道，但同一宿主用户仍能直接读外部夹具和访问该 IPC。简化的假回环服务不代表 Firefly 产品桥。 |
| AppContainer | `CreateProcess: The system cannot find the file specified.`；无进程探针结果、无回环命中。 | **启动受阻**，不是读操作被拒。 |
| LPAC | 同一启动错误；无进程探针结果、无回环命中。 | **启动受阻**，不能对比两种 token。 |
| 无共享 Windows VM | `HypervisorPresent=False`；`Microsoft-Hyper-V-All`、`Microsoft-Hyper-V-Hypervisor`、`Containers-DisposableClientVM`、`VirtualMachinePlatform` 和 `Microsoft-Windows-Subsystem-Linux` 的 `Win32_OptionalFeature.InstallState` 均为 `2`（未启用）。未发现可直接运行本轮基准的现有 VM 入口。 | **NOT_RUN**；不据此推断硬件不支持。未安装、未启用或重启。 |
| Firefly 现有逻辑（独立测试） | Python 3.12：`36 passed, 3 warnings, 72 subtests passed in 6.25s`，退出码 `0`。自身项目及显式配置目录的媒体导入可用，跨项目和配置范围外路径拒绝，越界文件存在与否不改变错误；另覆盖固定节点工作区、恢复、链接拒绝、附件原子写入及 UI 披露。 | 仅证明这些产品逻辑在隔离夹具下通过，**不是**受限进程到宿主桥的端到端验收。 |

最终探针构建退出码 `0`；探针退出码 `2`（必需的候选结果缺失），全程 `21.625s`，含构建。早期尝试分别有宿主 token 查询、夹具 ACL、运行取消问题，不作候选结论。`ERROR_FILE_NOT_FOUND` 的具体原因未确定；未创建官方[启动示例](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)使用的 [AppContainer 配置文件](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createappcontainerprofile)，不能据此认定缺少配置文件是原因。

没有在候选内执行读取、枚举、链接或 IPC，也没有验证真实 Pi/Codex 启动、续接、重试、附件桥、UNC/SMB、对象替换竞态或 VM 基准。本轮无可报告的候选启动性能或兼容性数据。

## 复查与放行

- 本机证据目录 `D:/ff-qa/a444-read-isolation-lab/`：`verify-lab.ps1`、`results/native-v5.source.go`、`results/native-v5-{command,result}.json`、`results/native-v5.log`；标准库探针源码 SHA-256 `b8810d0492eb4e7fd879a8d4fedee3c59fc5f0b0403475234a5de0314c68deb5`。脚本使用新建的合成测试根、离线编译及临时构建缓存。
- 产品逻辑证据：`verify.py bridge`、`results/bridge.command.json`、`results/bridge.log`、`results/bridge.xml`。v5 合成根和构建根、Python 运行根及取消的 v4 临时目录已删除，保留源码、命令、退出码和日志。
- 下一轮只有在获批的隔离测试账号或预配 VM 可用时才继续。先让官方受支持的 AppContainer 配置方式和最小探针成功启动，逐层核对有效 token、正例与负例；VM 则预先确认规格、授权及全部宿主重定向关闭。两个候选都须通过[原验收矩阵](444-agent-read-isolation.md#5-后续验证与放行门槛)及实际运行时/桥测试，才可进入产品选型；启动失败时不得回退到未受限执行。
