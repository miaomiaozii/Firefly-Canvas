# -*- coding: utf-8 -*-
"""Plan IR builder: 从 Excel 解析结果构建 Plan IR。

移植 C# AddTasksFromRows 逻辑 (Program.cs:1615-1694)。

设计原则：
- 与 C# AddTasksFromRows 逻辑对齐
- 保持确定性（相同输入 → 相同输出）
- 任务数量/依赖永远来自 Excel 解析器，LLM 不参与
"""
import re
from typing import List, Dict, Optional, Tuple, Any
from plan_ir import PlanIR, PlanTask, ReferenceImage


def build_plan_ir(
    source_file: str,
    parse_result: Dict[str, object],
) -> PlanIR:
    """从解析结果构建 Plan IR。

    Args:
        source_file: 原始文件名
        parse_result: parser.parse() 的返回结果

    Returns:
        PlanIR 对象
    """
    plan = PlanIR(
        source_file=source_file,
        sheet_count=len(parse_result.get("sheets", {})),
    )

    # 统计扫描行数（C# ScannedRowCount 的含义）
    total_rows = 0
    for sheet in parse_result.get("sheets", {}).values():
        total_rows += len(sheet.rows())
    plan.scanned_rows = total_rows

    # 提取任务（参考图映射：drawing 锚点行号 → 任务行，issue #52）
    tasks, unmapped_notes = extract_tasks(parse_result)
    plan.tasks = tasks
    plan.notes = list(unmapped_notes)

    return plan


def is_task_sheet(sheet_name: str, rows: List[List[str]]) -> bool:
    """判断是否为任务 sheet（vs 数据/参考 sheet）。
    
    任务 sheet 特征（严格判断，避免误判数据 sheet）：
    - 名称包含 "图片方案" 关键词（最明确的任务 sheet 标记）
    - 或者第一个 sheet 且包含任务序号模式
    """
    # 最严格：名称包含 "图片方案"
    if '图片方案' in sheet_name:
        return True
    
    # 排除明显的数据/参考 sheet
    data_sheet_keywords = ['详情', '评论', '竞品', '关键词', '分析', '数据', '参考']
    if any(kw in sheet_name for kw in data_sheet_keywords):
        return False
    
    # 检查是否有明确的 header row（包含多个任务相关列名）
    if rows and len(rows) > 2:
        # 检查前 3 行是否有 header row
        for i in range(min(3, len(rows))):
            row_text = ' '.join(rows[i])
            # Header row 通常包含多个关键词
            header_keywords = ['顺序', '要求', '内容', '要素', '示例', '任务', '名称', 'section']
            keyword_count = sum(1 for kw in header_keywords if kw in row_text)
            if keyword_count >= 2:  # 至少 2 个 header 关键词
                return True
    
    return False


