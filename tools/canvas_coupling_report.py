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
    "toast": "hub",
    "tr": "hub",
    "trf": "hub",
    "nodeRect": "hub",
    "world": "hub",
    "shell": "hub",
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
}

CATEGORY_ORDER = ("hub", "util", "state", "dom", "reach", "unclassified")


def _core_text() -> str:
    return (JS_DIR / CORE).read_text(encoding="utf-8")


def _declaration_kind(name: str, core_text: str) -> str:
    """名字在核心里的声明形态——分类的证据，不是猜的。"""
    for pattern, kind in (
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
        hits: Counter[str] = Counter()
        for name in core_names:
            if name in own:
                continue  # 同名局部变量不是对核心的耦合
            count = len(re.findall(rf"\b{re.escape(name)}\b", text))
            if count:
                hits[name] = count
        result[module] = dict(hits)
    return result


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
        definitions = len(re.findall(rf"^function\s+{re.escape(name)}\s*\(", stripped_core, re.M))
        result = max(0, calls - definitions)
        call_cache[name] = result
        return result

    per_category: Counter[str] = Counter()
    per_module: dict[str, dict[str, int]] = {}
    for module, refs in data.items():
        buckets: Counter[str] = Counter()
        for name, count in refs.items():
            category = CATEGORY_BY_NAME.get(name, "unclassified")
            per_category[category] += count
            buckets[category] += count
        per_module[module] = {
            "total": sum(refs.values()),
            "distinct_names": len(refs),
            **{category: buckets[category] for category in CATEGORY_ORDER if buckets[category]},
        }

    solo = [n for n in name_totals if len(owners[n]) == 1]
    shared = [n for n in name_totals if len(owners[n]) >= 2]
    movable = [n for n in solo if core_internal_calls(n) == 0]
    blocked = [n for n in solo if core_internal_calls(n) > 0]

    return {
        "core": CORE,
        "grand_total": sum(name_totals.values()),
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

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    scope = summary["scope"]
    print(f"核心：{summary['core']}    反向引用总数：{summary['grand_total']}")
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

    print("\n按模块（total / 独立名字数 / 可注入部分 = state+dom+reach）：")
    for module, stats in sorted(
        summary["per_module"].items(), key=lambda item: -item[1]["total"]
    ):
        injectable = sum(
            stats.get(category, 0) for category in ("state", "dom", "reach")
        )
        print(
            f"  {module:<32}{stats['total']:>5} / {stats['distinct_names']:>4}"
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
