# storage domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402

def user_dir(user_id):
    path = os.path.join(_svc._main('CONVERSATION_DIR'), user_id)
    os.makedirs(path, exist_ok=True)
    return path

def cleanup_expired_canvas_trash():
    cutoff = _svc._main('now_ms')() - CANVAS_TRASH_RETENTION_MS
    with CANVAS_LOCK:
        for filename in os.listdir(_svc._main('CANVAS_DIR')):
            if not filename.endswith('.json'):
                continue
            path = os.path.join(_svc._main('CANVAS_DIR'), filename)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                deleted_at = int(data.get('deleted_at') or 0)
                if deleted_at and deleted_at < cutoff:
                    os.remove(path)
            except Exception:
                continue

def output_storage(category='output'):
    return (_svc._main('OUTPUT_INPUT_DIR'), 'input') if category == 'input' else (_svc._main('OUTPUT_OUTPUT_DIR'), 'output')

def output_url_for(filename, category='output'):
    folder, subdir = output_storage(category)
    rel = str(filename or '').replace('\\', '/').lstrip('/')
    try:
        asset_rel = os.path.relpath(os.path.join(folder, rel), _svc._main('ASSETS_DIR')).replace('\\', '/')
        if not asset_rel.startswith('../') and asset_rel != '..':
            return f"/assets/{urllib.parse.quote(asset_rel, safe='/')}"
    except Exception:
        pass
    kind = 'upload' if category == 'input' else 'generated'
    return f"/api/storage-files/{kind}/{urllib.parse.quote(rel, safe='/')}"

def output_path_for(filename, category='output'):
    folder, _ = output_storage(category)
    return os.path.join(folder, filename)

def storage_kind_dir(kind):
    kind = str(kind or '').strip().lower()
    if kind == 'upload':
        return os.path.abspath(_svc._main('OUTPUT_INPUT_DIR'))
    if kind == 'generated':
        return os.path.abspath(_svc._main('OUTPUT_OUTPUT_DIR'))
    if kind == 'local':
        return os.path.abspath(_svc._main('LOCAL_UPLOAD_DIR'))
    raise HTTPException(status_code=404, detail='未知存储目录')

def storage_file_path(kind, rel):
    root = storage_kind_dir(kind)
    rel_path = str(rel or '').replace('\\', '/').lstrip('/')
    rel_path = os.path.normpath(rel_path).replace('\\', '/')
    if not rel_path or rel_path == '.' or rel_path == '..' or rel_path.startswith('../') or os.path.isabs(rel_path):
        raise HTTPException(status_code=400, detail='非法文件路径')
    path = os.path.abspath(os.path.join(root, rel_path))
    try:
        if os.path.commonpath([root, path]) != root:
            raise HTTPException(status_code=400, detail='非法文件路径')
    except ValueError:
        raise HTTPException(status_code=400, detail='非法文件路径')
    return path if os.path.exists(path) else None

def output_file_from_url(url):
    if isinstance(url, dict):
        url = url.get('url', '')
    if not url:
        return None
    clean = urllib.parse.unquote(url.split('?', 1)[0]).replace('\\', '/')
    if clean.startswith(f'{PROJECT_MEDIA_ADAPTER_SCHEME}://'):
        try:
            return _svc.project_media_adapter_path(clean)
        except ProjectStoreError:
            return None
    project_handle = project_media_adapter_reference_from_url(clean)
    if project_handle:
        try:
            return _svc.project_media_adapter_path(project_handle)
        except (ProjectStoreError, KeyError, TypeError):
            return None
    if clean.startswith('/api/storage-files/'):
        rest = clean[len('/api/storage-files/'):].lstrip('/')
        kind, _, rel = rest.partition('/')
        return storage_file_path(kind, rel) if kind and rel else None
    if not (clean.startswith('/output/') or clean.startswith('/assets/')):
        return None
    if clean.startswith('/assets/'):
        root = _svc._main('ASSETS_DIR')
        rel = clean[len('/assets/'):]
    else:
        rel = clean[len('/output/'):]
    rel = rel.lstrip('/')
    if not rel:
        return None
    roots = [_svc._main('ASSETS_DIR')] if clean.startswith('/assets/') else [_svc._main('OUTPUT_OUTPUT_DIR'), _svc._main('OUTPUT_DIR')]
    for root in roots:
        path = os.path.abspath(os.path.join(root, rel))
        output_root = os.path.abspath(root)
        if os.path.commonpath([output_root, path]) == output_root and os.path.exists(path):
            return path
    return None
