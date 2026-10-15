"""Excel 导入结果收集 API：打标 + 批量导出（zip / 指定目录，issue #53/#58）。"""

import asyncio
import os
import re
import sys
import json
import zipfile
import io
from pathlib import Path
from urllib.parse import quote
from app.constants import NODE_TYPE_PROMPT
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

from app.core import ProjectNotFound, RevisionConflict

router = APIRouter()

# 输出媒体字段（结果节点 smart-image 的 images 列表项 asset_id，含图片/视频）；
# 兼容顶层输出字段作为兑底。
_OUTPUT_ASSET_KEYS = ("output_asset_id", "model_result_asset_id")
_UNSAFE_NAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


async def _open_project_canvas(project_id: str) -> Dict[str, Any]:
    """打开项目并返回 (canvas, base_revision)；项目不存在 → 404。"""
    try:
        opened = await asyncio.to_thread(_app_main.project_store_call, 'open_project', project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="项目不存在")
    canvas = opened['canvas']
    base_revision = int(canvas.get('revision') or 0)
    return canvas, base_revision


async def _apply_mutations_checked(project_id: str, mutations: List[Dict[str, Any]], base_revision: int) -> None:
    """应用 mutations；版本冲突 → 409。"""
    try:
        await asyncio.to_thread(
            _app_main.project_store_call,
            'apply_mutations',
            project_id,
            mutations,
            base_revision=base_revision
        )
    except RevisionConflict:
        raise HTTPException(status_code=409, detail="画布已被其他操作修改，请刷新后重试")

# 获取主模块引用
_app_main = sys.modules.get(os.environ.get('FIREFLY_MAIN_MODULE', '')) or sys.modules.get('main') or sys.modules['__main__']


class TagNodeRequest(BaseModel):
    """打标请求。"""
    project_id: str
    node_id: str
    curated: bool = True
    tags: List[str] = []


class CollectNodesRequest(BaseModel):
    """收集节点请求。"""
    project_id: str
    node_ids: List[str]
    include_unselected: bool = False


@router.post("/api/excel/tag-node")
async def tag_node(payload: TagNodeRequest) -> Dict[str, Any]:
    """给结果节点打标（curated + tags）。"""
    project_id = payload.project_id
    node_id = payload.node_id

    # 打开项目
    canvas, base_revision = await _open_project_canvas(project_id)

    # 找到节点
    node = None
    for n in canvas.get('nodes', []):
        if n.get('id') == node_id:
            node = n
            break

    if not node:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")

    # 更新节点
    node['curated'] = payload.curated
    node['tags'] = payload.tags
    node['updated_at'] = _app_main.now_ms()

    # 应用 mutation
    mutations = [
        {
            'op': 'upsert_node',
            'node': node
        }
    ]

    await _apply_mutations_checked(project_id, mutations, base_revision)
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)

    return {
        'success': True,
        'message': f'节点 {node_id} 已打标',
        'node_id': node_id,
        'curated': payload.curated,
        'tags': payload.tags
    }


