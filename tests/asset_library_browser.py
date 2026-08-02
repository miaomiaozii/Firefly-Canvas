import os
import sys
import time
import tempfile
import pathlib
import shutil

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from firefly_projects import ProjectStore

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tests.conversation_browser as harness

HTML_PAGE_PATH = '/static/smart-canvas.html'
CLIENT_SCRIPT = ROOT / 'tests' / 'asset_library_browser_client.js'
VIEWPORTS = [(1424, 900)]


def tiny_png_bytes(color=(42, 123, 231)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buf, format='PNG')
    return buf.getvalue()


def fake_mp4_bytes():
    # minimal ISO-BMFF container recognized by video validators
    ftyp = b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2'
    mdat = b'\x00\x00\x00\x08mdat'
    return ftyp + mdat


def seed_store(store):
    project = store.create_project('Asset Library Suite')
    pid = project['id']
    store.import_media(pid, tiny_png_bytes(), 'photo-a.png', 'image/png')
    store.import_media(pid, fake_mp4_bytes(), 'clip-b.mp4', 'video/mp4')
    return pid


def load_app(root, store):
    app = harness.load_production_app(root, store)
    module = app.state.issue13_module
    return app, module


def main():
    browser = harness.find_browser()
    results = []
    for viewport in VIEWPORTS:
        root = tempfile.mkdtemp(prefix='asset-lib-')
        try:
            store = ProjectStore(pathlib.Path(root) / 'projects', snapshot_delay=0)
            project_id = seed_store(store)
            canvas_id = store.canvas(project_id)['id']
            app = harness.load_production_app(pathlib.Path(root) / 'runtime', store)
            server = harness.ConversationBrowserServer(store, app)
            harness.CLIENT_SCRIPT = CLIENT_SCRIPT
            server.start()
            try:
                fixture = {'project_id': project_id, 'selected_text': ''}
                harness.launch(browser, server, viewport, fixture)
                result = server.results[-1] if server.results else {'pass': False}
                results.append((viewport, result))
            finally:
                server.close()
        finally:
            try:
                shutil.rmtree(root, ignore_errors=True)
            except Exception:
                pass
    ok = True
    for viewport, result in results:
        print(viewport, 'PASS:', result) if result and result.get('pass') else print(viewport, 'FAIL:', result)
        if not (result and result.get('pass')):
            ok = False
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
