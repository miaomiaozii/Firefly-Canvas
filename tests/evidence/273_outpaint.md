# Spike(#304) 路线 A：bailian 指令式 outpaint 能力验证（整图指令外扩）

## 结论（一句话）

**路线 A 端点可行但属"整图重渲染"而非像素保留**：bailian 端点在册模型 `qwen-image-3.0-pro` 接受 image 输入 + 更大 size，输出尺寸精确等于请求尺寸（897x845 → 1256x1184），四周确实被向外扩展、背景与主体语义延续；但原图内容被整图重绘（中央区 50% 平均像素差 92.948/255），不是"原像素不动 + 外扩"，若用于画布回填需把原图中央区再合成回去（或接受重渲染）。

## 实验

| 项 | 值 |
|---|---|
| 端点 | `https://llm-u3cjfye74dk9hoh9.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`（api_providers.json 注册项 `bailian`，协议 openai） |
| 模型列表 | `GET /models`（Bearer 凭证经 `provider_env_key_value('bailian')` 从主仓 `API/.env` 取）→ HTTP 200，**182 个模型** |
| 列表中的图像模型 | `qwen-image-2.0`、`qwen-image-2.0-pro`（含 2026-03-03 / 04-22 / 06-22 快照）、`qwen-image-2.1-pro`、`qwen-image-3.0`、`qwen-image-3.0-pro`、`wan2.7-image(-pro)`、`z-image-turbo` |
| qwen-image-edit 系列 | **均不在册**（无 edit-plus、无 edit-max）；`qwen-image-edit-max-2026-01-16`（`app/core.py:348` 的 `BAILIAN_LOCAL_EDIT_MODEL`）404 与列表相互印证——现端点"编辑"能力由标准 `qwen-image-*-pro` 的 image 输入承载 |
| 选用模型 | `qwen-image-3.0-pro`（优先序 3.0-pro > 2.1-pro > 2.0-pro 中列表在册的第一个） |
| 调用链 | `app/core.py:_bailian_local_edit_adapter`（未改动）→ `POST /v1/images/generations`（JSON，`image=['data:image/png;base64,...']`，`size`，`response_format=b64_json`）；未触发 `/images/edits` multipart 回退 |
| 输入图 | `D:\Firefly-Canvas\assets\input\ai_ref_10073401ad09.png`，PIL 实测 **897x845** RGB（副本存 `tests/evidence/273_outpaint/in.png`） |
| prompt | `向外扩展画面四周，延续背景与构图，不改变原图已有内容` |
| 请求 size | `1256x1184`（原图 ×1.4，8 的整数倍取整） |
| 延迟 / 输出 | 78.8s，`outpaint_out.png` 1,011,531 字节 |

## PIL 验证（脚本 `tests/evidence/273_outpaint/spike.py` 自动执行，原始数据见同目录 `result.json`）

- 输出尺寸 PIL 实测 **1256x1184** RGB：
  - **等于请求尺寸** → `output_matches_requested = true`
  - 不等于原尺寸 → `output_matches_original = false`
- 原内容是否被重绘（中央区像素统计）：输出缩回 897x845 后取中央 50% 区域（box (224,211,672,633)）与输入逐通道平均绝对差 **MAD = 92.948**（0-255 每通道）。
  - 参考阈值：<1.0 像素级一致；1.0-5.0 轻微重绘；**>5.0 明显整图重绘** → 本例远超，判 **heavy-redraw**。
- 目测核对（`in.png` vs `outpaint_out.png`）：同一男性、同一黑色立领外套与橙色格纹内衬、同一纯白背景，构图语义一致；但取景变宽（头部上方与下方留白增多、人物占比变小），**面部细节被重绘**（相似但非逐像素相同）。四周扩展区域为白底延续，符合"外扩"预期。

## 判定

1. **尺寸扩展能力：✅ 成立** —— 输出严格等于请求的更大尺寸，模型按指令把画面整体取景放宽、外扩背景。
2. **原内容保留：❌ 非像素级** —— 整图重渲染（MAD 92.948），语义/身份保留、像素不保留。
3. **可用模型面**：该端点走 `qwen-image-3.0-pro`（列表在册、优先序最高）；`qwen-image-2.0/2.1/3.0` 系列与 `wan2.7-image` 也在册，edit-plus/edit-max 不可用。
4. **对 #273 outpaint 正式化的含义**：路线 A 可产出"更大画布 + 语义一致"的图；若产品要求原区域像素不变（画布局部回填场景），需在应用层做二次合成——以原图中央区覆盖输出图中央区（或只取输出的外扩边缘带拼回），不可直接用模型输出回填。

## 复现

```bash
python D:\ff-wt\w304\tests\evidence\273_outpaint\spike.py
# 依赖：D:\Firefly-Canvas 主仓 API/.env（bailian 凭证）+ data/api_providers.json
# 产物：tests/evidence/273_outpaint/{in.png, outpaint_out.png, result.json}
# 预算：本脚本 1 次模型列表调用（非图像）+ 1 次图像调用（≤2 次预算内）
```
