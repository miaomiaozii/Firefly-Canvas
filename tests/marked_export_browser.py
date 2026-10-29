# -*- coding: utf-8 -*-
"""Issue #206：「导出打标」按图片粒度——真实 headless Chrome 端到端验收。

场景：一个节点里 4 张图，UI 里只给其中 1 张打星标，点「已标记」面板导出：
- 路径 1（浏览器 zip 下载）：目录留空 → 真实浏览器完成点击导出，验证前端请求
  按图片粒度携带 asset_ids（恰为标星的那 1 张，不带 node_ids）；zip 字节若被
  浏览器成功下载则做字节级验证，否则降级为对同一运行实例的服务端校验。
- 路径 2（写服务器目录，#58）：填导出目录 → 真实浏览器全链路：请求 + API files
  列表 + 磁盘落盘，都只有那 1 张。

注意：本机装有 IDM（Internet Download Manager），其「Advanced Integration」在系统层
劫持带 Content-Disposition: attachment 的响应（合成 204 "Intercepted by the IDM
Advanced Integration"，代理/扩展开关均无法绕过），因此 zip 路径的字节级验证在本机
为「服务端（同一运行实例）」层级；浏览器层覆盖到：星标点击 → 面板 → 导出请求构造
→ 响应状态。在无 IDM 的机器上，同一脚本自动升级为浏览器层字节验证。

运行：python tests\\marked_export_browser.py [--browser <路径>] [--artifacts-dir <目录>]
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conversation_browser as harness  # noqa: E402

CLIENT_SCRIPT = Path(__file__).resolve().parent / "marked_export_browser_client.js"

NODE_ID = "gen-4"
EXPORT_DIR_NAME = "exported-marked"


def seed_store(store: ProjectStore) -> dict:
    """1 个节点 4 张图（4 个不同媒体资产）；星标由浏览器 UI 现场点击产生。"""
    project = store.create_project("marked-export-e2e")
    project_id = project["id"] if isinstance(project, dict) else project.id
    asset_ids = []
    for index in range(1, 5):
        asset = store.import_media(
            project_id,
            f"fake-marked-image-bytes-{index}".encode("ascii"),
            f"gen_{index}.png",
            "image/png",
        )
        asset_ids.append(asset["asset_id"])
    store.apply_mutations(
        project_id,
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": NODE_ID,
                    "type": "smart-image",
                    "x": 80,
                    "y": 120,
                    "w": 640,
                    "h": 480,
                    "scale": 1,
                    "images": [
                        {"asset_id": aid, "kind": "image", "name": f"gen_{i}.png"}
                        for i, aid in enumerate(asset_ids, start=1)
                    ],
                },
            }
        ],
        base_revision=0,
    )
    store.flush(project_id)
    marked_index = 1  # 4 张里的第 2 张
    return {
        "project_id": project_id,
        "selected_text": "",
        "asset_ids": asset_ids,
        "marked_asset": asset_ids[marked_index],
        "marked_bytes": f"fake-marked-image-bytes-{marked_index + 1}".encode("ascii"),
    }


def dir_evidence(projects_root: Path) -> dict:
    """服务器目录路径的磁盘落盘证据。"""
    out_dir = projects_root / EXPORT_DIR_NAME
    if not out_dir.is_dir():
        return {"exists": False, "files": [], "bytes": {}}
    files = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    return {
        "exists": True,
        "files": files,
        "bytes": {name: out_dir.joinpath(name).read_bytes() for name in files},
    }


def server_zip_check(base_url: str, project_id: str, marked_asset: str, marked_bytes: bytes) -> dict:
    """服务端校验（同一运行实例）：POST 导出 API（asset_ids 图片粒度），解 zip 验证字节。"""
    req = urllib.request.Request(
        f"{base_url}/api/excel/export-zip",
        data=json.dumps({"project_id": project_id, "asset_ids": [marked_asset]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "origin": base_url},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        status = res.status
        content = res.read()
    entries = []
    manifest = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        media = [name for name in names if name not in ("manifest.json", "nodes.json")]
        entries = [
            {"name": name, "bytes_ok": zf.read(name) == marked_bytes} for name in media
        ]
    except Exception as error:  # noqa: BLE001 - 证据型报错
        return {
            "ok": False,
            "status": status,
            "detail": f"解 zip 失败：{error}",
            "media": [],
            "manifest": manifest,
        }
    ok = (
        status == 200
        and len(entries) == 1
        and entries[0]["bytes_ok"]
        and len(manifest) == 1
        and manifest[0].get("asset_id") == marked_asset
    )
    return {
        "ok": ok,
        "status": status,
        "detail": json.dumps(entries, ensure_ascii=False),
        "media": [e["name"] for e in entries],
        "manifest": manifest,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Issue #206 导出打标真实浏览器验收")
    parser.add_argument("--browser", default="", help="浏览器可执行文件路径（默认自动探测 Chrome/Edge）")
    parser.add_argument("--artifacts-dir", default="", help="证据目录（可选）")
    args = parser.parse_args()
    browser = Path(args.browser) if args.browser else harness.find_browser()
    print(f"marked export e2e browser={browser}", flush=True)

    with tempfile.TemporaryDirectory(prefix="firefly-marked-export-e2e-") as root:
        root_path = Path(root)
        store = ProjectStore(root_path / "projects", snapshot_delay=0)
        fixture = seed_store(store)
        print(
            f"marked export e2e project={fixture['project_id']} "
            f"marked_asset={fixture['marked_asset']} of {len(fixture['asset_ids'])} images",
            flush=True,
        )
        app = harness.load_production_app(root_path / "runtime", store)
        server = harness.ConversationBrowserServer(store, app, client_script=CLIENT_SCRIPT)
        server.start()
        print(f"marked export e2e server listening on {server.base_url}", flush=True)
        launch_error = None
        result = None
        server_zip = None
        try:
            result = harness.launch(
                browser,
                server,
                (1280, 800),
                fixture,
                result_timeout=180,
                extra_query={"markedAsset": fixture["marked_asset"]},
                # 测试浏览器不走用户系统代理（本机 IDM 代理 10808）；注：IDM 的
                # 「Advanced Integration」在代理层之下劫持附件下载，该开关不能绕开，
                # zip 字节验证由下面的同实例服务端校验兑底。
                extra_args=["--no-proxy-server"],
            )
            # 服务端校验（同一运行实例、真实 ASGI 栈）：zip 路径字节级证据。
            server_zip = server_zip_check(
                server.base_url, fixture["project_id"], fixture["marked_asset"], fixture["marked_bytes"]
            )
        except RuntimeError as error:
            launch_error = str(error)
        finally:
            server.close()
            store.close()
        evidence = dir_evidence(root_path / "projects")

    if launch_error is not None:
        print("导出打标浏览器验收未通过（客户端断言失败，原文如下）：")
        print(launch_error)
        print(f"服务器目录磁盘证据：{json.dumps(evidence['files'], ensure_ascii=False)}")
        return 1

    assertions = result.get("assertions") or []
    extras = result.get("extras") or {}
    failures = []
    zip_layer = None

    # ── Python 侧终验 1：zip 路径字节级验证（浏览器字节优先，否则同实例服务端）──
    zip_b64 = extras.get("zipB64") or ""
    if zip_b64:
        try:
            zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(zip_b64)))
            names = zf.namelist()
            media = [name for name in names if name not in ("manifest.json", "nodes.json")]
            if len(media) != 1:
                failures.append(f"zip 路径：媒体文件应为 1 个，实际 {len(media)} 个：{names}")
            elif zf.read(media[0]) != fixture["marked_bytes"]:
                failures.append(f"zip 路径：{media[0]} 字节与被标星标图不符")
            manifest = json.loads(zf.read("manifest.json"))
            if len(manifest) != 1 or manifest[0].get("asset_id") != fixture["marked_asset"]:
                failures.append(f"zip 路径：manifest 异常：{json.dumps(manifest, ensure_ascii=False)}")
            zip_layer = f"浏览器层字节（headless 浏览器实际下载）media={media}"
            print(f"zip 路径（{zip_layer}）manifest_asset={manifest[0]['asset_id']}")
        except Exception as error:  # noqa: BLE001 - 证据型报错
            failures.append(f"zip 路径：解浏览器捕获的 zip 失败：{error}")
    else:
        status_text = extras.get("zipStatusText") or ""
        if server_zip is None:
            failures.append("zip 路径：浏览器层未捕获到字节，且缺少同实例服务端校验结果")
        elif not server_zip["ok"]:
            failures.append(
                "zip 路径：同实例服务端校验失败："
                f"{json.dumps(server_zip, ensure_ascii=False)}"
            )
        else:
            zip_layer = "同运行实例服务端（应用/ASGI 层）"
            print(
                f"zip 路径（{zip_layer}）：media={server_zip['media']} status={server_zip['status']} "
                f"manifest_asset={server_zip['manifest'][0]['asset_id'] if server_zip['manifest'] else None}"
            )
            print(
                f"zip 路径浏览器层状态：status={extras.get('zipStatus')} statusText={status_text!r}"
                + (" ← 被本机 IDM Advanced Integration 拦截，浏览器层字节不可得" if "IDM" in status_text else "")
            )

    # ── Python 侧终验 2：服务器目录路径磁盘落盘 ──
    files = evidence["files"]
    if len(files) != 1:
        failures.append(f"目录路径：磁盘文件应为 1 个，实际 {len(files)} 个：{files}")
    else:
        content = evidence["bytes"][files[0]]
        if content != fixture["marked_bytes"]:
            failures.append(f"目录路径：{files[0]} 字节与被标星标图不符")
        print(f"目录路径磁盘验证：files={files} bytes_ok={content == fixture['marked_bytes']}")

    if failures:
        print("导出打标浏览器验收未通过（Python 侧终验失败）：")
        for item in failures:
            print(f"- {item}")
        return 1

    print("PASS: 导出打标浏览器验收通过（1 节点 4 图、只标 1 张 → 导出只含那 1 张）")
    print(f"  - zip 路径：真实浏览器点击导出 + 请求按图片粒度携带 asset_ids；字节级验证层级：{zip_layer}")
    print(f"  - 目录路径：真实浏览器全链路（请求 + API files + 磁盘落盘 {len(evidence['files'])} 个文件）")
    print(f"  - 客户端断言 {len(assertions)} 项全部通过：")
    for item in assertions:
        detail = f"（{item['detail']}）" if item.get("detail") else ""
        text = f"- {item['name']}{detail}"
        print(text.encode("gbk", errors="replace").decode("gbk"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
