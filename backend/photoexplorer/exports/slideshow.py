"""Photo slideshow video with FFmpeg.

1. Every photo becomes one prepared frame (oriented, whole photo on a blurred
   copy of itself, optional caption), so FFmpeg only ever sees plain JPEGs,
   whatever the source format (HEIC, RAW...).
2. FFmpeg shows each frame for N seconds, optionally with a slow zoom
   ("Ken Burns"), joined by cross-transitions (xfade), with optional music
   that fades out at the end.
3. Long slideshows are rendered in chunks of CHUNK photos and then joined, so
   memory stays flat however many photos there are.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import frames, looks, selection, slide_styles

TRANSITIONS = ["fade", "dissolve", "smoothleft", "smoothright", "circleopen", "wipeleft", "slideup", "radial", "zoomin"]
RESOLUTIONS = {720: (1280, 720), 1080: (1920, 1080), 2160: (3840, 2160)}
ASPECTS = ("landscape", "portrait", "square")      # portrait = 9:16 for phones
CHUNK = 24
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class SlideshowOptions:
    seconds_per_photo: float = 3.5
    transition: str = "fade"        # a TRANSITIONS name, "mixed" or "none"
    transition_s: float = 0.8
    ken_burns: bool = True
    resolution: int = 1080
    fps: int = 30
    music_path: str | None = None
    music_volume: float = 1.0
    captions: str = "date"          # none | date | date_age
    person_id: int | None = None    # whose age to show with date_age
    title: str | None = None
    subtitle: str | None = None
    max_photos: int = 300
    skip_duplicates: bool = True
    # look (set by a theme; see looks.py)
    theme: str | None = None        # theme id, for the record
    aspect: str = "landscape"
    color: str = "natural"
    grain: float = 0.0
    vignette: float = 0.0
    frame: str = "blur"             # blur | black | white | print
    background: str = "#000000"     # behind black/white/print frames
    letterbox: bool = False
    font: str = "sans"
    caption_color: str = "#ffffff"
    caption_where: str = "bottom-left"
    title_color: str = "#ffffff"
    title_background: str = "#0c0c0e"
    # styles (see slide_styles.py)
    beat_sync: bool = False         # every cut on a beat of the music (hard cuts)
    split_screen: bool = False      # two portrait photos share a slide
    big_titles: bool = False        # large bold title card and captions
    month_cards: bool = False       # "Year in review": a card before each month
    trip_map: bool = False          # a drawn route card before each day (needs GPS)
    highlight_seconds: int = 0      # > 0: only the best photos, about this long


def frame_size(resolution: int, aspect: str) -> tuple[int, int]:
    w, h = RESOLUTIONS.get(resolution, RESOLUTIONS[1080])
    if aspect == "portrait":
        return h, w
    if aspect == "square":
        return h, h
    return w, h


class Cancelled(Exception):
    pass


def render(conn: sqlite3.Connection, ffmpeg: str, items: list[dict], o: SlideshowOptions, out_path: Path,
           progress: Callable[[float, str], None], cancel: threading.Event) -> None:
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found (put ffmpeg.exe in the app's bin folder)")
    if not items:
        raise RuntimeError("No photos to put in the slideshow")
    if o.music_path and not os.path.isfile(o.music_path):
        raise RuntimeError(f"Music file not found: {o.music_path}")
    W, H = frame_size(o.resolution, o.aspect)
    D = max(1.0, float(o.seconds_per_photo))
    T = 0.0 if o.transition == "none" else min(max(0.2, float(o.transition_s)), D / 2)
    person = None
    if o.person_id:
        r = conn.execute("SELECT name, birth_date FROM persons WHERE id = ?", (o.person_id,)).fetchone()
        person = dict(r) if r else None

    music_offset = 0.0
    if o.beat_sync and o.music_path:
        beat = slide_styles.find_beat(ffmpeg, o.music_path)
        if beat:
            D = slide_styles.beat_length(beat[0], D)
            T = 0.0                                  # hard cuts, right on the beat
            music_offset = beat[1]
    if o.highlight_seconds and o.highlight_seconds > 0:
        items = slide_styles.highlights(conn, items, max(3, round(o.highlight_seconds / D)))
    route = o.trip_map and slide_styles.has_route(items)
    trip_starts = set(slide_styles.trip_days(items)) if route else set()
    style = "bold" if o.big_titles else o.font

    tmp = out_path.parent / f".tmp-{uuid.uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    try:
        # ---- 1. frames (Ken Burns frames are 25% larger so the zoom stays sharp)
        fw, fh = (int(W * 1.25) // 2 * 2, int(H * 1.25) // 2 * 2) if o.ken_burns else (W, H)
        names: list[str] = []
        durs: list[float] = []
        punch: list[bool] = []                       # cards that zoom in faster

        def add(img, fast: bool = False) -> None:
            name = f"f{len(names):04d}.jpg"
            img.save(tmp / name, quality=92)
            names.append(name)
            durs.append(D)
            punch.append(fast)

        if o.title:
            if o.big_titles:
                add(slide_styles.big_title_card(o.title, o.subtitle or "", fw, fh, o.title_background,
                                                o.title_color), fast=True)
            else:
                add(looks.title_card(o.title, fw, fh, o.subtitle or "", o.font, o.title_color, o.title_background))
        margin = 0.05 if o.ken_burns else 0.0       # the zoom crops up to ~5% at each edge
        bars = (fh - round(fw / 2.35)) / 2 / fh if o.letterbox and fw > fh else 0.0
        groups = slide_styles.pairs(items) if o.split_screen else [[it] for it in items]
        index = {id(it): k for k, it in enumerate(items)}
        last_month = None
        day_no = 0
        for g_i, group in enumerate(groups, 1):
            if cancel.is_set():
                raise Cancelled()
            try:
                imgs = [frames.load(it["path"], it["kind"], max(fw, fh)) for it in group]
            except Exception:
                continue                     # unreadable photo: skip it, keep going
            it = group[0]
            when = slide_styles._dt(it)
            if o.month_cards and when and (when.year, when.month) != last_month:
                last_month = (when.year, when.month)
                thumbs = []
                for x in items:
                    t = slide_styles._dt(x)
                    if t and (t.year, t.month) == last_month and len(thumbs) < 4:
                        try:
                            thumbs.append(frames.load(x["path"], x["kind"], 640))
                        except Exception:
                            pass
                add(slide_styles.month_card(imgs[0], thumbs, when, fw, fh, style), fast=o.big_titles)
            if index[id(it)] in trip_starts:
                day_no += 1
                add(slide_styles.map_card(items, index[id(it)], day_no, when, imgs[0], fw, fh, style))
            text = selection.caption_for(it, o.captions, person)
            if len(imgs) == 2:
                img = slide_styles.split(looks.color(imgs[0], o.color), looks.color(imgs[1], o.color), fw, fh,
                                         frames.face_focus(conn, group[0]["id"]),
                                         frames.face_focus(conn, group[1]["id"]))
                written = False
            else:
                img = looks.color(imgs[0], o.color)
                img, written = looks.place(img, fw, fh, o.frame, o.background, "" if o.big_titles else text, o.font)
            img = looks.letterbox(img, o.letterbox)
            img = looks.grain(looks.vignette(img, o.vignette), o.grain, seed=g_i)
            if o.big_titles:
                slide_styles.big_caption(img, text, margin + bars)
            elif not written:
                looks.caption(img, text, o.font, o.caption_color, o.caption_where,
                              bottom_frac=max(0.0, bars - 0.02), margin=margin)
            add(img)
            progress(0.3 * g_i / len(groups), f"Preparing photo {g_i} of {len(groups)}")
        if not names:
            raise RuntimeError("None of the photos could be read")

        total = sum(durs) - (len(names) - 1) * T
        trans = [_transition(o.transition, k) for k in range(len(names))]

        # ---- 2/3. render (in chunks when long), then join with music
        if len(names) <= CHUNK:
            _run_ffmpeg(ffmpeg, tmp, _image_inputs(names, durs, o.fps),
                        _graph(len(names), durs, T, trans, (W, H), o, images=True, punch=punch),
                        total, o, out_path, final=True, music_offset=music_offset, progress=lambda f: progress(0.3 + 0.7 * f, "Making the video"),
                        cancel=cancel)
            return
        chunks = [names[i:i + CHUNK] for i in range(0, len(names), CHUNK)]
        seg_files, seg_lens = [], []
        done_s = 0.0
        for k, chunk in enumerate(chunks):
            seg = f"seg{k:03d}.mp4"
            start = k * CHUNK
            cd = durs[start:start + len(chunk)]
            length = sum(cd) - (len(chunk) - 1) * T
            base = 0.3 + 0.6 * done_s / total
            _run_ffmpeg(ffmpeg, tmp, _image_inputs(chunk, cd, o.fps),
                        _graph(len(chunk), cd, T, trans[start:start + len(chunk)], (W, H), o, images=True,
                               punch=punch[start:start + len(chunk)]),
                        length, o, tmp / seg, final=False,
                        progress=lambda f, b=base, l=length: progress(b + 0.6 * f * l / total, f"Making part {k + 1} of {len(chunks)}"),
                        cancel=cancel)
            seg_files.append(seg); seg_lens.append(length)
            done_s += length
        inputs = []
        for s in seg_files:
            inputs += ["-i", s]
        joins = [_transition(o.transition, (k + 1) * CHUNK) for k in range(len(seg_files))]
        _run_ffmpeg(ffmpeg, tmp, inputs, _graph(len(seg_files), seg_lens, T, joins, (W, H), o, images=False),
                    total, o, out_path, final=True, music_offset=music_offset,
                    progress=lambda f: progress(0.9 + 0.1 * f, "Joining the parts"),
                    cancel=cancel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _transition(name: str, k: int) -> str:
    if name == "mixed":
        return TRANSITIONS[k % len(TRANSITIONS)]
    return name if name in TRANSITIONS else "fade"


def _image_inputs(names: list[str], durs: list[float], fps: int) -> list[str]:
    args: list[str] = []
    for n, d in zip(names, durs):
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", n]
    return args


def _graph(n: int, lengths: list[float], T: float, trans: list[str], size: tuple[int, int],
           o: SlideshowOptions, images: bool, punch: list[bool] | None = None) -> str:
    W, H = size
    parts = []
    for i in range(n):
        if images and o.ken_burns:
            frames_n = max(1, int(round(lengths[i] * o.fps)))
            if punch and punch[i]:
                z = f"1+0.22*on/{frames_n}"                                                 # title cards: quick push in
            else:
                z = f"1+0.10*on/{frames_n}" if i % 2 == 0 else f"1.10-0.10*on/{frames_n}"   # alternate in / out
            parts.append(f"[{i}:v]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={o.fps},"
                         f"setsar=1,format=yuv420p[v{i}]")
        else:
            parts.append(f"[{i}:v]scale={W}:{H},setsar=1,fps={o.fps},format=yuv420p[v{i}]")
    if n == 1:
        parts.append("[v0]null[vout]")
    elif T <= 0:
        parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    else:
        prev, acc = "v0", lengths[0]
        for k in range(1, n):
            out = "vout" if k == n - 1 else f"x{k}"
            parts.append(f"[{prev}][v{k}]xfade=transition={trans[k]}:duration={T:.3f}:offset={acc - T:.3f}[{out}]")
            acc += lengths[k] - T
            prev = out
    return ";\n".join(parts)


def _run_ffmpeg(ffmpeg: str, cwd: Path, inputs: list[str], graph: str, total: float, o: SlideshowOptions,
                out: Path, final: bool, progress: Callable[[float], None], cancel: threading.Event,
                music_offset: float = 0.0) -> None:
    (cwd / "graph.txt").write_text(graph, encoding="utf-8")
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", *inputs]
    music = final and o.music_path
    if music:
        cmd += ["-stream_loop", "-1", "-i", os.path.abspath(o.music_path)]
    audio_graph = ""
    if music:
        n_inputs = inputs.count("-i")
        fade = min(2.5, total / 3)
        audio_graph = (f";\n[{n_inputs}:a]atrim={music_offset:.3f}:{music_offset + total:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=1,"
                       f"afade=t=out:st={total - fade:.3f}:d={fade:.3f},volume={o.music_volume:.2f}[aout]")
        (cwd / "graph.txt").write_text(graph + audio_graph, encoding="utf-8")
    cmd += ["-/filter_complex", "graph.txt", "-map", "[vout]"]
    if music:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    quality = ["-preset", "medium", "-crf", "20"] if final else ["-preset", "fast", "-crf", "15"]
    cmd += ["-c:v", "libx264", *quality, "-pix_fmt", "yuv420p", "-r", str(o.fps), "-t", f"{total:.3f}"]
    if final:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(out.resolve()) if final else out.name)

    log = cwd / "ffmpeg.log"
    with open(log, "w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=err, text=True,
                                creationflags=_NO_WINDOW)
        try:
            for line in proc.stdout:
                if cancel.is_set():
                    proc.kill()
                    raise Cancelled()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    progress(min(1.0, us / 1e6 / max(total, 0.001)))
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        raise RuntimeError("FFmpeg failed: " + " | ".join(tail))
