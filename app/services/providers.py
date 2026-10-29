# providers domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
from app.core import _storage_abs_path  # noqa: E402,F401
from . import secret_store, write_guard  # noqa: E402
import logging
logger = logging.getLogger(__name__)

def read_api_env_value(key: str) -> str:
    key = str(key or '').strip()
    if not key or not os.path.exists(_svc._main('API_ENV_FILE')):
        return ''
    try:
        with open(_svc._main('API_ENV_FILE'), 'r', encoding='utf-8-sig') as f:
            for raw_line in f.read().splitlines():
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                env_key, value = line.split('=', 1)
                if env_key.strip() == key:
                    return secret_store.decrypt_env_value(value.strip().strip('"').strip("'"))
    except Exception:
        return ''
    return ''

def provider_env_key_value(provider_id: str) -> str:
    provider_id = str(provider_id or '').strip().lower()
    env_key = provider_key_env(provider_id)
    key = os.getenv(env_key, '') or read_api_env_value(env_key)
    if key.startswith('dpapi:'):
        key = secret_store.decrypt_env_value(key)
    if key and not key.startswith('dpapi:'):
        return key
    return read_api_env_value(env_key)
    if provider_id == 'modelscope':
        return MODELSCOPE_API_KEY or ''
    return ''

def runninghub_wallet_key_value() -> str:
    env_key = runninghub_wallet_key_env()
    return os.getenv(env_key, '') or read_api_env_value(env_key)

def volcengine_access_key_value() -> str:
    env_key = volcengine_access_key_env()
    return os.getenv(env_key, '') or read_api_env_value(env_key)

def volcengine_secret_key_value() -> str:
    env_key = volcengine_secret_key_env()
    return os.getenv(env_key, '') or read_api_env_value(env_key)

def volcengine_provider_api_key(explicit_key: str='') -> str:
    explicit_key = str(explicit_key or '').strip()
    if explicit_key:
        return explicit_key
    return _svc._main('provider_env_key_value')('volcengine')

def public_provider(provider):
    provider = dict(provider or {})
    key = _svc._main('provider_env_key_value')(provider['id'])
    item = {**provider, 'has_key': bool(key), 'key_preview': mask_secret(key), 'key_env': provider_key_env(provider['id'])}
    if provider.get('id') == 'runninghub':
        wallet_key = _svc._main('runninghub_wallet_key_value')()
        item.update({'has_wallet_key': bool(wallet_key), 'wallet_key_preview': mask_secret(wallet_key), 'wallet_key_env': runninghub_wallet_key_env()})
    if provider.get('id') == 'volcengine':
        ak = volcengine_access_key_value()
        sk = volcengine_secret_key_value()
        item.update({'has_volcengine_access_key': bool(ak), 'volcengine_access_key_preview': mask_secret(ak), 'volcengine_access_key_env': volcengine_access_key_env(), 'has_volcengine_secret_key': bool(sk), 'volcengine_secret_key_preview': mask_secret(sk), 'volcengine_secret_key_env': volcengine_secret_key_env(), 'volcengine_project_name': provider.get('volcengine_project_name') or VOLCENGINE_DEFAULT_PROJECT_NAME, 'volcengine_region': provider.get('volcengine_region') or VOLCENGINE_DEFAULT_REGION})
    return item

def modelscope_provider_config():
    return _svc._main('get_api_provider_exact')('modelscope')

def modelscope_api_key(explicit_key: str=''):
    return strip_auth_scheme(explicit_key, 'Bearer') or strip_auth_scheme(_svc._main('provider_env_key_value')('modelscope'), 'Bearer') or strip_auth_scheme(MODELSCOPE_API_KEY, 'Bearer')

def modelscope_api_root(provider=None):
    provider = provider or modelscope_provider_config()
    base_root = str((provider or {}).get('base_url') or MODELSCOPE_CHAT_BASE_URL).strip().rstrip('/')
    if not base_root:
        base_root = MODELSCOPE_CHAT_BASE_URL
    return base_root if base_root.endswith('/v1') else f'{base_root}/v1'

def _update_env_values_unlocked(updates):
    env_file = _svc._main('API_ENV_FILE')
    write_guard.ensure_writable_config(env_file)
    os.makedirs(os.path.dirname(env_file), exist_ok=True)
    lines = []
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
    seen = set()
    next_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            next_lines.append(line)
            continue
        key = line.split('=', 1)[0].strip()
        if key in updates:
            next_lines.append(f'{key}={env_quote(secret_store.encrypt_env_value(updates[key]))}')
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f'{key}={env_quote(secret_store.encrypt_env_value(value))}')
    content = '\n'.join(next_lines).rstrip() + '\n'
    temporary = f"{env_file}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, env_file)
        for key, value in updates.items():
            os.environ[key] = str(value or '')
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass

