# #493 阶段 0：Soon 图片传输公开契约调查

## 结论与边界

- **研究完成，实施准入未完成。** 当前公开资料不能确定各 Soon Gemini 模型的单张输入字节、输入像素、请求总字节硬上限；不能据此发明压缩阈值。Flash preview 有“最多 14 张参考图”的官网能力描述，但各端点/分组的实际拒绝上限仍 UNKNOWN。
- URL 渠道的参考图、`image_size=4K`、`aspect_ratio` **字段存在不等于与原生行为等价**；三项等价性均 UNKNOWN。本阶段不实施切换、不选择默认渠道、不调用付费 API。
- Soon 官网确实公布异步图片轮询流程；**任务保留期、结果 URL 寿命、可靠下载/断点续传及断线后必可恢复的保证均 UNKNOWN**。缓存或日志保留时间不是 URL TTL。
- 用户质量合同保持独立：参考原图保留到长边 4096；PNG 保持 PNG；JPEG 未缩放时字节不变，缩放后仍为 JPEG；超限不静默降质、换格式或丢参考图。这里的 4096 是用户要求/父会话基线口径，**不是查到的 Soon 输入上限**。

调查日期：2026-11-23 UTC；工树 `D:/ff-wt/ui-493`，起点 `0ae5abc`。只读源码、既有研究与指定离线基线；网络仅公开文档/前端静态源码 GET，无鉴权、模型 POST、用户实例、项目、配置或 Key 访问。状态中的 **VERIFIED 仅表示引文/文档字段已核对，不表示实测准入或效果验收**；UNKNOWN 表示本次材料未建立该事实，不表示“不支持”。

## 1. 来源分层与实际模型名

**第一层：Soon 自己的官网与公开源码。** 从 `https://soonstudio.ai/models`、`/docs` 的公开入口追到官网 catalog/documentation bundle。S1 的模型记录和 S2 的轮询说明属于 Soon 自己发布的材料。官网明确说：“Listing a model does not grant permission to call it.”（S2）；目录记录不证明当前账号/分组可调用。

**第二层：#490 使用的 Apifox 兼容格式文档。** A1–A10 是供应商兼容 API 说明，不是 Google 原厂规范；本次 Soon 官网文档 bundle 未发现对 `gpt-best.apifox.cn` 的背书链接，故不把该站的全部声明升级为 Soon 各分组的专属承诺。#490 的一个模型/两个样本能旁证该接入路径，不能覆盖所有型号。

**第三层：Google 原厂文档。** G1/G2 只用于对照，不补齐 Soon 缺失的网关/代理/CDN 契约；live 页面已跳转到 `aistudio.google.com/docs/...`，还存在 preview/非 preview 命名变化。

### 目录清点（S1，不是固定别名表）

| 范围 | 公开源码中的精确 model ID |
|---|---|
| #490 已测 / Flash preview | `gemini-3.1-flash-image-preview`；以及 `-512px`、`-2k`、`-4k` 后缀记录 |
| Flash 非 preview | `gemini-3.1-flash-image`；以及 `-512px`、`-2k`、`-4k` 后缀记录；这些记录描述为空 |
| Pro | `gemini-3-pro-image`、`gemini-3-pro-image-preview`；两者各有 `-2k`、`-4k` 记录 |
| Lite | `gemini-3.1-flash-lite-image` |
| 2.5 | `gemini-2.5-flash-image`、`gemini-2.5-flash-image-preview` |
| URL 优化包装模型 | `nano-banana-pro` 及 `-2k`、`-4k`；`nano-banana`、`nano-banana-hd`；`nano-banana-2` 及 `-2k`、`-4k` |
| 额外遗留/歧义记录 | `gemini-2.0-flash-exp-image-generation`、`gemini-2.0-flash-exp-image`、`gemini-3.1-flash-image-4`；空描述，不据名称推导能力 |

