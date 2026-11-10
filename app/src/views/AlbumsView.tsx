// Albums: hand-picked collections. They never change or move files.
import TopBar from "../lib/topbar";
import { useCallback, useEffect, useState } from "react";
import { Album, api, del, patch, post, thumbUrl } from "../api";
import PhotoGrid from "../components/PhotoGrid";

export default function AlbumsView() {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [open, setOpen] = useState<Album | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => api<Album[]>("/api/albums").then((a) => {
    setAlbums(a);
    setOpen((o) => (o ? a.find((x) => x.id === o.id) ?? null : o));
  }).catch((e) => setErr(String(e))), []);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr(String(e)); }
    load();
  };

  if (open) return <AlbumPage album={open} onBack={() => { setOpen(null); load(); }} act={act} onChanged={load} err={err} />;

  return (
    <div className="h-full overflow-y-auto p-5 space-y-5">
      <TopBar>
        <span className="font-semibold">Albums</span>
        <span className="text-xs text-neutral-500">{albums.length} albums · adding or removing photos never touches the files</span>
        <span className="flex-1" />
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New album name"
          onKeyDown={(e) => e.key === "Enter" && name.trim() && act(async () => { await post("/api/albums", { name }); setName(""); })}
          className="w-52 rounded bg-neutral-900 px-2 py-1.5 text-sm outline-none focus:ring-1 focus:ring-sky-500" />
        <button className="rounded bg-sky-700 px-3 py-1.5 text-sm hover:bg-sky-600 disabled:opacity-40" disabled={!name.trim()}
          onClick={() => act(async () => { await post("/api/albums", { name }); setName(""); })}>New album</button>
      </TopBar>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {albums.length === 0 && (
        <p className="text-neutral-500">No albums yet. In any photo grid tick photos, then <b>Add to album</b>.</p>
      )}
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>
        {albums.map((a) => (
          <button key={a.id} className="group text-left" onClick={() => setOpen(a)}>
            <div className="aspect-square overflow-hidden rounded-lg bg-neutral-800 ring-2 ring-transparent group-hover:ring-sky-500">
              {a.cover_media_id && <img src={thumbUrl(a.cover_media_id)} className="h-full w-full object-cover" />}
            </div>
            <div className="mt-1 truncate text-sm">{a.name}</div>
            <div className="text-xs text-neutral-500">{a.photos.toLocaleString()} photos</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function AlbumPage({ album, onBack, act, onChanged, err }: {
  album: Album; onBack: () => void; act: (fn: () => Promise<unknown>) => Promise<void>; onChanged: () => void; err: string | null;
}) {
  const [name, setName] = useState(album.name);
  return (
    <div className="flex h-full flex-col">
      {err && <p className="px-4 pt-2 text-sm text-red-400">{err}</p>}
      <div className="min-h-0 flex-1">
        <PhotoGrid filters={{ albumId: album.id }} onAlbumChanged={onChanged} head={<>
        <button className="text-sm text-neutral-400 hover:text-white" onClick={onBack}>‹ Albums</button>
        <input value={name} onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== album.name && act(() => patch(`/api/albums/${album.id}`, { name }))}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          className="w-56 rounded bg-transparent px-1 font-semibold outline-none hover:bg-neutral-900 focus:bg-neutral-900" /></>}
          tail={<button className="rounded-xl px-2 py-1 text-sm text-red-400 hover:bg-neutral-800"
            onClick={() => confirm(`Delete the album "${album.name}"? The photos themselves are not touched.`)
              && act(() => del(`/api/albums/${album.id}`)).then(onBack)}>Delete album</button>}
          emptyText="This album is empty. Add photos from any grid: tick them, then Add to album." />
      </div>
    </div>
  );
}
