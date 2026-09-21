# -*- coding: utf-8 -*-
"""任务构建模块：从解析后的 Excel 数据构建 Plan IR。

包含核心构建逻辑：
- build_plan_ir: 主入口，从解析结果构建 Plan IR
- extract_tasks: 向后兼容的别名
- add_tasks_from_rows: 表头路径
- add_tasks_from_plan_blocks: 无表头第一级兜底（块式方案块）
- add_tasks_from_unmapped_rows: 无表头第二级兜底
- build_image_lookup: 构建 imageLookup
- infer_section, infer_output_type, infer_aspect_ratio, infer_initial_section
- normalize_section_name
- apply_main_link, detect_same_as_main, parse_linked_main_index
- first_mapped, join_mapped, build_fallback_requirements
- is_plan_block_start, find_plan_block_end, should_skip_plan_block
- has_explicit_image_cue, join_block, extract_image_count, extract_copy_text
- guess_plan_block_name
"""
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from plan_ir import PlanIR, PlanTask, ReferenceImage
from plan_ir.text_utils import (
    contains_any, compact_line, distinct, first_non_empty, join_row, join_distinct_lines,
    sanitize_prompt_field, clean_task_name, build_display_no, guess_name,
)
from plan_ir.sheet_classifier import is_design_plan_sheet_name, should_skip_sheet
from plan_ir.header_detector import find_header_row, map_headers, looks_like_repeated_header_row
from plan_ir.row_classifier import looks_like_task_row, try_get_section_marker, looks_like_aplus_task
from plan_ir.reference_resolver import (
    add_operation_references_from_mapped_cells, join_reference_text_notes,
)


# ─── Section / 输出类型 / 比例推断（C# :2209/:2363/:2372/:2404/:1755）────────


def normalize_section_name(section: Optional[str]) -> str:
    """C# NormalizeSectionName:2209 — A+ 类 → 高级A+方案；主图类 → 主图方案；否则原样。"""
    text = compact_line(section)
    if not text:
        return ""
    if contains_any(text, "高级A+", "高级A＋", "A+", "A＋", "aplus", "a plus"):
        return "高级A+方案"
    if contains_any(
        text, "主图", "main", "场景图", "白底图", "尺寸图", "功能卖点", "对比图", "使用图", "细节图", "礼物图"
    ):
        return "主图方案"
    return section or ""


def infer_section(sheet_name: Optional[str], row_text: Optional[str]) -> str:
    """C# InferSection:2363。"""
    text = ((sheet_name or "") + " " + (row_text or "")).lower()
    if "a+" in text or "高级a" in text:
        return "高级A+方案"
    if "主图" in text:
        return "主图方案"
    if contains_any(sheet_name or "", "作图", "店铺", "品牌") or "店铺" in text or "品牌" in text:
        return "品牌店铺方案"
    return "图片方案"


def infer_output_type(text: Optional[str], section: Optional[str]) -> str:
    """C# InferOutputType:2372。"""
    source = (text or "").lower()
    if "白底" in source:
        return "Amazon white-background main image"
    if "场景" in source:
        return "lifestyle scene image"
    if "尺寸" in source:
        return "dimension infographic"
    if "细节" in source:
        return "detail infographic"
    if "对比" in source:
        return "comparison infographic"
    if section and "A+" in section:
        return "Premium A+ content module"
    return "ecommerce product image"


_RE_ASPECT_DIMS = re.compile(r"(\d{3,5})\s*[x×*]\s*(\d{2,5})", re.IGNORECASE)


def infer_aspect_ratio(section: Optional[str], source_aspect_ratio: Optional[str], row_text: Optional[str]) -> str:
    """C# InferAspectRatio:2404 — 比例串（3:4 优先）→ 尺寸串（必返回）→ A+ 默认 21:9 → 1:1。"""
    text = ((source_aspect_ratio or "") + " " + (row_text or "")).lower()
    for ratio in ("3:4", "4:3", "16:9", "21:9", "1:1"):
        if ratio in text:
            return ratio
    dims = _RE_ASPECT_DIMS.findall(text)
    if dims:
        try:
            width = float(dims[-1][0])
            height = float(dims[-1][1])
            if height > 0:
                ratio_value = width / height
                if ratio_value >= 3.0:
                    return "21:9"
                if ratio_value >= 1.45:
                    return "16:9"
                if ratio_value <= 0.8:
                    return "3:4"
                return "1:1"
        except ValueError:
            pass
    if section and "A+" in section:
        return "21:9"
    return "1:1"


