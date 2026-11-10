"""Per-file work, run inside worker processes.

``process(job)`` hashes the file, reads metadata, computes perceptual hashes
and writes a thumbnail. It never touches the database: it returns a dict that
the scan manager writes in batches from a single writer.
"""
from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageOps

from . import dates
from .formats import long_path
from .hashing import bands, dhash, file_sha256, phash, to_signed64

_cfg: dict = {"thumb_dir": None, "thumb_size": 320, "ffmpeg": None, "ffprobe": None}
_HEIF = False
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def init_worker(thumb_dir: str, thumb_size: int, ffmpeg: str | None, ffprobe: str | None,
                low_priority: bool = True) -> None:
    global _HEIF
    _cfg.update(thumb_dir=thumb_dir, thumb_size=thumb_size, ffmpeg=ffmpeg, ffprobe=ffprobe)
    Image.MAX_IMAGE_PIXELS = 400_000_000  # big panoramas are legitimate
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        _HEIF = True
    except ImportError:
        pass
    if low_priority:
        _lower_priority()


def _lower_priority() -> None:
    """Keep the UI responsive while all cores are busy."""
    try:
        if sys.platform == "win32":
            BELOW_NORMAL = 0x00004000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(10)
    except Exception:
        pass


# ---------------------------------------------------------------- entry point

def process(job: tuple[int, str, str, int]) -> dict:
    media_id, path, kind, mtime_ns = job
    out: dict = {"id": media_id}
    try:
        digest, sampled = file_sha256(path)
        out["sha256"] = digest
        out["hash_sampled"] = int(sampled)
        if kind == "video":
            out.update(_video(path, digest))
        elif kind == "raw":
            out.update(_raw(path, digest))
        else:
            out.update(_image(path, digest))
    except Exception as e:  # one bad file must never stop the scan
        out["error"] = f"{type(e).__name__}: {e}"[:500]

    if not out.get("taken_at"):
        ts = dates.from_filename(os.path.basename(path))
        if ts:
            out["taken_at"], out["date_source"] = ts, "filename"
        else:
            out["taken_at"], out["date_source"] = dates.from_mtime_ns(mtime_ns), "mtime"
    return out


# ---------------------------------------------------------------- helpers

from .colors import color_of


def _thumb_path(digest: bytes) -> Path:
    h = digest.hex()
    return Path(_cfg["thumb_dir"]) / h[:2] / h[2:4] / f"{h}.webp"


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def _visual(img: Image.Image, digest: bytes) -> dict:
    """pHash/dHash + thumbnail from an already-oriented RGB image."""
    p = phash(img)
    res = {
        "phash": to_signed64(p),
        "dhash": to_signed64(dhash(img)),
    }
    res["ph0"], res["ph1"], res["ph2"], res["ph3"] = bands(p)
    tp = _thumb_path(digest)
    if not tp.exists():  # identical files share one thumbnail
        size = _cfg["thumb_size"]
        t = img.copy()
        t.thumbnail((size, size), Image.Resampling.BICUBIC, reducing_gap=2.0)
        tp.parent.mkdir(parents=True, exist_ok=True)
        # Identical files can be processed by two workers at once: unique temp
        # name per process, and losing the rename race is fine.
        tmp = tp.with_suffix(f".{os.getpid()}.tmp")
        t.save(tmp, "WEBP", quality=78, method=4)
        try:
            os.replace(tmp, tp)
        except PermissionError:
            if not tp.exists():
                raise
            tmp.unlink(missing_ok=True)
    res["has_thumb"] = 1
    res["color"] = color_of(img)
    return res