@router.post("/api/excel/collect-nodes")
async def collect_nodes(payload: CollectNodesRequest) -> Response:
    """收集节点并导出 zip。"""
    project_id = payload.project_id
    node_ids = payload.node_ids

    # 打开项目
    canvas, base_revision = await _open_project_canvas(project_id)

    collected_nodes = []

    for node_id in node_ids:
        for node in canvas.get('nodes', []):
            if node.get('id') == node_id:
                # 只收集 curated 的节点（或 include_unselected 时收集所有）
                if payload.include_unselected or node.get('curated'):
                    collected_nodes.append(node)

    # 位置：现有内容下方（避免与已有节点重叠）
    max_y = 0.0
    for n in canvas.get('nodes', []):
        try:
            ny = float(n.get('y', 0) or 0) + float(n.get('h', n.get('height', 0)) or 0)
        except (TypeError, ValueError):
            continue
        if ny > max_y:
            max_y = ny

    # 创建收集节点（确定性 ID，重复收集时更新同一节点；
    # 用 smart-prompt 文本节点保证前端可渲染，画布节点模型用 w/h/title 键）
    collected_at = int(_app_main.now_ms())
    collect_node = {
        'id': 'excel_collect',
        'type': NODE_TYPE_PROMPT,
        'title': 'Excel 收集结果',
        'x': 80,
        'y': max_y + 80,
        'w': 300,
        'h': 200,
        'text': f"Excel 收集结果\n收集节点数：{len(collected_nodes)}\n时间戳：{collected_at}",
        'excel_collect_data': {
            'node_ids': node_ids,
            'collected_at': collected_at,
            'include_unselected': payload.include_unselected
        }
    }

    # 应用 mutation
    mutations = [
        {
            'op': 'upsert_node',
            'node': collect_node
        }
    ]

    await _apply_mutations_checked(project_id, mutations, base_revision)
    await asyncio.to_thread(_app_main.project_store_call, 'flush', project_id)

    # 创建 zip 文件
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # 添加 nodes.json
        zipf.writestr('nodes.json', json.dumps(collected_nodes, ensure_ascii=False, indent=2))

    # 返回 zip
    buffer.seek(0)
    return Response(
        content=buffer.read(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename=excel_import_results.zip'}
    )


# ─── #53/#58：导出选中节点输出媒体（zip / 指定目录）───────────────────────────


class ExportZipRequest(BaseModel):
    """导出请求。

    export_dir（issue #58）：可选。为空 → 返回 zip 流给浏览器（默认，向后兼容）；
    非空 → 写入服务器指定目录（必须位于项目库根之下，拒绝路径穿越）。
    """
    project_id: str
    node_ids: List[str]
    include_unselected: bool = False
    export_dir: Optional[str] = None
    filename: Optional[str] = None


def _content_disposition(filename: str) -> str:
    """构建 Content-Disposition；非 latin-1 文件名走 RFC 5987（filename*=UTF-8''）。

    HTTP 头必须是 latin-1，CJK 文件名需 percent-encode，同时提供 ASCII 兑底名。
    """
    try:
        filename.encode("latin-1")
        return f"attachment; filename={filename}"
    except UnicodeEncodeError:
        quoted = quote(filename, safe="")
        ascii_name = "excel_export.zip"
        return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'' + quoted


def _node_output_assets(node: Dict[str, Any]) -> List[Dict[str, str]]:
    """收集节点输出媒体（asset_id + kind）。

    结果节点（smart-image）的生成结果统一写入 images 列表（complete_media_generation，
    图片与视频皆然，每项含 asset_id/kind）；顶层输出字段作为兑底。
    """
    out: List[Dict[str, str]] = []
    seen = set()

    def _add(asset_id: Any, kind: str) -> None:
        aid = str(asset_id or "").strip()
        if aid and aid not in seen:
            seen.add(aid)
            out.append({"asset_id": aid, "kind": kind or "image"})

    for item in node.get("images") or []:
        if isinstance(item, dict):
            _add(item.get("asset_id"), str(item.get("kind") or ""))
    for key in _OUTPUT_ASSET_KEYS:
        if isinstance(node.get(key), str):
            _add(node.get(key), "image")
    return out


def _export_base_name(node: Dict[str, Any], ordinal: int) -> str:
    """导出文件基名：{task_id}_{section}_{no}，清洗非法字符。

    任务标识取自展开计划时挂到节点的 excel_task 元数据（layout_engine：
    excel_task_id/excel_section/excel_task_data.display_no），缺失时兑底 node id / 序号。
    """
    task_data = node.get("excel_task_data")
    if not isinstance(task_data, dict):
        task_data = {}
    task_id = (
        str(node.get("excel_task_id") or "").strip()
        or str(task_data.get("task_id") or "").strip()
        or str(node.get("id") or "").strip()
        or "node"
    )
    section = (
        str(node.get("excel_section") or "").strip()
        or str(task_data.get("section") or "").strip()
    )
    no = task_data.get("display_no") or task_data.get("section_index") or ordinal
    base = "_".join(p for p in (task_id, section, str(no)) if p)
    base = _UNSAFE_NAME_CHARS.sub("_", base).strip("_")
    return base or f"node_{ordinal}"


def _default_projects_root() -> Path:
    from firefly_projects import default_projects_root
    return default_projects_root()


def _resolve_export_dir(raw: str) -> Path:
    """#58：校验导出目录（白名单 = 项目库根之下；拒绝穿越/绝对路径逃逸/空值）。"""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise HTTPException(status_code=400, detail="导出目录不能为空")
    store = getattr(_app_main, "FIREFLY_PROJECT_STORE", None)
    root = Path(getattr(store, "root", None) or _default_projects_root()).resolve()
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"无效的导出目录: {exc}") from exc
    if os.path.commonpath([str(root), str(candidate)]) != str(root):
        raise HTTPException(
            status_code=400,
            detail=f"导出目录必须位于项目库根之下: {root}",
        )
    return candidate


