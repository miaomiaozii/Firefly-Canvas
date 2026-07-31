# canvas domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402

def save_to_history(record):
    with CANVAS_LOCK, HISTORY_LOCK:
        history = []
        if os.path.exists(_svc._main('HISTORY_FILE')):
            try:
                with open(_svc._main('HISTORY_FILE'), 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except:
                pass
        if 'timestamp' not in record:
            record['timestamp'] = time.time()
        history.insert(0, record)
        with open(_svc._main('HISTORY_FILE'), 'w', encoding='utf-8') as f:
            json.dump(history[:5000], f, ensure_ascii=False, indent=4)

def canvas_path(canvas_id):
    cleaned = re.sub('[^a-zA-Z0-9_-]', '', canvas_id or '')
    if not cleaned:
        raise HTTPException(status_code=400, detail='无效的画布 ID')
    return os.path.join(_svc._main('CANVAS_DIR'), f'{cleaned}.json')

def save_canvas(canvas):
    if canvas.get('format') == 'firefly-canvas-v1' or canvas.get('kind') == 'core':
        raise HTTPException(status_code=410, detail='核心画布必须通过带版本号的增量项目接口保存')
    with CANVAS_LOCK:
        canvas['updated_at'] = max(_svc._main('now_ms')(), int(canvas.get('updated_at') or 0) + 1)
        with open(canvas_path(canvas['id']), 'w', encoding='utf-8') as f:
            json.dump(canvas, f, ensure_ascii=False, indent=2)

def new_canvas(title='未命名画布', icon='layers', kind='classic', project=None, board_x=None, board_y=None):
    project_id = str(project or '').strip()
    if not project_id:
        raise HTTPException(status_code=410, detail='旧画布创建入口已停用，请从项目列表新建项目')
    timestamp = _svc._main('now_ms')()
    canvas_kind = normalize_canvas_kind(kind)
    canvas = {'id': uuid.uuid4().hex, 'title': (title or ('智能画布' if canvas_kind == 'smart' else '未命名画布'))[:80], 'icon': (icon or ('sparkles' if canvas_kind == 'smart' else '🧩'))[:32], 'kind': canvas_kind, 'owner': '', 'color': '', 'pinned': False, 'project': project_id, 'created_at': timestamp, 'updated_at': timestamp, 'nodes': [], 'connections': [], 'viewport': {'x': 0, 'y': 0, 'scale': 1}}
    if board_x is not None:
        canvas['board_x'] = float(board_x)
    if board_y is not None:
        canvas['board_y'] = float(board_y)
    save_canvas(canvas)
    return canvas

def load_canvas(canvas_id):
    try:
        return _svc._main('project_store_call')('canvas', canvas_id)
    except ProjectNotFound:
        pass
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='画布不存在')
    with open(path, 'r', encoding='utf-8') as f:
        canvas = json.load(f)
    if canvas.get('deleted_at'):
        raise HTTPException(status_code=404, detail='画布已在回收站')
    return canvas

def load_canvas_any(canvas_id):
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='画布不存在')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def iter_canvas_records(include_deleted=False):
    _svc.cleanup_expired_canvas_trash()
    records = []
    for filename in os.listdir(_svc._main('CANVAS_DIR')):
        if not filename.endswith('.json'):
            continue
        try:
            with open(os.path.join(_svc._main('CANVAS_DIR'), filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        is_deleted = bool(data.get('deleted_at'))
        if include_deleted != is_deleted:
            continue
        records.append(canvas_record(data))
    return records

def list_canvases():
    records = iter_canvas_records(include_deleted=False)
    return sorted(records, key=lambda item: (0 if item.get('pinned') else 1, -int(item.get('updated_at') or item.get('created_at') or 0)))

def list_deleted_canvases():
    records = iter_canvas_records(include_deleted=True)
    return sorted(records, key=lambda item: item['deleted_at'], reverse=True)

def canvas_assets_index():
    canvases = []
    items = []
    canvas_counts = {'all': 0, 'smart': 0, 'classic': 0}
    item_counts = {'all': 0, 'smart': 0, 'classic': 0}
    _svc.cleanup_expired_canvas_trash()
    for filename in os.listdir(_svc._main('CANVAS_DIR')):
        if not filename.endswith('.json'):
            continue
        try:
            with open(os.path.join(_svc._main('CANVAS_DIR'), filename), 'r', encoding='utf-8') as f:
                canvas = json.load(f)
        except Exception:
            continue
        if canvas.get('deleted_at'):
            continue
        record = canvas_record(canvas)
        canvas_items = extract_canvas_assets(canvas)
        record['asset_count'] = len(canvas_items)
        canvases.append(record)
        items.extend(canvas_items)
        kind = record.get('kind') or 'classic'
        canvas_counts['all'] += 1
        canvas_counts[kind] = canvas_counts.get(kind, 0) + 1
        item_counts['all'] += len(canvas_items)
        item_counts[kind] = item_counts.get(kind, 0) + len(canvas_items)
    canvases.sort(key=lambda item: (0 if item.get('pinned') else 1, -int(item.get('updated_at') or item.get('created_at') or 0)))
    items.sort(key=lambda item: int(item.get('canvas_updated_at') or item.get('created_at') or 0), reverse=True)
    categories = [{'id': 'all', 'name': '全部画布', 'count': item_counts.get('all', 0), 'canvas_count': canvas_counts.get('all', 0)}, {'id': 'smart', 'name': '智能画布', 'count': item_counts.get('smart', 0), 'canvas_count': canvas_counts.get('smart', 0)}, {'id': 'classic', 'name': '普通画布', 'count': item_counts.get('classic', 0), 'canvas_count': canvas_counts.get('classic', 0)}]
    return {'categories': categories, 'canvases': canvases, 'items': items}
