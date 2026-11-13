"""本地 ComfyUI（MiniMax-H3-Image GGUF）工作流模板与尺寸映射。

#331：画布把本地 ComfyUI 当成一个生图服务商协议 `comfyui`：提交 API 格式的图、
轮询 /history、取 /view。工作流图以 Python 常量内置而不是外部 JSON 文件——
`app/data/` 整目录被 .gitignore 忽略（只有 model_specs.json 例外），放那儿会丢。

图结构取自实测可用的 9 节点模板（ComfyUI 0.45.2 + ComfyUI-GGUF）：
    1 UnetLoaderGGUF → 7 KSampler.model
    2 CLIPLoaderGGUF(type="minimax") → 4 MiniMaxH3ImageConditioning.clip
    3 VAELoader → 4 .vae 与 8 VAEDecode.vae
    4 → positive 给 5（ConditioningZeroOut 当 negative）与 7.positive；→ LATENT 给 7.latent_image
    7 → 8 VAEDecode → 9 SaveImage
节点 4 自己编码 latent，不需要 EmptyLatentImage；batch 恒为 1（模型硬性拒绝 batch>1）。
"""
import copy
import math
import os
import random

# 作者值：32B 蒸馏模型 6 步 / cfg 1.0 / euler+simple。别随手加到 20 步或抬 cfg。
COMFYUI_H3_DEFAULT_STEPS = 6
COMFYUI_H3_DEFAULT_CFG = 1.0
COMFYUI_H3_DEFAULT_SAMPLER = 'euler'
COMFYUI_H3_DEFAULT_SCHEDULER = 'simple'
COMFYUI_H3_FILENAME_PREFIX = 'MiniMax-H3-Image/canvas'

# 画布分辨率档 → 目标面积（MP）。1k 档就是模型地板 2048²（4 MP），再低会明显崩。
COMFYUI_MP_TIERS = {'1k': 4.0, '2k': 8.0, '4k': 16.0}
# 画布分辨率档的别名 → H3 面积档。画布不同入口传的写法不一样：
# `/api/image-params` 的 size 面板传小写 '1k'，model_specs 的枚举是大写 '1K'，
# 另外旧路径/保底路径还会传 'low'/'medium'/'high' 或空串。全吃下来，别让用户选了的档位白白落回默认。
COMFYUI_TIER_ALIASES = {
    '1k': '1k', '1': '1k', 'low': '1k', '低': '1k', '1024': '1k',
    '2k': '2k', '2': '2k', 'medium': '2k', '中': '2k', '2048': '2k',
    '4k': '4k', '4': '4k', 'high': '4k', '高': '4k', 'best': '4k', '4096': '4k',
}
# 图生图把参考图缩放到的那一档像素量；官方 edit 模板用 4 MP（= 1K 档）。
# 上限只是防手滑，不是模型限制：超过 16 MP 在 16GB 卡上会 OOM。
COMFYUI_EDIT_MAX_MEGAPIXELS = float(os.getenv('COMFYUI_EDIT_MAX_MEGAPIXELS', '16'))
COMFYUI_DEFAULT_MP_TIER = '2k'
# 边长步长 = 8：作者 ResolutionSelector 就是用 8 的倍数（节点 object_info 的 step 提示写的是 32，
# 但实测作者那套 8 倍数尺寸（如 5016×3344、2512×1672）能直接提交）。改成 32 就对不上公开尺寸表了。
COMFYUI_SIZE_STEP = 8
# 参考图上限：节点 images 允许到 100，但画布一次传 16 张已经超出模型的有效参考能力。
COMFYUI_MAX_REFERENCES = 16
# 画布传这些值（或不传）时视为“跟随参考图”
COMFYUI_AUTO_RATIO_VALUES = {'', 'auto', 'reference', 'keep', 'keep_ratio', 'follow', '跟随参考图'}

