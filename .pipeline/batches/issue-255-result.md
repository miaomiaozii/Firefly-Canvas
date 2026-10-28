# Issue #255 结果：图像分层 + 图层面板（MIT 移植 layers.ts/vectorPath.ts + 分数坐标骨架）

分支 `issue/255`（基线 b21aae1，5 commits：cb883b1 → 3ff7878 → ce29540 → 079cba7 → a003654）

## 1. 分数坐标骨架（第 1 步，地基）

- `static/js/canvas-geometry.js`（163 行，`window.CanvasGeometry`）：0..1 公共坐标纯函数——
  `clamp01` / `pointToFractional` / `pointToPixel` / `rectToFractional` / `rectToPixel` / `normalizeRect`（拖拽任意方向归一）/
  `pointInPolygon`（even-odd 射线法，自研）。
- `static/js/canvas-image-editor.js` 接线（3ff7878）：`editDrawPoint`（brush/mask/text 共用入口）与 crop 拖拽的
  client→显示像素换算统一改走 `CanvasGeometry` 纯函数，行为不变（既有 test-canvas-image-editor.mjs 35 例全绿 + 新增换算用例）。

## 2. MIT 移植（第 2 步）

移植对照表（源：ComfyArt 参考版本 v1.1.0 @ 13396a8；本地审计版 v1.0.1 @ 515d36e 逐行一致；MIT，Copyright (c) 2026 Terry Jia，已保留于文件头 + NOTICE.md 全文登记）：

| 源（ComfyArt） | 目标（Firefly-Canvas） | 说明 |
| --- | --- | --- |
| `src/vectorPath.ts` `scalePath` | `static/js/canvas-vector-path.js` `window.CanvasVectorPath.scalePath` | 逐式移植（含原版怪癖：含 `e` 的 number token 被指令判断吞掉 → 单测钉住） |
| `src/vectorPath.ts` `nodesToPath` | `window.CanvasVectorPath.nodesToPath` | 逐式移植（bbox 含手柄三点、w/h 下限 1、closed 追加末→首段+Z） |
| `src/layers.ts` `measure` | `static/js/canvas-layers.js` `window.CanvasLayers.measureLayer` | 去 media 依赖；OPAQUE=128 / SOLID=200 / diff 999 哨兵 语义保留 |
| `src/layers.ts` `overlap` | `window.CanvasLayers.layerOverlap` | IoU（alpha>128）逐式保留 |
| `src/layers.ts` `cropLayer` | `window.CanvasLayers.layerAlphaBBox` / `cropLayerData` | 上传部分不迁，改返回像素缓冲；alpha<=8 视为空 |
| `src/layers.ts` `separateRegions` 像素循环 | `window.CanvasLayers.splitLayerByBoxes` | 分数坐标 box → 区域像素，消费像素就地清零 |
| `src/layers.ts` `regionSplit` 掩膜合成 | `window.CanvasLayers.composeWithMask` | 忠实原版：判断用 `m[p]`（灰度图 RGBA 的 R 通道）而非 alpha；mask≥128 保留原像素、alpha 置 255、union 累积 |

**票面纠偏（重要）**：
- 票面称 `vectorPath.ts` 431 行——参考仓库中实际 **54 行**（两个参考版本一致）。
- 票面所引「点-多边形命中 `utils.ts:250-268`」——**`src/utils.ts` 在 ComfyArt 仓库中不存在**（全仓 grep 无命中）。
  该功能按票意自研为 `canvas-geometry.js` 的 `pointInPolygon`（标准 even-odd 射线法），NOTICE.md 已注明。

**砍掉项（明示）**：
- 未移植 `registerPlacer('replace'/'layers'/'regions')` 系（依赖 tldraw editor + comfy 后端）。
- 未移植 `grow`（canvas blur 膨胀，纯 canvas 依赖且本票不需要）。
- 未移植 `canvasOf`/`pixels`（纯 DOM 工具，我方直接走 2d context）、`loadPaper`/`normalized`（paper.js 依赖）。
- **第 4 步（可选增强）InpaintPanel 20 步 ImageData 笔刷撤销栈：我方已超额满足，零改动**——
  `canvas-image-editor.js` `editDrawSnapshot` 已存完整 `getImageData` 快照，`EDIT_DRAW_HISTORY_MAX = 40`（> 20），push/undo/redo 齐备。

