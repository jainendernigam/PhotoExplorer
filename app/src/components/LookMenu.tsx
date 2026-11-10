// The "Look" button at the top right: theme, layout, the opening animation and the Explorer "Open with" setting.
import { useEffect, useRef, useState } from "react";
import { LAYOUTS, setAppearance, SWATCH, THEMES, useAppearance } from "../lib/appearance";
import { post } from "../api";

export type OpenWith = { available: boolean; enabled: boolean; reason: string | null };

export default function LookMenu({ openWith, onOpenWith }: {
  openWith: OpenWith | null; onOpenWith: (enable: boolean) => void;
}) {
  const look = useAppearance();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open]);

  const dots = (t: keyof typeof SWATCH) => (
    <span className="flex -space-x-1">
      {SWATCH[t].map((c, i) => <span key={i} className="h-3.5 w-3.5 rounded-full ring-1 ring-black/10" style={{ background: c }} />)}
    </span>
  );

  return (
    <div ref={box} className="relative z-40">
      <button onClick={() => setOpen((o) => !o)} title="Theme and layout"
        className="flex items-center gap-2 rounded-full border border-neutral-700 bg-neutral-900/90 px-3 py-1 text-sm text-neutral-200 shadow hover:bg-neutral-800">
        {dots(look.theme)} Look
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 space-y-3 rounded-xl border border-neutral-700 bg-neutral-900 p-3 text-sm text-neutral-200 shadow-xl">
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Theme</div>
            {THEMES.map(([v, l]) => (
              <button key={v} onClick={() => setAppearance({ theme: v })}
                className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left ${look.theme === v ? "bg-neutral-800 font-medium" : "hover:bg-neutral-800"}`}>
                {dots(v)} <span className="flex-1">{l}</span>{look.theme === v && "✓"}
              </button>
            ))}
          </div>
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Layout</div>
            <div className="flex gap-1">
              {LAYOUTS.map(([v, l]) => (
                <button key={v} onClick={() => setAppearance({ layout: v })}
                  className={`flex-1 rounded px-2 py-1 ${look.layout === v ? "bg-sky-700 text-white" : "bg-neutral-800 hover:bg-neutral-700"}`}>{l}</button>
              ))}
            </div>
          </div>
          <div className="space-y-1 border-t border-neutral-800 pt-2 text-xs text-neutral-400">
            <label className="flex items-center justify-between gap-2">Memories strip in Library
              <input type="checkbox" checked={look.strip} onChange={(e) => setAppearance({ strip: e.target.checked })} /></label>
            <div className="flex items-center justify-between gap-2" title="Add your favourite songs here; memories will play them">My music
              <button className="rounded bg-neutral-800 px-2 py-0.5 text-neutral-200 hover:bg-neutral-700" onClick={() => post("/api/music/open-folder").catch(() => {})}>Open folder</button></div>
            <label className="flex items-center justify-between gap-2">Opening animation
              <input type="checkbox" checked={look.intro} onChange={(e) => setAppearance({ intro: e.target.checked })} /></label>
            <label className={`flex items-center justify-between gap-2 ${look.intro ? "" : "opacity-40"}`}>Opening tune
              <input type="checkbox" checked={look.tune} disabled={!look.intro} onChange={(e) => setAppearance({ tune: e.target.checked })} /></label>
          </div>
          {openWith && (
            <label className="flex items-center justify-between gap-2 border-t border-neutral-800 pt-2 text-xs text-neutral-400" title={openWith.reason ??
              "Adds Photo Explorer to Explorer's \"Open with\" menu for pictures (this Windows user only). Windows lets you make it the default there or in Settings > Default apps."}>
              In Explorer's "Open with"
              <input type="checkbox" checked={openWith.enabled} disabled={!openWith.available}
                onChange={(e) => onOpenWith(e.target.checked)} />
            </label>
          )}
        </div>
      )}
    </div>
  );
}
