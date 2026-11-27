"""Public HTTP contracts for the browser-only project image tools (#514)."""
import io
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms, ImageOps, JpegImagePlugin, PngImagePlugin

import main
from firefly_projects import ORIGINAL_MEDIA_TRANSACTION_NAME, ProjectNotFound, ProjectStore, ProjectStoreError
from local_edit import flip_image

ORIGIN = 'http://127.0.0.1:3198'
SAME_ORIGIN = {'Origin': ORIGIN}


def encoded(image, fmt='PNG', **kwargs):
    out = io.BytesIO()
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def decode_image_tool_result(data):
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def png_chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))


def rgba():
    image = Image.new('RGBA', (7, 5))
    image.putdata([(x * 27, y * 49, x + y, (x + y) * 19) for y in range(5) for x in range(7)])
    image.putpixel((0, 0), (100, 90, 80, 0))
    return image


def linear_rgb_profile():
    # Reuse sRGB primaries/white point, but replace the three sRGB TRCs with a linear curve.
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
    offset = len(profile)
    profile.extend(b'curv\0\0\0\0\0\0\0\1\1\0\0\0')
    struct.pack_into('>I', profile, 0, len(profile))
    for position in range(132, 132 + 12 * struct.unpack_from('>I', profile, 128)[0], 12):
        if profile[position:position + 4] in (b'rTRC', b'gTRC', b'bTRC'):
            struct.pack_into('>II', profile, position + 4, offset, 14)
    return bytes(profile)


@pytest.fixture(name='project')
def image_tools_project_fixture(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    pid = store.create_project('Human image tools')['id']
    client = TestClient(main.app, base_url=ORIGIN)
    try:
        yield client, store, pid, f'/api/project-library/{pid}/image-tools'
    finally:
        client.close()
        store.close()


def asset(project, content, filename='image.png', mime='image/png', *, pid=None):
    _, store, current, _ = project
    return store.import_media(pid or current, content, filename, mime)['asset_id']


def post_flip(project, ids, direction='horizontal', headers=SAME_ORIGIN, *, route=None):
    client, _, _, base = project
    return client.post((route or base) + '/flip', json={'asset_ids': ids, 'direction': direction}, headers=headers)


def post_color(project, source, pixels, headers=SAME_ORIGIN, *, route=None):
    client, _, _, base = project
    return client.post((route or base) + '/color-encode', data={'source_asset_id': source},
                       files={'pixels': ('pixels.png', pixels, 'image/png')}, headers=headers)


def result_image(project, file, source):
    client, _, pid, _ = project
    assert file['source_asset_id'] == source
    assert file['kind'] == 'image' and file['name']
    assert file['url'] == f"/api/project-library/{pid}/media/{file['asset_id']}"
    response = client.get(file['url'])
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == file['mime_type']
    return decode_image_tool_result(response.content)


def status(response, expected):
    assert response.status_code == expected, response.text
    return response.json()


@pytest.mark.parametrize(('direction', 'op'), [('horizontal', Image.Transpose.FLIP_LEFT_RIGHT),
                                               ('vertical', Image.Transpose.FLIP_TOP_BOTTOM)])
def test_flip_batch_order_pixels_metadata_and_no_canvas_agent_or_generation(project, direction, op):
    client, store, pid, _ = project
    first = rgba()
    second = Image.new('L', (3, 2))
    second.putdata([1, 2, 3, 15, 22, 55])
    icc = linear_rgb_profile()
    first_bytes = encoded(first, icc_profile=icc)
    first_id = asset(project, first_bytes, '图像.png')
    second_id = asset(project, encoded(second), 'mask.png')
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {
        'id': 'input', 'type': 'smart-image', 'images': [{'asset_id': first_id}, {'asset_id': second_id}],
    }}], base_revision=0)
    store.flush(pid)
    before = store.canvas(pid)
    journal = f'/api/projects/{pid}/ops'
    original_ops = status(client.get(journal), 200)
    before_tasks = set(main.CANVAS_TASKS)
    files = status(post_flip(project, [second_id, first_id, second_id], direction), 200)['files']
    assert len(files) == 3 and files[0] == files[2]
    for output, source, expected in zip(files, [second_id, first_id, second_id], [second, first, second]):
        image = result_image(project, output, source)
        assert image.format == 'PNG' and image.mode == expected.mode
        assert image.tobytes() == expected.transpose(op).tobytes()
        assert image.info.get('icc_profile') == (icc if source == first_id else None)
    assert store.canvas(pid) == before
    assert status(client.get(journal), 200) == original_ops == {'operations': []}
    assert set(main.CANVAS_TASKS) == before_tasks
    assert Path(store.media_record(pid, first_id)['path']).read_bytes() == first_bytes
    assert flip_image(second, direction).mode == 'RGBA'


