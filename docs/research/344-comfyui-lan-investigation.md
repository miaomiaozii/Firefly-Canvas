# #344 第一步：本机 Firefly 调用局域网 ComfyUI

## 结论与范围

**现有适配器已具备把同一条 ComfyUI 出站协议改指局域网地址的能力；不是从零开发“局域网模式”。** 在远端可达、没有额外认证障碍且节点/权重兼容的条件下，现有服务商的 `base_url` 就是地址入口。不需要让 Firefly 监听局域网、不需要 ComfyUI 回调 Firefly，也不需要给 Firefly 加登录。但**本次没有 LAN 实测，不能承诺用户那台生图机填地址后一定可用**。

- 裁定依据：[用户在 #344 的新方向](https://github.com/miaomiaozii/Firefly-Canvas/issues/344#issuecomment-7057402441)；频道 `MESSAGES.md:6846–6848、7006–7008`。原票“开放入站/登录”的旧设想不作为本轮实施范围。
- 本地基线：`f6365e78aba9a17ad30b6e00ab44e4dfb9cfbbd8`；独立 worktree `D:/ff-wt/a-344-investigation`。仅调查，不修改产品/测试代码，不接入新模型，不开放入站，不做登录，不调用真实或付费生图。
- #459 分支 `D:/ff-wt/a-402-442-457` 保持 `02bf90690165fb6efaeeddd0f74a9bce24ea4caa`，本票不带入其原始归档或 `.gitattributes`。

## 本地实现核对

| 事实 | 源码与离线证据 |
| --- | --- |
| 服务商设置已有 `base_url` 与 `comfyui` 协议；默认根地址为 `http://127.0.0.1:8188`。配置归一化只要求 HTTP(S)，没有按私网 IP 拒绝该服务商地址。 | [设置卡片](../../static/js/global-settings.js#L185)、[默认值](../../app/core.py#L195)、[normalize_provider](../../app/core.py#L993)。探针对 RFC1918 三段、IPv6 ULA、带路径的假域名均能把请求拼到指定根地址；不等于实际 DNS/TCP 可达。 |
| `/prompt` → `/history/{id}` → `/view`，有参考图时先 `/upload/image` 发送 multipart 字节；不把 Firefly 的项目 URL 交给 ComfyUI 回拉。 | [comfyui_api_root](../../app/services/generation.py#L201)、[上传](../../app/services/generation.py#L493)、[生成](../../app/services/generation.py#L538)。探针所有请求只到夹具指定主机，参考图上传成功；现有项目媒体原地解析测试也通过。 |
| 这是固定 MiniMax-H3-Image/GGUF 工作流，不是任意 ComfyUI 工作流/模型通用接入口。远端需具备对应节点、权重和兼容版本。 | [工作流与权重名](../../app/services/comfyui_workflows.py#L90)、[ADR 0025](../adr/0025-local-comfyui-as-image-provider.md)。检测一个节点不能证明所有依赖齐全。 |
| 不存在“为 provider 放行私网 IP”这一必需前置开发。provider 由用户显式配置，不应与不可信媒体 URL 的安全策略混淆。 | 上述归一化、地址拼接及五种夹具根地址已观察；本结论只覆盖现有 ComfyUI provider 链路，不主张扩大其他下载入口的放行范围。 |

## 官方服务端前提

核对日期：2026-11-21。源码固定为 Comfy-Org/ComfyUI `0072b8680dd2db0b6d68c06f718fa33bd92a728c`，**不代表用户安装版本**。

- 默认只监听 `127.0.0.1:8188`。远端主机可用 `--listen <LAN-IP>`；`--listen 0.0.0.0` 监听全部 IPv4 接口，裸 `--listen` 的观察值为 `0.0.0.0,::`。客户端填远端真实地址，不能填 `0.0.0.0` 或本机回环地址。[U1]
- 官方自托管示例为 `python main.py --listen 0.0.0.0 --port 8188`。这是**远端部署说明，不是本轮执行命令**。防火墙、路由、容器映射仍须让 Firefly 所在机器出站可达；本轮未改任何主机配置。[U2]
- 原生 API 接受节点图和文件上传，提供历史/图片下载；它们不要求回调 Firefly。因此无需为这个方向把 Firefly 改成入站服务。[U3][U4][U5]
- 官方明确默认无认证：只在可信网络绑定非回环地址，否则使用带认证的反向代理。TLS/认证、反代前缀、证书信任、上传限制等都取决于实际部署；监听参数不是认证措施。[U1][U2]
- 观察上游中，缺失 `/object_info/{node_class}` 返回 HTTP 200 `{}`，不是必须返回 404。这直接解释了下面连接探测的假阳性。[U6]

## 已确认的缺口（仅记录，未修产品代码）

| 问题 | 观察与影响 | 建议的后续边界 |
| --- | --- | --- |
| **连接测试可能假阳性** | [api_providers.py:429–446](../../app/routers/api_providers.py#L429) 不验证 `/system_stats` / 节点响应 JSON 内容。节点 `{}` 或两个接口都返回 HTTP 200 登录 HTML，仍回 `ok:true` 且“已检测到”节点；节点 404 也回 `ok:true`，只是消息警告。上游真实缺节点可能就是 200 `{}`。[U6] | 区分“HTTP 可达”与“该工作流可用”，校验 JSON 和目标节点，不能用绿灯冒充权重/全工作流就绪。 |
| **轮询 HTTP 错误被伪装成生成超时** | [generation.py:425–450](../../app/services/generation.py#L425) 没检查 history 的 HTTP status，非 JSON 也吞成空历史。夹具 history 401、503 或 200 HTML，最终都变成 504“生图超时”，丢失实际认证/服务错误；本轮用假时钟越过 deadline，没真实等待半小时。 | 保留 upstream status，区分拒绝访问、服务不可用、非协议响应与真正尚未完成；不自动重提任务。 |
| **取图阶段网络异常没有同等包装** | [generation.py:676–690](../../app/services/generation.py#L676) 直接 await `/view`。夹具 ReadTimeout 直接冒泡 `httpx.ReadTimeout`，而提交和轮询有 HTTPException 包装。 | 给取图断连/超时保留阶段和 prompt_id；本轮未测公开路由/界面最终如何展示，不推断所有入口都显示 500。 |
| **超时参数和提示需分层** | [core.py:198–201](../../app/core.py#L198)、[generation.py:566](../../app/services/generation.py#L566)：任务默认 1800s、poll 2s、queue 6s；HTTP connect/pool 10s、write 120s、read 至少 60s（默认 1800s）。连接测试各阶段 15s。提交 ConnectError 已给 503，提交超时已有 504“任务可能已入队”且不重提；连接测试则把连接拒绝/超时都归成 `status:0`“连不上”。任务 deadline 只在轮询边界检查，不是所有 await 的硬总时限。 | 保留已存在的安全提示，补阶段化可理解错误；先确定等待预算，不把网络未知状态当作可以安全重发。 |
| **“已撤单”提示不是可靠回执** | [generation.py:387–407](../../app/services/generation.py#L387) 撤队列忽略 HTTP 状态且尽力执行；探针令 history 503、撤单 POST 也 503，最终消息仍说“已从队列撤单”。运行中任务是否被中断还取决于后续队列快照。 | 文案区分尝试撤单、已确认撤单与状态未知，不能在连接失败后保证远端已停止。 |
| **认证反代/重定向不是现成无条件支持** | 当前连接测试和生成 Client 未把普通 `api_key` 转发为 Authorization；夹具加假 key 后所有请求仍无该头。观察 Client `follow_redirects:false`、`trust_env:true`；只有假反代路径拼接通过，不代表代理、TLS、私有证书或系统代理已验证。 | 先问实际部署方式；不把此缺口扩大成给 Firefly 做登录，也不建议把凭据塞进 Base URL。本轮不评估 URL 内凭据、SSO/自定义头方案。 |

## 离线验证与原始结果

所有 IP/域名都是 MockTransport 夹具，**不是扫描/访问目标**。探针禁用真实同步/异步 HTTP transport，不启动监听，不启动 ComfyUI。探针走普通非 Agent 的适配器路径，不等同于浏览器、真实 Agent 或整条线上链路验收。

- QA 根：`D:/ff-qa/a-344-investigation/`；每个子进程有独立 `home`、`runtime`、`projects`、`temp`，同时显式设置 `FIREFLY_RUNTIME_ROOT` 与 `FIREFLY_PROJECTS_ROOT`，不读取真实配置/用户项目，不碰 3000。
- 可复跑入口：`python -B D:/ff-qa/a-344-investigation/verify_344.py probe|h3|payload|fast`；命令、cwd、HEAD、环境和 exit 在各阶段 `metadata.json`；stdout/stderr 原件在同目录。`probe_344.py` 是调查脚本，不作为产品或回归测试提交。
- 探针原始结果：`PASS 21 offline cases`，exit 0；见 `probe-results.json` 和 `probe/stdout.txt`。覆盖私网/假域名根地址、参考图、连接探测、拒绝连接/超时、history HTTP/HTML 错误、失败撤单和取图超时。PASS 表示行为复现及断言通过，**不表示上述缺口已修好**。
- `tests/test_comfyui_h3_image.py`：`33 passed, 1 warning in 2.30s`，exit 0；`h3/stdout.txt` / `h3/junit.xml`。
- `tests/test_comfyui_canvas_payload.py`：`4 passed, 1 warning in 1.27s`，exit 0；`payload/stdout.txt` / `payload/junit.xml`。
- fast 首次尝试：exit 2，`execution-workspace` precondition failed，`could not enumerate repository files: [WinError 2] 系统找不到指定的文件。`；未执行九步，不算通过。原件移存 `fast-precondition-failed/`，当时 QA wrapper 误用 `C:/Program Files/Git/cmd`；独立 `Get-Command git` 确认实际为 `F:/Program Files/Git/cmd/git.exe`。
- 只修正 QA wrapper 的 PATH，并补上已有 PowerShell 的目录后，最终 fast 原始结果：`mode=fast, verdict=passed, final_exit_code=0, duration_seconds=23.562`；环境/命令在 `fast/metadata.json`，分步结果在 `fast/gate/report.json`。未改产品或门禁实现来绕过失败。

## 下一步与未验证

先由 review 决定是否进入修复：优先处理探测假阳性、轮询错误分类与撤单诚实提示，再决定阶段超时/取图错误包装。仍然沿用单个服务商根地址，不新增“局域网模式”开关，不放开 Firefly 入站，不引入登录；真实端点验证须另获许可且不得花钱。

未做：真实 LAN 连接/生图、真实模型/节点/权重安装验证、代理/TLS/系统代理实测、浏览器手测、真实 Agent 调用、全量 pytest、npm typecheck/lint/test。没有据此关闭 #344；不改 spec/plan 或已有 ADR 的产品决策。

## 官方来源

[U1]: https://github.com/Comfy-Org/ComfyUI/blob/0072b8680dd2db0b6d68c06f718fa33bd92a728c/comfy/cli_args.py#L63-L68 "实际核对的监听、端口与 TLS 参数"
[U2]: https://docs.comfy.org/development/deploy/self-hosting "官方自托管：监听示例、默认无认证、可信网络/认证反代"
[U3]: https://github.com/Comfy-Org/ComfyUI/blob/0072b8680dd2db0b6d68c06f718fa33bd92a728c/server.py#L468-L550 "multipart 文件上传"
[U4]: https://github.com/Comfy-Org/ComfyUI/blob/0072b8680dd2db0b6d68c06f718fa33bd92a728c/server.py#L1151-L1246 "历史和 API 图提交"
[U5]: https://github.com/Comfy-Org/ComfyUI/blob/0072b8680dd2db0b6d68c06f718fa33bd92a728c/server.py#L599-L749 "view 图片响应"
[U6]: https://github.com/Comfy-Org/ComfyUI/blob/0072b8680dd2db0b6d68c06f718fa33bd92a728c/server.py#L905-L911 "缺失 node_class 返回空 JSON 对象"
