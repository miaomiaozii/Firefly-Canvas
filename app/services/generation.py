# generation domain — extracted from app/services.py (W1d)
import sys
import time
import threading  # noqa: E402
import random  # noqa: E402
import email.utils  # noqa: E402
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
from app.core import _local_upload_kind_ext, banana_model_family, looks_like_gemini_image_model  # noqa: E402,F401
from firefly_diagnostics import emit as _diag_emit
import logging
logger = logging.getLogger(__name__)

async def wait_for_image_task(client, task_id, provider=None):
    is_apimart = is_apimart_provider(provider)
    timeout = APIMART_IMAGE_TASK_TIMEOUT if is_apimart else IMAGE_TASK_TIMEOUT
    interval = APIMART_IMAGE_POLL_INTERVAL if is_apimart else IMAGE_POLL_INTERVAL
    initial_delay = APIMART_IMAGE_INITIAL_POLL_DELAY if is_apimart else 0
    deadline = time.monotonic() + timeout
    last_payload = {}
    unknown_streak = 0
    while time.monotonic() < deadline:
        if initial_delay:
            await asyncio.sleep(min(initial_delay, max(0.0, deadline - time.monotonic())))
            initial_delay = 0
            if time.monotonic() >= deadline:
                break
        last_payload = await _svc._main('fetch_image_task_payload')(client, task_id, provider)
        status = image_task_status(last_payload)
        if not status:
            try:
                if extract_image(last_payload):
                    return last_payload
            except HTTPException:
                pass
        if status in IMAGE_TASK_SUCCESS_STATUSES:
            return last_payload
        if status in IMAGE_TASK_FAILED_STATUSES:
            raise HTTPException(status_code=502, detail=f'生图任务失败：{image_task_fail_reason(last_payload)}')
        if status in IMAGE_TASK_UNKNOWN_STATUSES:
            # #235：连续 N 次 provider 明确返回未知/不存在 → 终态 fail（文案显式带 task ID），
            # 不再空转到超时 504；单次抖动容忍——出现可识别状态即重置计数。
            unknown_streak += 1
            if unknown_streak >= IMAGE_TASK_UNKNOWN_TERMINAL_AFTER:
                raise HTTPException(status_code=502, detail=f'生图任务不存在或状态未知（task_id={task_id}，连续 {unknown_streak} 次查询返回 {status}），请确认任务 ID 后显式重试')
        else:
            unknown_streak = 0
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raw_text = json.dumps(last_payload, ensure_ascii=False)[:800] if last_payload else ''
    extra = f'，最后响应：{raw_text}' if raw_text else ''
    raise HTTPException(status_code=504, detail=f'生图任务超时（已等待 {int(timeout)} 秒），task_id={task_id}{extra}')

def smart_owned_result_items(images: List[Any], paths: List[str]) -> List[Any]:
    """Return generated results, including legacy results without the marker."""
    return [item for item in images if isinstance(item, dict) and item.get('loopInputPreview') is not True and (item.get('generatedResult') is True or any((_svc._main('json_references_media_path')(item, path) for path in paths)))]

def generate_video_preview_image(path: str, width: int) -> Image.Image:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('未找到 ffmpeg，无法生成视频预览图')
    fd, frame_path = tempfile.mkstemp(prefix='media_preview_frame_', suffix='.jpg')
    os.close(fd)
    try:
        cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-ss', '0.5', '-i', path, '-frames:v', '1', '-vf', f"scale='min({width},iw)':-2", frame_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not os.path.exists(frame_path) or os.path.getsize(frame_path) <= 0:
            raise RuntimeError((proc.stderr or 'ffmpeg 未能抽取视频首帧').strip()[:300])
        with Image.open(frame_path) as frame:
            img = ImageOps.exif_transpose(frame).copy()
            img.thumbnail((width, width), Image.LANCZOS)
            return img.convert('RGB')
    finally:
        try:
            os.remove(frame_path)
        except OSError:
            pass

async def classify_image_with_provider(abs_path, provider_id='', model='', ms_model='', prompt=''):
    text, resolved_model = await _svc._main('caption_image_with_provider')(abs_path, _svc.asset_classification_prompt(prompt), provider_id or _svc._main('get_primary_provider_id')(), model, ms_model)
    classification = _svc.parse_asset_classification_text(text)
    classification['model'] = resolved_model
    classification['provider'] = provider_id or _svc._main('get_primary_provider_id')()
    return classification

async def upload_media_for_apimart(client, provider, ref_url: str, kind: str) -> str:
    """按 kind 分派到对应的 APIMart 上传器，拿回上游可用的 http/https/asset:// URL。"""
    if kind == 'video':
        return await _svc._main('upload_video_for_apimart')(client, provider, ref_url)
    if kind == 'audio':
        return await _svc._main('upload_audio_for_apimart')(client, provider, ref_url)
    return await _svc._main('upload_image_for_apimart')(client, provider, ref_url)

async def submit_volcengine_avatar_asset(public_url: str, name: str, kind: str, project_name: str='default', group_name: str='') -> str:
    """把公网可访问素材提交到火山 Ark 私域素材库（异步）。返回 Asset Id 作为任务 ID。"""
    async with httpx.AsyncClient(timeout=120) as client:
        group_id = await _svc.volcengine_ensure_asset_group(client, project_name, group_name)
        created = await _svc.volcengine_ark_asset_call(client, 'CreateAsset', {'GroupId': group_id, 'URL': public_url, 'AssetType': apimart_avatar_asset_type(kind), 'Name': (name or 'asset')[:60], 'ProjectName': (project_name or 'default').strip() or 'default'})
    asset_id = str(created.get('Id') or '').strip()
    if not asset_id:
        raise HTTPException(status_code=502, detail=f'火山 CreateAsset 未返回 Asset Id：{str(created)[:200]}')
    return asset_id

async def check_volcengine_avatar_task(asset_id: str, project_name: str='default') -> Dict[str, Any]:
    """查询一次火山素材状态。返回 {status: Active/Processing/Failed, asset_uri, detail}。"""
    async with httpx.AsyncClient(timeout=60) as client:
        info = await _svc.volcengine_ark_asset_call(client, 'GetAsset', {'Id': asset_id, 'ProjectName': (project_name or 'default').strip() or 'default'})
    status = str(info.get('Status') or '').strip()
    if status == 'Active':
        return {'status': 'Active', 'asset_uri': f'asset://{asset_id}', 'detail': ''}
    if status == 'Failed':
        return {'status': 'Failed', 'asset_uri': '', 'detail': '火山素材处理失败，无法用于推理。'}
    return {'status': 'Processing', 'asset_uri': '', 'detail': '火山素材处理中'}

async def upload_local_video_to_cloud(ref_url: str, service: str='auto') -> Dict[str, str]:
    ref_url = str(ref_url or '').strip()
    if ref_url.startswith('http://') or ref_url.startswith('https://'):
        return {'url': ref_url, 'source': ref_url, 'service': 'existing'}
    path = _svc._main('local_media_path_for_cloud_upload')(ref_url)
    service = str(service or os.getenv('CLOUD_VIDEO_UPLOAD_SERVICE', 'auto') or 'auto').strip().lower()
    if service in {'litterbox', 'catbox'}:
        return await upload_video_to_litterbox(path, ref_url)
    if service in {'temp', 'temp.sh', 'tempsh'}:
        return await upload_video_to_temp_sh(path, ref_url)
    errors = []
    for name, func in (('litterbox', upload_video_to_litterbox), ('temp.sh', upload_video_to_temp_sh)):
        try:
            return await func(path, ref_url)
        except HTTPException as exc:
            errors.append(f'{name}: {exc.detail}')
    raise HTTPException(status_code=502, detail='云端上传失败：' + '；'.join(errors))

async def upload_local_video_to_temp_sh(ref_url: str) -> Dict[str, str]:
    return await upload_local_video_to_cloud(ref_url, 'auto')

async def generate_modelscope_provider_image(prompt, size, model, reference_images=None, provider=None):
    clean_token = _svc.modelscope_api_key()
    if not clean_token:
        raise HTTPException(status_code=400, detail='未配置 ModelScope API Key，请在 API 设置中填写。')
    width, height = parse_size_pair(size)
    refs = []
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        if not ref.get('url'):
            continue
        refs.append(_svc.modelscope_image_url(ref.get('url', ''), max_size=1536))
    headers = {'Authorization': f'Bearer {clean_token}', 'Content-Type': 'application/json', 'X-ModelScope-Async-Mode': 'true'}
    payload = {'model': selected_model(model, MODELSCOPE_DEFAULT_IMAGE_MODEL), 'prompt': prompt.strip()}
    if width and height:
        payload['width'] = width
        payload['height'] = height
        payload['size'] = f'{width}x{height}'
    if refs:
        payload['image_url'] = refs
    api_root = modelscope_image_api_root()
    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
        submit_res = await client.post(f'{api_root}/images/generations', headers=headers, json=payload)
        submit_res.raise_for_status()
        raw = submit_res.json()
        task_id = raw.get('task_id')
        if not task_id:
            try:
                return (extract_image(raw), raw)
            except HTTPException:
                raise HTTPException(status_code=502, detail=f'ModelScope 未返回 task_id：{raw}')
        deadline = time.monotonic() + AI_REQUEST_TIMEOUT
        last_payload = raw
        while time.monotonic() < deadline:
            await asyncio.sleep(IMAGE_POLL_INTERVAL)
            result = await client.get(f'{api_root}/tasks/{task_id}', headers={**headers, 'X-ModelScope-Task-Type': 'image_generation'})
            result.raise_for_status()
            data = result.json()
            last_payload = data
            status = str(data.get('task_status') or '').upper()
            if status == 'SUCCEED':
                images = data.get('output_images') or []
                if not images:
                    raise HTTPException(status_code=502, detail=f'ModelScope 成功但没有返回图片：{data}')
                return ({'type': 'url', 'value': images[0]}, data)
            if status in {'FAILED', 'FAIL', 'ERROR', 'CANCELED', 'CANCELLED', 'TIMEOUT', 'REVOKED'}:
                detail = data.get('error_info') or data.get('message') or data.get('detail') or str(data)
                raise HTTPException(status_code=502, detail=f'ModelScope 任务失败：{detail}')
        raise HTTPException(status_code=504, detail=f'ModelScope 生图任务超时：{last_payload}')

