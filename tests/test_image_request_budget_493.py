#493 用户裁定：soon 已知硬限是整个实际序列化请求体 16,777,216 B（本地目标 16,500,000），
#超限只压发送副本、每步重新实际序列化、只动当前体积最大的一张；未知上限 413 走 ×0.85 临时上限、最多 4 次重发。
#见 MESSAGES.md:3901–3921/:3941–3944 与 #493 最后三条评论。
import base64
import io
import json

import pytest
from fastapi import HTTPException
from PIL import Image

import app.services.image_request_budget as budget


def png_bytes(width, height, alpha=False, seed=0, noise=False):
    mode = 'RGBA' if alpha else 'RGB'
    img = Image.new(mode, (width, height))
    px = img.load()
    for y in range(height):
        for x in range(0, width, 2):
            px[x, y] = ((x * 7 + y * 13 + seed) % 256, (x * 3 + y * 11 + seed) % 256, (x * 5 + y * 17) % 256, 255 if alpha else 0) if alpha else ((x * 7 + y * 13 + seed) % 256, (x * 3 + y * 11 + seed) % 256, (x * 5 + y * 17) % 256)
    if noise:
        # 全像素随机：平坦图会被 PNG 压得很小，破坏「阶梯只会变小」的测试前提。
        import random
        rnd = random.Random(seed or 1)
        for y in range(height):
            for x in range(width):
                v = rnd.randrange(256)
                px[x, y] = (v, (v * 2) % 256, (v * 3) % 256) + ((255,) if alpha else ())
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


# ---------- 已知上限登记：只认两个实测端点 ----------

def test_known_limit_exact_registered_endpoints():
    limit = budget.lookup_known_body_limit('https://api.soonstudio.ai/v1/images/generations')
    assert limit is not None and limit.hard_bytes == 16777216 and limit.local_bytes == 16500000
    native = budget.lookup_known_body_limit('https://api.soonstudio.ai/v1beta/models/gemini-3.1-flash-image-preview:generateContent')
    assert native == limit


def test_unknown_endpoints_have_no_limit():
    for url in ('https://api.soonstudio.ai/v1/images/edits',
                'https://soonstudio.ai/v1/images/generations',
                'http://api.soonstudio.ai/v1/images/generations',
                'https://api.soonstudio.ai:8443/v1/images/generations',
                'https://api.soonstudio.ai/v1beta/models/x:streamGenerateContent',
                'https://other.example/v1/images/generations'):
        assert budget.lookup_known_body_limit(url) is None, url


# ---------- PreparedReference 阶梯 ----------

def prepared(raw, index=0):
    return budget.PreparedReference(index=index, raw=raw)

def test_baseline_passthrough_and_stage_order_opaque():
    ref = prepared(png_bytes(1600, 1200, seed=5))
    assert ref.stage == -1 and ref.current_bytes == len(ref.raw) and ref.current_mime.startswith('image/png')
    stages = ref.stages()
    # #500：不透明照片无无损 WebP；先原尺寸质量档 95→…→80，再尺寸档 q80。
    assert stages[0] == ('JPEG', 95)
    assert stages[:5] == [('JPEG', 95), ('JPEG', 92), ('JPEG', 90), ('JPEG', 85), ('JPEG', 80)]
    assert ('WEBP', 'lossless') not in stages
    assert stages[-1] == ('JPEG', 1536, 80)  # 底线 1536@q80

def test_alpha_stages_keep_alpha_path_and_no_jpeg():
    ref = prepared(png_bytes(1600, 1200, alpha=True, seed=5))
    assert ref.has_alpha is True
    for stage in ref.stages():
        assert stage[0] == 'WEBP', stage  # alpha 全程 WebP，不 JPEG
    assert ref.stages()[0] == ('WEBP', 95)  # #500：透明图走与 JPEG 相同的质量阶梯，无无损档
    assert ref.stages()[-1] == ('WEBP', 1536, 80)

def test_no_upscale_small_images_skip_resize_stages():
    ref = prepared(png_bytes(800, 600, seed=9))
    for stage in ref.stages():
        if len(stage) == 3:
            assert stage[1] < 800, stage  # 只对小于基准长边的档生效
    # 走到底线仍不放大
    while ref.has_next_stage():
        ref.advance()
    assert max(ref.width, ref.height) == 800  # 800<1536：底线 q80 在原尺寸

