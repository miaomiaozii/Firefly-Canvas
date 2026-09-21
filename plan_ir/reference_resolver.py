# -*- coding: utf-8 -*-
"""参考图解析模块。

提供：
- is_image_file_path: 判断是否为图片文件路径
- next_header_column: 获取下一个表头列索引
- resolve_anchored_reference_files: 解析锚定参考图
- resolve_reference_files_text: 解析文本引用
- resolve_reference_files: 解析参考图（锚定 + 文本）
- looks_like_machine_image_reference: 判断是否为机读图片引用
- join_reference_text_notes: 合并参考列文本到需求
- add_operation_references_from_mapped_cells: 从映射列添加参考图
"""
import os
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

from plan_ir.text_utils import compact_line, distinct, sanitize_prompt_field, join_row

# ─── 参考图解析（C# :1812/:1833/:1843/:1853/:1865/:2298/:2319）────────────────

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_RE_ID_TOKEN = re.compile(r"ID_[A-Za-z0-9]+", re.IGNORECASE)
_RE_DISPIMG = re.compile(r'=?DISPIMG\("ID_[^"]+"\s*,\s*\d+\)', re.IGNORECASE)
_RE_DISPIMG_XLFN = re.compile(r'=?_xlfn\.DISPIMG\("ID_[^"]+"\s*,\s*\d+\)', re.IGNORECASE)
_RE_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def is_image_file_path(file_path: str) -> bool:
    """C# IsImageFilePath:1865。"""
    if not file_path or not file_path.strip():
        return False
    ext = os.path.splitext(file_path.strip())[1].lower()
    return ext in _IMAGE_EXTS


def next_header_column(headers: List[str], column_index: int) -> int:
    """C# NextHeaderColumn:1833。"""
    if not headers:
        return column_index + 1
    for i in range(column_index + 1, len(headers)):
        if headers[i] and headers[i].strip():
            return i
    return column_index + 8


def resolve_anchored_reference_files(
    sheet_name: str,
    row_number: int,
    column_index: int,
    image_lookup: Dict[str, Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """C# ResolveAnchoredReferenceFiles:1843 — CELL:{sheet}!{row}:{col}# 前缀匹配（大小写不敏感）。"""
    if not image_lookup:
        return
    prefix = f"CELL:{sheet_name or ''}!{row_number}:{column_index}#"
    for key in sorted(image_lookup, key=lambda k: k.lower()):
        if key.lower().startswith(prefix.lower()):
            yield image_lookup[key]


def resolve_reference_files_text(
    cell_text: str,
    image_lookup: Dict[str, Dict[str, Any]],
) -> Iterator[Any]:
    """C# ResolveReferenceFiles:1853 — ID_ token（大小写不敏感）+ 图片路径（须存在）。"""
    text = cell_text or ""
    if image_lookup:
        # C# result 字典为 OrdinalIgnoreCase → 先归一化键再查
        low_lookup = {k.lower(): v for k, v in image_lookup.items()}
        for m in re.findall(r"ID_[A-Za-z0-9]+", text):
            value = low_lookup.get(m.lower())
            if value is not None:
                yield value
    parts = re.split(r"[\n;；]", text.replace("\r\n", "\n").replace("\r", "\n"))
    for part in parts:
        value = part.strip().strip('"')
        if value and is_image_file_path(value) and os.path.exists(value):
            yield value


def resolve_reference_files(
    cell_text: str,
    image_lookup: Optional[Dict[str, Dict[str, Any]]],
    sheet_name: str,
    row_number: int,
    column_index: int,
    headers: List[str],
) -> Iterator[Any]:
    """C# ResolveReferenceFiles:1812 — operationRef 列范围内锚定图 + 文本引用。"""
    if image_lookup is not None:
        end = next_header_column(headers, column_index)
        if end <= column_index:
            end = column_index + 1
        end = min(end, column_index + 8)
        for column in range(column_index, end):
            yield from resolve_anchored_reference_files(sheet_name, row_number, column, image_lookup)
    yield from resolve_reference_files_text(cell_text, image_lookup or {})


def looks_like_machine_image_reference(text: str) -> bool:
    """C# LooksLikeMachineImageReference:2319。"""
    compact = compact_line(text)
    if not compact or not compact.strip():
        return False
    if _RE_ID_TOKEN.search(compact):
        return True
    if _RE_DISPIMG.search(compact) or _RE_DISPIMG_XLFN.search(compact):
        return True
    if _RE_URL.search(compact):
        return True
    return False


def join_reference_text_notes(
    row: List[str],
    headers: List[str],
    col_map: Dict[str, List[int]],
    image_lookup: Dict[str, Dict[str, Any]],
) -> str:
    """C# JoinReferenceTextNotes:2298 — 参考列文本（非机读引用）并入需求。"""
    parts: List[str] = []
    for i in distinct(col_map.get("operationRef", []) if col_map else []):
        if i >= len(row):
            continue
        value = row[i]
        if not value or not value.strip():
            continue
        if looks_like_machine_image_reference(value):
            continue
        cleaned = sanitize_prompt_field(value)
        if not cleaned or not cleaned.strip():
            continue
        # C# 调 text-only 重载（ResolveReferenceFiles:1853），非锚定全参重载
        if any(v is not None for v in resolve_reference_files_text(cleaned, image_lookup or {})):
            continue
        source = compact_line(headers[i]) if i < len(headers) and headers[i] and headers[i].strip() else "参考"
        parts.append(f"{source}：{cleaned}")
    return "\r\n".join(distinct(parts))


def add_operation_references_from_mapped_cells(
    row: List[str],
    headers: List[str],
    col_map: Dict[str, List[int]],
    image_lookup: Dict[str, Dict[str, Any]],
    sheet_name: str,
    row_number: int,
) -> List[Tuple[Any, str]]:
    """C# AddOperationReferencesFromMappedCells:1785。

    返回 [(value, sourceColumn), ...]；value 为 lookup 值 dict 或原始路径字符串；
    已按 扩展名/存在性/路径去重 过滤。
    """
    if not col_map or "operationRef" not in col_map:
        return []
    result: List[Tuple[Any, str]] = []
    seen: set = set()
    for i in distinct(col_map["operationRef"]):
        if i < 0:
            continue
        source = compact_line(headers[i]) if i < len(headers) and headers[i] and headers[i].strip() else "Excel参考图"
        cell_text = row[i] if i < len(row) else ""
        for value in resolve_reference_files(cell_text, image_lookup, sheet_name, row_number, i, headers):
            path = value.get("path") if isinstance(value, dict) else value
            if not path:
                continue
            if not is_image_file_path(path):
                continue
            if isinstance(value, dict) and not os.path.exists(path):
                # lookup 命中的图在内存中（bytes 已随 parse_result），不要求磁盘存在
                if not value.get("data"):
                    continue
            elif not os.path.exists(path):
                continue
            if path.lower() in seen:
                continue
            seen.add(path.lower())
            result.append((value, source))
    return result
