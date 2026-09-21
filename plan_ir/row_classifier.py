# -*- coding: utf-8 -*-
"""行分类模块。

提供：
- looks_like_note_row: 判断是否为备注行
- try_get_section_marker: 获取区块标记
- looks_like_task_row: 判断是否为任务行
- starts_with_aplus_task_label: 判断是否以 A+ 任务标签开头
- has_number_prefix_aplus_task_label: 判断是否有数字前缀的 A+ 任务标签
- looks_like_aplus_task: 判断是否为 A+ 任务
"""
import re
from typing import List, Optional

from plan_ir.text_utils import contains_any, compact_line

# ─── A+ 标签（C# :1907/:1914/:1897）─────────────────────────────────────────

_APLUS_KEYWORDS = (
    "首图|首屏|头图|横幅|banner|模块|大场景|对比|功能|场景|细节|产品|轮播|拼接|详情|主图|卖点|核心|品牌|故事|品牌故事"
)
_RE_APLUS_DIGIT = re.compile(r"^\s*a\s*[+＋]\s*\d", re.IGNORECASE)
_RE_APLUS_CN = re.compile(r"^\s*a\s*[+＋]\s*[一二三四五六七八九十]", re.IGNORECASE)
_RE_APLUS_KEYWORD = re.compile(rf"^\s*a\s*[+＋]\s*({_APLUS_KEYWORDS})", re.IGNORECASE)
_RE_APLUS_NUMBER_PREFIX = re.compile(
    r"^\s*\d+\s*[、\.)-]\s*a\s*[+＋]\s*(\d|[一二三四五六七八九十]|" + _APLUS_KEYWORDS + ")",
    re.IGNORECASE,
)
_RE_APLUS_NUMBER_ONLY = re.compile(r"^\s*\d+\s*[、\.)]\s*$")


def starts_with_aplus_task_label(text: Optional[str]) -> bool:
    """C# StartsWithAPlusTaskLabel:1907 — ^A+数字 / ^A+中文数字 / ^A+关键词。"""
    if not text or not text.strip():
        return False
    return bool(
        _RE_APLUS_DIGIT.match(text)
        or _RE_APLUS_CN.match(text)
        or _RE_APLUS_KEYWORD.match(text)
    )


def has_number_prefix_aplus_task_label(text: Optional[str]) -> bool:
    """C# HasNumberPrefixAPlusTaskLabel:1914 — ^\\d+[、\\.)-]A+标签。"""
    if not text or not text.strip():
        return False
    return bool(_RE_APLUS_NUMBER_PREFIX.match(text))


def looks_like_aplus_task(name: Optional[str], requirements: Optional[str]) -> bool:
    """C# LooksLikeAPlusTask:1897。"""
    name_c = compact_line(name)
    if starts_with_aplus_task_label(name_c) or has_number_prefix_aplus_task_label(name_c):
        return True
    req_c = compact_line(requirements)
    if starts_with_aplus_task_label(req_c):
        return True
    if name_c.strip() and not _RE_APLUS_NUMBER_ONLY.match(name_c):
        return False
    return starts_with_aplus_task_label(req_c)


# ─── 行分类（C# :2246/:2266/:1877）───────────────────────────────────────────


def looks_like_note_row(row: List[str]) -> bool:
    """C# LooksLikeNoteRow:2266 — 首个非空格恰为 备注/说明/注意事项（可带冒号）。"""
    if not row:
        return False
    first = next((c for c in row if c and c.strip()), "")
    return compact_line(first) in ("备注", "备注：", "说明", "说明：", "注意事项", "注意事项：")


def try_get_section_marker(row: List[str], row_text: Optional[str]) -> str:
    """C# TryGetSectionMarker:1877 — A+/主图方案 区块标记行（非空 ≤2 格）。"""
    if not row_text or not row_text.strip():
        return ""
    non_empty = sum(1 for c in (row or []) if c and c.strip())
    if non_empty > 2:
        return ""
    text = compact_line(row_text)
    if contains_any(text, "高级A+", "高级A＋", "A+", "A＋"):
        if starts_with_aplus_task_label(text):
            return ""
        return "高级A+方案"
    if contains_any(text, "主图方案", "主图列表"):
        return "主图方案"
    return ""


def looks_like_task_row(row: List[str], row_text: Optional[str], has_header: bool) -> bool:
    """C# LooksLikeTaskRow:2246。"""
    if not row_text or not row_text.strip() or len(row_text) < 4:
        return False
    non_empty = sum(1 for c in (row or []) if c and c.strip())
    if looks_like_note_row(row):
        return False
    if contains_any(row_text, "合计", "总计"):
        return False
    if non_empty <= 1 and contains_any(row_text, "说明：", "注意事项"):
        return False
    if contains_any(row_text, "主图视频", "视频方案", "视频镜头") and not contains_any(
        row_text, "视频封面", "封面图片", "图片模块"
    ):
        return False
    if has_header:
        if non_empty <= 0:
            return False
        if non_empty == 1 and contains_any(row_text, "高级A+", "高级A＋", "A+", "A＋", "主图方案"):
            return False
        return non_empty >= 2 or contains_any(
            row_text, "主图", "首图", "尺寸图", "场景", "功能卖点", "对比图", "使用图", "细节", "场景补充"
        )
    return contains_any(
        row_text, "主图", "a+", "A+", "模块", "图片", "需求", "文案", "场景", "白底", "尺寸", "细节", "对比", "产品"
    ) or (non_empty >= 2 and len(row_text) > 20)
