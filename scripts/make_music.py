"""Composes the "Music pack 1" tracks for slideshows: original music made
from scratch with numpy (no samples, no third-party recordings), so the
tracks belong to this project and need no licence.

    runtime\\python\\python.exe scripts\\make_music.py            # all tracks -> content\\music-1
    runtime\\python\\python.exe scripts\\make_music.py calm-ambient

Each track: a chord progression, a melody built from the chords (seeded, so
the output is the same every run), simple synthesised instruments, reverb,
then levelled (calm tracks quieter than upbeat ones) and saved as AAC (.m4a)
through the bundled FFmpeg.
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "content" / "music-1"
FFMPEG = ROOT / "bin" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")

NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
        "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord(key: str, degree: int, minor: bool = False, seventh: bool = False, octave: int = 4) -> list[int]:
    """Triad (or 7th) on a scale degree (1-7) of the key, as MIDI notes."""
    scale = MINOR if minor else MAJOR
    root = 12 * (octave + 1) + NOTE[key]
    steps = [0, 2, 4, 6] if seventh else [0, 2, 4]
    out = []
    for s in steps:
        i = degree - 1 + s
        out.append(root + scale[i % 7] + 12 * (i // 7))
    return out


# ---------------------------------------------------------------- instruments

def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = min(n, int(attack * SR)), min(n, int(release * SR))
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[n - r:] *= np.linspace(1, 0, r)
    return e


def piano(f: float, dur: float, vel: float = 0.8) -> np.ndarray:
    t = _t(dur + 1.2)
    x = np.zeros_like(t)
    for k in range(1, 9):
        fk = f * k * (1 + 0.0004 * k * k)                 # slight stretch, like real strings
        x += (vel ** (0.6 + 0.2 * k)) / k ** 1.4 * np.sin(2 * np.pi * fk * t) * np.exp(-t * (1.2 + 0.9 * k))
    return x * _env(len(t), 0.004, 0.25) * 0.5


def musicbox(f: float, dur: float, vel: float = 0.8) -> np.ndarray:
    t = _t(max(dur, 1.6))
    x = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t * d)
            for r, a, d in ((1, 1.0, 2.2), (2.76, 0.45, 5.0), (5.4, 0.2, 9.0), (8.93, 0.08, 14.0)))
    return x * vel * _env(len(t), 0.002, 0.1) * 0.45


def pluck(f: float, dur: float, vel: float = 0.8) -> np.ndarray:
    t = _t(dur + 0.4)
    x = sum((1 / k) * np.sin(2 * np.pi * f * k * t) * np.exp(-t * (3 + 2.5 * k)) for k in range(1, 7))
    return x * vel * _env(len(t), 0.003, 0.08) * 0.55


def pad(freqs: list[float], dur: float, vel: float = 0.5, bright: float = 1.0) -> np.ndarray:
    t = _t(dur + 1.5)
    x = np.zeros_like(t)
    for f in freqs:
        for det in (-0.12, 0.0, 0.12):                    # three detuned voices per note
            ff = f * 2 ** (det / 12)
            for k in range(1, 6):
                x += (bright ** k) / k ** 1.6 * np.sin(2 * np.pi * ff * k * t + det * 7)
    x *= 1 + 0.15 * np.sin(2 * np.pi * 0.25 * t)          # slow swell
    return x * _env(len(t), 0.9, 1.5) * vel * 0.08 / max(1, len(freqs))


def bass(f: float, dur: float, vel: float = 0.8) -> np.ndarray:
    t = _t(dur)
    x = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
    return x * np.exp(-t * 1.5) * _env(len(t), 0.01, 0.08) * vel * 0.5


def kick(vel: float = 1.0) -> np.ndarray:
    t = _t(0.35)
    f = 45 + 80 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9) * vel * 0.9


def noise_hit(dur: float, decay: float, vel: float, tone: float = 0.0, seed: int = 0) -> np.ndarray:
    t = _t(dur)
    n = np.random.default_rng(seed).normal(0, 1, len(t))
    n = np.diff(n, prepend=0)                             # brighter: high-passed noise
    x = n * np.exp(-t * decay)
    if tone:
        x += 0.5 * np.sin(2 * np.pi * tone * t) * np.exp(-t * decay)
    return x * vel


def hat(vel=0.25, seed=0):
    return noise_hit(0.08, 60, vel, seed=seed) * 0.5


def clap(vel=0.6, seed=0):
    return noise_hit(0.25, 18, vel, tone=190, seed=seed) * 0.5


def shaker(vel=0.18, seed=0):
    return noise_hit(0.12, 35, vel, seed=seed) * 0.4


# ---------------------------------------------------------------- mixing

class Mix:
    def __init__(self, seconds: float):
        self.buf = np.zeros((int((seconds + 4) * SR), 2))

    def add(self, x: np.ndarray, at: float, pan: float = 0.0, gain: float = 1.0) -> None:
        i = int(at * SR)
        if i >= len(self.buf):
            return
        x = x[: len(self.buf) - i] * gain
        self.buf[i:i + len(x), 0] += x * np.sqrt((1 - pan) / 2)
        self.buf[i:i + len(x), 1] += x * np.sqrt((1 + pan) / 2)

    def finish(self, length: float, reverb: float = 0.25, room: float = 2.2) -> np.ndarray:
        dry = self.buf[: int(length * SR)]
        rng = np.random.default_rng(7)
        n = int(room * SR)
        wet = np.zeros_like(dry)
        for ch in range(2):                               # stereo reverb: decaying noise as the room
            ir = rng.normal(0, 1, n) * np.exp(-np.arange(n) / SR * 6.9 / room)
            ir[: int(0.012 * SR)] = 0
            size = 1 << int(np.ceil(np.log2(len(dry) + n)))
            w = np.fft.irfft(np.fft.rfft(dry[:, ch], size) * np.fft.rfft(ir, size), size)[: len(dry)]
            wet[:, ch] = w / (np.abs(ir).sum() ** 0.5 * 4)
        out = dry + reverb * wet
        fade = int(3.0 * SR)
        out[-fade:] *= np.linspace(1, 0, fade)[:, None]
        out[: int(0.05 * SR)] *= np.linspace(0, 1, int(0.05 * SR))[:, None]
        out = np.tanh(out / (np.percentile(np.abs(out), 99.9) + 1e-9) * 0.9)   # gentle limiter
        return out / np.abs(out).max() * 0.89


# ---------------------------------------------------------------- composition helpers

def melody_line(rng, chords: list[list[int]], beats_per_chord: int, rhythm: list[float], octave_shift: int = 12,
                scale_notes: list[int] | None = None) -> list[tuple[float, int, float]]:
    """(beat, midi, length) notes: chord tones on strong beats, passing notes between,
    with the first phrase repeated (AABA) so it sounds like a tune."""
    def phrase(seed_offset: int) -> list[tuple[float, int, float]]:
        r = np.random.default_rng(rng + seed_offset)
        notes, beat = [], 0.0
        prev = None
        for ci, ch in enumerate(chords):
            end = (ci + 1) * beats_per_chord
            pattern = list(rhythm)
            while beat < end - 1e-6:
                ln = pattern[len(notes) % len(pattern)]
                ln = min(ln, end - beat)
                tones = [n + octave_shift for n in ch] + [ch[0] + octave_shift + 12]
                if abs(beat - round(beat)) < 1e-6 or scale_notes is None:
                    cands = tones
                else:
                    cands = [n + octave_shift for n in scale_notes]
                if prev is not None:                      # prefer small steps
                    cands = sorted(cands, key=lambda n: abs(n - prev))[:3]
                n = int(r.choice(cands))
                if r.random() > 0.12:                     # a few rests
                    notes.append((beat, n, ln))
                prev = n
                beat += ln
        return notes
    a, b = phrase(0), phrase(1)
    span = len(chords) * beats_per_chord
    out = []
    for k, ph in enumerate((a, a, b, a)):
        out += [(s + k * span, n, ln) for s, n, ln in ph]
    return out


def render_track(spec: dict) -> np.ndarray:
    bpm, key, minor = spec["bpm"], spec["key"], spec.get("minor", False)
    beat = 60.0 / bpm
    bpc = spec.get("beats_per_chord", 4)
    prog = [chord(key, d, minor, spec.get("sevenths", False)) for d in spec["progression"]]
    loops = spec.get("loops", 8)                          # times the progression is played
    length = loops * len(prog) * bpc * beat
    mix = Mix(length)
    scale = [12 * 5 + NOTE[key] + s for s in (MINOR if minor else MAJOR)]
    mel = melody_line(spec.get("seed", 1), prog, bpc, spec.get("rhythm", [1.0]), 12, scale)
    mel_span = 4 * len(prog) * bpc
    lead = {"piano": piano, "musicbox": musicbox, "pluck": pluck}[spec.get("lead", "piano")]
    comp = spec.get("comp", "arp")
    for loop in range(loops):
        section = loop / loops
        for ci, ch in enumerate(prog):
            t0 = (loop * len(prog) + ci) * bpc * beat
            freqs = [hz(n) for n in ch]
            if spec.get("pad", True):
                mix.add(pad(freqs, bpc * beat, spec.get("pad_vel", 0.5), spec.get("bright", 0.8)), t0, 0, 1)
            # accompaniment
            if comp == "arp":
                pattern = [0, 1, 2, 1, 0, 2, 1, 2] if bpc >= 4 else [0, 1, 2, 1, 2, 1]
                step = spec.get("arp_step", 0.5)
                inst = {"piano": piano, "pluck": pluck, "musicbox": musicbox}[spec.get("arp_inst", "piano")]
                for k in range(int(bpc / step)):
                    n = ch[pattern[k % len(pattern)]] - 12 * spec.get("arp_down", 0)
                    mix.add(inst(hz(n), step * beat * 1.5, 0.45), t0 + k * step * beat, -0.3 + 0.2 * (k % 3), 0.7)
            elif comp == "chords":
                hits = spec.get("chord_hits", [0])
                inst = {"piano": piano, "pluck": pluck}[spec.get("arp_inst", "piano")]
                for h in hits:
                    for j, n in enumerate(ch):
                        mix.add(inst(hz(n - 12 * spec.get("arp_down", 0)), beat * 1.5, 0.4), t0 + h * beat + j * 0.012, -0.2 + 0.2 * j, 0.6)
            if spec.get("bass", False) and section > 0.1:
                for h in spec.get("bass_hits", [0, 2]):
                    mix.add(bass(hz(ch[0] - 24), beat * 1.8, 0.8), t0 + h * beat, 0, 0.9)
            # drums after the intro, quieter in the last part
            d = spec.get("drums")
            if d and 0.12 < section < 0.93:
                for b in range(bpc):
                    tb = t0 + b * beat
                    if "kick" in d and b % 2 == 0:
                        mix.add(kick(), tb, 0, 0.8)
                    if "clap" in d and b % 2 == 1:
                        mix.add(clap(seed=b), tb, 0.1, 0.7)
                    if "hat" in d:
                        mix.add(hat(seed=b), tb + beat / 2, 0.4, 0.8)
                    if "shaker" in d:
                        for s in range(4):
                            mix.add(shaker(seed=s + b), tb + s * beat / 4, -0.4, 0.7 if s % 2 else 1.0)
        # melody from the second time through the progression
        if loop >= spec.get("melody_from", 1):
            k = loop % 4
            base = (loop * len(prog)) * bpc * beat
            for s, n, ln in mel:
                if k * len(prog) * bpc <= s < (k + 1) * len(prog) * bpc:
                    off = (s - k * len(prog) * bpc) * beat
                    mix.add(lead(hz(n), ln * beat, 0.7), base + off, 0.15, spec.get("lead_gain", 0.9))
    return mix.finish(length, spec.get("reverb", 0.3))


TRACKS = [
    dict(id="gentle-piano", title="Gentle Piano", mood="calm", bpm=72, key="C", progression=[1, 5, 6, 4],
         lead="piano", comp="arp", arp_inst="piano", arp_down=1, rhythm=[1, 1, 2], seed=11, loops=8, reverb=0.35),
    dict(id="music-box", title="Music Box Lullaby", mood="lullaby", bpm=66, key="F", progression=[1, 4, 1, 5],
         beats_per_chord=3, lead="musicbox", comp="arp", arp_inst="musicbox", arp_step=1, rhythm=[1, 0.5, 0.5, 1],
         seed=23, loops=8, pad_vel=0.35, reverb=0.4),
    dict(id="warm-memories", title="Warm Memories", mood="warm", bpm=80, key="G", progression=[1, 3, 4, 5],
         lead="piano", comp="chords", arp_inst="piano", chord_hits=[0, 1.5, 3], arp_down=1, bass=True, rhythm=[1.5, 0.5, 2],
         seed=37, loops=8, reverb=0.3),
    dict(id="celebration", title="Celebration", mood="festive", bpm=120, key="D", progression=[1, 5, 6, 4],
         lead="musicbox", comp="chords", arp_inst="pluck", chord_hits=[0, 1, 2, 3], bass=True, bass_hits=[0, 1.5, 2, 3.5],
         drums=["kick", "clap", "hat"], rhythm=[0.5, 0.5, 1], seed=41, loops=12, reverb=0.2, bright=0.9),
    dict(id="sunny-day", title="Sunny Day", mood="upbeat", bpm=110, key="A", progression=[1, 4, 6, 5],
         lead="pluck", comp="arp", arp_inst="pluck", arp_step=0.5, bass=True, drums=["kick", "hat", "shaker"],
         rhythm=[0.5, 1, 0.5, 1], seed=53, loops=12, reverb=0.2),
    dict(id="travel-diaries", title="Travel Diaries", mood="adventure", bpm=100, key="E", minor=True,
         progression=[1, 6, 3, 7], lead="pluck", comp="arp", arp_inst="pluck", arp_step=0.25, bass=True,
         drums=["kick", "shaker"], rhythm=[1, 0.5, 0.5], seed=67, loops=10, reverb=0.25),
    dict(id="nostalgia", title="Nostalgia", mood="nostalgic", bpm=64, key="A", minor=True, progression=[1, 6, 3, 7],
         lead="piano", comp="arp", arp_inst="piano", arp_down=1, rhythm=[2, 1, 1], seed=71, loops=7, reverb=0.4,
         sevenths=True),
    dict(id="calm-ambient", title="Calm Ambient", mood="ambient", bpm=60, key="D", progression=[1, 4, 6, 4],
         sevenths=True, lead="musicbox", comp="none", rhythm=[2, 2], seed=83, loops=7, pad_vel=0.8, reverb=0.5,
         lead_gain=0.5, melody_from=2, bright=0.6),
    dict(id="evening-glow", title="Evening Glow", mood="reflective", bpm=70, key="Eb", progression=[1, 6, 2, 5],
         sevenths=True, lead="piano", comp="chords", arp_inst="piano", chord_hits=[0, 2], arp_down=1, bass=True,
         rhythm=[1, 1, 1, 1], seed=97, loops=8, reverb=0.35),
    dict(id="festive-bells", title="Festive Bells", mood="festive", bpm=96, key="C", progression=[1, 4, 5, 1],
         lead="musicbox", comp="arp", arp_inst="musicbox", arp_step=0.5, bass=True, drums=["shaker", "kick"],
         rhythm=[0.5, 0.5, 1], seed=101, loops=10, reverb=0.3),
]


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def main(only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    items = []
    for spec in TRACKS:
        name = f"{spec['id']}.m4a"
        if not only or spec["id"] in only:
            x = render_track(spec)
            wav = OUT / f"{spec['id']}.wav"
            write_wav(wav, x)
            subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                            "-af", f"loudnorm=I={-15 if spec.get('drums') else -18}:TP=-1.5:LRA=11", "-ar", str(SR),
                            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(OUT / name)], check=True)
            wav.unlink()
            print(f"{spec['title']}: {len(x) / SR:.0f} s")
        seconds = None
        try:
            out = subprocess.run([str(FFMPEG).replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                  "-of", "csv=p=0", str(OUT / name)], capture_output=True, text=True)
            seconds = round(float(out.stdout.strip()))
        except (ValueError, OSError):
            pass
        items.append({"id": spec["id"], "title": spec["title"], "mood": spec["mood"], "file": name,
                      "seconds": seconds, "licence": "Original music made for Photo Explorer"})
    (OUT / "pack.json").write_text(json.dumps({
        "id": "music-1", "kind": "music", "version": 1, "title": "Music pack 1",
        "description": f"{len(items)} original tracks: calm, lullaby, warm, festive, upbeat, travel, nostalgic, ambient.",
        "items": items}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
