# assets domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
from app.core import _DOUBLE_EXT_MEDIA, _DOUBLE_EXT_RE, _safe_asset_tag, _sniff_image_ext  # noqa: E402,F401
import logging
logger = logging.getLogger(__name__)

def unique_asset_category_dir(library, base_name: str) -> str:
    """为全局资源库分组生成一个唯一、文件系统安全的子文件夹名（library/<dir>/）。
    以分组名为基础（保留中文），与同库其它分组的 dir 及磁盘上已存在的文件夹去重。"""
    base = sanitize_asset_name(base_name, '分组').strip(' .') or '分组'
    existing = {str(c.get('dir')) for c in library.get('categories') or [] if isinstance(c, dict) and c.get('dir')}
    candidate = base
    i = 2
    while candidate in existing or os.path.exists(os.path.join(_svc._main('ASSET_LIBRARY_DIR'), candidate)):
        candidate = f'{base}_{i}'
        i += 1
    return candidate

def make_asset_library_item(src: str, name: str='', subdir: str='') -> Tuple[str, Dict[str, Any]]:
    kind = asset_library_media_kind(src)
    ext = asset_library_safe_extension(src, kind)
    safe_name = sanitize_asset_name(name or os.path.basename(src), 'asset')
    if not os.path.splitext(safe_name)[1]:
        safe_name += ext
    dest_name = f'lib_{uuid.uuid4().hex[:12]}_{safe_name}'
    subdir = str(subdir or '').strip('/').strip()
    if subdir:
        dest_dir = os.path.join(_svc._main('ASSET_LIBRARY_DIR'), subdir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, dest_name)
        rel = f'{subdir}/{dest_name}'
    else:
        dest_path = os.path.join(_svc._main('ASSET_LIBRARY_DIR'), dest_name)
        rel = dest_name
    shutil.copy2(src, dest_path)
    item = {'id': f'asset_{uuid.uuid4().hex[:12]}', 'name': os.path.splitext(safe_name)[0][:120], 'url': '/assets/library/' + urllib.parse.quote(rel, safe='/'), 'kind': kind, 'created_at': _svc._main('now_ms')()}
    return (dest_name, item)
    return lib

def load_asset_classification_prompt():
    try:
        if os.path.isfile(_svc._main('ASSET_CLASSIFICATION_PROMPT_FILE')):
            with open(_svc._main('ASSET_CLASSIFICATION_PROMPT_FILE'), 'r', encoding='utf-8-sig') as f:
                text = f.read().strip()
                if text:
                    return text
    except Exception as exc:
        logger.warning(f'读取素材分类规则失败: {exc}')
    return ASSET_CLASSIFICATION_PROMPT

def normalize_asset_classification(raw):
    if not isinstance(raw, dict):
        raw = {}
    categories = raw.get('categories') if isinstance(raw.get('categories'), dict) else {}
    clean_categories = {}
    flat = []
    for key, values in categories.items():
        norm_key = re.sub('[^A-Za-z0-9_-]+', '_', str(key or '').strip().lower())[:40]
        if not norm_key:
            continue
        if isinstance(values, str):
            values = re.split('[,，、/|;；\\n]+', values)
        if not isinstance(values, list):
            continue
        clean_values = []
        seen = set()
        for value in values:
            tag = _safe_asset_tag(value)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            clean_values.append(tag)
            flat.append({'dimension': norm_key, 'label': ASSET_CLASSIFICATION_DIMENSION_NAMES.get(norm_key, norm_key), 'tag': tag})
            if len(clean_values) >= 8:
                break
        if clean_values:
            clean_categories[norm_key] = clean_values
    tags = raw.get('tags') if isinstance(raw.get('tags'), list) else []
    clean_tags = []
    seen_tags = set()
    for value in tags:
        tag = _safe_asset_tag(value)
        if not tag or tag in seen_tags:
            continue
        seen_tags.add(tag)
        clean_tags.append(tag)
        flat.append({'dimension': 'tags', 'label': '标签', 'tag': tag})
        if len(clean_tags) >= 20:
            break
    seen_flat = set()
    flat_unique = []
    for item in flat:
        key = f"{item['dimension']}::{item['tag']}"
        if key in seen_flat:
            continue
        seen_flat.add(key)
        flat_unique.append(item)
    return {'summary': str(raw.get('summary') or '').strip()[:240], 'categories': clean_categories, 'tags': clean_tags, 'flat': flat_unique, 'updated_at': _svc._main('now_ms')()}

def parse_asset_classification_text(text):
    value = str(text or '').strip()
    if not value:
        return normalize_asset_classification({})
    value = re.sub('^```(?:json)?\\s*', '', value, flags=re.IGNORECASE).strip()
    value = re.sub('\\s*```$', '', value).strip()
    try:
        data = json.loads(value)
    except Exception:
        match = re.search('\\{.*\\}', value, re.S)
        data = json.loads(match.group(0)) if match else {}
    return normalize_asset_classification(data)