def test_every_stage_rebuilt_from_baseline_pixels():
    ref = prepared(png_bytes(900, 700, seed=3, noise=True))
    ref.advance()  # jpeg 95
    ref.advance()  # jpeg 92
    ref.advance()  # jpeg 90（#500 阶梯：质量档优先）
    direct = budget.encode_stage(ref.baseline_image(), ('JPEG', 90))
    assert ref.current_bytes == len(direct)  # 每档直接从基准像素生成，不叠已有损

def test_lossless_webp_is_lossless_for_alpha():
    # #500 废除无损 WebP 档：首档即 lossy WEBP q95 并保住 alpha（名字保留以稳定测试 ID）。
    raw = png_bytes(120, 90, alpha=True, seed=4)
    ref = prepared(raw)
    ref.advance()
    img = Image.open(io.BytesIO(ref.current_raw()))
    assert img.format == 'WEBP' and 'A' in img.getbands()
    assert ref.quality == 95 and ref.lossless is False

# ---------- 控制器：每步实际序列化、最大当前者、first-fit ----------

class FakeRequest:
    def __init__(self, content): self.content = content

def builder_overhead(overhead=b'{"model":"m","prompt":"p"}'):
    # build_request 契约：接收 PreparedReference 列表，返回带 .content 的请求；字节为真实序列化长度。
    def build(refs):
        values = [r.data_url() for r in refs]
        body = overhead + b'[' + b','.join(v.encode('ascii') for v in values) + b']'
        return FakeRequest(body)
    return build

def test_within_limit_sends_baseline_unchanged():
    refs = [prepared(png_bytes(64, 48, seed=1))]
    out = budget.prepare_budgeted_request(refs, build_request=builder_overhead(), local_bytes=16500000)
    assert out.changed is False and out.body_bytes == len(builder_overhead()([refs[0]]).content)
    assert refs[0].stage == -1

def test_boundary_equal_local_limit_accepted():
    ref = prepared(png_bytes(64, 48, seed=1))
    build = lambda refs: FakeRequest(b'[' + b','.join(r.data_url().encode() for r in refs) + b']')
    out = budget.prepare_budgeted_request([ref], build_request=build, local_bytes=len(b'[' + ref.data_url().encode() + b']'))
    assert out.changed is False  # <= 判定

def test_largest_current_selected_with_index_tiebreak():
    # #500 水位取代 first-fit 最大者：同体积同份额的并列图在同一轮内全部推进。
    a = prepared(png_bytes(150, 150, seed=11, noise=True), index=0)
    b = prepared(png_bytes(150, 150, seed=11, noise=True), index=1)
    assert len(a.raw) == len(b.raw)
    build = builder_overhead(overhead=b'x' * 5000)
    out = budget.prepare_budgeted_request([b, a], build_request=build, local_bytes=90000)
    stages_seen = out.steps
    assert len(stages_seen) >= 2
    first_round_indices = {row['index'] for row in stages_seen if row['share_bytes'] == stages_seen[0]['share_bytes']}
    assert first_round_indices == {0, 1}, '超份额的都推进，不再只动最大者'
    assert out.body_bytes <= 90000

def test_first_fit_advances_largest_and_stops():
    # #500 水位：小图份额内原样，大图按自己份额逐档；首个达标轮立即停。
    big_raw = png_bytes(700, 500, seed=2, noise=True)
    small_raw = png_bytes(160, 120, seed=6, noise=True)
    build = builder_overhead()
    probe = budget.PreparedReference(0, big_raw)
    small_probe = budget.PreparedReference(1, small_raw)
    base_body = len(build([probe, small_probe]).content)
    k, fit_body = None, None
    while probe.has_next_stage():
        probe.advance()
        b = len(build([probe, small_probe]).content)
        if b <= base_body - 1000:
            k, fit_body = probe.stage, b
            break
    assert k is not None, 'noise fixture must shrink'
    refs = [budget.PreparedReference(0, big_raw), budget.PreparedReference(1, small_raw)]
    out = budget.prepare_budgeted_request(refs, build_request=build, local_bytes=fit_body)
    assert out.changed is True and out.body_bytes <= fit_body
    assert refs[0].stage >= k and refs[1].stage == -1  # 只压大图；水位目标比旧 first-fit 更保守，至少到 k
    assert len(out.steps) == refs[0].stage + 1

