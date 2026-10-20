"""画布耦合构成报告（#173 遗留待决项，见 tests/test_canvas_core_layer.py:62）。

棘轮守卫只数「反向引用处数」，但那个数字不能指导怎么继续拆：
`canvas-undo.js` 6 处 / 29 个依赖名，15 个手写桩就能独立测；
`canvas-media-history.js` 210 个依赖名，就算处数降到 50 也照样测不动。
所以继续拆之前必须先回答：这 654 处引用的是**哪些名字**，每个名字**该不该留在核心**。

本工具做机械抽取 + 显式分类：
- 机械部分：复用守卫测试的解析口径（去注释行、排除模块自己声明过的同名标识符），
  抽出每个模块引用的 `smart-canvas.js` 顶层名字及处数，并查出该名字在核心里是怎么声明的。
- 显式部分：`CATEGORY_BY_NAME` 是人给的分类决定。工具会**列出所有未分类的名字**，
  所以分类不完整时是可见的，不会悄悄退化成一个自洽但过时的表。

#201 轨道 1（ADR 0020）把共享服务改成显式接口 `canvasCore.NAME` 后，引用口径必须
同时看得见两种写法：`canvasCore.render` 仍算作对 `render` 的反向引用（正则 \brender\b
本来也匹配点号后的名字），否则棘轮数字「变好」而耦合一点没少，守卫从此看不见真耦合。
所以每个模块额外拆分出 **自由全局引用（free）/ 接口引用（interface）**：总数不变，
自由侧只许降（tests/test_canvas_core_layer.py 的 FREE_GLOBAL_REFERENCE_CEILINGS），
那才是轨道 1 的真实进展——模块要桩的不同外部名字数随自由侧下降。

用法：
    python tools/canvas_coupling_report.py            # 汇总表
    python tools/canvas_coupling_report.py --names    # 带名字和分类的明细
    python tools/canvas_coupling_report.py --json     # 机器可读（给守卫/CI 用）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_canvas_core_layer import (  # noqa: E402
    JS_DIR,
    _is_canvas_script,
    _locally_declared_names,
    _script_order,
    _strip_comment_lines,
    _top_level_names,
)

CORE = "smart-canvas.js"

# 分类决定（人给的，不是抽出来的）。只对「被 ≥2 个模块引用」的名字需要人工判：
# 那些才是真共享服务，判错了会误导拆分方向。「只被 1 个模块引用」的名字
# 由 scope 字段客观判出归属，不需要人工分类。
#   hub      行为枢纽：核心提供的操作，模块本来就该调它。留在核心，不该注入。
#   state    共享可变状态：该由 canvas-core.js 或 owning module 提供，模块应接收注入。
#   dom      DOM 元素引用：该由 owning module 自己查，或从核心接口拿。
#   reach    越界访问：访问的是别的模块或核心内部实现，应改走显式 API。
# 工具会列出所有未分类的共享名字，所以分类不完整时是可见的。
CATEGORY_BY_NAME = {
    # --- 行为枢纽 ---
    "render": "hub",
    "scheduleSave": "hub",
    "saveProjectCanvasUntilSettled": "hub",
    # 以下三个是 2026-10-19 修好 TOP_LEVEL_NAME_RE 漏 `async function` 后才浮现的：
    # 它们一直是跨模块共享的核心名字，只是之前对提取器隐形、从未被分类。
    # 实测引用数：saveCanvas 4 / acceptProjectGenerationRegistration 4 / importProjectMediaReference 3。
    "saveCanvas": "hub",
    "importProjectMediaReference": "hub",
    "acceptProjectGenerationRegistration": "hub",
    # 同一批正则修正后浮现的单模块引用（共 25 处），按现有约定分类：
    # util = 纯辅助（对齐 apiErrorMessage / formatRunDuration）；dom = DOM 元素与刷新动作（对齐 refreshIcons）；
    # 其余都是对画布状态的动作/服务 → hub。
    "pollGenerationTaskResultForNode": "hub",
    "controlProjectImageTask": "hub",
    "flushProjectCanvasBeforeGeneration": "hub",
    "deleteSmartProjectOriginal": "hub",
    "restoreSmartProjectOriginal": "hub",
    "restoreSmartProjectOriginalUndo": "hub",
    "archiveProjectMediaAsset": "hub",
    "copySmartHistoryImage2k": "hub",
    "smartTrashRestoreItem": "hub",
    "smartTrashPurgeItem": "hub",
    "deleteCanvasLogEntry": "hub",
    "uploadImageBlobs": "hub",
    "refreshSmartProjectMediaTrashButton": "dom",
    "copyTextToClipboard": "util",
    "smartResponseErrorMessage": "util",
    "rhBuildNodeInfoList": "util",
    "toast": "hub",
    "tr": "hub",
    "trf": "hub",
    "nodeRect": "hub",
    "screenToWorld": "hub",
    "nodeById": "hub",
    "imagesForNode": "hub",
    "selectedNodeIds": "hub",
    "currentEditImage": "hub",
    "mediaKindForUrls": "hub",
    "mediaKindForItem": "hub",
    "isApiLikeEngine": "hub",
    "liveSmartNode": "hub",
    "scheduleStateSave": "hub",
    "upstreamNodesForKinds": "hub",
    "inputNodesFor": "hub",
    "stripImageGenerationMeta": "hub",
    "copyMediaSizeFields": "hub",
    "displayMediaUrl": "hub",
    "proxiedMediaUrl": "hub",
    "imageForDisplay": "hub",
    "imageLayout": "hub",
    "imageRefsOnly": "hub",
    "reducedAspectRatio": "hub",  # #200 搬家后由 tasks+image-editor 共用，纯函数留在核心
    "isVideoMediaItem": "hub",
    "outputUrlLooksVideo": "hub",
    "markSmartNodeComplete": "hub",
    "attachRunMeta": "hub",
    "smartSettingsForNode": "hub",
    "mediaNodeDefaultScale": "hub",
    "outputImagesForNode": "hub",
    "rememberRoundOutputs": "hub",
    "sleep": "hub",
    "safeExportFileName": "hub",
    "downloadNameForMediaItem": "hub",
    "downloadPreviewFile": "hub",
    "smartImageNameFromUrl": "hub",
    "imageResolutionLabel": "hub",
    "normalizedSizeLabel": "hub",
    "normalizedResolutionTier": "hub",
    # 被 >=2 模块引用的共享服务（本轮新判）
    "arrangeSmartGroupMembers": "hub",
    "clearSourceBusyStateIfDownstreamDone": "hub",
    "refreshIcons": "dom",
    "resolveChatModel": "hub",
    "resolveChatProviderId": "hub",
    "smartGroupContainingNode": "hub",
    "smartGroupImageRefs": "hub",
    "smartRecoverableImageTask": "hub",
    # --- 纯工具：应该下沉到 canvas-core.js，不该留在 smart-canvas.js ---
    "parseSizeValue": "util",
    # --- 共享可变状态 ---
    "settings": "state",
    "params": "state",
    "canvas": "state",
    "canvasId": "state",
    "canvasState": "state",
    "projectId": "state",
    "sourceProjectId": "state",
    "smartProjectId": "state",
    "nodes": "state",
    "connections": "state",
    "selectedNode": "state",
    "dragState": "state",
    "smartLoopContext": "state",
    "smartCascadeRuns": "state",
    "projectMediaLibrary": "state",
    "smartGenerationSubmissions": "state",
    "apiProviders": "state",
    "MEDIA_NODE_DEFAULT_SCALE": "state",
    "MEDIA_GROUP_DEFAULT_SCALE": "state",
    "MEDIA_GROUP_MAX_VISIBLE_ROWS": "state",
    "SMART_GROUP_MAX_VISIBLE_ROWS": "state",
    "SMART_REFERENCE_IMAGE_MAX": "state",
    "SMART_HISTORY_DOM_LIMIT": "state",
    "SMART_HISTORY_RENDER_BUFFER": "state",
    "SMART_HISTORY_OVERSCAN": "state",
    "LOCAL_EDIT_NODE_W": "state",
    "LOCAL_EDIT_NODE_H": "state",
    # --- DOM ---
    "world": "dom",
    "shell": "dom",
    "promptInput": "dom",
    "composer": "dom",
    "runBtn": "dom",
    "smartTrashList": "dom",
    "smartTrashPurgeBtn": "dom",
    "smartTrashBackdrop": "dom",
    "smartUnusedBackdrop": "dom",
    "escapeHtml": "dom",
    "escapeAttr": "dom",
    # --- 越界访问 ---
    "smartHistoryModal": "reach",
    "smartHistoryList": "reach",
    "smartHistoryCollapse": "reach",
    "smartHistoryPurgeTrash": "reach",
    "smartHistoryVirtualState": "reach",
    "connectionEraseState": "reach",
    # --- #201 前置补全：solo 名字分类（2026-10-19 实测 99 名字 / 130 处，全部 solo）---
    # 判定依据是定义而不是名字（读了 smart-canvas.js 顶层定义后判）：
    #   util = 纯函数：行为只由参数决定，不读写共享可变状态、不碰 DOM（可下沉 canvas-core.js）
    #   state = let/var 共享可变变量，或 const 配置表（沿用本表惯例）
    #   hub = 行为服务：读写共享状态或 DOM，或对画布节点/provider 的查询与变更
    # --- 补全 hub（行为服务，61 名字）---
    "updateZoomLabel": "hub",  # 写 #imageEditZoomLabel 文本（读 imageEditMode/zoom 状态）
    "rectOverlapNode": "hub",  # 节点查询：rect -> canvasState.nodes 中节点
    "clearDetachedRunInputRefs": "hub",  # 节点变更：清孤儿 run 输入引用
    "syncLocalEditInputRoles": "hub",  # 节点变更：由 connections 重建 local-edit 输入角色
    "bindSmartPreviewImageFallbacks": "hub",  # DOM 行为：给 img 绑预览 fallback 事件
    "positionComposerForNode": "hub",  # UI 操作：定位 composer 到节点旁
    "refreshComparePanel": "hub",  # UI 操作：刷新对比面板 DOM
    "activeSettingsSubject": "hub",  # 节点查询：当前设置主体节点
    "smartRunSnapshot": "hub",  # run 服务：读 settings 建运行快照
    "promptInputNodesFor": "hub",  # 节点查询：上游 prompt/loop/group 节点
    "defaultReferenceImagesFor": "hub",  # 节点查询：默认参考图
    "workflowInputNodesFor": "hub",  # 节点查询：workflow 上游节点
    "requestSmartEdgeReveal": "hub",  # UI 行为：智能边缘揭示动画
    "smartArrangeAtomicIds": "hub",  # 画布查询：id 折叠为分组原子（读 canvasState.nodes）
    "promptTextItemsForNode": "hub",  # 节点查询：prompt 文本项（读 smartLoopContext）
    "fitSmartLoopNode": "hub",  # 节点变更：调整 loop 节点 w/h
    "translateSmartNodeWithMembers": "hub",  # 节点变更：平移节点+组成员
    "assetLibraryIsProject": "hub",  # 画布查询：当前资源库是否项目级
    "openLongContentReader": "hub",  # UI 行为：打开长内容阅读器
    "videoProviderById": "hub",  # provider 查询：id -> 视频 provider（读 apiProviders）
    "removeProjectMediaFromCache": "hub",  # 状态变更：projectMediaLibrary 缓存移除
    "toggleAssetLibrary": "hub",  # UI 行为：资源库面板开/关
    "closeSmartCanvasLog": "hub",  # UI 行为：关画布日志抽屉
    "apiProviderById": "hub",  # provider 查询：id -> 图像 provider（读 apiProviders）
    "renderAssetLibrary": "hub",  # UI 操作：渲染资源库面板
    "viewportCenter": "hub",  # 视口服务：视口中心世界坐标（读 shell+viewport）
    "closeCreateMenu": "hub",  # UI 行为：关创建菜单
    "smartNodeInFlight": "hub",  # 节点查询：是否有在途运行（读 pending/submission）
    "inheritNodeMetaFromImage": "hub",  # 节点变更：剥 node.images 生成元数据
    "ungroupNode": "hub",  # 画布操作：释放分组（pushUndo、重建节点）
    "openAssetNameDialog": "hub",  # UI 行为：打开资产重命名对话框
    "renderMinimap": "hub",  # UI 操作：渲染小地图
    "addNodeToSmartGroup": "hub",  # 画布操作：节点加入/吸收进智能分组
    "applyThumbDisplaySizeToElement": "hub",  # DOM 操作：应用缩略图显示尺寸到元素
    "previewDownloadGroupItems": "hub",  # 画布查询：预览组下载项（读 previewNavState）
    "renderDynamicParams": "hub",  # UI 操作：渲染动态参数面板（读写 settings）
    "activeComposerNode": "hub",  # 节点查询：composer 绑定节点
    "updatePreviewMetaHint": "hub",  # UI 操作：更新预览 meta 提示
    "visibleReferenceImagesFor": "hub",  # 节点查询：可见参考图（读 mention 状态）
    "applyPreviewTransform": "hub",  # DOM 操作：应用预览 pan/zoom 变换
    "buildPromptRequest": "hub",  # prompt 服务：构建 prompt 请求（读 promptInput/loop ctx）
    "savePromptDraftForCurrent": "hub",  # draft 服务：存 prompt 草稿到 composer 主体
    "runningHubSelectedModel": "hub",  # RunningHub 查询：选中模型（读 settings）
    "modelscopeImageModels": "hub",  # provider 查询：ModelScope 图像模型
    "smartCascadeAnyRunning": "hub",  # 级联查询：是否有级联在跑
    "updateComposer": "hub",  # UI 操作：刷新 composer（DOM+状态）
    "runningHubModelApiSettings": "hub",  # RunningHub 查询：选中模型对应 API settings
    "syncRunButtonState": "hub",  # UI 操作：同步运行按钮状态
    "smartCascadeRunForLoop": "hub",  # 级联查询：loopId -> run（读 smartCascadeRuns）
    "rememberRecentSmartSettings": "hub",  # settings 服务：记录最近智能设置
    "smartCascadeIsLoopRunning": "hub",  # 级联查询：loopId 是否在跑
    "applyApiVideoGenerationResult": "hub",  # run 服务：视频结果落到节点（调 render）
    "rhActiveFields": "hub",  # RunningHub 查询：激活字段（读 settings）
    "promptNodeUpstreamPromptText": "hub",  # 节点查询：上游 prompt 文本（读 smartLoopContext）
    "manualSmartVideoLink": "hub",  # settings 查询：手动 temp-sh 视频链接
    "tempShUploadedUrlFor": "hub",  # 云链服务：本地 url -> 已上传 url（读 transient links）
    "promptNodeInputMediaForLLM": "hub",  # 节点查询：LLM 输入媒体
    "selectedRunningHubRef": "hub",  # RunningHub 查询：选中 ref（读写 settings.rhConfigKey）
    "providerChatModels": "hub",  # provider 查询：chat 模型（读 apiProviders）
    "chatApiProviders": "hub",  # provider 查询：可用 chat provider（读 apiProviders）
    "centerViewportOnWorldPoint": "hub",  # 视口行为：以世界点为中心（写 viewport）
    # --- 补全 util（纯函数，31 名字；候选下沉 canvas-core.js）---
    "smartPreviewImgHtml": "util",  # 纯 builder：URL+escape -> <img> HTML，无状态读写/DOM
    "sizeForRun": "util",  # 纯计算：settings 参数 -> 尺寸串
    "videoRefsOnly": "util",  # 纯 filter：refs 参数 -> 视频项
    "generationStageProgressHtml": "util",  # 纯 builder：progress 对象 -> 进度条 HTML
    "promptHtmlWithMentionTokens": "util",  # 纯 builder：text+refs -> mention token HTML
    "smartImageUsesWorkflowInput": "util",  # 纯谓词：node+ctx -> bool（只读 smartLoopContext 默认参）
    "smartRunNeedsPrompt": "util",  # 纯谓词：settings -> bool
    "audioRefsOnly": "util",  # 纯 filter：refs 参数 -> 音频项
    "apiErrorMessage": "util",  # 纯 format：错误数据 -> 消息串
    "formatRunDuration": "util",  # 纯 format：ms -> "m:ss"
    "modelDisplayName": "util",  # 纯查表：provider+model -> 显示名
    "smartNodeHasDisplayResult": "util",  # 纯谓词：node -> bool
    "zoomPanAroundPoint": "util",  # 纯计算：zoom/pan/rect/坐标 -> 新 {zoom,pan}
    "smartVideoPreviewHtml": "util",  # 纯 builder：url 参数 -> <video> HTML
    "mediaGroupGridHeight": "util",  # 纯计算：images/cols/rows/thumb -> 高
    "imageNameLabel": "util",  # 纯：img 参数 -> 名称串
    "smartGroupCompactMembers": "util",  # 纯 filter：group 参数 -> prompt/loop 成员
    "nearestFourKSizeFor": "util",  # 纯计算：w/h -> 最近 4K 规格（SIZE_MAP 常量）
    "normalizeSmartOnlineEngine": "util",  # 纯：value -> engine（fallback 'api'）
    "circledNumber": "util",  # 纯：n -> 圈数字符
    "isSmartRunnableNode": "util",  # 纯谓词：node -> bool
    "pendingBoxSize": "util",  # 纯计算：count+node -> 占位尺寸
    "smartLoopCount": "util",  # 纯：node -> clamp 计数
    "stripRunInputMeta": "util",  # 纯：meta -> 新 meta（清输入字段）
    "cloneSmartNode": "util",  # 纯 builder：node 参数 -> 新 node（只读参数+uid）
    "parseSizePair": "util",  # 纯："WxH" -> {width,height}
    "mediaRefSourceUrl": "util",  # 纯：ref 参数 -> 源 url
    "apiImageUsesExplicitResolution": "util",  # 纯谓词：settings+provider 能力 -> bool
    "promptNodePromptItems": "util",  # 纯：node -> 拆分后的 prompt 列表
    "parseSizeAspect": "util",  # 纯：尺寸/比例串 -> {w,h}
    "nodeBodyHtml": "util",  # 纯 builder：node+layout -> body HTML（分派纯子 builder）
    # --- 补全 state（共享可变变量 / const 配置表，7 名字）---
    "interactionLayerRaf": "state",  # let raf id：交互层刷新合并
    "generationCompleteSoundAt": "state",  # let 时间戳：完成音去重
    "previewComparePos": "state",  # let 数字：对比滑块位置
    "assetLibraryOpen": "state",  # let bool：资源库开关
    "MS_GEN_MODELS": "state",  # const 配置表：ModelScope 模型目录
    "previewPan": "state",  # let {x,y}：预览平移
    "previewZoom": "state",  # let 数字：预览缩放
}

CATEGORY_ORDER = ("hub", "util", "state", "dom", "reach", "unclassified")


def _core_text() -> str:
    return (JS_DIR / CORE).read_text(encoding="utf-8")


def _declaration_kind(name: str, core_text: str) -> str:
    """名字在核心里的声明形态——分类的证据，不是猜的。"""
    for pattern, kind in (
        # `async function` 必须单独列：只写 `^function` 会把 async 顶层函数
        # 归为 not-top-level，跟 _top_level_names 的盲点同源。
        (rf"^async\s+function\s+{re.escape(name)}\s*\(", "function"),
        (rf"^function\s+{re.escape(name)}\s*\(", "function"),
        (rf"^class\s+{re.escape(name)}\b", "class"),
        (rf"^const\s+{re.escape(name)}\s*=\s*(?:document\.(?:getElementById|querySelector)|\$\()", "dom-ref"),
        (rf"^const\s+{re.escape(name)}\b", "const"),
        (rf"^let\s+{re.escape(name)}\b", "let"),
        (rf"^var\s+{re.escape(name)}\b", "var"),
    ):
        if re.search(pattern, core_text, re.M):
            return kind
    return "not-top-level"


def count_references(text: str, core_names: set[str], local_names: set[str]) -> Counter:
    """每个核心名字的引用处数（引用口径，与棘轮守卫一致）。

    同时计入裸引用与 `canvasCore.NAME` 接口引用：正则 `\bNAME\b` 也匹配
    点号后的名字，所以改写成接口后总数不降（#201 陷阱 2 的守卫见
    tests/test_canvas_coupling_report.py）。
    """
    hits: Counter[str] = Counter()
    for name in core_names:
        if name in local_names:
            continue  # 同名局部变量不是对核心的耦合
        count = len(re.findall(rf"\b{re.escape(name)}\b", text))
        if count:
            hits[name] = count
    return hits


def count_interface_references(
    text: str, core_names: set[str], local_names: set[str]
) -> Counter:
    """`canvasCore.NAME` 形式的接口引用处数，归到对应的核心名字上。

    这是引用口径的拆分而不是新口径：总数 = 自由全局引用 + 接口引用。
    守卫的 FREE_GLOBAL_REFERENCE_CEILINGS 只对自由侧棘轮（只许降）——
    自由侧独立名字越少，模块独立单测要桩的外部名字就越少（ADR 0020 轨道 1）。
    """
    hits: Counter[str] = Counter()
    for name in core_names:
        if name in local_names:
            continue
        count = len(re.findall(rf"canvasCore\.{re.escape(name)}\b", text))
        if count:
            hits[name] = count
    return hits


def coupling() -> dict[str, dict[str, int]]:
    """module -> {referenced core top-level name: 引用处数}。

    口径与棘轮守卫完全一致（复用它的解析函数），所以本工具的总数能跟
    REVERSE_REFERENCE_CEILINGS 对得上；对不上就是 bug。
    """
    core_names = _top_level_names(_strip_comment_lines(_core_text()))
    order = [name for name in _script_order() if _is_canvas_script(name)]
    result: dict[str, dict[str, int]] = {}
    for module in order:
        if module == CORE:
            continue
        path = JS_DIR / module
        if not path.exists():
            continue
        text = _strip_comment_lines(path.read_text(encoding="utf-8"))
        own = _locally_declared_names(text)
        result[module] = dict(count_references(text, core_names, own))
    return result


def interface_coupling() -> dict[str, dict[str, int]]:
    """module -> {走 canvasCore 接口引用的核心名字: 处数}（口径见 count_interface_references）。"""
    core_names = _top_level_names(_strip_comment_lines(_core_text()))
    result: dict[str, dict[str, int]] = {}
    for module in [n for n in _script_order() if _is_canvas_script(n) and n != CORE]:
        path = JS_DIR / module
        if not path.exists():
            continue
        text = _strip_comment_lines(path.read_text(encoding="utf-8"))
        own = _locally_declared_names(text)
        result[module] = dict(count_interface_references(text, core_names, own))
    return result


def reverse_coupling() -> dict[str, object]:
    """反方向：声明在模块里、却被核心引用的名字。

    为什么需要这个函数：`coupling()` 只统计「模块引用声明在核心里的名字」（见
    `summarize()` 的 owners 语义），所以它**测不到反方向**。ADR 0020 初稿把轨道 3
    的规模写成「17 个名字 / 40 处」，那个数无法从本工具任何字段重现——直接导致
    后来人按错的规模排期。这里把反方向也做成可复现的。

    两个口径（都不是「债」的判定，只是证据）：
      reverse_*        核心引用了模块声明的名字（核心是 UI 编排层，调模块功能
                       本身合法——这是正常的组合关系，不是债）。
      core_dominant_*  其中「核心用得比 owner 模块自己还多」的那部分——这才是
                       ADR 0020 关心的「搬早了」信号：名字住在模块里，但主用方是核心。

    计数口径：`\bname\b` 出现数减去该文件里的声明数（与 `core_internal_calls` 一致）。
    `canvas-core.js` 排除在 owner 之外：核心调 canvas-core.js 是合法的下层依赖。
    """
    core_text = _strip_comment_lines(_core_text())
    core_declared = _top_level_names(core_text)
    # 单遍扫描建词频表。每个名字单独跑一遗正则会在这个文件上花 ~135s（它跟
    # test_quality_gate 同分片，会直接拖长门）。
    core_tokens = Counter(re.findall(r"[A-Za-z_$][\w$]*", core_text))

    def _refs(tokens: Counter, name: str, declared_here: int) -> int:
        return max(0, tokens.get(name, 0) - declared_here)

    rows: list[dict[str, object]] = []
    for module in [n for n in _script_order() if _is_canvas_script(n) and n not in (CORE, "canvas-core.js")]:
        path = JS_DIR / module
        if not path.exists():
            continue
        text = _strip_comment_lines(path.read_text(encoding="utf-8"))
        own_declared = _top_level_names(text)
        own_tokens = Counter(re.findall(r"[A-Za-z_$][\w$]*", text))
        for name in sorted(own_declared - core_declared):
            in_core = _refs(core_tokens, name, 0)
            if not in_core:
                continue
            own = _refs(own_tokens, name, 1 if name in own_declared else 0)
            rows.append(
                {"name": name, "owner": module, "core_refs": in_core, "owner_refs": own}
            )

    dominant = [r for r in rows if r["core_refs"] > r["owner_refs"]]
    return {
        "reverse_names": len(rows),
        "reverse_core_refs": sum(int(r["core_refs"]) for r in rows),
        "core_dominant_names": len(dominant),
        "core_dominant_core_refs": sum(int(r["core_refs"]) for r in dominant),
        "by_owner": dict(
            Counter(str(r["owner"]) for r in rows)
        ),
        "core_dominant_by_owner": dict(Counter(str(r["owner"]) for r in dominant)),
        "reverse_rows": sorted(rows, key=lambda r: -int(r["core_refs"])),
        "core_dominant_rows": sorted(
            dominant, key=lambda r: -(int(r["core_refs"]) - int(r["owner_refs"]))
        ),
    }


def summarize(data: dict[str, dict[str, int]]) -> dict[str, object]:
    # owner 数是客观事实，不是人工判断：只被 1 个模块引用 = 那个模块自己的代码
    # 寄居在核心里；被 >=2 个引用 = 真共享服务。这个切分比「名字看起来像什么」可靠。
    owners: dict[str, set[str]] = defaultdict(set)
    name_totals: Counter[str] = Counter()
    for module, refs in data.items():
        for name, count in refs.items():
            owners[name].add(module)
            name_totals[name] += count

    core_text = _core_text()
    stripped_core = _strip_comment_lines(core_text)
    # 核心有 16354 行，每个名字都跑一遗正则会把本测试拖到 70s+。缓存名字→结果。
    call_cache: dict[str, int] = {}

    def core_internal_calls(name: str) -> int:
        """核心自己调用这个名字几次（排除它自己的 function 声明）。

        >0 就意味着搬走它会造出「核心 -> 模块」反向依赖，不能纯搬家。
        """
        if name in call_cache:
            return call_cache[name]
        calls = len(re.findall(rf"\b{re.escape(name)}\s*\(", stripped_core))
        definitions = len(
            re.findall(rf"^(?:async\s+)?function\s+{re.escape(name)}\s*\(", stripped_core, re.M)
        )
        result = max(0, calls - definitions)
        call_cache[name] = result
        return result

    per_category: Counter[str] = Counter()
    per_module: dict[str, dict[str, int]] = {}
    iface = interface_coupling()
    for module, refs in data.items():
        buckets: Counter[str] = Counter()
        for name, count in refs.items():
            category = CATEGORY_BY_NAME.get(name, "unclassified")
            per_category[category] += count
            buckets[category] += count
        module_iface = iface.get(module, {})
        iface_refs = sum(module_iface.values())
        free_names = {n for n, c in refs.items() if c > module_iface.get(n, 0)}
        per_module[module] = {
            "total": sum(refs.values()),
            "distinct_names": len(refs),
            # #201 轨道 1：总数 = 自由全局 + 接口。自由侧是单测桩数量的驱动因子。
            "free_refs": sum(refs.values()) - iface_refs,
            "interface_refs": iface_refs,
            "distinct_free_names": len(free_names),
            "distinct_interface_names": len(module_iface),
            **{category: buckets[category] for category in CATEGORY_ORDER if buckets[category]},
        }

    solo = [n for n in name_totals if len(owners[n]) == 1]
    shared = [n for n in name_totals if len(owners[n]) >= 2]
    movable = [n for n in solo if core_internal_calls(n) == 0]
    blocked = [n for n in solo if core_internal_calls(n) > 0]

    free_total = sum(stats["free_refs"] for stats in per_module.values())
    interface_total = sum(stats["interface_refs"] for stats in per_module.values())

    return {
        "core": CORE,
        "grand_total": sum(name_totals.values()),
        # #201 轨道 1 拆分：总数口径不变，自由全局只许降（守卫另见
        # tests/test_canvas_core_layer.py 的 FREE_GLOBAL_REFERENCE_CEILINGS）。
        "free_total": free_total,
        "interface_total": interface_total,
        "per_category": dict(per_category),
        "per_module": per_module,
        "scope": {
            "solo_names": len(solo),
            "solo_refs": sum(name_totals[n] for n in solo),
            "shared_names": len(shared),
            "shared_refs": sum(name_totals[n] for n in shared),
            "movable_solo_names": len(movable),
            "blocked_by_core_call_names": len(blocked),
        },
        "movable": [
            {
                "name": n,
                "owner": sorted(owners[n])[0],
                "refs": name_totals[n],
                "core_internal_calls": core_internal_calls(n),
            }
            for n in sorted(movable, key=lambda n: -name_totals[n])
        ],
        "blocked": [
            {
                "name": n,
                "owner": sorted(owners[n])[0],
                "refs": name_totals[n],
                "core_internal_calls": core_internal_calls(n),
            }
            for n in sorted(blocked, key=lambda n: -core_internal_calls(n))
        ],
        "top_names": [
            {
                "name": name,
                "refs": count,
                "owner_count": len(owners[name]),
                "owners": sorted(owners[name]),
                "category": CATEGORY_BY_NAME.get(name, "unclassified"),
                "declared_in_core_as": _declaration_kind(name, core_text),
                "core_internal_calls": core_internal_calls(name),
            }
            for name, count in name_totals.most_common()
        ],
        "unclassified_shared_names": sorted(
            name for name in shared if name not in CATEGORY_BY_NAME
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--names", action="store_true", help="打印名字级明细与分类")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    summary = summarize(coupling())
    summary["reverse"] = reverse_coupling()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    scope = summary["scope"]
    print(
        f"核心：{summary['core']}    反向引用总数：{summary['grand_total']}"
        f"（自由全局 {summary['free_total']} / 接口 {summary['interface_total']}）"
    )
    print(
        f"  只被 1 个模块引用：{scope['solo_names']} 个名字 / {scope['solo_refs']} 处"
        f"（可纯搬家 {scope['movable_solo_names']}，核心也调用 {scope['blocked_by_core_call_names']}）"
    )
    print(
        f"  被 >=2 个模块引用：{scope['shared_names']} 个名字 / {scope['shared_refs']} 处"
        "（真共享服务）"
    )
    print("\n按分类：")
    for category in CATEGORY_ORDER:
        count = summary["per_category"].get(category, 0)
        if count:
            share = 100.0 * count / summary["grand_total"]
            print(f"  {category:<13}{count:>5}  {share:5.1f}%")

    print("\n反方向（声明在模块里、被核心引用；见 reverse_coupling() 的口径说明）：")
    rev = summary["reverse"]
    print(
        f"  核心引用模块声明的名字：{rev['reverse_names']} 个名字 / {rev['reverse_core_refs']} 处"
    )
    print(
        f"  其中核心用得比 owner 模块还多：{rev['core_dominant_names']} 个名字 /"
        f" {rev['core_dominant_core_refs']} 处（「搬早了」信号）"
    )
    print(f"  按 owner：{rev['by_owner']}")

    print("\n按模块（total / 独立名字数 / 自由全局处数 / 自由独立名字数 / 可注入部分 = state+dom+reach）：")
    for module, stats in sorted(
        summary["per_module"].items(), key=lambda item: -item[1]["total"]
    ):
        injectable = sum(
            stats.get(category, 0) for category in ("state", "dom", "reach")
        )
        print(
            f"  {module:<32}{stats['total']:>5} / {stats['distinct_names']:>4}"
            f"   自由 {stats['free_refs']:>4} / {stats['distinct_free_names']:>2} 名字"
            f"   可注入 {injectable:>4}"
        )

    if args.names:
        print("\n可纯搬走的寄居函数（核心不调用，按引用处数降序）：")
        for row in summary["movable"][:40]:
            print(f"  {row['name']:<40}{row['refs']:>4}  -> {row['owner']}")
        print("\n核心自己也调用（搬走会造出 核心->模块 依赖，需先解耦）：")
        for row in summary["blocked"][:25]:
            print(
                f"  {row['name']:<40}{row['core_internal_calls']:>4} 处核心内调用"
                f"  -> {row['owner']}"
            )
        print("\n共享服务明细（被 >=2 模块引用，按处数降序）：")
        for row in summary["top_names"]:
            if row["owner_count"] < 2:
                continue
            print(
                f"  {row['name']:<36}{row['refs']:>4}  用方{row['owner_count']:>2}  "
                f"{row['category']:<13}{row['declared_in_core_as']}"
            )
        unclassified = summary["unclassified_shared_names"]
        print(f"\n未分类的共享名字 {len(unclassified)} 个（需要人工判定）：")
        for name in unclassified:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
