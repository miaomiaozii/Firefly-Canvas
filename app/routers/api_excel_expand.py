"""Excel 计划展开 API：将 Plan IR 原子展开为工作流节点。"""

import asyncio
import base64
import logging
import mimetypes
import os
import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from layout_engine import DeterministicLayout

from app.core import ProjectNotFound, RevisionConflict

logger = logging.getLogger(__name__)

router = APIRouter()

# 获取主模块引用（用于访问 project_store_call）
_app_main = sys.modules.get(os.environ.get('FIREFLY_MAIN_MODULE', '')) or sys.modules.get('main') or sys.modules['__main__']


class ExpandPlanRequest(BaseModel):
    """展开计划请求。"""
    project_id: str  # 项目 ID
    plan: Dict[str, Any]  # Plan IR 的 to_dict() 输出


@router.post("/api/excel/expand-plan")
async def expand_plan(request: Request, payload: ExpandPlanRequest) -> Dict[str, Any]:
    """将 Plan IR 原子展开为工作流节点。

    实现：
    - 接收 project_id 和 Plan IR
    - 获取当前画布状态
    - 使用确定性布局器计算节点位置
    - 生成画布操作序列（节点、边）
    - 原子性地应用所有操作
    """
    project_id = payload.project_id
    plan = payload.plan

    # 验证 Plan IR 结构
    if not plan or "tasks" not in plan:
        raise HTTPException(status_code=400, detail="无效的 Plan IR")

    tasks = plan.get("tasks", [])

    if not tasks:
        raise HTTPException(status_code=400, detail="Plan IR 中没有任务")

    # 0. 上传参考图字节为项目资产（替换假的 asset_id 为真实 ID）
    await _upload_reference_images(project_id, tasks)

    # 1. 获取当前画布（项目不存在 → 404）
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="项目不存在")
    canvas = opened['canvas']
    base_revision = int(canvas.get('revision') or 0)

    # 2. 使用确定性布局器生成操作
    layout = DeterministicLayout()
    operations = layout.generate_canvas_operations(plan, canvas)

    # 3. 构建 mutations（节点 + 边）
    mutations = []

    # 创建节点
    for op in operations:
        if op['type'] == 'create_node':
            mutations.append({
                'op': 'upsert_node',
                'node': {
                    'id': op['node_id'],
                    'type': op['node_type'],
                    'title': op['label'],
                    'x': op['x'],
                    'y': op['y'],
                    'w': op['width'],
                    'h': op['height'],
                    'excel_task_id': op.get('excel_task_id', ''),
                    'excel_section': op.get('excel_section', ''),
                    **op.get('extra_data', {})
                }
            })

    # 创建边（edges）
    for op in operations:
        if op['type'] == 'create_edge':
            mutations.append({
                'op': 'upsert_edge',
                'edge': {
                    'id': f"{op['from']}-{op['to']}",
                    'from': op['from'],
                    'to': op['to'],
                    'kind': 'flow',
                    'label': op.get('label', '')
                }
            })

    # 4. 原子性应用所有 mutations（版本冲突 → 409）
    if mutations:
        try:
            await asyncio.to_thread(
                _app_main.project_store_call,
                'apply_mutations',
                project_id,
                mutations,
                base_revision=base_revision
            )
        except RevisionConflict:
            raise HTTPException(status_code=409, detail="画布已被其他操作修改，请刷新后重试")
        await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)

    # 5. 统计结果
    nodes_created = sum(1 for op in operations if op['type'] == 'create_node')
    edges_created = sum(1 for op in operations if op['type'] == 'create_edge')

    # 6. 计算新建节点的包围盒（用于前端视口总览）
    bounds = _calculate_bounds(operations)

    return {
        "success": True,
        "message": f"展开成功：{nodes_created} 节点, {edges_created} 边",
        "project_id": project_id,
        "nodes_created": nodes_created,
        "edges_created": edges_created,
        "bounds": bounds,
    }


async def _upload_reference_images(project_id: str, tasks: List[Dict[str, Any]]) -> None:
    """将 Plan IR 中携带的参考图字节上传为项目资产。

    遍历所有任务的 generation_references，对有 data（base64）字段的引用：
    1. 解码字节
    2. 通过 project_store_call('import_media') 上传为项目资产
    3. 用真实 asset_id 替换假的 asset_id（原为 maps/xxx.png 路径）
    4. 清除 data 字段（避免重复上传）

    无 data 字段的引用（如已有真实 asset_id）保持不变。
    """
    for task in tasks:
        refs = task.get("generation_references", [])
        if not refs:
            continue
        for ref in refs:
            data_b64 = ref.get("data")
            if not data_b64:
                continue
            try:
                data = base64.b64decode(data_b64)
            except Exception:
                continue
            if not data:
                continue
            # 从 asset_id（路径）提取文件名
            fake_id = ref.get("asset_id", "")
            filename = Path(fake_id).name if fake_id else "reference.png"
            # 推断 MIME 类型
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type = "image/png"
            try:
                record = await asyncio.to_thread(
                    _app_main.project_store_call,
                    'import_media',
                    project_id,
                    data,
                    filename,
                    mime_type,
                    {"source": ref.get("source", "Excel参考图")}
                )
                # 用真实 asset_id 替换（不设置 url，避免被项目存储校验为外部引用）
                ref["asset_id"] = record.get("asset_id", "")
            except Exception as e:
                # 上传失败不阻塞展开，节点仍会创建（空节点）
                logger.warning(f"[ExcelExpand] 参考图上传失败: {filename}: {e}")
            finally:
                ref["data"] = None  # 清除，避免重复上传


def _calculate_bounds(operations: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """计算新建节点的包围盒。"""
    if not operations:
        return None

    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')

    for op in operations:
        if op['type'] != 'create_node':
            continue
        x = float(op.get('x', 0))
        y = float(op.get('y', 0))
        w = float(op.get('width', 0))
        h = float(op.get('height', 0))
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x + w)
        max_y = max(max_y, y + h)

    if min_x == float('inf'):
        return None

    return {
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
    }
