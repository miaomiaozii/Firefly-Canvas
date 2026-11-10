# -*- coding: utf-8 -*-
"""#305 outpaint 路线 B spike：品红预扩边图 → bailian 图像模型局部回填 → 原区改动度量化。

流程（能力验证，不改任何既有文件）：
1. GET {base}/models 尝试列举在册模型（MaaS 专属端点预期 404；注册模型以
   #276 实测登记的 qwen-image-2.1-pro / qwen-image-2.1-turbo 为准，
   见 app/data/model_specs.json 与 .pipeline/batches/wave2 批次记录）。
2. 对 01_expanded_input.png（1346x1268，中央 897x845 原图 offset (224,211)，
   四边品红 #FF00FF）发指令：
   '仅将品红色区域替换为与原图边缘自然衔接的延续内容，中央原图区域保持不变'
   主模型 qwen-image-2.1-pro；失败则用预算内第二次调用回退 qwen-image-2.1-turbo。
3. PIL 裁回 (224,211,1121,1056) 与 00_src.png 逐像素平均差（原区改动度），
   并统计全图/四边带/原区内的品红残留像素计数。

产物：02_result.png、02_metrics.json（stdout 打印摘要）。
运行：python tests/evidence/273_outpaint_b/spike.py   # 预算：最多 2 次图像调用
"""
import base64
import io
import json
import os
import sys
import types
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
WORKTREE = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, WORKTREE)

# bailian provider 解析依赖运行时 main 模块绑定（api_providers.json 在运行时目录，
# 各 worktree 共享 D:/Firefly-Canvas 的 data/ 与 API/.env——#276 已写入 provider 记录）。
RT = r'D:/Firefly-Canvas'
_fake = types.ModuleType('spike_main')
_fake.API_PROVIDERS_FILE = os.path.join(RT, 'data', 'api_providers.json')
_fake.API_ENV_FILE = os.path.join(RT, 'API', '.env')
_fake.AI_BASE_URL = ''
sys.modules['spike_main'] = _fake
os.environ['FIREFLY_MAIN_MODULE'] = 'spike_main'

import requests
from PIL import Image

from app.core import _bailian_local_edit_adapter
from app.services.providers import api_headers, get_api_provider_exact, provider_endpoint_url

MODEL_PRIMARY = 'qwen-image-2.1-pro'      # #276 实测：同步出图、size WxH、面积 262144–6553600、参考图≤2
MODEL_FALLBACK = 'qwen-image-2.1-turbo'   # 预算内第 2 次调用
PROMPT = '仅将品红色区域替换为与原图边缘自然衔接的延续内容，中央原图区域保持不变'
SIZE = '1346x1268'
OFFSET = (224, 211)
CROP = (224, 211, 224 + 897, 211 + 845)   # 中央原图 897x845


def is_magenta(px, r_min=200, g_max=80, b_min=200):
    return px[0] >= r_min and px[1] <= g_max and px[2] >= b_min


def probe_models(p):
    """GET {base}/models（指令要求先探在册模型）。MaaS 专属端点预期 404。"""
    gen = provider_endpoint_url(p, 'image_generation_endpoint', '/v1/images/generations')
    u = urllib.parse.urlsplit(gen)
    h = api_headers(json_body=True, provider=p, model=MODEL_PRIMARY)
    out = {}
    for path in ('/v1/models', '/models'):
        url = f'{u.scheme}://{u.netloc}{path}'
        try:
            r = requests.get(url, headers=h, timeout=30)
            ids = None
            if r.status_code == 200:
                try:
                    ids = [m.get('id') for m in r.json().get('data', [])]
                except Exception:
                    ids = None
            out[path] = {'status': r.status_code, 'ids': ids, 'text': None if r.status_code == 200 else r.text[:200]}
        except Exception as e:
            out[path] = {'status': None, 'ids': None, 'text': str(e)[:200]}
    return out


def magenta_count(im, box=None):
    c = im.crop(box) if box else im
    px = c.load()
    n = 0
    for y in range(c.height):
        row = px
        for x in range(c.width):
            if is_magenta(row[x, y]):
                n += 1
    return n


