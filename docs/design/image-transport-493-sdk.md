# #493 图片传输 SDK 合同

模块范围：`app/services/image_transport.py`；最初独立 SDK 交付不改生成路由、参数、参考图、结果下载恢复或 UI。当前生成调用方及 Agent per-item scope 已接线，见 `image-generation-integration-493.md` / `image-submission-policy-493.md`，下述独立模块证据不代替集成验收。依据 ADR 0006、0017、0023；不得把上传重试用于已提交任务的重新生成。实现针对已安装 HTTPX **0.28.1** / httpcore **1.0.9** 的 seam；升级须重跑测试。

## 父任务接线

导出四个入口：

- `ImageTransportClient(*, telemetry_sink=None, phase_tag='generate', max_upload_retries=0, **kwargs)`：原 `httpx.AsyncClient` 子类，不替换 transport；所有原构造参数原样转交。
- `create_image_transport_client(provider=None, *, client_factory=None, telemetry_sink=None, phase_tag='generate', max_upload_retries=0, **normal_client_kwargs)`。
- `image_response_json(response)`：只调用一次实际 `response.json()`，返回/异常原样保留，记录实际解析耗时。
- `record_image_decode(response, kind, elapsed_ms, output_bytes)`：父任务围绕实际操作计时后报告；`kind` 只接受 `base64`、`image`、`extract`，忽略未知类型、非有限/负耗时或非法字节数。

父任务在原生、OpenAI、轮询、下载等**各自原有客户端创建点**替换工厂调用，传入当前模块动态的 `httpx.AsyncClient` 作为 `client_factory`，保留原有 timeout、headers、transport、mounts、pinning、SNI、redirect、流式消费和关闭位置。不要为埋点增加任何请求、header、query 或 idempotency key。

只有工厂严格等于安装包原始 `httpx._client.AsyncClient` 才启用子类。注入的旧 stub、monkeypatch 工厂、其他子类照原构造 kwargs 调用：不添加 telemetry kwargs，不替它处理代理设置。直接构造 `ImageTransportClient` 不读取 provider。

接线示例（仅展示调用合同；本模块不改变生成入口）：

```python
async with create_image_transport_client(
    provider, client_factory=httpx.AsyncClient, timeout=existing_timeout,
    telemetry_sink=collect_scalar_snapshot, phase_tag="generate",
    max_upload_retries=0,
) as client:
    response = await client.post(existing_endpoint, headers=existing_headers, json=existing_body)
    response.raise_for_status()
    payload = image_response_json(response)
```

已有 `build_request/send(stream=True)`、multipart、下载 DNS pinning/SNI 路径仍用原调用方式和 kwargs；不要改成示例的 JSON 调用。实际原生/通用生成、JSON/外部 decode 与 pinned downloader 已有调用点接线；独立 `result_downloads.py` 的下载恢复指标不能由此推定。辅助模块测试不证明任何 live wiring、付费结果、UI 或端到端防重复提交。

### 代理与证书

review（2026-11-24，MESSAGES.md:3893–3898）裁定：实际代理路线/收益仍为 **UNKNOWN**，**本批不做代理开关**，后续项留在 #493。`image_bypass_system_proxy` 不属于 provider/连接 DTO、规范化、保存或公开响应，也无全局设置控件/翻译。旧实验字段读取时丢弃，保存时移除；不隐式改写磁盘旧配置，也不只隐藏 UI 却保留可持久化功能。

工厂不解读 provider 网络字段，原构造 kwargs 原样保留，普通产品 HTTP 延续系统代理和默认证书信任；不为埋点添加 `transport=` 或重建 TLS context。显式 `verify/cert`、SSLContext/truststore、证书环境及 mounts 均沿用 HTTPX 合同，不新增 `verify=False`。私有实验调用方仍可显式传库级 `trust_env`，不是用户配置功能。pinned 资源下载器原有 `trust_env=False`、禁重定向、DNS/IP/Host/SNI 和 SSRF 守卫不变；显式参数不会被旧 provider 字段覆盖。

## 指标与事件

`client.telemetry_records` 为最多 **128** 条快照的内存 deque。可选同步 `telemetry_sink(dict)` 仅应内存接收，或记录经审核的标量；内部不记录文件、不联网。sink 异常不会改变原请求结果。不要传执行网络/文件 I/O 的 sink。

每个实际 HTTP exchange/上传尝试的结束发 `event='transport'`；JSON/外部解码完成后另外发包含累计指标的 `json_decode/base64_decode/image_decode/extract_decode` 快照。重试等待取消/拒绝或 stream 消费者失败可能发 `retry_cancelled/retry_refused/consumer_failed` 更新。**事件数不是请求数**，decode 更新不能被累计成新付费尝试。redirect/auth 自身沿 HTTPX 原逻辑产生独立 exchange；attempt 在各 exchange 内从 1 开始，不提供跨请求的秘密相关 ID。