## 3. LayersPanel（第 3 步）

- `static/js/canvas-layers.js`（469 行 ≤ 600）：上述移植纯函数 + 新增 `compositeLayers`（alpha-over 按层序合成，
  数组末位最上、`visible=false` 跳过）+ 图层 store 纯操作（add/remove/toggle/move/reorder，不可变；缺失 visible 按可见处理）。
- 面板（`static/smart-canvas.html` 内嵌于 imageEditModal）：`layers` 模式按钮 + `#imageLayersTools`（空白图层 / 导入笔刷 /
  图层列表 / 导出合成 PNG）。图层行：显隐切换、上移/下移、删除（底图禁删）、HTML5 DnD 拖拽重排。
- 导出：离屏 canvas 按层序（底→顶）合成可见层 → `toBlob('image/png')` 下载（`{图名}_layers.png`）。
- i18n 中英 15 key（`canvas.layers*`，`window.StudioI18n.register`）。
- 接线（无逻辑主体）：`canvas-image-editor.js` 模式白名单 +`layers`、工具条切换、layers 标题/副标题/隐藏主应用按钮、
  `setImageEditMode` 尾部 `window.CanvasLayers.onEditMode(mode)` 钩子。**`smart-canvas.js` 零改动** →
  耦合钉值 246/957/173/803 不动（ADR 0020 漂移日志无需新增；棘轮双表按实测登记 canvas-layers.js 反向引用=2、自由全局=2，
  其余新模块 0）。
- 守卫同步：`CANVAS_SCRIPTS`（canvas_frontend_sources.py）、harness `HTML_SCRIPT_ORDER`、`docs/agents/navigation-index.md`
  模块表 3 行、整页缓存戳统一 `20261028.225000.0001`（#224 教训：同页 `?v=` 必须单值，首跑 fast 门在此挂掉后修）。

## 4. 测试证据

- JS 单测（node --test，TDD）：新增 **25 例**——test-canvas-geometry.mjs 9、test-canvas-vector-path.mjs 8、test-canvas-layers.mjs 8；
  既有 16 个文件 321 例全绿（含 test-canvas-image-editor.mjs 35 例回归）。合计 346 pass / 0 fail。
- Python 守卫：`tests.test_canvas_module_units` + `tests.test_canvas_core_layer` + `tests.test_canvas_coupling_report` +
  `tests.test_canvas_frontend_sources` = 42 用例 OK（含 harness 顺序对账、棘轮双表、顶层名唯一、缓存戳单值、导航索引覆盖）。
- **quality_gate fast：passed**（15.4s，证据 `firefly-quality-gate\20261028-225047-fast-67448`）。
- **quality_gate domain：passed**（135.7s，chrome 正常起、无需重试，证据 `firefly-quality-gate\20261028-225116-domain-69844`）。
- **全量 pytest：1440 passed, 1 skipped, 0 failed**（515s）。基线已知失败 `test_agent_generate_tools`×3 **未复现**——
  在纯 b21aae1 worktree 上同跑该模块同样 16/16 通过，判定为环境相关（与本次改动无关，本次零 Python 改动）。
- 禁区核对：`app/` 下零改动（git show 逐 commit 核对：仅 static/、tests/、docs/agents/、NOTICE.md、.pipeline/）。

## 5. 未尽事项（明示）

- **图层全链浏览器证据（分层→显隐→重排→导出 PNG）未被自动浏览器测试覆盖**：domain 门绿 = 既有旅程无回归；
  新面板纯函数有单测，但无新增 `tests/*_browser.py`（时箱内未做完）。建议后续票补 layers 浏览器旅程测试。
- trial 模式未跑（票面门只要求 fast + domain 尽力）。
