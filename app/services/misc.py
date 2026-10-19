# misc domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402

def jimeng_command(clean_args, exe=None):
    exe = exe or _svc.jimeng_cli_executable()
    if _svc.jimeng_use_wsl():
        shell_line = '. ~/.profile >/dev/null 2>&1 || true; . ~/.bashrc >/dev/null 2>&1 || true; DREAMINA_BIN=$(command -v dreamina || find "$HOME" -maxdepth 4 -type f -name dreamina 2>/dev/null | head -n 1); if [ -z "$DREAMINA_BIN" ]; then echo \'dreamina CLI not found in WSL\' >&2; exit 127; fi; "$DREAMINA_BIN" ' + ' '.join((shlex.quote(arg) for arg in clean_args))
        return [exe, *_svc.jimeng_wsl_base_args(exe), '-e', 'sh', '-lc', shell_line]
    return [exe, *clean_args]

def jimeng_decode_cli_output(stdout, stderr):
    out_text = (decode_wsl_output(stdout) if _svc.jimeng_use_wsl() else stdout.decode('utf-8', errors='replace')).strip()
    err_text = (decode_wsl_output(stderr) if _svc.jimeng_use_wsl() else stderr.decode('utf-8', errors='replace')).strip()
    clean_err_text = jimeng_clean_wsl_stderr(err_text) if _svc.jimeng_use_wsl() else err_text
    return (out_text, clean_err_text)

# #212: 默认全局资源库的规范名 + 历史默认名（加载时一次性迁移）。
# 只认默认库的稳定身份 id='default' 且名字为已知历史名，用户自己重命名的库不受影响。
DEFAULT_LIBRARY_NAME = '默认全局资源库'
DEFAULT_LIBRARY_LEGACY_NAMES = {'默认资产库', '默认素材抽屉'}

def default_asset_library():
    categories = [{'id': 'characters', 'name': '角色', 'type': 'image', 'items': []}, {'id': 'scenes', 'name': '场景', 'type': 'image', 'items': []}]
    return {'active_library_id': 'default', 'libraries': [{'id': 'default', 'name': '默认全局资源库', 'type': 'asset', 'categories': categories}], 'categories': categories, 'updated_at': _svc._main('now_ms')()}

def normalize_asset_library(lib):
    if not isinstance(lib, dict):
        lib = default_asset_library()
    legacy_categories = lib.get('categories') if isinstance(lib.get('categories'), list) else None
    libraries = lib.get('libraries') if isinstance(lib.get('libraries'), list) else []
    if not libraries:
        libraries = [{'id': 'default', 'name': '默认全局资源库', 'type': 'asset', 'categories': legacy_categories or default_asset_library()['categories']}]
    for library in libraries:
        library['id'] = re.sub('[^A-Za-z0-9_-]+', '_', str(library.get('id') or f'lib_{uuid.uuid4().hex[:8]}'))[:40]
        # #212: 默认库历史显示名一次性迁移（“默认资产库”/“默认素材抽屉” → “默认全局资源库”）
        if str(library.get('id')) == 'default' and str(library.get('name')) in DEFAULT_LIBRARY_LEGACY_NAMES:
            library['name'] = DEFAULT_LIBRARY_NAME
        library['name'] = sanitize_asset_name(library.get('name') or '全局资源库', '全局资源库')
        cats = library.get('categories') if isinstance(library.get('categories'), list) else []
        cats[:] = [cat for cat in cats if isinstance(cat, dict) and cat.get('type', 'image') != 'workflow']
        for cat in cats:
            for item in cat.get('items') or []:
                migrate_asset_item_registrations(item)
        library['categories'] = cats
    active = str(lib.get('active_library_id') or libraries[0].get('id') or 'default')
    if not any((item.get('id') == active for item in libraries)):
        active = libraries[0].get('id') or 'default'
    active_library = next((item for item in libraries if item.get('id') == active), libraries[0])
    lib['libraries'] = libraries
    lib['active_library_id'] = active
    lib['categories'] = active_library.get('categories') or []
    lib['updated_at'] = int(lib.get('updated_at') or _svc._main('now_ms')())
    sort_asset_library_items(lib)
    return lib

def find_asset_library(lib, library_id=''):
    lib = normalize_asset_library(lib)
    library_id = str(library_id or lib.get('active_library_id') or '').strip()
    return next((item for item in lib.get('libraries', []) if item.get('id') == library_id), None) or (lib.get('libraries') or [None])[0]

