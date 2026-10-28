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

# 与同服务商其它图片模型（gpt-image-2 等）保持同一比例集。
# 高清修复本身没有 size/ratio 参数，输出比例继承输入图；声明画布标准比例才能路由，
# 而画布上不存在的自由比例（如外延的 1250:833）必须被停下并点名（#187 / ADR 0006）。
COMFLY_ASPECT_RATIOS = ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9', '9:21']

CAPABILITIES = {
    TOPAZ_IMAGE: {
        'media_types': ['image'],
        'max_reference_images': 1,
        # arbitrary_size：高清修复接受任意输入像素（输出=输入×倍率），没有固定像素表。
        'arbitrary_size': True,
        # 不能声明 arbitrary_resolution/arbitrary_aspect_ratio：那等于向路由谎称
        # 「8k / 任意比例都能出」，#187 契约要求没有模型支持时必须停下而不是静默降级。
        'aspect_ratios': list(COMFLY_ASPECT_RATIOS),
        # 档位即语义：model_by_resolution 只实现 1k/2k/4k（无 auto——auto 会静默用 fal 默认引擎与倍率）。
        'resolutions': ['1k', '2k', '4k'],
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
        'aspect_ratios': list(COMFLY_ASPECT_RATIOS),
        'resolutions': ['1k', '2k', '4k'],
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


# fal 官方 OpenAPI 的 model 枚举（2026-10-28 拉取 schemas.fal.ai 核实）。
# 写错一个引擎名会被 fal 422 拒掉，而画布只能在失败后告诉你——所以这里当场拦住。
FAL_MODEL_ENUMS = {
    TOPAZ_IMAGE: ['Standard V2', 'High Fidelity V2', 'Low Resolution V2', 'CGI', 'Text Refine',
                  'Wonder 3', 'Wonder', 'Standard MAX', 'Redefine', 'Recovery V2', 'Recovery'],
    TOPAZ_GENERATIVE: ['Wonder 3.5', 'Wonder 3', 'Wonder 2', 'Wonder', 'Recover 3', 'Standard MAX',
                       'Redefine', 'Recovery V2', 'Recovery'],
}


def validate_capabilities() -> None:
    """本地拦截：档位→引擎必须落在 fal 枚举里，比例/档位必须与 model_by_resolution 一致。"""
    for model, capability in CAPABILITIES.items():
        enum = FAL_MODEL_ENUMS[model]
        by_resolution = capability['fal_request']['model_by_resolution']
        bad = sorted(set(by_resolution.values()) - set(enum))
        if bad:
            raise SystemExit(f'{model} 的引擎名不在 fal 官方枚举内：{bad}；合法值：{enum}')
        declared = set(capability['resolutions'])
        if declared != set(by_resolution):
            raise SystemExit(f'{model} 的 resolutions 声明 {sorted(declared)} 与 model_by_resolution 键 {sorted(by_resolution)} 不一致')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', default='provider-ms5fzxu7')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    validate_capabilities()
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
        # 旧版本曾误写 arbitrary_aspect_ratio/arbitrary_resolution=True（向路由谎称 8k/任意比例可出，
        # 直接违反 #187 契约）。本工具是 fal 能力的唯一事实源：新规格不再声明的开关必须清掉。
        for key in ('arbitrary_size', 'arbitrary_aspect_ratio', 'arbitrary_resolution', 'arbitrary_quality'):
            if key not in capability:
                merged.pop(key, None)
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
