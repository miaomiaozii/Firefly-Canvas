"""Provider-scoped image proxy settings, without upstream or native host calls."""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


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
        if (upper.endswith('_PROXY') or any(word in upper for word in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL'))):
            monkeypatch.delenv(key, raising=False)
    import main
    from app.services import providers

    provider_file = tmp_path / 'providers.json'
    env_file = tmp_path / 'API' / '.env'
    monkeypatch.setattr(main, 'API_PROVIDERS_FILE', str(provider_file))
    monkeypatch.setattr(main, 'API_ENV_FILE', str(env_file))
    monkeypatch.setattr(main, 'provider_env_key_value', lambda provider_id: '')
    with patch.object(main, 'startup_event', side_effect=AssertionError('No native startup')), \
         patch.object(main, 'codex_status_core', side_effect=AssertionError('No CLI probes')), \
         patch.object(main, 'gemini_cli_status_core', side_effect=AssertionError('No CLI probes')):
        client = TestClient(main.app, base_url='http://127.0.0.1:3193',
                            headers={'origin': 'http://127.0.0.1:3193'})
        try:
            yield main, providers, client, provider_file
        finally:
            client.close()


def provider(provider_id='proxy-test', **overrides):
    return {'id': provider_id, 'name': provider_id, 'base_url': 'https://private-fake.invalid/v1',
            'protocol': 'openai', 'enabled': True, 'image_models': ['image-one'],
            'chat_models': ['chat-one'], 'video_models': ['video-one'],
            'video_model_capabilities': {'video-one': {'resolutions': ['1k', '2k', '4k']}}, **overrides}


def test_missing_setting_defaults_false_in_dtos_normalization_and_new_install(isolated_provider_app):
    main, providers, client, path = isolated_provider_app
    assert main.ApiProviderPayload(id='proxy-test').image_bypass_system_proxy is False
    assert main.TestConnectionPayload().image_bypass_system_proxy is False
    for model in (main.ApiProviderPayload, main.TestConnectionPayload):
        assert model.model_json_schema()['properties']['image_bypass_system_proxy'] == {
            'default': False, 'title': 'Image Bypass System Proxy', 'type': 'boolean'}
    assert main.normalize_provider(provider())['image_bypass_system_proxy'] is False
    defaults = providers.load_api_providers()
    assert defaults and not path.exists()
    assert all(item['image_bypass_system_proxy'] is False for item in defaults)
    assert providers.load_api_providers(read_only=True) == defaults
    response = client.put('/api/providers', json=[provider()])
    assert response.status_code == 200
    assert response.json()['providers'][0]['image_bypass_system_proxy'] is False
    assert json.loads(path.read_text(encoding='utf-8'))[0]['image_bypass_system_proxy'] is False


@pytest.mark.parametrize('flag', [True, False])
def test_exact_route_disk_load_public_roundtrip(isolated_provider_app, flag):
    main, providers, client, path = isolated_provider_app
    original_env = dict(os.environ)
    response = client.put('/api/providers', json=[provider(image_bypass_system_proxy=flag)])
    assert response.status_code == 200, response.text
    public = response.json()['providers'][0]
    stored = json.loads(path.read_text(encoding='utf-8'))[0]
    loaded = providers.load_api_providers(read_only=True)[0]
    fetched = client.get('/api/providers').json()['providers'][0]
    assert stored == loaded
    assert public == fetched == {**stored, 'has_key': False, 'key_preview': '',
                                 'key_env': main.provider_key_env('proxy-test')}
    assert public['image_bypass_system_proxy'] is flag
    assert public['image_models'] == ['image-one']
    assert public['chat_models'] == ['chat-one']
    assert public['video_models'] == ['video-one']
    assert dict(os.environ) == original_env


def test_mixed_providers_remain_independent_and_cli_does_not_opt_in(isolated_provider_app):
    main, providers, client, path = isolated_provider_app
    items = [provider('direct', image_bypass_system_proxy=True), provider('inherited'),
             provider('explicit-off', image_bypass_system_proxy=False),
             provider('native-cli', protocol='codex', image_bypass_system_proxy=True),
             provider('native-gemini', protocol='gemini-cli', image_bypass_system_proxy=True)]
    response = client.put('/api/providers', json=items)
    assert response.status_code == 200, response.text
    expected = {'direct': True, 'inherited': False, 'explicit-off': False,
                'native-cli': False, 'native-gemini': False}
    for entries in (response.json()['providers'], providers.load_api_providers(),
                    json.loads(path.read_text(encoding='utf-8'))):
        assert {item['id']: item['image_bypass_system_proxy'] for item in entries} == expected


@pytest.mark.parametrize('value', ['false', 'true', '0', '1', '', 0, 1, None, [], {}])
def test_non_boolean_disk_values_never_enable_bypass_and_dtos_reject_them(isolated_provider_app, value):
    main, providers, client, path = isolated_provider_app
    raw = provider(image_bypass_system_proxy=value)
    assert main.normalize_provider(raw)['image_bypass_system_proxy'] is False
    for model in (main.ApiProviderPayload, main.TestConnectionPayload):
        with pytest.raises(ValidationError):
            model(image_bypass_system_proxy=value)
    path.write_text(json.dumps([raw]), encoding='utf-8')
    assert providers.load_api_providers()[0]['image_bypass_system_proxy'] is False
    before = path.read_bytes()
    response = client.put('/api/providers', json=[raw])
    assert response.status_code == 422
    assert path.read_bytes() == before
    assert client.post('/api/providers/test-connection', json={'image_bypass_system_proxy': value}).status_code == 422


def test_legacy_protocol_merge_injects_explicit_false_for_new_provider(isolated_provider_app):
    main, providers, client, path = isolated_provider_app
    path.write_text(json.dumps([provider('legacy-ark', protocol='volcengine',
                                         image_bypass_system_proxy=True)]), encoding='utf-8')
    loaded = {item['id']: item for item in providers.load_api_providers()}
    assert loaded['legacy-ark']['image_bypass_system_proxy'] is True
    assert loaded['volcengine']['image_bypass_system_proxy'] is False


def test_public_provider_allowlist_retains_setting_and_redacts_inline_secrets(isolated_provider_app, monkeypatch):
    main, providers, client, path = isolated_provider_app
    secret = 'fixture-only-493-key-123456789'
    monkeypatch.setattr(main, 'provider_env_key_value', lambda provider_id: secret)
    raw = provider(image_bypass_system_proxy=True, api_key=secret, wallet_api_key=secret,
                   volcengine_access_key_id=secret, volcengine_secret_access_key=secret,
                   clear_key=True, unexpected_secret=secret)
    public = providers.public_provider(raw)
    assert public['image_bypass_system_proxy'] is True
    assert public['has_key'] is True
    assert public['key_preview'].endswith('6789')
    assert secret not in json.dumps(public)
    assert not {'api_key', 'wallet_api_key', 'volcengine_access_key_id',
                'volcengine_secret_access_key', 'clear_key', 'unexpected_secret'} & public.keys()
    assert public['video_model_capabilities'] == raw['video_model_capabilities']
    assert raw['api_key'] == secret


def test_connection_dto_retains_boolean_without_claiming_image_transport_test(isolated_provider_app):
    main, providers, client, path = isolated_provider_app
    payload = main.TestConnectionPayload(base_url='https://private-fake.invalid',
                                         image_bypass_system_proxy=True)
    assert payload.model_dump()['image_bypass_system_proxy'] is True
    # An invalid address cannot trigger a real probe, even when the image setting is on.
    response = client.post('/api/providers/test-connection', json={'image_bypass_system_proxy': True})
    assert response.status_code == 400
    assert not path.exists()
