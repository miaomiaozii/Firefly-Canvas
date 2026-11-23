# #490：失败归因与 Soon URL 渠道实测

## 结论与产品边界

- 已区分服务商 HTTP 失败、传输/接收中断、普通生成失败、下载/归档失败与最终画布写回失败；不能再把所有异常叫作「写回失败」。
- 删除了浏览器归档失败后再次调用生成接口的补偿循环；可能已经扣费时不重新提交。保留本地最终持久化的有限重试，不把它等同于再次付费生图。
- 现有日志能确认上游 HTTP 错误与 TLS/ReadError 的存在，但没有对应响应体字节数，**无法据此确定接收中断是大 base64、客户端读取、代理还是上游导致**。不据此调整传输参数或增加自动重试；分段测量和直连/代理比较属于后续独立 #493。
- 本次 OpenAI images 渠道确实可以取回已完成结果；但实际协议是先返回异步任务号，再查询得到 URL，而不是 POST 直接返回图片。默认渠道不变。

## 原日志与分类回归

本地脱敏汇总在 `D:/ff-qa/489-488/490/log-aggregates.json`：误标写回失败 23 行、TLS bad-record-mac 1 行、ReadError 2 行、HTTP 502/451/524/504 分别为 2/7/2/2 行。它们是**匹配的日志行数，不是去重请求数或失败率**；不能从中推导大小相关性。

回归覆盖类型化服务商异常、脱敏、接收中断、下载归档、真实 Store 写回、部分结果、取消与 Agent 原有语义；n=1/2/8 的生成次数保持精确约束。父会话独立定向结果为 153 passed、4 subtests、3 warnings。旧重复函数组仍为两处，只随真实路由实现更新 AST 指纹，未扩白名单或成员数。

## 两次已授权付费实测

权限依据：`MESSAGES.md:3736–3738`。只从用户全局配置读取 Key 并在内存解密；未复制、打印或提交 Key。测试项目使用隔离根及 `e2e-tabbit-` 前缀，结束后删除；未访问用户 3000 实例。

两次均为 `gemini-3.1-flash-image-preview`、`response_format=url`、`image_size=4K`、`aspect_ratio=4:3`；一次 generations，一次 edits 带自制红方块/蓝圆/绿三角参考图。全局预算账本记录 **2 次模型 POST，0 次客户端模型重试**。

| 观察项 | generations | edits |
|---|---:|---:|
| 初始 HTTP | 200 | 200 |
| 初始 JSON 字节 | 46 | 46 |
| 初始响应耗时（仅入队，不是出图耗时） | 4.078s | 4.969s |
| 初始有效字段 | task_id | task_id |
| 查询完成后 URL / 非空 b64_json 数 | 1 / 0 | 1 / 0 |
| 实际图片 | JPEG，4800×3584 | JPEG，4800×3584 |
| 原始下载字节 | 7,085,409 | 3,636,192 |
| 下载耗时（不是模型耗时） | 46.422s | 16.985s |
| Soon 结算记录 | ok / settled / 0.1644 credits | ok / settled / 0.1644 credits |

合计已结算 **0.3288 credits**；不换算成其他币种。两次返回尺寸相同，但不能把两个样本及近似 4:3 比例说成与原生 Gemini 全面等价；本轮没有另付费跑原生对照。父会话实际读取生成预览与参考图：编辑图保留红方块和绿三角，把蓝圆变为紫圆，证实参考图在这一样本生效。

### 取回事实及首轮 QA 错误

首轮 QA 误以为 POST 会直接返回图片，记录了 ValueError 且未保留上游任务号；原始失败 `run-c25e722863304b89/RESULTS.json` 没有改写或冒充成功。随后只做 GET：在官方 `/v1/images/tasks/{task_id}` 上，用本次自己的 `X-Studio-Task-Id` 客户端标识、以及对应账单 ID **都实际返回 SUCCESS 与同一结果 URL**。两条取回路径在各自样本中的 URL 完全相同，无需再次提交模型请求。

这比公开文档的保证范围更宽：只能说明本次账号/这两条 OpenAI images 任务可取回；**不证明旧 Gemini 同步断线请求已保存了可查询标识，更不承诺所有历史扣费请求都能恢复**。本票只研究，不把恢复或换渠道功能写入产品。

### URL 有效期与隐私

下载主机为 `webstatic.aiproxy.vip`。两次立即 GET 都为 200，后续 Range GET 都为 206；响应 `Cache-Control: max-age=14400`，没有观察到 Expires 或显式签名过期字段。**缓存时长不是 URL 寿命保证，精确有效期仍未知**，不能对用户承诺四小时或永久有效。

签名 URL 仅在内存使用；取回中间链接用本机 DPAPI 加密保存在私有 QA 根，不进入仓库或公开报告。下载无跨站 Bearer，关闭 HTTP 自动重试；图片原始网络字节与 SHA 留档，供审查的 PNG 已去元数据，不能用其大小代替网络响应字节。

## 证据与来源

- `D:/ff-qa/489-488/490-paid/PAID-BUDGET.json`：唯一两次模型提交账本。
- `D:/ff-qa/489-488/490-paid/run-c25e722863304b89/RESULTS.json`：首轮 QA 的原始失败及项目清理。
- `D:/ff-qa/489-488/490-paid/RECOVERY-GET-RESULTS.json`、`FORMAT-CHECK.json`：真实取回与响应格式。
- `D:/ff-qa/489-488/490-paid/BILLING-LATER-SHAPE.json`：两条自己的已结算记录，不含 Key。
- `D:/ff-qa/489-488/490-paid/recovered-f501ddfd9a304c06/RESULTS.json`：下载、尺寸、字节、SHA、Range 与清理。
- 公开规范：https://gpt-best.apifox.cn/api-420646217.md （generations）、https://gpt-best.apifox.cn/api-420646219.md （edits）、https://gpt-best.apifox.cn/api-356258956.md （异步结果查询）。公开原件与研究计划留在私有 QA 根，不能把计划当实测。
