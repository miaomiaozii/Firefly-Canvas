"""#500：序列化请求体预算与发送副本压缩阶梯（纯策略模块，不含网络 I/O）。

票面裁定（#500，取代 #493 阶梯语义）：
- 请求体放得下就发原图，一个字节都不改；没有对所有服务商写死的 4096 预缩放。
- 放不下：保持原尺寸 JPEG q95→92→90→85→80；仍放不下按长边
  6144→5120→4096→3584→3072→2560→2048→1536、每档质量 80。最低质量 80。
- 全程 4:4:4（subsampling=0）、保留 ICC（EXIF 方向一并保留，防止重编码后显示翻转）。
- 透明图走 WebP（同样质量阶梯）；不透明照片不走无损 WebP——实测比原 JPEG 还大且每档多花十几秒。
- 多图水位分配：总预算平均分给每张；小于自己份额的保持原样，余量重分。
- 全压到底仍超限：不发请求、不扣费，提示「参考图太多，请减少到 N 张」，N 按实际算出。
- 压缩只作用于发送副本，每档从同一基准像素重新编码；原文件零写入；不放大。
- 已知硬限只登记 review 实测过的两个 Soon 端点；其他厂商上限未知就不猜测。
"""
from __future__ import annotations

import base64
import io
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx
from fastapi import HTTPException
from PIL import Image

# review 实测（D:/ff-qa/soon-limit/）：~16,599,7xx 返回 400、16,899,7xx 返回 413；
# 本地安全目标取 16,500,000 B，与 #493 付费样本（8,007,313 B 成功）一致保守。
SOON_BODY_HARD_BYTES = 16_777_216
SOON_BODY_LOCAL_BYTES = 16_500_000

_SOON_HOST = 'api.soonstudio.ai'
_SOON_NATIVE_RE = re.compile(r'^/v1beta/models/[^/]+:generateContent$')


@dataclass(frozen=True)
class KnownBodyLimit:
    hard_bytes: int
    local_bytes: int


def lookup_known_body_limit(resolved_url: str) -> Optional[KnownBodyLimit]:
    # 精确 origin+端点身份匹配；端口非默认 443 不算实测过的那两个。
    try:
        parsed = httpx.URL(resolved_url)
    except httpx.InvalidURL:
        # httpx 0.28.1 对畸形串只抛 InvalidURL（端口越界/不可打印字符，已实证）；
        # 其余畸形串解析出空 scheme/host，由下面的身份匹配自然滤掉。
        return None
    if parsed.scheme != 'https' or parsed.host != _SOON_HOST:
        return None
    if parsed.port not in (None, 443):
        return None
    if parsed.path == '/v1/images/generations' or _SOON_NATIVE_RE.match(parsed.path):
        return KnownBodyLimit(SOON_BODY_HARD_BYTES, SOON_BODY_LOCAL_BYTES)
    return None


_MIME_BY_FORMAT = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}

# #500 票面阶梯。质量档保持原尺寸；尺寸档每档固定 q80；最低质量 80。
_QUALITY_STEPS = (95, 92, 90, 85, 80)
_RESIZE_CEILINGS = (6144, 5120, 4096, 3584, 3072, 2560, 2048, 1536)
_FLOOR_EDGE = 1536
_FLOOR_QUALITY = 80
# 序列化贡献与图像字节的换算余量：data:URL 头 + base64 膨胀在这里扣，宁可保守。
_URL_OVERHEAD_BYTES = 64


def _fit_edge(img: Image.Image, ceiling: int) -> Image.Image:
    edge = max(img.width, img.height)
    if edge <= ceiling or edge == 0:
        return img  # 绝不放大
    scale = ceiling / edge
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def encode_stage(img: Image.Image, stage: Tuple, *, icc_profile: Optional[bytes] = None, exif: Any = None) -> bytes:
    fmt = stage[0]
    quality = stage[-1]
    ceiling = stage[1] if len(stage) == 3 else None
    work = _fit_edge(img, ceiling) if ceiling else img
    # Pillow 12.2 的 JPEG 保存对字面量 exif=None 会 TypeError（info.get('exif', b'') 拿到 None）；只传非空元数据。
    meta = {}
    if icc_profile:
        meta['icc_profile'] = icc_profile
    if exif:
        meta['exif'] = exif
    buf = io.BytesIO()
    if fmt == 'JPEG':
        # 票面固定 4:4:4；ICC/EXIF 随档携带。不透明照片不走无损 WebP（票面明令跳过）。
        work.convert('RGB').save(buf, 'JPEG', quality=int(quality), subsampling=0, **meta)
    else:
        # 透明图 WebP 走与 JPEG 相同的质量阶梯（lossy），保住 alpha。
        work.save(buf, 'WEBP', quality=int(quality), method=4, **meta)
    return buf.getvalue()


