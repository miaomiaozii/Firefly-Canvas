# -*- coding: utf-8 -*-
"""Sheet 分类模块。

提供：
- is_design_plan_sheet_name: 判断是否为设计计划表
- is_supplemental_plan_sheet_name: 判断是否为补充计划表
- should_skip_sheet: 判断是否应跳过该表
"""
from typing import Optional

from plan_ir.text_utils import contains_any, compact_line


def is_design_plan_sheet_name(sheet_name: Optional[str]) -> bool:
    """C# IsDesignPlanSheetName:1559 — 含 作图|图片方案|生图方案 且不含 脚本|视频。"""
    name = sheet_name or ""
    return contains_any(name, "作图", "图片方案", "生图方案") and not contains_any(name, "脚本", "视频")


def is_supplemental_plan_sheet_name(sheet_name: Optional[str]) -> bool:
    """C# IsSupplementalPlanSheetName:1565 — A+/主图/详情类补充方案表。"""
    name = sheet_name or ""
    if contains_any(name, "脚本", "视频"):
        return False
    if contains_any(
        name,
        "A+方案", "A＋方案", "A+模块", "A＋模块", "A+页面", "A＋页面",
        "A+内容", "A＋内容", "主图方案", "详情页", "详情图",
    ):
        return True
    compact = compact_line(name).lower()
    return compact in ("主图", "a+", "a＋")


def should_skip_sheet(sheet_name: Optional[str], has_design_plan_sheet: bool) -> bool:
    """C# ShouldSkipSheet:1576 — 有设计表时只扫设计表+补充表。"""
    if has_design_plan_sheet:
        return (
            not is_design_plan_sheet_name(sheet_name)
            and not is_supplemental_plan_sheet_name(sheet_name)
        )
    return contains_any(sheet_name, "商品分类", "视频脚本", "脚本", "尺寸需求") or (
        (sheet_name or "").lower() == "sheet5"
    )
