import { useEffect, useState } from "react";
import { Filters } from "../api";
import PhotoGrid from "../components/PhotoGrid";
import MemoryStrip from "../components/MemoryStrip";
import Icon from "../lib/icons";
import { useAppearance } from "../lib/appearance";

// What the Library shows: everything, a kind of file, favourites or the archive.
type Show = "" | "image" | "raw" | "video" | "fav" | "archive";

export default function LibraryView({ refreshKey }: { refreshKey: number }) {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [show, setShow] = useState<Show>("");
  const look = useAppearance();

  useEffect(() => {  // search as you type, without a request per key press
    const t = setTimeout(() => setQuery(q), 350);
    return () => clearTimeout(t);
  }, [q]);

  const filters: Filters = {
    q: query,
    kind: show === "image" || show === "raw" || show === "video" ? show : undefined,
    fav: show === "fav" || undefined,
    show: show === "archive" ? "archive" : undefined,
  };
  const head = (
    <>
      <label className="pe-search flex w-80 max-w-full items-center gap-2 rounded-xl border border-neutral-700 bg-neutral-900 px-3 py-1.5 focus-within:ring-1 focus-within:ring-sky-500">
        <Icon name="search" className="h-4 w-4 text-neutral-500" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: beach, cake, Goa, file names, notes…"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        {q && <button className="text-neutral-500 hover:text-neutral-200" title="Clear" onClick={() => setQ("")}><Icon name="x" className="h-3.5 w-3.5" /></button>}
      </label>
      <select value={show} onChange={(e) => setShow(e.target.value as Show)}
        className="rounded-xl border border-neutral-700 bg-neutral-900 px-2.5 py-1.5 text-sm">
        <option value="">Photos and videos</option>
        <option value="image">Photos</option>
        <option value="raw">RAW</option>
        <option value="video">Videos</option>
        <option value="fav">♥ Favourites</option>
        <option value="archive">Archive</option>
      </select>
    </>
  );

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        {/* refreshKey remounts the grid after a scan finishes */}
        <PhotoGrid key={`${refreshKey}-${show}`} filters={filters} head={head}
          above={look.strip && !query && !show ? <MemoryStrip key={refreshKey} /> : undefined}
          emptyText={query ? "Nothing matches." : show === "fav" ? "No favourites yet: open a photo and press ♥."
            : show === "archive" ? "The archive is empty." : "No photos yet: add a folder and scan."} />
      </div>
    </div>
  );
}