def normalize_apimart_video_reference(value: str) -> str:
    text = str(value or '').strip()
    if valid_apimart_video_image_input(text):
        return text
    return _svc._main('local_asset_public_url')(text)

async def agnes_video_image_url(ref):
    url = str(getattr(ref, 'url', '') or '').strip()
    if not url:
        return ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    uploaded = await _svc.upload_local_video_to_cloud(url, 'auto')
    return uploaded.get('url') or ''

def list_projects():
    projects = _svc.ensure_default_project()
    counts = {}
    for rec in _svc.iter_canvas_records(include_deleted=False):
        pid = rec.get('project') or DEFAULT_PROJECT_ID
        counts[pid] = counts.get(pid, 0) + 1
    out = []
    for p in sorted(projects, key=lambda x: (int(x.get('order') or 0), x.get('created_at') or 0)):
        rec = project_record(p)
        rec['canvas_count'] = counts.get(rec['id'], 0)
        out.append(rec)
    return out

def codex_output_image_files(since_time=0):
    exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    root = os.path.abspath(_svc._main('OUTPUT_OUTPUT_DIR'))
    files = []
    try:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                continue
            mtime = os.path.getmtime(path)
            if mtime + 1 < float(since_time or 0):
                continue
            files.append((mtime, path))
    except Exception:
        return []
    return [path for _mtime, path in sorted(files, reverse=True)]

def codex_output_url_from_path(path):
    path = os.path.abspath(str(path or ''))
    root = os.path.abspath(_svc._main('OUTPUT_OUTPUT_DIR'))
    try:
        if os.path.commonpath([root, path]) == root:
            return _svc.output_url_for(os.path.basename(path), 'output')
    except Exception:
        return ''
    return ''

def jimeng_local_output_url(path, kind='image'):
    path = os.path.abspath(str(path or ''))
    if not os.path.isfile(path):
        return ''
    output_root = os.path.abspath(_svc._main('OUTPUT_OUTPUT_DIR'))
    try:
        if os.path.commonpath([output_root, path]) == output_root:
            return _svc.output_url_for(os.path.basename(path), 'output')
    except Exception:
        pass
    ext = os.path.splitext(path)[1].lower()
    allowed = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'}
    if ext not in allowed:
        ct = content_type_for_path(path)
        ext = '.mp4' if ct.startswith('video/') else '.png'
    prefix = 'jimeng_video_' if kind == 'video' else 'jimeng_'
    filename = f'{prefix}{uuid.uuid4().hex[:10]}{ext}'
    dest = _svc.output_path_for(filename, 'output')
    shutil.copyfile(path, dest)
    return _svc.output_url_for(filename, 'output')

async def jimeng_store_output_value(value, kind='image'):
    text = str(value or '').strip()
    if not text:
        return ''
    if text.startswith('/output/') or text.startswith('/assets/'):
        return text
    if text.startswith('file://'):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if os.name == 'nt' and re.match('^/[A-Za-z]:/', text):
            text = text[1:]
    if _svc.jimeng_use_wsl() and text.startswith('/mnt/'):
        text = wsl_path_to_windows(text)
    if text.startswith(('http://', 'https://')):
        if kind == 'video':
            return await _svc._main('save_remote_video_to_output')(text, prefix='jimeng_video_')
        return await _svc.save_ai_image_to_output({'type': 'url', 'value': text}, prefix='jimeng_')
    if os.path.isfile(text):
        return jimeng_local_output_url(text, kind)
    return ''

async def jimeng_query_result(submit_id, kind='image'):
    args = ['query_result', f'--submit_id={submit_id}', f"--download_dir={_svc.jimeng_cli_path_arg(_svc._main('OUTPUT_OUTPUT_DIR'))}"]
    return await _svc.run_jimeng_cli(args, timeout=min(300, jimeng_poll_seconds() + 60))

