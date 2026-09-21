# -*- coding: utf-8 -*-
"""Plan IR Builder (compatibility shim).

⚠️ 本文件是向后兼容垫片，仅 re-export plan_ir/ 包中的函数。
一旦所有调用者迁移到 `from plan_ir import build_plan_ir`，本文件可移除。

从 Excel 解析结果构建 Plan IR。实际实现位于 plan_ir/ 包中的 7 个模块：
- plan_ir/text_utils: 基础文本工具
- plan_ir/sheet_classifier: Sheet 分类
- plan_ir/header_detector: Header 检测和映射
- plan_ir/row_classifier: 行分类
- plan_ir/reference_resolver: 参考图解析
- plan_ir/task_builder: 任务构建
"""
from typing import Any, Dict, List, Optional, Tuple

# Re-export data structures
from plan_ir import PlanIR, PlanTask, ReferenceImage  # noqa: F401

# Re-export text utils
from plan_ir.text_utils import (
    contains_any,
    compact_line,
    first_non_empty,
    join_row,
    join_distinct_lines,
    sanitize_prompt_field,
    clean_task_name,
    build_display_no,
    guess_name,
)  # noqa: F401

# Re-export sheet classifier
from plan_ir.sheet_classifier import (
    is_design_plan_sheet_name,
    is_supplemental_plan_sheet_name,
    should_skip_sheet,
)  # noqa: F401

# Re-export header detector
from plan_ir.header_detector import (
    header_cell_score,
    looks_like_composite_header_cell,
    header_cell_looks_like_column_name,
    looks_like_repeated_header_row,
    has_likely_task_between,
    find_header_row,
    map_headers,
)  # noqa: F401

# Re-export row classifier
from plan_ir.row_classifier import (
    looks_like_note_row,
    try_get_section_marker,
    looks_like_task_row,
    starts_with_aplus_task_label,
    has_number_prefix_aplus_task_label,
    looks_like_aplus_task,
)  # noqa: F401

# Re-export reference resolver
from plan_ir.reference_resolver import (
    is_image_file_path,
    next_header_column,
    resolve_anchored_reference_files,
    resolve_reference_files_text,
    resolve_reference_files,
    looks_like_machine_image_reference,
    join_reference_text_notes,
    add_operation_references_from_mapped_cells,
)  # noqa: F401

# Re-export task builder
from plan_ir.task_builder import (
    normalize_section_name,
    infer_section,
    infer_output_type,
    infer_aspect_ratio,
    infer_initial_section,
    first_mapped,
    join_mapped,
    build_fallback_requirements,
    detect_same_as_main,
    parse_linked_main_index,
    apply_main_link,
    build_image_lookup,
    is_plan_block_start,
    find_plan_block_end,
    should_skip_plan_block,
    has_explicit_image_cue,
    join_block,
    extract_image_count,
    extract_copy_text,
    guess_plan_block_name,
    add_tasks_from_plan_blocks,
    add_tasks_from_rows,
    add_tasks_from_unmapped_rows,
    build_plan_ir,
    extract_tasks,
)  # noqa: F401

__all__ = [
    # Data structures
    "PlanIR",
    "PlanTask",
    "ReferenceImage",
    # Text utils
    "contains_any",
    "compact_line",
    "first_non_empty",
    "join_row",
    "join_distinct_lines",
    "sanitize_prompt_field",
    "clean_task_name",
    "build_display_no",
    "guess_name",
    # Sheet classifier
    "is_design_plan_sheet_name",
    "is_supplemental_plan_sheet_name",
    "should_skip_sheet",
    # Header detector
    "header_cell_score",
    "looks_like_composite_header_cell",
    "header_cell_looks_like_column_name",
    "looks_like_repeated_header_row",
    "has_likely_task_between",
    "find_header_row",
    "map_headers",
    # Row classifier
    "looks_like_note_row",
    "try_get_section_marker",
    "looks_like_task_row",
    "starts_with_aplus_task_label",
    "has_number_prefix_aplus_task_label",
    "looks_like_aplus_task",
    # Reference resolver
    "is_image_file_path",
    "next_header_column",
    "resolve_anchored_reference_files",
    "resolve_reference_files_text",
    "resolve_reference_files",
    "looks_like_machine_image_reference",
    "join_reference_text_notes",
    "add_operation_references_from_mapped_cells",
    # Task builder
    "normalize_section_name",
    "infer_section",
    "infer_output_type",
    "infer_aspect_ratio",
    "infer_initial_section",
    "first_mapped",
    "join_mapped",
    "build_fallback_requirements",
    "detect_same_as_main",
    "parse_linked_main_index",
    "apply_main_link",
    "build_image_lookup",
    "is_plan_block_start",
    "find_plan_block_end",
    "should_skip_plan_block",
    "has_explicit_image_cue",
    "join_block",
    "extract_image_count",
    "extract_copy_text",
    "guess_plan_block_name",
    "add_tasks_from_plan_blocks",
    "add_tasks_from_rows",
    "add_tasks_from_unmapped_rows",
    "build_plan_ir",
    "extract_tasks",
]
