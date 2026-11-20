"""#452: the HTTP response, not the Git checkout, gets a new cache stamp."""
import re
import subprocess
import sys
import tempfile
import unittest
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.static_pages import FireflyStaticFiles

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("project-list.html", "smart-canvas.html", "global-settings.html")


def asset_urls(body: bytes):
    for raw in re.findall(rb'(?:href|src)="(/static/[^\"]+)"', body):
        url = urlsplit(unescape(raw.decode("utf-8")))
        if url.path.endswith((".js", ".css")):
            yield url


class RuntimeAssetVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No listening socket or lifespan workers are needed to test the routes.
        from main import app

        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    def test_all_product_routes_share_a_fresh_stamp_without_editing_html(self):
        versions = set()
        before = {page: (ROOT / "static" / page).read_bytes() for page in PAGES}
        for route, page in [("/", PAGES[0])] + [(f"/static/{page}", page) for page in PAGES]:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(self.client.get(route).content, response.content)
                old_urls = list(asset_urls(before[page]))
                self.assertTrue(old_urls)
                new_urls = list(asset_urls(response.content))
                for old, new in zip(old_urls, new_urls, strict=True):
                    self.assertEqual(old.path, new.path)
                    self.assertEqual(old.fragment, new.fragment)
                    old_params, new_params = dict(parse_qsl(old.query)), dict(parse_qsl(new.query))
                    new_version = new_params.pop("v")
                    self.assertNotEqual(old_params.pop("v"), new_version)
                    self.assertEqual(old_params, new_params, new.geturl())
                    versions.add(new_version)
                self.assertEqual((ROOT / "static" / page).read_bytes(), before[page])
        self.assertEqual(len(versions), 1, "one stamp across all assets and product routes")

    def test_a_new_process_has_a_different_stamp(self):
        current = list(asset_urls(self.client.get("/").content))
        next_body = subprocess.check_output([sys.executable, "-c", (
            "from app.static_pages import html_response; import sys; "
            "sys.stdout.buffer.write(html_response('static/project-list.html').body)"
        )], cwd=ROOT, timeout=30)
        next_urls = list(asset_urls(next_body))
        self.assertTrue(current and next_urls)
        for old, new in zip(current, next_urls, strict=True):
            self.assertNotEqual(dict(parse_qsl(old.query))["v"], dict(parse_qsl(new.query))["v"])

    def test_old_file_validators_cannot_hide_a_new_html_stamp(self):
        route = "/static/project-list.html"
        response = self.client.get(route, headers={"If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)
        self.assertNotIn("last-modified", response.headers)
        self.assertNotIn("etag", response.headers)
        head = self.client.head(route)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.headers["content-length"], str(len(response.content)))
        self.assertEqual(head.content, b"")
        self.assertEqual(self.client.post(route).status_code, 405)
        self.assertEqual(self.client.get("/static/does-not-exist.html").status_code, 404)

    def test_js_and_css_still_use_normal_static_file_responses(self):
        for url in asset_urls(self.client.get("/").content):
            with self.subTest(url=url.geturl()):
                response = self.client.get(url.geturl())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, (ROOT / url.path.lstrip("/")).read_bytes())
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(self.client.get(url.geturl(), headers={
                    "If-None-Match": response.headers["etag"],
                }).status_code, 304)


class HtmlStampScopeTests(unittest.TestCase):
    def test_only_version_values_change_preserving_html_bytes_and_other_parameters(self):
        template = (
            '缓存测试\r\n<script src="/static/probe.js?v={v}&amp;agent=r3&amp;rev=449#anchor"></script>\r\n'
            "<link href = '/static/probe.css?v={v}&mode=preview'>\r\n"
            '<img src="/static/images/logo.png?v=one"><a href="/static/other.html?v=one">Test</a>\r\n'
            '<script src="https://example.test/static/other.js?v=one"></script>\r\n'
            '<script src="/static/unstamped.js"></script>\r\n'
        )
        before = template.format(v="old").encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="firefly-cache-stamp-") as temp:
            path = Path(temp) / "probe.html"
            path.write_bytes(before)
            app = FastAPI()
            app.mount("/static", FireflyStaticFiles(directory=temp))
            with TestClient(app) as client:
                response = client.get("/static/probe.html")
            self.assertEqual(response.status_code, 200)
            version, = re.findall(rb'probe\.js\?v=([^&]+)', response.content)
            self.assertNotEqual(version, b"old")
            self.assertEqual(response.content, template.format(v=version.decode("ascii")).encode("utf-8"))
            self.assertEqual(path.read_bytes(), before)


class AssetRefreshLauncherTests(unittest.TestCase):
    def test_restart_delegates_cache_busting_to_the_server(self):
        # 不执行真实重启脚本：它的停服务逻辑会针对正在运行的实例。
        source = (ROOT / "tools/restart-firefly.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("-ArgumentList @('tools\\run-server.py')", source)
        self.assertRegex(source, r"(?m)^Start-FireflyServer$")
        self.assertNotRegex(source, r"(?i)(\.html|\?v=|WriteAllText)")


if __name__ == "__main__":
    unittest.main()
