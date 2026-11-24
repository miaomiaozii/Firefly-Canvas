"""#500：请求体总预算阶梯（原图优先、质量 95→80、多图水位分配、到底零请求）。

票面验收：
- 原图放得下逐字节不变；单张按阶梯逐档，质量不低于 80；
- 多图水位分配（小图原样、余量给大图）；压到底仍超限零请求并给出 N；
- ICC 与 4:4:4 保留；透明图走 WebP；参考图顺序不变。
"""
import io
import random
import struct

import pytest
from fastapi import HTTPException
from PIL import Image

from app.services import image_request_budget as budget


def noise_jpeg(w, h, quality=92, seed=7, icc=b'fixture-icc', exif=None):
    img = Image.frombytes('RGB', (w, h), random.Random(seed).randbytes(w * h * 3))
    info = {}
    if icc is not None:
        info['icc_profile'] = icc
    if exif is not None:
        info['exif'] = exif
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=quality, **info)
    return buf.getvalue()


def noise_png(w, h, mode='RGBA', seed=11):
    data = random.Random(seed).randbytes(w * h * (4 if mode == 'RGBA' else 3))
    img = Image.frombytes(mode, (w, h), data)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def sof_sampling(buf: bytes):
    # 手动解析 SOF0/SOF1/SOF2 分量采样因子：4:4:4 即每分量 H=V=1。
    i = 2
    while i < len(buf) - 1:
        if buf[i] != 0xFF:
            i += 1
            continue
        marker = buf[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            n = struct.unpack('>H', buf[i + 4:i + 6])[0]
            comp = buf[i + 9]
            factors = [buf[i + 10 + k * 3 + 1] for k in range(comp)]
            return {f >> 4 for f in factors} == {1} and {f & 0xF for f in factors} == {1}
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
        else:
            # 定长段：跳过 FF xx 两字节 + (len-2) 载荷 = i + 2 + len（夹具原式多跳 2 字节会落在下一段内部）
            i += 2 + struct.unpack('>H', buf[i + 2:i + 4])[0]
    raise AssertionError('no SOF marker found')


def make_ref(raw, mime_hint=None):
    return budget.PreparedReference(0, raw=raw, mime_hint=mime_hint)


class TestSingleRules:
    def test_fits_original_byte_identical(self):
        raw = noise_jpeg(640, 480)
        refs = [make_ref(raw)]
        outcome = budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=10 ** 9)
        assert not outcome.changed
        assert refs[0].current_raw() == raw

    def test_ladder_quality_first_keeps_dimensions(self):
        # 原图超限：先保持原尺寸走 q95→…；能在某档质量放下就不缩尺寸。
        raw = noise_jpeg(2200, 2200, quality=98, seed=5)
        refs = [make_ref(raw)]
        probe = len(FakeRequest(refs).content)
        target = probe * 9 // 10  # 略小于原图序列化：质量档即可达标
        outcome = budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=target)
        assert outcome.changed and refs[0].changed
        assert (refs[0].width, refs[0].height) == (2200, 2200), 'quality stages keep original size'
        assert refs[0].current_format == 'JPEG' and 80 <= refs[0].quality <= 95

    def test_stage_list_matches_ticket_ladder(self):
        refs = [make_ref(noise_jpeg(9000, 100))]  # 长边 9000 的超宽图
        stages = refs[0].stages()
        qualities = [s[1] for s in stages if len(s) == 2]
        assert qualities == [95, 92, 90, 85, 80], 'opaque: JPEG 95..80, no lossless WebP'
        resized = [(s[1], s[2]) for s in stages if len(s) == 3]
        assert resized == [(6144, 80), (5120, 80), (4096, 80), (3584, 80), (3072, 80), (2560, 80), (2048, 80), (1536, 80)]
        assert all((s[-1] if isinstance(s[-1], int) else 100) >= 80 for s in stages)

    def test_alpha_uses_webp_lossy_ladder_not_lossless(self):
        ref = make_ref(noise_png(9000, 100, 'RGBA'))
        stages = ref.stages()
        assert all(s[0] == 'WEBP' for s in stages)
        assert [s[1] for s in stages if len(s) == 2] == [95, 92, 90, 85, 80]
        assert not any(s[1] == 'lossless' for s in stages)

    def test_quality_floor_never_below_80_and_final_edge_1536(self):
        raw = noise_jpeg(4000, 3000, quality=99, seed=13)
        refs = [make_ref(raw)]
        outcome = None
        for stage in list(refs[0].stages()):
            refs[0].advance()
            assert refs[0].quality >= 80
        assert refs[0].stage == len(refs[0].stages()) - 1
        assert max(refs[0].width, refs[0].height) == 1536 or max(refs[0].from_width, refs[0].from_height) <= 1536

    def test_icc_exif_and_444_subsampling_survive_stage(self):
        exif = Image.Exif(); exif[274] = 6
        raw = noise_jpeg(2600, 200, exif=exif)
        ref = make_ref(raw)
        ref.advance()  # q95
        out = ref.current_raw()
        with Image.open(io.BytesIO(out)) as img:
            assert img.info.get('icc_profile') == b'fixture-icc'
            assert img.getexif().get(274) == 6
        assert sof_sampling(out), 'subsampling must be 4:4:4'

    def test_transparent_reference_decodes_with_alpha(self):
        ref = make_ref(noise_png(3000, 2000, 'RGBA'), mime_hint='image/png')
        for _ in range(4):  # q95..q85
            ref.advance()
        with Image.open(io.BytesIO(ref.current_raw())) as img:
            assert img.format == 'WEBP' and img.mode == 'RGBA'

    def test_no_mandatory_4096_precondition(self):
        # 5000 边原图在预算内：逐字节原样（旧 4096 强制缩放废除）。
        raw = noise_png(5000, 10)
        ref = make_ref(raw)
        assert ref.current_raw() == raw and not ref.changed
        assert budget.prepare_budgeted_request([ref], build_request=lambda rs: FakeRequest(rs), local_bytes=10 ** 9).body_bytes > 0


