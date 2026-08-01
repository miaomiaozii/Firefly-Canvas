"""Unified, parameter-preserving routing for online media generation.

This module deliberately has no HTTP, filesystem, or FastAPI dependencies.
It selects an execution target and explains incompatibilities; adapters remain
responsible for translating the already-approved request to an upstream API.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence


ANGLE_PROMPT_PRESETS = {
    "none": "",
    "front": "正面视角",
    "front-three-quarter": "前侧四分之三视角",
    "side": "正侧面视角",
    "rear-three-quarter": "后侧四分之三视角",
    "rear": "背面视角",
    "top": "俯视角度",
    "low": "低机位仰视角度",
}

CLI_PROTOCOLS = {"codex", "gemini-cli", "jimeng"}
VIDEO_CLI_PROTOCOLS = {"jimeng"}
VIDEO_EXECUTION_PROTOCOLS = {"openai", "apimart", "volcengine", "runninghub"} | VIDEO_CLI_PROTOCOLS
FIXED_PROTOCOL_PROVIDER_IDS = {"modelscope", "volcengine", "jimeng", "runninghub"}
GEMINI_ASPECT_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
COMMON_IMAGE_SIZES = {
    "1k": ["1024x1024", "1024x1536", "1536x1024", "1008x1344", "1344x1008", "720x1280", "1280x720", "1280x544", "544x1280"],
    "2k": ["2048x2048", "1360x2048", "2048x1360", "1536x2048", "2048x1536", "1152x2048", "2048x1152", "2048x880", "880x2048"],
    "4k": ["4096x4096", "2352x3520", "3520x2352", "2448x3264", "3264x2448", "2160x3840", "3840x2160", "3840x1648", "1648x3840"],
}
ROUTED_PARAMETER_NAMES = (
    "media_type",
    "size",
    "width",
    "height",
    "aspect_ratio",
    "resolution",
    "duration",
    "quality",
    "reference_count",
    "image_reference_count",
    "video_reference_count",
    "audio_reference_count",
    "seed",
    "enhance_prompt",
    "enable_upsample",
    "watermark",
    "camera_fixed",
    "return_last_frame",
    "generate_audio",
    "protocol",
    "base_url",
    "api_key",
    "adapter_schema",
    "reference_size",
)


def _effective_protocol(provider: Mapping[str, Any], model: str) -> str:
    base = str(provider.get("protocol") or "openai").strip().lower()
    provider_id = str(provider.get("id") or "").strip().lower()
    if provider_id in FIXED_PROTOCOL_PROVIDER_IDS:
        return base
    overrides = provider.get("model_protocols") if isinstance(provider.get("model_protocols"), Mapping) else {}
    return str(overrides.get(model) or base).strip().lower()


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    size: str
    width: int | None
    height: int | None
    aspect_ratio: str
    resolution: str
    reference_asset_ids: tuple[str, ...] = ()
    media_type: str = "image"
    quality: str = "auto"
    count: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_asset_ids", tuple(self.reference_asset_ids or ()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "reference_asset_ids": list(self.reference_asset_ids),
            "reference_count": len(self.reference_asset_ids),
            "media_type": self.media_type,
            "quality": self.quality,
            "count": self.count,
        }


@dataclass(frozen=True)
class VideoGenerationRequest:
    """An immutable, user-authored online-video request for capability routing."""

    prompt: str
    size: str
    width: int | None
    height: int | None
    aspect_ratio: str
    resolution: str
    duration: int
    image_reference_asset_ids: tuple[str, ...] = ()
    video_reference_asset_ids: tuple[str, ...] = ()
    audio_reference_asset_ids: tuple[str, ...] = ()
    media_type: str = "video"
    quality: str = "auto"
    count: int = 1
    seed: int | None = None
    enhance_prompt: bool = False
    enable_upsample: bool = False
    watermark: bool = False
    camera_fixed: bool = False
    return_last_frame: bool = False
    generate_audio: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_reference_asset_ids", tuple(self.image_reference_asset_ids or ()))
        object.__setattr__(self, "video_reference_asset_ids", tuple(self.video_reference_asset_ids or ()))
        object.__setattr__(self, "audio_reference_asset_ids", tuple(self.audio_reference_asset_ids or ()))

    def to_dict(self) -> dict[str, Any]:
        image_ids = list(self.image_reference_asset_ids)
        video_ids = list(self.video_reference_asset_ids)
        audio_ids = list(self.audio_reference_asset_ids)
        return {
            "prompt": self.prompt,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration": self.duration,
            "image_reference_asset_ids": image_ids,
            "video_reference_asset_ids": video_ids,
            "audio_reference_asset_ids": audio_ids,
            "image_reference_count": len(image_ids),
            "video_reference_count": len(video_ids),
            "audio_reference_count": len(audio_ids),
            "reference_count": len(image_ids) + len(video_ids) + len(audio_ids),
            "media_type": self.media_type,
            "quality": self.quality,
            "count": self.count,
            "seed": self.seed,
            "enhance_prompt": self.enhance_prompt,
            "enable_upsample": self.enable_upsample,
            "watermark": self.watermark,
            "camera_fixed": self.camera_fixed,
            "return_last_frame": self.return_last_frame,
            "generate_audio": self.generate_audio,
        }


@dataclass(frozen=True)
class GenerationRoute:
    provider_id: str
    provider_name: str
    model: str
    protocol: str
    adapter: str
    reason: str
    request: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "model": self.model,
            "protocol": self.protocol,
            "adapter": self.adapter,
            "reason": self.reason,
            "request": copy.deepcopy(self.request),
        }


# Backward-compatible name for persisted image-route callers.
ImageGenerationRoute = GenerationRoute


class ModelRoutingError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        incompatible_parameters: Iterable[str] = (),
        code: str = "no_compatible_image_model",
    ) -> None:
        self.code = code
        incompatible = set(incompatible_parameters)
        ordered = [name for name in ROUTED_PARAMETER_NAMES if name in incompatible]
        self.incompatible_parameters = ordered
        suffix = f"（不兼容参数：{', '.join(ordered)}）" if ordered else ""
        super().__init__(message + suffix)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "incompatible_parameters": list(self.incompatible_parameters),
        }


def apply_angle_preset(prompt: str, preset: str) -> str:
    original = str(prompt or "").strip()
    key = str(preset or "none").strip().lower()
    instruction = ANGLE_PROMPT_PRESETS.get(key)
    if instruction is None:
        raise ValueError(f"未知角度参考预设：{preset}")
    if not instruction:
        return original
    return f"{original}\n\n角度参考：{instruction}。" if original else f"角度参考：{instruction}。"


def normalize_image_model_capabilities(value: Any, models: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Return sanitized explicit or trusted built-in online-image capabilities."""
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, dict[str, Any]] = {}
    for model in models:
        raw = source.get(model) if isinstance(source.get(model), Mapping) else {}
        normalized = _inferred_image_model_capabilities(model)
        for key in ("media_types", "sizes", "aspect_ratios", "resolutions"):
            if key in raw:
                normalized[key] = _string_list(raw.get(key))
        for key in ("min_width", "max_width", "min_height", "max_height", "max_reference_images"):
            if key in raw and raw.get(key) is not None:
                try:
                    normalized[key] = max(0, int(raw[key]))
                except (TypeError, ValueError):
                    continue
        for key in ("arbitrary_size", "arbitrary_aspect_ratio", "arbitrary_resolution"):
            if key in raw:
                normalized[key] = bool(raw.get(key))
        normalized.setdefault("media_types", ["image"])
        result[model] = normalized
    return result


