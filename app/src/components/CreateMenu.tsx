// "Create ▾": one button for everything made from the photos on screen (or the
// selection): play a slideshow now, make a slideshow video or a collage, or
// organize the files into folders. Enter or ↓ opens it, Esc closes it.
import { useEffect, useRef, useState } from "react";
import Icon, { IconName } from "../lib/icons";

export type CreateActions = {
  onPlay?: () => void;
  onVideo?: () => void;
  onCollage?: () => void;
  onOrganize?: () => void;      // left out where it doesn't apply
};

export default function CreateMenu({ count, selection, disabled, up, ...a }: CreateActions & {
  count: number; selection: boolean; disabled?: boolean; up?: boolean;
}) {
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
  const what = `${count.toLocaleString()} ${selection ? "selected" : "photos"}`;
  const item = (icon: IconName, label: string, hint: string, fn?: () => void, key?: string) => fn && (
    <button role="menuitem" onClick={() => { setOpen(false); fn(); }}
      className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-neutral-800">
      <Icon name={icon} className="h-4 w-4 shrink-0 text-sky-400" />
      <span className="flex-1"><span className="block">{label}</span><span className="block text-xs text-neutral-500">{hint}</span></span>
      {key && <kbd className="rounded border border-neutral-700 px-1.5 text-[10px] text-neutral-500">{key}</kbd>}
    </button>
  );
  return (
    <div ref={box} className="relative">
      <button disabled={disabled || !count} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); } }}
        className="pe-create flex items-center gap-1.5 whitespace-nowrap rounded-xl bg-sky-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-sky-500 disabled:opacity-40">
        <Icon name="sparkles" />Create<span className="text-xs opacity-80">▾</span>
      </button>
      {open && (
        <div role="menu" className={`absolute right-0 z-50 w-72 ${up ? "bottom-full mb-2" : "mt-2"} rounded-xl border border-neutral-700 bg-neutral-900 p-1.5 text-sm text-neutral-200 shadow-xl`}>
          <div className="px-3 pb-1 pt-1.5 text-[11px] uppercase tracking-wide text-neutral-500">Watch</div>
          {item("play", "Play slideshow", what, a.onPlay, "Space")}
          <div className="px-3 pb-1 pt-2 text-[11px] uppercase tracking-wide text-neutral-500">Make</div>
          {item("film", "Slideshow video", `${what}, saved as a video`, a.onVideo)}
          {item("collage", "Collage", `${what}, saved as one picture`, a.onCollage)}
          {a.onOrganize && <div className="px-3 pb-1 pt-2 text-[11px] uppercase tracking-wide text-neutral-500">Files</div>}
          {item("folders", "Organize into folders…", `Copy, link or move ${what}`, a.onOrganize)}
        </div>
      )}
    </div>
  );
}