def sync_static_html_versions():
    return None

def codex_env_value(key):
    return os.getenv(key, '') or read_api_env_value(key)

def codex_cli_executable():
    configured = str(codex_env_value('CODEX_BIN') or '').strip()
    if configured:
        return configured
    return shutil.which('codex') or shutil.which('codex.exe') or shutil.which('codex.cmd') or ''

def gpt_image_2_skill_executable():
    configured = str(codex_env_value('GPT_IMAGE_2_SKILL_BIN') or '').strip()
    if configured:
        return configured
    return shutil.which('gpt-image-2-skill') or shutil.which('gpt-image-2-skill.exe') or shutil.which('gpt-image-2-skill.cmd') or ''

def gpt_image_2_skill_auth_file():
    configured = str(codex_env_value('GPT_IMAGE_2_SKILL_AUTH_FILE') or codex_env_value('CODEX_AUTH_FILE') or '').strip()
    if configured:
        return configured
    project_auth = os.path.join(BASE_DIR, 'API', 'openai-gpt-account-auth.json')
    user_profile = os.getenv('USERPROFILE', '').strip()
    candidates = [project_auth, os.path.join(user_profile, '.codex', 'auth.json') if user_profile else '', os.path.join(os.path.expanduser('~'), '.codex', 'auth.json')]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[0] if candidates and candidates[0] else ''

def gpt_image_2_skill_api_key(auth_data=None):
    for key in ('GPT_IMAGE_2_SKILL_API_KEY', 'OPENAI_API_KEY'):
        value = str(codex_env_value(key) or '').strip()
        if value:
            return value
    if isinstance(auth_data, dict):
        value = str(auth_data.get('OPENAI_API_KEY') or auth_data.get('api_key') or auth_data.get('apiKey') or '').strip()
        if value:
            return value
    user_profile = os.getenv('USERPROFILE', '').strip()
    user_auth = os.path.join(user_profile, '.codex', 'auth.json') if user_profile else ''
    if user_auth:
        user_data = gpt_image_2_skill_auth_json(user_auth)
        value = str(user_data.get('OPENAI_API_KEY') or user_data.get('api_key') or user_data.get('apiKey') or '').strip()
        if value:
            return value
    return ''

def gpt_image_2_skill_provider_args(auth_file=''):
    auth_data = gpt_image_2_skill_auth_json(auth_file)
    if gpt_image_2_skill_access_token(auth_data):
        return (['--provider', 'codex', '--auth-file', auth_file] if auth_file else ['--provider', 'codex'], 'codex')
    api_key = gpt_image_2_skill_api_key(auth_data)
    if api_key:
        return (['--provider', 'openai', '--api-key', api_key], 'openai')
    return (['--provider', 'codex', '--auth-file', auth_file] if auth_file else ['--provider', 'codex'], 'codex')

def gemini_cli_env_value(key):
    return os.getenv(key, '') or read_api_env_value(key)

def gemini_cli_executable():
    for key in ('ANTIGRAVITY_BIN', 'AGY_BIN', 'GEMINI_BIN'):
        configured = str(gemini_cli_env_value(key) or '').strip().strip('"')
        if configured:
            return configured
    for name in ('agy', 'agy.exe'):
        found = shutil.which(name)
        if found:
            return found
    for candidate in antigravity_cli_winget_candidates():
        return candidate
    return shutil.which('gemini') or shutil.which('gemini.exe') or shutil.which('gemini.cmd') or ''

def gemini_cli_display_name(exe=None):
    return 'Antigravity CLI' if is_antigravity_cli(exe or _svc._main('gemini_cli_executable')()) else 'Gemini CLI'

def volcengine_media_reference_url(value, max_image_size=1536):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value:
        return ''
    if is_private_asset_url(value):
        return value
    if value.startswith('/api/project-library/'):
        value = project_media_adapter_reference_from_url(value)
        if not value:
            return ''
    if value.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://'):
        path = _svc._main('local_media_adapter_path')(value)
        if path and content_type_for_path(path).startswith('image/'):
            return _svc._main('reference_to_data_url')({'url': value}, max_size=max_image_size)
        return value
    if value.startswith('/output/') or value.startswith('/assets/'):
        return _svc._main('reference_to_data_url')({'url': value}, max_size=max_image_size)
    return value

async def volcengine_video_reference_content_items(value, max_frames=4, max_size=768):
    text = str(value or '').strip()
    if not text:
        return []
    if is_private_asset_url(text):
        return [{'type': 'video_url', 'video_url': {'url': text}, 'role': 'reference_video'}]
    frame_urls = await _svc._main('video_reference_to_frame_data_urls')(text, max_frames=max_frames, max_size=max_size)
    return [{'type': 'image_url', 'image_url': {'url': frame_url}, 'role': 'reference_image'} for frame_url in frame_urls if frame_url]

