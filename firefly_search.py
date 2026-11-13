"""跨项目搜索（fullnode 票 F / #324，ADR-0026）。

直扫各项目 canvas.json 白名单字段 + 项目名；内存 LRU 缓存按
(canvas.json 路径, mtime_ns, size) 惰性失效。不读 agent-events/sessions，
不建常驻索引（升级线见 ADR：项目数>500 或 P95>2s 须先立索引 ADR）。
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List

# 节点可搜字段白名单（canvas 字段，不含媒体 URL/HTML 富文本）
SEARCHABLE_NODE_FIELDS = ("title", "promptDraftText", "runPrompt", "runModelPrompt", "text")

MAX_CANVAS_BYTES = 8 * 1024 * 1024
MAX_RESULTS = 200
MAX_RESULTS_PER_PROJECT = 20
SNIPPET_RADIUS = 60
CACHE_CAPACITY = 64


def _snippet(text: str, needle_lower: str) -> str:
    pos = text.lower().find(needle_lower)
    if pos < 0:
        return text[: SNIPPET_RADIUS * 2]
    start = max(0, pos - SNIPPET_RADIUS)
    end = min(len(text), pos + len(needle_lower) + SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


class CrossProjectSearch:
    def __init__(self, projects_root: Path, *, max_canvas_bytes: int = MAX_CANVAS_BYTES) -> None:
        self.root = Path(projects_root)
        self.max_canvas_bytes = max_canvas_bytes
        self._digest_cache: "OrderedDict[str, tuple]" = OrderedDict()

    def _digest_for(self, canvas_path: Path) -> List[Dict[str, Any]]:
        try:
            stat = canvas_path.stat()
        except OSError:
            return []
        key = str(canvas_path)
        cached = self._digest_cache.get(key)
        if cached is not None and cached[0] == (stat.st_mtime_ns, stat.st_size):
            self._digest_cache.move_to_end(key)
            return cached[1]
        digest: List[Dict[str, Any]] = []
        if stat.st_size <= self.max_canvas_bytes:
            try:
                canvas = json.loads(canvas_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                canvas = None
            if isinstance(canvas, dict):
                for node in canvas.get("nodes") or []:
                    if not isinstance(node, dict):
                        continue
                    fields: Dict[str, str] = {}
                    for field in SEARCHABLE_NODE_FIELDS:
                        value = node.get(field)
                        if isinstance(value, str) and value.strip():
                            fields[field] = value
                    digest.append(
                        {
                            "id": str(node.get("id") or ""),
                            "kind": str(node.get("kind") or node.get("type") or ""),
                            "fields": fields,
                        }
                    )
        self._digest_cache[key] = ((stat.st_mtime_ns, stat.st_size), digest)
        self._digest_cache.move_to_end(key)
        while len(self._digest_cache) > CACHE_CAPACITY:
            self._digest_cache.popitem(last=False)
        return digest

    def search(self, query: str, *, limit: int = MAX_RESULTS) -> Dict[str, Any]:
        needle = (query or "").strip().lower()
        response: Dict[str, Any] = {"query": query, "projects": [], "total": 0, "truncated": False, "skipped_large": []}
        if not needle or not self.root.exists():
            return response
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir():
                continue
            try:
                metadata = json.loads((directory / "project.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if metadata.get("id") != directory.name:
                continue
            project_name = str(metadata.get("name") or "")
            hits: List[Dict[str, Any]] = []
            if needle in project_name.lower():
                hits.append({"node_id": "", "kind": "project", "field": "name", "snippet": project_name})
            canvas_path = directory / "canvas.json"
            try:
                if canvas_path.stat().st_size > self.max_canvas_bytes:
                    response["skipped_large"].append(directory.name)
                    nodes_digest: List[Dict[str, Any]] = []
                else:
                    nodes_digest = self._digest_for(canvas_path)
            except OSError:
                nodes_digest = []
            for node in nodes_digest:
                for field, value in node["fields"].items():
                    if needle in value.lower():
                        hits.append(
                            {
                                "node_id": node["id"],
                                "kind": node["kind"],
                                "field": field,
                                "snippet": _snippet(value, needle),
                            }
                        )
                        break
                if len(hits) >= MAX_RESULTS_PER_PROJECT:
                    break
            if hits:
                response["projects"].append(
                    {
                        "project_id": directory.name,
                        "name": project_name,
                        "updated_at": metadata.get("updated_at") or 0,
                        "hits": hits[:MAX_RESULTS_PER_PROJECT],
                    }
                )
                response["total"] += len(hits)
            if response["total"] >= min(limit, MAX_RESULTS):
                response["truncated"] = True
                break
        response["projects"].sort(key=lambda item: int(item["updated_at"]) * -1)
        return response