def main():
    src = Image.open(os.path.join(HERE, '00_src.png')).convert('RGB')
    expanded_bytes = open(os.path.join(HERE, '01_expanded_input.png'), 'rb').read()
    print(f'src={src.size} expanded_bytes={len(expanded_bytes)} crop={CROP}')

    p = get_api_provider_exact('bailian')
    if p is None:
        raise SystemExit('bailian provider 未注册（运行时 data/api_providers.json）')
    print('models probe:', json.dumps(probe_models(p), ensure_ascii=False))

    result_bytes = None
    used_model = None
    errors = []
    for model in (MODEL_PRIMARY, MODEL_FALLBACK):
        try:
            print(f'>> calling {model} size={SIZE} ...')
            result_bytes = _bailian_local_edit_adapter(
                {'image': expanded_bytes, 'prompt': PROMPT, 'model': model, 'size': SIZE})
            used_model = model
            print(f'<< {model} ok, {len(result_bytes)} bytes')
            break
        except Exception as e:
            errors.append(f'{model}: {e}')
            print(f'<< {model} FAILED: {e}')
    if result_bytes is None:
        print('BOTH CALLS FAILED:', json.dumps(errors, ensure_ascii=False))
        json.dump({'ok': False, 'errors': errors}, open(os.path.join(HERE, '02_metrics.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        return 1

    out_path = os.path.join(HERE, '02_result.png')
    open(out_path, 'wb').write(result_bytes)
    res = Image.open(io.BytesIO(result_bytes)).convert('RGB')
    resized = False
    if res.size != (1346, 1268):
        print(f'WARN result size {res.size} != 1346x1268, resizing (resized={True})')
        res = res.resize((1346, 1268), Image.LANCZOS)
        resized = True
        open(os.path.join(HERE, '02_result_resized.png'), 'wb').write(result_bytes)

    # --- 原区改动度：裁回 (224,211,1121,1056) 逐像素平均差 ---
    res_crop = res.crop(CROP)
    ps, pr = src.load(), res_crop.load()
    total_l1 = 0
    sq = 0.0
    changed_px = 0
    n = src.width * src.height
    for y in range(src.height):
        for x in range(src.width):
            a, b = ps[x, y], pr[x, y]
            d = abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
            total_l1 += d
            sq += (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
            if d / 3 > 15:   # 平均单通道差 > 15 记为"可见改动像素"
                changed_px += 1
    mean_l1 = total_l1 / n                # 0..765
    mean_per_ch = mean_l1 / 3              # 0..255
    rmse = (sq / (3 * n)) ** 0.5           # 每通道 RMSE 0..255

    # --- 品红残留（像素计数） ---
    bands = {
        'top': (0, 0, res.width, OFFSET[1]),
        'bottom': (0, CROP[3], res.width, res.height),
        'left': (0, OFFSET[1], OFFSET[0], CROP[3]),
        'right': (CROP[2], OFFSET[1], res.width, CROP[3]),
        'inside_src': CROP,
    }
    magenta = {k: magenta_count(res, box) for k, box in bands.items()}
    magenta['whole'] = magenta_count(res)

    metrics = {
        'ok': True,
        'model': used_model,
        'prompt': PROMPT,
        'size_requested': SIZE,
        'result_native_size': None,
        'resized': resized,
        'crop': list(CROP),
        'offset': list(OFFSET),
        'central_change': {
            'pixels': n,
            'mean_l1_total_0_765': round(mean_l1, 3),
            'mean_l1_per_channel_0_255': round(mean_per_ch, 3),
            'rmse_per_channel': round(rmse, 3),
            'visible_changed_pixels_gt15': changed_px,
            'visible_changed_pct': round(100.0 * changed_px / n, 2),
        },
        'magenta_pixels': magenta,
        'fallback_errors': errors,
    }
    json.dump(metrics, open(os.path.join(HERE, '02_metrics.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