def modelscope_image_url(value, max_size=1536):
    if not value:
        return value
    if isinstance(value, str) and value.startswith(('/output/', '/assets/', '/api/project-library/')):
        return _svc._main('reference_to_data_url')({'url': value}, max_size=max_size)
    return value

async def volcengine_ark_asset_call(client, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """调用一次火山 Ark Assets OpenAPI，返回 Result 内容；出错抛 HTTPException。"""
    ak = volcengine_access_key_value()
    sk = volcengine_secret_key_value()
    if not ak or not sk:
        raise HTTPException(status_code=400, detail='未配置火山引擎 AK/SK，请在 API 设置中填写 Access Key ID / Secret Access Key。')
    body_str = json.dumps(body, ensure_ascii=False)
    headers = volcengine_sign_v4_headers(ak, sk, action, body_str)
    url = f"https://{VOLCENGINE_ARK_ASSET_HOST}/?Action={urllib.parse.quote(action, safe='')}&Version={urllib.parse.quote(VOLCENGINE_ARK_ASSET_VERSION, safe='')}"
    resp = await client.post(url, headers=headers, content=body_str.encode('utf-8'), timeout=120)
    try:
        payload = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f'火山 {action} 返回非 JSON（{resp.status_code}）：{resp.text[:300]}')
    meta = payload.get('ResponseMetadata') if isinstance(payload, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get('Error'), dict):
        err = meta['Error']
        code = err.get('Code') or err.get('CodeN') or ''
        msg = err.get('Message') or ''
        raise HTTPException(status_code=502, detail=f'火山 {action} 失败：{code} {msg}'.strip())
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f'火山 {action} 失败（{resp.status_code}）：{resp.text[:300]}')
    result = payload.get('Result') if isinstance(payload, dict) and isinstance(payload.get('Result'), dict) else None
    return result if result is not None else payload if isinstance(payload, dict) else {}

async def volcengine_ensure_asset_group(client, project_name: str, group_name: str) -> str:
    """复用同名素材组合，没有则新建。返回 GroupId。"""
    name = (group_name or '可信素材').strip()[:60] or '可信素材'
    project_name = (project_name or 'default').strip() or 'default'
    try:
        listed = await volcengine_ark_asset_call(client, 'ListAssetGroups', {'Filter': {'Name': name, 'GroupType': 'AIGC'}, 'PageNumber': 1, 'PageSize': 10, 'ProjectName': project_name})
        for item in listed.get('Items') or []:
            if str(item.get('Name') or '').strip() == name and str(item.get('ProjectName') or 'default') == project_name:
                gid = str(item.get('Id') or '').strip()
                if gid:
                    return gid
    except HTTPException:
        pass
    created = await volcengine_ark_asset_call(client, 'CreateAssetGroup', {'Name': name, 'Description': name, 'ProjectName': project_name})
    gid = str(created.get('Id') or '').strip()
    if not gid:
        raise HTTPException(status_code=502, detail=f'火山 CreateAssetGroup 未返回 GroupId：{str(created)[:200]}')
    return gid

def volcengine_public_asset_url(url: str) -> str:
    """火山 CreateAsset 要求 URL 公网可访问；本地文件需 PUBLIC_BASE_URL，否则返回 ERR:。"""
    text = str(url or '').strip()
    if text.startswith('http://') or text.startswith('https://'):
        return text
    public = _svc._main('local_asset_public_url')(text)
    if public:
        return public
    return 'ERR:火山要求素材是公网可访问的 http/https URL；本地项目文件需配置 PUBLIC_BASE_URL/PUBLIC_MEDIA_BASE_URL 暴露为公网地址。'

def volcengine_image_payload(ref):
    value = _svc._main('reference_to_data_url')(ref, max_size=1536)
    if not value:
        return None
    return value

def runninghub_api_headers(provider, use_wallet=True):
    api_key = runninghub_api_key(provider, use_wallet=use_wallet)
    if not api_key:
        raise HTTPException(status_code=400, detail='未配置 RunningHub API Key，请在 API 设置中填写。')
    return {'Authorization': bearer_auth_value(api_key), 'Accept': 'application/json', 'Content-Type': 'application/json'}

def runninghub_json_headers(provider, use_wallet=True):
    return runninghub_api_headers(provider, use_wallet=use_wallet)

def runninghub_provider():
    return _svc._main('get_api_provider_exact')('runninghub')