def extract_tasks(parse_result: Dict[str, object]) -> Tuple[List[PlanTask], List[str]]:
    """从解析结果提取任务列表（对应 C# AddTasksFromRows 的调用循环）。

    Returns:
        (tasks, unmapped_notes)：未映射到任何任务行的参考图说明（C# 报告项）。
    """
    tasks: List[PlanTask] = []
    sort_index = 0
    counters: Dict[str, int] = {}
    unmapped_notes: List[str] = []

    for sheet_name, sheet_data in parse_result.get("sheets", {}).items():
        rows = sheet_data.rows()
        if not rows:
            continue

        # 只处理任务 sheet
        if not is_task_sheet(sheet_name, rows):
            continue

        # 参考图映射（issue #52 / C# AddOperationReferencesFromMappedCells）：
        # 按 drawing 锚点行号把图片关联到任务行；锚点行号是原始 sheet 行号，
        # 需通过 grid_row_numbers() 对齐到 rows() 过滤空行后的网格下标。
        row_numbers = sheet_data.grid_row_numbers()
        row_image_refs: Dict[int, List[Dict[str, Any]]] = {}
        for anchor in getattr(sheet_data, "image_anchors", []) or []:
            row_no = anchor.get("row")
            if not row_no or row_no not in row_numbers:
                continue
            row_image_refs.setdefault(row_numbers.index(row_no), []).append(anchor)
        for refs in row_image_refs.values():
            refs.sort(key=lambda a: (a.get("col") is None, a.get("col") or 0))

        sort_index = add_tasks_from_rows(tasks, rows, sheet_name, counters, sort_index, row_image_refs)

        # 报告未映射到任务行的图片（C# 对未匹配行会打印警告）；
        # asset_id 格式为「sheet!行号:rid」，取冒号后段才是 rid。
        mapped_rids = {
            ref.asset_id.rsplit(":", 1)[1]
            for task in tasks
            for ref in task.operation_references
            if ref.asset_id.count(":") >= 1
        }
        for anchor in getattr(sheet_data, "image_anchors", []) or []:
            rid = anchor.get("rid")
            if rid in mapped_rids:
                continue
            if anchor.get("row") is None:
                unmapped_notes.append(f"{sheet_name}: 图片 {rid} 使用绝对锚点，未关联到任务行")
            else:
                unmapped_notes.append(f"{sheet_name}: 图片 {rid} 位于行 {anchor['row']}，未匹配到任务行")

    return tasks, unmapped_notes


def add_tasks_from_rows(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
    image_lookup: Optional[Dict[int, List[Dict[str, Any]]]] = None,
) -> int:
    """对应 C# AddTasksFromRows。

    image_lookup: 网格行下标（rows 的 0-based 下标）→ 该行图片锚点列表
        （每项含 rid/row/col，row/col 为原始 1-based 行列）。用于把参考图
        关联到任务（C# AddOperationReferencesFromMappedCells 的数据级等价物）。
    """
    header_row = find_header_row(rows)
    if header_row < 0:
        # 无 header：兜底 unmapped rows
        add_tasks_from_unmapped_rows(tasks, rows, sheet_name, counters, sort_index)
        # 重新计算 sort_index
        sort_index = len(tasks)
        return sort_index

    headers = rows[header_row]
    header_map = map_headers(headers)
    start = header_row + 1
    current_section = infer_initial_section(sheet_name, rows, start)

    for i in range(start, len(rows)):
        row = rows[i]
        row_text = join_row(row)

        # Section marker
        section_marker = try_get_section_marker(row, row_text)
        if section_marker:
            current_section = section_marker
            continue

        # Skip repeated header
        if i != header_row and looks_like_repeated_header_row(row):
            continue

        # Skip non-task rows
        if not looks_like_task_row(row, row_text):
            continue

        # Extract mapped fields
        mapped_name = first_mapped(row, header_map, "name")
        mapped_requirements = join_mapped(row, header_map, "requirements")
        mapped_output_type = first_mapped(row, header_map, "outputType")
        row_lead = first_non_empty(row)
        mapped_section = normalize_section_name(first_mapped(row, header_map, "section"))

        # Section inference chain
        section = mapped_section
        if not section:
            # 检查是否是 A+ 任务：只有任务 ID 本身以 "A+" 开头才算 A+ 任务
            # 不能仅因为文本中包含 "A+" 就判断为 A+ 任务（主图任务也可能描述 A+ 用途）
            if row_lead.upper().startswith("A+"):
                section = "高级A+方案"
            else:
                # 主图任务
                section = "主图方案"
        if not section:
            section = current_section or infer_section(sheet_name, row_text) or ""

        if mapped_section:
            current_section = mapped_section
        # Update counters
        counters[section] = counters.get(section, 0) + 1
        sort_index += 1
        section_idx = counters[section]

        # Build requirements
        requirements = mapped_requirements
        if not requirements:
            requirements = build_fallback_requirements(row, header_map)
        ref_notes = join_reference_text_notes(row, headers, header_map, image_lookup)
        if ref_notes:
            requirements = join_distinct_lines(requirements, ref_notes)

        # Build task（外部 Excel 文本为不可信输入，统一过滤防 prompt injection）
        name = clean_text_for_prompt_injection(
            clean_task_name(mapped_name) or guess_name(row, row_text, section, section_idx)
        )
        task = PlanTask(
            task_id=f"excel_{sort_index}",
            name=name,
            section=section,
            source_row=f"{sheet_name}!{i + 1}",
            sort_index=sort_index,
            section_index=section_idx,
            display_no=build_display_no(section, section_idx),
            output_type=mapped_output_type or infer_output_type(row_text, section),
            source_aspect_ratio=first_mapped(row, header_map, "aspect"),
            requirements=clean_text_for_prompt_injection(requirements),
            copy_handling_mode="",  # set below
            copy_text=clean_text_for_prompt_injection(join_mapped(row, header_map, "copy")),
            overlay_plan="",
            base_prompt=clean_text_for_prompt_injection(first_mapped(row, header_map, "prompt")),
            base_negative_prompt="",
            same_as_main=False,
            linked_main_index=0,
            linked_main_label="",
            status="待处理",
            selected=True,
            last_failure_reason="",
            # 参考图映射（issue #52）：anchor 行 = 本任务行时关联；
            # asset_id 为「sheet!行号:rId」稳定标识（媒体字节在 parse_result.sheets
            # 的 images[rid]，Phase 3 媒体管道落地后替换为真实资源 ID）。
            operation_references=[
                ReferenceImage(
                    asset_id=f"{sheet_name}!{i + 1}:{anchor.get('rid')}",
                    source=f"Excel 内嵌图（{sheet_name} 行 {anchor.get('row')} 列 {anchor.get('col')}）",
                    url=None,
                )
                for anchor in (image_lookup or {}).get(i, [])
            ],
        )
        # Copy handling
        if task.copy_text:
            task.copy_handling_mode = "manual_overlay_after_generation"
        else:
            task.copy_handling_mode = "none"

        # SameAsMain / LinkedMain
        task.same_as_main = detect_same_as_main(task)
        task.linked_main_index = parse_linked_main_index(task)
        task.linked_main_label = (
            f"同主图 {task.linked_main_index}" if task.linked_main_index > 0
            else ("同主图" if task.same_as_main else "")
        )

        tasks.append(task)

    return sort_index


