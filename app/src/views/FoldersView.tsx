import { useCallback, useEffect, useMemo, useState } from "react";
import { api, post, shownSourcesParam } from "../api";
import PhotoGrid from "../components/PhotoGrid";

type Folder = { id: number; source_id: number; path: string; files: number };
const SHOW = 400;

export default function FoldersView({ refreshKey }: { refreshKey: number }) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [unlinked, setUnlinked] = useState<Folder[]>([]);
  const [filter, setFilter] = useState("");
  const [sel, setSel] = useState<Folder | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    const only = shownSourcesParam();
    api<Folder[]>(`/api/folders${only ? `?sources=${only}` : ""}`).then(setFolders).catch(() => {});
    api<Folder[]>("/api/folders/unlinked").then(setUnlinked).catch(() => {});
  }, []);
  useEffect(load, [load, refreshKey]);

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? folders.filter((x) => x.path.toLowerCase().includes(f)) : folders;
  }, [folders, filter]);

  const unlink = async (f: Folder) => {
    if (!confirm(`Unlink "${f.path}" and its subfolders from Photo Explorer?\n\n` +
      "The files stay on disk. The app forgets them (including faces and albums for these photos) " +
      "and future scans skip this folder. You can link it again below the folder list.")) return;
    try {
      const r = await post<{ files: number; subfolders: number }>(`/api/folders/${f.id}/unlink`);
      setMsg(`Unlinked ${r.files.toLocaleString()} files${r.subfolders ? ` in ${r.subfolders + 1} folders` : ""}.`);
      if (sel?.id === f.id) setSel(null);
    } catch (e) { setMsg(String(e)); }
    load();
  };
  const relink = async (f: Folder) => {
    try { await post(`/api/folders/${f.id}/relink`); setMsg("Linked again; scanning it now."); }
    catch (e) { setMsg(String(e)); }
    load();
  };

  return (
    <div className="flex h-full">
      <aside className="flex w-96 shrink-0 flex-col border-r border-neutral-800">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Filter ${folders.length.toLocaleString()} folders…`}
          className="m-2 rounded bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:ring-1 focus:ring-sky-500" />
        {msg && <div className="mx-2 mb-2 flex gap-2 rounded bg-neutral-900 px-2 py-1 text-xs text-neutral-300"><span className="flex-1">{msg}</span><button onClick={() => setMsg(null)}>✕</button></div>}
        <ul className="flex-1 overflow-y-auto text-sm">
          {shown.slice(0, SHOW).map((f) => (
            <li key={f.id} className="group relative">
              <button onClick={() => setSel(f)} title={f.path}
                className={`flex w-full gap-2 px-3 py-1 text-left hover:bg-neutral-900 ${sel?.id === f.id ? "bg-neutral-800" : ""}`}>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate">{f.path.split(/[\\/]/).pop()}</span>
                  <span className="truncate text-xs text-neutral-500">{f.path.split(/[\\/]/).slice(0, -1).join("\\")}</span>
                </span>
                <span className="text-neutral-500 group-hover:invisible">{f.files.toLocaleString()}</span>
              </button>
              <button onClick={() => unlink(f)} title="Leave this folder out of the library (files stay on disk)"
                className="absolute right-2 top-1.5 hidden rounded bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300 hover:bg-red-800 hover:text-white group-hover:block">
                Unlink
              </button>
            </li>
          ))}
          {shown.length > SHOW && <li className="px-3 py-2 text-xs text-neutral-500">{(shown.length - SHOW).toLocaleString()} more; type to filter.</li>}
          {unlinked.length > 0 && (
            <li className="mt-3 border-t border-neutral-800 px-3 pb-3 pt-2">
              <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Unlinked ({unlinked.length})</div>
              {unlinked.map((f) => (
                <div key={f.id} className="flex items-center gap-2 py-0.5 text-xs text-neutral-400">
                  <span className="min-w-0 flex-1 truncate" title={f.path}>{f.path}</span>
                  <button className="text-sky-400 hover:underline" onClick={() => relink(f)}>Link again</button>
                </div>
              ))}
            </li>
          )}
        </ul>
      </aside>
      <div className="min-w-0 flex-1">
        {sel ? <PhotoGrid key={`${sel.id}-${refreshKey}`} filters={{ folderId: sel.id }} />
          : <p className="p-6 text-neutral-500">Choose a folder.</p>}
      </div>
    </div>
  );
}
