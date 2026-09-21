# -*- coding: utf-8 -*-
"""基础文本工具模块。

提供：
- contains_any: 大小写不敏感子串匹配
- compact_line: 换行 → 空格，trim
- first_non_empty: 第一个非空白值
- join_row: 非空单元格去重后连接
- sanitize_prompt_field: 剥离 DISPIMG 公式 / ID_ token / URL / 纯链接行
- join_distinct_lines: 分块拆行、trim、去重后连接
- clean_task_name: 净化 + 紧凑化 + 70 字符截断
"""
import re
from typing import List, Optional

# ─── 基础文本工具 ─────────────────────────────────────────────────────────────


def contains_any(text: Optional[str], *needles: str) -> bool:
    """C# ContainsAny:2507 — 大小写不敏感子串匹配。"""
    if not text or not text.strip():
        return False
    low = text.lower()
    return any(n.lower() in low for n in needles)


def compact_line(text: Optional[str]) -> str:
    """C# CompactLine:2493 — 换行 → 空格，trim。"""
    return (text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()


def first_non_empty(*values: str) -> str:
    """C# FirstNonEmpty:2498 — 第一个非空白值（原样返回）。"""
    for v in values:
        if v and v.strip():
            return v
    return ""


def join_row(row: List[str]) -> str:
    """C# JoinRow:2488 — 非空单元格去重后以 \\r\\n 连接，trim。"""
    parts: List[str] = []
    seen = set()
    for c in row or []:
        if c is None:
            continue
        if c.strip() and c not in seen:
            seen.add(c)
            parts.append(c)
    return "\r\n".join(parts).strip()


# ─── 文本净化（C# SanitizePromptField:2384）───────────────────────────────────

_RE_DISPIMG_XLFN = re.compile(r'=?_xlfn\.DISPIMG\("ID_[^"]+"\s*,\s*\d+\)', re.IGNORECASE)
_RE_DISPIMG = re.compile(r'=?DISPIMG\("ID_[^"]+"\s*,\s*\d+\)', re.IGNORECASE)
_RE_ID_TOKEN = re.compile(r"ID_[A-Za-z0-9]+", re.IGNORECASE)
_RE_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_RE_LINK_LINE = re.compile(r"^(参考图片链接|参考链接|图片链接|链接)\s*[:：]?\s*$", re.IGNORECASE)
_RE_LINK_TAIL = re.compile(r"(参考图片链接|参考链接|图片链接|链接)\s*[:：]\s*$", re.IGNORECASE)


def sanitize_prompt_field(text: Optional[str]) -> str:
    """C# SanitizePromptField:2384 — 剥离 DISPIMG 公式 / ID_ token / URL / 纯链接行。"""
    if not text or not text.strip():
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _RE_DISPIMG_XLFN.sub("", cleaned)
    cleaned = _RE_DISPIMG.sub("", cleaned)
    cleaned = _RE_ID_TOKEN.sub("", cleaned)
    cleaned = _RE_URL.sub("", cleaned)
    lines: List[str] = []
    for raw in cleaned.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _RE_LINK_LINE.match(line):
            continue
        line = _RE_LINK_TAIL.sub("", line).strip()
        if line:
            lines.append(line)
    return "\r\n".join(lines).strip()


def join_distinct_lines(*blocks: str) -> str:
    """C# JoinDistinctLines:2330 — 分块拆行、trim、去重（保序）后以 \\r\\n 连接。"""
    values: List[str] = []
    for block in blocks:
        if not block or not block.strip():
            continue
        for line in block.replace("\r\n", "\n").split("\n"):
            v = line.strip()
            if v and v not in values:
                values.append(v)
    return "\r\n".join(values)


# ─── 命名与显示编号 ──────────────────────────────────────────────────────────


def clean_task_name(name: str) -> str:
    """C# CleanTaskName:1779 — 净化 + 紧凑化 + 70 字符截断。"""
    return compact_line(sanitize_prompt_field(name))[:70]


def build_display_no(section: str, index: int) -> str:
    """C# BuildDisplayNo:2430。"""
    if section and "A+" in section:
        return f"A+ 模块 {index}"
    if section and "主图" in section:
        return f"主图 {index}"
    if section and ("品牌" in section or "店铺" in section):
        return f"店铺图 {index}"
    return f"图片 {index}"


def guess_name(row: List[str], row_text: str, section: str, index: int) -> str:
    """C# GuessName:2478 — 首个非空单元格（≤60 字符）→ 行文本前 60 → 显示编号。"""
    first = next((c for c in (row or []) if c and c.strip()), "")
    if 0 < len(first) <= 60:
        return first
    prefix = build_display_no(section, index)
    if not row_text or not row_text.strip():
        return prefix
    compact = row_text.replace("\r", " ").replace("\n", " ")
    return compact[:60]