def _clean(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    s = str(v).replace("\x00", "").strip()
    return s or None


def _gps_coord(values, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in values)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    v = d + m / 60 + s / 3600
    if _clean(ref) in ("S", "W"):
        v = -v
    return v


def _exif_fields(exif: Image.Exif) -> dict:
    d: dict = {}
    if not exif:
        return d
    ifd = exif.get_ifd(0x8769)  # Exif sub-IFD
    ts = (dates.parse_exif_datetime(ifd.get(0x9003))      # DateTimeOriginal
          or dates.parse_exif_datetime(ifd.get(0x9004))   # DateTimeDigitized
          or dates.parse_exif_datetime(exif.get(0x0132)))  # DateTime
    if ts:
        d["taken_at"], d["date_source"] = ts, "exif"
        d["tz_offset"] = _clean(ifd.get(0x9011))
    d["camera_make"] = _clean(exif.get(0x010F))
    d["camera_model"] = _clean(exif.get(0x0110))
    d["lens"] = _clean(ifd.get(0xA434))
    gps = exif.get_ifd(0x8825)
    if gps:
        lat, lon = _gps_coord(gps.get(2), gps.get(1)), _gps_coord(gps.get(4), gps.get(3))
        if lat is not None and lon is not None and (abs(lat) > 1e-6 or abs(lon) > 1e-6) \
                and abs(lat) <= 90 and abs(lon) <= 180:
            d["gps_lat"], d["gps_lon"] = lat, lon
            try:
                alt = float(gps.get(6))
                d["gps_alt"] = -alt if gps.get(5) in (1, b"\x01") else alt
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    return d


# ---------------------------------------------------------------- per kind

def _image(path: str, digest: bytes) -> dict:
    with Image.open(long_path(path)) as im:
        w, h = im.size
        exif = im.getexif()
        res = _exif_fields(exif)
        orientation = exif.get(0x0112, 1) if exif else 1
        if orientation in (5, 6, 7, 8):
            w, h = h, w
        res["width"], res["height"] = w, h
        if im.format == "JPEG":
            # Decode at reduced scale straight from the DCT: 4-8x faster.
            ts = _cfg["thumb_size"]
            im.draft("RGB", (ts * 2, ts * 2))
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        img = ImageOps.exif_transpose(im)
        res.update(_visual(_to_rgb(img), digest))
    return res


def _raw(path: str, digest: bytes) -> dict:
    res = _raw_exif(path)
    img, w, h = _raw_preview(path)
    res["width"], res["height"] = w, h
    res.update(_visual(_to_rgb(img), digest))
    return res


def load_rgb(path: str, kind: str, max_side: int) -> Image.Image:
    """Oriented RGB image no larger than ``max_side`` (for face detection)."""
    if kind == "raw":
        img = _raw_preview(path)[0]
    else:
        with Image.open(long_path(path)) as im:
            if im.format == "JPEG":
                im.draft("RGB", (max_side, max_side))
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            img = ImageOps.exif_transpose(im)
    img = _to_rgb(img)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.BICUBIC, reducing_gap=2.0)
    return img


def _raw_preview(path: str) -> tuple[Image.Image, int, int]:
    """(oriented preview image, full width, full height) of a RAW file."""
    try:
        import rawpy
    except ImportError:
        raise RuntimeError("RAW support not installed (pip install rawpy exifread)")
    with rawpy.imread(long_path(path)) as raw:
        w, h, flip = raw.sizes.width, raw.sizes.height, raw.sizes.flip
        img = None
        try:
            th = raw.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(th.data))
                img.load()
                if img.getexif().get(0x0112, 1) == 1:
                    img = {3: lambda i: i.rotate(180), 5: lambda i: i.rotate(90, expand=True),
                           6: lambda i: i.rotate(-90, expand=True)}.get(flip, lambda i: i)(img)
                else:
                    img = ImageOps.exif_transpose(img)
            else:
                img = Image.fromarray(th.data)
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            pass
        if img is None:  # no embedded preview: develop at half size (slow)
            img = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True))
    if flip in (5, 6):
        w, h = h, w
    return img, w, h


