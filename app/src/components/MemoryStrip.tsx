// The memories strip at the top of the Library: "On this day", trips, a person
// over the years, the best of last year... Each card opens as a story (full
// screen, a photo every few seconds, bars at the top, click to move on), from
// which it can be saved as a slideshow video or a collage. Cards change daily;
// any card can be hidden. The whole strip can be switched off under Look.
import { useEffect, useRef, useState } from "react";
import { api, MemoryCard, musicUrl, previewUrl, thumbUrl, Track } from "../api";
import Icon from "../lib/icons";
import CreateDialog, { CreateSource } from "./CreateDialog";

const HIDDEN_KEY = "pe.memories.hidden";
const loadHidden = (): string[] => { try { return JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]"); } catch { return []; } };
const COLORS = ["#E24B4A", "#EF9F27", "#1D9E75", "#378ADD", "#7F77DD", "#D4537E"];

export default function MemoryStrip() {
  const [cards, setCards] = useState<MemoryCard[] | null>(null);
  const [hidden, setHidden] = useState<string[]>(loadHidden);
  const [open, setOpen] = useState<MemoryCard | null>(null);
  const [create, setCreate] = useState<CreateSource | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { api<MemoryCard[]>("/api/memories/strip").then(setCards).catch(() => setCards([])); }, []);

  const hide = (key: string) => {
    const n = [...hidden, key];
    setHidden(n);
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(n.slice(-300))); } catch { /* fine */ }
  };
  const shown = (cards ?? []).filter((c) => !hidden.includes(c.key));
  if (!shown.length) return null;

  return (
    <div className="px-3 pt-3">
      {notice && <div className="mb-2 text-xs text-emerald-400">{notice} <button onClick={() => setNotice(null)}>✕</button></div>}
      <div className="flex gap-3 overflow-x-auto pb-3">
        {shown.map((c, i) => (
          <div key={c.key} data-theme="dark" className="group relative h-48 w-36 shrink-0 cursor-pointer overflow-hidden rounded-2xl shadow-md ring-1 ring-black/10 transition-transform hover:-translate-y-0.5"
            style={{ background: COLORS[i % COLORS.length] }} onClick={() => setOpen(c)} title={`${c.title} · ${c.subtitle}`}>
            {c.cover && <img src={thumbUrl(c.cover)} alt="" draggable={false}
              className="absolute inset-0 h-full w-full object-cover transition-transform duration-[4000ms] group-hover:scale-110" />}
            <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-black/10 to-transparent" />
            <div className="absolute inset-x-2 top-2 h-1 rounded-full bg-white/40" />
            <div className="absolute inset-x-2.5 bottom-2.5 text-white">
              <div className="text-sm font-semibold leading-tight [text-shadow:0_1px_2px_#0008]">{c.title}</div>
              <div className="mt-0.5 text-[11px] text-white/80">{c.subtitle}</div>
            </div>
            <button className="absolute right-1.5 top-3 hidden rounded-full bg-black/50 p-1 text-white hover:bg-black/70 group-hover:block"
              title="Hide this memory" onClick={(e) => { e.stopPropagation(); hide(c.key); }}>
              <Icon name="x" className="h-3.5 w-3.5" /></button>
          </div>
        ))}
      </div>
      {open && <Story card={open} onClose={() => setOpen(null)}
        onCreate={() => { setCreate({ mediaIds: open.media_ids, count: open.media_ids.length }); setOpen(null); }} />}
      {create && <CreateDialog source={create} onClose={() => setCreate(null)} onStarted={(m) => setNotice(m)} />}
    </div>
  );
}

const SECS = 4;

