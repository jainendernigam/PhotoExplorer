"""Small secrets (Google sign-in token, client secret) at rest.

Windows: encrypted with DPAPI for the current Windows user (only this user on
this PC can decrypt). Elsewhere: a file readable only by the owner.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("Windows could not " + ("encrypt" if protect else "decrypt") + " the secret")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = _dpapi(data, True) if sys.platform == "win32" else data
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    blob = path.read_bytes()
    return _dpapi(blob, False) if sys.platform == "win32" else blob
