"""Translate saved canvas settings to the project image task wire format (#396)."""
import math
import re

from app.services.agent_errors import agent_error
from app.services.agent_node_state import _RATIO_PRESETS, _saved_ratio
from firefly_generation import COMMON_IMAGE_SIZES, GEMINI_FLASH_IMAGE_ASPECT_RATIOS, checked_image_model

RUN_SETTINGS_PROPERTIES = {
    "model": {"type": "string"},
    "provider_id": {"type": "string"},
    "aspect_ratio": {"type": "string", "description": "宽高比，例如 16:9"},
    "count": {"type": "integer", "minimum": 1, "maximum": 8},
    "routingMode": {"type": "string", "enum": ["auto", "manual"]},
}
# Tie order follows IMAGE_RATIO_PRESETS, not COMMON_IMAGE_SIZES pixel order.
_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "9:21", "1:4", "4:1", "1:8", "8:1")
_RATIO_KEY = {value: key for key, value in _RATIO_PRESETS.items()}
_SIZE_RATIOS = [*GEMINI_FLASH_IMAGE_ASPECT_RATIOS[:5], "9:16", "16:9", "21:9", "9:21", "4:5", "5:4", "1:4", "4:1", "1:8", "8:1"]


def invalid(message):
    return agent_error(400, "INVALID_ARGUMENT", message, "请检查节点的生图参数后重试。")


def validate_patch(value):
    if not isinstance(value, dict) or value.keys() - RUN_SETTINGS_PROPERTIES.keys():
        raise invalid("runSettings must be an object containing only model, provider_id, aspect_ratio, count or routingMode")
    for key, item in value.items():
        if key == "count":
            if type(item) is not int or not 1 <= item <= 8:
                raise invalid("runSettings.count must be an integer from 1 to 8")
        elif not isinstance(item, str) or len(item) > 200:
            raise invalid(f"runSettings.{key} must be a string of at most 200 characters")
        elif key == "routingMode" and item not in ("auto", "manual"):
            raise invalid("runSettings.routingMode must be auto or manual")
        elif key == "aspect_ratio" and not _saved_ratio(item):
            raise invalid("runSettings.aspect_ratio must be a positive width:height ratio")
    return value


def saved_ratio(settings):
    key = settings.get("ratio")
    if key in ("custom", "source"):
        return _saved_ratio(settings.get("customRatio"))
    return (_RATIO_PRESETS.get(key) if isinstance(key, str) else None) or _saved_ratio(settings.get("aspect_ratio"))


def merge_patch(settings, patch):
    prev = {key: settings.get(key) if key != "aspect_ratio" else saved_ratio(settings) for key in patch}
    merged = {**settings, **{key: value for key, value in patch.items() if key != "aspect_ratio"}}
    if "aspect_ratio" in patch and prev["aspect_ratio"] != patch["aspect_ratio"]:
        ratio = patch["aspect_ratio"].strip()
        # The composer writes `ratio`, so don't leave a stale alias that could win on a later browser edit.
        merged.pop("aspect_ratio", None)
        merged["ratio"] = _RATIO_KEY.get(ratio, "custom")
        if merged["ratio"] == "custom":
            merged["customRatio"] = ratio
    return merged, prev


def _count(value):
    if value is None or value is False or value == "":
        return 1
    try:
        number = float(value)
        if not math.isfinite(number) or not float(max(1, min(8, number))).is_integer():
            raise ValueError
        return max(1, min(8, int(number)))
    except (ValueError, TypeError, OverflowError) as exc:
        raise invalid("Saved runSettings.count does not resolve to an image count") from exc


def _ratio_log(ratio):
    a, b = map(float, ratio.split(":"))
    return math.log(a) - math.log(b)


def nearest_aspect_ratio(ratio, allowed):
    if not allowed or ratio in allowed:
        return ratio
    candidates = [item for item in _RATIOS if item in allowed]
    current = _ratio_log(ratio)
    def score(item):
        target = _ratio_log(item)
        return abs(target - current) + (math.log(2) if target and target * current < 0 else 0)
    return min(candidates, key=score) if candidates else ratio