async def wait_for_runninghub_image_task(client, provider, task_id):
    query_url = runninghub_openapi_url(provider, 'query')
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        response = await client.post(query_url, headers=_svc.runninghub_api_headers(provider), json={'taskId': task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw)
        if status in {'success', 'succeeded', 'completed', 'complete', 'finished', 'finish', 'done', '3'}:
            return raw
        if status in {'failed', 'fail', 'error', 'canceled', 'cancelled', '4'}:
            raise HTTPException(status_code=502, detail=f'RunningHub 任务失败：{raw}')
        try:
            return {'data': {'results': [runninghub_extract_image(raw)]}}
        except HTTPException:
            pass
    raise HTTPException(status_code=504, detail=f'RunningHub 生图任务超时：{last_payload}')

async def generate_runninghub_entry_image(prompt, size, model, reference_images, provider, entry, image_parameters=None):
    """运行 RunningHub 在线 AI 应用并返回首张图片结果。"""
    entry_id = entry['id']
    fields = rh_sort_fields([f for f in entry.get('fields') or [] if isinstance(f, dict) and f.get('enabled') is True])
    idx_map = rh_field_indexes(fields)
    use_wallet = False
    timeout = httpx.Timeout(connect=20.0, read=1800.0, write=240.0, pool=20.0)
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    aspect = str(explicit.get('aspect_ratio') or '') or runninghub_aspect_from_size(size, '')
    resolution = str(explicit.get('resolution') or '') or runninghub_resolution_from_size(size, '')
    parsed_width, parsed_height = parse_size_pair(size)
    width = explicit.get('width') if explicit.get('width') is not None else parsed_width
    height = explicit.get('height') if explicit.get('height') is not None else parsed_height

    def requested_size_field_value(field):
        names = {str(field.get('fieldName') or '').strip().lower(), str(field.get('fieldKey') or '').strip().lower(), str(field.get('label') or '').strip().lower()}
        if aspect and names & {'aspectratio', 'aspect_ratio', 'ratio'}:
            return runninghub_schema_value(field, aspect)
        if resolution and 'resolution' in names:
            return runninghub_schema_value(field, resolution)
        if width and 'width' in names:
            return width
        if height and 'height' in names:
            return height
        return None
    async with httpx.AsyncClient(timeout=timeout) as client:
        uploaded = []
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            ref_url = ref.get('url') if isinstance(ref, dict) else ref
            if not ref_url:
                continue
            file_name = await _svc._main('runninghub_upload_local_to_filename')(client, provider, ref_url, use_wallet)
            if file_name:
                uploaded.append(file_name)
        node_info_list = []
        prompt_text = str(prompt or '').strip()
        for field in fields:
            node_id = str(field.get('nodeId') or '').strip()
            field_name = str(field.get('fieldName') or '').strip()
            if not node_id or not field_name:
                continue
            kind_f = rh_field_kind(field)
            if kind_f in ('image', 'video', 'audio'):
                if kind_f != 'image':
                    continue
                index = idx_map.get((node_id, field_name), 0)
                value = uploaded[index] if index < len(uploaded) else ''
                if not value:
                    if field.get('required') is True:
                        value = rh_default_value(field)
                        if not value:
                            continue
                    else:
                        continue
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': value})
            elif rh_field_role(field) == 'prompt':
                value = prompt_text or rh_default_value(field)
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': value})
            elif kind_f == 'number' and field.get('random_enabled') is True:
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': rh_random_field_value(field)})
            else:
                value = requested_size_field_value(field)
                if value is None:
                    value = rh_default_value(field)
                node_info_list.append({'nodeId': node_id, 'fieldName': field_name, 'fieldValue': value})
        api_key = _svc.runninghub_api_key(provider, use_wallet=use_wallet)
        submit_url = runninghub_endpoint_url(provider, '/task/openapi/ai-app/run')
        body = {'apiKey': api_key, 'webappId': entry_id, 'nodeInfoList': node_info_list}
        response = await client.post(submit_url, headers=_svc.runninghub_app_headers(True, use_wallet), json=body)
        raw = response.json()
        if not (isinstance(raw, dict) and raw.get('code') in (0, '0')):
            raise HTTPException(status_code=502, detail=(raw.get('msg') if isinstance(raw, dict) else '') or f'RunningHub 提交失败：{raw}')
        task_id = raw.get('data', {}).get('taskId') if isinstance(raw.get('data'), dict) else ''
        if not task_id:
            raise HTTPException(status_code=502, detail=f'RunningHub 未返回 taskId：{raw}')
        query_url = runninghub_endpoint_url(provider, '/task/openapi/outputs')
        deadline = time.monotonic() + 1800
        last_payload = None
        while time.monotonic() < deadline:
            await asyncio.sleep(2.5)
            query_response = await client.post(query_url, headers=_svc.runninghub_app_headers(True), json={'apiKey': api_key, 'taskId': task_id})
            query_raw = query_response.json()
            last_payload = query_raw
            code = query_raw.get('code') if isinstance(query_raw, dict) else None
            if code in (0, '0'):
                outputs = runninghub_extract_outputs(query_raw.get('data'))
                for remote in outputs:
                    if str(remote or '').startswith(('http://', 'https://', '/output/', '/assets/')):
                        return ({'type': 'url', 'value': str(remote)}, query_raw)
                raise HTTPException(status_code=502, detail=f'RunningHub 任务无图片输出：{query_raw}')
            if code in (805, '805'):
                raise HTTPException(status_code=502, detail=f'RunningHub 任务失败：{runninghub_fail_reason(query_raw) or query_raw}')
        raise HTTPException(status_code=504, detail=f'RunningHub 任务超时：{last_payload}')

async def generate_runninghub_provider_image(prompt, size, model, reference_images=None, provider=None, image_parameters=None):
    entry = runninghub_entry_config_from_model(provider, model)
    if entry:
        return await generate_runninghub_entry_image(prompt, size, model, reference_images, provider, entry, image_parameters)
    model_def = await _svc._main('runninghub_model_definition')(provider, model)
    endpoint = runninghub_task_endpoint(provider, model_def.get('endpoint') or model)
    params = model_def.get('params') if isinstance(model_def.get('params'), list) else []
    explicit = image_parameters if isinstance(image_parameters, dict) else {}
    aspect = str(explicit.get('aspect_ratio') or '') or runninghub_aspect_from_size(size, '1:1')
    resolution = str(explicit.get('resolution') or '') or runninghub_resolution_from_size(size, '2k')
    body = {'prompt': prompt}
    if runninghub_schema_field(params, 'aspectRatio'):
        field = runninghub_schema_field(params, 'aspectRatio')
        body['aspectRatio'] = runninghub_schema_value(field, aspect)
    elif runninghub_schema_field(params, 'ratio'):
        field = runninghub_schema_field(params, 'ratio')
        body['ratio'] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, 'resolution'):
        field = runninghub_schema_field(params, 'resolution')
        body['resolution'] = runninghub_schema_value(field, resolution)
    parsed_width, parsed_height = parse_size_pair(size)
    width = explicit.get('width') if explicit.get('width') is not None else parsed_width
    height = explicit.get('height') if explicit.get('height') is not None else parsed_height
    if width and height:
        if runninghub_schema_field(params, 'width'):
            body['width'] = width
        if runninghub_schema_field(params, 'height'):
            body['height'] = height
    quality_field = runninghub_schema_field(params, 'quality')
    if quality_field:
        body['quality'] = runninghub_schema_value(quality_field, 'medium')
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=180.0, pool=20.0)) as client:
        image_urls = []
        for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
            url = await _svc.runninghub_upload_reference(client, provider, ref)
            if url:
                image_urls.append(url)
        if image_urls:
            image_field = runninghub_schema_field(params, 'imageUrls', 'imageUrl', 'images', 'image')
            key = str((image_field or {}).get('fieldKey') or 'imageUrls')
            if key.endswith('s') or (image_field or {}).get('multipleInputs') is True:
                body[key] = image_urls
            else:
                body[key] = image_urls[0]
        runninghub_apply_schema_defaults(body, params)
        response = await client.post(endpoint, headers=_svc.runninghub_json_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        try:
            return (runninghub_extract_image(raw), raw)
        except HTTPException:
            task_id = runninghub_extract_task_id(raw)
            if not task_id:
                raise HTTPException(status_code=502, detail=f'RunningHub 未返回 taskId 或图片结果：{raw}')
        result = await wait_for_runninghub_image_task(client, provider, task_id)
        return (runninghub_extract_image(result), result)

async def wait_for_runninghub_openapi_task(client, provider, task_id, output_kind=''):
    query_url = runninghub_openapi_url(provider, 'query')
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        response = await client.post(query_url, headers=_svc.runninghub_json_headers(provider), json={'taskId': task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw).upper()
        if status in {'SUCCESS', 'SUCCEEDED', 'COMPLETED', 'COMPLETE', 'FINISHED', 'DONE', '3'}:
            return raw
        if status in {'FAILED', 'FAIL', 'ERROR', 'CANCEL', 'CANCELED', 'CANCELLED', '4'}:
            raise HTTPException(status_code=502, detail=f'RunningHub 任务失败：{runninghub_fail_reason(raw) or raw}')
        if output_kind == 'video' and video_output_urls(raw):
            return raw
    raise HTTPException(status_code=504, detail=f'RunningHub 任务超时：{last_payload or task_id}')

async def generate_runninghub_video(payload, provider):
    model_def = await _svc._main('runninghub_model_definition')(provider, payload.model)
    endpoint = runninghub_task_endpoint(provider, model_def.get('endpoint') or payload.model)
    image_to_video = runninghub_is_image_to_video(payload.model) or runninghub_is_image_to_video(model_def.get('endpoint')) or runninghub_is_image_to_video(endpoint)
    params = model_def.get('params') if isinstance(model_def.get('params'), list) else []
    incompatible = runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video)
    if incompatible:
        raise HTTPException(status_code=400, detail={'code': 'unsupported_video_adapter_parameters', 'message': f"{provider.get('name') or provider.get('id') or 'RunningHub'} 不支持本次请求的全部参数", 'incompatible_parameters': incompatible})
    body = {'prompt': str(payload.prompt or '')}
    aspect = str(payload.aspect_ratio or '16:9').strip() or '16:9'
    if runninghub_schema_field(params, 'aspectRatio'):
        field = runninghub_schema_field(params, 'aspectRatio')
        body['aspectRatio'] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, 'ratio'):
        field = runninghub_schema_field(params, 'ratio')
        body['ratio'] = runninghub_schema_value(field, aspect)
    if runninghub_schema_field(params, 'size'):
        field = runninghub_schema_field(params, 'size')
        body['size'] = runninghub_schema_value(field, runninghub_size_for_aspect(aspect))
    if runninghub_schema_field(params, 'duration'):
        field = runninghub_schema_field(params, 'duration')
        body['duration'] = runninghub_schema_value(field, str(max(1, min(60, int(payload.duration or 5)))))
    if runninghub_schema_field(params, 'resolution'):
        field = runninghub_schema_field(params, 'resolution')
        body['resolution'] = runninghub_schema_value(field, str(payload.resolution or '720p').lower())
    if runninghub_schema_field(params, 'generateAudio'):
        body['generateAudio'] = bool(payload.generate_audio)
    if runninghub_schema_field(params, 'watermark'):
        body['watermark'] = bool(payload.watermark)
    async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
        image_refs = []
        for ref in (payload.images or [])[:10]:
            ref_url = getattr(ref, 'url', '') or ''
            if ref_url:
                up = await _svc.runninghub_upload_reference(client, provider, {'url': ref_url})
                if up:
                    image_refs.append({'url': up, 'role': str(getattr(ref, 'role', '') or '').strip().lower()})
        image_urls = [item['url'] for item in image_refs if item.get('url')]
        if image_urls:
            first_url = next((item['url'] for item in image_refs if item.get('role') in {'first_frame', 'first'}), image_urls[0])
            last_url = next((item['url'] for item in image_refs if item.get('role') in {'last_frame', 'last'}), image_urls[1] if len(image_urls) > 1 else '')
            first_field = runninghub_schema_field(params, 'firstFrameUrl', 'first_frame_url', 'firstFrameImage', 'first_frame_image')
            last_field = runninghub_schema_field(params, 'lastFrameUrl', 'last_frame_url', 'lastFrameImage', 'last_frame_image')
            if first_field and first_url:
                body[str(first_field.get('fieldKey'))] = first_url
            if last_field and last_url:
                body[str(last_field.get('fieldKey'))] = last_url
            image_field = runninghub_schema_field(params, 'imageUrls', 'image_urls', 'imageUrl', 'image_url', 'referenceImages', 'referenceImageUrls')
            key = str((image_field or {}).get('fieldKey') or '')
            if key and key in body:
                pass
            elif key and (key.endswith('s') or (image_field or {}).get('multipleInputs') is True):
                body[key] = image_urls
            elif key:
                body[key] = image_urls[0]
            elif image_to_video and 'firstFrameUrl' not in body:
                body['firstFrameUrl'] = first_url
        first_required = runninghub_schema_field(params, 'firstFrameUrl', 'first_frame_url', 'firstFrameImage', 'first_frame_image')
        if runninghub_schema_field_required(first_required) and (not body.get(str(first_required.get('fieldKey') or ''))):
            raise HTTPException(status_code=400, detail='当前 RunningHub 模型是图生视频，需要连接一张首帧图片后再生成。')
        if image_to_video and (not body.get('firstFrameUrl')) and (not image_urls):
            raise HTTPException(status_code=400, detail='当前 RunningHub 模型是图生视频，需要连接一张首帧图片后再生成。')
        runninghub_apply_schema_defaults(body, params)
        response = await client.post(endpoint, headers=_svc.runninghub_json_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        task_id = runninghub_extract_task_id(raw)
        if not task_id:
            fail_reason = runninghub_fail_reason(raw)
            if fail_reason:
                raise HTTPException(status_code=502, detail=f'RunningHub 视频接口错误：{fail_reason}')
        result = raw
        if task_id and (not video_output_urls(raw)):
            result = await wait_for_runninghub_openapi_task(client, provider, task_id, 'video')
        urls = video_output_urls(result)
        if not urls:
            outputs = runninghub_extract_outputs(result)
            urls = [url for url in outputs if str(url).startswith(('http://', 'https://', '/output/', '/assets/'))]
        if not urls:
            raise HTTPException(status_code=502, detail=f'RunningHub 视频生成成功但没有返回视频：{result}')
        local_urls = [await _svc._main('save_remote_video_to_output')(url, prefix='rh_video_') for url in urls]
        return {'videos': local_urls, 'task_id': task_id, 'raw': result}

def project_video_all_references(project_id: str, payload: ProjectVideoGenerationRequest) -> List[Dict[str, Any]]:
    return [*_svc._main('project_video_references')(project_id, payload.image_reference_asset_ids, 'image'), *_svc._main('project_video_references')(project_id, payload.video_reference_asset_ids, 'video'), *_svc._main('project_video_references')(project_id, payload.audio_reference_asset_ids, 'audio')]

async def generate_project_video(request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Adapt project-owned references to the existing online video adapters."""
    legacy_payload = project_canvas_video_payload(request_snapshot, route.provider_id, route.model, references)
    requested_count = max(1, int(request_snapshot.count or 1))
    candidates = []
    raw_results = []
    for _ in range(requested_count):
        result = await _svc._main('execute_canvas_video')(legacy_payload, provider) if provider is not None else await _svc._main('canvas_video')(legacy_payload)
        videos = [str(value or '').strip() for value in result.get('videos') or [] if str(value or '').strip()]
        if not videos:
            raise ProjectStoreError('在线模型没有返回视频')
        candidates.extend(({'type': 'url', 'value': value, 'mime_type': content_type_for_path(value)} for value in videos))
        if isinstance(result, dict):
            raw_results.append(result.get('raw') if isinstance(result.get('raw'), dict) else {})
    raw = raw_results[0] if requested_count == 1 else {'results': raw_results}
    return (candidates, raw)

async def execute_project_video_generation(project_id: str, request_snapshot: VideoGenerationRequest, route, references: List[Dict[str, Any]], provider: Optional[Mapping[str, Any]]=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run one adapter call inside a task-owned project-media capability scope."""
    clean_project_id = str(project_id or '').strip()
    if not clean_project_id:
        raise ProjectStoreError('在线视频任务缺少项目标识')
    scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(clean_project_id)
    try:
        for item in references:
            adapter_url = item.get('adapter_url') or ''
            require_project_media_adapter_scope(adapter_url)
            _svc._main('project_media_adapter_path')(adapter_url, item.get('kind') or '')
        if provider is None:
            return await _svc._main('generate_project_video')(request_snapshot, route, references)
        return await _svc._main('generate_project_video')(request_snapshot, route, references, provider=provider)
    finally:
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)

async def preflight_project_video_providers(request: VideoGenerationRequest, references: List[Dict[str, Any]], providers: List[Dict[str, Any]], *, provider_id: str='', model: str='') -> List[Dict[str, Any]]:
    """Resolve candidate execution blockers before routing can register a task."""
    snapshots = copy.deepcopy(list(providers or []))
    pinned_provider = str(provider_id or '').strip().lower()
    pinned_model = str(model or '').strip()
    for provider_index, provider in enumerate(snapshots):
        current_provider_id = str(provider.get('id') or '').strip().lower()
        if not provider.get('enabled', True) or (pinned_provider and current_provider_id != pinned_provider):
            continue
        remote_schema_failed = False
        for current_model in provider.get('video_models') or []:
            current_model = str(current_model or '').strip()
            if not current_model or (pinned_model and current_model != pinned_model):
                continue
            payload = project_canvas_video_payload(request, current_provider_id, current_model, references)
            execution_incompatible = _svc._main('project_video_candidate_execution_incompatible_parameters')(provider, current_model, references)
            project_video_mark_candidate_incompatible(provider, current_model, execution_incompatible)
            if _svc._main('video_adapter_incompatible_parameters')(payload, provider) or execution_incompatible:
                continue
            if not is_runninghub_provider(provider):
                continue
            configured = runninghub_configured_video_model_definition(provider, current_model)
            if configured is not None:
                definition = configured
            elif remote_schema_failed:
                project_video_mark_candidate_incompatible(provider, current_model, ['adapter_schema'])
                continue
            else:
                try:
                    definition = await _svc._main('runninghub_model_definition')(provider, current_model)
                except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    remote_schema_failed = True
                    project_video_mark_candidate_incompatible(provider, current_model, ['adapter_schema'])
                    continue
            provider.setdefault('_runninghub_video_model_definitions', {})[current_model] = copy.deepcopy(definition)
        provider.pop('_runninghub_model_registry', None)
        if not pinned_provider:
            try:
                _svc._main('route_video_generation')(request, snapshots[:provider_index + 1], model=pinned_model, adapter_incompatible_parameters=functools.partial(project_video_adapter_incompatible_parameters, references=references))
            except ModelRoutingError:
                pass
            else:
                return snapshots
    return snapshots

def project_video_adapter_incompatible_parameters(request: VideoGenerationRequest, provider: Mapping[str, Any], model: str, *, references: List[Dict[str, Any]]) -> List[str]:
    """Evaluate the real adapter and resolved project references before registration."""
    payload = project_canvas_video_payload(request, str(provider.get('id') or ''), model, references)
    incompatible = _svc._main('video_adapter_incompatible_parameters')(payload, provider)
    incompatible.extend(project_video_candidate_preflight_incompatible_parameters(provider, model))
    if incompatible or not is_runninghub_provider(provider):
        return list(dict.fromkeys(incompatible))
    model_definition = runninghub_configured_video_model_definition(provider, model) or {}
    endpoint = str(model_definition.get('endpoint') or model)
    image_to_video = runninghub_is_image_to_video(model) or runninghub_is_image_to_video(endpoint)
    params = model_definition.get('params') if isinstance(model_definition.get('params'), list) else []
    incompatible.extend(runninghub_video_incompatible_parameters(payload, params, image_to_video=image_to_video))
    return list(dict.fromkeys(incompatible))

async def generate_codex_provider_image_via_gpt_image_2_skill(prompt, size, model, ref_paths=None):
    exe = _svc._main('gpt_image_2_skill_executable')()
    if not exe:
        return None
    ref_paths = [str(path) for path in ref_paths or [] if path and os.path.isfile(str(path))]
    auth_file = _svc.gpt_image_2_skill_auth_file()
    auth_data = gpt_image_2_skill_auth_json(auth_file)
    provider_args, tool_provider = _svc.gpt_image_2_skill_provider_args(auth_file)
    attempts = [(provider_args, tool_provider)]
    fallback_api_key = _svc.gpt_image_2_skill_api_key(auth_data)
    if tool_provider == 'codex' and fallback_api_key:
        attempts.append((['--provider', 'openai', '--api-key', fallback_api_key], 'openai'))
    last_message = ''
    for attempt_index, (attempt_provider_args, attempt_provider) in enumerate(attempts):
        out_path = os.path.join(_svc._main('OUTPUT_OUTPUT_DIR'), f'gpt_image_2_{uuid.uuid4().hex}.png')
        mode = 'edit' if ref_paths else 'generate'
        args = [exe, '--json']
        args.extend(attempt_provider_args)
        args.extend(['images', mode, '--prompt', gpt_image_2_skill_prompt_arg(prompt, size, attempt_provider), '--out', out_path, '--model', gpt_image_2_skill_model_arg(model, attempt_provider), '--format', 'png', '--size', gpt_image_2_skill_size_arg(size, model, prompt, attempt_provider), '--quality', 'high'])
        for path in ref_paths:
            args.extend(['--ref-image', path])
        if ref_paths and attempt_provider == 'openai':
            args.extend(['--input-fidelity', 'high'])
        try:
            proc = await asyncio.create_subprocess_exec(*args, cwd=BASE_DIR, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=codex_timeout())
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise HTTPException(status_code=504, detail='GPT Image 2 Skill 执行超时。可设置 CODEX_CLI_TIMEOUT 增大等待时间。') from exc
        except FileNotFoundError:
            return None
        out_text, err_text = codex_decode_output(stdout, stderr)
        if proc.returncode != 0:
            message = gpt_image_2_skill_failure_message(out_text, err_text, proc.returncode)
            last_message = f'{attempt_provider}: {message}'
            auth_failed = bool(re.search('\\b401\\b|unauthori[sz]ed|access[_ -]?token|api[_ -]?key', message, re.I))
            if attempt_provider == 'codex' and attempt_index + 1 < len(attempts) and auth_failed:
                continue
            if auth_failed:
                return None
            raise HTTPException(status_code=502, detail=f'GPT Image 2 Skill 调用失败：{last_message[:1200]}')
        parsed, reported_paths = parse_gpt_image_2_skill_output(out_text, err_text)
        candidate_paths = []
        if os.path.isfile(out_path):
            candidate_paths.append(out_path)
        candidate_paths.extend([path for path in reported_paths if path and os.path.isfile(path)])
        urls = []
        for path in candidate_paths:
            processed_path = codex_postprocess_image_to_requested_size(path, size, attempt_provider)
            url = _svc.codex_output_url_from_path(processed_path or path)
            if url:
                urls.append(url)
        if not urls:
            status_text = (out_text or err_text or '')[:1200]
            raise HTTPException(status_code=502, detail=f'GPT Image 2 Skill 已返回，但没有在输出目录发现图片：{status_text}')
        return ({'type': 'url', 'value': urls[0]}, {'images': urls, 'text': out_text, 'provider': 'codex', 'tool': 'gpt-image-2-skill', 'tool_provider': attempt_provider, 'raw': parsed or {'stdout': out_text, 'stderr': err_text}})
    raise HTTPException(status_code=502, detail=f'GPT Image 2 Skill 调用失败：{last_message[:1200]}')

async def generate_codex_provider_image(prompt, size, model, reference_images=None, provider=None):
    ref_paths, temp_paths = await _svc.codex_reference_paths(reference_images)
    try:
        skill_result = await generate_codex_provider_image_via_gpt_image_2_skill(prompt, size, model, ref_paths)
        if skill_result:
            return skill_result
        raise HTTPException(status_code=400, detail='未找到 GPT Image 2 helper，OpenAI CLI 生图已禁用 $imagegen 回退。请先安装 gpt-image-2-skill 后再生成图片。')
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

async def generate_gemini_cli_provider_image(prompt, size, model, reference_images=None, provider=None):
    ref_paths, temp_paths = await _svc.gemini_cli_reference_paths(reference_images)
    since = time.time()
    try:
        ref_text = ''
        if ref_paths:
            ref_text = '\n参考图片本地路径：\n' + '\n'.join(ref_paths)
        size_context = f"{model or ''} {prompt or ''}"
        image_prompt = f"你正在为萤火画布生成图片。\n任务：{prompt}\n\n{gemini_cli_image_size_instruction(size, size_context)}\n{ref_text}\n\n如果当前 Antigravity CLI/模型支持图片生成或图片编辑，请把最终图片保存到这个本地目录：{_svc._main('OUTPUT_OUTPUT_DIR')}\n文件格式优先 png 或 jpg。只输出最终文件路径和一句简短说明；不要修改项目代码，不要创建额外文档。\n如果你无法真正创建图片文件，请在 60 秒内直接回复“无法生成图片文件”，不要只写计划，也不要持续尝试。"
        raw = await _svc.run_gemini_cli(image_prompt, model=model or GEMINI_CLI_DEFAULT_IMAGE_MODELS[0], timeout=gemini_cli_image_timeout() if is_antigravity_cli(_svc._main('gemini_cli_executable')()) else gemini_cli_timeout(), allow_tools=True)
        files = _svc.codex_output_image_files(since)
        urls = []
        for path in files:
            processed_path = codex_postprocess_image_to_requested_size(path, size, 'gemini-cli')
            url = _svc.codex_output_url_from_path(processed_path or path)
            if url and url not in urls:
                urls.append(url)
        if not urls:
            text = f"{raw.get('text') or raw.get('_stdout') or ''}\n{raw.get('_stderr') or ''}"
            pattern = '([A-Za-z]:\\\\[^\\r\\n\\"\'<>]+\\.(?:png|jpe?g|webp|gif)|/[^\\r\\n\\"\'<>]+\\.(?:png|jpe?g|webp|gif))'
            for match in re.findall(pattern, text, flags=re.I):
                match_path = match.strip()
                processed_path = codex_postprocess_image_to_requested_size(match_path, size, 'gemini-cli')
                url = _svc.codex_output_url_from_path(processed_path or match_path)
                if url and url not in urls:
                    urls.append(url)
        if not urls:
            status_text = (raw.get('text') or raw.get('_stdout') or raw.get('_stderr') or '')[:1200]
            raise HTTPException(status_code=502, detail=f'{_svc.gemini_cli_display_name()} 已返回，但没有在输出目录发现图片：{status_text}')
        return ({'type': 'url', 'value': urls[0]}, {'images': urls, 'text': raw.get('text'), 'provider': 'gemini-cli', 'raw': raw.get('raw')})
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except Exception:
                pass

def image_task_url_for_provider(provider, task_id):
    base_url = (provider.get('base_url') if provider else _svc._main('AI_BASE_URL')).rstrip('/')
    if normalize_image_request_mode((provider or {}).get('image_request_mode')) == 'openai-video-proxy':
        return f'{base_url}/videos/{task_id}' if base_url.endswith('/v1') else f'{base_url}/v1/videos/{task_id}'
    if is_apimart_provider(provider):
        return f'{base_url}/tasks/{task_id}' if base_url.endswith('/v1') else f'{base_url}/v1/tasks/{task_id}'
    return f'{base_url}/images/tasks/{task_id}' if base_url.endswith('/v1') else f'{base_url}/v1/images/tasks/{task_id}'

async def fetch_image_task_payload(client, task_id, provider=None):
    task_url = image_task_url_for_provider(provider, task_id)
    response = await httpx_request_with_transient_retries(client, 'GET', task_url, attempts=3, headers=_svc._main('api_headers')(provider=provider))
    if response.status_code == 404:
        # #235：上游 404 → 结构化 not-found 载荷（status=NOT_FOUND），不再裸抛 httpx.HTTPStatusError；
        # 由状态机（wait_for_image_task / query_upstream_image_task）按未知态终态化。
        return {'status': 'NOT_FOUND', 'task_id': task_id, 'message': f'生图任务不存在（task_id={task_id}）'}
    response.raise_for_status()
    return response.json()

async def upload_image_for_apimart(client, provider, ref_url: str) -> str:
    """把本地图片转成上游可接受的输入。
    按 APIMart 文档上传到 /v1/uploads/images，拿到可用于生成接口的 http/https URL。
    绝不把 /output/* 或 /assets/* 这类本地路径直接传给上游。
    返回上游可用 URL；返回值以 "ERR:" 开头表示具体失败原因（供前端展示）。"""
    ref_url = str(ref_url or '').strip()
    if not ref_url:
        return 'ERR:空地址'
    if ref_url.startswith('http://') or ref_url.startswith('https://') or ref_url.startswith('asset://'):
        return ref_url
    base_url = _svc.video_api_root(provider)
    upload_url = f'{base_url}/v1/uploads/images'
    if ref_url.startswith('data:'):
        try:
            if ';base64,' not in ref_url:
                return 'ERR:不支持的 data URL（缺少 base64 段）'
            header, encoded = ref_url.split(';base64,', 1)
            mime = header.split(':', 1)[1].split(';', 1)[0] if ':' in header else 'image/png'
            raw = base64.b64decode(encoded)
            filename, content, ct = apimart_upload_payload_from_bytes(raw, mime, name_hint='canvas_image')
            resp = await apimart_upload_post(client, upload_url, _svc._main('api_headers')(json_body=False, provider=provider), (filename, content, ct), timeout=60)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                logger.error(f'APIMart 上传 data URL 返回中未找到可用 asset/url: {str(rj)[:300]}')
                return 'ERR:APIMart 上传响应未包含可用 URL'
            logger.error(f'APIMart 上传 data URL 失败 ({resp.status_code}): {resp.text[:300]}')
            return f'ERR:APIMart 上传失败({resp.status_code})'
        except ValueError as e:
            return f'ERR:{e}'
        except Exception as e:
            logger.error(f'APIMart 上传 data URL 异常: {e}')
            return f'ERR:上传异常 {e}'
    path = _svc.local_media_adapter_path(ref_url, 'image')
    if path:
        try:
            filename, content, ct = apimart_upload_file_payload(path)
            resp = await apimart_upload_post(client, upload_url, _svc._main('api_headers')(json_body=False, provider=provider), (filename, content, ct), timeout=60)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                logger.error(f'APIMart 文件上传返回中未找到可用 asset/url: {str(rj)[:300]}')
                return 'ERR:APIMart 上传响应未包含可用 URL'
            logger.error(f'APIMart 文件上传失败 ({resp.status_code}): {resp.text[:300]}')
            return f'ERR:APIMart 上传失败({resp.status_code})'
        except ValueError as e:
            return f'ERR:{e}'
        except Exception as e:
            logger.error(f'APIMart 文件上传异常: {e}')
            return f'ERR:上传异常 {e}'
    return 'ERR:不支持的图片来源（仅支持 http/https/asset/data 或应用本地媒体）'

async def upload_video_for_apimart(client, provider, ref_url: str) -> str:
    """尽力把本地参考视频转换为 APIMart 可接受的 http/https 或 asset:// URL。
    文档只公开了图片上传；如果视频上传端点不可用，会回退到 PUBLIC_BASE_URL 方案。"""
    ref_url = str(ref_url or '').strip()
    if not ref_url:
        return 'ERR:空地址'
    if valid_apimart_video_image_input(ref_url):
        return ref_url
    public_url = _svc.local_asset_public_url(ref_url)
    if public_url:
        return public_url
    path = _svc.local_media_adapter_path(ref_url, 'video')
    if not path:
        return f'ERR:{_svc.apimart_video_reference_error(ref_url)}'
    if not ref_url.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://') and str(os.getenv('APIMART_TRY_VIDEO_UPLOAD') or '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return f'ERR:{_svc.apimart_video_reference_error(ref_url)}'
    base_url = _svc.video_api_root(provider)
    filename, content, content_type = apimart_upload_raw_file_payload(path)
    upload_paths = ('/v1/uploads/videos', '/v1/uploads/files', '/v1/uploads/images')
    last_error = ''
    for upload_path in upload_paths:
        upload_url = f'{base_url}{upload_path}'
        try:
            files = {'file': (filename, content, content_type)}
            resp = await client.post(upload_url, headers=_svc._main('api_headers')(json_body=False, provider=provider), files=files, timeout=180)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                last_error = '上传响应未包含可用 URL'
                logger.error(f'APIMart 视频上传返回中未找到可用 asset/url ({upload_path}): {str(rj)[:300]}')
                continue
            last_error = f'{upload_path} 返回 {resp.status_code}: {resp.text[:200]}'
            logger.error(f'APIMart 视频上传失败 {last_error}')
        except Exception as e:
            last_error = f'{upload_path} 异常：{e}'
            logger.error(f'APIMart 视频上传异常: {last_error}')
    return f'ERR:APIMart 未提供可用的视频文件上传入口（{last_error}）。请配置 PUBLIC_BASE_URL，或使用公网 http/https / asset:// 视频地址。'

async def upload_audio_for_apimart(client, provider, ref_url: str) -> str:
    """把本地参考音频转换为 APIMart 可接受的 http/https 或 asset:// URL。
    优先用公网地址（PUBLIC_BASE_URL），否则尝试上传到 APIMart 文件端点。
    返回值以 "ERR:" 开头表示失败原因。"""
    ref_url = str(ref_url or '').strip()
    if not ref_url:
        return 'ERR:空地址'
    if valid_apimart_video_image_input(ref_url):
        return ref_url
    public_url = _svc.local_asset_public_url(ref_url)
    if public_url:
        return public_url
    base_url = _svc.video_api_root(provider)
    upload_paths = ('/v1/uploads/audios', '/v1/uploads/files', '/v1/uploads/images')
    last_error = ''
    if ref_url.startswith('data:'):
        if ';base64,' not in ref_url:
            return 'ERR:不支持的 data URL（缺少 base64 段）'
        header, encoded = ref_url.split(';base64,', 1)
        mime = header.split(':', 1)[1].split(';', 1)[0] if ':' in header else 'audio/mpeg'
        try:
            raw = base64.b64decode(encoded)
        except Exception as exc:
            return f'ERR:音频 data URL 解码失败：{exc}'
        ext = mimetypes.guess_extension(mime) or '.mp3'
        filename, content, content_type = (f'canvas_audio{ext}', raw, mime or 'audio/mpeg')
    else:
        path = _svc.local_media_adapter_path(ref_url, 'audio')
        if not path:
            return f'ERR:{_svc.apimart_video_reference_error(ref_url)}'
        filename, content, content_type = apimart_upload_raw_file_payload(path)
    for upload_path in upload_paths:
        upload_url = f'{base_url}{upload_path}'
        try:
            files = {'file': (filename, content, content_type)}
            resp = await client.post(upload_url, headers=_svc._main('api_headers')(json_body=False, provider=provider), files=files, timeout=180)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                last_error = '上传响应未包含可用 URL'
                continue
            last_error = f'{upload_path} 返回 {resp.status_code}: {resp.text[:200]}'
        except Exception as exc:
            last_error = f'{upload_path} 异常：{exc}'
    return f'ERR:APIMart 未提供可用的音频文件上传入口（{last_error}）。请配置 PUBLIC_BASE_URL，或使用公网 http/https / asset:// 音频地址。'

async def submit_apimart_avatar_asset(provider, public_url: str, name: str, kind: str, project_name: str='default', group_name: str='') -> str:
    """把一个公网可访问的素材提交到 APIMart private-avatar 审核，立即返回任务 ID（不阻塞轮询）。"""
    base_url = _svc.video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    register_url = f'{base_url}/v1/seedance2/private-avatar'
    body = {'project_name': str(project_name or 'default').strip() or 'default', 'asset_type': apimart_avatar_asset_type(kind), 'group': {'name': (group_name or name or '数字人素材')[:60]}, 'assets': [{'url': public_url, 'name': (name or 'asset')[:60]}]}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(register_url, headers=_svc._main('api_headers')(provider=provider), json=body, timeout=120)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f'APIMart 数字人注册失败（{resp.status_code}）：{resp.text[:300]}')
        data = resp.json()
        task = data.get('data') if isinstance(data.get('data'), dict) else data
        task_id = str(task.get('id') or task.get('task_id') or '').strip()
        if not task_id:
            raise HTTPException(status_code=502, detail=f'APIMart 数字人注册返回中未找到任务 ID：{str(data)[:300]}')
        return task_id

