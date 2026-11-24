"""#493：序列化请求体预算与发送副本压缩阶梯（纯策略模块，不含网络 I/O）。

决策注释：
- 已知硬限只登记 review 实测过的两个 Soon 端点（MESSAGES.md:3901–3921）；
  其他厂商/端点上限未知就是未知，不做猜测、不写回登记。
- 判定必须基于 HTTPX 实际序列化后的请求体（build_request 的 content），
  不是 json.dumps 估算、不是原始图片字节求和。
- 压缩只作用于发送副本：每一档都从同一基准像素重新编码，禁止有损结果反复叠解码。
- alpha 全程走 WebP 保留透明；任何档都不放大。
"""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx
from fastapi import HTTPException
from PIL import Image

# review 实测：soon 两端点整个序列化请求体 16,777,216 B；本地安全目标留 277,216 B 余量。
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
    except Exception:
        return None
    if parsed.scheme != 'https' or parsed.host != _SOON_HOST:
        return None
    if parsed.port not in (None, 443):
        return None
    if parsed.path == '/v1/images/generations' or _SOON_NATIVE_RE.match(parsed.path):
        return KnownBodyLimit(SOON_BODY_HARD_BYTES, SOON_BODY_LOCAL_BYTES)
    return None


_MIME_BY_FORMAT = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}

# 阶梯：无损 WebP →（不透明再 JPEG q95）→ 同格式 q92/90/85/80 → 缩小长边各档 q85 → 底线 1536@q80。
_QUALITY_STEPS = (92, 90, 85, 80)
_RESIZE_CEILINGS = (3584, 3072, 2560, 2048, 1536)
_FLOOR_EDGE = 1536


def _fit_edge(img: Image.Image, ceiling: int) -> Image.Image:
    edge = max(img.width, img.height)
    if edge <= ceiling or edge == 0:
        return img  # 绝不放大
    scale = ceiling / edge
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)


def encode_stage(img: Image.Image, stage: Tuple) -> bytes:
    fmt = stage[0]
    if len(stage) == 2:
        ceiling, quality = None, stage[1]
    else:
        ceiling, quality = stage[1], stage[2]
    work = _fit_edge(img, ceiling) if ceiling else img
    buf = io.BytesIO()
    if fmt == 'JPEG':
        work.convert('RGB').save(buf, 'JPEG', quality=int(quality))
    else:
        lossless = quality == 'lossless'
        work.save(buf, 'WEBP', quality=100 if lossless else int(quality), lossless=lossless)
    return buf.getvalue()


class PreparedReference:
    """单张参考图的发送副本状态；基准 = 既有 4096 策略产物（media.generation_reference_bytes）。

    远程 URL 引用用 passthrough_value 登记：它的字符串体积诚实计入请求体，
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
            return
        self.passthrough = False
        img = Image.open(io.BytesIO(raw))
        if getattr(img, 'n_frames', 1) > 1:
            raise ValueError('animated reference not supported by budget ladder')  # 诚实拒绝，不做静默取首帧
        img.load()
        self.index = index
        self.raw = raw
        self.source_format = img.format or 'PNG'
        self.from_width, self.from_height = img.width, img.height
        self.has_alpha = (img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info)
        self._baseline = img
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

    def _build_stages(self) -> List[Tuple]:
        if self.passthrough:
            return []
        fmt = 'WEBP' if self.has_alpha else 'JPEG'
        edge = max(self.from_width, self.from_height)
        stages: List[Tuple] = [('WEBP', 'lossless')]
        if not self.has_alpha:
            stages.append(('JPEG', 95))
        stages.extend((fmt, q) for q in _QUALITY_STEPS)
        stages.extend((fmt, ceiling, 85) for ceiling in _RESIZE_CEILINGS if ceiling < edge)
        if edge > _FLOOR_EDGE:
            stages.append((fmt, _FLOOR_EDGE, 80))
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
        encoded = encode_stage(self._baseline, stage)
        self.stage += 1
        self.current_bytes = len(encoded)
        self.current_format = stage[0]
        self.current_mime = 'image/webp' if stage[0] == 'WEBP' else 'image/jpeg'
        self.lossless = stage[1] == 'lossless'
        self.quality = None if self.lossless else int(stage[-1])
        img = Image.open(io.BytesIO(encoded))
        self.width, self.height = img.width, img.height
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
    steps: List[Dict[str, Any]]


def _floor_error(references: Sequence[PreparedReference], build_request: Callable, local_bytes: int) -> HTTPException:
    body = len(build_request(list(references)).content)
    rows = [reference_projection(r) for r in references]
    excess = body - local_bytes
    if not references:
        message = (f'请求体 {body} B 超过本地目标 {local_bytes} B，但没有参考图可压缩：'
                   f'超出部分来自 prompt/配置/JSON 等非图片开销（超出 {excess} B）。')
    else:
        message = (f'{len(references)} 张参考图全部压到编码底线后请求体仍为 {body} B，超过本地目标 {local_bytes} B（超出 {excess} B）。'
                   f'建议减少参考图数量或缩短提示词。')
    return HTTPException(status_code=413, detail={
        'code': 'request_body_floor_exceeded',
        'message': message,
        'body_bytes': body,
        'limit_bytes': local_bytes,
        'excess_bytes': excess,
        'reference_count': len(references),
        'references': rows,
    })


def prepare_budgeted_request(references: Sequence[PreparedReference], *,
                             build_request: Callable[[Sequence["PreparedReference"]], Any],
                             local_bytes: int) -> BudgetOutcome:
    """基准先按实际序列化判定；超限则每轮只推进当前编码体积最大的一张（并列取小 index），每轮重新序列化，首个达标立即停止。

    build_request 接收当前状态的参考列表，必须返回带 .content（真实序列化字节）的请求对象——通常是 client.build_request。"""
    def serialize():
        return build_request(list(references))

    steps: List[Dict[str, Any]] = []
    request = serialize()
    body = len(request.content)
    if body <= local_bytes:
        return BudgetOutcome(request=request, body_bytes=body, changed=False, steps=[])
    while True:
        eligible = [r for r in references if r.has_next_stage()]
        if not eligible:
            raise _floor_error(references, build_request, local_bytes)
        target = max(eligible, key=lambda r: (r.current_bytes, -r.index))
        target.advance()
        request = serialize()
        body = len(request.content)
        # steps 记录推进轨迹：供 413 政策与排错复用，不触碰原图。
        row = reference_projection(target)
        row['body_bytes'] = body
        steps.append(row)
        if body <= local_bytes:
            return BudgetOutcome(request=request, body_bytes=body, changed=True, steps=steps)


def temporary_413_target(rejected_body_bytes: int) -> int:
    # 未知上限裁定（MESSAGES.md:3942 C）：临时目标 = floor(被拒正文 × 85/100)，仅限本次逻辑请求，不写回登记。
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
    # #493 裁定：节点/历史标注「已为本次请求压缩：PNG→WebP q90，4096→3072」；未压缩为空。
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