# ─── Header Detection ─────────────────────────────────────────────────────────

def find_header_row(rows: List[List[str]]) -> int:
    """查找 header row 索引（对应 C# FindHeaderRow）。返回 -1 表示未找到。"""
    # 在前 10 行中寻找有 2 个以上非空单元格的行
    for idx in range(min(10, len(rows))):
        row = rows[idx]
        non_empty = [c.strip() for c in row if c.strip()]
        if len(non_empty) >= 2:
            # 检查是否包含常见 header 关键词
            text = " ".join(non_empty).lower()
            if any(kw in text for kw in ["名称", "图片", "主图", "a+", "提示", "prompt", "类型", "output"]):
                return idx
    return -1


def map_headers(headers: List[str]) -> Dict[str, List[int]]:
    """映射 header 名称到列索引（对应 C# MapHeaders）。"""
    col_map: Dict[str, List[int]] = {
        "name": [], "requirements": [], "reference": [], "outputType": [], "section": [],
        "aspect": [], "prompt": [], "copy": [],
    }
    for idx, cell in enumerate(headers):
        cell_lower = cell.strip().lower()
        if not cell_lower:
            continue
        # name
        if any(kw in cell_lower for kw in ["名称", "图片名称", "name", "标题"]):
            col_map["name"].append(idx)
        # requirements
        elif any(kw in cell_lower for kw in ["要求", "需求", "说明", "requirements", "备注", "内容"]):
            col_map["requirements"].append(idx)
        # reference（参考图列：C# JoinReferenceTextNotes 的文本注记来源）
        elif any(kw in cell_lower for kw in ["参考图", "参考", "reference", "示例图"]):
            col_map["reference"].append(idx)
        # outputType
        elif any(kw in cell_lower for kw in ["类型", "output", "产出", "类别"]):
            col_map["outputType"].append(idx)
        # section
        elif any(kw in cell_lower for kw in ["分区", "section", "类别", "板块"]):
            col_map["section"].append(idx)
        # aspect
        elif any(kw in cell_lower for kw in ["比例", "aspect", "尺寸"]):
            col_map["aspect"].append(idx)
        # prompt
        elif any(kw in cell_lower for kw in ["提示", "prompt", "描述", "生成描述"]):
            col_map["prompt"].append(idx)
        # copy
        elif any(kw in cell_lower for kw in ["文案", "copy", "文字", "叠加文字"]):
            col_map["copy"].append(idx)
    return col_map