async def jimeng_store_outputs(raw, kind='image', allow_query=True):
    failure = jimeng_failure_reason(raw)
    if failure:
        raise HTTPException(status_code=502, detail=f'即梦生成失败：{failure}')
    values = jimeng_output_values(raw)
    urls = []
    for value in values:
        local_url = await jimeng_store_output_value(value, kind)
        if local_url and local_url not in urls:
            urls.append(local_url)
    if urls:
        return urls
    submit_id = jimeng_submit_id(raw)
    if submit_id and allow_query:
        queried = await jimeng_query_result(submit_id, kind)
        try:
            return await jimeng_store_outputs(queried, kind, allow_query=False)
        except HTTPException as exc:
            if getattr(exc, 'status_code', None) == 502:
                status_text = json.dumps(queried, ensure_ascii=False)[:800] if isinstance(queried, (dict, list)) else str(queried)[:800]
                raise HTTPException(status_code=502, detail=f'即梦任务已返回但没有下载到媒体：{status_text}') from exc
            raise
    status_text = json.dumps(raw, ensure_ascii=False)[:800] if isinstance(raw, (dict, list)) else str(raw)[:800]
    if submit_id:
        raise JimengPendingError(submit_id, kind, jimeng_queue_info(raw), raw)
    raise HTTPException(status_code=502, detail=f'即梦 CLI 未返回可用媒体结果：{status_text}')

async def jimeng_prepare_local_media(ref_url, kind='image'):
    text = str(ref_url or '').strip()
    if not text:
        return ('', [])
    adapter_path = _svc.local_media_adapter_path(text, kind)
    if adapter_path:
        return (adapter_path, [])
    if text.startswith('/output/') or text.startswith('/assets/'):
        path = _svc._main('output_file_from_url')(text)
        if path:
            return (path, [])
        raise HTTPException(status_code=404, detail=f'即梦参考素材不存在：{text}')
    if text.startswith('file://'):
        path = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if os.name == 'nt' and re.match('^/[A-Za-z]:/', path):
            path = path[1:]
        if os.path.isfile(path):
            return (path, [])
    if os.path.isfile(text):
        return (text, [])
    suffix = '.mp4' if kind == 'video' else '.mp3' if kind == 'audio' else '.png'
    temp_paths = []
    if text.startswith('data:'):
        if ';base64,' not in text:
            raise HTTPException(status_code=400, detail='即梦参考素材 data URL 缺少 base64 数据')
        header, encoded = text.split(';base64,', 1)
        mime = header.split(':', 1)[1].split(';', 1)[0] if ':' in header else ''
        suffix = mimetypes.guess_extension(mime) or suffix
        fd, path = tempfile.mkstemp(prefix='jimeng_ref_', suffix=suffix)
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
            fd, path = tempfile.mkstemp(prefix='jimeng_ref_', suffix=suffix)
            with os.fdopen(fd, 'wb') as f:
                f.write(response.content)
            temp_paths.append(path)
            return (path, temp_paths)
    raise HTTPException(status_code=400, detail=f'即梦 CLI 只支持本地文件参考素材，无法读取：{text[:120]}')

