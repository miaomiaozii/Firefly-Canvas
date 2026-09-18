# -*- coding: utf-8 -*-
"""Plan IR builder: 从 Excel 解析结果构建 Plan IR。

移植 C# ImagePlanDesignerP0 的 OpenXML package 路径（Program.cs，行号为权威规格）：
- ImportXlsxPackage:1140（主循环：ShouldSkipSheet / ScannedRowCount / imageLookup）
- AddTasksFromRows:1615（含 PlanBlocks 两级兜底）/ AddTasksFromUnmappedRows:1707 / ImportRowsForSmoke:1746
- AddTasksFromPlanBlocks:1921 / IsPlanBlockStart:1980 / FindPlanBlockEnd:1990 / ShouldSkipPlanBlock:2002
- HasExplicitImageCue:2010 / JoinBlock:2016 / ExtractImageCount:2029 / ExtractCopyText:2048 / GuessPlanBlockName:2063
- AddOperationReferencesFromMappedCells:1785 / ResolveReferenceFiles:1812,1853
- NextHeaderColumn:1833 / ResolveAnchoredReferenceFiles:1843 / IsImageFilePath:1865
- TryGetSectionMarker:1877 / LooksLikeAPlusTask:1897 / StartsWithAPlusTaskLabel:1907
- FindHeaderRow:2074 / HasLikelyTaskBetween:2096 / LooksLikeRepeatedHeaderRow:2116
- HeaderCellLooksLikeColumnName:2126 / LooksLikeCompositeHeaderCell:2170
- HeaderCellScore:2195 / NormalizeSectionName:2209 / MapHeaders:2218
- LooksLikeTaskRow:2246 / LooksLikeNoteRow:2266 / FirstMapped:2280 / JoinMapped:2289
- JoinReferenceTextNotes:2298 / LooksLikeMachineImageReference:2319
- JoinDistinctLines:2330 / BuildFallbackRequirements:2346 / InferSection:2363
- InferOutputType:2372 / SanitizePromptField:2384 / InferAspectRatio:2404
- BuildDisplayNo:2430 / DetectSameAsMain:2438 / ParseLinkedMainIndex:2444 / GuessName:2478
- JoinRow:2488 / CompactLine:2493 / FirstNonEmpty:2498 / ContainsAny:2507

验收口径：与 excel-import-smoke.json 基准逐项一致（SheetCount=13 / ScannedRowCount=23 /
ImportedTaskCount=15 / SampleTasks 15 项全字段 / 70 张内嵌图）。

设计原则：
- 与 C# ImagePlanDesignerP0 smoke 基准字段级对齐
- 保持确定性（相同输入 → 相同输出）
- 任务数量/依赖永远来自 Excel 解析器，LLM 不参与
"""
import os
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from plan_ir import PlanIR, PlanTask, ReferenceImage

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


# ─── Sheet 分类（C# :1559/:1565/:1576）───────────────────────────────────────


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


# ─── Section / 输出类型 / 比例推断（C# :2209/:2363/:2372/:2404/:1755）────────


def normalize_section_name(section: Optional[str]) -> str:
    """C# NormalizeSectionName:2209 — A+ 类 → 高级A+方案；主图类 → 主图方案；否则原样。"""
    text = compact_line(section)
    if not text:
        return ""
    if contains_any(text, "高级A+", "高级A＋", "A+", "A＋", "aplus", "a plus"):
        return "高级A+方案"
    if contains_any(
        text, "主图", "main", "场景图", "白底图", "尺寸图", "功能卖点", "对比图", "使用图", "细节图", "礼物图"
    ):
        return "主图方案"
    return section or ""


def infer_section(sheet_name: Optional[str], row_text: Optional[str]) -> str:
    """C# InferSection:2363。"""
    text = ((sheet_name or "") + " " + (row_text or "")).lower()
    if "a+" in text or "高级a" in text:
        return "高级A+方案"
    if "主图" in text:
        return "主图方案"
    if contains_any(sheet_name or "", "作图", "店铺", "品牌") or "店铺" in text or "品牌" in text:
        return "品牌店铺方案"
    return "图片方案"


