# #351 review 复测：429 → DELETE agent 节点 → 同 node_id 重建 → 能再次生成。
# 运行前提：起隔离实例（勿碰用户 3000）：FIREFLY_PORT=<port> FIREFLY_PROJECTS_ROOT=<temp-root> python tools/run-server.py，并按实例改下方 BASE/PID。
import asyncio, json
import httpx, websockets

BASE = "http://127.0.0.1:3106"
PID = "7eb09d270bb64251a43057edf56ea72c"
NODE = "agent351y"
HDR = {"X-Agent-Project": PID, "Content-Type": "application/json"}


async def confirm_approve(ws, c):
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
        if ev.get("type") == "confirm":
            print(f"  CONFIRM {ev['confirm_id']} {ev.get('tool')}")
            r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes/{NODE}/confirm",
                             headers=HDR, json={"confirm_id": ev["confirm_id"], "approved": True})
            print(f"  approve: {r.status_code}")
            return


async def trigger(c, target):
    t = asyncio.create_task(c.post(
        f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
        headers=HDR, json={"prompt": "a red circle", "node_id": target}))
    return t


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/projects/{PID}/canvas/nodes", headers=HDR,
                         json={"node": {"type": "text", "prompt": "a red circle", "x": 10, "y": 10}})
        target = str((r.json() or {}).get("node_id") or (r.json() or {}).get("node", {}).get("id"))
        print("target:", target)
        r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes", headers=HDR,
                         json={"node_id": NODE, "agentCli": "pi"})
        print("create:", r.status_code, str(r.json())[:100])

    async with websockets.connect(f"{BASE.replace('http','ws')}/api/projects/{PID}/agent-nodes/{NODE}/stream") as ws:
        async with httpx.AsyncClient(timeout=120) as c:
            for i in (1, 2):
                t = await trigger(c, target)
                await confirm_approve(ws, c)
                resp = await t
                print(f"TRIGGER#{i}: HTTP={resp.status_code} {resp.text[:120]}")
            t = await trigger(c, target)
            await confirm_approve(ws, c)
            resp = await t
            print(f"TRIGGER#3(should 429): HTTP={resp.status_code} {resp.text[:200]}")

    # DELETE 节点 → 重建 → 再生成（额度应回 0）
    async with httpx.AsyncClient(timeout=30) as c:
        d = await c.delete(f"{BASE}/api/projects/{PID}/agent-nodes/{NODE}", headers=HDR)
        print("DELETE:", d.status_code, d.text[:80])
        r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes", headers=HDR,
                         json={"node_id": NODE, "agentCli": "pi"})
        print("RECREATE:", r.status_code, str(r.json())[:100])

    async with websockets.connect(f"{BASE.replace('http','ws')}/api/projects/{PID}/agent-nodes/{NODE}/stream") as ws:
        async with httpx.AsyncClient(timeout=120) as c:
            t = await trigger(c, target)
            await confirm_approve(ws, c)
            resp = await t
            print(f"TRIGGER#4 after rebuild: HTTP={resp.status_code} {resp.text[:160]}")

asyncio.run(main())
