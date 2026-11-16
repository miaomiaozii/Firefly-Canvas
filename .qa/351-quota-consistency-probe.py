# 运行前提：隔离实例（如 3109）+ 独立 FIREFLY_PROJECTS_ROOT，勿碰用户 3000 实例；
# PROBE_PID 环境变量传入项目 id；需 tabbit 或浏览器打开该项目画布页使 MCP 工具面在位。
# #351 回归探针：默认上限 20 下 agent 连发 21 次生图——第 20 次 200、第 21 次 429、
# 拒绝不 spawn 进程；再经 MCP 面（POST /api/mcp?project=…&node=…）复测第 21/22 次，
# 判定 MCP 与 REST 是否共用同一每节点会话额度计数器（结论 2026-11-16：共用，视图一致）。
# 更名记录：原名 .qa/387-quota-probe.py——与真 #387（agent-events/删除 409）无关，
# 按 review 2026-11-16 17:23 裁定改名入库。
import asyncio, json, os
import httpx, websockets

BASE = "http://127.0.0.1:3109"
PID = os.environ["PROBE_PID"]
NODE = "agent387x"
HDR = {"X-Agent-Project": PID, "Content-Type": "application/json"}
MCP = f"{BASE}/api/mcp?project={PID}&node={NODE}"


async def confirm_respond(c, approved):
    async with websockets.connect(
            f"{BASE.replace('http','ws')}/api/projects/{PID}/agent-nodes/{NODE}/stream") as ws:
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
            if ev.get("type") == "confirm":
                await c.post(f"{BASE}/api/projects/{PID}/agent-nodes/{NODE}/confirm",
                             headers=HDR, json={"confirm_id": ev["confirm_id"], "approved": approved})
                return


async def confirm_approve(c):
    # 门挂起期间从节点 WS 拿 confirm 再批准
    await confirm_respond(c, True)


async def rest_round(c, target, i):
    t = asyncio.create_task(c.post(
        f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
        headers=HDR, json={"prompt": "a red circle", "node_id": target}))
    try:
        await confirm_approve(c)
    except Exception as e:
        resp = await t
        print(f"REST#{i}: (no gate: {type(e).__name__}) HTTP={resp.status_code} {resp.text[:150]}")
        return resp.status_code
    resp = await t
    print(f"REST#{i}: HTTP={resp.status_code} {resp.text[:150]}")
    return resp.status_code


MCP_HDR = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}


async def mcp_round(s, i):
    res = await s.post(f"{BASE}/api/mcp?project={PID}", headers=MCP_HDR,
                       json={"jsonrpc": "2.0", "id": i, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05",
                                        "capabilities": {},
                                        "clientInfo": {"name": "probe", "version": "1"}}})
    sid = res.headers.get("mcp-session-id")
    hdr = {**MCP_HDR, "mcp-session-id": sid}
    t = asyncio.create_task(s.post(MCP, headers=hdr, json={
        "jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
        "params": {"name": "generate.trigger",
                   "arguments": {"prompt": "a red circle"}}}))
    try:
        await confirm_approve(s)
    except Exception as e:
        resp = await t
        print(f"MCP#{i}: (no gate: {type(e).__name__}) HTTP={resp.status_code} {resp.text[:200]}")
        return resp.status_code
    resp = await t
    body = resp.text
    verdict = "QUOTA-REJECT" if "#351" in body else ("ERR" if '"isError":true' in body else "OK")
    print(f"MCP#{i}: HTTP={resp.status_code} verdict={verdict} tail={body[-160:]}")
    return verdict


async def main():
    async with httpx.AsyncClient(timeout=40) as c:
        if os.environ.get("PHASE") == "mcp":
            target = os.environ["TARGET_NODE"]
        else:
            r = await c.post(f"{BASE}/api/projects/{PID}/canvas/nodes", headers=HDR,
                             json={"node": {"type": "text", "prompt": "a red circle", "x": 10, "y": 10}})
            target = str((r.json() or {}).get("node_id") or (r.json() or {}).get("node", {}).get("id"))
        r = await c.post(f"{BASE}/api/projects/{PID}/agent-nodes", headers=HDR,
                         json={"node_id": NODE, "agentCli": "pi"})
        print("node:", r.status_code, str(r.json())[:100])
        # 预检：trigger 确实会挂在门上；用**驳回**完成预检（拒绝不耗额度）
        t = asyncio.create_task(c.post(
            f"{BASE}/api/projects/{PID}/generate/trigger?agent_node_id={NODE}",
            headers=HDR, json={"prompt": "a red circle", "node_id": target}))
        try:
            await confirm_respond(c, False)
            resp = await t
            print(f"preflight: gate hung, reject -> HTTP={resp.status_code} (quota not consumed)")
        except Exception:
            resp = await t
            print(f"preflight NO GATE: HTTP={resp.status_code} {resp.text[:200]}")
            raise SystemExit(2)

    codes = []
    if os.environ.get("PHASE") != "mcp":
      async with httpx.AsyncClient(timeout=180) as c:
        for i in range(1, 22):
            codes.append(await rest_round(c, target, i))
        print("REST summary:", f"20th={codes[19]}", f"21st={codes[20]}")

    if os.environ.get("PHASE", "rest") == "rest":
        return
    async with httpx.AsyncClient(timeout=180) as s:
        # MCP 面（真实路径 POST /api/mcp；需浏览器打开该项目画布页）：
        # 不重置额度，直接续 REST 的计数打第 21/22 次 → 预期被共享限额拒。
        mc = []
        for i in range(21, 23):
            mc.append(await mcp_round(s, i))
        print("MCP summary:", f"21st={mc[0]}", f"22nd={mc[1]}")

asyncio.run(main())
