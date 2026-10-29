"""Local settings with opt-in, current-Windows-user DPAPI credential protection."""

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


def directory():
    return (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "JiahuiShipmentAudit"
    )


def protect(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("仅 Windows 支持本机加密保存 Key；可不勾选保存直接使用。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("Windows 本机密钥保护失败，请取消保存后重试。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def load(path):
    config_path = path / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    key = ""
    if (path / "key.dpapi").exists():
        key = protect((path / "key.dpapi").read_bytes(), decrypt=True).decode("utf-8")
    return config, key


def save(path, config, key, remember):
    # Explicit whitelist: never serialize credentials into JSON.
    safe = {k: config[k] for k in ("base_url", "model", "instructions")}
    encrypted = protect(key.encode("utf-8")) if remember and key else None
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
    key_path = path / "key.dpapi"
    if encrypted:
        key_path.write_bytes(encrypted)
    else:
        key_path.unlink(missing_ok=True)