class FakeRequest:
    def __init__(self, refs):
        parts = [f'"img{r.index}":"{r.data_url()}"' for r in refs]
        self.content = ('{' + ','.join(parts) + '}').encode('utf-8')


class _SumRequest:
    # review 复现脚本（D:/ff-qa/soon-limit/waterfill_probe.py）同构：请求体 = 各 data URL 字节直拼，零 JSON 开销。
    def __init__(self, refs):
        self.content = b''.join(r.data_url().encode('ascii') for r in refs)


def _probe_noise(w: int, h: int, seed: int) -> bytes:
    # 与 waterfill_probe.py 同一生成方式（effect_noise + q100 + subsampling=0），保证复现同一组字节。
    saved = random.getstate()
    random.seed(seed)
    try:
        im = Image.effect_noise((w, h), 60).convert('RGB')
    finally:
        random.setstate(saved)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=100, subsampling=0)
    return buf.getvalue()


def serialized_sizes(refs):
    return [len(r.data_url()) for r in refs]


class TestMultiRules:
    def test_all_fit_untouched(self):
        raws = [noise_jpeg(320, 240, seed=s) for s in (1, 2, 3)]
        refs = [budget.PreparedReference(i, raw=r) for i, r in enumerate(raws)]
        outcome = budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=10 ** 9)
        assert not outcome.changed
        assert all(r.current_raw() == raw for r, raw in zip(refs, raws))

    def test_water_level_small_image_stays_original(self):
        small = noise_jpeg(64, 64, seed=1)                      # ~2KB
        big = noise_jpeg(2400, 2400, quality=97, seed=2)        # ~4MB 级
        big2 = noise_jpeg(2400, 2400, quality=97, seed=3)
        refs = [budget.PreparedReference(0, raw=small),
                budget.PreparedReference(1, raw=big),
                budget.PreparedReference(2, raw=big2)]
        base_body = len(FakeRequest(refs).content)
        local = base_body * 3 // 4  # 必须压：但小图远小于任何合理份额
        outcome = budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=local)
        assert outcome.changed
        assert refs[0].current_raw() == small, 'smaller-than-share image must stay original bytes'
        assert refs[1].changed and refs[2].changed
        assert outcome.body_bytes <= local

    def test_water_level_surplus_redistributes(self):
        # tiny + medium + huge：tiny 冻结后，份额在 medium/huge 间重分；medium 若已在自己新份额内则也不再压。
        tiny = noise_jpeg(32, 32, seed=1)                       # <1KB
        medium = noise_jpeg(700, 700, quality=90, seed=2)       # ~250KB
        huge = noise_jpeg(2600, 2600, quality=97, seed=3)       # ~5MB 级
        refs = [budget.PreparedReference(0, raw=tiny),
                budget.PreparedReference(1, raw=medium),
                budget.PreparedReference(2, raw=huge)]
        base_body = len(FakeRequest(refs).content)
        local = base_body - len(huge) // 2  # 只需 huge 压一半左右（水位下 huge 份额宽裕）
        outcome = budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=local)
        assert outcome.body_bytes <= local
        assert refs[0].current_raw() == tiny and refs[1].current_raw() == medium, 'in-share images stay original'
        assert refs[2].changed

    def test_water_level_surplus_reaches_big_images(self):
        # review 复现场景（D:/ff-qa/soon-limit/waterfill_probe.py 原场景回归）：
        # 小图 900²（q100 噪声）+ 两张大图 3000²，预算 = 原图 data URL 合计 × 0.55。
        # 旧实现在同一轮里先冻结小图、紧接着用含小图的旧 share 压大图，两张大图被压到 q80
        # （body 15.29M / 预算 20.56M，26% 没用上）；现在冻结集合收敛后份额重算，
        # 两张大图都应停在 q92，且整体首次放得下即停。
        small = _probe_noise(900, 900, 1)
        big1 = _probe_noise(3000, 3000, 2)
        big2 = _probe_noise(3000, 3000, 3)
        refs = [budget.PreparedReference(i, raw=r) for i, r in enumerate((small, big1, big2))]
        budget_bytes = int(sum(len(r) for r in (small, big1, big2)) * 4 / 3 * 0.55)
        outcome = budget.prepare_budgeted_request(refs, build_request=_SumRequest, local_bytes=budget_bytes)
        assert refs[0].stage == -1 and refs[0].current_raw() == small, '小于份额的小图保持原字节'
        for big in (refs[1], refs[2]):
            assert big.quality == 92 and max(big.width, big.height) == 3000, '大图应停在 q92，不被旧 share 压过头'
        assert outcome.body_bytes <= budget_bytes
        assert outcome.body_bytes >= budget_bytes * 9 // 10, '请求体 ≥ 预算的 90%'

    def test_floor_zero_request_with_suggested_count(self):
        raws = [noise_jpeg(3000, 3000, quality=97, seed=s) for s in (1, 2, 3)]
        refs = [budget.PreparedReference(i, raw=r) for i, r in enumerate(raws)]
        calls = []

        class Inflated(FakeRequest):
            pass

        original_encode = budget.encode_stage

        def fat(img, stage, **meta):  # 新 encode_stage 带 icc/exif 关键字；夹具签名同步，断言不变
            return b'x' * 4_000_000 + original_encode(img, stage, **meta)

        budget.encode_stage = fat
        try:
            with pytest.raises(HTTPException) as caught:
                budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=8_000_000)
        finally:
            budget.encode_stage = original_encode
        detail = caught.value.detail
        assert detail['code'] == 'request_body_floor_exceeded'
        assert '参考图太多' in detail['message'] and '减少到' in detail['message']
        assert isinstance(detail['suggested_max_references'], int) and 0 <= detail['suggested_max_references'] < len(refs)
        # 零请求语义：prepare 不发送任何东西，调用方收到异常即止。

    def test_reference_order_and_index_unchanged(self):
        raws = [noise_jpeg(300, 200, seed=1), noise_jpeg(2400, 2400, quality=97, seed=2), noise_jpeg(100, 100, seed=3)]
        refs = [budget.PreparedReference(i, raw=r) for i, r in enumerate(raws)]
        base = len(FakeRequest(refs).content)
        budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=base // 2)
        assert [r.index for r in refs] == [0, 1, 2]
        # 序列化顺序不变：body 内 img0,img1,img2 依序
        body = FakeRequest(refs).content.decode()
        assert body.index('img0') < body.index('img1') < body.index('img2')

    def test_passthrough_remote_counted_never_advanced(self):
        refs = [budget.PreparedReference(0, passthrough_value='https://cdn.example/a.png'),
                budget.PreparedReference(1, raw=noise_jpeg(2000, 2000, quality=97, seed=4))]
        base = len(FakeRequest(refs).content)
        budget.prepare_budgeted_request(refs, build_request=lambda rs: FakeRequest(rs), local_bytes=base - 1)
        assert not refs[0].changed and refs[0].has_next_stage() is False
        assert refs[1].changed
