# #500 参考图请求体预算：原图优先 + 质量阶梯 + 水位分配

> 本票取代 #493 的阶梯语义（`soon-url-default-493.md` 预算节中已被划去的行）。
> 已知上限登记、413 UNKNOWN 政策、远程透传、429/接受合同、只压发送副本等边界均沿用 #493，不重复改写。

## 用户裁定（票面）

1. **原图优先**：实际序列化请求体放得下就发原图，一个字节都不改。废除对所有服务商一刀切的强制 4096 预缩放（#493 基线语义作废）。
2. **单张规则**（超限才动，只压发送副本）：
   - 不透明照片（含不透明 PNG 源）：保持原尺寸 JPEG **q95→92→90→85→80**；仍超限按长边 **6144→5120→4096→3584→3072→2560→2048→1536**，每档质量 80。最低质量 80。
   - 全程 **4:4:4**（subsampling=0），保留 **ICC**；EXIF 随档携带防方向翻转。
   - **透明图走 WebP**（同一质量阶梯）；**不透明照片不走无损 WebP**——实测比原 JPEG 还大且每档多花十几秒。
3. **多图水位分配**：总预算扣除 prompt/JSON 等非图开销后平均分给每张；小于自己份额的保持原样冻结，余量在下一轮重分给超限图。取代 #493 的 first-fit 最大者优先。
4. **到底零请求**：全部压到底线（1536@q80）序列化仍超本地目标 → 不发请求、不扣费，报「参考图太多：…请减少到 N 张」；N 由底线体积从小到大贪心算出（`suggested_max_references`）。
5. 压缩只作用于发送副本：每档从同一基准像素重新编码（无有损叠加）；原文件零写入；不放大。

## 实现接缝

- `app/services/image_request_budget.py`：`_QUALITY_STEPS`/`_RESIZE_CEILINGS`/`_FLOOR_EDGE=1536`/`_FLOOR_QUALITY=80`；`PreparedReference` 基线=原图字节；`prepare_budgeted_request` 为水位循环（`share_bytes` 记入 steps）。
- `generation._budgeted_prepared_references`：`_generation_reference_to_data_url(ref, max_edge=10**9)`（#500：基线原图；`media.generation_reference_bytes` 签名与默认值不动，其它调用方不受影响）。
- 报告/标注结构不变：`reference_preparation`、`image_items[i].budget.references`、预渲染文案。

## 边界与已知限制

- **apimart 分支维持 4096 预缩放**（该通道未接预算阶梯，#500 未授权对其新增未知上限风险；如需一并废除另开票）。
- Pillow 12.2：JPEG 保存对字面量 `exif=None` 抛 TypeError（`info.get("exif", b"")` 取到 None），`encode_stage` 只传非空元数据；`Image.Exif.read()` 已移除，用 `Exif.load(bytes)`（需剥 `Exif\x00\x00` 前缀）。
- 测试 `sof_sampling` 夹具的定长段跳过为 `i + 2 + len`（原式 `i + 4 + len` 多跳 2 字节落在下一段内部）。

## 验证

- `tests/test_image_request_budget_500.py`（新增 14 例：原图字节一致、质量优先、底线、4:4:4+ICC/EXIF、透明 WebP alpha、水位冻结/重分、到底零请求+N、顺序/索引、passthrough 计量）。
- 旧 #493 断言按票面翻新的：`test_soon_url_default_493.py`（mandatory-4096 对、oversized-measured 对）、`test_image_generation_pipeline_493.py::test_real_native_reference_policy`、`test_image_request_budget_493.py`（stage 顺序、alpha 阶梯、基准重建、无损档废除、水位两轮语义、floor 文案）。函数名保留以稳定测试 ID。
