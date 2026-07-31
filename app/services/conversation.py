# conversation domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402

def load_conversation(user_id, conversation_id):
    path = _svc._main('conversation_path')(user_id, conversation_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='对话不存在')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

async def codex_reference_paths(reference_images=None):
    paths = []
    temp_paths = []
    try:
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            url = ref.get('url') if isinstance(ref, dict) else getattr(ref, 'url', '')
            if not url:
                continue
            path, created = await _svc._main('codex_prepare_local_media')(url)
            if path:
                paths.append(path)
            temp_paths.extend(created)
        return (paths, temp_paths)
    except Exception:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass
        raise

async def run_gemini_cli(prompt, model='', timeout=None, allow_tools=False):
    exe = _svc._main('gemini_cli_executable')()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 Antigravity CLI。请先安装 Google Antigravity CLI，并完成 agy 登录。')
    timeout_seconds = timeout or gemini_cli_timeout()
    if is_antigravity_cli(exe):
        args = [exe, '--print-timeout', f'{int(timeout_seconds)}s']
        selected = gemini_cli_model(model)
        if selected and selected != 'auto':
            args.extend(['--model', selected])
        if allow_tools:
            args.append('--dangerously-skip-permissions')
        args.extend(['-p', str(prompt or '')])
    else:
        args = [exe, '--model', gemini_cli_model(model), '--output-format', 'json', '--skip-trust']
        if allow_tools:
            args.extend(['--approval-mode', 'yolo'])
        args.extend(['--prompt', str(prompt or '')])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(*args, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        raise HTTPException(status_code=504, detail=f'{_svc.gemini_cli_display_name(exe)} 执行超时。可设置 GEMINI_CLI_TIMEOUT 增大等待时间。') from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f'未找到 {_svc.gemini_cli_display_name(exe)}：{exe}') from exc
    out_text, err_text = codex_decode_output(stdout, stderr)
    raw, text = gemini_cli_parse_stdout(out_text)
    if proc.returncode != 0:
        message = err_text or out_text or f'exit={proc.returncode}'
        raise HTTPException(status_code=502, detail=f'{_svc.gemini_cli_display_name(exe)} 调用失败：{message[:1200]}')
    return {'text': text or out_text, 'raw': raw, '_stdout': out_text, '_stderr': err_text}

async def gemini_cli_reference_paths(reference_images=None):
    return await codex_reference_paths(reference_images)

async def gemini_cli_chat_text(payload, history_messages=None):
    temp_paths = []
    try:
        prompt, image_values = gemini_cli_chat_prompt(payload, history_messages)
        image_paths, temp_paths = await gemini_cli_reference_paths(image_values)
        if image_paths:
            prompt = f'{prompt}\n\n可参考的本地图片路径：\n' + '\n'.join(image_paths)
        prompt = f'{prompt}\n\n请直接回答用户，输出纯文本，不要修改项目文件。'
        raw = await run_gemini_cli(prompt, model=getattr(payload, 'model', '') or GEMINI_CLI_DEFAULT_CHAT_MODELS[0], timeout=gemini_cli_timeout(), allow_tools=False)
        text = str(raw.get('text') or '').strip()
        return (text or f'{_svc.gemini_cli_display_name()} 返回了空回复。', raw)
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

