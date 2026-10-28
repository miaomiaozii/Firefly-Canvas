#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 comfly 服务商开启 fal.ai 平台兼容（fal-ai/topaz 高清修复系列）。

fal 官方每个端点的参数 schema 不同，所以参数不写死在代码里：
  · provider['model_request_modes'][model] = 'fal'   → 走队列协议（/fal/…）
  · provider['image_model_capabilities'][model]['fal_request']
        endpoint    : fal 端点 ID（拼进 URL：{base}/fal/{endpoint}）
        path_prefix : 中转商前缀，comfly 为 'fal'
        params      : 提交体参数；不写 model 时用 fal 官方默认
        model_by_resolution : 画布档位 → Topaz 引擎（可选）
运行：python tools/configure_comfly_fal_topaz.py [--provider <id>] [--dry-run]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'api_providers.json')

TOPAZ_IMAGE = 'fal-ai/topaz/upscale/image'
TOPAZ_GENERATIVE = 'fal-ai/topaz/upscale/image/generative'

CAPABILITIES = {
    TOPAZ_IMAGE: {
        'media_types': ['image'],
        'max_reference_images': 1,
        'arbitrary_size': True,
        'arbitrary_aspect_ratio': True,
        'arbitrary_resolution': True,
        'fal_request': {
            'endpoint': TOPAZ_IMAGE,
            'path_prefix': 'fal',
            # 官方默认：model=Standard V2、upscale_factor=2、output_format=jpeg。
            # 只覆盖我们确信的：无损输出 + 人像增强开。model 交给档位映射。
            'params': {'output_format': 'png', 'face_enhancement': True, 'crop_to_fill': False},
            'model_by_resolution': {
                '1k': 'Standard V2',
                '2k': 'High Fidelity V2',
                '4k': 'Wonder 3',
            },
        },
    },
    TOPAZ_GENERATIVE: {
        'media_types': ['image'],
        'max_reference_images': 1,
        'arbitrary_size': True,
        'arbitrary_aspect_ratio': True,
        'arbitrary_resolution': True,
        'fal_request': {
            'endpoint': TOPAZ_GENERATIVE,
            'path_prefix': 'fal',
            'params': {'output_format': 'png'},
            'model_by_resolution': {
                '1k': 'Recover 3',
                '2k': 'Wonder 3',
                '4k': 'Wonder 3.5',
            },
        },
    },
}

DISPLAY_NAMES = {
    TOPAZ_IMAGE: 'Topaz 高清修复',
    TOPAZ_GENERATIVE: 'Topaz 生成式高清修复',
}


def find_provider(providers, wanted):
    """先按 id 找；id 不稳时回退到显示名（comfly 曾被重命名为 provider-ms5fzxu7）。"""
    target = next((item for item in providers if item.get('id') == wanted), None)
    if target is None:
        target = next((item for item in providers if str(item.get('name') or '').strip().lower() == str(wanted).strip().lower()), None)
        if target is not None:
            print(f'按 id 未命中，已按显示名定位到 {target.get("id")}')
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', default='provider-ms5fzxu7')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    providers = json.load(io.open(CONFIG, encoding='utf-8'))
    if isinstance(providers, dict):
        providers = providers.get('providers', [])
    target = find_provider(providers, args.provider)
    if target is None:
        print(f'未找到服务商 {args.provider}；现有：{[p.get("id") for p in providers]}', file=sys.stderr)
        return 1

    models = target.setdefault('image_models', [])
    for model in (TOPAZ_IMAGE, TOPAZ_GENERATIVE):
        if model not in models:
            models.append(model)

    modes = target.setdefault('model_request_modes', {})
    modes.update({model: 'fal' for model in CAPABILITIES})

    capabilities = target.setdefault('image_model_capabilities', {})
    for model, capability in CAPABILITIES.items():
        merged = dict(capabilities.get(model) or {})
        merged.update(capability)
        capabilities[model] = merged

    names = target.setdefault('model_names', {})
    names.update({model: label for model, label in DISPLAY_NAMES.items() if not names.get(model)})

    available = target.setdefault('available_models', {})
    image_pool = available.setdefault('image_models', [])
    for model in CAPABILITIES:
        if model not in image_pool:
            image_pool.append(model)

    if args.dry_run:
        print(json.dumps({k: target.get(k) for k in ('model_request_modes', 'image_model_capabilities')}, ensure_ascii=False, indent=2)[:2000])
        return 0

    shutil.copyfile(CONFIG, CONFIG + '.bak-fal-topaz')
    io.open(CONFIG, 'w', encoding='utf-8').write(json.dumps(providers, ensure_ascii=False, indent=2))
    print(f'已写入 {CONFIG}（备份：{CONFIG}.bak-fal-topaz）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
