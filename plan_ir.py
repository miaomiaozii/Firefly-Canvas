# -*- coding: utf-8 -*-
"""Plan IR（结构化任务清单）。

参考历史 C# 工具 ImagePlanDesignerP0 的 ImageTask 类（Program.cs:406-436）。

原则：
- 任务数量/依赖永远来自 Excel 解析器
- LLM 只润色 + 歧义追问，禁止 LLM 编造任务
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ReferenceImage:
    """参考图（操作参考 / 生成参考）。"""
    asset_id: str
    source: str  # 来源描述
    url: Optional[str] = None


@dataclass
class PlanTask:
    """计划任务（从 Excel 解析而来）。"""
    # 基础标识
    task_id: str = ""
    name: str = ""
    section: str = ""
    source_row: str = ""  # sheet_name!row_number

    # 排序
    sort_index: int = 0
    section_index: int = 0
    display_no: str = ""

    # 内容
    output_type: str = ""  # 主图 / 高级A+方案 / ...
    source_aspect_ratio: str = ""
    requirements: str = ""
    copy_handling_mode: str = "none"  # none / manual_overlay_after_generation
    copy_text: str = ""
    overlay_plan: str = ""
    base_prompt: str = ""
    base_negative_prompt: str = ""

    # 关系
    same_as_main: bool = False
    linked_main_index: int = 0
    linked_main_label: str = ""

    # 状态
    status: str = "待处理"
    selected: bool = True
    last_failure_reason: str = ""

    # 参考图
    operation_references: List[ReferenceImage] = field(default_factory=list)
    generation_references: List[ReferenceImage] = field(default_factory=list)


@dataclass
class PlanIR:
    """计划 IR（整个 Excel 导入的结构化表示）。"""
    source_file: str  # 源文件路径
    sheet_count: int = 0
    scanned_rows: int = 0
    tasks: List[PlanTask] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于 API 响应 / 持久化）。"""
        return {
            "source_file": self.source_file,
            "sheet_count": self.sheet_count,
            "scanned_rows": self.scanned_rows,
            "tasks": [
                {
                    "task_id": t.task_id,
                    "name": t.name,
                    "section": t.section,
                    "source_row": t.source_row,
                    "sort_index": t.sort_index,
                    "section_index": t.section_index,
                    "display_no": t.display_no,
                    "output_type": t.output_type,
                    "source_aspect_ratio": t.source_aspect_ratio,
                    "requirements": t.requirements,
                    "copy_handling_mode": t.copy_handling_mode,
                    "copy_text": t.copy_text,
                    "base_prompt": t.base_prompt,
                    "same_as_main": t.same_as_main,
                    "linked_main_index": t.linked_main_index,
                    "linked_main_label": t.linked_main_label,
                    "status": t.status,
                    "operation_references": [
                        {
                            "asset_id": r.asset_id,
                            "source": r.source,
                            "url": r.url
                        }
                        for r in t.operation_references
                    ],
                    "generation_references": [
                        {
                            "asset_id": r.asset_id,
                            "source": r.source,
                            "url": r.url
                        }
                        for r in t.generation_references
                    ],
                }
                for t in self.tasks
            ],
            "notes": self.notes,
        }

    def summary(self) -> Dict[str, Any]:
        """返回摘要统计。"""
        main_count = sum(1 for t in self.tasks if "主图" in t.output_type)
        aplus_count = sum(1 for t in self.tasks if "A+" in t.output_type or "高级" in t.output_type)
        return {
            "total_tasks": len(self.tasks),
            "main_image_count": main_count,
            "aplus_count": aplus_count,
            "by_section": self._group_by_section(),
        }

    def _group_by_section(self) -> Dict[str, int]:
        """按 section 分组统计。"""
        sections = {}
        for t in self.tasks:
            key = t.section or "未知分区"
            sections[key] = sections.get(key, 0) + 1
        return sections


# 注意：任务 ID 由调用方根据 sort_index 生成，格式为 excel_{sort_index}