# ─── Row Classification ───────────────────────────────────────────────────────

def try_get_section_marker(row: List[str], row_text: str) -> Optional[str]:
    """检查是否为 section marker 行（对应 C# TryGetSectionMarker）。
    
    在基准 Excel 中，section 是通过行内容推断的，没有显式的 section marker 行。
    主图任务（1、主图...）→ "主图方案" section
    A+ 任务（A+1、...）→ "高级A+方案" section
    """
    # 这个函数在基准 Excel 中返回 None（没有显式 section marker）
    # Section 推断由 looks_like_task_row 和 infer_section 完成
    return None


def looks_like_repeated_header_row(row: List[str]) -> bool:
    """检查是否为重复 header 行（对应 C# LooksLikeRepeatedHeaderRow）。"""
    text = " ".join(c.strip().lower() for c in row if c.strip())
    # 如果一行包含多个常见 header 关键词，视为重复 header
    header_keywords = ["名称", "类型", "提示", "比例", "要求", "prompt", "output", "name"]
    matches = sum(1 for kw in header_keywords if kw in text)
    return matches >= 3


def looks_like_task_row(row: List[str], row_text: str) -> bool:
    """检查是否为任务行（对应 C# LooksLikeTaskRow）。
    
    任务行特征：
    - 第一列或第二列以数字+顿号开头（如 "1、主图"、"2、功能卖点"）
    - 或者以 "A+" + 数字开头（如 "A+1、"、"A+2"）
    - 不包含 "视频" 关键词（视频任务不算图片任务）
    """
    if not row_text.strip():
        return False
    
    # 排除视频任务
    if "视频" in row_text:
        return False
    
    # 检查前两列是否有任务序号模式
    for cell in row[:2]:
        cell = cell.strip()
        # 模式1: "1、主图" / "2、功能卖点" 等
        if re.match(r"^\d+[、,.．]\s*", cell):
            return True
        # 模式2: "A+1、" / "A+2" 等
        if re.match(r"^A\+\d+", cell, re.IGNORECASE):
            return True
    
    return False


# ─── Section Inference ────────────────────────────────────────────────────────

def infer_initial_section(sheet_name: str, rows: List[List[str]], start: int) -> str:
    """推断初始 section（对应 C# InferInitialSection）。"""
    # 从 sheet 名称推断
    section = infer_section(sheet_name, "")
    if section:
        return section
    # 从后续行内容推断
    for i in range(min(start + 5, len(rows))):
        row_text = " ".join(rows[i])
        section = infer_section(sheet_name, row_text)
        if section:
            return section
    return ""


def infer_section(sheet_name: str, row_text: str) -> str:
    """推断 section（对应 C# InferSection）。"""
    # 从 sheet name 推断
    if "图片方案" in sheet_name or "主图" in sheet_name:
        return "主图方案"
    if "A+" in sheet_name or "a+" in sheet_name.lower():
        return "高级A+方案"
    if "详情" in sheet_name:
        return "详情图"

    # 从行文本推断
    if row_text:
        if "A+" in row_text or "a+" in row_text:
            return "高级A+方案"
        if "主图" in row_text:
            return "主图方案"
        if "详情" in row_text:
            return "详情图"

    return ""


