# agents domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
from app.core import _AGENT_TASK_INPUT_KEYS, _agent_execution_tasks_from_plan, _agent_find_task_node, _agent_history_output, _agent_int_param, _agent_memory_task_active, _agent_nodes_by_id, _agent_normalize_candidates, _agent_normalize_task, _agent_plan_from_node, _agent_plan_hash, _agent_recovery_payload, _agent_task_canonical, _agent_task_layout  # noqa: E402,F401

def _agent_task_node(task: Mapping[str, Any], *, agent_id: str, execution_id: str, status: str='queued', x: Optional[float]=None, y: Optional[float]=None, w: int=300, h: int=236) -> Dict[str, Any]:
    task_id = str(task.get('task_id') or '')
    return {'id': task_id, 'type': 'agent-task', 'title': f"Agent 任务 {int(task.get('index') or 0) + 1}", 'agent_id': agent_id, 'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'kind': str(task.get('kind') or 'image'), 'model_type': str(task.get('model_type') or ''), 'count': int(task.get('count') or 1), 'prompt': str(task.get('prompt') or ''), 'params': copy.deepcopy(task.get('params') or {}), 'reference_asset_ids': copy.deepcopy(task.get('reference_asset_ids') or []), 'status': status, 'created_at': int(task.get('created_at') or _svc._main('now_ms')()), 'updated_at': _svc._main('now_ms')(), 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result'), 'idempotency_key': str(task.get('idempotency_key') or task_id), 'dispatch_state': str(task.get('dispatch_state') or 'queued'), 'x': float(x if x is not None else task.get('x') or 0), 'y': float(y if y is not None else task.get('y') or 0), 'w': int(w or task.get('w') or 300), 'h': int(h or task.get('h') or 236)}

def _agent_result_node(task: Mapping[str, Any], *, agent_id: str, execution_id: str, x: Optional[float]=None, y: Optional[float]=None, w: int=300, h: int=236) -> Dict[str, Any]:
    kind = str(task.get('kind') or 'image')
    result_node_id = str(task.get('result_node_id') or f"{task.get('task_id')}-result")
    if kind == 'prompt':
        return {'id': result_node_id, 'type': 'smart-prompt', 'title': 'Agent 提示词结果', 'x': 0, 'y': 0, 'text': '', 'source_agent_task_id': str(task.get('task_id') or ''), 'outputKind': 'prompt', 'generationState': 'queued', 'agent_id': agent_id, 'agent_task_id': str(task.get('task_id') or ''), 'execution_id': execution_id, 'created_at': _svc._main('now_ms')(), 'x': float(x if x is not None else task.get('result_x') or 0), 'y': float(y if y is not None else task.get('result_y') or 0), 'w': int(w or task.get('result_w') or 300), 'h': int(h or task.get('result_h') or 236)}
    return {'id': result_node_id, 'type': 'smart-image', 'title': 'Agent 图片结果' if kind == 'image' else 'Agent 视频结果', 'x': 0, 'y': 0, 'images': [], 'outputKind': kind, 'pending': int(task.get('count') or 1), 'running': False, 'agent_id': agent_id, 'agent_task_id': str(task.get('task_id') or ''), 'execution_id': execution_id, 'created_at': _svc._main('now_ms')(), 'x': float(x if x is not None else task.get('result_x') or 0), 'y': float(y if y is not None else task.get('result_y') or 0), 'w': int(w or task.get('result_w') or 300), 'h': int(h or task.get('result_h') or 236)}

async def _agent_call_adapter(task_request: Dict[str, Any], kind: str) -> Any:
    adapter = _svc._main('agent_image_adapter') if kind == 'image' else _svc._main('agent_video_adapter')
    if adapter is None:
        raise RuntimeError(f'Agent {kind} 在线适配器尚未配置')
    result = adapter(task_request)
    if inspect.isawaitable(result):
        try:
            return await asyncio.wait_for(result, timeout=max(1, float(_svc._main('PROJECT_AGENT_TASK_TIMEOUT_SECONDS'))))
        except asyncio.TimeoutError as exc:
            raise RuntimeError('Agent 在线适配器超时，任务已安全失败') from exc
    return result

def _agent_mark_task_terminal(project_id: str, agent_id: str, plan_id: str, execution_id: str, task_id: str, status: str, **fields: Any) -> None:
    """Merge a task result and close the execution when every task is terminal."""
    plan = _svc._main('_agent_update_plan')(project_id, agent_id, plan_id, task_updates={task_id: {'status': status, **copy.deepcopy(fields)}})
    if not plan:
        return
    execution = next((item for item in plan.get('executions') or [] if str(item.get('execution_id') or '') == str(execution_id)), None)
    if not isinstance(execution, dict):
        return
    task_statuses = []
    for item in execution.get('tasks') or []:
        item_id = str(item.get('task_id') or '')
        plan_item = next((candidate for candidate in plan.get('tasks') or [] if str(candidate.get('task_id') or '') == item_id), {})
        task_statuses.append({'task_id': item_id, 'result_node_id': str(plan_item.get('result_node_id') or item.get('result_node_id') or ''), 'kind': str(plan_item.get('kind') or item.get('kind') or ''), 'status': str(plan_item.get('status') or 'queued'), **({'error': str(plan_item.get('error') or '')[:1000]} if plan_item.get('error') else {})})
    terminal = all((item.get('status') in {'succeeded', 'failed', 'uncertain', 'cancelled'} for item in task_statuses))
    all_succeeded = terminal and all((item.get('status') == 'succeeded' for item in task_statuses))
    all_cancelled = terminal and all((item.get('status') == 'cancelled' for item in task_statuses))
    execution_status = 'completed' if all_succeeded else 'cancelled' if all_cancelled else 'failed' if terminal else 'running'
    _svc._main('_agent_update_plan')(project_id, agent_id, plan_id, execution_update={'execution_id': execution_id, 'status': execution_status, 'updated_at': _svc._main('now_ms')(), 'tasks': task_statuses}, status=execution_status)

