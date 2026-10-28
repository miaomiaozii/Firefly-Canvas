# issue-238-result：缩放自适应缩略图 + 视频 hover 播放（总控接管实施）

## 背景

两轮 worker（75m×2）均零 commit 空产出（卡死在前端契约测试范式探索）。第 0 步票面核验：
票面与派工一致（Perf(W1) 处方 ComfyArt 95ab202 a/b）。总控按票面直接实现。

## 改动（零后端）

**static/js/canvas-media-history.js**
- `MEDIA_PREVIEW_TIERS=[256,512,1024]`、`mediaPreviewTierFor(cssLongEdge, zoom, dpr)`（<=档位）、
  `setPreviewUrlWidth`（只重写既有 w 参数）、`isRealPreviewEndpoint`。
- `smartVideoPreviewHtml/smartVideoFallbackHtml` 从 smart-canvas.js 上移到模块（与
  smartMediaPreviewUrl 同层，宿主删除重复声明避免全局词法冲突）；预览 video 改为
  `preload="none" + poster=对应档缩略图 + data-video-preview="1"`，不再 metadata 预取字节。

**static/js/smart-canvas.js**
- `singleMediaHtml` 图片/视频预览按 `smartNodePreviewTier(w,h)`（节点长边×viewport.scale×dpr 取档）。
- `applyViewport` → `schedulePreviewLodRefresh`（200ms debounce）→ `refreshCanvasPreviewLodWidths`
  重写已渲染 `img[data-preview-src]` src 与 `video[poster]` 的 w 档。
- shell 委托 `pointerover/pointerout`：hover → muted+loop play()；离开 → pause + currentTime=0。
  点击播放按钮走既有 controls 激活路径，未动。

## 测试与证据

- `tests/canvas_module_units/test-canvas-media-lod.mjs`：9 例（档位边界/zoom×dpr/URL 重写/
  poster+preload="none"/不可预览源不造 poster）。
- `tests/test_media_lod_contract.py`：4 例宿主接线契约。
- `tests/video_failure_browser_client.js`：真实浏览器 4 条新断言（渲染出 preload=none、
  poster 带档位、hover 置位 hoverPlaying、pointerout 复位）→ runner PASS（12 assertions），
  日志实测 w=512→256→1024 档位刷新。
- perf500/边界/node_flow/fly_fixes/experience 浏览器 runner + 全量 pytest：见下方链式日志
  /tmp/238_*.log（合并前复核）。

## 已知权衡（记录不隐瞒）

- 测试环境 fake mp4 无法生成预览帧（preview?w= 500，属 fake 数据问题非接线问题）；
  真环境 mp4+ffmpeg 走既有 `is_video_preview_file` 路径。若某环境预览生成不可用，
  视频卡会短暂无帧画面（此前靠 metadata 预取首帧）——播放按钮路径不受影响。
- perf500 回归判定以链式日志为准。

## Commit

54ea6b1（+ result commit）。基线 issue/238 @ 6feca94。

## 收尾（2026-10-29，总控）

**分支与票面状态**
- 实现已全量合入 wave1-live（merge df93897，含棘轮同步+ui-integrity 基线刷新）；远端
  issue/238 曾于合并后删除，本轮重新推送。
- 票面 10-28 19:40 已由 miaomiaozii 关闭（关票评论引用 merge df93897 + fast 证据
  20261029-033955-fast-58700）；本轮补挂 assignee（miaomiaozii）。

**核验（wt-238，树 = origin/wave1-live @ a1b1f95）**
1. 前向同步：`git merge origin/wave1-live` fast-forward a3c5126 → a1b1f95（吸收 110
   提交，零冲突；batch-2/3/4 各票 + #269 已在其中）。
2. 禁区核验：238 系提交仅触 static/js/canvas-media-history.js、static/js/smart-canvas.js、
   tests/test_media_lod_contract.py、tests/canvas_module_units/test-canvas-media-lod.mjs、
   tests/native_browser.py——对 app/agent_tools.py / agent_host* / static/js/agent/* /
   fal / generation / media 零触碰。
3. 棘轮：已按 ADR 0020 在合入时钉值（docs/adr/0020 + tests/test_canvas_coupling_report.py，
   948/797，merge 冲突手工解决）；本轮跑 wave1-live tip 树，coupling 测试绿，无新漂移。
4. 全量 pytest（2026-10-29 03:56，日志 /tmp/238-full-suite.log）：**1526 passed, 1 skipped,
   628 subtests passed, 527.26s, 0 failed**。树与 wave1-live 基线同一 tip，结果与基线完全
   一致；用例数较 10-28 同分支运行（1331 passed）的 +195 来自合入后 batch 各票（#253/#255/
   #256/#257/#258/#268/#269 等），非本票引入。
5. 环境性失败甄别（10-28 历史失败，根因均已修复且已含于现树，本轮 0 环境失败）：
   - 扩展用例 4 失败：harness `--disable-extensions` 旗标杀掉 --load-extension 加载的扩展
     → a3c5126 撤销该旗标；
   - 棘轮 2 失败 + test_project_canvas_mutation_client 1 失败（238 预览路径相关）→
     df93897 棘轮同步 + ui-integrity 基线刷新解决；
   - gate 清理 PermissionError [WinError 32]（/tmp/238_gate.log）：Windows 文件锁环境
     问题，非代码问题。
6. 门（2026-10-29，wt-238 @ a1b1f95）：
   - fast：**passed 15.8s** → `C:\Users\Administrator\AppData\Local\Temp\firefly-quality-gate\20261029-035503-fast-76912`
   - domain：**passed 131.0s** → `C:\Users\Administrator\AppData\Local\Temp\firefly-quality-gate\20261029-041044-domain-72940`（一次通过，无 chrome 并发重试）
7. 基线参照（wave1-live 当日已跑）：trial passed 214.5s（…033341-trial-43920）、
   domain passed 130.8s（…020724-domain-70444）。
8. 合并前链式日志（10-28）：/tmp/238_chain.log（perf500/边界/node_flow/fly_fixes/
   experience 5 个浏览器 runner 全 PASS；末段全量 pytest 历史失败见上）、/tmp/238_pytest.log
   （4 failed）、/tmp/238_pytest2.log（2 failed，后续修复全绿）。

**完成清单**
- [x] 前向同步 wave1-live（ff a3c5126 → a1b1f95，零冲突）
- [x] 全量 pytest 对照基线（1526 passed / 0 failed，环境性失败甄别见上）
- [x] 补缺口：无新增 TODO（已知权衡为环境 fake 数据问题，见「已知权衡」）
- [x] fast + domain 门 passed（证据目录已录）
- [x] push origin issue/238（分支 tip = 本收尾 commit）
- [x] `gh issue edit 238 --add-assignee miaomiaozii`
- [x] result.md 更新（本节）