本地 `tests/test_soon_provider.py:23` 的 `SOON_MODELS` 还包括 `gemini-nano-banana-2.1`，`firefly_generation.py:264` 把 `nano-banana-pro` 路由到 `gemini-3-pro-image`。这是**现有产品行为/历史线索，不是供应商契约**。本次官网 catalog 原文未检出 `gemini-nano-banana-2.1`，所以其当前身份、分组、URL 兼容性全部 UNKNOWN，不能据此删除它，也不能用 Google 同名模型替它补合同。

## 2. Soon 输入限制：紧凑矩阵

下表的“字节”分清**单个编码文件**与**整个 HTTP body**（JSON/base64 或 multipart 开销另计）；“像素”指输入宽高/长边/总像素，**不指输出 4K 档位**。查不到数值即 UNKNOWN，不解释为无限。

| Soon 模型范围（见上表精确 ID） | 单张输入字节 | 输入像素上限 | 参考图数量 | 请求总字节 | 输入格式合同 |
|---|---|---|---|---|---|
| Flash preview 及三个后缀 | UNKNOWN | UNKNOWN | VERIFIED：官网描述“最多 14 张参考图（10 物体 + 4 角色）”；端点/分组硬拒绝上限 UNKNOWN（S1） | UNKNOWN | A2 的 PNG/JPEG 文件示例、A3 的 `image/png` 示例 VERIFIED；Soon 完整 MIME 白名单/保真承诺 UNKNOWN |
| Flash 非 preview 及三个后缀 | UNKNOWN | UNKNOWN | UNKNOWN；不继承 preview 的描述 | UNKNOWN | UNKNOWN（S1 空描述） |
| Pro / Pro preview 及四个后缀 | UNKNOWN | UNKNOWN | VERIFIED：官网只说“支持多张参考图”；数值上限 UNKNOWN（S1；A8 仅示例两图） | UNKNOWN | 原生 PNG 示例 VERIFIED（A8），Soon 完整 MIME 白名单 UNKNOWN |
| Lite | UNKNOWN | UNKNOWN | UNKNOWN；A9 有数组但无数值上限 | UNKNOWN | UNKNOWN |
| 2.5 / 2.5 preview | UNKNOWN | UNKNOWN | VERIFIED：官网说“支持多张参考图”；数值上限 UNKNOWN（S1） | UNKNOWN | UNKNOWN |
| `nano-banana-pro*` / `nano-banana*` / `nano-banana-2*` 包装模型 | UNKNOWN | UNKNOWN | 多图字段/描述 VERIFIED，数值上限 UNKNOWN（S1、A7、A10） | UNKNOWN | UNKNOWN；不能把格式兼容当作无损转发 |
| `gemini-nano-banana-2.1`、遗留/歧义记录 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |

数值/格式声明的**精确引文**与适用边界：

- **14 张只核实为官网能力描述**：S1 原文“参考图一致性：最多 14 张参考图（10 物体 + 4 角色）”。S1 快照保存四条 preview 记录的原文；它们未给出 native / generations / edits、分组、超限错误或 HTTP 准入规则。不能把 14 设为所有 Gemini 的统一上限，也不能把示例数组无 `maxItems` 解释为无限。
- **4096 是输出描述，不是输入像素 cap**：S1 原文“分辨率：最高 4K（4096×4096），与 Pro 持平”。同一段被复制到尺寸后缀记录，不足以约束每个后缀的真实输出。#490 的实际尺寸见下节，不能用这句强行归一化结果。
- **Lite 不是默认 4K 候选**：S1 的 Lite 原文“仅支持 1K ”；A9 没有 `image_size` 字段。只确认官网宣称的输出档位，不推导输入长边或单图字节限制。
- **PNG/JPEG 是示例，不是完备格式保证**：A2 原文 `file://E:\Downloads\1745936044575403500.png` 与 `file://E:\Downloads\微信图片_20250826114255_1785.jpg`；A3/A8 原文 `mimeType: image/png`。A1 原文“参考图数组，url 或 b64_json”描述的是输入载体，不是图片编码。以上均未保证 PNG 不转 JPEG、alpha/色彩/元数据保留或输入原字节直通。
- **包装模型不能等同原生**：S1 的 `nano-banana-pro` 原文“我们基于 gemini-3-pro-image-preview 专门画图优化的api模型”，并说“可以通过参数 image_size 控制返回 1K,2K,4K (默认1K)”；这不是与原生同构/同质量保证。A10 原文 `nano-banana-hd 是高清版4K画质`，而 S1 为“生成 2K 高清大图”，两源存在口径差异，不裁定真实档位。