async def check_apimart_avatar_task(provider, task_id: str) -> Dict[str, Any]:
    """查询一次 APIMart 审核任务。返回 {status: Active/Processing/Failed, asset_uri, detail}。"""
    base_url = _svc.video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    task_url = f'{base_url}/v1/tasks/{task_id}'
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(task_url, headers=_svc._main('api_headers')(provider=provider), timeout=60)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f'查询审核状态失败（{resp.status_code}）：{resp.text[:200]}')
        payload = resp.json()
    node = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    status = str(node.get('status') or '').strip().lower()
    if status in AVATAR_TASK_DONE_STATUSES:
        asset_uri = extract_apimart_avatar_asset_uri(payload)
        if not asset_uri:
            return {'status': 'Failed', 'asset_uri': '', 'detail': '审核完成，但未返回可用的 asset:// 地址（可能部分素材被拒）。'}
        return {'status': 'Active', 'asset_uri': asset_uri, 'detail': ''}
    if status in AVATAR_TASK_FAIL_STATUSES:
        return {'status': 'Failed', 'asset_uri': '', 'detail': f'审核未通过（{status}）。'}
    return {'status': 'Processing', 'asset_uri': '', 'detail': '审核中'}

async def save_ai_image_to_output(image_data, prefix='online_', category='output'):
    filename = f'{prefix}{uuid.uuid4().hex[:10]}.png'
    path = _svc.output_path_for(filename, category)
    if image_data['type'] == 'b64':
        mime_type = str(image_data.get('mime_type') or '').lower()
        if 'jpeg' in mime_type or 'jpg' in mime_type:
            filename = filename[:-4] + '.jpg'
            path = _svc.output_path_for(filename, category)
        elif 'webp' in mime_type:
            filename = filename[:-4] + '.webp'
            path = _svc.output_path_for(filename, category)
        with open(path, 'wb') as f:
            f.write(base64.b64decode(image_data['value']))
        return _svc.output_url_for(filename, category)
    value = image_data['value']
    if value.startswith('/output/') or value.startswith('/assets/'):
        return value
    value = rewrite_runninghub_file_url(value)
    try:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(value)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '')
            if 'jpeg' in content_type or 'jpg' in content_type:
                filename = filename[:-4] + '.jpg'
                path = _svc.output_path_for(filename, category)
            elif 'webp' in content_type:
                filename = filename[:-4] + '.webp'
                path = _svc.output_path_for(filename, category)
            with open(path, 'wb') as f:
                f.write(response.content)
            return _svc.output_url_for(filename, category)
    except Exception as e:
        logger.warning(f'保存上游图片失败: {e}; url={value}')
        return value

async def save_remote_video_to_output(url, prefix='video_', category='output'):
    if not url:
        return ''
    if url.startswith('/output/') or url.startswith('/assets/'):
        return url
    video_exts = {'.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv', '.flv'}
    parsed = urllib.parse.urlparse(str(url or '').strip())
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return url
    clean_ext = os.path.splitext(parsed.path)[1].lower()
    stem = f'{prefix}{uuid.uuid4().hex[:10]}'
    filename = f"{stem}{(clean_ext if clean_ext in video_exts else '.mp4')}"
    path = _svc.output_path_for(filename, category)
    try:
        timeout = httpx.Timeout(connect=20.0, read=VIDEO_POLL_TIMEOUT, write=60.0, pool=20.0)
        headers = {'User-Agent': 'Firefly-Canvas/1.0', 'Accept': 'video/*,application/octet-stream,*/*;q=0.8'}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = (response.headers.get('Content-Type') or '').lower()
            if 'text/html' in content_type or 'application/json' in content_type:
                raise RuntimeError(f'unexpected video content type: {content_type}')
            ext = clean_ext
            if ext in video_exts:
                filename = f'{stem}{ext}'
                path = _svc.output_path_for(filename, category)
            elif 'webm' in content_type:
                filename = f'{stem}.webm'
                path = _svc.output_path_for(filename, category)
            elif 'quicktime' in content_type or 'mov' in content_type:
                filename = f'{stem}.mov'
                path = _svc.output_path_for(filename, category)
            elif 'x-matroska' in content_type or 'mkv' in content_type:
                filename = f'{stem}.mkv'
                path = _svc.output_path_for(filename, category)
            elif 'x-flv' in content_type or 'flv' in content_type:
                filename = f'{stem}.flv'
                path = _svc.output_path_for(filename, category)
            with open(path, 'wb') as f:
                f.write(response.content)
            if os.path.getsize(path) <= 0:
                raise RuntimeError('empty video response')
            return _svc.output_url_for(filename, category)
    except Exception as e:
        logger.warning(f'保存上游视频失败: {e}')
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return url

async def generate_gemini_provider_image(prompt, size, model, reference_images=None, provider=None, image_parameters=None):
    model_name = gemini_model_name(model)
    endpoint = _svc.gemini_endpoint_url(provider, model_name)
    parts = [{'text': prompt.strip()}]
    for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]:
        part = _svc.gemini_reference_part(ref)
        if part:
            parts.append(part)
    body = {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': {'responseModalities': ['TEXT', 'IMAGE'], 'imageConfig': gemini_image_config(size, image_parameters)}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=_svc._main('api_headers')(provider=provider), json=body)
        response.raise_for_status()
        raw = response.json()
        return (extract_image(raw), raw)

async def generate_volcengine_provider_image(prompt, size, model, reference_images=None, provider=None, preserve_size=False):
    endpoint = _svc.volcengine_endpoint_url(provider)
    if not preserve_size:
        size = normalize_volcengine_size(size, model)
    body = {'model': model, 'prompt': prompt, 'size': size, 'response_format': 'url'}
    images = [_svc.volcengine_image_payload(ref) for ref in (reference_images or [])[:ONLINE_IMAGE_REFERENCE_MAX]]
    images = [value for value in images if value]
    if images:
        body['image'] = images
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=_svc._main('api_headers')(provider=provider), json=body)
        response.raise_for_status()
        raw = response.json()
        return (extract_image(raw), raw)

@functools.lru_cache(maxsize=8)
def _lossless_webp_reference_upload(path, mtime_ns, source_size):
    source_path = Path(path)
    original = source_path.read_bytes()
    fallback = (source_path.name, original, content_type_for_path(path))
    if source_path.suffix.lower() != '.png' or len(original) != source_size:
        return fallback
    try:
        with Image.open(BytesIO(original)) as image:
            image.load()
            if image.mode not in {'RGB', 'RGBA'} or getattr(image, 'n_frames', 1) != 1:
                return fallback
            if image.getexif().get(274, 1) != 1:
                return fallback
            source = image.copy()
            save_options = {'lossless': True, 'method': 4, 'exact': True}
            if image.info.get('icc_profile'):
                save_options['icc_profile'] = image.info['icc_profile']
            if image.info.get('exif'):
                save_options['exif'] = image.info['exif']
        buffer = BytesIO()
        source.save(buffer, 'WEBP', **save_options)
        encoded = buffer.getvalue()
        if len(encoded) >= len(original):
            return fallback
        with Image.open(BytesIO(encoded)) as decoded:
            decoded.load()
            if decoded.size != source.size or decoded.mode != source.mode or decoded.tobytes() != source.tobytes():
                return fallback
        return (f'{source_path.stem}.webp', encoded, 'image/webp')
    except Exception:
        return fallback


