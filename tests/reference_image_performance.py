"""Measure reference-image upload preparation, with an optional bounded live probe."""

import argparse
import asyncio
import json
import sys
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_PROJECT_ID = "520e1418682f4a8f84f20a7e61e46eb6"
DEFAULT_ASSET_ID = "254129c2ac2dc3a5989ba486cbe8f90df0c55c738df5bc3686a97cfb56b05fd9"
DEFAULT_PROMPT = (
    "将图中的黑猫改成一只可爱的白猫，毛发自然分布，圆润明亮的眼睛，毛发柔软蓬松，"
    "安静地坐在温馨的室内环境中，暖黄色灯光洒落，周围有柔软毛毯、木质家具、窗边盆栽"
    "与小装饰品，整体氛围治愈、舒适、温暖，细节丰富，画面柔和，高质量，写实风格"
)


def emit(stage, **details):
    print(json.dumps({"stage": stage, **details}, ensure_ascii=False), flush=True)


def resolve_source(args):
    if args.path:
        path = Path(args.path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"参考图不存在：{path}")
        return path

    import main

    record = main.project_store_call("media_record", args.project_id, args.asset_id)
    if not isinstance(record, dict) or not record.get("path"):
        raise FileNotFoundError(f"项目素材不存在：{args.project_id}/{args.asset_id}")
    path = Path(record["path"])
    if not path.is_file():
        raise FileNotFoundError(f"项目素材文件不存在：{path}")
    return path


def verify_pixel_exact(source_path, payload):
    with Image.open(source_path) as source, Image.open(BytesIO(payload)) as decoded:
        source.load()
        decoded.load()
        if source.size != decoded.size:
            return False
        if source.mode not in {"RGB", "RGBA"}:
            return True
        return ImageChops.difference(source, decoded.convert(source.mode)).getbbox() is None


def run_offline(source_path):
    from app.services import generation

    generation._lossless_webp_reference_upload.cache_clear()
    source_bytes = source_path.stat().st_size
    started = time.perf_counter()
    filename, payload, mime_type = generation._openai_edit_reference_upload(str(source_path), True)
    first_seconds = time.perf_counter() - started
    started = time.perf_counter()
    cached = generation._openai_edit_reference_upload(str(source_path), True)
    cache_seconds = time.perf_counter() - started
    exact = verify_pixel_exact(source_path, payload)
    result = {
        "source": str(source_path),
        "source_bytes": source_bytes,
        "upload_filename": filename,
        "upload_mime": mime_type,
        "upload_bytes": len(payload),
        "saved_percent": round((1 - len(payload) / source_bytes) * 100, 2),
        "first_prepare_seconds": round(first_seconds, 3),
        "cached_prepare_seconds": round(cache_seconds, 6),
        "pixel_exact": exact,
        "cache_hit": cached[1] is payload and generation._lossless_webp_reference_upload.cache_info().hits >= 1,
    }
    emit("offline_complete", **result)
    if not exact:
        raise RuntimeError("无损上传载荷与源图像素不一致")
    return result


async def heartbeat(started, stop):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except asyncio.TimeoutError:
            emit("live_waiting", elapsed_seconds=round(time.perf_counter() - started, 1))


async def run_live(args):
    import main
    from app.core import PROJECT_MEDIA_ADAPTER_PROJECT, project_media_adapter_reference

    if args.timeout > 180:
        raise ValueError("live硬超时最多180秒，拒绝无反馈长跑")
    asset_ids = [args.asset_id, *args.extra_asset_id]
    references = [
        {
            "url": project_media_adapter_reference(args.project_id, asset_id),
            "name": f"reference-{index + 1}.png",
            "role": "source",
        }
        for index, asset_id in enumerate(asset_ids)
    ]
    scope = PROJECT_MEDIA_ADAPTER_PROJECT.set(args.project_id)
    stop = asyncio.Event()
    started = time.perf_counter()
    reporter = asyncio.create_task(heartbeat(started, stop))
    emit(
        "live_started",
        timeout_seconds=args.timeout,
        provider=args.provider,
        model=args.model,
        reference_count=len(references),
    )
    try:
        request = main.generate_ai_image(
            args.prompt,
            args.size,
            "high",
            args.model,
            references,
            args.provider,
            preserve_model=True,
            image_parameters={
                "aspect_ratio": args.aspect_ratio,
                "resolution": args.resolution,
                "media_type": "image",
            },
        )
        image, raw = await asyncio.wait_for(request, timeout=args.timeout)
        value = str(image.get("value") or "") if isinstance(image, dict) else ""
        emit(
            "live_complete",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            result_type=image.get("type") if isinstance(image, dict) else type(image).__name__,
            result_bytes=len(value),
            raw_keys=sorted(raw) if isinstance(raw, dict) else [],
        )
    except asyncio.TimeoutError as exc:
        emit("live_timeout", elapsed_seconds=round(time.perf_counter() - started, 3))
        raise RuntimeError(f"真实探测超过{args.timeout}秒，已自动终止") from exc
    finally:
        stop.set()
        await reporter
        PROJECT_MEDIA_ADAPTER_PROJECT.reset(scope)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--asset-id", default=DEFAULT_ASSET_ID)
    parser.add_argument("--extra-asset-id", action="append", default=[])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--provider", default="provider-ms5fzxu7")
    parser.add_argument("--model", default="gemini-3.1-flash-image-preview")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--size", default="3840x1648")
    parser.add_argument("--aspect-ratio", default="21:9")
    parser.add_argument("--resolution", default="4k")
    return parser.parse_args()


def main_entry():
    args = parse_args()
    source = resolve_source(args)
    run_offline(source)
    if args.live:
        asyncio.run(run_live(args))


if __name__ == "__main__":
    main_entry()
