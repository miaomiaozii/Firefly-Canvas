"""Router: /api/models domain (clean-room rewrite).

Exposes the effective chat/image/video model lists. Model lists are derived
live from environment overrides (FIREFLY_* env or CHAT_MODELS/IMAGE_MODELS/
VIDEO_MODELS) plus the built-in defaults, so provider settings changes are
reflected on the next request without restart.
"""
from __future__ import annotations
import sys as _sys
import os as _os

_app_main = _sys.modules.get(_os.environ.get('FIREFLY_MAIN_MODULE', '')) or _sys.modules.get('main') or _sys.modules['__main__']

from fastapi import APIRouter

from app.core import CHAT_MODEL, IMAGE_MODEL, model_list

router = APIRouter()

# Built-in default video model catalogue (own data, kept stable across versions).
_DEFAULT_VIDEO_MODELS = [
    # —— Veo 系列 ——
    "veo2", "veo2-fast", "veo2-pro",
    "veo3", "veo3-fast", "veo3-pro",
    "veo3.1", "veo3.1-fast", "veo3.1-quality", "veo3.1-lite",
    # —— Sora ——
    "sora-2", "sora-2-pro",
    # —— 阿里 通义万相 ——
    "wan2.6-t2v", "wan2.6-i2v",
    "wan2.5-t2v-preview", "wan2.5-i2v-preview",
    "wan2.2-t2v-plus", "wan2.2-i2v-plus", "wan2.2-i2v-flash",
    # —— 火山 豆包 Seedance ——
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro-250528",
    "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-0-lite-i2v-250428",
]

_DEFAULT_CHAT_MODELS = ["gpt-4o-mini", "gemini-3.1-flash-image-preview-2k"]
_DEFAULT_IMAGE_MODELS = ["nano-banana-pro"]

@router.get('/api/models')
async def ai_models():
    return {
        'chat_models': model_list("CHAT_MODELS", CHAT_MODEL, _DEFAULT_CHAT_MODELS),
        'image_models': model_list("IMAGE_MODELS", IMAGE_MODEL, _DEFAULT_IMAGE_MODELS),
        'video_models': model_list("VIDEO_MODELS", "veo3-fast", _DEFAULT_VIDEO_MODELS),
    }