### 原厂信息隔离栏（不能写成 Soon cap）

| 已核实的 Google 原文 | 来源 | 不能推出 |
|---|---|---|
| “Inline image data limits your total request size (text prompts, system instructions, and inline bytes) to 20MB.”（换行见快照） | G1 | 不能变成 Soon 的单图 20MB 或总 body 20MB；MB 不擅换 MiB |
| `PNG - image/png`、`JPEG - image/jpeg`、`WEBP - image/webp`、`HEIC - image/heic`、`HEIF - image/heif`（原文含 Markdown 反引号） | G1 | 不能变成 Soon 的 MIME 白名单 |
| “Gemini models support a maximum of 3,600 image files per request.” | G1 通用理解指南 | 不能用作生图参考图上限，更不是 Soon 上限 |
| “Nano Banana models allow you to mix up to 14 reference images.”（后文细分 2.1 / Flash / Pro） | G2 | 不能替 Soon Pro、2.5、Lite 或同名别名补上 14 张合同 |

## 3. URL 与原生：三项等价性门

对象优先是 #490 已测的 **`gemini-3.1-flash-image-preview`**；其余型号不能搭便车。下面只比较文档，不运行生成。

| 必检项 | VERIFIED：公开字段/有限历史观察 | UNKNOWN：尚缺的等价证据 |
|---|---|---|
| 参考图 | A1：`image` 为 array，“参考图数组，url 或 b64_json”；A2：multipart `image`，“支持多图或不带参考图”；A3：`contents[].parts[].inlineData.{data,mimeType}`，示例两图。#490 一次 edits 样本参考确实生效 | native 与 URL 对相同 PNG/JPEG 原字节、多图数量/顺序、alpha、实际可见细节、身份一致性的对照；“一张生效”不证明全等。A2 prose 允许无参考图却 required:image，且 binary string 配多文件 example，编码细节仍有歧义 |
| 4K | A1/A2 有 `image_size`，enum 含 `4K`；A3 有 `generationConfig.imageConfig.imageSize`，enum 含 `4K`。#490 两样本以 `image_size=4K` 下载到 JPEG 4800×3584 | 同模型/同分组的参数真正生效、与原生同档且不静默改模型/降质。A1 同时写“仅 nano-banana-2 支持”，与本页 Flash model 示例不自洽；不能静默修文。A7 的 Pro 页面又把 model 写成 `nano-banana-2`，不能拿包装别名代证 |
| 宽高比 | A1/A2 有 `aspect_ratio`，enum 含 `4:3`；A3 有 `imageConfig.aspectRatio`。#490 提交 `4:3` 后得上述实际尺寸 | 各比例、各分组与原生相同语义/裁剪规则/允许尺寸；不能用近似比例的一个样本证明全部。A1/A2 枚举没有 `9:21`，而本地 `GEMINI_ASPECT_RATIOS` 包含它；暂列合同差异，不改代码 |

**三门结果均 UNKNOWN，不能作为默认 SoonGemini 改 URL 的通过依据。** 这是未取得等价证据，不是宣称 URL 不可用。原生页面 A3 标题写 Pro preview，实际路径却是 Flash；应按具体 path/model 核对，不能照标题建别名。

