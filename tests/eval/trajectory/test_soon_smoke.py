import json
import subprocess
import sys

from tests.eval.trajectory.safety import REPO, isolated_env


def test_smoke_boots_the_real_candidate_and_records_only_its_single_result(tmp_path):
    root = tmp_path / 'owned'
    root.mkdir()
    (root / 'run-owner.json').write_text('{}', encoding='utf-8')
    env, _ = isolated_env(root)
    script = '''
import asyncio
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch
import httpx
from tests.eval.trajectory import soon_smoke
from tests.eval.trajectory.stub import ImageBudget, SafetyViolation

root = Path(sys.argv[1])
ImageBudget.create(root / 'image-budget.sqlite')
config = root / 'test-settings.json'
config.write_text(json.dumps([{'id': 'test-soon', 'name': 'Synthetic', 'enabled': True,
    'protocol': 'openai', 'image_request_mode': 'openai',
    'image_models': [soon_smoke.MODEL], 'base_url': 'https://api.soonstudio.ai/v1'}]), encoding='utf-8')
key_file = root / 'test.env'
key_file.write_text('API_PROVIDER_TEST_SOON_KEY=synthetic-offline-credential', encoding='utf-8')
seen = []

def fake_dns(host, port, *args, **kwargs):
    assert host == soon_smoke.HOST and port == 443
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.215.14', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]

async def provider(request):
    seen.append((request.method, str(request.url)))
    assert request.headers['authorization'] == 'Bearer synthetic-offline-credential'
    assert ImageBudget(root / 'image-budget.sqlite').snapshot()['smoke'] == 1
    addresses = await asyncio.get_running_loop().getaddrinfo(soon_smoke.HOST, 443)
    assert [row[4] for row in addresses] == [('93.184.215.14', 443)]
    try:
        socket.getaddrinfo('unapproved.invalid', 443)
    except SafetyViolation:
        pass
    else:
        raise AssertionError('The smoke must not unguard unrelated DNS')
    if request.method == 'POST':
        assert json.loads(request.content)['n'] == 1
        return httpx.Response(200, json={'data': {'task_id': 'task-offline'}})
    return httpx.Response(200, json={'status': 'completed', 'data': [{'url': 'https://result.invalid/private.png'}]})

with (patch.object(soon_smoke, '_SETTINGS', config), patch.object(soon_smoke, '_ENV_FILE', key_file),
      patch.object(socket, 'getaddrinfo', fake_dns),
      patch.object(soon_smoke.httpx, 'AsyncHTTPTransport', return_value=httpx.MockTransport(provider))):
    result = soon_smoke.smoke_child(root)
if result != 0:
    raise RuntimeError((root / 'smoke-result.json').read_text(encoding='utf-8'))
assert seen == [('POST', 'https://api.soonstudio.ai/v1/images/generations?async=true'),
                ('GET', 'https://api.soonstudio.ai/v1/images/tasks/task-offline')]
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(root)], cwd=REPO, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    report = (root / 'smoke-result.json').read_text(encoding='utf-8')
    evidence = report + (root / 'soon-events.jsonl').read_text(encoding='utf-8')
    assert all(value not in evidence for value in ('synthetic-offline-credential', 'task-offline', 'private.png'))
    assert json.loads(report) == {'images_returned': 1, 'asset_downloaded': False, 'result_kind': 'url',
        'provider': 'api.soonstudio.ai', 'model': 'gemini-3.1-flash-image-preview',
        'budget': {'normal': 0, 'campaign': 0, 'smoke': 1, 'total': 1}, 'post_dispatches': 1,
        'post_statuses': [200], 'poll_statuses': [200]}
