"""Issue #342：全局 /api 同源守卫 + CORS 删除 + token 端点不回传明文。

守卫模型（main.protect_api_origin → app.core.ensure_agent_same_origin，#240 口径）：
- Host 必须在白名单内（默认只有回环；FIREFLY_ALLOWED_HOSTS 逗号分隔追加，#344 留口）
- 带 Origin/Referer 的请求必须与应用来源精确匹配；Origin 存在但解析不出
  scheme://host（file://、sandbox 的 opaque "null"）一律拒绝
- 无 Origin/Referer = 本机程序化客户端（MCP / pi sidecar / UXP）放行

Coverage（对应 review 票面验收）：
- 路由表遍历：每个 /api 路由都在守卫谓词 api_guard_covers 覆盖范围内
- OPTIONS 预检：不再是 405，且响应不带 access-control-allow-origin
- 跨源 POST 写请求被 403
- Host 伪装（无 Origin）被 403
- Origin: null / file:// 被拒（Origin:null 绕过加固）
- 回环无 Origin 的程序化客户端不回归
- 副作用 GET（审计清单 STRICT_SIDE_EFFECT_GETS）要求显式 Origin/Referer，
  无头与外部 Origin 一律 403（D2 补条：no-referrer 跨站 GET 不能走副作用路由）
- 跨站 <form> POST（带 Origin 不带 fetch 元数据）被 403（D7 补例）
- FIREFLY_ALLOWED_HOSTS 追加生效
- /api/config/token 只回 has_token 标志，不回传明文 token
"""
import os
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main

GUARD_DETAIL = "只允许从当前应用页面操作本机数据"
LOOPBACK_BASE = "http://127.0.0.1:8765"


