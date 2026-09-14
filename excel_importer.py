# -*- coding: utf-8 -*-
"""Excel 导入器（OpenXML 解析 .xlsx，无 COM 依赖）。

对齐历史 C# 工具 ImagePlanDesignerP0 的 OpenXML package 路径（行号为权威规格，
Program.cs）：ImportXlsxPackage:1140 / ReadXlsxSheetRows:1497 / ReadXlsxCell:1527 /
ColumnIndexFromCellRef:1542 / ExtractCellImages:1232 / ExtractAnchoredDrawingImages:1281 /
ReadRowHeights:1357 / ResolveAnchorRowNumber:1365 / CellImageKey:1456。

职责：
1. 解析 xlsx 文件（shared strings / sheet 关系 / 表格图片抽取）
2. 提取单元格数据（行/列/值）
3. 提供基础 API 供 Plan IR 构建器使用

关键语义（与 C# 对齐）：
- raw_rows / rows()：每个 <row> 元素一条（文档顺序），保留空行，参差宽度
  （补齐到该行自身最大列）。C# 用 <row> 元素位置做行索引（忽略 r 属性），
  行索引 +1 即 SourceRow。
- row_heights：drawing 锚点中心 Y 行归属用（0-based 行号 → EMU，ht*12700，
  缺省 190500 EMU = 15pt 标准行高）。
- image_anchors：{row（中心 Y 解析后的 1-based 行号）, col（from.col，0-based）,
  seq（drawing 内 blip 序号）, rid, path（模拟 C# maps/ 落盘文件名）}。
- cell_images：{name: {"data": bytes, "path": "maps/<name><ext>"}}（WPS
  xl/cellimages.xml，C# CellImageLookup；普通 Excel 无此 part → 空 dict）。

技术栈：
- Python zipfile 读 xlsx（zip 格式）
- xml.etree.ElementTree 解析 OpenXML（spreadsheetml 命名空间）
- 无外部依赖（不引入 openpyxl）
"""

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"

# C# RowHeightEmu:1392 默认值：标准行高 15pt = 190500 EMU
DEFAULT_ROW_HEIGHT_EMU = 190500

_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def safe_import_filename(name: str) -> str:
    """C# SafeImportFileName — 非法字符 → _，空白序列 → _，空 → untitled。"""
    value = name or ""
    cleaned = "".join("_" if ch in _INVALID_FILENAME_CHARS else ch for ch in value).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "untitled"


@dataclass
class CellData:
    """单元格数据（扁平列表，兼容/调试用）。"""
    row: int  # 1-based（<row> 的 r 属性，缺失时按顺序推断）
    col: int  # 1-based
    value: str
    type: str = "text"  # text / number / formula / image


@dataclass
class SheetData:
    """工作表数据。"""
    name: str
    # 每个 <row> 元素一行（文档顺序、保留空行、参差宽）— C# ReadXlsxSheetRows:1497 语义
    raw_rows: List[List[str]] = field(default_factory=list)
    # 各 <row> 元素的 r 属性（缺失为 None）；与 raw_rows 等长
    row_numbers: List[Optional[int]] = field(default_factory=list)
    # 行高表：0-based 行号 → EMU（C# ReadRowHeights:1357，ht*12700）
    row_heights: Dict[int, int] = field(default_factory=dict)
    cells: List[CellData] = field(default_factory=list)
    images: Dict[str, bytes] = field(default_factory=dict)  # rid -> 图片字节
    # drawing 锚点：{row（中心 Y 解析 1-based）, col（from.col 0-based）, seq, rid, path}
    image_anchors: List[Dict[str, Any]] = field(default_factory=list)

    def rows(self) -> List[List[str]]:
        """返回 C# 语义行数据（每个 <row> 元素一条，保留空行，参差宽度）。"""
        return [list(r) for r in self.raw_rows]


