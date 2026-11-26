"""A real private server bootstrap, deliberately no native prompt/auth/model."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from tests.eval.trajectory.live import stop_tree
from tests.eval.trajectory.safety import RECORDING_KEY_ENV, REPO, clean_private, isolated_env, save
from tests.eval.trajectory.stub import tiny_png


def test_private_server_bootstraps_fenced_without_native_or_auth(tmp_path):
    root = tmp_path / 'e2e-tabbit-405-bootstrap'
    root.mkdir()
    env, _ = isolated_env(root)
    save(root / 'run-owner.json', {'synthetic': True})
    render = root / 'synthetic-product.png'
    render.write_bytes(tiny_png())
    with socket.socket() as probe:
        for port in range(3281, 3299):
            try:
                probe.bind(('127.0.0.1', port))
                break
            except OSError:
                continue
        else:
            raise AssertionError('No dedicated test port available')
    recording_key = 'ab' * 32
    env.update(FIREFLY_PORT=str(port), FIREFLY_AGENT_API_BASE=f'http://127.0.0.1:{port}',
               **{RECORDING_KEY_ENV: recording_key})
    save(root / 'config.json', {'root': str(root), 'scenario': 'normal', 'port': port,
                               'max_provider_calls': 3, 'native_cli': sys.executable, 'model': 'synthetic-not-run',
                               'render_path': str(render), 'autonomy': 'standard', 'llm_base_url': 'https://unused.invalid'})
    log = (root / 'bootstrap.log').open('w', encoding='utf-8')
    child = subprocess.Popen([sys.executable, '-B', '-m', 'tests.eval.trajectory.backend', str(root / 'config.json')],
                             cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False) as client:
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise AssertionError((root / 'bootstrap.log').read_text(encoding='utf-8'))
                if (root / 'seed.json').exists():
                    try:
                        if client.get(f'http://127.0.0.1:{port}/', timeout=1).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                time.sleep(.1)
            else:
                raise AssertionError('Private server failed readiness deadline')
        seed = json.loads((root / 'seed.json').read_text(encoding='utf-8'))
        assert seed['preflight']['installed']
        assert not (root / 'private-codex/auth.json').exists()
        rows = [json.loads(line) for line in (root / 'backend.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [r['kind'] for r in rows] == ['preflight']
        assert all(recording_key.encode() not in path.read_bytes() for path in root.iterdir() if path.is_file())
    finally:
        result = stop_tree(child)
        log.close()
        assert not result['remaining_owned_pids']
        assert all(clean_private(root).values())
