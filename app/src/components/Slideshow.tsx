// Live slideshow inside the viewer: plays the photos of the current view
// (library, folder, album, person...) full screen, one after another with a
// soft fade and a slow drift, with optional music. Space pauses, arrows step,
// Esc stops. Videos are skipped. Choices are remembered on this PC.
import { useEffect, useRef, useState } from "react";
import { api, Media, musicUrl, previewUrl, Track } from "../api";

type Opts = { secs: number; shuffle: boolean; motion: boolean; music: string };   // music: track id, "" = none
const KEY = "pe.slideshow";
const SPEEDS = [3, 5, 8, 12];
let seq = 0;

function loadOpts(): Opts {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    return { secs: SPEEDS.includes(v.secs) ? v.secs : 5, shuffle: v.shuffle === true, motion: v.motion !== false,
      music: typeof v.music === "string" ? v.music : "" };
  } catch { return { secs: 5, shuffle: false, motion: true, music: "" }; }
}

export default function Slideshow({ index, total, getItem, onIndex, onStop }: {
  index: number; total: number; getItem: (i: number) => Media | undefined;
  onIndex: (i: number) => void; onStop: () => void;
}) {
  const [opts, setOptsState] = useState<Opts>(loadOpts);
  const [paused, setPaused] = useState(false);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [chrome, setChrome] = useState(true);          // controls visible (hide after a moment of no mouse)
  const [shown, setShown] = useState<{ id: number; n: number }[]>([]);   // pictures on screen (old one fades out)
  const order = useRef<number[]>([]);                  // shuffled order
  const audio = useRef<HTMLAudioElement>(null);
  const hideTimer = useRef<number>();
  const item = getItem(index);

  const setOpts = (c: Partial<Opts>) => setOptsState((o) => {
    const n = { ...o, ...c };
    try { localStorage.setItem(KEY, JSON.stringify(n)); } catch { /* fine */ }
    return n;
  });

  useEffect(() => { api<Track[]>("/api/music").then(setTracks).catch(() => {}); }, []);

  // full screen while it plays (where the window allows it)
  useEffect(() => {
    document.documentElement.requestFullscreen?.().catch(() => {});
    return () => { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); };
  }, []);

  // which index comes after i
  const nextOf = (i: number, dir = 1): number => {
    if (total <= 1) return i;
    if (!opts.shuffle) return (i + dir + total) % total;
    if (order.current.length !== total) {
      const a = Array.from({ length: total }, (_, k) => k);
      for (let k = a.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [a[k], a[j]] = [a[j], a[k]]; }
      order.current = a;
    }
    const p = order.current.indexOf(i);
    return order.current[(p + dir + total) % total];
  };

  // skip videos straight away
  useEffect(() => {
    if (item?.kind === "video") onIndex(nextOf(index));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id]);

  // the picture on screen: fade the new one in over the old one
  useEffect(() => {
    if (!item || item.kind === "video") return;
    setShown((s) => (s[s.length - 1]?.id === item.id ? s : [...s.slice(-1), { id: item.id, n: ++seq }]));
  }, [item?.id]);

  // move on after the chosen time; warm the next picture first
  useEffect(() => {
    const nx = nextOf(index);
    const m = getItem(nx);
    if (m && m.kind !== "video") { const im = new Image(); im.src = previewUrl(m.id); }
    if (paused || !item) return;
    const t = setTimeout(() => onIndex(nx), opts.secs * 1000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, paused, opts.secs, opts.shuffle, item?.id]);

  useEffect(() => {
    const a = audio.current;
    if (!a) return;
    if (paused || !opts.music) a.pause(); else a.play().catch(() => {});
  }, [paused, opts.music]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      e.stopImmediatePropagation();
      if (e.key === "Escape") onStop();
      else if (e.key === " ") { e.preventDefault(); setPaused((p) => !p); }
      else if (e.key === "ArrowRight") onIndex(nextOf(index));
      else if (e.key === "ArrowLeft") onIndex(nextOf(index, -1));
      poke();
    };
    window.addEventListener("keydown", onKey, true);     // before the viewer's own keys
    return () => window.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, opts.shuffle, total]);

  const poke = () => {
    setChrome(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setChrome(false), 2500);
  };
  useEffect(() => { poke(); return () => window.clearTimeout(hideTimer.current); }, []);

  const track = tracks.find((t) => t.id === opts.music);
  const dur = opts.secs + 1.2;

  return (
    <div data-theme="dark" className="fixed inset-0 z-[60] overflow-hidden bg-black text-white"
      style={{ cursor: chrome ? "default" : "none" }} onMouseMove={poke}
      onClick={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).tagName === "IMG") setPaused((p) => !p); }}>
      {shown.map((s, i) => (
        <img key={s.n} src={previewUrl(s.id)} alt="" draggable={false}
          className="pe-slide absolute inset-0 h-full w-full object-contain"
          style={{
            animation: `pe-slide-in 900ms ease-out both${opts.motion ? `, pe-drift-${s.n % 2} ${dur}s linear both` : ""}`,
            animationPlayState: paused ? "paused" : "running",
            zIndex: i,
          }} />
      ))}
      {track && <audio ref={audio} src={musicUrl(track.id)} loop autoPlay={!paused} />}
      {paused && <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/50 px-5 py-3 text-2xl">❚❚</div>}

      <div className={`absolute inset-x-0 bottom-0 z-10 flex flex-wrap items-center justify-center gap-2 bg-gradient-to-t from-black/80 to-transparent px-4 pb-4 pt-10 text-sm transition-opacity duration-300 ${chrome ? "opacity-100" : "pointer-events-none opacity-0"}`}
        onClick={(e) => e.stopPropagation()}>
        <button className="rounded bg-white/10 px-3 py-1.5 hover:bg-white/20" title="Previous (←)" onClick={() => onIndex(nextOf(index, -1))}>‹</button>
        <button className="w-24 rounded bg-sky-600 px-3 py-1.5 hover:bg-sky-500" title="Pause or play (Space)" onClick={() => setPaused((p) => !p)}>
          {paused ? "▶ Play" : "❚❚ Pause"}</button>
        <button className="rounded bg-white/10 px-3 py-1.5 hover:bg-white/20" title="Next (→)" onClick={() => onIndex(nextOf(index))}>›</button>
        <span className="mx-2 h-5 w-px bg-white/20" />
        <label className="flex items-center gap-1">Every
          <select value={opts.secs} onChange={(e) => setOpts({ secs: Number(e.target.value) })} className="rounded bg-white/10 px-1 py-1">
            {SPEEDS.map((s) => <option key={s} value={s} className="bg-neutral-900">{s} s</option>)}
          </select></label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={opts.shuffle} onChange={(e) => setOpts({ shuffle: e.target.checked })} /> Shuffle</label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={opts.motion} onChange={(e) => setOpts({ motion: e.target.checked })} /> Motion</label>
        <label className="flex items-center gap-1">Music
          <select value={opts.music} onChange={(e) => setOpts({ music: e.target.value })} className="max-w-48 rounded bg-white/10 px-1 py-1">
            <option value="" className="bg-neutral-900">None</option>
            {tracks.map((t) => <option key={t.id} value={t.id} className="bg-neutral-900">{t.title}</option>)}
          </select></label>
        <span className="mx-2 h-5 w-px bg-white/20" />
        <span className="tabular-nums text-neutral-400">{(index + 1).toLocaleString()} / {total.toLocaleString()}</span>
        <button className="rounded bg-white/10 px-3 py-1.5 hover:bg-white/20" title="Stop (Esc)" onClick={onStop}>■ Stop</button>
      </div>
    </div>
  );
}
