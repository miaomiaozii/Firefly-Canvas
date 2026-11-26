"""Fail-closed boundaries shared by the live driver and its isolated server."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[3]
MARKER = '.trajectory-owned-auth'
SECRET_KEY = re.compile(r'(?i)authorization|cookie|password|secret|api.?key|access.?token|refresh.?token|id_token')
SECRET_TEXT = re.compile(r'(?i)(?:bearer\s+[^\s"\']+|(?:sk-|sess-)[\w-]{12,}|eyJ[\w-]+\.[\w-]+\.[\w-]+|(?:access_token|refresh_token|api_key|authorization)\s*[:=]\s*[^\s,;}]+)')
_PLAN_TASK_ID = re.compile(r'(task-[0-9a-f]{32})(-prompt|-image)?')
_TASK_REFERENCES = {'task_id', 'task_ids', 'planTaskId', 'excel_task_id', 'prompt_node', 'gen_node',
                    'id', 'node_id', 'node_ids', 'result_node_id', 'from', 'to', 'items',
                    'retry_root_id', 'retry_parent_id'}
RECORDING_KEY_ENV = 'FIREFLY_TRAJECTORY_RECORDING_KEY'
_KNOWN_SECRETS = set()


def register_owned_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY.search(key) and isinstance(child, str) and len(child) > 8:
                _KNOWN_SECRETS.add(child)
            else:
                register_owned_secrets(child)
    elif isinstance(value, list):
        for child in value:
            register_owned_secrets(child)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def overlaps(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def safe_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute path without traversal is required')
    # These are sources/user homes, never evaluation data, even on a different drive.
    if any(part.lower() in {'users', 'user3000', 'stashes', 'windows', 'program files', 'program files (x86)'}
           for part in path.parts) or str(path).lower().startswith(('z:', '\\\\', '//')):
        raise ValueError('Unsafe user/system/source path')
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Links/junctions are not permitted')
    return path.resolve()


def in_repository(path):
    return any((parent / '.git').exists() for parent in (path, *path.parents))


def external_new(path, forbidden=()):
    path = safe_path(path)
    if path.exists() or in_repository(path) or any(overlaps(path, item) for item in (REPO, Path('D:/Firefly-Canvas'), *forbidden)):
        raise ValueError('Output must be NEW, external and disjoint from inputs/repositories')
    if len(path.parts) < 3:
        raise ValueError('Output must not be a drive/top-level directory')
    return path


def validate(args):
    if args.port != 3194 and not 3200 <= args.port <= 3299:
        raise ValueError('Only dedicated 3194/32xx ports are permitted; never user port 3000')
    if not args.model.strip() or not 30 <= args.timeout <= 3600 or not 1 <= args.max_provider_calls <= 100:
        raise ValueError('Explicit model and bounded timeout/provider attempts required')
    manifest = safe_path(args.fixtures_manifest)
    fixture_root = manifest.parent
    source = None
    if getattr(args, 'strata', False):
        from tests.eval.trajectory.strata import ENDPOINT, MODEL

        if (args.llm_base_url != ENDPOINT or args.model != MODEL or args.auth_home_source is not None
                or args.ack_owned_auth_copy or args.ack_paid_model_approval or args.host != 'codex'
                or args.autonomy != 'standard' or args.scenario not in ('normal', 'campaign')):
            raise ValueError('Strata evaluation requires the exact approved endpoint/model, standard E0 and no auth')
        if in_repository(fixture_root):
            raise ValueError('Strata fixtures must be external')
    else:
        endpoint = urlparse(args.llm_base_url)
        if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.query or endpoint.fragment:
            raise ValueError('Explicit HTTPS LLM endpoint without inline credentials required')
        source = safe_path(args.auth_home_source)
        if in_repository(source) or in_repository(fixture_root) or any(overlaps(source, item) for item in (REPO, Path('D:/Firefly-Canvas'), fixture_root)):
            raise ValueError('Auth copy must be external and disjoint from source/fixtures')
        if not args.ack_owned_auth_copy or (source / MARKER).read_text(encoding='utf-8').strip() != 'owned-readonly-auth-copy':
            raise ValueError('Owned readonly auth-copy marker/acknowledgement required')
        auth = safe_path(source / 'auth.json')
        if not auth.is_file() or auth.stat().st_size == 0:
            raise ValueError('Owned auth copy is unavailable; no fallback')
    native = Path(args.native_cli)
    if not native.is_absolute() or not native.is_file():
        raise ValueError('Explicit native CLI is unavailable; no PATH fallback')
    forbidden = (fixture_root, native.parent) + ((source,) if source is not None else ())
    output = external_new(args.output_root, forbidden)
    return output, source, manifest


def e0_inputs(manifest_path, render):
    raw = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if raw.get('schema') != 1 or raw.get('total_files') != len(raw.get('files', [])):
        raise ValueError('Invalid fixture manifest')
    records = raw['files']
    paths = [r['relative_path'] for r in records]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate fixture paths')
    e0 = [r for r in records if r['case'] == 'E0']
    books = [r for r in e0 if r['kind'] == 'workbook']
    if len(books) != 1 or books[0]['relative_path'] != 'E0/original/plan.xlsx':
        raise ValueError('E0 workbook inventory mismatch')
    chosen = [r for r in e0 if r['kind'] == 'render' and r['relative_path'] == render]
    if len(chosen) != 1:
        raise ValueError('Explicit pre-provisioned E0 product render is required')
    for record in records:
        relative = record['relative_path'].replace('\\', '/')
        if Path(relative).is_absolute() or '..' in Path(relative).parts or ':' in relative or re.search('遮罩|cryptomatte|_mask|_ID\\.', relative, re.I):
            raise ValueError('Unsafe/mask manifest entry')
    for record in [books[0], chosen[0]]:
        path = safe_path(Path(manifest_path).parent / record['relative_path'])
        if not path.is_relative_to(Path(manifest_path).parent.resolve()):
            raise ValueError('Fixture escaped manifest directory')
        if path.stat().st_size != record['bytes'] or digest(path) != record['sha256']:
            raise ValueError('E0 fixture integrity mismatch')
    return books[0], chosen[0]


def redact(value, *, recording_key=None, _field=None):
    if isinstance(value, dict):
        return {redact(k): '[REDACTED]' if SECRET_KEY.search(k) else redact(v, recording_key=recording_key, _field=k)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        item_field = _field if _field in {'items', 'task_ids', 'node_ids'} else None
        return [redact(v, recording_key=recording_key, _field=item_field) for v in value]
    if isinstance(value, str):
        for secret in sorted(_KNOWN_SECRETS, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        # References derived from the same task must join across evidence without publishing its original ID.
        if _field in _TASK_REFERENCES and (match := _PLAN_TASK_ID.fullmatch(value)):
            task, suffix = match.groups()
            if recording_key is not None:
                alias = hmac.digest(recording_key, task.encode('ascii'), 'sha256').hex()[:32]
                return f'eval-plan-{alias}{suffix or ""}'
            if _field == 'task_id' and suffix is None:
                return value
        is_embedded_json = '"task_id"' in value or (recording_key is not None and _field in {'text', 'arguments'})
        if is_embedded_json and value.lstrip().startswith(('{', '[')):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                cleaned = redact(decoded, recording_key=recording_key)
                return value if cleaned == decoded else json.dumps(cleaned, ensure_ascii=False)
        return SECRET_TEXT.sub('[REDACTED]', value)
    return value


def save(path, value, *, recording_key=None):
    cleaned = redact(value, recording_key=recording_key)
    Path(path).write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def isolated_env(root):
    # Inherited provider credentials/proxies must never reach the application or model child.
    allowed = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PROGRAMFILES',
               'PROGRAMFILES(X86)', 'PROGRAMDATA', 'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)',
               'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS', 'LANG', 'LC_ALL'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    folders = {'FIREFLY_RUNTIME_ROOT': 'runtime', 'FIREFLY_PROJECTS_ROOT': 'projects', 'HOME': 'home',
               'USERPROFILE': 'home', 'APPDATA': 'roaming', 'LOCALAPPDATA': 'local', 'TEMP': 'tmp', 'TMP': 'tmp',
               'TMPDIR': 'tmp', 'PI_CODING_AGENT_DIR': 'private-pi', 'PI_CODING_AGENT_SESSION_DIR': 'private-pi/sessions',
               'CODEX_HOME': 'private-codex'}
    for key, relative in folders.items():
        folder = root / relative
        folder.mkdir(parents=True, exist_ok=True)
        env[key] = str(folder)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               FIREFLY_MAIN_MODULE='main', FIREFLY_CODEX_WARMUP='off',
               API_PROVIDER_TRAJECTORY_STUB_KEY='inert-not-a-provider-key')
    return env, folders


def clean_private(root):
    # Only a freshly-created run root is passed here; evidence is outside these private subdirectories.
    if not (root / 'run-owner.json').is_file():
        raise ValueError('Refusing cleanup without an owned run receipt')
    names = ('runtime', 'projects', 'home', 'roaming', 'local', 'tmp', 'private-pi', 'private-codex')
    for name in names:
        path = root / name
        if path.exists():
            if path.is_symlink():
                raise ValueError('Refusing linked cleanup')
            shutil.rmtree(path)
    return {name: not (root / name).exists() for name in names}
