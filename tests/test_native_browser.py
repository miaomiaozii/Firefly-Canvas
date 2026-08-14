import asyncio
import base64
import hashlib
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from unittest.mock import patch

from tests.native_browser import (
    AsgiBrowserServer,
    CdpConnection,
    NativeBrowser,
    VIEWPORTS,
    browser_process_environment,
)


class FakeCdp:
    def __init__(
        self,
        *,
        emit_events=True,
        observed_mouse_button=None,
        observed_modifiers=None,
        observed_upload_focus="input#fileInput",
        point_within_selector=True,
        dialog_failures=0,
        unblocked_point=None,
        observed_mouse_target="button#run",
        observed_release_mouse_target=None,
        native_owner="button#run",
        emit_semantic_click=True,
        defer_mousedown_until_release=False,
        replace_native_owner_on_release=False,
    ):
        self.commands = []
        self.events = []
        self.emit_events = emit_events
        self.observed_mouse_button = observed_mouse_button
        self.observed_modifiers = observed_modifiers
        self.observed_upload_focus = observed_upload_focus
        self.point_within_selector = point_within_selector
        self.dialog_failures = dialog_failures
        self.unblocked_point = unblocked_point
        self.observed_mouse_target = observed_mouse_target
        self.observed_release_mouse_target = observed_release_mouse_target or observed_mouse_target
        self.native_owner = native_owner
        self.emit_semantic_click = emit_semantic_click
        self.defer_mousedown_until_release = defer_mousedown_until_release
        self.replace_native_owner_on_release = replace_native_owner_on_release
        self.pending_mousedown = None

    def modifiers_for(self, params):
        if self.observed_modifiers is not None:
            return list(self.observed_modifiers)
        mask = int(params.get("modifiers") or 0)
        return [
            name
            for name, bit in (("Alt", 1), ("Control", 2), ("Meta", 4), ("Shift", 8))
            if mask & bit
        ]

    def append_native_event(self, observation):
        self.events.append(
            {
                "method": "Runtime.consoleAPICalled",
                "params": {
                    "args": [
                        {
                            "value": "__FIREFLY_NATIVE_INPUT__"
                            + __import__("json").dumps(observation)
                        }
                    ]
                },
            }
        )

    def command(self, method, params=None):
        params = params or {}
        self.commands.append((method, params))
        if method == "Page.handleJavaScriptDialog" and self.dialog_failures:
            self.dialog_failures -= 1
            raise RuntimeError("CDP Page.handleJavaScriptDialog failed: No dialog is showing")
        if method == "Runtime.evaluate":
            expression = params.get("expression", "")
            if "__fireflyFindUnblockedPoint" in expression:
                return {"result": {"value": self.unblocked_point}}
            if "__fireflyPointWithinElement" in expression:
                return {"result": {"value": self.point_within_selector}}
            if "__fireflySetNativeOwner" in expression:
                return {
                    "result": {
                        "value": {
                            "owner": self.native_owner,
                            "ownerInstance": 1,
                        }
                    }
                }
            if "__fireflyClearNativeOwner" in expression:
                return {"result": {"value": None}}
            if "__fireflyDescribeElement" in expression:
                return {
                    "result": {
                        "value": {
                            "x": 20,
                            "y": 30,
                            "width": 120,
                            "height": 40,
                            "hit": "button#run",
                            "focus": "body",
                            "unobscured": True,
                            "blocked": False,
                            "blockingHit": "",
                        }
                    }
                }
            if expression == "location.href":
                return {"result": {"value": "http://127.0.0.1:49152/smart-canvas?project_id=p1"}}
            if "document.readyState === 'complete'" in expression:
                return {"result": {"value": True}}
        if self.emit_events and method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed":
            observation = {
                "type": "mousedown",
                "trusted": True,
                "x": params["x"],
                "y": params["y"],
                "button": {"left": 0, "middle": 1, "right": 2}.get(params.get("button"), 0),
                "modifiers": self.modifiers_for(params),
                "target": self.observed_mouse_target,
                "targetInstance": 1,
                "clickOwner": self.observed_mouse_target,
                "clickOwnerInstance": 1,
                "nativeOwner": self.native_owner,
                "nativeOwnerInstance": 1,
                "nativeOwnerConnected": True,
                "targetWithinNativeOwner": True,
                "focus": self.observed_mouse_target,
            }
            if self.defer_mousedown_until_release:
                self.pending_mousedown = observation
            else:
                self.append_native_event(observation)
        if self.emit_events and method == "Input.dispatchMouseEvent" and params.get("type") == "mouseWheel":
            self.append_native_event(
                {
                    "type": "wheel",
                    "trusted": True,
                    "x": params["x"],
                    "y": params["y"],
                    "deltaX": params["deltaX"],
                    "deltaY": params["deltaY"],
                    "modifiers": self.modifiers_for(params),
                    "target": "div#promptInput",
                    "focus": "div#promptInput",
                }
            )
        if self.emit_events and method == "Input.dispatchMouseEvent" and params.get("type") == "mouseReleased":
            if self.pending_mousedown is not None:
                self.append_native_event(self.pending_mousedown)
                self.pending_mousedown = None
            button = self.observed_mouse_button
            if button is None:
                button = {"left": 0, "middle": 1, "right": 2}.get(params.get("button"), 0)
            owner_instance = 2 if self.replace_native_owner_on_release else 1
            release_target = self.observed_release_mouse_target
            observation = {
                "type": "mouseup",
                "trusted": True,
                "x": params["x"],
                "y": params["y"],
                "button": button,
                "modifiers": self.modifiers_for(params),
                "target": release_target,
                "targetInstance": 2 if release_target != self.observed_mouse_target else 1,
                "clickOwner": release_target,
                "clickOwnerInstance": 2 if release_target != self.observed_mouse_target else 1,
                "nativeOwner": self.native_owner,
                "nativeOwnerInstance": owner_instance,
                "nativeOwnerConnected": not self.replace_native_owner_on_release,
                "targetWithinNativeOwner": not self.replace_native_owner_on_release,
                "focus": release_target,
            }
            self.append_native_event(observation)
            if self.emit_semantic_click:
                self.append_native_event(
                    {
                        **observation,
                        "type": "click" if button == 0 else "contextmenu",
                    }
                )
        if self.emit_events and method == "Input.dispatchKeyEvent" and params.get("type") == "keyUp":
            self.append_native_event(
                {
                    "type": "keyup",
                    "trusted": True,
                    "x": 0,
                    "y": 0,
                    "key": params["key"],
                    "modifiers": self.modifiers_for(params),
                    "target": "textarea",
                    "focus": "textarea",
                }
            )
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": 7}
        if self.emit_events and method == "DOM.setFileInputFiles":
            self.append_native_event(
                {
                    "type": "change",
                    "trusted": True,
                    "x": 0,
                    "y": 0,
                    "key": "",
                    "modifiers": [],
                    "target": "input#fileInput",
                    "focus": self.observed_upload_focus,
                }
            )
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(b"fixture-png").decode("ascii")}
        return {}


