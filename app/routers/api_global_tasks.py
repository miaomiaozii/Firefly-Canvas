"""Router: /api/global-tasks — 全局跨项目任务总控台（issue #262）。

数据面（全只读，对齐 ADR 0021 租户边界，不新增 ops.db 租户）：
- app/core.py CANVAS_TASKS：内存内飞任务（queued/running + 本进程终态），实时 ≤2s 轮询源；
- 每项目 canvas.history：持久化终态任务（tasks 租户语义随画布快照落盘，ADR 0017/#234）；
- 每项目 ops.db usage_turns：usage 租户（#258）成本/token，经 result_node_id 归因到产物节点。

逐任务取消复用 /api/project-library/{pid}/image-generation-tasks/{tid}/cancel
（前端 controlProjectImageTask 的同一端点），本路由不新增取消面。
"""
from __future__ import annotations
import os as _os
import sys as _sys
import time
from typing import Any, Dict, List

_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

from fastapi import APIRouter, Query

from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK

router = APIRouter()

ACTIVE_STATUSES = ('queued', 'running')
PROMPT_PREVIEW_MAX = 120
TASK_ROW_LIMIT_MAX = 200


def _as_ms(value: Any) -> int:
    """created_at 可能是 epoch 秒（CANVAS_TASKS）或 epoch 毫秒（history 记录），统一为毫秒。"""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if number <= 0:
        return 0
    if number < 1e12:
        number *= 1000
    return int(number)


def _kind_from_type(task_type: str) -> str:
    task_type = str(task_type or '')
    if 'video' in task_type:
        return 'video'
    if 'local-edit' in task_type or task_type == 'local_edit':
        return 'local-edit'
    if 'image' in task_type:
        return 'image'
    return 'other'


def _status_name(raw: Any) -> str:
    status = str(raw or '').strip().lower()
    if status in ('pending', 'in_progress', 'processing', 'pending_task'):
        return 'queued'
    return status or 'unknown'


