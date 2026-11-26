# #444：AppContainer / LPAC 启动失败排查

**结论：已复现 `CreateProcessW` 的 `ERROR_FILE_NOT_FOUND`（Win32 2），根因未确认。** 给合成 EXE 和其自建目录补授两个包组的读／执行权限后，两种候选仍不能启动；检查的系统 DLL 已有相应 ACL。没有候选执行读取探针，不能判定拒读。VM 未运行，严格读取模式不放行，现有 [#423 写入合同](../design/agent-external-access-423.md)不变。

## 对照与观察

Windows 11 专业工作站版 `10.0.26200` x64，Go `1.26.2 windows/amd64`，基线 `a4c206586774432f5e7c12038a79a2f9b567e052`。先重跑[上轮探针](444-agent-read-isolation-lab.md)，构建退出 `0`、探针退出 `2`，`18.847s`（含构建）；再次观察到 AppContainer、LPAC 同一启动错误。下列对照沿用同用户正例与同一合成夹具，不用账号切换或模型。

| 问题 | 单独对照 | 观察与限制 |
| --- | --- | --- |
| 合成可执行文件是否缺包组 RX？ | 同一轮使用同一 EXE、SID、参数；先按当前包 SID 授权，再在自建合成根、工作区和 EXE 三处加 `ALL APPLICATION PACKAGES`（`S-1-15-2-1`）及 `ALL RESTRICTED APPLICATION PACKAGES`（`S-1-15-2-2`）`RX`。授权不沿目录继承传播到 hardlink 指向的同级文件。 | 补授权前后，AppContainer 和 LPAC 均在启动时返回 `WIN32_2`；补授这三处不能解除故障。没有修改外层既有目录的 ACL，也没有测试放宽全机 ACL。 |
| 依赖 DLL 是否缺两个包组 RX？ | 以 PE 导入表确认探针静态直接依赖只有 `kernel32.dll`，没有随夹具发布的私有 DLL。只读查询 `kernel32.dll`、`KernelBase.dll`、`ntdll.dll` 的 DACL。 | 三个 DLL 对两个 SID 均各有一条适用于文件自身的允许 ACE，包含 RX，未见这两个 SID 的拒绝 RX 位。没有修改系统文件。结论仅限这三份 DACL，不是加载器追踪或有效 token 对所有依赖的 AccessCheck。 |
| 显式工作目录是否造成 Win32 2？ | 保留上述补授 ACL，把父进程当前目录设为同一个合成工作区，改用 `lpCurrentDirectory=NULL`，不改变工作区。 | 两候选仍返回 `WIN32_2`。更换传递方式未解除故障，不等于验证了任意其他路径或宿主配置。 |

最终矩阵构建退出 `0`、探针退出 `2`（候选探针结果缺失，`21.370s` 含构建）。6 次受限启动（两种模式 × 基线、补 ACL、继承 cwd）均在 `CreateProcessW` 返回错误，未到启动后的 Job 归属和恢复步骤。无候选退出码、有效 token、回环命中或哨兵结果；不能用输出缺失推断拒读。

## 四组哨兵

| 哨兵 | 直接宿主对照（父、子、孙） | AppContainer / LPAC |
| --- | --- | --- |
| 文件读取和枚举 | 两组都明确读到工作区、同级、模拟用户目录和公共读取对照的标记，并列出测试文件。 | 未执行 |
| 链接 | 两组均从工作区内的 junction 列出并读到同级标记；hardlink 也返回预期标记。 | 未执行 |
| 命名 IPC | 两组均打开同会话已触发事件。独立假回环每组分别有 3 个允许响应（200、有标记）及 3 个拒绝响应（403、无标记）。 | 未执行 |
| 假凭证 | 显式放行环境变量时三层可见，显式交付的文件句柄仅首层读取成功；最小环境且不继承时三层均缺环境变量，句柄检查均为 `WIN32_6`。 | 未执行 |

两组宿主对照各三层均退出 `0`，验证夹具可观测；这不是受限 token、真实 Agent 或 Firefly 桥的验收。

## 运行与下一步

在 Windows x64 的 PowerShell 7 使用 [`run.ps1`](../../tools/diagnostics/windows-appcontainer/run.ps1) 和 [`canary.go`](../../tools/diagnostics/windows-appcontainer/canary.go)。指定本机 Go 可执行文件、已有专用测试目录、尚不存在的证据目录：

```powershell
pwsh -NoProfile -File tools/diagnostics/windows-appcontainer/run.ps1 `
  -GoExe 'C:/Program Files/Go/bin/go.exe' `
  -EvidenceDir 'D:/ff-qa/a444-appcontainer-start/results/matrix-v2' `
  -ScratchParent 'D:/ff-qa/a444-appcontainer-start'
```

本机保留 `D:/ff-qa/a444-appcontainer-start/results/matrix-v2/`（确切 argv、源码哈希、原始日志、DLL ACL 摘要、退出码）及 `D:/ff-qa/a444-appcontainer-start/results/audit.json`。只更改新建夹具的 ACL；没有读真实项目或凭证，也没有创建账号、配置文件、改动系统／用户目录、设置回环例外或启动 VM。回环只通向本轮自建假服务。

下一条有判别力的试验，是在获准的专用测试环境内比较同一探针使用仅派生 SID，与用 [Microsoft 文档的 `CreateAppContainerProfile`](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createappcontainerprofile) 注册测试配置文件之后的启动结果；后者会写入每用户文件夹和注册表，不在本轮权限内。没有该正对照，不能断言缺少 profile 就是根因。只有确认候选进程与有效 token 确实启动，才能把四组哨兵的结果用于读取隔离判定。