class NativeBrowserContractTests(unittest.TestCase):
    def test_cdp_accepts_a_dialog_while_the_triggering_command_is_blocked(self):
        class Websocket:
            def __init__(self):
                self.sent = []
                self.responses = [
                    '{"method":"Page.javascriptDialogOpening","params":{"message":"confirm"}}',
                    '{"id":2,"result":{}}',
                    '{"id":1,"result":{"released":true}}',
                ]

            def send(self, message):
                self.sent.append(__import__("json").loads(message))

            def recv(self, timeout):
                return self.responses.pop(0)

        websocket = Websocket()
        cdp = CdpConnection(websocket)

        result = cdp.command_accepting_dialog(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased"},
        )

        self.assertEqual(result, {"released": True})
        self.assertEqual(websocket.sent[0]["method"], "Input.dispatchMouseEvent")
        self.assertEqual(websocket.sent[1]["method"], "Page.handleJavaScriptDialog")
        self.assertEqual(websocket.sent[1]["params"], {"accept": True})

    def test_cdp_accepts_a_dialog_announced_after_the_triggering_command_response(self):
        class Websocket:
            def __init__(self):
                self.sent = []
                self.responses = [
                    '{"id":1,"result":{"released":true}}',
                    '{"method":"Page.javascriptDialogOpening","params":{"message":"confirm"}}',
                    '{"id":2,"result":{}}',
                ]

            def send(self, message):
                self.sent.append(__import__("json").loads(message))

            def recv(self, timeout):
                return self.responses.pop(0)

        websocket = Websocket()
        cdp = CdpConnection(websocket)

        result = cdp.command_accepting_dialog(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased"},
        )

        self.assertEqual(result, {"released": True})
        self.assertEqual(websocket.sent[1]["method"], "Page.handleJavaScriptDialog")

    def test_quality_gate_browser_uses_host_profile_identity_without_changing_test_home(self):
        with patch.dict(
            os.environ,
            {
                "USERPROFILE": r"C:\isolated-test-home",
                "HOME": r"C:\isolated-test-home",
                "FIREFLY_QUALITY_GATE_BROWSER_USERPROFILE": r"C:\host-profile",
            },
        ):
            environment = browser_process_environment()

        self.assertEqual(environment["USERPROFILE"], r"C:\host-profile")
        self.assertEqual(environment["HOME"], r"C:\isolated-test-home")

    def test_asgi_browser_server_serves_the_real_app_on_loopback(self):
        async def app(scope, receive, send):
            self.assertEqual(scope["type"], "http")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"firefly"})

        with AsgiBrowserServer(app) as server:
            self.assertGreaterEqual(urlparse(server.base_url).port, 49152)
            with urllib.request.urlopen(f"{server.base_url}/", timeout=5) as response:
                self.assertEqual(response.read(), b"firefly")

    def test_asgi_browser_server_bounds_graceful_shutdown_with_a_hanging_request(self):
        request_started = threading.Event()

        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            request_started.set()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await asyncio.Event().wait()

        server = AsgiBrowserServer(app, graceful_shutdown_timeout=0.1).start()
        request_errors = []

        def request():
            try:
                with urllib.request.urlopen(f"{server.base_url}/hanging", timeout=5) as response:
                    response.read()
            except Exception as error:
                request_errors.append(error)

        request_thread = threading.Thread(target=request, daemon=True)
        request_thread.start()
        self.assertTrue(request_started.wait(timeout=2))
        server_thread = server.thread
        started = time.monotonic()

        server.close()

        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(server_thread.is_alive())
        request_thread.join(timeout=2)
        self.assertFalse(request_thread.is_alive())
        self.assertTrue(request_errors)

    def test_required_desktop_viewports_are_canonical(self):
        self.assertEqual(
            VIEWPORTS,
            ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600)),
        )

    def test_environment_fixes_viewport_dpr_and_light_theme(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)

        browser.set_environment((800, 600))

        self.assertIn(
            (
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 800,
                    "height": 600,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                    "screenWidth": 800,
                    "screenHeight": 600,
                },
            ),
            cdp.commands,
        )
        media = next(params for method, params in cdp.commands if method == "Emulation.setEmulatedMedia")
        self.assertEqual(media["media"], "screen")
        self.assertEqual(media["features"], [{"name": "prefers-color-scheme", "value": "light"}])

    def test_click_keyboard_and_drag_use_cdp_input_and_record_trusted_coordinates(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)

        click = browser.native_click("#run")
        keyboard = browser.native_press("Delete")
        space = browser.native_press(" ")
        drag = browser.native_drag(".node-head", dx=40, dy=20, steps=2)

        mouse = [params for method, params in cdp.commands if method == "Input.dispatchMouseEvent"]
        keys = [params for method, params in cdp.commands if method == "Input.dispatchKeyEvent"]
        self.assertEqual([item["type"] for item in mouse[:3]], ["mouseMoved", "mousePressed", "mouseReleased"])
        self.assertEqual([item["type"] for item in keys], ["rawKeyDown", "keyUp", "rawKeyDown", "keyUp"])
        self.assertEqual(keys[2]["key"], " ")
        self.assertEqual(keys[2]["code"], "Space")
        self.assertEqual(keys[2]["windowsVirtualKeyCode"], 32)
        self.assertEqual([item["type"] for item in mouse[-5:]], [
            "mouseMoved",
            "mousePressed",
            "mouseMoved",
            "mouseMoved",
            "mouseReleased",
        ])
        self.assertTrue(click["trusted"])
        self.assertEqual(click["point"], {"x": 80.0, "y": 50.0})
        self.assertEqual(click["observed_target"], "button#run")
        self.assertEqual(click["observed_type"], "click")
        self.assertEqual(click["observed_release_type"], "mouseup")
        self.assertEqual(keyboard["action"], "keyboard")
        self.assertEqual(keyboard["observed_type"], "keyup")
        self.assertEqual(space["observed_key"], " ")
        self.assertEqual(drag["end"], {"x": 120.0, "y": 70.0})
        self.assertEqual(drag["observed_start_target"], "button#run")
        self.assertTrue(drag["observed_within_selector"])

    def test_failed_network_request_keeps_its_original_url(self):
        cdp = FakeCdp(emit_events=False)
        cdp.events = [
            {"method": "Network.requestWillBeSent", "params": {"requestId": "img-1", "request": {"url": "http://127.0.0.1:54321/media/image.png?width=512", "method": "GET"}, "type": "Image"}},
            {"method": "Network.loadingFailed", "params": {"requestId": "img-1", "errorText": "net::ERR_ABORTED", "type": "Image", "canceled": True}},
        ]
        summaries = NativeBrowser(cdp).request_summaries
        failed = summaries[-1]
        self.assertEqual(failed["url"], "http://127.0.0.1:54321/media/image.png")
        self.assertEqual(failed["request_id"], "img-1")
        self.assertTrue(failed["canceled"])

    def test_native_wheel_and_reload_are_trusted_recorded_browser_operations(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)

        wheel = browser.native_wheel("#promptInput", 480, modifiers=("Shift",))
        reload_operation = browser.reload()

        wheel_command = next(
            params
            for method, params in cdp.commands
            if method == "Input.dispatchMouseEvent" and params.get("type") == "mouseWheel"
        )
        self.assertEqual(wheel_command["deltaY"], 480.0)
        self.assertEqual(wheel_command["modifiers"], 8)
        self.assertTrue(wheel["trusted"])
        self.assertEqual(wheel["observed_target"], "div#promptInput")
        self.assertEqual(wheel["observed_delta_y"], 480.0)
        self.assertEqual(reload_operation["action"], "reload")
        self.assertEqual(reload_operation["url"], "http://127.0.0.1:49152/smart-canvas?project_id=p1")
        self.assertIn(
            ("Page.navigate", {"url": "http://127.0.0.1:49152/smart-canvas?project_id=p1"}),
            cdp.commands,
        )

    def test_native_click_dispatches_release_before_waiting_for_mousedown_telemetry(self):
        cdp = FakeCdp(defer_mousedown_until_release=True)

        operation = NativeBrowser(cdp).native_click("#run")

        mouse_types = [
            params["type"]
            for method, params in cdp.commands
            if method == "Input.dispatchMouseEvent"
        ]
        self.assertEqual(mouse_types[:3], ["mouseMoved", "mousePressed", "mouseReleased"])
        self.assertEqual(operation["observed_start_target"], "button#run")
        self.assertEqual(operation["observed_release_type"], "mouseup")
        self.assertEqual(operation["observed_type"], "click")

    def test_native_click_allows_child_to_parent_retarget_within_one_selector_owner(self):
        operation = NativeBrowser(
            FakeCdp(
                observed_mouse_target="img.node-img",
                observed_release_mouse_target="div.image-node",
                native_owner="div.image-node",
            )
        ).native_click(".image-node")

        self.assertEqual(operation["observed_start_target"], "img.node-img")
        self.assertEqual(operation["observed_release_target"], "div.image-node")
        self.assertEqual(operation["native_owner"], "div.image-node")
        self.assertTrue(operation["observed_within_owner"])

    def test_native_click_rejects_a_selector_owner_replaced_between_press_and_release(self):
        browser = NativeBrowser(FakeCdp(replace_native_owner_on_release=True))

        with self.assertRaisesRegex(RuntimeError, "replaced.*selector owner"):
            browser.native_click("#run")

    def test_native_click_requires_the_browser_semantic_click_event(self):
        browser = NativeBrowser(FakeCdp(emit_semantic_click=False))

        with self.assertRaisesRegex(RuntimeError, "native click event"):
            browser.native_click("#run")

    def test_native_click_can_accept_a_javascript_dialog_after_real_input(self):
        cdp = FakeCdp(dialog_failures=2)
        browser = NativeBrowser(cdp)

        operation = browser.native_click("#run", accept_dialog=True)

        methods = [method for method, _params in cdp.commands]
        self.assertGreater(methods.index("Page.handleJavaScriptDialog"), methods.index("Input.dispatchMouseEvent"))
        self.assertEqual(methods.count("Page.handleJavaScriptDialog"), 3)
        self.assertTrue(operation["trusted"])
        self.assertTrue(operation["dialog_accepted"])

    def test_native_drag_rejects_release_outside_the_selected_element(self):
        browser = NativeBrowser(FakeCdp(point_within_selector=False))

        with self.assertRaisesRegex(RuntimeError, "outside the selected element"):
            browser.native_drag(".node-head", dx=40, dy=20, steps=2)

    def test_native_drag_dispatches_and_observes_modifiers_and_release_container(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)

        operation = browser.native_drag(
            "#shell",
            dx=140,
            dy=90,
            modifiers=("Control",),
            release_selector="#shell",
        )

        mouse = [params for method, params in cdp.commands if method == "Input.dispatchMouseEvent"]
        self.assertTrue(all(item.get("modifiers") == 2 for item in mouse[1:]))
        self.assertEqual(operation["modifiers"], ["Control"])
        self.assertEqual(operation["observed_modifiers"], ["Control"])
        self.assertEqual(operation["release_selector"], "#shell")
        self.assertTrue(operation["observed_within_selector"])

    def test_native_drag_rejects_mismatched_observed_modifiers(self):
        browser = NativeBrowser(FakeCdp(observed_modifiers=()))

        with self.assertRaisesRegex(RuntimeError, "native modifiers"):
            browser.native_drag("#shell", dx=40, dy=20, modifiers=("Control",))

    def test_native_upload_sets_real_files_and_records_content_identity(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)
        with tempfile.TemporaryDirectory(prefix="firefly-native-upload-") as root:
            first = Path(root) / "landscape.png"
            second = Path(root) / "portrait.png"
            first.write_bytes(b"landscape-source")
            second.write_bytes(b"portrait-source")

            operation = browser.native_upload("#fileInput", (first, second))

        upload = next(params for method, params in cdp.commands if method == "DOM.setFileInputFiles")
        self.assertEqual(upload["nodeId"], 7)
        self.assertEqual(upload["files"], [str(first.resolve()), str(second.resolve())])
        self.assertEqual(operation["action"], "file-upload")
        self.assertEqual(operation["selector"], "#fileInput")
        self.assertEqual(operation["observed_type"], "change")
        self.assertEqual(operation["observed_modifiers"], [])
        self.assertEqual(operation["focus_after"], "input#fileInput")
        self.assertTrue(operation["trusted"])
        self.assertEqual(
            operation["files"],
            [
                {
                    "name": "landscape.png",
                    "size": len(b"landscape-source"),
                    "sha256": hashlib.sha256(b"landscape-source").hexdigest(),
                },
                {
                    "name": "portrait.png",
                    "size": len(b"portrait-source"),
                    "sha256": hashlib.sha256(b"portrait-source").hexdigest(),
                },
            ],
        )

    def test_native_upload_rejects_change_event_with_focus_on_another_control(self):
        browser = NativeBrowser(FakeCdp(observed_upload_focus="body"))
        with tempfile.TemporaryDirectory(prefix="firefly-native-upload-focus-") as root:
            image = Path(root) / "image.png"
            image.write_bytes(b"image-source")

            with self.assertRaisesRegex(RuntimeError, "retain focus on its input"):
                browser.native_upload("#fileInput", (image,))

    def test_native_input_rejects_a_stale_trusted_event(self):
        cdp = FakeCdp(emit_events=False)
        cdp.append_native_event(
            {
                "type": "mouseup",
                "trusted": True,
                "x": 80,
                "y": 50,
                "target": "button#old",
                "focus": "button#old",
            }
        )
        browser = NativeBrowser(cdp)

        with self.assertRaisesRegex(RuntimeError, "fresh native keyup event"):
            browser.native_press("Delete")

    def test_click_can_open_a_native_context_menu_with_the_right_button(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)

        operation = browser.native_click("#run", button="right")

        mouse = [params for method, params in cdp.commands if method == "Input.dispatchMouseEvent"]
        self.assertEqual(mouse[1]["button"], "right")
        self.assertEqual(mouse[2]["button"], "right")
        self.assertEqual(operation["button"], "right")
        self.assertEqual(operation["observed_button"], 2)
        self.assertEqual(operation["observed_type"], "contextmenu")
        self.assertEqual(operation["observed_release_type"], "mouseup")
        self.assertTrue(operation["trusted"])

    def test_native_click_rejects_a_mismatched_observed_mouse_button(self):
        cdp = FakeCdp(observed_mouse_button=0)
        browser = NativeBrowser(cdp)

        with self.assertRaisesRegex(RuntimeError, "native mouse button"):
            browser.native_click("#run", button="right")

    def test_native_click_unblocked_scans_and_records_the_real_hit_contract(self):
        point = {
            "candidateIndex": 3,
            "relative": [0.72, 0.64],
            "point": {"x": 720, "y": 640},
            "hit": "div#world.world",
            "hitAncestry": ["div#world.world", "div#shell"],
        }
        browser = NativeBrowser(
            FakeCdp(unblocked_point=point, observed_mouse_target="div#world.world")
        )

        operation = browser.native_click_unblocked(
            "#shell",
            blocked_selector=".image-node,#composer,.smart-top-left",
            button="right",
        )

        self.assertTrue(operation["blank_canvas"])
        self.assertEqual(operation["candidate_index"], 3)
        self.assertEqual(operation["candidate_relative"], [0.72, 0.64])
        self.assertEqual(operation["hit_ancestry"], ["div#world.world", "div#shell"])
        self.assertEqual(operation["blocked_selector"], ".image-node,#composer,.smart-top-left")
        self.assertEqual(operation["blocking_hit"], "")
        self.assertEqual(operation["observed_target"], "div#world.world")
        self.assertTrue(operation["trusted"])

    def test_native_click_unblocked_fails_when_every_candidate_is_blocked(self):
        browser = NativeBrowser(FakeCdp(unblocked_point=None))

        with self.assertRaisesRegex(RuntimeError, "no unblocked native click point"):
            browser.native_click_unblocked(
                "#shell",
                blocked_selector=".image-node,#composer,.smart-top-left",
                button="right",
            )

    def test_request_ledger_records_the_request_before_any_response_arrives(self):
        cdp = FakeCdp()
        cdp.events.append(
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "pending-provider-request",
                    "type": "Fetch",
                    "request": {
                        "method": "POST",
                        "url": "http://127.0.0.1:49152/api/generate?token=secret",
                    },
                },
            }
        )
        browser = NativeBrowser(cdp)

        self.assertEqual(
            browser.request_summaries,
            [
                {
                    "method": "Network.requestWillBeSent",
                    "url": "http://127.0.0.1:49152/api/generate",
                    "request_method": "POST",
                    "type": "Fetch",
                    "request_id": "pending-provider-request",
                }
            ],
        )

    def test_screenshot_bytes_are_written_to_the_requested_artifact(self):
        cdp = FakeCdp()
        browser = NativeBrowser(cdp)
        with tempfile.TemporaryDirectory(prefix="firefly-native-browser-test-") as root:
            target = Path(root) / "checkpoint.png"

            browser.capture_screenshot(target)

            self.assertEqual(target.read_bytes(), b"fixture-png")


if __name__ == "__main__":
    unittest.main()