async def run_jimeng_cli(args, timeout=120, raw_text=False):
    exe = _svc.jimeng_cli_executable()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 dreamina CLI。请先安装：curl -fsSL https://jimeng.jianying.com/cli | bash，并完成 dreamina login。')
    clean_args = [str(arg) for arg in args if str(arg) != '']
    command = _svc.jimeng_command(clean_args, exe)
    try:
        proc = await asyncio.create_subprocess_exec(*command, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"即梦 CLI 执行超时：{' '.join(command[:3])}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f'未找到即梦 CLI：{exe}') from exc
    out_text, clean_err_text = _svc.jimeng_decode_cli_output(stdout, stderr)
    if proc.returncode != 0:
        message = clean_err_text or out_text or f'exit={proc.returncode}'
        raise HTTPException(status_code=502, detail=f'即梦 CLI 调用失败：{message[:1000]}')
    if raw_text:
        return {'_stdout': out_text, '_stderr': clean_err_text}
    raw = jimeng_extract_json(f'{out_text}\n{clean_err_text}'.strip())
    if isinstance(raw, dict):
        raw.setdefault('_stdout', out_text)
        if clean_err_text:
            raw.setdefault('_stderr', clean_err_text)
    return raw

def gemini_reference_part(ref):
    value = _svc._main('reference_to_data_url')(ref, max_size=1536)
    if not value:
        return None
    if isinstance(value, str) and value.startswith('data:image/') and (';base64,' in value):
        header, encoded = value.split(';base64,', 1)
        mime_type = header.replace('data:', '', 1) or 'image/png'
        return {'inlineData': {'mimeType': mime_type, 'data': encoded}}
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        return {'fileData': {'mimeType': 'image/png', 'fileUri': value}}
    return None

def upstream_message_from_record(item):
    role = item.get('role')
    if role not in {'user', 'assistant'} or item.get('type') == 'image':
        return None
    attachments = item.get('attachments') or []
    if attachments and role == 'user':
        text = item.get('content', '')
        blocks = _svc._main('attachment_text_blocks')(attachments)
        if blocks:
            text = f'{text}\n\n以下是用户上传附件的可读内容，请在回答时参考：\n\n' + '\n\n---\n\n'.join(blocks)
        content = [{'type': 'text', 'text': text}]
        image_urls = []
        for ref in image_references(attachments[:CHAT_ATTACHMENT_MAX]):
            url = _svc._main('reference_to_data_url')(ref)
            if url:
                image_urls.append(url)
        image_urls.extend(_svc._main('attachment_embedded_image_data_urls')(attachments[:CHAT_ATTACHMENT_MAX], max_images=max(0, CHAT_ATTACHMENT_MAX - len(image_urls))))
        for url in image_urls[:CHAT_ATTACHMENT_MAX]:
            content.append({'type': 'image_url', 'image_url': {'url': url}})
        return {'role': role, 'content': content}
    return {'role': role, 'content': item.get('content', '')}

async def finalize_visible_project_conversation_terminal(project_id: str, node_id: str, request_id: str) -> None:
    """Make terminal API visibility include all recovery-state cleanup."""
    forget_project_conversation_task(project_id, node_id, request_id)
    forget_project_conversation_ready_result(project_id, node_id, request_id)
    await asyncio.to_thread(_svc._main('forget_project_conversation_durable'), project_id, node_id, request_id)

def conversation_path(user_id, conversation_id):
    cleaned = re.sub('[^a-zA-Z0-9_-]', '', conversation_id or '')
    if not cleaned:
        raise HTTPException(status_code=400, detail='无效的对话 ID')
    return os.path.join(_svc.user_dir(user_id), f'{cleaned}.json')

def save_conversation(user_id, conversation):
    with CANVAS_LOCK, CONVERSATION_LOCK:
        path = conversation_path(user_id, conversation['id'])
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(conversation, f, ensure_ascii=False, indent=2)

def new_conversation(user_id, title='新对话'):
    timestamp = _svc._main('now_ms')()
    conversation = {'id': uuid.uuid4().hex, 'title': (title or '新对话')[:80], 'created_at': timestamp, 'updated_at': timestamp, 'messages': []}
    save_conversation(user_id, conversation)
    return conversation

def list_conversations(user_id):
    records = []
    for filename in os.listdir(_svc.user_dir(user_id)):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(_svc.user_dir(user_id), filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        messages = data.get('messages', [])
        last_message = next((m for m in reversed(messages) if m.get('role') != 'system'), None)
        records.append({'id': data.get('id'), 'title': data.get('title', '新对话'), 'created_at': data.get('created_at', 0), 'updated_at': data.get('updated_at', 0), 'last_message': (last_message or {}).get('content', '')})
    return sorted(records, key=lambda item: item['updated_at'], reverse=True)

def resolve_chat_provider(provider: str, model: str, ms_model: str):
    if provider == 'modelscope':
        clean_token = _svc.modelscope_api_key()
        if not clean_token:
            raise HTTPException(status_code=400, detail='未配置 ModelScope API Key，请在 API 设置中填写。')
        base = _svc.modelscope_api_root()
        hdrs = {'Authorization': bearer_auth_value(clean_token), 'Content-Type': 'application/json'}
        mdl = selected_model(ms_model or model, MODELSCOPE_CHAT_MODELS[0] if MODELSCOPE_CHAT_MODELS else 'MiniMax/MiniMax-M2.7')
        return (base, hdrs, mdl)
    api_provider = _svc._main('get_api_provider')(provider or '')
    if is_codex_provider(api_provider):
        raise HTTPException(status_code=400, detail='OpenAI CLI 使用本机 codex 登录态，不需要 API Key。请使用画布/聊天里的 OpenAI CLI 专用通道。')
    if is_gemini_cli_provider(api_provider):
        raise HTTPException(status_code=400, detail='Antigravity CLI 使用本机 agy 登录态，不需要 API Key。请使用画布/聊天里的 Antigravity CLI 专用通道。')
    base_root = (api_provider.get('base_url') or _svc._main('AI_BASE_URL')).rstrip('/')
    if not base_root:
        raise HTTPException(status_code=400, detail=f"{api_provider.get('name') or api_provider['id']} 未配置 Base URL")
    default_model = preferred_chat_model(api_provider)
    mdl = selected_model(model, default_model)
    protocol = effective_protocol(api_provider, mdl)
    if protocol == 'gemini':
        base = base_root if base_root.endswith('/v1beta') else base_root + '/v1beta'
    elif protocol == 'volcengine':
        base = base_root if base_root.endswith('/api/v3') else base_root + '/api/v3'
    elif protocol == 'runninghub':
        base = RUNNINGHUB_LLM_BASE_URL
    else:
        base = base_root if base_root.endswith('/v1') else base_root + '/v1'
    hdrs = _svc._main('api_headers')(provider=api_provider, model=mdl)
    return (base, hdrs, mdl)

async def run_codex_cli(prompt, model='', image_paths=None, timeout=None, output_last_message=True):
    exe = _svc._main('codex_cli_executable')()
    if not exe:
        raise HTTPException(status_code=400, detail='未找到 OpenAI Codex CLI。请先运行 CLI/windows/openai/install_openai_codex_cli.bat，并完成 codex 登录。')
    image_paths = [str(path) for path in image_paths or [] if path and os.path.isfile(str(path))]
    last_path = ''
    args = [exe, 'exec', '--cd', BASE_DIR, '--sandbox', 'workspace-write', '--skip-git-repo-check']
    exec_model = codex_model_for_exec(model)
    if exec_model:
        args.extend(['--model', exec_model])
    for path in image_paths:
        args.extend(['--image', path])
    if output_last_message:
        fd, last_path = tempfile.mkstemp(prefix='codex_last_', suffix='.txt', dir=_svc._main('OUTPUT_OUTPUT_DIR'))
        os.close(fd)
        args.extend(['--output-last-message', last_path])
    args.append('-')
    prompt_bytes = str(prompt or '').encode('utf-8')
    try:
        proc = await asyncio.create_subprocess_exec(*args, cwd=BASE_DIR, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=prompt_bytes), timeout=timeout or codex_timeout())
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail='OpenAI Codex CLI 执行超时。可设置 CODEX_CLI_TIMEOUT 增大等待时间。') from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f'未找到 OpenAI Codex CLI：{exe}') from exc
    out_text, err_text = codex_decode_output(stdout, stderr)
    last_text = ''
    if last_path and os.path.exists(last_path):
        try:
            with open(last_path, 'r', encoding='utf-8-sig') as f:
                last_text = f.read().strip()
        except Exception:
            last_text = ''
        try:
            os.remove(last_path)
        except Exception:
            pass
    if proc.returncode != 0:
        message = err_text or out_text or last_text or f'exit={proc.returncode}'
        raise HTTPException(status_code=502, detail=f'OpenAI Codex CLI 调用失败：{message[:1200]}')
    return {'text': last_text or out_text, '_stdout': out_text, '_stderr': err_text}

