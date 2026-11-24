# #493：有证据边界的 Soon URL 自动通道与参考图预算

## 授权与证据

依据实际 `D:/Firefly-Canvas/.local/agent-work/channel/review-batch-ui/MESSAGES.md:3813–3816,3852–3866`：整票实施获准，但参考图、4K、比例须先在 URL 通道实测等价；硬限未摸清前超限明确报错，不能静默降质。领域约束见 `CONTEXT.md`、ADR 0006。此文只更新下述运行时自动通道，不代表 #493/#495 整票验收。

已结束、不可重放的付费依据：

- 汇总 `D:/ff-qa/493-paid/PAID-COMPLETED-SUMMARY.json` 与 `LIVE-SOURCE-RELEASE.json`。
- 冻结原件 `C:/ff-qa/493-paid-492f841cf3f14dbd8fa763f4fb03e5a4/`：`SOURCE-MANIFEST.json`、`sources/new/`、各 finished receipt、`references/`。本次只读，不运行付费 harness、不解密、不读取真实服务商配置/密钥，不追加实测。
- 16 个付费标签已全部消耗（13 次主对照 + 3 次边界），每标签记录一次业务调用/POST；不补额、不重放。13 次主对照均请求同一 RGBA 原件及 prepared SHA `1af00dc6751ec03031a507176068667a54eedc870f908a9c0b755f1db8fad9f4`、4K、4:3；实际均为 4800×3584。父代理查看 contact 后报告：13 张均保留三形状/顺序、红绿，中心蓝色改为紫色。8 张新通道结果均 PNG，归档与远端源字节一致；这不是输出像素相同或生成质量优于原生的证明。
- 指标来源：`D:/ff-qa/493-paid/LIVE-METRICS-REPORT.md` / `LIVE-METRICS-SUMMARY.json`。新默认信任组初始 POST 平均 **3.793s 是异步 ACK，不是生成耗时**；durable-start → finished-file 墙钟近似平均 **56.094s**，包含准备、轮询及等待、下载、校验/保存与日志开销，不是纯生成延迟或用户点击耗时。OLD 与 NEW 共用 **NEW 原字节 saver**，不能当作旧归档器性能对照。
- `proxy` 只是允许环境/系统信任的计划标签，不能证明实际代理路线；两组 NEW 下载均 `trust_env=False`。费用、物理 TTFB、真实硬限仍未知，不作「直连更快」结论。父代理有限视觉核对确认三形状布局/颜色修改，不推导全局质量或全部参数等价。

## 自动路由：仅一次调用内的配置副本

`app/services/providers.py::_soon_url_auto_provider` 仅在以下全部成立时启用：

1. 原始模型 ID 精确为 **gemini-3.1-flash-image-preview**；不扩到其它 Gemini、别名或 `-4k` 变体。本机随机服务商 ID 不代表 API 能力，不参与 profile 匹配；两个合成 ID 使用同一精确端点/模型时应得到相同 profile。
2. Base URL 为 `https://api.soonstudio.ai`（允许尾斜线、既有 `/v1`），没有 query/fragment、额外端口或自定义生成端点；地域域名未验证，保留原行为。
3. 平台协议为 `openai`，图片模式为既有默认 `openai`；该模型没有显式 `model_protocols` 或 `model_request_modes` 条目。

helper 命中后只复制本次调用的两个映射，指定 `openai` + `openai-json`。调用方应在原生分支前采用副本，并按付费候选锁定原始模型、保留显式比例/分辨率。未命中或存在任何单模型映射时，不覆盖人工选择，沿用原行为；其它 Soon Gemini 仍走既有原生路径，其它端点不改变。原生 helper 和显式 `_generate_soon_url_candidate` 均保留。不修改用户配置、初始选择、模型清单、能力表、凭据、代理开关或全局 resolver。

**证据限制**：付费 cohort 只有一个账户；这是精确端点/模型的通用请求 profile，不是所有账户、供应商分组、权限或配额均支持此能力的认证。移除本机 ID 条件不新增跨账户付费证据，不扩大 host/model/body，也不推断地域域名能力。人工映射始终优先。

