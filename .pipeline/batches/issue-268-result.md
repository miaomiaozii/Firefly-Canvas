# issue-268 结果报告（聊天附件扩视频/音频 contact sheet——#260 拆出遗留切片）

**状态：完成**（票面要求全部落地：聊天附件接受视频/音频；视频→带时间戳 contact sheet 作为模型可见输入；音频→保守波形卡（不转写、不引重依赖）；i18n 中英；TDD；`quality_gate fast` + `domain` 绿 + 全量 pytest 对照 `origin/int-256` 基线零新失败，见文末验证证据表）

分支 `issue/268`（基于 `origin/int-256` @ `d6cd3c7`，HEAD `ac408dc`），3 commits。

## Commits

| commit | 内容 |
|---|---|
| `f0cc5ef` | feat(chat-media): #268 视频 contact sheet / 音频波形卡服务——抽帧+时间戳拼贴、WAV 波形卡，无重依赖 |
| `76005d1` | feat(api): #268 /api/chat-media 端点——POST video-contact-sheet 与 audio-card（multipart，错误体带 detail） |
| `ac408dc` | feat(canvas-tui): #268 聊天附件 UI——📎 按钮/待发附件行/移除，prompt 载荷带 images（视频/音频经后端转图），i18n 中英 |

## 票面要求落地

### ① 聊天附件接受视频/音频（前端 TUI）

`static/js/canvas-agent-tui.js`：

- 输入行加 📎 按钮 + 隐藏 `<input type="file" accept="image/*,video/*,audio/*" multiple>`（`data-agent-attach` / `data-agent-tui-file`）。
- 图片附件 → FileReader dataURL 直入待发队列；视频 → `POST /api/chat-media/video-contact-sheet`；音频 → `POST /api/chat-media/audio-card`；两者返回的 `data_url` 作为普通图片附件入队。
- 待发附件行（`agent-tui-attach-row`）：缩略图 + 文件名 + 说明（视频 "视频 x → contact sheet（N 帧，a s–b s）"；音频 "音频 x → 波形卡（未转写）"）+ 移除按钮；发送前可逐条移除。
- `sendAgentPrompt` 归一化：`images` 元素可为 `{dataUrl,name,caption}` 对象，发给后端的 WS/HTTP payload 统一为 dataURL 字符串列表；user 消息记录 `attachments` 文件名列表（消息下 📎 小字展示，可追溯）。
- 运行中（busy）Enter 走 follow-up 通道（该通道无 images 参数），附件保留在队列随下一条 prompt 发出。
- 失败（后端 4xx/5xx 或类型不支持）→ 会话内 system 消息提示，不弹框。

i18n（`static/js/i18n/smart-canvas.js` 追加 register 块，中英）：`smart.agentAttachFile` / `agentAttachRemove` / `agentAttachVideoReady` / `agentAttachAudioReady` / `agentAttachUnsupported` / `agentAttachFailed`。

### ② 视频 → contact sheet（模型可见输入）

新模块 `app/services/chat_media_attachments.py`（385 行）：

- `probe_duration(path)`：ffprobe 时长；`extract_frames(path, count, max_size)` 按时长均匀 seek（`times[i] = duration·i/(count-1)`，末帧钳到 `duration-0.05`），**seek 失败向前回退重试** delta ∈ {0, 0.05, 0.1, 0.2, 0.3, 0.5}（防 seek 点落在流末尾）；时长未知 → `fps=1` 顺序抽帧兜底；帧临时目录由 `cleanup_frame_dir()` 在拼贴后清理。
- `build_contact_sheet(frames, max_px=SHEET_MAX_PX)`：≤4 帧 2 列、>4 帧 3 列、4:3 单元格 + 每行黑色时间戳标签条（`{t:.2f}s`，msyh 字体），JPEG q=88。
- `video_contact_sheet_from_path(path, frames, max_size)` → `{data_url, duration_s, frames_at_s, name, kind:'video_contact_sheet'}`；frames 钳到 [1, `VIDEO_MAX_FRAMES_CAP`=8]，默认 `VIDEO_MAX_FRAMES_DEFAULT`=4（chat 附件限额；上游参考 design 的 FRAMES=16/SHEET_PX=1536 属视频节点 hover 场景，chat 附件取小限额控 token）。
- 依赖仅 ffmpeg/ffprobe 子进程 + 既有 Pillow；`shutil.which` 探测，缺失时 `MediaAttachmentError` 明确报错。