#490 的观察来源为本仓库 [既有记录](soon-url-paid-plan-490.md)：两次模型 POST、URL 异步取回，均 JPEG 4800×3584；一次 edits 保留部分形状并改变指定颜色。该记录明确未做原生付费对照、TTL 未知。本阶段不重新访问它的私有账单/签名 URL，不将这些历史样本升级为新调用或跨模型合同。

## 4. 异步任务与下载保证

| 问题 | 状态与精确依据 |
|---|---|
| 如何启用/识别异步 | VERIFIED：A4/A5 原文“仅需添加 query 参数 async=true，请求body 与之前一致即可”；S2 原文“When submission returns task_id, HTTP 200 means accepted, not finished.”。`response_format=url` 是表示选择，不是文档定义的异步开关；并非所有 URL 响应必返回任务号 |
| 正确查询口 | VERIFIED：S2 原文“GET the returned pollUrl, or /v1/images/tasks/{task_id}, with the same API key and channel group and no request body.”；不要把 ID 拼到 generations/edits 后。A6 定义 GET 路径 |
| 状态与结果形状 | VERIFIED：A6 原文 `IN_PROGRESS （处理中）`、`FAILURE （失败）`、`SUCCESS （成功）`；示例为 `data.data.data[]` 中 `url`、空 `b64_json`。这是示例，不能当所有上游/版本的强 schema；A4 prose 的 task_id 与 response schema 的 data:string 也有差异 |
| 已完成任务带 URL | VERIFIED（文档层）：A4/A5 原文“如果任务完成，响应会包含生成的图像 URL。”。不保证实际下载成功或规定返回 PNG/JPEG |
| URL 的最短有效期 / 永久可用 | UNKNOWN。S2 仅说“Results often live on upstream CDN — download promptly.”；随后日志保留时间不是 URL 生命周期。#490 的 Cache-Control 观察也不是 TTL 承诺 |
| task_id 保留期、重启/断网后可恢复期限、轮询间隔与最大耗时 | UNKNOWN。S2 的“Hot logs ~7 days; analytics ~90 days (ops config may vary).”不是图片任务可查询期或完成 SLA |
| 原生同步断线后一定能找回 | UNKNOWN。#490 只证明其两个 images 任务通过客户端标识/账单 ID 可取回；公开材料没有保证任意旧原生请求存在可查 ID |
| 下载 MIME/原字节稳定性、大小上限、校验和、Range/续传、鉴权/重定向契约 | UNKNOWN。A6 是结果 URL 示例，S2 只劝及时下载；历史立即 GET/Range 成功不等于保证 |
| exactly-once、重复 POST 不重复计费、失败/下载失败可安全重提交 | UNKNOWN。不得把 GET 轮询/下载重试与再次调用生成等同；本阶段不拟定新的重试策略 |

## 5. 潜在合同阻塞项（交父会话，不在本阶段裁决）

1. **输入预算缺失**：向 Soon 获取按精确 model ID、分组、native/JSON/multipart/远程参考 URL 区分的单图文件字节、输入宽高/总像素、参考数量、HTTP body 总字节以及超限错误。询问是否按解码前/后或 base64 计量；不能用 Google 数字代替。
2. **保真与预算冲突**：若同格式、未缩放 JPEG 原字节或 4096 参考超出真实预算，需用户可见的拒绝/人工处理合同；不能擅自重编码 JPEG、PNG 转 JPEG、降到旧尺寸、截断参考数组。UNKNOWN 不是启用静默压缩的理由。
3. **三项等价证据未齐**：先澄清 Apifox 与 Soon 的适用关系、Flash `image_size` 限定、模型别名/分组，本票付费对照已由用户授权（`MESSAGES.md:3785–3787` 及 #493 最新评论），无需再次申请；但应先建立无损埋点与三参数验证，再运行同输入对照。本阶段不消耗付费调用，也不选默认。
4. **恢复/下载承诺缺口**：任务与结果各自保留期、标识持久化/查询规则、下载鉴权/续传与失败结算需明确；不能承诺“已扣费必找回”或“URL 有效四小时”。
5. **已存在的质量红灯不能被文档通过抹掉**：只读核对 `D:/ff-qa/493-stage0/REFERENCE-BASELINE.json`，父会话两次离线实际链路均 `pass:false`，网络/付费计数为零，输出一致。RGB PNG 被转 JPEG 并缩小、未缩放 JPEG 字节改变、alpha PNG 被缩小。它是本地行为基线，不是 HTTP/原生/付费验收；本阶段不重跑或修复。

