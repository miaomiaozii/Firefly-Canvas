# projects domain — extracted from app/services.py (W1d)
from ._base import *  # noqa: F401,F403
import app.services as _svc  # noqa: E402

def load_projects():
    try:
        with open(_svc._main('PROJECTS_PATH'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        projects = data.get('projects') if isinstance(data, dict) else data
        if isinstance(projects, list):
            return [p for p in projects if isinstance(p, dict) and p.get('id')]
    except Exception:
        pass
    return []

def save_projects(projects):
    with CANVAS_LOCK:
        with open(_svc._main('PROJECTS_PATH'), 'w', encoding='utf-8') as f:
            json.dump({'projects': projects}, f, ensure_ascii=False, indent=2)

def ensure_default_project():
    """兼容读取旧分类元数据；核心产品不再创建默认项目。"""
    return load_projects()

def new_project(name='新项目'):
    projects = ensure_default_project()
    ts = _svc._main('now_ms')()
    clean = (str(name or '').strip() or '新项目')[:60]
    order = max([int(p.get('order') or 0) for p in projects], default=0) + 1
    proj = {'id': uuid.uuid4().hex, 'name': clean, 'order': order, 'created_at': ts, 'updated_at': ts}
    projects.append(proj)
    save_projects(projects)
    return proj

def switch_project_library(path: str, move_existing: bool):
    global FIREFLY_PROJECT_STORE, PROJECT_LIBRARY_MIGRATION_ACTIVE
    with PROJECT_LIBRARY_SWITCH_LOCK:
        PROJECT_LIBRARY_MIGRATION_ACTIVE = True
        old_store = _svc._main('FIREFLY_PROJECT_STORE')
        new_store = None
        migration = None
        completed = False
        try:
            with PROJECT_MEDIA_IMPORT_TASKS_LOCK:
                active_imports = [future for future in PROJECT_MEDIA_IMPORT_FUTURES.values() if not future.done()]
            if active_imports:
                raise ProjectStoreError('仍有项目媒体正在导入，请等待完成后再移动项目库')
            projects = old_store.list_projects()
            if projects and (not move_existing):
                raise ProjectStoreError('当前项目库不为空，必须显式选择移动已有项目')
            target = Path(path).expanduser()
            if move_existing:
                migration = old_store.copy_library_to(target)
            new_store = ProjectStore(target, snapshot_delay=old_store.snapshot_delay)
            project_count = len(new_store.list_projects())
            if move_existing and project_count != len(projects):
                raise ProjectStoreError('新项目库项目数量校验失败')
            _svc._main('update_env_values')({'FIREFLY_PROJECTS_ROOT': str(new_store.root)})
            FIREFLY_PROJECT_STORE = new_store
            new_store = None
            completed = True
            try:
                old_store.close()
            except Exception as exc:
                print(f'关闭原项目库失败（新项目库已安全切换）: {exc}')
            return {'project_library': {'path': str(_svc._main('FIREFLY_PROJECT_STORE').root), 'project_count': project_count}, 'migration': migration}
        finally:
            if new_store is not None:
                try:
                    new_store.close()
                except Exception as exc:
                    print(f'关闭候选项目库失败: {exc}')
            if not completed and migration:
                shutil.rmtree(migration['target'], ignore_errors=True)
                if migration.get('target_preexisted'):
                    os.makedirs(migration['target'], exist_ok=True)
            PROJECT_LIBRARY_MIGRATION_ACTIVE = False

def project_image_references(project_id: str, asset_ids: List[str]) -> List[Dict[str, Any]]:
    references = []
    for index, asset_id in enumerate(asset_ids):
        record = _svc._main('project_store_call')('media_record', project_id, asset_id)
        if record.get('kind') != 'image':
            raise ProjectStoreError(f'参考资源不是图片: {asset_id}')
        references.append({'url': f"/api/project-library/{urllib.parse.quote(project_id, safe='')}/media/{urllib.parse.quote(asset_id, safe='')}", 'name': record.get('name') or f'reference-{index + 1}.png', 'kind': 'image', 'mime': record.get('mime_type') or 'image/png', 'asset_id': asset_id})
    return references

def export_project_package(project_id: str, path: str):
    with project_lifecycle_lock(project_id):
        if project_has_active_media_import(project_id):
            raise ProjectMediaImportActive('项目仍有媒体正在导入，请稍后再导出')
        if project_has_active_image_generation(project_id):
            raise ProjectMediaImportActive('项目仍有图片正在生成，请稍后再导出')
        if project_has_active_video_generation(project_id):
            raise ProjectMediaImportActive('项目仍有视频正在生成，请稍后再导出')
        if _svc.project_has_active_conversation(project_id):
            raise ProjectMediaImportActive('项目仍有对话任务待完成或待恢复，请稍后再导出')
        if _svc.project_has_active_agent_task(project_id):
            raise ProjectMediaImportActive('项目仍有 Agent 任务正在执行，请稍后再导出')
        with PROJECT_LIBRARY_SWITCH_LOCK:
            store = _svc._main('FIREFLY_PROJECT_STORE')
        return store.export_fly_to(project_id, path)
