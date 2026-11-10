"""Themes and music for slideshows.

* Built-in themes live here (BUILTIN_THEMES).
* Packs downloaded by "Update software" live in <data>/content/<pack id>/,
  each with a pack.json:
      {"id": "music-1", "kind": "music" | "themes", "version": 1, "title": "...",
       "items": [...]}
  Theme items have the same shape as BUILTIN_THEMES; music items are
  {"id", "title", "mood", "file", "seconds", "licence"} with `file` next to pack.json.
* The user's own songs: any audio file in <data>/music.

A theme is a name, a description, slideshow settings (any SlideshowOptions
field) and an optional music id.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}


def _t(id, name, description, music=None, **options) -> dict:
    return {"id": id, "name": name, "description": description, "music": music, "options": options}


BUILTIN_THEMES = [
    _t("classic", "Classic", "Gentle fades and a slow zoom, natural colour.", "gentle-piano",
       transition="fade", seconds_per_photo=3.5, ken_burns=True, color="natural", frame="blur"),
    _t("family-album", "Family Album", "Warm tones, white photo-print border, handwritten captions.", "warm-memories",
       transition="dissolve", seconds_per_photo=4.0, ken_burns=True, color="warm", frame="print",
       background="#3b2f2a", font="hand", caption_where="bottom-center", caption_color="#fff6e8",
       title_background="#3b2f2a", title_color="#fff6e8"),
    _t("celebration", "Celebration", "Faster, brighter, lively transitions for birthdays and festivals.", "celebration",
       transition="mixed", seconds_per_photo=2.5, transition_s=0.5, ken_burns=True, color="vivid", frame="blur",
       font="bold", title_background="#4a1650", title_color="#ffd84d"),
    _t("growing-up", "Growing Up", "Age captions and playful transitions for a child's timeline.", "music-box",
       transition="circleopen", seconds_per_photo=3.5, ken_burns=True, color="warm", frame="white",
       background="#d9ecf7", captions="date_age", font="hand", caption_where="bottom-center",
       caption_color="#ffffff", title_background="#d9ecf7", title_color="#2b4a63"),
    _t("travel", "Travel", "Sliding transitions and wide framing.", "travel-diaries",
       transition="smoothleft", seconds_per_photo=3.0, ken_burns=True, color="vivid", frame="blur", font="bold"),
    _t("vintage", "Vintage", "Sepia, film grain and darkened corners.", "nostalgia",
       transition="dissolve", seconds_per_photo=4.0, transition_s=1.2, ken_burns=True, color="sepia",
       grain=0.6, vignette=0.55, frame="print", background="#1d1712", font="serif", caption_color="#f3e3c3",
       title_background="#1d1712", title_color="#f3e3c3"),
    _t("black-white", "Black & White", "Timeless mono with slow dissolves.", "evening-glow",
       transition="dissolve", seconds_per_photo=4.5, transition_s=1.4, ken_burns=True, color="mono",
       vignette=0.3, frame="black", font="serif"),
    _t("cinematic", "Cinematic", "Cinema bars, slow zoom and film colour.", "calm-ambient",
       transition="fade", seconds_per_photo=4.5, transition_s=1.2, ken_burns=True, color="cinematic",
       vignette=0.35, frame="blur", letterbox=True, font="serif", caption_where="bottom-center"),
    _t("minimal", "Minimal", "Straight cuts, no zoom, no captions.", None,
       transition="none", seconds_per_photo=3.0, ken_burns=False, color="natural", frame="black", captions="none"),
    _t("quick-reel", "Quick Reel", "Vertical 9:16 for phones and status updates, 2 seconds a photo.", "sunny-day",
       transition="mixed", seconds_per_photo=2.0, transition_s=0.4, ken_burns=True, color="vivid",
       frame="blur", aspect="portrait", font="bold", caption_where="bottom-center", max_photos=60),
    _t("beat-sync", "Beat Sync", "Cuts land on the beat of the music. Pick a lively song.", "celebration",
       beat_sync=True, transition="none", seconds_per_photo=1.5, ken_burns=True, color="vivid", frame="blur",
       font="bold", caption_where="bottom-center", max_photos=80),
    _t("split-screen", "Split Screen", "Two upright photos share the screen, split on a slant.", "sunny-day",
       split_screen=True, transition="smoothleft", seconds_per_photo=3.0, transition_s=0.6, ken_burns=True,
       color="natural", frame="blur", font="bold"),
    _t("big-titles", "Big Titles", "Large bold titles that push in, and bold captions.", "celebration",
       big_titles=True, transition="fade", seconds_per_photo=3.0, transition_s=0.5, ken_burns=True, color="vivid",
       frame="blur", title_background="#c2185b", title_color="#ffffff"),
    _t("highlight-reel", "Highlight Reel", "About 30 seconds of the best photos: faces, smiles, sharp shots.", "sunny-day",
       highlight_seconds=30, transition="mixed", seconds_per_photo=2.0, transition_s=0.4, ken_burns=True,
       color="vivid", frame="blur", font="bold", caption_where="bottom-center", max_photos=600),
    _t("trip-map", "Trip Map", "A drawn route of the trip before each day (uses the photos' GPS).", "travel-diaries",
       trip_map=True, transition="smoothleft", seconds_per_photo=3.0, ken_burns=True, color="vivid", frame="blur",
       font="bold"),
    _t("zoom-through", "Zoom Through", "Each photo zooms into the next.", "calm-ambient",
       transition="zoomin", seconds_per_photo=3.0, transition_s=0.7, ken_burns=True, color="natural", frame="blur"),
    _t("year-in-review", "Year in Review", "A big month card before each month's photos.", "warm-memories",
       month_cards=True, transition="dissolve", seconds_per_photo=2.5, transition_s=0.6, ken_burns=True,
       color="warm", frame="blur", font="bold", captions="none", max_photos=200),
]


class Content:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "content"
        self.music_dir = data_dir / "music"

    # ---------------------------------------------------------------- packs

    def packs(self) -> list[dict]:
        out = []
        if not self.dir.is_dir():
            return out
        for d in sorted(self.dir.iterdir()):
            f = d / "pack.json"
            if not f.is_file():
                continue
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("bad pack %s: %s", d, e)
                continue
            if p.get("id") == d.name and p.get("kind") in ("themes", "music"):
                p["_dir"] = d
                out.append(p)
        return out

    def installed(self) -> dict[str, int]:
        return {p["id"]: int(p.get("version", 0)) for p in self.packs()}

    # ---------------------------------------------------------------- themes

    def themes(self) -> list[dict]:
        out = [dict(t, source="built-in") for t in BUILTIN_THEMES]
        seen = {t["id"] for t in out}
        for p in self.packs():
            if p["kind"] != "themes":
                continue
            for t in p.get("items", []):
                if isinstance(t, dict) and t.get("id") and t["id"] not in seen and isinstance(t.get("options", {}), dict):
                    seen.add(t["id"])
                    out.append({"id": t["id"], "name": t.get("name", t["id"]), "description": t.get("description", ""),
                                "music": t.get("music"), "options": t.get("options", {}), "source": p.get("title", p["id"])})
        return out

    # ---------------------------------------------------------------- music

    def music(self) -> list[dict]:
        """Every track: pack tracks first, then the user's own music folder."""
        out = []
        for p in self.packs():
            if p["kind"] != "music":
                continue
            for m in p.get("items", []):
                if not isinstance(m, dict) or not m.get("id") or not m.get("file"):
                    continue
                path = (p["_dir"] / m["file"]).resolve()
                if path.parent != p["_dir"].resolve() or not path.is_file():
                    continue
                out.append({"id": m["id"], "title": m.get("title", m["id"]), "mood": m.get("mood", ""),
                            "seconds": m.get("seconds"), "licence": m.get("licence", ""),
                            "source": p.get("title", p["id"]), "path": str(path)})
        if self.music_dir.is_dir():
            for f in sorted(self.music_dir.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                    out.append({"id": "mine:" + f.name, "title": f.stem, "mood": "", "seconds": None,
                                "licence": "", "source": "My music", "path": str(f)})
        return out

    def music_path(self, music_id: str) -> str | None:
        return next((m["path"] for m in self.music() if m["id"] == music_id), None)
