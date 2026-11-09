# Issue #290 结果：canvas.edit_erase + production local_edit_adapter

## 分支与提交

- 分支：`feat/290-edit-erase`（基于 main @5867c97；未 push、未 merge、main 无任何改动）
- 提交（3 个，均含绿色进度快照）：
  1. `188ead3` skeleton: canvas.edit_erase + bailian local_edit_adapter (#290) — TODO stubs（首 3 分钟内提交）
  2. `c4077ad` fix(#290): wire_local_edit_adapter 晚于 API_ENV_FILE 定义执行 + 半初始化回退（import main 绿）
  3. `f72a783` feat(#290): canvas.edit_erase 双面实现 + 生产 bailian local_edit_adapter——选区裁剪零 mask 送模型、结果合成回节点图、确认门+下一步建议；52 pytest 绿

## 文件变更（7 files, +684 / -4）

| 文件 | 变更 | 内容 |
|---|---|---|
| `app/core.py` | +101 | `_bailian_local_edit_adapter(request) -> bytes`（:352，同步，紧邻 :339 `_offline_local_edit_adapter`）：qwen-image-edit-max-2026-01-16（model_specs.json img2img+size），调用方式镜像既有图片生成链（provider_endpoint_url + api_headers + /images/generations，image 数组传 data URL/base64）；`images_api_unsupported` 时回退 /images/edits multipart（镜像 generation.py post_openai_edits）；`_bailian_http_session()` 测试缝；常量 `BAILIAN_PROVIDER_ID='bailian'` / `BAILIAN_LOCAL_EDIT_MODEL='qwen-image-edit-max-2026-01-16'` / `BAILIAN_LOCAL_EDIT_TIMEOUT_S=180`；`wire_local_edit_adapter(app_main) -> bool`：bailian 凭据存在才把生产 adapter 挂到 `app_main.local_edit_adapter`，凭据查询崩溃时回退进程环境变量 `API_PROVIDER_BAILIAN_KEY`（绝不拖垮启动） |
| `main.py` | +4/-1 | 导入并调用 `wire_local_edit_adapter(main)` —— 位置在 `load_env_file()` 之后（须晚于 `API_ENV_FILE` 定义，:1100） |
| `app/routers/api_agent_tools.py` | +82 | `erase_node_image_core(project_id, node_id, rect, prompt)`（:1151 区）：校验节点/rect/prompt（错误文案带下一步建议：canvas.list_nodes / canvas.get_node / media.upload / canvas.view / canvas.delete_node）→ `validate_rect` + `crop_rect` + `encode_png` 得裁剪区字节 → 构造与 `create_project_local_edit`（api_project_library.py:3546）同形的 `request_data`（mode=modify、selection=归一化 [l,t,r,b]、size=选区 WxH）→ `_app_main.local_edit_adapter`（支持同步/协程/awaitable 三态，与 REST 链一致）→ `as_rgba` + `composite_rect` 合成回原图像素坐标 → `_replace_node_image` 覆写节点图（原资源保留）；结果含 `rect`/`selection`/`next_step`；adapter 异常 → 502「局部编辑模型调用失败…检查 bailian 凭据/网络（原图未改动）」；新 REST 路由 `POST /api/projects/{project_id}/canvas/edit_erase` |
| `app/routers/api_mcp.py` | +14/-1 | TOOLS 23→24：`canvas.edit_erase`（node_id + rect{ x,y,width,height, additionalProperties:false } + prompt(minLength 1)，required=[node_id, rect, prompt]，描述声明确认门/ADR 0007）；`CONFIRMATION_GATED_TOOLS` 加入（:92）；`_impl_edit_erase`（确认门先行 + core 调用 + HTTPException→ValueError）；TOOL_IMPLS 接线 |
| `app/agent_extensions/canvas-tools.ts` | +48 | `registerTool("canvas.edit_erase")`：完整 Type.Object 参数（rect x/y/width/height + prompt）；execute 镜像 edit_outpaint——`/agent/confirm` 确认门（拒绝/超时分支）→ `POST /canvas/edit_erase`（与 Python TOOL_IMPL 同一 REST 端点） |
| `tests/test_mcp_endpoint.py` | +2/-2 | 守卫 23→24 + 期望名加入 canvas.edit_erase |
| `tests/test_mcp_canvas_edit_erase_290.py` | +433（新） | 17 个测试，零真实网络/零真实凭据：adapter 单测（fake http 会话：b64 响应解码字节 + body 契约含 ADR 0007 无 mask 断言、URL 下载、400+unsupported→/images/edits multipart 回退、500→RuntimeError、空 prompt/空图 ValueError、provider 未注册 RuntimeError）；wire 单测（凭据在→替换、无凭据→保留离线 fake、查询崩溃→进程环境变量回退）；MCP 面（24 项、严格 schema、确认门声明、gate/impl 接线、model_specs 登记）；确认门 E2E（批准：选区像素=模型色、选区外=原色、adapter 请求契约 mode/selection/size、原资源保留、next_step 含 canvas.view；拒绝：McpToolError「拒绝」且零 adapter 调用；校验错误下一步建议：越界/非正 rect/非 dict rect/缺节点/无图节点/adapter 失败 502 提示） |

## 测试计数

- `python -m pytest tests/test_mcp_endpoint.py tests/test_mcp_canvas_edit_erase_290.py -q` → **52 passed**（endpoint 守卫 35 + 290 新增 17）
- `node --test tests/canvas_module_units/test-agent-canvas-tools.mjs` → **7 passed**
- 无回归：289 outpaint 测试套件随 quality_gate domain 全量通过

## 门（全部绿）

- `python tools/quality_gate.py fast` → passed (16.4s)
- `python tools/quality_gate.py domain` → passed (131.3s)
- `node --test tests/canvas_module_units/test-agent-canvas-tools.mjs` → pass 7 / fail 0
- `python -m pytest tests/test_mcp_endpoint.py tests/test_mcp_canvas_edit_erase_290.py -q` → 52 passed

## TODO 偏差与说明

- **无遗留 TODO**：skeleton 的三个 stub（adapter、core、TS execute）全部实现完毕。
- ADR 0007 遵守：core/adapter 全程无 mask 字段（REST 链先裁剪，模型只收裁剪图 + prompt）；测试显式断言 body/请求契约中无 `mask`。
- 生产 adapter 在本机有 bailian 凭据 → `import main` 后 `main.local_edit_adapter is _bailian_local_edit_adapter`（已验证）；无凭据环境自动保留 `_offline_local_edit_adapter`。
- 未发生任何真实付费 API 调用（所有网络路径均被 stub：`_bailian_http_session` / `get_api_provider_exact` / `api_headers` / `main.local_edit_adapter`）。
- 环境注意：`wire_local_edit_adapter` 必须在 `main.py` 的 `load_env_file()` 之后调用（`provider_env_key_value` 依赖 `main.API_ENV_FILE`，:1100 才定义）——这是 skeleton→c4077ad 修复的实际坑，wire 函数内置半初始化回退兜底。
- 未动：`.pipeline/batches/plan-273-edit-capabilities.md`、gh issues 及任务清单外的任何文件（工作区 5 个 pre-existing untracked 文件保持原样）。