COMFYUI_H3_T2I_WORKFLOW = {
    '1': {
        'class_type': 'UnetLoaderGGUF',
        'inputs': {'unet_name': 'MiniMax-H3-Image-Pruned-Q4_K_M.gguf'},
        '_meta': {'title': 'MiniMax-H3-Image @unet'},
    },
    '2': {
        'class_type': 'CLIPLoaderGGUF',
        # type 只能是 minimax（32B 编码器唯一合法值），别改。
        'inputs': {'clip_name': 'qwen3vl_32b_minimax_h3-Q4_K_M.gguf', 'type': 'minimax'},
        '_meta': {'title': 'Qwen3-VL 32B text encoder'},
    },
    '3': {
        'class_type': 'VAELoader',
        'inputs': {'vae_name': 'minimax_h3_image_vae_fp16.safetensors'},
        '_meta': {'title': 'MiniMax-H3-Image VAE'},
    },
    '4': {
        'class_type': 'MiniMaxH3ImageConditioning',
        'inputs': {
            'clip': ['2', 0],
            'vae': ['3', 0],
            'prompt': '',
            'width': 2048,
            'height': 2048,
        },
        '_meta': {'title': 'Positive @prompt @size'},
    },
    '5': {
        'class_type': 'ConditioningZeroOut',
        'inputs': {'conditioning': ['4', 0]},
        '_meta': {'title': 'negative'},
    },
    '7': {
        'class_type': 'KSampler',
        'inputs': {
            'model': ['1', 0],
            'positive': ['4', 0],
            'negative': ['5', 0],
            'latent_image': ['4', 1],
            'seed': 0,
            'steps': COMFYUI_H3_DEFAULT_STEPS,
            'cfg': COMFYUI_H3_DEFAULT_CFG,
            'sampler_name': COMFYUI_H3_DEFAULT_SAMPLER,
            'scheduler': COMFYUI_H3_DEFAULT_SCHEDULER,
            'denoise': 1.0,
        },
        '_meta': {'title': 'sampler'},
    },
    '8': {
        'class_type': 'VAEDecode',
        'inputs': {'samples': ['7', 0], 'vae': ['3', 0]},
        '_meta': {'title': 'decode'},
    },
    '9': {
        'class_type': 'SaveImage',
        'inputs': {'images': ['8', 0], 'filename_prefix': COMFYUI_H3_FILENAME_PREFIX},
        '_meta': {'title': 'Save Image @save'},
    },
}


COMFYUI_ASPECT_ALIASES = {
    'square': (1.0, 1.0), 'portrait': (2.0, 3.0), 'landscape': (3.0, 2.0),
    'portrait43': (3.0, 4.0), 'landscape43': (4.0, 3.0), 'story': (9.0, 16.0),
    'wide': (16.0, 9.0), 'ultrawide': (21.0, 9.0), 'ultratall': (9.0, 21.0),
    'portrait45': (4.0, 5.0), 'landscape45': (5.0, 4.0),
}


def comfyui_parse_aspect(value):
    """解析 '3:2' / '3x2' / '3/2' / 'wide' 这类比例；非法返回 (0, 0)。

    画布节点上的比例 chip 是预设名（square/wide/story…），而 model_specs 里是 '16:9'；
    `aspectRatioForRun` 一般已经转过了，但扩图/自定义路径会直接把预设名传下来，所以两边都认。
    """
    text = str(value or '').strip().replace('：', ':')
    if not text:
        return (0.0, 0.0)
    alias = COMFYUI_ASPECT_ALIASES.get(text.lower())
    if alias:
        return alias
    for sep in (':', 'x', 'X', '*', '/'):
        if sep in text:
            left, _, right = text.partition(sep)
            try:
                a, b = float(left), float(right)
            except (TypeError, ValueError):
                return (0.0, 0.0)
            if a > 0 and b > 0:
                return (a, b)
            return (0.0, 0.0)
    return (0.0, 0.0)


def _comfyui_snap(value, step=COMFYUI_SIZE_STEP):
    step = max(1, int(step or 1))
    return max(step, int(round(float(value) / step)) * step)


