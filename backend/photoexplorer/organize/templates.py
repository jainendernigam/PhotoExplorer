"""Folder and file-name patterns, e.g. "{person}/{year}/{year}-{month}"."""
from __future__ import annotations

import re
from datetime import datetime, timezone

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

TOKENS = {
    "year": "2019", "month": "06", "month_name": "June", "day": "25",
    "date": "2019-06-25", "time": "120000",
    "person": "a named person in the photo (one folder per person)",
    "camera": "Canon EOS 80D", "folder": "the photo's current folder name",
    "source": "the library folder it came from", "kind": "image / raw / video",
    "name": "the original file name without extension",
}

FOLDER_PRESETS = {
    "year_month": ("Year / Year-Month", "{year}/{year}-{month}"),
    "year": ("Year", "{year}"),
    "year_month_name": ("Year / Month name", "{year}/{month} {month_name}"),
    "person_year": ("Person / Year", "{person}/{year}"),
    "person_year_month": ("Person / Year / Year-Month", "{person}/{year}/{year}-{month}"),
    "camera_year": ("Camera / Year", "{camera}/{year}"),
    "flat": ("All in one folder", ""),
}
NAME_PRESETS = {
    "original": ("Keep original names", "{name}"),
    "date_time": ("Date and time first (sorts by date)", "{date} {time} {name}"),
}

_TOKEN = re.compile(r"\{(\w+)\}")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate(template: str, allow_slash: bool = True) -> None:
    for t in _TOKEN.findall(template):
        if t not in TOKENS:
            raise ValueError(f"Unknown pattern part {{{t}}}. Use: " + ", ".join("{%s}" % k for k in TOKENS))
    if ".." in re.split(r"[\\/]", template):
        raise ValueError("Patterns cannot contain '..'")
    if not allow_slash and re.search(r"[\\/]", template):
        raise ValueError("File-name pattern cannot contain folders")
    if re.match(r"^([A-Za-z]:|[\\/])", template):
        raise ValueError("Pattern must be relative (no drive letter)")


def safe_segment(s: str, limit: int = 100) -> str:
    """One folder or file name that Windows, macOS and Linux all accept."""
    s = _BAD.sub("_", s).strip().rstrip(". ")
    s = re.sub(r"\s+", " ", s)[:limit].rstrip(". ") or "_"
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s


def context(row: dict, person: str | None) -> dict:
    ts = row.get("taken_at")
    if ts is None:
        d = None
    else:
        d = datetime.fromtimestamp(ts, tz=timezone.utc)
    camera = " ".join(x for x in (row.get("camera_make"), row.get("camera_model")) if x)
    # "Canon Canon EOS 80D" -> "Canon EOS 80D"
    if row.get("camera_make") and (row.get("camera_model") or "").lower().startswith(row["camera_make"].lower()):
        camera = row["camera_model"]
    path = row["path"].replace("\\", "/")
    name = row["filename"]
    return {
        "year": f"{d:%Y}" if d else "Unknown date",
        "month": f"{d:%m}" if d else "00",
        "month_name": MONTHS[d.month - 1] if d else "Unknown",
        "day": f"{d:%d}" if d else "00",
        "date": f"{d:%Y-%m-%d}" if d else "unknown-date",
        "time": f"{d:%H%M%S}" if d else "000000",
        "person": person or "",
        "camera": camera or "Unknown camera",
        "folder": path.rsplit("/", 2)[-2] if "/" in path else "",
        "source": row.get("source_label") or row.get("source_path", "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1],
        "kind": row.get("kind", ""),
        "name": name.rsplit(".", 1)[0] if "." in name else name,
    }


def render_folder(template: str, ctx: dict) -> list[str]:
    text = _TOKEN.sub(lambda m: ctx.get(m.group(1), ""), template)
    return [safe_segment(p) for p in re.split(r"[\\/]+", text) if p.strip()]


def render_name(template: str, ctx: dict, ext: str) -> str:
    stem = _TOKEN.sub(lambda m: ctx.get(m.group(1), ""), template or "{name}").strip() or ctx["name"]
    return safe_segment(stem, 150) + (f".{ext}" if ext else "")
