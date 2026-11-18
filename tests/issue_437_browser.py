"""Headless #437 placeholder check and port-0 lifecycle runner."""
import argparse
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import native_browser
from tests.conversation_browser import load_production_app
from tests.generation_lifecycle_browser import open_history
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser
from tests.test_trash_retained_437 import RetainedTrashTests


def bind_ephemeral():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    if sock.getsockname()[1] == 3000:
        sock.close()
        return bind_ephemeral()
    sock.listen(2048)
    return sock


def cleanup(fixture, browser, server):
    debug_port = int((browser.profile / 'DevToolsActivePort').read_text().splitlines()[0]) if browser else None
    server_port = int(server.base_url.rsplit(':', 1)[1]) if server and server.base_url else None
    try:
        if browser:
            browser.close()
            assert browser.process.poll() is not None, 'browser process still running'
    finally:
        try:
            if server:
                server.close()
        finally:
            fixture.tearDown()
    for port in (debug_port, server_port):
        if port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                assert probe.connect_ex(('127.0.0.1', port)) != 0, f'port {port} still accepting connections'
    print('PASS cleanup browser process and ports', debug_port, server_port)


def multiple():
    fixture = RetainedTrashTests('test_multiple_records_restore_and_purge')
    fixture.setUp()
    browser = server = None
    try:
        fixture.delete(0, 1)
        fixture.delete(1, 2)
        fixture.delete(2, 3)
        app = load_production_app(Path(fixture.temp.name) / 'app', fixture.store)
        server = AsgiBrowserServer(app).start()
        browser = NativeBrowser.launch(find_browser('chrome'))
        browser.set_environment((1280, 720))
        open_history(browser, server.base_url, fixture.pid)
        browser.wait_for('document.querySelector("#smartTrashOpenBtn:not([hidden])")')
        browser.native_click('#smartTrashOpenBtn')
        browser.wait_for('document.querySelectorAll("[data-trash-deletion]").length === 3')
        browser.wait_for('Array.from(document.querySelectorAll(".smart-trash-thumb")).every(i => i.complete && i.naturalWidth > 0)', timeout=15)
        rows = browser.evaluate('Array.from(document.querySelectorAll("[data-trash-deletion]")).map(e => e.dataset.trashDeletion)')
        assert len(set(rows)) == 3, rows
        selected = rows[0]
        others = rows[1:]
        records = {item['deletion_id']: item for item in fixture.store.list_deleted_media(fixture.pid)}
        selector = f'[data-trash-deletion="{selected}"] [data-trash-preview]'
        browser.native_click(selector)
        browser.wait_for('document.querySelector(".smart-trash-detail-restore")')
        browser.native_click('.smart-trash-detail-restore')
        browser.wait_for('document.querySelectorAll("[data-trash-deletion]").length === 2', timeout=15)
        remaining = fixture.store.list_deleted_media(fixture.pid)
        assert {item['deletion_id'] for item in remaining} == set(others), remaining
        outputs = fixture.store.canvas(fixture.pid)['history'][0]['outputs']
        assert records[selected]['output_id'] in [item['output_id'] for item in outputs], outputs
        assert all(records[other]['output_id'] not in [item['output_id'] for item in outputs] for other in others), outputs
        print('PASS valid PNG thumbnails and preview restore exact occurrence', selected)
        browser.native_click(f'[data-trash-deletion="{others[0]}"] [data-trash-forever]')
        browser.wait_for('document.querySelector(".ff-dialog-btn-danger")')
        browser.native_click('.ff-dialog-btn-danger')
        browser.wait_for('document.querySelectorAll("[data-trash-deletion]").length === 0', timeout=15)
        assert not fixture.store.list_deleted_media(fixture.pid)
        assert fixture.client.get(f'/api/project-library/{fixture.pid}/media/{fixture.aid}').content == fixture.original
        print('PASS asset-wide purge removes both remaining occurrences and retains live original')
    finally:
        cleanup(fixture, browser, server)


def placeholder():
    fixture = RetainedTrashTests('test_missing_live_original_is_404_for_both_views')
    fixture.setUp()
    browser = server = None
    try:
        fixture.delete(0, 1)
        (fixture.project_dir / 'media' / fixture.media['filename']).unlink()
        app = load_production_app(Path(fixture.temp.name) / 'app', fixture.store)
        server = AsgiBrowserServer(app).start()
        browser = NativeBrowser.launch(find_browser('chrome'))
        browser.set_environment((1280, 720))
        open_history(browser, server.base_url, fixture.pid)
        browser.wait_for('document.querySelector("#smartTrashOpenBtn:not([hidden])")')
        browser.native_click('#smartTrashOpenBtn')
        selector = f'[data-trash-asset="{fixture.aid}"] .smart-trash-thumb-wrap.no-thumb'
        browser.wait_for(f'document.querySelector({selector!r})', timeout=15)
        result = browser.evaluate(f'''(() => {{
            const wrap = document.querySelector({selector!r});
            return {{icon: getComputedStyle(wrap.querySelector('.smart-trash-icon')).display,
                     image: getComputedStyle(wrap.querySelector('img')).display}};
        }})()''')
        assert result['icon'] != 'none' and result['image'] == 'none', result
        print('PASS missing original: 404 displays recycle-bin placeholder', result)
    finally:
        cleanup(fixture, browser, server)


if __name__ == '__main__':
    native_browser.bind_browser_loopback_socket = bind_ephemeral
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['placeholder', 'multiple', 'journey'])
    parser.add_argument('--artifacts-dir')
    args = parser.parse_args()
    if args.mode == 'placeholder':
        placeholder()
    elif args.mode == 'multiple':
        multiple()
    else:
        from tests import generation_lifecycle_browser
        if not args.artifacts_dir:
            parser.error('--artifacts-dir is required for journey')
        sys.argv = [sys.argv[0], '--artifacts-dir', args.artifacts_dir]
        raise SystemExit(generation_lifecycle_browser.main())