class PreparedReference:
    """单张参考图的发送副本状态；基准 = 原图字节（#500：无 4096 预缩放）。

    远程 URL 引用用 passthrough_value 登记：字符串体积诚实计入请求体，
    但没有可压副本，阶梯上永不推进（不抓取、不改写）。"""

    def __init__(self, index: int, raw: Optional[bytes] = None, passthrough_value: Optional[str] = None, mime_hint: Optional[str] = None):
        self.index = index
        if passthrough_value is not None:
            self.passthrough = True
            self.raw = b''
            self.source_format = 'URL'
            self.from_width = self.from_height = 0
            self.has_alpha = False
            self._baseline = None
            self._stages = []
            self.stage = -1
            self.current_bytes = len(passthrough_value.encode('utf-8'))
            self.current_mime = None
            self.current_format = 'URL'
            self.quality = None
            self.lossless = False
            self.width = self.height = 0
            self._raw_current = b''
            self._data_url = passthrough_value
            self._frozen = False
            return
        self.passthrough = False
        self.raw = raw  # 调用方传入的原图字节基线（不写回磁盘；阶梯只改发送副本）
        img = Image.open(io.BytesIO(raw))
        if getattr(img, 'n_frames', 1) > 1:
            raise ValueError('animated reference not supported by budget ladder')  # 诚实拒绝，不做静默取首帧
        img.load()
        self.source_format = img.format or 'PNG'
        self.from_width, self.from_height = img.width, img.height
        self.has_alpha = (img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info)
        self._baseline = img
        self._icc = img.info.get('icc_profile')
        # PIL save(exif=) 对裸 JPEG EXIF 字节会按缩略图解析（no SOF marker）；必须经 Image.Exif 对象重序列化。
        _exif_raw = img.info.get('exif')
        self._exif = None
        if isinstance(_exif_raw, bytes):
            try:
                _exif_obj = Image.Exif()
                _exif_stream = _exif_raw
                if _exif_stream.startswith(b'Exif\x00\x00'):
                    # JPEG info['exif'] 带 Exif 前缀；Exif.load 期望裸 TIFF 头（Pillow 12 已无 read()）。
                    _exif_stream = _exif_stream[6:]
                _exif_obj.load(_exif_stream)
                self._exif = _exif_obj
            except (SyntaxError, struct.error):
                # Pillow 12.2 的 Exif.load → ImageFileDirectory_v2.__init__：头不是 TIFF 魔数抛
                # SyntaxError；头只有 4–7 字节（魔数合法但偏移截断）抛 struct.error；load 主体
                # 只兜 OSError、短读走 Corrupt EXIF 警告——只有这两个会逃逸（对 Pillow 12.2.0 实证）。
                # 损坏的 EXIF 元数据按无 EXIF 处理，不阻断发图。
                self._exif = None
        elif _exif_raw is not None:
            self._exif = _exif_raw
        self._stages = self._build_stages()
        self.stage = -1
        self.current_bytes = len(raw)
        self.current_mime = mime_hint or _MIME_BY_FORMAT.get(self.source_format, 'image/png')
        self.current_format = self.source_format
        self.quality: Optional[int] = None
        self.lossless = False
        self.width, self.height = img.width, img.height
        self._raw_current = raw
        self._data_url: Optional[str] = None
        self._frozen = False

    @property
    def passthrough_value(self) -> str:
        # 公开只读接缝：远程 URL 透传值（仅 passthrough 引用合法）。
        assert self.passthrough
        return self._data_url

    def _build_stages(self) -> List[Tuple]:
        if self.passthrough:
            return []
        # 票面单张规则：透明 → WebP；不透明 → JPEG（含不透明 PNG 源，重编码即 JPEG）。
        fmt = 'WEBP' if self.has_alpha else 'JPEG'
        edge = max(self.from_width, self.from_height)
        stages: List[Tuple] = [(fmt, q) for q in _QUALITY_STEPS]
        stages.extend((fmt, ceiling, _FLOOR_QUALITY) for ceiling in _RESIZE_CEILINGS if ceiling < edge)
        return stages

    def stages(self) -> List[Tuple]:
        return list(self._stages)

    def has_next_stage(self) -> bool:
        return self.stage < len(self._stages) - 1

    def baseline_image(self) -> Image.Image:
        return self._baseline

    def advance(self) -> None:
        # 每一档都从基准像素重新生成，杜绝有损叠加。
        stage = self._stages[self.stage + 1]
        encoded = encode_stage(self._baseline, stage, icc_profile=self._icc, exif=self._exif)
        self.stage += 1
        self.current_bytes = len(encoded)
        self.current_format = stage[0]
        self.current_mime = 'image/webp' if stage[0] == 'WEBP' else 'image/jpeg'
        self.lossless = False
        self.quality = int(stage[-1])
        # 输出尺寸用算术推导，不重解码（省 CPU，且兼容只测体积的下游/夹具）。
        if len(stage) == 3:
            scaled = _fit_edge(self._baseline, stage[1])
            self.width, self.height = scaled.width, scaled.height
        else:
            self.width, self.height = self._baseline.width, self._baseline.height
        self._raw_current = encoded
        self._data_url = None

    @property
    def changed(self) -> bool:
        return self.stage >= 0

    def current_raw(self) -> bytes:
        return getattr(self, '_raw_current', self.raw)

    def data_url(self) -> str:
        if self._data_url is None:
            self._data_url = f'data:{self.current_mime};base64,{base64.b64encode(self.current_raw()).decode("ascii")}'
        return self._data_url

    def serialized_contribution(self) -> int:
        # 该引用在序列化请求体里占的字节（passthrough 用字符串本体）。
        if self.passthrough:
            return self.current_bytes
        return len(self.data_url())

    def floor_projection(self) -> Dict[str, Any]:
        # 不改动当前状态地预估压到底线的贡献：水位零请求时算 N 用。
        if self.passthrough or not self._stages:
            return {'contribution': self.serialized_contribution(), 'movable': False}
        last = self._stages[-1]
        encoded = encode_stage(self._baseline, last, icc_profile=self._icc, exif=self._exif)
        return {'contribution': len(encoded) * 4 // 3 + _URL_OVERHEAD_BYTES, 'movable': True}


def reference_projection(ref: PreparedReference) -> Dict[str, Any]:
    # 记录/UI 只允许标量安全投影：不含 URL、路径、prompt、凭据或 base64。
    return {
        'index': ref.index,
        'source_format': ref.source_format,
        'final_format': ref.current_format,
        'quality': ref.quality,
        'lossless': ref.lossless,
        'from_width': ref.from_width,
        'from_height': ref.from_height,
        'width': ref.width,
        'height': ref.height,
        'byte_count': ref.current_bytes,
        'stage': ref.stage,
        'changed': ref.changed,
    }


@dataclass
class BudgetOutcome:
    request: Any
    body_bytes: int
    changed: bool
    steps: List[Dict[str, Any]] = field(default_factory=list)


def _suggested_max_references(references: Sequence[PreparedReference], body: int, local_bytes: int) -> int:
    # N：底线体积从小到大贪心能装下的最多张数（按实际情况算，不拍脑袋）。
    overhead = body - sum(r.serialized_contribution() for r in references)
    floors = sorted(r.floor_projection()['contribution'] for r in references)
    total = overhead
    count = 0
    for size in floors:
        if total + size > local_bytes:
            break
        total += size
        count += 1
    return count


def _floor_error(references: Sequence[PreparedReference], build_request: Callable, local_bytes: int) -> HTTPException:
    body = len(build_request(list(references)).content)
    rows = [reference_projection(r) for r in references]
    excess = body - local_bytes
    n = _suggested_max_references(references, body, local_bytes)
    if not references:
        message = (f'请求体 {body} B 超过本地目标 {local_bytes} B，但没有参考图可压缩：'
                   f'超出部分来自 prompt/配置/JSON 等非图片开销（超出 {excess} B）。')
        n = 0
    else:
        message = (f'参考图太多：{len(references)} 张全部压到底线（长边 1536、质量 80）后请求体仍为 {body} B，'
                   f'超过本地目标 {local_bytes} B（超出 {excess} B），请减少到 {n} 张。')
    return HTTPException(status_code=413, detail={
        'code': 'request_body_floor_exceeded',
        'message': message,
        'body_bytes': body,
        'limit_bytes': local_bytes,
        'excess_bytes': excess,
        'reference_count': len(references),
        'suggested_max_references': int(n),
        'references': rows,
    })


def prepare_budgeted_request(references: Sequence[PreparedReference], *,
                             build_request: Callable[[Sequence["PreparedReference"]], Any],
                             local_bytes: int) -> BudgetOutcome:
    """#500 水位分配：基准先按实际序列化判定；超限则总预算（扣除 prompt/JSON 等非图开销）
    平均分给未冻结的图——小于自己份额的冻结原样（冻结集合反复收敛直到不变），
    其余大图在各自份额内逐档下压：每轮只压一档并重新序列化，份额每轮按「本地目标 −
    非图开销 − 已冻结占用」重算，省下的预算实时重分，整体首次放得下立即停；
    全部冻结仍超限则零请求（raise）。

    build_request 接收当前状态的参考列表，必须返回带 .content（真实序列化字节）的请求对象。

    每轮三选一（冻结/压一档/底线冻结）保证轮数有界：压档总数有限、冻结总数有限，
    之后必在循环头命中 body ≤ local 或 movable 为空。"""
    def serialize():
        return build_request(list(references))

    steps: List[Dict[str, Any]] = []
    request = serialize()
    body = len(request.content)
    if body <= local_bytes:
        return BudgetOutcome(request=request, body_bytes=body, changed=False, steps=steps)
    # 非图开销（prompt/JSON 骨架）全程恒定：按首次序列化算一次，从图预算里扣除。
    overhead = body - sum(r.serialized_contribution() for r in references)
    for ref in references:
        ref._frozen = False
    while True:
        request = serialize()
        body = len(request.content)
        if body <= local_bytes:
            return BudgetOutcome(request=request, body_bytes=body, changed=any(r.changed for r in references), steps=steps)
        movable = [r for r in references if not r._frozen]
        if not movable:
            raise _floor_error(references, build_request, local_bytes)
        # 水位 = 冻结图占用之外的剩余额度：小图冻掉的部分自动回流给未冻结图。
        frozen_total = sum(r.serialized_contribution() for r in references if r._frozen)
        share = max(0, (local_bytes - overhead - frozen_total) // len(movable))
        for ref in movable:
            if ref.serialized_contribution() <= share:
                ref._frozen = True  # 小于自己那份：保持原样，余量下一轮重分
            elif ref.has_next_stage():
                # 每轮只压一档：下一轮重算份额并整体复测，首次放得下即停，
                # 不用旧的（含小图的）share 把大图压过头。
                ref.advance()
                row = reference_projection(ref)
                row['share_bytes'] = share
                steps.append(row)
            else:
                ref._frozen = True  # 压到底线仍超份额：留最小体积，冻结


def temporary_413_target(rejected_body_bytes: int) -> int:
    # 未知上限裁定（#493 MESSAGES.md:3942 C，#500 未改此语义）：临时目标 = floor(被拒正文 × 85/100)，仅限本次逻辑请求，不写回登记。
    return (int(rejected_body_bytes) * 85) // 100


def advance_until_below(ref: PreparedReference, *, build_single: Callable[[Sequence["PreparedReference"]], Any], target_bytes: int) -> Optional[Dict[str, Any]]:
    while ref.has_next_stage():
        ref.advance()
        body = len(build_single([ref]).content)
        if body < target_bytes:
            return {'body_bytes': body, 'stage': ref.stage}
    return None


_FORMAT_LABELS = {'WEBP': 'WebP', 'JPEG': 'JPEG', 'PNG': 'PNG'}


def compression_label(projection: Dict[str, Any]) -> str:
    # 节点/历史标注「已为本次请求压缩：PNG→JPEG q90，4096→3072」；未压缩为空。
    if not projection.get('changed'):
        return ''
    source = _FORMAT_LABELS.get(str(projection.get('source_format') or ''), str(projection.get('source_format') or ''))
    final = _FORMAT_LABELS.get(str(projection.get('final_format') or ''), str(projection.get('final_format') or ''))
    label = f"{source}→{final} 无损" if projection.get('lossless') else f"{source}→{final} q{projection.get('quality')}"
    from_edge = max(int(projection.get('from_width') or 0), int(projection.get('from_height') or 0))
    to_edge = max(int(projection.get('width') or 0), int(projection.get('height') or 0))
    if to_edge and to_edge != from_edge:
        label += f"，{from_edge}→{to_edge}"
    return label


def report_note(report: Optional[List[Dict[str, Any]]]) -> str:
    labels = [compression_label(item) for item in (report or []) if compression_label(item)]
    return f"已为本次请求压缩：{'；'.join(labels)}" if labels else ''