def test_floor_exhausted_error_lists_every_image():
    refs = [prepared(png_bytes(300, 200, seed=7, noise=True), index=0),
            prepared(png_bytes(300, 200, alpha=True, seed=8, noise=True), index=1)]
    with pytest.raises(HTTPException) as caught:
        budget.prepare_budgeted_request(refs, build_request=builder_overhead(overhead=b'x' * 50000), local_bytes=1000)
    detail = caught.value.detail
    assert caught.value.status_code == 413
    assert detail['code'] == 'request_body_floor_exceeded'
    assert detail['reference_count'] == 2 and detail['body_bytes'] > detail['limit_bytes']
    assert detail['excess_bytes'] == detail['body_bytes'] - detail['limit_bytes']
    rows = detail['references']
    assert len(rows) == 2
    for row in rows:
        for key in ('index', 'source_format', 'final_format', 'quality', 'lossless', 'from_width', 'from_height', 'width', 'height', 'byte_count', 'stage', 'changed'):
            assert key in row, (row, key)
    assert '参考图太多' in detail['message'] and '减少到' in detail['message']  # #500 票面文案

def test_zero_image_overhead_fails_honestly():
    build = builder_overhead(overhead=b'x' * 100)
    with pytest.raises(HTTPException) as caught:
        budget.prepare_budgeted_request([], build_request=build, local_bytes=50)
    detail = caught.value.detail
    assert '非图片开销' in detail['message'] or 'prompt' in detail['message']
    assert detail['reference_count'] == 0
    assert '减少参考图' not in detail['message']

# ---------- 413 政策（纯函数层）----------

def test_unknown_cap_temporary_target_floor_85():
    assert budget.temporary_413_target(1000001) == (1000001 * 85) // 100  # 向下取整
    assert budget.temporary_413_target(999) == 849

def test_unknown_cap_selection_uses_ladder_first_below_target():
    big = prepared(png_bytes(700, 500, seed=2, noise=True), index=0)
    build = builder_overhead()
    body0 = len(build([big]).content)
    target = budget.temporary_413_target(body0)
    out = budget.advance_until_below(big, build_single=build, target_bytes=target)
    assert out is not None and out['body_bytes'] < target
    assert big.stage >= 0

def test_remote_passthrough_measured_but_never_advanced():
    url = 'https://cdn.example/a.png'
    ref = budget.PreparedReference(0, passthrough_value=url)
    assert ref.current_bytes == len(url.encode()) and not ref.has_next_stage()
    assert ref.data_url() == url and ref.stages() == []
    with pytest.raises(HTTPException):
        budget.prepare_budgeted_request([ref], build_request=builder_overhead(overhead=b'x' * 10), local_bytes=5)
    assert ref.stage == -1  # 远程只计量，不可压

def test_report_projection_has_no_private_fields():
    ref = prepared(png_bytes(120, 100, seed=1), index=3)
    ref.advance(); ref.advance()
    row = budget.reference_projection(ref)
    text = json.dumps(row, ensure_ascii=False)
    assert 'url' not in {k.lower() for k in row}
    assert 'base64' not in text and 'data:' not in text
    assert row['index'] == 3 and row['changed'] is True


def test_compression_label_describes_format_quality_and_resize():
    label = budget.compression_label
    assert label({'changed': False}) == ''
    assert label({'changed': True, 'source_format': 'PNG', 'final_format': 'WEBP', 'lossless': True,
                   'quality': None, 'from_width': 4096, 'from_height': 4096, 'width': 4096, 'height': 3072}) == 'PNG→WebP 无损'
    assert label({'changed': True, 'source_format': 'PNG', 'final_format': 'WEBP', 'lossless': False,
                   'quality': 90, 'from_width': 4096, 'from_height': 4096, 'width': 3072, 'height': 3072}) == 'PNG→WebP q90，4096→3072'
    assert label({'changed': True, 'source_format': 'PNG', 'final_format': 'JPEG', 'lossless': False,
                   'quality': 95, 'from_width': 1500, 'from_height': 1000, 'width': 1500, 'height': 1000}) == 'PNG→JPEG q95'


def test_report_note_prefixes_only_when_present():
    report = [{'changed': True, 'source_format': 'PNG', 'final_format': 'WEBP', 'lossless': False, 'quality': 90,
               'from_width': 4096, 'from_height': 4096, 'width': 3072, 'height': 3072},
              {'changed': False}]
    assert budget.report_note(report) == '已为本次请求压缩：PNG→WebP q90，4096→3072'
    assert budget.report_note([{'changed': False}]) == ''
    assert budget.report_note(None) == ''
