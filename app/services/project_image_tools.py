"""Local still-image codecs for #514. No canvas, task, or Agent state enters here."""
from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageCms, ImageOps, PngImagePlugin

from local_edit import flip_image

MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_BATCH_BYTES = 4 * MAX_IMAGE_BYTES
_MAX_IMAGE_PIXELS = 40_000_000

_CODECS = {
    'PNG': ('.png', 'image/png', {'1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I;16', 'I;16B'}),
    'JPEG': ('.jpg', 'image/jpeg', {'L', 'RGB', 'CMYK'}),
    'WEBP': ('.webp', 'image/webp', {'RGB', 'RGBA'}),
}
_SRGB_CHROMATICITY = (0.3127, 0.329, 0.64, 0.33, 0.3, 0.6, 0.15, 0.06)
_CODEC_ERRORS = (OSError, ValueError, SyntaxError, TypeError, IndexError, KeyError, OverflowError, struct.error)


class ImageToolError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.detail = {'code': code, 'message': message}


def _open_image(stream: BinaryIO, *, browser_pixels: bool = False) -> Image.Image:
    header = stream.read(29)
    stream.seek(0)
    label = '浏览器像素' if browser_pixels else '源图片'
    formats = ('PNG',) if browser_pixels else (*_CODECS, 'GIF')
    image = None
    success = False
    try:
        image = Image.open(stream, formats=formats)
        if getattr(image, 'n_frames', 1) != 1 or getattr(image, 'is_animated', False):
            raise ImageToolError(415, 'multi_frame_image', f'{label}必须为静态单帧图片')
        if image.format not in _CODECS or image.mode not in _CODECS[image.format][2]:
            raise ImageToolError(415, 'unsupported_image_format', f'{label}格式或像素模式不支持')
        if image.width * image.height > _MAX_IMAGE_PIXELS:
            raise ImageToolError(413, 'image_too_large', f'{label}超过 4000 万像素限制')
        # Pillow decodes multichannel 16-bit PNG to 8-bit; don't let it silently lose samples.
        if image.format == 'PNG' and header[24] == 16 and (header[25] != 0 or browser_pixels):
            raise ImageToolError(415, 'unsupported_png_depth', f'{label}的 PNG 位深不支持')
        if browser_pixels:
            if image.mode not in ('RGB', 'RGBA') or 'transparency' in image.info:
                raise ImageToolError(415, 'invalid_pixels_format', '浏览器像素必须为 8 位 RGB/RGBA PNG')
            image.load()
            if image.getexif().get(274, 1) != 1:
                raise ImageToolError(415, 'invalid_pixels_orientation', '浏览器像素不能带有 EXIF 旋转方向')
        else:
            ImageOps.exif_transpose(image, in_place=True)
            if image.getexif().get(274, 1) != 1:
                raise ImageToolError(415, 'unsupported_image_orientation', '源图片的 EXIF 方向无效')
            if image.format == 'PNG' and image.mode == 'L' and header[24] in (2, 4) and 'transparency' in image.info:
                # Pillow expands grayscale samples to 8 bits, but leaves their tRNS key unscaled.
                image.info['transparency'] *= 255 // ((1 << header[24]) - 1)
        if 'icc_profile' in image.info and not image.info['icc_profile']:
            raise ImageToolError(415, 'invalid_image_profile', f'{label}的 ICC 无法读取')
        success = True
        return image
    except Image.DecompressionBombError as exc:
        raise ImageToolError(413, 'image_too_large', f'{label}超过安全像素限制') from exc
    except _CODEC_ERRORS as exc:
        if isinstance(exc, OSError) and exc.errno is not None:
            raise
        raise ImageToolError(415, 'unsupported_image_format', f'{label}无法解码或格式不支持') from exc
    finally:
        if image is not None and not success:
            image.close()


def _encoding_options(source: Image.Image, *, color: bool) -> dict:
    info = source.info
    options = {key: info[key] for key in ('icc_profile', 'exif') if info.get(key)}
    if 'exif' not in options and 'Raw profile type exif' in info:
        options['exif'] = source.getexif().tobytes()
    if source.format in ('PNG', 'JPEG') and info.get('dpi'):
        options['dpi'] = info['dpi']
    if source.format == 'PNG':
        chunks = PngImagePlugin.PngInfo()
        if color:
            # Color upload samples are in sRGB, not in any unprofiled source gAMA/cHRM space.
            if 'icc_profile' not in options and any(key in info for key in ('srgb', 'gamma', 'chromaticity')):
                chunks.add(b'sRGB', b'\0')
        else:
            if 'srgb' in info and 'icc_profile' not in options:
                chunks.add(b'sRGB', struct.pack('>B', info['srgb']))
            if 'gamma' in info:
                chunks.add(b'gAMA', struct.pack('>I', round(info['gamma'] * 100000)))
            if 'chromaticity' in info:
                chunks.add(b'cHRM', struct.pack('>8I', *(round(value * 100000) for value in info['chromaticity'])))
            if 'transparency' in info:
                transparency = info['transparency']
                # PNG's 1-bit tRNS key is a raw sample; Pillow exposes it as 0 or 255.
                options['transparency'] = int(bool(transparency)) if source.mode == '1' else transparency
        options['pnginfo'] = chunks
    elif source.format == 'JPEG':
        options.update(quality=100, subsampling=0)
        if 'comment' in info:
            options['comment'] = info['comment']
    elif source.format == 'WEBP':
        # exact=True keeps even the RGB samples behind fully transparent pixels.
        options.update(lossless=True, exact=True)
    return options


