import sys
import tempfile
import pathlib
import shutil

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from firefly_projects import ProjectStore

ROOT = pathlib.Path(__file__).resolve().parents[1]
import tests.conversation_browser as harness

HTML_PAGE_PATH = '/static/global-settings.html'
CLIENT_SCRIPT = ROOT / 'tests' / 'global_settings_browser_client.js'
VIEWPORTS = [(1424, 900), (390, 844)]


def seed_store(store):
    project = store.create_project('Global Settings Suite')
    return project['id']


def launch(browser, server, viewport):
    width, height = viewport
    profile = tempfile.mkdtemp(prefix='gs-profile-')
    before = len(server.results)
    url = f'{server.base_url}/static/global-settings.html?browserTest=1'
    import subprocess
    process = subprocess.Popen(
        [str(browser), '--headless=new', '--disable-gpu', '--disable-background-networking', '--disable-component-update', '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', f'--window-size={width},{height}', f'--user-data-dir={profile}', url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        return server.wait_for_result(before, timeout=120)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:
            pass
        harness.remove_profile(pathlib.Path(profile))


def main():
    browser = harness.find_browser()
    results = []
    for viewport in VIEWPORTS:
        root = tempfile.mkdtemp(prefix='gs-')
        try:
            store = ProjectStore(pathlib.Path(root) / 'projects', snapshot_delay=0)
            seed_store(store)
            app = harness.load_production_app(pathlib.Path(root) / 'runtime', store)
            server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
            harness.CLIENT_SCRIPT = CLIENT_SCRIPT
            harness.INJECT_PATHS = {'/static/global-settings.html'}
            server.start()
            try:
                result = launch(browser, server, viewport)
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
