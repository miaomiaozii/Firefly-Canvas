"""Local secret encryption for provider keys and env secrets.

Uses Windows DPAPI (CryptProtectData/CryptUnprotectData) via ctypes, so
secrets are encrypted at rest with the current Windows user's key and can
only be decrypted by the same user on the same machine. Plain values are
also accepted and pass through untouched, which keeps existing config files
readable and makes the migration to encrypted storage seamless.

Stored format: ``dpapi:<base64(ciphertext)>``. A value starting with
``dpapi:`` is expected to be encrypted; anything else is treated as plain.
"""
import base64
import ctypes
import ctypes.wintypes
import os
import sys

_PREFIX = 'dpapi:'

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_LOCAL_MACHINE = 0x4


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ('cbData', ctypes.wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_char)),
    ]


def _crypt_protect(data: bytes) -> bytes:
    blob_in = _DataBlob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)
    ):
        raise OSError(f'CryptProtectData failed (error {ctypes.get_last_error()})')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _crypt_unprotect(data: bytes) -> bytes:
    blob_in = _DataBlob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)
    ):
        raise OSError(f'CryptUnprotectData failed (error {ctypes.get_last_error()})')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt_secret(value: str) -> str:
    """Encrypt a secret string for at-rest storage on this machine/user."""
    if value is None:
        return None
    text = str(value)
    if not text or text.startswith(_PREFIX):
        return text
    try:
        ciphertext = _crypt_protect(text.encode('utf-8'))
    except (OSError, AttributeError, ImportError):
        return text
    return f'{_PREFIX}{base64.b64encode(ciphertext).decode("ascii")}'


def decrypt_secret(value) -> str:
    """Decrypt a stored secret; plain values pass through unchanged."""
    if value is None:
        return None
    text = str(value)
    if not text.startswith(_PREFIX):
        return text
    try:
        payload = base64.b64decode(text[len(_PREFIX):])
        plaintext = _crypt_unprotect(payload)
    except (OSError, ValueError, AttributeError, ImportError):
        return text
    try:
        return plaintext.decode('utf-8')
    except UnicodeDecodeError:
        return text


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_env_value(value: str) -> str:
    return encrypt_secret(value)


def decrypt_env_value(value: str) -> str:
    return decrypt_secret(value)
