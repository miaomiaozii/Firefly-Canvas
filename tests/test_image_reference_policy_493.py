"""#493: image-generation copies are distinct from the legacy chat/video adapter."""
import base64
from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

import main
from app.services import conversation, media
from firefly_projects import ProjectStore, ProjectStoreError


class ImageGenerationReferencePolicyTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='image-reference-493-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        for name in ('OUTPUT_DIR', 'OUTPUT_OUTPUT_DIR', 'ASSETS_DIR'):
            self.enterContext(patch.object(main, name, str(self.root)))

    def reference(self, name='reference.png', *, size=(2048, 1024), mode='RGB', fmt='PNG', **options):
        image = Image.new(mode, size, {'RGB': (24, 80, 139), 'RGBA': (24, 80, 139, 73), 'LA': (100, 73), 'L': 100, 'P': 0}[mode])
        self.addCleanup(image.close)
        if mode == 'P':
            image.putpalette([24, 80, 139, 200, 30, 40] + [0] * 762)
        target = self.root / name
        image.save(target, format=fmt, **options)
        return {'url': f'/output/{name}'}, target.read_bytes()

    def decoded(self, raw):
        image = Image.open(BytesIO(raw))
        image.load()
        self.addCleanup(image.close)
        return image

    def assert_invalid(self, ref, *, code=None):
        with self.assertRaises(HTTPException) as caught:
            media.generation_reference_bytes(ref)
        self.assertEqual(caught.exception.status_code, 400)
        if code:
            self.assertEqual(caught.exception.detail['code'], code)

    def test_png_and_jpeg_through_4096_are_exact_original_bytes_without_encoder(self):
        for fmt, suffix in (('PNG', 'png'), ('JPEG', 'jpg')):
            for size in ((1, 1), (1024, 768), (2048, 1024), (4096, 17), (17, 4096)):
                with self.subTest(fmt=fmt, size=size):
                    ref, original = self.reference(f'original.{suffix}', size=size, fmt=fmt)
                    with patch.object(Image.Image, 'save', autospec=True, side_effect=Image.Image.save) as encode:
                        raw, mime, filename = media.generation_reference_bytes(ref)
                    encode.assert_not_called()
                    self.assertEqual(raw, original)
                    self.assertEqual(mime, f'image/{"jpeg" if fmt == "JPEG" else "png"}')
                    self.assertEqual(filename, f'original.{suffix}')
                    self.assertEqual(self.decoded(raw).size, size)

    def test_5000_png_and_jpeg_resize_to_4096_proportionally_in_original_format(self):
        for fmt, suffix in (('PNG', 'png'), ('JPEG', 'jpg')):
            for size, expected in (((5000, 2500), (4096, 2048)), ((2500, 5000), (2048, 4096)), ((4097, 100), (4096, 100))):
                with self.subTest(fmt=fmt, size=size):
                    ref, original = self.reference(f'large.{suffix}', size=size, fmt=fmt)
                    raw, mime, filename = media.generation_reference_bytes(ref)
                    self.assertNotEqual(raw, original)
                    self.assertEqual((self.decoded(raw).size, self.decoded(raw).format), (expected, fmt))
                    self.assertEqual(mime, f'image/{"jpeg" if fmt == "JPEG" else "png"}')
                    self.assertEqual(filename, f'large.{suffix}')
                    self.assertEqual((self.root / filename).read_bytes(), original)

    def test_alpha_png_is_exact_when_small_and_retains_alpha_when_resized(self):
        for mode in ('RGBA', 'LA', 'P'):
            for size, expected in (((2048, 1024), (2048, 1024)), ((5000, 200), (4096, 164))):
                with self.subTest(mode=mode, size=size):
                    options = {'transparency': bytes([73, 255])} if mode == 'P' else {}
                    ref, original = self.reference(mode + '.png', mode=mode, size=size, **options)
                    raw, mime, _name = media.generation_reference_bytes(ref)
                    result = self.decoded(raw)
                    self.assertEqual((result.size, result.format, mime), (expected, 'PNG', 'image/png'))
                    self.assertEqual(result.convert('RGBA').getchannel('A').getextrema(), (73, 73))
                    if size == expected:
                        self.assertEqual(raw, original)

    def test_png_colour_key_transparency_is_preserved_when_resized(self):
        for mode, key in (('L', 100), ('RGB', (24, 80, 139))):
            with self.subTest(mode=mode):
                ref, _original = self.reference(mode + '.png', mode=mode, size=(5000, 100), transparency=key)
                raw, mime, _name = media.generation_reference_bytes(ref)
                image = self.decoded(raw)
                self.assertEqual((mime, image.format, image.size), ('image/png', 'PNG', (4096, 82)))
                self.assertEqual(image.convert('RGBA').getchannel('A').getextrema(), (0, 0))

    def test_data_url_and_gemini_inline_data_share_exact_original_bytes(self):
        ref, original = self.reference()
        for input_ref in (ref, {'url': 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')}):
            with self.subTest(source=input_ref['url'][:25]):
                data_url = media.generation_reference_to_data_url(input_ref)
                self.assertEqual(data_url, 'data:image/png;base64,' + base64.b64encode(original).decode('ascii'))
                part = conversation.gemini_generation_reference_part(input_ref)
                self.assertEqual(part, {'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(original).decode('ascii')}})

    def test_oversized_data_url_cannot_bypass_resize_or_alpha_policy(self):
        _ref, original = self.reference(size=(5000, 2500), mode='RGBA')
        ref = {'url': 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')}
        part = conversation.gemini_generation_reference_part(ref)['inlineData']
        result = self.decoded(base64.b64decode(part['data']))
        self.assertEqual((part['mimeType'], result.format, result.size, result.mode), ('image/png', 'PNG', (4096, 2048), 'RGBA'))
        self.assertEqual(result.getchannel('A').getextrema(), (73, 73))

    def test_detected_format_not_mislabelled_extension_or_data_url_mime_is_authoritative(self):
        ref, original = self.reference(name='mislabelled.jpg')
        for input_ref in (ref, {'url': 'data:image/jpeg;base64,' + base64.b64encode(original).decode('ascii')}):
            with self.subTest(source=input_ref['url'][:25]):
                raw, mime, filename = media.generation_reference_bytes(input_ref)
                self.assertEqual((raw, mime, Path(filename).suffix), (original, 'image/png', '.png'))

    def test_resized_png_keeps_text_icc_dpi_and_exif_orientation(self):
        text = PngInfo()
        text.add_text('Author', 'fixture-author')
        text.add_itxt('Description', '透明参考图', lang='zh', tkey='描述')
        text.add(b'gAMA', struct.pack('>I', 45455))
        chromaticity = (31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
        text.add(b'cHRM', struct.pack('>8I', *chromaticity))
        exif = Image.Exif()
        exif[274] = 6
        ref, _original = self.reference(size=(5000, 200), mode='RGBA', pnginfo=text, icc_profile=b'fixture-icc', dpi=(144, 144), exif=exif)
        raw, mime, _name = media.generation_reference_bytes(ref)
        image = self.decoded(raw)
        self.assertEqual(mime, 'image/png')
        self.assertEqual(image.info['Author'], 'fixture-author')
        self.assertEqual(image.info['Description'], '透明参考图')
        self.assertEqual(image.text['Description'].lang, 'zh')
        self.assertEqual(image.text['Description'].tkey, '描述')
        self.assertEqual(image.info['gamma'], 0.45455)
        self.assertEqual(image.info['chromaticity'], tuple(value / 100000 for value in chromaticity))
        self.assertEqual(image.info['icc_profile'], b'fixture-icc')
        self.assertAlmostEqual(image.info['dpi'][0], 144, delta=0.03)
        self.assertEqual(image.getexif()[274], 6)

    def test_resized_png_keeps_srgb_colour_metadata(self):
        info = PngInfo()
        info.add(b'sRGB', b'\x00')
        ref, original = self.reference(size=(5000, 100), pnginfo=info)
        self.assertEqual(self.decoded(original).info['srgb'], 0)
        raw, mime, _name = media.generation_reference_bytes(ref)
        image = self.decoded(raw)
        self.assertEqual((mime, image.size, image.info['srgb']), ('image/png', (4096, 82), 0))

    def test_jpeg_keeps_exif_orientation_icc_quantization_sampling_and_progressive(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = 'fixture-author'
        for size in ((512, 256), (5000, 200)):
            with self.subTest(size=size):
                ref, original = self.reference('oriented.jpg', size=size, fmt='JPEG', quality=93, subsampling=0, progressive=True, exif=exif, icc_profile=b'fixture-icc', dpi=(144, 144))
                source = self.decoded(original)
                raw, mime, _name = media.generation_reference_bytes(ref)
                image = self.decoded(raw)
                self.assertEqual(mime, 'image/jpeg')
                self.assertEqual(image.getexif()[274], 6)
                self.assertEqual(image.getexif()[315], 'fixture-author')
                self.assertEqual(image.info['icc_profile'], b'fixture-icc')
                self.assertEqual(image.info['dpi'], (144, 144))
                self.assertEqual(image.quantization, source.quantization)
                self.assertEqual(image.layer, source.layer)
                self.assertTrue(image.info.get('progressive'))
                self.assertEqual(image.size, (512, 256) if size[0] == 512 else (4096, 164))
                self.assertEqual(ImageOps.exif_transpose(image).size, tuple(reversed(image.size)))
                if size[0] == 512:
                    self.assertEqual(raw, original)

    def test_webp_preserves_original_bytes_or_resizes_in_webp_with_alpha(self):
        for size in ((512, 256), (5000, 200)):
            with self.subTest(size=size):
                ref, original = self.reference('reference.webp', size=size, fmt='WEBP', mode='RGBA', lossless=True)
                raw, mime, _name = media.generation_reference_bytes(ref)
                image = self.decoded(raw)
                self.assertEqual((mime, image.format), ('image/webp', 'WEBP'))
                self.assertEqual(image.getchannel('A').getextrema(), (73, 73))
                self.assertEqual(image.size, size if size[0] == 512 else (4096, 164))
                if size[0] == 512:
                    self.assertEqual(raw, original)

    def test_unresized_other_raster_format_is_preserved_but_unsupported_resize_is_explicit(self):
        ref, original = self.reference('reference.bmp', size=(128, 64), fmt='BMP')
        with self.assertRaises(ProjectStoreError):
            media.generation_reference_bytes(ref)
        inline = {'url': 'data:image/bmp;base64,' + base64.b64encode(original).decode('ascii')}
        self.assertEqual(media.generation_reference_bytes(inline), (original, 'image/bmp', 'reference.bmp'))
        _ref, original = self.reference('large.bmp', size=(5000, 2), fmt='BMP')
        inline = {'url': 'data:image/bmp;base64,' + base64.b64encode(original).decode('ascii')}
        self.assert_invalid(inline, code='image_generation_reference_resize_unsupported')

    def test_animation_is_not_silently_flattened(self):
        for size in ((128, 64), (5000, 2)):
            first, second = Image.new('RGB', size, 'red'), Image.new('RGB', size, 'blue')
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            path = self.root / 'animation.gif'
            first.save(path, format='GIF', save_all=True, append_images=[second], duration=120, loop=0)
            ref = {'url': '/output/animation.gif'}
            if size[0] <= 4096:
                raw, mime, _name = media.generation_reference_bytes(ref)
                self.assertEqual((raw, mime), (path.read_bytes(), 'image/gif'))
                self.assertEqual(self.decoded(raw).n_frames, 2)
            else:
                self.assert_invalid(ref, code='image_generation_reference_resize_unsupported')

    def test_resize_or_save_failure_never_falls_back_to_oversized_original(self):
        ref, _original = self.reference(size=(5000, 200))
        for method in ('thumbnail', 'save'):
            with self.subTest(method=method), patch.object(Image.Image, method, side_effect=OSError('fixture resize failure')):
                self.assert_invalid(ref, code='image_generation_reference_invalid')

    def test_missing_malformed_corrupt_or_non_image_reference_is_not_skipped(self):
        bad_path = self.root / 'corrupt.png'
        bad_path.write_bytes(b'not an image')
        svg = self.root / 'vector.png'
        svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        for ref in ({}, {'url': ''}, {'url': '/output/missing.png'}, {'url': '/output/../missing.png'}, {'url': '/output/corrupt.png'}, {'url': '/output/vector.png'}, {'url': str(bad_path)}, {'url': 'file:///private.png'}, {'url': 'data:image/png;base64,!!!'}, {'url': 'data:image/png;base64,'}, {'url': 'data:text/plain;base64,YWJj'}, {'url': 'data:image/png,abc'}):
            with self.subTest(ref=ref):
                self.assert_invalid(ref)
                with self.assertRaises((HTTPException, ProjectStoreError)):
                    conversation.gemini_generation_reference_part(ref)

    def test_truncated_png_and_jpeg_do_not_return_even_when_below_limit(self):
        for fmt, suffix in (('PNG', 'png'), ('JPEG', 'jpg')):
            ref, original = self.reference(f'truncated.{suffix}', fmt=fmt)
            (self.root / f'truncated.{suffix}').write_bytes(original[:len(original) // 2])
            self.assert_invalid(ref)

    def test_custom_max_edge_and_invalid_edges_are_explicit(self):
        ref, _original = self.reference(size=(512, 256))
        raw, _mime, _name = media.generation_reference_bytes(ref, max_edge=128)
        self.assertEqual(self.decoded(raw).size, (128, 64))
        url = media.generation_reference_to_data_url(ref, max_edge=128)
        self.assertEqual(self.decoded(base64.b64decode(url.split(',', 1)[1])).size, (128, 64))
        for edge in (0, -1, None, True, 1.5, '4096'):
            with self.subTest(edge=edge), self.assertRaises(ValueError):
                media.generation_reference_bytes(ref, max_edge=edge)

    def test_remote_generation_requires_verified_mime_without_changing_chat_uri(self):
        for url in ('https://example.invalid/reference.jpg?signature=fixture', 'http://example.invalid/reference.png', 'http://127.0.0.1:3199/output/reference.png'):
            for hint in (None, 'image/png', 'image/jpeg'):
                ref = {'url': url, 'mime_type': hint}
                with self.subTest(url=url, hint=hint):
                    self.assertEqual(media.generation_reference_to_data_url(ref), url)
                    with self.assertRaises(HTTPException) as rejected:
                        conversation.gemini_generation_reference_part(ref)
                    self.assertEqual(rejected.exception.status_code, 400)
                    self.assertEqual(rejected.exception.detail['code'], 'image_generation_reference_mime_unverified')
                    self.assertEqual(conversation.gemini_reference_part(ref), {'fileData': {'mimeType': 'image/png', 'fileUri': url}})
                    self.assert_invalid(ref)
        self.assertIsNone(conversation.gemini_generation_reference_part(None))

    def test_actual_project_media_resolver_enforces_project_scope(self):
        _ref, original = self.reference()
        store = ProjectStore(self.root / 'projects', snapshot_delay=0.01)
        self.addCleanup(store.close)
        project = store.create_project('reference fixture')
        record = store.import_media(project['id'], original, 'owned.png', 'image/png')
        handle = main.project_media_adapter_reference(project['id'], record['asset_id'])
        legacy = f"/api/project-library/{project['id']}/media/{record['asset_id']}"
        with patch.object(main, 'FIREFLY_PROJECT_STORE', store):
            for value in (handle, legacy):
                with self.subTest(value=value):
                    with self.assertRaises(ProjectStoreError):
                        media.generation_reference_bytes({'url': value})
                    token = main.PROJECT_MEDIA_ADAPTER_PROJECT.set('foreign-project')
                    try:
                        with self.assertRaises(ProjectStoreError):
                            media.generation_reference_bytes({'url': value})
                    finally:
                        main.PROJECT_MEDIA_ADAPTER_PROJECT.reset(token)
                    token = main.PROJECT_MEDIA_ADAPTER_PROJECT.set(project['id'])
                    try:
                        raw, mime, _name = media.generation_reference_bytes({'url': value})
                        self.assertEqual((raw, mime), (original, 'image/png'))
                    finally:
                        main.PROJECT_MEDIA_ADAPTER_PROJECT.reset(token)

    def test_actual_assets_and_storage_file_resolution_remains_supported(self):
        ref, original = self.reference()
        for value in ('/assets/reference.png', '/api/storage-files/generated/reference.png'):
            with self.subTest(value=value):
                raw, mime, _name = media.generation_reference_bytes({'url': value})
                self.assertEqual((raw, mime), (original, 'image/png'))
        self.assertEqual(media.generation_reference_bytes(ref)[0], original)

    def test_legacy_chat_video_helper_and_gemini_part_are_unchanged(self):
        for mode, fmt, suffix in (('RGB', 'PNG', 'png'), ('RGBA', 'PNG', 'png'), ('RGB', 'JPEG', 'jpg')):
            ref, original = self.reference('legacy.' + suffix, mode=mode, fmt=fmt)
            url = media.reference_to_data_url(ref, max_size=1536)
            result = self.decoded(base64.b64decode(url.split(',', 1)[1]))
            self.assertEqual(result.size, (1536, 768))
            self.assertEqual(result.format, 'PNG' if mode == 'RGBA' else 'JPEG')
            self.assertNotEqual(base64.b64decode(url.split(',', 1)[1]), original)
            part = conversation.gemini_reference_part(ref)['inlineData']
            self.assertEqual(part['data'], url.split(',', 1)[1])
        _ref, small = self.reference('small.jpg', size=(512, 256), fmt='JPEG', quality=93)
        legacy = media.reference_to_data_url({'url': '/output/small.jpg'}, max_size=1536)
        self.assertNotEqual(base64.b64decode(legacy.split(',', 1)[1]), small)

    def test_new_helpers_do_not_expand_legacy_main_namespace(self):
        for name in ('generation_reference_bytes', 'generation_reference_to_data_url', 'gemini_generation_reference_part'):
            self.assertFalse(hasattr(main, name), name)


if __name__ == '__main__':
    unittest.main()
