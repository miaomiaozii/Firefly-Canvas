"""Router: /api/asset-library domain (auto-split from main.py)."""
from __future__ import annotations
import sys as _sys
import os as _os
_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']
import asyncio
import os
import json
import re
import time
import base64
import shutil
import uuid
import hashlib
import mimetypes
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from app.core import *
from app.core import _volc_hmac
_from_main_names = ['ASSET_LIBRARY_DIR', 'api_headers', 'check_apimart_avatar_task', 'check_volcengine_avatar_task', 'classify_asset_image_best_effort', 'classify_image_with_provider', 'find_asset_category_in_library', 'find_asset_category_with_library', 'find_asset_library', 'get_api_provider', 'load_asset_library', 'local_asset_public_url', 'make_asset_library_item', 'normalize_asset_library', 'now_ms', 'output_file_from_url', 'remove_asset_library_file', 'save_asset_library', 'submit_apimart_avatar_asset', 'submit_volcengine_avatar_asset', 'unique_asset_category_dir', 'upload_audio_for_apimart', 'upload_image_for_apimart', 'upload_media_for_apimart', 'upload_video_for_apimart', 'video_api_root', 'volcengine_access_key_value', 'volcengine_ark_asset_call', 'volcengine_ensure_asset_group', 'volcengine_public_asset_url', 'volcengine_secret_key_value']
for _nm in _from_main_names:
    globals()[_nm] = getattr(_app_main, _nm)
router = APIRouter()

class AssetLibraryCategoryRequest(BaseModel):
    name: str = '新文件夹'
    type: str = 'image'
    library_id: str = ''

class AssetLibraryRequest(BaseModel):
    name: str = '资产库'

class AssetLibraryAddRequest(BaseModel):
    category_id: str = ''
    url: str = ''
    name: str = ''
    library_id: str = ''

class AssetLibraryBatchAddRequest(BaseModel):
    category_id: str = ''
    library_id: str = ''
    items: List[AssetLibraryAddRequest] = []

class AssetLibraryRenameRequest(BaseModel):
    name: str = ''
    library_id: str = ''

class AssetLibraryBatchDeleteRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ''

class AssetLibraryBatchMoveRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ''
    target_library_id: str = ''
    target_category_id: str = ''

class AssetLibraryBatchCropRequest(BaseModel):
    ids: List[str] = []
    library_id: str = ''
    target_library_id: str = ''
    target_category_id: str = ''
    mode: str = 'square'

class AssetAvatarRegisterRequest(BaseModel):
    library_id: str = ''
    provider_id: str = ''
    project_name: str = 'default'
    group_name: str = ''

class AssetLibraryClassifyRequest(BaseModel):
    library_id: str = ''
    ids: List[str] = []
    provider: str = 'comfly'
    model: str = ''
    ms_model: str = ''
    prompt: str = ''

def avatar_platform_for_provider(provider) -> str:
    if not provider:
        return ''
    if is_apimart_provider(provider):
        return 'apimart'
    if is_volcengine_provider(provider):
        return 'volcengine'
    return ''

def remove_asset_library_file(item) -> None:
    """删除资产对应的本地文件（仅限 library 副本，删了不影响 /output 原图）。日志不影响主流程。"""
    try:
        url = item.get('url') if isinstance(item, dict) else ''
        path = _app_main.output_file_from_url(url)
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception as exc:
        print(f'删除资产文件失败: {exc}')

def find_asset_category_with_library(lib, category_id, library_id=''):
    lib = normalize_asset_library(lib)
    preferred = str(library_id or '').strip()
    libraries = lib.get('libraries', []) or []
    if preferred:
        libraries = [item for item in libraries if item.get('id') == preferred]
    for library in libraries:
        for cat in library.get('categories', []) or []:
            if cat.get('id') == category_id:
                return (library, cat)
    return (None, None)

