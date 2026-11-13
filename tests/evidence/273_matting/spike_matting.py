# #307 spike matting：qwen-image-edit-max 指令式抠图验证（只读主仓，只新建产物）
import sys, os, io, json, time

t_start = time.time()
sys.path.insert(0, r'D:\Firefly-Canvas')
os.chdir(r'D:\Firefly-Canvas')
from PIL import Image

print('importing app.core ...', flush=True)
import app.core  # noqa: F401
import app.services._base as _svc
import app.services.providers as _P
from app.core import _bailian_local_edit_adapter
# _svc._main 的 fallback 只查 sys.modules、不会 import main（app/services/_base.py:82-92），
# 故手工绑定最小 fake main，指向真实配置文件（app/core.py:346-349、main.py:1098/1112）。
import types as _types
_fake = _types.ModuleType('fake_main')
_fake.API_PROVIDERS_FILE = r'D:\Firefly-Canvas\data\api_providers.json'
_fake.API_ENV_FILE = r'D:\Firefly-Canvas\API\.env'
_fake.DATA_DIR = r'D:\Firefly-Canvas\data'
_fake.load_api_providers = _P.load_api_providers
_fake.provider_env_key_value = _P.provider_env_key_value
_fake.runninghub_wallet_key_value = _P.runninghub_wallet_key_value
_fake.get_api_provider_exact = _P.get_api_provider_exact
_svc._bind_main(_fake)
key = _P.provider_env_key_value('bailian') or ''
print('import ok in %.1fs | bailian key resolved: len=%d prefix=%s' % (time.time() - t_start, len(key), key[:4]), flush=True)
if not key:
    raise SystemExit('bailian key 未解析到（API/.env / env），终止')

SRC = 'assets/input/ai_ref_10073401ad09.png'  # 897x845 人物头部特写，浅色背景
OUTDIR = 'tests/evidence/273_matting'
os.makedirs(OUTDIR, exist_ok=True)
with open(SRC, 'rb') as f:
    src_bytes = f.read()
with open(os.path.join(OUTDIR, 'input.png'), 'wb') as f:
    f.write(src_bytes)

def analyze(data):
    im = Image.open(io.BytesIO(data))
    info = {'mode': im.mode, 'size': list(im.size), 'format': im.format}
    rgba = im.convert('RGBA')
    a = rgba.getchannel('A')
    hist = a.histogram()
    total = rgba.size[0] * rgba.size[1]
    info['alpha_min'] = a.getextrema()[0]
    info['alpha_max'] = a.getextrema()[1]
    info['non255_ratio'] = round((total - hist[255]) / total, 6)
    info['fully_transparent_ratio'] = round(hist[0] / total, 6)
    info['semi_transparent_ratio'] = round(sum(hist[1:255]) / total, 6)
    info['real_alpha'] = info['non255_ratio'] > 0.0
    return im, info

result = {'source': SRC}

MODELS = ['qwen-image-2.0-pro', 'qwen-image-3.0-pro', 'qwen-image-2.1-pro']  # 端点 /v1/models 实测在册；注册模型 qwen-image-edit-max-2026-01-16 报 404 model_not_found

# ---- 实验 A：透明背景抠图 ----
PROMPT_A = '提取画面主体，去除背景，输出透明背景 PNG，保留主体边缘细节'
result['prompt_a'] = PROMPT_A
model_a = None
for m in MODELS:
    try:
        t0 = time.time()
        out_a = _bailian_local_edit_adapter({'image': src_bytes, 'prompt': PROMPT_A, 'size': '1024x1024', 'model': m})
        model_a = m
        result['a_model'] = m
        result['a_seconds'] = round(time.time() - t0, 1)
        with open(os.path.join(OUTDIR, 'outA.png'), 'wb') as f:
            f.write(out_a)
        im_a, info_a = analyze(out_a)
        im_a.convert('RGBA').save(os.path.join(OUTDIR, 'outA_rgba.png'))
        result['a'] = info_a
        break
    except Exception as e:  # noqa: BLE001
        result.setdefault('a_attempts', []).append({'model': m, 'error': repr(e)[:300]})
if 'a' not in result:
    with open(os.path.join(OUTDIR, 'metrics.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    raise SystemExit(1)

# ---- 实验 B（A 无真 alpha 才发）：纯绿底 ----
if not result['a']['real_alpha']:
    PROMPT_B = '去除背景，背景替换为纯绿色 #00FF00，主体不变'
    result['prompt_b'] = PROMPT_B
    t0 = time.time()
    out_b = _bailian_local_edit_adapter({'image': src_bytes, 'prompt': PROMPT_B, 'size': '1024x1024', 'model': model_a})
    result['b_seconds'] = round(time.time() - t0, 1)
    with open(os.path.join(OUTDIR, 'outB.png'), 'wb') as f:
        f.write(out_b)
    im_b, info_b = analyze(out_b)
    px = list(im_b.convert('RGB').getdata())
    green = sum(1 for p in px if p[1] > 200 and p[0] < 80 and p[2] < 80)
    info_b['pure_green_ratio'] = round(green / len(px), 4)
    result['b'] = info_b

with open(os.path.join(OUTDIR, 'metrics.json'), 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print(json.dumps(result, ensure_ascii=False, indent=1))
print('TOTAL %.1fs' % (time.time() - t_start), flush=True)
