"""User-scoped encrypted credentials for the uploader."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YaoWoUploader"
CREDENTIAL_PATH = APP_DIR / "credentials.dpapi"
_ENV_NAMES = {
    "bailian_api_key": "DASHSCOPE_API_KEY",
}


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _protect(data):
    if os.name != "nt":
        raise RuntimeError("加密凭据存储仅支持 Windows")
    source, source_buffer = _blob(data)
    encrypted = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "YaoWoUploader", None, None, None, 0,
        ctypes.byref(encrypted),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(encrypted.pbData, encrypted.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(encrypted.pbData)
        del source_buffer


def _unprotect(data):
    if os.name != "nt":
        raise RuntimeError("加密凭据存储仅支持 Windows")
    source, source_buffer = _blob(data)
    decrypted = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0,
        ctypes.byref(decrypted),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(decrypted.pbData)
        del source_buffer


def _load_all():
    if not CREDENTIAL_PATH.exists():
        return {}
    try:
        raw = _unprotect(CREDENTIAL_PATH.read_bytes())
        values = json.loads(raw.decode("utf-8"))
        return {
            str(key): str(value)
            for key, value in values.items()
            if value
        }
    except Exception as exc:
        raise RuntimeError(f"无法读取当前用户的上传器加密凭据：{exc}") from exc


def get_credential(name):
    env_name = _ENV_NAMES.get(name)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()
    return _load_all().get(name, "").strip()


def set_credential(name, value):
    value = str(value or "").strip()
    values = _load_all()
    if value:
        values[name] = value
    else:
        values.pop(name, None)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        values, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    temp_path = CREDENTIAL_PATH.with_suffix(".tmp")
    temp_path.write_bytes(_protect(payload))
    os.replace(temp_path, CREDENTIAL_PATH)
