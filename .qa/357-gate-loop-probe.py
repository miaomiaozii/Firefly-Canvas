# #357 交验探针：agent 写操作走服务端确认门——发起节点收到 confirm 事件，拒绝 → 409，零写入。
# 运行前提：起隔离实例（勿碰用户 3000）：FIREFLY_PORT=<port> FIREFLY_PROJECTS_ROOT=<temp-root> python tools/run-server.py，并按实例改下方 BASE/PID。
import asyncio, json, sys
import httpx, websockets

BASE = "http://127.0.0.1:3102"
WS = "ws://127.0.0.1:3102"
PID = "7eb09d270bb64251a43057edf56ea72c"
NODE = "gate-live-pi"
HDR = {"X-Agent-Project": PID, "Content-Type": "application/json"}

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes", headers=HDR,
                         json={"node_id": NODE, "agentCli": "pi"})
        print("create-node:", r.status_code, str(r.json())[:160])

    url = f"{WS}/api/projects/{PID}/agent-nodes/{NODE}/stream"
    async with websockets.connect(url) as ws:
        async with httpx.AsyncClient(timeout=180) as c:
            # 1) 发起 agent 写操作（带 gateQuery）→ 应阻塞在确认门
            task = asyncio.create_task(c.post(
                f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
                headers=HDR, json={"prompt": "a red circle", "node_id": "gen-target"}))
            # 2) 等 confirm 事件投影到发起节点
            confirm_id = None
            deadline = asyncio.get_event_loop().time() + 20
            while asyncio.get_event_loop().time() < deadline and not confirm_id:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("type") == "confirm":
                    confirm_id = ev.get("confirm_id")
                    print("CONFIRM-EVENT:", json.dumps({k: ev.get(k) for k in
                          ("type", "confirm_id", "tool", "message")}, ensure_ascii=False))
            if not confirm_id:
                print("FAIL: no confirm event within 20s"); sys.exit(1)
            # 3) 在发起节点上拒绝
            r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes/{NODE}/confirm",
                             headers=HDR, json={"confirm_id": confirm_id, "approved": False})
            print("reject:", r.status_code, str(r.json())[:120])
            # 4) 写操作应 409 被拒
            resp = await task
            print("trigger:", resp.status_code, resp.text[:200])
            # 5) 零写入验证
            nodes = (await c.get(f"{BASE}/api/projects/{PID}/canvas/nodes", headers=HDR)).json()
            lst = nodes if isinstance(nodes, list) else nodes.get("nodes", [])
            print("canvas node ids after reject:", [n.get("id") for n in lst])
            # 6) 事件日志里确认事件是否可回放（前端重建用）
            evs = (await c.get(f"{BASE}/api/projects/{PID}/agent-nodes/{NODE}/events",
                               headers=HDR)).json()
            conf_persisted = [e for e in (evs if isinstance(evs, list) else evs.get("events", []))
                              if (e.get("event") or e).get("type") == "confirm"]
            print("confirm persisted in replay log:", len(conf_persisted) > 0)

asyncio.run(main())