async def upload_media_for_apimart(client, provider, ref_url: str, kind: str) -> str:
    """按 kind 分派到对应的 APIMart 上传器，拿回上游可用的 http/https/asset:// URL。"""
    if kind == 'video':
        return await upload_video_for_apimart(client, provider, ref_url)
    if kind == 'audio':
        return await upload_audio_for_apimart(client, provider, ref_url)
    return await upload_image_for_apimart(client, provider, ref_url)

def apimart_avatar_asset_type(kind: str) -> str:
    return {'video': 'Video', 'audio': 'Audio'}.get(str(kind or '').lower(), 'Image')

def extract_apimart_avatar_asset_uri(payload) -> str:
    """从 /v1/tasks 审核结果里取出 asset://<id> 形式的可信素材 URI。"""
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_avatar_asset_uri(item)
            if found:
                return found
        return ''
    if not isinstance(payload, dict):
        return ''
    for key in ('asset_url', 'assetUrl', 'uri', 'url'):
        value = str(payload.get(key) or '').strip()
        if value.startswith('asset://'):
            return value
    for key in ('usable_assets', 'assets', 'result', 'data'):
        found = extract_apimart_avatar_asset_uri(payload.get(key))
        if found:
            return found
    asset_id = str(payload.get('asset_id') or payload.get('assetId') or '').strip()
    if asset_id:
        return f'asset://{asset_id}'
    return ''

async def submit_apimart_avatar_asset(provider, public_url: str, name: str, kind: str, project_name: str='default', group_name: str='') -> str:
    """把一个公网可访问的素材提交到 APIMart private-avatar 审核，立即返回任务 ID（不阻塞轮询）。"""
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    register_url = f'{base_url}/v1/seedance2/private-avatar'
    body = {'project_name': str(project_name or 'default').strip() or 'default', 'asset_type': apimart_avatar_asset_type(kind), 'group': {'name': (group_name or name or '数字人素材')[:60]}, 'assets': [{'url': public_url, 'name': (name or 'asset')[:60]}]}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(register_url, headers=api_headers(provider=provider), json=body, timeout=120)
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
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    task_url = f'{base_url}/v1/tasks/{task_id}'
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(task_url, headers=api_headers(provider=provider), timeout=60)
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

def _volc_hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def volcengine_sign_v4_headers(ak: str, sk: str, action: str, body_str: str, service: str=VOLCENGINE_ARK_ASSET_SERVICE, region: str=VOLCENGINE_ARK_ASSET_REGION, version: str=VOLCENGINE_ARK_ASSET_VERSION, host: str=VOLCENGINE_ARK_ASSET_HOST) -> Dict[str, str]:
    """火山引擎 OpenAPI 签名 V4（POST + JSON body）。返回需随请求发送的鉴权头。"""
    method = 'POST'
    content_type = 'application/json'
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime('%Y%m%dT%H%M%SZ')
    short_date = x_date[:8]
    payload_hash = hashlib.sha256(body_str.encode('utf-8')).hexdigest()
    canonical_query = f"Action={urllib.parse.quote(action, safe='')}&Version={urllib.parse.quote(version, safe='')}"
    canonical_headers = f'content-type:{content_type}\nhost:{host}\nx-content-sha256:{payload_hash}\nx-date:{x_date}\n'
    signed_headers = 'content-type;host;x-content-sha256;x-date'
    canonical_request = '\n'.join([method, '/', canonical_query, canonical_headers, signed_headers, payload_hash])
    algorithm = 'HMAC-SHA256'
    credential_scope = f'{short_date}/{region}/{service}/request'
    string_to_sign = '\n'.join([algorithm, x_date, credential_scope, hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()])
    k_date = _volc_hmac(sk.encode('utf-8'), short_date)
    k_region = _volc_hmac(k_date, region)
    k_service = _volc_hmac(k_region, service)
    k_signing = _volc_hmac(k_service, 'request')
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    authorization = f'{algorithm} Credential={ak}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}'
    return {'Content-Type': content_type, 'Host': host, 'X-Date': x_date, 'X-Content-Sha256': payload_hash, 'Authorization': authorization}

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

