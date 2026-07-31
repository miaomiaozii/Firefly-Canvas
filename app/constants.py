"""Firefly Canvas: static configuration constants.

Pure path/limit constants that never change at runtime. Extracted from
app/core.py so routers and services can depend on the small constant surface
instead of importing the whole core module.
"""
from __future__ import annotations

import os
import uuid

FIREFLY_REPO_URL = 'https://github.com/miaomiaozii/Firefly-Canvas'
CLIENT_ID = str(uuid.uuid4())
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_ROOT = os.path.abspath(os.getenv('FIREFLY_RUNTIME_ROOT', BASE_DIR))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
STATIC_RUNNINGHUB_DIR = os.path.join(STATIC_DIR, 'runninghub')
STATIC_RUNNINGHUB_THUMBNAIL_DIR = os.path.join(STATIC_RUNNINGHUB_DIR, 'thumbnails')
STATIC_RUNNINGHUB_API_PROVIDERS_FILE = os.path.join(STATIC_RUNNINGHUB_DIR, 'api_providers.json')
STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE = os.path.join(STATIC_RUNNINGHUB_DIR, 'models_registry.json')
CANVAS_TRASH_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
LOCAL_IMAGE_IMPORT_MAX_BYTES = int(os.getenv('LOCAL_IMAGE_IMPORT_MAX_BYTES', str(50 * 1024 * 1024)))
LOCAL_IMAGE_IMPORT_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
RUNNINGHUB_THUMBNAIL_EXTS = ('.jpg',)