def infer_initial_section(sheet_name: Optional[str], rows: List[List[str]], start: int) -> str:
    """C# InferInitialSection:1755。"""
    sheet = sheet_name or ""
    if contains_any(sheet, "主图") and contains_any(sheet, "a+", "A+", "高级A"):
        return "主图方案"
    if contains_any(sheet, "a+", "A+", "高级A"):
        return "高级A+方案"
    for i in range(start, len(rows)):
        text = join_row(rows[i])
        marker = try_get_section_marker(rows[i], text)
        if marker and "A+" in marker:
            return "主图方案"
    if contains_any(sheet, "图片方案", "作图", "主图"):
        return "主图方案"
    return infer_section(sheet_name, "")


# ─── 映射列取值（C# :2280/:2289）─────────────────────────────────────────────


def _col_values(row: List[str], col_map: Dict[str, List[int]], key: str) -> List[str]:
    cols = col_map.get(key, []) if col_map else []
    return [row[i] for i in cols if i < len(row)]


def first_mapped(row: List[str], col_map: Dict[str, List[int]], key: str) -> str:
    """C# FirstMapped:2280。"""
    for v in _col_values(row, col_map, key):
        if v and v.strip():
            return v
    return ""


def join_mapped(row: List[str], col_map: Dict[str, List[int]], key: str) -> str:
    """C# JoinMapped:2289 — 非空单元格值去重（.Distinct()）后以 \r\n 连接。"""
    parts = [v for v in _col_values(row, col_map, key) if v and v.strip()]
    return "\r\n".join(distinct(parts))


# ─── 回退需求 / 显示编号 / 主图关联（C# :2346/:2430/:2438/:2444/:2478）────────


def build_fallback_requirements(row: List[str], col_map: Optional[Dict[str, List[int]]]) -> str:
    """C# BuildFallbackRequirements:2346 — 排除已映射列，取未映射非空单元格（净化去重）。"""
    skip: set = set()
    if col_map:
        for key in ("name", "section", "outputType", "copy", "aspect", "prompt", "operationRef"):
            skip.update(col_map.get(key, []))
    values: List[str] = []
    for i, text in enumerate(row or []):
        if i in skip or not text or not text.strip():
            continue
        cleaned = sanitize_prompt_field(text)
        if cleaned and cleaned.strip() and cleaned not in values:
            values.append(cleaned)
    if values:
        return "\r\n".join(values)
    return sanitize_prompt_field(join_row(row))


def detect_same_as_main(task: PlanTask) -> bool:
    """C# DetectSameAsMain:2438 — 扫 Name+Requirements+CopyText+OverlayPlan 全文，命中任一标记。"""
    text = f"{task.name} {task.requirements} {task.copy_text} {task.overlay_plan}".lower()
    return contains_any(text, "a+同主图", "同主图", "同第", "参考主图")


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_LINKED_MAIN_MARKERS = ("主图", "同第", "参考主图")


def parse_linked_main_index(task: PlanTask) -> int:
    """C# ParseLinkedMainIndex:2444 — 从首个标记位置扫到文本末尾，取第一个数字/中文数字（一~九）。"""
    text = f"{task.name} {task.requirements} {task.copy_text} {task.overlay_plan}"
    for marker in _LINKED_MAIN_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        for ch in text[idx:]:
            if ch.isdigit():
                try:
                    return int(ch)
                except ValueError:  # 上标等非 Nd 数字，C# char.IsDigit 亦不命中
                    continue
            if ch in _CN_DIGITS:
                return _CN_DIGITS[ch]
    return 0


def apply_main_link(task: PlanTask) -> None:
    """主图关联三字段（表头/无表头路径共用）。"""
    task.same_as_main = detect_same_as_main(task)
    task.linked_main_index = parse_linked_main_index(task)
    task.linked_main_label = (
        f"同主图 {task.linked_main_index}"
        if task.linked_main_index > 0
        else ("同主图" if task.same_as_main else "")
    )


