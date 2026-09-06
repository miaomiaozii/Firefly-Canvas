# -*- coding: utf-8 -*-
"""excel_importer 单元测试：合成 xlsx 的解析 / 行组织 / 图片抽取。"""

import io
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from excel_importer import XlsxParser

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx_bytes(
    sheets=None,
    shared_strings=None,
    with_image=False,
) -> bytes:
    """构造最小合法 xlsx（内存字节）。

    sheets: {sheet_name: [[cell, cell, ...], ...]}（None 值为空单元格）
    """
    sheets = sheets or {}
    names = list(sheets.keys()) or ["Sheet1"]
    empty_sheets = dict.fromkeys(names)
    for n, rows in sheets.items():
        empty_sheets[n] = rows

    parts = {}

    # workbook.xml
    sheet_xml = "".join(
        f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, n in enumerate(names)
    )
    parts["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheet_xml}</sheets></workbook>'
    ).encode("utf-8")

    # workbook rels
    rels = "".join(
        f'<Relationship Id="rId{i+1}" '
        f'Type="{NS_PKG_REL}worksheet" Target="worksheets/sheet{i+1}.xml"/>'
        for i, _ in enumerate(names)
    )
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG_REL}">{rels}</Relationships>'
    ).encode("utf-8")

    # sheets
    for i, (name, rows) in enumerate(empty_sheets.items()):
        row_xml_parts = []
        for r_idx, row in enumerate(rows or [], start=1):
            cell_parts = []
            for c_idx, val in enumerate(row):
                if val is None or val == "":
                    continue
                ref = f"{_col_letter(c_idx)}{r_idx}"
                if isinstance(val, int):
                    cell_parts.append(f'<c r="{ref}"><v>{val}</v></c>')
                else:
                    if shared_strings is not None:
                        if val not in shared_strings:
                            shared_strings.append(val)
                        s_idx = shared_strings.index(val)
                        cell_parts.append(f'<c r="{ref}" t="s"><v>{s_idx}</v></c>')
                    else:
                        cell_parts.append(
                            f'<c r="{ref}" t="inlineStr"><is><t>{val}</t></is></c>'
                        )
            if cell_parts:
                row_xml_parts.append(f'<row r="{r_idx}">{"".join(cell_parts)}</row>')
        parts[f"xl/worksheets/sheet{i+1}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{NS_MAIN}"><sheetData>{"".join(row_xml_parts)}</sheetData></worksheet>'
        ).encode("utf-8")

    # sharedStrings（若提供）
    if shared_strings is not None:
        si_xml = "".join(
            f'<si><t>{s}</t></si>' for s in shared_strings
        )
        parts["xl/sharedStrings.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{NS_MAIN}" count="{len(shared_strings)}" '
            f'uniqueCount="{len(shared_strings)}">{si_xml}</sst>'
        ).encode("utf-8")

    # 图片（drawing + media）
    if with_image:
        parts["xl/worksheets/_rels/sheet1.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PKG_REL}">'
            f'<Relationship Id="rId1" Type="{NS_PKG_REL}drawing" Target="../drawings/drawing1.xml"/>'
            '</Relationships>'
        ).encode("utf-8")
        parts["xl/drawings/drawing1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<xdr:wsDr xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" '
            f'xmlns:r="{NS_REL}">'
            '<xdr:twoCellAnchor>'
            '<xdr:from><xdr:col>1</xdr:col><xdr:row>2</xdr:row></xdr:from>'
            '<xdr:to><xdr:col>2</xdr:col><xdr:row>3</xdr:row></xdr:to>'
            '<xdr:pic><xdr:blipFill><a:blip r:embed="rId10"/></xdr:blipFill>'
            '<xdr:spPr/></xdr:pic>'
            '</xdr:twoCellAnchor>'
            '</xdr:wsDr>'
        ).encode("utf-8")
        parts["xl/drawings/_rels/drawing1.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PKG_REL}">'
            f'<Relationship Id="rId10" Type="{NS_PKG_REL}image" Target="../media/image1.png"/>'
            '</Relationships>'
        ).encode("utf-8")
        parts["xl/media/image1.png"] = b"\x89PNG-fake-image-bytes"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in parts.items():
            zf.writestr(path, content)
    return buf.getvalue()


    def test_image_anchor_preserved(self):
        # issue #52：drawing 锚点行列必须保留（0-based → 1-based）
        data = make_xlsx_bytes(
            sheets={"S1": [["名称"], ["1、主图1"], ["占位行"], ["占位行2"]]},
            with_image=True,
        )
        result = XlsxParser(data).parse()
        sheet = result["sheets"]["S1"]
        self.assertIn("rId10", sheet.images)
        self.assertEqual(
            sheet.image_anchors,
            [{"rid": "rId10", "row": 3, "col": 2}],  # from col=1,row=2（0-based）
        )

    def test_grid_row_numbers_aligns_with_empty_filtering(self):
        # rows() 过滤整行空行后，grid_row_numbers 给出保留行的原始行号
        data = make_xlsx_bytes(sheets={"S1": [["名称", "要求"], [None, None], ["1、主图1", "白底"]]})
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertEqual(sheet.rows(), [["名称", "要求"], ["1、主图1", "白底"]])
        self.assertEqual(sheet.grid_row_numbers(), [1, 3])