async def codex_prepare_local_media(ref_url):
    text = str(ref_url or '').strip()
    if not text:
        return ('', [])
    if text.startswith(('/output/', '/assets/', '/api/project-library/')):
        path = _svc._main('output_file_from_url')(text)
        if path:
            return (path, [])
        raise HTTPException(status_code=404, detail=f'OpenAI CLI 参考素材不存在：{text}')
    if text.startswith('file://'):
        path = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if os.name == 'nt' and re.match('^/[A-Za-z]:/', path):
            path = path[1:]
        if os.path.isfile(path):
            return (path, [])
    if os.path.isfile(text):
        return (text, [])
    temp_paths = []
    suffix = '.png'
    if text.startswith('data:'):
        if ';base64,' not in text:
            raise HTTPException(status_code=400, detail='OpenAI CLI 参考素材 data URL 缺少 base64 数据')
        header, encoded = text.split(';base64,', 1)
        mime = header.split(':', 1)[1].split(';', 1)[0] if ':' in header else ''
        suffix = mimetypes.guess_extension(mime) or suffix
        fd, path = tempfile.mkstemp(prefix='codex_ref_', suffix=suffix)
        with os.fdopen(fd, 'wb') as f:
            f.write(base64.b64decode(encoded))
        temp_paths.append(path)
        return (path, temp_paths)
    if text.startswith(('http://', 'https://')):
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0), follow_redirects=True) as client:
            response = await client.get(text)
            response.raise_for_status()
            clean_path = urllib.parse.urlparse(text).path
            suffix = os.path.splitext(clean_path)[1] or mimetypes.guess_extension(response.headers.get('content-type', '')) or suffix
            fd, path = tempfile.mkstemp(prefix='codex_ref_', suffix=suffix)
            with os.fdopen(fd, 'wb') as f:
                f.write(response.content)
            temp_paths.append(path)
            return (path, temp_paths)
    raise HTTPException(status_code=400, detail=f'OpenAI CLI 无法读取参考素材：{text[:120]}')