class XlsxParser:
    """解析 xlsx 文件（OpenXML 格式）。"""

    def __init__(self, file_path: Path | str | bytes):
        self.file_path = file_path

    def parse(self) -> Dict[str, Any]:
        """解析 xlsx，返回 sheets / shared strings 结果 / 关系映射 / 单元格图片。

        返回格式：
        {
            "sheets": {sheet_name: SheetData, ...},   # workbook 顺序
            "relationships": {rid: target, ...},
            "cell_images": {name: {"data": bytes, "path": str}, ...},
        }
        """
        if isinstance(self.file_path, bytes):
            zip_bytes = self.file_path
        else:
            zip_bytes = Path(self.file_path).read_bytes()

        from io import BytesIO

        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
            shared_strings = self._parse_shared_strings(zf)
            workbook = self._parse_workbook(zf)
            relationships = self._parse_workbook_relationships(zf)
            # 单元格图片（WPS xl/cellimages.xml，C# CellImageLookup；issue #101 基准复测）
            cell_images = self._parse_cell_images(zf)

            result: Dict[str, Any] = {
                "sheets": {},
                "relationships": relationships,
                "cell_images": cell_images,
            }

            # 解析每个 sheet
            for sheet in workbook:
                sheet_data = self._parse_sheet(zf, sheet, relationships, shared_strings)
                if sheet_data:
                    result["sheets"][sheet["name"]] = sheet_data

        return result

    # ─── 单元格图片（C# ExtractCellImages:1232 前半）────────────────────────

    def _parse_cell_images(self, zf: zipfile.ZipFile) -> Dict[str, Dict[str, Any]]:
        """解析 WPS 的 xl/cellimages.xml（C# ExtractCellImages:1232-1280）。

        WPS 把「插入到单元格」的图片定义写在 xl/cellimages.xml（cNvPr name 如
        ID_2605，blip 经 xl/_rels/cellimages.xml.rels 指向 xl/media）。返回
        {name: {"data": bytes, "path": "maps/<name><ext>"}}；path 模拟 C# 的
        maps/ 落盘文件名（扩展名取 rels target，缺省 .png）。普通 Excel 文件
        没有此 part，返回空 dict。
        """
        try:
            ci_content = zf.read("xl/cellimages.xml")
            rels_content = zf.read("xl/_rels/cellimages.xml.rels")
        except KeyError:
            return {}

        # rId → 图片 part 路径（cellimages.xml 在 xl/ 下，相对 target 以 xl/ 为基准）
        path_by_rid: Dict[str, str] = {}
        try:
            rels_content = zf.read("xl/_rels/cellimages.xml.rels")
            rels_root = ET.fromstring(rels_content)
        except (KeyError, ET.ParseError):
            return {}
        for rel in rels_root.findall(f".//{{{NS_PKG_REL}}}Relationship"):
            rid = rel.get("Id")
            target = rel.get("Target", "")
            if not rid or not target or target == "NULL" or target.upper() == "NULL":
                continue
            if target.startswith("/"):
                part_uri = target
            else:
                part_uri = "/xl/" + target.lstrip("/")
            ext = Path(part_uri).suffix.lower()
            path_by_rid[rid] = {
                "zip_path": part_uri.lstrip("/"),
                "ext": ext or ".png",
            }

        def _local(tag) -> str:
            return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

        images: Dict[str, Dict[str, Any]] = {}
        try:
            ci_root = ET.fromstring(ci_content)
        except ET.ParseError:
            return images
        for cell in ci_root.iter():
            if _local(cell.tag) != "cellImage":
                continue
            name: Optional[str] = None
            embed: Optional[str] = None
            for sub in cell.iter():
                t = _local(sub.tag)
                if t == "cNvPr" and name is None:
                    name = sub.get("name")
                elif t == "blip" and embed is None:
                    embed = sub.get(f"{{{NS_REL}}}embed")
            if not name or not embed or embed not in path_by_rid:
                continue
            info = path_by_rid[embed]
            try:
                data = zf.read(info["zip_path"])
            except KeyError:
                continue
            images[name] = {"data": data, "path": f"maps/{safe_import_filename(name)}{info['ext']}"}
        return images

    # ─── shared strings / workbook / rels ───────────────────────────────────

    def _parse_shared_strings(self, zf: zipfile.ZipFile) -> List[str]:
        """解析 sharedStrings.xml，返回字符串列表（C# ReadSharedString:1487 等价）。"""
        try:
            content = zf.read("xl/sharedStrings.xml")
        except KeyError:
            return []

        root = ET.fromstring(content)
        strings = []
        for si in root.findall(f".//{{{NS_MAIN}}}si"):
            text = "".join(node.text or "" for node in si.iter() if node.tag.endswith("}t"))
            strings.append(text)
        return strings

    def _parse_workbook(self, zf: zipfile.ZipFile) -> List[Dict[str, str]]:
        """解析 workbook.xml，返回 sheet 列表（id, name），保持文档顺序。"""
        try:
            content = zf.read("xl/workbook.xml")
        except KeyError:
            return []

        root = ET.fromstring(content)
        sheets = []
        for sheet in root.iter(f"{{{NS_MAIN}}}sheet"):
            rid = sheet.get(f"{{{NS_REL}}}id") or sheet.get("r:id") or sheet.get("rId")
            if rid:
                sheets.append({
                    "id": rid,
                    "name": sheet.get("name", ""),
                })
        return sheets

    def _parse_workbook_relationships(self, zf: zipfile.ZipFile) -> Dict[str, str]:
        """解析 workbook.xml.rels，返回 rid -> target 映射。"""
        try:
            content = zf.read("xl/_rels/workbook.xml.rels")
        except KeyError:
            return {}

        root = ET.fromstring(content)
        relationships = {}
        for rel in root.findall(f".//{{{NS_PKG_REL}}}Relationship"):
            relationships[rel.get("Id")] = (rel.get("Target", "").replace("\\", "/"))
        return relationships

    # ─── 工作表解析（C# ReadXlsxSheetRows:1497 语义）────────────────────────

    def _parse_sheet(
        self,
        zf: zipfile.ZipFile,
        sheet_info: Dict[str, str],
        relationships: Dict[str, str],
        shared_strings: List[str],
    ) -> Optional[SheetData]:
        """解析单个工作表（每个 <row> 元素一行；行高表；drawing 锚点）。"""
        sheet_rid = sheet_info["id"]
        sheet_name = sheet_info["name"]

        target = relationships.get(sheet_rid)
        if not target:
            return None

        # 转换为绝对路径
        if target.startswith("/xl/"):
            path = target[1:]
        elif target.startswith("xl/"):
            path = target
        else:
            path = f"xl/{target}"

        try:
            content = zf.read(path)
        except KeyError:
            return None

        root = ET.fromstring(content)

        sheet_data = SheetData(name=sheet_name)

        sheet_data_elem = root.find(f"{{{NS_MAIN}}}sheetData")
        if sheet_data_elem is not None:
            prev_row_num = 0
            for row_elem in sheet_data_elem.findall(f"{{{NS_MAIN}}}row"):
                r_attr = row_elem.get("r")
                has_r = bool(r_attr and r_attr.isdigit())
                row_num = int(r_attr) if has_r else prev_row_num + 1
                prev_row_num = row_num
                sheet_data.row_numbers.append(row_num if has_r else None)

                # C# ReadRowHeights:1357 — ht（点）* 12700 → EMU，键 = rowNumber-1
                ht_attr = row_elem.get("ht")
                if has_r and ht_attr:
                    try:
                        sheet_data.row_heights[int(r_attr) - 1] = int(round(float(ht_attr) * 12700))
                    except ValueError:
                        pass

                # 每个 <c> 元素：列号取 r 属性列字母（C# ColumnIndexFromCellRef:1542），
                # 缺失时按当前行 maxIndex+1 顺序补位；值为 C# ReadXlsxCell:1527 语义。
                cells_map: Dict[int, str] = {}
                max_index = -1
                for cell in row_elem.findall(f"{{{NS_MAIN}}}c"):
                    idx = self._column_index_from_cell_ref(cell.get("r", ""))
                    if idx < 0:
                        idx = max_index + 1
                    max_index = max(max_index, idx)
                    cells_map[idx] = self._read_cell_value(cell, shared_strings)

                row_arr = [""] * (max_index + 1)
                for idx, value in cells_map.items():
                    row_arr[idx] = value
                sheet_data.raw_rows.append(row_arr)

                for idx, value in cells_map.items():
                    sheet_data.cells.append(
                        CellData(row=row_num, col=idx + 1, value=value, type="text")
                    )

        # 解析图片（如果有）— 需要 row_heights 做中心 Y 行归属
        self._parse_sheet_images(zf, path, sheet_data)

        return sheet_data

    def _column_index_from_cell_ref(self, cell_ref: str) -> int:
        """C# ColumnIndexFromCellRef:1542 — 列字母 → 0-based 索引；无列字母返回 -1。"""
        if not cell_ref or not cell_ref.strip():
            return -1
        index = 0
        seen = False
        for ch in cell_ref:
            if not ch.isalpha():
                break
            seen = True
            index = index * 26 + (ord(ch.upper()) - ord("A") + 1)
        return index - 1 if seen else -1

    def _read_cell_value(self, cell: ET.Element, shared_strings: List[str]) -> str:
        """C# ReadXlsxCell:1527 — shared string / inlineStr / v / 公式回退，统一 trim。

        - t="s"：sharedStrings 索引
        - t="inlineStr"：拼接所有 <t> 文本
        - v 为空且有 <f>：返回 "=" + 公式文本（C# 保留 DISPIMG 等公式供 ID_ 匹配）
        """
        t = (cell.get("t") or "").lower()
        if t == "inlinestr":
            parts = [
                node.text or ""
                for node in cell.iter()
                if isinstance(node.tag, str) and node.tag.endswith("}t")
            ]
            return "".join(parts).strip()
        v_elem = cell.find(f"{{{NS_MAIN}}}v")
        value = (v_elem.text or "").strip() if v_elem is not None else ""
        if t == "s" and value.isdigit():
            idx = int(value)
            if idx < len(shared_strings):
                return shared_strings[idx].strip()
        if not value:
            f_elem = cell.find(f"{{{NS_MAIN}}}f")
            if f_elem is not None and (f_elem.text or "").strip():
                return "=" + (f_elem.text or "").strip()
        return value

    # ─── drawing 图片锚点（C# ExtractAnchoredDrawingImages:1281）────────────

    def _parse_sheet_images(
        self,
        zf: zipfile.ZipFile,
        sheet_path: str,
        sheet_data: SheetData,
    ) -> None:
        """解析工作表中的图片（如果有）。

        OpenXML 图片结构：
        - xl/worksheets/_rels/sheetN.xml.rels: sheet -> drawing 映射
        - xl/drawings/drawingN.xml: 定义图片位置和锚点
        - xl/drawings/_rels/drawingN.xml.rels: drawing -> image 映射
        - xl/media/imageN.png: 实际图片文件
        """
        sheet_name = sheet_path.split("/")[-1]
        sheet_dir = sheet_path.rsplit("/", 1)[0] if "/" in sheet_path else "xl"
        ws_rels_path = f"{sheet_dir}/_rels/{sheet_name}.rels"

        # C# :1305 — 处理所有 target 含 "drawing"（忽略大小写）的 rel，不 break：
        # vmlDrawing*.vml 也会命中，但解析不出 xdr 锚点（自然 0 张）；真正的 drawingN.xml 随后被处理
        try:
            ws_rels_content = zf.read(ws_rels_path)
            ws_rels_root = ET.fromstring(ws_rels_content)
            drawing_paths = [
                self._resolve_part_path(sheet_path, rel.get("Target", ""))
                for rel in ws_rels_root.findall(f"{{{NS_PKG_REL}}}Relationship")
                if (rel.get("Target") or "").lower().find("drawing") >= 0
            ]
        except (KeyError, ET.ParseError):
            drawing_paths = []

        for drawing_path in drawing_paths:
            self._parse_drawing_images(zf, drawing_path, sheet_data)

    def _parse_drawing_images(
        self,
        zf: zipfile.ZipFile,
        drawing_path: str,
        sheet_data: SheetData,
    ) -> None:
        """解析 drawing 文件（C# ExtractAnchoredDrawingImages:1281-1356）。

        - 文档顺序遍历 wsDr 直接子节点（twoCellAnchor/oneCellAnchor）
        - 行号 = 中心 Y 算法（ResolveAnchorRowNumber:1365，行高表来自 sheet ht 属性）
        - 列号 = from.col（0-based，C# CellImageKey:1456 直接使用）
        - seq = drawing 内已解析 blip 的顺序号（C# sequence 计数器）
        """
        try:
            content = zf.read(drawing_path)
            if not content:
                return
            root = ET.fromstring(content)
        except (KeyError, ET.ParseError):
            return

        sequence = 0
        # C# :1317 — 仅遍历 wsDr 直接子节点（AlternateContent 包装项 C# 不处理）
        for anchor in root:
            tag = anchor.tag.rsplit("}", 1)[-1] if isinstance(anchor.tag, str) else ""
            if tag not in ("twoCellAnchor", "oneCellAnchor"):
                continue
            from_elem = anchor.find(f"{{{NS_XDR}}}from")
            if from_elem is None:
                continue
            row = self._int_child(from_elem, "row", -1)
            col = self._int_child(from_elem, "col", -1)
            if row < 0 or col < 0:
                continue
            to_elem = anchor.find(f"{{{NS_XDR}}}to")
            row_off = self._int_child(from_elem, "rowOff", 0)
            to_row = self._int_child(to_elem, "row", row)
            to_row_off = self._int_child(to_elem, "rowOff", row_off)
            assigned_row = self._resolve_anchor_row_number(
                row, row_off, to_row, to_row_off, sheet_data.row_heights
            )
            for blip in anchor.iter(f"{{{NS_A}}}blip"):
                embed_rid = blip.get(f"{{{NS_REL}}}embed")
                if not embed_rid:
                    continue
                resolved = self._resolve_image(zf, drawing_path, embed_rid)
                if not resolved:
                    continue
                data, media_path = resolved
                sequence += 1
                ext = Path(media_path).suffix.lower() or ".png"
                sheet_data.images[embed_rid] = data
                sheet_data.image_anchors.append({
                    "row": assigned_row,
                    "col": col,  # 0-based（C# CellImageKey 直接用 from.col）
                    "seq": sequence,
                    "rid": embed_rid,
                    # 模拟 C# maps/{sheet}_R{row}C{col+1}_{seq} 落盘文件名（SafeImportFileName）
                    "path": f"maps/{safe_import_filename(f'{sheet_data.name}_R{assigned_row}C{col + 1}_{sequence}')}"
                    + ext,
                })

    @staticmethod
    def _int_child(elem: Optional[ET.Element], name: str, fallback: int) -> int:
        """C# IntChild:1463 — 读取 xdr 子元素的整数值，缺失/不可解析返回 fallback。"""
        if elem is None:
            return fallback
        child = elem.find(f"{{{NS_XDR}}}{name}")
        if child is None or not (child.text or "").strip():
            return fallback
        try:
            return int(child.text.strip())
        except ValueError:
            return fallback

    @staticmethod
    def _resolve_anchor_row_number(
        from_row: int,
        from_row_off: int,
        to_row: int,
        to_row_off: int,
        row_heights: Dict[int, int],
    ) -> int:
        """C# ResolveAnchorRowNumber:1365 — 锚点中心 Y 行归属。

        from_row/to_row 为 0-based；row_heights 键 0-based 行号 → EMU
        （ht*12700，缺省 190500）。返回 1-based sheet 行号。
        """
        if from_row < 0:
            return 1
        if to_row < from_row:
            to_row = from_row

        def row_height(z: int) -> int:
            h = row_heights.get(z, 0)
            return h if h > 0 else DEFAULT_ROW_HEIGHT_EMU

        def row_top(z: int) -> int:
            top = 0
            for r in range(z):
                top += row_height(r)
            return top

        from_y = row_top(from_row) + max(0, from_row_off)
        to_y = row_top(to_row) + max(0, to_row_off)
        if to_y < from_y:
            to_y = from_y
        center_y = from_y + ((to_y - from_y) // 2)
        max_row = max(from_row, to_row) + 3
        for r in range(max_row + 1):
            top = row_top(r)
            bottom = top + row_height(r)
            if top <= center_y < bottom:
                return r + 1
        return from_row + 1

    def _resolve_image(
        self,
        zf: zipfile.ZipFile,
        drawing_path: str,
        embed_rid: str,
    ) -> Optional[Tuple[bytes, str]]:
        """解析 drawing 图片引用，返回 (图片字节, zip 内路径)；失败返回 None。"""
        drawing_name = drawing_path.replace("\\", "/").split("/")[-1]
        rels_path = f"xl/drawings/_rels/{drawing_name}.rels"

        try:
            rels_content = zf.read(rels_path)
        except KeyError:
            return None

        root = ET.fromstring(rels_content)
        # C# drawingRels：StringComparer.OrdinalIgnoreCase
        rid_map: Dict[str, str] = {}
        for rel in root.findall(f".//{{{NS_PKG_REL}}}Relationship"):
            rid = rel.get("Id")
            if rid:
                rid_map[rid.lower()] = rel.get("Target", "")
        target = rid_map.get(embed_rid.lower())
        if not target:
            return None
        image_path = self._resolve_part_path(drawing_path, target)
        try:
            return zf.read(image_path), image_path
        except KeyError:
            return None

    @staticmethod
    def _resolve_part_path(base_part_path: str, target: str) -> str:
        """C# ResolvePackagePath — 相对 target 解析为 zip 内绝对 part 路径（处理 ../ 与 / 前缀）。"""
        target = target.replace("\\", "/")
        base_dir = base_part_path.rsplit("/", 1)[0] if "/" in base_part_path else ""
        parts = (base_dir.split("/") if base_dir else []) + target.lstrip("/").split("/")
        stack: List[str] = []
        for p in parts:
            if p in ("", "."):
                continue
            if p == "..":
                if stack:
                    stack.pop()
            else:
                stack.append(p)
        return "/".join(stack)