def infer_output_type(text: Optional[str], section: Optional[str]) -> str:
    """C# InferOutputType:2372。"""
    source = (text or "").lower()
    if "白底" in source:
        return "Amazon white-background main image"
    if "场景" in source:
        return "lifestyle scene image"
    if "尺寸" in source:
        return "dimension infographic"
    if "细节" in source:
        return "detail infographic"
    if "对比" in source:
        return "comparison infographic"
    if section and "A+" in section:
        return "Premium A+ content module"
    return "ecommerce product image"


_RE_ASPECT_DIMS = re.compile(r"(\d{3,5})\s*[x×*]\s*(\d{2,5})", re.IGNORECASE)


def infer_aspect_ratio(section: Optional[str], source_aspect_ratio: Optional[str], row_text: Optional[str]) -> str:
    """C# InferAspectRatio:2404 — 比例串（3:4 优先）→ 尺寸串（必返回）→ A+ 默认 21:9 → 1:1。"""
    text = ((source_aspect_ratio or "") + " " + (row_text or "")).lower()
    for ratio in ("3:4", "4:3", "16:9", "21:9", "1:1"):
        if ratio in text:
            return ratio
    dims = _RE_ASPECT_DIMS.findall(text)
    if dims:
        try:
            width = float(dims[-1][0])
            height = float(dims[-1][1])
            if height > 0:
                ratio_value = width / height
                if ratio_value >= 3.0:
                    return "21:9"
                if ratio_value >= 1.45:
                    return "16:9"
                if ratio_value <= 0.8:
                    return "3:4"
                return "1:1"
        except ValueError:
            pass
    if section and "A+" in section:
        return "21:9"
    return "1:1"


def infer_initial_section(sheet_name: Optional[str], rows: List[List[str]], start: int) -> str:
    """C# InferInitialSection:1755。"""
    sheet = sheet_name or ""
    if contains_any(sheet, "主图") and contains_any(sheet, "a+", "A+", "高级A"):
        return "主图方案"
    if contains_any(sheet, "a+", "A+", "高级A"):
        return "高级A+方案"
    for i in range(start, len(rows)):
        text = join_row(rows[i])
        marker = try_get_section_marker(rows[i], text)
        if marker and "A+" in marker:
            return "主图方案"
    if contains_any(sheet, "图片方案", "作图", "主图"):
        return "主图方案"
    return infer_section(sheet_name, "")


# ─── Header 机制（C# :2074-:2244）────────────────────────────────────────────


def header_cell_score(compact: str) -> int:
    """C# HeaderCellScore:2195 — 表头单元格关键词计分（入参需已 CompactLine）。"""
    if not compact or not compact.strip():
        return 0
    if contains_any(compact, "图序", "图号", "序号", "顺序"):
        return 1
    if contains_any(compact, "任务名称", "图片名称", "模块名称", "任务标题", "图片标题"):
        return 2
    if contains_any(compact, "内容/要素", "内容要素", "展示元素", "运营需求", "画面需求", "作图需求", "要求"):
        return 2
    if contains_any(compact, "图片示例", "参考图", "参考图片", "效果图") or compact == "参考":
        return 2
    if contains_any(compact, "图类型", "画面类型", "输出类型", "方案类型", "图片类型"):
        return 2
    if contains_any(
        compact, "提示词", "prompt", "文案", "文字", "字幕", "叠字", "比例", "宽高", "尺寸",
        "类型", "名称", "标题", "类目", "像素", "板块", "所属", "分类", "描述", "布局",
        "场景", "备注", "方案", "运营",
    ):
        return 1
    if len(compact) <= 18 and contains_any(compact, "任务", "图片", "图", "需求", "卖点", "内容"):
        return 1
    return 0


_COMPOSITE_HEADER_KEYWORDS = (
    "任务名称", "图片名称", "模块名称", "任务标题", "图片标题",
    "所属板块", "所属分类", "方案分类",
    "图类型", "画面类型", "输出类型", "方案类型", "图片类型",
    "运营文案", "卖点文案", "展示文案", "画面文案",
    "画面比例", "图片比例", "图片尺寸", "输出尺寸", "图片像素",
)


def looks_like_composite_header_cell(compact: str) -> bool:
    """C# LooksLikeCompositeHeaderCell:2170 — ≤18 字符且含复合列名关键词。"""
    if not compact or not compact.strip() or len(compact) > 18:
        return False
    return contains_any(compact, *_COMPOSITE_HEADER_KEYWORDS)


