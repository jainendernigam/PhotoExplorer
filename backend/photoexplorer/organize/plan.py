"""Work out where every photo goes, before touching anything."""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from ..scanner.formats import long_path
from ..scanner.hashing import file_sha256
from . import templates


@dataclass
class OrganizeOptions:
    folder_template: str = "{year}/{year}-{month}"
    name_template: str = "{name}"
    persons: list[int] | None = None   # with {person}: only these people's folders (default: everyone named)
    no_person: str = "Other photos"    # folder for photos without any of those people
    include_videos: bool = True

    def validate(self) -> None:
        templates.validate(self.folder_template)
        templates.validate(self.name_template, allow_slash=False)


@dataclass
class PlanItem:
    media_id: int
    src: str
    dest: str
    size: int
    sha256: bytes | None
    status: str = "new"          # new | exists (identical file already there) | same (duplicate inside this plan)


@dataclass
class Plan:
    items: list[PlanItem] = field(default_factory=list)
    trimmed_people: int = 0      # photos placed in only the first of several people's folders (move)

    @property
    def new(self) -> list[PlanItem]:
        return [i for i in self.items if i.status == "new"]


def _rows(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        out += [dict(r) for r in conn.execute(
            "SELECT m.id, m.path, m.filename, m.ext, m.kind, m.size, m.sha256, m.taken_at, m.camera_make, "
            "m.camera_model, s.path AS source_path, s.label AS source_label "
            f"FROM media m JOIN sources s ON s.id = m.source_id WHERE m.status = 1 AND m.id IN ({','.join('?' * len(chunk))})",
            chunk)]
    out.sort(key=lambda r: (r["taken_at"] or 0, r["id"]))
    return out


def _people(conn: sqlite3.Connection, ids: list[int], only: list[int] | None) -> dict[int, list[str]]:
    res: dict[int, list[str]] = defaultdict(list)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        q = ("SELECT DISTINCT f.media_id, p.id, p.name FROM faces f JOIN persons p ON p.id = f.person_id "
             f"WHERE f.media_id IN ({','.join('?' * len(chunk))})")
        for mid, pid, name in conn.execute(q, chunk):
            if not only or pid in only:
                res[mid].append((only.index(pid) if only else 0, name))
    return {k: [n for _, n in sorted(v)] for k, v in res.items()}


def build(conn: sqlite3.Connection, media_ids: list[int], dest_root: str, o: OrganizeOptions,
          one_place_per_photo: bool = False) -> Plan:
    """one_place_per_photo: for Move (a file can only be in one place) a photo
    with several people goes to the first one's folder."""
    o.validate()
    rows = _rows(conn, media_ids)
    if not o.include_videos:
        rows = [r for r in rows if r["kind"] != "video"]
    by_person = "{person}" in o.folder_template
    people = _people(conn, [r["id"] for r in rows], o.persons) if by_person else {}

    plan = Plan()
    taken: dict[str, PlanItem] = {}                    # normcased dest -> item
    for r in rows:
        names = (people.get(r["id"]) or [o.no_person]) if by_person else [None]
        if one_place_per_photo and len(names) > 1:
            plan.trimmed_people += 1
            names = names[:1]
        for person in names:
            ctx = templates.context(r, person)
            folder = os.path.join(dest_root, *templates.render_folder(o.folder_template, ctx))
            ext = r["filename"].rsplit(".", 1)[1] if "." in r["filename"] else ""
            name = templates.render_name(o.name_template, ctx, ext)
            item = PlanItem(r["id"], r["path"], os.path.join(folder, name), r["size"], r["sha256"])
            _place(item, taken)
            plan.items.append(item)
    return plan


def _same_file(path: str, size: int, sha: bytes | None) -> bool:
    try:
        if os.path.getsize(long_path(path)) != size or sha is None:
            return False
        return file_sha256(path)[0] == sha
    except OSError:
        return False


def _place(item: PlanItem, taken: dict[str, PlanItem]) -> None:
    """Pick a free destination: identical content already there (on disk or
    earlier in this plan) is skipped; anything else gets ' (1)', ' (2)'..."""
    base, ext = os.path.splitext(item.dest)
    n = 0
    while True:
        dest = item.dest if n == 0 else f"{base} ({n}){ext}"
        key = os.path.normcase(dest)
        other = taken.get(key)
        if other is not None:
            if other.sha256 is not None and other.sha256 == item.sha256:
                item.dest, item.status = dest, "same"
                return
        elif os.path.lexists(long_path(dest)):
            if _same_file(dest, item.size, item.sha256):
                item.dest, item.status = dest, "exists"
                taken[key] = item
                return
        else:
            item.dest = dest
            taken[key] = item
            return
        n += 1
