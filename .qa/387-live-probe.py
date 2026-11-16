# #387 live 验收：带真实对话的项目删除后，原目录不再出现 agent-events/；
# 再次删除/清理不返回 409。隔离实例（3110 + FIREFLY_PROJECTS_ROOT=iso-387b）。
import asyncio, json, os, sys
from pathlib import Path
import httpx

ROOT = Path(os.environ["ISO_ROOT"])
BASE = "http://127.0.0.1:3110"


async def main():
    HDR = {"Origin": BASE, "Referer": f"{BASE}/"}
    async with httpx.AsyncClient(timeout=60, headers=HDR) as c:
        r = await c.post(f"{BASE}/api/project-library", json={"name": "live387"})
        print("create-project:", r.status_code)
        pid = r.json()["project"]["id"]
        live = ROOT / pid
        print("live dir:", live)

        r = await c.post(f"{BASE}/api/projects/{pid}/agent-nodes",
                         json={"node_id": "agent387", "agentCli": "pi"})
        print("create-node:", r.status_code, str(r.json())[:120])

        # 真实对话：pi 宿主跑一轮
        r = await c.post(f"{BASE}/api/projects/{pid}/agent-nodes/agent387/prompt",
                         json={"message": "Reply with exactly: ok387"})
        print("prompt:", r.status_code, str(r.json())[:120])
        events_seen = False
        for _ in range(60):
            await asyncio.sleep(1)
            f = live / "agent-events" / "agent387.jsonl"
            if f.is_file() and f.stat().st_size > 0:
                events_seen = True
                break
        print("agent-events BEFORE delete:", events_seen,
              [str(p.relative_to(live)) for p in live.glob("agent-events/*")] if events_seen else "")
        r = await c.get(f"{BASE}/api/projects/{pid}/agent-nodes/agent387/status")
        print("node status:", r.status_code, str(r.json())[:150])

        # 删除（入回收区）——宿主应先被停掉，不得再写回
        r = await c.delete(f"{BASE}/api/project-library/{pid}")
        print("DELETE(trash):", r.status_code, str(r.json())[:160])
        for _ in range(10):
            await asyncio.sleep(1)
            if live.exists():
                print("LIVE REBUILT!", [str(p) for p in live.rglob("*")][:10])
                break
        print("live dir exists after 10s:", live.exists())

        # 再次删除：不许 409
        r = await c.delete(f"{BASE}/api/project-library/{pid}")
        print("DELETE#2:", r.status_code, str(r.json())[:120])

        # 手工再造残留（模拟历史脏数据）→ 再删一次：404 且残留被顺手清掉
        (live / "agent-events").mkdir(parents=True, exist_ok=True)
        (live / "agent-events" / "junk.jsonl").write_text('{"x":1}\n', encoding="utf-8")
        r = await c.delete(f"{BASE}/api/project-library/{pid}")
        print("DELETE#3(with manual leftover):", r.status_code, str(r.json())[:120])
        print("leftover cleaned:", not live.exists())

        # 清理回收区：不许 409
        r = await c.delete(f"{BASE}/api/project-library/trash/{pid}")
        print("PURGE:", r.status_code, str(r.json())[:120])
        print("trash dir exists:", (ROOT.parent / (str(ROOT) + "-trash") if False else Path(str(ROOT) + "-trash")).exists())

        # 停节点确认进程已死（防写回）
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -match 'firefly-agent-agent387' } | Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True)
        print("pi host procs for agent387:", out.stdout.strip() or out.stderr.strip())

asyncio.run(main())
