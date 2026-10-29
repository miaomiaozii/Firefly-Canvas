import json
import io
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import wave
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

from firefly_projects import ProjectStore
from tests.conversation_browser import ROOT, ConversationBrowserServer, load_production_app, seed_store


class FixtureApp:
    async def __call__(self, scope, receive, send):
        path = scope.get("path") or ""
        if path == "/static/js/smart-canvas.js":
            body = (ROOT / "static" / "js" / "smart-canvas.js").read_bytes()
            status = 200
            content_type = "text/javascript; charset=utf-8"
        elif path == "/static/smart-canvas.html":
            body = b"<html><body><h1>fixture canvas</h1></body></html>"
            status = 200
            content_type = "text/html; charset=utf-8"
        elif path == "/api/forwarded":
            body = b'{"from":"fixture-app"}'
            status = 200
            content_type = "application/json"
        else:
            body = b"not found"
            status = 404
            content_type = "text/plain; charset=utf-8"
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode("latin-1"))],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})


class LifecycleFixtureApp(FixtureApp):
    router = SimpleNamespace(lifespan_context=object())

    def __init__(self):
        self.started = False
        self.stopped = False

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            message = await receive()
            if message.get("type") == "lifespan.startup":
                self.started = True
                await send({"type": "lifespan.startup.complete"})
            message = await receive()
            if message.get("type") == "lifespan.shutdown":
                self.stopped = True
                await send({"type": "lifespan.shutdown.complete"})
            return
        await super().__call__(scope, receive, send)


def run_viewport_isolation_assertions(temp_root: Path) -> None:
    """原 test_each_viewport_loads... 的断言体；在子进程 runner 内执行。

    load_production_app 会弹掉/重建 sys.modules 里的 app.*/main——绝不能
    在共享的 pytest 进程里跑（#243 全量回归 12 个 MCP 假失败的根因）。
    """
    first_store = ProjectStore(temp_root / "viewport-first", snapshot_delay=0)
    second_store = ProjectStore(temp_root / "viewport-second", snapshot_delay=0)
    try:
        first_app = load_production_app(temp_root / "runtime-first", first_store)
        second_app = load_production_app(temp_root / "runtime-second", second_store)
    finally:
        first_store.close()
        second_store.close()

    assert first_app is not second_app
    assert first_app.state.issue13_module is not second_app.state.issue13_module
    assert first_app.state.issue13_module.FIREFLY_PROJECT_STORE is first_store
    assert second_app.state.issue13_module.FIREFLY_PROJECT_STORE is second_store
    assert (
        first_app.state.issue13_module.PROJECT_CONVERSATION_TASKS
        is not second_app.state.issue13_module.PROJECT_CONVERSATION_TASKS
    )
    assert (
        first_app.state.issue13_module.PROJECT_CONVERSATION_READY_RESULTS
        is not second_app.state.issue13_module.PROJECT_CONVERSATION_READY_RESULTS
    )
    assert (
        first_app.state.issue13_module.RUNTIME_ROOT
        != second_app.state.issue13_module.RUNTIME_ROOT
    )


class ConversationBrowserHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-issue13-harness-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.server = ConversationBrowserServer(self.store, FixtureApp())
        self.server.start()

    def tearDown(self):
        self.server.close()
        self.store.close()
        self.temp_dir.cleanup()

    def post_result(self, payload):
        request = urllib.request.Request(
            f"{self.server.base_url}/__conversation_browser_results",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_static_assets_keep_query_parameters_and_are_served_from_root(self):
        with urllib.request.urlopen(
            f"{self.server.base_url}/static/js/smart-canvas.js?cache=issue13",
            timeout=3,
        ) as response:
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("smart-canvas", body)

    def test_canvas_html_is_injected_only_after_the_provided_app_serves_it(self):
        with urllib.request.urlopen(
            f"{self.server.base_url}/static/smart-canvas.html?id=project-1",
            timeout=3,
        ) as response:
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("fixture canvas", body)
        self.assertIn('/tests/conversation_browser_client.js', body)

    def test_native_browser_server_can_disable_script_injection(self):
        native_server = ConversationBrowserServer(self.store, FixtureApp(), client_script=None)
        native_server.start()
        try:
            with urllib.request.urlopen(
                f"{native_server.base_url}/static/smart-canvas.html?id=project-1",
                timeout=3,
            ) as response:
                body = response.read().decode("utf-8")
        finally:
            native_server.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(body, "<html><body><h1>fixture canvas</h1></body></html>")

    def test_static_path_cannot_escape_root(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                f"{self.server.base_url}/static/../main.py?cache=issue13",
                timeout=3,
            )
        self.assertEqual(caught.exception.code, 404)

    def test_non_injected_routes_are_forwarded_to_the_provided_asgi_app(self):
        with urllib.request.urlopen(f"{self.server.base_url}/api/forwarded?query=kept", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"from": "fixture-app"})

    def test_extraction_menu_keyboard_contract_is_present_in_product_script(self):
        source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        client = (ROOT / "tests" / "conversation_browser_client.js").read_text(encoding="utf-8")
        self.assertIn("setAttribute('role', 'menu')", source)
        self.assertIn("role=\"menuitem\"", source)
        self.assertIn("menu.querySelector('[data-extract-prompt-action]')?.focus()", source)
        self.assertIn("if(event.key === 'Escape')", source)
        self.assertIn("if(!event.target.closest('[data-extract-prompt-selection]')) closeConversationExtractionMenu()", source)
        self.assertIn("let conversationSelectionCandidate = null", source)
        self.assertIn("function conversationSelectionFromDom(messageEl)", source)
        self.assertIn("cachedSelection?.sourceNodeId === node.id", source)
        self.assertIn("menu?.setAttribute('aria-hidden', 'true')", source)
        self.assertIn('tabindex="0"', source)
        self.assertIn('aria-haspopup="menu"', source)
        self.assertIn("event.key === 'ContextMenu'", source)
        self.assertIn("event.shiftKey && event.key === 'F10'", source)
        self.assertIn("new KeyboardEvent('keydown', {key:'F10', shiftKey:true", client)

    def test_aborted_conversation_polling_releases_unregistered_local_placeholder(self):
        source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn("const CONVERSATION_MAX_NOT_FOUND_POLLS = 30", source)
        self.assertIn("notFoundPolls >= CONVERSATION_MAX_NOT_FOUND_POLLS", source)
        self.assertIn("abandonUnregisteredConversationTask(node, requestId)", source)
        self.assertIn("smart.conversationTaskReleased", source)  # #97: toast moved to i18n key (zh value in static/js/i18n/smart-canvas.js)

    def test_browser_seed_uses_decodable_image_video_and_audio_fixtures(self):
        fixture = seed_store(self.store)
        records = {item["kind"]: item for item in self.store.list_media(fixture["project_id"])}
        records = {
            kind: self.store.media_record(fixture["project_id"], item["asset_id"])
            for kind, item in records.items()
        }

        self.assertTrue(records["image"]["path"])
        self.assertEqual(Path(records["image"]["path"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        video = Path(records["video"]["path"]).read_bytes()
        self.assertEqual(video[:4], b"RIFF")
        self.assertEqual(video[8:12], b"AVI ")
        with wave.open(io.BytesIO(Path(records["audio"]["path"]).read_bytes()), "rb") as stream:
            self.assertEqual((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()), (1, 2, 8_000))

    def test_duplicate_final_result_is_recorded_once(self):
        payload = {"kind": "final", "result_id": "result-1", "pass": False}
        self.assertEqual(self.post_result(payload), (200, {"ok": True}))
        self.assertEqual(self.post_result(payload), (200, {"ok": True, "duplicate": True}))
        self.assertEqual(self.server.results, [payload])

    def test_send_bytes_swallows_connection_reset_during_headers(self):
        handler_type = self.server.httpd.RequestHandlerClass
        handler = handler_type.__new__(handler_type)
        handler.send_response = Mock(side_effect=ConnectionResetError(10054, "peer reset"))
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()

        handler_type.send_bytes(handler, b"payload", "text/plain")

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_not_called()
        handler.end_headers.assert_not_called()
        handler.wfile.write.assert_not_called()

    def test_production_asgi_lifespan_is_started_and_stopped(self):
        app = LifecycleFixtureApp()
        server = ConversationBrowserServer(self.store, app)
        server.start()
        try:
            self.assertTrue(app.started)
        finally:
            server.close()
        self.assertTrue(app.stopped)

    def test_each_viewport_loads_an_isolated_production_module_and_project_root(self):
        # 本测试的 load_production_app 会从 sys.modules 弹掉并重新导入全部
        # app.*/main 模块——这是对测试进程的永久污染（后续同进程测试拿到陈旧
        # 模块绑定与已删除的 store root）。#243：改在子进程里跑，主进程模块表
        # 不受影响；断言逻辑在 _ViewportIsolationSubprocessRunner 中保持不变。
        runner = ROOT / "tests" / "_conversation_viewport_isolation_runner.py"
        completed = subprocess.run(
            [sys.executable, str(runner)],
            capture_output=True,
            timeout=300,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or b"").decode(
                "utf-8", errors="replace"
            )[-4000:]
            self.fail(f"viewport isolation subprocess failed:\n{detail}")


if __name__ == "__main__":
    unittest.main()
