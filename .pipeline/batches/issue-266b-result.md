# issue-266b 结果报告（#266 尾票：batch_edit + edit_* 扩面）

**状态：完成**（两条熔断线全过：保命线 batch_edit + 第二条 edit_* 再扩两项本地变换；票面主项 canvas.view/view_media/edit_crop 已在 wave1-live 合入，本尾票补齐工具面剩余项；TDD；fast 门绿；见文末验证证据表）

分支 `issue/266b`（基于 `origin/wave1-live` @ `3068a73`），2 commits，均已 push。

## Commits

| commit | 内容 |
|---|---|
| `f290cdb` | feat(#266b): canvas.batch_edit（§59.3：N 节点+一句 prompt=N 次生成） |
| `0af5dd7` | feat(#266b): canvas.edit_rotate / canvas.edit_flip（edit_* 再扩两项本地 PIL 变换） |

## 票面要求落地

### ① batch_edit（保命线，§59.3 语义）

票面验收原话（`gh issue view 266`）：batch_edit = 选中 N 个节点 + 一句 prompt = N 次生成，**结果放各自旁边**——即调用既有生成通路，**非本地 PIL 处理**。spike（30 行内）确认：连续 N 次调用既有 `trigger_generation_core` 通路安全（独立 CANVAS_TASKS 任务 + 后台运行，互不阻塞），无需新任务机制。

- `app/routers/api_agent_tools.py`：`batch_edit_core(project_id, node_ids, prompt)` —— 校验（node_ids 非空字符串数组、单次 ≤ `BATCH_EDIT_MAX_NODES`=10、prompt 非空；错误文案带下一步建议：canvas.list_nodes / 拆批 / 填 prompt）→ 逐节点把 batch prompt 写入节点（upsert_node）→ 走既有 `trigger_generation_core`（消耗 N 个外部生成任务）→ 返回 `{success, count, prompt, tasks:[{node_id, task_id}]}`。端点 `POST /api/projects/{project_id}/canvas/batch_edit`。
- `app/agent_extensions/canvas-tools.ts`：`canvas.batch_edit` 工具声明（node_ids 1..10 + prompt；不走确认门——与 generate.trigger 一致，生成类写操作由生成流自身管控）。
- `app/routers/api_mcp.py`：TOOLS 声明表（严格 schema，minItems 1 / maxItems 10）+ TOOL_IMPLS 映射。

### ② edit_* 再扩两项（第二条线，本地 PIL 变换）

选择 rotate + flip：票面允许范围内的高价值本地操作，且包装 `canvas-image-editor.js` 既有能力（该模块首行即「裁剪、旋转、预览导航与对比」——rotate 是既有能力；flip 同族本地变换）。与 edit_crop 同模式：**本地 PIL 处理覆写节点图、纯本地不耗生成、写操作走 #242 确认门**（批准/拒绝分支），新资源入资源库、原资源保留可恢复。

- `local_edit.py`：
  - `rotate_image(image, angle)` —— 90/180/270 逆时针（PIL `transpose` 语义，{90:ROTATE_90, 180:ROTATE_180, 270:ROTATE_270}），其余角度/非数字拒绝（ValueError 带 90/180/270 提示）。
  - `flip_image(image, direction)` —— horizontal=左右镜像（FLIP_LEFT_RIGHT）/ vertical=上下镜像（FLIP_TOP_BOTTOM），其余方向拒绝。
- `app/routers/api_agent_tools.py`：
  - `rotate_node_image_core(project_id, node_id, angle)` / `flip_node_image_core(project_id, node_id, direction)` + 端点 `POST /canvas/edit_rotate|edit_flip`；错误文案带下一步建议（canvas.get_node / 合法值提示 / 缺节点 canvas.list_nodes）。
  - crop 写回路径拆出共享 `_load_node_image`（404 资源不存在 → media.list 建议；400 图像无法读取）/ `_replace_node_image`（新资源 import_media + 覆写节点 images 条目 + 同步 output_asset_id + upsert_node），crop/rotate/flip 三 core 复用。
- `app/agent_extensions/canvas-tools.ts`：两工具声明（angle: 90|180|270 字面量联合；direction: horizontal|vertical 字面量联合；先调 `/agent/confirm` 确认门，拒绝返回 approved:false）。
- `app/routers/api_mcp.py`：
  - TOOLS 声明表（严格 schema：additionalProperties:false、enum 限定，描述含「确认门」声明）；
  - `CONFIRMATION_GATED_TOOLS` 扩入 `canvas.edit_rotate` / `canvas.edit_flip`（3→5）；
  - `TOOL_IMPLS` 加 `_impl_edit_rotate` / `_impl_edit_flip`（gate 先行后执行 core，HTTPException→ValueError，同 `_impl_edit_crop` 顺序）。

## 测试（TDD，先红后绿）

- `tests/test_mcp_canvas_batch_edit_266.py`（7 例，f290cdb 合入）：严格 schema / 确认门外置 / N 任务 + N prompt 写入 / 缺节点·空 prompt·空 node_ids·超 10·缺项目 错误文案。
- `tests/test_mcp_canvas_edit2_266.py`（9 例，0af5dd7 合入）：
  - 声明表：两工具进 api_mcp.TOOLS + 严格 schema（additionalProperties:false、required、enum）+ 描述含「确认门」+ 进 CONFIRMATION_GATED_TOOLS/TOOL_IMPLS；
  - 确认门：批准旋转（16x8→8x16 宽高互换 + 节点图替换为新资源 + 原资源保留在资源库）/ 批准水平翻转（**像素级断言**：左半红右半蓝图翻转后 (1,4) 变蓝、(14,4) 变红）/ 拒绝不动节点（McpToolError 含「拒绝」）；
  - 错误文案带下一步：非法 angle（45）含 90/180/270 / 非法 direction 含 horizontal+vertical / 无图节点含 media.upload / 缺节点含 node_id + canvas.list_nodes。
- `tests/test_mcp_endpoint.py`：声明表计数 19→20（batch_edit）→22（rotate/flip），工具名单同步。

## 验证证据

| 门 | 结果 |
|---|---|
| `pytest tests/test_mcp_canvas_view_266.py tests/test_mcp_canvas_edit_266.py tests/test_mcp_canvas_batch_edit_266.py tests/test_mcp_canvas_edit2_266.py tests/test_mcp_endpoint.py` | 69 passed |
| 声明表 secret/declaration 表测试（13 例） | 全绿未弄红 |
| 禁区 | 未触碰 `static/js/agent/`、`app/services/generation.py` |

## 尾项（未做，登记备查）

- 调色类本地操作（如 edit_adjust 亮度/对比度）未做——票面允许范围内剩余项，两条熔断线已全过后按「时间不够就保命线收口」纪律收口；如需续做，直接复用 `_load_node_image`/`_replace_node_image` 共享写回 + 同 edit_crop 三处齐样板。
- 前端确认门 UI 对 rotate/flip 的展示（消息文案已随 impl 给出「旋转/镜像节点 X 的图像并覆写节点图（原资源保留在资源库）」）——画布 TUI 侧无新交互需求，通用确认弹层直接复用。