@pytest.mark.parametrize('headers', [
    {}, {'X-Agent-Project': 'pretend'}, {'Origin': 'https://evil.invalid'},
    {'Origin': 'null', 'Referer': ORIGIN + '/canvas'},
    {'Origin': 'http://localhost:3198'}, {'Origin': ORIGIN + '/canvas'},
    {'Origin': '', 'Referer': ORIGIN + '/canvas'},
    [('Origin', ORIGIN), ('Origin', 'https://evil.invalid')],
])
def test_both_routes_require_strict_same_origin(project, headers):
    pixels = encoded(rgba())
    source = asset(project, pixels)
    status(post_flip(project, [source], headers=headers), 403)
    status(post_color(project, source, pixels, headers=headers), 403)
    assert len(project[1].list_media(project[2])) == 1


def test_referer_fallback_and_human_cannot_use_agent_flip(project):
    client, store, pid, _ = project
    source = asset(project, encoded(rgba()))
    status(post_flip(project, [source], headers={'Referer': ORIGIN + '/canvas'}), 200)
    response = client.post(f'/api/projects/{pid}/canvas/edit_flip', headers=SAME_ORIGIN,
                           json={'node_id': 'input', 'direction': 'horizontal'})
    assert status(response, 400)['detail']['code'] == 'INVALID_ARGUMENT'
    assert len(store.list_media(pid)) == 2


def test_lookup_is_project_local_and_batch_preflight_archives_nothing(project):
    _, store, pid, _ = project
    source = asset(project, encoded(rgba()))
    other = store.create_project('Other')['id']
    foreign = asset(project, encoded(Image.new('RGB', (2, 2), 'red')), pid=other)
    for unavailable in (foreign, 'absent'):
        status(post_flip(project, [source, unavailable]), 404)
        status(post_color(project, unavailable, encoded(rgba())), 404)
    status(post_flip(project, [source], route='/api/project-library/absent/image-tools'), 404)
    status(post_color(project, source, encoded(rgba()), route='/api/project-library/absent/image-tools'), 404)
    assert len(store.list_media(pid)) == 1
    assert len(store.list_media(other)) == 1


@pytest.mark.parametrize('body', [
    {'asset_ids': [], 'direction': 'horizontal'}, {'asset_ids': [2], 'direction': 'horizontal'},
    {'asset_ids': ['../outside'], 'direction': 'horizontal'}, {'asset_ids': ['id'], 'direction': 'diagonal'},
    {'asset_ids': ['id'], 'direction': 'horizontal', 'node_id': 'input'},
])
def test_invalid_flip_body_is_rejected(project, body):
    client, _, _, url = project
    status(client.post(url + '/flip', headers=SAME_ORIGIN, json=body), 422)


@pytest.mark.parametrize(('fmt', 'mime', 'options'), [
    ('GIF', 'image/gif', {'save_all': True}), ('PNG', 'image/png', {'save_all': True}),
    ('WEBP', 'image/webp', {'save_all': True, 'lossless': True}), ('BMP', 'image/bmp', {}),
])
def test_animated_or_unsupported_source_is_explicit_and_never_partially_archived(project, fmt, mime, options):
    image = rgba()
    source = asset(project, encoded(image))
    if options.get('save_all'):
        options = dict(options, append_images=[Image.new('RGBA', image.size, 'yellow')], duration=100)
    invalid = asset(project, encoded(image, fmt, **options), f'unsupported.{fmt.lower()}', mime)
    detail = status(post_flip(project, [source, invalid]), 415)['detail']
    assert detail
    assert status(post_color(project, invalid, encoded(image)), 415)['detail'] == detail
    assert len(project[1].list_media(project[2])) == 2


