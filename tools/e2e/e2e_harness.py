#!/usr/bin/env python3
"""#360 e2e 隔离夹具：套件自己建项目、播种、删干净，并给用户项目做指纹。

为什么存在（#360 根因）：`tools/e2e/scenario_*.sh` 与 `tools/e2e_qa.sh` 以前都取
`/api/healthz` 的 `open_project_ids[0]`，也就是**用户当前打开的真实项目**，然后在里面
删节点、复制、拖拽、框选——自动保存还会把中间态写进用户的 canvas.json。项目从哪来
这件事散在五个脚本里各抄一份，收成本夹具一处。

四个子命令（bash 侧只碰这四个）：

  create     建 `e2e-tabbit-<UTC>` 项目 + 播种节点/连线，stdout 最后一行 = 项目 id
  cleanup    trash + purge 删干净，残留则 exit 1 并打印原因
  fingerprint 扫描项目库所有 `*/canvas.json` 的 (mtime_ns, size) 写成 JSON
  verify     用 fingerprint 的快照复核：用户项目的 canvas.json 被动过就 exit 1

播种不是可选的装饰：不播种时画布是空的，`.image-node` 一个都没有，鼠标套件（m1–m6）
和删除/撤回套件（d1–d4）会整批空跑并「通过」。连线也一样——m6（拖拽后连线跟随）
没有连线就只能 skip。

指纹的口径：只认 `FIREFLY_PROJECTS_ROOT`（未设时用 `firefly_projects.default_projects_root()`，
即 `D:\\Firefly-Canvas-Projects`），只扫库根下一层项目目录，跳过 `.project-trash`
（回收区在 `<root>/.project-trash/<id>`）。判据用 mtime_ns + size，不用内容哈希——
票面验收写的是「canvas.json 修改时间不变」，而且这里要抓的是「有没有被写过」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BASE = "http://127.0.0.1:3000"
NAME_PREFIX = "e2e-tabbit-"
STATE_FILE = ROOT / ".pipeline" / "evidence" / "e2e-project-id"
TRASH_DIR_NAME = ".project-trash"


def _utf8_stdout() -> None:
    """Windows 默认 GBK 控制台，中文输出会炸（#292 同因）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _origin(base: str) -> str:
    return base.rstrip("/")


def _request(method: str, url: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    """同源守卫（app/core.py:3132 ensure_same_origin_request）要求 Origin 与 Host 一致。"""
    body = None
    headers = {"Accept": "application/json", "Origin": _origin(urlsplit_origin(url))}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"HTTP {exc.code} {method} {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"连不上 {method} {url}: {exc}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{method} {url} 返回不是 JSON: {raw[:200]}") from exc


def urlsplit_origin(url: str) -> str:
    scheme, _, rest = url.partition("://")
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}"


def projects_root() -> Path:
    override = os.environ.get("FIREFLY_PROJECTS_ROOT", "").strip()
    if override:
        return Path(override)
    from firefly_projects import default_projects_root

    return default_projects_root()


def seed_mutations(prefix: str, nodes: int, edge: bool) -> list[dict]:
    """播种节点（+ 可选首尾连线）。刻意不用 type=image：那是服务端生成态，
    普通 upsert 携 pendingTasks 会被拒（firefly_projects.py:3406）。"""
    mutations = []
    for index in range(1, max(0, nodes) + 1):
        mutations.append({
            "op": "upsert_node",
            "node": {
                "id": f"{prefix}-n{index}",
                "title": f"e2e 节点 {index}",
                "x": 120 + (index - 1) * 900,
                "y": 120,
                "w": 240,
                "h": 240,
            },
        })
    if edge and nodes >= 2:
        mutations.append({
            "op": "upsert_edge",
            "edge": {
                "id": f"{prefix}-e1",
                "from": f"{prefix}-n1",
                "to": f"{prefix}-n2",
                "kind": "flow",
            },
        })
    return mutations


def cmd_create(args: argparse.Namespace) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{NAME_PREFIX}{stamp}-{uuid.uuid4().hex[:6]}"
    created = _request("POST", f"{args.base}/api/project-library", {"name": name})
    project = created.get("project") or {}
    project_id = str(project.get("id") or "")
    if not project_id:
        raise SystemExit(f"建项目没返回 id：{json.dumps(created, ensure_ascii=False)[:300]}")
    mutations = seed_mutations("e2e", args.nodes, args.edge)
    if mutations:
        _request("PATCH", f"{args.base}/api/project-library/{project_id}/canvas",
                 {"mutations": mutations, "base_revision": 0, "client_id": "e2e-harness"})
    if args.state_file:
        Path(args.state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.state_file).write_text(project_id, encoding="utf-8")
    print(f"SEEDED project={project_id} name={name} nodes={args.nodes} edge={int(args.edge)}")
    print(project_id)
    return 0


