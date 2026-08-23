"""确定性布局器：根据 Plan IR 计算节点位置，避免重叠。

三分区布局：
- 左区：方案节点（Excel 计划）
- 中区：Agent 节点
- 右区：任务节点（按 section 分组，主图/A+）

设计原则：
- 确定性：相同输入 → 相同输出
- 紧凑布局：节点之间有足够间距
- 无重叠：所有节点不重叠
"""

from typing import Dict, List, Any, Tuple
from dataclasses import dataclass


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
    PLAN_NODE_W = 280.0  # 方案节点宽度
    PLAN_NODE_H = 200.0  # 方案节点高度
    AGENT_NODE_W = 300.0  # Agent 节点宽度
    AGENT_NODE_H = 360.0  # Agent 节点高度
    TASK_NODE_W = 240.0  # 任务节点宽度
    TASK_NODE_H = 160.0  # 任务节点高度
    HORIZONTAL_GAP = 60.0  # 水平间距
    VERTICAL_GAP = 30.0  # 垂直间距
    MARGIN = 80.0  # 边距
    ZONE_GAP = 100.0  # 分区间距

    def generate_canvas_operations(self, plan: Dict[str, Any], existing_canvas: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """生成画布操作序列（节点、边）。

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
        plan_id = "excel_plan"
        agent_id = "excel_agent"

        # 1. 创建方案节点（左区）
        operations.append({
            "type": "create_node",
            "node_id": plan_id,
            "node_type": "smart-prompt",
            "x": self.MARGIN + offset_x,
            "y": self.MARGIN + offset_y,
            "width": self.PLAN_NODE_W,
            "height": self.PLAN_NODE_H,
            "label": "Excel 导入方案",
            "excel_task_id": "",
            "excel_section": "plan",
            "extra_data": {
                "text": f"来源：{plan.get('source_file', 'Excel')}\n任务数：{len(tasks)}\nsheets: {plan.get('sheet_count', 0)}\n扫描行数：{plan.get('scanned_rows', 0)}",
                "excel_plan_data": plan,
            },
        })

        # 2. 创建 Agent 节点（中区）
        agent_x = self.MARGIN + self.PLAN_NODE_W + self.ZONE_GAP + offset_x
        operations.append({
            "type": "create_node",
            "node_id": agent_id,
            "node_type": "smart-agent",
            "x": agent_x,
            "y": self.MARGIN + offset_y,
            "width": self.AGENT_NODE_W,
            "height": self.AGENT_NODE_H,
            "label": "Excel 导入 Agent",
            "excel_task_id": "",
            "excel_section": "agent",
            "extra_data": {
                "agent_id": agent_id,
            },
        })

        # 3. 创建任务节点（右区，按 section 分组）
        sections = self._group_tasks_by_section(tasks)
        task_x = agent_x + self.AGENT_NODE_W + self.ZONE_GAP
        current_y = self.MARGIN + offset_y

        for section, section_tasks in sections.items():
            # Section 标题位置
            section_y = current_y

            # 计算 section 内的网格布局
            row = 0
            col = 0
            max_cols = 2  # 每 section 最多 2 列

            # 列距：任务宽 + 任务→结果间距(20) + 结果宽(200) + 列间距
            # （避免第一列的结果节点与第二列的任务节点水平重叠）
            col_stride = self.TASK_NODE_W + 20 + 200 + self.HORIZONTAL_GAP

            for task in section_tasks:
                # 任务节点
                task_id = task["task_id"]
                result_id = f"{task_id}-result"

                tx = task_x + col * col_stride
                ty = section_y + row * (self.TASK_NODE_H + self.VERTICAL_GAP)

                operations.append({
                    "type": "create_node",
                    "node_id": task_id,
                    "node_type": "agent-task",
                    "x": tx,
                    "y": ty,
                    "width": self.TASK_NODE_W,
                    "height": self.TASK_NODE_H,
                    "label": f"{task.get('display_no', '')} {task.get('name', '')}",
                    "excel_task_id": task_id,
                    "excel_section": section,
                    "extra_data": {
                        "agent_id": agent_id,
                        "task_id": task_id,
                        "kind": "image",
                        "status": "queued",
                        "prompt": task.get('base_prompt', ''),
                        "requirements": task.get('requirements', ''),
                        "section": section,
                        "section_index": task.get('section_index', 0),
                        "source_row": task.get('source_row', ''),
                        "excel_task_data": task,
                    },
                })

                # 结果节点
                rx = tx + self.TASK_NODE_W + 20
                operations.append({
                    "type": "create_node",
                    "node_id": result_id,
                    "node_type": "smart-image",
                    "x": rx,
                    "y": ty,
                    "width": 200,
                    "height": self.TASK_NODE_H,
                    "label": f"结果 {task.get('display_no', '')}",
                    "excel_task_id": task_id,
                    "excel_section": section,
                    "extra_data": {
                        "agent_id": agent_id,
                        "agent_task_id": task_id,
                        "outputKind": "image",
                        "pending": 1,
                        "running": False,
                        "excel_task_data": task,
                    },
                })

                # 移动到下一位置
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1

            # 更新 y 坐标到下一 section
            rows = (len(section_tasks) + max_cols - 1) // max_cols
            current_y += rows * (self.TASK_NODE_H + self.VERTICAL_GAP) + 40

        # 4. 创建边（connections）
        # Plan -> Agent
        operations.append({
            "type": "create_edge",
            "from": plan_id,
            "to": agent_id,
            "label": "",
        })

        # Agent -> 每个任务
        for task in tasks:
            task_id = task["task_id"]
            result_id = f"{task_id}-result"

            # Agent -> Task
            operations.append({
                "type": "create_edge",
                "from": agent_id,
                "to": task_id,
                "label": "",
            })

            # Task -> Result
            operations.append({
                "type": "create_edge",
                "from": task_id,
                "to": result_id,
                "label": "",
            })

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
