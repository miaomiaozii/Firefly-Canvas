"""确定性布局器：根据 Plan IR 计算节点位置，避免重叠。

每任务布局：
- 提示词节点（smart-prompt）：text = task.base_prompt
- 生图节点（smart-image）：runSettings{model, aspect_ratio, prompt} + pending=1
- 参考图节点（smart-image，可选）：从 task.generation_references 创建
- 边：提示词 → 生图，参考图 → 生图

设计原则：
- 确定性：相同输入 → 相同输出
- 紧凑布局：节点之间有足够间距
- 无重叠：所有节点不重叠
"""

from typing import Dict, List, Any, Tuple
from dataclasses import dataclass

# 默认生图模型（nano-banana 系列）
DEFAULT_IMAGE_MODEL = "nano-banana-2"


@dataclass
class NodePosition:
    """节点位置。"""
    task_id: str
    x: float
    y: float
    width: float
    height: float


class DeterministicLayout:
    """确定性布局器。"""

    # 布局参数
    PROMPT_NODE_W = 280.0  # 提示词节点宽度
    PROMPT_NODE_H = 160.0  # 提示词节点高度
    IMAGE_NODE_W = 240.0   # 生图节点宽度
    IMAGE_NODE_H = 160.0   # 生图节点高度
    REF_NODE_W = 200.0     # 参考图节点宽度
    REF_NODE_H = 160.0     # 参考图节点高度
    HORIZONTAL_GAP = 60.0  # 水平间距（提示词→生图）
    VERTICAL_GAP = 30.0    # 垂直间距
    MARGIN = 80.0          # 边距
    ZONE_GAP = 100.0       # 分区间距（已有画布偏移用）

    def generate_canvas_operations(self, plan: Dict[str, Any], existing_canvas: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """生成画布操作序列（节点、边）。

        每任务产出：
        - 1 个 smart-prompt 节点（提示词）
        - 1 个 smart-image 节点（生图，带 runSettings + pending=1）
        - 1 条边（提示词 → 生图）

        Args:
            plan: Plan IR 的 to_dict() 输出
            existing_canvas: 现有画布（用于避免重叠，可选）

        Returns:
            操作序列列表
        """
        tasks = plan.get("tasks", [])
        if not tasks:
            return []

        # 计算起始偏移量（避免与现有节点重叠）
        offset_x, offset_y = self._calculate_offset(existing_canvas)

        operations = []

        # 按 section 分组，每组一个网格区域
        sections = self._group_tasks_by_section(tasks)
        current_y = self.MARGIN + offset_y

        for section, section_tasks in sections.items():
            # 计算 section 内的网格布局
            row = 0
            col = 0
            max_cols = 2  # 每 section 最多 2 列

            # 列距：提示词宽 + 提示词→生图间距 + 生图宽 + 列间距
            col_stride = (self.PROMPT_NODE_W + self.HORIZONTAL_GAP +
                          self.IMAGE_NODE_W + self.HORIZONTAL_GAP)

            # 先计算每个任务的行高，然后按行分组，每行使用最大行高
            # 行高 = 提示词高 + 参考图总数 * (参考图高 + 间距)
            task_row_heights = []
            for task in section_tasks:
                refs = task.get("generation_references", [])
                num_refs = len(refs)
                row_height = self.PROMPT_NODE_H
                if num_refs > 0:
                    row_height += num_refs * (self.REF_NODE_H + self.VERTICAL_GAP)
                task_row_heights.append(row_height)

            # 计算每行的最大行高
            row_heights = []
            for r in range((len(section_tasks) + max_cols - 1) // max_cols):
                start = r * max_cols
                end = min(start + max_cols, len(section_tasks))
                row_max = max(task_row_heights[start:end])
                row_heights.append(row_max)

            # 计算每行的起始 y 坐标
            row_y_positions = []
            y = current_y
            for rh in row_heights:
                row_y_positions.append(y)
                y += rh + self.VERTICAL_GAP

            # 现在按行放置任务
            row = 0
            col = 0
            for idx, task in enumerate(section_tasks):
                task_id = task["task_id"]
                prompt_id = f"{task_id}-prompt"
                image_id = f"{task_id}-image"

                refs = task.get("generation_references", [])
                row_height = task_row_heights[idx]

                # 提示词节点 x
                px = self.MARGIN + offset_x + col * col_stride
                # 生图节点 x（提示词右侧）
                ix = px + self.PROMPT_NODE_W + self.HORIZONTAL_GAP
                # y 坐标（使用该行的起始 y）
                ty = row_y_positions[row]

                # 1. 提示词节点（smart-prompt）
                operations.append({
                    "type": "create_node",
                    "node_id": prompt_id,
                    "node_type": "smart-prompt",
                    "x": px,
                    "y": ty,
                    "width": self.PROMPT_NODE_W,
                    "height": self.PROMPT_NODE_H,
                    "label": f"{task.get('display_no', '')} {task.get('name', '')}",
                    "excel_task_id": task_id,
                    "excel_section": section,
                    "extra_data": {
                        "text": task.get('base_prompt') or task.get('requirements', ''),
                        "excel_task_data": task,
                    },
                })

                # 2. 生图节点（smart-image）
                aspect_ratio = task.get('target_aspect_ratio', '') or '1:1'
                operations.append({
                    "type": "create_node",
                    "node_id": image_id,
                    "node_type": "smart-image",
                    "x": ix,
                    "y": ty,
                    "width": self.IMAGE_NODE_W,
                    "height": self.IMAGE_NODE_H,
                    "label": f"生成 {task.get('display_no', '')}",
                    "excel_task_id": task_id,
                    "excel_section": section,
                    "extra_data": {
                        "runSettings": {
                            "model": DEFAULT_IMAGE_MODEL,
                            "aspect_ratio": aspect_ratio,
                            "prompt": task.get('base_prompt') or task.get('requirements', ''),
                        },
                        "pending": 1,
                        "running": False,
                        "outputKind": "image",
                        "excel_task_data": task,
                    },
                })

                # 3. 边：提示词 → 生图
                operations.append({
                    "type": "create_edge",
                    "from": prompt_id,
                    "to": image_id,
                    "label": "",
                })

                # 4. 参考图节点（如果有）
                for i, ref in enumerate(refs):
                    ref_id = f"{task_id}-ref-{i}"
                    ref_x = px
                    ref_y = ty + (i + 1) * (self.REF_NODE_H + self.VERTICAL_GAP)

                    # 参考图节点（smart-image，传图形态）
                    operations.append({
                        "type": "create_node",
                        "node_id": ref_id,
                        "node_type": "smart-image",
                        "x": ref_x,
                        "y": ref_y,
                        "width": self.REF_NODE_W,
                        "height": self.REF_NODE_H,
                        "label": f"参考图 {i + 1}",
                        "excel_task_id": task_id,
                        "excel_section": section,
                        "extra_data": {
                            "images": [{
                                "asset_id": ref.get("asset_id", ""),
                                "kind": "image",
                                "name": ref.get("source", f"ref-{i}"),
                            }],
                            "outputKind": "image",
                            "excel_ref_index": i,
                            "excel_task_data": task,
                        },
                    })

                    # 边：参考图 → 生图
                    operations.append({
                        "type": "create_edge",
                        "from": ref_id,
                        "to": image_id,
                        "label": "",
                    })

                # 移动到下一位置
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1

            # 更新 y 坐标到下一 section
            current_y = y + 40

        return operations

    def _group_tasks_by_section(self, tasks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """按 section 分组任务。"""
        sections = {}
        for task in tasks:
            section = task.get("section", "未分类")
            if section not in sections:
                sections[section] = []
            sections[section].append(task)
        return sections

    def _calculate_offset(self, existing_canvas: Dict[str, Any] = None) -> Tuple[float, float]:
        """计算布局偏移量，避免与现有节点重叠。"""
        if not existing_canvas or not existing_canvas.get("nodes"):
            return 0.0, 0.0

        nodes = existing_canvas["nodes"]
        if not nodes:
            return 0.0, 0.0

        # 计算现有节点的最大边界
        max_x = max_y = 0.0
        for node in nodes:
            # 画布节点模型用 w/h 键（兼容 width/height 历史键名）
            x = float(node.get("x", 0) or 0)
            y = float(node.get("y", 0) or 0)
            w = float(node.get("w", node.get("width", 0)) or 0)
            h = float(node.get("h", node.get("height", 0)) or 0)
            max_x = max(max_x, x + w)
            max_y = max(max_y, y + h)

        # 在现有节点边界外放置新内容
        offset_x = max(max_x + self.ZONE_GAP, self.MARGIN)
        offset_y = max(max_y + self.ZONE_GAP, self.MARGIN)

        return offset_x, offset_y