def test_invalid_media_color_png_and_dimensions_are_rejected(project):
    image = rgba()
    source = asset(project, encoded(image))
    corrupt = asset(project, b'invalid image bytes')
    not_image = asset(project, b'video bytes', 'video.mp4', 'video/mp4')
    status(post_flip(project, [source, corrupt]), 415)
    status(post_flip(project, [source, not_image]), 415)
    status(post_color(project, source, b'not PNG'), 415)
    status(post_color(project, source, encoded(image.convert('RGB'), 'JPEG')), 415)
    status(post_color(project, source, encoded(image.resize((5, 7)))), 422)
    assert len(project[1].list_media(project[2])) == 3


def test_flip_16bit_grayscale_and_still_webp_keep_samples(project):
    gray = Image.new('I;16', (5, 2))
    gray.putdata([65535, 4096, 257, 0, 14, 9, 1800, 33000, 45001, 259])
    webp = rgba()
    inputs = [asset(project, encoded(gray), 'gray.png'),
              asset(project, encoded(webp, 'WEBP', lossless=True, exact=True), 'alpha.webp', 'image/webp')]
    outputs = status(post_flip(project, inputs), 200)['files']
    for expected, fmt, source, out in zip([gray, webp], ['PNG', 'WEBP'], inputs, outputs):
        image = result_image(project, out, source)
        assert image.format == fmt and image.mode == expected.mode
        assert image.tobytes() == expected.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes()
        if fmt == 'PNG':
            assert image.getextrema()[1] == 65535


def test_indexed_png_flip_preserves_palette_transparency_and_display_metadata(project):
    image = Image.new('P', (3, 2))
    image.putpalette([100, 90, 80, 210, 65, 27, 254, 245, 236])
    image.putdata([0, 2, 1, 2, 1, 0])
    chunks = PngImagePlugin.PngInfo()
    chunks.add(b'gAMA', struct.pack('>I', 52000))
    original = encoded(image, transparency=bytes([0, 155, 255]), pnginfo=chunks)
    source = asset(project, original)
    file = status(post_flip(project, [source]), 200)['files'][0]
    result = result_image(project, file, source)
    original_image = decode_image_tool_result(original)
    assert result.mode == 'P' and result.getpalette() == original_image.getpalette()
    assert result.info['transparency'] == original_image.info['transparency']
    assert result.info['gamma'] == original_image.info['gamma']
    assert result.convert('RGBA').tobytes() == original_image.convert('RGBA').transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes()


@pytest.mark.parametrize(('depth', 'data', 'target_key'), [(1, b'\x80', 1), (2, b'\x70', 85)])
def test_grayscale_png_flip_keeps_a_valid_transparent_sample(project, depth, data, target_key):
    png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, depth, 0, 0, 0, 0))
           + png_chunk(b'tRNS', b'\0\1') + png_chunk(b'IDAT', zlib.compress(b'\0' + data))
           + png_chunk(b'IEND', b''))
    source = asset(project, png)
    file = status(post_flip(project, [source]), 200)['files'][0]
    response = project[0].get(file['url'])
    assert png_chunk(b'tRNS', struct.pack('>H', target_key)) in response.content
    assert list(decode_image_tool_result(response.content).convert('RGBA').getchannel('A').get_flattened_data()) == [255, 0]


def test_reject_16bit_rgb_when_pillow_would_silently_reduce_depth(project):
    png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 16, 2, 0, 0, 0))
           + png_chunk(b'IDAT', zlib.compress(b'\0' + struct.pack('>6H', 1, 257, 4095, 0, 127, 65535)))
           + png_chunk(b'IEND', b''))
    source = asset(project, png)
    status(post_flip(project, [source]), 415)
    status(post_color(project, source, encoded(Image.new('RGB', (2, 1)))), 415)


