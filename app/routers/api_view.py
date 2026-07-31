"""Router: /api/view domain (clean-room rewrite).

Depends only on the exact symbols it uses: output_path_for (services) and
content_type_for_path (core). No core star-import.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core import content_type_for_path
from app.services import output_path_for

router = APIRouter()


@router.get('/api/view')
def view_image(filename: str, type: str = 'input', subfolder: str = ''):
    if not subfolder and type in ('input', 'output'):
        safe_name = os.path.basename(filename or '')
        if safe_name:
            local_path = output_path_for(safe_name, 'input' if type == 'input' else 'output')
            if os.path.isfile(local_path):
                return FileResponse(local_path, media_type=content_type_for_path(local_path))
    raise HTTPException(status_code=404, detail='Image not found on any available backend')
