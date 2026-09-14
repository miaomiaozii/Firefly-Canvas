# -*- coding: utf-8 -*-
"""plan_ir_builder 单元测试：C# ImagePlanDesignerP0 OpenXML 路径语义（Program.cs 行号为权威）。

覆盖：
- Sheet 过滤（ShouldSkipSheet:1576 / IsDesignPlanSheetName:1559 / IsSupplementalPlanSheetName:1565）
- 表头识别（FindHeaderRow:2074 / HeaderCellScore:2195 / MapHeaders:2218）
- 行分类（LooksLikeTaskRow:2246 / LooksLikeNoteRow:2266 / TryGetSectionMarker:1877）
- A+ 任务（LooksLikeAPlusTask:1897 / BuildDisplayNo:2430）
- 比例推断（InferAspectRatio:2404）
- 参考图解析（AddOperationReferencesFromMappedCells:1785 / ResolveAnchoredReferenceFiles:1843）
- 提示词净化（SanitizePromptField:2384）
- 主循环口径（ImportXlsxPackage:1140 — ScannedRowCount / 图片注记）
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plan_ir_builder import (
    add_operation_references_from_mapped_cells,
    build_display_no,
    build_image_lookup,
    build_plan_ir,
    clean_task_name,
    extract_tasks,
    find_header_row,
    first_non_empty,
    guess_name,
    header_cell_score,
    infer_aspect_ratio,
    infer_initial_section,
    infer_output_type,
    infer_section,
    is_design_plan_sheet_name,
    is_supplemental_plan_sheet_name,
    join_distinct_lines,
    looks_like_aplus_task,
    looks_like_note_row,
    looks_like_repeated_header_row,
    looks_like_task_row,
    map_headers,
    normalize_section_name,
    sanitize_prompt_field,
    should_skip_sheet,
    starts_with_aplus_task_label,
    try_get_section_marker,
)


class FakeSheet:
    """builder 消费的 sheet 数据（rows() + image_anchors + images）。"""

    def __init__(self, rows, anchors=None, images=None):
        self._rows = rows
        self.image_anchors = anchors or []
        self.images = images or {}

    def rows(self):
        return self._rows


def parse_result(sheets, cell_images=None):
    return {"sheets": sheets, "cell_images": cell_images or {}}


def _main_sheet_rows():
    """模拟 图片方案-高地牛 的表头与任务行（0-based 索引；无图序列，避免 C# ②图序→name 映射）。"""
    header = ["任务名称", "内容/要素", "画面需求", "图片示例", "备注"]
    rows = [["图片方案（作图需求）"]]
    rows.append(header)
    rows.append(["主图 1", "产品整体展示", "白底，突出主体", "", ""])
    rows.append(["主图 2", "场景图", "露营场景", "https://x.com/a.png", ""])
    rows.append(["备注", "注意构图", "", "", ""])
    rows.append(["主图 4", "尺寸图", "标注 30x20cm", "", ""])
    return rows


class TestSheetFiltering(unittest.TestCase):
    def test_design_sheet_names(self):
        self.assertTrue(is_design_plan_sheet_name("图片方案-高地牛"))
        self.assertTrue(is_design_plan_sheet_name("作图需求表"))
        self.assertFalse(is_design_plan_sheet_name("视频脚本"))
        self.assertFalse(is_design_plan_sheet_name("商品分类"))

    def test_supplemental_sheet_names(self):
        self.assertTrue(is_supplemental_plan_sheet_name("A+方案"))
        self.assertTrue(is_supplemental_plan_sheet_name("主图方案"))
        self.assertTrue(is_supplemental_plan_sheet_name("A+模块"))
        self.assertFalse(is_supplemental_plan_sheet_name("视频脚本-A+"))

    def test_skip_when_design_exists(self):
        # 有设计表 → 只扫设计表+补充表
        self.assertFalse(should_skip_sheet("图片方案-高地牛", True))
        self.assertFalse(should_skip_sheet("A+方案", True))
        self.assertTrue(should_skip_sheet("商品分类", True))
        self.assertTrue(should_skip_sheet("评论透视", True))

    def test_skip_without_design(self):
        self.assertTrue(should_skip_sheet("商品分类", False))
        self.assertTrue(should_skip_sheet("视频脚本", False))
        self.assertTrue(should_skip_sheet("Sheet5", False))