_COLUMN_NAME_EXACT = (
    "顺序", "序号", "图序", "图号", "要求", "需求", "运营需求", "画面需求", "作图需求",
    "内容", "内容/要素", "内容要素", "展示元素", "图片示例", "参考图", "参考图片", "参考",
    "效果图", "文案", "卖点文案", "文字", "字幕", "叠字", "比例", "尺寸", "宽高", "像素",
    "类型", "名称", "标题", "类目", "描述", "布局", "场景", "备注", "方案", "运营",
    "提示词", "prompt", "板块", "所属", "分类", "方案类型", "图片类型", "输出类型",
    "图类型", "画面类型", "任务", "模块", "图片",
)


def header_cell_looks_like_column_name(compact: str) -> bool:
    """C# HeaderCellLooksLikeColumnName:2126 — 精确列名集合 + 前缀 '顺序 ' + 复合格。"""
    if not compact or not compact.strip():
        return False
    if compact == "顺序" or compact.startswith("顺序 "):
        return True
    return compact in _COLUMN_NAME_EXACT or looks_like_composite_header_cell(compact)


def looks_like_repeated_header_row(row: List[str]) -> bool:
    """C# LooksLikeRepeatedHeaderRow:2116 — ≥2 个列名样式格且总分达标（2 格≥2，否则≥3）。"""
    non_empty = [compact_line(c) for c in (row or []) if c and c.strip()]
    if len(non_empty) < 2:
        return False
    scored_cells = sum(1 for c in non_empty if header_cell_looks_like_column_name(c))
    score = sum(header_cell_score(c) for c in non_empty)
    required_score = 2 if len(non_empty) == 2 else 3
    return scored_cells >= 2 and score >= required_score


def has_likely_task_between(rows: List[List[str]], start: int, end_exclusive: int) -> bool:
    """C# HasLikelyTaskBetween:2096 — (start, endExclusive) 内存在疑似任务行。"""
    for i in range(max(0, start), min(len(rows), end_exclusive)):
        row_text = join_row(rows[i])
        if not row_text or not row_text.strip():
            continue
        if looks_like_repeated_header_row(rows[i]):
            continue
        if try_get_section_marker(rows[i], row_text):
            continue
        if sum(1 for c in rows[i] if c and c.strip()) >= 2:
            return True
        if looks_like_task_row(rows[i], row_text, False):
            return True
    return False


def find_header_row(rows: List[List[str]]) -> int:
    """C# FindHeaderRow:2074 — 前 80 行内重复表头行；任务间隔保护。"""
    best_row = -1
    best_score = 0
    for i in range(min(len(rows), 80)):
        if not looks_like_repeated_header_row(rows[i]):
            continue
        score = sum(header_cell_score(compact_line(c)) for c in rows[i])
        if score > best_score:
            if best_row >= 0 and has_likely_task_between(rows, best_row + 1, i):
                continue
            best_score = score
            best_row = i
        elif score == best_score and score >= 2 and not has_likely_task_between(rows, best_row + 1, i):
            best_row = i
    return best_row if best_score >= 2 else -1


def _add_map(col_map: Dict[str, List[int]], key: str, index: int) -> None:
    """C# AddMap:2244 — 同键多列（Append）。"""
    col_map.setdefault(key, []).append(index)


def map_headers(header_row: List[str]) -> Dict[str, List[int]]:
    """C# MapHeaders:2218 — 有序优先级链（先匹配先占用，同键多列）。"""
    col_map: Dict[str, List[int]] = {}
    for i, h in enumerate(header_row or []):
        if not h or not h.strip():
            continue
        if compact_line(h) == "参考" or contains_any(h, "效果图", "图片示例", "参考图", "参考图补充", "参考图片", "图片参考"):
            _add_map(col_map, "operationRef", i)
        elif contains_any(h, "图序", "图号", "序号", "顺序"):
            _add_map(col_map, "name", i)
        elif contains_any(h, "文案", "叠字", "文字", "字幕", "卖点文案"):
            _add_map(col_map, "copy", i)
        elif contains_any(h, "内容/要素", "内容要素", "展示元素", "评论+卖点", "卖点", "运营需求", "画面需求", "作图需求", "要求", "类目"):
            _add_map(col_map, "requirements", i)
        elif contains_any(h, "比例", "宽高", "尺寸", "像素"):
            _add_map(col_map, "aspect", i)
        elif contains_any(h, "提示词", "prompt"):
            _add_map(col_map, "prompt", i)
        elif contains_any(h, "板块", "所属", "分类", "方案类型", "图片类型"):
            _add_map(col_map, "section", i)
        elif contains_any(h, "输出类型", "图类型", "画面类型", "类型"):
            _add_map(col_map, "outputType", i)
        elif contains_any(h, "任务", "模块", "图片", "名称", "标题", "序号"):
            _add_map(col_map, "name", i)
        elif contains_any(h, "运营", "需求", "方案", "画面", "描述", "要求", "布局", "场景", "备注", "内容"):
            _add_map(col_map, "requirements", i)
    return col_map


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


