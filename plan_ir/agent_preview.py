"""Literal workbook projection used by plan.import_excel (#399)."""
from collections import Counter
import re

from plan_ir.header_detector import find_header_row, map_headers
from plan_ir.sheet_classifier import is_design_plan_sheet_name, should_skip_sheet
from plan_ir.task_builder import add_tasks_from_rows, build_image_lookup, extract_copy_text
from plan_ir.text_utils import build_display_no, join_distinct_lines, join_row, sanitize_prompt_field

MAX_TASKS = 200
_MASK = re.compile(r"遮罩|蒙版|\bmask\b", re.I)
_NO_REMAKE = re.compile(r"(?:老图|旧图|原图).{0,10}(?:不用|无需|不必|不需要|不再)重做|(?:不需要|无需)出图")
_VIDEO_COVER = re.compile(r"视频封面|封面图片")
_VIDEO_WORK = re.compile(r"视频方案|视频脚本|视频镜头|产品视频|拍摄视频|视频制作")
_COPY_LINE = re.compile(r"文案\s*[:：]")


def _skip(row, fields):
    text = join_row(row)
    if _NO_REMAKE.search("".join(text.split())):
        return "no_remake"
    identity = " ".join(row[i] for i in fields.get("name", []) + fields.get("outputType", []) if i < len(row))
    identity = identity.strip() or next((cell for cell in row if cell.strip()), "")
    if (("视频" in identity or re.search(r"\bvideo\b", identity, re.I) or _VIDEO_WORK.search(text))
            and not _VIDEO_COVER.search(text)):
        return "video"
    if _MASK.search(identity):
        return "mask"
    return ""


def _visual(text, overlay):
    copy_lines = set(overlay.splitlines())
    return sanitize_prompt_field("\n".join(line for line in text.splitlines()
                                           if not _COPY_LINE.search(line) and line.strip() not in copy_lines))


def project_workbook(parsed, sheet_name):
    sheets = parsed["sheets"]
    has_design = any(is_design_plan_sheet_name(name) for name in sheets)
    sheets = {name: sheet for name, sheet in sheets.items()
              if (name == sheet_name if sheet_name is not None else not should_skip_sheet(name, has_design))}
    lookup = build_image_lookup(sheets, parsed["cell_images"])
    tasks, field_map, exclusions, warnings = [], {}, [], []
    counters = {}
    raw_tasks = []
    for name, sheet in sheets.items():
        rows = sheet.rows()
        physical = []
        for number in sheet.row_numbers:
            physical.append(number or (physical[-1] + 1 if physical else 1))
        header = find_header_row(rows)
        fields = map_headers(rows[header]) if header >= 0 else {}
        field_map[name] = {field: [column + 1 for column in columns] for field, columns in fields.items()}
        excluded = set()
        for index in range(header + 1, len(rows)):
            if reason := _skip(rows[index], fields):
                exclusions.append({"source_row": f"{name}!{physical[index]}", "skip_reason": reason})
                excluded.add(physical[index])
                rows[index] = []
        local = []
        add_tasks_from_rows(local, rows, name, counters, len(raw_tasks), lookup,
                            embedded_only=True, row_numbers=physical, split_blocks=False)
        task_rows = set()
        for task in local:
            index = int(task.source_row.rsplit("!", 1)[1]) - 1
            task.source_row = f"{name}!{physical[index]}"
            task_rows.add(physical[index])
            raw_tasks.append(task)
        physical_rows = set(physical)
        for anchor in sheet.image_anchors:
            row, col = anchor["row"], anchor["col"]
            if row in excluded:
                continue
            issue = ("ANCHOR_OUT_OF_RANGE" if row not in physical_rows else
                     "CONTINUATION_OR_NON_TASK_ROW" if row not in task_rows else None)
            if issue:
                warnings.append({"code": issue, "source_row": f"{name}!{row}", "column": col + 1})

    numbers = Counter()
    for raw in raw_tasks:
        inline_copy = "\n".join(line for part in (raw.requirements, raw.base_prompt) for line in part.splitlines()
                                if _COPY_LINE.search(line))
        overlay = join_distinct_lines(raw.copy_text, extract_copy_text(inline_copy))
        text = _visual(raw.base_prompt, overlay) or _visual(raw.requirements, overlay)
        if not text:
            # Copy-only rows are visual briefs, not permission to render overlay text (#399).
            text = f"为{sanitize_prompt_field(raw.name) or '本任务'}制作一张商品展示图；产品外观以用户提供的产品参考图为准。"
            if direction := sanitize_prompt_field(overlay):
                text += f"\n仅参考以下文案的主题安排画面，文字留待后期叠加，不在图中绘制：\n{direction}"
            warnings.append({"code": "PROMPT_NEEDS_REVIEW", "source_row": raw.source_row})
        refs = [{"kind": "workbook_image", "name": ref.asset_id.rsplit("/", 1)[-1],
                 "source": ref.source, "enabled": False, "usage": "description_only"}
                for ref in raw.generation_references if ref.data]
        if not refs:
            warnings.append({"code": "NO_WORKBOOK_IMAGE", "source_row": raw.source_row})
        # One main/A+ brief is one output, even when it describes multiple panels (#399).
        numbers[raw.section] += 1
        tasks.append({
            "name": raw.name, "section": raw.section,
            "display_no": build_display_no(raw.section, numbers[raw.section]),
            "source_row": raw.source_row, "source_rows": [raw.source_row],
            "base_prompt": text, "overlay_plan": overlay, "target_aspect_ratio": raw.target_aspect_ratio,
            "same_as_main": raw.same_as_main, "linked_main_index": raw.linked_main_index,
            "selected": False, "refs": [], "suggested_refs": refs,
        })
        if len(tasks) > MAX_TASKS:
            raise ValueError(f"Workbook exceeds {MAX_TASKS} plan tasks")
    return {"tasks": tasks, "fields": field_map, "excluded": exclusions, "warnings": warnings}
