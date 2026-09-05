"""Service: Load and access model specifications from model_specs.json.

This module provides a single source of truth for model parameters, API types,
and capabilities. The JSON file is the authoritative reference for:
- Which API endpoint to use for each model
- What parameters each model supports
- Whether a model supports vision (image input)
- Aspect ratio / size options for image models

Usage:
    from app.services.model_specs import get_model_spec, is_vision_model, get_aspect_ratios

    spec = get_model_spec("nano-banana-2")
    if is_vision_model("gpt-4o"):
        # Can send images
        pass
    ratios = get_aspect_ratios("nano-banana-2")
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

# Path to model_specs.json
_SPECS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                          'app', 'data', 'model_specs.json')

# Cache loaded specs
_specs_cache: Optional[Dict[str, Any]] = None

# Fallback vision detection keywords (used when model not in specs)
_LEGACY_VISION_KEYS = [
    'vision', 'vl-', '-vl-', 'internvl', 'qvq', 'qwen-vl', 'doubao-vision', 
    'glm-4v', 'minicpm-v', 'gpt-4o', 'gpt-4.1', 'gpt-5', 'claude', 'gemini'
]


def load_specs(force_reload: bool = False) -> Dict[str, Any]:
    """Load model specs from JSON file.
    
    Args:
        force_reload: If True, reload from disk even if cached.
    
    Returns:
        Dictionary with chat_models, image_models, video_models keys.
    """
    global _specs_cache
    
    if _specs_cache is not None and not force_reload:
        return _specs_cache
    
    try:
        with open(_SPECS_PATH, 'r', encoding='utf-8') as f:
            _specs_cache = json.load(f)
        return _specs_cache
    except FileNotFoundError:
        # Return empty structure if file doesn't exist
        _specs_cache = {"chat_models": {}, "image_models": {}, "video_models": {}}
        return _specs_cache
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid model_specs.json: {e}")


def get_model_spec(model_id: str) -> Optional[Dict[str, Any]]:
    """Get specification for a specific model.
    
    Args:
        model_id: The model ID (e.g., "nano-banana-2")
    
    Returns:
        Model spec dictionary or None if not found.
    """
    specs = load_specs()
    model_id_lower = str(model_id or '').strip().lower()
    if not model_id_lower:
        return None

    # Search in all model categories (exact match first)
    for category in ["chat_models", "image_models", "video_models"]:
        for mid, spec in specs.get(category, {}).items():
            if mid.lower() == model_id_lower:
                return spec
    
    # Try partial match (e.g., "gpt-5" might match "gpt-5.4")
    for category in ["chat_models", "image_models", "video_models"]:
        for mid, spec in specs.get(category, {}).items():
            if mid.lower().startswith(model_id_lower):
                return spec
    
    return None


def is_vision_model(model_id: str) -> bool:
    """Check if a model supports vision (image input).
    
    Checks model_specs.json first, falls back to name-based detection.
    
    Args:
        model_id: The model ID
    
    Returns:
        True if the model supports vision input, False otherwise.
    """
    spec = get_model_spec(model_id)
    if spec:
        return bool(spec.get("supports_vision", False))
    
    # Fallback to legacy name-based detection
    lc = str(model_id or '').strip().lower()
    if not lc:
        return False
    return any(key in lc for key in _LEGACY_VISION_KEYS)


def get_api_type(model_id: str) -> Optional[str]:
    """Get the API type for a model.
    
    Args:
        model_id: The model ID
    
    Returns:
        API type string (e.g., "responses", "images_generations") or None.
    """
    spec = get_model_spec(model_id)
    return spec.get("api_type") if spec else None


def get_aspect_ratios(model_id: str) -> List[str]:
    """Get supported aspect ratios for an image model.
    
    Args:
        model_id: The model ID
    
    Returns:
        List of supported aspect ratios.
    """
    spec = get_model_spec(model_id)
    if not spec:
        return []
    
    params = spec.get("parameters", {})
    aspect_ratio = params.get("aspect_ratio", {})
    return aspect_ratio.get("options", [])


def get_image_sizes(model_id: str) -> List[str]:
    """Get supported image sizes for an image model.
    
    Args:
        model_id: The model ID
    
    Returns:
        List of supported image sizes (e.g., ["1K", "2K", "4K"]).
    """
    spec = get_model_spec(model_id)
    if not spec:
        return []
    
    params = spec.get("parameters", {})
    image_size = params.get("image_size", {})
    return image_size.get("options", [])


def get_model_category(model_id: str) -> Optional[str]:
    """Return which category a model belongs to: 'chat', 'image', or 'video'.

    Uses exact match only (no prefix), so it is safe to rely on for classification.
    Returns None if the model is not in the spec.
    """
    specs = load_specs()
    mid_lower = str(model_id or '').strip().lower()
    if not mid_lower:
        return None
    for cat, kind in (('video_models', 'video'), ('image_models', 'image'), ('chat_models', 'chat')):
        for m in specs.get(cat, {}):
            if str(m).lower() == mid_lower:
                return kind
    return None


def get_model_list_by_category(category: str) -> List[str]:
    """Get all model IDs in a category.
    
    Args:
        category: "chat_models", "image_models", or "video_models"
    
    Returns:
        List of model IDs.
    """
    specs = load_specs()
    return list(specs.get(category, {}).keys())


def reload_specs():
    """Force reload specs from disk.
    
    Call this after modifying model_specs.json if you need changes to take
    effect without restarting the application.
    """
    load_specs(force_reload=True)