def asset_classification_prompt(extra_prompt=''):
    base = load_asset_classification_prompt()
    extra = str(extra_prompt or '').strip()
    if not extra:
        return base
    return base + '\n\n用户补充分类要求：\n' + extra[:4000]

async def classify_asset_image_best_effort(abs_path, provider_id='', model='', ms_model='', prompt=''):
    try:
        return await _svc.classify_image_with_provider(abs_path, provider_id, model, ms_model, prompt)
    except Exception as exc:
        logger.warning(f'素材智能分类失败: {exc}')
        return None

def find_asset_category_in_library(lib, category_id, library_id=''):
    library = _svc.find_asset_library(lib, library_id)
    if not library:
        return None
    for cat in library.get('categories', []):
        if cat.get('id') == category_id:
            return cat
    return None

def find_asset_category_with_library(lib, category_id, library_id=''):
    lib = _svc.normalize_asset_library(lib)
    preferred = str(library_id or '').strip()
    libraries = lib.get('libraries', []) or []
    if preferred:
        libraries = [item for item in libraries if item.get('id') == preferred]
    for library in libraries:
        for cat in library.get('categories', []) or []:
            if cat.get('id') == category_id:
                return (library, cat)
    return (None, None)

def normalize_prompt_library_item(item):
    if not isinstance(item, dict):
        item = {}
    name = sanitize_asset_name(item.get('name') or '提示词', '提示词')
    positive = str(item.get('positive') or item.get('text') or '').strip()
    return {'id': re.sub('[^A-Za-z0-9_-]+', '_', str(item.get('id') or item.get('item_id') or f'tpl_{uuid.uuid4().hex[:12]}'))[:60], 'name': name, 'category': normalize_prompt_category_id(item.get('category') or 'custom'), 'scene': str(item.get('scene') or '').strip()[:500], 'positive': positive, 'negative': str(item.get('negative') or '').strip(), 'params': item.get('params') if isinstance(item.get('params'), dict) else {}, 'created_at': int(item.get('created_at') or _svc._main('now_ms')()), 'updated_at': int(item.get('updated_at') or item.get('created_at') or _svc._main('now_ms')())}

def default_prompt_libraries():
    return {'active_library_id': 'system', 'libraries': [seed_system_prompt_library()], 'updated_at': _svc._main('now_ms')()}

def normalize_prompt_libraries(data):
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    raw_libraries = data.get('libraries') if isinstance(data.get('libraries'), list) else []
    raw_libraries = [lib for lib in raw_libraries if isinstance(lib, dict)]
    if not any((lib.get('id') == 'system' for lib in raw_libraries)):
        raw_libraries = [seed_system_prompt_library()] + raw_libraries
    libraries = []
    seen_lib_ids = set()
    for raw in raw_libraries:
        is_system = raw.get('id') == 'system'
        if is_system:
            lib_id = 'system'
        else:
            lib_id = re.sub('[^A-Za-z0-9_-]+', '_', str(raw.get('id') or f'lib_{uuid.uuid4().hex[:12]}'))[:60] or f'lib_{uuid.uuid4().hex[:12]}'
        if lib_id in seen_lib_ids:
            continue
        seen_lib_ids.add(lib_id)
        items = []
        seen_items = set()
        for raw_item in raw.get('items') if isinstance(raw.get('items'), list) else []:
            if not isinstance(raw_item, dict):
                continue
            item = normalize_prompt_library_item(raw_item)
            item_id = item.get('id') or f'tpl_{uuid.uuid4().hex[:12]}'
            if item_id in seen_items:
                continue
            seen_items.add(item_id)
            items.append(item)
        default_name = '系统提示词库' if is_system else '提示词库'
        raw_categories = raw.get('categories') if isinstance(raw.get('categories'), list) else []
        if not is_system:
            builtin_ids = {'view', 'storyboard', 'character', 'product', 'lighting', 'custom'}
            raw_categories = [c for c in raw_categories if isinstance(c, dict) and normalize_prompt_category_id(c.get('id') or c.get('name') or '') not in builtin_ids]
        libraries.append({'id': lib_id, 'name': sanitize_asset_name(raw.get('name') or default_name, default_name), 'type': 'prompt', 'readonly': False, 'system': is_system, 'categories': normalize_prompt_template_categories(raw_categories, include_defaults=is_system), 'items': items})
    active = str(data.get('active_library_id') or 'system')
    if not any((lib['id'] == active for lib in libraries)):
        active = 'system' if any((lib['id'] == 'system' for lib in libraries)) else libraries[0]['id'] if libraries else 'system'
    return {'active_library_id': active, 'libraries': libraries, 'updated_at': int(data.get('updated_at') or _svc._main('now_ms')())}

