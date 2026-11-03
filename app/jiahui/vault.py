"""Store only the user's explicitly configured DeepSeek key in Windows Credential Manager."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from pathlib import Path


class SessionVault:
    persistent = False

    def read(self) -> str:
        return ""

    def write(self, value: str) -> None:
        raise OSError("当前系统不支持Windows凭据管理器，可仅在本次运行中使用Key。")


class WindowsVault:
    persistent = True

    def __init__(self, home: Path):
        self.target = (
            "JiahuiAssistant/DeepSeek/"
            + hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:24]
        )

    @staticmethod
    def _api():
        if os.name != "nt":
            raise OSError("Windows Credential Manager is unavailable")

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        api.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        api.CredReadW.restype = wintypes.BOOL
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        return api, Credential

    def read(self) -> str:
        api, credential_type = self._api()
        pointer = ctypes.POINTER(credential_type)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # type: ignore[attr-defined]  # Windows-only API
                return ""
            raise OSError("无法读取Windows凭据，请检查当前Windows账号。")
        try:
            return ctypes.string_at(
                pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize
            ).decode("utf-8")
        finally:
            api.CredFree(pointer)

    def write(self, value: str) -> None:
        if not value or len(value.encode("utf-8")) > 4096:
            raise ValueError("Key长度无效")
        api, credential_type = self._api()
        blob = (wintypes.BYTE * len(value.encode("utf-8"))).from_buffer_copy(value.encode("utf-8"))
        credential = credential_type()
        credential.Type = 1
        credential.TargetName = self.target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE: current Windows user, survives restart
        credential.UserName = "DeepSeek"
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise OSError("无法保存到Windows凭据管理器，Key没有写入普通文件。")

    def delete(self) -> None:
        api, _ = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:  # type: ignore[attr-defined]
            raise OSError("无法删除Windows凭据")


def credential_vault(home: Path):
    return WindowsVault(home) if os.name == "nt" else SessionVault()
