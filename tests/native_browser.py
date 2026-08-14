"""Reusable Chrome DevTools Protocol harness for trusted browser input evidence."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from websockets.sync.client import connect


VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
BROWSER_PORT_RANGE = range(49152, 65536)


def browser_process_environment() -> dict[str, str]:
    environment = os.environ.copy()
    browser_userprofile = environment.get("FIREFLY_QUALITY_GATE_BROWSER_USERPROFILE", "").strip()
    if browser_userprofile:
        environment["USERPROFILE"] = browser_userprofile
    return environment


def bind_browser_loopback_socket() -> socket.socket:
    for port in BROWSER_PORT_RANGE:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server_socket.bind(("127.0.0.1", port))
            server_socket.listen(2048)
            return server_socket
        except OSError:
            server_socket.close()
    raise RuntimeError("no Chrome-safe loopback port is available")


class AsgiBrowserServer:
    """Run the production ASGI app on an ephemeral loopback port for browser journeys."""

    def __init__(
        self,
        app,
        *,
        startup_timeout: float = 10,
        graceful_shutdown_timeout: float = 5,
    ):
        self.app = app
        self.startup_timeout = startup_timeout
        self.graceful_shutdown_timeout = graceful_shutdown_timeout
        self.server = None
        self.thread = None
        self.base_url = None
        self.socket = None

    def start(self):
        self.socket = bind_browser_loopback_socket()
        port = self.socket.getsockname()[1]
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=self.graceful_shutdown_timeout,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.socket]},
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.server.started and self.server.servers:
                self.base_url = f"http://127.0.0.1:{port}"
                return self
            if not self.thread.is_alive():
                break
            time.sleep(0.05)
        self.close()
        raise RuntimeError("ASGI browser server failed to start")

    def close(self):
        if self.server is not None:
            self.server.should_exit = True
        try:
            if self.thread is not None:
                self.thread.join(timeout=10)
                if self.thread.is_alive():
                    raise RuntimeError("ASGI browser server failed to stop")
        finally:
            if self.socket is not None:
                self.socket.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def find_browser(name: str = "chrome") -> Path:
    candidates = {
        "chrome": (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ),
        "edge": (
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ),
    }
    if name not in candidates:
        raise ValueError(f"unsupported browser: {name}")
    browser = next((path for path in candidates[name] if path.is_file()), None)
    if browser is None:
        raise RuntimeError(f"Windows stable {name.title()} is required")
    return browser.resolve()


class CdpConnection:
    def __init__(self, websocket, *, timeout: float = 15):
        self.websocket = websocket
        self.timeout = timeout
        self.next_id = 1
        self.events: list[dict] = []

    def command(self, method: str, params: dict | None = None) -> dict:
        command_id = self.next_id
        self.next_id += 1
        self.websocket.send(
            json.dumps({"id": command_id, "method": method, "params": params or {}})
        )
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP command timed out: {method}")
            message = json.loads(self.websocket.recv(timeout=remaining))
            if message.get("id") != command_id:
                self.events.append(message)
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            return message.get("result") or {}

    def command_accepting_dialog(self, method: str, params: dict | None = None) -> dict:
        command_id = self.next_id
        self.next_id += 1
        self.websocket.send(
            json.dumps({"id": command_id, "method": method, "params": params or {}})
        )
        dialog_command_id = None
        dialog_accepted = False
        command_result = None
        deadline = time.monotonic() + self.timeout
        dialog_deadline = None
        while command_result is None or not dialog_accepted:
            active_deadline = min(deadline, dialog_deadline) if dialog_deadline else deadline
            remaining = active_deadline - time.monotonic()
            if remaining <= 0:
                if command_result is not None and dialog_command_id is None:
                    raise RuntimeError(f"CDP {method} completed without opening the expected dialog")
                raise TimeoutError(f"CDP command timed out while accepting dialog: {method}")
            try:
                raw_message = self.websocket.recv(timeout=remaining)
            except TimeoutError:
                if command_result is not None and dialog_command_id is None:
                    raise RuntimeError(f"CDP {method} completed without opening the expected dialog")
                raise
            message = json.loads(raw_message)
            if message.get("method") == "Page.javascriptDialogOpening" and dialog_command_id is None:
                self.events.append(message)
                dialog_deadline = None
                dialog_command_id = self.next_id
                self.next_id += 1
                self.websocket.send(
                    json.dumps(
                        {
                            "id": dialog_command_id,
                            "method": "Page.handleJavaScriptDialog",
                            "params": {"accept": True},
                        }
                    )
                )
                continue
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                command_result = message.get("result") or {}
                if dialog_command_id is None:
                    dialog_deadline = min(deadline, time.monotonic() + 1)
                continue
            if message.get("id") == dialog_command_id:
                if "error" in message:
                    raise RuntimeError(
                        f"CDP Page.handleJavaScriptDialog failed: {message['error']}"
                    )
                dialog_accepted = True
                continue
            self.events.append(message)
        return command_result

    def close(self) -> None:
        self.websocket.close()


class NativeBrowser:
    def __init__(
        self,
        cdp,
        *,
        process: subprocess.Popen | None = None,
        profile: Path | None = None,
        log_stream=None,
        executable: Path | None = None,
    ):
        self.cdp = cdp
        self.process = process
        self.profile = profile
        self.log_stream = log_stream
        self.executable = executable
        self.operation_sequence = 0
        self.operations: list[dict] = []
        self._observation_installed = False

    @classmethod
    def launch(cls, executable: Path, *, startup_timeout: float = 30) -> "NativeBrowser":
        profile = Path(tempfile.mkdtemp(prefix="firefly-native-browser-profile-"))
        log_stream = (profile / "browser.stderr.log").open("w+b")
        process = subprocess.Popen(
            [
                str(executable),
                "--headless=new",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-extensions",
                "--disable-gpu",
                "--force-color-profile=srgb",
                "--hide-scrollbars",
                "--no-default-browser-check",
                "--no-first-run",
                "--remote-allow-origins=*",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=log_stream,
            env=browser_process_environment(),
        )
        try:
            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + startup_timeout
            port = None
            while port is None:
                try:
                    port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
                except (FileNotFoundError, PermissionError, ValueError, IndexError):
                    if process.poll() is not None:
                        raise RuntimeError(f"browser exited during CDP startup: {process.returncode}")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("browser CDP endpoint did not start")
                    time.sleep(0.05)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                targets = json.loads(response.read().decode("utf-8"))
            page = next((target for target in targets if target.get("type") == "page"), None)
            if page is None or not page.get("webSocketDebuggerUrl"):
                raise RuntimeError("browser CDP page target is unavailable")
            websocket = connect(
                page["webSocketDebuggerUrl"],
                open_timeout=5,
                close_timeout=2,
                max_size=16 * 1024 * 1024,
            )
            browser = cls(
                CdpConnection(websocket),
                process=process,
                profile=profile,
                log_stream=log_stream,
                executable=executable,
            )
            browser._enable_domains()
            return browser
        except Exception as error:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log_stream.flush()
            log_stream.seek(0)
            stderr_tail = log_stream.read().decode("utf-8", errors="replace")[-4000:].strip()
            log_stream.close()
            shutil.rmtree(profile, ignore_errors=True)
            if stderr_tail:
                raise RuntimeError(f"{error}; browser stderr: {stderr_tail}") from error
            raise

    def _enable_domains(self) -> None:
        for domain in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable", "DOM.enable"):
            self.cdp.command(domain)

    def _install_observation(self) -> None:
        if self._observation_installed:
            return
        source = """
            (() => {
              localStorage.setItem('studio_theme', 'light');
              localStorage.setItem('canvas_theme', 'light');
              window.__fireflyNativeEvents = [];
              const instances = new WeakMap();
              let nextInstance = 1;
              const instanceId = target => {
                if (!(target instanceof Element)) return 0;
                if (!instances.has(target)) instances.set(target, nextInstance++);
                return instances.get(target);
              };
              const describe = (target) => {
                if (!(target instanceof Element)) return String(target);
                const id = target.id ? `#${target.id}` : '';
                const classes = [...target.classList].slice(0, 3).map(value => `.${value}`).join('');
                return `${target.tagName.toLowerCase()}${id}${classes}`;
              };
              let nativeOwner = null;
              window.__fireflySetNativeOwner = target => {
                nativeOwner = target instanceof Element ? target : null;
                return nativeOwner ? {
                  owner: describe(nativeOwner),
                  ownerInstance: instanceId(nativeOwner),
                } : null;
              };
              window.__fireflyClearNativeOwner = () => { nativeOwner = null; };
              for (const type of ['mousedown', 'mouseup', 'click', 'contextmenu', 'wheel', 'keydown', 'keyup', 'input', 'change']) {
                addEventListener(type, event => {
                  const clickOwner = event.target instanceof Element
                    ? event.target.closest('button,a,[role="button"],input,label') || event.target
                    : null;
                  const targetWithinNativeOwner = Boolean(
                    nativeOwner
                    && event.target instanceof Element
                    && (event.target === nativeOwner || nativeOwner.contains(event.target))
                  );
                  const observation = {
                    type,
                    trusted: event.isTrusted,
                    x: Number(event.clientX || 0),
                    y: Number(event.clientY || 0),
                    key: String(event.key || ''),
                    target: describe(event.target),
                    targetInstance: instanceId(event.target),
                    clickOwner: describe(clickOwner),
                    clickOwnerInstance: instanceId(clickOwner),
                    nativeOwner: describe(nativeOwner),
                    nativeOwnerInstance: instanceId(nativeOwner),
                    nativeOwnerConnected: Boolean(nativeOwner?.isConnected),
                    targetWithinNativeOwner,
                    focus: describe(document.activeElement),
                    modifiers: [
                      event.altKey ? 'Alt' : '',
                      event.ctrlKey ? 'Control' : '',
                      event.metaKey ? 'Meta' : '',
                      event.shiftKey ? 'Shift' : '',
                    ].filter(Boolean),
                  };
                  if (type === 'mousedown' || type === 'mouseup' || type === 'click' || type === 'contextmenu') {
                    observation.button = Number(event.button);
                  }
                  if (type === 'wheel') {
                    observation.deltaX = Number(event.deltaX);
                    observation.deltaY = Number(event.deltaY);
                  }
                  window.__fireflyNativeEvents.push(observation);
                  console.debug('__FIREFLY_NATIVE_INPUT__' + JSON.stringify(observation));
                }, true);
              }
            })();
        """
        self.cdp.command("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        self.cdp.command("Runtime.evaluate", {"expression": source})
        self._observation_installed = True

    def set_environment(self, viewport: tuple[int, int]) -> None:
        width, height = viewport
        self.cdp.command(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
                "screenWidth": width,
                "screenHeight": height,
            },
        )
        self.cdp.command(
            "Emulation.setEmulatedMedia",
            {
                "media": "screen",
                "features": [{"name": "prefers-color-scheme", "value": "light"}],
            },
        )
        self._install_observation()

    def evaluate(self, expression: str, *, await_promise: bool = False):
        response = self.cdp.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
            },
        )
        if response.get("exceptionDetails"):
            raise RuntimeError(f"browser evaluation failed: {response['exceptionDetails']}")
        return (response.get("result") or {}).get("value")

    def navigate(self, url: str, *, timeout: float = 30) -> None:
        response = self.cdp.command("Page.navigate", {"url": url})
        if response.get("errorText"):
            raise RuntimeError(f"browser navigation failed: {response['errorText']}")
        self.wait_for("document.readyState === 'complete'", timeout=timeout)

    def reload(self, *, timeout: float = 30) -> dict:
        url = str(self.evaluate("location.href") or "")
        response = self.cdp.command("Page.navigate", {"url": url})
        if response.get("errorText"):
            raise RuntimeError(f"browser reload failed: {response['errorText']}")
        self.wait_for("document.readyState === 'complete'", timeout=timeout)
        return self._record({"action": "reload", "url": url})

    def wait_for(self, expression: str, *, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.evaluate(f"Boolean({expression})")
            if value:
                return value
            time.sleep(0.05)
        raise TimeoutError(f"browser condition timed out: {expression}")

    def _describe_element(
        self,
        selector: str,
        relative: tuple[float, float] | None = None,
        blocked_selector: str | None = None,
    ) -> dict:
        encoded = json.dumps(selector)
        encoded_blocked = json.dumps(blocked_selector or "")
        rx, ry = relative or (0.5, 0.5)
        value = self.evaluate(
            f"""(async () => {{
              const __fireflyDescribeElement = document.querySelector({encoded});
              if (!__fireflyDescribeElement) return null;
              const describeElement = element => element
                ? `${{element.tagName.toLowerCase()}}${{element.id ? '#' + element.id : ''}}${{[...element.classList].slice(0, 3).map(value => '.' + value).join('')}}`
                : '';
              const describe = () => {{
                const rect = __fireflyDescribeElement.getBoundingClientRect();
                const x = rect.left + rect.width * {rx!r};
                const y = rect.top + rect.height * {ry!r};
                const hit = document.elementFromPoint(x, y);
                const blockedSelector = {encoded_blocked};
                const blockingHit = hit && blockedSelector ? hit.closest(blockedSelector) : null;
                const hitAncestry = [];
                for(let element=hit; element; element=element.parentElement){{
                  hitAncestry.push(describeElement(element));
                  if(element === __fireflyDescribeElement) break;
                }}
                return {{
                  x: rect.left, y: rect.top, width: rect.width, height: rect.height,
                  hit: describeElement(hit),
                  hitAncestry,
                  unobscured: hit === __fireflyDescribeElement || __fireflyDescribeElement.contains(hit),
                  blocked: Boolean(blockingHit),
                  blockingHit: describeElement(blockingHit),
                  inViewport:x >= 0 && x <= innerWidth && y >= 0 && y <= innerHeight,
                  focus: document.activeElement ? document.activeElement.tagName.toLowerCase() : '',
                }};
              }};
              let state = describe();
              if (!state.inViewport || !state.unobscured) {{
                __fireflyDescribeElement.scrollIntoView({{block: 'center', inline: 'center'}});
                await new Promise(resolve => setTimeout(resolve, 200));
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                state = describe();
              }}
              return state;
            }})()""",
            await_promise=True,
        )
        if not value:
            raise RuntimeError(f"browser element is unavailable: {selector}")
        return value

    def _point_within_element(
        self,
        selector: str,
        point: tuple[float, float],
        blocked_selector: str | None = None,
    ) -> bool:
        encoded = json.dumps(selector)
        encoded_blocked = json.dumps(blocked_selector or "")
        x, y = point
        return bool(
            self.evaluate(
                f"""(() => {{
                  const __fireflyPointWithinElement = document.querySelector({encoded});
                  if (!__fireflyPointWithinElement) return false;
                  const hit = document.elementFromPoint({x!r}, {y!r});
                  const blockedSelector = {encoded_blocked};
                  return Boolean(
                    hit
                    && (!blockedSelector || !hit.closest(blockedSelector))
                    && (
                      hit === __fireflyPointWithinElement
                      || __fireflyPointWithinElement.contains(hit)
                    )
                  );
                }})()"""
            )
        )

    def _native_event_watermark(self) -> int | tuple[int, int]:
        protocol_watermark = len(getattr(self.cdp, "events", []))
        if not isinstance(self.cdp, CdpConnection):
            return protocol_watermark
        try:
            page_watermark = int(
                self.evaluate("(window.__fireflyNativeEvents || []).length") or 0
            )
        except Exception:
            page_watermark = 0
        return protocol_watermark, page_watermark

    def _native_event_after(
        self,
        watermark: int | tuple[int, int],
        expected_type: str,
        *,
        key: str | None = None,
        point: tuple[float, float] | None = None,
        button: int | None = None,
        modifiers: tuple[str, ...] | None = None,
    ) -> dict:
        prefix = "__FIREFLY_NATIVE_INPUT__"
        if isinstance(watermark, tuple):
            protocol_watermark, page_watermark = watermark
        else:
            protocol_watermark, page_watermark = watermark, None
        deadline = time.monotonic() + 1
        candidates: list[dict] = []
        while time.monotonic() < deadline:
            for event in getattr(self.cdp, "events", [])[protocol_watermark:]:
                if event.get("method") != "Runtime.consoleAPICalled":
                    continue
                args = event.get("params", {}).get("args", [])
                value = args[0].get("value") if args else None
                if not isinstance(value, str) or not value.startswith(prefix):
                    continue
                try:
                    observed = json.loads(value[len(prefix) :])
                except json.JSONDecodeError:
                    continue
                if observed not in candidates:
                    candidates.append(observed)
            if page_watermark is not None:
                try:
                    page_events = self.evaluate(
                        f"(window.__fireflyNativeEvents || []).slice({page_watermark})"
                    )
                except Exception:
                    page_events = []
                for observed in page_events or []:
                    if isinstance(observed, dict) and observed not in candidates:
                        candidates.append(observed)
            for observed in candidates:
                if observed.get("type") != expected_type:
                    continue
                if key is not None and observed.get("key") != key:
                    continue
                if button is not None and observed.get("button") != button:
                    continue
                if modifiers is not None and observed.get("modifiers") != list(modifiers):
                    continue
                if point is not None and (
                    abs(float(observed.get("x", float("inf"))) - point[0]) > 1
                    or abs(float(observed.get("y", float("inf"))) - point[1]) > 1
                ):
                    continue
                return observed
            try:
                self.cdp.command("Runtime.evaluate", {"expression": "void 0"})
            except Exception:
                pass
            time.sleep(0.01)
        detail = f"fresh native {expected_type} event"
        if key is not None:
            detail += f" for {key!r}"
        if button is not None:
            detail += f" with native mouse button {button}"
        if modifiers is not None:
            detail += f" with native modifiers {list(modifiers)!r}"
        if candidates:
            detail += f"; observed candidates: {json.dumps(candidates[-5:], ensure_ascii=False)}"
        raise RuntimeError(f"browser did not observe a {detail}")

    @staticmethod
    def _modifier_input(modifiers: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
        bits = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
        unknown = set(modifiers) - set(bits)
        if unknown:
            raise ValueError(f"unsupported native modifiers: {sorted(unknown)}")
        canonical = tuple(name for name in bits if name in modifiers)
        return sum(bits[name] for name in canonical), canonical

    def _record(self, operation: dict) -> dict:
        self.operation_sequence += 1
        record = {"sequence": self.operation_sequence, **operation}
        self.operations.append(record)
        return record

    def native_click(
        self,
        selector: str,
        *,
        relative: tuple[float, float] | None = None,
        button: str = "left",
        accept_dialog: bool = False,
        blocked_selector: str | None = None,
    ) -> dict:
        if button not in {"left", "right"}:
            raise ValueError(f"unsupported native mouse button: {button}")
        expected_button = 0 if button == "left" else 2
        rx, ry = relative or (0.5, 0.5)
        for _attempt in range(3):
            state = self._describe_element(selector, relative, blocked_selector)
            x = float(state["x"] + state["width"] * rx)
            y = float(state["y"] + state["height"] * ry)
            self.cdp.command("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            state = self._describe_element(selector, relative, blocked_selector)
            x = float(state["x"] + state["width"] * rx)
            y = float(state["y"] + state["height"] * ry)
            if not state.get("blocked") and self._point_within_element(
                selector,
                (x, y),
                blocked_selector,
            ):
                break
        else:
            raise RuntimeError("native click target did not settle at an unobscured point")
        encoded_selector = json.dumps(selector)
        owner_state = self.evaluate(
            f"window.__fireflySetNativeOwner?.(document.querySelector({encoded_selector}))"
        )
        if not isinstance(owner_state, dict) or not int(owner_state.get("ownerInstance") or 0):
            raise RuntimeError(f"native click selector owner is unavailable: {selector}")
        watermark = self._native_event_watermark()
        try:
            self.cdp.command(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": 1},
            )
            release = {"type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": 1}
            if accept_dialog and hasattr(self.cdp, "command_accepting_dialog"):
                self.cdp.command_accepting_dialog("Input.dispatchMouseEvent", release)
            else:
                self.cdp.command("Input.dispatchMouseEvent", release)
            if accept_dialog and not hasattr(self.cdp, "command_accepting_dialog"):
                dialog_deadline = time.monotonic() + 2
                while True:
                    try:
                        self.cdp.command("Page.handleJavaScriptDialog", {"accept": True})
                        break
                    except RuntimeError as error:
                        if "No dialog is showing" not in str(error) or time.monotonic() >= dialog_deadline:
                            raise
                        time.sleep(0.02)
            pressed = self._native_event_after(
                watermark,
                "mousedown",
                point=(x, y),
                button=expected_button,
                modifiers=(),
            )
            observed_release = self._native_event_after(
                watermark,
                "mouseup",
                point=(x, y),
                button=expected_button,
                modifiers=(),
            )
            semantic_type = "click" if button == "left" else "contextmenu"
            observed = self._native_event_after(
                watermark,
                semantic_type,
                point=(x, y),
                button=expected_button,
                modifiers=(),
            )
        finally:
            try:
                self.evaluate("window.__fireflyClearNativeOwner?.()")
            except Exception:
                pass
        owner_instance = int(owner_state.get("ownerInstance") or 0)
        owner_chain_is_intact = all(
            int(event.get("nativeOwnerInstance") or 0) == owner_instance
            and event.get("nativeOwnerConnected") is True
            and event.get("targetWithinNativeOwner") is True
            for event in (pressed, observed_release, observed)
        )
        if not owner_chain_is_intact:
            raise RuntimeError(
                "native click crossed a replaced, disconnected, or retargeted selector owner: "
                f"owner={owner_state!r}, pressed={pressed!r}, "
                f"released={observed_release!r}, semantic={observed!r}"
            )
        return self._record(
            {
                "action": "click",
                "selector": selector,
                "button": button,
                "observed_button": observed.get("button"),
                "point": {"x": x, "y": y},
                "hit": state.get("hit"),
                "hit_ancestry": state.get("hitAncestry") or [],
                "blocking_hit": state.get("blockingHit") or "",
                "blocked_selector": blocked_selector or "",
                "unobscured": state.get("unobscured"),
                "focus_before": state.get("focus"),
                "focus_after": observed.get("focus"),
                "native_owner": owner_state.get("owner"),
                "native_owner_instance": owner_instance,
                "observed_within_owner": owner_chain_is_intact,
                "observed_start_target": pressed.get("target"),
                "observed_start_owner": pressed.get("nativeOwner"),
                "observed_start_owner_instance": pressed.get("nativeOwnerInstance"),
                "observed_start_owner_connected": pressed.get("nativeOwnerConnected"),
                "observed_start_within_owner": pressed.get("targetWithinNativeOwner"),
                "observed_release_target": observed_release.get("target"),
                "observed_release_owner": observed_release.get("nativeOwner"),
                "observed_release_owner_instance": observed_release.get("nativeOwnerInstance"),
                "observed_release_owner_connected": observed_release.get("nativeOwnerConnected"),
                "observed_release_within_owner": observed_release.get("targetWithinNativeOwner"),
                "observed_release_type": observed_release.get("type"),
                "observed_target": observed.get("target"),
                "observed_owner": observed.get("nativeOwner"),
                "observed_owner_instance": observed.get("nativeOwnerInstance"),
                "observed_owner_connected": observed.get("nativeOwnerConnected"),
                "observed_semantic_within_owner": observed.get("targetWithinNativeOwner"),
                "observed_type": observed.get("type"),
                "observed_point": {"x": observed.get("x"), "y": observed.get("y")},
                "trusted": observed.get("trusted") is True,
                "dialog_accepted": accept_dialog,
            }
        )

    def native_click_unblocked(
        self,
        selector: str,
        *,
        blocked_selector: str,
        button: str = "left",
        candidates: tuple[tuple[float, float], ...] = (
            (0.72, 0.64),
            (0.82, 0.64),
            (0.62, 0.64),
            (0.90, 0.64),
            (0.72, 0.54),
            (0.82, 0.54),
            (0.62, 0.54),
            (0.90, 0.54),
            (0.72, 0.44),
            (0.82, 0.44),
            (0.62, 0.44),
            (0.90, 0.44),
            (0.72, 0.74),
            (0.82, 0.74),
            (0.62, 0.74),
            (0.90, 0.74),
            (0.50, 0.88),
            (0.35, 0.88),
            (0.20, 0.88),
            (0.08, 0.88),
            (0.50, 0.18),
            (0.35, 0.18),
            (0.20, 0.18),
            (0.08, 0.18),
        ),
    ) -> dict:
        if not blocked_selector.strip():
            raise ValueError("unblocked native click requires a blocked selector")
        encoded_selector = json.dumps(selector)
        encoded_blocked = json.dumps(blocked_selector)
        encoded_candidates = json.dumps(candidates)
        point = self.evaluate(
            f"""(() => {{
              const __fireflyFindUnblockedPoint = document.querySelector({encoded_selector});
              if (!__fireflyFindUnblockedPoint) return null;
              const rect = __fireflyFindUnblockedPoint.getBoundingClientRect();
              const blockedSelector = {encoded_blocked};
              const candidates = {encoded_candidates};
              const describe = element => element
                ? `${{element.tagName.toLowerCase()}}${{element.id ? '#' + element.id : ''}}${{[...element.classList].slice(0, 3).map(value => '.' + value).join('')}}`
                : '';
              for(let candidateIndex=0; candidateIndex<candidates.length; candidateIndex++){{
                const relative = candidates[candidateIndex];
                const x = rect.left + rect.width * relative[0];
                const y = rect.top + rect.height * relative[1];
                if(x < 0 || x > innerWidth || y < 0 || y > innerHeight) continue;
                const hit = document.elementFromPoint(x, y);
                if(!hit || !(__fireflyFindUnblockedPoint === hit || __fireflyFindUnblockedPoint.contains(hit))) continue;
                if(hit.closest(blockedSelector)) continue;
                const hitAncestry = [];
                for(let element=hit; element; element=element.parentElement){{
                  hitAncestry.push(describe(element));
                  if(element === __fireflyFindUnblockedPoint) break;
                }}
                return {{
                  candidateIndex,
                  relative,
                  point:{{x,y}},
                  hit:describe(hit),
                  hitAncestry,
                }};
              }}
              return null;
            }})()"""
        )
        if not isinstance(point, dict):
            raise RuntimeError(
                f"no unblocked native click point exists within {selector}: {blocked_selector}"
            )
        relative = point.get("relative") or []
        if len(relative) != 2:
            raise RuntimeError("unblocked native click scan returned an invalid relative point")
        operation = self.native_click(
            selector,
            relative=(float(relative[0]), float(relative[1])),
            button=button,
            blocked_selector=blocked_selector,
        )
        if operation.get("blocking_hit"):
            raise RuntimeError(
                f"unblocked native click settled on a blocked descendant: {operation['blocking_hit']}"
            )
        operation.update(
            {
                "blank_canvas": True,
                "candidate_index": int(point.get("candidateIndex") or 0),
                "candidate_relative": [float(relative[0]), float(relative[1])],
                "scan_hit": str(point.get("hit") or ""),
                "hit_ancestry": operation.get("hit_ancestry")
                or list(point.get("hitAncestry") or []),
            }
        )
        return operation

    def native_press(self, key: str, *, modifiers: tuple[str, ...] = ()) -> dict:
        modifier_mask, canonical_modifiers = self._modifier_input(modifiers)
        key_codes = {"Delete": ("Delete", 46), "Backspace": ("Backspace", 8), "Enter": ("Enter", 13), " ": ("Space", 32)}
        code, virtual_key = key_codes.get(key, (f"Key{key.upper()}" if len(key) == 1 else key, ord(key.upper()) if len(key) == 1 else 0))
        params = {
            "key": key,
            "code": code,
            "windowsVirtualKeyCode": virtual_key,
            "nativeVirtualKeyCode": virtual_key,
            "modifiers": modifier_mask,
        }
        watermark = self._native_event_watermark()
        self.cdp.command("Input.dispatchKeyEvent", {"type": "rawKeyDown", **params})
        self.cdp.command("Input.dispatchKeyEvent", {"type": "keyUp", **params})
        observed = self._native_event_after(
            watermark,
            "keyup",
            key=key,
            modifiers=canonical_modifiers,
        )
        return self._record(
            {
                "action": "keyboard",
                "key": key,
                "modifiers": list(canonical_modifiers),
                "observed_modifiers": observed.get("modifiers"),
                "focus_after": observed.get("focus"),
                "observed_target": observed.get("target"),
                "observed_type": observed.get("type"),
                "observed_key": observed.get("key"),
                "trusted": observed.get("trusted") is True,
            }
        )

    def native_type(self, text: str) -> dict:
        if not text:
            raise ValueError("native text input must not be empty")
        watermark = self._native_event_watermark()
        for character in text:
            virtual_key = ord(character.upper()) if character.isascii() and character.isalpha() else ord(character)
            params = {
                "key": character,
                "text": character,
                "unmodifiedText": character,
                "windowsVirtualKeyCode": virtual_key,
                "nativeVirtualKeyCode": virtual_key,
            }
            self.cdp.command("Input.dispatchKeyEvent", {"type": "keyDown", **params})
            self.cdp.command("Input.dispatchKeyEvent", {"type": "keyUp", **params})
        observed = self._native_event_after(watermark, "keyup", key=text[-1])
        return self._record(
            {
                "action": "keyboard",
                "text_length": len(text),
                "focus_after": observed.get("focus"),
                "observed_target": observed.get("target"),
                "observed_type": observed.get("type"),
                "observed_key": observed.get("key"),
                "trusted": observed.get("trusted") is True,
            }
        )

    def native_drag(
        self,
        selector: str,
        *,
        dx: float,
        dy: float,
        steps: int = 8,
        relative: tuple[float, float] | None = None,
        modifiers: tuple[str, ...] = (),
        release_selector: str | None = None,
    ) -> dict:
        modifier_mask, canonical_modifiers = self._modifier_input(modifiers)
        state = self._describe_element(selector, relative)
        rx, ry = relative or (0.5, 0.5)
        start_x = float(state["x"] + state["width"] * rx)
        start_y = float(state["y"] + state["height"] * ry)
        end_x = start_x + dx
        end_y = start_y + dy
        watermark = self._native_event_watermark()
        self.cdp.command(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": start_x, "y": start_y, "modifiers": modifier_mask},
        )
        self.cdp.command(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": start_x,
                "y": start_y,
                "button": "left",
                "buttons": 1,
                "modifiers": modifier_mask,
            },
        )
        pressed = self._native_event_after(
            watermark,
            "mousedown",
            point=(start_x, start_y),
            button=0,
            modifiers=canonical_modifiers,
        )
        if not self._point_within_element(selector, (start_x, start_y)):
            self.cdp.command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": start_x,
                    "y": start_y,
                    "button": "left",
                    "modifiers": modifier_mask,
                },
            )
            raise RuntimeError(
                "native drag started outside the selected element: "
                f"state={state!r}, point={{'x': {start_x!r}, 'y': {start_y!r}}}, pressed={pressed!r}"
            )
        for index in range(1, steps + 1):
            self.cdp.command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": start_x + dx * index / steps,
                    "y": start_y + dy * index / steps,
                    "button": "left",
                    "buttons": 1,
                    "modifiers": modifier_mask,
                },
            )
        self.cdp.command(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": end_x,
                "y": end_y,
                "button": "left",
                "modifiers": modifier_mask,
            },
        )
        observed = self._native_event_after(
            watermark,
            "mouseup",
            point=(end_x, end_y),
            button=0,
            modifiers=canonical_modifiers,
        )
        containment_selector = release_selector or selector
        observed_within_selector = self._point_within_element(containment_selector, (end_x, end_y))
        if not observed_within_selector:
            end_state = self.evaluate(
                f"""(() => {{
                  const element = document.querySelector({json.dumps(containment_selector)});
                  const rect = element?.getBoundingClientRect();
                  const hit = document.elementFromPoint({end_x!r}, {end_y!r});
                  return {{
                    element:rect ? {{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom}} : null,
                    hit:hit ? `${{hit.tagName.toLowerCase()}}${{hit.id ? '#' + hit.id : ''}}${{[...hit.classList].slice(0, 3).map(value => '.' + value).join('')}}` : '',
                    bodyClass:document.body.className,
                    dragState:typeof dragState === 'undefined' ? null : dragState,
                  }};
                }})()"""
            )
            raise RuntimeError(
                f"native drag ended outside the selected element: start={state!r}, end={end_state!r}"
            )
        return self._record(
            {
                "action": "drag",
                "selector": selector,
                "release_selector": containment_selector,
                "modifiers": list(canonical_modifiers),
                "start": {"x": start_x, "y": start_y},
                "end": {"x": end_x, "y": end_y},
                "observed_start_target": pressed.get("target"),
                "hit": state.get("hit"),
                "unobscured": state.get("unobscured"),
                "observed_target": observed.get("target"),
                "observed_type": observed.get("type"),
                "observed_button": observed.get("button"),
                "observed_modifiers": observed.get("modifiers"),
                "observed_within_selector": observed_within_selector,
                "observed_point": {"x": observed.get("x"), "y": observed.get("y")},
                "focus_after": observed.get("focus"),
                "trusted": observed.get("trusted") is True,
            }
        )

    def native_wheel(
        self,
        selector: str,
        delta_y: float,
        *,
        delta_x: float = 0,
        relative: tuple[float, float] | None = None,
        modifiers: tuple[str, ...] = (),
    ) -> dict:
        state = self._describe_element(selector, relative)
        if not state.get("unobscured"):
            raise RuntimeError(f"native wheel target is obscured: {state!r}")
        x = float(state["x"]) + float(state["width"]) * (relative or (0.5, 0.5))[0]
        y = float(state["y"]) + float(state["height"]) * (relative or (0.5, 0.5))[1]
        modifier_mask, canonical_modifiers = self._modifier_input(modifiers)
        watermark = self._native_event_watermark()
        self.cdp.command(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": float(delta_x),
                "deltaY": float(delta_y),
                "modifiers": modifier_mask,
            },
        )
        observed = self._native_event_after(
            watermark,
            "wheel",
            point=(x, y),
            modifiers=canonical_modifiers,
        )
        return self._record(
            {
                "action": "wheel",
                "selector": selector,
                "modifiers": list(canonical_modifiers),
                "point": {"x": x, "y": y},
                "delta_x": float(delta_x),
                "delta_y": float(delta_y),
                "observed_target": observed.get("target"),
                "observed_type": observed.get("type"),
                "observed_modifiers": observed.get("modifiers"),
                "observed_delta_x": observed.get("deltaX"),
                "observed_delta_y": observed.get("deltaY"),
                "trusted": observed.get("trusted") is True,
            }
        )

    def native_upload(self, selector: str, files: tuple[Path, ...]) -> dict:
        paths = tuple(Path(path).resolve() for path in files)
        if not paths or any(not path.is_file() for path in paths):
            raise ValueError("native file upload requires existing files")
        document = self.cdp.command("DOM.getDocument")
        root_id = int((document.get("root") or {}).get("nodeId") or 0)
        target = self.cdp.command(
            "DOM.querySelector",
            {"nodeId": root_id, "selector": selector},
        )
        node_id = int(target.get("nodeId") or 0)
        if not node_id:
            raise RuntimeError(f"browser file input is unavailable: {selector}")
        self.cdp.command("DOM.focus", {"nodeId": node_id})
        watermark = self._native_event_watermark()
        self.cdp.command(
            "DOM.setFileInputFiles",
            {"nodeId": node_id, "files": [str(path) for path in paths]},
        )
        observed = self._native_event_after(watermark, "change", modifiers=())
        if observed.get("focus") != observed.get("target"):
            raise RuntimeError(
                "browser file upload did not retain focus on its input: "
                f"target={observed.get('target')!r}, focus={observed.get('focus')!r}"
            )
        identities = []
        for path in paths:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            identities.append(
                {"name": path.name, "size": path.stat().st_size, "sha256": digest.hexdigest()}
            )
        return self._record(
            {
                "action": "file-upload",
                "selector": selector,
                "files": identities,
                "observed_target": observed.get("target"),
                "observed_type": observed.get("type"),
                "observed_modifiers": observed.get("modifiers"),
                "focus_after": observed.get("focus"),
                "trusted": observed.get("trusted") is True,
            }
        )

    def capture_screenshot(self, path: Path) -> Path:
        response = self.cdp.command(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(response["data"]))
        return path

    def browser_version(self) -> dict:
        return self.cdp.command("Browser.getVersion")

    @property
    def console_errors(self) -> list[dict]:
        errors = []
        for event in getattr(self.cdp, "events", []):
            method = event.get("method")
            params = event.get("params") or {}
            if method == "Runtime.exceptionThrown":
                errors.append(event)
            elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "assert"}:
                errors.append(event)
            elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") == "error":
                errors.append(event)
        return errors

    @property
    def request_summaries(self) -> list[dict]:
        summaries = []
        request_urls: dict[str, str] = {}
        extra_info_urls: dict[str, str] = {}
        for event in getattr(self.cdp, "events", []):
            method = event.get("method")
            params = event.get("params") or {}
            if method == "Network.requestWillBeSentExtraInfo":
                request_id = str(params.get("requestId") or "")
                associated = str(params.get("associatedCookiesUrl") or "").split("?", 1)[0]
                headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
                referer = str(headers.get("Referer") or headers.get("referer") or "").split("?", 1)[0]
                if request_id and (associated or referer):
                    extra_info_urls[request_id] = associated or referer
                continue
            if method == "Network.requestWillBeSent":
                request = params.get("request") or {}
                request_id = str(params.get("requestId") or "")
                url = str(request.get("url") or "").split("?", 1)[0]
                if request_id and url:
                    request_urls[request_id] = url
                summaries.append(
                    {
                        "method": method,
                        "url": url,
                        "request_method": request.get("method"),
                        "type": params.get("type"),
                        "request_id": request_id,
                    }
                )
            elif method == "Network.responseReceived":
                response = params.get("response") or {}
                summaries.append(
                    {
                        "method": method,
                        "url": str(response.get("url") or "").split("?", 1)[0],
                        "status": response.get("status"),
                        "type": params.get("type"),
                    }
                )
            elif method == "Network.loadingFailed":
                request_id = str(params.get("requestId") or "")
                summaries.append(
                    {
                        "method": method,
                        "url": request_urls.get(request_id) or extra_info_urls.get(request_id, ""),
                        "request_id": request_id,
                        "error": params.get("errorText"),
                        "type": params.get("type"),
                        "canceled": params.get("canceled") is True,
                        "blocked_reason": params.get("blockedReason") or "",
                    }
                )
        return summaries

    def close(self) -> None:
        try:
            self.cdp.close()
        finally:
            if self.process is not None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.log_stream is not None:
                self.log_stream.close()
            if self.profile is not None:
                shutil.rmtree(self.profile, ignore_errors=True)
