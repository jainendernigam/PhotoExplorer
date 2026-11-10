// "Create" dialog: slideshow video or collage from selected photos, or from
// everything the current view shows (e.g. a person's timeline).
import { useEffect, useRef, useState } from "react";
import { CollagePreview, SlideshowPreview } from "./CreatePreview";
import { api, Filters, filtersForExport, musicUrl, Person, pickAudioFile, pickFolder, post, Theme, Track } from "../api";

export type CreateSource = { mediaIds?: number[]; filters?: Filters; count: number };

const TRANSITIONS = [
  ["fade", "Fade"], ["dissolve", "Dissolve"], ["smoothleft", "Slide"], ["circleopen", "Circle"],
  ["wipeleft", "Wipe"], ["radial", "Clock"], ["zoomin", "Zoom through"], ["mixed", "Mixed"], ["none", "None (cut)"],
];
// Collage layouts: [id, label, what it does, a background that suits it (used while the background is still white)].
const LAYOUTS: [string, string, string, string | null][] = [
  ["mosaic", "Mosaic (keeps photo shapes)", "Rows that fill the width; photos keep their shape.", null],
  ["grid", "Grid", "Equal tiles in rows and columns.", null],
  ["featured", "One large + small", "The first photo big, the rest beside it.", null],
  ["polaroid", "Polaroid scatter", "Tilted instant prints with the month and year written on each.", "#e9dfcf"],
  ["magazine", "Magazine", "One big photo, a bold title with the dates, smaller photos beside it.", null],
  ["story", "Story strips (9:16)", "Phone-shaped panels, one per day, up to 3 photos each.", "#202022"],
  ["bubbles", "Bubbles", "Round photos of different sizes.", "#fff6f1"],
  ["heart", "Heart", "Square tiles filling a heart shape.", "#fff4f6"],
  ["filmstrip", "Film strip", "Photos in film frames with sprocket holes, title below.", "#f2f2f2"],
  ["year", "Year in photos", "One photo for each month of the year most photos are from.", null],
  ["then_now", "Then and now", "The oldest and the newest photo side by side with their years.", "#111113"],
];
const SIZES = [
  ["landscape_4k", "Landscape 4K (3840×2160)"], ["square", "Square (3000×3000)"],
  ["a4_portrait", "A4 portrait, print"], ["a4_landscape", "A4 landscape, print"], ["phone", "Phone wallpaper"],
];

// Settings a theme sets that have their own control below; the rest of a theme's
// options (colour, frame, fonts...) are sent as they are.
const CONTROLLED = ["seconds_per_photo", "transition", "ken_burns", "captions", "max_photos", "aspect"];
const BASE = { seconds_per_photo: 3.5, transition: "fade", ken_burns: true, captions: "date", max_photos: 150, aspect: "landscape" };

