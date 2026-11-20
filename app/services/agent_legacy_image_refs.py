"""Read-only planning and post-admission staging for #460 saved mentions."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PureWindowsPath
import tempfile
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

from app.services.agent_errors import agent_error
from firefly_projects import ProjectStoreError
from firefly_withdrawn_refs import REATTACH_HINT, withdrawn_assets


_MAX_MEDIA_BYTES = 1024 * 1024 * 1024  # Match ProjectStore's import limit.


@dataclass(frozen=True)
class LegacyImage:
    url: str
    asset_id: str


def unavailable():
    return agent_error(400, 'REF_NOT_IN_PROJECT', 'Selected reference cannot be archived from a saved local mention',
                       '请先将参考图导入或归档到此项目，再重新挂图。')


def _withdrawn():
    return agent_error(409, 'REF_NOT_IN_PROJECT', 'A selected legacy reference was withdrawn', REATTACH_HINT)


def _source_path(app, store, locator):
    from app.routers.api_project_library import project_media_source_path

    url = urlsplit(locator)
    path = unquote(url.path)
    if url.scheme or url.netloc or url.fragment or '\\' in path or ':' in path or '\x00' in path:
        raise unavailable()
    parts = path.split('/')[1:]
    if any(not part or part in {'.', '..'} or part.endswith(('.', ' ')) or PureWindowsPath(part).is_reserved()
           for part in parts):
        raise unavailable()
    runtime = Path(app.RUNTIME_ROOT).resolve()
    if path.startswith('/assets/') and locator.startswith('/assets/'):
        roots = [(app.ASSETS_DIR, runtime / 'assets')]
    elif path.startswith('/output/') and locator.startswith('/output/'):
        roots = [(app.OUTPUT_OUTPUT_DIR, runtime / 'assets' / 'output'), (app.OUTPUT_DIR, runtime / 'output')]
    elif len(parts) >= 4 and parts[:2] == ['api', 'storage-files'] and locator.startswith('/api/storage-files/'):
        kinds = {
            'upload': (app.OUTPUT_INPUT_DIR, runtime / 'assets' / 'input'),
            'generated': (app.OUTPUT_OUTPUT_DIR, runtime / 'assets' / 'output'),
            'local': (app.LOCAL_UPLOAD_DIR, runtime / 'assets' / 'uploads'),
        }
        roots = [kinds[parts[2]]] if parts[2] in kinds else []
    else:
        roots = []
    # The browser resolver also knows configurable storage. Agent import is confined
    # to built-in application roots, including after symlink/junction resolution.
    if not roots or any(Path(configured).resolve() != builtin for configured, builtin in roots):
        raise unavailable()
    resolved = project_media_source_path(locator)
    if not resolved:
        raise unavailable()
    source = Path(resolved).resolve(strict=True)
    if (not any(source.is_relative_to(root) for _, root in roots)
            or source.is_relative_to(store.root.resolve()) or not source.is_file()):
        raise unavailable()
    return source


def inspect_legacy_images(app, store, canvas, locators):
    from app.routers.api_project_library import validate_project_media_source

    retired = withdrawn_assets(canvas)
    result = {}
    for locator in dict.fromkeys(locators):
        try:
            with _source_path(app, store, locator).open('rb') as source:
                if not 0 < os.fstat(source.fileno()).st_size <= _MAX_MEDIA_BYTES:
                    raise unavailable()
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
                source.seek(0)
                validate_project_media_source(source, 'image', '')
        except (OSError, ValueError, ProjectStoreError, HTTPException) as exc:
            raise unavailable() from exc
        if digest in retired:
            raise _withdrawn()
        result[locator] = LegacyImage(locator, digest)
    return result


def archive_legacy_images(app, store, project_id, canvas, images):
    from app.routers.api_project_library import validate_project_media_source

    if withdrawn_assets(canvas).intersection(image.asset_id for image in images):
        raise _withdrawn()
    imported = []
    with tempfile.TemporaryDirectory(prefix='ff-agent-ref-') as temp:
        staged = []
        # Recheck the approved fingerprint on the exact bytes we will archive.
        # Validate the full selection before importing any item.
        for position, image in enumerate(images):
            target = Path(temp) / str(position)
            digest = hashlib.sha256()
            size = 0
            try:
                source_path = _source_path(app, store, image.url)
                with source_path.open('rb') as source, target.open('wb') as dest:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        size += len(chunk)
                        if size > _MAX_MEDIA_BYTES:
                            raise unavailable()
                        digest.update(chunk)
                        dest.write(chunk)
                if digest.hexdigest() != image.asset_id:
                    raise unavailable()
                mime = validate_project_media_source(str(target), 'image', '')
            except (OSError, ValueError, ProjectStoreError, HTTPException) as exc:
                raise unavailable() from exc
            staged.append((target, image, source_path.name, mime))
        for target, image, name, mime in staged:
            if store.import_agent_reference_file(project_id, target, image.asset_id, name, mime, image.url):
                imported.append(image.asset_id)
    return imported