# ─── 映射列取值（C# :2280/:2289）─────────────────────────────────────────────


def _col_values(row: List[str], col_map: Dict[str, List[int]], key: str) -> List[str]:
    cols = col_map.get(key, []) if col_map else []
    return [row[i] for i in cols if i < len(row)]


def first_mapped(row: List[str], col_map: Dict[str, List[int]], key: str) -> str:
    """C# FirstMapped:2280。"""
    for v in _col_values(row, col_map, key):
        if v and v.strip():
            return v
    return ""


def join_mapped(row: List[str], col_map: Dict[str, List[int]], key: str) -> str:
    """C# JoinMapped:2289 — 非空单元格值去重（.Distinct()）后以 \r\n 连接。"""
    parts = [v for v in _col_values(row, col_map, key) if v and v.strip()]
    return "\r\n".join(_distinct(parts))


# ─── 回退需求 / 显示编号 / 主图关联（C# :2346/:2430/:2438/:2444/:2478）────────


def build_fallback_requirements(row: List[str], col_map: Optional[Dict[str, List[int]]]) -> str:
    """C# BuildFallbackRequirements:2346 — 排除已映射列，取未映射非空单元格（净化去重）。"""
    skip: set = set()
    if col_map:
        for key in ("name", "section", "outputType", "copy", "aspect", "prompt", "operationRef"):
            skip.update(col_map.get(key, []))
    values: List[str] = []
    for i, text in enumerate(row or []):
        if i in skip or not text or not text.strip():
            continue
        cleaned = sanitize_prompt_field(text)
        if cleaned and cleaned.strip() and cleaned not in values:
            values.append(cleaned)
    if values:
        return "\r\n".join(values)
    return sanitize_prompt_field(join_row(row))


def detect_same_as_main(task: PlanTask) -> bool:
    """C# DetectSameAsMain:2438 — 扫 Name+Requirements+CopyText+OverlayPlan 全文，命中任一标记。"""
    text = f"{task.name} {task.requirements} {task.copy_text} {task.overlay_plan}".lower()
    return contains_any(text, "a+同主图", "同主图", "同第", "参考主图")


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_LINKED_MAIN_MARKERS = ("主图", "同第", "参考主图")


def parse_linked_main_index(task: PlanTask) -> int:
    """C# ParseLinkedMainIndex:2444 — 从首个标记位置扫到文本末尾，取第一个数字/中文数字（一~九）。"""
    text = f"{task.name} {task.requirements} {task.copy_text} {task.overlay_plan}"
    for marker in _LINKED_MAIN_MARKERS:
        idx = text.find(marker)
        if idx < 0:
            continue
        for ch in text[idx:]:
            if ch.isdigit():
                try:
                    return int(ch)
                except ValueError:  # 上标等非 Nd 数字，C# char.IsDigit 亦不命中
                    continue
            if ch in _CN_DIGITS:
                return _CN_DIGITS[ch]
    return 0


# ─── 参考图解析（C# :1812/:1833/:1843/:1853/:1865/:2298/:2319）────────────────

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


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
    for i in _distinct(col_map.get("operationRef", []) if col_map else []):
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
    return "\r\n".join(_distinct(parts))


def _distinct(seq: List[int]) -> List[int]:
    return list(dict.fromkeys(seq))


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
    for i in _distinct(col_map["operationRef"]):
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


# ─── imageLookup 构建（C# ExtractCellImages:1232 结果集，OrdinalIgnoreCase）────