def _raw_exif(path: str) -> dict:
    try:
        import exifread
    except ImportError:
        return {}
    with open(long_path(path), "rb") as f:
        tags = exifread.process_file(f, details=False)
    d: dict = {}
    ts = dates.parse_exif_datetime(str(tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime") or ""))
    if ts:
        d["taken_at"], d["date_source"] = ts, "exif"
    d["camera_make"] = _clean(tags.get("Image Make"))
    d["camera_model"] = _clean(tags.get("Image Model"))
    d["lens"] = _clean(tags.get("EXIF LensModel"))
    lat, lon = tags.get("GPS GPSLatitude"), tags.get("GPS GPSLongitude")
    if lat and lon:
        la = _gps_coord([float(x.num) / float(x.den or 1) for x in lat.values], tags.get("GPS GPSLatitudeRef"))
        lo = _gps_coord([float(x.num) / float(x.den or 1) for x in lon.values], tags.get("GPS GPSLongitudeRef"))
        if la is not None and lo is not None:
            d["gps_lat"], d["gps_lon"] = la, lo
    return d


def _run(cmd: list[str], timeout: int = 60) -> bytes | None:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _probe_with_ffmpeg(ffmpeg: str, path: str) -> dict | None:
    """The parts of ffprobe's JSON that _video uses, read from `ffmpeg -i`'s report
    (the portable app ships ffmpeg only, to keep the download small)."""
    import re
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, timeout=60,
                           creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = r.stderr.decode("utf-8", errors="replace")
    if "Input #0" not in text:
        return None
    fmt_tags: dict = {}
    streams: list[dict] = []
    duration = None
    block = None                                    # where "key : value" lines currently go
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if s.startswith("Duration:"):
            m = re.match(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", s)
            if m:
                duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            block = None
        elif s.startswith("Stream #0:"):
            kind = re.search(r": (Video|Audio|Subtitle|Data): (\w+)", s)
            st = {"codec_type": kind.group(1).lower() if kind else "data",
                  "codec_name": kind.group(2) if kind else None, "tags": {}, "side_data_list": []}
            size = re.search(r", (\d{2,5})x(\d{2,5})\b", s)
            if size and st["codec_type"] == "video":
                st["width"], st["height"] = int(size.group(1)), int(size.group(2))
            streams.append(st)
            block = None
        elif s == "Metadata:":
            block = fmt_tags if indent == 2 else (streams[-1]["tags"] if streams else None)
        elif re.match(r"display ?matrix:", s, re.I) and streams:      # "displaymatrix:" / "Display Matrix:"
            m = re.search(r"rotation of (-?\d+(?:\.\d+)?)", s)
            if m:
                streams[-1]["side_data_list"].append({"rotation": int(round(float(m.group(1))))})
        elif block is not None and ":" in s:
            k, v = s.split(":", 1)
            block[k.strip()] = v.strip()
    return {"format": {"duration": duration, "tags": fmt_tags}, "streams": streams}


def _video(path: str, digest: bytes) -> dict:
    res: dict = {}
    ffprobe, ffmpeg = _cfg["ffprobe"], _cfg["ffmpeg"]
    duration = 0.0
    info = None
    if ffprobe:
        raw = _run([ffprobe, "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", long_path(path)])
        info = json.loads(raw) if raw else None
    elif ffmpeg:
        info = _probe_with_ffmpeg(ffmpeg, long_path(path))
    if info:
        fmt = info.get("format", {})
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        duration = float(fmt.get("duration") or 0)
        res["duration_ms"] = int(duration * 1000)
        parsed = (dates.parse_iso_datetime(tags.get("com.apple.quicktime.creationdate"))
                  or dates.parse_iso_datetime(tags.get("creation_time")))
        if parsed:
            res["taken_at"], res["tz_offset"] = parsed
            res["date_source"] = "video"
        loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
        if loc:
            import re
            m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc)
            if m:
                res["gps_lat"], res["gps_lon"] = float(m.group(1)), float(m.group(2))
        v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
        if v:
            w, h = v.get("width"), v.get("height")
            rot = int((v.get("tags") or {}).get("rotate", 0) or 0)
            for sd in v.get("side_data_list") or []:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            if abs(rot) in (90, 270) and w and h:
                w, h = h, w
            res["width"], res["height"] = w, h
            res["video_codec"] = v.get("codec_name")
    if ffmpeg:
        at = f"{min(1.0, duration / 2):.2f}" if duration else "0"
        png = _run([ffmpeg, "-v", "error", "-ss", at, "-i", long_path(path), "-frames:v", "1",
                    "-vf", "scale='min(640,iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"])
        if png:
            with Image.open(io.BytesIO(png)) as frame:
                res.update(_visual(_to_rgb(frame), digest))
    return res
