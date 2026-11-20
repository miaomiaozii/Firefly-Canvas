"""Run Tabbit QA against a runner-owned Firefly instance; never reuse a running app."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.e2e.e2e_harness import seed_mutations
from tools.process_tree import kill_process_tree

SCENARIO_SCRIPTS = tuple(f"tools/e2e/{p.name}" for p in sorted((ROOT / "tools/e2e").glob("scenario_*.sh")))
QA_SCRIPT = "tools/e2e_qa.sh"
ENTRY_SCRIPTS = SCENARIO_SCRIPTS + (QA_SCRIPT,)
SCENARIO_TIMEOUTS = {script: {
    "tools/e2e/scenario_delete_undo.sh": 720,
    "tools/e2e/scenario_keys.sh": 420,
    "tools/e2e/scenario_mouse.sh": 420,
    "tools/e2e/scenario_ux_rules.sh": 360,
    QA_SCRIPT: 240,
}.get(script, 360) for script in ENTRY_SCRIPTS}
# 给 release 门的其他步骤保留预算；各套件预算还要受本次总上限约束。
RUN_TIMEOUT_SECONDS = 540
PORTS = range(3100, 3200)
PREAMBLE = ROOT / "tools/e2e/scenario_preamble.js"


def launcher() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Tabbit/LocalAgent/bin/tabbit-cli.exe"
    return Path.home() / ".local/bin/tabbit-cli"


def isolated_env(work: Path, port: int) -> dict[str, str]:
    if port not in PORTS:
        raise ValueError("Only 31xx ports are allowed")
    # Do not inherit API keys, root overrides, or CLI configuration from the caller.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "PATHEXT", "PROGRAMFILES", "PROGRAMDATA"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    folders = {"HOME": "home", "USERPROFILE": "home", "APPDATA": "home/roaming", "LOCALAPPDATA": "home/local",
               "TMP": "tmp", "TEMP": "tmp", "TMPDIR": "tmp", "FIREFLY_RUNTIME_ROOT": "runtime",
               "FIREFLY_PROJECTS_ROOT": "projects"}
    for key, name in folders.items():
        path = work / name
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(FIREFLY_PORT=str(port), FIREFLY_AGENT_API_BASE=f"http://127.0.0.1:{port}", PYTHONUTF8="1")
    return env


def pick_port() -> int:
    for port in PORTS:
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError("No free loopback port in 3100–3199")


def serve(work: Path, port: int, nonce: str) -> None:
    work = work.resolve(strict=True)
    for key, expected in (("FIREFLY_RUNTIME_ROOT", "runtime"), ("FIREFLY_PROJECTS_ROOT", "projects")):
        if not os.environ.get(key) or Path(os.environ[key]).resolve() != work / expected:
            raise RuntimeError(f"{key} not owned by this run")
    if port not in PORTS:
        raise ValueError("Only 31xx ports are allowed")
    import uvicorn
    import main

    # Legacy startup migrations and CLI probes are unrelated to browser input QA.
    main.app.router.on_startup.clear()

    @main.app.get(f"/e2e-ready/{nonce}")
    async def ready():
        return {"nonce": nonce}

    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(base: str, method: str, path: str, body=None, timeout=5):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Origin": base, "Content-Type": "application/json"}
    req = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def wait_ready(base: str, nonce: str, process) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and process.poll() is None:
        try:
            if request(base, "GET", f"/e2e-ready/{nonce}", timeout=.5) == {"nonce": nonce}:
                return
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise RuntimeError("Runner-owned server not confirmed; no project requests made")


def create_project(base: str, token: str, agent: bool):
    name = f"e2e-tabbit-{token}"
    project = request(base, "POST", "/api/project-library", {"name": name})["project"]
    pid = project["id"]
    if len(pid) != 32 or any(c not in "0123456789abcdef" for c in pid) or project["name"] != name:
        raise RuntimeError("Unexpected fixture identity")
    seeds = seed_mutations("e2e", 2, True, agent)
    request(base, "PATCH", f"/api/project-library/{pid}/canvas", {
        "mutations": seeds, "base_revision": 0, "client_id": "e2e-harness",
    })
    print(f"SEEDED project={pid} name={name}", flush=True)
    nodes = [s["node"] for s in seeds if "node" in s]
    return pid, {
        "nodeIds": sorted(n["id"] for n in nodes),
        "nodes": sorted(f"{n['id']}@{n['x']},{n['y']}" for n in nodes),
        "edges": sorted(f"{e['id']}:{e['from']}>{e['to']}" for e in (s["edge"] for s in seeds if "edge" in s)),
    }


def register(script: str, bash: str, directory: Path):
    directory.mkdir()
    env = {**os.environ, "FIREFLY_E2E_REGISTRY": directory.as_posix(), "PYTHONUTF8": "1"}
    proc = subprocess.run([bash, script], cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=15)
    if proc.returncode:
        raise RuntimeError(f"Registering {script}: {proc.stderr}")
    if proc.stdout:
        print(proc.stdout.rstrip())
    suite, title, timeout, agent = (directory / "suite").read_text(encoding="utf-8").splitlines()
    return suite, title, int(timeout), agent == "1", sorted(directory.glob("*.js"))


def tabbit_call(cli: Path, args: list[str], log: Path, timeout: float, program: Path | None = None):
    with (program or Path(os.devnull)).open("rb") as stdin:
        proc = subprocess.run([str(cli), *args], stdin=stdin, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    log.with_suffix(".stdout.json").write_text(proc.stdout, encoding="utf-8")
    log.with_suffix(".stderr.log").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode:
        raise RuntimeError(f"Tabbit exit {proc.returncode}: {proc.stderr[-500:] or proc.stdout[-500:]}")
    return json.loads(proc.stdout)


def browser_result(response: dict, screenshot: Path) -> tuple[str, str]:
    if response.get("status") != "succeeded":
        return "fail", f"Tabbit did not complete: {response.get('error') or response.get('status')}"
    receipt = response.get("result")
    if not isinstance(receipt, dict) or not isinstance(payload := receipt.get("value"), dict):
        return "fail", "Missing structured browser result"
    shot = (receipt.get("nextAction") or {}).get("path")
    has_screenshot = bool(shot and Path(shot).is_file())
    if has_screenshot:
        shutil.copy2(shot, screenshot)
    result = payload.get("scene")
    if not isinstance(result, dict):
        return "fail", "Missing structured scene result"
    if result.get("error"):
        return "fail", str(result["error"])
    if isinstance(result.get("skip"), str) and result["skip"].startswith("KNOWN-GAP:"):
        return "skip", result["skip"]
    if result.get("evidence") and not result.get("skip"):
        if not has_screenshot:
            return "fail", f"No screenshot: {payload.get('shotError') or 'no artifact'}"
        return "pass", str(result["evidence"])
    return "fail", "Scene did not produce evidence"


def run_scene(cli: Path, base: str, title: str, scene: Path, timeout: int, agent: bool, evidence: Path):
    token = uuid.uuid4().hex[:12]
    pid, seed = create_project(base, token, agent)
    name = scene.stem.split("-", 1)[1]
    task = f"{title} {name} {token}"
    url = f"{base}/static/smart-canvas.html?id={pid}&project={pid}&project_mode=1&focus_node=e2e-n1"
    prelude = {"E2E_BASE": base, "E2E_PID": pid, "E2E_URL": url, "E2E_SEED": seed}
    program = scene.with_name(scene.stem + ".program.js")
    program.write_text(
        "\n".join(f"const {key} = {json.dumps(value)};" for key, value in prelude.items())
        + "\nlet scene, screenshot, shotError;\ntry {\n"
        + PREAMBLE.read_text(encoding="utf-8") + "\nscene = await (async () => {\n"
        + scene.read_text(encoding="utf-8") + "\n})();\n"
        + "} catch (e) { scene = {error: String(e.stack || e).slice(0, 1500)}; }\n"
        + "try { screenshot = await page.screenshot({timeout: 8000, fullPage: false}); }\n"
        + "catch(e) { shotError = String(e); }\nreturn {scene, screenshot, shotError};\n",
        encoding="utf-8",
    )
    log = evidence / name
    try:
        deadline = max(timeout, 60000)  # minimum supported by Tabbit
        response = tabbit_call(cli, ["nodejs", "--task", task, "--request-id", token,
                                    "--timeout-ms", str(deadline)], log, deadline / 1000 + 12, program)
        result = browser_result(response, evidence / f"{name}.png")
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, KeyError) as exc:
        result = "fail", f"{type(exc).__name__}: {exc}"
    finally:
        # Every task starts a new owned fixture tab. Never acquire or close the user's tabs.
        try:
            cleanup = tabbit_call(cli, ["finish", "--task", task, "--discard"], evidence / f"{name}-finish", 12)
            if not cleanup.get("finished") or cleanup.get("forcedCleanup"):
                raise RuntimeError(f"Unconfirmed Tabbit cleanup: {cleanup}")
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, KeyError) as exc:
            result = "fail", f"{result[1]}; Tabbit cleanup failed: {exc}"
        # All disk cleanup happens after the server stops, via TemporaryDirectory.
    return name, *result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", choices=ENTRY_SCRIPTS)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / ".qa/artifacts/tabbit")
    args = parser.parse_args()
    cli, bash = launcher(), shutil.which("bash")
    if not cli.is_file() or not bash:
        # #310: the synthetic quality-gate home deliberately lacks a real Tabbit install.
        # Report zero executed scenarios; no gate budget or sandbox bypass.
        print("TABBIT-E2E NOT_RUN: launcher or bash unavailable; pass=0 fail=0 skip=0")
        return 0 if os.environ.get("FIREFLY_QUALITY_GATE_GUARD_EVENTS") else 2
    evidence = args.evidence_dir.resolve() / uuid.uuid4().hex[:12]
    evidence.mkdir(parents=True)
    tally = {"pass": 0, "fail": 0, "skip": 0}
    rows = []
    run_deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="e2e-tabbit-") as work_name:
        work = Path(work_name).resolve()
        port, nonce = pick_port(), uuid.uuid4().hex
        env = isolated_env(work, port)
        base = f"http://127.0.0.1:{port}"
        print(f"E2E origin={base} runtime={env['FIREFLY_RUNTIME_ROOT']} projects={env['FIREFLY_PROJECTS_ROOT']} evidence={evidence}", flush=True)
        with (evidence / "server.log").open("wb") as server_log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(work),
                                       str(port), nonce], cwd=ROOT, env=env, stdout=server_log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(base, nonce, process)
                for index, script in enumerate(args.suite or ENTRY_SCRIPTS):
                    suite, title, timeout, agent, scenes = register(script, bash, work / f"suite-{index}")
                    deadline = min(run_deadline, time.monotonic() + SCENARIO_TIMEOUTS[script])
                    counts = dict.fromkeys(tally, 0)
                    for scene in scenes:
                        remaining_ms = int((deadline - time.monotonic() - 35) * 1000)
                        if remaining_ms < 60000:
                            name = scene.stem.split("-", 1)[1]
                            status, detail = "fail", "Suite or run deadline exhausted"
                        else:
                            name, status, detail = run_scene(cli, base, title, scene, min(timeout, remaining_ms), agent, evidence)
                        print(f"{status.upper()} {name}: {detail}", flush=True)
                        counts[status] += 1
                        rows.append({"suite": suite, "name": name, "status": status, "detail": detail})
                    for key in tally:
                        tally[key] += counts[key]
                    print(f"RESULT suite={suite} pass={counts['pass']} fail={counts['fail']} skip={counts['skip']}", flush=True)
            finally:
                kill_process_tree(process)
    print(f"CLEANUP project and runtime roots removed={not work.exists()}", flush=True)
    (evidence / "summary.json").write_text(json.dumps({**tally, "scenes": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"E2E_SUMMARY pass={tally['pass']} fail={tally['fail']} skip={tally['skip']} evidence={evidence}", flush=True)
    return 1 if tally["fail"] else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) == 5 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit(main())
