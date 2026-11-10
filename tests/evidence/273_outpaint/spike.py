"""Spike(#304) 路线 A：bailian 指令式 outpaint 能力验证（整图指令外扩）。

流程（预算：2 次图像调用，本脚本只用 1 次）：
1. GET {base}/models 列 bailian 端点在册模型（不计图像调用预算）。
2. 挑一个能收 image 输入的模型：qwen-image-3.0-pro / 2.1-pro / 2.0-pro / qwen-image-edit-plus*。
3. 对本地现成图发"向外扩展画面四周"指令，size 给原图 1.4 倍。
4. PIL 验证：输出尺寸 = 请求尺寸还是原尺寸？同尺寸则中央区像素差判断原内容是否被重绘。

约束：只新建 tests/evidence/273_outpaint/ 下文件，不改任何既有文件。
运行：python tests/evidence/273_outpaint/spike.py（需 D:\\Firefly-Canvas 主仓有 bailian 凭证）。
"""
import base64
import io
import json
import os
import sys
import time
import types
from datetime import datetime, timezone

REPO = r'D:\ff-wt\w304'
RUNTIME = r'D:\Firefly-Canvas'  # 主仓：API/.env + data/api_providers.json 所在
EVIDENCE_DIR = os.path.join(REPO, 'tests', 'evidence', '273_outpaint')

os.environ.setdefault('FIREFLY_RUNTIME_ROOT', RUNTIME)
sys.path.insert(0, REPO)

# app.services 通过 _main() 解析 API_ENV_FILE / API_PROVIDERS_FILE：绑定一个轻量 fake main 模块
import app.core  # noqa: F401  (确保 app.core 完整加载)
from app.services import _base  # noqa: E402

_fake_main = types.ModuleType('spike_fake_main')
_fake_main.API_ENV_FILE = os.path.join(RUNTIME, 'API', '.env')
_fake_main.API_PROVIDERS_FILE = os.path.join(RUNTIME, 'data', 'api_providers.json')
_fake_main.AI_BASE_URL = ''
_base._bind_main(_fake_main)

from PIL import Image  # noqa: E402
from app.core import _bailian_local_edit_adapter  # noqa: E402
from app.services.providers import (  # noqa: E402
    get_api_provider_exact,
    provider_endpoint_url,
    provider_env_key_value,
)

INPUT_IMAGE = os.path.join(RUNTIME, 'assets', 'input', 'ai_ref_10073401ad09.png')
PROMPT = '向外扩展画面四周，延续背景与构图，不改变原图已有内容'
SCALE = 1.4
PREFERRED_MODELS = ['qwen-image-3.0-pro', 'qwen-image-2.1-pro', 'qwen-image-2.0-pro']


def snap8(v):
    """DashScope 尺寸惯例：8 的整数倍。"""
    return max(64, int(round(v / 8.0)) * 8)