def normalize_section_name(name: str) -> str:
    """规范化 section 名称（对应 C# NormalizeSectionName）。"""
    if not name:
        return ""
    name = name.strip()
    if "主图" in name:
        return "主图"
    if "A+" in name or "a+" in name.lower():
        return "高级A+方案"
    if "详情" in name:
        return "详情图"
    return name


def infer_output_type(row_text: str, section: str) -> str:
    """推断 output type（对应 C# InferOutputType）。
    
    优先使用 section 作为 output type，避免因为 requirements/prompt 中包含 "A+" 字样
    而错误分类（例如主图任务的 requirements 可能描述 "A+ 用途"）。
    """
    # 优先使用 section（已在任务分类时正确推断）
    if section:
        if "主图" in section:
            return "主图"
        if "A+" in section or "高级" in section:
            return "高级A+方案"
        if "详情" in section:
            return "详情图"
    
    # fallback: 根据 row_text 推断
    if row_text:
        if "A+" in row_text or "a+" in row_text:
            return "高级A+方案"
        if "主图" in row_text:
            return "主图"
    
    return section or ""


# ─── Field Extraction Helpers ─────────────────────────────────────────────────

def first_mapped(row: List[str], col_map: Dict[str, List[int]], key: str) -> str:
    """获取映射字段的第一个非空值（对应 C# FirstMapped）。"""
    for idx in col_map.get(key, []):
        if idx < len(row) and row[idx].strip():
            return row[idx].strip()
    return ""


def join_mapped(row: List[str], col_map: Dict[str, List[int]], key: str) -> str:
    """获取映射字段的所有值并拼接（对应 C# JoinMapped）。"""
    parts = []
    for idx in col_map.get(key, []):
        if idx < len(row) and row[idx].strip():
            parts.append(row[idx].strip())
    return " ".join(parts)


def first_non_empty(row: List[str]) -> str:
    """获取行中第一个非空值（对应 C# FirstNonEmpty）。"""
    for cell in row:
        if cell and cell.strip():
            return cell.strip()
    return ""


def join_row(row: List[str]) -> str:
    """拼接行文本（对应 C# JoinRow）。"""
    return " ".join(c.strip() for c in row if c.strip())


def build_fallback_requirements(row: List[str], col_map: Dict[str, List[int]]) -> str:
    """构建 fallback requirements（对应 C# BuildFallbackRequirements）。"""
    # 用所有非空且非 name 列的值作为 requirements
    name_cols = set(col_map.get("name", []))
    parts = []
    for idx, cell in enumerate(row):
        if idx in name_cols:
            continue
        if cell and cell.strip():
            parts.append(cell.strip())
    return " ".join(parts[:5])  # 限制长度


def join_distinct_lines(text1: str, text2: str) -> str:
    """拼接两段文本，去重（对应 C# JoinDistinctLines）。"""
    if not text2:
        return text1
    if not text1:
        return text2
    lines1 = set(text1.split("\n"))
    lines2 = text2.split("\n")
    result = text1
    for line in lines2:
        if line not in lines1:
            result += "\n" + line
    return result


def join_reference_text_notes(
    row: List[str],
    headers: List[str],
    col_map: Dict[str, List[int]],
    image_lookup: Optional[Dict[int, List[Dict[str, Any]]]],
) -> str:
    """提取引用文本注释（对应 C# JoinReferenceTextNotes）。

    把「参考图」列里的单元格文本注记（如「参考竞品 A 的构图」）拼接进任务要求；
    无参考列或单元格全空时返回空串。
    """
    parts = [row[idx].strip() for idx in col_map.get("reference", []) if idx < len(row) and row[idx].strip()]
    return " ".join(parts)


# ─── Naming & Display ─────────────────────────────────────────────────────────