async def submit_volcengine_avatar_asset(public_url: str, name: str, kind: str, project_name: str='default', group_name: str='') -> str:
    """把公网可访问素材提交到火山 Ark 私域素材库（异步）。返回 Asset Id 作为任务 ID。"""
    async with httpx.AsyncClient(timeout=120) as client:
        group_id = await volcengine_ensure_asset_group(client, project_name, group_name)
        created = await volcengine_ark_asset_call(client, 'CreateAsset', {'GroupId': group_id, 'URL': public_url, 'AssetType': apimart_avatar_asset_type(kind), 'Name': (name or 'asset')[:60], 'ProjectName': (project_name or 'default').strip() or 'default'})
    asset_id = str(created.get('Id') or '').strip()
    if not asset_id:
        raise HTTPException(status_code=502, detail=f'火山 CreateAsset 未返回 Asset Id：{str(created)[:200]}')
    return asset_id

async def check_volcengine_avatar_task(asset_id: str, project_name: str='default') -> Dict[str, Any]:
    """查询一次火山素材状态。返回 {status: Active/Processing/Failed, asset_uri, detail}。"""
    async with httpx.AsyncClient(timeout=60) as client:
        info = await volcengine_ark_asset_call(client, 'GetAsset', {'Id': asset_id, 'ProjectName': (project_name or 'default').strip() or 'default'})
    status = str(info.get('Status') or '').strip()
    if status == 'Active':
        return {'status': 'Active', 'asset_uri': f'asset://{asset_id}', 'detail': ''}
    if status == 'Failed':
        return {'status': 'Failed', 'asset_uri': '', 'detail': '火山素材处理失败，无法用于推理。'}
    return {'status': 'Processing', 'asset_uri': '', 'detail': '火山素材处理中'}

def volcengine_public_asset_url(url: str) -> str:
    """火山 CreateAsset 要求 URL 公网可访问；本地文件需 PUBLIC_BASE_URL，否则返回 ERR:。"""
    text = str(url or '').strip()
    if text.startswith('http://') or text.startswith('https://'):
        return text
    public = local_asset_public_url(text)
    if public:
        return public
    return 'ERR:火山要求素材是公网可访问的 http/https URL；本地画布文件需配置 PUBLIC_BASE_URL/PUBLIC_MEDIA_BASE_URL 暴露为公网地址。'

@router.get('/api/asset-library')
async def get_asset_library():
    return {'library': load_asset_library()}

@router.post('/api/asset-library/libraries')
async def create_asset_library(payload: AssetLibraryRequest):
    lib = load_asset_library()
    library = {'id': f'lib_{uuid.uuid4().hex[:12]}', 'name': sanitize_asset_name(payload.name, '资产库'), 'type': 'asset', 'categories': []}
    library['categories'].append({'id': f'cat_{uuid.uuid4().hex[:12]}', 'name': '默认分组', 'type': 'image', 'items': []})
    lib.setdefault('libraries', []).append(library)
    lib['active_library_id'] = library['id']
    save_asset_library(lib)
    return {'library': lib, 'asset_library': library}

