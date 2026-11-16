"""Issue #342（重开轮）：WebSocket 面的同源守卫覆盖。

结论链（review 21:19 要求先证再修）：
- /api/projects/{pid}/agent-nodes/{nid}/stream：api_agent_nodes.py:22 的
  APIRouter(dependencies=[Depends(ensure_agent_same_origin)]) 对 WS 路由同样生效，
  外部 Origin 升级被拒（WebSocketDenialResponse 403）。本文件把它钉成回归，
  不再叠加重复校验。
- /ws/stats：此前只有 client_id=ps_* 有 _ensure_photoshop_loopback，canvas_/匿名
  连接对任意网页开放。修复 = accept 前 ensure_agent_same_origin(websocket)，
  口径与 protect_api_origin 中间件（main.py）完全一致。

覆盖：
- stream：evil Origin 拒 / Origin null 拒 / 同域放行（真节点，accept 后立刻关）
- ws/stats：evil Origin 拒 / Origin null 拒 / 同域放行 / 无 Origin 程序化放行 /
  ps_ 客户端回环校验保留（守卫在前、回环在后）
- 写路由逐条矩阵：全部 /api 写路由 OPTIONS+POST 带 evil Origin → 403，
  且响应不带 access-control-allow-origin（逐条打印，tee 到 evidence 即逐条结果）
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore

LOOPBACK_BASE = "http://127.0.0.1:8765"
EVIL_ORIGIN = "http://evil.test"
GUARD_DETAIL = "只允许从当前应用页面操作本机数据"


def iter_route_paths(routes, prefix=""):
    for route in routes:
        if hasattr(route, "path"):
            yield prefix + route.path, sorted(set(getattr(route, "methods", None) or set()))
        elif type(route).__name__ == "_IncludedRouter":
            sub = getattr(route.include_context, "prefix", "") or ""
            yield from iter_route_paths(route.original_router.routes, prefix + sub)


def ws_handshake_probe(client, path, origin=None):
    """返回 'CONNECTED' / 'CLOSED'（握手被拒或 accept 后即关）。
    本版 starlette TestClient 无 WebSocketDenialResponse：拒升级只会抛
    WebSocketDisconnect，区分不了 403 与 accept-后-关；真实 403 语义由真实例
    （websockets/tabbit）探测钉，单测钉“连不上”这个硬行为。host 必须显式传：
    WS 握手默认 Host=testserver，会被回环白名单拦掉（实测）。"""
    import gc
    headers = {"host": "127.0.0.1:8765"}
    if origin is not None:
        headers["origin"] = origin
    cm = client.websocket_connect(path, headers=headers)
    try:
        cm.__enter__()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        return f"CLOSED:{status}" if status is not None else "CLOSED"
    cm.__exit__(None, None, None)
    gc.collect()
    return "CONNECTED"


class WsStatsOriginGuardTests(unittest.TestCase):
    """#342：/ws/stats 握手同源守卫（缺口修复）。"""

    def _ws(self, path, origin=None):
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        return ws_handshake_probe(client, path, origin)

    def test_ws_stats_evil_origin_denied(self):
        self.assertEqual(self._ws("/ws/stats?client_id=canvas_probe", EVIL_ORIGIN),
                         "CLOSED:403")

    def test_ws_stats_null_origin_denied(self):
        self.assertEqual(self._ws("/ws/stats?client_id=canvas_probe", "null"),
                         "CLOSED:403")

    def test_ws_stats_same_origin_connects(self):
        self.assertEqual(self._ws("/ws/stats?client_id=canvas_probe", LOOPBACK_BASE),
                         "CONNECTED")

    def test_ws_stats_programmatic_clients_unchanged(self):
        # MCP / 前端同源外的程序化形态：无 Origin 放行（票面边界，认证归 #344）
        self.assertEqual(self._ws("/ws/stats?client_id=canvas_probe"), "CONNECTED")
        # ps_ 回环校验保留且仍在守卫之后（accept 后断开 = 旧行为 CLOSED）
        self.assertEqual(self._ws("/ws/stats?client_id=ps_probe"), "CLOSED")


class AgentStreamOriginRegressionTests(unittest.TestCase):
    """#342：stream 的守卫来自路由级依赖（#240），证伪「accept 前零校验」并钉成回归。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-342r-")
        self.root = Path(self.temp.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("票342r-stream")["id"]
        self.client = TestClient(main.app, base_url=LOOPBACK_BASE,
                                 headers={"origin": LOOPBACK_BASE})

    def tearDown(self):
        # 关掉 REST 创建时拉起的宿主会话，否则残留 pi 子进程会拖住解释器退出
        # （实测：不退时 pytest 跑完打完全行后挂 5 分钟）。
        try:
            self.client.delete(f"/api/projects/{self.project_id}/agent-nodes/live-node")
        except Exception:
            pass
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp.cleanup()

    def _ws_stream(self, origin=None):
        return ws_handshake_probe(
            self.client,
            f"/api/projects/{self.project_id}/agent-nodes/live-node/stream",
            origin)

    def test_stream_evil_origin_denied_by_router_dependency(self):
        self.assertEqual(self._ws_stream(EVIL_ORIGIN), "CLOSED:403")

    def test_stream_null_origin_denied(self):
        self.assertEqual(self._ws_stream("null"), "CLOSED:403")

    def test_stream_same_origin_connects(self):
        # 真节点存在时同域放行（accept 后才进会话管理）——证明守卫不误伤前端。
        created = self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes",
            json={"node_id": "live-node"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(self._ws_stream(LOOPBACK_BASE), "CONNECTED")


class WriteRouteOriginMatrixTests(unittest.TestCase):
    """#342 验收「逐条」：每条 /api 写路由的 OPTIONS+POST 跨源都是 403，无 CORS 头。"""

    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def test_every_write_route_rejects_cross_origin(self):
        routes = [(p, [m for m in mm if m in self.WRITE_METHODS])
                  for p, mm in iter_route_paths(main.app.router.routes)
                  if any(m in self.WRITE_METHODS for m in mm)]
        self.assertGreaterEqual(len(routes), 155, "写路由数少于 155，路由展开 fixture 失效")
        client = TestClient(main.app, base_url=LOOPBACK_BASE)
        failures = []
        for path, methods in sorted(routes):
            concrete = re.sub(r"\{[^}]+\}", "probe", path)
            for method in ("OPTIONS", "POST"):
                response = client.request(method, concrete,
                                          headers={"origin": EVIL_ORIGIN},
                                          json={})
                line = f"{method} {concrete} [{','.join(methods)}] -> {response.status_code}"
                print(line)
                if response.status_code != 403:
                    failures.append(line + f" detail={response.text[:120]}")
                if "access-control-allow-origin" in {k.lower() for k in response.headers}:
                    failures.append(line + " 仍带 access-control-allow-origin")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