## 6. 引文与快照索引

以下均为**明确标注的选段快照**，不是完整页面归档；正文数值声明需连同引文限定语理解。每个文件均含来源 URL、获取方式与逐字原文。根目录 **P = `D:/ff-qa/493-stage0/primary/`**，下表路径均相对 P。

| ID | 公开 URL | 快照路径 |
|---|---|---|
| S1 | https://soonstudio.ai/assets/comflyModels-C5ghjtOP.js | `soon-catalog.excerpt.md` |
| S2 | https://soonstudio.ai/assets/ApiDocsPage-Mnp9ltd_.js （官网入口 https://soonstudio.ai/docs） | `soon-docs.excerpt.md` |
| A1 | https://gpt-best.apifox.cn/api-420646217.md | `apifox-flash-generations.excerpt.md` |
| A2 | https://gpt-best.apifox.cn/api-420646219.md | `apifox-flash-edits.excerpt.md` |
| A3 | https://gpt-best.apifox.cn/api-420647492.md | `apifox-flash-native.excerpt.md` |
| A4 | https://gpt-best.apifox.cn/api-356258955.md | `apifox-async-generations.excerpt.md` |
| A5 | https://gpt-best.apifox.cn/api-339685644.md | `apifox-async-edits.excerpt.md` |
| A6 | https://gpt-best.apifox.cn/api-356258956.md | `apifox-task-query.excerpt.md` |
| A7 | https://gpt-best.apifox.cn/api-420361512.md | `apifox-pro-generations.excerpt.md` |
| A8 | https://gpt-best.apifox.cn/api-406882253.md | `apifox-pro-native.excerpt.md` |
| A9 | https://gpt-best.apifox.cn/api-481108294.md | `apifox-lite-generations.excerpt.md` |
| A10 | https://gpt-best.apifox.cn/api-341817446.md | `apifox-banana-generations.excerpt.md` |
| G1 | https://ai.google.dev/gemini-api/docs/image-understanding → https://aistudio.google.com/docs/image-understanding | `google-image-understanding.excerpt.md` |
| G2 | https://ai.google.dev/gemini-api/docs/image-generation → https://aistudio.google.com/docs/image-generation | `google-image-generation.excerpt.md` |

`P/SOURCE-LEDGER.md` 保存获取失败、检索范围、HTTP/内容 SHA 与校验结论。首次静态 main bundle 的无自定义 UA GET 为 403；一次带研究 UA 的 GET 为 200 后继续正常静态源码调查，无失败重试循环。Google 长页面首次工具输出被截断，未读部分不作为结论来源。

验证仅针对研究交付：14 份快照的 90 个代码块均经公开 GET 逐字匹配（首次一处漏逗号已修正复核），14 个快照引用全部解析成功，状态矩阵/质量合同与 Markdown 检查通过。`git diff --check` 无错误；对新文档的 `git diff --no-index --check -- /dev/null docs/design/image-transport-493-stage0.md` 无空白错误，退出 1 表示新增文件差异。工树仅新增本文件，HEAD 未变。研究子任务未运行产品测试、浏览器或付费验收；父会话的独立核对见下一节，没有把文档检查冒充原生等价或付费验收。

## 7. 父会话独立核对与下一阶段

