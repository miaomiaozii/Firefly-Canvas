import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


STATUS = '/api/global-settings/chat-dock'
PREFERENCES = '/api/global-settings/preferences'
LOOPBACK = 'http://127.0.0.1:8765'
UNAVAILABLE = {'available': False, 'version': None, 'directory': None}


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'GLOBAL_SETTINGS_FILE', str(tmp_path / 'global_settings.json'))
    monkeypatch.delenv('FIREFLY_CHAT_DOCK_EXTENSION_DIR', raising=False)


@pytest.fixture
def browser():
    return TestClient(main.app, base_url=LOOPBACK, client=('127.0.0.1', 50000),
                      headers={'Referer': LOOPBACK + '/static/global-settings.html'})


def test_repository_bundle_is_read_when_no_override_is_configured(browser):
    directory = Path(main.__file__).resolve().parent / 'extensions' / 'chat-dock'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    response = browser.get(STATUS)
    assert response.status_code == 200
    assert response.json() == {'available': True, 'version': manifest['version'], 'directory': str(directory)}


def test_configured_bundle_is_read(browser, monkeypatch, tmp_path):
    directory = tmp_path / 'portable bundle' / 'env' / 'chat-dock'
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text('{"version":"12.3.4"}', encoding='utf-8')
    monkeypatch.setenv('FIREFLY_CHAT_DOCK_EXTENSION_DIR', str(directory))
    assert browser.get(STATUS).json() == {'available': True, 'version': '12.3.4', 'directory': str(directory.resolve())}


@pytest.mark.parametrize('manifest', [
    None, b'\xff', b'{', b'[]', b'{}', b'{"version":1}', b'{"version":"release"}',
    b'{"version":"0.0"}', b'{"version":"01.2"}', b'{"version":"65536"}',
])
def test_invalid_override_does_not_fall_back(browser, monkeypatch, tmp_path, manifest):
    directory = tmp_path / 'override'
    monkeypatch.setenv('FIREFLY_CHAT_DOCK_EXTENSION_DIR', str(directory))
    if manifest is not None:
        directory.mkdir()
        (directory / 'manifest.json').write_bytes(manifest)
    response = browser.get(STATUS)
    assert response.status_code == 200
    assert response.json() == UNAVAILABLE


def test_explicit_blank_override_is_unavailable(browser, monkeypatch):
    monkeypatch.setenv('FIREFLY_CHAT_DOCK_EXTENSION_DIR', ' ')
    assert browser.get(STATUS).json() == UNAVAILABLE


@pytest.mark.parametrize('headers', [{}, {'Origin': 'http://other.test'}, {'Origin': 'null', 'Referer': LOOPBACK}])
def test_chat_dock_bundle_requires_explicit_same_origin(headers):
    client = TestClient(main.app, base_url=LOOPBACK)
    assert client.get(STATUS, headers=headers).status_code == 403


def test_chat_dock_and_other_preferences_are_updated_independently(browser):
    path = Path(main.GLOBAL_SETTINGS_FILE)
    assert browser.get(PREFERENCES).json()['preferences']['chat_dock_enabled'] is False
    assert not path.exists()
    path.write_text('{"theme":"light","chat_dock_enabled":"true","upstream_direct":false,"other":42}', encoding='utf-8')
    assert browser.get(PREFERENCES).json()['preferences']['chat_dock_enabled'] is False

    enabled = browser.patch(PREFERENCES, json={'chat_dock_enabled': True}).json()['preferences']
    assert enabled['chat_dock_enabled'] is True
    assert enabled['upstream_direct'] is False
    assert enabled['theme'] == 'light'
    assert main.save_global_preferences('dark', 'zh')['chat_dock_enabled'] is True
    changed = browser.patch(PREFERENCES, json={'theme': 'light', 'upstream_direct': True}).json()['preferences']
    assert changed['chat_dock_enabled'] is True
    assert changed['upstream_direct'] is True
    assert browser.get(PREFERENCES).json()['preferences'] == changed

    disabled = browser.patch(PREFERENCES, json={'chat_dock_enabled': False}).json()['preferences']
    assert disabled['chat_dock_enabled'] is False
    assert disabled['upstream_direct'] is True
    assert json.loads(path.read_text(encoding='utf-8')) == {**disabled, 'other': 42}


@pytest.mark.parametrize('flag', [None, 'false', 'true', 0, 1, []])
def test_chat_dock_patch_rejects_non_booleans_without_writing(browser, flag):
    assert browser.patch(PREFERENCES, json={'chat_dock_enabled': flag}).status_code == 422
    assert not Path(main.GLOBAL_SETTINGS_FILE).exists()


OPEN_FOLDER = '/api/global-settings/chat-dock/open-folder'


@pytest.fixture
def opened(monkeypatch):
    import app.routers.api_global_settings as settings_router

    calls = []
    monkeypatch.setattr(settings_router, '_open_in_file_manager', calls.append)
    return calls


def test_open_folder_opens_the_resolved_bundle_for_loopback_clients(browser, monkeypatch, tmp_path, opened):
    directory = tmp_path / 'env' / 'chat-dock'
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text('{"version":"0.2.0"}', encoding='utf-8')
    monkeypatch.setenv('FIREFLY_CHAT_DOCK_EXTENSION_DIR', str(directory))
    response = browser.post(OPEN_FOLDER)
    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert opened == [directory.resolve()]


def test_open_folder_refuses_non_loopback_clients_even_when_same_origin(opened):
    lan = TestClient(main.app, base_url=LOOPBACK, client=('192.168.1.20', 50000),
                     headers={'Referer': LOOPBACK + '/static/global-settings.html'})
    response = lan.post(OPEN_FOLDER)
    assert response.status_code == 403
    assert opened == []


def test_open_folder_reports_a_missing_bundle(browser, monkeypatch, tmp_path, opened):
    monkeypatch.setenv('FIREFLY_CHAT_DOCK_EXTENSION_DIR', str(tmp_path / 'missing'))
    assert browser.post(OPEN_FOLDER).status_code == 404
    assert opened == []


def test_open_folder_requires_same_origin(opened):
    assert TestClient(main.app, base_url=LOOPBACK, client=('127.0.0.1', 50000)).post(OPEN_FOLDER).status_code == 403
    assert opened == []