def runninghub_api_key(provider=None, use_wallet=False, prefer_wallet=False):
    provider = provider or runninghub_provider()
    provider_id = (provider or {}).get('id') or 'runninghub'
    free_key = str((provider or {}).get('api_key') or '').strip() or _svc._main('provider_env_key_value')(provider_id)
    wallet_key = str((provider or {}).get('wallet_api_key') or '').strip() or _svc._main('runninghub_wallet_key_value')()
    if use_wallet and (not wallet_key):
        raise HTTPException(status_code=400, detail='未配置 RunningHub 账户余额 API Key。标准模型接口只能走账户余额，请在 RH 设置中填写账户余额 Key。')
    api_key = wallet_key if (use_wallet or prefer_wallet) and wallet_key else free_key
    if not api_key:
        raise HTTPException(status_code=400, detail='未配置 RunningHub API Key，请在 RH 设置中填写。')
    return api_key

def runninghub_app_headers(json_body=True, use_wallet=False, provider=None):
    headers = {'Host': 'www.runninghub.cn'}
    provider = provider or runninghub_provider()
    if provider:
        api_key = runninghub_api_key(provider, use_wallet=use_wallet)
        if api_key:
            headers['Authorization'] = bearer_auth_value(api_key)
    if json_body:
        headers['Content-Type'] = 'application/json'
    return headers

async def fetch_runninghub_llm_models(provider=None):
    headers = runninghub_api_headers(provider)
    errors = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for url in RUNNINGHUB_LLM_MODELS_URLS:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400 or looks_like_html_response(resp.text):
                    errors.append(f'{url}: HTTP {resp.status_code} {resp.text[:180]}')
                    continue
                raw = resp.json() if resp.text else {}
                grouped, ids = parse_upstream_models(raw, 'openai')
                if ids:
                    return ([runninghub_registry_model_from_id(mid, 'chat') for mid in ids], {'source': url, 'count': len(ids)})
                errors.append(f'{url}: empty')
            except Exception as exc:
                errors.append(f'{url}: {str(exc)[:180]}')
    return ([], {'source': '', 'count': 0, 'errors': errors[-3:]})

async def runninghub_upload_reference(client, provider, ref):
    path = _svc._main('local_media_adapter_path')(ref.get('url', ''))
    if not path:
        value = ref.get('url', '')
        return value if str(value).startswith(('http://', 'https://')) else ''
    upload_url = runninghub_openapi_url(provider, 'media/upload/binary')
    headers = {'Authorization': bearer_auth_value(runninghub_api_key(provider, use_wallet=True)), 'Accept': 'application/json'}
    with open(path, 'rb') as fh:
        files = {'file': (os.path.basename(path), fh, content_type_for_path(path))}
        response = await client.post(upload_url, headers=headers, files=files, timeout=120)
    response.raise_for_status()
    raw = response.json()
    data = raw.get('data') if isinstance(raw, dict) else None
    candidates = [raw, data] if isinstance(data, dict) else [raw]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get('download_url') or item.get('downloadUrl') or item.get('url') or item.get('fileUrl') or item.get('file_url')
        if value:
            return str(value)
    raise HTTPException(status_code=502, detail=f'RunningHub 上传图片未返回 download_url：{raw}')

def api_key_from_payload(payload, protocol: str=''):
    explicit = str(getattr(payload, 'api_key', '') or '').strip()
    provider_id = str(getattr(payload, 'provider_id', '') or '').strip().lower()
    protocol = str(protocol or protocol_from_payload(payload) or '').strip().lower()
    if explicit:
        return explicit
    if provider_id:
        if provider_id == 'runninghub':
            value = os.getenv(runninghub_wallet_key_env(), '')
            if value:
                return value
        value = _svc._main('provider_env_key_value')(provider_id)
        if value:
            return value
    if protocol == 'volcengine':
        return volcengine_provider_api_key('')
    return ''