function Story({ card, onClose, onCreate }: { card: MemoryCard; onClose: () => void; onCreate: () => void }) {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const [t0, setT0] = useState(() => performance.now());
  const [now, setNow] = useState(t0);
  const raf = useRef(0);
  const n = card.media_ids.length;

  const go = (d: number) => {
    setI((x) => {
      const nx = x + d;
      if (nx >= n) { music.fadeOut(onClose); return x; }
      return Math.max(0, nx);
    });
    setT0(performance.now());
  };
  useEffect(() => {
    const tick = () => { setNow(performance.now()); raf.current = requestAnimationFrame(tick); };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, []);
  const [pausedAt, setPausedAt] = useState(0);
  const music = useStoryMusic(card, paused);
  useEffect(() => {
    if (paused) { setPausedAt(performance.now()); return; }
    if (pausedAt) { setT0((t) => t + performance.now() - pausedAt); setPausedAt(0); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused]);
  const frac = paused ? (pausedAt - t0) / (SECS * 1000) : (now - t0) / (SECS * 1000);
  useEffect(() => { if (!paused && frac >= 1) go(1); });   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const next = card.media_ids[i + 1];
    if (next) { const im = new Image(); im.src = previewUrl(next); }
  }, [i, card]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") music.fadeOut(onClose);
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === " ") { e.preventDefault(); setPaused((p) => !p); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });   // eslint-disable-line react-hooks/exhaustive-deps

  const id = card.media_ids[i];
  return (
    <div data-theme="dark" className="fixed inset-0 z-[60] flex items-center justify-center bg-black text-white">
      <div className="relative h-full max-h-[100vh] w-full overflow-hidden">
        <img key={id} src={previewUrl(id)} alt="" draggable={false}
          className="absolute inset-0 h-full w-full object-contain"
          style={{ animation: `pe-slide-in 600ms ease-out both, pe-drift-${i % 2} ${SECS + 0.6}s linear both`,
            animationPlayState: paused ? "paused" : "running" }} />
        {/* tap left / right half to go back / on, hold the middle to pause */}
        <button className="absolute inset-y-0 left-0 w-1/3" aria-label="Previous" onClick={() => go(-1)} />
        <button className="absolute inset-y-0 left-1/3 w-1/3" aria-label="Pause" onClick={() => setPaused((p) => !p)} />
        <button className="absolute inset-y-0 right-0 w-1/3" aria-label="Next" onClick={() => go(1)} />
        <div className="pointer-events-none absolute inset-x-0 top-0 bg-gradient-to-b from-black/70 to-transparent px-4 pb-10 pt-3">
          <div className="flex gap-1">
            {card.media_ids.map((m, k) => (
              <div key={m} className="h-1 flex-1 overflow-hidden rounded-full bg-white/30">
                <div className="h-full bg-white" style={{ width: `${k < i ? 100 : k > i ? 0 : Math.min(100, Math.max(0, frac * 100))}%` }} />
              </div>
            ))}
          </div>
          <div className="mt-3 text-lg font-semibold [text-shadow:0_1px_3px_#000]">{card.title}</div>
          <div className="text-sm text-white/80">{card.subtitle}</div>
        </div>
        <div className="absolute right-3 top-8 flex gap-2">
          <button className="flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1.5 text-sm backdrop-blur hover:bg-white/25"
            title="Save these photos as a slideshow video or a collage" onClick={onCreate}><Icon name="sparkles" />Save as video or collage</button>
          {music.track && <MusicControls m={music} />}
          <button className="rounded-full bg-white/15 p-2 hover:bg-white/25" title="Close (Esc)" onClick={() => music.fadeOut(onClose)}><Icon name="x" /></button>
        </div>
        {paused && <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/60 p-4"><Icon name="pause" className="h-6 w-6" /></div>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ music

const MUTE_KEY = "pe.memories.mute";
const SONG_KEY = "pe.memories.song";   // { [card key]: track id } chosen by the user
const VOL = 0.6;
const songs = (): Record<string, string> => { try { return JSON.parse(localStorage.getItem(SONG_KEY) || "{}"); } catch { return {}; } };

type Music = {
  track: { id: string; title: string } | null; muted: boolean; tracks: Track[];
  setMuted: (m: boolean) => void; choose: (id: string) => void; fadeOut: (then: () => void) => void;
};

// The song fitting the memory (or the one the user picked for it): fades in
// over 2 s, loops, pauses with the story and when the window loses focus,
// fades out over 1.5 s at the end. A missing song just means silence.
function useStoryMusic(card: MemoryCard, paused: boolean): Music {
  const [muted, setMutedState] = useState(() => { try { return localStorage.getItem(MUTE_KEY) === "1"; } catch { return false; } });
  const [tracks, setTracks] = useState<Track[]>([]);
  const [chosen, setChosen] = useState<string | undefined>(() => songs()[card.key]);
  const [blurred, setBlurred] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const fade = useRef(0);
  useEffect(() => { api<Track[]>("/api/music").then(setTracks).catch(() => {}); }, []);
  const picked = chosen ? tracks.find((t) => t.id === chosen) : undefined;
  const track = picked ? { id: picked.id, title: picked.title } : card.music ?? null;

  const ramp = (to: number, ms: number, then?: () => void) => {
    const a = audio.current;
    window.clearInterval(fade.current);
    if (!a) { then?.(); return; }
    const from = a.volume, t0 = performance.now();
    fade.current = window.setInterval(() => {
      const k = Math.min(1, (performance.now() - t0) / ms);
      a.volume = Math.max(0, Math.min(1, from + (to - from) * k));
      if (k >= 1) { window.clearInterval(fade.current); then?.(); }
    }, 50);
  };
  useEffect(() => {
    if (!track) return;
    const a = new Audio(musicUrl(track.id));
    a.loop = true; a.volume = 0; a.onerror = () => { audio.current = null; };
    audio.current = a;
    return () => { window.clearInterval(fade.current); a.pause(); a.src = ""; };
  }, [track?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onBlur = () => setBlurred(true), onFocus = () => setBlurred(false);
    window.addEventListener("blur", onBlur); window.addEventListener("focus", onFocus);
    return () => { window.removeEventListener("blur", onBlur); window.removeEventListener("focus", onFocus); };
  }, []);
  useEffect(() => {
    const a = audio.current;
    if (!a) return;
    if (muted || paused || blurred) { ramp(0, 300, () => a.pause()); return; }
    a.play().then(() => ramp(VOL, a.currentTime < 0.5 ? 2000 : 400)).catch(() => {});
  }, [muted, paused, blurred, track?.id]);   // eslint-disable-line react-hooks/exhaustive-deps

  return {
    track, muted, tracks,
    setMuted: (m) => { setMutedState(m); try { localStorage.setItem(MUTE_KEY, m ? "1" : "0"); } catch { /* fine */ } },
    choose: (id) => {
      setChosen(id);
      try { localStorage.setItem(SONG_KEY, JSON.stringify({ ...songs(), [card.key]: id })); } catch { /* fine */ }
    },
    fadeOut: (then) => (audio.current && !audio.current.paused ? ramp(0, 1500, then) : then()),
  };
}

function MusicControls({ m }: { m: Music }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative flex items-center gap-1">
      <button className="flex max-w-[14rem] items-center gap-1.5 rounded-full bg-white/15 px-3 py-1.5 text-sm backdrop-blur hover:bg-white/25"
        title="Change song" onClick={() => setOpen((o) => !o)}>♪ <span className="truncate">{m.track?.title}</span></button>
      <button className="rounded-full bg-white/15 px-2.5 py-1.5 text-sm hover:bg-white/25" title={m.muted ? "Sound on" : "Mute"}
        onClick={() => m.setMuted(!m.muted)}>{m.muted ? "🔇" : "🔊"}</button>
      {open && (
        <div className="absolute right-0 top-full z-10 mt-2 max-h-80 w-72 overflow-y-auto rounded-xl bg-neutral-900/95 p-1.5 text-sm shadow-xl">
          <div className="px-3 pb-1 pt-1 text-[11px] uppercase tracking-wide text-neutral-500">Change song</div>
          {m.tracks.map((t) => (
            <button key={t.id} onClick={() => { m.choose(t.id); setOpen(false); }}
              className={`block w-full rounded-lg px-3 py-1.5 text-left hover:bg-white/10 ${m.track?.id === t.id ? "text-sky-300" : ""}`}>
              {t.title}<span className="ml-2 text-xs text-neutral-500">{t.mood || t.source}</span></button>
          ))}
          {!m.tracks.length && <p className="px-3 py-2 text-neutral-400">No songs yet. Add your own in the My music folder (Look menu).</p>}
        </div>
      )}
    </div>
  );
}
