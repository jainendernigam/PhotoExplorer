// Viewer extras: colour picker, frame-by-frame control for animated pictures,
// and the full photo-details list.
import { Fragment, useEffect, useRef, useState } from "react";
import { api, FramesInfo, frameUrl, PhotoDetails, post } from "../api";

// ---------------------------------------------------------------- colour picker

const hex = (r: number, g: number, b: number) => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();

function hsl(r: number, g: number, b: number): string {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
  let h = 0, s = 0;
  if (mx !== mn) {
    const d = mx - mn;
    s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h *= 60;
  }
  return `${Math.round(h)}°, ${Math.round(s * 100)}%, ${Math.round(l * 100)}%`;
}

function cmyk(r: number, g: number, b: number): string {
  const k = 1 - Math.max(r, g, b) / 255;
  if (k >= 1) return "0, 0, 0, 100";
  const f = (v: number) => Math.round(((1 - v / 255 - k) / (1 - k)) * 100);
  return `${f(r)}, ${f(g)}, ${f(b)}, ${Math.round(k * 100)}`;
}

/** Reads pixels of the picture shown in `img` (fetched again as a blob so the canvas can read it). */
export function ColorPicker({ img, src, onClose }: { img: HTMLImageElement | null; src: string; onClose: () => void }) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number; c: [number, number, number] } | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    setReady(false);
    fetch(src).then((r) => r.blob()).then((b) => createImageBitmap(b)).then((bmp) => {
      if (!alive) return;
      const c = document.createElement("canvas");
      c.width = bmp.width; c.height = bmp.height;
      c.getContext("2d", { willReadFrequently: true })!.drawImage(bmp, 0, 0);
      canvas.current = c;
      setReady(true);
    }).catch(() => {});
    return () => { alive = false; };
  }, [src]);

  useEffect(() => {
    if (!img) return;
    const move = (e: MouseEvent) => {
      const c = canvas.current;
      if (!c) return;
      const r = img.getBoundingClientRect();
      const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
      if (fx < 0 || fy < 0 || fx >= 1 || fy >= 1) { setPos(null); return; }
      const d = c.getContext("2d")!.getImageData(Math.floor(fx * c.width), Math.floor(fy * c.height), 1, 1).data;
      setPos({ x: e.clientX, y: e.clientY, c: [d[0], d[1], d[2]] });
    };
    const click = (e: MouseEvent) => {
      if (!pos) return;
      e.stopPropagation(); e.preventDefault();
      const h = hex(...pos.c);
      navigator.clipboard?.writeText(h).catch(() => {});
      setPinned(h);
    };
    img.addEventListener("mousemove", move);
    img.addEventListener("click", click, true);
    img.style.cursor = "crosshair";
    return () => { img.removeEventListener("mousemove", move); img.removeEventListener("click", click, true); img.style.cursor = ""; };
  }, [img, pos]);

  const c = pos?.c;
  return (
    <>
      {c && pos && (
        <div className="pointer-events-none fixed z-[70] w-48 rounded-lg bg-neutral-900/95 p-2 text-xs shadow-xl"
          style={{ left: Math.min(pos.x + 18, window.innerWidth - 210), top: Math.min(pos.y + 18, window.innerHeight - 120) }}>
          <div className="mb-1 flex items-center gap-2">
            <span className="h-8 w-8 rounded border border-white/30" style={{ background: `rgb(${c.join(",")})` }} />
            <span className="font-mono text-sm">{hex(...c)}</span>
          </div>
          <div className="font-mono text-neutral-300">RGB {c.join(", ")}</div>
          <div className="font-mono text-neutral-300">HSL {hsl(...c)}</div>
          <div className="font-mono text-neutral-300">CMYK {cmyk(...c)}</div>
        </div>
      )}
      <div className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded bg-black/70 px-3 py-1.5 text-xs">
        {!ready ? "Getting the colours ready…" : pinned ? <>Copied <span className="font-mono">{pinned}</span>. Click another spot, or </>
          : "Point at the photo to read a colour; click to copy it. "}
        <button className="ml-1 underline" onClick={onClose}>Done (P)</button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- frames

/** Play / pause and step through the frames of a GIF, animated WEBP / PNG or multi-page TIFF. */
export function FramesBar({ id, info, frame, playing, onFrame, onPlaying, onNotice }: {
  id: number; info: FramesInfo; frame: number; playing: boolean;
  onFrame: (n: number) => void; onPlaying: (p: boolean) => void; onNotice: (m: string) => void;
}) {
  const n = info.count;
  useEffect(() => {                          // warm the frames near the current one
    if (playing) return;
    [frame + 1, frame - 1].filter((k) => k >= 0 && k < n).forEach((k) => { new Image().src = frameUrl(id, k); });
  }, [id, frame, n, playing]);
  const save = async (which: number[] | null) => {
    try {
      const r = await post<{ saved: number; folder: string }>(`/api/media/${id}/frames/save`, { which });
      onNotice(`Saved ${r.saved} frame${r.saved === 1 ? "" : "s"} as PNG in ${r.folder}`);
    } catch (e) { onNotice(String(e)); }
  };
  const btn = "rounded bg-white/10 px-2 py-1 hover:bg-white/20 disabled:opacity-30";
  return (
    <div className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-lg bg-black/75 px-2 py-1.5 text-xs">
      <button className={btn} title="First frame" onClick={() => { onPlaying(false); onFrame(0); }}>⏮</button>
      <button className={btn} title="Previous frame (,)" disabled={frame === 0} onClick={() => { onPlaying(false); onFrame(frame - 1); }}>◀</button>
      <button className={`${btn} w-16`} onClick={() => onPlaying(!playing)}>{playing ? "⏸ Pause" : "▶ Play"}</button>
      <button className={btn} title="Next frame (.)" disabled={frame >= n - 1} onClick={() => { onPlaying(false); onFrame(frame + 1); }}>▶</button>
      <input type="range" min={0} max={n - 1} value={frame} className="w-40"
        onChange={(e) => { onPlaying(false); onFrame(Number(e.target.value)); }} />
      <span className="w-24 text-center text-neutral-300">{playing ? `${n} frames` : `Frame ${frame + 1} of ${n}`}</span>
      <button className={btn} disabled={playing} onClick={() => save([frame])}>Save frame</button>
      <button className={btn} onClick={() => save(null)}>Save all</button>
    </div>
  );
}

// ---------------------------------------------------------------- details

export function useDetails(id: number | undefined, on: boolean): PhotoDetails | null {
  const [d, setD] = useState<PhotoDetails | null>(null);
  useEffect(() => {
    setD(null);
    if (!id || !on) return;
    let alive = true;
    api<PhotoDetails>(`/api/media/${id}/details`).then((x) => alive && setD(x)).catch(() => {});
    return () => { alive = false; };
  }, [id, on]);
  return d;
}

export function DetailsBlock({ d }: { d: PhotoDetails | null }) {
  const [all, setAll] = useState(false);
  if (!d) return null;
  const settings = Object.entries(d.settings);
  const tags = Object.entries(d.tags);
  return (
    <div className="space-y-2">
      {settings.length > 0 && (
        <div>
          <div className="text-[11px] uppercase text-neutral-500">Camera settings</div>
          <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {settings.map(([k, v]) => <Fragment key={k}><span className="text-neutral-400">{k}</span><span className="break-words">{v}</span></Fragment>)}
          </div>
        </div>
      )}
      {tags.length > 0 && (
        <div>
          <button className="text-xs text-sky-400 hover:underline" onClick={() => setAll((x) => !x)}>
            {all ? "Hide" : "Show"} all {tags.length} details</button>
          {all && (
            <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
              {tags.map(([k, v]) => <Fragment key={k}><span className="text-neutral-500">{k}</span><span className="break-all text-neutral-300">{v}</span></Fragment>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
