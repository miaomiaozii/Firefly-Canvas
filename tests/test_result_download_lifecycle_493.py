"""Normal application shutdown preserves accepted paid-result ownership and bytes."""
import asyncio
import copy
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

import main
from app.routers import api_project_library as api
from app.services import result_downloads as rd
from firefly_projects import ProjectStore
from tests.test_result_downloads_493 import fixture, checkpoint, network, png, Body
from tests.test_result_downloads_g_493 import admit_agent, remove_result
from tests.test_result_downloads_d2_493 import client


@pytest.mark.parametrize('owner', ['ordinary', 'agent', 'deleted-node'])
def test_normal_shutdown_reopens_and_range_resumes_original_result(fixture, owner):
    f = fixture
    if owner == 'agent':
        task_id, context = admit_agent(f)
    else:
        task_id = f.history['task_id']
    content = png(True)
    async def run():
        blocked, release = asyncio.Event(), asyncio.Event()
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield content[:131072]
                blocked.set()
                await release.wait()
                yield content[131072:]
        def respond(req):
            if len(f.requests) == 1:
                return httpx.Response(200, headers={'Content-Length': str(len(content)), 'ETag': '"paid-original"'}, stream=SlowBody())
            assert req.headers['Range'] == 'bytes=131072-'
            assert req.headers['If-Range'] == '"paid-original"'
            return httpx.Response(206, headers={'Content-Length': str(len(content)-131072),
                'Content-Range': f'bytes 131072-{len(content)-1}/{len(content)}', 'ETag': '"paid-original"'}, stream=Body(content[131072:]))
        network(f, respond)
        item_id = await checkpoint(f)
        if owner == 'deleted-node':
            remove_result(f)
            f.store.flush(f.project)
        queue = f.queue.load(f.history['id'])
        original = copy.deepcopy(f.store.canvas(f.project))
        partial = Path(f.store.image_result_download_path(f.project, 'result', task_id, f.history['id'], queue['generation'], item_id))
        worker = asyncio.create_task(f.queue.archive(f.history['id'], item_id))
        await asyncio.wait_for(blocked.wait(), 2)
        f.monkeypatch.setitem(main.CANVAS_TASKS, task_id, {'type': 'project-online-image', 'status': 'running',
            'project_id': f.project, 'result_node_id': 'result', 'created_at': time.time(), 'request_payload': {}})
        # Ordinary generation futures are drained by main; Agent/manual retrieval workers must also drain.
        if owner == 'ordinary':
            f.monkeypatch.setitem(main.PROJECT_IMAGE_GENERATION_FUTURES, task_id, worker)
        await asyncio.wait_for(main.shutdown_event(), 3)
        assert worker.done(), 'shutdown closed Store with a live download worker'
        assert worker.cancelled()
        assert partial.read_bytes() == content[:131072]
        assert not main.CANVAS_TASKS[task_id].get('cancel_requested')
        root = f.store.root
        reopened = ProjectStore(root, snapshot_delay=0)
        f.monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', reopened)
        fresh = rd.downloads(main)
        try:
            assert fresh is not f.queue and fresh.app.FIREFLY_PROJECT_STORE is reopened
            assert reopened.canvas(f.project) == original
            assert list(fresh.project_queues(f.project))[0]['history_id'] == f.history['id']
            result = await asyncio.wait_for(fresh.resume(f.project, f.history['id']), 3)
            assert result['status'] == 'succeeded'
            canvas = reopened.canvas(f.project)
            assert [n['id'] for n in canvas['nodes']] == [n['id'] for n in original['nodes']]
            history = next(h for h in canvas['history'] if h['id'] == f.history['id'])
            assert history['task_id'] == task_id and history['result_node_id'] == 'result'
            assert history['outputs'][0]['output_id'] == queue['items'][0]['output_id']
            assert Path(reopened.media_record(f.project, history['outputs'][0]['asset_id'])['path']).read_bytes() == content
            assert len(f.requests) == 2 and all(req.method == 'GET' for req in f.requests)
            assert not partial.exists()
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            reopened.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['no-checkpoint', 'invalid-owner', 'explicit-user-cancel'])
def test_shutdown_does_not_preserve_unaccepted_or_cancelled_generation(fixture, mode):
    f = fixture
    network(f, lambda req: pytest.fail('Cancellation must not retrieve'))
    async def run():
        if mode != 'no-checkpoint':
            await checkpoint(f)
        if mode == 'invalid-owner':
            queue = f.queue.load(f.history['id'])
            queue['generation'] = 'stale'
            f.queue.save(queue)
        task_id = f.history['task_id']
        f.monkeypatch.setitem(main.CANVAS_TASKS, task_id, {'type': 'project-online-image', 'status': 'running',
            'project_id': f.project, 'result_node_id': 'result', 'created_at': time.time(), 'request_payload': {}})
        if mode == 'explicit-user-cancel':
            main.CANVAS_TASKS[task_id]['request_payload'] = {'prompt': 'fixture', 'result_node_id': 'result', 'base_revision': f.store.canvas(f.project)['revision']}
            async with client() as c:
                response = await c.post(f'/api/project-library/{f.project}/image-generation-tasks/{task_id}/cancel')
                assert response.status_code == 200, response.text
        await api.shutdown_project_generation_tasks()
        canvas = f.store.canvas(f.project)
        assert main.CANVAS_TASKS[task_id]['status'] == 'cancelled'
        assert canvas['history'][0]['status'] == 'cancelled'
        assert not canvas['nodes'][0].get('pendingTasks')
        if mode != 'no-checkpoint':
            assert not list(f.queue.project_queues(f.project))
        assert not f.requests
    asyncio.run(run())


@pytest.mark.parametrize('threaded', [False, True])
def test_shutdown_drains_retrieval_store_write_before_close(fixture, threaded):
    f = fixture
    network(f, lambda req: httpx.Response(200, stream=Body(png())))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_call, original_close = main.project_store_call, f.store.close
    def call(method, *args, **kwargs):
        if method == 'import_image_result_download':
            entered.set()
            assert release.wait(5)
            try:
                return original_call(method, *args, **kwargs)
            finally:
                finished.set()
        return original_call(method, *args, **kwargs)
    def close():
        assert finished.is_set(), 'Store.close raced a cancelled retrieval write'
        return original_close()
    f.monkeypatch.setattr(main, 'project_store_call', call)
    f.monkeypatch.setattr(f.store, 'close', close)
    async def run():
        await checkpoint(f)
        async def retrieve():
            try:
                await f.queue.resume(f.project, f.history['id'])
            except asyncio.CancelledError:
                return 'paused'
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(asyncio.run, retrieve()) if threaded else asyncio.create_task(retrieve())
            assert await asyncio.to_thread(entered.wait, 3)
            shutdown = asyncio.create_task(main.shutdown_event())
            await asyncio.to_thread(time.sleep, .03)
            assert not shutdown.done() and not finished.is_set()
            release.set()
            await asyncio.wait_for(shutdown, 3)
            if threaded:
                assert worker.result(timeout=2) == 'paused'
            else:
                assert await worker == 'paused'
        assert finished.is_set() and not f.queue.active
        assert f.store.canvas(f.project)['history'][0]['id'] == f.history['id']
    try:
        asyncio.run(run())
    finally:
        release.set()
