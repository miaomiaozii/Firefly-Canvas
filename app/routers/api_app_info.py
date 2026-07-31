"""Router: /api/app-info domain (clean-room rewrite).

Self-contained: depends only on app.constants, no core import.
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter

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
def app_info():
    version = current_app_version()
    return {'name': '萤火画布', 'version': version, 'repo_url': FIREFLY_REPO_URL, 'repository': {'label': '萤火画布', 'url': FIREFLY_REPO_URL}}