def test_jpeg_exif_visual_flip_icc_and_measured_double_flip(project):
    image = Image.new('RGB', (83, 61))
    image.putdata([((x * 11 + y * 3) % 256, (x * 5 + y * 8) % 256,
                    (x * 2 + y * 19 + (x > 32) * 61) % 256)
                   for y in range(image.height) for x in range(image.width)])
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = 'Firefly fixture'
    icc = linear_rgb_profile()
    data = encoded(image, 'JPEG', quality=95, subsampling=0, exif=exif, icc_profile=icc)
    source = asset(project, data, 'oriented.jpg', 'image/jpeg')
    visual = ImageOps.exif_transpose(decode_image_tool_result(data))
    first = status(post_flip(project, [source]), 200)['files'][0]
    first_image = result_image(project, first, source)
    second = status(post_flip(project, [first['asset_id']]), 200)['files'][0]
    second_image = result_image(project, second, first['asset_id'])
    for output in (first_image, second_image):
        assert output.format == 'JPEG' and output.size == visual.size
        assert output.getexif().get(274, 1) == 1 and output.getexif()[315] == 'Firefly fixture'
        assert output.info['icc_profile'] == icc and JpegImagePlugin.get_sampling(output) == 0
        assert all(v == 1 for table in output.quantization.values() for v in table)
    assert first_image.tobytes() != second_image.tobytes()
    for label, actual, expected in [('first', first_image, visual.transpose(Image.Transpose.FLIP_LEFT_RIGHT)),
                                    ('double', second_image, visual)]:
        errors = [abs(a - b) for a, b in zip(actual.tobytes(), expected.tobytes())]
        peak, mean = max(errors), sum(errors) / len(errors)
        print(f'JPEG {label} flip RGB per-channel abs error: max={peak}, mean={mean:.6f}')
        assert peak <= 12 and mean <= 2.0


def test_color_uses_browser_pixels_transforms_to_original_icc_and_only_archives(project):
    client, store, pid, _ = project
    original = rgba()
    icc = linear_rgb_profile()
    source_bytes = encoded(original, icc_profile=icc)
    source = asset(project, source_bytes)
    pixels = rgba().transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    before = store.canvas(pid)
    before_tasks = set(main.CANVAS_TASKS)
    file = status(post_color(project, source, encoded(pixels)), 200)['file']
    result = result_image(project, file, source)
    assert result.format == 'PNG' and result.mode == 'RGBA'
    assert result.info['icc_profile'] == icc
    expected = ImageCms.profileToProfile(pixels.convert('RGB'), ImageCms.createProfile('sRGB'),
                                         ImageCms.getOpenProfile(io.BytesIO(icc)), outputMode='RGB')
    assert expected.tobytes() != pixels.convert('RGB').tobytes()
    assert result.convert('RGB').tobytes() == expected.tobytes()
    assert result.getchannel('A').tobytes() == pixels.getchannel('A').tobytes()
    assert store.canvas(pid) == before and set(main.CANVAS_TASKS) == before_tasks
    assert status(client.get(f'/api/projects/{pid}/ops'), 200) == {'operations': []}
    assert Path(store.media_record(pid, source)['path']).read_bytes() == source_bytes


def test_color_unprofiled_png_is_exact_and_rejects_unsupported_icc(project):
    image = rgba()
    adjusted = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    source = asset(project, encoded(image))
    exif = Image.Exif()
    exif[315] = 'Must not be inherited from the upload'
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    file = status(post_color(project, source, encoded(adjusted, icc_profile=srgb, exif=exif)), 200)['file']
    output = result_image(project, file, source)
    assert output.tobytes() == adjusted.tobytes() and not output.info.get('icc_profile') and not output.getexif()
    bad_profile = asset(project, encoded(image, icc_profile=b'not an ICC profile'))
    status(post_color(project, bad_profile, encoded(adjusted)), 415)


def test_color_preserves_jpeg_and_applies_browser_pixels_to_visual_dimensions(project):
    source_image = Image.new('RGB', (11, 9), (27, 202, 31))
    exif = Image.Exif()
    exif[274] = 8
    exif[315] = 'Firefly color fixture'
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    source = asset(project, encoded(source_image, 'JPEG', exif=exif, icc_profile=srgb), 'source.jpg', 'image/jpeg')
    pixels = Image.new('RGBA', (9, 11), (153, 81, 252, 255))
    status(post_color(project, source, encoded(pixels, icc_profile=linear_rgb_profile())), 415)
    status(post_color(project, source, encoded(pixels.transpose(Image.Transpose.TRANSPOSE))), 422)
    file = status(post_color(project, source, encoded(pixels, icc_profile=srgb)), 200)['file']
    result = result_image(project, file, source)
    assert result.format == 'JPEG' and result.size == pixels.size
    assert result.info['icc_profile'] == srgb
    assert result.getexif().get(274, 1) == 1 and result.getexif()[315] == 'Firefly color fixture'
    assert max(abs(a - b) for a, b in zip(result.tobytes(), pixels.convert('RGB').tobytes())) <= 2


