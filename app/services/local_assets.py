# local_assets domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
from app.core import _local_upload_display_name, _local_upload_folder_node, _local_upload_kind_ext, _local_upload_rel_path  # noqa: E402,F401

def _local_upload_classification_path(filename):
    return os.path.splitext(os.path.join(_svc._main('LOCAL_UPLOAD_DIR'), filename))[0] + '.classification.json'

def _read_local_upload_classification(filename):
    path = _local_upload_classification_path(filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return _svc.normalize_asset_classification(json.load(f))
    except Exception:
        return None

def _write_local_upload_classification(filename, classification):
    path = _local_upload_classification_path(filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(_svc.normalize_asset_classification(classification), f, ensure_ascii=False, indent=2)

def _local_upload_abs(rel):
    rel_path = _local_upload_rel_path(rel)
    path = os.path.abspath(os.path.join(_svc._main('LOCAL_UPLOAD_DIR'), rel_path))
    root = os.path.abspath(_svc._main('LOCAL_UPLOAD_DIR'))
    try:
        common = os.path.commonpath([root, path])
    except ValueError:
        raise HTTPException(status_code=400, detail='非法路径')
    if common != root:
        raise HTTPException(status_code=400, detail='非法路径')
    return (rel_path, path)

def _local_upload_safe_path(name):
    filename, path = _local_upload_abs(name)
    if not filename:
        raise HTTPException(status_code=400, detail='文件名不能为空')
    return (filename, path)

def _local_upload_safe_folder(path_value):
    rel, path = _local_upload_abs(path_value)
    return (rel, path)

def _local_upload_caption_path(filename):
    return os.path.splitext(os.path.join(_svc._main('LOCAL_UPLOAD_DIR'), filename))[0] + '.txt'

def _read_local_upload_caption(filename):
    caption_path = _local_upload_caption_path(filename)
    if not os.path.isfile(caption_path):
        return ('', '')
    try:
        with open(caption_path, 'r', encoding='utf-8-sig') as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(caption_path, 'r', encoding='gb18030', errors='replace') as f:
            text = f.read()
    except OSError:
        return ('', '')
    return (text, os.path.basename(caption_path))

def _local_upload_item(filename):
    path = os.path.join(_svc._main('LOCAL_UPLOAD_DIR'), filename)
    rel = _local_upload_rel_path(filename)
    try:
        stat = os.stat(path)
        size = stat.st_size
        created_at = stat.st_mtime
    except OSError:
        size = 0
        created_at = 0
    kind, _ = _local_upload_kind_ext(filename, '')
    item = {'id': rel, 'file': rel, 'name': _local_upload_display_name(rel), 'url': f"/api/storage-files/local/{urllib.parse.quote(rel, safe='/')}", 'kind': kind or 'image', 'size': size, 'created_at': created_at, 'folder': os.path.dirname(rel).replace('\\', '/')}
    if kind == 'image':
        try:
            with Image.open(path) as img:
                item['natural_w'], item['natural_h'] = img.size
                item['width'], item['height'] = img.size
        except Exception:
            pass
        caption, caption_file = _read_local_upload_caption(filename)
        item['caption'] = caption
        item['caption_file'] = caption_file
        classification = _read_local_upload_classification(filename)
        if classification:
            item['classification'] = classification
    return item

def _local_upload_tree_and_items():
    root_node = _local_upload_folder_node('', '全部上传')
    folder_map = {'': root_node}
    items = []
    for current, dirs, files in os.walk(_svc._main('LOCAL_UPLOAD_DIR')):
        dirs[:] = sorted([d for d in dirs if not d.startswith('.') and (not d.startswith('._'))], key=str.lower)
        rel_dir = os.path.relpath(current, _svc._main('LOCAL_UPLOAD_DIR')).replace('\\', '/')
        if rel_dir == '.':
            rel_dir = ''
        node = folder_map.get(rel_dir)
        if node is None:
            node = _local_upload_folder_node(rel_dir)
            folder_map[rel_dir] = node
        for dirname in dirs:
            child_rel = f'{rel_dir}/{dirname}'.lstrip('/')
            child = _local_upload_folder_node(child_rel)
            folder_map[child_rel] = child
            node['children'].append(child)
        for name in sorted(files, key=str.lower):
            if name.startswith('.') or name.startswith('._'):
                continue
            rel_file = f'{rel_dir}/{name}'.lstrip('/')
            kind, _ = _local_upload_kind_ext(name, '')
            if kind is None:
                continue
            item = _local_upload_item(rel_file)
            node['items'].append(item)
            items.append(item)

    def fill_counts(node):
        total = len(node.get('items') or [])
        for child in node.get('children') or []:
            total += fill_counts(child)
        node['count'] = total
        return total
    fill_counts(root_node)
    items.sort(key=lambda it: it.get('created_at') or 0, reverse=True)
    return (root_node, items)

def import_local_image_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in LOCAL_IMAGE_IMPORT_EXTS:
        raise HTTPException(status_code=400, detail='仅支持 PNG、JPG、JPEG、WEBP、GIF 图片')
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='本地图片不存在或无法读取')
    try:
        size = os.path.getsize(path)
    except OSError:
        raise HTTPException(status_code=404, detail='本地图片不存在或无法读取')
    if size <= 0:
        raise HTTPException(status_code=400, detail='本地图片为空')
    if size > LOCAL_IMAGE_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail='本地图片过大，请使用 50MB 以内的图片')
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail='文件不是可识别的图片')
    filename = f'ai_ref_{uuid.uuid4().hex[:12]}{ext}'
    dest = _svc.output_path_for(filename, 'input')
    try:
        shutil.copyfile(path, dest)
    except OSError:
        raise HTTPException(status_code=500, detail='导入本地图片失败')
    return {'url': _svc.output_url_for(filename, 'input'), 'name': os.path.basename(path) or filename, 'kind': 'image'}
