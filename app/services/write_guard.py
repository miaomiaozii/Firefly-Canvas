"""Guard against test runs clobbering real configuration files.

Tests execute inside the same repository checkout, and a test that calls
``update_env_values`` or ``save_api_providers`` without first redirecting
``API_ENV_FILE`` / ``API_PROVIDERS_FILE`` to a temporary location would
overwrite the real API keys with test fixtures. This module detects that
situation and refuses the write, which surfaces the mistake loudly instead
of silently destroying user configuration.

A writer is considered "safe" when either:
- ``FIREFLY_TEST_MODE`` is not set (normal application run), or
- the target file does not live inside the repository's own config
  directory (tests redirect to temp paths).
"""
import os

_REPO_CONFIG_MARKERS = (
    '/api/.env',
    '/data/api_providers.json',
    '/data/global_settings.json',
)


def _normalize(path):
    try:
        return os.path.normpath(os.path.abspath(os.path.realpath(path))).replace('\\', '/')
    except (OSError, ValueError, TypeError):
        return str(path or '').replace('\\', '/')


def _repo_root():
    # app/services/write_guard.py -> repo root
    current = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(current, '..', '..')).replace('\\', '/')


def ensure_writable_config(path):
    """Raise RuntimeError when a test-mode write targets a real config file."""
    if not os.environ.get('FIREFLY_TEST_MODE'):
        return
    normalized = _normalize(path)
    root = _repo_root()
    if not normalized.lower().startswith(root.lower() + '/'):
        return
    lower = normalized.lower()
    for marker in _REPO_CONFIG_MARKERS:
        if lower.endswith(marker):
            raise RuntimeError(
                f'refusing to overwrite real config file {path} while FIREFLY_TEST_MODE is set; '
                'tests must redirect API_ENV_FILE / API_PROVIDERS_FILE to a temporary location'
            )


def test_mode_active():
    return bool(os.environ.get('FIREFLY_TEST_MODE'))