def _openai_edit_reference_upload(path, optimize_lossless=False):
    stat = os.stat(path)
    if optimize_lossless:
        return _lossless_webp_reference_upload(os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    return (os.path.basename(path), Path(path).read_bytes(), content_type_for_path(path))


class _ProgressUpload:
    def __init__(self, stream, size, on_progress):
        self._stream = stream
        self._size = max(0, int(size or 0))
        self._on_progress = on_progress
        self._maximum_position = 0

    def read(self, size=-1):
        chunk = self._stream.read(size)
        try:
            position = int(self._stream.tell())
        except Exception:
            position = self._maximum_position + len(chunk or b'')
        self._maximum_position = max(self._maximum_position, min(self._size, position))
        self._on_progress(self._maximum_position)
        return chunk

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _provider_diagnostic_sensitive_values(prompt, provider_id, preserve_model=False):
    values = [str(prompt or '')]
    provider_label = str(provider_id or 'comfly')
    try:
        provider_key = _svc._main('provider_env_key_value')(provider_label)
    except Exception:
        provider_key = ''
    if provider_key:
        values.append(str(provider_key))
    try:
        provider_getter = 'get_api_provider_exact' if preserve_model else 'get_api_provider'
        provider = _svc._main(provider_getter)(provider_label)
    except Exception:
        provider = None
    if isinstance(provider, dict):
        for key in ('api_key', 'wallet_api_key', 'volcengine_secret_access_key'):
            if provider.get(key):
                values.append(str(provider[key]))
        for use_wallet in (False, True):
            try:
                runninghub_key = _svc.runninghub_api_key(provider, use_wallet=use_wallet)
            except Exception:
                runninghub_key = ''
            if runninghub_key:
                values.append(str(runninghub_key))
    for loader_name in ('volcengine_access_key_value', 'volcengine_secret_key_value'):
        try:
            loaded = _svc._main(loader_name)()
        except Exception:
            loaded = ''
        if loaded:
            values.append(str(loaded))
    return values


async def generate_ai_image(prompt, size, quality, model, reference_images=None, provider_id='comfly', *, preserve_model=False, image_parameters=None, progress_callback=None):
    """Provider-boundary diagnostics span around the image generation pipeline.

    Emits one provider.request event and exactly one terminal event
    (provider.response or provider.failed) per call; payloads never enter the
    log — only correlation fields and a redacted exception chain.
    """
    started = time.monotonic()
    provider_label = str(provider_id or 'comfly')
    model_label = str(model or '')
    _diag_emit('provider.request', provider_id=provider_label, model=model_label, state='requested')
    try:
        result = await _generate_ai_image_impl(
            prompt, size, quality, model, reference_images, provider_id=provider_id,
            preserve_model=preserve_model, image_parameters=image_parameters,
            progress_callback=progress_callback,
        )
    except Exception:
        _diag_emit(
            'provider.failed',
            provider_id=provider_label,
            model=model_label,
            state='failed',
            duration_ms=int((time.monotonic() - started) * 1000),
            exception=sys.exc_info()[1],
            sensitive_values=_provider_diagnostic_sensitive_values(
                prompt,
                provider_label,
                preserve_model=preserve_model,
            ),
        )
        raise
    _diag_emit(
        'provider.response',
        provider_id=provider_label,
        model=model_label,
        state='succeeded',
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


# #41: provider 出站的受控并发 + 429 退避重试（v1 与 legacy 两条生图路径共用）。
# 并发敏感 provider 在多个请求同时在飞时会拒绝其中大部分（例如 8 连发只接受 1 个），
# 因此既要「连发」又不能无界并发：每 provider 一个信号量限制同时在飞请求数，并对 429
# 做有限退避重试。信号量按 event loop 区分存储：同 loop 复用，异 loop 重建。
_PROVIDER_GENERATION_SEMAPHORES = {}
_PROVIDER_GENERATION_SEMAPHORES_LOCK = threading.Lock()


def _provider_generation_semaphore(provider_id: str) -> 'asyncio.Semaphore':
    loop = asyncio.get_running_loop()
    with _PROVIDER_GENERATION_SEMAPHORES_LOCK:
        entry = _PROVIDER_GENERATION_SEMAPHORES.get(provider_id)
        if entry is None or entry[0] is not loop:
            try:
                max_concurrency = max(1, int(os.environ.get('FIREFLY_PROVIDER_GENERATION_MAX_CONCURRENCY', '8') or '8'))
            except (TypeError, ValueError):
                max_concurrency = 8
            entry = (loop, asyncio.Semaphore(max_concurrency))
            _PROVIDER_GENERATION_SEMAPHORES[provider_id] = entry
        return entry[1]


def _retry_after_seconds(response) -> 'Optional[float]':
    """从 429 响应解析 Retry-After（秒数或 HTTP 日期），无则返回 None。"""
    value = str((response.headers.get('retry-after') if hasattr(response, 'headers') else '') or '').strip()
    if not value:
        return None
    try:
        seconds = float(value)
        if seconds >= 0:
            return seconds
    except (TypeError, ValueError):
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed is not None:
            return max(0.0, parsed.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        pass
    return None


async def run_provider_generation_with_backoff(provider_id: str, generate_callable, *args, **kwargs):
    """受控并发 + 429 有限退避地执行一次 provider 生图调用。

    count=8 类连发任务的所有 provider 出站都应经由本 helper（而不是裸调
    generate_ai_image）：
    - 每 provider 一个信号量限制同时在飞请求数（默认 3，env
      FIREFLY_PROVIDER_GENERATION_MAX_CONCURRENCY 可调）；
    - 429 在退避后有限重试（默认 5 次，env FIREFLY_PROVIDER_429_RETRY_ATTEMPTS 可调；
      provider 携带 Retry-After 时优先按其等待，否则线性递增 ×±20% 抖动避免锁步互撞，
      env FIREFLY_PROVIDER_429_BACKOFF_SECONDS 可调基础退避）。
    其余异常原样上抛。
    """
    semaphore = _provider_generation_semaphore(str(provider_id or 'default'))
    try:
        attempts = max(1, int(os.environ.get('FIREFLY_PROVIDER_429_RETRY_ATTEMPTS', '5') or '5'))
    except (TypeError, ValueError):
        attempts = 5
    try:
        backoff_seconds = float(os.environ.get('FIREFLY_PROVIDER_429_BACKOFF_SECONDS', '0.5') or '0.5')
    except (TypeError, ValueError):
        backoff_seconds = 0.5
    for attempt in range(attempts):
        try:
            async with semaphore:
                return await generate_callable(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt + 1 >= attempts:
                raise
            retry_after = _retry_after_seconds(exc.response)
            if retry_after is not None:
                sleep_seconds = max(0.05, retry_after)
            else:
                sleep_seconds = max(0.01, backoff_seconds * (attempt + 1) * random.uniform(0.8, 1.2))
            log_net_error(f"生图 429 限流，退避重试 attempt={attempt + 1} provider={provider_id}", exc)
            await asyncio.sleep(sleep_seconds)


def fal_queue_paths(base_url, endpoint, request_id='', prefix='fal'):
    """fal 队列三个端点的 URL 拼接（中转商将 queue.fal.run 反代到自己的 /fal 前缀）。

    base_url 不带 /v1：comfly 的 fal 兼容层挂在站点根，{BASE}/fal/{model}。
    """
    root = str(base_url or '').strip().rstrip('/')
    if root.lower().endswith('/v1'):
        root = root[:-3].rstrip('/')
    prefix = str(prefix or 'fal').strip().strip('/')
    model_path = str(endpoint or '').strip().strip('/')
    base = f'{root}/{prefix}/{model_path}'
    if not request_id:
        return base
    rid = str(request_id).strip().strip('/')
    return {'request': base, 'status': f'{base}/requests/{rid}/status', 'result': f'{base}/requests/{rid}'}


def fal_resolution_upscale_factor(value):
    """画布分辨率档位 → fal 高清修复的 upscale_factor（1k=1×/2k=2×/4k=4×）。

    只在服务商未显式配定 upscale_factor 时作为默认，不覆盖 fal_request.params。
    """
    tier = str(value or '').strip().lower()
    return {'1k': 1, '1080p': 1, '720p': 1, '2k': 2, '4k': 4}.get(tier)


def fal_submit_body(model, image_url, params, resolution=None, model_by_resolution=None):
    """组装 fal 队列提交体：model 只出现在 URL，参数来自服务商配置（fal_request.params）。

    fal 的 schema 是严格的：多余的 size/aspect_ratio/quality 会被 422 拒，
    所以这里只发 image_url + 配置的参数，最多从画布档位补 upscale_factor / model。
    """
    body = {str(key): value for key, value in (params or {}).items() if str(key).strip()}
    if image_url:
        body['image_url'] = image_url
    tier = str(resolution or '').strip().lower()
    if 'upscale_factor' not in body:
        factor = fal_resolution_upscale_factor(tier)
        if factor:
            body['upscale_factor'] = factor
    if 'model' not in body and model_by_resolution:
        engine = model_by_resolution.get(tier)
        if engine:
            body['model'] = engine
    return body


def fal_url_host(value):
    return str(value or '').split('://')[-1].split('/', 1)[0].strip().lower()


def fal_request_urls(base_url, model, request_id, submitted, prefix='fal'):
    """轮询/取结果的地址：只相信同源，否则用本地前缀重建。

    中转商把 fal 官方的 status_url/response_url（https://queue.fal.run/…）原样回填，
    直连会绕过中转鉴权（fal 官方用 `Authorization: key`，不是 Bearer）→ 必须换回自己域名。
    """
    own = fal_queue_paths(base_url, model, request_id, prefix)
    base_host = fal_url_host(base_url)
    urls = {}
    for name, fallback in (('status', own['status']), ('result', own['result'])):
        raw = str((submitted or {}).get(f'{name}_url') or '').strip()
        parsed_host = fal_url_host(raw)
        urls[name] = raw if raw.startswith('http') and base_host and parsed_host == base_host else fallback
    return urls


FAL_ERROR_HINTS = {
    'no_upstream_key': '这是渠道侧问题：服务商的 fal 上游密钥池里没有可用 key（未接入或额度耗尽），画布无法绕过。请在服务商后台为 fal 渠道补密钥/额度，或换一个已开通 fal 的渠道。',
    'invalid_api_key': '服务商拒绝了本次鉴权（token 无效或已过期），请在 API 平台管理里更新该服务商的 Key。',
    'model_not_found': '服务商没有这个 fal 端点：请核对 image_model_capabilities[model].fal_request.endpoint 与服务商实际模型名。',
    'insufficient_quota': '服务商余额/额度不足，高清修复无法提交。',
}


def fal_error_detail(provider_name, stage, response):
    """把中转/fal 众层嵌套的错误体拆成人能读的一句，并补上可行动的下一步。

    中转常见形态：{code:'fail_submit_task', message:'{"error":{"message":...,"code":"no_upstream_key"}}'}
    —— 外层 code 无用，真正的错误码被包在 message 的字符串 JSON 里。
    """
    raw = str(response.text or '').strip()
    code = ''
    message = ''
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    if isinstance(data, dict):
        code = str(data.get('code') or '').strip().lower()
        message = str(data.get('message') or '').strip()
        inner = None
        try:
            inner = json.loads(message)
        except Exception:
            inner = None
        if isinstance(inner, dict):
            error = inner.get('error') if isinstance(inner.get('error'), dict) else inner
            code = str(error.get('code') or code).strip().lower()
            message = str(error.get('message') or message).strip()
        elif isinstance(data.get('error'), dict):
            error = data['error']
            code = str(error.get('code') or code).strip().lower()
            message = str(error.get('message') or message).strip()
    hint = FAL_ERROR_HINTS.get(code, '')
    status = int(getattr(response, 'status_code', 0) or 0)
    if not hint and status == 422:
        hint = 'fal 的 schema 很严：提交体里多出 upscale 模型不吃的字段会被直接拒。请检查 image_model_capabilities[model].fal_request.params。'
    if not hint and status == 429:
        hint = 'fal 队列已满或被限流，稍后重试；也可降低档位避开高峰。'
    if not hint and status in {502, 503, 504}:
        hint = '上游服务不可用，稍后重试；若持续如此，请在服务商后台查该渠道状态。'
    detail = f'{provider_name} fal {stage}失败（HTTP {status}）'
    if code:
        detail += f'，上游错误码 {code}'
    body_text = message or raw[:200]
    if body_text:
        detail += f'：{body_text[:240]}'
    if hint:
        detail += f'。{hint}'
    return detail


async def generate_fal_queue_image(client, base_url, provider, model, image_refs, params, *, prefix='fal', progress_callback=None, resolution=None, model_by_resolution=None):
    """fal.ai 队列协议：提交 → 轮询 status → 拉取结果（Topaz 高清修复等 image-to-image 模型）。

    必须提供参考图：fal 高清修复类模型没有「文生图」语义，提前提而不是让上游 422。
    """
    paths = fal_queue_paths(base_url, model, prefix=prefix)
    image_url = ''
    relay_error = ''
    for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]:
        # fal 不会拉本机路径：站内参考图必须先公网化（图床 → PUBLIC_MEDIA_BASE_URL 公网基址）。
        try:
            image_url = await _svc.fal_reference_public_url(ref)
        except HTTPException as exc:
            relay_error = str(exc.detail)
            continue
        if image_url:
            break
    if not image_url:
        if relay_error:
            raise HTTPException(status_code=400, detail=relay_error)
        raise HTTPException(status_code=400, detail='高清修复模型需要一张参考图（选一张画布上的图作为输入）')
    body = fal_submit_body(model, image_url, params, resolution, model_by_resolution)
    headers = _svc._main('api_headers')(provider=provider, model=model)
    response = await client.post(paths, headers=headers, json=body)
    if response.status_code >= 400:
        provider_name = provider.get('name') or provider['id']
        raise HTTPException(status_code=response.status_code, detail=fal_error_detail(provider_name, '任务提交', response))
    try:
        submitted = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fal 任务提交返回非 JSON：{(response.text or '')[:300]}") from exc
    request_id = str(submitted.get('request_id') or submitted.get('requestId') or submitted.get('id') or '').strip()
    if not request_id:
        # 部分中转源站同步回结果：直接当完成体处理，不再轮询。
        if extract_image_flexible(submitted):
            return _FalQueueResponse(submitted)
        raise HTTPException(status_code=502, detail=f"fal 任务提交未返回 request_id：{json.dumps(submitted, ensure_ascii=False)[:400]}")
    request_urls = fal_request_urls(base_url, model, request_id, submitted, prefix)
    status_url = request_urls['status']
    deadline = time.monotonic() + RESPONSES_POLL_MAX_SECONDS
    last_status = ''
    unknown_streak = 0
    while time.monotonic() < deadline:
        status_response = await client.get(status_url, headers=headers)
        if status_response.status_code >= 400:
            provider_name = provider.get('name') or provider['id']
            raise HTTPException(status_code=status_response.status_code, detail=fal_error_detail(provider_name, '任务状态查询', status_response))
        try:
            status_payload = status_response.json()
        except Exception:
            status_payload = {}
        status = str(status_payload.get('status') or '').strip().upper()
        last_status = status or last_status
        if status in {'COMPLETED', 'SUCCEEDED', 'SUCCESS'}:
            break
        if status in {'ERRORED', 'ERROR', 'FAILED', 'CANCELLED'}:
            logs = ' | '.join(str(item)[:200] for item in (status_payload.get('logs') or [])[:4])
            raise HTTPException(status_code=502, detail=f"fal 任务失败（{status}）：{logs or '上游未给出日志'}")
        if status in IMAGE_TASK_UNKNOWN_STATUSES:
            # #235 终态语义复用（不许另发明）：连续 N 次 provider 明确返回未知/不存在 → 终态 fail
            # （文案显式带 request_id），不再空转到 25 分钟超时；单次抖动容忍——出现可识别状态即重置计数。
            unknown_streak += 1
            if unknown_streak >= IMAGE_TASK_UNKNOWN_TERMINAL_AFTER:
                raise HTTPException(status_code=502, detail=f"fal 任务不存在或状态未知（request_id={request_id}，连续 {unknown_streak} 次状态查询返回 {status}），请确认请求 ID 后显式重试")
        else:
            unknown_streak = 0
        if callable(progress_callback):
            try:
                progress_callback('provider_generating', queue_position=status_payload.get('queue_position'), fal_status=status)
            except Exception:
                pass
        await asyncio.sleep(RESPONSES_POLL_INTERVAL)
    else:
        raise HTTPException(status_code=502, detail=f"fal 任务未在 {RESPONSES_POLL_MAX_SECONDS / 60:g} 分钟内完成（最后状态：{last_status or '未知'}），可在服务商后台用 request_id {request_id} 查询")
    result_url = request_urls['result']
    result_response = await client.get(result_url, headers=headers)
    if result_response.status_code >= 400:
        provider_name = provider.get('name') or provider['id']
        raise HTTPException(status_code=502, detail=fal_error_detail(provider_name, '结果拉取', result_response))
    try:
        payload = result_response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fal 结果返回非 JSON：{(result_response.text or '')[:300]}") from exc
    payload.setdefault('request_id', request_id)
    return _FalQueueResponse(payload)


class _FalQueueResponse:
    """把 fal 队列结果体适配成与 HTTP 响应同形的对象，复用调用点的统一尾段。"""

    status_code = 200
    text = ''

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


async def _generate_ai_image_impl(prompt, size, quality, model, reference_images=None, provider_id='comfly', *, preserve_model=False, image_parameters=None, progress_callback=None):
    def report_progress(phase, **details):
        if not callable(progress_callback):
            return
        try:
            progress_callback(phase, **details)
        except Exception:
            pass

    require_project_media_values_scope([str((ref.get('url') if isinstance(ref, dict) else getattr(ref, 'url', '')) or '').strip() for ref in reference_images or [] if str((ref.get('url') if isinstance(ref, dict) else getattr(ref, 'url', '')) or '').strip()])
    provider = _svc._main('get_api_provider_exact')(provider_id) if preserve_model else _svc._main('get_api_provider')(provider_id)
    if provider['id'] == 'modelscope':
        return await generate_modelscope_provider_image(prompt, size, model, reference_images, provider)
    if is_codex_provider(provider):
        return await generate_codex_provider_image(prompt, size, model, reference_images, provider)
    if is_gemini_cli_provider(provider):
        return await generate_gemini_cli_provider_image(prompt, size, model, reference_images, provider)
    if is_runninghub_provider(provider):
        return await generate_runninghub_provider_image(prompt, size, model, reference_images, provider, image_parameters)
    if effective_protocol(provider, model) == 'gemini':
        return await generate_gemini_provider_image(prompt, size, model, reference_images, provider, image_parameters)
    if is_volcengine_provider(provider):
        return await generate_volcengine_provider_image(prompt, size, model, reference_images, provider, preserve_model)
    is_comfly_provider = 'comfly' in str((provider.get('base_url') or _svc._main('AI_BASE_URL') or '')).strip().lower() or 'comfly' in str(provider.get('id') or '').strip().lower()
    inline_base64 = is_comfly_provider
    if preserve_model:
        route_params = {key: value for key, value in (image_parameters or {}).items() if key in {'aspect_ratio', 'resolution'} and value not in (None, '')}
        image_route = route_openai_image_request(provider, selected_model(model, IMAGE_MODEL), size)
        image_route['params'].update(route_params)
    else:
        image_route = route_openai_image_request(provider, model, size)
    if preserve_model and image_route['params'].get('resolution') and (banana_model_family(image_route['model']) or looks_like_gemini_image_model(image_route['model'])):
        image_route['params']['image_size'] = str(image_route['params']['resolution']).upper()
    model = image_route['model']
    routed_image_params = image_route['params']
    is_gpt2 = is_gpt_image_2_model(model)
    if is_gpt2:
        if routed_image_params.get('aspect_ratio') or routed_image_params.get('resolution'):
            # gpt-image 家族（含 gpt-image-2.5-flare/-sunburst）只接受 size，
            # 必须把画布的 比例+档位 换算成合规像素，不能透传 aspect_ratio/resolution。
            size = gpt_image_2_request_size(size, routed_image_params.get('aspect_ratio'), routed_image_params.get('resolution'))
            routed_image_params.pop('aspect_ratio', None)
            routed_image_params.pop('resolution', None)
        else:
            # 没有比例/档位参数时，也不能把超出服务商硬约束的像素直接发出去（如 4K 正方形 4096）。
            size = gpt_image_2_request_size(size)
    if is_comfly_provider:
        image_route['params'].pop('resolution', None)
    is_apimart = is_apimart_provider(provider)
    quality = str(quality or '').strip().lower()
    if quality not in {'low', 'medium', 'high'}:
        quality = ''
    base_url = (provider.get('base_url') or _svc._main('AI_BASE_URL')).rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    gen_url = _svc.provider_endpoint_url(provider, 'image_generation_endpoint', '/v1/images/generations')
    edit_url = _svc.provider_endpoint_url(provider, 'image_edit_endpoint', '/v1/images/edits')
    refs = [ref for ref in reference_images or [] if ref.get('url')]
    mask_refs = [ref for ref in refs if str(ref.get('role') or '').strip().lower() == 'mask' or str(ref.get('name') or '').lower().endswith('_mask.png')]
    image_refs = [ref for ref in refs if ref not in mask_refs]
    image_request_mode = effective_image_request_mode(provider, model)
    # 长耗时模式（gpt-image-2 / APIMART / openai-json 系）：read 超时收敛到 AI_REQUEST_TIMEOUT
    # （#249 审计：此处原硬编码 1800.0，不跟随 REQUEST_TIMEOUT env，现统一单一事实来源，默认值不变）；
    # connect/write/pool 为固定工程值（非 provider 约束）。
    request_timeout = httpx.Timeout(connect=20.0, read=AI_REQUEST_TIMEOUT, write=120.0, pool=20.0) if is_gpt2 or is_apimart or image_request_mode in {'openai-json', 'openai-video-proxy', 'openai-responses', 'fal'} else AI_REQUEST_TIMEOUT
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        response = None

        async def post_openai_edits(edit_files=None):
            data = {'model': model, 'prompt': prompt}
            if is_comfly_provider:
                data['response_format'] = 'url'
            if not routed_image_params.get('aspect_ratio'):
                data['size'] = size
            if routed_image_params:
                data.update(routed_image_params)
            if quality and not is_comfly_provider:
                data['quality'] = quality
            if not callable(progress_callback):
                return await client.post(
                    edit_url,
                    headers=_svc._main('api_headers')(json_body=False, provider=provider, model=model),
                    data=data,
                    files=edit_files if edit_files is not None else {},
                )
            request = client.build_request('POST', edit_url, headers=_svc._main('api_headers')(json_body=False, provider=provider, model=model), data=data, files=edit_files if edit_files is not None else {})
            response = await client.send(request, stream=True)
            total_header = str(response.headers.get('content-length') or '').strip()
            response_total = int(total_header) if total_header.isdigit() else 0
            report_progress('receiving', bytes_done=0, bytes_total=response_total)
            content = await response.aread()
            report_progress('receiving', bytes_done=len(content), bytes_total=response_total or len(content))
            return response
        if image_request_mode == 'fal':
            # fal.ai 平台兼容（comfly /fal/ 前缀）：Topaz 高清修复等队列模型。
            # 参数不写死 —— fal_request.params 来自服务商配置；body 里没有 model/size/quality。
            fal_spec = fal_request_spec(provider, model)
            response = await generate_fal_queue_image(
                client, base_url, provider, fal_spec['endpoint'] or model, image_refs, fal_spec['params'],
                prefix=fal_spec['path_prefix'], progress_callback=progress_callback,
                resolution=(image_parameters or {}).get('resolution'), model_by_resolution=fal_spec['model_by_resolution'],
            )
        elif image_request_mode == 'openai-video-proxy':
            body = {'model': model, 'prompt': prompt, 'aspect_ratio': routed_image_params.get('aspect_ratio') or runninghub_aspect_from_size(size, '1:1')}
            if routed_image_params.get('resolution'):
                body['resolution'] = routed_image_params['resolution']
            video_url = f'{base_url}/videos' if base_url.endswith('/v1') else f'{base_url}/v1/videos'
            refs_for_proxy = image_refs[:6]
            local_image_paths = [_svc.openai_video_proxy_local_image_path(ref) for ref in refs_for_proxy]
            has_local_images = any(local_image_paths)
            if has_local_images:
                form_data = {key: value for key, value in body.items()}
                for ref, local_path in zip(refs_for_proxy, local_image_paths):
                    if local_path:
                        continue
                    url = await _svc.openai_video_proxy_public_reference_url(ref)
                    if url:
                        existing_images = form_data.get('images')
                        if isinstance(existing_images, list):
                            existing_images.append(url)
                        elif existing_images:
                            form_data['images'] = [existing_images, url]
                        else:
                            form_data['images'] = url
                files = []
                for local_path in local_image_paths:
                    if not local_path:
                        continue
                    with open(local_path, 'rb') as fh:
                        content = fh.read()
                    files.append(('images', (os.path.basename(local_path), content, content_type_for_path(local_path))))
                headers = _svc._main('api_headers')(json_body=False, provider=provider, model=model)

                def post_video_proxy_multipart():
                    with httpx.Client(timeout=request_timeout) as sync_client:
                        return sync_client.post(video_url, headers=headers, data=form_data, files=files)
                response = await asyncio.to_thread(post_video_proxy_multipart)
            else:
                if refs_for_proxy:
                    body['images'] = [await _svc.openai_video_proxy_public_reference_url(ref) for ref in refs_for_proxy]
                response = await httpx_request_with_transient_retries(client, 'POST', video_url, attempts=2, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
        elif image_request_mode == 'openai-responses':
            tool = {'type': 'image_generation'}
            tool['action'] = 'edit' if image_refs else 'generate'
            if size and str(size).strip().lower() != 'auto':
                tool['size'] = size if preserve_model else responses_proxy_tool_size(size)
            if quality:
                tool['quality'] = quality
            size_instruction = responses_image_size_instruction(size)
            input_text = f'{size_instruction}\n\n{prompt}' if size_instruction else prompt
            content = [{'type': 'input_text', 'text': input_text}]
            force_public_refs = bool(locked_recommended_provider_rule(provider.get('id'), provider.get('name'), base_url))
            for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]:
                image_url = await _svc.responses_input_image_url(ref, require_public_url=force_public_refs)
                if image_url:
                    content.append({'type': 'input_image', 'image_url': image_url})
            body = {'model': model, 'input': [{'role': 'user', 'content': content}], 'tools': [tool], 'tool_choice': {'type': 'image_generation'}}
            responses_url = _svc.provider_endpoint_url(provider, 'image_generation_endpoint', '/v1/responses')
            response = await post_openai_responses(client, responses_url, _svc._main('api_headers')(provider=provider, model=model), body)
        elif image_request_mode == 'openai-json':
            extra_body = {'response_format': 'url'}
            if image_refs:
                extra_body['image'] = [_svc._main('reference_to_data_url')(ref, max_size=1536) for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]]
            body = {'model': model, 'prompt': prompt}
            if not routed_image_params.get('aspect_ratio'):
                body['size'] = size
            body['extra_body'] = extra_body
            if routed_image_params:
                body.update(routed_image_params)
            response = await client.post(gen_url, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
        elif is_apimart:
            apimart_size, resolution = (size, str((image_parameters or {}).get('resolution') or '')) if preserve_model else apimart_size_resolution(size)
            body = {'model': model, 'prompt': prompt, 'n': 1, 'size': apimart_size, 'resolution': resolution, 'official_fallback': False}
            if image_refs:
                body['image_urls'] = [_svc._main('reference_to_data_url')(ref, max_size=1536) for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]]
            if preserve_model and routed_image_params.get('aspect_ratio'):
                body['aspect_ratio'] = routed_image_params['aspect_ratio']
            response = await client.post(gen_url, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
        elif is_gpt2 and (not image_refs) and (not mask_refs):
            body = {'model': model, 'prompt': prompt}
            if not routed_image_params.get('aspect_ratio'):
                body['size'] = size
            if routed_image_params:
                body.update(routed_image_params)
            if quality:
                body['quality'] = quality
            response = await client.post(gen_url, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
            if response.status_code >= 400 and images_api_unsupported(response):
                response = await post_openai_edits()
        elif image_refs:
            files = []
            opened = []
            edit_failed_status = None
            edit_failed_text = ''
            try:
                for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]:
                    path = _svc._main('output_file_from_url')(ref.get('url', ''))
                    if not path:
                        continue
                    if inline_base64:
                        filename, payload, mime_type = await asyncio.to_thread(_openai_edit_reference_upload, path, True)
                        files.append(('image', (filename, payload, mime_type)))
                        continue
                    fh = open(path, 'rb')
                    opened.append(fh)
                    files.append(('image', (os.path.basename(path), fh, content_type_for_path(path))))
                if mask_refs:
                    mask_path = _svc._main('output_file_from_url')(mask_refs[0].get('url', ''))
                    if mask_path:
                        fh = open(mask_path, 'rb')
                        opened.append(fh)
                        files.append(('mask', (os.path.basename(mask_path), fh, content_type_for_path(mask_path))))
                tracked_files = files
                if callable(progress_callback):
                    upload_sizes = []
                    for _field, (_filename, content, _mime_type) in files:
                        if isinstance(content, (bytes, bytearray)):
                            upload_sizes.append(len(content))
                        else:
                            try:
                                upload_sizes.append(os.fstat(content.fileno()).st_size)
                            except Exception:
                                upload_sizes.append(0)
                    upload_total = sum(upload_sizes)
                    upload_positions = [0] * len(files)
                    upload_last_reported = -1

                    def report_upload(index, position):
                        nonlocal upload_last_reported
                        upload_positions[index] = max(upload_positions[index], int(position or 0))
                        uploaded = min(upload_total, sum(upload_positions))
                        if uploaded == upload_total or uploaded - upload_last_reported >= 256 * 1024:
                            upload_last_reported = uploaded
                            report_progress('uploading', bytes_done=uploaded, bytes_total=upload_total)
                        if upload_total > 0 and uploaded >= upload_total:
                            report_progress('provider_generating')

                    tracked_files = []
                    if upload_total > 0:
                        report_progress('uploading', bytes_done=0, bytes_total=upload_total)
                    for index, (field, (filename, content, mime_type)) in enumerate(files):
                        stream = BytesIO(content) if isinstance(content, (bytes, bytearray)) else content
                        tracked_files.append((field, (filename, _ProgressUpload(stream, upload_sizes[index], lambda position, index=index: report_upload(index, position)), mime_type)))
                try:
                    response = await post_openai_edits(tracked_files)
                    if response.status_code >= 400:
                        edit_failed_status = response.status_code
                        edit_failed_text = response.text[:500]
                        response = None
                except httpx.HTTPError as e:
                    edit_failed_status = -1
                    edit_failed_text = str(e)
                    response = None
            finally:
                for fh in opened:
                    fh.close()
            if response is None:
                if is_gpt2:
                    raise HTTPException(status_code=502, detail=f'GPT-Image-2 编辑接口 /images/edits 调用失败：{edit_failed_text[:300] or edit_failed_status}。已停止自动重试，避免上游可能已扣费后再次请求。')
                logger.warning(f'/images/edits failed ({edit_failed_status}): {edit_failed_text[:200]} → 回退到 /images/generations + image:[] JSON')
                image_payload = [_svc._main('reference_to_data_url')(ref, max_size=1536) for ref in image_refs[:ONLINE_IMAGE_REFERENCE_MAX]]
                body = {'model': model, 'prompt': prompt, 'response_format': 'url', 'n': 1, 'image': image_payload}
                if not routed_image_params.get('aspect_ratio'):
                    body['size'] = size
                if routed_image_params:
                    body.update(routed_image_params)
                if quality and not is_comfly_provider:
                    body['quality'] = quality
                response = await client.post(gen_url, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
                if response.status_code >= 400 and images_api_unsupported(response):
                    raise HTTPException(status_code=502, detail=f'编辑接口 /images/edits 调用失败，且该平台不支持 /images/generations：{edit_failed_text[:300] or edit_failed_status}')
        else:
            body = {'model': model, 'prompt': prompt}
            if not routed_image_params.get('aspect_ratio'):
                body['size'] = size
            body['response_format'] = 'url'
            body['n'] = 1
            if routed_image_params:
                body.update(routed_image_params)
            if quality and not is_comfly_provider:
                body['quality'] = quality
            response = await client.post(gen_url, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
            if response.status_code >= 400 and images_api_unsupported(response):
                response = await post_openai_edits()
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            resp_text = (exc.response.text or '')[:800]
            provider_name = provider.get('name') or provider['id']
            raise HTTPException(status_code=exc.response.status_code, detail=f'{provider_name} 图片接口错误（HTTP {exc.response.status_code}）：{resp_text or exc.response.reason_phrase}') from exc
        try:
            raw = response.json()
        except Exception as exc:
            resp_text = (response.text or '')[:800]
            provider_name = provider.get('name') or provider['id']
            if is_fhl_provider(provider) and '_worker_keepalive' in resp_text:
                raise HTTPException(status_code=502, detail=f'{provider_name} 图片接口返回了未完成的 keepalive 响应，未返回图片数据。请重试或降低尺寸；响应片段：{resp_text[:200]}') from exc
            raise HTTPException(status_code=502, detail=f'{provider_name} 图片接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}') from exc
        try:
            return (extract_image(raw), raw)
        except HTTPException as exc:
            if image_request_mode == 'openai-responses':
                fallback_image = responses_output_text_image(raw)
                if fallback_image:
                    return (fallback_image, raw)
                try:
                    logger.warning(f'RS 响应中没有图片，原始返回（截断）：{json.dumps(raw, ensure_ascii=False)[:800]}')
                except Exception:
                    pass
                raise HTTPException(status_code=502, detail=responses_no_image_detail(raw) or exc.detail)
            task_id = extract_task_id(raw)
            if not task_id:
                raise
        try:
            task_result = await wait_for_image_task(client, task_id, provider)
            return (extract_image(task_result), task_result)
        except HTTPException as exc:
            setattr(exc, 'upstream_task_id', task_id)
            raise

_GENERATION_DOWNLOAD_MAX_BYTES = 1024 * 1024 * 1024
_GENERATION_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_GENERATION_DOWNLOAD_MIN_PARALLEL = 2 * 1024 * 1024
_GENERATION_DOWNLOAD_MAX_WORKERS = 6


async def _download_generation_asset(value: str, temporary_path: Path, timeout: httpx.Timeout) -> None:
    """Download an online generation asset with Range-based parallel chunking.

    CDNs commonly throttle a single connection (e.g. 172 KB/s observed on
    webstatic.aiproxy.vip); splitting the payload across parallel Range
    requests multiplies the effective throughput. Falls back to a plain
    sequential download when the server does not advertise Range support or
    the payload is small. Verifies that every chunk was fully received and
    that the final size matches the declared Content-Length.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        current_url = value
        for redirect_count in range(6):
            request_url, request_headers, request_extensions = await ensure_public_generation_target(current_url)
            async with client.stream('GET', request_url, headers=request_headers, extensions=request_extensions) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('location')
                    if not location or redirect_count >= 5:
                        raise ProjectStoreError('在线模型返回的图片地址重定向过多')
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_length = response.headers.get('content-length')
                total = int(content_length) if content_length and content_length.isdigit() else 0
                if total <= 0 or total < _GENERATION_DOWNLOAD_MIN_PARALLEL or total > _GENERATION_DOWNLOAD_MAX_BYTES:
                    with temporary_path.open('wb') as handle:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > _GENERATION_DOWNLOAD_MAX_BYTES:
                                raise ProjectStoreError('在线模型返回的图片超过 1GB 限制')
                            handle.write(chunk)
                    return
                break
        else:
            raise ProjectStoreError('在线模型返回的图片地址重定向过多')
        await _parallel_range_download(client, request_url, request_headers, request_extensions, temporary_path, total)


async def _parallel_range_download(client: httpx.AsyncClient, request_url: str, request_headers: Dict[str, str], request_extensions: Dict[str, Any], temporary_path: Path, total: int) -> None:
    """Download ``total`` bytes in parallel Range chunks into temporary_path."""
    if total > _GENERATION_DOWNLOAD_MAX_BYTES:
        raise ProjectStoreError('在线模型返回的图片超过 1GB 限制')
    chunk_size = _GENERATION_DOWNLOAD_CHUNK_BYTES
    ranges = [(start, min(start + chunk_size - 1, total - 1)) for start in range(0, total, chunk_size)]

    async def fetch_chunk(start: int, end: int) -> bytes:
        async with client.stream('GET', request_url, headers={**request_headers, 'Range': f'bytes={start}-{end}'}, extensions=request_extensions) as response:
            response.raise_for_status()
            if response.status_code == 200:
                raise ProjectStoreError('在线模型返回的图片不支持分段下载')
            parts = [part async for part in response.aiter_bytes(1024 * 1024)]
            received = b''.join(parts)
            if len(received) != (end - start + 1):
                raise ProjectStoreError('在线模型返回的图片分块下载不完整')
            return received

    semaphore = asyncio.Semaphore(_GENERATION_DOWNLOAD_MAX_WORKERS)

    async def bounded_fetch(start: int, end: int) -> Tuple[int, bytes]:
        async with semaphore:
            return start, await fetch_chunk(start, end)

    chunks = await asyncio.gather(*(bounded_fetch(start, end) for start, end in ranges))
    with temporary_path.open('wb') as handle:
        for start, content in chunks:
            handle.write(content)


async def archive_project_generated_image(project_id: str, image_data: Dict[str, Any], index: int) -> Dict[str, Any]:
    kind = str(image_data.get('type') or 'url')
    mime_type = str(image_data.get('mime_type') or 'image/png')
    filename = generated_image_filename(index, mime_type)
    source_info = {'type': 'online_generation', 'name': filename, 'lifecycle': 'transient'}
    width = height = 0
    if kind == 'b64':
        try:
            encoded = str(image_data.get('value') or '').strip()
            content = base64.b64decode(encoded + '=' * (-len(encoded) % 4), validate=True)
        except (ValueError, TypeError) as exc:
            raise ProjectStoreError('在线模型返回了无效的图片数据') from exc
        width, height, mime_type = validate_generated_image(BytesIO(content))
        filename = generated_image_filename(index, mime_type)
        record = await asyncio.to_thread(_svc._main('project_store_call'), 'import_media', project_id, content, filename, mime_type, source_info)
    else:
        value = str(image_data.get('value') or '').strip()
        local_path = _svc._main('output_file_from_url')(value)
        if local_path:
            width, height, mime_type = validate_generated_image(local_path)
            filename = generated_image_filename(index, mime_type)
            record = await asyncio.to_thread(_svc._main('project_store_call'), 'import_media_file', project_id, local_path, filename, mime_type, {**source_info, 'name': filename, 'locator': value})
        elif value.startswith(('http://', 'https://')):
            # 回传图通常几 MB，正常网络数秒内完成；read 超时收紧到 90s，避免慢 CDN
            # 时单张图挂住 5 分钟导致整批任务「写入画布」阶段十几分钟不推进。
            timeout = httpx.Timeout(connect=20.0, read=90.0, write=60.0, pool=20.0)
            descriptor, temporary_name = tempfile.mkstemp(prefix='firefly-generated-', suffix='.tmp')
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                download_attempts = max(1, int(os.environ.get('FIREFLY_GENERATION_DOWNLOAD_RETRY_ATTEMPTS', '3') or '3'))
                for attempt in range(download_attempts):
                    try:
                        await _download_generation_asset(value, temporary_path, timeout)
                        break
                    except ProjectStoreError:
                        if attempt + 1 >= download_attempts:
                            raise
                        log_net_error(f'在线生图结果下载失败，重试 attempt={attempt + 1}', sys.exc_info()[1])
                        await asyncio.sleep(min(8.0, 1.5 * (attempt + 1)))
                width, height, mime_type = validate_generated_image(temporary_path)
                filename = generated_image_filename(index, mime_type)
                record = await asyncio.to_thread(_svc._main('project_store_call'), 'import_media_file', project_id, temporary_path, filename, mime_type, {**source_info, 'name': filename, 'locator': value})
            finally:
                temporary_path.unlink(missing_ok=True)
        else:
            raise ProjectStoreError('在线模型没有返回可归档的图片')
    node_image = {'asset_id': record['asset_id'], 'kind': 'image', 'name': record.get('name') or filename, 'natural_w': width, 'natural_h': height}
    return node_image

async def archive_project_generated_video(project_id: str, video_data: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Verify and atomically archive one online-video result as project media."""
    kind = str(video_data.get('type') or 'url')
    declared_mime = str(video_data.get('mime_type') or 'video/mp4')
    filename = f'generated-{index + 1}.mp4'
    source_info = {'type': 'online_generation', 'name': filename, 'lifecycle': 'transient'}
    record = None
    verified_mime = declared_mime
    if kind == 'b64':
        try:
            content = base64.b64decode(str(video_data.get('value') or ''), validate=True)
        except (ValueError, TypeError) as exc:
            raise ProjectStoreError('在线模型返回了无效的视频数据') from exc
        if len(content) > _svc._main('PROJECT_GENERATED_VIDEO_MAX_BYTES'):
            raise ProjectStoreError('在线模型返回的视频超过 1GB 限制')
        _width, _height, verified_mime = validate_generated_video(BytesIO(content))
        suffix = mimetypes.guess_extension(verified_mime) or '.mp4'
        filename = f'generated-{index + 1}{suffix}'
        source_info['name'] = filename
        record = await asyncio.to_thread(_svc._main('project_store_call'), 'import_media', project_id, content, filename, verified_mime, source_info)
    else:
        value = str(video_data.get('value') or '').strip()
        local_path = _svc._main('output_file_from_url')(value)
        if local_path:
            _width, _height, verified_mime = validate_generated_video(local_path)
            suffix = mimetypes.guess_extension(verified_mime) or '.mp4'
            filename = f'generated-{index + 1}{suffix}'
            record = await asyncio.to_thread(_svc._main('project_store_call'), 'import_media_file', project_id, local_path, filename, verified_mime, {**source_info, 'name': filename, 'locator': value})
        elif value.startswith(('http://', 'https://')):
            descriptor, temporary_name = tempfile.mkstemp(prefix='firefly-generated-video-', suffix='.tmp')
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                total = 0
                current_url = value
                timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    for redirect_count in range(6):
                        request_url, request_headers, request_extensions = await ensure_public_generation_target(current_url, '视频')
                        async with client.stream('GET', request_url, headers=request_headers, extensions=request_extensions) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get('location')
                                if not location or redirect_count >= 5:
                                    raise ProjectStoreError('在线模型返回的视频地址重定向过多')
                                current_url = urllib.parse.urljoin(current_url, location)
                                continue
                            response.raise_for_status()
                            with temporary_path.open('wb') as handle:
                                async for chunk in response.aiter_bytes(1024 * 1024):
                                    total += len(chunk)
                                    if total > _svc._main('PROJECT_GENERATED_VIDEO_MAX_BYTES'):
                                        raise ProjectStoreError('在线模型返回的视频超过 1GB 限制')
                                    handle.write(chunk)
                            break
                    else:
                        raise ProjectStoreError('在线模型返回的视频地址重定向过多')
                _width, _height, verified_mime = validate_generated_video(temporary_path)
                suffix = mimetypes.guess_extension(verified_mime) or '.mp4'
                filename = f'generated-{index + 1}{suffix}'
                record = await asyncio.to_thread(_svc._main('project_store_call'), 'import_media_file', project_id, temporary_path, filename, verified_mime, {**source_info, 'name': filename, 'locator': value})
            finally:
                temporary_path.unlink(missing_ok=True)
        else:
            raise ProjectStoreError('在线模型没有返回可归档的视频')
    return {'asset_id': record['asset_id'], 'kind': 'video', 'name': record.get('name') or filename, 'mime_type': record.get('mime_type') or verified_mime}

async def run_project_image_task(task_id: str, project_id: str, payload: ProjectImageGenerationRequest, request_snapshot: ImageGenerationRequest, route, registered_revision: int):
    started_at = _svc._main('now_ms')()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]['status'] = 'running'
        CANVAS_TASKS[task_id]['updated_at'] = time.time()
    try:
        preexisting_asset_ids = {item.get('asset_id') for item in await asyncio.to_thread(_svc._main('project_store_call'), 'list_media', project_id) if item.get('asset_id')}
        references = await asyncio.to_thread(_svc.project_image_references, project_id, payload.reference_asset_ids)
        executed_prompt = apply_angle_preset(payload.prompt, payload.angle_preset)
        image_parameters = {'width': request_snapshot.width, 'height': request_snapshot.height, 'aspect_ratio': request_snapshot.aspect_ratio, 'resolution': request_snapshot.resolution, 'media_type': request_snapshot.media_type}
        scope_token = PROJECT_MEDIA_ADAPTER_PROJECT.set(project_id)
        progress_history_id = f'generation-{uuid.uuid4().hex}'
        try:
            for _ in range(payload.n):
                image_data, raw = await _svc._main('generate_ai_image')(executed_prompt, payload.size, payload.quality, route.model, references, route.provider_id, preserve_model=True, image_parameters=image_parameters)
                try:
                    candidates = extract_images(raw) if isinstance(raw, dict) else []
                except HTTPException:
                    candidates = []
                if not candidates:
                    candidates = [image_data]
                for candidate in candidates:
                    item = await archive_project_generated_image(project_id, candidate, len(generated_items))
                    generated_items.append(item)
                    # 逐张增量写回：生成一张就落到画布一张，避免全部生成完才一次性写入。
                    # 增量记录沿用同一 history id，最终 succeeded 记录会覆盖它。
                    progress_timestamp = _svc._main('now_ms')()
                    progress_record = {
                        'id': progress_history_id,
                        'type': 'online-image',
                        'status': 'running',
                        'prompt': payload.prompt,
                        'executed_prompt': executed_prompt,
                        'angle_preset': payload.angle_preset,
                        'created_at': started_at,
                        'createdAt': started_at,
                        'completed_at': progress_timestamp,
                        'runMs': max(0, progress_timestamp - started_at),
                        'routing_mode': payload.routing_mode,
                        'requested_provider_id': payload.provider_id,
                        'requested_model': payload.model,
                        'actual_provider_id': route.provider_id,
                        'actual_provider_name': route.provider_name,
                        'actual_model': route.model,
                        'model': route.model,
                        'route_reason': route.reason,
                        'params': request_snapshot.to_dict(),
                        'outputs': [{'asset_id': produced['asset_id'], 'kind': 'image'} for produced in generated_items],
                        'result_node_id': payload.result_node_id,
                    }
                    progress_canvas = await asyncio.to_thread(_svc._main('project_store_call'), 'complete_image_generation', project_id, payload.result_node_id, [item], progress_record, task_id=task_id, partial=True)
                    await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
                    await _svc._main('manager').broadcast_canvas_updated(project_id, int(progress_canvas.get('updated_at') or progress_timestamp))
        finally:
            PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope_token)
        if not generated_items:
            raise ProjectStoreError('在线模型没有返回图片')
        timestamp = _svc._main('now_ms')()
        history_record = {'id': progress_history_id, 'type': 'online-image', 'status': 'succeeded', 'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'angle_preset': payload.angle_preset, 'created_at': started_at, 'createdAt': started_at, 'completed_at': timestamp, 'runMs': max(0, timestamp - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'outputs': [{'asset_id': item['asset_id'], 'kind': 'image'} for item in generated_items], 'result_node_id': payload.result_node_id}
        canvas = await asyncio.to_thread(_svc._main('project_store_call'), 'complete_image_generation', project_id, payload.result_node_id, generated_items, history_record, task_id=task_id)
        await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        image_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        result = {'prompt': payload.prompt, 'executed_prompt': executed_prompt, 'images': [item['url'] for item in image_items], 'image_items': image_items, 'provider_id': route.provider_id, 'provider_name': route.provider_name, 'model': route.model, 'route': route.to_dict(), 'history_id': history_record['id'], 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
        await _svc.cleanup_project_generation_orphans(project_id)
        await _svc._main('manager').broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or _svc._main('now_ms')()))
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        failure_time = _svc._main('now_ms')()
        failure_result = None
        failure_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-image', 'status': 'failed', 'prompt': payload.prompt, 'executed_prompt': apply_angle_preset(payload.prompt, payload.angle_preset), 'angle_preset': payload.angle_preset, 'created_at': started_at, 'createdAt': started_at, 'completed_at': failure_time, 'runMs': max(0, failure_time - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'outputs': [], 'result_node_id': payload.result_node_id, 'error': str(detail)}
        try:
            failed_canvas = await asyncio.to_thread(_svc._main('project_store_call'), 'fail_image_generation', project_id, payload.result_node_id, task_id, failure_record)
            await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
            await _svc._main('manager').broadcast_canvas_updated(project_id, int(failed_canvas.get('updated_at') or failure_time))
            failure_result = {'history_recorded': True, 'history_record': failure_record, 'project_id': project_id, 'project_revision': failed_canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in failed_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_IMAGE_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
            CANVAS_TASKS[task_id].update({'status': 'failed', 'error': str(detail), 'status_code': getattr(exc, 'status_code', 500), 'result': failure_result, 'updated_at': time.time()})
        await _svc.cleanup_project_generation_orphans(project_id)

async def run_project_video_task(task_id: str, project_id: str, payload: ProjectVideoGenerationRequest, request_snapshot: VideoGenerationRequest, route, registered_revision: int, references: Optional[List[Dict[str, Any]]]=None, provider: Optional[Mapping[str, Any]]=None):
    started_at = _svc._main('now_ms')()
    generated_items = []
    preexisting_asset_ids = set()
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id]['status'] = 'running'
        CANVAS_TASKS[task_id]['updated_at'] = time.time()
    try:
        preexisting_asset_ids = {item.get('asset_id') for item in await asyncio.to_thread(_svc._main('project_store_call'), 'list_media', project_id) if item.get('asset_id')}
        references = copy.deepcopy(references) if references is not None else await asyncio.to_thread(project_video_all_references, project_id, payload)
        candidates, raw = await _svc._main('execute_project_video_generation')(project_id, request_snapshot, route, references, provider)
        for candidate in candidates:
            generated_items.append(await archive_project_generated_video(project_id, candidate, len(generated_items)))
        if not generated_items:
            raise ProjectStoreError('在线模型没有返回视频')
        completed_at = _svc._main('now_ms')()
        history_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-video', 'status': 'succeeded', 'prompt': payload.prompt, 'executed_prompt': payload.prompt, 'created_at': started_at, 'createdAt': started_at, 'completed_at': completed_at, 'runMs': max(0, completed_at - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'references': [{'asset_id': item['asset_id'], 'kind': item['kind']} for item in references], 'outputs': [{'asset_id': item['asset_id'], 'kind': 'video'} for item in generated_items], 'result_node_id': payload.result_node_id}
        canvas = await asyncio.to_thread(_svc._main('project_store_call'), 'complete_media_generation', project_id, payload.result_node_id, generated_items, history_record, media_kind='video')
        await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
        base = f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/"
        video_items = [{**item, 'url': base + urllib.parse.quote(item['asset_id'], safe='')} for item in generated_items]
        result = {'prompt': payload.prompt, 'videos': [item['url'] for item in video_items], 'video_items': video_items, 'media_items': video_items, 'provider_id': route.provider_id, 'provider_name': route.provider_name, 'model': route.model, 'route': route.to_dict(), 'history_id': history_record['id'], 'history_recorded': True, 'history_record': history_record, 'project_id': project_id, 'project_revision': canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None), 'raw': raw if isinstance(raw, dict) else {}}
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
        await _svc.cleanup_project_generation_orphans(project_id)
        await _svc._main('manager').broadcast_canvas_updated(project_id, int(canvas.get('updated_at') or completed_at))
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        failure_time = _svc._main('now_ms')()
        failure_record = {'id': f'generation-{uuid.uuid4().hex}', 'type': 'online-video', 'status': 'failed', 'prompt': payload.prompt, 'executed_prompt': payload.prompt, 'created_at': started_at, 'createdAt': started_at, 'completed_at': failure_time, 'runMs': max(0, failure_time - started_at), 'routing_mode': payload.routing_mode, 'requested_provider_id': payload.provider_id, 'requested_model': payload.model, 'actual_provider_id': route.provider_id, 'actual_provider_name': route.provider_name, 'actual_model': route.model, 'model': route.model, 'route_reason': route.reason, 'params': request_snapshot.to_dict(), 'outputs': [], 'result_node_id': payload.result_node_id, 'error': str(detail)}
        failure_result = None
        try:
            failed_canvas = await asyncio.to_thread(_svc._main('project_store_call'), 'fail_media_generation', project_id, payload.result_node_id, task_id, failure_record)
            await asyncio.to_thread(_svc._main('project_store_call'), 'flush', project_id)
            await _svc._main('manager').broadcast_canvas_updated(project_id, int(failed_canvas.get('updated_at') or failure_time))
            failure_result = {'history_recorded': True, 'history_record': failure_record, 'project_id': project_id, 'project_revision': failed_canvas['revision'], 'registered_revision': registered_revision, 'result_node_id': payload.result_node_id, 'final_node': next((item for item in failed_canvas.get('nodes') or [] if item.get('id') == payload.result_node_id), None)}
        except Exception:
            pass
        with CANVAS_TASK_LOCK:
            orphan_ids = PROJECT_VIDEO_GENERATION_ORPHANS.setdefault(project_id, set())
            orphan_ids.update((item.get('asset_id') for item in generated_items if item.get('asset_id') and item.get('asset_id') not in preexisting_asset_ids))
            CANVAS_TASKS[task_id].update({'status': 'failed', 'error': str(detail), 'status_code': getattr(exc, 'status_code', 500), 'result': failure_result, 'updated_at': time.time()})
        await _svc.cleanup_project_generation_orphans(project_id)

async def run_canvas_image_task(task_id: str, payload: OnlineImageRequest):
    with CANVAS_TASK_LOCK:
        if task_id in CANVAS_TASKS:
            CANVAS_TASKS[task_id]['status'] = 'running'
            CANVAS_TASKS[task_id]['updated_at'] = time.time()
    try:
        result = await _svc.build_online_image_result(payload)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'succeeded', 'result': result, 'error': '', 'updated_at': time.time()})
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        status_code = getattr(exc, 'status_code', 500)
        upstream_task_id = getattr(exc, 'upstream_task_id', '') or extract_task_id_from_text(detail)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({'status': 'failed', 'error': str(detail), 'status_code': status_code, 'upstream_task_id': upstream_task_id, 'updated_at': time.time()})

