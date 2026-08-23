# -*- coding: utf-8 -*-
"""Excel 导入器（OpenXML 解析 .xlsx，无 COM 依赖）。

参考历史 C# 工具 ImagePlanDesignerP0 的能力（AddTasksFromRows 在 Program.cs:1615-1694）。

职责：
1. 解析 xlsx 文件（shared strings / sheet 关系 / 表格图片抽取）
2. 提取单元格数据（行/列/值）
3. 提供基础 API 供 Plan IR 构建器使用

技术栈：
- Python zipfile 读 xlsx（zip 格式）
- xml.etree.ElementTree 解析 OpenXML（spreadsheetml 命名空间）
- 无外部依赖（不引入 openpyxl）
"""

import zipfile
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class CellData:
    """单元格数据。"""
    row: int  # 1-based
    col: int  # 1-based
    value: str
    type: str = "text"  # text / number / formula / image


@dataclass
class SheetData:
    """工作表数据。"""
    name: str
    cells: List[CellData] = field(default_factory=list)
    images: Dict[str, bytes] = field(default_factory=dict)  # 图片引用 -> 图片字节

    def rows(self) -> List[List[str]]:
        """返回按行组织的单元格值（row-major），补齐为矩形网格。

        所有行统一填充到 max_col 宽度（缺失单元格为空字符串），
        便于下游按列索引；过滤整行为空的行。
        """
        if not self.cells:
            return []
        max_row = max(c.row for c in self.cells)
        max_col = max(c.col for c in self.cells)
        grid: List[List[str]] = [["" for _ in range(max_col)] for _ in range(max_row)]
        for c in self.cells:
            grid[c.row - 1][c.col - 1] = c.value
        return [row for row in grid if any(cell for cell in row)]