class TestHeaderDetection(unittest.TestCase):
    def test_header_row_detected(self):
        rows = _main_sheet_rows()
        self.assertEqual(find_header_row(rows), 1)

    def test_no_header_returns_minus_one(self):
        rows = [
            ["1、主图1 产品整体展示白底"],
            ["2、主图2 场景图露营"],
        ]
        self.assertEqual(find_header_row(rows), -1)

    def test_map_headers_priority(self):
        headers = ["图序", "任务名称", "内容/要素", "画面需求", "图片示例", "备注", "比例"]
        col_map = map_headers(headers)
        self.assertEqual(col_map["name"], [0, 1])  # 图序 → name；任务名称 → name（同键多列）
        self.assertEqual(col_map["requirements"], [2, 3, 5])
        self.assertEqual(col_map["operationRef"], [4])
        self.assertEqual(col_map["aspect"], [6])

    def test_map_headers_reference_keywords(self):
        col_map = map_headers(["参考图", "参考图片", "效果图", "参考"])
        self.assertEqual(col_map["operationRef"], [0, 1, 2, 3])

    def test_repeated_header_row(self):
        self.assertTrue(
            looks_like_repeated_header_row(["图序", "任务名称", "内容/要素", "图片示例"])
        )
        self.assertFalse(looks_like_repeated_header_row(["1", "主图 1", "产品整体展示"]))

    def test_header_cell_score(self):
        self.assertEqual(header_cell_score("任务名称"), 2)
        self.assertEqual(header_cell_score("参考图"), 2)
        self.assertEqual(header_cell_score("图序"), 1)
        self.assertEqual(header_cell_score("备注"), 1)
        # 短文本含 图 等泛词 → C# 短分支计 1 分
        self.assertEqual(header_cell_score("1、主图1 产品整体展示"), 1)
        self.assertEqual(header_cell_score("高山草甸里的篝火帐篷与雪山"), 0)


class TestRowClassification(unittest.TestCase):
    def test_task_row_with_header(self):
        row = ["1", "主图 1", "产品整体展示", "白底，突出主体", "", ""]
        self.assertTrue(looks_like_task_row(row, " ".join(row), True))

    def test_note_row_skipped(self):
        row = ["备注", "注意构图", "", "", "", ""]
        self.assertTrue(looks_like_note_row(row))
        self.assertFalse(looks_like_task_row(row, " ".join(row), True))

    def test_single_cell_section_line_skipped(self):
        row = ["主图方案", "", "", "", "", ""]
        self.assertFalse(looks_like_task_row(row, "主图方案", True))

    def test_section_marker(self):
        self.assertEqual(try_get_section_marker(["高级A+方案"], "高级A+方案"), "高级A+方案")
        self.assertEqual(try_get_section_marker(["主图方案"], "主图方案"), "主图方案")
        self.assertEqual(try_get_section_marker(["A+1 首图"], "A+1 首图"), "")
        self.assertEqual(try_get_section_marker(["任意长文本内容行", "x", "y"], "任意长文本内容行 x y"), "")


class TestAPlusTasks(unittest.TestCase):
    def test_aplus_label(self):
        self.assertTrue(starts_with_aplus_task_label("A+1 首图"))
        self.assertTrue(starts_with_aplus_task_label("A+一 横幅"))
        self.assertTrue(starts_with_aplus_task_label("A+场景 大场景"))
        self.assertFalse(starts_with_aplus_task_label("主图 1"))

    def test_aplus_task_detection(self):
        self.assertTrue(looks_like_aplus_task("A+1 首图", ""))
        self.assertTrue(looks_like_aplus_task("1、A+2 首屏", ""))
        self.assertFalse(looks_like_aplus_task("主图 1", "A+ 内容在需求里描述"))

    def test_display_no(self):
        self.assertEqual(build_display_no("主图方案", 3), "主图 3")
        self.assertEqual(build_display_no("高级A+方案", 2), "A+ 模块 2")
        self.assertEqual(build_display_no("图片方案", 5), "图片 5")


