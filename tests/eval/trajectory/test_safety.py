import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.eval.trajectory.backend import assert_isolated
from tests.eval.trajectory.live import policy, stop_tree
from tests.eval.trajectory.safety import (MARKER, REPO, clean_private, digest, e0_inputs, external_new,
                                         isolated_env, redact, safe_path, validate)
from tests.eval.trajectory.stub import SafetyViolation


def args(root):
    source = root / 'owned-copy'
    source.mkdir()
    (source / MARKER).write_text('owned-readonly-auth-copy', encoding='utf-8')
    (source / 'auth.json').write_text('{"synthetic":"not-a-credential"}', encoding='utf-8')
    fixtures = root / 'fixtures'
    fixtures.mkdir()
    (fixtures / 'manifest.json').write_text('{}', encoding='utf-8')
    native = root / 'bin/codex.exe'
    native.parent.mkdir()
    native.write_bytes(b'synthetic-not-executable')
    return argparse.Namespace(port=3194, model='explicit-model', timeout=60, max_provider_calls=60,
                              llm_base_url='https://model.example/v1', auth_home_source=source,
                              ack_owned_auth_copy=True, fixtures_manifest=fixtures / 'manifest.json',
                              native_cli=native, output_root=root / 'new-output')


@pytest.mark.parametrize('bad', [3000, 3100, 3193, 3300, 443])
def test_port_fail_closed(tmp_path, bad):
    options = args(tmp_path)
    options.port = bad
    with pytest.raises(ValueError, match='ports'):
        validate(options)
    assert not options.output_root.exists()


@pytest.mark.parametrize('change', ['model', 'endpoint', 'marker', 'auth', 'ack', 'existing', 'fixture_overlap', 'source_overlap'])
def test_no_silent_auth_model_or_isolation_fallback(tmp_path, change):
    options = args(tmp_path)
    if change == 'model':
        options.model = ' '
    elif change == 'endpoint':
        options.llm_base_url = 'https://secret@model.example/v1'
    elif change == 'marker':
        (options.auth_home_source / MARKER).unlink()
    elif change == 'auth':
        (options.auth_home_source / 'auth.json').unlink()
    elif change == 'ack':
        options.ack_owned_auth_copy = False
    elif change == 'existing':
        options.output_root.mkdir()
    elif change == 'fixture_overlap':
        options.output_root = options.fixtures_manifest.parent / 'output'
    else:
        options.output_root = options.auth_home_source / 'output'
    with pytest.raises((ValueError, FileNotFoundError)):
        validate(options)


@pytest.mark.parametrize('path', ['Z:/anything', 'D:/Users/user3000/test', 'D:/safe/../unsafe',
                                  'relative/path', 'D:/stashes/output', 'D:/Windows/output'])
def test_user_source_system_and_traversal_paths_rejected(path):
    with pytest.raises(ValueError):
        safe_path(Path(path))


def test_repo_and_existing_outputs_rejected(tmp_path):
    with pytest.raises(ValueError, match='NEW'):
        external_new(REPO / 'new-report.json')
    with pytest.raises(ValueError, match='NEW'):
        external_new(tmp_path)


def test_owned_environment_scrubs_credentials_and_cleanup_retains_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-secret')
    monkeypatch.setenv('OTHER_PROVIDER_TOKEN', 'synthetic-secret')
    root = tmp_path / 'owned'
    root.mkdir()
    env, mapping = isolated_env(root)
    assert 'OPENAI_API_KEY' not in env and 'OTHER_PROVIDER_TOKEN' not in env
    assert all(Path(env[key]).is_relative_to(root) for key in mapping)
    (root / 'run-owner.json').write_text('{}', encoding='utf-8')
    (root / 'retained.json').write_text('{}', encoding='utf-8')
    with monkeypatch.context() as context:
        for key, value in env.items():
            context.setenv(key, value)
        assert_isolated(root)
        context.setenv('FIREFLY_PROJECTS_ROOT', str(tmp_path / 'foreign'))
        with pytest.raises(SafetyViolation, match='Isolation'):
            assert_isolated(root)
    assert all(clean_private(root).values())
    assert (root / 'retained.json').exists()


def test_redacts_auth_but_preserves_counts_tool_ids_and_model():
    redacted = redact({'Authorization': 'private', 'api_key': 'secret', 'toolCallId': '123',
                       'model': 'explicit', 'text': 'Bearer secret-value', 'total_images': 15})
    assert redacted['Authorization'] == redacted['api_key'] == redacted['text'] == '[REDACTED]'
    assert redacted['toolCallId'] == '123' and redacted['total_images'] == 15


def test_manifest_only_reads_explicit_e0_inputs_and_verifies_hashes(tmp_path):
    root = tmp_path / 'fixtures'
    records = []
    for kind, relative in [('workbook', 'E0/original/plan.xlsx'), ('render', 'E0/renders/product.png')]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic')
        records.append({'case': 'E0', 'kind': kind, 'relative_path': relative, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'total_files': 2, 'files': records}), encoding='utf-8')
    assert e0_inputs(manifest, 'E0/renders/product.png') == tuple(records)
    with pytest.raises(ValueError, match='Explicit'):
        e0_inputs(manifest, 'E0/renders/not-listed.png')
    (root / 'E0/renders/product.png').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='integrity'):
        e0_inputs(manifest, 'E0/renders/product.png')


def test_policy_only_approves_literal_plan_and_one_safe_batch():
    tasks = [{'section': section} for section, n in [('主图方案', 8), ('高级A+方案', 7)] for _ in range(n)]
    assert policy({'tool': 'plan.apply', 'params': {'tasks': tasks}}, 'standard')
    assert not policy({'tool': 'plan.apply', 'params': {'tasks': tasks + tasks[:3]}}, 'standard')
    batch = {'tool': 'generate.request_batch', 'params': {'total_images': 15, 'fallback_models': ['trajectory-fallback'], 'max_retries': 2}}
    assert policy(batch, 'standard') and not policy(batch, 'autonomous')
    assert not policy({'tool': 'canvas.delete_node'}, 'standard')


def test_help_has_no_product_import_or_runtime_writes(tmp_path):
    code = ('import sys; from tools.run_agent_eval import main; '
            'sys.argv=["run_agent_eval.py", "live", "--help"]; '
            '\ntry: main()\nexcept SystemExit as e: assert e.code == 0\n'
            'assert "main" not in sys.modules; assert "app.core" not in sys.modules')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', FIREFLY_RUNTIME_ROOT=str(tmp_path / 'never-runtime'),
               FIREFLY_PROJECTS_ROOT=str(tmp_path / 'never-projects'))
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=REPO, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--auth-home-source' in result.stdout
    assert not (tmp_path / 'never-runtime').exists() and not (tmp_path / 'never-projects').exists()


def test_cleanup_stops_only_its_actual_child():
    child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(120)'])
    try:
        result = stop_tree(child)
        assert not result['remaining_owned_pids'] and child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
