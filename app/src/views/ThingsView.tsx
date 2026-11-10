// Things: photos sorted by what is in them (screenshots, selfies, documents,
// receipts, dogs, cats), pets grouped by look, and the switch that sets up
// search by content (a free AI model that runs on this PC).
import { useCallback, useEffect, useState } from "react";
import { api, Category, Filters, markMedia, PetGroup, post, SmartStatus, thumbUrl } from "../api";
import PhotoGrid from "../components/PhotoGrid";
import Icon from "../lib/icons";

type Pick = { title: string; filters: Filters };

export default function ThingsView({ busy, refreshKey }: { busy: boolean; refreshKey: number }) {
  const [cats, setCats] = useState<Category[]>([]);
  const [smart, setSmart] = useState<SmartStatus | null>(null);
  const [pets, setPets] = useState<{ named: PetGroup[]; groups: PetGroup[] }>({ named: [], groups: [] });
  const [pick, setPick] = useState<Pick | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ categories: Category[]; smart: SmartStatus }>("/api/things").then((r) => { setCats(r.categories); setSmart(r.smart); }).catch(() => {});
    api<{ named: PetGroup[]; groups: PetGroup[] }>("/api/pets").then(setPets).catch(() => {});
  }, []);
  useEffect(load, [load, refreshKey, busy]);
  // follow a download
  useEffect(() => {
    if (smart?.download?.state !== "running") return;
    const t = setInterval(() => api<SmartStatus>("/api/smart/status").then(setSmart).catch(() => {}), 1000);
    return () => clearInterval(t);
  }, [smart?.download?.state]);

  if (pick) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-3 border-b border-neutral-800 px-4 py-2">
          <button className="flex items-center gap-1 text-sm text-neutral-400 hover:text-white" onClick={() => { setPick(null); load(); }}>
            <Icon name="chevronLeft" /> Things</button>
          <span className="font-semibold">{pick.title}</span>
          {pick.filters.tag === "screenshot" && (
            <button className="ml-auto flex items-center gap-1.5 rounded-xl border border-neutral-700 px-3 py-1 text-sm hover:bg-neutral-800"
              title="Hide every screenshot from the library (they stay on disk; see Library › Archive)"
              onClick={async () => {
                if (!confirm("Move all screenshots to the Archive?\n\nThey stay on disk and can be brought back from Library › Archive.")) return;
                const ids = await api<{ items: { id: number }[] }>("/api/media?tag=screenshot&limit=5000").then((r) => r.items.map((m) => m.id));
                const r = await markMedia(ids, { archived: true });
                setMsg(`${r.changed.toLocaleString()} screenshots moved to the Archive.`); setPick(null); load();
              }}><Icon name="archive" />Archive all screenshots</button>
          )}
        </div>
        <div className="min-h-0 flex-1"><PhotoGrid filters={pick.filters} /></div>
      </div>
    );
  }

  const dl = smart?.download;
  const shot = cats.find((c) => c.tag === "screenshot");
  return (
    <div className="h-full space-y-7 overflow-y-auto p-5">
      <header className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Things</h1>
        <span className="text-sm text-neutral-400">Photos sorted by what is in them</span>
      </header>
      {msg && <p className="text-sm text-emerald-400">{msg} <button onClick={() => setMsg(null)}>✕</button></p>}

      {smart && !smart.available && (
        <section className="rounded-2xl border border-sky-800 bg-sky-950/40 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Icon name="sparkles" className="h-6 w-6 text-sky-300" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold">Set up search by content</div>
              <div className="text-sm text-neutral-400">Find photos by what is in them ("beach", "birthday cake", "red car"), sort them into the groups below, and find your pets.
                Uses a free AI model (CLIP, MIT licence) that runs on this PC: a one-time download of about {smart.size_mb} MB. Your photos never leave the PC.</div>
            </div>
            {dl?.state === "running" ? (
              <div className="w-48 text-sm">
                <div className="h-2 overflow-hidden rounded bg-neutral-800"><div className="h-full bg-sky-500" style={{ width: `${dl.total ? (dl.done / dl.total) * 100 : 0}%` }} /></div>
                <div className="mt-1 text-xs text-neutral-400">Downloading… {Math.round(dl.done / 1e6)} / {Math.round(dl.total / 1e6)} MB</div>
              </div>
            ) : (
              <button className="rounded-xl bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500"
                onClick={() => post<SmartStatus["download"]>("/api/smart/download").then(() => api<SmartStatus>("/api/smart/status").then(setSmart)).catch((e) => setMsg(String(e)))}>
                Download and set up</button>
            )}
          </div>
          {dl?.state === "error" && <p className="mt-2 text-sm text-red-400">{dl.error}</p>}
        </section>
      )}
      {smart?.available && smart.todo > 0 && (
        <p className="flex items-center gap-3 rounded-xl bg-neutral-900 p-3 text-sm text-neutral-300">
          <Icon name="sparkles" className="h-4 w-4 text-sky-300" />
          {busy ? `Learning what is in your photos… ${smart.done.toLocaleString()} done, ${smart.todo.toLocaleString()} to go.`
            : `${smart.todo.toLocaleString()} photos not yet looked at.`}
          {!busy && <button className="ml-auto rounded-lg bg-neutral-800 px-3 py-1 hover:bg-neutral-700" onClick={() => post("/api/smart/run").then(load).catch((e) => setMsg(String(e)))}>Look at them now</button>}
        </p>
      )}

      {shot && shot.count >= 10 && (
        <div className="flex items-center gap-3 rounded-xl border border-amber-800/60 bg-amber-950/30 p-3 text-sm">
          <Icon name="archive" className="h-5 w-5 text-amber-300" />
          <span className="flex-1">{shot.count.toLocaleString()} screenshots are mixed in with your photos. Look through them and archive the ones you don't need.</span>
          <button className="rounded-lg bg-amber-700 px-3 py-1 text-white hover:bg-amber-600" onClick={() => setPick({ title: "Screenshots", filters: { tag: "screenshot" } })}>Review</button>
        </div>
      )}

      <section>
        <h2 className="mb-3 text-xs uppercase tracking-wide text-neutral-400">Categories</h2>
        {cats.length === 0 && <p className="text-sm text-neutral-500">{smart?.available ? "Nothing sorted yet." : "Appears once search by content is set up."}</p>}
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
          {cats.map((c) => (
            <button key={c.tag} onClick={() => setPick({ title: c.label, filters: { tag: c.tag } })}
              className="group overflow-hidden rounded-2xl bg-neutral-900 text-left ring-1 ring-neutral-800 hover:ring-sky-500">
              <img src={thumbUrl(c.cover)} alt="" loading="lazy" className="aspect-square w-full object-cover transition-transform group-hover:scale-105" />
              <div className="px-3 py-2"><div className="font-medium">{c.label}</div><div className="text-xs text-neutral-500">{c.count.toLocaleString()} photos</div></div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-3 flex items-center gap-2 text-xs uppercase tracking-wide text-neutral-400"><Icon name="paw" className="h-4 w-4" />Pets</h2>
        {pets.named.length + pets.groups.length === 0 && <p className="text-sm text-neutral-500">No pets found yet.</p>}
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
          {pets.named.map((p) => (
            <PetCard key={`n${p.id}`} p={p} onOpen={() => setPick({ title: p.name!, filters: { pet: String(p.id) } })}
              onName={(name) => post("/api/pets/name", { pet_id: p.id, name }).then(load)} />
          ))}
          {pets.groups.map((p) => (
            <PetCard key={`c${p.cluster}`} p={p} onOpen={() => setPick({ title: `A ${p.species}`, filters: { pet: `c${p.cluster}` } })}
              onName={(name) => post("/api/pets/name", { cluster: p.cluster, name }).then(load)} />
          ))}
        </div>
      </section>
    </div>
  );
}

function PetCard({ p, onOpen, onName }: { p: PetGroup; onOpen: () => void; onName: (n: string) => void }) {
  const [v, setV] = useState(p.name ?? "");
  return (
    <div className="text-center">
      <button onClick={onOpen} className="mx-auto block h-28 w-28 overflow-hidden rounded-full ring-2 ring-transparent hover:ring-sky-500">
        <img src={thumbUrl(p.cover)} alt="" loading="lazy" className="h-full w-full object-cover" />
      </button>
      <input value={v} onChange={(e) => setV(e.target.value)} placeholder={`Name this ${p.species}`}
        onKeyDown={(e) => e.key === "Enter" && v.trim() && onName(v.trim())} onBlur={() => v.trim() && v !== (p.name ?? "") && onName(v.trim())}
        className="mt-1 w-full rounded bg-transparent px-1 text-center text-sm outline-none hover:bg-neutral-900 focus:bg-neutral-900" />
      <div className="text-xs text-neutral-500">{p.photos.toLocaleString()} photos</div>
    </div>
  );
}