# ─── imageLookup 构建（C# ExtractCellImages:1232 结果集，OrdinalIgnoreCase）────


def build_image_lookup(
    sheets: Dict[str, Any],
    cell_images: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """构建 C# imageLookup 等价物：{lowercase_key: {"path","data","origin"}}。

    - cell images：key = cNvPr name（ID_xxxx），origin="cellimage"
    - drawing 锚点：key = CELL:{sheet}!{row}:{col}#{seq}（C# CellImageKey:1456），origin="anchored"
    """
    lookup: Dict[str, Dict[str, Any]] = {}
    for name, val in (cell_images or {}).items():
        if isinstance(val, dict):
            data, path = val.get("data"), val.get("path")
        else:  # 兼容旧格式（纯 bytes）
            data, path = val, f"maps/{name}.png"
        if data is not None:
            lookup[name.lower()] = {"path": path or f"maps/{name}.png", "data": data, "origin": "cellimage"}
    for sheet_name, sheet_data in (sheets or {}).items():
        for anchor in getattr(sheet_data, "image_anchors", None) or []:
            data = getattr(sheet_data, "images", {}).get(anchor.get("rid"))
            if data is None:
                continue
            key = f"CELL:{sheet_name}!{anchor.get('row')}:{anchor.get('col')}#{anchor.get('seq')}"
            lookup[key.lower()] = {
                "path": anchor.get("path") or key,
                "data": data,
                "origin": "anchored",
            }
    return lookup


# ─── 块式方案块（C# AddTasksFromPlanBlocks:1921 起，无表头第一级兜底）──────────────

_RE_MODULE_LABEL = re.compile(r"模块\s*\d|模块\s*[一二三四五六七八九]")
_RE_BLOCK_START_COUNT = re.compile(r"出\s*\d+\s*张|共\s*\d+\s*张|\d+\s*张(长图|方图|左大图|右小图|图|banner)", re.IGNORECASE)
_RE_IMAGE_CUE = re.compile(r"(?:出|共)\s*\d+\s*张|\d+\s*张(长图|方图|左大图|右小图|图|banner)", re.IGNORECASE)
_RE_CATEGORY_PAGE = re.compile(r"^分类页\s*\d|^🏠|^Home", re.IGNORECASE)
_RE_COUNT_TOTAL = re.compile(r"共\s*(\d+)\s*张(?:长图|方图|左大图|右小图|图|banner)?", re.IGNORECASE)
_RE_COUNT_EACH = re.compile(r"(?:出\s*)?(\d+)\s*张(?:长图|方图|左大图|右小图|图|banner)?", re.IGNORECASE)


def is_plan_block_start(text: Optional[str]) -> bool:
    """C# IsPlanBlockStart:1980 — 视频封面/封面图片 / 模块N / 出N张|共N张|N张(长图|...)。"""
    if not text or not text.strip():
        return False
    if contains_any(text, "不需要出图", "无需出图"):
        return False
    if contains_any(text, "视频") and not contains_any(text, "视频封面", "封面图片", "图片模块"):
        return False
    return (
        contains_any(text, "视频封面", "封面图片")
        or bool(_RE_MODULE_LABEL.search(text))
        or bool(_RE_BLOCK_START_COUNT.search(text))
    )


def find_plan_block_end(rows: List[List[str]], start: int) -> int:
    """C# FindPlanBlockEnd:1990 — 下一个块起始/分类页行索引；无则 len(rows)。"""
    for i in range(start, len(rows)):
        text = join_row(rows[i])
        if not text or not text.strip():
            continue
        if is_plan_block_start(text):
            return i
        if _RE_CATEGORY_PAGE.search(text):
            return i
    return len(rows)


def should_skip_plan_block(text: Optional[str]) -> bool:
    """C# ShouldSkipPlanBlock:2002 — 空白 / 不需要出图 / 视频（无豁免词）。"""
    if not text or not text.strip():
        return True
    if contains_any(text, "不需要出图", "无需出图"):
        return True
    if contains_any(text, "视频") and not contains_any(text, "视频封面", "封面图片", "视频+图片模块"):
        return True
    return False


def has_explicit_image_cue(text: Optional[str]) -> bool:
    """C# HasExplicitImageCue:2010 — 视频封面/封面图片 或 出/共N张 / N张(长图|...)。"""
    return contains_any(text, "视频封面", "封面图片") or bool(_RE_IMAGE_CUE.search(text or ""))


def join_block(rows: List[List[str]], start: int, end: int) -> str:
    """C# JoinBlock:2016 — 逐行 compact、剔除空行、18 行上限、保序去重、\r\n 连接并 trim。"""
    lines: List[str] = []
    for i in range(start, min(end, len(rows))):
        text = compact_line(join_row(rows[i]))
        if text and text.strip():
            lines.append(text)
        if len(lines) >= 18:
            break
    return "\r\n".join(dict.fromkeys(lines)).strip()


def extract_image_count(text: Optional[str]) -> int:
    """C# ExtractImageCount:2029 — 共N张 优先（上限 12）；否则逐项 N张 求和（还需前缀排除、单项≤12）；
    无线索且含 视频封面/封面图片 → 1；结果钳制 [1, 12]。"""
    total_match = _RE_COUNT_TOTAL.search(text or "")
    if total_match:
        total_count = int(total_match.group(1))
        if total_count > 0:
            return min(total_count, 12)
    total = 0
    for m in _RE_COUNT_EACH.finditer(text or ""):
        prefix = (text or "")[max(0, m.start() - 4):m.start()]
        if "还需" in prefix:
            continue
        count = int(m.group(1))
        if count > 0 and count <= 12:
            total += count
    if total <= 0 and contains_any(text, "视频封面", "封面图片"):
        total = 1
    return max(1, min(1 if total <= 0 else total, 12))


def extract_copy_text(text: Optional[str]) -> str:
    """C# ExtractCopyText:2048 — 每行含「文案」则取首个全/半角冒号后的值（无冒号取整行），保序去重，\r\n 连接。"""
    lines: List[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        index = line.lower().find("文案")
        if index < 0:
            continue
        value = line
        m = re.search(r"[：:]", line[index:])
        if m:
            colon = index + m.start()
            if colon + 1 < len(line):
                value = line[colon + 1:].strip()
        if value and value.strip():
            lines.append(value)
    return "\r\n".join(dict.fromkeys(lines))


def guess_plan_block_name(block_text: Optional[str], section: str, index: int) -> str:
    """C# GuessPlanBlockName:2063 — 首个非空行（70 字符截断）；无则显示编号。"""
    for raw in (block_text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        return line[:70]
    return build_display_no(section, index)


def add_tasks_from_plan_blocks(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
) -> int:
    """C# AddTasksFromPlanBlocks:1921 — 块式方案块（无表头第一级兜底）。返回 sort_index。

    忠实移植注意：
    - Name 不做 clean_task_name（C# 块路径就是 baseName/count 拼接）；
    - 不挂 operation_references / cell_image（C# 该路径没有）；
    - C# for 循环内 `i = Math.Max(i, end - 1)` 后还有 i++ → python while 等价 `+ 1`。
    """
    section = infer_section(sheet_name, "")
    i = 0
    while i < len(rows):
        row_text = join_row(rows[i])
        if not is_plan_block_start(row_text):
            i += 1
            continue

        end = find_plan_block_end(rows, i + 1)
        block_text = join_block(rows, max(0, i - 2), end)
        if should_skip_plan_block(block_text):
            i = max(i, end - 1) + 1
            continue
        if not has_explicit_image_cue(block_text) and _RE_MODULE_LABEL.search(row_text):
            i += 1
            continue

        count = extract_image_count(block_text)
        next_index = counters[section] + 1 if section in counters else 1
        base_name = first_non_empty(
            compact_line(row_text),
            guess_plan_block_name(block_text, section, next_index),
        )
        copy_text = extract_copy_text(block_text)
        aspect = infer_aspect_ratio(section, "", block_text)

        for copy_index in range(1, count + 1):
            if section not in counters:
                counters[section] = 0
            counters[section] += 1
            sort_index += 1
            name = f"{base_name} {copy_index}/{count}" if count > 1 else base_name
            task = PlanTask(
                task_id=f"excel_{sort_index}",
                name=name,
                section=section,
                source_row=f"{sheet_name}!{i + 1}",
                sort_index=sort_index,
                section_index=counters[section],
                display_no=build_display_no(section, counters[section]),
                output_type=infer_output_type(block_text, section),
                source_aspect_ratio=aspect,
                target_aspect_ratio=aspect,
                requirements=block_text,
                copy_handling_mode="none" if not (copy_text and copy_text.strip()) else "manual_overlay_after_generation",
                copy_text=copy_text,
                status="待处理",
                selected=True,
            )
            apply_main_link(task)
            tasks.append(task)
        i = max(i, end - 1) + 1
    return sort_index


# ─── 任务构建主循环（C# AddTasksFromRows:1615 / AddTasksFromUnmappedRows:1707）


def add_tasks_from_rows(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
    image_lookup: Dict[str, Dict[str, Any]],
) -> int:
    """C# AddTasksFromRows:1615 — 表头路径（image_lookup 为 None 时退化为无锚定图）。

    无表头分支为两级兜底（C# :1618-1623）：先 AddTasksFromPlanBlocks（块式方案块），
    仅其 0 产出时才 AddTasksFromUnmappedRows。返回 sort_index（既有 `return len(tasks)`
    会丢 sort_index、多 sheet 时 task_id 重复，顺手修复）。
    """
    header_row = find_header_row(rows)
    if header_row < 0:
        before = len(tasks)
        sort_index = add_tasks_from_plan_blocks(tasks, rows, sheet_name, counters, sort_index)
        if len(tasks) == before:
            sort_index = add_tasks_from_unmapped_rows(tasks, rows, sheet_name, counters, sort_index)
        return sort_index
    headers = rows[header_row]
    col_map = map_headers(headers)
    current_section = infer_initial_section(sheet_name, rows, header_row + 1)

    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        row_text = join_row(row)
        section_marker = try_get_section_marker(row, row_text)
        if section_marker:
            current_section = section_marker
            continue
        if looks_like_repeated_header_row(row):
            continue
        if not looks_like_task_row(row, row_text, True):
            continue

        mapped_name = first_mapped(row, col_map, "name")
        mapped_requirements = join_mapped(row, col_map, "requirements")
        mapped_output_type = first_mapped(row, col_map, "outputType")
        row_lead = first_non_empty(*row)
        mapped_section = first_mapped(row, col_map, "section")

        section = normalize_section_name(mapped_section)
        if not section or not section.strip():
            output_type_section = normalize_section_name(mapped_output_type)
            if output_type_section and output_type_section.strip():
                section = output_type_section
            else:
                section = "高级A+方案" if looks_like_aplus_task(mapped_name, mapped_requirements) else ""
                if not section:
                    section = "高级A+方案" if looks_like_aplus_task(row_lead, mapped_requirements) else ""
        if not section or not section.strip():
            section = first_non_empty(
                current_section,
                infer_section(sheet_name, row_text),
            )
        else:
            current_section = section
        counters[section] = counters.get(section, 0) + 1
        section_idx = counters[section]
        sort_index += 1

        requirements = mapped_requirements
        copy_text = join_mapped(row, col_map, "copy")
        if not requirements or not requirements.strip():
            requirements = build_fallback_requirements(row, col_map)
        requirements = join_distinct_lines(
            requirements,
            join_reference_text_notes(row, headers, col_map, image_lookup),
        )

        row_number = i + 1  # 1-based（C# 行索引 +1 = SourceRow）
        refs = add_operation_references_from_mapped_cells(
            row, headers, col_map, image_lookup, sheet_name, row_number
        )

        source_aspect = first_mapped(row, col_map, "aspect")
        task_name = clean_task_name(first_non_empty(mapped_name, guess_name(row, row_text, section, section_idx)))
        task = PlanTask(
            task_id=f"excel_{sort_index}",
            name=task_name,
            section=section,
            source_row=f"{sheet_name}!{row_number}",
            sort_index=sort_index,
            section_index=section_idx,
            display_no=build_display_no(section, section_idx),
            output_type=first_non_empty(mapped_output_type, infer_output_type(row_text, section)),
            source_aspect_ratio=source_aspect,
            target_aspect_ratio=infer_aspect_ratio(section, source_aspect, row_text),
            requirements=requirements,
            copy_handling_mode="none" if not (copy_text and copy_text.strip()) else "manual_overlay_after_generation",
            copy_text=copy_text,
            base_prompt=first_mapped(row, col_map, "prompt"),
            status="待处理",
            selected=True,
        )
        # 单元格图片挂接（issue #101：任务带图统计用；C# 该路径不挂，python 扩展）
        for value, _source in refs:
            if isinstance(value, dict) and value.get("origin") == "cellimage":
                task.cell_image_name = os.path.splitext(os.path.basename(value["path"]))[0]
                task.cell_image_data = value.get("data") or b""
                task.has_cell_image = True
                break
        apply_main_link(task)
        task.generation_references = [
            ReferenceImage(
                asset_id=value.get("path") if isinstance(value, dict) else value,
                source=source,
                data=value.get("data") if isinstance(value, dict) else None,
            )
            for value, source in refs
        ]
        tasks.append(task)
    return sort_index


def add_tasks_from_unmapped_rows(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
) -> int:
    """C# AddTasksFromUnmappedRows:1707 — 无表头兜底路径。"""
    current_section = infer_initial_section(sheet_name, rows, 0)
    for i, row in enumerate(rows):
        row_text = join_row(row)
        section_marker = try_get_section_marker(row, row_text)
        if section_marker:
            current_section = section_marker
            continue
        if looks_like_repeated_header_row(row):
            continue
        if not looks_like_task_row(row, row_text, False):
            continue
        section = first_non_empty(current_section, infer_section(sheet_name, row_text))
        counters[section] = counters.get(section, 0) + 1
        section_idx = counters[section]
        sort_index += 1
        first = next((c for c in row if c and c.strip()), "")
        if first.startswith("同主图"):
            first = "同主图"
        name = clean_task_name(first if first.strip() else guess_name(row, row_text, section, section_idx))
        task = PlanTask(
            task_id=f"excel_{sort_index}",
            name=name,
            section=section,
            source_row=f"{sheet_name}!{i + 1}",
            sort_index=sort_index,
            section_index=section_idx,
            display_no=build_display_no(section, section_idx),
            output_type=infer_output_type(row_text, section),
            target_aspect_ratio=infer_aspect_ratio(section, "", row_text),
            requirements=sanitize_prompt_field(join_row(row)),
            status="待处理",
            selected=True,
        )
        apply_main_link(task)
        tasks.append(task)
    return sort_index


# ─── 入口（C# ImportXlsxPackage:1140 / ImportRowsForSmoke:1746）───────────────


def build_plan_ir(source_file: str, parse_result: Dict[str, Any]) -> PlanIR:
    """C# ImportXlsxPackage 主循环。

    返回 PlanIR 对象。
    口径：
    - should_skip_sheet（有设计表时只扫设计表+补充表）
    - ScannedRowCount = 过滤后各表 <row> 元素数之和
    - 图片注记 = imageLookup 键数（cellimages + drawing 锚点图）
    """
    tasks: List[PlanTask] = []
    notes: List[str] = []
    counters: Dict[str, int] = {}
    sort_index = 0
    scanned_rows = 0

    sheets: Dict[str, Any] = parse_result.get("sheets") or {}
    cell_images: Dict[str, Any] = parse_result.get("cell_images") or {}
    image_lookup = build_image_lookup(sheets, cell_images)
    has_design = any(is_design_plan_sheet_name(name) for name in sheets)

    for sheet_name, sheet_data in sheets.items():
        if should_skip_sheet(sheet_name, has_design):
            continue
        rows = sheet_data.rows()
        scanned_rows += len(rows)
        sort_index = add_tasks_from_rows(
            tasks, rows, sheet_name, counters, sort_index, image_lookup
        )

    if image_lookup:
        notes.append(f"已抽取 Excel 单元格图片：{len(image_lookup)} 张。")

    plan = PlanIR(
        source_file=source_file,
        tasks=tasks,
        sheet_count=len(sheets),
        scanned_rows=scanned_rows,
        notes=notes,
    )
    # C# :1207-1210 — 有任务时追加 OpenXML 兜底注记
    if len(plan.tasks) > 0:
        plan.notes.append("已用 OpenXML 兜底读取 .xlsx，并自动识别任务字段。")
    return plan


# Backward compatibility alias
extract_tasks = build_plan_ir
