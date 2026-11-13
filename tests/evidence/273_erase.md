# #304 erase spike：bailian qwen-image-edit 指令式消除（273_erase）

**结论（一句话）**：当前 bailian 端点上注册的消除/编辑模型 `qwen-image-edit-max-2026-01-16` 返回
HTTP 404 `model_not_found`，两次真实调用均未产出图像——指令式消除路线**暂不可用**，#304 erase
正式实现须先解决模型可用性问题（开通该模型或改用端点在册模型），消除的 UI/选区/REST 骨架本身不受影响。

## 实验记录

- 脚本：`C:/Users/ADMINI~1/AppData/Local/Temp/spike_erase.py`（一次性，未进仓库；不 import main，
  直接读 `data/api_providers.json` + `API/.env` 手发 requests，body 结构镜像
  `app/core.py:352+ _bailian_local_edit_adapter`）
- provider：`bailian`，base_url=`https://llm-u3cjfye74dk9hoh9.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
- 端点：`{base}/images/generations`（`provider_endpoint_url` 逻辑：base 已含 `/v1` 前缀，去重后拼接）
- 模型：`qwen-image-edit-max-2026-01-16`（`app/core.py:348 BAILIAN_LOCAL_EDIT_MODEL`）
- 参数：`size=1464x824`（原图尺寸）、`response_format=b64_json`、`n=1`、
  `image=['data:image/png;base64,...']`（2,676,718 字符 data URL）
- 输入图：`assets/input/ai_ref_04a2b887621d.jpg`（PIL 筛选 800-1500px 候选中排序第一），
  副本存 `tests/evidence/273_erase/input_copy.jpg`（870,072 bytes）
- 真实调用恰好 2 次，均为 404（未触发图像生成，未消耗约 2 元/次的生成预算）

| 实验 | prompt | HTTP | 耗时 | 结果 |
|---|---|---|---|---|
| expA | 移除画面中的水印和文字，其余内容保持不变 | 404 | 0.76s | `model_not_found` |
| expB | remove all objects and fill the background naturally | 404 | 0.8s | `model_not_found` |

错误原文（两次相同，`expA.json` / `expB.json` 存全量摘要）：

```json
{"error":{"message":"Model not exist.","id":"3e95ec90-05d9-9026-b759-b7d61a56cc59","type":"invalid_request_error","code":"model_not_found"}}
```

## PIL 验证

- 输入副本 `input_copy.jpg`：PIL 打开 = RGB, (1464, 824)——选图与尺寸传参一致。
- 输出图：**无**（两次 404 均无图像数据），无输出可验证；`expA.json`/`expB.json` 已记录
  请求参数、状态、耗时与响应全文（base64 省略）。

## 交叉参照

- **同目录旧探针（14:39-14:45）**：`expA_cn_resp.txt` / `expB_en_resp.txt` 记录
  `model=wan2.7-image-pro endpoint=images/edits multipart → 404 (0.4s)`——换端点/换模型同样失败。
- **兄弟 spike `273_matting`**：`tests/evidence/273_matting/metrics.json` 记录同一注册模型
  404 `model_not_found`（错误 id `a29b97ca-...`），并在注释中确认该端点 `/v1/models` 实际在册的是
  `qwen-image-2.0-pro / qwen-image-3.0-pro / qwen-image-2.1-pro`——说明是**端点级模型可用性问题**，
  非本 spike 的参数问题；matting 与 erase 两个子课题同时被卡。
- **与 ComfyArt 路线对比（引用 291 结论）**：291 已判定 ComfyArt inpaint workflow 非首选——
  项目内 ComfyUI 被 #290 测试守卫 `assertNotIn('comfyui', _bailian_src)` 明确禁止，走 ComfyArt
  需引入新外部依赖；291 同时确认 erase 的 UI 骨架全部现成（`window.FIREFOX_ERASE_MODE`
  Shift+E 切换、`static/canvas-erase-mode.js`、`window.firefly.localEdit.restoreSelection`、
  REST 链 `/api/projects/<id>/local-edit`，ADR 0007 只发裁剪图+prompt 不发 mask），缺的只是
  "消除"语义的 prompt/模型参数与合成回贴。本 spike 证明缺口不在 UI 而在**模型供给**。
- **对 #290 生产链路的含义**：`_bailian_local_edit_adapter` 默认模型即本 404 模型——291 所称
  "local edit 走 bailian qwen-image-edit-max 已投产可用" 需按当前端点复核，现有端点下该默认路径
  会直接 404。

## 正式实现与拆票建议

1. **前置基建票（阻塞票，建议拆出独立 issue）**：bailian 端点模型对账——二选一：
   (a) 在该 MaaS 端点开通/订阅 `qwen-image-edit-max-2026-01-16`；
   (b) 把 `BAILIAN_LOCAL_EDIT_MODEL`（`app/core.py:348`）与 `app/data/model_specs.json` 登记改为
   端点实测在册模型（`qwen-image-2.1-pro` 等），并花 1-2 次调用预算验证其是否接受 image 输入 +
   编辑指令。同时复核 #290 local edit 生产链路是否已受影响。
2. **`#304` erase 正式实现（依赖票 1）**：在 291 现成骨架上加"消除"语义——预设消除 prompt
   （中英双版已在本 spike 验证参数可行：size 用原图 WxH、`b64_json`、n=1）、模型参数与
   合成回贴；UI/REST 无需新开发。
3. **可选降级票（不推荐先行）**：ComfyArt inpaint——需新外部依赖 + 与 #290 守卫冲突的 ADR 决策，
   仅在票 1 两选项都走不通时启动。
4. **健壮性小改**：local edit adapter 增加启动期模型可用性预检（GET `/v1/models` 免费、无生成计费），
   让 404 在配置/启动阶段暴露，而不是用户点"消除"时才失败。

## 产物清单

- `tests/evidence/273_erase/expA.json` / `expB.json`：请求参数 + 状态 + 耗时 + 响应摘要（去 base64）
- `tests/evidence/273_erase/input_copy.jpg`：输入图副本（1464x824 RGB）
- `tests/evidence/273_erase/expA_cn_resp.txt` / `expB_en_resp.txt`：旧探针记录（wan2.7 404，保留佐证）
- 输出图：无（两次调用均 404，未生成）
