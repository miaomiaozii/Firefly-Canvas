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

from plan_ir import PlanTask
from plan_ir_builder import (
    add_operation_references_from_mapped_cells,
    add_tasks_from_plan_blocks,
    extract_copy_text,
    extract_image_count,
    find_plan_block_end,
    guess_plan_block_name,
    has_explicit_image_cue,
    is_plan_block_start,
    join_block,
    should_skip_plan_block,
    build_display_no,
    build_fallback_requirements,
    build_image_lookup,
    build_plan_ir,
    clean_task_name,
    detect_same_as_main,
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
    join_mapped,
    join_reference_text_notes,
    looks_like_aplus_task,
    looks_like_note_row,
    looks_like_repeated_header_row,
    looks_like_task_row,
    map_headers,
    normalize_section_name,
    parse_linked_main_index,
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

    def test_sheet_count_counts_all_parse_result_sheets(self):
        # #103 守护项：sheet_count = parse_result["sheets"] 全部条目（对齐 C# :1188 口径的 python 侧上限；
        # excel_importer.parse 在 sheet part 缺失时会少写条目，已知偏差见完成报告）
        sheets = {"Sheet1": FakeSheet([["a"]]), "Sheet2": FakeSheet([["b"]])}
        plan = build_plan_ir("test.xlsx", parse_result(sheets))
        self.assertEqual(plan.sheet_count, 2)

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


class TestFallbackRequirements(unittest.TestCase):
    """C# BuildFallbackRequirements:2346 — 排除已映射列，取未映射非空单元格。"""

    def test_takes_unmapped_columns_only(self):
        row = ["主图 3", "", "白底图", "", "未映射描述"]
        col_map = {"name": [0], "requirements": [1], "outputType": [2]}
        self.assertEqual(build_fallback_requirements(row, col_map), "未映射描述")

    def test_all_mapped_falls_back_to_join_row(self):
        row = ["主图 3", "", "白底图"]
        col_map = {"name": [0], "requirements": [1], "outputType": [2]}
        self.assertEqual(
            build_fallback_requirements(row, col_map),
            "主图 3\r\n白底图",
        )

    def test_section_aspect_columns_excluded(self):
        row = ["主图 3", "", "白底图", "3:4", "自由描述"]
        col_map = {"name": [0], "requirements": [1], "section": [2], "aspect": [3]}
        self.assertEqual(build_fallback_requirements(row, col_map), "自由描述")

    def test_no_col_map_takes_all_non_empty(self):
        row = ["主图 3", "", "白底图"]
        self.assertEqual(
            build_fallback_requirements(row, None),
            "主图 3\r\n白底图",
        )


class TestMainLinkDetection(unittest.TestCase):
    """C# DetectSameAsMain:2438 / ParseLinkedMainIndex:2444 — 扫 Name+Requirements+CopyText+OverlayPlan。"""

    @staticmethod
    def _task(**kw):
        base = dict(name="任务", requirements="", copy_text="", overlay_plan="")
        base.update(kw)
        return PlanTask(**base)

    # ── detect_same_as_main ──

    def test_same_as_main_reference_marker(self):
        self.assertTrue(detect_same_as_main(self._task(requirements="参考主图第2张")))

    def test_same_as_main_same_nth(self):
        self.assertTrue(detect_same_as_main(self._task(requirements="同第2")))

    def test_same_as_main_in_name(self):
        self.assertTrue(detect_same_as_main(self._task(name="同主图变体")))

    def test_same_as_main_in_copy(self):
        self.assertTrue(detect_same_as_main(self._task(copy_text="文案同主图")))

    def test_same_as_main_in_overlay(self):
        self.assertTrue(detect_same_as_main(self._task(overlay_plan="叠字同第3")))

    def test_same_as_main_false_without_marker(self):
        self.assertFalse(detect_same_as_main(self._task(requirements="白底产品图")))

    # ── parse_linked_main_index ──

    def test_index_reference_main_nth(self):
        self.assertEqual(parse_linked_main_index(self._task(requirements="参考主图第2张")), 2)

    def test_index_parenthetical(self):
        self.assertEqual(parse_linked_main_index(self._task(requirements="同主图（使用第3张）")), 3)

    def test_index_same_nth(self):
        self.assertEqual(parse_linked_main_index(self._task(requirements="同第2")), 2)

    def test_index_chinese_digit(self):
        self.assertEqual(parse_linked_main_index(self._task(requirements="同主图，第三张")), 3)

    def test_index_from_name(self):
        self.assertEqual(parse_linked_main_index(self._task(name="主图 2 变体")), 2)

    def test_index_none(self):
        self.assertEqual(parse_linked_main_index(self._task(requirements="白底产品图")), 0)


class TestReferenceTextNotes(unittest.TestCase):
    """C# JoinReferenceTextNotes:2298 — 注记 = {表头或参考}：{净化文本}，整条去重。"""

    def test_nonempty_header_prefix(self):
        row = ["使用这张图的构图"]
        headers = ["参考图"]
        col_map = {"operationRef": [0]}
        self.assertEqual(
            join_reference_text_notes(row, headers, col_map, {}),
            "参考图：使用这张图的构图",
        )

    def test_empty_header_uses_reference(self):
        row = ["使用这张图的构图"]
        headers = [""]
        col_map = {"operationRef": [0]}
        self.assertEqual(
            join_reference_text_notes(row, headers, col_map, {}),
            "参考：使用这张图的构图",
        )

    def test_machine_reference_skipped(self):
        row = ["ID_ABC123"]
        headers = ["参考图"]
        col_map = {"operationRef": [0]}
        self.assertEqual(join_reference_text_notes(row, headers, col_map, {}), "")

    def test_identical_notes_deduped(self):
        row = ["同样的描述", "同样的描述"]
        headers = ["参考图", "参考图"]
        col_map = {"operationRef": [0, 1]}
        self.assertEqual(
            join_reference_text_notes(row, headers, col_map, {}),
            "参考图：同样的描述",
        )


class TestJoinMappedDedup(unittest.TestCase):
    """C# JoinMapped:2289 — 链尾 .Distinct()。"""

    def test_duplicate_values_deduped(self):
        row = ["相同文本", "相同文本", "不同"]
        col_map = {"requirements": [0, 1, 2]}
        self.assertEqual(join_mapped(row, col_map, "requirements"), "相同文本\r\n不同")

    def test_unmapped_key_returns_empty(self):
        self.assertEqual(join_mapped(["a"], {}, "requirements"), "")


class TestPlanBlockHelpers(unittest.TestCase):
    """C# AddTasksFromPlanBlocks:1921 配套助手（IsPlanBlockStart:1980 / FindPlanBlockEnd:1990 /
    ShouldSkipPlanBlock:2002 / HasExplicitImageCue:2010 / JoinBlock:2016 /
    ExtractImageCount:2029 / ExtractCopyText:2048 / GuessPlanBlockName:2063）。"""

    # ── is_plan_block_start（C# :1980）──

    def test_module_label_starts_block(self):
        self.assertTrue(is_plan_block_start("模块一：使用场景"))

    def test_count_phrases_start_block(self):
        self.assertTrue(is_plan_block_start("出2张场景图"))
        self.assertTrue(is_plan_block_start("共3张长图"))

    def test_video_cover_starts_block(self):
        self.assertTrue(is_plan_block_start("视频封面"))

    def test_video_without_cover_excluded(self):
        self.assertFalse(is_plan_block_start("视频脚本 模块一"))

    def test_no_image_needed_excluded(self):
        self.assertFalse(is_plan_block_start("模块一 不需要出图"))

    def test_blank_and_plain_line_not_start(self):
        self.assertFalse(is_plan_block_start(""))
        self.assertFalse(is_plan_block_start(None))
        self.assertFalse(is_plan_block_start("普通描述行"))

    # ── should_skip_plan_block（C# :2002）──

    def test_blank_block_skipped(self):
        self.assertTrue(should_skip_plan_block(""))

    def test_no_image_needed_skipped(self):
        self.assertTrue(should_skip_plan_block("模块一 无需出图"))

    def test_video_without_exemption_skipped(self):
        self.assertTrue(should_skip_plan_block("视频脚本 模块一"))

    def test_video_plus_image_module_not_skipped(self):
        self.assertFalse(should_skip_plan_block("视频+图片模块 出2张"))

    def test_normal_block_not_skipped(self):
        self.assertFalse(should_skip_plan_block("模块一 出2张"))

    # ── has_explicit_image_cue（C# :2010）──

    def test_count_phrases_are_cues(self):
        self.assertTrue(has_explicit_image_cue("出2张"))
        self.assertTrue(has_explicit_image_cue("共3张长图"))

    def test_video_cover_is_cue(self):
        self.assertTrue(has_explicit_image_cue("视频封面"))

    def test_module_label_alone_not_cue(self):
        self.assertFalse(has_explicit_image_cue("模块一：使用场景"))

    # ── extract_image_count（C# :2029）──

    def test_total_phrase_wins(self):
        self.assertEqual(extract_image_count("共3张长图"), 3)

    def test_per_item_count(self):
        self.assertEqual(extract_image_count("出2张场景图"), 2)

    def test_still_needed_prefix_excluded(self):
        self.assertEqual(extract_image_count("出2张 还需1张"), 2)

    def test_video_cover_defaults_to_one(self):
        self.assertEqual(extract_image_count("视频封面"), 1)

    def test_no_cue_defaults_to_one(self):
        self.assertEqual(extract_image_count("模块一"), 1)

    def test_capped_at_twelve(self):
        self.assertEqual(extract_image_count("共20张"), 12)

    # ── extract_copy_text（C# :2048）──

    def test_single_line_after_colon(self):
        self.assertEqual(extract_copy_text("文案：突出logo"), "突出logo")

    def test_multi_line_values(self):
        self.assertEqual(extract_copy_text("文案: 双行\r\n第二行文案：B"), "双行\r\nB")

    def test_no_copy_line_returns_empty(self):
        self.assertEqual(extract_copy_text("模块一 出2张"), "")

    # ── join_block（C# :2016）──

    def test_drops_empty_dedupes_joins_crlf(self):
        rows = [["a"], [""], ["a"], ["b"]]
        self.assertEqual(join_block(rows, 0, 4), "a\r\nb")

    def test_eighteen_line_cap(self):
        rows = [[f"行{i}"] for i in range(20)]
        self.assertEqual(len(join_block(rows, 0, 20).split("\r\n")), 18)

    # ── find_plan_block_end（C# :1990）──

    def test_next_block_start_is_end(self):
        rows = [["x"], ["模块一"], ["y"]]
        self.assertEqual(find_plan_block_end(rows, 0), 1)

    def test_category_page_is_end(self):
        rows = [["y"], ["分类页 2"]]
        self.assertEqual(find_plan_block_end(rows, 0), 1)

    def test_no_end_returns_row_count(self):
        rows = [["y"], ["z"]]
        self.assertEqual(find_plan_block_end(rows, 0), 2)

    # ── guess_plan_block_name（C# :2063）──

    def test_first_nonempty_line(self):
        self.assertEqual(guess_plan_block_name("\r\n第二行", "主图方案", 1), "第二行")

    def test_seventy_char_truncation(self):
        self.assertEqual(len(guess_plan_block_name("长" * 100, "主图方案", 1)), 70)

    def test_empty_block_uses_display_no(self):
        self.assertEqual(guess_plan_block_name("", "主图方案", 3), "主图 3")


class TestPlanBlocksEndToEnd(unittest.TestCase):
    """#103 块式无表头两级兜底（C# AddTasksFromRows:1615-1624 / AddTasksFromPlanBlocks:1921）。

    期望值按 C# 原文语义推演（非任务描述初稿）：
    - "出2张场景图" 命中 IsPlanBlockStart（出N张）→ 自身成为块起点，截断上一块；
    - "模块一：使用场景" 块（rows 0-1）无显式出图线索且行文本命中 模块 regex → 被跳过；
    - "模块二：对比场景" 块（rows 2-4）含 "出2张场景图" → count=2，copy 亦为 "突出logo"。
    """

    @staticmethod
    def _core_rows():
        return [
            ["高地牛 A+ 补充方案"],
            ["模块一：使用场景"],
            ["出2张场景图"],
            ["文案：突出logo"],
            ["模块二：对比场景"],
        ]

    def test_core_block_fixture_produces_four_tasks(self):
        rows = self._core_rows()
        # 防 fixture 失效：单格行 score<2 → 无表头
        self.assertEqual(find_header_row(rows), -1)
        plan = build_plan_ir("t.xlsx", parse_result({"A+方案": FakeSheet(rows)}))
        self.assertEqual(len(plan.tasks), 4)
        self.assertEqual(
            [t.name for t in plan.tasks],
            ["出2张场景图 1/2", "出2张场景图 2/2", "模块二：对比场景 1/2", "模块二：对比场景 2/2"],
        )
        # section = infer_section("A+方案", "")（C# :2363）
        for t in plan.tasks:
            self.assertEqual(t.section, "高级A+方案")
        # C# 用块起始行 i+1（1-based），非 copyIndex 行
        self.assertEqual([t.source_row for t in plan.tasks], ["A+方案!3", "A+方案!3", "A+方案!5", "A+方案!5"])
        self.assertEqual([t.sort_index for t in plan.tasks], [1, 2, 3, 4])
        self.assertEqual([t.task_id for t in plan.tasks], ["excel_1", "excel_2", "excel_3", "excel_4"])
        self.assertEqual([t.section_index for t in plan.tasks], [1, 2, 3, 4])
        self.assertEqual([t.display_no for t in plan.tasks], ["A+ 模块 1", "A+ 模块 2", "A+ 模块 3", "A+ 模块 4"])
        # 文案：块文本含 "文案：突出logo"（C# ExtractCopyText:2048）
        self.assertEqual([t.copy_text for t in plan.tasks], ["突出logo"] * 4)
        self.assertEqual(
            [t.copy_handling_mode for t in plan.tasks],
            ["manual_overlay_after_generation"] * 4,
        )
        # requirements = JoinBlock 原文（C# :2016，从 max(0, i-2) 起）
        self.assertEqual(
            plan.tasks[0].requirements,
            "高地牛 A+ 补充方案\r\n模块一：使用场景\r\n出2张场景图\r\n文案：突出logo",
        )
        self.assertEqual(
            plan.tasks[2].requirements,
            "出2张场景图\r\n文案：突出logo\r\n模块二：对比场景",
        )
        # 比例：A+ 默认 21:9（C# InferAspectRatio:2404）
        self.assertEqual([t.target_aspect_ratio for t in plan.tasks], ["21:9"] * 4)
        self.assertEqual([t.source_aspect_ratio for t in plan.tasks], ["21:9"] * 4)
        # 输出类型：块文本含 场景（C# InferOutputType:2372）
        self.assertEqual([t.output_type for t in plan.tasks], ["lifestyle scene image"] * 4)
        # 主图关联：无标记
        self.assertEqual([t.same_as_main for t in plan.tasks], [False] * 4)
        self.assertEqual([t.linked_main_index for t in plan.tasks], [0] * 4)
        self.assertEqual([t.linked_main_label for t in plan.tasks], [""] * 4)
        # 状态默认值
        self.assertEqual([t.status for t in plan.tasks], ["待处理"] * 4)
        self.assertEqual([t.selected for t in plan.tasks], [True] * 4)

    def test_no_block_markers_falls_back_to_unmapped(self):
        # 无块标记 → PlanBlocks 0 产出 → AddTasksFromUnmappedRows 老路径（改动前基线一致：2 任务）
        rows = [["场景图A"], ["场景图B"]]
        self.assertEqual(find_header_row(rows), -1)
        plan = build_plan_ir("t.xlsx", parse_result({"Sheet1": FakeSheet(rows)}))
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual([t.name for t in plan.tasks], ["场景图A", "场景图B"])
        self.assertEqual([t.section for t in plan.tasks], ["图片方案", "图片方案"])
        self.assertEqual([t.display_no for t in plan.tasks], ["图片 1", "图片 2"])

    def test_skipped_block_falls_back_to_unmapped(self):
        # ShouldSkipPlanBlock 命中（不需要出图）→ 0 产出 → unmapped 回退：
        # "模块一：使用场景" 命中 模块 关键词成任务；"不需要出图" 不含 图片 整词不成任务（改动前基线一致：1 任务）
        rows = [["模块一：使用场景"], ["不需要出图"]]
        self.assertEqual(find_header_row(rows), -1)
        plan = build_plan_ir("t.xlsx", parse_result({"A+方案": FakeSheet(rows)}))
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].name, "模块一：使用场景")
        self.assertEqual(plan.tasks[0].section, "高级A+方案")

    def test_sort_index_continues_across_sheets(self):
        # 无表头分支返回 sort_index（既有 return len(tasks) 丢 sort_index 的 bug 顺手修复）
        rows_a = [["出2张场景图"]]
        rows_b = [["出1张对比图"]]
        plan = build_plan_ir(
            "t.xlsx",
            parse_result({"A+方案": FakeSheet(rows_a), "A+方案2": FakeSheet(rows_b)}),
        )
        self.assertEqual([t.task_id for t in plan.tasks], ["excel_1", "excel_2", "excel_3"])
        self.assertEqual([t.sort_index for t in plan.tasks], [1, 2, 3])

    def test_add_tasks_from_plan_blocks_returns_sort_index(self):
        from plan_ir import PlanTask

        tasks: list[PlanTask] = []
        rows = [["出2张场景图"], ["文案：突出logo"]]
        sort_index = add_tasks_from_plan_blocks(tasks, rows, "A+方案", {}, 10)
        self.assertEqual(sort_index, 12)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].task_id, "excel_11")


class TestMainLinkEndToEnd(unittest.TestCase):
    """#102 验收用例：requirements 空列回退 + 参考主图第N张（现有 15 任务基线抓不住这些分支）。"""

    def test_empty_requirements_column_uses_unmapped_fallback(self):
        rows = [
            ["任务名称", "内容/要素", "画面需求", "图片示例", "备注", "其他"],
            ["主图 3", "", "", "", "", "突出logo"],
        ]
        plan = build_plan_ir("t.xlsx", parse_result({"Sheet1": FakeSheet(rows)}))
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].requirements, "突出logo")

    def test_reference_main_nth_sets_link_fields(self):
        rows = [
            ["任务名称", "内容/要素", "画面需求", "图片示例", "备注"],
            ["场景图", "场景图", "参考主图第2张，改为露营场景", "", ""],
        ]
        plan = build_plan_ir("t.xlsx", parse_result({"Sheet1": FakeSheet(rows)}))
        self.assertEqual(len(plan.tasks), 1)
        task = plan.tasks[0]
        self.assertTrue(task.same_as_main)
        self.assertEqual(task.linked_main_index, 2)
        self.assertEqual(task.linked_main_label, "同主图 2")


if __name__ == "__main__":
    unittest.main()
