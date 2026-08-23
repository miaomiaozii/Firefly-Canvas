"""Excel 导入结果收集 API：打标 + 批量导出。"""

import asyncio
import os
import sys
import json
import zipfile
import io
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

from app.core import ProjectNotFound, RevisionConflict

router = APIRouter()


async def _open_project_canvas(project_id: str) -> Dict[str, Any]:
    """打开项目并返回 (canvas, base_revision)；项目不存在 → 404。"""
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="项目不存在")
    canvas = opened['canvas']
    base_revision = int(canvas.get('revision') or 0)
    return canvas, base_revision


async def _apply_mutations_checked(project_id: str, mutations: List[Dict[str, Any]], base_revision: int) -> None:
    """应用 mutations；版本冲突 → 409。"""
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

# 获取主模块引用
_app_main = sys.modules.get(os.environ.get('FIREFLY_MAIN_MODULE', '')) or sys.modules.get('main') or sys.modules['__main__']


class TagNodeRequest(BaseModel):
    """打标请求。"""
    project_id: str
    node_id: str
    curated: bool = True
    tags: List[str] = []


class CollectNodesRequest(BaseModel):
    """收集节点请求。"""
    project_id: str
    node_ids: List[str]
    include_unselected: bool = False


@router.post("/api/excel/tag-node")
async def tag_node(payload: TagNodeRequest) -> Dict[str, Any]:
    """给结果节点打标（curated + tags）。"""
    project_id = payload.project_id
    node_id = payload.node_id

    # 打开项目
    canvas, base_revision = await _open_project_canvas(project_id)

    # 找到节点
    node = None
    for n in canvas.get('nodes', []):
        if n.get('id') == node_id:
            node = n
            break

    if not node:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")

    # 更新节点
    node['curated'] = payload.curated
    node['tags'] = payload.tags
    node['updated_at'] = _app_main.now_ms()

    # 应用 mutation
    mutations = [
        {
            'op': 'upsert_node',
            'node': node
        }
    ]

    await _apply_mutations_checked(project_id, mutations, base_revision)
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)

    return {
        'success': True,
        'message': f'节点 {node_id} 已打标',
        'node_id': node_id,
        'curated': payload.curated,
        'tags': payload.tags
    }


@router.post("/api/excel/collect-nodes")
async def collect_nodes(payload: CollectNodesRequest) -> Response:
    """收集节点并导出 zip。"""
    project_id = payload.project_id
    node_ids = payload.node_ids

    # 打开项目
    canvas, base_revision = await _open_project_canvas(project_id)

    collected_nodes = []

    for node_id in node_ids:
        for node in canvas.get('nodes', []):
            if node.get('id') == node_id:
                # 只收集 curated 的节点（或 include_unselected 时收集所有）
                if payload.include_unselected or node.get('curated'):
                    collected_nodes.append(node)

    # 位置：现有内容下方（避免与已有节点重叠）
    max_y = 0.0
    for n in canvas.get('nodes', []):
        try:
            ny = float(n.get('y', 0) or 0) + float(n.get('h', n.get('height', 0)) or 0)
        except (TypeError, ValueError):
            continue
        if ny > max_y:
            max_y = ny

    # 创建收集节点（确定性 ID，重复收集时更新同一节点；
    # 用 smart-prompt 文本节点保证前端可渲染，画布节点模型用 w/h/title 键）
    collected_at = int(_app_main.now_ms())
    collect_node = {
        'id': 'excel_collect',
        'type': 'smart-prompt',
        'title': 'Excel 收集结果',
        'x': 80,
        'y': max_y + 80,
        'w': 300,
        'h': 200,
        'text': f"Excel 收集结果\n收集节点数：{len(collected_nodes)}\n时间戳：{collected_at}",
        'excel_collect_data': {
            'node_ids': node_ids,
            'collected_at': collected_at,
            'include_unselected': payload.include_unselected
        }
    }

    # 应用 mutation
    mutations = [
        {
            'op': 'upsert_node',
            'node': collect_node
        }
    ]

    await _apply_mutations_checked(project_id, mutations, base_revision)
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)

    # 创建 zip 文件
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # 添加 nodes.json
        zipf.writestr('nodes.json', json.dumps(collected_nodes, ensure_ascii=False, indent=2))

    # 返回 zip
    buffer.seek(0)
    return Response(
        content=buffer.read(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename=excel_import_results.zip'}
    )