@router.patch('/api/asset-library/libraries/{library_id}')
async def rename_asset_library(library_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    library = find_asset_library(lib, library_id)
    if not library or library.get('id') != library_id:
        raise HTTPException(status_code=404, detail='资产库不存在')
    library['name'] = sanitize_asset_name(payload.name, library.get('name') or '资产库')
    save_asset_library(lib)
    return {'library': lib, 'asset_library': library}

@router.delete('/api/asset-library/libraries/{library_id}')
async def delete_asset_library(library_id: str):
    lib = load_asset_library()
    libraries = lib.get('libraries') or []
    if len(libraries) <= 1:
        raise HTTPException(status_code=400, detail='至少保留一个资产库')
    if not any((item.get('id') == library_id for item in libraries)):
        raise HTTPException(status_code=404, detail='资产库不存在')
    lib['libraries'] = [item for item in libraries if item.get('id') != library_id]
    if lib.get('active_library_id') == library_id:
        lib['active_library_id'] = lib['libraries'][0].get('id')
    save_asset_library(lib)
    return {'library': lib}

@router.post('/api/asset-library/categories')
async def create_asset_library_category(payload: AssetLibraryCategoryRequest):
    lib = load_asset_library()
    library = find_asset_library(lib, payload.library_id)
    if not library:
        raise HTTPException(status_code=404, detail='资产库不存在')
    category = {'id': f'cat_{uuid.uuid4().hex[:12]}', 'name': sanitize_asset_name(payload.name, '新文件夹'), 'type': 'image', 'items': []}
    category['dir'] = unique_asset_category_dir(library, payload.name)
    try:
        os.makedirs(os.path.join(ASSET_LIBRARY_DIR, category['dir']), exist_ok=True)
    except Exception as exc:
        print(f'创建分组文件夹失败: {exc}')
    library.setdefault('categories', []).append(category)
    lib['active_library_id'] = library.get('id') or lib.get('active_library_id')
    save_asset_library(lib)
    return {'library': lib, 'category': category}

@router.patch('/api/asset-library/categories/{category_id}')
async def rename_asset_library_category(category_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    _, cat = find_asset_category_with_library(lib, category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail='分类不存在')
    cat['name'] = sanitize_asset_name(payload.name, cat.get('name') or '新文件夹')
    save_asset_library(lib)
    return {'library': lib, 'category': cat}

@router.delete('/api/asset-library/categories/{category_id}')
async def delete_asset_library_category(category_id: str, library_id: str=''):
    lib = load_asset_library()
    library, cat = find_asset_category_with_library(lib, category_id, library_id)
    if not cat:
        raise HTTPException(status_code=404, detail='分类不存在')
    for item in cat.get('items') or []:
        remove_asset_library_file(item)
    cat_dir = str(cat.get('dir') or '').strip('/').strip()
    if cat_dir:
        try:
            target = os.path.join(ASSET_LIBRARY_DIR, cat_dir)
            if os.path.isdir(target) and os.path.abspath(target).startswith(os.path.abspath(ASSET_LIBRARY_DIR) + os.sep):
                shutil.rmtree(target, ignore_errors=True)
        except Exception as exc:
            print(f'删除分组文件夹失败: {exc}')
    library['categories'] = [c for c in library.get('categories', []) if c.get('id') != category_id]
    save_asset_library(lib)
    return {'library': lib}

@router.post('/api/asset-library/items')
async def add_asset_library_item(payload: AssetLibraryAddRequest):
    lib = load_asset_library()
    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail='分类不存在')
    if cat.get('type') != 'image':
        raise HTTPException(status_code=400, detail='该分类暂不支持添加媒体')
    src = _app_main.output_file_from_url(payload.url)
    if not src:
        raise HTTPException(status_code=400, detail='只支持保存本地 /assets 或 /output 媒体')
    _, item = make_asset_library_item(src, payload.name or os.path.basename(src), subdir=cat.get('dir') or '')
    if item.get('kind') == 'image':
        classification = await classify_asset_image_best_effort(_app_main.output_file_from_url(item.get('url') or '') or src)
        if classification:
            item['classification'] = classification
    cat.setdefault('items', []).append(item)
    save_asset_library(lib)
    return {'library': lib, 'item': item}

@router.post('/api/asset-library/items/batch')
async def batch_add_asset_library_items(payload: AssetLibraryBatchAddRequest):
    added = []
    lib = load_asset_library()
    cat = find_asset_category_in_library(lib, payload.category_id, payload.library_id)
    if not cat:
        raise HTTPException(status_code=404, detail='分类不存在')
    if cat.get('type') != 'image':
        raise HTTPException(status_code=400, detail='该分类暂不支持添加媒体')
    for entry in (payload.items or [])[:200]:
        entry.category_id = payload.category_id
        entry.library_id = payload.library_id
        src = _app_main.output_file_from_url(entry.url)
        if not src:
            continue
        _, item = make_asset_library_item(src, entry.name or os.path.basename(src), subdir=cat.get('dir') or '')
        if item.get('kind') == 'image':
            classification = await classify_asset_image_best_effort(_app_main.output_file_from_url(item.get('url') or '') or src)
            if classification:
                item['classification'] = classification
        cat.setdefault('items', []).append(item)
        added.append(item)
    save_asset_library(lib)
    return {'library': lib, 'items': added}

@router.patch('/api/asset-library/items/{item_id}')
async def rename_asset_library_item(item_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    for library in lib.get('libraries', []):
        for cat in library.get('categories', []):
            for item in cat.get('items', []):
                if item.get('id') == item_id:
                    item['name'] = sanitize_asset_name(payload.name, item.get('name') or 'asset')
                    save_asset_library(lib)
                    return {'library': lib, 'item': item}
    raise HTTPException(status_code=404, detail='资产不存在')

def find_asset_item_in_library(lib, item_id, library_id=''):
    for library in lib.get('libraries', []):
        if library_id and library.get('id') != library_id:
            continue
        for cat in library.get('categories', []):
            for item in cat.get('items', []):
                if item.get('id') == item_id:
                    return item
    return None

@router.post('/api/asset-library/items/classify')
async def classify_asset_library_items(payload: AssetLibraryClassifyRequest):
    lib = load_asset_library()
    results = []
    changed = False
    for item_id in (payload.ids or [])[:80]:
        item = find_asset_item_in_library(lib, item_id, payload.library_id)
        result = {'id': item_id, 'ok': False, 'classification': None, 'error': ''}
        if not item:
            result['error'] = '资产不存在'
            results.append(result)
            continue
        if asset_library_media_kind(item.get('url') or '') != 'image' and item.get('kind') != 'image':
            result['error'] = '仅支持图片素材智能分类'
            results.append(result)
            continue
        path = _app_main.output_file_from_url(item.get('url') or '')
        if not path or not os.path.isfile(path):
            result['error'] = '文件不存在'
            results.append(result)
            continue
        try:
            classification = await classify_image_with_provider(path, payload.provider, payload.model, payload.ms_model, payload.prompt)
            item['classification'] = classification
            changed = True
            result.update({'ok': True, 'classification': classification})
        except Exception as exc:
            result['error'] = str(getattr(exc, 'detail', '') or exc)
        results.append(result)
    if changed:
        save_asset_library(lib)
    return {'library': lib, 'count': sum((1 for item in results if item.get('ok'))), 'items': results}

@router.post('/api/asset-library/items/{item_id}/register-avatar')
async def register_asset_library_avatar(item_id: str, payload: AssetAvatarRegisterRequest):
    lib = load_asset_library()
    target_item = find_asset_item_in_library(lib, item_id, payload.library_id)
    if not target_item:
        raise HTTPException(status_code=404, detail='资产不存在')
    provider = _app_main.get_api_provider(payload.provider_id)
    platform = avatar_platform_for_provider(provider)
    if platform not in AVATAR_SUPPORTED_PLATFORMS:
        name = (provider or {}).get('name') or (provider or {}).get('id') or '该平台'
        raise HTTPException(status_code=400, detail=f'「{name}」暂不支持数字人/真人认证（目前仅 APIMart 可用，火山等平台待接入官方资产 API）。')
    kind = str(target_item.get('kind') or 'image').lower()
    if kind not in ('image', 'video', 'audio'):
        kind = 'image'
    if platform == 'apimart':
        project_name = str(payload.project_name or 'default').strip() or 'default'
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            public_url = await upload_media_for_apimart(client, provider, target_item.get('url') or '', kind)
        if not valid_apimart_video_image_input(public_url):
            reason = public_url[4:] if isinstance(public_url, str) and public_url.startswith('ERR:') else '无法获取公网可访问地址'
            raise HTTPException(status_code=400, detail=f'素材无法提交到 APIMart：{reason}\n请配置 PUBLIC_BASE_URL，或确认本地文件存在。')
        task_id = await submit_apimart_avatar_asset(provider, public_url, target_item.get('name') or 'asset', kind, project_name=project_name, group_name=payload.group_name)
    elif platform == 'volcengine':
        project_name = str(provider.get('volcengine_project_name') or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME
        public_url = volcengine_public_asset_url(target_item.get('url') or '')
        if public_url.startswith('ERR:'):
            raise HTTPException(status_code=400, detail=public_url[4:])
        task_id = await submit_volcengine_avatar_asset(public_url, target_item.get('name') or 'asset', kind, project_name=project_name, group_name=payload.group_name or '')
    else:
        raise HTTPException(status_code=400, detail='该平台的认证后端尚未接入。')
    regs = target_item.get('registrations')
    if not isinstance(regs, dict):
        regs = {}
    regs[platform] = {'provider_id': provider['id'], 'project_name': project_name, 'task_id': task_id, 'status': 'Processing', 'detail': '已提交，审核中', 'asset_uri': '', 'asset_id': '', 'registered_at': _app_main.now_ms()}
    target_item['registrations'] = regs
    save_asset_library(lib)
    return {'library': lib, 'item': target_item}

@router.post('/api/asset-library/items/{item_id}/avatar-status')
async def check_asset_library_avatar(item_id: str, payload: AssetAvatarRegisterRequest):
    lib = load_asset_library()
    target_item = find_asset_item_in_library(lib, item_id, payload.library_id)
    if not target_item:
        raise HTTPException(status_code=404, detail='资产不存在')
    regs = target_item.get('registrations') if isinstance(target_item.get('registrations'), dict) else {}
    provider = _app_main.get_api_provider(payload.provider_id or '')
    platform = avatar_platform_for_provider(provider)
    if platform not in AVATAR_SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail='该平台暂不支持数字人/真人认证审核。')
    reg = regs.get(platform) if isinstance(regs.get(platform), dict) else {}
    task_id = str(reg.get('task_id') or '').strip()
    if not task_id:
        raise HTTPException(status_code=400, detail='该素材还没有提交到这个平台的认证审核。')
    if platform == 'apimart':
        result = await check_apimart_avatar_task(provider, task_id)
    elif platform == 'volcengine':
        result = await check_volcengine_avatar_task(task_id, str(reg.get('project_name') or VOLCENGINE_DEFAULT_PROJECT_NAME).strip() or VOLCENGINE_DEFAULT_PROJECT_NAME)
    else:
        raise HTTPException(status_code=400, detail='该平台的认证后端尚未接入。')
    reg['status'] = result['status']
    reg['detail'] = result.get('detail') or ''
    if result['status'] == 'Active' and result.get('asset_uri'):
        reg['asset_uri'] = result['asset_uri']
        reg['asset_id'] = result['asset_uri'].replace('asset://', '')
    regs[platform] = reg
    target_item['registrations'] = regs
    save_asset_library(lib)
    return {'library': lib, 'item': target_item}

@router.delete('/api/asset-library/items/{item_id}')
async def delete_asset_library_item(item_id: str):
    lib = load_asset_library()
    removed = None
    for library in lib.get('libraries', []):
        for cat in library.get('categories', []):
            keep = []
            for item in cat.get('items', []):
                if item.get('id') == item_id:
                    removed = item
                else:
                    keep.append(item)
            cat['items'] = keep
    if not removed:
        raise HTTPException(status_code=404, detail='资产不存在')
    remove_asset_library_file(removed)
    save_asset_library(lib)
    return {'library': lib}

@router.post('/api/asset-library/items/delete')
async def batch_delete_asset_library_items(payload: AssetLibraryBatchDeleteRequest):
    ids = {str(item) for item in payload.ids or [] if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail='没有选择资产')
    lib = load_asset_library()
    removed = 0
    removed_items = []
    for library in lib.get('libraries', []):
        if payload.library_id and library.get('id') != payload.library_id:
            continue
        for cat in library.get('categories', []):
            keep = []
            for item in cat.get('items', []):
                if item.get('id') in ids:
                    removed += 1
                    removed_items.append(item)
                else:
                    keep.append(item)
            cat['items'] = keep
    for item in removed_items:
        remove_asset_library_file(item)
    save_asset_library(lib)
    return {'library': lib, 'removed': removed}

@router.post('/api/asset-library/items/move')
async def batch_move_asset_library_items(payload: AssetLibraryBatchMoveRequest):
    ids = {str(item) for item in payload.ids or [] if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail='没有选择资产')
    lib = load_asset_library()
    target_cat = find_asset_category_in_library(lib, payload.target_category_id, payload.target_library_id)
    if not target_cat:
        raise HTTPException(status_code=404, detail='目标分组不存在')
    target_type = target_cat.get('type') or 'image'
    moved = []
    for library in lib.get('libraries', []):
        if payload.library_id and library.get('id') != payload.library_id:
            continue
        for cat in library.get('categories', []):
            if (cat.get('type') or 'image') != target_type:
                continue
            keep = []
            for item in cat.get('items', []):
                if item.get('id') in ids:
                    moved.append(item)
                else:
                    keep.append(item)
            cat['items'] = keep
    existing_ids = {item.get('id') for item in target_cat.get('items', [])}
    for item in moved:
        if item.get('id') not in existing_ids:
            target_cat.setdefault('items', []).append(item)
            existing_ids.add(item.get('id'))
    save_asset_library(lib)
    return {'library': lib, 'moved': len(moved)}

@router.post('/api/asset-library/items/crop')
async def batch_crop_asset_library_items(payload: AssetLibraryBatchCropRequest):
    ids = {str(item) for item in payload.ids or [] if str(item)}
    if not ids:
        raise HTTPException(status_code=400, detail='没有选择资产')
    lib = load_asset_library()
    target_cat = None
    if payload.target_category_id:
        target_cat = find_asset_category_in_library(lib, payload.target_category_id, payload.target_library_id)
        if not target_cat:
            raise HTTPException(status_code=404, detail='目标分组不存在')
        if target_cat.get('type') != 'image':
            raise HTTPException(status_code=400, detail='目标分组不支持媒体')
    added = []
    for library in lib.get('libraries', []):
        if payload.library_id and library.get('id') != payload.library_id:
            continue
        for cat in library.get('categories', []):
            if cat.get('type') != 'image':
                continue
            source_items = [item for item in cat.get('items', []) or [] if item.get('id') in ids]
            for item in source_items:
                src = _app_main.output_file_from_url(item.get('url') or '')
                if not src or not os.path.isfile(src):
                    continue
                try:
                    with Image.open(src) as img:
                        img = img.convert('RGBA')
                        w, h = img.size
                        side = min(w, h)
                        if side <= 0:
                            continue
                        left = max(0, (w - side) // 2)
                        top = max(0, (h - side) // 2)
                        cropped = img.crop((left, top, left + side, top + side))
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                        tmp_path = tmp.name
                        tmp.close()
                        try:
                            cropped.save(tmp_path, 'PNG')
                            base_name = os.path.splitext(item.get('name') or 'asset')[0] + '_crop.png'
                            dest_cat = target_cat or cat
                            _, next_item = make_asset_library_item(tmp_path, base_name, subdir=dest_cat.get('dir') or '')
                            dest_cat.setdefault('items', []).append(next_item)
                            added.append(next_item)
                        finally:
                            try:
                                os.remove(tmp_path)
                            except Exception:
                                pass
                except Exception:
                    continue
    save_asset_library(lib)
    return {'library': lib, 'added': len(added), 'items': added}