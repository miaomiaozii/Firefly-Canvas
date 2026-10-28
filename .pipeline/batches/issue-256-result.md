# issue-256 结果：OCR→可编辑文本落 prompt 节点

## 选型决策（ADR-lite）

- **OCR 引擎：`rapidocr-onnxruntime==1.4.4`**（纯 Python + onnxruntime，CPU 推理，pip 离线可装，无系统级依赖）。
- **ADR 0004 判定**：0004 禁的是图片*生成*链路的进程内推理运行时（详见 docs/adr/0004）。OCR 属图片**轻量后处理**（文字识别，非生成推理），且端点只读项目已存在媒体、不产生活体运行时 → 判定**不冲突**，维持票面首选方案。切换成本被隔离在 `app/routers/api_ocr.py` 的 `_get_ocr_engine` + `ocr_bytes` 两个函数内（改外部 OCR API provider 即路由层不动）——判定与隔离依据都写在该文件头部注释。
- **移植来源**：ComfyArt `src/elements.ts:33-35` `runOcr` 交互模式（图片节点「提取文字」→ 服务端 OCR → 落可编辑文本），MIT 许可，已在 `NOTICE.md` 新增「第三方来源登记」节登记；实现代码为本项目原创。
- **端点形态**：`POST /api/ocr`，三源解析按优先级 `asset_id`（配 `project_id`，`project_store_call('media_record', ...)` 就地解析，与 `api_project_library.py:2996` 同款解析链）> 内部 URL `/api/project-library/{pid}/media/{aid}`（就地解析，前端 `img.url` 原样传入不发 HTTP）> `data:` base64 URL > `http(s)` 远端 URL。25MB 上限（413）、PIL 格式白名单 PNG/JPEG/WEBP/GIF/BMP（415）、同步 OCR 走 `asyncio.to_thread`、引擎单例延迟初始化（首次加载 ~800ms）。
- **UI**：右键普通图片节点打开既有创建菜单（此前直接 `return` 无任何菜单），菜单内「提取文字」卡片仅在图片节点上下文可见（`openCreateMenu` 按 `imageNodeId` 切 `hidden`）；点击后 `createPromptNodeFromOcr()` 走 `/api/ocr`，用 `canvasCore.nodeRect` 定位、`createPromptNode` **复用现有节点类型**落可编辑 prompt 节点（不新增节点形态），`source_node_id` 回链源图。
- **API/.env.example**：无需新增 key（rapidocr 本地引擎，无外部密钥）。

## 文件清单

| 文件 | 改动 |
|---|---|
| `app/routers/api_ocr.py` | 新增（214 行）：`/api/ocr` 路由 + ADR-lite 注释 |
| `tests/test_ocr_api.py` | 新增（163 行）：9 条单测（TDD 先行，接缝风格同 `test_canvas_asset_zip_report.py`） |
| `main.py` | 注册 `_r_api_ocr.router`（online_image 与 project_library 之间，字母序） |
| `requirements.txt` | 新增 `rapidocr-onnxruntime==1.4.4`（精确锁版本防 onnx 模型漂移） |
| `static/smart-canvas.html` | `#createMenu` 新增 `data-create-type="ocr"` 卡片（默认 hidden） |
| `static/js/smart-canvas.js` | `shell.oncontextmenu` 图片节点分支；`openCreateMenu` 记 `createMenuImageNodeId` + 切卡片可见性；新增 `createPromptNodeFromOcr()` |
| `static/js/canvas-node-ops.js` | `createNodeFromMenu` 加 ocr 分支；`createPromptNode` 支持 `options.title` |
| `static/js/i18n/smart-canvas.js` | 8 个 key 中英双补（createOcrTitle/Sub + ocrWorking/Done/Empty/Failed/NoImage/PromptTitle） |
| `NOTICE.md` | 新增「第三方来源登记」节：ComfyArt MIT 来源与引用范围 |

## 测试证据

| 项 | 结果 |
|---|---|
| `tests/test_ocr_api.py`（真实引擎跑真实 PNG） | 9/9 passed |
| `python tools/quality_gate.py fast` | passed（15s，含 javascript-syntax / surface-contract / egress-attribution） |
| 全量 pytest 对照（e879b6b 基线 vs 本分支） | 基线：1413 passed, 1 skipped（rc=0, 523s）；本分支：__待补__ |
| `python tools/quality_gate.py domain` | __待补__ |
| `python tools/quality_gate.py trial` | __待补__ |