def apimart_video_reference_error(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return '空的视频地址'
    if text.startswith(('/output/', '/assets/')):
        if not _svc._main('output_file_from_url')(text):
            return '这是本地项目文件路径，但后端没有找到对应文件，请重新上传视频后再试。'
        return '这是本地项目文件，APIMart 无法访问 127.0.0.1/局域网路径；请在 API/.env 配置 PUBLIC_MEDIA_BASE_URL 或 PUBLIC_BASE_URL 为可公网访问的媒体地址（例如内网穿透 HTTPS 地址），或改用公网 http/https 视频 URL、审核后的 asset:// 地址。'
    if text.startswith('data:') or text.startswith('blob:') or text.startswith('file:'):
        return 'APIMart 的 video_urls 不支持 data/blob/file 地址；请改用公网 http/https 视频 URL，或审核后的 asset:// 地址。'
    return 'APIMart 的 video_urls 只支持公网 http/https URL 或 asset:// 私域素材 URL。'

async def build_online_image_result(payload: OnlineImageRequest):
    require_online_image_project_media_scope(payload)
    provider = _svc._main('get_api_provider')(payload.provider_id)
    default_model = (provider.get('image_models') or [IMAGE_MODEL])[0]
    model = selected_model(payload.model, default_model)
    request_size = snap_size_to_multiple(payload.size, 16)
    refs = [ref.dict() for ref in payload.reference_images if ref.url]
    image_refs = image_references(refs)
    count = max(1, min(8, int(payload.n or 1)))

    async def generate_one():
        # #41: 受控并发 + 429 退避（共享 helper）。#40 的全串行 Semaphore(1) 会让
        # count=8 变成「间隔很久一张一张传」，这里恢复并发连发。
        image_data, raw_item = await _svc._main('run_provider_generation_with_backoff')(
            provider['id'], _svc._main('generate_ai_image'),
            payload.prompt, request_size, payload.quality, model, image_refs, provider['id'],
        )
        try:
            image_items = extract_images(raw_item) if isinstance(raw_item, dict) else [image_data]
        except HTTPException:
            image_items = [image_data]
        local_urls = []
        local_items = []
        for item in image_items:
            local_url = await _svc.save_ai_image_to_output(item, prefix='online_')
            if local_url:
                local_urls.append(local_url)
                local_items.append(_svc.image_output_meta(local_url, item))
        return (local_urls, local_items, raw_item)
    try:
        generated = await asyncio.gather(*(generate_one() for _ in range(count)))
    except httpx.HTTPStatusError as exc:
        log_net_error(f"生图 HTTP状态错误 provider={provider.get('id')} model={model} size={request_size}", exc)
        text = exc.response.text or ''
        friendly = friendly_image_error_detail(text, request_size, model)
        detail = friendly or f'上游生图接口错误：{text[:300]}'
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        log_net_error(f"生图 网络/TLS错误 provider={provider.get('id')} model={model}", exc)
        raise HTTPException(status_code=502, detail=f'请求上游生图接口失败：{exc}') from exc
    local_urls = [url for urls, _items, _raw in generated for url in urls or [] if url]
    local_items = [item for _urls, items, _raw in generated for item in items or [] if item.get('url')]
    raw = generated[0][2] if generated else {}
    if not local_urls:
        provider_name = provider.get('name') or provider['id']
        raw_text = json.dumps(raw, ensure_ascii=False)[:800] if isinstance(raw, (dict, list)) else str(raw)[:800]
        raise HTTPException(status_code=502, detail=f'{provider_name} 没有返回图片：{raw_text}')
    result = {'prompt': payload.prompt, 'images': local_urls, 'image_items': local_items, 'timestamp': time.time(), 'type': 'online', 'model': model, 'provider_id': provider['id'], 'provider_name': provider.get('name') or provider['id'], 'task_id': extract_task_id(raw) if isinstance(raw, dict) else None, 'request_id': raw.get('id') if isinstance(raw, dict) else None, 'params': {'provider_id': provider['id'], 'model': model, 'size': request_size, 'requested_size': payload.size, 'quality': payload.quality, 'n': count, 'reference_images': refs}, 'raw_usage': raw.get('usage') if isinstance(raw, dict) else None}
    _svc.save_to_history(result)
    if GLOBAL_LOOP:
        asyncio.run_coroutine_threadsafe(_svc._main('manager').broadcast_new_image(result), GLOBAL_LOOP)
    return result

def project_video_references(project_id: str, asset_ids: List[str], kind: str) -> List[Dict[str, Any]]:
    references = []
    for index, asset_id in enumerate(asset_ids):
        try:
            record = _svc._main('project_store_call')('media_record', project_id, asset_id)
        except ProjectNotFound as exc:
            raise ProjectStoreError(f'参考资源不属于当前项目: {asset_id}') from exc
        if record.get('kind') != kind:
            raise ProjectStoreError(f"参考资源不是{ {'image': '图片', 'video': '视频', 'audio': '音频'}[kind]}: {asset_id}")
        source_path = Path(str(record.get('path') or '')).resolve()
        if not source_path.is_file():
            raise ProjectNotFound(f'项目媒体不存在: {asset_id}')
        references.append({'asset_id': asset_id, 'kind': kind, 'name': record.get('name') or f'reference-{index + 1}', 'mime': record.get('mime_type') or 'application/octet-stream', 'path': str(source_path), 'adapter_url': project_media_adapter_reference(project_id, asset_id)})
    return references

async def cleanup_project_generation_orphans(project_id: str):

    def cleanup():
        with project_lifecycle_lock(project_id):
            with CANVAS_TASK_LOCK:
                if any((item.get('type') in {'project-online-image', 'project-online-video'} and item.get('project_id') == project_id and (item.get('status') in {'queued', 'running'}) for item in CANVAS_TASKS.values())):
                    return
                candidates = PROJECT_IMAGE_GENERATION_ORPHANS.pop(project_id, set()) | PROJECT_VIDEO_GENERATION_ORPHANS.pop(project_id, set())
            for asset_id in candidates:
                try:
                    _svc._main('project_store_call')('delete_media_if_unreferenced', project_id, asset_id)
                except Exception:
                    continue
    await asyncio.to_thread(cleanup)
