# shared domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
import firefly_json_io  # noqa: E402

def shared_folders_load():
    data = firefly_json_io.read_global_json(_svc._main('SHARED_FOLDERS_FILE'), dict, default={})
    folders = data.get('folders')
    if not isinstance(folders, list):
        folders = []
    return {'folders': [f for f in folders if isinstance(f, dict)]}

def shared_folders_save(data):
    firefly_json_io.read_global_json(_svc._main('SHARED_FOLDERS_FILE'), dict)
    firefly_json_io.write_json_atomic(_svc._main('SHARED_FOLDERS_FILE'), data)