def comfyui_resolution_tier(resolution='', size=''):
    """分辨率档 → H3 面积档（'1k'/'2k'/'4k'）。

    传了能认的档就用它；传空/auto/无法识别时，**从画布给的 size 面积反推**，而不是无条件回落到 2K：
    历史 bug —— 画布某些路径（旧节点、扩图、直调 API）不传 resolution，适配器直接按 2K=8MP 跑，
    用户选 1K 却得到 8MP/两分钟；反过来 4K 也会静默降成 8MP。
    """
    tier = COMFYUI_TIER_ALIASES.get(str(resolution or '').strip().lower())
    if tier:
        return tier
    # auto/空/不认得的写法：按 请求面积 反推最近的档（宁大勿小，H3 地板是 4MP）
    area_mp = 0.0
    try:
        width, height = str(size or '').strip().lower().replace('*', 'x').partition('x')[::2]
        area_mp = (float(width) * float(height)) / (1024 * 1024)
    except (TypeError, ValueError):
        area_mp = 0.0
    if area_mp <= 0:
        return COMFYUI_DEFAULT_MP_TIER
    if area_mp <= 4.5:
        return '1k'
    if area_mp <= 8.5:
        return '2k'
    return '4k'


def comfyui_h3_resolution(aspect_ratio='', resolution='', size=''):
    """把画布的比例 + 面积档换算成 ComfyUI 要的 width/height（8 的倍数）。

    算法与作者 ResolutionSelector 一致：目标面积 = MP × 1024 × 1024，
    先用比例反解宽、再取高，两边各自对齐 8。
    实测对照（16 MP 档）：1:1→4096²、3:2→5016×3344、4:3→4728×3544、
    16:9→5464×3072、21:9→6256×2680；8 MP 档 1:1→2896²；4 MP 档 1:1→2048²。
    """
    megapixels = COMFYUI_MP_TIERS[comfyui_resolution_tier(resolution, size)]
    ratio_a, ratio_b = comfyui_parse_aspect(aspect_ratio)
    if not ratio_a or not ratio_b:
        # 没有比例时退回画布给的像素尺寸（换算成同面积档，避免用户看到 512² 直接崩）
        ratio_a, ratio_b = _comfyui_ratio_from_size(size)
    area = megapixels * 1024 * 1024
    width = _comfyui_snap(math.sqrt(area * ratio_a / ratio_b))
    height = _comfyui_snap(width * ratio_b / ratio_a)
    return (width, height)


COMFYUI_EDIT_SIZE_STEP = 32


def comfyui_edit_megapixels(resolution='', size=''):
    """图生图的目标像素量（对应官方工作流的 `scale total pixels`）。

    官方 edit 模板默认 4 MP（节点 56），与画布 1K 档一致；画布选 2K/4K 就抬到 8/16 MP。
    官方 note 原话：「MiniMax H3 Image doesn't work well in low resolution, so this workflow
    upscale the input image to 4MP at first.」——图生图不能拿小图直接跑。
    """
    return min(float(COMFYUI_MP_TIERS[comfyui_resolution_tier(resolution, size)]), float(COMFYUI_EDIT_MAX_MEGAPIXELS))


def comfyui_align_size(width, height, step=COMFYUI_EDIT_SIZE_STEP):
    """把宽高各自对齐到 step 的倍数（官方模板 node 48：`scale to multiple`, 32）。

    ComfyUI 内部的 latent 对齐是 16，官方模板多对齐到 32 是为了让 VAE 每边张量能被
    更多并行维度整除；两边都对齐 32 则内部那层对齐一定不会反向改变尺寸。
    """
    step = max(1, int(step or 1))
    return (_comfyui_snap(width, step), _comfyui_snap(height, step))


def comfyui_edit_target_size(width, height, resolution='', step=COMFYUI_EDIT_SIZE_STEP, size=''):
    """参考图 → 工作流尺寸：先 scale total pixels 到面积档，再对齐 step。

    返回值就是节点 4 的 width/height，也就是输出图的实际尺寸。
    图生图的尺寸跟着参考图走（官方工作流用 GetImageSize 回填），不跟画布比例走。
    """
    try:
        source_width = float(width or 0)
        source_height = float(height or 0)
    except (TypeError, ValueError):
        source_width, source_height = 0.0, 0.0
    if source_width <= 0 or source_height <= 0:
        return comfyui_h3_resolution('', resolution, size)
    area = comfyui_edit_megapixels(resolution, size) * 1024 * 1024
    ratio = source_width / source_height
    target_height = math.sqrt(area / ratio)
    target_width = target_height * ratio
    return comfyui_align_size(round(target_width), round(target_height), step)


