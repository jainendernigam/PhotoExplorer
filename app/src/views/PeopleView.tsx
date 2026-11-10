// People: named people, unnamed face groups to name, and a page per person
// or group where wrong faces can be removed or moved.
import { useCallback, useEffect, useState } from "react";
import { api, chipUrl, Cluster, del, FaceItem, FaceModels, FacesStatus, patch, Person, post } from "../api";
import PhotoGrid from "../components/PhotoGrid";

type Sel = { person: Person } | { cluster: Cluster } | null;

export default function PeopleView({ busy, scanState, onChanged }: { busy: boolean; scanState?: string; onChanged: () => void }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [status, setStatus] = useState<FacesStatus | null>(null);
  const [sel, setSel] = useState<Sel>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, c, s] = await Promise.all([
        api<Person[]>(`/api/people?include_hidden=${showHidden}`),
        api<Cluster[]>("/api/clusters?min_size=2&limit=200"),
        api<FacesStatus>("/api/faces/status"),
      ]);
      setPeople(p); setClusters(c); setStatus(s); setErr(null);
    } catch (e) { setErr(String(e)); }
  }, [showHidden]);
  useEffect(() => { load(); }, [load, busy, scanState]);  // groups are refreshed while faces are found
  const finding = scanState === "faces" || scanState === "clustering";

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr(String(e)); }
    await load(); onChanged();
  };

  if (sel && "person" in sel) {
    const fresh = people.find((p) => p.id === sel.person.id) ?? sel.person;
    return <PersonPage person={fresh} people={people} onBack={() => { setSel(null); load(); }} act={act} />;
  }
  if (sel && "cluster" in sel) {
    return <ClusterPage cluster={sel.cluster} people={people} onBack={() => { setSel(null); load(); }} act={act} />;
  }

  return (
    <div className="h-full overflow-y-auto p-5 space-y-8">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">People</h1>
        {status && (
          <span className="text-sm text-neutral-400">
            {status.faces.toLocaleString()} faces in {status.photos.toLocaleString()} photos
            {status.photos_todo > 0 && ` · ${status.photos_todo.toLocaleString()} photos not yet checked`}
          </span>
        )}
        <span className="flex-1" />
        <label className="flex items-center gap-1 text-xs text-neutral-400">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> Show hidden
        </label>
        <button className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700 disabled:opacity-40" disabled={busy || !status?.available}
          title="Look for faces in photos not checked yet, then regroup"
          onClick={() => act(() => post("/api/faces/run", { mode: "faces" }))}>
          {status && status.photos_todo > 0 && status.photos > 0 ? `Continue finding faces (${status.photos_todo.toLocaleString()} left)` : "Find faces"}
        </button>
        <button className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700 disabled:opacity-40" disabled={busy || !status?.available}
          title="Regroup all faces (keeps every name you gave)"
          onClick={() => act(() => post("/api/faces/run", { mode: "cluster" }))}>Regroup</button>
        <button className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700 disabled:opacity-40" disabled={busy || !status?.available}
          title="Look again in every photo with the improved face finder (keeps every name you gave)"
          onClick={() => confirm("Look for faces again in every photo?\n\nThis finds faces missed before. It takes about as long as the first time; " +
            "names you gave are kept. You can pause it and continue later.") && act(() => post("/api/faces/run", { mode: "recheck" }))}>Look again</button>
      </header>
      {status && !status.available && (
        <p className="rounded bg-amber-950/50 p-3 text-sm text-amber-300">Face recognition is not set up: missing {status.missing.join(", ")}.</p>
      )}
      {err && <p className="text-sm text-red-400">{err}</p>}
      {status?.available && <FaceModelCard busy={busy} onUse={() => act(async () => {})} />}
      {finding && (
        <p className="rounded bg-sky-950/40 p-3 text-sm text-sky-300">
          Finding faces… Groups appear here every few minutes while it runs. Closing the app pauses it; it continues where it stopped.
        </p>
      )}

      <section>
        <h2 className="mb-3 text-xs uppercase tracking-wide text-neutral-400">Named ({people.length})</h2>
        {people.length === 0 && <p className="text-sm text-neutral-500">Name a group below to start.</p>}
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
          {people.map((p) => (
            <button key={p.id} className={`group text-center ${p.hidden ? "opacity-50" : ""}`} onClick={() => setSel({ person: p })}>
              <Chip faceId={p.cover_face_id} className="mx-auto h-24 w-24 rounded-full ring-2 ring-transparent group-hover:ring-sky-500" />
              <div className="mt-1 truncate text-sm">{p.name}</div>
              <div className="text-xs text-neutral-500">{p.photos.toLocaleString()} photos</div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-xs uppercase tracking-wide text-neutral-400">Unnamed groups ({clusters.length})</h2>
        {clusters.length === 0 && <p className="text-sm text-neutral-500">No unnamed groups.</p>}
        <div className="space-y-3">
          {clusters.map((c) => (
            <ClusterRow key={c.cluster_id} c={c} people={people} busy={busy}
              onOpen={() => setSel({ cluster: c })}
              onName={(name) => act(() => post(`/api/clusters/${c.cluster_id}/name`, { name }))} />
          ))}
        </div>
      </section>

      {status && status.ungrouped > 0 && (
        <section>
          <h2 className="mb-3 text-xs uppercase tracking-wide text-neutral-400">Not in any group</h2>
          <button className="rounded-lg bg-neutral-900 px-3 py-2 text-sm hover:bg-neutral-800"
            onClick={() => setSel({ cluster: { cluster_id: -1, faces: status.ungrouped, sample_face_ids: [] } })}>
            {status.ungrouped.toLocaleString()} faces did not match any group. Show them ›
          </button>
        </section>
      )}
    </div>
  );
}

export function Chip({ faceId, className = "" }: { faceId: number | null; className?: string }) {
  return faceId ? (
    <img src={chipUrl(faceId)} loading="lazy" className={`object-cover bg-neutral-800 ${className}`} draggable={false} />
  ) : <div className={`bg-neutral-800 ${className}`} />;
}

function NameInput({ people, onSubmit, placeholder = "Who is this?", disabled }: {
  people: Person[]; onSubmit: (name: string) => void; placeholder?: string; disabled?: boolean;
}) {
  const [v, setV] = useState("");
  const submit = () => { if (v.trim()) { onSubmit(v.trim()); setV(""); } };
  return (
    <div className="flex gap-1">
      <input list="pe-people-names" value={v} disabled={disabled} placeholder={placeholder}
        onChange={(e) => setV(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()}
        className="w-44 rounded bg-neutral-800 px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-sky-500" />
      <button className="rounded bg-sky-600 px-2 text-sm hover:bg-sky-500 disabled:opacity-40" disabled={disabled || !v.trim()} onClick={submit}>OK</button>
      <datalist id="pe-people-names">{people.map((p) => <option key={p.id} value={p.name} />)}</datalist>
    </div>
  );
}

function ClusterRow({ c, people, busy, onOpen, onName }: {
  c: Cluster; people: Person[]; busy: boolean; onOpen: () => void; onName: (n: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-neutral-900 p-2">
      <button className="flex gap-1" onClick={onOpen} title="Show all faces in this group">
        {c.sample_face_ids.slice(0, 8).map((id) => <Chip key={id} faceId={id} className="h-14 w-14 rounded" />)}
      </button>
      <div className="text-xs text-neutral-400 w-20">{c.faces} faces</div>
      <NameInput people={people} onSubmit={onName} disabled={busy} />
    </div>
  );
}

// ---------------------------------------------------------------- face lists

function useFaces(query: string) {
  const [faces, setFaces] = useState<FaceItem[]>([]);
  const [more, setMore] = useState(false);
  const load = useCallback(async (after = 0) => {
    const r = await api<FaceItem[]>(`/api/faces?${query}&limit=500&after_id=${after}`);
    setFaces((f) => (after ? [...f, ...r] : r));
    setMore(r.length === 500);
  }, [query]);
  useEffect(() => { load(0); }, [load]);
  return { faces, more, loadMore: () => load(faces[faces.length - 1]?.id ?? 0), reload: () => load(0) };
}

function FaceGrid({ faces, selected, toggle }: { faces: FaceItem[]; selected: Set<number>; toggle: (id: number) => void }) {
  return (
    <div className="grid gap-1" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(88px, 1fr))" }}>
      {faces.map((f) => (
        <button key={f.id} onClick={() => toggle(f.id)} title={`${f.taken?.slice(0, 10) ?? ""}${f.assigned_by === "auto" ? " · suggested" : ""}`}
          className={`relative aspect-square overflow-hidden rounded ${selected.has(f.id) ? "ring-2 ring-sky-500" : ""}`}>
          <Chip faceId={f.id} className="h-full w-full" />
          {f.assigned_by === "auto" && <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-amber-400" />}
          {selected.has(f.id) && <span className="absolute inset-0 bg-sky-500/30" />}
        </button>
      ))}
    </div>
  );
}

function useSelection() {
  const [sel, setSel] = useState<Set<number>>(new Set());
  return {
    sel,
    toggle: (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }),
    clear: () => setSel(new Set()),
  };
}

// ---------------------------------------------------------------- person page

function PersonPage({ person, people, onBack, act }: {
  person: Person; people: Person[]; onBack: () => void; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [tab, setTab] = useState<"photos" | "faces">("photos");
  const [name, setName] = useState(person.name);
  const [birth, setBirth] = useState(person.birth_date ?? "");
  const { faces, more, loadMore, reload } = useFaces(`person_id=${person.id}`);
  const { sel, toggle, clear } = useSelection();
  const others = people.filter((p) => p.id !== person.id);

  const doAct = async (fn: () => Promise<unknown>) => { await act(fn); clear(); reload(); };

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-neutral-800 p-4">
        <button className="text-sm text-neutral-400 hover:text-white" onClick={onBack}>‹ People</button>
        <Chip faceId={person.cover_face_id} className="h-12 w-12 rounded-full" />
        <input value={name} onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== person.name && act(() => patch(`/api/people/${person.id}`, { name }))}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          className="w-48 rounded bg-transparent px-1 text-lg font-semibold outline-none hover:bg-neutral-900 focus:bg-neutral-900" />
        <span className="text-sm text-neutral-400">{person.photos.toLocaleString()} photos</span>
        <label className="flex items-center gap-1 text-xs text-neutral-400">Born
          <input type="date" value={birth} onChange={(e) => setBirth(e.target.value)}
            onBlur={() => birth !== (person.birth_date ?? "") && act(() => patch(`/api/people/${person.id}`, { birth_date: birth }))}
            className="rounded bg-neutral-900 px-1 py-0.5 text-neutral-200" />
        </label>
        <span className="flex-1" />
        <select className="rounded bg-neutral-900 px-2 py-1 text-sm" value=""
          onChange={(e) => {
            const target = others.find((p) => p.id === Number(e.target.value));
            if (target && confirm(`Merge ${person.name} into ${target.name}? All faces move to ${target.name}.`))
              act(() => post(`/api/people/${target.id}/merge`, { source_ids: [person.id] })).then(onBack);
          }}>
          <option value="">Merge into…</option>
          {others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="rounded bg-neutral-900 px-2 py-1 text-sm hover:bg-neutral-800"
          onClick={() => act(() => patch(`/api/people/${person.id}`, { hidden: !person.hidden }))}>{person.hidden ? "Unhide" : "Hide"}</button>
        <button className="rounded bg-neutral-900 px-2 py-1 text-sm text-red-400 hover:bg-neutral-800"
          onClick={() => confirm(`Remove the name "${person.name}"? Faces go back to unnamed groups; photos are not touched.`)
            && act(() => del(`/api/people/${person.id}`)).then(onBack)}>Remove name</button>
      </header>
      <nav className="flex gap-4 border-b border-neutral-900 px-4 text-sm">
        {(["photos", "faces"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`py-2 capitalize ${tab === t ? "border-b-2 border-sky-500 text-white" : "text-neutral-400"}`}>{t}</button>
        ))}
      </nav>
      {tab === "photos" ? (
        <div className="min-h-0 flex-1"><PhotoGrid filters={{ persons: [person.id] }} /></div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-4 space-y-3">
          <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 bg-neutral-950 py-2 text-sm">
            <span className="text-neutral-400">{sel.size ? `${sel.size} selected` : "Select faces that are wrong."}
              <span className="ml-2 text-xs text-amber-400">● suggested by grouping</span></span>
            {sel.size > 0 && <>
              <button className="rounded bg-amber-700 px-2 py-1 hover:bg-amber-600"
                onClick={() => doAct(() => post("/api/faces/reject", { face_ids: [...sel], person_id: person.id }))}>Not {person.name}</button>
              <button className="rounded bg-neutral-800 px-2 py-1 hover:bg-neutral-700"
                onClick={() => doAct(() => post("/api/faces/assign", { face_ids: [...sel], person_id: person.id }))}>Confirm</button>
              <NameInput people={others} placeholder="Move to…" onSubmit={(n) => {
                const p = people.find((x) => x.name.toLowerCase() === n.toLowerCase());
                doAct(() => post("/api/faces/assign", p ? { face_ids: [...sel], person_id: p.id } : { face_ids: [...sel], name: n }));
              }} />
            </>}
          </div>
          <FaceGrid faces={faces} selected={sel} toggle={toggle} />
          {more && <button className="text-sm text-sky-400" onClick={loadMore}>Load more</button>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- cluster page

function ClusterPage({ cluster, people, onBack, act }: {
  cluster: Cluster; people: Person[]; onBack: () => void; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const { faces, more, loadMore, reload } = useFaces(`cluster_id=${cluster.cluster_id}`);
  const { sel, toggle, clear } = useSelection();
  const unnamed = faces.filter((f) => f.person_id == null);
  const assign = (name: string, ids: number[]) => {
    const p = people.find((x) => x.name.toLowerCase() === name.toLowerCase());
    return post("/api/faces/assign", p ? { face_ids: ids, person_id: p.id } : { face_ids: ids, name });
  };
  return (
    <div className="h-full overflow-y-auto p-4 space-y-3">
      <header className="flex flex-wrap items-center gap-3">
        <button className="text-sm text-neutral-400 hover:text-white" onClick={onBack}>‹ People</button>
        <h1 className="text-lg font-semibold">{cluster.cluster_id < 0 ? `Not in any group · ${cluster.faces.toLocaleString()} faces` : `Unnamed group · ${unnamed.length} faces`}</h1>
        <span className="flex-1" />
        {cluster.cluster_id >= 0 && <>
          <span className="text-sm text-neutral-400">Name the whole group:</span>
          <NameInput people={people} onSubmit={(n) => act(() => post(`/api/clusters/${cluster.cluster_id}/name`, { name: n })).then(onBack)} />
        </>}
      </header>
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 bg-neutral-950 py-2 text-sm">
        <span className="text-neutral-400">{sel.size ? `${sel.size} selected` : "Or select some faces to name just those."}</span>
        {sel.size > 0 && (
          <NameInput people={people} placeholder="These are…" onSubmit={async (n) => {
            await act(() => assign(n, [...sel])); clear(); reload();
          }} />
        )}
      </div>
      <FaceGrid faces={unnamed} selected={sel} toggle={toggle} />
      {more && <button className="text-sm text-sky-400" onClick={loadMore}>Load more</button>}
    </div>
  );
}

// ---------------------------------------------------------------- better face recognition

const MODEL_NAMES: Record<string, string> = { sface: "Standard (built in)", arcface_s: "Better", arcface_l: "Best" };

function FaceModelCard({ busy, onUse }: { busy: boolean; onUse: () => void }) {
  const [m, setM] = useState<FaceModels | null>(null);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => { api<FaceModels>("/api/faces/models").then(setM).catch(() => {}); }, []);
  useEffect(load, [load]);
  const running = m && Object.values(m.downloads).some((d) => d?.state === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [running, load]);
  if (!m) return null;
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-3 text-sm">
      <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="flex-1">Face recognition: <b>{MODEL_NAMES[m.current]}</b>
          {m.current === "sface" && <span className="text-neutral-400"> · missing people? Try a better model</span>}</span>
        <span className="text-neutral-500">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-neutral-400">Better models recognise the same person across years, angles and light much more reliably.
            They come from InsightFace (free for personal, non-commercial use) and are downloaded from InsightFace's own page.
            After switching, press <b>Look again</b> so every photo is redone; names you gave are kept.</p>
          {(["sface", "arcface_s", "arcface_l"] as const).map((k) => {
            const have = m.installed.includes(k), d = m.downloads[k];
            return (
              <div key={k} className="flex items-center gap-3 rounded-lg bg-neutral-950/50 px-3 py-2">
                <span className="w-40 font-medium">{MODEL_NAMES[k]}</span>
                <span className="flex-1 text-xs text-neutral-500">{k === "sface" ? "Fast, included" : `${m.packs[k].size_mb} MB download`}</span>
                {d?.state === "running" ? (
                  <span className="w-40 text-xs"><span className="block h-1.5 overflow-hidden rounded bg-neutral-800"><span className="block h-full bg-sky-500" style={{ width: `${d.total ? (d.done / d.total) * 100 : 0}%` }} /></span>
                    {Math.round(d.done / 1e6)} / {Math.round(d.total / 1e6)} MB</span>
                ) : m.current === k ? <span className="text-xs text-emerald-400">In use</span>
                  : have ? (
                    <button className="rounded bg-sky-700 px-3 py-1 text-white hover:bg-sky-600 disabled:opacity-40" disabled={busy}
                      onClick={() => post("/api/faces/models/use", { kind: k }).then(() => { load(); onUse(); setMsg("Switched. Now press Look again so every photo is redone with it."); }).catch((e) => setMsg(String(e)))}>Use</button>
                  ) : (
                    <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700"
                      onClick={() => post("/api/faces/models/download", { kind: k }).then(load).catch((e) => setMsg(String(e)))}>Download</button>
                  )}
                {d?.state === "error" && <span className="text-xs text-red-400">{d.error}</span>}
              </div>
            );
          })}
          {msg && <p className="text-xs text-emerald-400">{msg}</p>}
        </div>
      )}
    </div>
  );
}
