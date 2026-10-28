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
