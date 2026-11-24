"""#493 proxy switch is deferred: no public schema, persistence or routing opt-in."""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


FLAG = 'image_bypass_system_proxy'


@pytest.fixture
def isolated_provider_app(tmp_path, monkeypatch):
    roots = {
        'FIREFLY_RUNTIME_ROOT': 'runtime', 'FIREFLY_PROJECTS_ROOT': 'projects',
        'HOME': 'home', 'USERPROFILE': 'home', 'APPDATA': 'appdata',
        'LOCALAPPDATA': 'localappdata', 'TMP': 'tmp', 'TEMP': 'tmp', 'TMPDIR': 'tmp',
        'PI_CODING_AGENT_DIR': 'pi', 'PI_CODING_AGENT_SESSION_DIR': 'pi-sessions',
        'CODEX_HOME': 'codex', 'XDG_CONFIG_HOME': 'xdg-config', 'XDG_CACHE_HOME': 'xdg-cache',
        'XDG_DATA_HOME': 'xdg-data', 'XDG_STATE_HOME': 'xdg-state',
    }
    for key, folder in roots.items():
        directory = tmp_path / folder
        directory.mkdir(exist_ok=True)
        monkeypatch.setenv(key, str(directory))
    for key in list(os.environ):
        upper = key.upper()
        if upper.endswith('_PROXY') or any(word in upper for word in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')):
            monkeypatch.delenv(key, raising=False)
    import main
    from app.services import providers

    provider_file = tmp_path / 'providers.json'
    monkeypatch.setattr(main, 'API_PROVIDERS_FILE', str(provider_file))
    monkeypatch.setattr(main, 'API_ENV_FILE', str(tmp_path / 'API' / '.env'))
    monkeypatch.setattr(main, 'provider_env_key_value', lambda provider_id: '')

    def forbidden(*args, **kwargs):
        raise AssertionError('No secrets, upstream or native calls in deferral tests')

    monkeypatch.setattr(providers.secret_store, 'decrypt_secret', forbidden)
    monkeypatch.setattr(providers.secret_store, 'encrypt_secret', forbidden)
    monkeypatch.setattr(providers.secret_store, 'decrypt_env_value', forbidden)
    monkeypatch.setattr(main, 'update_env_values', forbidden)
    with patch.object(main, 'startup_event', side_effect=forbidden), \
         patch.object(main, 'codex_status_core', side_effect=forbidden), \
         patch.object(main, 'gemini_cli_status_core', side_effect=forbidden):
        client = TestClient(main.app, base_url='http://127.0.0.1:3193',
                            headers={'origin': 'http://127.0.0.1:3193'})
        try:
            yield main, providers, client, provider_file
        finally:
            client.close()


def deferred_proxy_provider(provider_id='proxy-test', **overrides):
    return {'id': provider_id, 'name': provider_id, 'base_url': 'https://private-fake.invalid/v1',
            'protocol': 'openai', 'enabled': True, 'image_models': ['image-one'],
            'chat_models': ['chat-one'], 'video_models': ['video-one'],
            'video_model_capabilities': {'video-one': {'resolutions': ['1k', '2k', '4k']}}, **overrides}


def test_deferred_setting_absent_in_dtos_normalization_and_new_install(isolated_provider_app):
    main, providers, client, path = isolated_provider_app
    for model in (main.ApiProviderPayload, main.TestConnectionPayload):
        assert FLAG not in model.model_fields
        assert FLAG not in model.model_json_schema()['properties']
        assert FLAG not in model().model_dump()
    assert FLAG not in main.normalize_provider(deferred_proxy_provider())
    defaults = providers.load_api_providers()
    assert defaults and not path.exists()
    assert all(FLAG not in item for item in defaults)
    assert providers.load_api_providers(read_only=True) == defaults
    response = client.put('/api/providers', json=[deferred_proxy_provider()])
    assert response.status_code == 200
    assert FLAG not in response.json()['providers'][0]
    assert FLAG not in json.loads(path.read_text(encoding='utf-8'))[0]


@pytest.mark.parametrize('value', [True, False, 'false', 'true', '0', '1', '', 0, 1, None, [], {}])
def test_removed_boolean_and_non_boolean_values_cannot_roundtrip_or_persist(isolated_provider_app, value):
    main, providers, client, path = isolated_provider_app
    raw = deferred_proxy_provider(**{FLAG: value})
    original_env = dict(os.environ)
    for model in (main.ApiProviderPayload, main.TestConnectionPayload):
        assert FLAG not in model(**{FLAG: value}).model_dump()
        assert FLAG not in json.loads(model(**{FLAG: value}).model_dump_json())
    assert FLAG not in main.normalize_provider(raw)
    path.write_text(json.dumps([raw]), encoding='utf-8')
    before = path.read_bytes()
    for read_only in (False, True):
        assert FLAG not in providers.load_api_providers(read_only=read_only)[0]
        assert path.read_bytes() == before
    response = client.put('/api/providers', json=[raw])
    assert response.status_code == 200, response.text
    stored = json.loads(path.read_text(encoding='utf-8'))[0]
    loaded = providers.load_api_providers(read_only=True)[0]
    public = response.json()['providers'][0]
    assert stored == loaded
    assert public == client.get('/api/providers').json()['providers'][0]
    assert FLAG not in stored and FLAG not in public
    for key in ('image_models', 'chat_models', 'video_models'):
        assert public[key] == raw[key]
    assert dict(os.environ) == original_env


@pytest.mark.parametrize('value', [True, False, 'false', 1, None])
def test_direct_private_save_also_drops_experimental_flag(isolated_provider_app, value):
    _, providers, _, path = isolated_provider_app
    raw = deferred_proxy_provider(**{FLAG: value})
    providers.save_api_providers([raw])
    assert json.loads(path.read_text(encoding='utf-8')) == [{key: val for key, val in raw.items() if key != FLAG}]
    assert FLAG in raw


def test_mixed_providers_and_legacy_merge_never_reintroduce_setting(isolated_provider_app):
    _, providers, client, path = isolated_provider_app
    items = [deferred_proxy_provider('direct', **{FLAG: True}), deferred_proxy_provider('inherited'),
             deferred_proxy_provider('explicit-off', **{FLAG: False}),
             deferred_proxy_provider('native-cli', protocol='codex', **{FLAG: True}),
             deferred_proxy_provider('native-gemini', protocol='gemini-cli', **{FLAG: True})]
    response = client.put('/api/providers', json=items)
    assert response.status_code == 200, response.text
    for entries in (response.json()['providers'], providers.load_api_providers(),
                    json.loads(path.read_text(encoding='utf-8'))):
        assert all(FLAG not in item for item in entries)
    path.write_text(json.dumps([deferred_proxy_provider('legacy-ark', protocol='volcengine', **{FLAG: True})]), encoding='utf-8')
    loaded = {item['id']: item for item in providers.load_api_providers()}
    assert {'legacy-ark', 'volcengine'} <= loaded.keys()
    assert all(FLAG not in item for item in loaded.values())


def test_public_allowlist_drops_setting_and_redacts_inline_secrets(isolated_provider_app, monkeypatch):
    main, providers, _, _ = isolated_provider_app
    secret = 'fixture-only-493-key-123456789'
    monkeypatch.setattr(main, 'provider_env_key_value', lambda provider_id: secret)
    raw = deferred_proxy_provider(**{FLAG: True}, api_key=secret, wallet_api_key=secret,
                   volcengine_access_key_id=secret, volcengine_secret_access_key=secret,
                   clear_key=True, unexpected_secret=secret)
    public = providers.public_provider(raw)
    assert FLAG not in public
    assert public['has_key'] is True and public['key_preview'].endswith('6789')
    assert secret not in json.dumps(public)
    assert not {'api_key', 'wallet_api_key', 'volcengine_access_key_id',
                'volcengine_secret_access_key', 'clear_key', 'unexpected_secret'} & public.keys()
    assert public['video_model_capabilities'] == raw['video_model_capabilities']
    assert raw['api_key'] == secret


def test_connection_dto_drops_flag_without_triggering_probe(isolated_provider_app):
    main, _, client, path = isolated_provider_app
    assert FLAG not in main.TestConnectionPayload(**{FLAG: True}).model_dump()
    response = client.post('/api/providers/test-connection', json={FLAG: True})
    assert response.status_code == 400
    assert not path.exists()