async def codex_chat_text(payload, history_messages=None):
    image_paths = []
    temp_paths = []
    try:
        image_values = []
        if hasattr(payload, 'images'):
            image_values.extend([{'url': item} for item in getattr(payload, 'images', None) or [] if item])
        if hasattr(payload, 'reference_images'):
            image_values.extend([ref.dict() for ref in getattr(payload, 'reference_images', None) or [] if getattr(ref, 'url', '')])
        image_paths, temp_paths = await codex_reference_paths(image_values)
        raw = await run_codex_cli(codex_chat_prompt(payload, history_messages), model=getattr(payload, 'model', '') or CODEX_DEFAULT_CHAT_MODELS[0], image_paths=image_paths, timeout=codex_timeout(), output_last_message=True)
        text = str(raw.get('text') or '').strip()
        return (text or 'Codex CLI 返回了空回复。', raw)
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def pick_chat_image_provider(provider_id='', fallback_id=''):
    providers = [p for p in _svc._main('load_api_providers')() if p.get('enabled', True) and (p.get('image_models') or [])]
    for target in (provider_id, fallback_id):
        clean = str(target or '').strip().lower()
        if clean:
            matched = next((p for p in providers if p.get('id') == clean), None)
            if matched:
                return matched
    if providers:
        primary = next((p for p in providers if p.get('primary')), None)
        return primary or providers[0]
    return _svc._main('get_api_provider')(provider_id or fallback_id or 'comfly')

