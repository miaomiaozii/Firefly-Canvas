---
status: accepted
date: 2026-10-28
---

# 交给第三方上游的参考图必须公网化：三级阶梯，保真优先

2026-10-28 高清修复（fal-ai/topaz/upscale/image）真联调首次点击即失败：画布把站内参考图路径
`/api/project-library/<project_id>/media/<sha256>` 原样交给上游，上游拉不到本机地址，
只能由本地守卫拦下并报"参考图不是公网 URL"。关联：#3（fal.ai-all 适配）、
`docs/adr/0021-per-project-sqlite-sidecar-ops-db.md`（项目库媒体归属）。

## 决策

- **单一入口**：所有需要把参考图交给第三方上游的生成路径，必须先过
  `app/services/media.py:fal_reference_public_url(ref)`，不得直接把站内路径塞进上游请求体。
  该函数返回一个上游可拉取的 URL，失败时抛带可行动提示的 400，绝不静默丢图。
- **三级阶梯（顺序即语义，不可调换；三级落空即停下报错）**：
  1. 已是公网 http(s) 且非回环/私网 → 原样透传；
  2. **图床上传（原图直传）** → 复用 `API/utils/cloud_media.py:upload_local_video_to_cloud`
     的 catbox(72h) → litterbox(72h) → catbox(24h) 阶梯；
  3. **本地反代兜底** → 环境变量 `PUBLIC_MEDIA_BASE_URL` 拼站内路径（自建隧道/反代场景）；
  4. 三级全部不可用 → 抛 400，错误里点名两条出路（修图床网络 / 配 `PUBLIC_MEDIA_BASE_URL`）。
- **不做 base64 内联**（本 ADR 初稿曾列为第 4 级，已推翻）：fal 官方 OpenAPI（schemas.fal.ai，
  `fal-ai/topaz/upscale/image` 与 `…/generative` 的 input_schema）把 `image_url` 定义为
  `type: string` / "Url of the image to be upscaled"，没有 base64/data-URI 形态；
  而内联通道必经 `API/utils/media_io.py:reference_to_data_url` 的 2048/3072 上限缩放——
  对高清修复就是先降质再升质，任务语义直接被毁。因此宁可停下报错，也不能静默交一张缩过的图。
- **保真优先于便利**：高清修复的输入必须保持原分辨率，所以阶梯里排在最前的是原图直传，
  而不是任何会重新编码图片的通道。
- **不复用 `upload_image_to_cloud`**：`API/utils/cloud_media.py:455-457` 把站内路径前缀
  `/api/project-library/` 重写为 `/output/`（旧挂载假设），重写后的 URL 对图床是 404，
  会让图片上传器在真上传之前短路。因此新通道用 `output_file_from_url` 解析物理文件，
  再交给视频上传器（它对任意本地文件只认 `os.path.isfile`），并绕开前缀重写。
- **回环/私网判定**：`127.0.0.1`、`localhost`、`0.0.0.0`、`::1`、`10/8`、`172.16/12`、
  `192.168/16`、`169.254/16` 一律视为"站内"，即使写成 http 绝对 URL 也要公网化，不能透传。
- **上游错误必须可行动**：中转把 fal 原始错误包在 `message` 的字符串 JSON 里
  （外层只有 `fail_submit_task` 这类噪声码），`app/services/generation.py:fal_error_detail`
  负责挖出内层 `code`/`message` 并映射下一步（渠道无上游密钥 → 渠道侧问题；
  401 → 更新服务商 Key；422 → 检查 `fal_request.params`；404 → 核对 endpoint）。

## 影响

- 新增部署开关：`PUBLIC_MEDIA_BASE_URL`（未配置且图床不可用时，高清修复直接停下并报可行动错误，
  不会降级成缩图）。
- 能力声明与路由同构：`image_model_capabilities` 不得声明 `arbitrary_resolution`/
  `arbitrary_aspect_ratio`——那等于向路由谎称"8k/任意比例可出"，违反 #187 契约
  （没有模型支持时必须停下并点名参数）。高清修复的档位即语义：1k/2k/4k 对应
  `fal_request.model_by_resolution` 里的具体 Topaz 引擎，合法引擎名由
  `tools/configure_comfly_fal_topaz.py:FAL_MODEL_ENUMS`（按 fal 官方枚举 2026-10-28 核实）当场校验。
- 未来任何第三方"URL 拉取式"上游（不只 fal）共用同一入口与同一阶梯，不再各写一套。
- 隐私边界：走图床意味着把原图临时交给第三方图床，72h 后过期；对敏感素材应改用
  `PUBLIC_MEDIA_BASE_URL` 自建通道。
