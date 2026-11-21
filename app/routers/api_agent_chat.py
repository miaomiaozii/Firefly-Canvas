"""Project-scoped Agent conversation reads, shared by REST, MCP and Pi."""
import asyncio
import os
import sys

from fastapi import APIRouter, Depends, Request

from app.core import ensure_agent_same_origin
from app.services.agent_chat import read_chat
from app.services.agent_errors import agent_error

_app_main = sys.modules.get(os.environ.get('FIREFLY_MAIN_MODULE', '')) or sys.modules.get('main') or sys.modules['__main__']

router = APIRouter(prefix='/api/projects/{project_id}/chat', tags=['agent-chat'],
                   dependencies=[Depends(ensure_agent_same_origin)])


async def chat_core(project_id, actor, tool, args):
    return await asyncio.to_thread(read_chat, _app_main.FIREFLY_PROJECT_STORE, project_id, actor, tool, args)


async def _read(request, project_id, tool):
    declared = request.headers.get('X-Agent-Project')
    if declared is not None and declared != project_id:
        raise agent_error(403, 'PROJECT_MISMATCH', 'X-Agent-Project does not match the requested project',
                          '使用当前项目的 X-Agent-Project，不能跨项目读取对话。')
    args = dict(request.query_params)
    actor = args.pop('agent_node_id', None)
    for key in ('limit', 'before_seq'):
        if key in args:
            try:
                args[key] = int(args[key])
            except ValueError:
                raise agent_error(400, 'INVALID_ARGUMENT', f'{key} must be an integer') from None
    return await chat_core(project_id, actor, tool, args)


@router.get('/history')
async def history(request: Request, project_id: str):
    return await _read(request, project_id, 'chat.history')


@router.get('/search')
async def search(request: Request, project_id: str):
    return await _read(request, project_id, 'chat.search')
