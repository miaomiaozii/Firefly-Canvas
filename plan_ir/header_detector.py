# -*- coding: utf-8 -*-
"""Header 检测和映射模块。

提供：
- header_cell_score: 表头单元格关键词计分
- looks_like_composite_header_cell: 判断是否为复合表头单元格
- header_cell_looks_like_column_name: 判断是否为列名样式
- looks_like_repeated_header_row: 判断是否为重复表头行
- has_likely_task_between: 判断两行之间是否有疑似任务行
- find_header_row: 查找表头行
- map_headers: 将表头行映射为列名到索引的映射
"""
from typing import Dict, List

from plan_ir.text_utils import contains_any, compact_line, join_row
from plan_ir.row_classifier import looks_like_task_row, try_get_section_marker


def header_cell_score(compact: str) -> int:
    """C# HeaderCellScore:2195 — 表头单元格关键词计分（入参需已 CompactLine）。"""
    if not compact or not compact.strip():
        return 0
    if contains_any(compact, "图序", "图号", "序号", "顺序"):
        return 1
    if contains_any(compact, "任务名称", "图片名称", "模块名称", "任务标题", "图片标题"):
        return 2
    if contains_any(compact, "内容/要素", "内容要素", "展示元素", "运营需求", "画面需求", "作图需求", "要求"):
        return 2
    if contains_any(compact, "图片示例", "参考图", "参考图片", "效果图") or compact == "参考":
        return 2
    if contains_any(compact, "图类型", "画面类型", "输出类型", "方案类型", "图片类型"):
        return 2
    if contains_any(
        compact, "提示词", "prompt", "文案", "文字", "字幕", "叠字", "比例", "宽高", "尺寸",
        "类型", "名称", "标题", "类目", "像素", "板块", "所属", "分类", "描述", "布局",
        "场景", "备注", "方案", "运营",
    ):
        return 1
    if len(compact) <= 18 and contains_any(compact, "任务", "图片", "图", "需求", "卖点", "内容"):
        return 1
    return 0


_COMPOSITE_HEADER_KEYWORDS = (
    "任务名称", "图片名称", "模块名称", "任务标题", "图片标题",
    "所属板块", "所属分类", "方案分类",
    "图类型", "画面类型", "输出类型", "方案类型", "图片类型",
    "运营文案", "卖点文案", "展示文案", "画面文案",
    "画面比例", "图片比例", "图片尺寸", "输出尺寸", "图片像素",
)


def looks_like_composite_header_cell(compact: str) -> bool:
    """C# LooksLikeCompositeHeaderCell:2170 — ≤18 字符且含复合列名关键词。"""
    if not compact or not compact.strip() or len(compact) > 18:
        return False
    return contains_any(compact, *_COMPOSITE_HEADER_KEYWORDS)


_COLUMN_NAME_EXACT = (
    "顺序", "序号", "图序", "图号", "要求", "需求", "运营需求", "画面需求", "作图需求",
    "内容", "内容/要素", "内容要素", "展示元素", "图片示例", "参考图", "参考图片", "参考",
    "效果图", "文案", "卖点文案", "文字", "字幕", "叠字", "比例", "尺寸", "宽高", "像素",
    "类型", "名称", "标题", "类目", "描述", "布局", "场景", "备注", "方案", "运营",
    "提示词", "prompt", "板块", "所属", "分类", "方案类型", "图片类型", "输出类型",
    "图类型", "画面类型", "任务", "模块", "图片",
)


def header_cell_looks_like_column_name(compact: str) -> bool:
    """C# HeaderCellLooksLikeColumnName:2126 — 精确列名集合 + 前缀 '顺序 ' + 复合格。"""
    if not compact or not compact.strip():
        return False
    if compact == "顺序" or compact.startswith("顺序 "):
        return True
    return compact in _COLUMN_NAME_EXACT or looks_like_composite_header_cell(compact)