def normalize_video_model_capabilities(value: Any, models: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Return sanitized video capabilities, preserving provider overrides.

    Existing provider records predate per-model video capability metadata.  A
    conservative, common online-video capability profile keeps those records
    usable while still making explicit provider metadata authoritative.
    """
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, dict[str, Any]] = {}
    for model in models:
        raw = source.get(model) if isinstance(source.get(model), Mapping) else {}
        normalized = _inferred_video_model_capabilities(model)
        for key in ("media_types", "sizes", "aspect_ratios", "resolutions", "qualities"):
            if key in raw:
                normalized[key] = _string_list(raw.get(key))
        for key in (
            "min_width", "max_width", "min_height", "max_height", "min_duration", "max_duration",
            "max_image_references", "max_video_references", "max_audio_references",
        ):
            if key in raw and raw.get(key) is not None:
                try:
                    normalized[key] = max(0, int(raw[key]))
                except (TypeError, ValueError):
                    continue
        for key in ("arbitrary_size", "arbitrary_aspect_ratio", "arbitrary_resolution", "arbitrary_quality"):
            if key in raw:
                normalized[key] = bool(raw.get(key))
        if isinstance(raw.get("adapter_schema"), Mapping):
            normalized["adapter_schema"] = copy.deepcopy(dict(raw["adapter_schema"]))
        result[model] = normalized
    return result


def _inferred_video_model_capabilities(model: str) -> dict[str, Any]:
    """Infer only the shared UI-safe envelope for an unannotated video model."""
    identifier = str(model or "").strip().lower().replace("_", "-")
    capability: dict[str, Any] = {
        "media_types": ["video"],
        "sizes": [
            "640x360", "854x480", "1280x720", "1920x1080",
            "360x640", "480x854", "720x1280", "1080x1920",
            "720x720", "1024x1024", "1280x544", "544x1280",
            "640x480", "480x640", "856x360", "360x856",
            "960x720", "720x960", "1440x1080", "1080x1440",
            "1920x816", "816x1920", "1080x1080", "1536x1536",
        ],
        "aspect_ratios": ["1:1", "4:3", "3:4", "16:9", "9:16", "21:9", "9:21"],
        "resolutions": ["", "auto", "480p", "720p", "1080p", "1k", "2k", "4k"],
        "qualities": ["auto", "standard", "high"],
        "min_duration": 1,
        "max_duration": 60,
        "max_image_references": 0,
        "max_video_references": 0,
        "max_audio_references": 0,
    }
    if any(token in identifier for token in ("t2v", "t2video", "text2video", "text-to-video")):
        pass
    elif any(token in identifier for token in ("i2v", "image2video", "image-to-video")):
        capability.update({
            "max_image_references": 1,
            "max_video_references": 0,
            "max_audio_references": 0,
        })
    elif any(token in identifier for token in ("multimodal", "multi-modal", "seedance", "doubao", "wan2", "3.0", "3.5")):
        capability.update({
            "max_image_references": 3,
            "max_video_references": 2,
            "max_audio_references": 3,
        })
    elif any(token in identifier for token in ("veo", "sora", "kling", "vidu")):
        capability["max_image_references"] = 1
    return capability


def _inferred_image_model_capabilities(model: str) -> dict[str, Any]:
    identifier = str(model or "").strip().lower()
    capability: dict[str, Any] = {"media_types": ["image"]}
    banana = re.fullmatch(r"nano-banana-(?:pro|2)(?:-(1k|2k|4k))?", identifier)
    if identifier in {"banana", "nano-banana-pro"}:
        banana_resolution = "1k"
    else:
        banana_resolution = banana.group(1) if banana else None
        if banana and not banana_resolution:
            banana_resolution = "1k"
    if banana_resolution:
        capability.update({
            "aspect_ratios": list(GEMINI_ASPECT_RATIOS),
            "resolutions": [banana_resolution],
            "sizes": list(COMMON_IMAGE_SIZES[banana_resolution]),
            "max_reference_images": 20,
        })
    elif identifier.startswith("gemini-") and "image" in identifier:
        capability.update({
            "aspect_ratios": list(GEMINI_ASPECT_RATIOS),
            "resolutions": ["1k", "2k", "4k"],
            "sizes": [size for values in COMMON_IMAGE_SIZES.values() for size in values],
            "max_reference_images": 20,
        })
    elif "gpt-image" in identifier or identifier.replace("_", "-").startswith("gptimage"):
        capability.update({
            "aspect_ratios": list(GEMINI_ASPECT_RATIOS),
            "resolutions": ["auto", "1k", "2k", "4k"],
            "sizes": [size for values in COMMON_IMAGE_SIZES.values() for size in values],
            "max_reference_images": 20,
        })
    elif any(token in identifier for token in ("text-to-image", "text2image", "txt2img")):
        capability["max_reference_images"] = 0
    return capability


def route_image_generation(
    request: ImageGenerationRequest,
    providers: Sequence[Mapping[str, Any]],
    *,
    provider_id: str = "",
    model: str = "",
) -> GenerationRoute:
    """Choose a compatible online model without modifying request parameters."""
    pinned_provider = str(provider_id or "").strip().lower()
    pinned_model = str(model or "").strip()
    request_snapshot = request.to_dict()
    candidates: list[tuple[Mapping[str, Any], str, dict[str, Any], list[str]]] = []

    for provider in providers:
        current_provider_id = str(provider.get("id") or "").strip().lower()
        if not current_provider_id or not bool(provider.get("enabled", True)):
            continue
        if pinned_provider and current_provider_id != pinned_provider:
            continue
        models = _string_list(provider.get("image_models"))
        capabilities = normalize_image_model_capabilities(provider.get("image_model_capabilities"), models)
        resolution_tag = str(request_snapshot.get("resolution") or "").strip().lower()
        candidates_to_check: list[str] = []
        for current_model in models:
            if pinned_model and current_model != pinned_model:
                continue
            candidates_to_check.append(current_model)
            if pinned_model and resolution_tag:
                variant = f"{pinned_model}-{resolution_tag}"
                if variant in models and variant not in candidates_to_check:
                    candidates_to_check.append(variant)
        for current_model in candidates_to_check:
            mismatches = _incompatible_parameters(request_snapshot, capabilities[current_model])
            candidates.append((provider, current_model, capabilities[current_model], mismatches))
            if not mismatches:
                protocol = str(provider.get("protocol") or "openai").strip().lower()
                pins = []
                if pinned_provider:
                    pins.append("服务商")
                if pinned_model:
                    pins.append("模型")
                reason = f"手动固定{'和'.join(pins)}并通过能力校验" if pins else "自动匹配全部用户参数"
                return GenerationRoute(
                    provider_id=current_provider_id,
                    provider_name=str(provider.get("name") or current_provider_id),
                    model=current_model,
                    protocol=protocol,
                    adapter="cli" if protocol in CLI_PROTOCOLS else "http",
                    reason=reason,
                    request=copy.deepcopy(request_snapshot),
                )

    if not candidates:
        missing = []
        if pinned_provider:
            missing.append(f"服务商 {pinned_provider}")
        if pinned_model:
            missing.append(f"模型 {pinned_model}")
        target = " / ".join(missing) or "已启用的图片模型"
        raise ModelRoutingError(f"找不到{target}", code="image_model_not_found")

    best = min(candidates, key=lambda item: len(item[3]))
    target = "手动固定的模型" if pinned_model else ("手动固定服务商中的模型" if pinned_provider else "在线图片模型")
    raise ModelRoutingError(
        f"没有{target}完整支持本次请求",
        incompatible_parameters=best[3],
    )


def route_video_generation(
    request: VideoGenerationRequest,
    providers: Sequence[Mapping[str, Any]],
    *,
    provider_id: str = "",
    model: str = "",
    adapter_incompatible_parameters: Callable[
        [VideoGenerationRequest, Mapping[str, Any], str], Iterable[str]
    ] | None = None,
) -> GenerationRoute:
    """Choose a compatible online-video model without changing user parameters."""
    pinned_provider = str(provider_id or "").strip().lower()
    pinned_model = str(model or "").strip()
    request_snapshot = request.to_dict()
    candidates: list[tuple[Mapping[str, Any], str, dict[str, Any], list[str]]] = []

    for provider in providers:
        current_provider_id = str(provider.get("id") or "").strip().lower()
        if not current_provider_id or not bool(provider.get("enabled", True)):
            continue
        if pinned_provider and current_provider_id != pinned_provider:
            continue
        models = _string_list(provider.get("video_models"))
        capabilities = normalize_video_model_capabilities(provider.get("video_model_capabilities"), models)
        for current_model in models:
            if pinned_model and current_model != pinned_model:
                continue
            protocol = _effective_protocol(provider, current_model)
            mismatches = _incompatible_video_parameters(request_snapshot, capabilities[current_model])
            if adapter_incompatible_parameters is not None:
                mismatches.extend(
                    str(name).strip()
                    for name in (adapter_incompatible_parameters(request, provider, current_model) or ())
                    if str(name).strip()
                )
            if protocol not in VIDEO_EXECUTION_PROTOCOLS:
                mismatches.append("protocol")
            mismatches = list(dict.fromkeys(mismatches))
            candidates.append((provider, current_model, capabilities[current_model], mismatches))
            if not mismatches:
                pins = []
                if pinned_provider:
                    pins.append("服务商")
                if pinned_model:
                    pins.append("模型")
                reason = f"手动固定{'和'.join(pins)}并通过能力校验" if pins else "自动匹配全部用户参数"
                return GenerationRoute(
                    provider_id=current_provider_id,
                    provider_name=str(provider.get("name") or current_provider_id),
                    model=current_model,
                    protocol=protocol,
                    adapter="cli" if protocol in CLI_PROTOCOLS else "http",
                    reason=reason,
                    request=copy.deepcopy(request_snapshot),
                )

    if not candidates:
        missing = []
        if pinned_provider:
            missing.append(f"服务商 {pinned_provider}")
        if pinned_model:
            missing.append(f"模型 {pinned_model}")
        target = " / ".join(missing) or "已启用的视频模型"
        raise ModelRoutingError(f"找不到{target}", code="video_model_not_found")

    best = min(candidates, key=lambda item: len(item[3]))
    target = "手动固定的模型" if pinned_model else ("手动固定服务商中的模型" if pinned_provider else "在线视频模型")
    if "protocol" in best[3]:
        best_provider, best_model = best[0], best[1]
        best_protocol = _effective_protocol(best_provider, best_model)
        if best_protocol in CLI_PROTOCOLS:
            message = f"{target}不可执行：当前 CLI 协议没有在线视频适配器（仅保留即梦 CLI）"
        else:
            message = f"{target}不可执行：协议 {best_protocol or 'unknown'} 没有在线视频适配器"
        raise ModelRoutingError(
            message,
            incompatible_parameters=best[3],
            code="no_compatible_video_model",
        )
    raise ModelRoutingError(
        f"没有{target}完整支持本次请求",
        incompatible_parameters=best[3],
        code="no_compatible_video_model",
    )


def _incompatible_common_parameters(request: Mapping[str, Any], capability: Mapping[str, Any]) -> list[str]:
    incompatible: list[str] = []
    media_types = capability.get("media_types") or []
    if request.get("media_type") not in media_types and "*" not in media_types:
        incompatible.append("media_type")

    sizes = capability.get("sizes") or []
    requested_size = request.get("size")
    size_supported = bool(capability.get("arbitrary_size") or "*" in sizes or (requested_size is None) or requested_size in sizes)
    if not size_supported:
        incompatible.append("size")

    aspect_ratios = capability.get("aspect_ratios") or []
    requested_aspect_ratio = request.get("aspect_ratio")
    if requested_aspect_ratio is not None and not capability.get("arbitrary_aspect_ratio") and "*" not in aspect_ratios and requested_aspect_ratio not in aspect_ratios:
        incompatible.append("aspect_ratio")

    resolutions = capability.get("resolutions") or []
    requested_resolution = request.get("resolution")
    if requested_resolution is not None and not capability.get("arbitrary_resolution") and "*" not in resolutions and requested_resolution not in resolutions:
        incompatible.append("resolution")

    for key, minimum_key, maximum_key in (
        ("width", "min_width", "max_width"),
        ("height", "min_height", "max_height"),
    ):
        value = request.get(key)
        if value is None:
            continue
        if size_supported:
            continue
        minimum = capability.get(minimum_key)
        maximum = capability.get(maximum_key)
        if minimum is None or maximum is None or value < minimum or value > maximum:
            incompatible.append(key)

    return list(dict.fromkeys(incompatible))


def _incompatible_parameters(request: Mapping[str, Any], capability: Mapping[str, Any]) -> list[str]:
    incompatible = _incompatible_common_parameters(request, capability)
    maximum_references = capability.get("max_reference_images")
    reference_count = int(request.get("reference_count") or 0)
    if reference_count and maximum_references is None:
        incompatible.append("reference_count")
    elif maximum_references is not None and reference_count > maximum_references:
        incompatible.append("reference_count")
    return list(dict.fromkeys(incompatible))


def _incompatible_video_parameters(request: Mapping[str, Any], capability: Mapping[str, Any]) -> list[str]:
    incompatible = _incompatible_common_parameters(request, capability)

    qualities = capability.get("qualities") or []
    if not capability.get("arbitrary_quality") and "*" not in qualities and request.get("quality") not in qualities:
        incompatible.append("quality")

    duration = request.get("duration")
    minimum_duration = capability.get("min_duration")
    maximum_duration = capability.get("max_duration")
    if duration is not None and (
        minimum_duration is None or maximum_duration is None or duration < minimum_duration or duration > maximum_duration
    ):
        incompatible.append("duration")

    for request_key, capability_key, incompatible_name in (
        ("image_reference_count", "max_image_references", "image_reference_count"),
        ("video_reference_count", "max_video_references", "video_reference_count"),
        ("audio_reference_count", "max_audio_references", "audio_reference_count"),
    ):
        count = int(request.get(request_key) or 0)
        maximum = capability.get(capability_key)
        if count and (maximum is None or count > maximum):
            incompatible.append(incompatible_name)
    return list(dict.fromkeys(incompatible))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result
