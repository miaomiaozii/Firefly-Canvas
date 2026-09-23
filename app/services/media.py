# media domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
import logging
logger = logging.getLogger(__name__)

def reset_canvas_result_nodes_for_media(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Clear generated media while preserving prompts, references, settings and links."""
    reset_ids = []
    updated_nodes = []
    for node in list(canvas.get('nodes') or []):
        node = dict(node)
        node_type = str(node.get('type') or '').strip().lower()
        changed = False
        if isinstance(node.get('generatedOutputs'), list):
            outputs = list(node.get('generatedOutputs') or [])
            kept_outputs = [item for item in outputs if not any((_svc._main('json_references_media_path')(item, path) for path in paths))]
            if len(kept_outputs) != len(outputs):
                node['generatedOutputs'] = kept_outputs
                changed = True
        if node_type in {'smart-image', 'output'} and isinstance(node.get('images'), list):
            images = list(node.get('images') or [])
            if node_type == 'smart-image':
                owned_items = _svc.smart_owned_result_items(images, paths)
            else:
                owned_items = images
            owns_target = any((_svc._main('json_references_media_path')(item, path) for item in owned_items for path in paths))
            kept_images = [item for item in images if not (owns_target and item in owned_items and any((_svc._main('json_references_media_path')(item, path) for path in paths)))]
            if len(kept_images) != len(images):
                node['images'] = kept_images
                changed = True
                if node_type == 'output':
                    node['_pending'] = []
                    node['imageComparisons'] = {}
                if node_type == 'smart-image':
                    node['pending'] = 0
                    node['running'] = False
                    node['queued'] = False
                    for key in ('jimengPending', 'pendingTasks', 'runStartedAt', 'runFinishedAt', 'runElapsedMs', 'runTimerHidden', 'outputKind', 'w', 'h'):
                        node.pop(key, None)
        elif node_type == 'image' and any((_svc._main('json_references_media_path')(node.get('url'), path) for path in paths)):
            node['url'] = ''
            node['mediaKind'] = 'image'
            node['name'] = '空白图片'
            changed = True
        if changed and node.get('id'):
            reset_ids.append(str(node['id']))
        updated_nodes.append(node)
    if reset_ids:
        canvas['nodes'] = updated_nodes
    return reset_ids

def reference_to_data_url(ref, max_size=None, max_bytes=None):
    """把本地输出文件转为 data URL（base64）。max_size 限制最长边像素，避免 payload 过大。"""
    value = str(ref.get('url', '') or '').strip()
    path = _svc._main('local_media_adapter_path')(value, 'image')
    if not path:
        return value
    file_size = os.path.getsize(path)
    if max_bytes is not None and file_size > int(max_bytes):
        raise HTTPException(status_code=400, detail={'code': 'conversation_media_file_too_large', 'message': '项目图片参考素材超过对话适配器单文件上限'})
    if max_size:
        try:
            with Image.open(path) as img:
                w, h = img.size
                if max_bytes is not None and w * h > _svc._main('PROJECT_CONVERSATION_IMAGE_MAX_PIXELS'):
                    raise HTTPException(status_code=400, detail={'code': 'conversation_image_dimensions_exceeded', 'message': '项目图片像素尺寸超过对话适配器安全上限'})
                img.load()
                if max(w, h) > max_size:
                    img.thumbnail((max_size, max_size), Image.LANCZOS)
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                buf = BytesIO()
                fmt = 'PNG' if img.mode == 'RGBA' else 'JPEG'
                img.save(buf, format=fmt, quality=88 if fmt == 'JPEG' else None)
                encoded = base64.b64encode(buf.getvalue()).decode('ascii')
                mime = 'image/png' if fmt == 'PNG' else 'image/jpeg'
                return f'data:{mime};base64,{encoded}'
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f'reference resize failed, fallback to raw: {e}')
    with open(path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:{content_type_for_path(path)};base64,{encoded}'

def media_adapter_data_url(value: str, expected_kind: str, *, max_bytes: Optional[int]=None) -> str:
    """Encode one local adapter input without exposing an app/project URL upstream."""
    path = _svc._main('local_media_adapter_path')(value, expected_kind)
    if not path:
        return str(value or '').strip()
    size = os.path.getsize(path)
    if max_bytes is not None and size > int(max_bytes):
        raise HTTPException(status_code=400, detail={'code': 'conversation_media_file_too_large', 'message': f'项目{expected_kind}参考素材超过对话适配器单文件上限'})
    if size > _svc._main('PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES'):
        raise HTTPException(status_code=400, detail=f'项目{expected_kind}参考素材超过内联适配上限，请配置公网媒体地址或使用支持文件上传的服务商')
    with open(path, 'rb') as handle:
        encoded = base64.b64encode(handle.read()).decode('ascii')
    return f'data:{content_type_for_path(path)};base64,{encoded}'

def media_reference_to_url(value, max_image_size=None):
    if not isinstance(value, str) or not value:
        return ''
    if value.startswith('/output/') or value.startswith('/assets/'):
        return _svc._main('reference_to_data_url')({'url': value}, max_size=max_image_size)
    return value

def probe_local_audio_duration_seconds(value: str) -> Optional[float]:
    path = _svc._main('local_media_adapter_path')(value)
    if not path or not os.path.isfile(path):
        return None
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        proc = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path], capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            return None
        duration = float(str(proc.stdout or '').strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except Exception:
        return None

def local_video_path_for_cloud_upload(ref_url: str) -> str:
    return _svc._main('local_media_path_for_cloud_upload')(ref_url, ('video/',))

async def yuli_fetch_reference_bytes(client, ref_url):
    """把参考图（input_reference 垫图）取成 (filename, bytes, mime)，
    支持 /output、/assets 本地文件、data URL、http(s) URL。失败返回 None。"""
    ref_url = str(ref_url or '').strip()
    if not ref_url:
        return None
    if ref_url.startswith('data:'):
        header, b64 = ref_url.partition(',')
        mime = (header[5:].split(';')[0] or 'image/png').strip()
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return None
        ext = (mime.split('/')[-1] or 'png').split('+')[0]
        return (f'input_reference.{ext}', raw, mime)
    path = _svc._main('local_media_adapter_path')(ref_url, 'image')
    if path:
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except Exception:
            return None
        mime = content_type_for_path(path)
        return (os.path.basename(path) or 'input_reference', raw, mime)
    if ref_url.startswith('http://') or ref_url.startswith('https://'):
        try:
            resp = await client.get(ref_url)
            resp.raise_for_status()
            raw = resp.content
        except Exception:
            return None
        mime = (resp.headers.get('content-type') or 'image/png').split(';')[0].strip()
        ext = (mime.split('/')[-1] or 'png').split('+')[0]
        return (f'input_reference.{ext}', raw, mime)
    return None

async def responses_input_image_url(ref, require_public_url=False) -> str:
    """RS / Responses 的 input_image。
    本机/内网 URL 不能透传（上游拉不到会挂到 Cloudflare 120s 超时/524）。
    本地文件优先上传图床（同视频卡片的 Litterbox/temp.sh 通道）换公网短链——
    几 MB 的 base64 请求体会让部分中转源站处理超时，公网 URL 让请求体和文生图一样小；
    图床不可用时回退内联 base64（Responses 协议两种都支持）。"""
    raw = ref.get('url', '') if isinstance(ref, dict) else ref
    text = str(raw or '').strip()
    if not text:
        return ''
    local_path = text
    if re.match('^https?://', text, re.I):
        parsed = urllib.parse.urlsplit(text)
        host = (parsed.hostname or '').lower()
        if host in {'127.0.0.1', 'localhost', '::1'} or re.match('^(192\\.168\\.|10\\.|172\\.(1[6-9]|2\\d|3[01])\\.)', host):
            local_path = urllib.parse.unquote(parsed.path or '')
        else:
            return text
    local_file = _svc._main('output_file_from_url')(local_path)
    if not local_file:
        if require_public_url:
            raise HTTPException(status_code=400, detail=f'RS 参考图不是公网 URL，无法传给上游：{text[:160]}')
        return ''
    if require_public_url:
        return await openai_video_proxy_public_reference_url(local_path)
    try:
        uploaded = await _svc.upload_local_video_to_cloud(local_path)
        url = str((uploaded or {}).get('url') or '')
        if url.startswith(('http://', 'https://')):
            return url
    except HTTPException as exc:
        logger.warning(f'RS 参考图上传图床失败，回退内联 base64：{exc.detail}')
    except Exception as exc:
        logger.warning(f'RS 参考图上传图床异常，回退内联 base64：{exc}')
    data_url = _svc._main('reference_to_data_url')({'url': local_path}, max_size=1536)
    return data_url if data_url.startswith('data:') else ''

def local_media_adapter_path(value: str, expected_kind: str='') -> Optional[str]:
    """Resolve only app-local or internal project media handles for an adapter."""
    text = str(value or '').strip()
    legacy_project_handle = project_media_adapter_reference_from_url(text)
    if text.startswith('/api/project-library/') and (not legacy_project_handle):
        raise ProjectStoreError('项目媒体 URL 无效')
    if legacy_project_handle:
        text = legacy_project_handle
    if text.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://'):
        return _svc.project_media_adapter_path(text, expected_kind)
    if not text.startswith(('/output/', '/assets/', '/api/project-library/', '/api/storage-files/')):
        return None
    path = _svc._main('output_file_from_url')(text)
    if not path:
        return None
    expected = str(expected_kind or '').strip().lower()
    if expected:
        actual_mime = content_type_for_path(path).split(';', 1)[0].lower()
        if not actual_mime.startswith(f'{expected}/'):
            raise ProjectStoreError(f'本地参考素材不是{expected}文件')
    return path

def local_media_path_from_url(url: str) -> Optional[str]:
    """Resolve a local media URL using the same one-to-one mapping as app mounts."""
    if not url:
        return None
    clean = urllib.parse.unquote(str(url).split('?', 1)[0]).replace('\\', '/')
    if clean.startswith('/api/storage-files/'):
        rest = clean[len('/api/storage-files/'):].lstrip('/')
        kind, _, rel = rest.partition('/')
        return _svc.storage_file_path(kind, rel) if kind and rel else None
    if clean.startswith('/assets/'):
        root = _svc._main('ASSETS_DIR')
        rel = clean[len('/assets/'):]
    elif clean.startswith('/output/'):
        root = _svc._main('OUTPUT_DIR')
        rel = clean[len('/output/'):]
    else:
        return None
    rel = rel.lstrip('/')
    if not rel:
        return None
    root = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root, rel))
    try:
        return path if os.path.commonpath([root, path]) == root and os.path.exists(path) else None
    except ValueError:
        return None

def generated_media_path_from_url(url: str) -> Optional[str]:
    """Resolve a URL only when it points inside a generated-output directory."""
    try:
        path = local_media_path_from_url(url)
    except (HTTPException, OSError, ValueError):
        return None
    if not path or not os.path.isfile(path):
        return None
    path = os.path.realpath(path)
    for root in (_svc._main('OUTPUT_OUTPUT_DIR'), _svc._main('OUTPUT_DIR')):
        root = os.path.realpath(root)
        try:
            if os.path.commonpath([root, path]) == root:
                return path
        except ValueError:
            continue
    return None

def json_references_media_path(value: Any, target_path: str) -> bool:
    """Return True when a JSON-compatible value references the local media file."""
    target = os.path.normcase(os.path.realpath(target_path))
    if isinstance(value, str):
        try:
            resolved = local_media_path_from_url(value.strip())
        except (HTTPException, OSError, ValueError):
            return False
        return bool(resolved and os.path.normcase(os.path.realpath(resolved)) == target)
    if isinstance(value, dict):
        return any((json_references_media_path(item, target) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return any((json_references_media_path(item, target) for item in value))
    return False

def persisted_json_references_media_path(target_path: str) -> bool:
    """Scan persisted user documents before deleting generated media.

    Canvas history is only one possible owner. Nodes, other canvases, and asset
    metadata all count as live references and keep the file on disk.
    """
    candidates = [_svc._main('ASSET_LIBRARY_PATH')]
    for root in (_svc._main('CANVAS_DIR'),):
        if os.path.isdir(root):
            for current, files in os.walk(root):
                candidates.extend((os.path.join(current, name) for name in files if name.lower().endswith('.json')))
    seen = set()
    for path in candidates:
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            with open(path, 'r', encoding='utf-8-sig') as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        if json_references_media_path(value, target_path):
            return True
    return False

def expand_canvas_generated_media_paths(canvas: Dict[str, Any], paths: List[str]) -> List[str]:
    """Include every generated result owned by a result node touched by the log."""
    expanded = list(paths)
    for node in list(canvas.get('nodes') or []):
        node_type = str(node.get('type') or '').strip().lower()
        images = list(node.get('images') or [])
        if node_type == 'output':
            owned_items = images
        elif node_type == 'smart-image':
            owned_items = _svc.smart_owned_result_items(images, paths)
        else:
            owned_items = []
        if not any((json_references_media_path(item, path) for item in owned_items for path in paths)):
            continue
        for item in owned_items:
            for url in collect_local_media_urls(item):
                candidate = generated_media_path_from_url(url)
                if candidate and candidate not in expanded:
                    expanded.append(candidate)
    return expanded

def delete_media_preview_cache(path: str) -> int:
    """Delete derived previews for a source file before the source disappears."""
    try:
        stat = os.stat(path)
    except OSError:
        return 0
    source = os.path.realpath(path)
    removed = 0
    for width in range(0, 4097):
        keys = [hashlib.sha1(f'{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}|jpg'.encode('utf-8', 'ignore')).hexdigest() + '.jpg']
        if 64 <= width <= 2048:
            preview_key = hashlib.sha1(f'{source}|{stat.st_mtime_ns}|{stat.st_size}|{width}'.encode('utf-8', 'ignore')).hexdigest()
            keys.extend((preview_key + '.webp', preview_key + '.png'))
        for name in keys:
            cache_path = os.path.join(_svc._main('MEDIA_PREVIEW_DIR'), name)
            try:
                if os.path.isfile(cache_path):
                    os.remove(cache_path)
                    removed += 1
            except OSError:
                pass
    return removed

def media_preview_cache_paths(path: str, width: int):
    stat = os.stat(path)
    key = hashlib.sha1(f'{os.path.realpath(path)}|{stat.st_mtime_ns}|{stat.st_size}|{width}'.encode('utf-8', 'ignore')).hexdigest()
    return (os.path.join(_svc._main('MEDIA_PREVIEW_DIR'), f'{key}.webp'), os.path.join(_svc._main('MEDIA_PREVIEW_DIR'), f'{key}.png'))

def local_media_file_by_basename(name: str):
    safe = os.path.basename(urllib.parse.unquote(str(name or '')))
    if not safe:
        return None
    roots = [_svc._main('OUTPUT_OUTPUT_DIR'), _svc._main('OUTPUT_INPUT_DIR'), os.path.join(_svc._main('ASSETS_DIR'), 'output'), os.path.join(_svc._main('ASSETS_DIR'), 'input'), os.path.join(_svc._main('ASSETS_DIR'), 'library')]
    for root in roots:
        path = os.path.abspath(os.path.join(root, safe))
        root_abs = os.path.abspath(root)
        if os.path.commonpath([root_abs, path]) == root_abs and os.path.isfile(path):
            return path
    return None

def is_image_reference_value(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith('data:image/'):
        return True
    if value.startswith('data:'):
        return False
    if value.startswith('/output/') or value.startswith('/assets/'):
        path = _svc._main('output_file_from_url')(value)
        return bool(path and content_type_for_path(path).startswith('image/'))
    clean = value.split('?', 1)[0].lower()
    if re.search('\\.(mp4|webm|mov|m4v|avi|mkv|mp3|wav|m4a|aac|ogg|flac)$', clean):
        return False
    return True

def is_video_reference_value(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith('data:video/'):
        return True
    if value.startswith('data:'):
        return False
    if value.startswith('/output/') or value.startswith('/assets/'):
        path = _svc._main('output_file_from_url')(value)
        return bool(path and content_type_for_path(path).startswith('video/'))
    clean = value.split('?', 1)[0].lower()
    return bool(re.search('\\.(mp4|webm|mov|m4v|avi|mkv)$', clean))

def convert_output_to_jpg(url, quality=88):
    path = _svc._main('output_file_from_url')(url)
    if not path:
        return url
    root, ext = os.path.splitext(path)
    if ext.lower() in ['.jpg', '.jpeg']:
        return url
    jpg_path = f'{root}.jpg'
    try:
        with Image.open(path) as img:
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
                img = bg
            else:
                img = img.convert('RGB')
            img.save(jpg_path, 'JPEG', quality=quality, optimize=True)
        try:
            root = _svc._main('ASSETS_DIR') if os.path.commonpath([os.path.abspath(_svc._main('ASSETS_DIR')), os.path.abspath(jpg_path)]) == os.path.abspath(_svc._main('ASSETS_DIR')) else _svc._main('OUTPUT_DIR')
        except ValueError:
            root = _svc._main('OUTPUT_DIR')
        rel = os.path.relpath(jpg_path, root).replace('\\', '/')
        prefix = '/assets' if root == _svc._main('ASSETS_DIR') else '/output'
        return f'{prefix}/{rel}'
    except Exception as e:
        logger.warning(f'转换 JPG 失败: {e}')
        return url

def attachment_embedded_image_data_urls(refs, max_images=4):
    urls = []
    for ref in refs or []:
        if not isinstance(ref, dict) or is_image_reference(ref):
            continue
        path = _svc._main('output_file_from_url')(ref.get('url', ''))
        if not path or os.path.splitext(path)[1].lower() != '.xlsx':
            continue
        urls.extend(xlsx_embedded_image_data_urls(path, max_images=max(0, max_images - len(urls))))
        if len(urls) >= max_images:
            break
    return urls[:max_images]

def attachment_text_blocks(refs, limit_each=MAX_ATTACHMENT_TEXT_CHARS):
    blocks = []
    for ref in (refs or [])[:CHAT_ATTACHMENT_MAX]:
        if not isinstance(ref, dict) or is_image_reference(ref):
            continue
        path = _svc._main('output_file_from_url')(ref.get('url', ''))
        text = read_text_attachment(path, limit_each) if path else ''
        if not text:
            continue
        name = ref.get('name') or os.path.basename(path)
        blocks.append(f'附件：{name}\n{text}')
    return blocks

async def video_reference_to_frame_data_urls(value, max_frames=6, max_size=768):
    if not isinstance(value, str) or not value:
        return []
    path = _svc._main('output_file_from_url')(value)
    cleanup_path = ''
    if not path and value.startswith(('http://', 'https://')):
        suffix = os.path.splitext(urllib.parse.urlparse(value).path)[1] or '.mp4'
        fd, cleanup_path = tempfile.mkstemp(prefix='canvas_llm_video_', suffix=suffix)
        os.close(fd)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=10.0)) as client:
                response = await client.get(value)
                response.raise_for_status()
                with open(cleanup_path, 'wb') as f:
                    f.write(response.content)
            path = cleanup_path
        except Exception as e:
            logger.warning(f'[canvas-llm] video download failed: {e}')
            if cleanup_path and os.path.exists(cleanup_path):
                try:
                    os.remove(cleanup_path)
                except OSError:
                    pass
            return []
    if not path or not os.path.exists(path):
        return []
    frame_dir = tempfile.mkdtemp(prefix='canvas_llm_frames_')
    try:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            return []
        pattern = os.path.join(frame_dir, 'frame_%03d.jpg')
        cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', path, '-vf', f"fps=1,scale='min({max_size},iw)':-2", '-frames:v', str(max(1, max_frames)), pattern]
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            logger.warning(f'[canvas-llm] ffmpeg frame extract failed: {proc.stderr[:300]}')
            return []
        frames = []
        for name in sorted(os.listdir(frame_dir)):
            if not name.lower().endswith(('.jpg', '.jpeg', '.png')):
                continue
            frame_path = os.path.join(frame_dir, name)
            with open(frame_path, 'rb') as f:
                frames.append(f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode('ascii')}")
        return frames
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if cleanup_path and os.path.exists(cleanup_path):
            try:
                os.remove(cleanup_path)
            except OSError:
                pass

def local_asset_public_url(value: str) -> str:
    text = str(value or '').strip()
    if not text.startswith(('/output/', '/assets/')):
        return ''
    if not _svc._main('output_file_from_url')(text):
        return ''
    base = _svc.public_base_url()
    if not base:
        return ''
    return f"{base}{urllib.parse.quote(text, safe='/:?&=%#.-_~')}{public_media_url_suffix()}"

async def openai_video_proxy_public_reference_url(ref) -> str:
    """异步生图（openai-video-proxy）的参考图公网化。
    不走公网隧道（暴露本机服务风险高）：本地文件上传图床（Litterbox/temp.sh，72h 短链），
    与 RS 模式同一通道；真正的公网 URL 原样透传；若手动配置了 PUBLIC_MEDIA_BASE_URL 则作为兜底。"""
    raw = ref.get('url', '') if isinstance(ref, dict) else ref
    text = str(raw or '').strip()
    if not text:
        return ''
    parsed = urllib.parse.urlsplit(text)
    local_path = ''
    if parsed.scheme in {'http', 'https'}:
        host = (parsed.hostname or '').lower()
        if host in {'127.0.0.1', 'localhost', '::1'} or re.match('^(192\\.168\\.|10\\.|172\\.(1[6-9]|2\\d|3[01])\\.)', host):
            local_path = urllib.parse.unquote(parsed.path or '')
        else:
            return text
    elif text.startswith(('/output/', '/assets/')):
        local_path = text
    if local_path and _svc._main('output_file_from_url')(local_path):
        upload_error = ''
        try:
            uploaded = await _svc.upload_local_video_to_cloud(local_path)
            url = str((uploaded or {}).get('url') or '')
            if url.startswith(('http://', 'https://')):
                return url
        except HTTPException as exc:
            upload_error = str(exc.detail)
        public_url = local_asset_public_url(local_path)
        if public_url:
            return public_url
        raise HTTPException(status_code=400, detail=f"参考图上传图床失败，无法转成公网 URL：{upload_error[:200] or '未知错误'}。请检查网络后重试。")
    raise HTTPException(status_code=400, detail=f'参考图不是公网 URL，无法传给上游：{text[:160]}')

def openai_video_proxy_local_image_path(ref) -> str:
    raw = ref.get('url', '') if isinstance(ref, dict) else ref
    text = str(raw or '').strip()
    if not text:
        return ''
    local_path = ''
    if re.match('^https?://', text, re.I):
        parsed = urllib.parse.urlsplit(text)
        host = (parsed.hostname or '').lower()
        if host in {'127.0.0.1', 'localhost', '::1'} or re.match('^(192\\.168\\.|10\\.|172\\.(1[6-9]|2\\d|3[01])\\.)', host):
            local_path = urllib.parse.unquote(parsed.path or '')
    elif text.startswith(('/output/', '/assets/')):
        local_path = text
    path = _svc._main('output_file_from_url')(local_path) if local_path else None
    if not path:
        return ''
    return path if content_type_for_path(path).startswith('image/') else ''

def local_media_path_for_cloud_upload(ref_url: str, allowed_prefixes=('image/', 'video/')) -> str:
    ref_url = str(ref_url or '').strip()
    if not ref_url:
        raise HTTPException(status_code=400, detail='没有可上传的媒体文件')
    if ref_url.startswith('http://') or ref_url.startswith('https://'):
        return ''
    if ref_url.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://'):
        path = local_media_adapter_path(ref_url)
    elif ref_url.startswith(('/output/', '/assets/', '/api/project-library/')):
        path = _svc._main('output_file_from_url')(ref_url)
    else:
        raise HTTPException(status_code=400, detail='云端上传只支持项目或画布里的本地图片/视频文件')
    if not path:
        raise HTTPException(status_code=404, detail='本地媒体文件不存在或已被删除')
    ct = content_type_for_path(path)
    if not any((ct.startswith(prefix) for prefix in allowed_prefixes)):
        raise HTTPException(status_code=400, detail='请选择图片或视频文件再上传云端')
    max_bytes = int(os.getenv('TEMP_SH_MAX_BYTES', str(4 * 1024 * 1024 * 1024)))
    size = os.path.getsize(path)
    if size > max_bytes:
        raise HTTPException(status_code=400, detail=f'媒体文件超过云端上传大小限制：{size} bytes')
    return path

def image_output_meta(url, source_item=None):
    meta = {'url': url, 'kind': 'image'}
    if not url:
        return meta
    parsed_name = os.path.basename(urllib.parse.urlparse(str(url)).path)
    if parsed_name:
        meta['name'] = parsed_name
    if isinstance(source_item, dict):
        for key in ('natural_w', 'natural_h', 'width', 'height', 'w', 'h', 'layout_w', 'layout_h'):
            try:
                value = int(float(source_item.get(key) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                meta[key] = value
    path = _svc._main('output_file_from_url')(url)
    if path and os.path.exists(path):
        try:
            with Image.open(path) as img:
                width, height = img.size
            if width > 0 and height > 0:
                meta.update({'natural_w': width, 'natural_h': height, 'width': width, 'height': height})
        except Exception:
            pass
    return meta

def image_size_from_reference(ref):
    path = _svc._main('output_file_from_url')(ref)
    if not path:
        return ''
    try:
        with Image.open(path) as img:
            width, height = img.size
        if width > 0 and height > 0:
            return f'{width}x{height}'
    except Exception as exc:
        logger.warning(f'[chat-agent] failed to read reference image size: {exc}')
    return ''

def video_api_root(provider):
    base_url = (provider.get('base_url') or _svc._main('AI_BASE_URL')).rstrip('/')
    if is_volcengine_provider(provider):
        if base_url.endswith('/api/v3'):
            base_url = base_url[:-len('/api/v3')]
        return base_url
    if base_url.endswith('/v1') or base_url.endswith('/v2'):
        base_url = base_url.rsplit('/', 1)[0]
    return base_url

def video_submit_url_candidates(provider, base_url, model=''):
    if str(provider.get('video_generation_endpoint') or '').strip():
        return [_svc.provider_endpoint_url(provider, 'video_generation_endpoint', '/v1/videos/generations')]
    root = video_api_root(provider)
    if is_agnes_provider(provider):
        return [f'{root}/v1/videos']
    if is_lingjing_provider(provider):
        return [f'{root}/v1/videos']
    if is_apimart_provider(provider):
        return [f'{root}/v1/videos/generations']
    if is_volcengine_provider(provider):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.path and parsed.path.rstrip('/'):
            return [base_url]
        return [f'{base_url}/api/v3/contents/generations/tasks']
    if is_yuli_provider(provider):
        return [f'{root}/v1/video/create']
    model_id = str(model or '').strip().lower()
    if (model_id.startswith('veo') or model_id.startswith('wan2') or model_id.startswith('wanx')
            or model_id.startswith('doubao-seedance') or model_id.startswith('seedance')
            or model_id.startswith('grok-video') or model_id.startswith('grok-imagine-video')
            or model_id.startswith('grok-1.5-video') or model_id.startswith('minimax-hailuo')
            or model_id == 'hailuo-video' or model_id.startswith('sora-2') or model_id == 'sora2'):
        return [f'{root}/v2/videos/generations', f'{root}/v1/videos/generations']
    return [f'{root}/v1/videos/generations', f'{root}/v2/videos/generations']

def reserve_project_media_import(project_id: str, source_key: str):
    with project_lifecycle_lock(project_id):
        _svc._main('project_store_call')('open_project', project_id)
        executor = ensure_project_media_import_executor()
        with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
            cutoff = _svc._main('now_ms')() - 60 * 60 * 1000
            for stale_id in [key for key, value in PROJECT_MEDIA_IMPORT_TASKS.items() if value.get('status') in {'completed', 'failed'} and int(value.get('updated_at') or 0) < cutoff]:
                PROJECT_MEDIA_IMPORT_TASKS.pop(stale_id, None)
                PROJECT_MEDIA_IMPORT_FUTURES.pop(stale_id, None)
            duplicate = next((value for value in PROJECT_MEDIA_IMPORT_TASKS.values() if value.get('project_id') == project_id and value.get('source_key') == source_key and (value.get('status') == 'pending')), None)
            if duplicate is not None:
                return (executor, duplicate, False)
            pending = [value for value in PROJECT_MEDIA_IMPORT_TASKS.values() if value.get('status') == 'pending']
            if len(pending) >= PROJECT_MEDIA_IMPORT_MAX_PENDING:
                raise HTTPException(status_code=429, detail='媒体导入队列已满，请稍后重试')
            if sum((1 for value in pending if value.get('project_id') == project_id)) >= _svc._main('PROJECT_MEDIA_IMPORT_MAX_PER_PROJECT'):
                raise HTTPException(status_code=429, detail='当前项目的媒体导入队列已满')
            task_id = uuid.uuid4().hex
            task = {'id': task_id, 'project_id': project_id, 'source_key': source_key, 'status': 'pending', 'created_at': _svc._main('now_ms')(), 'updated_at': _svc._main('now_ms')()}
            PROJECT_MEDIA_IMPORT_TASKS[task_id] = task
            return (executor, task, True)

def build_project_media_preview(project_id: str, asset_id: str, width: int) -> str:
    record = _svc._main('project_store_call')('media_record', project_id, asset_id)
    if record.get('kind') not in {'image', 'video'}:
        raise ProjectStoreError('该媒体没有图像预览')
    source = Path(record['path'])
    preview_root = source.parent.parent / 'previews'
    preview_root.mkdir(parents=True, exist_ok=True)
    preview_path = preview_root / f'{asset_id}-{width}.webp'
    if preview_path.is_file():
        return str(preview_path)
    image = _svc._main('generate_video_preview_image')(str(source), width) if record.get('kind') == 'video' else Image.open(source)
    try:
        image.thumbnail((width, width), Image.Resampling.LANCZOS)
        if image.mode not in {'RGB', 'RGBA'}:
            converted = image.convert('RGBA' if 'transparency' in image.info else 'RGB')
            image.close()
            image = converted
        temporary = preview_root / f'.{asset_id}-{width}.{uuid.uuid4().hex}.tmp'
        try:
            image.save(temporary, format='WEBP', quality=82, method=4)
            os.replace(temporary, preview_path)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        image.close()
    return str(preview_path)