def _encode(source: Image.Image, result: Image.Image, target: Path, *, color: bool) -> None:
    try:
        options = _encoding_options(source, color=color)
        if color:
            # Some encoders inherit unspecified metadata from the image being written.
            result.info.clear()
        with target.open('wb') as output:
            result.save(output, format=source.format, **options)
    except _CODEC_ERRORS as exc:
        if isinstance(exc, OSError) and exc.errno is not None:
            raise
        raise ImageToolError(415, 'unsupported_image_encoding', '无法用源图片格式安全编码') from exc
    if target.stat().st_size > MAX_IMAGE_BYTES:
        raise ImageToolError(413, 'image_too_large', '编码结果超过 64 MiB 限制')


def _check_browser_srgb(pixels: Image.Image, rgb: Image.Image, srgb: ImageCms.ImageCmsProfile) -> None:
    info = pixels.info
    if 'icc_profile' in info:
        try:
            embedded = ImageCms.getOpenProfile(io.BytesIO(info['icc_profile'] or b''))
            # ICC descriptions are not authoritative. Require that the actual upload
            # samples are already sRGB under color-managed decoding.
            with ImageCms.profileToProfile(rgb, embedded, srgb, outputMode='RGB') as converted:
                if converted.tobytes() != rgb.tobytes():
                    raise ImageToolError(415, 'pixels_not_srgb', '浏览器像素的 ICC 不是 sRGB')
        except (ImageCms.PyCMSError, OSError, ValueError, TypeError) as exc:
            raise ImageToolError(415, 'pixels_not_srgb', '浏览器像素的 ICC 无法识别为 sRGB') from exc
    elif 'srgb' not in info and (info.get('gamma', 0.45455) != 0.45455 or
                                info.get('chromaticity', _SRGB_CHROMATICITY) != _SRGB_CHROMATICITY):
        raise ImageToolError(415, 'pixels_not_srgb', '浏览器像素必须在 sRGB 色彩空间')


def _color_result(source: Image.Image, pixels: Image.Image) -> Image.Image:
    if source.size != pixels.size:
        raise ImageToolError(422, 'pixel_dimensions_mismatch', '浏览器像素尺寸必须与源图片视觉尺寸相同')
    if source.mode.startswith('I;16'):
        raise ImageToolError(415, 'unsupported_png_depth', '调色不支持 16 位源图片')
    has_alpha = pixels.mode == 'RGBA' and source.format != 'JPEG'
    if pixels.mode == 'RGBA' and source.format == 'JPEG' and pixels.getchannel('A').getextrema() != (255, 255):
        raise ImageToolError(415, 'unsupported_pixels_alpha', 'JPEG 不支持透明浏览器像素')
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB'))
    with pixels.convert('RGB') as rgb:
        _check_browser_srgb(pixels, rgb, srgb)
        if 'icc_profile' not in source.info:
            if source.mode == 'CMYK':
                raise ImageToolError(415, 'unsupported_icc_conversion', '无 ICC 的 CMYK 源图片不支持调色')
            return pixels.copy() if has_alpha else rgb.copy()
        try:
            profile = ImageCms.getOpenProfile(io.BytesIO(source.info['icc_profile'] or b''))
            profile_space = profile.profile.xcolor_space.strip()
            source_space = ('GRAY' if source.mode in ('1', 'L', 'LA') else
                            'CMYK' if source.mode == 'CMYK' else 'RGB')
            output_mode = {'GRAY': 'L', 'RGB': 'RGB', 'CMYK': 'CMYK'}[profile_space]
            if source_space != profile_space:
                raise ValueError('ICC color space does not match source samples')
            converted = ImageCms.profileToProfile(rgb, srgb, profile, outputMode=output_mode)
        except (ImageCms.PyCMSError, OSError, ValueError, TypeError, KeyError) as exc:
            raise ImageToolError(415, 'unsupported_icc_conversion', '无法将 sRGB 像素转换到源图片 ICC') from exc
    if has_alpha:
        converted.putalpha(pixels.getchannel('A'))
    return converted


def render_image(source_path: Path, target: Path, *, direction: str = '', pixels: BinaryIO | None = None) -> tuple[str, str]:
    """Encode a verified still image to staging before any project media is archived."""
    with source_path.open('rb') as stream, _open_image(stream) as source:
        if pixels is None:
            with flip_image(source, direction, preserve_mode=True) as flipped:
                _encode(source, flipped, target, color=False)
        else:
            with _open_image(pixels, browser_pixels=True) as uploaded, _color_result(source, uploaded) as result:
                _encode(source, result, target, color=True)
        suffix, mime, _ = _CODECS[source.format]
        return suffix, mime