class XlsxParser:
    """解析 xlsx 文件（OpenXML 格式）。"""

    def __init__(self, file_path: Path | str | bytes):
        self.file_path = file_path

    def parse(self) -> Dict[str, Any]:
        """解析 xlsx，返回 sheets / shared_strings / 关系映射。

        返回格式：
        {
            "sheets": {sheet_name: SheetData, ...},
            "relationships": {rid: target, ...},
        }
        """
        if isinstance(self.file_path, bytes):
            zip_bytes = self.file_path
        else:
            zip_bytes = Path(self.file_path).read_bytes()

        from io import BytesIO

        result: Dict[str, Any] = {
            "sheets": {},
            "relationships": {},
        }

        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
            shared_strings = self._parse_shared_strings(zf)
            workbook = self._parse_workbook(zf)
            relationships = self._parse_workbook_relationships(zf)
            result["relationships"] = relationships

            # 解析每个 sheet
            for i, sheet in enumerate(workbook, 1):
                sheet_data = self._parse_sheet(zf, sheet, relationships, shared_strings)
                if sheet_data:
                    result["sheets"][sheet["name"]] = sheet_data

        return result

    def _parse_shared_strings(self, zf: zipfile.ZipFile) -> List[str]:
        """解析 sharedStrings.xml，返回字符串列表。"""
        try:
            content = zf.read("xl/sharedStrings.xml")
        except KeyError:
            return []

        root = ET.fromstring(content)
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        strings = []
        for si in root.findall(f".//{{{ns}}}si"):
            text = "".join(node.text or "" for node in si.iter() if node.tag.endswith("}t"))
            strings.append(text)
        return strings

    def _parse_workbook(self, zf: zipfile.ZipFile) -> List[Dict[str, str]]:
        """解析 workbook.xml，返回 sheet 列表（id, name）。"""
        try:
            content = zf.read("xl/workbook.xml")
        except KeyError:
            return []

        root = ET.fromstring(content)
        ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        sheets = []
        for sheet in root.iter(f"{{{ns_main}}}sheet"):
            # r:id 属性带命名空间
            rid = sheet.get(f"{{{ns_rel}}}id") or sheet.get("r:id") or sheet.get("rId")
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
        ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        relationships = {}
        for rel in root.findall(f".//{{{ns}}}Relationship"):
            relationships[rel.get("Id")] = rel.get("Target", "")
        return relationships

    def _parse_sheet(
        self,
        zf: zipfile.ZipFile,
        sheet_info: Dict[str, str],
        relationships: Dict[str, str],
        shared_strings: List[str],
    ) -> Optional[SheetData]:
        """解析单个工作表。"""
        sheet_rid = sheet_info["id"]
        sheet_name = sheet_info["name"]

        # 获取 sheet 文件路径
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
        ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

        sheet_data = SheetData(name=sheet_name)

        # 解析单元格（行信息在 row 元素的 r 属性上，列信息在 cell 的 r 属性上）
        sheet_data_elem = root.find(f"{{{ns_main}}}sheetData")
        if sheet_data_elem is not None:
            for row_elem in sheet_data_elem.findall(f"{{{ns_main}}}row"):
                for cell in row_elem.findall(f"{{{ns_main}}}c"):
                    cell_data = self._parse_cell(cell, shared_strings)
                    if cell_data:
                        sheet_data.cells.append(cell_data)

        # 解析图片（如果有）
        self._parse_sheet_images(zf, path, sheet_data)

        return sheet_data

    def _parse_cell(self, cell: ET.Element, shared_strings: List[str]) -> Optional[CellData]:
        """解析单元格，返回 CellData 或 None。"""
        ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        # 解析单元格引用（如 A1, BC23）
        ref = cell.get("r", "A1")
        match = re.match(r"([A-Z]+)(\d+)", ref)
        if not match:
            return None
        col = self._col_letter_to_number(match.group(1))
        row = int(match.group(2))
        cell_type = cell.get("t", "n")

        # 获取值
        value = ""
        v_elem = cell.find(f"{{{ns_main}}}v")
        if v_elem is not None:
            if cell_type == "s":  # shared string
                try:
                    idx = int(v_elem.text)
                    if idx < len(shared_strings):
                        value = shared_strings[idx]
                except (ValueError, IndexError):
                    pass
            else:  # number / formula string / inline
                value = v_elem.text or ""

        if not value and cell_type == "inlineStr":
            # 尝试获取 inline string
            is_elem = cell.find(f"{{{ns_main}}}is/{{{ns_main}}}t")
            if is_elem is not None:
                value = is_elem.text or ""

        return CellData(row=row, col=col, value=str(value), type=cell_type)

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
        # 构建 worksheet rels 文件路径
        # xl/worksheets/sheet1.xml -> xl/worksheets/_rels/sheet1.xml.rels
        sheet_name = sheet_path.split("/")[-1]  # sheet1.xml
        sheet_dir = sheet_path.rsplit("/", 1)[0] if "/" in sheet_path else "xl"
        ws_rels_path = f"{sheet_dir}/_rels/{sheet_name}.rels"

        # 解析 worksheet rels，找到 drawing 文件
        drawing_path = None
        try:
            ws_rels_content = zf.read(ws_rels_path)
            ws_rels_root = ET.fromstring(ws_rels_content)
            ns_rel = "http://schemas.openxmlformats.org/package/2006/relationships"

            for rel in ws_rels_root.findall(f"{{{ns_rel}}}Relationship"):
                target = rel.get("Target", "")
                # 检查是否是 drawing 类型
                if "drawing" in rel.get("Type", ""):
                    # 相对路径: ../drawings/drawing1.xml -> xl/drawings/drawing1.xml
                    if target.startswith("../"):
                        drawing_path = f"xl/{target[3:]}"
                    elif not target.startswith("xl/"):
                        drawing_path = f"xl/{target}"
                    else:
                        drawing_path = target
                    break
        except (KeyError, ET.ParseError):
            pass

        if not drawing_path:
            return

        # 解析 drawing 文件，提取图片
        self._parse_drawing_images(zf, drawing_path, sheet_data)

    def _parse_drawing_images(
        self,
        zf: zipfile.ZipFile,
        drawing_path: str,
        sheet_data: SheetData,
    ) -> None:
        """解析 drawing 文件，提取所有图片。"""
        try:
            content = zf.read(drawing_path)
            if not content:
                return
            root = ET.fromstring(content)
        except (KeyError, ET.ParseError):
            return

        # DrawingML 命名空间
        ns_xdr = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
        ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

        # 查找所有图片 (xdr:twoCellAnchor / xdr:oneCellAnchor 中的 a:blip)
        for anchor_tag in ["twoCellAnchor", "oneCellAnchor"]:
            anchors = root.findall(f".//{{{ns_xdr}}}{anchor_tag}")
            for anchor in anchors:
                # 查找 blip 元素 (a:blip)
                for blip in anchor.iter(f"{{{ns_a}}}blip"):
                    # 获取图片引用 (r:embed)
                    embed_rid = blip.get(f"{{{ns_r}}}embed")
                    if not embed_rid:
                        continue

                    # 解析图片 (通过 drawing rels 查找实际文件)
                    image_data = self._resolve_image(zf, drawing_path, embed_rid)
                    if image_data:
                        # 以 rId 作为 key 存储图片
                        sheet_data.images[embed_rid] = image_data

    def _resolve_image(
        self,
        zf: zipfile.ZipFile,
        drawing_path: str,
        embed_rid: str,
    ) -> Optional[bytes]:
        """解析图片引用，返回图片字节。

        Args:
            zf: ZipFile 对象
            drawing_path: drawing 文件路径 (如 xl/drawings/drawing1.xml)
            embed_rid: 图片引用 (如 rId1)

        Returns:
            图片字节，或 None
        """
        # 构建对应的 rels 文件路径
        # xl/drawings/drawing1.xml -> xl/drawings/_rels/drawing1.xml.rels
        drawing_name = drawing_path.replace("\\", "/").split("/")[-1]  # drawing1.xml
        rels_path = f"xl/drawings/_rels/{drawing_name}.rels"

        try:
            rels_content = zf.read(rels_path)
        except KeyError:
            return None

        # 解析 rels
        root = ET.fromstring(rels_content)
        ns_rel = "http://schemas.openxmlformats.org/package/2006/relationships"

        # 查找对应的 relationship
        image_path = None
        for rel in root.findall(f".//{{{ns_rel}}}Relationship"):
            if rel.get("Id") == embed_rid:
                target = rel.get("Target", "")
                # 相对路径 (如 ../media/image1.png)
                if target.startswith("../"):
                    image_path = f"xl/{target[3:]}"
                elif not target.startswith("xl/"):
                    image_path = f"xl/drawings/{target}"
                else:
                    image_path = target
                break

        if not image_path:
            return None

        # 读取图片
        try:
            return zf.read(image_path)
        except KeyError:
            return None

    def _col_letter_to_number(self, letters: str) -> int:
        """将列字母（A, B, ..., Z, AA, AB, ...）转为数字（1, 2, ..., 26, 27, 28, ...）。"""
        col = 0
        for c in letters.upper():
            col = col * 26 + (ord(c) - ord('A') + 1)
        return col

