"""#493：上游直连开关（默认开）+ 纯策略工厂契约（离线）。

设计边界（用户裁定）：只覆盖上游服务商请求；pinned 下载（trust_env=False+proxy 强制）
与显式 transport/mount/proxy 归属优先，不得被注入覆盖；自定义 client 工厂按原 kwargs
直通，不得隐藏别名。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import upstream_http

import main


def _proxy_free(transport) -> bool:
    # httpcore 的直连池是 ConnectionPool/AsyncConnectionPool；带代理的是 HTTPProxy/AsyncHTTPProxy。
    return type(transport._pool).__name__ in ("ConnectionPool", "AsyncConnectionPool")


class UpstreamClientOptionsTests(unittest.TestCase):
    def test_direct_true_mounts_proxy_free_transport_for_exact_origin(self):
        options = upstream_http.upstream_client_options(
            "https://api.soonstudio.ai/v1/images/generations", direct=True, mode="async")
        mounts = options["mounts"]
        self.assertEqual(list(mounts), ["https://api.soonstudio.ai"])
        transport = mounts["https://api.soonstudio.ai"]
        self.assertIs(type(transport), httpx.AsyncHTTPTransport)
        self.assertTrue(_proxy_free(transport))

    def test_direct_false_returns_options_without_mounts(self):
        options = upstream_http.upstream_client_options(
            "https://api.soonstudio.ai/v1/images/generations", direct=False, mode="async")
        self.assertNotIn("mounts", options)

    def test_origin_with_port_and_pathless_target_mounts_exact_origin(self):
        options = upstream_http.upstream_client_options(
            "https://gateway.example.com:8443", direct=True, mode="sync")
        self.assertEqual(list(options["mounts"]), ["https://gateway.example.com:8443"])
        self.assertIs(type(options["mounts"]["https://gateway.example.com:8443"]), httpx.HTTPTransport)

    def test_multiple_targets_mount_every_origin_once(self):
        options = upstream_http.upstream_client_options(
            ["https://api.a.test/v1/chat", "https://api.b.test/task", "https://api.a.test/other"],
            direct=True, mode="async")
        self.assertEqual(sorted(options["mounts"]),
                         ["https://api.a.test", "https://api.b.test"])

    def test_explicit_transport_or_proxy_ownership_is_not_overridden(self):
        handler = lambda request: httpx.Response(200)  # noqa: E731
        for kwargs in (
            {"transport": httpx.MockTransport(handler)},
            {"mounts": {"https://api.a.test": httpx.MockTransport(handler)}},
            {"proxy": "http://explicit.test:8080"},
        ):
            options = upstream_http.upstream_client_options(
                "https://api.a.test/v1", direct=True, mode="async", **kwargs)
            self.assertEqual(options, kwargs)

    def test_user_mount_for_same_origin_wins_over_direct(self):
        handler = lambda request: httpx.Response(200)  # noqa: E731
        custom = httpx.MockTransport(handler)
        options = upstream_http.upstream_client_options(
            "https://api.a.test/v1", direct=True, mode="async",
            mounts={"https://api.a.test": custom})
        self.assertEqual(options["mounts"], {"https://api.a.test": custom})

    def test_verify_and_timeout_kwargs_survive_injection(self):
        options = upstream_http.upstream_client_options(
            "https://api.a.test/v1", direct=True, mode="async",
            verify=False, timeout=37.5, headers={"Accept": "application/json"})
        self.assertIs(options["verify"], False)
        self.assertEqual(options["timeout"], 37.5)
        self.assertEqual(options["headers"], {"Accept": "application/json"})
        self.assertTrue(_proxy_free(options["mounts"]["https://api.a.test"]))

    def test_create_upstream_async_client_injects_mounts_for_httpx(self):
        import asyncio

        async def scenario():
            client = upstream_http.create_upstream_async_client(
                "https://api.soonstudio.ai/v1/images/generations", direct=True, timeout=10)
            try:
                transport = client._transport_for_url(httpx.URL("https://api.soonstudio.ai/v1/images/generations"))
                self.assertTrue(_proxy_free(transport))
                other = client._transport_for_url(httpx.URL("https://elsewhere.test/x"))
                self.assertIsNot(other, transport)
            finally:
                await client.aclose()
        asyncio.run(scenario())

    def test_env_proxy_respected_when_direct_false_bypassed_when_direct_true(self):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:18080"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:18080"
        try:
            with upstream_http.create_upstream_client(
                    "https://api.soonstudio.ai/v1", direct=True, timeout=5) as client:
                direct_transport = client._transport_for_url(httpx.URL("https://api.soonstudio.ai/v1"))
                self.assertTrue(_proxy_free(direct_transport))
                with upstream_http.create_upstream_client(
                        "https://api.soonstudio.ai/v1", direct=False, timeout=5) as proxied:
                    default_transport = proxied._transport_for_url(httpx.URL("https://api.soonstudio.ai/v1"))
                    self.assertFalse(_proxy_free(default_transport))
        finally:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)

    def test_create_upstream_client_factory_passthrough_without_policy(self):
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return "custom-client"

        result = upstream_http.create_upstream_client(
            "https://api.a.test/v1", direct=True, client_factory=factory, timeout=5)
        self.assertEqual(result, "custom-client")
        self.assertNotIn("mounts", captured)
        self.assertEqual(captured, {"timeout": 5})


class GlobalUpstreamDirectPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.previous_settings_file = main.GLOBAL_SETTINGS_FILE
        main.GLOBAL_SETTINGS_FILE = str(self.base / "global_settings.json")

    def tearDown(self):
        main.GLOBAL_SETTINGS_FILE = self.previous_settings_file
        self.temp_dir.cleanup()

    def test_default_is_true_without_settings_file(self):
        self.assertIs(main.load_global_preferences()["upstream_direct"], True)
        self.assertIs(main.load_upstream_direct(), True)

    def test_patch_roundtrip_and_theme_only_patch_keeps_stored_value(self):
        saved = main.save_global_preferences("light", "zh", upstream_direct=False)
        self.assertIs(saved["upstream_direct"], False)
        self.assertIs(main.load_upstream_direct(), False)
        again = main.save_global_preferences("dark", "zh")
        self.assertIs(again["upstream_direct"], False)
        self.assertIs(main.load_global_preferences()["upstream_direct"], False)

    def test_corrupt_and_string_values_fall_back_to_true(self):
        Path(main.GLOBAL_SETTINGS_FILE).write_text(
            json.dumps({"upstream_direct": "false"}), encoding="utf-8")
        self.assertIs(main.load_global_preferences()["upstream_direct"], True)
        Path(main.GLOBAL_SETTINGS_FILE).write_text(
            json.dumps({"upstream_direct": False}), encoding="utf-8")
        self.assertIs(main.load_upstream_direct(), False)

    def test_preferences_api_rejects_non_bool_and_null(self):
        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url="http://127.0.0.1:8765") as test_client:
            for payload in ({"upstream_direct": "false"}, {"upstream_direct": None}):
                response = test_client.patch("/api/global-settings/preferences", json=payload)
                self.assertEqual(response.status_code, 422)

    def test_preferences_api_roundtrip(self):
        from fastapi.testclient import TestClient
        with TestClient(main.app, base_url="http://127.0.0.1:8765") as test_client:
            saved = test_client.patch(
                "/api/global-settings/preferences", json={"theme": "dark", "language": "zh", "upstream_direct": False})
            self.assertEqual(saved.status_code, 200)
            self.assertIs(saved.json()["preferences"]["upstream_direct"], False)
            loaded = test_client.get("/api/global-settings/preferences")
            self.assertIs(loaded.json()["preferences"]["upstream_direct"], False)


if __name__ == "__main__":
    unittest.main()