def cmd_seed_json(args: argparse.Namespace) -> int:
    """把种子变更集以 JSON 输出——场景 preamble 在页内自愈时要用同一份种子，
    几何（坐标/尺寸）只能在 Python 里定义一份。"""
    print(json.dumps(seed_mutations("e2e", args.nodes, args.edge), ensure_ascii=False, separators=(",", ":")))
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    project_id = args.project_id.strip()
    if not project_id:
        print("CLEANUP residue  未给出 --project-id")
        return 1
    last_error = ""
    for method, url in (
        ("DELETE", f"{args.base}/api/project-library/{project_id}"),
        ("DELETE", f"{args.base}/api/project-library/trash/{project_id}"),
    ):
        try:
            _request(method, url)
        except SystemExit as exc:
            # 404 = 已不在该层（正常：没进回收区时 purge 会 404），其余留作证据
            last_error = str(exc.code) if hasattr(exc, "code") else str(exc)
            if "404" not in last_error:
                break
    live = _request("GET", f"{args.base}/api/project-library").get("projects") or []
    trashed = _request("GET", f"{args.base}/api/project-library/trash").get("projects") or []
    still_live = [p for p in live if str(p.get("id")) == project_id]
    still_trashed = [p for p in trashed if str(p.get("id")) == project_id]
    if still_live or still_trashed:
        where = "库内" if still_live else "回收区"
        print(f"CLEANUP residue {project_id} 仍在{where}（{last_error}）——请手工清理 "
              f"并检查 {projects_root()} 下是否有残留目录")
        return 1
    print(f"CLEANUP ok {project_id}")
    return 0


def scan_fingerprints(exclude: set[str]) -> dict[str, dict]:
    root = projects_root()
    if not root.is_dir():
        raise SystemExit(f"项目库不存在：{root}（设 FIREFLY_PROJECTS_ROOT 或先建库）")
    found: dict[str, dict] = {}
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        if directory.name in exclude:
            continue
        canvas = directory / "canvas.json"
        if not canvas.is_file():
            continue
        stat = canvas.stat()
        found[f"{directory.name}/canvas.json"] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    return {"root": str(root), "projects": found}


def cmd_fingerprint(args: argparse.Namespace) -> int:
    snapshot = scan_fingerprints(set(args.exclude or []))
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"FINGERPRINT {len(snapshot['projects'])} 个 canvas.json -> {args.out}（root={snapshot['root']}）")
    else:
        print(text)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = scan_fingerprints(set(args.exclude or []))
    changed, removed = [], []
    for key, value in (before.get("projects") or {}).items():
        current = after["projects"].get(key)
        if current is None:
            removed.append(key)
        elif current["mtime_ns"] != value["mtime_ns"] or current["size"] != value["size"]:
            changed.append(f"{key} mtime {value['mtime_ns']}->{current['mtime_ns']} "
                           f"size {value['size']}->{current['size']}")
    if not changed and not removed:
        print(f"UNTOUCHED {len(before.get('projects') or {})} 个用户项目的 canvas.json 未被写过")
        return 0
    print(f"TOUCHED {len(changed)} 个被改动、{len(removed)} 个消失（root={after['root']}）：")
    for line in changed:
        print(f"  改动 {line}")
    for key in removed:
        print(f"  消失 {key}")
    return 1


def main() -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="Firefly e2e 隔离夹具（#360）")
    # --base 挂在每个子命令上（parents），bash 侧统一写 `create --base URL`；
    # 挂顶层会被 argparse 要求写在子命令前面，调用顺序一错就报 unrecognized arguments。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", default=os.environ.get("FIREFLY_BASE", DEFAULT_BASE))
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", parents=[common], help="建测试项目并播种")
    create.add_argument("--nodes", type=int, default=3)
    create.add_argument("--no-edge", dest="edge", action="store_false", default=True)
    create.add_argument("--state-file", default=str(STATE_FILE))
    create.set_defaults(func=cmd_create)

    seed = sub.add_parser("seed-json", parents=[common], help="输出种子变更集 JSON（页内自愈用）")
    seed.add_argument("--nodes", type=int, default=3)
    seed.add_argument("--no-edge", dest="edge", action="store_false", default=True)
    seed.set_defaults(func=cmd_seed_json)


    cleanup = sub.add_parser("cleanup", parents=[common], help="trash + purge 删除测试项目")
    cleanup.add_argument("--project-id", required=True)
    cleanup.set_defaults(func=cmd_cleanup)

    fingerprint = sub.add_parser("fingerprint", parents=[common], help="扫描用户项目 canvas.json 指纹")
    fingerprint.add_argument("--out", default="")
    fingerprint.add_argument("--exclude", action="append", default=[])
    fingerprint.set_defaults(func=cmd_fingerprint)

    verify = sub.add_parser("verify", parents=[common], help="对照指纹快照，用户项目被动过则 exit 1")
    verify.add_argument("--before", required=True)
    verify.add_argument("--exclude", action="append", default=[])
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
