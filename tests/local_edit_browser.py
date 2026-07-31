"""Real headless-Chrome local-edit smoke acceptance at desktop and mobile viewports."""
from __future__ import annotations
import io, json, sys, tempfile, urllib.parse, subprocess
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from firefly_projects import ProjectStore
from tests import conversation_browser as harness
CLIENT_SCRIPT=ROOT/'tests/local_edit_browser_client.js'

def png(color):
    b=io.BytesIO();Image.new('RGBA',(12,8),color).save(b,'PNG');return b.getvalue()
def seed(store):
    p=store.create_project('Issue 15 Local Edit'); src=store.import_media(p['id'],png((10,20,30,40)),'source.png','image/png'); ref=store.import_media(p['id'],png((80,70,60,255)),'reference.png','image/png')
    store.apply_mutations(p['id'],[
      {'op':'upsert_node','node':{'id':'source-node','type':'smart-image','images':[{'asset_id':src['asset_id'],'kind':'image','name':'source.png'}]}},
      {'op':'upsert_node','node':{'id':'reference-node','type':'smart-image','images':[{'asset_id':ref['asset_id'],'kind':'image','name':'reference.png'}]}},
      {'op':'upsert_node','node':{'id':'prompt-node','type':'smart-prompt','text':'browser prompt'}},
      {'op':'upsert_node','node':{'id':'local-1','type':'local-edit','mode':'modify','aspect_ratio':'1:1','selection':[2,1,9,6],'prompt':'browser prompt','images':[]}},
      {'op':'upsert_edge','edge':{'from':'source-node','to':'local-1','kind':'input','role':'original'}},
      {'op':'upsert_edge','edge':{'from':'reference-node','to':'local-1','kind':'input','role':'references'}},
      {'op':'upsert_edge','edge':{'from':'prompt-node','to':'local-1','kind':'input','role':'prompt'}},
    ],base_revision=0);store.flush(p['id']);return p['id']
def launch(browser,server,pid,viewport):
    w,h=viewport;profile=Path(tempfile.mkdtemp(prefix='firefly-issue15-browser-'));before=len(server.results)
    url=f'{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(pid)}&project={urllib.parse.quote(pid)}&browserTest=1'
    proc=subprocess.Popen([str(browser),'--headless=new','--disable-gpu','--disable-background-networking','--disable-extensions','--no-first-run','--window-size=%d,%d'%(w,h),f'--user-data-dir={profile}',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=ROOT)
    try:return server.wait_for_result(before,timeout=60)
    finally:
      proc.terminate();proc.wait(timeout=5);harness.remove_profile(profile)
def main():
    browser=harness.find_browser();harness.CLIENT_SCRIPT=CLIENT_SCRIPT; runs=[]
    with tempfile.TemporaryDirectory(prefix='firefly-issue15-projects-') as root:
      for i,vp in enumerate(((1424,805),(390,844))):
        store=ProjectStore(Path(root)/f'p{i}',snapshot_delay=0);pid=seed(store);app=harness.load_production_app(Path(root)/f'r{i}',store)
        # Browser acceptance is offline by design; production defaults to the
        # routed online adapter and this explicit seam keeps the test hermetic.
        app.state.issue13_module.local_edit_adapter = app.state.issue13_module._offline_local_edit_adapter
        server=harness.ConversationBrowserServer(store,app);server.start()
        try:runs.append(launch(browser,server,pid,vp))
        finally:server.close();store.close()
    summary={'issue':15,'browser':str(browser),'viewports':[(1424,805),(390,844)],'runs':runs,'pass':len(runs)==2 and all(r.get('pass') for r in runs)};print(json.dumps(summary,ensure_ascii=False,indent=2));return 0 if summary['pass'] else 1
if __name__=='__main__':raise SystemExit(main())
