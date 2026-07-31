# shared domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402

def shared_folders_load():
    try:
        with open(_svc._main('SHARED_FOLDERS_FILE'), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    folders = data.get('folders')
    if not isinstance(folders, list):
        folders = []
    return {'folders': [f for f in folders if isinstance(f, dict)]}

def shared_folder_by_id(folder_id):
    for entry in shared_folders_load().get('folders', []):
        if entry.get('id') == folder_id:
            return entry
    return None

def shared_folders_save(data):
    os.makedirs(_svc._main('DATA_DIR'), exist_ok=True)
    with open(_svc._main('SHARED_FOLDERS_FILE'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
