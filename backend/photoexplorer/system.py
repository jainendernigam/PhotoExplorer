"""Small Windows integrations.

* open_file: open a file in its default program (the PDF viewer for prints).
* "Open with Photo Explorer": adds Photo Explorer to Explorer's "Open with"
  list for pictures (current user only, under HKCU\\Software\\Classes). Windows
  then lets the user pick it, or make it the default, from "Open with" or
  Settings > Default apps; programs can't set the default themselves. Turning
  it off removes exactly what was added.
"""
from __future__ import annotations

import os
import subprocess
import sys

PROGID = "PhotoExplorer.Image"
EXTS = [".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"]


class SystemError_(Exception):
    pass


def open_file(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - our own PDF
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _exe() -> str | None:
    p = os.environ.get("PHOTOEXPLORER_EXE")
    return p if p and os.path.isfile(p) else None


def _command(exe: str) -> str:
    return f'"{exe}" "%1"'


def open_with_status() -> dict:
    if sys.platform != "win32":
        return {"available": False, "enabled": False, "reason": "Only on Windows"}
    exe = _exe()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}\shell\open\command") as k:
            cmd = winreg.QueryValue(k, None)
    except OSError:
        cmd = None
    return {"available": exe is not None, "enabled": bool(cmd) and exe is not None and cmd == _command(exe),
            "other_copy": bool(cmd) and (exe is None or cmd != _command(exe)),
            "reason": None if exe else "Only in the installed app (not in the test version)"}


def set_open_with(enable: bool) -> dict:
    if sys.platform != "win32":
        raise SystemError_("Only on Windows")
    import winreg
    HK = winreg.HKEY_CURRENT_USER
    base = r"Software\Classes"
    exe = _exe()
    name = os.path.basename(exe) if exe else "PhotoExplorer.exe"
    if enable:
        if not exe:
            raise SystemError_("Only in the installed app (not in the test version)")
        with winreg.CreateKey(HK, rf"{base}\{PROGID}") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, "Photo Explorer picture")
        with winreg.CreateKey(HK, rf"{base}\{PROGID}\DefaultIcon") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, f'"{exe}",0')
        with winreg.CreateKey(HK, rf"{base}\{PROGID}\shell\open\command") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, _command(exe))
        with winreg.CreateKey(HK, rf"{base}\Applications\{name}\shell\open\command") as k:
            winreg.SetValue(k, None, winreg.REG_SZ, _command(exe))
        with winreg.CreateKey(HK, rf"{base}\Applications\{name}") as k:
            winreg.SetValueEx(k, "FriendlyAppName", 0, winreg.REG_SZ, "Photo Explorer")
        for ext in EXTS:
            with winreg.CreateKey(HK, rf"{base}\{ext}\OpenWithProgids") as k:
                winreg.SetValueEx(k, PROGID, 0, winreg.REG_SZ, "")
            with winreg.CreateKey(HK, rf"{base}\Applications\{name}\SupportedTypes") as k:
                winreg.SetValueEx(k, ext, 0, winreg.REG_SZ, "")
    else:
        for ext in EXTS:
            try:
                with winreg.OpenKey(HK, rf"{base}\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, PROGID)
            except OSError:
                pass
        for key in (rf"{PROGID}\shell\open\command", rf"{PROGID}\shell\open", rf"{PROGID}\shell",
                    rf"{PROGID}\DefaultIcon", PROGID,
                    rf"Applications\{name}\shell\open\command", rf"Applications\{name}\shell\open",
                    rf"Applications\{name}\shell", rf"Applications\{name}\SupportedTypes", rf"Applications\{name}"):
            try:
                winreg.DeleteKey(HK, rf"{base}\{key}")
            except OSError:
                pass
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except Exception:
        pass
    return open_with_status()