**当前已接线**：实际 `generate_ai_image` 在原生分支前调用 `_soon_url_auto_provider`，不再以本机服务商 ID 限制；命中后锁定原模型并保留显式比例/分辨率。同一精确目标的 JSON URL 请求（包括人工明确 JSON 配置）均在创建提交客户端前执行参考图预算。原 portable cohort 的调用方阻塞和 RED 日志是历史证据，不是当前待办；本次文档核对不冒充最终候选全量门禁通过。

请求继续使用付费候选的已有实现：`POST /v1/images/generations`，顶层 `model/prompt/aspect_ratio/resolution/image_size`，`extra_body.response_format=url`，参考图为 `extra_body.image` 中的 data URL。不新增 `n`、`quality`、端点或模型回退。4K/4:3 付费候选的 JSON 为 9,182 字节；显式候选的实际 HTTPX 请求已与便携冻结记录逐字节/SHA 相等，参考图解码后原件 SHA/alpha 保持。普通 `generate_ai_image` 默认入口现已采用该调用内 profile；历史便携断言与原始失败保留，不据此声称本次重跑或最终全量通过。其它用户参数只延续既有表达，不声称已实测所有组合。

## 8,000,000 字节是保守政策，不是 Soon 硬限

付费边界观测（各一次，均已消耗）：

| 参考图原始/准备后字节 | 实际 JSON 字节 | 结果 |
| ---: | ---: | --- |
| 8,007,313 | 10,676,846 | 接受并归档 |
| 15,006,472 | 20,009,058 | HTTP 413 |
| 25,013,491 | 33,351,750 | HTTP 413 |

不能由这些点诊断真实上限、判定限制属于供应商还是中间链路，或把 JSON/base64 大小冒充图片字节上限。当前明确采用略低于已接受点的 **8,000,000 十进制字节**预算；因此已接受的 8,007,313 字节样本也被本地政策拒绝。硬限保持 **UNKNOWN**。

`app/services/generation.py::_soon_url_reference_data_urls` 在创建提交客户端之前，对这个端点/模型 profile 实际走 JSON URL 通道的参考图准备一次：

- 先复用必须执行的最长边 **4096**、保留原格式/alpha/原件策略，再按 **prepared 原始图片字节总和**核算；不是每张独享额度，也不是 base64/JSON 长度。既有参考图数量与 mask 分离规则不扩大。
- 合计 **≤8,000,000** 原样进入已有 body；空参考图合法。超过返回本地 HTTP **413** `soon_url_reference_budget_exceeded`，零生成 POST。
- 远程 HTTP(S) 参考图无法测量，返回 HTTP **413** `soon_url_reference_budget_unmeasurable`，明确要求先导入项目；不 GET 远程图片、不信任客户端声称的大小/MIME。
- 不为预算重压缩、改 PNG 格式、再缩小或丢参考图；超过 4096 的必需准备不是预算降质。源文件不变。无效本地图仍沿用既有明确校验错误。
- 上游真实 HTTP 413 原样报错，不转原生、不换模型、不改端点、不自动重发；既有 SDK 仅凭正面 trace/可重放证据允许最多两次额外上传尝试（1s/3s、每逻辑图片共享），HTTP 状态本身不授权 SDK 重试。独立的明确拒绝 legacy 429 业务退避可继续；接受/模糊结果、完整上传后读取失败、无证据均不得重新生成。

预算仅约束此目标的 URL 请求（含人工明确配置的同一 JSON 通道）；不把 URL 边界测量推广为原生接口或其它服务商的限制。

## 历史便携验证与留证

以下计数属于 `493-portable-376cc9e9-240e-44d6-aab9-721e101c6ef2` 的旧源码快照；原始失败与日志不改写。当前实现与整票门禁状态见 `image-generation-integration-493.md`，最终 integrated candidate 全量尚未运行。

