// Print: lay photos out on pages and save a PDF; then open it to print, or keep it.
import { useEffect, useState } from "react";
import { post } from "../api";

const LAYOUTS = [
  ["full", "One photo per page"], ["two", "2 per page"], ["four", "4 per page"],
  ["contact", "Contact sheet (small photos with names)"],
];
const PAPERS = [["a4", "A4"], ["letter", "Letter"], ["a3", "A3"], ["4x6", "4 × 6 in photo paper"]];

export default function PrintDialog({ mediaIds, onClose }: { mediaIds: number[]; onClose: () => void }) {
  const [layout, setLayout] = useState(mediaIds.length > 8 ? "contact" : "full");
  const [paper, setPaper] = useState("a4");
  const [landscape, setLandscape] = useState(false);
  const [captions, setCaptions] = useState(false);
  const [cols, setCols] = useState(5);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ path: string; pages: number } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const make = async () => {
    setBusy(true); setErr(null); setDone(null);
    try {
      setDone(await post<{ path: string; pages: number }>("/api/print", {
        media_ids: mediaIds, layout, paper, landscape, captions, contact_cols: cols,
        title: mediaIds.length === 1 ? "Print" : layout === "contact" ? "Contact sheet" : "Print",
      }));
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };
  const act = (fn: () => Promise<unknown>) => fn().catch((e) => setErr(String(e)));

  const row = "flex items-center justify-between gap-3 text-sm";
  const input = "rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500";
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-black/70" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-[26rem] max-w-[95vw] space-y-3 rounded-xl bg-neutral-900 p-5 shadow-2xl">
        <div className="flex items-center">
          <h2 className="flex-1 text-lg font-semibold">Print {mediaIds.length} photo{mediaIds.length === 1 ? "" : "s"}</h2>
          <button className="text-neutral-400 hover:text-white" onClick={onClose}>✕</button>
        </div>
        <label className={row}>Layout
          <select value={layout} onChange={(e) => setLayout(e.target.value)} className={input}>
            {LAYOUTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></label>
        {layout === "contact" && (
          <label className={row}>Photos across
            <input type="number" min={2} max={10} value={cols} onChange={(e) => setCols(Number(e.target.value))} className={`${input} w-20`} /></label>
        )}
        <label className={row}>Paper
          <select value={paper} onChange={(e) => setPaper(e.target.value)} className={input}>
            {PAPERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></label>
        <label className={row}>Landscape pages <input type="checkbox" checked={landscape} onChange={(e) => setLandscape(e.target.checked)} /></label>
        {layout !== "contact" && (
          <label className={row}>File name and date under each photo
            <input type="checkbox" checked={captions} onChange={(e) => setCaptions(e.target.checked)} /></label>
        )}
        <p className="text-xs text-neutral-500">Photos are fitted whole (not cropped). A PDF is saved in your Memories folder;
          open it to print on any printer, or keep it to share.</p>
        {done && (
          <div className="space-y-2 rounded-lg bg-neutral-950 p-3 text-sm">
            <div className="text-emerald-400">Saved {done.pages} page{done.pages === 1 ? "" : "s"}.</div>
            <div className="flex gap-2">
              <button className="rounded bg-sky-600 px-3 py-1 hover:bg-sky-500" onClick={() => act(() => post("/api/open-pdf", { path: done.path }))}>Open to print</button>
              <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700" onClick={() => act(() => post("/api/reveal-file", { path: done.path }))}>Show in folder</button>
            </div>
          </div>
        )}
        {err && <p className="text-sm text-red-400">{err}</p>}
        <div className="flex justify-end gap-2">
          <button className="rounded px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-800" onClick={onClose}>Close</button>
          <button className="rounded bg-sky-600 px-4 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-40" disabled={busy} onClick={make}>
            {busy ? "Making the PDF…" : done ? "Make again" : "Make PDF"}</button>
        </div>
      </div>
    </div>
  );
}
