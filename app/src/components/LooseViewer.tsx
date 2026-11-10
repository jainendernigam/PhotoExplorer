// Viewer for a picture opened from Explorer ("Open with Photo Explorer"): the
// file and the other pictures in its folder, whether or not they are in the
// library. Arrow keys move, Esc closes, I shows details, S presents.
import { useEffect, useState } from "react";
import { api, loosePreviewUrl, PhotoDetails, post } from "../api";
import { DetailsBlock } from "./ViewerTools";
import { closePresenter, listen, openPresenter, send } from "../lib/present";
import { fmtBytes } from "../lib/format";

type Listing = { files: { path: string; name: string; media_id: number | null }[]; index: number; folder: string };

export default function LooseViewer({ path, onClose, onAddFolder }: {
  path: string; onClose: () => void; onAddFolder: (folder: string) => void;
}) {
  const [list, setList] = useState<Listing | null>(null);
  const [i, setI] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState(true);
  const [details, setDetails] = useState<(PhotoDetails & { size: number; name: string }) | null>(null);
  const [presenting, setPresenting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    api<Listing>(`/api/loose/list?path=${encodeURIComponent(path)}`)
      .then((l) => { setList(l); setI(l.index); }).catch((e) => setErr(String(e)));
  }, [path]);

  const cur = list?.files[i];
  useEffect(() => {
    setDetails(null);
    if (!cur || !info) return;
    let alive = true;
    api<PhotoDetails & { size: number; name: string }>(`/api/loose/details?path=${encodeURIComponent(cur.path)}`)
      .then((d) => alive && setDetails(d)).catch(() => {});
    // the next and previous pictures load ahead
    [i + 1, i - 1].forEach((k) => { const f = list?.files[k]; if (f) new Image().src = loosePreviewUrl(f.path); });
    return () => { alive = false; };
  }, [cur?.path, info, i, list]);

  useEffect(() => {
    if (!presenting || !cur) return;
    const msg = { type: "show" as const, src: loosePreviewUrl(cur.path), kind: "image" as const };
    send(msg);
    return listen((m) => { if (m.type === "hello") send(msg); if (m.type === "bye") setPresenting(false); });
  }, [presenting, cur?.path]);
  const togglePresent = async () => {
    if (presenting) { await closePresenter(); setPresenting(false); return; }
    try { setNotice(await openPresenter()); setPresenting(true); } catch (e) { setNotice(String(e)); }
  };

  const n = list?.files.length ?? 0;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" && i + 1 < n) setI(i + 1);
      else if (e.key === "ArrowLeft" && i > 0) setI(i - 1);
      else if (e.key.toLowerCase() === "i") setInfo((v) => !v);
      else if (e.key.toLowerCase() === "s") togglePresent();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i, n, presenting]);
  useEffect(() => () => { if (presenting) closePresenter(); }, [presenting]);

  const inLibrary = list ? list.files.filter((f) => f.media_id != null).length : 0;
  return (
    <div data-theme="dark" className="fixed inset-0 z-50 flex bg-black">
      <div className="relative flex min-w-0 flex-1 items-center justify-center">
        {err ? <p className="text-red-400">{err}</p> : !cur ? <span className="text-neutral-500">Loading…</span>
          : <img key={cur.path} src={loosePreviewUrl(cur.path)} draggable={false} className="block max-h-[100vh] max-w-full object-contain" />}
        <button className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-xl hover:bg-white/20 disabled:opacity-20"
          disabled={i === 0} onClick={() => setI(i - 1)}>‹</button>
        <button className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-xl hover:bg-white/20 disabled:opacity-20"
          disabled={i + 1 >= n} onClick={() => setI(i + 1)}>›</button>
        <div className="absolute left-3 top-3 flex flex-wrap items-center gap-2 text-xs">
          <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={onClose}>✕ Close</button>
          <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={() => setInfo((v) => !v)}>Info (I)</button>
          <button className={`rounded px-2 py-1 ${presenting ? "bg-amber-600" : "bg-white/10 hover:bg-white/20"}`} onClick={togglePresent}>
            {presenting ? "Stop presenting (S)" : "Present (S)"}</button>
          {cur && <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={() => post("/api/reveal-file", { path: cur.path })}>Show in folder</button>}
          {list && inLibrary < n && (
            <button className="rounded bg-sky-700 px-2 py-1 hover:bg-sky-600" title="Add this folder to the library and scan it"
              onClick={() => onAddFolder(list.folder)}>Add this folder to the library</button>
          )}
          <span className="px-2 py-1 text-neutral-500">{(i + 1).toLocaleString()} / {n.toLocaleString()}</span>
          {notice && <span className="rounded bg-emerald-900/80 px-2 py-1 text-emerald-200">{notice}
            <button className="ml-2" onClick={() => setNotice(null)}>✕</button></span>}
        </div>
      </div>
      {info && cur && (
        <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-l border-neutral-800 bg-neutral-950 p-4 text-sm">
          <div><div className="text-[11px] uppercase text-neutral-500">File</div><div className="break-words">{cur.name}</div></div>
          <div><div className="text-[11px] uppercase text-neutral-500">Folder</div><div className="break-words">{list?.folder}</div></div>
          {details && <div><div className="text-[11px] uppercase text-neutral-500">Size</div><div>{fmtBytes(details.size)}</div></div>}
          {cur.media_id != null && <div className="text-xs text-emerald-400">In your library</div>}
          <DetailsBlock d={details} />
        </aside>
      )}
    </div>
  );
}
