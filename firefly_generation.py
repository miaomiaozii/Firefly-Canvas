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
# 服务商文档（gpt-best.apifox.cn api-341817446 / api-420361512）：nano-banana 与
# nano-banana-pro（gemini-3-pro-image-preview）的基础宽高比。
# 21:9 与其镜像 9:21 一起支持（服务商档位 1280x544 / 544x1280 成对存在）。
GEMINI_ASPECT_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "9:21"]
# 服务商文档（api-420646217 / api-481108294 / api-512112774 / api-512273870）：
# Nano Banana 2 家族（gemini-3.1-flash-image-preview[-lite]、nano-banana-2*、
# gemini-nano-banana-2.1*）额外支持极端比例 1:4 / 4:1 / 8:1 / 1:8，共 15 种。
GEMINI_FLASH_IMAGE_ASPECT_RATIOS = [*GEMINI_ASPECT_RATIOS, "1:4", "4:1", "8:1", "1:8"]
# gpt-image 家族（gpt-image-2 / -all / 2.5-flare / 2.5-sunburst / gpt-image-1.5 /
# gpt-4o-image）不吃 aspect_ratio，只接受 size，且服务商硬约束长宽比 ≤3:1
# （api-447258891 / api-515692647 / api-515693868）。所以极端比例 1:4 / 4:1 /
# 8:1 / 1:8 对 gpt 家族不可用，可用集就是 11 种基础比例。
GPT_IMAGE_ASPECT_RATIOS = list(GEMINI_ASPECT_RATIOS)
# 每种比例的像素档位（前端 SIZE_MAP 必须与此逐一对应，见 docs/api-parameter-rules.md）。
# 服务商在收到 aspect_ratio 时不写 size，档位只用于画布几何与能力校验。
COMMON_IMAGE_SIZES = {
    "1k": [
        "1024x1024", "1024x1536", "1536x1024", "1008x1344", "1344x1008", "720x1280", "1280x720", "1280x544", "544x1280",
        "1152x1440", "1440x1152", "384x1536", "1536x384", "192x1536", "1536x192",
    ],
    "2k": [
        "2048x2048", "1360x2048", "2048x1360", "1536x2048", "2048x1536", "1152x2048", "2048x1152", "2048x880", "880x2048",
        "1600x2000", "2000x1600", "512x2048", "2048x512", "256x2048", "2048x256",
    ],
    "4k": [
        "4096x4096", "2352x3520", "3520x2352", "2448x3264", "3264x2448", "2160x3840", "3840x2160", "3840x1648", "1648x3840",
        "3072x3840", "3840x3072", "960x3840", "3840x960", "480x3840", "3840x480",
    ],
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
        if raw:
            # Explicit declarations are authoritative: the permissive defaults
            # inferred for unknown models (#135) must not leak into models that
            # declare their own capabilities.
            for key in ("arbitrary_size", "arbitrary_aspect_ratio", "arbitrary_resolution"):
                normalized.pop(key, None)
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
        # fal 队列协议（fal.ai 平台兼容）的端点参数表：与视频 adapter_schema 同理，
        # 参数集来自服务商/fal 官方 schema，必须随能力一起存活，不能写死在代码里。
        if isinstance(raw.get("fal_request"), Mapping):
            normalized["fal_request"] = copy.deepcopy(dict(raw["fal_request"]))
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


def _banana_aspect_ratios(identifier: str) -> list[str]:
    """Aspect-ratio set for a banana/gemini image model, per provider docs.

    Only the Nano Banana 2 family (3.1-Flash based) accepts the extreme ratios
    1:4 / 4:1 / 8:1 / 1:8; nano-banana and nano-banana-pro stay on the base set.
    Both sets include the 21:9 mirror 9:21 (paired provider size presets).
    """
    if re.fullmatch(r"(?:gemini-)?nano-banana-(?:2|2\.1)(?:-(?:1k|2k|4k))?", identifier):
        return list(GEMINI_FLASH_IMAGE_ASPECT_RATIOS)
    if "nano-banana" not in identifier and "3.1-flash" in identifier:
        return list(GEMINI_FLASH_IMAGE_ASPECT_RATIOS)
    return list(GEMINI_ASPECT_RATIOS)


def _inferred_image_model_capabilities(model: str) -> dict[str, Any]:
    identifier = str(model or "").strip().lower()
    capability: dict[str, Any] = {"media_types": ["image"]}
    banana = re.fullmatch(r"(?:gemini-)?nano-banana-(?:pro|2\.1|2)(?:-(1k|2k|4k))?", identifier)
    if identifier == "nano-banana":
        capability.update({
            "aspect_ratios": list(GEMINI_ASPECT_RATIOS),
            "resolutions": ["1k"],
            "sizes": list(COMMON_IMAGE_SIZES["1k"]),
            "max_reference_images": 20,
        })
        return capability
    if identifier == "nano-banana-hd":
        capability.update({
            "aspect_ratios": list(GEMINI_ASPECT_RATIOS),
            "resolutions": ["2k", "4k"],
            "sizes": [*COMMON_IMAGE_SIZES["2k"], *COMMON_IMAGE_SIZES["4k"]],
            "max_reference_images": 20,
        })
        return capability
    if identifier in {"banana", "nano-banana-pro"}:
        banana_resolution = "1k"
    else:
        banana_resolution = banana.group(1) if banana else None
        if banana and not banana_resolution:
            banana_resolution = "1k"
    if banana_resolution:
        capability.update({
            "aspect_ratios": _banana_aspect_ratios(identifier),
            "resolutions": [banana_resolution],
            "sizes": list(COMMON_IMAGE_SIZES[banana_resolution]),
            "max_reference_images": 20,
        })
    elif identifier.startswith("gemini-") and "image" in identifier:
        capability.update({
            "aspect_ratios": _banana_aspect_ratios(identifier),
            "resolutions": ["1k", "2k", "4k"],
            "sizes": [size for values in COMMON_IMAGE_SIZES.values() for size in values],
            "max_reference_images": 20,
        })
    elif "gpt-image" in identifier or identifier.replace("_", "-").startswith("gptimage") or ("gpt" in identifier and "image" in identifier):
        # gpt 家族的参数形态与 banana 不同：banana 走 aspect_ratio + image_size，
        # gpt-image 只接受 size，像素由 app/core.py:gpt_image_2_request_size 按
        # 「比例 + 档位」换算（取整到 16 倍数、长边 ≤3840、总像素 ≤8294400、≤3:1）。
        # 因此不能用 COMMON_IMAGE_SIZES（banana 档位白名单）去校验 gpt 的 size：
        # 那会把 4:5 / 5:4 等合法选择误判成不兼容（#issue: 400 image_model 路由失败）。
        # size 交给服务商自校验，本地只锁比例和档位。
        capability.update({
            "aspect_ratios": list(GPT_IMAGE_ASPECT_RATIOS),
            "arbitrary_size": True,
            "resolutions": ["auto", "1k", "2k", "4k"],
            "max_reference_images": 20,
        })
    elif any(token in identifier for token in ("text-to-image", "text2image", "txt2img")):
        capability["max_reference_images"] = 0
    else:
        # Unknown model with no reliable capability data: be permissive rather than
        # over-restricting. ADR 0006 fail-fast applies to known families (gemini/banana/
        # gpt-image) with declared limits; unknown models defer to the provider, which
        # rejects truly unsupported parameters itself.
        capability.update({
            "arbitrary_size": True,
            "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True,
            "max_reference_images": 20,
        })
    return capability


def _resolve_display_model_name(provider: Mapping[str, Any], model: str) -> str:
    """Map a friendly display name (provider.model_names) back to the raw model id.

    The canvas renders model options with provider display names (e.g. "Nano
    Banana 2.1") while keeping the raw id as the value; generation requests may
    still carry the friendly name (manual entry, agent instructions), so routing
    resolves it against the provider's own name map before matching known models.
    """
    names = provider.get("model_names")
    if not isinstance(names, Mapping):
        return ""
    target = str(model or "").strip().lower()
    if not target:
        return ""
    for raw_id, label in names.items():
        if str(label or "").strip().lower() == target:
            return str(raw_id).strip()
    return ""


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
        available_models = provider.get("available_models") or {}
        discovered_models = _string_list(available_models.get("image_models")) if isinstance(available_models, Mapping) else []
        known_models = list(dict.fromkeys([*models, *discovered_models]))
        capabilities = normalize_image_model_capabilities(provider.get("image_model_capabilities"), known_models)
        resolution_tag = str(request_snapshot.get("resolution") or "").strip().lower()
        candidates_to_check: list[str] = []
        if pinned_model:
            effective_model = pinned_model
            if effective_model not in known_models:
                effective_model = _resolve_display_model_name(provider, effective_model) or effective_model
            variant = ""
            if resolution_tag in {"2k", "4k"}:
                if effective_model == "nano-banana":
                    variant = "nano-banana-hd"
                elif not re.search(r"-(?:1k|2k|4k)$", effective_model):
                    variant = f"{effective_model}-{resolution_tag}"
            if variant in known_models:
                candidates_to_check.append(variant)
            if effective_model in known_models:
                candidates_to_check.append(effective_model)
        else:
            candidates_to_check.extend(models)
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
        f"没有{target}完整支持本次请求{_capability_hint(*best, request_snapshot)}",
        incompatible_parameters=best[3],
    )


CAPABILITY_HINT_KEYS = {"aspect_ratio": "aspect_ratios", "resolution": "resolutions", "size": "sizes", "quality": "qualities"}


def _capability_hint(provider: Mapping[str, Any], model: str, capability: Mapping[str, Any], mismatches: Sequence[str], request: Mapping[str, Any]) -> str:
    """把「哪个参数超出该模型能力、该模型实际支持什么」写进路由错误文案。

    预检失败时前端只显示 message，光一句「不支持本次请求」无法自查。
    典型场景：节点上残留 banana 的 1:4，切到 gpt-image 家族（长宽比上限 3:1）后
    aspect_ratio 不兼容——文案里直接给出该模型支持的比例清单和本次请求值。
    """
    parts = []
    for parameter in mismatches:
        key = CAPABILITY_HINT_KEYS.get(parameter)
        if not key:
            continue
        allowed = capability.get(key) or []
        if not isinstance(allowed, (list, tuple)) or not allowed or "*" in allowed:
            continue
        listed = "/".join(str(item) for item in allowed)
        parts.append(f"{model} 的 {parameter} 支持 {listed}，本次请求是 {request.get(parameter)}")
    return f"（{'；'.join(parts)}）" if parts else ""


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
