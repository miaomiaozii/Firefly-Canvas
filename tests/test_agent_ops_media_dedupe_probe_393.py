"""Regression probe for platform differences in file metadata APIs."""
import asyncio
import hashlib
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

import main
from app.routers import api_agent_tools as routes
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


def test_duplicate_source_with_different_handle_and_path_ctime(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('probe')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    source = store.root / project / 'input.png'
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(output, format='PNG')
    source.write_bytes(output.getvalue())
    original_fstat = os.fstat

    def fstat(fd):
        stat = original_fstat(fd)
        if sys._getframe(1).f_code.co_name != 'upload_media_core':
            return stat
        # On Windows, handle and path stat can expose different ctime values.
        return SimpleNamespace(st_dev=stat.st_dev, st_ino=stat.st_ino, st_size=stat.st_size,
                               st_mtime_ns=stat.st_mtime_ns, st_ctime_ns=source.stat().st_ctime_ns + 1)

    async def upload():
        with agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn', tool='media.upload', args={}):
            return await routes.upload_media_core(project, str(source))

    try:
        first = asyncio.run(upload())
        monkeypatch.setattr(os, 'fstat', fstat)
        second = asyncio.run(upload())
        assert second['asset_id'] == first['asset_id']
        assert 'op_id' not in second
        assert len(list_operations(OpsDb(store.root), project)) == 1
    finally:
        store.close()


def _changed(stat, **changes):
    values = {name: getattr(stat, name) for name in
              ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('scenario', ['handle_ctime', 'path_ctime', 'path_id',
                                      'samefile_false', 'samefile_race', 'symlink_race'])
def test_unstable_source_does_not_take_dedupe_shortcut(tmp_path, monkeypatch, scenario):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('probe')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    source = (store.root / project / 'input.png').resolve()
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(output, format='PNG')
    source.write_bytes(output.getvalue())

    async def upload():
        with agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn', tool='media.upload', args={}):
            return await routes.upload_media_core(project, str(source))

    try:
        first = asyncio.run(upload())
        original_fstat, original_stat = os.fstat, Path.stat
        original_samefile, original_resolve = os.path.samefile, Path.resolve
        handle_calls = path_calls = 0
        samefile_done = False

        def fstat(fd):
            nonlocal handle_calls
            stat = original_fstat(fd)
            if sys._getframe(1).f_code.co_name == 'upload_media_core':
                handle_calls += 1
                if scenario == 'handle_ctime' and handle_calls == 2:
                    return _changed(stat, st_ctime_ns=stat.st_ctime_ns + 1)
            return stat

        def path_stat(path, *args, **kwargs):
            nonlocal path_calls
            stat = original_stat(path, *args, **kwargs)
            if path == source and sys._getframe(1).f_code.co_name == 'upload_media_core':
                path_calls += 1
                if path_calls == 3 and scenario in ('path_ctime', 'path_id', 'samefile_race'):
                    assert scenario != 'samefile_race' or samefile_done
                    field = 'st_ino' if scenario == 'path_id' else 'st_ctime_ns'
                    return _changed(stat, **{field: getattr(stat, field) + 1})
            return stat

        def samefile(*args):
            nonlocal samefile_done
            result = original_samefile(*args)
            samefile_done = True
            return False if scenario == 'samefile_false' else result

        def resolve(path, *args, **kwargs):
            result = original_resolve(path, *args, **kwargs)
            if path == source and samefile_done and scenario == 'symlink_race':
                return source.parent / 'different-target.png'
            return result

        monkeypatch.setattr(os, 'fstat', fstat)
        monkeypatch.setattr(Path, 'stat', path_stat)
        monkeypatch.setattr(os.path, 'samefile', samefile)
        monkeypatch.setattr(Path, 'resolve', resolve)
        if scenario == 'symlink_race':
            with pytest.raises(HTTPException) as error:
                asyncio.run(upload())
            assert error.value.status_code == 404
            assert len(list_operations(OpsDb(store.root), project)) == 1
        else:
            second = asyncio.run(upload())
            assert second['asset_id'] == first['asset_id']
            assert 'op_id' in second
            assert len(list_operations(OpsDb(store.root), project)) == 2
    finally:
        store.close()


def test_mutation_during_hash_does_not_take_dedupe_shortcut(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('probe')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    source = store.root / project / 'input.png'
    red, blue = io.BytesIO(), io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(red, format='PNG')
    Image.new('RGB', (8, 8), 'blue').save(blue, format='PNG')
    source.write_bytes(red.getvalue())

    async def upload():
        with agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn', tool='media.upload', args={}):
            return await routes.upload_media_core(project, str(source))

    try:
        first = asyncio.run(upload())
        original_sha256 = hashlib.sha256
        mutated = False

        def sha256(*args, **kwargs):
            digest = original_sha256(*args, **kwargs)
            if sys._getframe(1).f_code.co_name != 'upload_media_core':
                return digest

            class MutatingDigest:
                def update(self, chunk):
                    nonlocal mutated
                    digest.update(chunk)
                    timestamp = source.stat().st_mtime_ns
                    source.write_bytes(blue.getvalue())
                    os.utime(source, ns=(timestamp + 2_000_000_000, timestamp + 2_000_000_000))
                    mutated = True

                def hexdigest(self):
                    return digest.hexdigest()

            return MutatingDigest()

        monkeypatch.setattr(hashlib, 'sha256', sha256)
        second = asyncio.run(upload())
        assert mutated and second['asset_id'] != first['asset_id']
        assert 'op_id' in second
        assert len(list_operations(OpsDb(store.root), project)) == 2
    finally:
        store.close()


def test_replacement_before_open_does_not_take_dedupe_shortcut(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('probe')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    source = (store.root / project / 'input.png').resolve()
    replacement = source.with_name('replacement.png')
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(output, format='PNG')
    source.write_bytes(output.getvalue())
    replacement.write_bytes(output.getvalue())
    timestamp = source.stat().st_mtime_ns
    os.utime(replacement, ns=(timestamp, timestamp))

    async def upload():
        with agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn', tool='media.upload', args={}):
            return await routes.upload_media_core(project, str(source))

    try:
        first = asyncio.run(upload())
        original_open = Path.open
        replaced = False

        def open_with_replacement(path, *args, **kwargs):
            nonlocal replaced
            if path == source and not replaced:
                replaced = True
                os.replace(replacement, source)
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', open_with_replacement)
        second = asyncio.run(upload())
        assert replaced and second['asset_id'] == first['asset_id']
        assert 'op_id' in second
        assert len(list_operations(OpsDb(store.root), project)) == 2
    finally:
        store.close()