def _agent_submit_task(task: Mapping[str, Any], project_id: str, agent_id: str, execution_id: str) -> Optional[Future]:
    """Register and submit one durable task exactly once per process."""
    task_id = str(task.get('task_id') or task.get('id') or '')
    if not task_id:
        return None
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(task_id)
        if current and current.get('status') in {'succeeded', 'failed', 'uncertain'}:
            return None
        existing_future = PROJECT_AGENT_FUTURES.get(task_id)
        if task_id in PROJECT_AGENT_RUNNING_IDS or (existing_future is not None and (not existing_future.done())):
            return existing_future
        PROJECT_AGENT_TASKS[task_id] = {'id': task_id, 'task_id': task_id, 'project_id': str(project_id), 'agent_id': str(agent_id), 'execution_id': str(execution_id), 'status': 'queued', 'created_at': int(task.get('created_at') or _svc._main('now_ms')()), 'updated_at': time.time(), 'result': None, 'error': '', 'task_snapshot': copy.deepcopy(dict(task))}
    future = ensure_project_image_generation_executor().submit(asyncio.run, _svc._main('run_project_agent_task')(task_id, project_id, agent_id, copy.deepcopy(dict(task)), execution_id))
    with PROJECT_AGENT_TASKS_LOCK:
        PROJECT_AGENT_FUTURES[task_id] = future

    def _forget(done_future: Future, task_id=task_id):
        with PROJECT_AGENT_TASKS_LOCK:
            if PROJECT_AGENT_FUTURES.get(task_id) is done_future:
                PROJECT_AGENT_FUTURES.pop(task_id, None)
            PROJECT_AGENT_RUNNING_IDS.discard(task_id)
            terminal_ids = [key for key, value in PROJECT_AGENT_TASKS.items() if value.get('status') in {'succeeded', 'failed', 'uncertain'}]
            if len(terminal_ids) > 512:
                terminal_ids.sort(key=lambda key: float(PROJECT_AGENT_TASKS[key].get('updated_at') or 0))
                for old_id in terminal_ids[:-512]:
                    PROJECT_AGENT_TASKS.pop(old_id, None)
    future.add_done_callback(_forget)
    return future


def project_agent_cancel_task(project_id: str, agent_id: str, task_id: str) -> bool:
    """Cancel a queued or running Agent task; returns False when already terminal."""
    task_id = str(task_id)
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(task_id)
        if not current:
            return False
        if str(current.get('project_id') or '') != str(project_id):
            return False
        if str(current.get('agent_id') or '') != str(agent_id):
            return False
        if current.get('status') in {'succeeded', 'failed', 'cancelled', 'uncertain'}:
            return False
        current['status'] = 'cancelled'
        current['updated_at'] = time.time()
        current['error'] = '任务已取消'
    return True


def project_agent_retry_task(project_id: str, agent_id: str, task_id: str) -> bool:
    """Reset a failed task to queued so the agent execution loop may re-run it."""
    task_id = str(task_id)
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(task_id)
        if not current:
            return False
        if str(current.get('project_id') or '') != str(project_id):
            return False
        if str(current.get('agent_id') or '') != str(agent_id):
            return False
        if current.get('status') != 'failed':
            return False
        current['status'] = 'queued'
        current['updated_at'] = time.time()
        current['error'] = ''
        future = PROJECT_AGENT_FUTURES.get(task_id)
        if future is not None and not future.done():
            return True
    snapshot = current.get('task_snapshot') if isinstance(current.get('task_snapshot'), dict) else dict(current)
    _agent_submit_task(snapshot, project_id, agent_id, str(current.get('execution_id') or ''))
    return True


def project_agent_is_cancelled(task_id: str) -> bool:
    """True when the task was cancelled while it was queued or running."""
    with PROJECT_AGENT_TASKS_LOCK:
        current = PROJECT_AGENT_TASKS.get(str(task_id))
        return bool(current and current.get('status') == 'cancelled')