def _row_from_live(task_id: str, task: Dict[str, Any], projects: Dict[str, Dict[str, Any]],
                   usage_by_node: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    status = _status_name(task.get('status'))
    created_at = _as_ms(task.get('created_at'))
    finished_at = None
    duration_sec = None
    if status in ACTIVE_STATUSES:
        duration_sec = max(0.0, (time.time() * 1000 - created_at) / 1000) if created_at else None
    else:
        finished_at = _as_ms(task.get('updated_at')) or None
        if created_at and finished_at:
            duration_sec = max(0.0, (finished_at - created_at) / 1000)
    route = task.get('route') if isinstance(task.get('route'), dict) else {}
    request_payload = task.get('request_payload') if isinstance(task.get('request_payload'), dict) else {}
    provider = str(task.get('provider_id') or route.get('provider_id') or request_payload.get('provider_id') or '')
    model = str(task.get('model') or route.get('model') or request_payload.get('model') or '')
    result_node_id = str(task.get('result_node_id') or request_payload.get('result_node_id') or '')
    row = {
        'task_id': str(task_id),
        'project_id': str(task.get('project_id') or ''),
        'kind': _kind_from_type(task.get('type')),
        'status': status,
        'provider': provider,
        'provider_name': str(route.get('provider_name') or ''),
        'model': model,
        'prompt': str(request_payload.get('prompt') or task.get('prompt') or '')[:PROMPT_PREVIEW_MAX],
        'created_at': created_at,
        'finished_at': finished_at,
        'duration_sec': round(duration_sec, 3) if duration_sec is not None else None,
        'error': str(task.get('error') or ''),
        'result_node_id': result_node_id,
        'cost_usd': None,
        'tokens': None,
        'cancellable': (
            str(task.get('type') or '') == 'project-online-image'
            and status in ACTIVE_STATUSES
            and not task.get('cancel_requested')
            and str(task.get('project_id') or '') in projects
        ),
    }
    _attach_usage(row, usage_by_node)
    _attach_project(row, projects)
    return row


def _row_from_history(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(record.get('task_id') or record.get('taskId') or '')
    if not task_id:
        return None
    status = _status_name(record.get('status'))
    created_at = _as_ms(record.get('created_at') or record.get('createdAt'))
    finished_at = _as_ms(record.get('completed_at')) or None
    duration_sec = None
    if record.get('runMs') is not None:
        try:
            duration_sec = max(0.0, float(record.get('runMs'))) / 1000
        except (TypeError, ValueError):
            duration_sec = None
    elif created_at and finished_at:
        duration_sec = max(0.0, (finished_at - created_at) / 1000)
    route = record.get('route') if isinstance(record.get('route'), dict) else {}
    return {
        'task_id': task_id,
        'project_id': str(project_id),
        'kind': _kind_from_type(record.get('type')),
        'status': status,
        'provider': str(record.get('actual_provider_id') or record.get('provider_id') or ''),
        'provider_name': str(record.get('actual_provider_name') or route.get('provider_name') or ''),
        'model': str(record.get('actual_model') or record.get('model') or ''),
        'prompt': str(record.get('prompt') or '')[:PROMPT_PREVIEW_MAX],
        'created_at': created_at,
        'finished_at': finished_at,
        'duration_sec': round(duration_sec, 3) if duration_sec is not None else None,
        'error': str(record.get('error') or ''),
        'result_node_id': str(record.get('result_node_id') or ''),
        'cost_usd': None,
        'tokens': None,
        'cancellable': False,
    }


def _attach_usage(row: Dict[str, Any], usage_by_node: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
    node_id = row.get('result_node_id') or ''
    if not node_id:
        return
    node_usage = (usage_by_node.get(row.get('project_id') or '') or {}).get(node_id)
    if not node_usage:
        return
    row['cost_usd'] = round(float(node_usage.get('cost_usd') or 0.0), 6)
    row['tokens'] = {
        'input_tokens': int(node_usage.get('input_tokens') or 0),
        'output_tokens': int(node_usage.get('output_tokens') or 0),
        'total_tokens': int(node_usage.get('total_tokens') or 0),
    }


def _attach_project(row: Dict[str, Any], projects: Dict[str, Dict[str, Any]]) -> None:
    project = projects.get(row.get('project_id') or '')
    row['project_name'] = str(project.get('name') or '') if project else ''


@router.get('/api/global-tasks')
async def list_global_tasks(
    status: str = Query(default=''),
    project_id: str = Query(default=''),
    limit: int = Query(default=50, ge=1, le=TASK_ROW_LIMIT_MAX),
):
    """全局任务总控台：跨项目列出 queued/running/终态任务（provider/耗时/成本列数据源）。

    status 过滤：'' 全部；'active' = queued+running；其余按终态精确匹配。
    """
    import asyncio
    store = _app_main.FIREFLY_PROJECT_STORE
    project_records = await asyncio.to_thread(store.list_projects)
    projects: Dict[str, Dict[str, Any]] = {
        str(p['id']): {'id': str(p['id']), 'name': str(p.get('name') or ''), 'canvas_id': str(p.get('canvas_id') or p['id']), 'updated_at': int(p.get('updated_at') or 0)}
        for p in project_records
    }
    # 持久化终态任务（tasks 租户语义：canvas.history 随快照落盘）
    rows: Dict[str, Dict[str, Any]] = {}
    canvases = await asyncio.to_thread(_collect_history_records, store, list(projects))
    for project_id_key, records in canvases.items():
        for record in records:
            row = _row_from_history(project_id_key, record)
            if row and row['task_id'] not in rows:
                _attach_project(row, projects)
                rows[row['task_id']] = row
    # 内存内飞任务优先（同一 task_id 以内存为准——更新）
    with CANVAS_TASK_LOCK:
        live_tasks = {str(tid): dict(task) for tid, task in CANVAS_TASKS.items() if isinstance(task, dict) and task.get('project_id')}
    for task_id, task in live_tasks.items():
        rows[task_id] = _row_from_live(task_id, task, projects, {})
    # usage 租户成本归因（#258）
    usage_by_node = await asyncio.to_thread(_collect_usage_by_node, store, list(projects))
    for row in rows.values():
        _attach_usage(row, usage_by_node)
    # 项目行（含 usage 汇总，供总控台成本列与 reveal 跳转）
    project_rows: List[Dict[str, Any]] = []
    for pid in sorted(projects, key=lambda key: -int(projects[key].get('updated_at') or 0)):
        project = {key: value for key, value in projects[pid].items() if key != 'updated_at'}
        usage_summary = await asyncio.to_thread(_app_main.project_store_call, 'list_usage_summary', pid, None)
        total = usage_summary.get('total') or {}
        project_rows.append({
            'id': project['id'],
            'name': project['name'],
            'canvas_id': project['canvas_id'],
            'usage': {
                'turns': int(usage_summary.get('turns') or 0),
                'cost_usd': round(float(total.get('cost_usd') or 0.0), 6),
                'total_tokens': int(total.get('total_tokens') or 0),
            },
        })
    task_rows = list(rows.values())
    if status:
        wanted = {s.strip().lower() for s in status.split(',') if s.strip()}
        if wanted == {'active'}:
            task_rows = [t for t in task_rows if t['status'] in ACTIVE_STATUSES]
        else:
            task_rows = [t for t in task_rows if t['status'] in wanted]
    if project_id:
        task_rows = [t for t in task_rows if t['project_id'] == project_id]
    task_rows.sort(key=lambda t: (t.get('created_at') or 0, t.get('task_id') or ''), reverse=True)
    return {
        'generated_at': int(time.time() * 1000),
        'tasks': task_rows[:limit],
        'projects': project_rows,
    }


def _collect_history_records(store, project_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """逐项目打开画布读 history（只读遍历；本地应用项目数有限，2s 轮询可承受）。"""
    result: Dict[str, List[Dict[str, Any]]] = {}
    for pid in project_ids:
        try:
            opened = store.open_project(pid)
        except Exception:
            continue
        canvas = opened.get('canvas') or {}
        records = [r for r in (canvas.get('history') or []) if isinstance(r, dict) and (r.get('task_id') or r.get('taskId'))]
        if records:
            result[pid] = records
    return result


def _collect_usage_by_node(store, project_ids: List[str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for pid in project_ids:
        try:
            buckets = store.list_usage_by_node(pid)
        except Exception:
            continue
        if buckets:
            result[pid] = buckets
    return result
