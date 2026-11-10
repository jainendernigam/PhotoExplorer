// Live preview in the Create dialog. A slideshow is played here with the chosen
// photos, captions, transition, slow zoom, colour look, shape and music, so the
// settings can be tried before the video is made (it is close, not exact: theme
// extras such as month cards or maps only appear in the video). A collage is
// drawn small by the engine with the real layout, a moment after each change.
import { useEffect, useRef, useState } from "react";
import { Filters, filtersForExport, post, previewUrl } from "../api";

type Src = { mediaIds?: number[]; filters?: Filters };
type Item = { id: number; caption: string };

export type SlidePreviewProps = {
  source: Src; title: string; secs: number; transition: string; kenBurns: boolean; captions: string;
  personId?: number; aspect: string; maxPhotos: number; skipDups: boolean; color?: string; musicUrl?: string | null;
};
export type CollagePreviewProps = {
  source: Src; title: string; layout: string; size: string; spacing: number; background: string;
  maxPhotos: number; skipDups: boolean;
};

const body = (source: Src, kind: string, options: Record<string, unknown>) => ({
  kind, options, ...(source.mediaIds ? { media_ids: source.mediaIds } : { filters: filtersForExport(source.filters ?? {}) }),
});

// Debounced request: runs fn once the inputs stop changing for a moment.
function useDebounced<T>(fn: () => Promise<T>, deps: unknown[], wait: number) {
  const [res, setRes] = useState<{ data?: T; err?: string; loading: boolean }>({ loading: true });
  useEffect(() => {
    let alive = true;
    setRes((r) => ({ ...r, loading: true }));
    const t = setTimeout(() => fn().then((data) => alive && setRes({ data, loading: false }))
      .catch((e) => alive && setRes({ err: String(e), loading: false })), wait);
    return () => { alive = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return res;
}

const LOOKS: Record<string, string> = {
  natural: "none", warm: "sepia(.25) saturate(1.25) hue-rotate(-8deg)", cool: "saturate(.9) hue-rotate(12deg) brightness(1.03)",
  vivid: "saturate(1.5) contrast(1.1)", faded: "contrast(.85) saturate(.7) brightness(1.08)", sepia: "sepia(.85)",
  mono: "grayscale(1) contrast(1.05)", cinematic: "contrast(1.15) saturate(.85) sepia(.15)",
};
const MIXED = ["fade", "smoothleft", "circleopen", "wipeleft", "zoomin", "radial"];

export function SlideshowPreview(p: SlidePreviewProps) {
  const res = useDebounced(
    () => post<{ used: number; items: Item[] }>("/api/exports/preview", body(p.source, "slideshow", {
      max_photos: p.maxPhotos, skip_duplicates: p.skipDups, captions: p.captions,
      person_id: p.captions === "date_age" ? p.personId ?? null : null,
    })),
    [p.maxPhotos, p.skipDups, p.captions, p.personId, JSON.stringify(p.source)], 350);
  const items = res.data?.items ?? [];
  const slides: (Item | "title")[] = [...(p.title ? ["title" as const] : []), ...items];
  const [i, setI] = useState(0);
  const [n, setN] = useState(0);              // bumps on every slide change (restarts its animations)
  const [paused, setPaused] = useState(false);
  const [sound, setSound] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);

  useEffect(() => { setI(0); setN((x) => x + 1); }, [res.data, p.title]);
  useEffect(() => {
    if (paused || slides.length < 2) return;
    const t = setTimeout(() => { setI((x) => (x + 1) % slides.length); setN((x) => x + 1); }, Math.max(1, p.secs) * 1000);
    return () => clearTimeout(t);
  }, [i, paused, slides.length, p.secs]);
  useEffect(() => {
    const a = audio.current;
    if (!a) return;
    if (sound && !paused) a.play().catch(() => {}); else a.pause();
  }, [sound, paused, p.musicUrl]);
  // warm the next picture
  useEffect(() => {
    const nx = slides[(i + 1) % Math.max(1, slides.length)];
    if (nx && nx !== "title") { const im = new Image(); im.src = previewUrl(nx.id); }
  }, [i, slides]);

  const cur = slides[i % Math.max(1, slides.length)];
  const prev = slides.length > 1 ? slides[(i - 1 + slides.length) % slides.length] : undefined;
  const tr = p.transition === "mixed" ? MIXED[n % MIXED.length] : p.transition;
  const ratio = p.aspect === "portrait" ? "9 / 16" : p.aspect === "square" ? "1 / 1" : "16 / 9";
  const filter = LOOKS[p.color ?? "natural"] ?? "none";

  const layer = (s: Item | "title", key: string, enter: boolean) => (
    <div key={key} className="absolute inset-0 overflow-hidden"
      style={enter && tr !== "none" ? { animation: `pe-t-${tr} 800ms ease-out both`, animationPlayState: paused ? "paused" : "running" } : undefined}>
      {s === "title" ? (
        <div className="grid h-full place-items-center bg-neutral-950 px-4 text-center text-xl font-semibold text-white">{p.title}</div>
      ) : (
        <>
          <img src={previewUrl(s.id)} alt="" draggable={false} className="h-full w-full object-contain"
            style={{ filter, animation: enter && p.kenBurns ? `pe-drift-${n % 2} ${p.secs + 0.8}s linear both` : undefined,
              // the outgoing photo stays where its slow zoom ended
              transform: !enter && p.kenBurns && (n - 1) % 2 === 0 ? "scale(1.07) translate(-1%, -0.6%)" : undefined,
              animationPlayState: paused ? "paused" : "running" }} />
          {s.caption && <div className="absolute bottom-2 left-3 text-sm font-medium text-white [text-shadow:0_1px_3px_#000]">{s.caption}</div>}
        </>
      )}
    </div>
  );

  return (
    <div className="space-y-2">
      <div className="relative mx-auto max-h-[60vh] overflow-hidden rounded-lg bg-black" style={{ aspectRatio: ratio, width: p.aspect === "portrait" ? "auto" : "100%", height: p.aspect === "portrait" ? "60vh" : undefined }}
        onClick={() => setPaused((v) => !v)}>
        {cur && prev && prev !== cur && layer(prev, `p${n}`, false)}
        {cur && layer(cur, `c${n}`, true)}
        {!cur && <div className="grid h-full place-items-center text-sm text-neutral-500">{res.loading ? "Getting photos…" : res.err ?? "No photos"}</div>}
        {paused && <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/60 px-4 py-2 text-white">❚❚</div>}
      </div>
      <div className="flex items-center gap-2 text-xs text-neutral-400">
        <button className="rounded bg-neutral-800 px-2 py-1 text-neutral-200 hover:bg-neutral-700" onClick={() => setPaused((v) => !v)}>{paused ? "▶ Play" : "❚❚ Pause"}</button>
        {p.musicUrl && <button className="rounded bg-neutral-800 px-2 py-1 text-neutral-200 hover:bg-neutral-700" onClick={() => setSound((v) => !v)}>{sound ? "🔊 Sound on" : "🔈 Sound off"}</button>}
        <span className="flex-1 text-right">{slides.length ? `${(i % slides.length) + 1} / ${slides.length}` : ""}{res.loading && items.length ? " · updating…" : ""}</span>
      </div>
      <p className="text-[11px] text-neutral-500">Preview. Theme extras (month cards, maps, frames) appear in the finished video.</p>
      {p.musicUrl && <audio ref={audio} src={p.musicUrl} loop />}
    </div>
  );
}

export function CollagePreview(p: CollagePreviewProps) {
  const res = useDebounced(
    () => post<{ used: number; image: string }>("/api/exports/preview", body(p.source, "collage", {
      layout: p.layout, size: p.size, spacing: p.spacing, background: p.background, title: p.title || null,
      max_photos: p.maxPhotos, skip_duplicates: p.skipDups,
    })),
    [p.layout, p.size, p.spacing, p.background, p.title, p.maxPhotos, p.skipDups, JSON.stringify(p.source)], 600);
  const [img, setImg] = useState<string | null>(null);
  useEffect(() => { if (res.data) setImg(res.data.image); }, [res.data]);
  return (
    <div className="space-y-2">
      <div className="relative grid min-h-48 place-items-center overflow-hidden rounded-lg bg-black">
        {img ? <img src={img} alt="Collage preview" className="max-h-[60vh] w-full object-contain" draggable={false} />
          : <span className="p-8 text-sm text-neutral-500">{res.loading ? "Drawing the collage…" : res.err ?? ""}</span>}
        {img && res.loading && <span className="absolute right-2 top-2 rounded bg-black/70 px-2 py-0.5 text-xs text-white">Updating…</span>}
      </div>
      {res.err && img && <p className="text-xs text-red-400">{res.err}</p>}
      <p className="text-[11px] text-neutral-500">Preview at small size{res.data ? ` · ${res.data.used} photos` : ""}. The saved collage is full quality.</p>
    </div>
  );
}