def test_pending_original_transaction_is_not_replayed(project):
    _, store, pid, _ = project
    source = asset(project, encoded(rgba()))
    project_dir = store.root / pid
    transaction = project_dir / 'trash' / ORIGINAL_MEDIA_TRANSACTION_NAME
    transaction.parent.mkdir(exist_ok=True)
    transaction.write_bytes(b'pending transaction')
    canvas = (project_dir / 'canvas.json').read_bytes()
    index = (project_dir / 'media' / 'index.json').read_bytes()
    status(post_flip(project, [source]), 409)
    status(post_color(project, source, encoded(rgba())), 409)
    assert transaction.read_bytes() == b'pending transaction'
    assert (project_dir / 'canvas.json').read_bytes() == canvas
    assert (project_dir / 'media' / 'index.json').read_bytes() == index
    transaction.unlink()


def test_batch_rechecks_sources_before_writing_any_outputs(project, monkeypatch):
    from app.routers import api_project_library

    _, store, pid, _ = project
    source = asset(project, encoded(rgba()))
    media_path = Path(store.media_record(pid, source)['path'])
    index_path = media_path.parent / 'index.json'
    before = index_path.read_bytes()
    original_render = api_project_library.render_image

    def changed(*args, **kwargs):
        result = original_render(*args, **kwargs)
        media_path.write_bytes(b'source changed')
        return result

    monkeypatch.setattr(api_project_library, 'render_image', changed)
    status(post_flip(project, [source]), 409)
    assert index_path.read_bytes() == before
    assert {p.name for p in media_path.parent.iterdir()} == {media_path.name, 'index.json'}


def test_media_tools_never_open_or_repair_the_canvas(project, monkeypatch):
    _, store, pid, _ = project
    pixels = rgba()
    source = asset(project, encoded(pixels))
    adjusted = encoded(pixels.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    canvas_path = store.root / pid / 'canvas.json'
    canvas_path.write_bytes(b'not a readable canvas')

    def forbidden(*args, **kwargs):
        pytest.fail('media-only operations must not open or repair the canvas')

    monkeypatch.setattr(store, 'open_project', forbidden)
    first = status(post_flip(project, [source]), 200)['files'][0]
    assert status(post_color(project, source, adjusted), 200)['file']['asset_id'] == first['asset_id']
    assert canvas_path.read_bytes() == b'not a readable canvas'


def test_color_original_is_retained_after_save_and_can_be_deleted_explicitly(project):
    _, store, pid, _ = project
    pixels = rgba()
    source = asset(project, encoded(pixels), 'source.png')
    derivative = status(post_color(project, source, encoded(pixels.transpose(Image.Transpose.FLIP_LEFT_RIGHT))), 200)['file']['asset_id']
    assert source != derivative
    edit = {'op': 'color', 'algo_version': 1, 'params': {'exposure': 5}, 'source_asset_id': source}
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {
        'id': 'graded', 'type': 'smart-image', 'images': [{'asset_id': derivative, 'edit': edit}],
    }}], base_revision=0)
    store.flush(pid)
    assert store.open_project(pid)['canvas']['nodes'][0]['images'][0]['edit'] == edit
    assert store.delete_media_if_unreferenced(pid, source) is False
    with pytest.raises(ProjectStoreError, match='仍被'):
        store.archive_unreferenced_media(pid, source)
    store.delete_project_media_asset(pid, source, base_revision=1)
    assert store.open_project(pid)['canvas']['nodes'][0]['images'] == [{'asset_id': derivative}]
    with pytest.raises(ProjectNotFound):
        store.media_record(pid, source)
