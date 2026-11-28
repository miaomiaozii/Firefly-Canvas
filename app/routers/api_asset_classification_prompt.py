"""Router: /api/asset-classification-prompt domain (auto-split from main.py)."""
from __future__ import annotations
from fastapi import APIRouter

from app.core import ASSET_CLASSIFICATION_PROMPT, Dict
from app.services.assets import load_asset_classification_prompt, save_asset_classification_prompt

router = APIRouter()

@router.get('/api/asset-classification-prompt')
async def get_asset_classification_prompt():
    current = load_asset_classification_prompt()
    return {'prompt': current, 'default_prompt': ASSET_CLASSIFICATION_PROMPT, 'custom': current.strip() != ASSET_CLASSIFICATION_PROMPT.strip()}

@router.patch('/api/asset-classification-prompt')
async def update_asset_classification_prompt(payload: Dict[str, str]):
    prompt = save_asset_classification_prompt((payload or {}).get('prompt') or '')
    return {'prompt': prompt, 'custom': True}