async def wait_for_video_task(client, provider, task_id, submit_url=''):
    base_url = _svc.video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    task_urls = video_task_url_candidates(provider, base_url, task_id, submit_url)
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    delay = max(2.0, IMAGE_POLL_INTERVAL)
    last_payload = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        raw = None
        last_error = None
        only_not_found = True
        for task_url in task_urls:
            try:
                response = await client.get(task_url, headers=_svc._main('api_headers')(provider=provider))
                response.raise_for_status()
                raw = response.json()
                break
            except Exception as exc:
                last_error = exc
                # #235：仅当全部候选都 404 → 显式「服务商侧任务不存在」（文案带 task ID）；
                # 有任何非 404 错误保持旧行为抛最后一个错误。
                if not (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404):
                    only_not_found = False
                continue
        if raw is None:
            if last_error is not None and only_not_found:
                raise HTTPException(status_code=502, detail=f'服务商侧视频任务不存在（task_id={task_id}），请确认任务 ID 后显式重试')
            if last_error:
                raise last_error
            raise HTTPException(status_code=502, detail=f'视频任务查询失败：{task_id}')
        last_payload = raw
        task_data = raw.get('data') if isinstance(raw.get('data'), dict) else raw.get('detail') if isinstance(raw.get('detail'), dict) else raw
        status = str(task_data.get('status') or task_data.get('task_status') or raw.get('status') or raw.get('task_status') or '').upper()
        if status in VIDEO_TASK_SUCCESS_STATUSES:
            return raw
        if status not in VIDEO_TASK_FAILURE_STATUSES and video_output_urls(raw):
            return raw
        if status in VIDEO_TASK_FAILURE_STATUSES:
            error = task_data.get('error') if isinstance(task_data.get('error'), dict) else {}
            reason = task_data.get('fail_reason') or task_data.get('message') or error.get('message') or raw.get('error') or raw.get('message') or str(raw)
            raise HTTPException(status_code=502, detail=humanize_video_task_failure(reason))
        delay = min(delay * 1.6, 12)
    raise HTTPException(status_code=504, detail=f'视频生成任务超时：{last_payload or task_id}')

