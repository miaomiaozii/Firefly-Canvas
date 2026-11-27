"""Read-only cover selection and a derivative in the existing project preview cache."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from PIL import Image


COVER_WIDTH = 512
COVER_ERRORS = (OSError, ValueError, Image.DecompressionBombError)
_ASSET = re.compile(r'[A-Za-z0-9._-]{1,200}')


def cover_live_image_path(safe_path, directory, asset_id, items):
    if not isinstance(asset_id, str) or not _ASSET.fullmatch(asset_id):
        return None
    record = items.get(asset_id)
    if not isinstance(record, dict) or record.get('kind') != 'image':
        return None
    filename = record.get('filename')
    if (not isinstance(filename, str) or filename in {'', '.', '..'}
            or any(mark in filename for mark in (':', '/', '\\', '\0'))):
        return None
    path = safe_path(directory, f'media/{filename}')
    return path if path.is_file() else None


def _cover_order(value, missing):
    return value if type(value) is int and value >= 0 else missing


def first_live_project_image(canvas, history, media_path):
    nodes = {}
    for node in canvas.get('nodes') or ():
        if not isinstance(node, dict) or not isinstance(node.get('id'), str):
            continue
        nodes[node['id']] = {
            (item['asset_id'], str(item.get('output_id') or ''))
            for item in node.get('images') or ()
            if isinstance(item, dict) and isinstance(item.get('asset_id'), str)
        }
    if not nodes:
        return None
    rows = (row for row in history if isinstance(row, dict) and isinstance(row.get('result_node_id'), str)
            and row.get('type') in {'online-image', 'agent-image'})
    for row in sorted(rows, key=lambda row: _cover_order(row.get('created_at'),
                                  _cover_order(row.get('createdAt'), 0))):
        matching = nodes.get(row['result_node_id'])
        if not matching:
            continue
        outputs = (item for item in row.get('outputs') or () if isinstance(item, dict))
        for output in sorted(outputs, key=lambda item: _cover_order(item.get('output_order'), float('inf'))):
            asset = output.get('asset_id')
            if not isinstance(asset, str) or output.get('kind', 'image') != 'image':
                continue
            occurrence = str(output.get('output_id') or '')
            if ((asset, occurrence) in matching if occurrence else any(key == asset for key, _ in matching)):
                if source := media_path(asset):
                    return asset, source
    return None


def cached_project_cover(safe_path, source, asset_id):
    directory = source.parent.parent
    relative = f'previews/{asset_id}-{COVER_WIDTH}.webp'
    target = safe_path(directory, relative)
    if target.is_file():
        return target
    sizes = []
    for path in target.parent.glob(f'{asset_id}-*.webp'):
        width = path.stem.removeprefix(f'{asset_id}-')
        if width.isdecimal() and len(width) <= 4 and 64 <= int(width) <= 2048:
            sizes.append((abs(int(width) - COVER_WIDTH), path.name))
    for _, name in sorted(sizes):
        candidate = safe_path(directory, f'previews/{name}')
        if candidate.is_file():
            return candidate
    return None


def warm_project_cover(safe_path, source, asset_id):
    """Caller owns the project operation lock; never run a decoder under the library lock."""
    if cached := cached_project_cover(safe_path, source, asset_id):
        return cached
    target = safe_path(source.parent.parent, f'previews/{asset_id}-{COVER_WIDTH}.webp')
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        with Image.open(source) as image:
            image.thumbnail((COVER_WIDTH, COVER_WIDTH), Image.Resampling.LANCZOS)
            mode = 'RGBA' if 'transparency' in image.info or 'A' in image.getbands() else 'RGB'
            with image.convert(mode) as rgb:
                rgb.save(temporary, format='WEBP', quality=82, method=4)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
