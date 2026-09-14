# -*- coding: utf-8 -*-
"""excel_importer 单元测试：合成 xlsx 的解析 / 行组织 / 图片抽取。

C# ImagePlanDesignerP0 OpenXML package 路径语义（Program.cs 行号为权威）：
- ReadXlsxSheetRows:1022 — 每个 <row> 元素一行，列索引取自 r 引用（不规则宽度）
- ReadRowHeights:1104 — ht(points)×12700 EMU
- ResolveAnchorRowNumber:1392 — 锚点中心 Y 归属行（0-based+1）
- 锚点 seq 按锚点序递增；输出 maps/{sheet}_R{row}C{col+1}_{seq}.{ext}
- cellimages.xml（WPS DISPIMG）按 cNvPr name 注册 ID_ 查找表
- worksheet rels：所有 target 含 "drawing" 的 rel 都处理（无 break）
"""

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
    explicit_empty_rows=False,
    row_heights=None,
) -> bytes:
    """构造最小合法 xlsx（内存字节）。

    sheets: {sheet_name: [[cell, cell, ...], ...]}（None 值为空单元格）
    explicit_empty_rows: 空行也输出 <row r="N"/> 元素（C# 语义下保留一行）
    row_heights: {0-based 行索引: points}
    """
    sheets = sheets or {}
    names = list(sheets.keys()) or ["Sheet1"]

    parts = {}
    sheet_xml = "".join(
        f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>' for i, n in enumerate(names)
    )
    parts["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheet_xml}</sheets></workbook>'
    ).encode("utf-8")
    rels = "".join(
        f'<Relationship Id="rId{i+1}" '
        f'Type="{NS_PKG_REL}worksheet" Target="worksheets/sheet{i+1}.xml"/>'
        for i, _ in enumerate(names)
    )
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG_REL}">{rels}</Relationships>'
    ).encode("utf-8")

    for i, (name, rows) in enumerate(sheets.items()):
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
            ht_attr = ""
            if row_heights and (r_idx - 1) in row_heights:
                ht_attr = f' ht="{row_heights[r_idx - 1]}"'
            if cell_parts:
                row_xml_parts.append(f'<row r="{r_idx}"{ht_attr}>{"".join(cell_parts)}</row>')
            elif explicit_empty_rows:
                row_xml_parts.append(f'<row r="{r_idx}"{ht_attr}/>')
        parts[f"xl/worksheets/sheet{i+1}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{NS_MAIN}"><sheetData>{"".join(row_xml_parts)}</sheetData></worksheet>'
        ).encode("utf-8")

    if shared_strings is not None:
        si_xml = "".join(f"<si><t>{s}</t></si>" for s in shared_strings)
        parts["xl/sharedStrings.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{NS_MAIN}" count="{len(shared_strings)}" '
            f'uniqueCount="{len(shared_strings)}">{si_xml}</sst>'
        ).encode("utf-8")

    if with_image:
        parts["xl/worksheets/_rels/sheet1.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PKG_REL}">'
            f'<Relationship Id="rId1" Type="{NS_PKG_REL}drawing" Target="../drawings/drawing1.xml"/>'
            "</Relationships>"
        ).encode("utf-8")
        parts["xl/drawings/drawing1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<xdr:wsDr xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" '
            f'xmlns:r="{NS_REL}">'
            "<xdr:twoCellAnchor>"
            "<xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
            "<xdr:to><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>3</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
            '<xdr:pic><xdr:blipFill><a:blip r:embed="rId10"/></xdr:blipFill>'
            "<xdr:spPr/></xdr:pic>"
            "</xdr:twoCellAnchor>"
            "</xdr:wsDr>"
        ).encode("utf-8")
        parts["xl/drawings/_rels/drawing1.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PKG_REL}">'
            f'<Relationship Id="rId10" Type="{NS_PKG_REL}image" Target="../media/image1.png"/>'
            "</Relationships>"
        ).encode("utf-8")
        parts["xl/media/image1.png"] = b"\x89PNG-fake-image-bytes"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in parts.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _repack(data: bytes, extra_parts: dict) -> bytes:
    """重新打包 xlsx 字节并追加/覆盖 part。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data), "r") as zin:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.namelist():
                if item not in extra_parts:
                    zout.writestr(item, zin.read(item))
            for path, content in extra_parts.items():
                zout.writestr(path, content)
    return buf.getvalue()


class TestXlsxParser(unittest.TestCase):
    def test_shared_strings_and_numbers(self):
        shared = []
        data = make_xlsx_bytes(
            sheets={"S1": [["名称", "要求"], ["1、主图1", 42]]}, shared_strings=shared
        )
        rows = XlsxParser(data).parse()["sheets"]["S1"].rows()
        self.assertEqual(rows[0], ["名称", "要求"])
        self.assertEqual(rows[1][0], "1、主图1")
        self.assertEqual(rows[1][1], "42")

    def test_ragged_rows_keep_cell_positions(self):
        # C# ReadXlsxSheetRows：行长 = 最大列索引+1（尾部无单元格不填充）
        data = make_xlsx_bytes(sheets={"S1": [["A1", None], [None, "C3"]]})
        rows = XlsxParser(data).parse()["sheets"]["S1"].rows()
        self.assertEqual(rows[0], ["A1"])
        self.assertEqual(rows[1], ["", "C3"])

    def test_empty_row_element_preserved(self):
        # <row r="2"/> 元素存在 → 保留一个（空）行（C# 语义：每元素一行）
        data = make_xlsx_bytes(
            sheets={"S1": [["A", "B"], [None, None], ["C", "D"]]},
            explicit_empty_rows=True,
        )
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertEqual(sheet.rows(), [["A", "B"], [], ["C", "D"]])
        self.assertEqual(sheet.row_numbers, [1, 2, 3])

    def test_row_element_omitted_row_dropped(self):
        # 无 <row> 元素 → 无行（与 rows() 旧行为兼容的最小集）
        data = make_xlsx_bytes(sheets={"S1": [["A", "B"], [None, None], ["C", "D"]]})
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertEqual(len(sheet.rows()), 2)
        self.assertEqual(sheet.row_numbers, [1, 3])

    def test_multiple_sheets(self):
        data = make_xlsx_bytes(sheets={"甲": [["x"]], "乙": [["y"], ["z"]]})
        result = XlsxParser(data).parse()
        self.assertEqual(set(result["sheets"].keys()), {"甲", "乙"})
        self.assertEqual(result["sheets"]["乙"].rows(), [["y"], ["z"]])

    def test_row_heights_emu(self):
        # C# ReadRowHeights:1104 — ht(points) × 12700 EMU
        data = make_xlsx_bytes(
            sheets={"S1": [["a"], ["b"], ["c"]]}, row_heights={0: 17.0, 2: 25.5}
        )
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertEqual(sheet.row_heights, {0: 215900, 2: 323850})

    def test_image_extraction_and_anchor(self):
        data = make_xlsx_bytes(sheets={"S1": [["a"], ["b"], ["c"], ["d"]]}, with_image=True)
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertIn("rId10", sheet.images)
        self.assertEqual(sheet.images["rId10"], b"\x89PNG-fake-image-bytes")
        # from row=2(0-based) rowOff=0, to row=3 rowOff=0，默认行高 190500：
        # from_y=381000, to_y=571500, center=476250 → row 2(0-based) → 1-based 3
        self.assertEqual(
            sheet.image_anchors,
            [
                {
                    "row": 3,
                    "col": 1,
                    "seq": 1,
                    "rid": "rId10",
                    "path": "maps/S1_R3C2_1.png",
                }
            ],
        )

    def test_anchor_center_row_with_custom_heights(self):
        # 第 3 行（0-based 2）行高很大 → 中心 Y 归属第 3 行
        data = make_xlsx_bytes(
            sheets={"S1": [["a"], ["b"], ["c"], ["d"]]},
            with_image=True,
            row_heights={2: 200.0},
        )
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        anchor = sheet.image_anchors[0]
        # from_y=381000, to_y=381000+2540000=2921000, center=1651000 < 381000+2540000 → row 2 (0-based)
        self.assertEqual(anchor["row"], 3)

    def test_anchor_row_above_first(self):
        # from row < 0 或无锚点信息 → 回退 row 1
        data = make_xlsx_bytes(sheets={"S1": [["a"]]})
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        row = XlsxParser._resolve_anchor_row_number(-1, 0, -1, 0, [])
        self.assertEqual(row, 1)

    def test_seq_increments_per_anchor(self):
        # 同 sheet 多个锚点 → seq 1,2,3
        data = make_xlsx_bytes(sheets={"S1": [["a"], ["b"], ["c"]]}, with_image=True)
        data = _repack(
            data,
            {
                "xl/drawings/drawing1.xml": (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<xdr:wsDr xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" '
                    f'xmlns:r="{NS_REL}">'
                    "<xdr:twoCellAnchor>"
                    "<xdr:from><xdr:col>0</xdr:col><xdr:row>0</xdr:row>"
                    "<xdr:rowOff>0</xdr:rowOff></xdr:from>"
                    "<xdr:to><xdr:col>1</xdr:col><xdr:row>1</xdr:row>"
                    "<xdr:rowOff>0</xdr:rowOff></xdr:to>"
                    '<xdr:pic><xdr:blipFill><a:blip r:embed="rId10"/></xdr:blipFill>'
                    "<xdr:spPr/></xdr:pic></xdr:twoCellAnchor>"
                    "<xdr:twoCellAnchor>"
                    "<xdr:from><xdr:col>0</xdr:col><xdr:row>1</xdr:row>"
                    "<xdr:rowOff>0</xdr:rowOff></xdr:from>"
                    "<xdr:to><xdr:col>1</xdr:col><xdr:row>2</xdr:row>"
                    "<xdr:rowOff>0</xdr:rowOff></xdr:to>"
                    '<xdr:pic><xdr:blipFill><a:blip r:embed="rId10"/></xdr:blipFill>'
                    "<xdr:spPr/></xdr:pic></xdr:twoCellAnchor>"
                    "</xdr:wsDr>"
                ).encode("utf-8"),
            },
        )
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        self.assertEqual([a["seq"] for a in sheet.image_anchors], [1, 2])

    def test_vml_and_drawing_rels_both_processed(self):
        # sheet9 形态：vmlDrawing + drawing 两个 rel 都含 "drawing" → 都处理
        data = make_xlsx_bytes(sheets={"S1": [["a"], ["b"], ["c"]]}, with_image=True)
        data = _repack(
            data,
            {
                "xl/worksheets/_rels/sheet1.xml.rels": (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<Relationships xmlns="{NS_PKG_REL}">'
                    f'<Relationship Id="rId1" Type="{NS_PKG_REL}vmlDrawing" Target="../drawings/vmlDrawing1.vml"/>'
                    f'<Relationship Id="rId2" Type="{NS_PKG_REL}drawing" Target="../drawings/drawing1.xml"/>'
                    "</Relationships>"
                ).encode("utf-8"),
                "xl/drawings/vmlDrawing1.vml": (
                    b"<?xml version='1.0'?><v:shapelayout></v:shapelayout>"
                ),
            },
        )
        sheet = XlsxParser(data).parse()["sheets"]["S1"]
        # vml 解析不出 xdr 锚点（0 张），真正的 drawing 正常出 1 锚点
        self.assertEqual(len(sheet.image_anchors), 1)

    def test_missing_shared_strings_ok(self):
        data = make_xlsx_bytes(sheets={"S1": [[1, 2]]})
        rows = XlsxParser(data).parse()["sheets"]["S1"].rows()
        self.assertEqual(rows[0], ["1", "2"])

    def test_formula_fallback_when_no_value(self):
        # C# ReadXlsxCell — <v> 缺失时返回 <f> 公式文本
        data = make_xlsx_bytes(sheets={"S1": [["a"]]})
        data = _repack(
            data,
            {
                "xl/worksheets/sheet1.xml": (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<worksheet xmlns="{NS_MAIN}"><sheetData>'
                    '<row r="1"><c r="A1" t="str"><f>_xlfn.DISPIMG("ID_1",1)</f></c></row>'
                    "</sheetData></worksheet>"
                ).encode("utf-8"),
            },
        )
        rows = XlsxParser(data).parse()["sheets"]["S1"].rows()
        self.assertEqual(rows[0][0], '=_xlfn.DISPIMG("ID_1",1)')

    def test_invalid_bytes_raise(self):
        with self.assertRaises(Exception):
            XlsxParser(b"not a zip file").parse()


class TestCellImages(unittest.TestCase):
    """WPS xl/cellimages.xml 解析（C# ExtractCellImages:1232 等价）。"""

    def _make(self, with_cellimages=True, null_rel=False):
        base = make_xlsx_bytes(sheets={"S1": [["名称"], ["1、主图1", "参考图ID_777"]]})
        parts = {}
        if with_cellimages:
            parts["xl/cellimages.xml"] = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<etc:cellImages xmlns:etc="http://www.wps.cn/officeDocument/2020/etCustomData" '
                f'xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" xmlns:r="{NS_REL}">'
                "<etc:cellImage>"
                '<xdr:cNvPr id="1" name="ID_777"/>'
                '<xdr:pic><xdr:blipFill><a:blip r:embed="rId1"/></xdr:blipFill>'
                "<xdr:spPr/></xdr:pic>"
                "</etc:cellImage>"
                "</etc:cellImages>"
            ).encode("utf-8")
            target = "media/missing.png" if null_rel else "media/image1.png"
            parts["xl/_rels/cellimages.xml.rels"] = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{NS_PKG_REL}">'
                f'<Relationship Id="rId1" Type="{NS_PKG_REL}image" Target="{target}"/>'
                "</Relationships>"
            ).encode("utf-8")
            if not null_rel:
                parts["xl/media/image1.png"] = b"\x89PNG-cellimage-bytes"
        return _repack(base, parts)

    def test_cellimages_parsed_by_name(self):
        result = XlsxParser(self._make()).parse()
        entry = result["cell_images"]["ID_777"]
        self.assertEqual(entry["data"], b"\x89PNG-cellimage-bytes")
        self.assertTrue(entry["path"].startswith("maps/ID_777"))

    def test_no_cellimages_gives_empty_dict(self):
        result = XlsxParser(make_xlsx_bytes(sheets={"S1": [["a"]]})).parse()
        self.assertEqual(result["cell_images"], {})

    def test_cellimages_broken_rels_yields_empty(self):
        result = XlsxParser(self._make(null_rel=True)).parse()
        self.assertEqual(result["cell_images"], {})

    def test_cellimages_null_target_skipped(self):
        data = self._make()
        data = _repack(
            data,
            {
                "xl/_rels/cellimages.xml.rels": (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<Relationships xmlns="{NS_PKG_REL}">'
                    f'<Relationship Id="rId1" Type="{NS_PKG_REL}image" Target="NULL"/>'
                    "</Relationships>"
                ).encode("utf-8"),
            },
        )
        result = XlsxParser(data).parse()
        self.assertEqual(result["cell_images"], {})


if __name__ == "__main__":
    unittest.main()