def size_for_ratio(ratio, key, resolution, settings):
    if resolution == "custom":
        return str(settings.get("customSize") or "").strip() or "1024x1024"
    if key in ("custom", "source") and ratio:
        log_ratio = _ratio_log(ratio)
        # Clamp the calculation, not the requested aspect_ratio; avoid numeric overflow at extreme input ratios.
        scale = math.exp(max(-100, min(100, log_ratio)))
        long_side = {"1k": 1536, "2k": 2048, "4k": 3840}.get(resolution, 1024)
        limit = {"1k": 1572864, "2k": 4194304, "4k": 8294400}.get(resolution, long_side**2)
        w = long_side if scale >= 1 else min(long_side * scale, math.sqrt(limit * scale))
        h = long_side if scale <= 1 else min(long_side / scale, math.sqrt(limit / scale))
        return f"{max(64, int(w / 16) * 16)}x{max(64, int(h / 16) * 16)}"
    return COMMON_IMAGE_SIZES.get(resolution, COMMON_IMAGE_SIZES["1k"])[_SIZE_RATIOS.index(ratio) if ratio in _SIZE_RATIOS else 0]


def node_image_provider_id(node):
    # Canvas defaults can supply parameters, but cannot bind a node to a provider.
    settings = node.get('runSettings')
    return str(settings.get('provider_id') or '').strip() if isinstance(settings, dict) else ''


def allowed_aspect_ratios(provider, model, model_spec_for):
    capabilities = (provider.get('image_model_capabilities') or {}).get(model)
    return (capabilities or {}).get('aspect_ratios') or (
        (model_spec_for(model) or {}).get('parameters', {}).get('aspect_ratio', {}).get('options'))


def image_settings(node, canvas, providers, model_spec_for):
    node_settings = node.get("runSettings")
    defaults = canvas.get("settings")
    settings = {**(defaults if isinstance(defaults, dict) else {}),
                **(node_settings if isinstance(node_settings, dict) else {})}
    if isinstance(node_settings, dict) and "aspect_ratio" in node_settings and "ratio" not in node_settings:
        settings.pop("ratio", None)
    from app.services.canvas_image_refs import outpaint_geometry

    outpaint = outpaint_geometry(node)
    if (settings.get("apiKind") == "video" and not outpaint) or settings.get("engine", "api") not in ("", "api"):
        raise invalid("Use browser generation for non-image-API settings")
    if not settings and not outpaint:
        return dict(size=str(node.get("generation_size") or "1024x1024"), width=node.get("width"),
                    height=node.get("height"), aspect_ratio=str(node.get("aspect_ratio") or "1:1"),
                    resolution=str(node.get("generation_resolution") or "1k"))
    provider_id = node_image_provider_id(node)
    model = str(settings.get("model") or "").strip() if provider_id else ""
    provider = next((p for p in providers if p.get("id") == provider_id.lower()), {})
    parameter_model = checked_image_model(provider, model)
    capabilities = (provider.get("image_model_capabilities") or {}).get(parameter_model)
    allowed = allowed_aspect_ratios(provider, parameter_model, model_spec_for)
    ratio = outpaint or saved_ratio(settings)
    key = "custom" if outpaint else settings.get("ratio")
    if key not in ("custom", "source"):
        ratio = nearest_aspect_ratio(ratio, allowed) if ratio in _RATIOS else ratio or "1:1"
        key = _RATIO_KEY.get(ratio, "custom")
    explicit = capabilities is None or capabilities.get("arbitrary_resolution") is True or bool(capabilities.get("resolutions"))
    compact_model = re.sub(r"[^a-z0-9]", "", parameter_model.lower())
    default_resolution = "4k" if compact_model.startswith("gptimage2") or compact_model.endswith("gptimage2") else "1k"
    resolution = str(settings.get("resolution") or "")
    size = size_for_ratio(ratio, key, resolution or default_resolution, settings) if explicit and not outpaint else ""
    if explicit and not outpaint and (resolution == "auto" or not re.fullmatch(r"\d+\s*[xX*]\s*\d+", size)):
        raise invalid("Saved image size cannot be submitted as a project image task")
    if not ratio and key in ("custom", "source") and size:
        w, h = map(int, re.split(r"[xX*]", size))
        if min(w, h) > 0:
            divisor = math.gcd(w, h)
            ratio = f"{w // divisor}:{h // divisor}"
    manual = settings.get("routingMode") == "manual"
    return dict(size=size, aspect_ratio=ratio or "", resolution=resolution if explicit else "",
                n=_count(settings.get("count")), routing_mode="manual" if manual else "auto",
                provider_id=provider_id, model=model,
                quality=str(settings.get("quality") or "auto"), output_format=str(settings.get("outputFormat") or ""),
                angle_preset=str(settings.get("anglePreset") or "none"))
