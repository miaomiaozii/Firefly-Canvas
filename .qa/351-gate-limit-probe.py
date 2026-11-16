# #351 交验探针：生成确认门（批准/超时两分支）+ 每会话生成上限 429 + 结果/历史落盘。
# 运行前提：起隔离实例（勿碰用户 3000）：FIREFLY_PORT=<port> FIREFLY_PROJECTS_ROOT=<temp-root> python tools/run-server.py，并按实例改下方 BASE/PID。
import asyncio, json, sys
import httpx, websockets

BASE = "http://127.0.0.1:3104"
WS = "ws://127.0.0.1:3104"
PID = "7eb09d270bb64251a43057edf56ea72c"
NODE = "agent351b"
TARGET = "gen-351-a"
HDR = {"X-Agent-Project": PID, "Content-Type": "application/json"}


async def get_confirm(ws, c, tool_hint):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=25)
        ev = json.loads(raw)
        if ev.get("type") == "confirm":
            print(f"  CONFIRM: id={ev['confirm_id']} tool={ev.get('tool')} msg={ev.get('message')!r}")
            return ev["confirm_id"]
        # 忽略 session/status 等控制事件


async def approve(c, cid, approved):
    r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes/{NODE}/confirm",
                     headers=HDR, json={"confirm_id": cid, "approved": approved})
    print(f"  confirm({approved}):", r.status_code)


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes", headers=HDR,
                         json={"node_id": NODE, "agentCli": "pi"})
        print("agent-node:", r.status_code, str(r.json())[:120])
        # 目标节点带 prompt（生成对象）；agent 面 create_node 服务端生成 id
        r = await c.post(f"{BASE}/api/projects/{PID}/canvas/nodes", headers=HDR,
                         json={"node": {"type": "text", "prompt": "a red circle",
                                        "x": 10, "y": 10}})
        print("target-node:", r.status_code, str(r.json())[:160])
        globals()["TARGET"] = str((r.json() or {}).get("node_id") or TARGET)

    async with websockets.connect(f"{WS}/api/projects/{PID}/agent-nodes/{NODE}/stream") as ws:
        async with httpx.AsyncClient(timeout=200) as c:
            task_ids = []
            for i in (1, 2):
                print(f"TRIGGER #{i}")
                t = asyncio.create_task(c.post(
                    f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
                    headers=HDR, json={"prompt": "a red circle", "node_id": TARGET}))
                cid = await get_confirm(ws, c, i)
                await approve(c, cid, True)
                resp = await t
                body = resp.json() if resp.status_code == 200 else {}
                if resp.status_code == 200:
                    task_ids.append(body.get("task_id"))
                print(f"  trigger#{i}: HTTP={resp.status_code} {str(body)[:160]}")

            # 上限：第 3 次（先过门后过限额 → 批准后 429）
            print("TRIGGER #3 (limit)")
            t = asyncio.create_task(c.post(
                f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
                headers=HDR, json={"prompt": "a red circle", "node_id": TARGET}))
            cid = await get_confirm(ws, c, 3)
            await approve(c, cid, True)
            resp = await t
            print(f"  trigger#3: HTTP={resp.status_code} {resp.text[:200]}")

            # 超时分支：第 4 次不批准，等 90s 门超时 → 应 409，零写入
            print("TRIGGER #4 (timeout branch, waiting 90s...)")
            t = asyncio.create_task(c.post(
                f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
                headers=HDR, json={"prompt": "a red circle", "node_id": TARGET}))
            cid = await get_confirm(ws, c, 4)
            resp = await t  # 不批准，等超时
            print(f"  trigger#4: HTTP={resp.status_code} {resp.text[:200]}")

            # 任务终态 + 结果节点
            for tid in task_ids:
                st = (await c.get(f"{BASE}/api/projects/{PID}/generate/tasks/{tid}",
                                  headers=HDR)).json()
                print(f"TASK {tid}: status={st.get('status')} err={str(st.get('error'))[:120]}"
                      f" result_node={st.get('result',{}).get('result_node_id','')}")
            nodes = (await c.get(f"{BASE}/api/projects/{PID}/canvas/nodes", headers=HDR)).json()
            lst = nodes if isinstance(nodes, list) else nodes.get("nodes", [])
            print("canvas nodes:", [n.get("id") for n in lst])

asyncio.run(main())
