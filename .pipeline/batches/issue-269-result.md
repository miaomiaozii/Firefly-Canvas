# issue-269 结果报告（图层面板全链浏览器旅程证据：#255 验收第 1 条的浏览器证据）

**状态：完成**（票面要求全部落地：新 runner `tests/layers_panel_browser.py` 覆盖 创建多层 → 切换显隐 → 拖拽重排 → 导出合成 PNG 像素断言 全链；注册进 `quality_gate` trial/release 并行组；只加测试与最小必要注册，未碰产品代码，未碰 fal/generation/media/agent_tools；`quality_gate fast` 绿 + 门契约测试全绿，见文末验证证据表）

分支 `issue/269`（基于 `origin/wave1-live` @ `cb2e8f3`，HEAD `bbe7d9b`），1 commit。已 push 并 assign `miaomiaozii`。

## Commits

| commit | 内容 |
|---|---|
| `bbe7d9b` | #269: browser journey evidence for the #255 layers panel（新 runner + client + quality_gate 注册 + 门契约测试更新） |

## 票面要求落地

### ① 新 runner：`tests/layers_panel_browser.py` + `tests/layers_panel_browser_client.js`

复用既有旅程 runner 模式（`image_editor_func_browser.py` 同款骨架）：

- Python 侧：临时 `ProjectStore`（`snapshot_delay=0`）种入一张 **640x480 纯色 PNG**（底色 `rgb(60,64,96)`）+ 一个 `smart-image` 节点 `edit-node`；`tests.conversation_browser` 基建起本地生产 app 服务 + headless Chrome；`finish_run("layers-panel", runs)` 收尾。
- Client 侧（页面内 JS，原生事件、无 Playwright）：等待 `canvasState.nodes` boot → `openImageEditor('edit-node', 0)` 并断言 `#cropImage` 为原图尺寸 640x480（非 2048 预览）→ `setImageEditMode("layers", true)` 进入图层模式。

### ② 全链五段旅程（33 断言）

1. **创建多层**：`#editDrawCanvas` 画纯色矩形（红 `rgb(255,0,0)`，归一化 0.10..0.45 x 0.10..0.45）→ 点 `#layerImportBrushBtn` 导入笔刷像素成层；`#layerAddBlankBtn` 加空白层；再画蓝 `rgb(0,0,255)`（0.25..0.60 x 0.10..0.45，与红有重叠区 0.25..0.45）导入。断言显示序逐层为 蓝、空白、红、`layer-base`。
2. **导出 1（初始层序，蓝在红上）**：采样点断言——重叠区像素=蓝（顶层胜出）、红独占=红、蓝独占=蓝、未触碰=底色。
3. **切换显隐**：点行内 `.layer-vis` 隐藏蓝 → 导出 2（重叠区变红、蓝独占区回退底色）；再隐藏红 → 导出 3（全部回退底色，导出按钮仍启用，因为还有可见层）；四个层全隐藏 → 断言 `#layerExportBtn` 置灰（产品逻辑 `btn.disabled = visibleLayers(store).length === 0`，`static/js/canvas-layers.js:408`）；全部恢复 → 按钮重新启用。
4. **拖拽重排**：走面板绑定的 HTML5 DnD 事件链（`dragstart`/`dragover`(带 `DataTransfer` 与行内 clientY 定位插入点)/`dragend`）把蓝行拖到红行之下 → 断言面板新序 空白、红、蓝、底图 → 导出 4（重叠区变红=红升顶）；再拖回顶部 → 导出 5（重叠区恢复蓝，round trip 一致）。
5. **像素来源即产品导出产物**：产品 `exportCompositePng()` 走离屏 `canvas.toBlob` 生成真实导出 PNG 字节；client 在 `HTMLCanvasElement.prototype.toBlob` 包一层记录真实字节（原回调照常执行），再 `createImageBitmap` 解码采样。断言的就是产品导出函数产出的像素，不依赖 headless 下载落盘位置。PNG 无损 + 纯色填充，容差 16 仅防边缘抗锯齿。

### ③ 注册进 quality_gate（最小必要注册）

`tools/quality_gate.py` 4 处：

- `STEP_DEFINITIONS` 增 `"layers-panel-evidence": python tests/layers_panel_browser.py`（附注释说明按 #167/#185 先例）。
- trial / release 步骤元组各插入 `"layers-panel-evidence"`（位于 `canvas-boundaries-evidence` 之后）。
- `BROWSER_EVIDENCE_STEP_NAMES` frozenset 增该步（进并行组）。

fast/domain **不含**该步，预算不受影响（浏览器证据步预算紧张，按票面走「挂进既有模式」的稳妥路线：trial/release 已有多个并行浏览器证据步，新步并行执行不额外增加最坏预算；门契约预算测试已验证 trial 300s / release 900s 预算仍成立）。

`tests/test_quality_gate.py` 3 处配套（门只验接线，产品行为由真门跑验的既有套路）：

- `setUp` fixture 桩元组增 `layers_panel_browser.py`（最小桩，同 `canvas_boundaries_browser.py` 等 4 个 runner 套路）。
- `test_all_modes_are_cumulative_and_keep_their_declared_budgets` 的 trial/release 期望步骤列表各插入 `layers-panel-evidence`。
- 注释标注 #269。

## 验证证据

| 项 | 结果 |
|---|---|
| 新 runner 本地运行数 | **共 3 次：第 1 次失败（测试自身断言逻辑缺陷，见下），最终形态连续 2 次通过**（第 2、3 次均 33/33 断言通过，`PASS: layers-panel browser checks passed (33 assertions)`，各约 4s 纯旅程时间） |
| 第 1 次失败原因 | 测试自身期望错误：期望「隐藏 base 层后导出按钮置灰」，但空白层仍可见时按产品逻辑（`visibleLayers().length === 0` 才禁用）按钮应保持启用。属测试断言缺陷而非产品 bug，已修正为「四层全隐藏 → 按钮置灰」。 |
| 产品代码 bug | **未发现**。全链行为（导入笔刷成层、空白层、显隐切换、DnD 重排、按层序离屏合成导出、导出按钮启用逻辑）均与产品代码一致。 |
| `python tools/quality_gate.py fast` | **passed**（verdict=passed，final_exit_code=0，15.75s） |
| `pytest tests/test_quality_gate.py` | **95 passed, 17 subtests passed, 0 failed**（4m27s；含 trial/release 真实跑门 + 预算断言 + 并行组断言） |
| 约束核对 | 未改任何产品代码（`git diff --stat`：仅 `tests/test_quality_gate.py` +5/-1、`tools/quality_gate.py` +6、两个新测试文件）；未触碰 fal/generation/media/agent_tools。 |

## 已知边界

- 像素断言基于「纯色填充 + PNG 无损 + 采样点深入区域内部避开矩形边缘」的确定性前提；采样点/矩形坐标常量定义在 client 内（与 seed 图对应），改 seed 图需同步。
- 拖拽走产品绑定的 DnD 事件处理器（headless Chrome 下真实 pointer 拖拽不可靠，事件链驱动是既有旅程 runner 的通行做法），能覆盖产品 `dragstart/dragover/dragend` 全部逻辑分支，但不覆盖操作系统级拖拽手感。
- 导出捕获用 `toBlob` 原型插桩（仅测试运行时生效，跑完即还原环境）；产品代码零改动。