def central_region_mean_abs_diff(im_a, im_b):
    """两图同尺寸时，中央 50% 区域逐像素平均绝对差（0-255 每通道）。"""
    w, h = im_a.size
    box = (w // 4, h // 4, 3 * w // 4, 3 * h // 4)
    a = im_a.crop(box).getdata()
    b = im_b.crop(box).getdata()
    total = 0
    for pa, pb in zip(a, b):
        for i in range(3):
            total += abs(pa[i] - pb[i])
    return total / ((3 * w // 4 - w // 4) * (3 * h // 4 - h // 4) * 3)


def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    result = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'issue': '#304 (route A outpaint spike, #273 lineage)',
        'input': os.path.relpath(INPUT_IMAGE, REPO),
        'prompt': PROMPT,
        'scale': SCALE,
    }

    provider = get_api_provider_exact('bailian')
    key = (provider_env_key_value('bailian') or '').strip()
    if not key:
        raise SystemExit('bailian 凭证缺失（需 D:\\Firefly-Canvas\\API\\.env 或 API_PROVIDER_BAILIAN_KEY）')
    gen_url = provider_endpoint_url(provider, 'image_generation_endpoint', '/v1/images/generations')
    base_url = gen_url.rsplit('/images/generations', 1)[0].rstrip('/')

    # ---- 1) 模型列表（GET {base}/models，不计图像调用预算）----
    import requests
    t0 = time.time()
    r = requests.get(base_url + '/models', headers={'Authorization': 'Bearer ' + key}, timeout=60)
    listing = {'url': base_url + '/models', 'status': r.status_code, 'latency_ms': int((time.time() - t0) * 1000)}
    model_ids = []
    if r.status_code == 200:
        data = r.json()
        items = data.get('data') if isinstance(data, dict) else data
        model_ids = sorted(str(m['id']) for m in items if isinstance(m, dict) and m.get('id'))
        listing['count'] = len(model_ids)
    result['models_listing'] = listing
    result['model_ids'] = model_ids

    # ---- 2) 选模型 ----
    chosen, chosen_source = None, None
    for m in PREFERRED_MODELS:
        if m in model_ids:
            chosen, chosen_source = m, 'listing(preferred)'
            break
    if not chosen:
        edit_plus = sorted(m for m in model_ids if 'qwen-image-edit' in m and 'plus' in m)
        if edit_plus:
            chosen, chosen_source = edit_plus[0], 'listing(edit-plus)'
    if not chosen:
        registered = provider.get('image_models') or []
        chosen, chosen_source = str(registered[0] or 'qwen-image-2.1-pro'), 'provider_registry'
    result['model'] = chosen
    result['model_source'] = chosen_source

    # ---- 3) 一次图像调用：outpaint ----
    im_in = Image.open(INPUT_IMAGE).convert('RGB')
    w, h = im_in.size
    req_w, req_h = snap8(w * SCALE), snap8(h * SCALE)
    size = f'{req_w}x{req_h}'
    result['input_size'] = f'{w}x{h}'
    result['requested_size'] = size
    with open(INPUT_IMAGE, 'rb') as f:
        png_bytes = f.read()

    out_path = os.path.join(EVIDENCE_DIR, 'outpaint_out.png')
    in_copy = os.path.join(EVIDENCE_DIR, 'in.png')
    with open(in_copy, 'wb') as f:
        f.write(png_bytes)
    t0 = time.time()
    try:
        out_bytes = _bailian_local_edit_adapter({'image': png_bytes, 'prompt': PROMPT, 'size': size, 'model': chosen})
        call = {'ok': True, 'latency_s': round(time.time() - t0, 1), 'out_bytes': len(out_bytes)}
        with open(out_path, 'wb') as f:
            f.write(out_bytes)
    except Exception as e:  # noqa: BLE001 — 记录失败原文
        call = {'ok': False, 'latency_s': round(time.time() - t0, 1), 'error': f'{type(e).__name__}: {e}'}
    result['call'] = call

    if call['ok']:
        im_out = Image.open(io.BytesIO(out_bytes)).convert('RGB')
        ow, oh = im_out.size
        result['output_size'] = f'{ow}x{oh}'
        result['output_matches_requested'] = (ow, oh) == (req_w, req_h)
        result['output_matches_original'] = (ow, oh) == (w, h)
        # 中央区重绘判定（无论尺寸是否变化都算一遍：输出缩回原图尺寸后比中央 50%）
        im_out_resized = im_out.resize((w, h))
        mad = central_region_mean_abs_diff(im_in, im_out_resized)
        result['central_50pct_mean_abs_diff'] = round(mad, 3)
        # 参考阈值：<1.0 像素级几乎一致；1-5 轻微重绘；>5 明显整图重绘
        result['central_verdict'] = (
            'near-identical(<1.0)' if mad < 1.0 else 'mild-redraw(1.0-5.0)' if mad <= 5.0 else 'heavy-redraw(>5.0)'
        )

    with open(os.path.join(EVIDENCE_DIR, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
