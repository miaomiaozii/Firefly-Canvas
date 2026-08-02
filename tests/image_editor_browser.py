"""Real headless-Chrome acceptance for the per-node image editor (preview/crop/outpaint/mask/brush/grid)."""
from __future__ import annotations
import io, json, sys, tempfile, urllib.parse, subprocess
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from firefly_projects import ProjectStore
from tests import conversation_browser as harness
CLIENT_SCRIPT=ROOT/'tests/image_editor_browser_client.js'

def png(color):
    b=io.BytesIO();Image.new('RGBA',(64,64),color).save(b,'PNG');return b.getvalue()

def seed(store):
    p=store.create_project('Issue Image Editor')
    src=store.import_media(p['id'],png((12,34,56,255)),'editable.png','image/png')
    store.apply_mutations(p['id'],[
      {'op':'upsert_node','node':{'id':'edit-node','type':'smart-image','images':[{'asset_id':src['asset_id'],'kind':'image','name':'editable.png'}]}},
      {'op':'upsert_node','node':{'id':'prompt-node','type':'smart-prompt','text':'editor prompt'}},
    ],base_revision=0);store.flush(p['id']);return p['id']

def launch(browser,server,pid,viewport):
    w,h=viewport;profile=Path(tempfile.mkdtemp(prefix='firefly-edit-browser-'))
    before=len(server.results)
    url=f'{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(pid)}&project={urllib.parse.quote(pid)}&browserTest=1'
    proc=subprocess.Popen([str(browser),'--headless=new','--disable-gpu','--disable-background-networking','--disable-extensions','--no-first-run','--window-size=%d,%d'%(w,h),f'--user-data-dir={profile}',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=ROOT)
    try:return server.wait_for_result(before,timeout=60)
    finally:
      proc.terminate();proc.wait(timeout=5);harness.remove_profile(profile)

def main():
    browser=harness.find_browser();harness.CLIENT_SCRIPT=CLIENT_SCRIPT;runs=[]
    with tempfile.TemporaryDirectory(prefix='firefly-edit-projects-') as root:
      for i,vp in enumerate(((1424,900),(390,844))):
        store=ProjectStore(Path(root)/f'p{i}',snapshot_delay=0)
        pid=seed(store)
        app=harness.load_production_app(Path(root)/f'r{i}',store)
        server=harness.ConversationBrowserServer(store,app);server.start()
        try:runs.append(launch(browser,server,pid,vp))
        finally:server.close();store.close()
    summary={'issue':'image-editor','browser':str(browser),'viewports':[(1424,900),(390,844)],'runs':runs,'pass':len(runs)==2 and all(r.get('pass') for r in runs)}
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0 if summary['pass'] else 1

if __name__=='__main__':raise SystemExit(main())