async def project_conversation_adapter(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run one project conversation turn through the injectable chat seam.

    The project node owns the durable messages and references.  The adapter
    receives only the current node's messages plus media explicitly connected
    to it, so the legacy global conversation store is never consulted.
    """
    project_id = str(request.get('project_id') or '').strip()
    references = [item for item in request.get('references') or [] if isinstance(item, dict)]
    _svc._main('project_conversation_media_budget')(project_id, references)
    messages = [item for item in request.get('messages') or [] if isinstance(item, dict)]
    current_message = str(request.get('message') or '')
    current_message_id = str(request.get('current_message_id') or '')
    history_messages = messages
    if current_message_id:
        current_index = next((index for index, item in enumerate(messages) if str(item.get('id') or '') == current_message_id), None)
        if current_index is not None:
            history_messages = messages[:current_index]
    elif messages and messages[-1].get('role') == 'user' and (str(messages[-1].get('text') or '') == current_message):
        history_messages = messages[:-1]
    llm_messages = [{'role': item.get('role'), 'content': item.get('text') or ''} for item in history_messages if item.get('role') in {'user', 'assistant'} and str(item.get('text') or '').strip()]
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
    try:
        images = [_svc._main('reference_to_data_url')({'url': project_media_adapter_reference(project_id, item['asset_id'])}, max_size=1536, max_bytes=_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES')) for item in references if str(item.get('kind') or '').strip().lower() == 'image' and item.get('asset_id')]
        videos = [_svc.media_adapter_data_url(project_media_adapter_reference(project_id, item['asset_id']), 'video', max_bytes=_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES')) for item in references if str(item.get('kind') or '').strip().lower() == 'video' and item.get('asset_id')]
        audio_references = [{'asset_id': str(item['asset_id']).strip(), 'kind': 'audio', 'name': str(item.get('name') or item['asset_id']).strip()[:160], 'url': _svc.media_adapter_data_url(project_media_adapter_reference(project_id, item['asset_id']), 'audio', max_bytes=_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES'))} for item in references if str(item.get('kind') or '').strip().lower() == 'audio' and item.get('asset_id')]
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
    payload = CanvasLLMRequest(message=current_message, system_prompt=str(request.get('system_prompt') or ''), model=str(request.get('model') or ''), messages=llm_messages, provider=str(request.get('provider') or 'comfly'), ms_model=str(request.get('ms_model') or ''), images=images, videos=videos, audios=[item['url'] for item in audio_references], audio_references=audio_references)
    result = await _svc._main('canvas_llm')(payload)
    return {'text': str(result.get('text') or '接口返回了空回复。'), 'model': result.get('model') or str(request.get('model') or ''), 'raw_usage': result.get('raw_usage')}

def project_media_adapter_path(value: str, expected_kind: str='') -> str:
    """Resolve an internal project-media handle to its verified local source path."""
    text = str(value or '').strip()
    require_project_media_adapter_scope(text)
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme != PROJECT_MEDIA_ADAPTER_SCHEME or not parsed.netloc or parsed.query or parsed.fragment:
        raise ProjectStoreError('项目媒体适配句柄无效')
    project_id = urllib.parse.unquote(parsed.netloc)
    asset_id = urllib.parse.unquote(parsed.path.lstrip('/'))
    if not project_id or not asset_id or '/' in asset_id or ('\\' in asset_id):
        raise ProjectStoreError('项目媒体适配句柄无效')
    record = _svc._main('project_store_call')('media_record', project_id, asset_id)
    actual_kind = str(record.get('kind') or '').strip().lower()
    expected = str(expected_kind or '').strip().lower()
    if expected and actual_kind != expected:
        raise ProjectStoreError(f'项目媒体类型不匹配：需要 {expected}，实际是 {actual_kind}')
    path = Path(str(record.get('path') or '')).resolve()
    if not path.is_file():
        raise ProjectNotFound('项目媒体不存在')
    return str(path)

def project_library_status():
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _svc._main('FIREFLY_PROJECT_STORE')
        projects = store.list_projects()
        return {'path': str(store.root), 'project_count': len(projects), 'migration_active': PROJECT_LIBRARY_MIGRATION_ACTIVE}

def project_agent_connected_inputs(project_id: str, canvas: Mapping[str, Any], node_id: str) -> Dict[str, Any]:
    """Read only prompt/media nodes on direct incoming input edges.

    In particular, this intentionally does not walk a graph, inspect the
    project's other nodes, or trust asset URLs supplied by the client.  Every
    media identifier is resolved through the owning ProjectStore before it is
    admitted to the plan.
    """
    nodes = _agent_nodes_by_id(canvas)
    source_ids: List[str] = []
    for edge in canvas.get('connections') or []:
        if not isinstance(edge, dict):
            continue
        target = edge.get('to') or edge.get('target') or edge.get('toNode') or edge.get('targetNodeId')
        source = edge.get('from') or edge.get('source') or edge.get('fromNode') or edge.get('sourceNodeId')
        kind = str(edge.get('kind') or 'flow')
        if str(target or '') == str(node_id) and source and (kind in {'input', 'flow'}):
            source_ids.append(str(source))
    prompts: List[str] = []
    prompt_node_ids: List[str] = []
    media: List[Dict[str, Any]] = []
    seen_assets: set[str] = set()
    for source_id in source_ids:
        source = nodes.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = str(source.get('type') or '')
        if source_type == 'smart-prompt':
            text = str(source.get('text') or source.get('prompt') or '').strip()
            if text:
                prompts.append(text[:ONLINE_IMAGE_PROMPT_MAX_LENGTH])
                prompt_node_ids.append(source_id)
            continue
        if source_type not in {'', 'image', 'smart-image'}:
            continue
        for item in source.get('images') or []:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get('asset_id') or '').strip()
            if not asset_id or asset_id in seen_assets:
                continue
            try:
                record = _svc._main('project_store_call')('media_record', project_id, asset_id)
            except ProjectNotFound as exc:
                raise HTTPException(status_code=400, detail={'code': 'agent_input_media_missing', 'message': f'Agent 的输入媒体不属于当前项目: {asset_id}'}) from exc
            except ProjectStoreError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            expected_kind = str(item.get('kind') or record.get('kind') or '').strip().lower()
            actual_kind = str(record.get('kind') or '').strip().lower()
            if expected_kind not in {'image', 'video', 'audio'} or expected_kind != actual_kind:
                raise HTTPException(status_code=400, detail={'code': 'agent_input_media_kind_mismatch', 'message': f'Agent 输入媒体类型不匹配: {asset_id}'})
            seen_assets.add(asset_id)
            media.append({'asset_id': asset_id, 'kind': actual_kind, 'name': str(record.get('name') or item.get('name') or asset_id)[:160]})
    return {'prompt': '\n\n'.join(prompts).strip(), 'prompts': prompts, 'prompt_node_ids': prompt_node_ids, 'media': media}

async def project_agent_default_image_adapter(task_request: Dict[str, Any]) -> Dict[str, Any]:
    """Route one Agent image task through the existing online image boundary.

    This wrapper deliberately returns adapter candidates only.  The Agent
    worker remains the single owner of project-media archiving, result-node
    completion, and Agent history, so a provider failure cannot silently look
    successful or create a second generation record.
    """
    project_id = str(task_request.get('project_id') or '')
    params = task_request.get('params') if isinstance(task_request.get('params'), dict) else {}
    prompt = str(task_request.get('prompt') or '').strip()
    reference_ids = [str(value).strip() for value in params.get('reference_asset_ids') or [] if str(value).strip()]
    request_snapshot = ImageGenerationRequest(prompt=prompt, size=str(params.get('size') or '1024x1024'), width=_agent_int_param(params, 'width'), height=_agent_int_param(params, 'height'), aspect_ratio=str(params.get('aspect_ratio') or '1:1'), resolution=str(params.get('resolution') or '1k'), reference_asset_ids=tuple(reference_ids), media_type='image', quality=str(params.get('quality') or 'auto'), count=max(1, min(8, int(task_request.get('count') or params.get('n') or 1))))
    providers = _svc._main('load_api_providers')()
    model_hint = str(params.get('model') or '').strip()
    if not model_hint and str(task_request.get('model_type') or '').strip() not in {'', 'online-image', 'image'}:
        model_hint = str(task_request.get('model_type') or '').strip()
    route = _svc._main('route_image_generation')(request_snapshot, providers, provider_id=str(params.get('provider_id') or ''), model=model_hint)
    references = await asyncio.to_thread(_svc.project_image_references, project_id, reference_ids)
    image_parameters = {'width': request_snapshot.width, 'height': request_snapshot.height, 'aspect_ratio': request_snapshot.aspect_ratio, 'resolution': request_snapshot.resolution, 'media_type': 'image', 'agent_idempotency_key': str(task_request.get('idempotency_key') or task_request.get('task_id') or '')}
    candidates: List[Dict[str, Any]] = []
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
    try:
        for _ in range(request_snapshot.count):
            image_data, raw = await _svc._main('generate_ai_image')(prompt, request_snapshot.size, request_snapshot.quality, route.model, references, route.provider_id, preserve_model=True, image_parameters=image_parameters)
            try:
                extracted = extract_images(raw) if isinstance(raw, dict) else []
            except HTTPException:
                extracted = []
            candidates.extend(extracted or [image_data])
            if len(candidates) >= request_snapshot.count:
                break
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
    candidates = [item for item in candidates if isinstance(item, dict)][:request_snapshot.count]
    if len(candidates) != request_snapshot.count:
        raise ProjectStoreError('在线模型没有返回计划数量的图片')
    return {'images': candidates, 'route': route.to_dict()}

async def project_agent_default_video_adapter(task_request: Dict[str, Any]) -> Dict[str, Any]:
    """Route one Agent video task through the existing online video boundary."""
    project_id = str(task_request.get('project_id') or '')
    params = task_request.get('params') if isinstance(task_request.get('params'), dict) else {}
    prompt = str(task_request.get('prompt') or '').strip()
    references = [item for item in task_request.get('references') or [] if isinstance(item, dict)]
    image_ids = [str(item.get('asset_id')) for item in references if item.get('kind') == 'image']
    video_ids = [str(item.get('asset_id')) for item in references if item.get('kind') == 'video']
    audio_ids = [str(item.get('asset_id')) for item in references if item.get('kind') == 'audio']
    request_snapshot = VideoGenerationRequest(prompt=prompt, size=str(params.get('size') or '1024x576'), width=_agent_int_param(params, 'width'), height=_agent_int_param(params, 'height'), aspect_ratio=str(params.get('aspect_ratio') or '16:9'), resolution=str(params.get('resolution') or '720p'), duration=max(1, min(60, int(params.get('duration') or 5))), image_reference_asset_ids=tuple(image_ids), video_reference_asset_ids=tuple(video_ids), audio_reference_asset_ids=tuple(audio_ids), media_type='video', quality=str(params.get('quality') or 'auto'), count=max(1, min(4, int(task_request.get('count') or params.get('n') or 1))), seed=_agent_int_param(params, 'seed'), enhance_prompt=bool(params.get('enhance_prompt', False)), enable_upsample=bool(params.get('enable_upsample', False)), watermark=bool(params.get('watermark', False)), camera_fixed=bool(params.get('camera_fixed', False)), return_last_frame=bool(params.get('return_last_frame', False)), generate_audio=bool(params.get('generate_audio', False)))
    provider_id = str(params.get('provider_id') or '')
    model = str(params.get('model') or '').strip()
    if not model and str(task_request.get('model_type') or '').strip() not in {'', 'online-video', 'video'}:
        model = str(task_request.get('model_type') or '').strip()
    resolved_references = [*await asyncio.to_thread(_svc.project_video_references, project_id, image_ids, 'image'), *await asyncio.to_thread(_svc.project_video_references, project_id, video_ids, 'video'), *await asyncio.to_thread(_svc.project_video_references, project_id, audio_ids, 'audio')]
    provider_records = await _svc._main('preflight_project_video_providers')(request_snapshot, resolved_references, _svc._main('load_api_providers')(), provider_id=provider_id, model=model)
    route = _svc._main('route_video_generation')(request_snapshot, provider_records, provider_id=provider_id, model=model, adapter_incompatible_parameters=functools.partial(_svc.project_video_adapter_incompatible_parameters, references=resolved_references))
    provider = next((item for item in provider_records if str(item.get('id') or '').strip().lower() == route.provider_id), None)
    candidates, raw = await _svc._main('execute_project_video_generation')(project_id, request_snapshot, route, resolved_references, provider=provider)
    candidates = [item for item in candidates if isinstance(item, dict)][:request_snapshot.count]
    if len(candidates) != request_snapshot.count:
        raise ProjectStoreError('在线模型没有返回计划数量的视频')
    return {'videos': candidates, 'raw': raw if isinstance(raw, dict) else {}, 'route': route.to_dict()}

def _agent_update_plan(project_id: str, agent_id: str, plan_id: str, *, task_updates: Optional[Mapping[str, Mapping[str, Any]]]=None, execution_update: Optional[Mapping[str, Any]]=None, status: Optional[str]=None) -> Optional[Dict[str, Any]]:
    for _attempt in range(8):
        opened = _svc._main('project_store_call')('open_project', project_id)
        agent = project_agent_node(opened['canvas'], agent_id)
        plan = _agent_plan_from_node(agent, plan_id)
        if not plan:
            return None
        if task_updates:
            next_tasks = []
            for item in plan.get('tasks') or []:
                task_id = str(item.get('task_id') or '')
                replacement = task_updates.get(task_id)
                updated_item = copy.deepcopy(item)
                if replacement:
                    updated_item.update(copy.deepcopy(replacement))
                next_tasks.append(updated_item)
            plan['tasks'] = next_tasks
        if execution_update:
            executions = [item for item in plan.get('executions') or [] if isinstance(item, dict)]
            execution_id = str(execution_update.get('execution_id') or '')
            found = False
            for item in executions:
                if str(item.get('execution_id') or '') == execution_id:
                    item.update(copy.deepcopy(execution_update))
                    found = True
                    break
            if not found and execution_id:
                executions.append(copy.deepcopy(dict(execution_update)))
            plan['executions'] = executions[-32:]
        if status:
            plan['status'] = status
        plan['updated_at'] = _svc._main('now_ms')()
        replacement_agent = copy.deepcopy(agent)
        replacement_agent['agent_plan'] = plan
        try:
            _svc._main('_agent_apply_mutations')(project_id, [{'op': 'upsert_node', 'node': replacement_agent}], retries=1)
            return plan
        except RevisionConflict:
            continue
    raise ProjectStoreError(f'Agent 计划 {plan_id} 状态保存冲突，状态需要恢复')

async def run_project_agent_task(task_id: str, project_id: str, agent_id: str, task: Dict[str, Any], execution_id: str) -> None:
    """Run one confirmed, persisted Agent task through its injected adapter."""
    task_id = str(task_id)
    started_at = _svc._main('now_ms')()
    with PROJECT_AGENT_TASKS_LOCK:
        if task_id in PROJECT_AGENT_RUNNING_IDS:
            return
        memory = PROJECT_AGENT_TASKS.get(task_id)
        if memory and memory.get('status') in {'succeeded', 'failed'}:
            return
        PROJECT_AGENT_RUNNING_IDS.add(task_id)
        PROJECT_AGENT_TASKS[task_id] = {'id': task_id, 'task_id': task_id, 'project_id': project_id, 'agent_id': agent_id, 'execution_id': execution_id, 'status': 'running', 'created_at': started_at, 'updated_at': time.time(), 'result': None, 'error': ''}
    kind = str(task.get('kind') or 'image')
    raw_params = copy.deepcopy(task.get('params') or {})
    task_prompt = str(task.get('prompt') or '')
    if not task_prompt and isinstance(raw_params.get('prompt'), str):
        task_prompt = raw_params['prompt'].strip()
        task['prompt'] = task_prompt
    request = {'project_id': project_id, 'agent_id': agent_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'task_id': task_id, 'kind': kind, 'model_type': str(task.get('model_type') or ''), 'prompt': task_prompt, 'params': raw_params, 'references': copy.deepcopy(task.get('references') or [])}
    generated_items: List[Dict[str, Any]] = []
    history_record: Dict[str, Any]
    try:
        idempotency_key = str(task.get('idempotency_key') or task_id)
        _svc._main('_agent_set_task_node')(project_id, task_id, status='running', started_at=started_at, idempotency_key=idempotency_key, dispatch_state='dispatched' if kind != 'prompt' else 'local')
        if kind == 'prompt':
            completed_at = _svc._main('now_ms')()
            generated_items = [{'kind': 'prompt', 'text': str(task.get('prompt') or '')}]
            history_record = {'id': f'agent-generation-{task_id}', 'type': 'agent-prompt', 'status': 'succeeded', 'createdAt': started_at, 'completedAt': completed_at, 'runMs': max(0, completed_at - started_at), 'platform': 'Agent', 'model': str(task.get('model_type') or 'agent-prompt-node'), 'parameters': copy.deepcopy(task.get('params') or {}), 'input_references': [], 'request': {'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id}, 'agent_id': agent_id, 'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'prompt': str(task.get('prompt') or ''), 'created_at': started_at, 'completed_at': completed_at, 'model_type': str(task.get('model_type') or 'agent-prompt-node'), 'params': copy.deepcopy(task.get('params') or {}), 'references': [], 'outputs': [], 'prompt_output': {'text': str(task.get('prompt') or '')}, 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result')}
            result_node_id = str(task.get('result_node_id') or f'{task_id}-result')
            for _attempt in range(8):
                opened = _svc._main('project_store_call')('open_project', project_id)
                result_node = next((node for node in opened['canvas'].get('nodes') or [] if isinstance(node, dict) and str(node.get('id') or '') == result_node_id), None)
                if not result_node or str(result_node.get('type') or '') != 'smart-prompt':
                    raise ProjectStoreError('Agent 提示词结果节点不存在')
                replacement_result = copy.deepcopy(result_node)
                replacement_result.update({'text': str(task.get('prompt') or ''), 'outputKind': 'prompt', 'generationState': 'succeeded', 'updated_at': completed_at})
                history = [item for item in opened['canvas'].get('logs') or [] if str(item.get('id') or '') != history_record['id']]
                history.append(copy.deepcopy(history_record))
                try:
                    canvas = _svc._main('_agent_apply_mutations')(project_id, [{'op': 'upsert_node', 'node': replacement_result}, {'op': 'set_canvas_meta', 'logs': history[-5000:]}], retries=1)
                    break
                except RevisionConflict:
                    continue
            else:
                raise ProjectStoreError('Agent 提示词结果无法保存')
            await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
            _svc._main('_agent_set_task_node')(project_id, task_id, status='succeeded', completed_at=completed_at, result={'prompt': str(task.get('prompt') or ''), 'history_id': history_record['id']}, error='')
            _agent_mark_task_terminal(project_id, agent_id, str(task.get('plan_id') or ''), execution_id, task_id, 'succeeded', completed_at=completed_at, prompt_output={'text': str(task.get('prompt') or '')})
            result = {'status': 'succeeded', 'task_id': task_id, 'prompt': str(task.get('prompt') or ''), 'history_id': history_record['id'], 'result_node_id': result_node_id, 'project_revision': int(canvas.get('revision') or 0)}
            with PROJECT_AGENT_TASKS_LOCK:
                PROJECT_AGENT_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'updated_at': time.time()})
            return
        if project_agent_is_cancelled(task_id):
            with PROJECT_AGENT_TASKS_LOCK:
                PROJECT_AGENT_TASKS[task_id].update({'status': 'cancelled', 'updated_at': time.time(), 'error': '任务已取消'})
            _svc._main('_agent_set_task_node')(project_id, task_id, status='cancelled', completed_at=_svc._main('now_ms')(), result=None, error='任务已取消')
            _agent_mark_task_terminal(project_id, agent_id, str(task.get('plan_id') or ''), execution_id, task_id, 'cancelled', completed_at=_svc._main('now_ms')())
            return
        raw_result = await _agent_call_adapter(request, kind)
        candidates = _agent_normalize_candidates(raw_result, kind)
        expected_count = int(task.get('count') or 1)
        if len(candidates) != expected_count:
            raise RuntimeError(f'Agent 适配器返回 {len(candidates)} 个结果，计划要求 {expected_count} 个')
        for index, candidate in enumerate(candidates):
            if candidate.get('asset_id'):
                raise RuntimeError('Agent 适配器不能把现有项目媒体作为生成结果')
            elif kind == 'image':
                archived = await _svc.archive_project_generated_image(project_id, candidate, index)
                if str(archived.get('asset_id') or '') in {str(item.get('asset_id') or '') for item in task.get('references') or []}:
                    raise RuntimeError('Agent 生成内容与参考原件重复，不能把输入原件作为结果')
                generated_items.append(archived)
            else:
                archived = await _svc.archive_project_generated_video(project_id, candidate, index)
                if str(archived.get('asset_id') or '') in {str(item.get('asset_id') or '') for item in task.get('references') or []}:
                    raise RuntimeError('Agent 生成内容与参考原件重复，不能把输入原件作为结果')
                generated_items.append(archived)
        if project_agent_is_cancelled(task_id):
            for generated in list(generated_items):
                asset_id = str(generated.get('asset_id') or '') if isinstance(generated, dict) else ''
                if asset_id:
                    try:
                        _svc._main('project_store_call')('delete_media_if_unreferenced', project_id, asset_id)
                    except Exception:
                        pass
            with PROJECT_AGENT_TASKS_LOCK:
                PROJECT_AGENT_TASKS[task_id].update({'status': 'cancelled', 'updated_at': time.time(), 'error': '任务已取消'})
            _svc._main('_agent_set_task_node')(project_id, task_id, status='cancelled', completed_at=_svc._main('now_ms')(), result=None, error='任务已取消')
            _agent_mark_task_terminal(project_id, agent_id, str(task.get('plan_id') or ''), execution_id, task_id, 'cancelled', completed_at=_svc._main('now_ms')())
            return
        completed_at = _svc._main('now_ms')()
        history_record = {'id': f'agent-generation-{task_id}', 'type': 'agent-prompt' if kind == 'prompt' else 'agent-image' if kind == 'image' else 'agent-video', 'status': 'succeeded', 'createdAt': started_at, 'completedAt': completed_at, 'runMs': max(0, completed_at - started_at), 'platform': 'Agent', 'model': str(task.get('model_type') or ''), 'parameters': copy.deepcopy(task.get('params') or {}), 'input_references': [_agent_history_output(project_id, item, str(item.get('kind') or 'image')) for item in task.get('references') or []], 'request': {'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id}, 'agent_id': agent_id, 'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'prompt': str(task.get('prompt') or ''), 'created_at': started_at, 'completed_at': completed_at, 'model_type': str(task.get('model_type') or ''), 'params': copy.deepcopy(task.get('params') or {}), 'references': [{'asset_id': str(item.get('asset_id')), 'kind': str(item.get('kind') or '')} for item in task.get('references') or []], 'outputs': [_agent_history_output(project_id, item, kind) for item in generated_items], 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result')}
        canvas = await asyncio.to_thread(_svc._main('project_store_call'), 'complete_media_generation', project_id, str(task.get('result_node_id') or f'{task_id}-result'), generated_items, history_record, media_kind=kind)
        await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
        _svc._main('_agent_set_task_node')(project_id, task_id, status='succeeded', completed_at=completed_at, result={'media': copy.deepcopy(generated_items), 'history_id': history_record['id']}, error='')
        _agent_mark_task_terminal(project_id, agent_id, str(task.get('plan_id') or ''), execution_id, task_id, 'succeeded', completed_at=completed_at, outputs=copy.deepcopy(generated_items))
        result = {'status': 'succeeded', 'task_id': task_id, 'media': copy.deepcopy(generated_items), 'history_id': history_record['id'], 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result'), 'project_revision': int(canvas.get('revision') or 0)}
        with PROJECT_AGENT_TASKS_LOCK:
            PROJECT_AGENT_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'updated_at': time.time()})
    except Exception as exc:
        detail = str(getattr(exc, 'detail', None) or exc)
        failed_at = _svc._main('now_ms')()
        for generated in list(generated_items):
            asset_id = str(generated.get('asset_id') or '') if isinstance(generated, dict) else ''
            if asset_id:
                try:
                    _svc._main('project_store_call')('delete_media_if_unreferenced', project_id, asset_id)
                except Exception:
                    pass
        generated_items = []
        history_record = {'id': f'agent-generation-{task_id}', 'type': 'agent-prompt' if kind == 'prompt' else 'agent-image' if kind == 'image' else 'agent-video', 'status': 'failed', 'createdAt': started_at, 'completedAt': failed_at, 'runMs': max(0, failed_at - started_at), 'platform': 'Agent', 'model': str(task.get('model_type') or ''), 'parameters': copy.deepcopy(task.get('params') or {}), 'input_references': [_agent_history_output(project_id, item, str(item.get('kind') or 'image')) for item in task.get('references') or []], 'request': {'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id}, 'agent_id': agent_id, 'task_id': task_id, 'plan_id': str(task.get('plan_id') or ''), 'execution_id': execution_id, 'prompt': str(task.get('prompt') or ''), 'created_at': started_at, 'completed_at': failed_at, 'model_type': str(task.get('model_type') or ''), 'params': copy.deepcopy(task.get('params') or {}), 'references': [{'asset_id': str(item.get('asset_id')), 'kind': str(item.get('kind') or '')} for item in task.get('references') or []], 'outputs': [], 'result_node_id': str(task.get('result_node_id') or f'{task_id}-result'), 'error': detail[:1000]}
        try:
            _svc._main('_agent_set_task_node')(project_id, task_id, status='failed', completed_at=failed_at, result=None, error=detail[:1000])
            for _attempt in range(8):
                opened = _svc._main('project_store_call')('open_project', project_id)
                result_node = next((node for node in opened['canvas'].get('nodes') or [] if str(node.get('id') or '') == str(task.get('result_node_id') or f'{task_id}-result')), None)
                if not result_node:
                    break
                replacement_result = copy.deepcopy(result_node)
                replacement_result.update({'pending': 0, 'running': False, 'generationState': 'failed', 'error': detail[:1000]})
                history = copy.deepcopy(opened['canvas'].get('logs') or [])
                history = [item for item in history if str(item.get('id') or '') != history_record['id']]
                history.append(history_record)
                try:
                    _svc._main('_agent_apply_mutations')(project_id, [{'op': 'upsert_node', 'node': replacement_result}, {'op': 'set_canvas_meta', 'logs': history[-5000:]}], retries=1)
                    break
                except RevisionConflict:
                    continue
            _agent_mark_task_terminal(project_id, agent_id, str(task.get('plan_id') or ''), execution_id, task_id, 'failed', completed_at=failed_at, error=detail[:1000])
            _svc._main('project_store_call')('flush', project_id)
        except Exception:
            pass
        result = {'status': 'failed', 'task_id': task_id, 'error': detail[:1000]}
        with PROJECT_AGENT_TASKS_LOCK:
            PROJECT_AGENT_TASKS[task_id].update({'status': 'failed', 'result': result, 'error': detail[:1000], 'updated_at': time.time()})

def _agent_active_counts(project_id: str, agent_id: str='') -> Tuple[int, int, int]:
    global_count = project_count = node_count = 0
    with PROJECT_AGENT_TASKS_LOCK:
        for task in PROJECT_AGENT_TASKS.values():
            if task.get('status') not in {'queued', 'running'}:
                continue
            global_count += 1
            if str(task.get('project_id') or '') == str(project_id):
                project_count += 1
                if str(task.get('agent_id') or '') == str(agent_id):
                    node_count += 1
    try:
        projects = _svc._main('project_store_call')('list_projects')
    except Exception:
        projects = []
    for project in projects:
        candidate_project = str(project.get('id') or '')
        try:
            opened = _svc._main('project_store_call')('open_project', candidate_project)
        except Exception:
            continue
        for node in opened['canvas'].get('nodes') or []:
            if not isinstance(node, dict) or str(node.get('type') or '') != 'agent-task':
                continue
            if str(node.get('status') or '') not in {'queued', 'running'}:
                continue
            task_id = str(node.get('task_id') or node.get('id') or '')
            with PROJECT_AGENT_TASKS_LOCK:
                if task_id in PROJECT_AGENT_TASKS:
                    continue
            global_count += 1
            if candidate_project == str(project_id):
                project_count += 1
                if str(node.get('agent_id') or '') == str(agent_id):
                    node_count += 1
    return (global_count, project_count, node_count)

def _agent_admit_execution_sync(project_id: str, node_id: str, payload_data: Mapping[str, Any]):
    plan_id = str(payload_data.get('plan_id') or '').strip()
    execution_id = str(payload_data.get('execution_id') or '').strip()
    plan_hash = str(payload_data.get('plan_hash') or '').strip()
    payload_tasks = payload_data.get('tasks') or []
    if not execution_id:
        raise HTTPException(status_code=400, detail={'code': 'agent_execution_id_required', 'message': 'Agent 执行需要稳定 execution_id 以保证幂等'})
    lock = project_agent_execution_lock(project_id, node_id)
    with PROJECT_AGENT_ADMISSION_LOCK, lock:
        try:
            opened = _svc._main('project_store_call')('open_project', project_id)
            canvas = opened['canvas']
            agent = project_agent_node(canvas, node_id)
            plan = _agent_plan_from_node(agent, plan_id)
            if not plan:
                raise HTTPException(status_code=404, detail='Agent 计划不存在')
            stored_hash = str(plan.get('plan_hash') or '')
            canonical_hash = _agent_plan_hash(plan)
            if not stored_hash or stored_hash != canonical_hash:
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划已被修改，请重新生成并确认'})
            expected_hash = canonical_hash
            if plan_hash and plan_hash != expected_hash:
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划已变化，请重新确认'})
            if not plan_hash and (not str(plan.get('request_id') or '')):
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': '确认 Agent 计划需要 plan_hash 或 request_id'})
            current_inputs = project_agent_connected_inputs(project_id, canvas, node_id)
            if json.dumps(plan.get('inputs') or {}, ensure_ascii=False, sort_keys=True, separators=(',', ':')) != json.dumps(current_inputs, ensure_ascii=False, sort_keys=True, separators=(',', ':')):
                raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 直连输入已变化，请重新生成并确认'})
            validated_tasks = []
            for index, stored_task in enumerate(plan.get('tasks') or []):
                if not isinstance(stored_task, dict):
                    raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划任务格式已变化'})
                normalized = _agent_normalize_task(stored_task, index, goal=str(plan.get('goal') or ''), inputs=current_inputs)
                if _agent_task_canonical(normalized) != _agent_task_canonical(stored_task):
                    raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': 'Agent 计划任务已被修改，请重新生成并确认'})
                validated_tasks.append(normalized)
            if payload_tasks:
                comparable = []
                for item in payload_tasks:
                    if not isinstance(item, dict):
                        raise HTTPException(status_code=400, detail='确认任务必须是对象')
                    unknown_confirm_fields = set(item.keys()) - _AGENT_TASK_INPUT_KEYS
                    if unknown_confirm_fields:
                        raise HTTPException(status_code=400, detail={'code': 'agent_task_field_unknown', 'message': f'Agent 不允许确认字段: {sorted((str(key) for key in unknown_confirm_fields))[0]}'})
                    comparable.append({key: item.get(key) for key in ('task_id', 'kind', 'model_type', 'count', 'prompt', 'params', 'reference_asset_ids') if key in item})
                expected = [{key: item.get(key) for key in ('task_id', 'kind', 'model_type', 'count', 'prompt', 'params', 'reference_asset_ids') if key in item} for item in plan.get('tasks') or []]
                if comparable != expected:
                    raise HTTPException(status_code=409, detail={'code': 'agent_plan_changed', 'message': '确认内容不是已展示的 Agent 计划'})
            executions = [item for item in plan.get('executions') or [] if isinstance(item, dict)]
            existing_execution = next((item for item in executions if str(item.get('execution_id') or '') == execution_id), None)
            if existing_execution is not None:
                uncertain_task_ids: List[str] = []
                submit_tasks: List[Dict[str, Any]] = []
                for summary in existing_execution.get('tasks') or []:
                    if not isinstance(summary, dict) or str(summary.get('status') or '') not in {'queued', 'running'}:
                        continue
                    task_id = str(summary.get('task_id') or '')
                    durable_node = _agent_find_task_node(canvas, task_id)
                    if not durable_node:
                        continue
                    durable_status = str(durable_node.get('status') or '')
                    if durable_status == 'queued':
                        recovery_task = _agent_recovery_payload(durable_node, plan)
                        if recovery_task:
                            submit_tasks.append(recovery_task)
                    elif durable_status == 'running':
                        if not _agent_memory_task_active(task_id):
                            _svc._main('_agent_mark_recovered_uncertain')(project_id, node_id, plan, durable_node)
                            uncertain_task_ids.append(task_id)
                if uncertain_task_ids:
                    raise HTTPException(status_code=409, detail={'code': 'agent_execution_uncertain', 'message': 'Agent 在线调用状态未知；为避免重复计费，请重新生成计划并确认新的 execution_id', 'task_ids': uncertain_task_ids})
                return {'kind': 'duplicate', 'ok': True, 'project_id': project_id, 'agent_id': node_id, 'plan_id': plan['plan_id'], 'execution_id': execution_id, 'status': str(existing_execution.get('status') or 'running'), 'tasks': [copy.deepcopy(item) for item in existing_execution.get('tasks') or []], 'plan': copy.deepcopy(plan), 'submit_tasks': submit_tasks}
            if str(plan.get('status') or '') not in {'awaiting_confirmation', 'confirmed', 'running'}:
                raise HTTPException(status_code=409, detail='Agent 计划已经完成，不能重复确认')
            if str(plan.get('status') or '') == 'running':
                raise HTTPException(status_code=409, detail='Agent 计划正在执行，请等待当前执行完成')
            tasks = _agent_execution_tasks_from_plan(plan, execution_id)
            global_count, project_count, node_count = _svc._main('_agent_active_counts')(project_id, node_id)
            if global_count + len(tasks) > _svc._main('PROJECT_AGENT_MAX_PENDING'):
                raise HTTPException(status_code=429, detail='Agent 任务队列已满')
            if project_count + len(tasks) > PROJECT_AGENT_MAX_PER_PROJECT:
                raise HTTPException(status_code=429, detail='当前项目的 Agent 任务队列已满')
            if node_count + len(tasks) > _svc._main('PROJECT_AGENT_MAX_PER_NODE'):
                raise HTTPException(status_code=429, detail='当前 Agent 节点的待处理任务已达上限')
            mutations: List[Dict[str, Any]] = []
            task_w = 300
            result_w = 300
            layouts = _agent_task_layout(canvas, agent, len(tasks))
            for task_index, task in enumerate(tasks):
                task['status'] = 'queued'
                layout = layouts[task_index]
                row_y = layout['y']
                task['x'] = layout['x']
                task['y'] = row_y
                task['w'] = task_w
                task['h'] = 236
                task['result_x'] = layout['result_x']
                task['result_y'] = layout['result_y']
                task['result_w'] = result_w
                task['result_h'] = 236
                mutations.append({'op': 'upsert_node', 'node': _agent_task_node(task, agent_id=node_id, execution_id=execution_id, x=layout['x'], y=row_y, w=task_w, h=236)})
                mutations.append({'op': 'upsert_node', 'node': _agent_result_node(task, agent_id=node_id, execution_id=execution_id, x=task['result_x'], y=row_y, w=result_w, h=236)})
                if str(task.get('kind') or '') != 'prompt':
                    mutations.append({'op': 'upsert_edge', 'edge': {'id': f"{node_id}-{task['task_id']}", 'from': node_id, 'to': task['task_id'], 'kind': 'agent-task'}})
                    mutations.append({'op': 'upsert_edge', 'edge': {'id': f"{task['task_id']}-result", 'from': task['task_id'], 'to': task['result_node_id'], 'kind': 'agent-result'}})
            execution = {'execution_id': execution_id, 'status': 'running', 'created_at': _svc._main('now_ms')(), 'updated_at': _svc._main('now_ms')(), 'tasks': [{'task_id': task['task_id'], 'result_node_id': task['result_node_id'], 'kind': task['kind'], 'status': 'queued'} for task in tasks]}
            plan['status'] = 'running'
            plan['updated_at'] = _svc._main('now_ms')()
            plan['executions'] = [*executions, execution][-32:]
            replacement_agent = copy.deepcopy(agent)
            replacement_agent['agent_plan'] = plan
            mutations.append({'op': 'upsert_node', 'node': replacement_agent})
            _svc._main('_agent_apply_mutations')(project_id, mutations)
            _svc._main('project_store_call')('flush', project_id)
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail={'message': str(exc), 'canvas': exc.canvas}) from exc
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HTTPException:
            raise
        except (ProjectStoreError, InvalidMutation, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {'kind': 'new', 'ok': True, 'project_id': project_id, 'agent_id': node_id, 'plan_id': plan['plan_id'], 'execution_id': execution_id, 'status': 'running', 'tasks': [{'task_id': task['task_id'], 'result_node_id': task['result_node_id'], 'kind': task['kind'], 'status': 'queued'} for task in tasks], 'plan': copy.deepcopy(plan), 'submit_tasks': copy.deepcopy(tasks)}

def project_conversation_media_budget(project_id: str, references: List[Dict[str, Any]]) -> None:
    """Validate the complete provider-consumed media set before reading any bytes."""
    counts = {'image': 0, 'video': 0, 'audio': 0}
    limits = {'image': _svc._main('PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES'), 'video': PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, 'audio': PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES}
    total_bytes = 0
    for item in references:
        kind = str(item.get('kind') or '').strip().lower()
        asset_id = str(item.get('asset_id') or '').strip()
        if kind not in counts or not asset_id:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_invalid', 'message': '对话参考素材缺少有效类型或资源标识'})
        counts[kind] += 1
        if counts[kind] > limits[kind]:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话{kind}参考素材数量超过上限 {limits[kind]}'})
        record = _svc._main('project_store_call')('media_record', project_id, asset_id)
        if str(record.get('kind') or '').strip().lower() != kind:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_kind_mismatch', 'message': '对话参考素材类型与项目记录不一致'})
        size = int(record.get('size') or Path(str(record.get('path') or '')).stat().st_size)
        if size > _svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES'):
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_file_too_large', 'message': f"对话参考素材超过单文件 {_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_FILE_BYTES')} 字节上限"})
        total_bytes += size
        if total_bytes > _svc._main('PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES'):
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_total_too_large', 'message': f"对话参考素材总量超过 {_svc._main('PROJECT_CONVERSATION_MEDIA_MAX_TOTAL_BYTES')} 字节上限"})

def project_media_source_path(source_url: str) -> Optional[str]:
    text = str(source_url or '').strip()
    if text.startswith(('/output/', '/assets/', '/api/storage-files/')):
        return _svc._main('output_file_from_url')(text)
    return None

def project_has_active_conversation(project_id: str, *, trashed: bool=False) -> bool:
    """Conservatively block lifecycle moves while a paid turn can still be recovered."""
    if not trashed:
        with PROJECT_CONVERSATION_TASKS_LOCK:
            if any((key[0] == str(project_id) and (not handle.done()) for key, handle in PROJECT_CONVERSATION_TASKS.items())):
                return True
    with PROJECT_LIBRARY_SWITCH_LOCK:
        store = _svc._main('FIREFLY_PROJECT_STORE')
    if store is None:
        return False
    project_dir = (Path(store.project_trash_root) if trashed else Path(store.root)) / str(project_id)
    if not project_dir.is_dir():
        return False
    try:
        if trashed:
            canvas = json.loads(store._safe_project_path(project_dir, 'canvas.json').read_text(encoding='utf-8'))
        else:
            canvas = store.open_project(project_id)['canvas']
        return project_has_recoverable_conversation_sidecars(store, project_dir, project_id, canvas) or canvas_has_pending_conversation(canvas)
    except (ProjectStoreError, OSError, ValueError, json.JSONDecodeError):
        return True

def project_has_active_agent_task(project_id: str) -> bool:
    """Block lifecycle moves while any Agent task for the project is still queued or running."""
    with PROJECT_AGENT_TASKS_LOCK:
        return any(
            str(task.get('project_id') or '') == str(project_id) and task.get('status') in {'queued', 'running'}
            for task in PROJECT_AGENT_TASKS.values()
        )


def project_lifecycle_store_call(method_name: str, project_id: str, *, block_active_import: bool=False):
    with project_lifecycle_lock(project_id):
        if block_active_import and project_has_active_media_import(project_id):
            raise ProjectMediaImportActive('项目仍有媒体正在导入，请等待完成后再删除')
        if block_active_import and project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive('项目仍有图片正在生成，请等待完成后再删除')
        if block_active_import and project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive('项目仍有视频正在生成，请等待完成后再删除')
        if block_active_import and project_has_active_conversation(project_id, trashed=method_name == 'purge_trashed_project'):
            raise ProjectMediaImportActive('项目仍有对话任务待完成或待恢复，不能删除')
        if block_active_import and project_has_active_agent_task(project_id):
            raise ProjectMediaImportActive('项目仍有 Agent 任务正在执行，请等待完成后再删除')
        return _svc._main('project_store_call')(method_name, project_id)

def project_conversation_connected_references(project_id: str, canvas: Dict[str, Any], node_id: str) -> List[Dict[str, Any]]:
    """Resolve only media nodes directly connected into one conversation node."""
    nodes_by_id = {str(node.get('id')): node for node in canvas.get('nodes') or [] if isinstance(node, dict) and str(node.get('id') or '')}
    source_ids = []
    for edge in canvas.get('connections') or []:
        if not isinstance(edge, dict):
            continue
        target = edge.get('to') or edge.get('target') or edge.get('toNode') or edge.get('targetNodeId')
        kind = str(edge.get('kind') or 'flow')
        source = edge.get('from') or edge.get('source') or edge.get('fromNode') or edge.get('sourceNodeId')
        if str(target or '') == node_id and source and (kind in {'input', 'flow'}):
            source_ids.append(str(source))
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    seen_assets = set()
    for source_id in source_ids:
        source = nodes_by_id.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = str(source.get('type') or '')
        if source_type not in {'', 'image', 'smart-image'}:
            continue
        for item in source.get('images') or []:
            if not isinstance(item, dict) or not str(item.get('asset_id') or '').strip():
                continue
            asset_id = str(item['asset_id']).strip()
            if asset_id in seen_assets:
                continue
            seen_assets.add(asset_id)
            candidates.append((asset_id, item))
    maximum_total = _svc._main('PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES') + PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES + PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES
    if len(candidates) > maximum_total:
        raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话参考素材总数量超过上限 {maximum_total}'})
    hint_counts = {'image': 0, 'video': 0, 'audio': 0}
    hint_limits = {'image': _svc._main('PROJECT_CONVERSATION_MAX_IMAGE_REFERENCES'), 'video': PROJECT_CONVERSATION_MAX_VIDEO_REFERENCES, 'audio': PROJECT_CONVERSATION_MAX_AUDIO_REFERENCES}
    for _asset_id, item in candidates:
        kind = str(item.get('kind') or '').strip().lower()
        if kind in hint_counts:
            hint_counts[kind] += 1
            if hint_counts[kind] > hint_limits[kind]:
                raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话{kind}参考素材数量超过上限 {hint_limits[kind]}'})
    references: List[Dict[str, Any]] = []
    actual_counts = {'image': 0, 'video': 0, 'audio': 0}
    for asset_id, item in candidates:
        record = _svc._main('project_store_call')('media_record', project_id, asset_id)
        kind = str(record.get('kind') or item.get('kind') or '').strip().lower()
        if kind not in actual_counts:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_invalid', 'message': '直连对话参考素材类型不受支持'})
        actual_counts[kind] += 1
        if actual_counts[kind] > hint_limits[kind]:
            raise HTTPException(status_code=400, detail={'code': 'conversation_media_count_exceeded', 'message': f'对话{kind}参考素材数量超过上限 {hint_limits[kind]}'})
        references.append({'asset_id': asset_id, 'kind': kind, 'name': str(item.get('name') or record.get('name') or record.get('filename') or asset_id)[:160]})
    return references
