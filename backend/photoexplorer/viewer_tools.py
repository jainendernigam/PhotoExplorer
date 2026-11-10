"""Viewer helpers: full photo details (EXIF and colour profile) and the frames
of animated or multi-page pictures (GIF, WEBP, APNG, TIFF)."""
from __future__ import annotations

import io
import os
from fractions import Fraction

from PIL import Image, ImageCms, ImageOps, ExifTags

from .scanner.formats import long_path

ANIMATED_EXTS = {"gif", "webp", "png", "apng", "tif", "tiff"}
MAX_FRAMES = 2000

_FLASH = {0: "No flash", 1: "Flash fired", 5: "Flash fired, no return", 7: "Flash fired, return detected",
          9: "Flash, compulsory", 16: "No flash, compulsory off", 24: "No flash, auto", 25: "Flash, auto"}
_EXPOSURE_PROGRAM = {1: "Manual", 2: "Normal program", 3: "Aperture priority", 4: "Shutter priority",
                     5: "Creative", 6: "Action", 7: "Portrait", 8: "Landscape"}
_METERING = {1: "Average", 2: "Centre-weighted", 3: "Spot", 4: "Multi-spot", 5: "Pattern", 6: "Partial"}
_WB = {0: "Auto", 1: "Manual"}
_COLOR_SPACE = {1: "sRGB", 65535: "Uncalibrated"}


def _open(path: str) -> Image.Image:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return Image.open(long_path(path))


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _shutter(v) -> str | None:
    t = _num(v)
    if not t:
        return None
    if t >= 1:
        return f"{t:g} s"
    return f"1/{round(1 / t)} s"


def _text(v) -> str | None:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if v is None:
        return None
    v = str(v).replace("\x00", "").strip()
    return v or None


def details(path: str, kind: str) -> dict:
    """Readable camera settings, all EXIF tags, and the colour profile."""
    out: dict = {"settings": {}, "tags": {}, "profile": None, "format": None, "mode": None, "frames": 1}
    if kind == "video":
        return out
    try:
        im = _open(path)
    except Exception as e:                       # RAW and others Pillow can't open
        out["error"] = str(e)[:200]
        return out
    with im:
        out["format"], out["mode"] = im.format, im.mode
        out["frames"] = getattr(im, "n_frames", 1)
        icc = im.info.get("icc_profile")
        if icc:
            try:
                prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                out["profile"] = _text(ImageCms.getProfileDescription(prof)) or "Embedded profile"
            except Exception:
                out["profile"] = "Embedded profile"
        exif = im.getexif()
        tags: dict[str, str] = {}
        merged = dict(exif)
        for ifd in (0x8769, 0xA005):            # Exif and Interop sub-directories
            try:
                merged.update(exif.get_ifd(ifd))
            except Exception:
                pass
        for k, v in merged.items():
            name = ExifTags.TAGS.get(k)
            if not name or name in ("ExifOffset", "GPSInfo", "MakerNote", "UserComment", "PrintImageMatching",
                                    "InteropOffset") or isinstance(v, (bytes, tuple)) and len(str(v)) > 120:
                continue
            t = _text(v if not isinstance(v, tuple) else ", ".join(map(str, v)))
            if t:
                tags[name] = t[:200]
        out["tags"] = dict(sorted(tags.items()))
        g = lambda name: merged.get(next((k for k, n in ExifTags.TAGS.items() if n == name), -1))
        s = out["settings"]
        if (f := _num(g("FNumber"))):
            s["Aperture"] = f"f/{f:.1f}".replace(".0", "")
        if (sh := _shutter(g("ExposureTime"))):
            s["Shutter"] = sh
        if (iso := g("ISOSpeedRatings") or g("PhotographicSensitivity")):
            s["ISO"] = str(iso if not isinstance(iso, tuple) else iso[0])
        if (fl := _num(g("FocalLength"))):
            f35 = g("FocalLengthIn35mmFilm")
            s["Focal length"] = f"{fl:g} mm" + (f" ({f35} mm in 35 mm terms)" if f35 else "")
        if (eb := _num(g("ExposureBiasValue"))) is not None and eb:
            s["Exposure compensation"] = f"{Fraction(eb).limit_denominator(3)} EV"
        for name, table, label in (("Flash", _FLASH, "Flash"), ("ExposureProgram", _EXPOSURE_PROGRAM, "Mode"),
                                   ("MeteringMode", _METERING, "Metering"), ("WhiteBalance", _WB, "White balance"),
                                   ("ColorSpace", _COLOR_SPACE, "Colour space")):
            v = g(name)
            if v is not None and v in table:
                s[label] = table[v]
        if (sw := _text(g("Software"))):
            s["Software"] = sw
        if out["profile"]:
            s["Colour profile"] = out["profile"]
        s["Format"] = f"{im.format or '?'} · {im.mode}" + (f" · {out['frames']} frames" if out["frames"] > 1 else "")
    return out


# ---------------------------------------------------------------- frames


def frames_info(path: str) -> dict:
    with _open(path) as im:
        n = min(getattr(im, "n_frames", 1), MAX_FRAMES)
        durations = []
        if n > 1:
            for i in range(n):
                im.seek(i)
                durations.append(int(im.info.get("duration", 100) or 100))
        return {"count": n, "durations": durations, "width": im.width, "height": im.height,
                "loop": im.info.get("loop")}


def frame(path: str, n: int, max_side: int = 2048) -> Image.Image:
    with _open(path) as im:
        n = max(0, min(n, getattr(im, "n_frames", 1) - 1))
        im.seek(n)
        f = im.convert("RGBA")
    f.thumbnail((max_side, max_side))
    return f


def save_frames(path: str, out_dir: str, which: list[int] | None) -> list[str]:
    """Writes frames as PNG files into out_dir; which=None means all frames."""
    stem = os.path.splitext(os.path.basename(path))[0]
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    with _open(path) as im:
        total = min(getattr(im, "n_frames", 1), MAX_FRAMES)
        for i in (which if which is not None else range(total)):
            if 0 <= i < total:
                im.seek(i)
                p = os.path.join(out_dir, f"{stem} frame {i + 1:03d}.png")
                im.convert("RGBA").save(p)
                saved.append(p)
    return saved


# ---------------------------------------------------------------- loose files ("Open with")

LOOSE_EXTS = {"jpg", "jpeg", "jpe", "jfif", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif", "avif",
              "ico", "jxl"}


def folder_images(path: str) -> list[str]:
    """Pictures in the same folder as `path`, by name (for an opened file)."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = os.listdir(long_path(folder))
    except OSError:
        return [path]
    files = sorted((n for n in names if n.rsplit(".", 1)[-1].lower() in LOOSE_EXTS), key=str.lower)
    return [os.path.join(folder, n) for n in files] or [path]


def loose_preview(path: str, max_side: int) -> bytes:
    with _open(path) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((max_side, max_side))
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (0, 0, 0))
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3])
            im = bg
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=90)
        return buf.getvalue()