class ApiSameOriginGuardTests(unittest.TestCase):
    """#342 全局 /api 同源守卫行为。"""

    def test_route_table_all_api_routes_covered(self):
        # 路由表遍历：新增 /api 路由若不在守卫谓词内，此测试立刻失败。
        # fastapi 0.141 的 include_router 懒包装为 _IncludedRouter，需递归展开；
        # 同时交叉验证 openapi() 展开表（兼容 hidden 路由与未来重构）。
        def iter_route_paths(routes, prefix=""):
            for route in routes:
                if hasattr(route, "path"):
                    yield prefix + route.path
                elif type(route).__name__ == "_IncludedRouter":
                    sub_prefix = getattr(route.include_context, "prefix", "") or ""
                    yield from iter_route_paths(route.original_router.routes, prefix + sub_prefix)

        api_routes = [p for p in iter_route_paths(main.app.router.routes) if p.startswith("/api")]
        for path in api_routes:
            self.assertTrue(main.api_guard_covers(path), f"#342 守卫谓词未覆盖路由：{path}")
        openapi_paths = [p for p in main.app.openapi()["paths"] if p.startswith("/api")]
        for path in openapi_paths:
            self.assertTrue(main.api_guard_covers(path), f"#342 守卫谓词未覆盖 openapi 路由：{path}")
        self.assertGreater(len(api_routes), 50, "路由表遍历没抓到 /api 路由，fixture 失效")
        self.assertGreater(len(openapi_paths), 50, "openapi 展开没抓到 /api 路由，fixture 失效")

    def test_no_cors_middleware_registered(self):
        # #342：allow_origins=["*"] 的 CORSMiddleware 已删除
        from fastapi.middleware.cors import CORSMiddleware
        self.assertFalse(
            any(m.cls is CORSMiddleware for m in main.app.user_middleware),
            "CORSMiddleware 仍在中间件栈里",
        )

    def test_options_preflight_rejected_without_cors_headers(self):
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.options("/api/projects", headers={"origin": "http://evil.test"})
        self.assertEqual(response.status_code, 403, response.text[:300])
        self.assertNotIn("access-control-allow-origin", response.headers,
                         "预检响应仍带 CORS 头")

    def test_cross_origin_post_write_blocked(self):
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.post("/api/projects",
                               headers={"origin": "http://evil.test"},
                               json={"name": "跨源写入"})
        self.assertEqual(response.status_code, 403, response.text[:300])
        self.assertIn(GUARD_DETAIL, response.text)

    def test_host_spoof_without_origin_blocked(self):
        # 无 Origin 不再无条件放行：Host 不在白名单即 403（DNS 重绑定防线）
        client = TestClient(main.app, base_url="http://evil.example:8765")
        response = client.get("/api/config/token")
        self.assertEqual(response.status_code, 403, response.text[:300])

    def test_null_origin_rejected(self):
        # Origin:null（file://、sandbox 页面）不能被当成"无 Origin 的本机客户端"
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.get("/api/config/token", headers={"origin": "null"})
        self.assertEqual(response.status_code, 403, response.text[:300])

    def test_file_scheme_origin_rejected(self):
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.get("/api/config/token", headers={"origin": "file://"})
        self.assertEqual(response.status_code, 403, response.text[:300])

    def test_same_origin_browser_passes(self):
        client = TestClient(main.app, base_url=LOOPBACK_BASE,
                            headers={"origin": LOOPBACK_BASE})
        response = client.get("/api/config/token")
        self.assertEqual(response.status_code, 200, response.text[:300])

    def test_loopback_programmatic_client_no_origin_passes(self):
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.get("/api/config/token")
        self.assertEqual(response.status_code, 200, response.text[:300])

    def test_token_endpoint_never_returns_plaintext(self):
        # #342：只回"已配置"标志；明文 token 不出接口
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.get("/api/config/token")
        self.assertEqual(response.status_code, 200, response.text[:300])
        body = response.json()
        self.assertEqual(set(body.keys()), {"has_token"}, f"响应不应含明文 token：{body}")

    def test_firefly_allowed_hosts_extends_whitelist(self):
        # #344 留口：env 追加白名单后，非回环 Host 也放行（默认口径不变）
        previous = os.environ.get("FIREFLY_ALLOWED_HOSTS")
        os.environ["FIREFLY_ALLOWED_HOSTS"] = "192.168.1.10"
        try:
            client = TestClient(main.app, base_url="http://192.168.1.10:8765")
            response = client.get("/api/config/token")
            self.assertEqual(response.status_code, 200, response.text[:300])
        finally:
            if previous is None:
                os.environ.pop("FIREFLY_ALLOWED_HOSTS", None)
            else:
                os.environ["FIREFLY_ALLOWED_HOSTS"] = previous

    def test_firefly_allowed_hosts_must_be_exact_host(self):
        previous = os.environ.get("FIREFLY_ALLOWED_HOSTS")
        os.environ["FIREFLY_ALLOWED_HOSTS"] = "192.168.1.10"
        try:
            client = TestClient(main.app, base_url="http://192.168.1.11:8765")
            response = client.get("/api/config/token")
            self.assertEqual(response.status_code, 403, response.text[:300])
        finally:
            if previous is None:
                os.environ.pop("FIREFLY_ALLOWED_HOSTS", None)
            else:
                os.environ["FIREFLY_ALLOWED_HOSTS"] = previous

    def test_cross_site_form_post_blocked(self):
        # D7 补例：跨站 <form> POST 会带 Origin（浏览器对所有跨站 POST 发 Origin），
        # 即使不带 fetch 元数据、用 form 编码，也必须被 403 拒绝。
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        response = client.post(
            "/api/canvases",
            headers={"origin": "http://evil.test", "content-type": "application/x-www-form-urlencoded"},
            data="title=attack",
        )
        self.assertEqual(response.status_code, 403, response.text[:300])
        self.assertIn(GUARD_DETAIL, response.text)

    # 副作用 GET（会删数据 / 发上游请求 / spawn CLI / 写 RPC 帧 / 改任务状态）：
    # 按票面「要么 POST、要么要求必须带 Origin」选择后者，路由内 inline
    # ensure_same_origin_request（严格：Origin/Referer 必须存在且匹配）。
    STRICT_SIDE_EFFECT_GETS = [
        "/api/canvases",
        "/api/canvases/trash",
        "/api/canvas-assets",
        "/api/download-output",
        "/api/codex/status",
        "/api/gemini-cli/status",
        "/api/global-settings",
        "/api/providers/{provider_id}/fetch-models",
        "/api/runninghub/query",
        "/api/projects/{project_id}/agent-nodes/{node_id}/fork-messages",
        "/api/project-library/{project_id}/image-generation-tasks/{task_id}",
        "/api/project-library/{project_id}/video-generation-tasks/{task_id}",
        "/api/project-library/{project_id}/conversation/{node_id}/messages/{request_id}",
        "/api/project-library/{project_id}/trash/media",
        "/api/project-library/{project_id}/trash/media/{asset_id}/preview",
    ]

    def test_side_effect_gets_registered_in_route_table(self):
        # 防止清单漂移到不存在的路径：每条都得在路由表里真实存在。
        def iter_route_paths(routes, prefix=""):
            for route in routes:
                if hasattr(route, "path"):
                    yield prefix + route.path
                elif type(route).__name__ == "_IncludedRouter":
                    sub_prefix = getattr(route.include_context, "prefix", "") or ""
                    yield from iter_route_paths(route.original_router.routes, prefix + sub_prefix)

        api_paths = set(iter_route_paths(main.app.router.routes))
        for path in self.STRICT_SIDE_EFFECT_GETS:
            self.assertIn(path, api_paths, f"副作用 GET 清单路径不在路由表：{path}")

    def test_side_effect_gets_reject_no_header_and_cross_origin(self):
        # 无 Origin/Referer（攻击页 referrerpolicy=no-referrer 的 <img>/<link> GET）
        # 与外部 Origin 都必须 403；同域 Origin / Referer 不被守卫拦（状态码不是 403）。
        import re as _re
        concrete_queries = {"/api/download-output": "?url=probe"}
        for path in self.STRICT_SIDE_EFFECT_GETS:
            concrete = _re.sub(r"\{[^}]+\}", "probe", path) + concrete_queries.get(path, "")
            client = TestClient(main.app, base_url=LOOPBACK_BASE)
            response = client.get(concrete)
            self.assertEqual(response.status_code, 403,
                             f"副作用 GET 无头请求未被拒：{path} → {response.status_code}")
            response = client.get(concrete, headers={"origin": "http://evil.test"})
            self.assertEqual(response.status_code, 403,
                             f"副作用 GET 外部 Origin 未被拒：{path}")
            response = client.get(concrete, headers={"origin": LOOPBACK_BASE})
            self.assertNotEqual(response.status_code, 403,
                                f"副作用 GET 同域 Origin 被误拦：{path} → {response.text[:200]}")
            response = client.get(concrete, headers={"referer": LOOPBACK_BASE + "/some/page"})
            self.assertNotEqual(response.status_code, 403,
                                f"副作用 GET 同域 Referer 被误拦：{path} → {response.text[:200]}")


if __name__ == "__main__":
    unittest.main()