def looks_like_repeated_header_row(row: List[str]) -> bool:
    """C# LooksLikeRepeatedHeaderRow:2116 — ≥2 个列名样式格且总分达标（2 格≥2，否则≥3）。"""
    non_empty = [compact_line(c) for c in (row or []) if c and c.strip()]
    if len(non_empty) < 2:
        return False
    scored_cells = sum(1 for c in non_empty if header_cell_looks_like_column_name(c))
    score = sum(header_cell_score(c) for c in non_empty)
    required_score = 2 if len(non_empty) == 2 else 3
    return scored_cells >= 2 and score >= required_score


def has_likely_task_between(rows: List[List[str]], start: int, end_exclusive: int) -> bool:
    """C# HasLikelyTaskBetween:2096 — (start, endExclusive) 内存在疑似任务行。"""
    for i in range(max(0, start), min(len(rows), end_exclusive)):
        row_text = join_row(rows[i])
        if not row_text or not row_text.strip():
            continue
        if looks_like_repeated_header_row(rows[i]):
            continue
        if try_get_section_marker(rows[i], row_text):
            continue
        if sum(1 for c in rows[i] if c and c.strip()) >= 2:
            return True
        if looks_like_task_row(rows[i], row_text, False):
            return True
    return False


def find_header_row(rows: List[List[str]]) -> int:
    """C# FindHeaderRow:2074 — 前 80 行内重复表头行；任务间隔保护。"""
    best_row = -1
    best_score = 0
    for i in range(min(len(rows), 80)):
        if not looks_like_repeated_header_row(rows[i]):
            continue
        score = sum(header_cell_score(compact_line(c)) for c in rows[i])
        if score > best_score:
            if best_row >= 0 and has_likely_task_between(rows, best_row + 1, i):
                continue
            best_score = score
            best_row = i
        elif score == best_score and score >= 2 and not has_likely_task_between(rows, best_row + 1, i):
            best_row = i
    return best_row if best_score >= 2 else -1


def _add_map(col_map: Dict[str, List[int]], key: str, index: int) -> None:
    """C# AddMap:2244 — 同键多列（Append）。"""
    col_map.setdefault(key, []).append(index)


def map_headers(header_row: List[str]) -> Dict[str, List[int]]:
    """C# MapHeaders:2218 — 有序优先级链（先匹配先占用，同键多列）。"""
    col_map: Dict[str, List[int]] = {}
    for i, h in enumerate(header_row or []):
        if not h or not h.strip():
            continue
        if compact_line(h) == "参考" or contains_any(h, "效果图", "图片示例", "参考图", "参考图补充", "参考图片", "图片参考"):
            _add_map(col_map, "operationRef", i)
        elif contains_any(h, "图序", "图号", "序号", "顺序"):
            _add_map(col_map, "name", i)
        elif contains_any(h, "文案", "叠字", "文字", "字幕", "卖点文案"):
            _add_map(col_map, "copy", i)
        elif contains_any(h, "内容/要素", "内容要素", "展示元素", "评论+卖点", "卖点", "运营需求", "画面需求", "作图需求", "要求", "类目"):
            _add_map(col_map, "requirements", i)
        elif contains_any(h, "比例", "宽高", "尺寸", "像素"):
            _add_map(col_map, "aspect", i)
        elif contains_any(h, "提示词", "prompt"):
            _add_map(col_map, "prompt", i)
        elif contains_any(h, "板块", "所属", "分类", "方案类型", "图片类型"):
            _add_map(col_map, "section", i)
        elif contains_any(h, "输出类型", "图类型", "画面类型", "类型"):
            _add_map(col_map, "outputType", i)
        elif contains_any(h, "任务", "模块", "图片", "名称", "标题", "序号"):
            _add_map(col_map, "name", i)
        elif contains_any(h, "运营", "需求", "方案", "画面", "描述", "要求", "布局", "场景", "备注", "内容"):
            _add_map(col_map, "requirements", i)
    return col_map
