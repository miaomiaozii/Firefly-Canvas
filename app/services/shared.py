# shared domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402
import firefly_json_io  # noqa: E402

def shared_folders_load():
    # #345：读坏了不能当成“没配过共享文件夹”——这份文件会被保存回写，静默清空就是用户丢数据。
    # read_json 会先把坏文件改名 .corrupt-<UTC> 保住字节并记诊断（界面会提示）。
    data = firefly_json_io.read_json(_svc._main('SHARED_FOLDERS_FILE'), default={})
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
    # #345：同一个文件先被截断再写，中途死掉就是一个 0 字节的共享文件夹列表。
    firefly_json_io.write_json_atomic(_svc._main('SHARED_FOLDERS_FILE'), data)