async def decide_chat_agent_action(payload, conversation, refs):
    has_previous_image = bool(latest_chat_image_refs(conversation, 1))
    fallback = heuristic_agent_decision(payload.message, refs, has_previous_image)
    provider_cfg = _svc._main('get_api_provider')(payload.provider) if payload.provider not in ('modelscope',) else {}
    if is_codex_provider(provider_cfg):
        fallback['router_model'] = selected_model(payload.model, (provider_cfg.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        return fallback
    if is_gemini_cli_provider(provider_cfg):
        fallback['router_model'] = selected_model(payload.model, (provider_cfg.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        return fallback
    chat_base, chat_hdrs, model = _svc._main('resolve_chat_provider')(payload.provider, payload.model, payload.ms_model)
    history = conversation['messages'][-MAX_HISTORY_MESSAGES:]
    custom_system_prompt = str(getattr(payload, 'system_prompt', '') or '').strip()
    system = '你是图片创作聊天 Agent 的意图路由器。只返回 JSON，不要 Markdown。\naction 只能是 chat、generate_image、edit_image。\nchat: 普通问答或不需要调用图片工具。\ngenerate_image: 用户要求生成、绘制、创建新图片。\nedit_image: 用户要求修改参考图、上一张图、刚才生成的图，或上传了参考图并要求基于它变化。\nprompt 是交给生图/改图工具的完整中文提示词；普通聊天时也填用户原话。\nreply 是可选的短状态文本。'
    upstream_messages = [{'role': 'system', 'content': system}]
    for item in history[-10:]:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)
    upstream_messages.append({'role': 'user', 'content': f"""当前用户输入：{payload.message}\n用户设置的系统提示词：{custom_system_prompt or '无'}\n本次上传参考图数量：{len(refs)}\n对话中是否已有上一张生成图：{('是' if has_previous_image else '否')}\n请返回 JSON，例如 {{"action":"generate_image","prompt":"...","reply":"..."}}"""})
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {'model': model, 'messages': upstream_messages}
            if is_apimart_provider(provider_cfg):
                req_body['stream'] = False
            response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            raw = response.json()
            decision = parse_agent_decision(text_from_chat_response(raw), payload.message, refs, has_previous_image)
            decision['router_model'] = model
            return decision
    except Exception as exc:
        print(f'[chat-agent] intent router fallback: {exc}')
        fallback['router_model'] = model
        return fallback

async def build_chat_text_reply(payload, conversation):
    provider_cfg = _svc._main('get_api_provider')(payload.provider) if payload.provider not in ('modelscope',) else {}
    if is_codex_provider(provider_cfg):
        model = selected_model(payload.model, (provider_cfg.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await codex_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
        return {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _svc._main('now_ms')(), 'model': model, 'raw_usage': None, 'raw': raw}
    if is_gemini_cli_provider(provider_cfg):
        model = selected_model(payload.model, (provider_cfg.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload.model = model
        text, raw = await gemini_cli_chat_text(payload, conversation['messages'][-MAX_HISTORY_MESSAGES:])
        return {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text, 'created_at': _svc._main('now_ms')(), 'model': model, 'raw_usage': None, 'raw': raw}
    chat_base, chat_hdrs, model = _svc._main('resolve_chat_provider')(payload.provider, payload.model, payload.ms_model)
    is_apimart = is_apimart_provider(provider_cfg)
    upstream_messages = [{'role': 'system', 'content': chat_system_prompt(payload)}]
    for item in conversation['messages'][-MAX_HISTORY_MESSAGES:]:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {'model': model, 'messages': upstream_messages}
            if is_apimart:
                req_body['stream'] = False
            response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ''
        friendly = friendly_chat_error_detail(body, model, provider_cfg)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f'上游接口错误：{body}') from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'请求上游接口失败：{exc}') from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else raw
    return {'id': uuid.uuid4().hex, 'role': 'assistant', 'content': text_from_chat_response(raw).strip() or '接口返回了空回复。', 'created_at': _svc._main('now_ms')(), 'model': model, 'raw_usage': raw_data.get('usage') if isinstance(raw_data, dict) else None}

async def persist_project_conversation_pending(project_id: str, node_id: str, user_message: Dict[str, Any], *, node_adapter: Optional[Dict[str, str]]=None, enforce_admission: bool=False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    request_id = str(user_message.get('request_id') or '')
    for _attempt in range(8):
        try:

            def persist_once():
                with PROJECT_CONVERSATION_ADMISSION_LOCK if enforce_admission else nullcontext():
                    with project_lifecycle_lock(str(project_id)):
                        opened = _svc._main('project_store_call')('open_project', project_id)
                        current_canvas = opened['canvas']
                        conversation = conversation_node_by_id(current_canvas, node_id)
                        if not isinstance(conversation, dict) or conversation.get('type') != 'smart-conversation':
                            raise HTTPException(status_code=400, detail='目标节点不是对话节点')
                        existing = conversation_task_snapshot(current_canvas, node_id, request_id)
                        if existing:
                            return (current_canvas, existing)
                        if enforce_admission:
                            runner = _svc._main('PROJECT_CONVERSATION_RUNNER')
                            if runner is not None:
                                pending_keys = _svc._main('conversation_pending_task_keys')(project_id, canvas=current_canvas, include_all_projects=True)
                                admission_error = runner.admission_error(project_id, node_id, request_id, canvas=current_canvas, pending_keys=pending_keys)
                                if admission_error:
                                    raise HTTPException(status_code=429, detail=admission_error)
                        updated_node = copy.deepcopy(conversation)
                        messages = [item for item in updated_node.get('messages') or [] if isinstance(item, dict)]
                        updated_node['messages'] = (messages + [copy.deepcopy(user_message)])[-500:]
                        if node_adapter:
                            updated_node['conversation_adapter'] = copy.deepcopy(node_adapter)
                        _svc._main('project_store_call')('apply_mutations', project_id, [{'op': 'upsert_node', 'node': updated_node}], base_revision=int(current_canvas.get('revision') or 0))
                        _svc._main('project_store_call')('flush', project_id)
                        saved = _svc._main('project_store_call')('open_project', project_id)
                        snapshot = conversation_task_snapshot(saved['canvas'], node_id, request_id)
                        if not snapshot:
                            raise ProjectStoreError('对话任务登记后无法读取')
                        return (saved['canvas'], snapshot)
            return await asyncio.to_thread(persist_once)
        except RevisionConflict:
            continue
    raise HTTPException(status_code=409, detail='项目持续被修改，对话任务尚未登记')
