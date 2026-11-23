"""Public application metadata and backend source identity, without core imports."""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Response

from app.backend_version import backend_version_info
from app.constants import BASE_DIR, FIREFLY_REPO_URL

router = APIRouter()


def current_app_version():
    version_file = os.path.join(BASE_DIR, 'VERSION')
    try:
        if os.path.exists(version_file):
            with open(version_file, 'r', encoding='utf-8') as f:
                version = (f.read().strip().splitlines() or [''])[0].strip()
                if version:
                    return version
    except Exception:
        pass
    try:
        return time.strftime('%Y.%m.%d', time.localtime())
    except Exception:
        return ''


@router.get('/api/app-info')
def app_info(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    version = current_app_version()
    return {'name': '萤火画布', 'version': version, 'repo_url': FIREFLY_REPO_URL, 'repository': {'label': '萤火画布', 'url': FIREFLY_REPO_URL}, 'backend': backend_version_info()}