- 所有 `*_ms` 时间点以对应 `_send_single_request` 尝试开始的 `perf_counter` 为锚；`request_build_including_serialization_ms` 是另行实测的 build 时长，诚实包含 JSON 序列化/Multipart 初始化，不包含进此网络锚。
- `request_body_yielded_bytes`：原请求流向 transport 交付的实体字节上界，不是已写 socket 字节。
- `request_write_complete_ms`：真实 origin `send_request_body.complete` trace；只是客户端协议写调用完成，**不是 ACK、服务商接受或推理开始**。CONNECT 独立请求的 trace 不计为 origin 完成。
- `response_headers_complete_ms`：HTTP 头解析完成（无 trace 的 transport 为返回响应对象边界），**不是真实 TTFB**。
- `first_raw_body_yield_ms / response_raw_entity_bytes / raw_body_complete_ms`：原响应流交给 HTTPX 的首次非空 yield、压缩实体字节数、正常耗尽；不含 HTTP framing/headers/TLS，不是物理网络到达时间。
- `first_decoded_body_yield_ms / response_decoded_yielded_bytes`：原 `aiter_bytes(chunk_size=原值)` 的实际 yield。gzip 计数与压缩实体分别记录；partial 场景不含 HTTPX chunker 内部尚未交付的解码数据，是 lower bound。
- `body_complete / body_complete_ms`：观察到实际消费正常耗尽。提前 close、取消、损坏 gzip、读取失败不伪造完成。空 body 的 first yield 保持 null；缓存内容再读不重复计数。eager custom transport 已经消耗的内容保持 unobserved/null。
- `json_decode_ms`、三类 `*_decode_ms / *_output_bytes`：实际 JSON 调用/父调用方报告的操作；模块不做第二次解码或编码。
- `failure_phase / failure_class / cancelled / attempt / retry_scheduled / elapsed_ms`：固定 phase/SDK 异常分类，不含异常消息或自定义异常类名。`phase_tag` 仅允许 generate/poll/download/probe，其余为 other。`physical_ttfb_ms` 永远 null。

不收集 request/response repr、URL、headers、prompt、base64、文件名/路径、payload hash、trace info。缺失阶段保留 null，不用时间点推算服务商排队/推理时长。已有 trace extension 按安装版 httpcore 的 coroutine 检查、调用顺序和失败语义转发（Future/普通同步返回值同样拒绝）；callback 失败绝不成为上传重试证据。自定义 transport 通过 `request.aread()` 替换的 stream 保留，不被观察器恢复为已耗尽的旧 generator。上传、body await 和 `client.stream` 消费者取消都会保留 partial 指标并原样传播异常；手动 `send(stream=True)` 后在调用方代码外部发生的错误，只能看到未完成/提前关闭，模块无法知道未交给它的异常。

## 安全上传重试

SDK 缺省 **0**，MiniMax 仍为零；已接线的原生/通用等 HTTP 生图入口显式 opt-in。最多 **2** 次追加尝试，延迟 **1s/3s**；`_image_upload_retry_scope` 的 task-inherited allowance 在同一逻辑图片的业务重试及 Agent 子任务间共享，不按新客户端或业务尝试重置。SDK-finalized 错误不得重启业务预算。不改请求构建/Multipart boundary/headers，不增加幂等头。明确拒绝后的 legacy 429 业务退避是另一层既有政策，不是 HTTP 状态授权上传重试；接受/模糊结果均不得重新生成。

仅 POST、原始安装包 AsyncHTTPTransport、可证明重放的原始流，且存在以下正面证据之一：

1. SDK connect/TLS 的失败 trace，且 origin body 尚未开始；
2. origin 上传写失败 trace，并且完整流的已 yield 上界严格小于声明 Content-Length（无 Transfer-Encoding）。

此外必须没有 origin write-complete、没有 origin response-headers-complete。**缺 trace 不是证据**。完全 yield 后的 WriteError、EndOfMessage 写失败、未知 transport、未知长度、GET、响应读取失败、429/5xx 都不会触发上传重试。httpcore HTTP/1.1 会吞掉 WriteError 再试读错误头；只有上述 incomplete 正面证据仍成立时，才允许对随后冒出的读错误重试，绝不把已完成上传后的 ReadError 当作重试理由。

可重放范围有意保守：原始 ByteStream 不可变 bytes；原始 MultipartStream 的不可变字段/bytes/str 文件和精确 `io.BytesIO`。保留同一构建流；BytesIO 在重试前和退避后比对字节，变动则拒绝重试并传播原错误；重试开始前恢复到构建后、首次发送前记录的精确 cursor，随后完全由原 HTTPX multipart 渲染器决定读取方式。不会重建 multipart、改变 boundary/headers 或预先消费流。磁盘文件、ProgressUpload/自定义包装、任意生成器不证明原样重放，**即便 seekable 也拒绝**。调用方不得并发修改/共享同一请求流；本模块不加锁、不预读磁盘、不重新序列化。

最终四文件/31 项新增测试来源：`C:/ff-qa/493-retry-final-61dae08980b2481a84fa9d0ed8ee3a38/REPORT.md`，父代理已整合 `b738`。其共享预算与原断言结果见 submission policy；不是当前最终全量门禁结果。

## 离线证据与边界

`tests/test_image_transport_493.py` 直接加载纯模块，不导入 app/main。真实安装的 HTTPX/httpcore/h11 配合自有 fake network backend，比较 observed/unobserved HTTP/1 字节和写调用、JSON/Multipart 构建一次、gzip、空/partial/cancel、CONNECT 与 1xx、redirect/hooks/cookies、1s/3s/最多三次、响应/模糊上传禁止重试、代理与 TLS context 保留。增加真实 AsyncClient + MockTransport 前后 method/target/raw headers/body 与 stream-replacement 对照、SDK 同步/Future trace 拒绝语义及 callback 异常不重试、非零 cursor 的 BytesIO 部分上传回放、非法同步请求流错误记账。没有 provider、计费、真实 TLS 握手或外部下载调用。未安装 h2，HTTP/2 未运行验证；不声称带宽、物理 TTFB、平台/代理实测、全链路 exactly-once 或回传恢复已验收。
