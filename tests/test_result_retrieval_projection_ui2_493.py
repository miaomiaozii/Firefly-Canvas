"""Export real #493 persisted projections for the frontend VM, without starting a server."""
import asyncio
import json
import os
from pathlib import Path
from tests.test_result_downloads_493 import fixture, checkpoint
from tests.test_result_downloads_d2_493 import client
from tests.test_result_downloads_g_493 import admit_agent, remove_result
from firefly_projects import ProjectStore


def test_export_restarted_canonical_and_real_list_for_frontend(fixture, tmp_path):
    f = fixture
    async def run():
        task, context = admit_agent(f)
        await checkpoint(f)
        await f.queue.failed(f.history['id'], RuntimeError('offline projection failure'))
        f.store.flush(f.project)
        restarted = ProjectStore(f.store.root, snapshot_delay=0)
        try:
            canonical = restarted.open_project(f.project)['canvas']
            history = next(h for h in canonical['history'] if h['id'] == f.history['id'])
            node = next(n for n in canonical['nodes'] if n['id'] == 'result')
            assert history['task_id'] == task
            pending = next(item for item in node['pendingTasks'] if item['taskId'] == task)
            assert pending['download']['history_id'] == history['id']
            async with client() as c:
                listing = await c.get(f'/api/project-library/{f.project}/image-result-downloads')
                assert listing.status_code == 200, listing.text
                actual = await c.get(f'/api/canvases/{f.project}')
                assert actual.status_code == 200, actual.text
            payload = {'canvas': actual.json()['canvas'], 'durable': canonical, 'listing': listing.json(),
                       'agent': {'id': 'readonly-task-card', 'type': 'agent-task', 'task_id': task,
                                 'result_node_id': 'result', 'status': 'failed'}}
            assert 'PRIVATE-URL' not in json.dumps(payload)
            # External export is optional; CI must exercise the same contract in its own temp root.
            target = Path(os.environ.get('FIREFLY_UI2_QA', str(tmp_path))) / 'canonical-projection.json'
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        finally:
            restarted.close()
        remove_result(f)
        async with client() as c:
            state = await c.get(f'/api/project-library/{f.project}/image-result-downloads')
            assert state.status_code == 200 and state.json()['groups'][0]['node_exists'] is False
    asyncio.run(run())