async def wait_for_agnes_video_task(client, provider, video_id, model):
    base_url = _svc.video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    query_url = f"{base_url}/agnesapi?{urllib.parse.urlencode({'video_id': video_id, 'model_name': model})}"
    legacy_url = f"{base_url}/v1/videos/{urllib.parse.quote(str(video_id), safe='')}"
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    delay = 5.0
    last_payload = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        raw = None
        last_error = None
        for url in (query_url, legacy_url):
            try:
                response = await client.get(url, headers=_svc._main('api_headers')(provider=provider, model=model))
                response.raise_for_status()
                raw = response.json()
                break
            except Exception as exc:
                last_error = exc
        if raw is None:
            if last_error:
                raise last_error
            raise HTTPException(status_code=502, detail=f'Agnes 视频任务查询失败：{video_id}')
        last_payload = raw
        task_data = raw.get('data') if isinstance(raw.get('data'), dict) else raw
        status = str(task_data.get('status') or raw.get('status') or '').upper()
        if status in VIDEO_TASK_SUCCESS_STATUSES or video_output_urls(raw):
            return raw
        if status in VIDEO_TASK_FAILURE_STATUSES:
            error = task_data.get('error') if isinstance(task_data.get('error'), dict) else {}
            reason = task_data.get('message') or error.get('message') or raw.get('error') or raw.get('message') or str(raw)
            raise HTTPException(status_code=502, detail=humanize_video_task_failure(reason))
        delay = min(delay * 1.35, 12)
    raise HTTPException(status_code=504, detail=f'Agnes 视频生成任务超时：{last_payload or video_id}')

async def generate_agnes_video(client, payload, provider, base_url, requested_model):
    model = selected_model(requested_model, 'agnes-video-v2.0')
    width, height = agnes_video_dimensions(payload.aspect_ratio, payload.resolution, payload.width, payload.height)
    num_frames, frame_rate = agnes_video_frame_count(payload.duration, 24)
    body = {'model': model, 'prompt': str(payload.prompt or ''), 'width': width, 'height': height, 'num_frames': num_frames, 'frame_rate': frame_rate}
    image_urls = []
    image_roles = []
    for ref in (payload.images or [])[:4]:
        url = await _svc.agnes_video_image_url(ref)
        if url:
            image_urls.append(url)
            image_roles.append(str(getattr(ref, 'role', '') or '').strip().lower())
    if len(image_urls) == 1:
        body['image'] = image_urls[0]
    elif len(image_urls) > 1:
        body['extra_body'] = {'image': image_urls}
        has_frame_roles = any((role in {'first_frame', 'last_frame'} for role in image_roles))
        if payload.multimodal or has_frame_roles:
            body['extra_body']['mode'] = 'keyframes'
    if payload.seed is not None:
        body['seed'] = payload.seed
    submit_url = f'{base_url}/v1/videos'
    response = await client.post(submit_url, headers=_svc._main('api_headers')(provider=provider, model=model), json=body)
    response.raise_for_status()
    raw = response.json()
    video_id = str(raw.get('video_id') or '').strip()
    task_id = str(raw.get('task_id') or raw.get('id') or '').strip()
    result = raw
    if video_id and (not video_output_urls(raw)):
        result = await wait_for_agnes_video_task(client, provider, video_id, model)
    elif task_id and (not video_output_urls(raw)):
        result = await wait_for_video_task(client, provider, task_id, submit_url)
    urls = video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f'Agnes 视频生成成功但没有返回视频：{result}')
    local_urls = [await _svc._main('save_remote_video_to_output')(url) for url in urls]
    return {'videos': local_urls, 'task_id': task_id or video_id, 'video_id': video_id or None, 'raw': result}

async def generate_lingjing_openai_video(client, payload, provider, base_url, requested_model):
    """灵境 API OpenAI 视频格式：POST /v1/videos，参考图走 multipart input_reference。"""
    submit_url = f'{base_url}/v1/videos'
    data = {'model': lingjing_openai_video_model(selected_model(requested_model, 'veo_3_1-fast')), 'prompt': str(payload.prompt or ''), 'seconds': yuli_video_seconds(payload.duration), 'size': yuli_openai_size(payload.aspect_ratio or payload.size), 'watermark': 'true' if payload.watermark else 'false'}
    files = []
    for ref in (payload.images or [])[:3]:
        ref_file = await _svc.yuli_fetch_reference_bytes(client, getattr(ref, 'url', ''))
        if ref_file:
            files.append(('input_reference', ref_file))
    headers = _svc._main('api_headers')(json_body=False, provider=provider)
    if files:
        response = await client.post(submit_url, headers=headers, data=data, files=files)
    else:
        multipart_fields = [(key, (None, value)) for key, value in data.items()]
        response = await client.post(submit_url, headers=headers, files=multipart_fields)
    response.raise_for_status()
    try:
        raw = response.json()
    except Exception as exc:
        resp_text = (response.text or '')[:500]
        raise HTTPException(status_code=502, detail=f'灵境 API 视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}') from exc
    task_id = str(raw.get('id') or extract_task_id(raw) or raw.get('task_id') or '').strip()
    result = raw
    if task_id and (not video_output_urls(raw)):
        result = await wait_for_video_task(client, provider, task_id, submit_url)
    urls = video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f'灵境 API 视频生成成功但没有返回视频：{result}')
    local_urls = [await _svc._main('save_remote_video_to_output')(url) for url in urls]
    return {'videos': local_urls, 'task_id': task_id, 'raw': result}

async def generate_yuli_openai_video(client, payload, provider, base_url, requested_model):
    """玉玉API veo3.1 走 OpenAI multipart 格式 /v1/videos，支持 seconds 时长控制。"""
    submit_url = f'{base_url}/v1/videos'
    data = {'model': yuli_openai_model_name(requested_model), 'prompt': str(payload.prompt or ''), 'seconds': yuli_video_seconds(payload.duration), 'size': yuli_openai_size(payload.aspect_ratio), 'watermark': 'true' if payload.watermark else 'false'}
    files = {}
    for ref in (payload.images or [])[:1]:
        ref_file = await _svc.yuli_fetch_reference_bytes(client, getattr(ref, 'url', ''))
        if ref_file:
            files['input_reference'] = ref_file
            break
    headers = _svc._main('api_headers')(json_body=False, provider=provider)
    if files:
        response = await client.post(submit_url, headers=headers, data=data, files=files)
    else:
        multipart_fields = {key: (None, value) for key, value in data.items()}
        response = await client.post(submit_url, headers=headers, files=multipart_fields)
    response.raise_for_status()
    try:
        raw = response.json()
    except Exception as exc:
        resp_text = (response.text or '')[:500]
        raise HTTPException(status_code=502, detail=f'玉玉API 视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}') from exc
    task_id = raw.get('id') or extract_task_id(raw) or raw.get('task_id')
    result = raw
    if task_id and (not video_output_urls(raw)):
        result = await wait_for_video_task(client, provider, task_id, submit_url)
    urls = video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f'视频生成成功但没有返回视频：{result}')
    local_urls = [await _svc._main('save_remote_video_to_output')(url) for url in urls]
    return {'videos': local_urls, 'task_id': task_id, 'raw': result}

async def generate_kling_video(client, payload, provider, base_url, requested_model):
    """快手可灵官方格式：POST /kling/v1/videos/text2video，查询 GET /kling/v1/images/text2video/{task_id}。"""
    submit_url = f'{base_url}/kling/v1/videos/text2video'
    data = {
        'model_name': kling_model_name(requested_model),
        'prompt': str(payload.prompt or ''),
        'aspect_ratio': kling_aspect_ratio(payload.aspect_ratio or payload.size),
        'duration': kling_duration(payload.duration),
        'mode': 'pro' if getattr(payload, 'quality', '') == 'pro' else 'std',
    }
    if payload.negative_prompt:
        data['negative_prompt'] = str(payload.negative_prompt or '')[:200]
    headers = _svc._main('api_headers')(json_body=False, provider=provider)
    response = await client.post(submit_url, headers=headers, json=data)
    response.raise_for_status()
    try:
        raw = response.json()
    except Exception as exc:
        resp_text = (response.text or '')[:500]
        raise HTTPException(status_code=502, detail=f'可灵视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}') from exc
    task_id = str(raw.get('data') or {}).get('task_id') if isinstance(raw.get('data'), dict) else None
    if not task_id:
        task_id = str(raw.get('task_id') or extract_task_id(raw) or '').strip()
    if not task_id:
        raise HTTPException(status_code=502, detail=f'可灵视频接口未返回任务 ID：{raw}')
    task_status = (raw.get('data') or {}).get('task_status') if isinstance(raw.get('data'), dict) else None
    result = raw
    if task_status != 'succeed':
        result = await wait_for_kling_video_task(client, provider, task_id)
    urls = kling_video_output_urls(result)
    if not urls:
        raise HTTPException(status_code=502, detail=f'可灵视频生成成功但没有返回视频：{result}')
    local_urls = [await _svc._main('save_remote_video_to_output')(url) for url in urls]
    return {'videos': local_urls, 'task_id': task_id, 'raw': result}

