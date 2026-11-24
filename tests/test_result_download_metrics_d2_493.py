"""Actual HTTPX observation seams, with in-process synthetic HTTP transports only."""
import asyncio
import json
import time
import httpx
from tests.test_result_downloads_493 import isolated_app, fixture, checkpoint, retrieval_result_png, Body, URL


def test_actual_stream_and_recovery_json_decode_metrics_persist(fixture):
    f=fixture
    from app.services import image_transport as transport
    from app.routers import api_project_library as api
    seen=[]
    def respond(request):
        seen.append(request)
        assert request.method=='GET'
        if 'images/tasks/' in request.url.path:
            body=json.dumps({'data':[{'url':URL,'expires_in':60}]}).encode()
            return httpx.Response(200,headers={'Content-Type':'application/json'},stream=Body(body))
        return httpx.Response(200,headers={'Content-Length':str(len(retrieval_result_png()))},stream=Body(retrieval_result_png()))
    def factory(*, client_factory=None, **kwargs):
        assert client_factory is None
        return transport.ImageTransportClient(transport=httpx.MockTransport(respond),
            telemetry_sink=transport._IMAGE_OBSERVATION_SINK.get(),**kwargs)
    f.monkeypatch.setattr(transport,'create_image_transport_client',factory)
    async def run():
        await checkpoint(f,expires_at=time.time()-1)
        assert (await f.queue.resume(f.project,f.history['id'],refresh=api._recover_project_image_result_link))['status']=='succeeded'
    asyncio.run(run())
    record=f.store.canvas(f.project)['history'][0]
    metrics=record['download']['items'][0]['transport_metrics']
    assert any(m['phase_tag']=='poll' and m['json_decode_ms'] is not None for m in metrics)
    assert any(m['phase_tag']=='download' and m['image_decode_ms'] is not None and m['image_output_bytes']==len(retrieval_result_png()) for m in metrics)
    streamed=[m for m in metrics if m['phase_tag']=='download' and m['body_complete']]
    assert streamed and streamed[-1]['response_raw_entity_bytes']==len(retrieval_result_png())
    assert streamed[-1]['response_decoded_yielded_bytes']==len(retrieval_result_png())
    assert all(m['physical_ttfb_ms'] is None and m['base64_decode_ms'] is None for m in metrics)
    assert 'PRIVATE-URL' not in json.dumps(metrics)
    assert len(seen)==2
    from firefly_projects import ProjectStore
    f.store.flush(f.project)
    f.store.close()
    restarted=ProjectStore(f.store.root,snapshot_delay=0)
    try:
        assert restarted.canvas(f.project)['history'][0]['download']['items'][0]['transport_metrics']==metrics
    finally:
        restarted.close()