class TestAspectInference(unittest.TestCase):
    def test_ratio_tokens(self):
        self.assertEqual(infer_aspect_ratio("主图方案", "", "白底 3:4"), "3:4")
        self.assertEqual(infer_aspect_ratio("主图方案", "", "尺寸 16:9"), "16:9")
        self.assertEqual(infer_aspect_ratio("高级A+方案", "", "任意"), "21:9")  # A+ 默认
        self.assertEqual(infer_aspect_ratio("主图方案", "", "任意"), "1:1")

    def test_dimension_strings(self):
        self.assertEqual(infer_aspect_ratio("主图方案", "", "尺寸 1500x1000"), "16:9")
        self.assertEqual(infer_aspect_ratio("主图方案", "", "尺寸 1000x1500"), "3:4")
        self.assertEqual(infer_aspect_ratio("主图方案", "", "尺寸 4000x1000"), "21:9")
        self.assertEqual(infer_aspect_ratio("主图方案", "", "2000x1200"), "16:9")
        self.assertEqual(infer_aspect_ratio("主图方案", "", "30x20cm"), "1:1")  # 位数不足不匹配

    def test_3_to_4_wins_over_1_to_1(self):
        # C# 顺序：3:4 在 1:1 之前
        self.assertEqual(infer_aspect_ratio("主图方案", "", "3:4 和 1:1"), "3:4")


class TestPromptSanitization(unittest.TestCase):
    def test_dispimg_formula_removed(self):
        text = '产品白底图\n=_xlfn.DISPIMG("ID_A1B2C3D4",1)\n突出主体'
        self.assertNotIn("ID_", sanitize_prompt_field(text))
        self.assertNotIn("DISPIMG", sanitize_prompt_field(text))
        self.assertIn("产品白底图", sanitize_prompt_field(text))

    def test_url_and_link_lines_removed(self):
        text = "白底图\nhttps://example.com/a.png\n参考图片链接："
        cleaned = sanitize_prompt_field(text)
        self.assertNotIn("http", cleaned)
        self.assertNotIn("参考图片链接", cleaned)
        self.assertIn("白底图", cleaned)


class TestReferenceResolution(unittest.TestCase):
    def _sheet(self, anchors=None, images=None):
        return {"图片方案-高地牛": FakeSheet([], anchors=anchors or [], images=images or {})}

    def test_anchored_lookup_by_cell_key(self):
        # C# :1843 — CELL:sheet!row:col#seq 前缀匹配
        image_lookup = build_image_lookup(
            self._sheet(
                anchors=[
                    {"row": 3, "col": 3, "seq": 1, "rid": "rId11", "path": "maps/S_R3C4_1.png"},
                    {"row": 3, "col": 3, "seq": 2, "rid": "rId12", "path": "maps/S_R3C4_2.png"},
                ],
                images={"rId11": b"x", "rId12": b"y"},
            ),
            {},
        )
        rows = _main_sheet_rows()
        col_map = map_headers(rows[1])
        refs = add_operation_references_from_mapped_cells(
            rows[2], rows[1], col_map, image_lookup, "图片方案-高地牛", 3
        )
        paths = [value["path"] for value, _ in refs]
        self.assertEqual(paths, ["maps/S_R3C4_1.png", "maps/S_R3C4_2.png"])

    def test_no_anchor_no_ref(self):
        image_lookup = build_image_lookup(self._sheet(), {})
        rows = _main_sheet_rows()
        refs = add_operation_references_from_mapped_cells(
            rows[2], rows[1], map_headers(rows[1]), image_lookup, "图片方案-高地牛", 3
        )
        self.assertEqual(refs, [])

    def test_id_text_token_matches_cellimage(self):
        cell_images = {"ID_777": {"data": b"\x89PNG", "path": "maps/ID_777.png"}}
        image_lookup = build_image_lookup(self._sheet(), cell_images)
        header = ["图序", "任务名称", "内容/要素", "图片示例"]
        row = ["1", "主图 1", "展示", "参考图 ID_777"]
        col_map = map_headers(header)
        refs = add_operation_references_from_mapped_cells(
            row, header, col_map, image_lookup, "图片方案-高地牛", 2
        )
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0][0]["path"], "maps/ID_777.png")