async def wait_for_kling_video_task(client, provider, task_id):
    query_url = f'{_svc.video_api_root(provider)}/kling/v1/videos/text2video/{task_id}'
    headers = _svc._main('api_headers')(json_body=False, provider=provider)
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    last_detail = ''
    while time.monotonic() < deadline:
        try:
            response = await client.get(query_url, headers=headers)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            await asyncio.sleep(2.0)
            last_detail = f'查询失败：{exc}'
            continue
        data = body.get('data') if isinstance(body.get('data'), dict) else {}
        task_status = str(data.get('task_status') or body.get('task_status') or '').strip()
        if task_status == 'succeed':
            return body
        if task_status == 'failed':
            detail = str(data.get('task_status_msg') or body.get('task_status_msg') or last_detail or '任务失败')
            raise HTTPException(status_code=502, detail=f'可灵视频任务失败：{detail}')
        last_detail = str(data.get('task_status_msg') or '') or last_detail
        await asyncio.sleep(3.0)
    raise HTTPException(status_code=502, detail=f'可灵视频任务超时：{last_detail or "任务未在限定时间内完成"}')

def kling_model_name(requested_model):
    model_id = str(requested_model or '').strip().lower()
    if model_id == 'kling-video-v3' or model_id.startswith('kling-video-v1'):
        return 'kling-v1-6'
    if model_id.startswith('kling-v1'):
        return 'kling-v1-6'
    return str(requested_model or 'kling-v1-6')

def kling_aspect_ratio(size):
    ratio = _svc._main('gemini_supported_aspect_ratio')(size)
    if ratio in ('16:9', '9:16', '1:1'):
        return ratio
    return '16:9'

def kling_duration(duration):
    try:
        seconds = int(float(duration or 0))
    except (TypeError, ValueError):
        seconds = 0
    return '10' if seconds >= 10 else '5'

def kling_video_output_urls(raw):
    urls = []
    data = raw.get('data') if isinstance(raw.get('data'), dict) else {}
    result = data.get('task_result') if isinstance(data.get('task_result'), dict) else {}
    videos = result.get('videos') if isinstance(result.get('videos'), list) else []
    for item in videos:
        if isinstance(item, dict):
            url = str(item.get('url') or '').strip()
            if url:
                urls.append(url)
    return urls or video_output_urls(raw)

def project_video_candidate_inline_reference_kinds(provider: Mapping[str, Any], model: str) -> set[str]:
    if is_runninghub_provider(provider) or is_apimart_provider(provider) or is_yuli_provider(provider) or is_agnes_provider(provider, model):
        return set()
    if is_volcengine_provider(provider):
        base_url = _svc.video_api_root(provider)
        is_proxy = bool(urllib.parse.urlparse(base_url).path.rstrip('/'))
        return {'video', 'audio'} if is_proxy else {'audio'}
    return {'video', 'audio'}

