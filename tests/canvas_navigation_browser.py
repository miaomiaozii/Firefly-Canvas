"""Real headless-Chrome acceptance for cross-canvas navigation (list -> canvas A via real click, canvas B standalone)."""
from __future__ import annotations
import json, sys, tempfile, urllib.parse, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from firefly_projects import ProjectStore
from tests import conversation_browser as harness
CLIENT_SCRIPT=ROOT/'tests/canvas_navigation_browser_client.js'

def seed(store, prefix):
    pid=store.create_project(f'{prefix} Project')['id']
    store.apply_mutations(pid,[
      {'op':'upsert_node','node':{'id':f'{prefix}-node','type':'smart-prompt','text':f'{prefix} prompt'}},
    ],base_revision=0);store.flush(pid);return pid

def launch(browser,server,url,viewport,segments):
    w,h=viewport;profile=Path(tempfile.mkdtemp(prefix='firefly-nav-browser-'))
    proc=subprocess.Popen([str(browser),'--headless=new','--disable-gpu','--disable-background-networking','--disable-extensions','--no-first-run','--window-size=%d,%d'%(w,h),f'--user-data-dir={profile}',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=ROOT)
    try:
        results=[]
        for _ in range(segments):
            before=len(server.results)
            results.append(server.wait_for_result(before,timeout=120))
        return results
    finally:
      proc.terminate();proc.wait(timeout=5);harness.remove_profile(profile)

def main():
    browser=harness.find_browser();harness.CLIENT_SCRIPT=CLIENT_SCRIPT;harness.INJECT_PATHS={'/static/project-list.html',harness.HTML_PAGE_PATH};runs=[]
    with tempfile.TemporaryDirectory(prefix='firefly-nav-projects-') as root:
      for i,vp in enumerate(((1424,900),(390,844))):
        store=ProjectStore(Path(root)/f'p{i}',snapshot_delay=0)
        pid_a=seed(store,'Alpha');pid_b=seed(store,'Beta')
        cid_b=store.canvas(pid_b)['id']
        app=harness.load_production_app(Path(root)/f'r{i}',store)
        server=harness.ConversationBrowserServer(store,app);server.start()
        try:
          list_url=f'{server.base_url}/static/project-list.html?browserTest=1'
          seg1=launch(browser,server,list_url,vp,2)
          seg2=launch(browser,server,f'{server.base_url}/static/smart-canvas.html?id={urllib.parse.quote(cid_b)}&project={urllib.parse.quote(pid_b)}&browserTest=1',vp,1)
          runs.append({'listAndAlpha':seg1,'betaStandalone':seg2})
        finally:server.close();store.close()
    all_segments=[seg for run in runs for group in run.values() for seg in group]
    summary={'issue':'canvas-navigation','browser':str(browser),'viewports':[(1424,900),(390,844)],'runs':runs,'pass':len(all_segments)==6 and all(seg.get('pass') for seg in all_segments)}
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0 if summary['pass'] else 1

if __name__=='__main__':raise SystemExit(main())