### ③ 音频 → 保守波形卡（不转写）

- `audio_card_from_path(path, name)` → `{data_url, name, duration_s, kind:'audio_card', transcription: None}`：ffmpeg 解码前 N 秒为 mono float32 → 归一化 0..1 包络 → 深色波形卡 JPEG（文件名 + 时长标注）。WAV 纯标准库 wave 即可测试，零第三方新增依赖。
- **选型需维护者决策**：上游参考 design（`docs/reference-comfyart-canvas.md:435`）音频策略是"先本地转写"，但本仓库无既有 ASR/外部转写 API 通道（grep 无 whisper/stt 基建）。按票面约束"勿私自引重依赖"，本票只做保守波形卡（`transcription: None` 占位，后续转写能力接入时同结构扩展），**未实现转写**——需维护者选型（本地 whisper / 外部 ASR API）后另开票。

### ④ API 端点

`app/routers/api_chat_media.py`（139 行）+ `main.py` 按既有字母序 idiom 注册：

- `POST /api/chat-media/video-contact-sheet`（multipart `file` 或 `url` 表单字段；`frames` 表单参数；站内引用走 `main.output_file_from_url` 兜底 getattr）
- `POST /api/chat-media/audio-card`（同入参形态）
- 重活 `asyncio.to_thread`；`MediaAttachmentError`→422 detail；缺媒体 400；找不到文件 404；下载失败 400。

## 硬约束合规

- **禁区零接触**：未改 `app/agent_tools.py` / `app/services/agent_host.py` / fal 相关文件 / `app/services/generation.py` / `app/services/media.py`——无冲突点，无需停摆报告。视频抽帧走独立新模块，不复用 `media.py` 内任何函数。
- 新文件 ≤600 行：service 385 / router 139 / 测试 260。
- i18n 中英 6 键；TDD（先固定 service/router 行为再实现，mjs 用例同步扩展对象附件归一化断言）。
- 上游 #246 视频 hover/抽帧基建为视频节点场景（禁区 `media.py` 领地），本票仅复用其"ffmpeg 抽帧 + 时间戳拼贴"设计概念，代码全部新增。

## 验证证据

| 项 | 结果 |
|---|---|
| 新增单测 `tests/test_chat_media_attachments.py` | 17 passed（unittest 风格，pytest 与质量门 TestLoader 均可收集；视频用例依赖本机 ffmpeg，实测通过；WAV 用例标准库生成） |
| 模块单测 `tests/test_canvas_module_units.py`（node mjs 面） | 22 passed（含 #268 扩展：`sendAgentPrompt` 对象附件→dataURL 归一化 + `attachments` 记录断言） |
| `python tools/quality_gate.py fast` | passed（15.9s，exit 0，evidence `20261029-005727-fast-62316`） |
| `python tools/quality_gate.py domain` | passed（139.2s，exit 0，**一次通过**，无并发 chrome 失败，evidence `20261029-005751-domain-40872`） |
| 全量 pytest（本分支） | 1531 passed, 1 skipped, 0 failed（539.8s） |
| 全量 pytest 基线（`origin/int-256` @ d6cd3c7，`/d/ff-wt/pytest-268-baseline.log`） | 1514 passed, 1 skipped, 0 failed（530.4s） |
| 对照结论 | 差值 17 = 本票新增测试数；**0 新失败，无回归** |

## 遗留 / 建议

- 音频转写选型（本地 whisper vs 外部 ASR API）需维护者决策后另开票；本票 `transcription` 字段已占位，接入时同结构扩展。
- 视频附件当前固定小限额（≤8 帧/默认 4 帧）控 token；如需"更多帧用 view_video"式升级（上游 design `:3200`），依赖会话 B 的 agent_tools 领地，另票处理。