export default function CreateDialog({ source, onClose, onStarted }: {
  source: CreateSource; onClose: () => void; onStarted: (msg: string) => void;
}) {
  const [kind, setKind] = useState<"slideshow" | "collage">("slideshow");
  const [people, setPeople] = useState<Person[]>([]);
  const onlyPerson = source.filters?.persons?.length === 1 ? source.filters.persons[0] : undefined;
  const [title, setTitle] = useState("");
  const [outDir, setOutDir] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // slideshow
  const [secs, setSecs] = useState(3.5);
  const [transition, setTransition] = useState("fade");
  const [kenBurns, setKenBurns] = useState(true);
  const [resolution, setResolution] = useState(1080);
  const [captions, setCaptions] = useState("date");
  const [personId, setPersonId] = useState<number | undefined>(onlyPerson);
  const [music, setMusic] = useState<string | null>(null);
  const [maxShow, setMaxShow] = useState(150);
  const [aspect, setAspect] = useState("landscape");
  const [themes, setThemes] = useState<Theme[]>([]);
  const [themeId, setThemeId] = useState("classic");
  const [look, setLook] = useState<Record<string, unknown>>({});
  const [tracks, setTracks] = useState<Track[]>([]);
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  // collage
  const [layout, setLayout] = useState("mosaic");
  const [size, setSize] = useState("landscape_4k");
  const [spacing, setSpacing] = useState(12);
  const [background, setBackground] = useState("#ffffff");
  const [maxCollage, setMaxCollage] = useState(30);
  const [skipDups, setSkipDups] = useState(true);

  useEffect(() => {
    api<Person[]>("/api/people").then((p) => {
      setPeople(p);
      const one = p.find((x) => x.id === onlyPerson);
      if (one?.birth_date) setCaptions("date_age");
      if (one) setTitle(`${one.name}`);
    }).catch(() => {});
  }, [onlyPerson]);

  useEffect(() => {
    Promise.all([api<Theme[]>("/api/themes"), api<Track[]>("/api/music")]).then(([th, tr]) => {
      setThemes(th); setTracks(tr);
      const first = th.find((t) => t.id === "classic") ?? th[0];
      if (first) applyTheme(first, tr);
    }).catch(() => {});
    return () => audio.current?.pause();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyTheme = (t: Theme, tr: Track[] = tracks) => {
    const o = { ...BASE, ...t.options } as Record<string, unknown>;
    setThemeId(t.id);
    setSecs(Number(o.seconds_per_photo)); setTransition(String(o.transition)); setKenBurns(Boolean(o.ken_burns));
    setMaxShow(Number(o.max_photos)); setAspect(String(o.aspect));
    // age captions only when there is someone with a birth date to count from
    setCaptions(o.captions === "date_age" && !personId ? "date" : String(o.captions));
    setLook(Object.fromEntries(Object.entries(t.options).filter(([k]) => !CONTROLLED.includes(k))));
    const track = tr.find((x) => x.id === t.music);
    setMusic(track ? track.path : null);
    stopPreview();
  };
  const stopPreview = () => { audio.current?.pause(); setPlaying(false); };
  const preview = () => {
    if (playing) return stopPreview();
    const t = tracks.find((x) => x.path === music);
    if (!t) return;
    audio.current?.pause();
    audio.current = new Audio(musicUrl(t.id));
    audio.current.onended = () => setPlaying(false);
    audio.current.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const max = kind === "slideshow" ? maxShow : maxCollage;
  const used = Math.min(source.count, max);
  const highlight = kind === "slideshow" ? Number(look.highlight_seconds ?? 0) : 0;
  const minutes = highlight > 0 ? Math.min(highlight / 60, ((used + (title ? 1 : 0)) * secs) / 60)
    : ((used + (title ? 1 : 0)) * secs) / 60;

  const start = async () => {
    setBusy(true); setErr(null);
    const options = kind === "slideshow"
      ? { ...look, theme: themeId, aspect, seconds_per_photo: secs, transition, ken_burns: kenBurns, resolution, captions,
          person_id: captions === "date_age" ? personId ?? null : null,
          music_path: music, title: title || null, max_photos: maxShow, skip_duplicates: skipDups }
      : { layout, size, spacing, background, title: title || null, max_photos: maxCollage, skip_duplicates: skipDups };
    try {
      const r = await post<{ id: number; photos: number }>("/api/exports", {
        kind, title: title || null, options, output_dir: outDir,
        ...(source.mediaIds ? { media_ids: source.mediaIds } : { filters: filtersForExport(source.filters ?? {}) }),
      });
      onStarted(`${kind === "slideshow" ? "Slideshow" : "Collage"} started from ${r.photos.toLocaleString()} photos. Follow it under Memories.`);
      onClose();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const label = "flex items-center justify-between gap-3 text-sm";
  const input = "rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500";
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="flex max-h-[94vh] w-[66rem] max-w-[97vw] gap-5 rounded-xl bg-neutral-900 p-5 shadow-2xl">
      <div className="w-[30rem] shrink-0 space-y-4 overflow-y-auto pr-1">
        <div className="flex items-center gap-2">
          <h2 className="flex-1 text-lg font-semibold">Create from {source.count.toLocaleString()} photo{source.count === 1 ? "" : "s"}</h2>
          <button className="text-neutral-400 hover:text-white" onClick={onClose}>✕</button>
        </div>
        <div className="flex gap-1 rounded-lg bg-neutral-800 p-1 text-sm">
          {(["slideshow", "collage"] as const).map((k) => (
            <button key={k} onClick={() => setKind(k)}
              className={`flex-1 rounded-md py-1.5 ${kind === k ? "bg-neutral-950 text-white" : "text-neutral-400"}`}>
              {k === "slideshow" ? "Slideshow video" : "Collage"}
            </button>
          ))}
        </div>

        <label className={label}>Title <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="optional" className={`${input} w-60`} /></label>

        {kind === "slideshow" ? (
          <>
            <div className="space-y-1.5">
              <div className="text-sm">Theme</div>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {themes.map((t) => (
                  <button key={t.id} onClick={() => applyTheme(t)} title={`${t.description}${t.source !== "built-in" ? ` (${t.source})` : ""}`}
                    className={`w-28 shrink-0 rounded-lg border p-2 text-left ${themeId === t.id ? "border-sky-500 bg-sky-950/40" : "border-neutral-800 bg-neutral-800/50 hover:border-neutral-600"}`}>
                    <div className="h-8 rounded" style={{ background: swatch(t) }} />
                    <div className="mt-1 truncate text-xs font-medium">{t.name}</div>
                  </button>
                ))}
              </div>
              <p className="text-xs text-neutral-500">{themes.find((t) => t.id === themeId)?.description} You can still change anything below.</p>
            </div>
            <label className={label}>Shape
              <select value={aspect} onChange={(e) => setAspect(e.target.value)} className={input}>
                <option value="landscape">Landscape 16:9 (TV, laptop)</option><option value="portrait">Vertical 9:16 (phone, status)</option><option value="square">Square 1:1</option>
              </select></label>
            <label className={label}>Seconds per photo
              <input type="number" min={1} max={20} step={0.5} value={secs} onChange={(e) => setSecs(Number(e.target.value))} className={`${input} w-24`} /></label>
            <label className={label}>Transition
              <select value={transition} onChange={(e) => setTransition(e.target.value)} className={input}>
                {TRANSITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></label>
            <label className={label}>Slow zoom (Ken Burns)
              <input type="checkbox" checked={kenBurns} onChange={(e) => setKenBurns(e.target.checked)} /></label>
            <label className={label}>Quality
              <select value={resolution} onChange={(e) => setResolution(Number(e.target.value))} className={input}>
                <option value={720}>720p (small file)</option><option value={1080}>1080p Full HD</option><option value={2160}>4K (slow)</option>
              </select></label>
            <label className={label}>Captions
              <select value={captions} onChange={(e) => setCaptions(e.target.value)} className={input}>
                <option value="none">None</option><option value="date">Month and year</option><option value="date_age">Month, year and age</option>
              </select></label>
            {captions === "date_age" && (
              <label className={label}>Age of
                <select value={personId ?? ""} onChange={(e) => setPersonId(Number(e.target.value) || undefined)} className={input}>
                  <option value="">choose…</option>
                  {people.filter((p) => p.birth_date).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select></label>
            )}
            <div className={label}>Music
              <span className="flex min-w-0 items-center gap-2">
                <select value={music ?? ""} className={`${input} max-w-56`}
                  onChange={async (e) => {
                    stopPreview();
                    if (e.target.value === "__file") { const f = await pickAudioFile(); if (f) setMusic(f); }
                    else setMusic(e.target.value || null);
                  }}>
                  <option value="">No music</option>
                  {Object.entries(groupBy(tracks, (t) => t.source)).map(([src, list]) => (
                    <optgroup key={src} label={src}>
                      {list.map((t) => <option key={t.id} value={t.path}>{t.title}{t.mood ? ` · ${t.mood}` : ""}</option>)}
                    </optgroup>
                  ))}
                  {music && !tracks.some((t) => t.path === music) && <option value={music}>{music.split(/[\\/]/).pop()}</option>}
                  <option value="__file">Another file on this PC…</option>
                </select>
                {music && tracks.some((t) => t.path === music) && (
                  <button className="w-7 shrink-0 rounded bg-neutral-800 py-1 hover:bg-neutral-700" title={playing ? "Stop" : "Listen"} onClick={preview}>{playing ? "■" : "▶"}</button>
                )}
              </span></div>
            {Boolean(look.beat_sync) && !music && <p className="-mt-2 text-xs text-amber-300">Beat Sync needs music: choose a song.</p>}
            {Boolean(look.trip_map) && <p className="-mt-2 text-xs text-neutral-500">The route is drawn from the photos' GPS, without an internet map. Photos without a location are still shown.</p>}
            {tracks.length === 0 && <p className="-mt-2 text-xs text-neutral-500">Get music packs under Update software, or add songs to your music folder there.</p>}
            <label className={label}>At most
              <span><input type="number" min={2} max={1000} value={maxShow} onChange={(e) => setMaxShow(Number(e.target.value))} className={`${input} w-24`} /> photos</span></label>
          </>
        ) : (
          <>
            <label className={label}>Layout
              <select value={layout} className={input} onChange={(e) => {
                const prev = LAYOUTS.find((l) => l[0] === layout)?.[3] ?? "#ffffff";
                const next = LAYOUTS.find((l) => l[0] === e.target.value);
                setLayout(e.target.value);
                if (background.toLowerCase() === prev.toLowerCase()) setBackground(next?.[3] ?? "#ffffff");
              }}>
                {LAYOUTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></label>
            <p className="-mt-2 text-xs text-neutral-500">{LAYOUTS.find((l) => l[0] === layout)?.[2]}</p>
            <label className={label}>Size
              <select value={size} onChange={(e) => setSize(e.target.value)} className={input}>
                {SIZES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></label>
            <label className={label}>Gap between photos
              <input type="number" min={0} max={80} value={spacing} onChange={(e) => setSpacing(Number(e.target.value))} className={`${input} w-24`} /></label>
            <label className={label}>Background
              <input type="color" value={background} onChange={(e) => setBackground(e.target.value)} className="h-7 w-14 rounded bg-transparent" /></label>
            <label className={label}>At most
              <span><input type="number" min={1} max={100} value={maxCollage} onChange={(e) => setMaxCollage(Number(e.target.value))} className={`${input} w-24`} /> photos</span></label>
          </>
        )}

        <label className={label}>Leave out duplicates and look-alikes
          <input type="checkbox" checked={skipDups} onChange={(e) => setSkipDups(e.target.checked)} /></label>

        <div className={label}>Save to
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-xs text-neutral-400" title={outDir ?? ""}>{outDir ?? "Memories folder (default)"}</span>
            <button className="rounded bg-neutral-800 px-2 py-1 hover:bg-neutral-700" onClick={async () => setOutDir(await pickFolder("Save to"))}>Choose…</button>
          </span></div>

        <p className="text-xs text-neutral-500">
          {source.count > max ? `${used} of ${source.count.toLocaleString()} photos, spread evenly from oldest to newest. ` : ""}
          {skipDups ? "Duplicates and look-alikes are left out, so fewer may be used. " : ""}{kind === "slideshow" ? ` About ${minutes < 1 ? "<1" : Math.round(minutes)} min of video.` : ""}
        </p>
        {err && <p className="text-sm text-red-400">{err}</p>}
        <div className="flex justify-end gap-2">
          <button className="rounded px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-800" onClick={onClose}>Cancel</button>
          <button className="rounded bg-sky-600 px-4 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
            disabled={busy || (captions === "date_age" && kind === "slideshow" && !personId)} onClick={start}>Create</button>
        </div>
      </div>
      <div className="min-w-0 flex-1 self-center">
        {kind === "slideshow" ? (
          <SlideshowPreview source={source} title={title} secs={secs} transition={transition} kenBurns={kenBurns}
            captions={captions} personId={personId} aspect={aspect} maxPhotos={maxShow} skipDups={skipDups}
            color={typeof look.color === "string" ? look.color : undefined}
            musicUrl={(() => { const t = tracks.find((x) => x.path === music); return t ? musicUrl(t.id) : null; })()} />
        ) : (
          <CollagePreview source={source} title={title} layout={layout} size={size} spacing={spacing}
            background={background} maxPhotos={maxCollage} skipDups={skipDups} />
        )}
      </div>
      </div>
    </div>
  );
}

function groupBy<T>(items: T[], key: (t: T) => string): Record<string, T[]> {
  const out: Record<string, T[]> = {};
  for (const it of items) (out[key(it)] ??= []).push(it);
  return out;
}

/** A small colour preview of a theme for its card. */
function swatch(t: Theme): string {
  const o = t.options as Record<string, string>;
  const looks: Record<string, string> = {
    natural: "linear-gradient(135deg,#4b8bbe,#7ec27e,#f2c46d)", warm: "linear-gradient(135deg,#d9895b,#f2c46d,#b5654a)",
    cool: "linear-gradient(135deg,#3d6f9e,#8fc3d9,#5b7f9c)", vivid: "linear-gradient(135deg,#ff3d7f,#ffb400,#00c2ff)",
    faded: "linear-gradient(135deg,#b9a99a,#d9cfc4,#9fb0b3)", sepia: "linear-gradient(135deg,#5a4130,#b08a5e,#e8d3a8)",
    mono: "linear-gradient(135deg,#1c1c1c,#8a8a8a,#e6e6e6)", cinematic: "linear-gradient(135deg,#0f3b4a,#c78a4a,#1b2a33)",
  };
  return looks[o.color ?? "natural"] ?? looks.natural;
}