`tests/test_soon_url_default_493.py` 使用实际函数 AST、真实 HTTPX/httpcore 的内存网络后端；不启动 main/原生宿主，不调用真实提供方。所有测试只读取仓库源码与 `tests/fixtures/soon-url-493/`，不读取外部付费目录，没有 SKIP/条件放行。服务商 ID 全部为合成值。

便携夹具仅两份：`paid-candidate-body.json.gz.b64` 压缩保存精确请求 JSON（其中仅嵌入一次 6,565 字节合成三形状 RGBA PNG），`provenance.json` 保存白名单 receipt 字段及原 receipt、SOURCE-MANIFEST、冻结源码/函数 SHA。没有原始 headers、凭据、账户标识、结果链接或整份 generation 源码。夹具缺失/篡改会失败，不下载、不回退到外部文件。

body SHA **4571273b3ae885cec3227383af92e45423553fcd4cb1dac35a97d1926d5d2a0c** 来自一次离线提取：校验 manifest 对冻结源码的 SHA 后，仅执行冻结候选/实现的 AST，经安装的 HTTPX 与内存后端捕获序列化 body；原 receipt 锚定 9,182 字节、提示词 SHA、原始/准备后 PNG SHA **1af00dc6…fad9f4**。原 receipt 没有记录完整 body SHA，不能谎称这个 hash 是当时线上直接记录的。测试同时检查当前显式候选和普通入口实际 body/字节/hash，而非仅比较字段数或复制当前实现的期望值。

8,007,313 字节接受样本只保留原 receipt SHA、图片 SHA/大小/尺寸及请求大小元数据。回归测试本地生成 2048×977 RGBA PNG，在 IEND 前插入 CRC 正确的未知私有 ancillary padding，精确达到 8,007,313；执行 `Image.verify()`/解码，并证明实际准备函数原字节返回、无编码。这个合成图不是原付费样本，不伪造相同 SHA。普通/人工 JSON 入口均保留本地 413、8,000,000 政策与零 POST 的强断言；原便携 cohort 当时的调用方阻塞使这两项失败，未削弱测试；当前调用方已接线，不把原 RED 结论当作现状。

覆盖普通生成入口而非仅 resolver：冻结候选请求等价、原始模型锁定、4K/比例/参考图 SHA、双 ID 与精确端点边界、人工映射优先、其它 host/model 旧行为、空参考图、多图合计精确阈值、远程不可测零 POST、强制 4096 后再计量、有效大 PNG 不重编码、不以 JSON 大小拒绝、上游 413 单 POST、接受任务后仅 GET 原任务及保持运行时鉴权形态、项目作用域先于准备/提交。

本轮证据根：`C:/ff-qa/493-portable-376cc9e9-240e-44d6-aab9-721e101c6ef2/`；旧日志未覆盖。白名单子进程环境无继承凭据，16 个独立环境根；保存前后 SHA、隔离清单与完整日志。收集前审计阻断 DNS/连接/数据报及外部付费目录读取，只容许 Windows asyncio 标准库 self-pipe；禁止导入 main。HTTPX 禁用测试中的系统代理发现，不能据此声称真实代理、TLS 或在线运行时间已验证。

由该目录 `run-offline.py` 运行 pytest（`-p no:cacheprovider -q`）：焦点 `test_soon_url_default_493.py` **65 项：34 passed、31 failed**，原 43 项及新增断言均实际执行；相关五套 `test_image_generation_pipeline_493.py`、`test_image_submission_policy_493.py`、`test_image_transport_493.py`、`test_generation_poll_download_t_493.py`、`test_firefly_generation.py` **182 passed、18 subtests passed**。合跑及复核 `final.log` / `final-confirm.log` 均为 **216 passed、31 failed、18 subtests passed，零 SKIP/零 deselected**；失败集中暴露未获准修改的调用方 ID 条件。显式候选完整 body/hash 对照通过，但不能报告整个工作已通过。两份 Python 文件 compile/AST 与限定路径 `git diff --check` 通过。没有付费重跑、真实 key/config/env 读取或整票浏览器验收。