def build_image_lookup(
    sheets: Dict[str, Any],
    cell_images: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """构建 C# imageLookup 等价物：{lowercase_key: {"path","data","origin"}}。

    - cell images：key = cNvPr name（ID_xxxx），origin="cellimage"
    - drawing 锚点：key = CELL:{sheet}!{row}:{col}#{seq}（C# CellImageKey:1456），origin="anchored"
    """
    lookup: Dict[str, Dict[str, Any]] = {}
    for name, val in (cell_images or {}).items():
        if isinstance(val, dict):
            data, path = val.get("data"), val.get("path")
        else:  # 兼容旧格式（纯 bytes）
            data, path = val, f"maps/{name}.png"
        if data is not None:
            lookup[name.lower()] = {"path": path or f"maps/{name}.png", "data": data, "origin": "cellimage"}
    for sheet_name, sheet_data in (sheets or {}).items():
        for anchor in getattr(sheet_data, "image_anchors", None) or []:
            data = getattr(sheet_data, "images", {}).get(anchor.get("rid"))
            if data is None:
                continue
            key = f"CELL:{sheet_name}!{anchor.get('row')}:{anchor.get('col')}#{anchor.get('seq')}"
            lookup[key.lower()] = {
                "path": anchor.get("path") or key,
                "data": data,
                "origin": "anchored",
            }
    return lookup


# ─── 块式方案块（C# AddTasksFromPlanBlocks:1921 起，无表头第一级兜底）──────────────

_RE_MODULE_LABEL = re.compile(r"模块\s*\d|模块\s*[一二三四五六七八九]")
_RE_BLOCK_START_COUNT = re.compile(r"出\s*\d+\s*张|共\s*\d+\s*张|\d+\s*张(长图|方图|左大图|右小图|图|banner)", re.IGNORECASE)
_RE_IMAGE_CUE = re.compile(r"(?:出|共)\s*\d+\s*张|\d+\s*张(长图|方图|左大图|右小图|图|banner)", re.IGNORECASE)
_RE_CATEGORY_PAGE = re.compile(r"^分类页\s*\d|^🏠|^Home", re.IGNORECASE)
_RE_COUNT_TOTAL = re.compile(r"共\s*(\d+)\s*张(?:长图|方图|左大图|右小图|图|banner)?", re.IGNORECASE)
_RE_COUNT_EACH = re.compile(r"(?:出\s*)?(\d+)\s*张(?:长图|方图|左大图|右小图|图|banner)?", re.IGNORECASE)


def is_plan_block_start(text: Optional[str]) -> bool:
    """C# IsPlanBlockStart:1980 — 视频封面/封面图片 / 模块N / 出N张|共N张|N张(长图|...)。"""
    if not text or not text.strip():
        return False
    if contains_any(text, "不需要出图", "无需出图"):
        return False
    if contains_any(text, "视频") and not contains_any(text, "视频封面", "封面图片", "图片模块"):
        return False
    return (
        contains_any(text, "视频封面", "封面图片")
        or bool(_RE_MODULE_LABEL.search(text))
        or bool(_RE_BLOCK_START_COUNT.search(text))
    )


def find_plan_block_end(rows: List[List[str]], start: int) -> int:
    """C# FindPlanBlockEnd:1990 — 下一个块起始/分类页行索引；无则 len(rows)。"""
    for i in range(start, len(rows)):
        text = join_row(rows[i])
        if not text or not text.strip():
            continue
        if is_plan_block_start(text):
            return i
        if _RE_CATEGORY_PAGE.search(text):
            return i
    return len(rows)


def should_skip_plan_block(text: Optional[str]) -> bool:
    """C# ShouldSkipPlanBlock:2002 — 空白 / 不需要出图 / 视频（无豁免词）。"""
    if not text or not text.strip():
        return True
    if contains_any(text, "不需要出图", "无需出图"):
        return True
    if contains_any(text, "视频") and not contains_any(text, "视频封面", "封面图片", "视频+图片模块"):
        return True
    return False


def has_explicit_image_cue(text: Optional[str]) -> bool:
    """C# HasExplicitImageCue:2010 — 视频封面/封面图片 或 出/共N张 / N张(长图|...)。"""
    return contains_any(text, "视频封面", "封面图片") or bool(_RE_IMAGE_CUE.search(text or ""))


def join_block(rows: List[List[str]], start: int, end: int) -> str:
    """C# JoinBlock:2016 — 逐行 compact、剔除空行、18 行上限、保序去重、\r\n 连接并 trim。"""
    lines: List[str] = []
    for i in range(start, min(end, len(rows))):
        text = compact_line(join_row(rows[i]))
        if text and text.strip():
            lines.append(text)
        if len(lines) >= 18:
            break
    return "\r\n".join(dict.fromkeys(lines)).strip()


def extract_image_count(text: Optional[str]) -> int:
    """C# ExtractImageCount:2029 — 共N张 优先（上限 12）；否则逐项 N张 求和（还需前缀排除、单项≤12）；
    无线索且含 视频封面/封面图片 → 1；结果钳制 [1, 12]。"""
    total_match = _RE_COUNT_TOTAL.search(text or "")
    if total_match:
        total_count = int(total_match.group(1))
        if total_count > 0:
            return min(total_count, 12)
    total = 0
    for m in _RE_COUNT_EACH.finditer(text or ""):
        prefix = (text or "")[max(0, m.start() - 4):m.start()]
        if "还需" in prefix:
            continue
        count = int(m.group(1))
        if count > 0 and count <= 12:
            total += count
    if total <= 0 and contains_any(text, "视频封面", "封面图片"):
        total = 1
    return max(1, min(1 if total <= 0 else total, 12))


def extract_copy_text(text: Optional[str]) -> str:
    """C# ExtractCopyText:2048 — 每行含「文案」则取首个全/半角冒号后的值（无冒号取整行），保序去重，\r\n 连接。"""
    lines: List[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        index = line.lower().find("文案")
        if index < 0:
            continue
        value = line
        m = re.search(r"[：:]", line[index:])
        if m:
            colon = index + m.start()
            if colon + 1 < len(line):
                value = line[colon + 1:].strip()
        if value and value.strip():
            lines.append(value)
    return "\r\n".join(dict.fromkeys(lines))


def guess_plan_block_name(block_text: Optional[str], section: str, index: int) -> str:
    """C# GuessPlanBlockName:2063 — 首个非空行（70 字符截断）；无则显示编号。"""
    for raw in (block_text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        return line[:70]
    return build_display_no(section, index)


def add_tasks_from_plan_blocks(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
) -> int:
    """C# AddTasksFromPlanBlocks:1921 — 块式方案块（无表头第一级兜底）。返回 sort_index。

    忠实移植注意：
    - Name 不做 clean_task_name（C# 块路径就是 baseName/count 拼接）；
    - 不挂 operation_references / cell_image（C# 该路径没有）；
    - C# for 循环内 `i = Math.Max(i, end - 1)` 后还有 i++ → python while 等价 `+ 1`。
    """
    section = infer_section(sheet_name, "")
    i = 0
    while i < len(rows):
        row_text = join_row(rows[i])
        if not is_plan_block_start(row_text):
            i += 1
            continue

        end = find_plan_block_end(rows, i + 1)
        block_text = join_block(rows, max(0, i - 2), end)
        if should_skip_plan_block(block_text):
            i = max(i, end - 1) + 1
            continue
        if not has_explicit_image_cue(block_text) and _RE_MODULE_LABEL.search(row_text):
            i += 1
            continue

        count = extract_image_count(block_text)
        next_index = counters[section] + 1 if section in counters else 1
        base_name = first_non_empty(
            compact_line(row_text),
            guess_plan_block_name(block_text, section, next_index),
        )
        copy_text = extract_copy_text(block_text)
        aspect = infer_aspect_ratio(section, "", block_text)

        for copy_index in range(1, count + 1):
            if section not in counters:
                counters[section] = 0
            counters[section] += 1
            sort_index += 1
            name = f"{base_name} {copy_index}/{count}" if count > 1 else base_name
            task = PlanTask(
                task_id=f"excel_{sort_index}",
                name=name,
                section=section,
                source_row=f"{sheet_name}!{i + 1}",
                sort_index=sort_index,
                section_index=counters[section],
                display_no=build_display_no(section, counters[section]),
                output_type=infer_output_type(block_text, section),
                source_aspect_ratio=aspect,
                target_aspect_ratio=aspect,
                requirements=block_text,
                copy_handling_mode="none" if not (copy_text and copy_text.strip()) else "manual_overlay_after_generation",
                copy_text=copy_text,
                status="待处理",
                selected=True,
            )
            apply_main_link(task)
            tasks.append(task)
        i = max(i, end - 1) + 1
    return sort_index


# ─── 任务构建主循环（C# AddTasksFromRows:1615 / AddTasksFromUnmappedRows:1707）


def apply_main_link(task: PlanTask) -> None:
    """主图关联三字段（表头/无表头路径共用）。"""
    task.same_as_main = detect_same_as_main(task)
    task.linked_main_index = parse_linked_main_index(task)
    task.linked_main_label = (
        f"同主图 {task.linked_main_index}"
        if task.linked_main_index > 0
        else ("同主图" if task.same_as_main else "")
    )


def add_tasks_from_rows(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
    image_lookup: Dict[str, Dict[str, Any]],
) -> int:
    """C# AddTasksFromRows:1615 — 表头路径（image_lookup 为 None 时退化为无锚定图）。

    无表头分支为两级兜底（C# :1618-1623）：先 AddTasksFromPlanBlocks（块式方案块），
    仅其 0 产出时才 AddTasksFromUnmappedRows。返回 sort_index（既有 `return len(tasks)`
    会丢 sort_index、多 sheet 时 task_id 重复，顺手修复）。
    """
    header_row = find_header_row(rows)
    if header_row < 0:
        before = len(tasks)
        sort_index = add_tasks_from_plan_blocks(tasks, rows, sheet_name, counters, sort_index)
        if len(tasks) == before:
            sort_index = add_tasks_from_unmapped_rows(tasks, rows, sheet_name, counters, sort_index)
        return sort_index
    headers = rows[header_row]
    col_map = map_headers(headers)
    current_section = infer_initial_section(sheet_name, rows, header_row + 1)

    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        row_text = join_row(row)
        section_marker = try_get_section_marker(row, row_text)
        if section_marker:
            current_section = section_marker
            continue
        if looks_like_repeated_header_row(row):
            continue
        if not looks_like_task_row(row, row_text, True):
            continue

        mapped_name = first_mapped(row, col_map, "name")
        mapped_requirements = join_mapped(row, col_map, "requirements")
        mapped_output_type = first_mapped(row, col_map, "outputType")
        row_lead = first_non_empty(*row)
        mapped_section = first_mapped(row, col_map, "section")

        section = normalize_section_name(mapped_section)
        if not section or not section.strip():
            output_type_section = normalize_section_name(mapped_output_type)
            if output_type_section and output_type_section.strip():
                section = output_type_section
            else:
                section = "高级A+方案" if looks_like_aplus_task(mapped_name, mapped_requirements) else ""
                if not section:
                    section = "高级A+方案" if looks_like_aplus_task(row_lead, mapped_requirements) else ""
        if not section or not section.strip():
            section = first_non_empty(
                current_section,
                infer_section(sheet_name, row_text),
            )
        else:
            current_section = section
        counters[section] = counters.get(section, 0) + 1
        section_idx = counters[section]
        sort_index += 1

        requirements = mapped_requirements
        copy_text = join_mapped(row, col_map, "copy")
        if not requirements or not requirements.strip():
            requirements = build_fallback_requirements(row, col_map)
        requirements = join_distinct_lines(
            requirements,
            join_reference_text_notes(row, headers, col_map, image_lookup),
        )

        row_number = i + 1  # 1-based（C# 行索引 +1 = SourceRow）
        refs = add_operation_references_from_mapped_cells(
            row, headers, col_map, image_lookup, sheet_name, row_number
        )

        source_aspect = first_mapped(row, col_map, "aspect")
        task_name = clean_task_name(first_non_empty(mapped_name, guess_name(row, row_text, section, section_idx)))
        task = PlanTask(
            task_id=f"excel_{sort_index}",
            name=task_name,
            section=section,
            source_row=f"{sheet_name}!{row_number}",
            sort_index=sort_index,
            section_index=section_idx,
            display_no=build_display_no(section, section_idx),
            output_type=first_non_empty(mapped_output_type, infer_output_type(row_text, section)),
            source_aspect_ratio=source_aspect,
            target_aspect_ratio=infer_aspect_ratio(section, source_aspect, row_text),
            requirements=requirements,
            copy_handling_mode="none" if not (copy_text and copy_text.strip()) else "manual_overlay_after_generation",
            copy_text=copy_text,
            base_prompt=first_mapped(row, col_map, "prompt"),
            status="待处理",
            selected=True,
        )
        # 单元格图片挂接（issue #101：任务带图统计用；C# 该路径不挂，python 扩展）
        for value, _source in refs:
            if isinstance(value, dict) and value.get("origin") == "cellimage":
                task.cell_image_name = os.path.splitext(os.path.basename(value["path"]))[0]
                task.cell_image_data = value.get("data") or b""
                task.has_cell_image = True
                break
        apply_main_link(task)
        task.generation_references = [
            ReferenceImage(
                asset_id=value.get("path") if isinstance(value, dict) else value,
                source=source,
            )
            for value, source in refs
        ]
        tasks.append(task)
    return sort_index


def add_tasks_from_unmapped_rows(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
) -> int:
    """C# AddTasksFromUnmappedRows:1707 — 无表头兜底路径。"""
    current_section = infer_initial_section(sheet_name, rows, 0)
    for i, row in enumerate(rows):
        row_text = join_row(row)
        section_marker = try_get_section_marker(row, row_text)
        if section_marker:
            current_section = section_marker
            continue
        if looks_like_repeated_header_row(row):
            continue
        if not looks_like_task_row(row, row_text, False):
            continue
        section = first_non_empty(current_section, infer_section(sheet_name, row_text))
        counters[section] = counters.get(section, 0) + 1
        section_idx = counters[section]
        sort_index += 1
        first = next((c for c in row if c and c.strip()), "")
        if first.startswith("同主图"):
            first = "同主图"
        name = clean_task_name(first if first.strip() else guess_name(row, row_text, section, section_idx))
        task = PlanTask(
            task_id=f"excel_{sort_index}",
            name=name,
            section=section,
            source_row=f"{sheet_name}!{i + 1}",
            sort_index=sort_index,
            section_index=section_idx,
            display_no=build_display_no(section, section_idx),
            output_type=infer_output_type(row_text, section),
            target_aspect_ratio=infer_aspect_ratio(section, "", row_text),
            requirements=sanitize_prompt_field(join_row(row)),
            status="待处理",
            selected=True,
        )
        apply_main_link(task)
        tasks.append(task)
    return sort_index


# ─── 入口（C# ImportXlsxPackage:1140 / ImportRowsForSmoke:1746）───────────────


def extract_tasks(parse_result: Dict[str, Any]):
    """C# ImportXlsxPackage 主循环。

    返回 (tasks, notes, scanned_rows)。
    口径：
    - should_skip_sheet（有设计表时只扫设计表+补充表）
    - ScannedRowCount = 过滤后各表 <row> 元素数之和
    - 图片注记 = imageLookup 键数（cellimages + drawing 锚点图）
    """
    tasks: List[PlanTask] = []
    notes: List[str] = []
    counters: Dict[str, int] = {}
    sort_index = 0
    scanned_rows = 0

    sheets: Dict[str, Any] = parse_result.get("sheets") or {}
    cell_images: Dict[str, Any] = parse_result.get("cell_images") or {}
    image_lookup = build_image_lookup(sheets, cell_images)
    has_design = any(is_design_plan_sheet_name(name) for name in sheets)

    for sheet_name, sheet_data in sheets.items():
        if should_skip_sheet(sheet_name, has_design):
            continue
        rows = sheet_data.rows()
        scanned_rows += len(rows)
        if not rows:
            continue
        sort_index = add_tasks_from_rows(tasks, rows, sheet_name, counters, sort_index, image_lookup)

    if image_lookup:
        notes.append(f"已抽取 Excel 单元格图片：{len(image_lookup)} 张。")
    return tasks, notes, scanned_rows


def build_plan_ir(source_file: str, parse_result: Dict[str, Any]) -> PlanIR:
    """构建 PlanIR（C# Report 口径：SheetCount/ScannedRowCount/ImportedTaskCount）。"""
    sheets = parse_result.get("sheets") or {}
    plan = PlanIR(source_file=source_file, sheet_count=len(sheets))
    tasks, notes, scanned_rows = extract_tasks(parse_result)
    plan.tasks = tasks
    plan.notes = notes
    plan.scanned_rows = scanned_rows
    # C# :1207-1210 — 有任务时追加 OpenXML 兜底注记
    if len(plan.tasks) > 0:
        plan.notes.append("已用 OpenXML 兜底读取 .xlsx，并自动识别任务字段。")
    return plan
