"""Verify generation completion broadcasts canvas_updated over the /ws/stats WebSocket (cross-loop audit)."""
from __future__ import annotations
import asyncio, base64, io, json, sys, tempfile, threading, time, urllib.parse
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import uvicorn
import websockets
from firefly_projects import ProjectStore
from tests import conversation_browser as harness

def png(color):
    b=io.BytesIO();Image.new('RGBA',(24,24),color).save(b,'PNG');return b.getvalue()

def fake_image(prompt, size=None, quality=None, model=None, references=None, provider_id=None, *args, **kwargs):
    payload={'prompt':prompt,'size':size or '1024x1024','quality':quality or 'high','model':model or 'nano-banana-pro','provider_id':provider_id}
    return [{'type':'b64','value':base64.b64encode(png((200,30,40,255))).decode(),'kind':'image','name':'ws-sync.png','mime_type':'image/png'}], payload

def build(store_root):
    store=ProjectStore(Path(store_root)/'projects',snapshot_delay=0)
    p=store.create_project('Issue WS Sync')
    store.apply_mutations(p['id'],[
      {'op':'upsert_node','node':{'id':'ws-image','type':'smart-image','images':[]}},
      {'op':'upsert_node','node':{'id':'ws-prompt','type':'smart-prompt','text':'ws sync prompt'}},
    ],base_revision=0);store.flush(p['id'])
    app=harness.load_production_app(Path(store_root)/'runtime',store)
    module=app.state.issue13_module
    module.generate_ai_image=fake_image
    providers=[{'id':'comfly','name':'comfly','protocol':'openai','base_url':'http://127.0.0.1:9','api_key':'ws-key','enabled':True,'primary':True,'image_models':['nano-banana-pro'],'image_model_capabilities':{'sizes':['1024x1024'],'aspect_ratios':['1:1'],'resolutions':['1k','2k','4k'],'max_reference_images':4},'video_models':[],'chat_models':[],'video_model_capabilities':{}}]
    module.load_api_providers=lambda *a,**k: providers
    module.provider_env_key_value=lambda pid,*a,**k: 'ws-key'
    module.AI_BASE_URL='http://127.0.0.1:9'
    return store, app, module, p['id']

async def main():
    with tempfile.TemporaryDirectory(prefix='firefly-ws-sync-') as root:
        store, app, module, pid = build(root)
        port=8791
        config=uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error')
        server=uvicorn.Server(config)
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(100):
            if server.started: break
            time.sleep(0.05)
        base=f'http://127.0.0.1:{port}'
        async with websockets.connect(f'ws://127.0.0.1:{port}/ws/stats?client_id=audit-1') as ws:
            first=await asyncio.wait_for(ws.recv(),timeout=5)
            got=[]
            import httpx
            current=store.canvas(pid)
            async with httpx.AsyncClient(base_url=base,headers={'Origin':base,'Host':f'127.0.0.1:{port}'}) as client:
                resp=await client.post(f'/api/project-library/{pid}/image-generation-tasks',json={
                    'prompt':'ws sync prompt','routing_mode':'manual','provider_id':'comfly','model':'nano-banana-pro',
                    'size':'1024x1024','resolution':'1k','ratio':'1:1','aspect_ratio':'1:1','n':1,'quality':'high',
                    'result_node_id':'ws-image','base_revision':current['revision'],
                })
                print('submit status:',resp.status_code,resp.text[:160])
                assert resp.status_code==202,(resp.status_code,resp.text)
                deadline=time.time()+40
                while time.time()<deadline:
                    msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=45))
                    if msg.get('type')=='canvas_updated':
                        got.append(msg)
                        break
                    got.append(msg)
            print('canvas_updated received:',json.dumps(got,ensure_ascii=False)[:300])
            assert any(m.get('type')=='canvas_updated' for m in got),'A-01: no canvas_updated broadcast after generation'
            print('WS_SYNC_OK')
        server.should_exit=True
        thread.join(timeout=10)
        store.close()

if __name__=='__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print('WS_SYNC_FAIL:',repr(exc))
        raise SystemExit(1)