class TestXlsxParser(unittest.TestCase):
    def test_shared_strings_and_numbers(self):
        shared = []
        data = make_xlsx_bytes(
            sheets={"S1": [["名称", "要求"], ["1、主图1", 42]]},
            shared_strings=shared,
        )
        result = XlsxParser(data).parse()
        self.assertIn("S1", result["sheets"])
        rows = result["sheets"]["S1"].rows()
        self.assertEqual(rows[0], ["名称", "要求"])
        self.assertEqual(rows[1][0], "1、主图1")
        self.assertEqual(rows[1][1], "42")

    def test_rows_row_major_and_empty_filtering(self):
        shared = []
        # 第 1 行有值，第 2 行整行空，第 3 行有值 → rows() 应过滤空行
        data = make_xlsx_bytes(
            sheets={"S1": [["A1", None], [None, None], [None, "C3"]]},
            shared_strings=shared,
        )
        rows = XlsxParser(data).parse()["sheets"]["S1"].rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ["A1", ""])
        self.assertEqual(rows[1], ["", "C3"])

    def test_multiple_sheets(self):
        shared = []
        data = make_xlsx_bytes(
            sheets={"甲": [["x"]], "乙": [["y"], ["z"]]},
            shared_strings=shared,
        )
        result = XlsxParser(data).parse()
        self.assertEqual(set(result["sheets"].keys()), {"甲", "乙"})
        self.assertEqual(result["sheets"]["乙"].rows(), [["y"], ["z"]])

    def test_image_extraction(self):
        shared = []
        data = make_xlsx_bytes(
            sheets={"S1": [["a"]]},
            shared_strings=shared,
            with_image=True,
        )
        result = XlsxParser(data).parse()
        images = result["sheets"]["S1"].images
        self.assertEqual(len(images), 1)
        self.assertEqual(next(iter(images.values())), b"\x89PNG-fake-image-bytes")

    def test_image_anchor_preserved(self):
        # issue #52：drawing 锚点行列必须保留（0-based → 1-based）
        data = make_xlsx_bytes(
            sheets={"S1": [["名称"], ["1、主图1"], ["占位行"], ["占位行2"]]},
            with_image=True,
        )
        result = XlsxParser(data).parse()
        sheet = result["sheets"]["S1"]
        self.assertIn("rId10", sheet.images)
        self.assertEqual(
            sheet.image_anchors,
            [{"rid": "rId10", "row": 3, "col": 2}],  # from col=1,row=2（0-based）
        )

    def test_grid_row_numbers_aligns_with_empty_filtering(self):
        # rows() 过滤整行空行后，grid_row_numbers 给出保留行的原始行号
        data = make_xlsx_bytes(sheets={"S1": [["名称", "要求"], [None, None], ["1、主图1", "白底"]]})
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertEqual(sheet.rows(), [["名称", "要求"], ["1、主图1", "白底"]])
        self.assertEqual(sheet.grid_row_numbers(), [1, 3])

    def test_missing_shared_strings_ok(self):
        # 无 sharedStrings.xml（只有数字/内联）不应报错
        data = make_xlsx_bytes(sheets={"S1": [[1, 2]]})
        rows = XlsxParser(data).parse()["sheets"]["S1"].rows()
        self.assertEqual(rows[0], ["1", "2"])

    def test_invalid_bytes_raise(self):
        with self.assertRaises(Exception):
            XlsxParser(b"not a zip file").parse()


if __name__ == "__main__":
    unittest.main()
