"""Capture-date parsing.

All dates are stored as "wall-clock seconds": the local date/time the photo
was taken, encoded as if it were UTC. This sorts correctly and shows the same
time the camera showed, regardless of the PC's time zone.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone

_MIN_YEAR = 1900


def wall_seconds(dt: datetime) -> int:
    return calendar.timegm(dt.replace(tzinfo=None).timetuple())


def wall_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _valid(dt: datetime) -> bool:
    return _MIN_YEAR <= dt.year <= datetime.now().year + 1


def parse_exif_datetime(value) -> int | None:
    """'2019:05:12 10:20:30' (EXIF) -> wall seconds. Tolerates junk/blank."""
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    s = str(value).strip().strip("\x00")
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):?(\d{2})?", s)
    if not m:
        return None
    try:
        dt = datetime(*(int(x) for x in m.groups(default="0")))
    except ValueError:
        return None
    return wall_seconds(dt) if _valid(dt) else None


def parse_iso_datetime(value: str | None) -> tuple[int, str | None] | None:
    """Video tags. With an offset ('+0530') keep the wall clock; with 'Z'
    (UTC, typical for MP4 creation_time) convert to this PC's local time."""
    if not value:
        return None
    s = value.strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", s)
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    except ValueError:
        return None
    tz = m.group(3)
    offset = None
    if tz == "Z":
        dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    elif tz:
        tz = tz.replace(":", "")
        offset = f"{tz[:3]}:{tz[3:]}"
    if not _valid(dt) or dt.year < 1971:   # 1904/1970 = "unset" in many cameras
        return None
    return wall_seconds(dt), offset


# IMG_20190512_103045.jpg, IMG-20190512-WA0001.jpg, 2019-05-12 10.30.45.jpg,
# PXL_20230101_101010123.jpg, Screenshot_2020-01-02-03-04-05.png
_FILENAME_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?"
)


def from_filename(name: str) -> int | None:
    m = _FILENAME_DATE.search(name)
    if not m:
        return None
    try:
        dt = datetime(*(int(x) for x in m.groups(default="0")))
    except ValueError:
        return None
    return wall_seconds(dt) if _valid(dt) else None


def from_mtime_ns(mtime_ns: int) -> int:
    return wall_seconds(datetime.fromtimestamp(mtime_ns / 1e9))