async def fetch_models_from_upstream(base_url: str, api_key: str, protocol: str='openai', image_request_mode: str='openai'):
    """从上游模型列表端点拉取模型，并按名称做轻量分类。"""
    protocol = protocol if protocol in SUPPORTED_PROVIDER_PROTOCOLS else 'openai'
    if protocol == 'codex':
        status = await _svc._main('codex_status')()
        payload = codex_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'gemini-cli':
        status = await _svc._main('gemini_cli_status')()
        payload = gemini_cli_models_payload(raw={'status': status})
        payload['message'] = status.get('message') or payload['message']
        return payload
    if protocol == 'runninghub':
        provider = {'id': 'runninghub', 'name': 'RunningHub', 'base_url': base_url or RUNNINGHUB_DEFAULT_BASE_URL, 'protocol': 'runninghub', 'api_key': api_key}
        return await _svc._main('runninghub_models_payload')(provider)
    base_url = (base_url or '').strip().rstrip('/')
    if not base_url:
        raise HTTPException(status_code=400, detail='请先填写请求地址')
    if not re.match('^https?://', base_url):
        raise HTTPException(status_code=400, detail='请求地址必须以 http:// 或 https:// 开头')
    api_key = volcengine_provider_api_key(api_key) if protocol == 'volcengine' else (api_key or '').strip()
    if not api_key:
        key_name = '方舟 API Key' if protocol == 'volcengine' else 'API Key'
        raise HTTPException(status_code=400, detail=f'请先填写或保存 {key_name}')
    url = upstream_models_url(base_url, protocol)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=upstream_model_headers(api_key, protocol))
            endpoint_label = '/v1beta/models' if protocol == 'gemini' else '/api/v3/models' if protocol == 'volcengine' else '/openapi/v2/models' if protocol == 'runninghub' else '/v1/models'
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location') or resp.headers.get('location') or ''
                suffix = f'：{location}' if location else ''
                raise HTTPException(status_code=400, detail=f'上游 {endpoint_label} 发生跳转{suffix}，请填写 API Base URL，不要填写网页登录地址')
            if looks_like_html_response(resp.text):
                raise HTTPException(status_code=400, detail=f'上游 {endpoint_label} 返回网页 HTML，请检查请求地址是否为 API Base URL')
            if resp.status_code >= 400:
                if protocol == 'volcengine':
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(status=probe.get('status') or resp.status_code, message=f"{probe.get('message') or '方舟任务接口可达'}；但 /api/v3/models 不可用。请按实际方舟控制台模型名称手动填写视频模型。", raw={'models_error': resp.text[:300], **(probe.get('raw') or {})})
                        return {'total': payload['model_count'], 'protocol': payload['protocol'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
                elif protocol == 'openai':
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(status=probe.get('status') or resp.status_code, message=f"{probe.get('message') or '检测到方舟/Ark 兼容入口'}；OpenAI /v1/models 不可用，已自动切换为方舟协议。请按实际方舟控制台模型名称手动填写视频模型。", raw={'models_error': resp.text[:300], **(probe.get('raw') or {})})
                        return {'total': payload['model_count'], 'protocol': payload['protocol'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
                raise HTTPException(status_code=resp.status_code, detail=f'上游 {endpoint_label} 失败：{resp.text[:300]}')
            raw = resp.json()
    except httpx.HTTPError as e:
        if protocol == 'volcengine':
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    detected, probe = await probe_volcengine_auto_detect(client, base_url, api_key)
                    if detected:
                        payload = volcengine_default_model_payload(status=probe.get('status') or 0, message=f"{probe.get('message') or '方舟任务接口可达'}；但模型列表请求失败。请按实际方舟控制台模型名称手动填写视频模型。", raw={'models_error': str(e)[:300], **(probe.get('raw') or {})})
                        return {'total': payload['model_count'], 'protocol': payload['protocol'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
            except Exception:
                pass
        raise HTTPException(status_code=502, detail=f'请求上游模型列表失败：{e}')
    grouped, ids = parse_upstream_models(raw, protocol)
    grouped, ids = apply_agnes_model_defaults(base_url, grouped, ids)
    grouped = apply_locked_recommended_model_rules(base_url, grouped)
    if protocol == 'volcengine' and (not ids):
        payload = volcengine_default_model_payload(raw=raw)
        return {'total': payload['model_count'], 'image_models': payload['image_models'], 'chat_models': payload['chat_models'], 'video_models': payload['video_models'], 'all': payload['all'], 'message': payload['message'], 'raw': payload['raw']}
    return {'total': len(ids), 'image_models': grouped['image'], 'chat_models': grouped['chat'], 'video_models': grouped['video'], 'recommended_models': recommend_upstream_models(grouped), 'all': ids, 'image_request_mode': detect_image_request_mode(base_url, ids) or normalize_image_request_mode(image_request_mode)}

def save_storage_settings(payload):
    dirs = {}
    for key, fallback in _svc._main('DEFAULT_STORAGE_DIRS').items():
        dirs[key] = _storage_abs_path((payload or {}).get(key), fallback)
    for path in dirs.values():
        os.makedirs(path, exist_ok=True)
    os.makedirs(_svc._main('DATA_DIR'), exist_ok=True)
    with open(_svc._main('STORAGE_SETTINGS_FILE'), 'w', encoding='utf-8') as f:
        json.dump(dirs, f, ensure_ascii=False, indent=2)
    _svc._main('apply_storage_settings')(dirs)
    return {'dirs': dirs}

def provider_endpoint_url(provider, key, default_path):
    base_url = str((provider or {}).get('base_url') or _svc._main('AI_BASE_URL')).strip().rstrip('/')
    override = str((provider or {}).get(key) or '').strip()
    if override:
        if re.match('^https?://', override, re.I):
            return override.rstrip('/')
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme and parsed.netloc:
            return f'{parsed.scheme}://{parsed.netloc}{override}'
        return override
    for prefix in ('/api/v3', '/v1beta', '/v1', '/v2'):
        if base_url.endswith(prefix) and default_path.startswith(f'{prefix}/'):
            return f'{base_url}{default_path[len(prefix):]}'
    return f'{base_url}{default_path}'

def load_api_providers():
    defaults = default_api_providers()
    if not os.path.exists(_svc._main('API_PROVIDERS_FILE')):
        return merge_default_api_providers(defaults)
    try:
        with open(_svc._main('API_PROVIDERS_FILE'), 'r', encoding='utf-8') as f:
            raw = json.load(f)
        providers = [normalize_provider(item) for item in raw if isinstance(item, dict)]
        providers = [_decrypt_provider_secrets(item) for item in providers]
        providers = [item for item in providers if item.get('id') not in REMOVED_PROVIDER_IDS]
        return merge_default_api_providers(providers or defaults, inject_missing=not bool(providers))
    except Exception as e:
        logger.warning(f'加载 API 平台配置失败: {e}')
        return defaults

def _decrypt_provider_secrets(provider):
    provider = dict(provider or {})
    for key in ('api_key', 'wallet_api_key', 'volcengine_secret_access_key'):
        if key in provider and provider.get(key):
            provider[key] = secret_store.decrypt_secret(provider[key])
    return provider

def save_api_providers(providers):
    providers_file = _svc._main('API_PROVIDERS_FILE')
    write_guard.ensure_writable_config(providers_file)
    os.makedirs(_svc._main('DATA_DIR'), exist_ok=True)
    with GLOBAL_CONFIG_LOCK:
        with open(providers_file, 'w', encoding='utf-8') as f:
            json.dump([_encrypt_provider_secrets(item) for item in providers], f, ensure_ascii=False, indent=2)

def _encrypt_provider_secrets(provider):
    provider = dict(provider or {})
    for key in ('api_key', 'wallet_api_key', 'volcengine_secret_access_key'):
        if key in provider and provider.get(key):
            provider[key] = secret_store.encrypt_secret(provider[key])
    return provider

def public_api_providers():
    return [public_provider(p) for p in _svc._main('load_api_providers')()]

def get_primary_provider_id(providers=None):
    """Return the preferred provider id, falling back to the first enabled one."""
    providers = providers if providers is not None else _svc._main('load_api_providers')()
    primary = next((p for p in providers if p.get('primary') and p.get('enabled', True)), None)
    if primary:
        return primary['id']
    enabled = next((p for p in providers if p.get('enabled', True)), None)
    if enabled:
        return enabled['id']
    return providers[0]['id'] if providers else 'volcengine'

def get_api_provider(provider_id='comfly'):
    providers = _svc._main('load_api_providers')()
    target = (provider_id or '').strip().lower()
    if not target or not any((p['id'] == target for p in providers)):
        target = get_primary_provider_id(providers)
    provider = next((p for p in providers if p['id'] == target), None)
    if not provider:
        raise HTTPException(status_code=400, detail=f'未找到 API 平台：{target}')
    if not provider.get('enabled', True):
        raise HTTPException(status_code=400, detail=f"API 平台已禁用：{provider.get('name') or target}")
    return provider

def get_api_provider_exact(provider_id: str):
    providers = _svc._main('load_api_providers')()
    target = (provider_id or '').strip().lower()
    provider = next((p for p in providers if p['id'] == target), None)
    if not provider:
        raise HTTPException(status_code=400, detail=f"未找到 API 平台：{target or '(empty)'}。新增平台未保存时请使用当前表单拉取模型。")
    if not provider.get('enabled', True):
        raise HTTPException(status_code=400, detail=f"API 平台已禁用：{provider.get('name') or target}")
    return provider

def update_env_values(updates):
    with API_ENV_LOCK:
        return _update_env_values_unlocked(updates)

def load_global_preferences():
    defaults = {'theme': 'dark', 'language': 'zh'}
    try:
        with open(_svc._main('GLOBAL_SETTINGS_FILE'), 'r', encoding='utf-8-sig') as handle:
            raw = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return defaults
    theme = raw.get('theme') if isinstance(raw, dict) else None
    language = raw.get('language') if isinstance(raw, dict) else None
    return {'theme': theme if theme in {'light', 'dark'} else defaults['theme'], 'language': language if language in {'zh', 'en'} else defaults['language']}

def save_global_preferences(theme: str, language: str, autosave_interval_minutes: Optional[int]=None):
    preferences = {'theme': theme if theme in {'light', 'dark'} else 'dark', 'language': language if language in {'zh', 'en'} else 'zh'}
    os.makedirs(os.path.dirname(_svc._main('GLOBAL_SETTINGS_FILE')), exist_ok=True)
    temporary = f"{_svc._main('GLOBAL_SETTINGS_FILE')}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(preferences, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _svc._main('GLOBAL_SETTINGS_FILE'))
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
    return preferences

def api_headers(json_body=True, provider=None, model=''):
    if provider:
        if is_codex_provider(provider) or is_gemini_cli_provider(provider):
            raise HTTPException(status_code=400, detail='CLI 协议使用本机登录态，不需要 API Key。当前入口应走对应 CLI 专用通道。')
        api_key = _svc._main('provider_env_key_value')(provider['id'])
        provider_name = provider.get('name') or provider['id']
        if not api_key:
            raise HTTPException(status_code=400, detail=f'未配置 {provider_name} 的 API Key，请在 API 平台管理中填写。')
    else:
        api_key = AI_API_KEY
        if not api_key:
            raise HTTPException(status_code=400, detail='未配置 COMFLY_API_KEY，请在 API/.env 中填写。')
    if provider and effective_protocol(provider, model) == 'gemini':
        headers = {'Accept': 'application/json', 'x-goog-api-key': api_key}
    else:
        headers = {'Accept': 'application/json', 'Authorization': bearer_auth_value(api_key)}
    if json_body:
        headers['Content-Type'] = 'application/json'
    return headers

def public_base_url() -> str:
    value = (read_api_env_value('PUBLIC_MEDIA_BASE_URL') or os.getenv('PUBLIC_MEDIA_BASE_URL') or PUBLIC_MEDIA_BASE_URL or read_api_env_value('PUBLIC_BASE_URL') or os.getenv('PUBLIC_BASE_URL') or PUBLIC_BASE_URL or '').strip().rstrip('/')
    if value and re.match('^https?://', value, re.I):
        return value
    return ''

def gemini_endpoint_url(provider, model):
    model_name = urllib.parse.quote(gemini_model_name(model), safe='')
    return provider_endpoint_url(provider, 'image_generation_endpoint', f'/v1beta/models/{model_name}:generateContent')

def volcengine_endpoint_url(provider):
    return provider_endpoint_url(provider, 'image_generation_endpoint', '/api/v3/images/generations')

def runninghub_local_asset_path(url):
    text = str(url or '').strip()
    if not text:
        return None
    if text.startswith('/assets/input/') or text.startswith('/input/'):
        clean = urllib.parse.unquote(text.split('?', 1)[0]).replace('\\', '/')
        rel = clean[len('/assets/input/'):] if clean.startswith('/assets/input/') else clean[len('/input/'):]
        root = _svc._main('OUTPUT_INPUT_DIR')
    elif text.startswith('/assets/output/'):
        clean = urllib.parse.unquote(text.split('?', 1)[0]).replace('\\', '/')
        rel = clean[len('/assets/output/'):]
        root = _svc._main('OUTPUT_OUTPUT_DIR')
    elif text.startswith(('/output/', '/assets/', '/api/project-library/', f'{PROJECT_MEDIA_ADAPTER_SCHEME}://')):
        if text.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://'):
            return _svc.local_media_adapter_path(text)
        return _svc._main('output_file_from_url')(text)
    else:
        return None
    rel = rel.lstrip('/')
    if not rel:
        return None
    path = os.path.abspath(os.path.join(root, rel))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, path]) != root_abs or not os.path.exists(path):
        return None
    return path

async def runninghub_store_remote_output(client, remote):
    remote = rewrite_runninghub_file_url(remote)
    if not str(remote or '').startswith(('http://', 'https://')):
        return remote
    response = await client.get(remote, follow_redirects=True)
    if not response.is_success:
        return remote
    ext = runninghub_output_ext(remote, response.headers.get('content-type', ''))
    filename = f'rh_{uuid.uuid4().hex[:12]}.{ext}'
    path = _svc.output_path_for(filename, 'output')
    with open(path, 'wb') as f:
        f.write(response.content)
    return _svc.output_url_for(filename, 'output')

async def fetch_runninghub_model_registry(provider=None, include_fallback=True, include_meta=False):
    urls = [('openapi', runninghub_openapi_url(provider, 'models')), ('github', RUNNINGHUB_MODEL_REGISTRY_URL)]
    if os.path.exists(STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE):
        urls.append(('local', STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE))
    headers = runninghub_api_headers(provider)
    errors = []
    source = ''
    items = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for source_name, url in urls:
            try:
                if source_name == 'local':
                    with open(url, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                else:
                    req_headers = headers if source_name == 'openapi' else {'Accept': 'application/json'}
                    resp = await client.get(url, headers=req_headers)
                    if resp.status_code >= 400 or looks_like_html_response(resp.text):
                        errors.append(f'{source_name}: HTTP {resp.status_code} {resp.text[:180]}')
                        continue
                    raw = resp.json() if resp.text else []
                parsed = runninghub_registry_items_from_raw(raw)
                if parsed:
                    items = parsed
                    source = source_name
                    break
                errors.append(f'{source_name}: empty')
            except Exception as exc:
                errors.append(f'{source_name}: {str(exc)[:180]}')
                continue
    llm_items, llm_meta = await fetch_runninghub_llm_models(provider)
    combined = [*items]
    seen = {runninghub_model_id(item) for item in combined if runninghub_model_id(item)}
    for item in llm_items:
        mid = runninghub_model_id(item)
        if mid and mid not in seen:
            combined.append(item)
            seen.add(mid)
    if combined:
        meta = {'source': source or 'llm', 'openapi_count': len(items), 'llm_count': len(llm_items), 'llm_source': llm_meta.get('source') or '', 'errors': [*errors[-3:], *(llm_meta.get('errors') or [])[-3:]]}
        return (combined, meta) if include_meta else combined
    if include_fallback:
        fallback = runninghub_registry_fallback()
        meta = {'source': 'fallback', 'openapi_count': 0, 'llm_count': 0, 'llm_source': '', 'errors': [*errors[-3:], *(llm_meta.get('errors') or [])[-3:]]}
        return (fallback, meta) if include_meta else fallback
    raise HTTPException(status_code=502, detail=f"拉取 RunningHub 模型注册表失败：{'; '.join(errors[-4:]) or 'unknown error'}")

async def runninghub_models_payload(provider=None):
    registry, meta = await _svc._main('fetch_runninghub_model_registry')(provider, include_fallback=True, include_meta=True)
    payload = runninghub_registry_payload(registry)
    payload['raw'] = {'registry_count': len(registry), **meta}
    if meta.get('source') == 'fallback':
        payload['message'] = 'RunningHub 模型接口未返回完整列表，当前显示内置兜底模型。'
    else:
        payload['message'] = f"RunningHub 模型列表来自 {meta.get('source')}"
    return payload

async def runninghub_model_definition(provider, model):
    requested = str(model or '').strip().strip('/')
    configured = runninghub_configured_video_model_definition(provider, requested)
    if configured is not None:
        return configured
    registry = provider.get('_runninghub_model_registry')
    if not isinstance(registry, list):
        registry = await _svc._main('fetch_runninghub_model_registry')(provider, include_fallback=True)
        if isinstance(provider, dict):
            provider['_runninghub_model_registry'] = copy.deepcopy(registry)
    for item in registry:
        mid = runninghub_model_id(item)
        endpoint = str(item.get('endpoint') or '').strip().strip('/')
        if requested and requested in {mid, endpoint, f'/openapi/v2/{endpoint}', f'openapi/v2/{endpoint}'}:
            if endpoint:
                return item
            alias = runninghub_endpoint_alias_for_model(mid or requested)
            if alias:
                patched = dict(item)
                patched['endpoint'] = alias
                return patched
            return item
    endpoint = requested
    if endpoint.startswith('/openapi/v2/'):
        endpoint = endpoint[len('/openapi/v2/'):]
    elif endpoint.startswith('openapi/v2/'):
        endpoint = endpoint[len('openapi/v2/'):]
    endpoint = runninghub_endpoint_alias_for_model(requested) or endpoint
    return {'name_en': requested, 'endpoint': endpoint or RUNNINGHUB_DEFAULT_IMAGE_MODELS[0], 'output_type': classify_upstream_model(requested), 'params': []}

async def runninghub_upload_local_to_filename(client, provider, url, use_wallet=False):
    """把本地/远程素材上传到 RunningHub /task/openapi/upload，返回 fileName（供 nodeInfoList 使用）。"""
    text = str(url or '').strip()
    if not text:
        return ''
    path = runninghub_local_asset_path(text)
    if path:
        filename = os.path.basename(path)
        content_type = content_type_for_path(path)
        with open(path, 'rb') as fh:
            content = fh.read()
    elif text.startswith(('http://', 'https://')):
        response = await client.get(text, follow_redirects=True)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get('content-type') or 'application/octet-stream'
        filename = os.path.basename(urllib.parse.urlsplit(text).path) or 'asset.bin'
    else:
        return ''
    if not content:
        return ''
    api_key = runninghub_api_key(provider, use_wallet=use_wallet)
    upload_url = runninghub_endpoint_url(provider, '/task/openapi/upload')
    files = {'file': (filename, content, content_type)}
    data = {'apiKey': api_key, 'fileType': 'input'}
    response = await client.post(upload_url, headers=runninghub_app_headers(False, use_wallet), data=data, files=files)
    raw = response.json()
    if isinstance(raw, dict) and raw.get('code') in (0, '0') and isinstance(raw.get('data'), dict) and raw['data'].get('fileName'):
        return raw['data']['fileName']
    raise HTTPException(status_code=502, detail=(raw.get('msg') if isinstance(raw, dict) else '') or f'RunningHub 上传素材失败：{raw}')