def public_prompt_libraries(data=None):
    data = normalize_prompt_libraries(data or _svc._main('load_prompt_libraries')())
    return {'active_library_id': data.get('active_library_id') or (data.get('libraries') or [{}])[0].get('id') or 'system', 'libraries': data.get('libraries') or [], 'updated_at': data.get('updated_at') or _svc._main('now_ms')()}

def migrate_double_extension_uploads():
    """修复历史遗留的双重扩展名（如 foo.png.png）：去掉重复的一层，并同步重命名 caption/classification 旁车文件。
    旧版 URL 导入会把自带扩展名的 entry.name 又拼一次 ext，导致文件名重复后缀、URL 对不上而无法显示。"""
    if not os.path.isdir(_svc._main('LOCAL_UPLOAD_DIR')):
        return
    renamed = 0
    for current, _dirs, files in os.walk(_svc._main('LOCAL_UPLOAD_DIR')):
        for name in files:
            m = _DOUBLE_EXT_RE.search(name)
            if not m or m.group(1).lower() not in _DOUBLE_EXT_MEDIA:
                continue
            old_path = os.path.join(current, name)
            new_path = os.path.join(current, name[:-len(m.group(1))])
            if os.path.exists(new_path):
                continue
            try:
                os.rename(old_path, new_path)
            except OSError:
                continue
            renamed += 1
            old_base = os.path.splitext(old_path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in ('.classification.json', '.txt'):
                src_side, dst_side = (old_base + suffix, new_base + suffix)
                if os.path.exists(src_side) and (not os.path.exists(dst_side)):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if renamed:
        logger.info(f'修复双重扩展名素材: {renamed} 个')

def migrate_mislabeled_image_extensions():
    """有些采集来的图片内容与扩展名不符（例如 WebP 内容却叫 .png），导致服务端按错误 content-type 返回、
    严格的客户端（PS UXP）解不出来。这里按真实魔数纠正扩展名，并同步重命名 caption/classification 旁车。"""
    if not os.path.isdir(_svc._main('LOCAL_UPLOAD_DIR')):
        return
    img_exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
    fixed = 0
    for current, _dirs, files in os.walk(_svc._main('LOCAL_UPLOAD_DIR')):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in img_exts:
                continue
            path = os.path.join(current, name)
            real = _sniff_image_ext(path)
            if not real:
                continue
            if real == ext or (real == '.jpg' and ext == '.jpeg'):
                continue
            new_name = os.path.splitext(name)[0] + real
            new_path = os.path.join(current, new_name)
            if os.path.exists(new_path):
                continue
            try:
                os.rename(path, new_path)
            except OSError:
                continue
            fixed += 1
            old_base = os.path.splitext(path)[0]
            new_base = os.path.splitext(new_path)[0]
            for suffix in ('.classification.json', '.txt'):
                src_side, dst_side = (old_base + suffix, new_base + suffix)
                if os.path.isfile(src_side) and (not os.path.exists(dst_side)):
                    try:
                        os.rename(src_side, dst_side)
                    except OSError:
                        pass
    if fixed:
        logger.info(f'纠正图片扩展名(内容与后缀不符): {fixed} 个')

def remove_asset_library_file(item) -> None:
    """删除资产对应的本地文件（仅限 library 副本，删了不影响 /output 原图）。日志不影响主流程。"""
    try:
        url = item.get('url') if isinstance(item, dict) else ''
        path = _svc._main('output_file_from_url')(url)
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception as exc:
        logger.warning(f'删除资产文件失败: {exc}')

def save_asset_classification_prompt(text):
    value = str(text or '').strip()
    if not value:
        raise HTTPException(status_code=400, detail='分类规则不能为空')
    if len(value) > 20000:
        raise HTTPException(status_code=400, detail='分类规则过长')
    os.makedirs(_svc._main('DATA_DIR'), exist_ok=True)
    with open(_svc._main('ASSET_CLASSIFICATION_PROMPT_FILE'), 'w', encoding='utf-8') as f:
        f.write(value)
    return value

def load_prompt_libraries():
    if not os.path.exists(_svc._main('PROMPT_LIBRARY_PATH')):
        data = default_prompt_libraries()
        return save_prompt_libraries(data)
    try:
        with open(_svc._main('PROMPT_LIBRARY_PATH'), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = default_prompt_libraries()
    if not isinstance(data, dict):
        data = default_prompt_libraries()
    normalized = normalize_prompt_libraries(data)
    if normalized.get('active_library_id') != data.get('active_library_id') or normalized.get('libraries') != data.get('libraries'):
        return save_prompt_libraries(normalized)
    return normalized

def save_prompt_libraries(data):
    data = normalize_prompt_libraries(data)
    data['updated_at'] = _svc._main('now_ms')()
    os.makedirs(_svc._main('DATA_DIR'), exist_ok=True)
    with open(_svc._main('PROMPT_LIBRARY_PATH'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