def _comfyui_ratio_from_size(size):
    match = str(size or '').strip().lower().replace('*', 'x')
    left, _, right = match.partition('x')
    try:
        width, height = float(left), float(right)
    except (TypeError, ValueError):
        return (1.0, 1.0)
    if width <= 0 or height <= 0:
        return (1.0, 1.0)
    return (width, height)


def comfyui_ratio_is_auto(value) -> bool:
    """画布没选比例（或显式要“跟随参考图”）时返回 True。
    确认过的事实：本地节点不要求参考图与画布同比例——方形参考图 + 16:9 画布实测 63秒出 2720×1536，
    主体（毯子）完整保留、无拉伸。所以“参考图就锁死输出比例”不是模型限制，只是官方 edit 模板
    用 GetImageSize 回填 width/height 的便利写法。适配器默认听画布的。"""
    return str(value or '').strip().lower() in COMFYUI_AUTO_RATIO_VALUES


def comfyui_h3_seed(seed=None):
    try:
        value = int(seed)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = random.randint(1, 2 ** 31 - 1)
    return int(value) % (2 ** 31)


def comfyui_h3_workflow(
    prompt,
    width,
    height,
    seed=None,
    steps=COMFYUI_H3_DEFAULT_STEPS,
    cfg=COMFYUI_H3_DEFAULT_CFG,
    sampler_name=COMFYUI_H3_DEFAULT_SAMPLER,
    scheduler=COMFYUI_H3_DEFAULT_SCHEDULER,
    filename_prefix=COMFYUI_H3_FILENAME_PREFIX,
    reference_names=None,
):
    """在 9 节点模板上注入参数；给了参考图就挂 LoadImage 并接节点 4 的 images。

    参考图必须写成**扁平键** `images.image_0` / `images.image_1` ……：
    - 嵌套写法 `{"images": {"image_1": [...]}}` 会被后端**静默丢弃**（实测：节点收到空 images，
      宽度高度都退回 2048，等于默默变成纯文生图，不报任何错）。
    - 序号从 0 开始，和前端 autogrow 一致（`comfyui_frontend` 的
      `autogrowOrdinalToName` → `${group}.${prefix}${ordinal}`，ordinal 从 0 数）。
      所以 `<Picture 1>` 对应 `images.image_0`。
    LoadImage 的 image 用 /upload/image 返回的 name。
    """
    graph = copy.deepcopy(COMFYUI_H3_T2I_WORKFLOW)
    graph['4']['inputs'].update(
        {
            'prompt': str(prompt or ''),
            'width': _comfyui_snap(width),
            'height': _comfyui_snap(height),
        }
    )
    graph['7']['inputs'].update(
        {
            'seed': comfyui_h3_seed(seed),
            'steps': int(steps or COMFYUI_H3_DEFAULT_STEPS),
            'cfg': float(cfg if cfg is not None else COMFYUI_H3_DEFAULT_CFG),
            'sampler_name': str(sampler_name or COMFYUI_H3_DEFAULT_SAMPLER),
            'scheduler': str(scheduler or COMFYUI_H3_DEFAULT_SCHEDULER),
        }
    )
    if filename_prefix:
        graph['9']['inputs']['filename_prefix'] = str(filename_prefix)
    names = [str(name or '').strip() for name in (reference_names or []) if str(name or '').strip()]
    if names:
        for index, name in enumerate(names[:COMFYUI_MAX_REFERENCES]):
            node_id = f'ref{index}'
            graph[node_id] = {
                'class_type': 'LoadImage',
                'inputs': {'image': name},
                '_meta': {'title': f'reference {index + 1}'},
            }
            graph['4']['inputs'][f'images.image_{index}'] = [node_id, 0]
    return graph