- 父会话运行 `python -B D:/ff-qa/493-stage0/verify-primary.py`：14 份公开来源、90 段引文全部逐字匹配，保存完整公开响应及 SHA。无鉴权、无重试、无模型 POST。原研究中的失败请求仍保留在来源账本。
- 真实参考图接缝命令：`python -B D:/ff-qa/493-stage0/reference-loop.py`。两次各四例均为预期 RED，输出字节和断言一致，子进程 exit 1。RGB PNG 2048×1024、8596 字节变成 JPEG 1536×768、19061 字节；JPEG 1024×768 未缩放却从 5159 变成 12939 字节；5000×2500 PNG 未按新合同缩到 4096，而缩到 1536；alpha PNG 保留格式但也缩小。平色合成输入只证明合同问题，不能用于宣称上传性能或真实画质提升。
- 该命令执行实际 `conversation.gemini_reference_part` → `media.reference_to_data_url`；仅路径解析/运行时查找接缝受控，不是 HTTP/CLI/模型验收。两次网络、原生进程、越界写入、敏感配置读取计数均为 0，服务/付费调用为 0。成功运行的隔离根已清理；完整清理与原始失败记录随阶段交付保存。第一次 Popen 保护器误用函数导致的 asyncio 类继承错误属于夹具失败，不算产品红例；旧脚本、stderr 和失败日志均保留。
- 父会话核对已安装 HTTPX 0.28.1 / HTTPCore 1.0.9。源码接缝表在 `D:/ff-qa/493-stage0/MEASUREMENT-SEAMS.md`；其合成内存探针是子任务报告，不冒充父会话的产品测试或吞吐实测。请求正文写完不是服务器确认；响应头完整/首个 body yield 都不能改名成真正首字节到达。第一阶段应按各自真实语义命名，测不到的字段保留不可用，不填 0。
- 测量不得更换现有代理路由或重建 multipart。`trust_env=False` 不只影响代理，也影响证书环境变量；是否绕过代理与证书策略需分开守住，不能用 `verify=False`。现有下载的 DNS/IP 固定与 SSRF 防线不退化。可能已经扣费的失败不得自动重发生成；GET 轮询/下载和再次生成分开记账。

尚无分段性能数据；后续按以下可证伪假设测量，不预先归因：

1. 大响应传输占主要时间：同参数对照若 URL 下载与原生 base64 的接收字节/耗时明显不同则支持；若响应传输很短则否定。
2. 代理链影响稳定性或吞吐：只在 URL 通道、相同输入参数下改变直连/代理；若两组无显著差异，则不据此默认绕过。小样本没有 TLS 错误也不能证明根因消失。
3. 上传准备/体积是主要项：分别测参考处理、序列化、客户端正文写完与实际字节；不能用文件读取完成代替上传完成。
4. 上游等待占主要时间：若正文写完到响应头完成很长，而准备/传输很短，优化本地编码或代理未必收益明显；该间隔仍包含网络与服务端等待，不能称为纯模型推理时间。

**建议阶段顺序（尚未执行）**：阶段 1 只加可关闭、无秘密、零额外请求的分段埋点，并用真实 SDK/受控后端验证前后 HTTP 方法、目标、头、正文和失败/取消语义不变；阶段 2 在输入上限及三参数门明确后做原图/URL/代理配置，补不重发回归和 31xx 浏览器验收；再做付费数据对照、全量逐 ID 比较、清理交付。阶段门以 review 后续放行为准。

**最小采样建议（尚未执行、不是重新申请付费授权）**：固定同一提示词、同一原始参考图 SHA、同一模型/分组、4K 与比例。旧原生+代理 5 次，新 URL+代理 5 次，新 URL+直连 3 次，共 13 次生成尝试；前后各满足至少 5 次，URL 直连/代理各至少 3 次，后两组只改变代理。每次失败也计入尝试，不用额外生成补齐成功数，不自动重发可能扣费的请求。该设计能比较整体前后及 URL 的代理差异，不能孤立量化每项组合优化的因果贡献。若模型/分组不支持或成本范围改变，先报告，不偷换模型或参数。