@router.post("/api/excel/export-zip")
async def export_zip(payload: ExportZipRequest) -> Any:
    """导出选中节点的全部输出媒体（图片/视频）。

    - 文件以任务 ID 命名：{task_id}_{section}_{no}.{ext}（issue #53）
    - export_dir 为空：返回 zip 流给浏览器（默认，向后兼容）
    - export_dir 非空：写入服务器指定目录并返回 JSON（issue #58）
    """
    project_id = payload.project_id

    canvas, _ = await _open_project_canvas(project_id)

    selected = []
    for node_id in payload.node_ids:
        node = next((n for n in canvas.get("nodes", []) if n.get("id") == node_id), None)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")
        selected.append(node)

    # 收集 (node, asset_id, kind) 并解析媒体文件；不存在的媒体（已删除等）跳过
    entries = []  # [node, asset_id, kind, Path]
    for ordinal, node in enumerate(selected, start=1):
        for item in _node_output_assets(node):
            try:
                record = await asyncio.to_thread(
                    _app_main.project_store_call, "media_record", project_id, item["asset_id"]
                )
            except Exception:
                continue
            path = Path(str(record.get("path") or ""))
            if path.is_file():
                entries.append([node, item["asset_id"], item.get("kind", "image"), path])

    if not entries:
        raise HTTPException(status_code=404, detail="选中节点没有可导出的输出媒体")

    # 命名 + 去重
    used_names = set()
    manifest = []
    for ordinal, (node, asset_id, kind, _path) in enumerate(entries, start=1):
        ext = _path.suffix or (".mp4" if kind == "video" else ".png")
        base = _export_base_name(node, ordinal)
        name = f"{base}{ext}"
        n = 2
        while name in used_names:
            name = f"{base}_{n}{ext}"
            n += 1
        used_names.add(name)
        manifest.append({
            "file": name,
            "asset_id": asset_id,
            "kind": kind,
            "node_id": str(node.get("id") or ""),
        })

    if payload.export_dir:
        # #58：写入服务器目录
        out_dir = _resolve_export_dir(payload.export_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            written = []
            for (node, asset_id, kind, path), entry in zip(entries, manifest):
                target = out_dir / entry["file"]
                target.write_bytes(path.read_bytes())
                written.append(str(target))
        except HTTPException:
            raise
        except (OSError, PermissionError) as exc:
            raise HTTPException(status_code=500, detail=f"导出目录写入失败: {exc}") from exc
        return {
            "success": True,
            "mode": "dir",
            "export_dir": str(out_dir),
            "count": len(written),
            "files": written,
            "manifest": manifest,
        }

    # 默认：zip 流
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for (node, asset_id, kind, path), entry in zip(entries, manifest):
            zipf.write(path, arcname=entry["file"])
        zipf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zipf.writestr("nodes.json", json.dumps(selected, ensure_ascii=False, indent=2))

    buffer.seek(0)
    stamp = int(_app_main.now_ms())
    raw_name = str(payload.filename or f"excel_export_{stamp}").strip()
    zip_filename = re.sub(r"\.{2,}", ".", _UNSAFE_NAME_CHARS.sub("_", raw_name)).strip("._") or "excel_export"
    if not zip_filename.lower().endswith(".zip"):
        zip_filename += ".zip"
    disposition = _content_disposition(zip_filename)
    return Response(
        content=buffer.read(),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )
