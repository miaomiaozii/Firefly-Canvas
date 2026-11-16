# #389 live 验收：确认门发出的 confirm 事件写入事件回放日志——确认挂起期间
# 「刷新页面」（新客户端全量回放 since=0）能重建待决确认卡并可批准；结束后不再重放。
# 隔离实例：FIREFLY_PORT=3111 FIREFLY_PROJECTS_ROOT=%TEMP%\iso-389 python tools/run-server.py
import asyncio, json, sys
import httpx, websockets

BASE = "http://127.0.0.1:3111"
WS = "ws://127.0.0.1:3111"
HDR = {"X-Agent-Project": "", "Content-Type": "application/json"}
NODE = "agent389"


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/project-library", json={"name": "live389"})
        print("create-project:", r.status_code)
        pid = r.json()["project"]["id"]
        HDR["X-Agent-Project"] = pid
        r = await c.post(f"{BASE}/api/projects/{pid}/agent-nodes", headers=HDR,
                         json={"node_id": NODE, "agentCli": "pi"})
        print("agent-node:", r.status_code, str(r.json())[:120])
        r = await c.post(f"{BASE}/api/projects/{pid}/canvas/nodes", headers=HDR,
                         json={"node": {"type": "text", "prompt": "a red circle", "x": 10, "y": 10}})
        target = str((r.json() or {}).get("node_id") or "")
        print("target-node:", r.status_code, target)

    async with websockets.connect(f"{WS}/api/projects/{pid}/agent-nodes/{NODE}/stream") as ws:
        async with httpx.AsyncClient(timeout=200) as c:
            print("TRIGGER (gated write)")
            t = asyncio.create_task(c.post(
                f"{BASE}/api/projects/{pid}/generate/trigger?agent_node_id={NODE}",
                headers=HDR, json={"prompt": "a red circle", "node_id": target}))
            cid = None
            while cid is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if ev.get("type") == "confirm":
                    cid = ev["confirm_id"]
                    print(f"  live CONFIRM: id={cid} tool={ev.get('tool')} seq={ev.get('seq')}")
                    assert ev.get("seq"), "live confirm 必须带 seq（回放链同源）"

            # —— 模拟「刷新页面」：全新客户端只拿回放日志（since=0）——
            d = (await c.get(f"{BASE}/api/projects/{pid}/agent-nodes/{NODE}/events?since=0")).json()
            pend = [rec for rec in d.get("events", []) if rec.get("event", {}).get("type") == "confirm"]
            print(f"  replay@refresh(since=0): {len(d.get('events', []))} recs, pending confirms={len(pend)}")
            assert pend, "确认挂起期间回放日志必须含 confirm（刷新可重建）"
            assert pend[-1]["event"]["confirm_id"] == cid, "重建的卡必须指向同一 confirm_id"

            # —— 用回放里看到的 id 批准（前端重建卡后的批准路径）——
            r = await c.post(f"{BASE}/api/projects/{pid}/agent-nodes/{NODE}/confirm",
                             headers=HDR, json={"confirm_id": cid, "approved": True})
            print("  approve-from-replay:", r.status_code)
            resp = await t
            print("  trigger:", resp.status_code, str(resp.json())[:150])

            # —— 结束后：回放含 confirm_resolved；live WS 也收到 ——
            resolved = None
            while resolved is None:
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if ev.get("type") == "confirm_resolved":
                    resolved = ev
            print(f"  live CONFIRM_RESOLVED: approved={resolved.get('approved')} reason={resolved.get('reason')} seq={resolved.get('seq')}")
            d2 = (await c.get(f"{BASE}/api/projects/{pid}/agent-nodes/{NODE}/events?since=0")).json()
            types = [rec.get("event", {}).get("type") for rec in d2.get("events", [])]
            print("  replay types:", types)
            assert "confirm_resolved" in types
            i_c, i_r = types.index("confirm"), types.index("confirm_resolved")
            assert i_c < i_r and d2["events"][i_r]["event"]["approved"] is True
            last_seq = d2["events"][-1]["seq"]

            # 再批准一次同一 id（迟到的旧卡）→ 服务端无该门处理器；且日志不再新增 confirm
            r = await c.post(f"{BASE}/api/projects/{pid}/agent-nodes/{NODE}/confirm",
                             headers=HDR, json={"confirm_id": cid, "approved": True})
            d3 = (await c.get(f"{BASE}/api/projects/{pid}/agent-nodes/{NODE}/events?since={last_seq}")).json()
            print("  late re-approve:", r.status_code, "| new events since last_seq:", len(d3.get("events", [])))
    print("OK #389 live acceptance passed")

asyncio.run(main())