def clean_task_name(name: str) -> str:
    """清理任务名称（对应 C# CleanTaskName）。"""
    if not name:
        return ""
    # 去除引号、多余空格
    name = name.strip().strip("\"'")
    name = re.sub(r"\s+", " ", name)
    return name[:80]


def guess_name(row: List[str], row_text: str, section: str, section_idx: int) -> str:
    """猜测任务名称（对应 C# GuessName）。"""
    # 用第一列或前几列内容
    lead = first_non_empty(row)
    if lead:
        return lead[:60]
    # 用 section + 序号
    return f"{section} {section_idx}"


def build_display_no(section: str, section_idx: int) -> str:
    """构建显示编号（对应 C# BuildDisplayNo）。"""
    if "主图" in section:
        return f"主图 {section_idx}"
    if "A+" in section:
        return f"A+ 模块 {section_idx}"
    if "详情" in section:
        return f"详情图 {section_idx}"
    return f"{section} {section_idx}" if section else str(section_idx)


# ─── Relationship Detection ───────────────────────────────────────────────────

def detect_same_as_main(task: PlanTask) -> bool:
    """检测是否同主图（对应 C# DetectSameAsMain）。"""
    text = task.name + task.requirements + task.base_prompt
    return "同主图" in text


def parse_linked_main_index(task: PlanTask) -> int:
    """解析关联主图序号（对应 C# ParseLinkedMainIndex）。"""
    text = task.name + task.requirements
    match = re.search(r"同主图\s*(\d+)", text)
    if match:
        return int(match.group(1))
    return 0


# ─── Fallback Task Extraction ─────────────────────────────────────────────────

def clean_text_for_prompt_injection(text: str, max_length: int = 500) -> str:
    """清理文本以防止 prompt injection。"""
    if not text:
        return ""
    # 限制长度
    text = text[:max_length]
    # 移除控制字符（保留换行、制表符、空格）
    text = ''.join(c for c in text if c.isprintable() or c in '\n\t\r')
    # 移除可疑的模式（简化版，可根据需要扩展）
    suspicious_patterns = [
        r'(?i)(ignore|忽略).{0,50}(instruction|指令|指令)',
        r'(?i)(new|new)\s*instruction',
        r'(?i)(ignore|忽略)\s*all\s*(previous|prior|上述)',
    ]
    for pattern in suspicious_patterns:
        text = re.sub(pattern, '[filtered]', text)
    return text

def add_tasks_from_unmapped_rows(
    tasks: List[PlanTask],
    rows: List[List[str]],
    sheet_name: str,
    counters: Dict[str, int],
    sort_index: int,
):
    """从未映射行提取任务（对应 C# AddTasksFromUnmappedRows）。简化实现。"""
    # 简单策略：每个非空行都作为任务
    section = infer_section(sheet_name, "") or "主图"
    for i, row in enumerate(rows):
        row_text = join_row(row)
        if not row_text.strip():
            continue
        if looks_like_repeated_header_row(row):
            continue
        sort_index += 1
        counters[section] = counters.get(section, 0) + 1
        section_idx = counters[section]
        # 清理文本防止 prompt injection
        clean_name = clean_text_for_prompt_injection(clean_task_name(first_non_empty(row)) or f"任务 {sort_index}")
        clean_requirements = clean_text_for_prompt_injection(row_text[:200])
        task = PlanTask(
            task_id=f"excel_{sort_index}",
            name=clean_name,
            section=section,
            source_row=f"{sheet_name}!{i + 1}",
            sort_index=sort_index,
            section_index=section_idx,
            display_no=build_display_no(section, section_idx),
            output_type=infer_output_type(row_text, section),
            source_aspect_ratio="",
            requirements=clean_requirements,
            copy_handling_mode="none",
            copy_text="",
            overlay_plan="",
            base_prompt="",
            base_negative_prompt="",
            status="待处理",
            selected=True,
        )
        tasks.append(task)