def project_video_candidate_execution_incompatible_parameters(provider: Mapping[str, Any], model: str, references: List[Dict[str, Any]]) -> List[str]:
    """Return synchronous execution blockers for one concrete route candidate."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)
    if is_runninghub_provider(provider):
        wallet_key = str(provider.get('wallet_api_key') or '').strip() or _svc._main('runninghub_wallet_key_value')()
        if not wallet_key:
            add('api_key')
    else:
        if not _svc.video_api_root(provider):
            add('base_url')
        if not _svc._main('provider_env_key_value')(str(provider.get('id') or '')):
            add('api_key')
    inline_kinds = project_video_candidate_inline_reference_kinds(provider, model)
    if inline_kinds:
        for reference in references:
            if str(reference.get('kind') or '').strip().lower() not in inline_kinds:
                continue
            path = Path(str(reference.get('path') or ''))
            try:
                if path.stat().st_size > _svc._main('PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES'):
                    add('reference_size')
                    break
            except OSError:
                add('reference_size')
                break
    return incompatible

def video_adapter_incompatible_parameters(payload: CanvasVideoRequest, provider: Mapping[str, Any]) -> List[str]:
    """Return parameters a specialized video adapter would drop, clamp, or truncate."""
    incompatible: List[str] = []

    def add(name: str) -> None:
        if name not in incompatible:
            incompatible.append(name)
    if str(payload.media_type or 'video').strip().lower() != 'video':
        add('media_type')
    parsed_width, parsed_height = parse_size_pair(payload.size)
    if parsed_width and parsed_height:
        if payload.width is not None and payload.width != parsed_width:
            add('width')
        if payload.height is not None and payload.height != parsed_height:
            add('height')
    elif payload.width is not None or payload.height is not None:
        add('size')
    quality = str(payload.quality or 'auto').strip().lower() or 'auto'
    size = str(payload.size or '').strip()
    aspect_ratio = str(payload.aspect_ratio or '').strip()
    resolution = str(payload.resolution or '').strip().lower()
    image_count = len(payload.images or [])
    video_count = len(payload.videos or [])
    audio_count = len(payload.audios or [])
    if effective_protocol(provider, payload.model) not in VIDEO_EXECUTION_PROTOCOLS:
        add('protocol')
    is_runninghub = is_runninghub_provider(provider)
    is_apimart = is_apimart_provider(provider)
    is_volcengine = is_volcengine_provider(provider)
    volc_is_proxy = bool(is_volcengine and urllib.parse.urlparse(_svc.video_api_root(provider)).path.rstrip('/'))
    is_volcengine_direct = is_volcengine and (not volc_is_proxy)
    is_yuli = is_yuli_provider(provider)
    is_lingjing = is_lingjing_provider(provider)
    is_agnes = is_agnes_provider(provider, payload.model)
    specialized = is_runninghub or is_apimart or is_volcengine_direct or is_yuli or is_lingjing or is_agnes
    if (not specialized) and 'protocol' in incompatible:
        return incompatible
    if not specialized:
        return incompatible
    if quality != 'auto':
        add('quality')
    if is_runninghub:
        if payload.width is not None:
            add('width')
        if payload.height is not None:
            add('height')
        if size:
            add('size')
        if image_count > 10:
            add('image_reference_count')
        if video_count:
            add('video_reference_count')
        if audio_count:
            add('audio_reference_count')
        if payload.seed is not None:
            add('seed')
        if payload.enhance_prompt:
            add('enhance_prompt')
        if payload.enable_upsample:
            add('enable_upsample')
        if canvas_video_camera_fixed(payload):
            add('camera_fixed')
        if payload.return_last_frame:
            add('return_last_frame')
        return incompatible
    if is_apimart:
        if payload.width is not None:
            add('width')
        if payload.height is not None:
            add('height')
        if size:
            add('size')
        is_veo31 = is_apimart_veo31_model(payload.model)
        if is_veo31:
            if payload.duration < 4 or payload.duration > 8:
                add('duration')
            if aspect_ratio not in {'16:9', '9:16'}:
                add('aspect_ratio')
            if resolution and resolution not in {'720p', '1080p', '4k'}:
                add('resolution')
            veo_model = apimart_veo31_model(payload.model)
            image_limit = 0 if veo_model == 'veo3.1-lite' else 2 if veo_model == 'veo3.1-quality' else 3
            if video_count:
                add('video_reference_count')
            if audio_count:
                add('audio_reference_count')
            if payload.seed is not None:
                add('seed')
            if payload.return_last_frame:
                add('return_last_frame')
            if payload.generate_audio:
                add('generate_audio')
        else:
            if payload.duration < 4 or payload.duration > 15:
                add('duration')
            if aspect_ratio and apimart_video_size(aspect_ratio) != aspect_ratio and (aspect_ratio != 'keep_ratio'):
                add('aspect_ratio')
            image_limit = 9
            role_count = sum((1 for ref in payload.images or [] if str(getattr(ref, 'role', '') or '').strip() in {'first_frame', 'last_frame', 'reference_image'}))
            if role_count and role_count != image_count:
                add('image_reference_count')
            if role_count and video_count:
                add('video_reference_count')
        if image_count > image_limit:
            add('image_reference_count')
        if not is_veo31 and video_count > 3:
            add('video_reference_count')
        if not is_veo31 and audio_count > 3:
            add('audio_reference_count')
        if payload.enhance_prompt:
            add('enhance_prompt')
        if payload.enable_upsample:
            add('enable_upsample')
        if payload.watermark:
            add('watermark')
        if canvas_video_camera_fixed(payload):
            add('camera_fixed')
        return incompatible
    if is_volcengine_direct:
        if payload.width is not None:
            add('width')
        if payload.height is not None:
            add('height')
        if size:
            add('size')
        if payload.duration < 1 or payload.duration > 60:
            add('duration')
        if image_count > 9:
            add('image_reference_count')
        accepted_image_count = 0
        frame_roles_used = set()
        for ref in (payload.images or [])[:9]:
            role = volcengine_content_role(getattr(ref, 'role', ''), 'image')
            if role in {'first_frame', 'last_frame'}:
                if role in frame_roles_used:
                    continue
                frame_roles_used.add(role)
                accepted_image_count += 1
            elif payload.multimodal:
                accepted_image_count += 1
            elif 'first_frame' not in frame_roles_used:
                frame_roles_used.add('first_frame')
                accepted_image_count += 1
        if accepted_image_count != image_count:
            add('image_reference_count')
        if video_count > 3 or any((not is_private_asset_url(str(value or '').strip()) for value in payload.videos or [])):
            add('video_reference_count')
        if audio_count > 3:
            add('audio_reference_count')
        if image_count > 1 and (not payload.multimodal) and (not any((str(getattr(ref, 'role', '') or '').strip().lower() in {'first_frame', 'last_frame'} for ref in payload.images or []))):
            add('image_reference_count')
        if resolution and (not volcengine_video_resolution(resolution)):
            add('resolution')
        if payload.enhance_prompt:
            add('enhance_prompt')
        if payload.enable_upsample:
            add('enable_upsample')
        if payload.return_last_frame:
            add('return_last_frame')
        return incompatible
    if is_agnes:
        if size:
            add('size')
        if payload.duration != 5:
            add('duration')
        if image_count > 4:
            add('image_reference_count')
        if video_count:
            add('video_reference_count')
        if audio_count:
            add('audio_reference_count')
        has_width = payload.width is not None
        has_height = payload.height is not None
        if has_width != has_height:
            add('width' if has_width else 'height')
        if has_width and has_height:
            if aspect_ratio not in {'', '16:9'}:
                add('aspect_ratio')
            if resolution:
                add('resolution')
        else:
            if aspect_ratio and aspect_ratio not in {'16:9', '9:16', '4:3', '3:4', '1:1', '21:9', '9:21'}:
                add('aspect_ratio')
            if resolution and resolution not in {'480p', '720p', '780p', '1080p'}:
                add('resolution')
        if payload.enhance_prompt:
            add('enhance_prompt')
        if payload.enable_upsample:
            add('enable_upsample')
        if payload.watermark:
            add('watermark')
        if canvas_video_camera_fixed(payload):
            add('camera_fixed')
        if payload.return_last_frame:
            add('return_last_frame')
        if payload.generate_audio:
            add('generate_audio')
        return incompatible
    if is_yuli or is_lingjing:
        if payload.width is not None:
            add('width')
        if payload.height is not None:
            add('height')
        if size:
            add('size')
        if resolution:
            add('resolution')
        yuli_openai = bool(is_yuli and yuli_is_veo_openai_model(payload.model))
        image_limit = 1 if yuli_openai else 3
        if image_count > image_limit:
            add('image_reference_count')
        if video_count:
            add('video_reference_count')
        if audio_count:
            add('audio_reference_count')
        if aspect_ratio and aspect_ratio not in {'16:9', '9:16'}:
            add('aspect_ratio')
        if is_yuli and (not yuli_openai) and (payload.duration != 5):
            add('duration')
        if payload.seed is not None:
            add('seed')
        if (not is_yuli or yuli_openai) and payload.enhance_prompt:
            add('enhance_prompt')
        if (not is_yuli or yuli_openai) and payload.enable_upsample:
            add('enable_upsample')
        if is_yuli and (not yuli_openai) and payload.watermark:
            add('watermark')
        if canvas_video_camera_fixed(payload):
            add('camera_fixed')
        if payload.return_last_frame:
            add('return_last_frame')
        if payload.generate_audio:
            add('generate_audio')
    return incompatible

async def execute_canvas_video(payload: CanvasVideoRequest, provider: Mapping[str, Any], on_provider_task=None):
    incompatible = video_adapter_incompatible_parameters(payload, provider)
    if incompatible:
        raise HTTPException(status_code=400, detail={'code': 'unsupported_video_adapter_parameters', 'message': str(provider.get('name') or provider['id']) + ' 不支持请求的参数'})
    adapter_label, adapter_ready = _svc._main('video_adapter_label')(payload.model, provider)
    if not adapter_ready:
        raise HTTPException(status_code=400, detail=f"模型 {payload.model} 未适配（{adapter_label}）。可用适配器：官方-可灵/火山/RunningHub、中转站统一格式(veo3.1/wan 等)、OpenAI 兼容。请先在 API 设置中更换为已适配模型。")
    if is_runninghub_provider(provider):
        try:
            return await generate_runninghub_video(payload, provider)
        except HTTPException as exc:
            logger.error(f'RunningHub 视频生成失败 model={payload.model}: {exc.detail}')
            raise
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f'RunningHub 视频接口错误：{text}') from exc
        except httpx.HTTPError as exc:
            log_net_error(f'视频(RunningHub) 网络/TLS错误 model={payload.model}', exc)
            raise HTTPException(status_code=502, detail=f'请求 RunningHub 视频接口失败：{exc}') from exc
    base_url = _svc.video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    api_key = _svc._main('provider_env_key_value')(provider['id'])
    if not api_key:
        raise HTTPException(status_code=400, detail=f"未配置 {provider.get('name') or provider['id']} 的 API Key，请在 API 设置中填写。")
    is_apimart = is_apimart_provider(provider)
    is_volcengine = is_volcengine_provider(provider)
    is_yuli = is_yuli_provider(provider)
    is_lingjing = is_lingjing_provider(provider)
    is_agnes = is_agnes_provider(provider, payload.model)
    volc_is_proxy = bool(is_volcengine and urllib.parse.urlparse(base_url).path.rstrip('/'))
    submit_urls = _svc.video_submit_url_candidates(provider, base_url, payload.model)
    submit_url = submit_urls[0]
    requested_model = selected_model(payload.model, 'agnes-video-v2.0' if is_agnes else 'veo3-fast')
    is_veo31 = is_apimart and is_apimart_veo31_model(requested_model)
    is_kling = _svc._main('is_kling_model')(requested_model)
    if is_kling:
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as kling_client:
                return await generate_kling_video(kling_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f'可灵视频接口错误：{text}') from exc
        except httpx.HTTPError as exc:
            log_net_error(f'视频(可灵) 网络/TLS错误 model={requested_model}', exc)
            raise HTTPException(status_code=502, detail=f'请求可灵视频接口失败：{exc}') from exc
    if is_agnes:
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as agnes_client:
                return await generate_agnes_video(agnes_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f'Agnes 视频接口错误：{text}') from exc
        except httpx.HTTPError as exc:
            log_net_error(f'视频(Agnes) 网络/TLS错误 model={requested_model}', exc)
            raise HTTPException(status_code=502, detail=f'请求 Agnes 视频接口失败：{exc}') from exc
    if is_lingjing:
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as lingjing_client:
                return await generate_lingjing_openai_video(lingjing_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f'灵境 API 视频接口错误：{text}') from exc
        except httpx.HTTPError as exc:
            log_net_error(f'视频(灵境) 网络/TLS错误 model={requested_model}', exc)
            raise HTTPException(status_code=502, detail=f'请求灵境 API 视频接口失败：{exc}') from exc
    if is_yuli and yuli_is_veo_openai_model(requested_model):
        try:
            async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as yuli_client:
                return await generate_yuli_openai_video(yuli_client, payload, provider, base_url, requested_model)
        except httpx.HTTPStatusError as exc:
            text = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=f'上游视频接口错误：{text}') from exc
        except httpx.HTTPError as exc:
            log_net_error(f'视频(玉玉) 网络/TLS错误 model={requested_model}', exc)
            raise HTTPException(status_code=502, detail=f'请求上游视频接口失败：{exc}') from exc
    try:
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            if is_apimart:
                image_with_roles = []
                invalid_images = []
                video_payload = []
                invalid_videos = []
                for ref_url in payload.videos[:3]:
                    ref_url = str(ref_url or '').strip()
                    if not ref_url:
                        continue
                    normalized_video_url = await upload_video_for_apimart(client, provider, ref_url)
                    if valid_apimart_video_image_input(normalized_video_url):
                        video_payload.append(normalized_video_url)
                    else:
                        reason = normalized_video_url[4:] if isinstance(normalized_video_url, str) and normalized_video_url.startswith('ERR:') else _svc.apimart_video_reference_error(ref_url)
                        invalid_videos.append((ref_url, reason))
                if invalid_videos:
                    first_url, first_reason = invalid_videos[0]
                    sample = invalid_video_image_preview(first_url)
                    raise HTTPException(status_code=400, detail=f'输入视频无法转换为 APIMart 支持的格式：{sample}\n原因：{first_reason}')
                apimart_model = apimart_veo31_model(requested_model) if is_veo31 else ''
                if apimart_model == 'veo3.1-lite' and payload.images:
                    raise HTTPException(status_code=400, detail='veo3.1-lite 不支持图片输入，请改用 veo3.1-fast 或 veo3.1-quality。')
                image_limit = 0 if apimart_model == 'veo3.1-lite' else 3 if is_veo31 else 9
                for ref in payload.images[:image_limit]:
                    if not ref.url:
                        continue
                    role = str(ref.role or '').strip()
                    if not is_veo31 and role in {'first_frame', 'last_frame', 'reference_image'}:
                        up_url = await upload_image_for_apimart(client, provider, ref.url)
                        if valid_apimart_video_image_input(up_url):
                            image_with_roles.append({'url': up_url, 'role': role})
                        else:
                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith('ERR:') else '未知错误'
                            invalid_images.append((ref.url, reason))
                image_payload = []
                if not image_with_roles:
                    for ref in payload.images[:image_limit]:
                        if not ref.url:
                            continue
                        up_url = await upload_image_for_apimart(client, provider, ref.url)
                        if valid_apimart_video_image_input(up_url):
                            image_payload.append(up_url)
                        else:
                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith('ERR:') else '未知错误'
                            invalid_images.append((ref.url, reason))
                if payload.images and (not image_with_roles) and (not image_payload):
                    first_url, first_reason = invalid_images[0] if invalid_images else ('', '未知错误')
                    sample = invalid_video_image_preview(first_url)
                    raise HTTPException(status_code=400, detail=f'输入图片无法转换为视频接口支持的格式：{sample}\n原因：{first_reason}\n请确认本地文件存在且不超过 10MB；VEO3.1 需要图片是 APIMart 可访问的 http/https / asset:// / data URL。')
                if is_veo31:
                    model = apimart_model
                    body = {'prompt': payload.prompt, 'model': model, 'duration': apimart_veo31_duration(payload.duration), 'aspect_ratio': apimart_veo31_aspect(payload.aspect_ratio), 'resolution': apimart_veo31_resolution(payload.resolution)}
                    if image_payload and model != 'veo3.1-lite':
                        video_images = image_payload[:3]
                        if model == 'veo3.1-quality' and len(video_images) > 2:
                            video_images = video_images[:2]
                        body['image_urls'] = video_images
                        if len(video_images) == 2:
                            body['generation_type'] = 'frame'
                        elif len(video_images) >= 3 and model != 'veo3.1-quality':
                            body['generation_type'] = 'reference'
                    if model != 'veo3.1-lite':
                        body['official_fallback'] = False
                else:
                    body = {'prompt': payload.prompt, 'model': selected_model(payload.model, 'doubao-seedance-2.0'), 'duration': apimart_video_duration(payload.duration), 'size': apimart_video_size(payload.aspect_ratio or payload.size), 'resolution': payload.resolution or '480p'}
                    if image_with_roles and video_payload:
                        raise HTTPException(status_code=400, detail='APIMart Seedance 的 image_with_roles 不能和 video_urls 同时使用，请只保留图片首尾帧或参考视频其中一种。')
                    if image_with_roles:
                        body['image_with_roles'] = image_with_roles
                    elif image_payload:
                        body['image_urls'] = image_payload[:9]
                    if video_payload:
                        body['video_urls'] = video_payload
                    audio_payload = []
                    invalid_audios = []
                    for ref_url in (payload.audios or [])[:3]:
                        ref_url = str(ref_url or '').strip()
                        if not ref_url:
                            continue
                        normalized_audio_url = await upload_audio_for_apimart(client, provider, ref_url)
                        if valid_apimart_video_image_input(normalized_audio_url):
                            audio_payload.append(normalized_audio_url)
                        else:
                            reason = normalized_audio_url[4:] if isinstance(normalized_audio_url, str) and normalized_audio_url.startswith('ERR:') else '未知错误'
                            invalid_audios.append((ref_url, reason))
                    if invalid_audios:
                        first_url, first_reason = invalid_audios[0]
                        raise HTTPException(status_code=400, detail=f'参考音频无法转换为 APIMart 支持的地址：{invalid_video_image_preview(first_url)}\n原因：{first_reason}')
                    if audio_payload:
                        body['audio_urls'] = audio_payload
                    if payload.trusted_asset:
                        img_count = len(body.get('image_urls') or []) or len(image_with_roles)
                        body['prompt'] = apply_trusted_asset_prompt_index(body['prompt'], img_count, len(video_payload), len(audio_payload))
                    if payload.seed is not None:
                        body['seed'] = payload.seed
                    if payload.return_last_frame:
                        body['return_last_frame'] = True
                    if payload.generate_audio:
                        body['generate_audio'] = True
            elif is_volcengine and (not volc_is_proxy):
                text = str(payload.prompt or '').strip()
                volc_model = selected_model(payload.model, 'doubao-seedance-2-0-fast-260128')
                body = {'model': volc_model, 'content': [{'type': 'text', 'text': text}]}
                body['duration'] = volcengine_video_duration(payload.duration)
                if payload.aspect_ratio:
                    body['ratio'] = payload.aspect_ratio
                resolution = volcengine_video_resolution(payload.resolution)
                if resolution:
                    body['resolution'] = resolution
                if payload.watermark:
                    body['watermark'] = True
                if payload.generate_audio:
                    body['generate_audio'] = True
                if canvas_video_camera_fixed(payload):
                    body['camerafixed'] = True
                image_like_urls = set()
                frame_roles_used = {'first_frame': False, 'last_frame': False}
                volc_video_count = 0

                def append_volcengine_image(url: str, role: str):
                    if role in {'first_frame', 'last_frame'}:
                        if frame_roles_used.get(role):
                            return False
                        frame_roles_used[role] = True
                    elif role != 'reference_image':
                        return False
                    body['content'].append({'type': 'image_url', 'image_url': {'url': url}, 'role': role})
                    image_like_urls.add(url)
                    return True
                for ref in payload.images[:9]:
                    url = _svc.volcengine_media_reference_url(ref.url, max_image_size=1536)
                    if not url:
                        continue
                    role = volcengine_content_role(ref.role, 'image')
                    if role in {'first_frame', 'last_frame'}:
                        append_volcengine_image(url, role)
                    elif payload.multimodal:
                        append_volcengine_image(url, 'reference_image')
                    elif not frame_roles_used['first_frame']:
                        append_volcengine_image(url, 'first_frame')
                for url in (payload.videos or [])[:3]:
                    text_url = str(url or '').strip()
                    if not text_url:
                        continue
                    media_url = _svc.volcengine_media_reference_url(text_url, max_image_size=1536 if looks_like_image_media_url(text_url) else None)
                    if not media_url:
                        continue
                    if media_url in image_like_urls or looks_like_image_media_url(media_url):
                        append_volcengine_image(media_url, 'reference_image' if payload.multimodal else 'first_frame')
                        continue
                    video_items = await _svc.volcengine_video_reference_content_items(media_url)
                    body['content'].extend(video_items)
                    volc_video_count += 1
                for url in (payload.audios or [])[:3]:
                    duration = _svc.probe_local_audio_duration_seconds(url)
                    if duration is not None and (duration < 1.8 or duration > 15.2):
                        raise HTTPException(status_code=400, detail=f'参考音频时长 {duration:.2f} 秒超出范围：方舟 Seedance 参考音频要求在 1.8 ~ 15.2 秒之间，请裁剪后再插入。')
                    audio_url = _svc.media_adapter_data_url(url, 'audio')
                    if not audio_url:
                        continue
                    body['content'].append({'type': 'audio_url', 'audio_url': {'url': audio_url}, 'role': volcengine_content_role('', 'audio')})
                if payload.trusted_asset and body['content'] and (body['content'][0].get('type') == 'text'):
                    body['content'][0]['text'] = apply_trusted_asset_prompt_index(body['content'][0].get('text') or '', len(image_like_urls), volc_video_count, 0)
                if payload.seed is not None:
                    body['seed'] = payload.seed
            elif is_yuli:
                yuli_images = []
                for ref in payload.images[:3]:
                    ref_url = str(getattr(ref, 'url', '') or '').strip()
                    if not ref_url:
                        continue
                    if ref_url.startswith('http://') or ref_url.startswith('https://'):
                        yuli_images.append(ref_url)
                    else:
                        data_url = _svc._main('reference_to_data_url')(ref.dict(), max_size=1536)
                        if data_url:
                            yuli_images.append(data_url)
                prompt_text = str(payload.prompt or '')
                body = {'model': selected_model(payload.model, 'veo3.1-fast'), 'prompt': prompt_text, 'enhance_prompt': bool(payload.enhance_prompt)}
                if yuli_images:
                    body['images'] = yuli_images
                ratio = str(payload.aspect_ratio or '').strip()
                if ratio in {'16:9', '9:16'}:
                    body['aspect_ratio'] = ratio
                if payload.enable_upsample:
                    body['enable_upsample'] = True
            else:
                unified_format = _svc._main('is_unified_format_video_model')(requested_model)
                if unified_format:
                    body = {'prompt': payload.prompt, 'model': requested_model}
                    ratio = str(payload.aspect_ratio or '').strip()
                    if ratio and ratio != 'adaptive':
                        body['aspect_ratio'] = ratio
                    if payload.duration:
                        body['duration'] = payload.duration
                    if payload.resolution:
                        body['resolution'] = payload.resolution
                    if payload.watermark:
                        body['watermark'] = payload.watermark
                    if payload.seed is not None:
                        body['seed'] = payload.seed
                    if payload.generate_audio:
                        body['generate_audio'] = True
                    if payload.enhance_prompt:
                        body['enhance_prompt'] = True
                    if payload.enable_upsample:
                        body['enable_upsample'] = True
                    if canvas_video_camera_fixed(payload):
                        body['camerafixed'] = True
                    if payload.return_last_frame:
                        body['return_last_frame'] = True
                    image_payload = []
                    for ref in payload.images:
                        if ref.url:
                            image_payload.append(_svc._main('reference_to_data_url')(ref.dict(), max_size=1536))
                    if image_payload:
                        body['images'] = image_payload
                    video_payload = [_svc.media_adapter_data_url(value, 'video') for value in payload.videos or [] if str(value or '').strip()]
                    if video_payload:
                        body['videos'] = video_payload
                else:
                    image_payload = []
                    for ref in payload.images:
                        if ref.url:
                            image_payload.append(_svc._main('reference_to_data_url')(ref.dict(), max_size=1536))
                    video_payload = [_svc.media_adapter_data_url(value, 'video') for value in payload.videos or [] if str(value or '').strip()]
                    audio_payload = [_svc.media_adapter_data_url(value, 'audio') for value in payload.audios or [] if str(value or '').strip()]
                    body = {'prompt': payload.prompt, 'model': selected_model(payload.model, 'veo3-fast'), 'duration': payload.duration, 'width': payload.width, 'height': payload.height, 'watermark': payload.watermark, 'quality': payload.quality, 'media_type': payload.media_type}
                    if payload.aspect_ratio:
                        body['aspect_ratio'] = payload.aspect_ratio
                        body['ratio'] = payload.aspect_ratio
                    if payload.size:
                        body['size'] = payload.size
                    if payload.resolution:
                        body['resolution'] = payload.resolution
                    if image_payload:
                        body['images'] = image_payload
                    if video_payload:
                        body['videos'] = video_payload
                    if audio_payload:
                        body['audios'] = audio_payload
                    if payload.enhance_prompt:
                        body['enhance_prompt'] = True
                    if payload.enable_upsample:
                        body['enable_upsample'] = True
                    if payload.seed is not None:
                        body['seed'] = payload.seed
                    if canvas_video_camera_fixed(payload):
                        body['camerafixed'] = True
                    if payload.return_last_frame:
                        body['return_last_frame'] = True
                    if payload.generate_audio:
                        body['generate_audio'] = True
            raw = None
            html_response = None
            last_response = None
            last_json_error = None
            total_candidates = len(submit_urls)
            for idx, candidate_url in enumerate(submit_urls):
                submit_url = candidate_url
                is_last = idx == total_candidates - 1
                response = await client.post(submit_url, headers=_svc._main('api_headers')(provider=provider, model=requested_model), json=body)
                last_response = response
                if response.status_code >= 400:
                    endpoint_missing = response.status_code in (404, 405) or looks_like_html_response(response.text)
                    if endpoint_missing and (not is_last):
                        continue
                    response.raise_for_status()
                try:
                    raw = response.json()
                    break
                except Exception as exc:
                    last_json_error = exc
                    if looks_like_html_response(response.text):
                        html_response = response
                        continue
                    if not is_last:
                        continue
                    resp_text = response.text[:500]
                    raise HTTPException(status_code=502, detail=f'上游视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}')
            if raw is None:
                resp = html_response or last_response
                status_code = getattr(resp, 'status_code', 200)
                resp_text = (getattr(resp, 'text', '') or '')[:500]
                raise HTTPException(status_code=502, detail=f'上游视频接口返回了网页 HTML，而不是 JSON（状态 {status_code}）。\n\n这通常表示 API 设置里的 Base URL 指到了第三方聚合平台的管理后台/网页入口，或该平台不支持当前视频接口路径。请确认 Base URL 是接口地址，例如以 /v1 结尾的 OpenAI 兼容地址，并确认该平台实际支持视频生成端点。\n\n原始响应：{resp_text}') from last_json_error
            task_id = extract_task_id(raw) or raw.get('task_id') or raw.get('id')
            if task_id and on_provider_task is not None:
                try:
                    # #82：提交成功即持久化服务商任务标识，服务重启后可按 task_id 重轮询，避免重复计费。
                    # 仅统一格式路径（APIMart/veo3/veo3.1/火山/玉玉/OpenAI 兼容）；专属适配器（可灵/RunningHub 等）后续补。
                    await on_provider_task({
                        'provider_id': str(provider.get('id') or ''),
                        'provider_name': str(provider.get('name') or ''),
                        'model': str(requested_model or ''),
                        'task_id': str(task_id),
                        'task_url': str(submit_url or ''),
                        'submitted_at': int(time.time() * 1000),
                    })
                except Exception:
                    pass  # 元数据持久化失败不阻断生成主流程
            result = raw
            if task_id and (not video_output_urls(raw)):
                result = await wait_for_video_task(client, provider, task_id, submit_url)
            urls = video_output_urls(result)
            if not urls:
                raise HTTPException(status_code=502, detail=f'视频生成成功但没有返回视频：{result}')
            local_urls = [await _svc._main('save_remote_video_to_output')(url) for url in urls]
            return {'videos': local_urls, 'task_id': task_id, 'raw': result}
    except httpx.HTTPStatusError as exc:
        text = exc.response.text
        try:
            requested_model = body.get('model', '') or payload.model or ''
        except NameError:
            requested_model = payload.model or ''
        provider_name = provider.get('name') or provider['id']
        valid_models_match = re.search('not in\\s*\\[([^\\]]+)\\]', text)
        if valid_models_match:
            valid_models = [m.strip() for m in valid_models_match.group(1).split(',') if m.strip()]
            sample = valid_models[:30]
            more = f'（共 {len(valid_models)} 个，仅显示前 {len(sample)} 个）' if len(valid_models) > len(sample) else ''
            hint = f"上游「{provider_name}」不识别模型「{requested_model}」。\n\n上游支持的视频模型清单{more}：\n  {', '.join(sample)}\n\n请到「API 设置」里把视频模型改成上面列表中的一个。"
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if 'channel not found' in text or 'model_not_found' in text:
            hint = f"上游「{provider_name}」识别了模型「{requested_model}」，但你的 API Key 账号下**没有该模型的可用通道**。\n\n原因：你的账号没开通这个模型的访问权限（付费/订阅相关）。\n\n解决方法：\n  1. 登录 {provider.get('base_url') or '上游平台'} 控制台，开通该模型 / 充值；\n  2. 或在「API 设置」里把视频模型改成你账号已开通的型号（如 veo3-fast / veo2-fast / sora-2 等）。"
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if 'text.duration' in text or 'specified duration is not supported' in text:
            hint = f'上游「{provider_name}」模型「{requested_model}」不支持当前时长参数。\n\n不同视频模型支持的时长不一样；如果选择了模型不支持的时长，上游可能报错，也可能自动按平台默认时长生成，例如 5 秒。\n\n请把视频时长切回该模型支持的值，或改用支持更长时长的视频模型。'
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if 'audio duration' in text.lower():
            too_long = 'less than or equal' in text.lower() or '15.2' in text
            bound_hint = '太长（超过 15.2 秒）' if too_long else '太短（不足 1.8 秒）'
            hint = f'上游「{provider_name}」模型「{requested_model}」拒绝了参考音频：时长{bound_hint}。\n\n方舟 Seedance 的参考音频时长必须在 1.8 ~ 15.2 秒之间，请把音频裁剪到这个区间后再作为参考音频输入。'
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        if 'inputimagesensitivecontentdetected' in text.lower() or 'privacyinformation' in text.lower() or 'may contain real person' in text.lower():
            hint = f'上游「{provider_name}」拦截了输入参考图，原因是图片里可能包含真人身份/隐私信息。\n\n这不是代码协议错误，而是火山视频模型的内容安全策略。\n\n建议你这样处理：\n  1. 改用非真人参考图，例如插画、AI 头像、商品图、场景图；\n  2. 先把真人脸做模糊、遮挡、裁掉，或转成明显的二次元/插画风；\n  3. 如果只是想做文生视频，先去掉参考图只保留文字提示词测试。'
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        raise HTTPException(status_code=exc.response.status_code, detail=f'上游视频接口错误：{text}') from exc
    except httpx.HTTPError as exc:
        log_net_error(f"视频 网络/TLS错误 provider={provider.get('id')} model={payload.model}", exc)
        raise HTTPException(status_code=502, detail=f'请求上游视频接口失败：{exc}') from exc

def copy_project_media_to_global_library(project_id: str, asset_id: str) -> Dict[str, Any]:
    record = _svc._main('project_store_call')('media_record', project_id, asset_id)
    kind, extension = _local_upload_kind_ext(str(record.get('name') or record.get('filename') or ''), str(record.get('mime_type') or ''))
    if kind not in {'image', 'video', 'audio'}:
        raise ProjectStoreError('该媒体类型不能保存到全局资源库')
    base = os.path.splitext(os.path.basename(str(record.get('name') or 'project-media')))[0]
    base = re.sub('[^0-9A-Za-z一-鿿._-]+', '_', base).strip('_') or 'project-media'
    filename = f'up_{uuid.uuid4().hex[:12]}_{base[:60]}{extension}'
    os.makedirs(_svc._main('LOCAL_UPLOAD_DIR'), exist_ok=True)
    destination = Path(_svc._main('LOCAL_UPLOAD_DIR')) / filename
    temporary = Path(_svc._main('LOCAL_UPLOAD_DIR')) / f'.{filename}.{uuid.uuid4().hex}.tmp'
    try:
        with open(record['path'], 'rb') as source_handle, temporary.open('wb') as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return _svc._local_upload_item(filename)

def smart_group_export_folder(folder: str, group_name: str) -> str:
    text = str(folder or '').strip()
    if text:
        path = os.path.abspath(os.path.expanduser(text))
    else:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        safe_group = sanitize_export_filename(group_name or 'group', 'group')
        path = os.path.abspath(os.path.join(_svc._main('OUTPUT_DIR'), 'smart-groups', f'{safe_group}-{stamp}'))
    os.makedirs(path, exist_ok=True)
    return path

async def caption_image_with_provider(abs_path, prompt, provider_id, model, ms_model=''):
    llm_provider = _svc._main('get_api_provider')(provider_id) if provider_id not in ('modelscope',) else {}
    if is_codex_provider(llm_provider):
        resolved_model = selected_model(model, (llm_provider.get('chat_models') or CODEX_DEFAULT_CHAT_MODELS)[0])
        payload = CanvasLLMRequest(message=(prompt or '描述图片').strip() or '描述图片', provider=provider_id or 'codex', model=resolved_model, images=[abs_path])
        text, _raw = await _svc.codex_chat_text(payload, [])
        return (text, resolved_model)
    if is_gemini_cli_provider(llm_provider):
        resolved_model = selected_model(model, (llm_provider.get('chat_models') or GEMINI_CLI_DEFAULT_CHAT_MODELS)[0])
        payload = CanvasLLMRequest(message=(prompt or '描述图片').strip() or '描述图片', provider=provider_id or 'gemini-cli', model=resolved_model, images=[abs_path])
        text, _raw = await _svc.gemini_cli_chat_text(payload, [])
        return (text, resolved_model)
    chat_base, chat_hdrs, resolved_model = _svc._main('resolve_chat_provider')(provider_id, model, ms_model, need_vision=True)
    is_apimart = is_apimart_provider(llm_provider)
    prompt_text = (prompt or '描述图片').strip() or '描述图片'
    data_url = image_path_to_data_url(abs_path, max_size=1024)
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': prompt_text}, {'type': 'image_url', 'image_url': {'url': data_url}}]}]
    raw = None
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {'model': resolved_model, 'messages': messages}
            if is_apimart:
                req_body['stream'] = False
            response = await client.post(f'{chat_base}/chat/completions', headers=chat_hdrs, json=req_body)
            response.raise_for_status()
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ''
        friendly = friendly_chat_error_detail(body, resolved_model, llm_provider)
        raise HTTPException(status_code=exc.response.status_code, detail=friendly or f'上游接口错误：{body}') from exc
    except httpx.HTTPError as exc:
        log_net_error(f'对话 网络/TLS错误 provider={llm_provider} model={resolved_model}', exc)
        raise HTTPException(status_code=502, detail=f'请求上游接口失败：{exc}') from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'解析上游响应失败：{exc}') from exc
    text = text_from_chat_response(raw).strip() if isinstance(raw, dict) else ''
    return (text or '接口返回了空回复。', resolved_model)