class TestPlanIRBuild(unittest.TestCase):
    def test_build_plan_ir_counts(self):
        sheets = {
            "图片方案-高地牛": FakeSheet(_main_sheet_rows()),
            "商品分类": FakeSheet([["A", "B"], ["1", "2"], ["3", "4"]]),
            "评论透视": FakeSheet([["x"]] * 10),
        }
        plan = build_plan_ir("test.xlsx", parse_result(sheets))
        self.assertEqual(plan.sheet_count, 3)
        # ScannedRowCount 只数未过滤 sheet 的 <row> 元素
        self.assertEqual(plan.scanned_rows, len(_main_sheet_rows()))
        self.assertEqual(len(plan.tasks), 3)  # 主图1/2/4（备注行跳过）
        self.assertEqual([t.name for t in plan.tasks], ["主图 1", "主图 2", "主图 4"])
        self.assertTrue(any("已抽取" not in n for n in plan.notes))  # 无图 → 无图片注记

    def test_scanned_rows_counts_all_row_elements(self):
        rows = _main_sheet_rows() + [[""] * 6]  # 空行元素也算（C# UsedRange.Rows.Count 口径近似）
        sheets = {"图片方案-高地牛": FakeSheet(rows)}
        plan = build_plan_ir("test.xlsx", parse_result(sheets))
        self.assertEqual(plan.scanned_rows, len(rows))

    def test_image_note_counts_lookup(self):
        sheets = {
            "图片方案-高地牛": FakeSheet(
                _main_sheet_rows(),
                anchors=[
                    {"row": 3, "col": 4, "seq": 1, "rid": "rId1", "path": "maps/S_R3C5_1.png"},
                ],
                images={"rId1": b"x"},  # C#：媒体 part 存在才注册 lookup
            )
        }
        cell_images = {"ID_1": {"data": b"x", "path": "maps/ID_1.png"},
                       "ID_2": {"data": b"x", "path": "maps/ID_2.png"}}
        plan = build_plan_ir("test.xlsx", parse_result(sheets, cell_images))
        self.assertIn("已抽取 Excel 单元格图片：3 张。", plan.notes)

    def test_section_marker_switches_section(self):
        rows = [["图片方案（作图需求）"]]
        rows.append(["图序", "任务名称", "内容/要素", "画面需求", "图片示例", "备注"])
        rows.append(["1", "主图 1", "整体", "白底", "", ""])
        rows.append(["高级A+方案"])
        rows.append(["A+1 首图", "横幅大图", "场景", "21:9 宽幅", "", ""])
        plan = build_plan_ir("test.xlsx", parse_result({"图片方案-高地牛": FakeSheet(rows)}))
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(plan.tasks[0].section, "主图方案")
        self.assertEqual(plan.tasks[1].section, "高级A+方案")
        self.assertEqual(plan.tasks[1].display_no, "A+ 模块 1")
        self.assertEqual(plan.tasks[1].target_aspect_ratio, "21:9")

    def test_task_fields(self):
        plan = build_plan_ir("test.xlsx", parse_result({"图片方案-高地牛": FakeSheet(_main_sheet_rows())}))
        t1 = plan.tasks[0]
        self.assertEqual(t1.name, "主图 1")
        self.assertEqual(t1.source_row, "图片方案-高地牛!3")
        self.assertEqual(t1.display_no, "主图 1")
        self.assertEqual(t1.section_index, 1)
        self.assertIn("产品整体展示", t1.requirements)
        self.assertTrue(t1.selected)
        self.assertEqual(t1.status, "待处理")

    def test_url_in_reference_column_not_in_requirements(self):
        # 参考列 URL 不作为文本注记进入 requirements
        rows = _main_sheet_rows()
        plan = build_plan_ir("test.xlsx", parse_result({"图片方案-高地牛": FakeSheet(rows)}))
        t2 = plan.tasks[1]
        self.assertNotIn("https://", t2.requirements)

    def test_output_type_inference(self):
        self.assertEqual(infer_output_type("白底图", "主图方案"), "Amazon white-background main image")
        self.assertEqual(infer_output_type("场景图", "主图方案"), "lifestyle scene image")
        self.assertEqual(infer_output_type("尺寸图", "主图方案"), "dimension infographic")
        self.assertEqual(infer_output_type("任意", "高级A+方案"), "Premium A+ content module")

    def test_initial_section_inference(self):
        rows = [["图片方案（作图需求）"], ["图序", "任务名称"], ["1", "主图 1"]]
        self.assertEqual(
            infer_initial_section("图片方案-高地牛", rows, 1), "主图方案"
        )

    def test_guess_name_fallbacks(self):
        self.assertEqual(guess_name(["名称A"], "名称A 其他", "主图方案", 1), "名称A")
        long_name = "长" * 100
        self.assertEqual(len(guess_name([long_name], long_name, "主图方案", 1)), 60)
        self.assertEqual(guess_name(["", ""], "内容文本", "主图方案", 3), "内容文本"[:60])

    def test_join_distinct_lines(self):
        self.assertEqual(
            join_distinct_lines("a\nb", "b\nc", ""),
            "a\r\nb\r\nc",
        )

    def test_first_non_empty(self):
        self.assertEqual(first_non_empty("", "  ", "x", "y"), "x")
        self.assertEqual(first_non_empty("", "  "), "")


if __name__ == "__main__":
    unittest.main()
