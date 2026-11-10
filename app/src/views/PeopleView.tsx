// People: named people, unnamed face groups to name, "Is this X?" questions
// about faces the app isn't sure of, and a page per person or group where
// wrong faces can be removed or moved.
import { useCallback, useEffect, useState } from "react";
import { api, chipUrl, Cluster, del, FaceItem, FaceModels, FacesStatus, patch, Person, PetGroup, post, Suggestion, thumbUrl } from "../api";
import PhotoGrid, { MoreMenu } from "../components/PhotoGrid";
import TopBar from "../lib/topbar";
import Icon from "../lib/icons";
import { ageAt } from "../lib/format";
import { openLibrary } from "../lib/nav";

type Sel = { person: Person } | { cluster: Cluster } | { review: true } | null;
const HIDDEN_GROUPS = "pe.people.hiddenGroups";
const loadHiddenGroups = (): number[] => { try { return JSON.parse(localStorage.getItem(HIDDEN_GROUPS) || "[]"); } catch { return []; } };

export default function PeopleView({ busy, scanState, onChanged, openId }: {
  busy: boolean; scanState?: string; onChanged: () => void; openId?: number;
}) {
  const [people, setPeople] = useState<Person[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [status, setStatus] = useState<FacesStatus | null>(null);
  const [pets, setPets] = useState<PetGroup[]>([]);
  const [sel, setSel] = useState<Sel>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [find, setFind] = useState("");
  const [hiddenGroups, setHiddenGroups] = useState<number[]>(loadHiddenGroups);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, c, s] = await Promise.all([
        api<Person[]>(`/api/people?include_hidden=${showHidden}`),
        api<Cluster[]>("/api/clusters?min_size=2&limit=200"),
        api<FacesStatus>("/api/faces/status"),
      ]);
      setPeople(p); setClusters(c); setStatus(s); setErr(null);
      api<{ named: PetGroup[] }>("/api/pets").then((r) => setPets(r.named)).catch(() => {});
    } catch (e) { setErr(String(e)); }
  }, [showHidden]);
  useEffect(() => { load(); }, [load, busy, scanState]);  // groups are refreshed while faces are found
  useEffect(() => {    // "open this person" from elsewhere (photo viewer)
    if (openId == null) return;
    api<Person[]>("/api/people?include_hidden=true").then((all) => {
      const p = all.find((x) => x.id === openId);
      if (p) setSel({ person: p });
    }).catch(() => {});
  }, [openId]);
  const finding = scanState === "faces" || scanState === "clustering";

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr(String(e)); }
    await load(); onChanged();
  };
  const hideGroup = (id: number) => {
    const n = [...hiddenGroups, id];
    setHiddenGroups(n);
    try { localStorage.setItem(HIDDEN_GROUPS, JSON.stringify(n.slice(-2000))); } catch { /* fine */ }
  };

  if (sel && "person" in sel) {
    const fresh = people.find((p) => p.id === sel.person.id) ?? sel.person;
    return <PersonPage person={fresh} people={people} onBack={() => { setSel(null); load(); }} act={act} />;
  }
  if (sel && "cluster" in sel) {
    return <ClusterPage cluster={sel.cluster} people={people} onBack={() => { setSel(null); load(); }} act={act} />;
  }
  if (sel && "review" in sel) {
    return <ReviewPage onBack={() => { setSel(null); load(); onChanged(); }} />;
  }

  const f = find.trim().toLowerCase();
  const shownPeople = f ? people.filter((p) => p.name.toLowerCase().includes(f)) : people;
  const shownGroups = clusters.filter((c) => !hiddenGroups.includes(c.cluster_id));
  return (
    <div className="h-full space-y-7 overflow-y-auto p-5">
      <TopBar>
        <label className="pe-search flex min-w-[12rem] max-w-sm flex-1 items-center gap-2 rounded-xl border border-neutral-700 bg-neutral-900 px-3 py-1.5 focus-within:ring-1 focus-within:ring-sky-500">
          <Icon name="search" className="h-4 w-4 text-neutral-500" />
          <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Search people" className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        </label>
        <span className="whitespace-nowrap text-xs text-neutral-500">
          {people.length} people · {shownGroups.length} groups to name
          {status && status.photos_todo > 0 && ` · ${status.photos_todo.toLocaleString()} photos not yet checked`}
        </span>
        <span className="flex-1" />
        {status && (status.suggestions ?? 0) > 0 && (
          <button className="flex items-center gap-1.5 rounded-xl bg-amber-500 px-3 py-1.5 font-medium text-black hover:bg-amber-400" onClick={() => setSel({ review: true })}
            title="Faces the app isn't sure about">Review <span className="rounded-full bg-black/20 px-1.5 text-xs">{status.suggestions}</span></button>
        )}
        <div className="pe-toolbar flex items-center overflow-visible rounded-xl border border-neutral-700 bg-neutral-900">
          <button className="border-r border-neutral-700 px-3 py-1.5 hover:bg-neutral-800 disabled:opacity-40" disabled={busy || !status?.available}
            title="Look for faces in photos not checked yet; new faces join the people you named"
            onClick={() => act(() => post("/api/faces/run", { mode: "faces" }))}>
            {status && status.photos_todo > 0 && status.photos > 0 ? `Continue (${status.photos_todo.toLocaleString()} left)` : "Find faces"}
          </button>
          <MoreMenu up={false} items={[
            { label: "Regroup all faces", hint: "Keeps every name you gave", fn: () => act(() => post("/api/faces/run", { mode: "cluster" })) },
            { label: "Look again in every photo", hint: "Finds faces missed before; slow", fn: () => {
              if (confirm("Look for faces again in every photo?\n\nThis finds faces missed before. It takes about as long as the first time; names you gave are kept. You can pause it and continue later."))
                act(() => post("/api/faces/run", { mode: "recheck" }));
            } },
            { label: showHidden ? "Leave out hidden people" : "Show hidden people", fn: () => setShowHidden((v) => !v) },
            ...(hiddenGroups.length ? [{ label: `Show ${hiddenGroups.length} hidden groups again`, fn: () => { setHiddenGroups([]); localStorage.removeItem(HIDDEN_GROUPS); } }] : []),
          ]} />
        </div>
      </TopBar>
      {status && !status.available && (
        <p className="rounded bg-amber-950/50 p-3 text-sm text-amber-300">Face recognition is not set up: missing {status.missing.join(", ")}.</p>
      )}
      {err && <p className="text-sm text-red-400">{err}</p>}
      {status && (status.photos_failed ?? 0) > 0 && (
        <p className="rounded-xl bg-neutral-900 p-3 text-sm text-neutral-400">
          {status.photos_failed!.toLocaleString()} photos could not be checked for faces after three tries (damaged or unreadable files). They are left out.</p>
      )}
      {(status?.suggestions ?? 0) > 0 && <SamePerson onDone={() => { load(); onChanged(); }} onAll={() => setSel({ review: true })} />}
      {status?.available && <FaceModelCard busy={busy} onUse={() => act(async () => {})} />}
      {finding && (
        <p className="rounded bg-sky-950/40 p-3 text-sm text-sky-300">
          Finding faces… New faces join the people you named; faces the app isn't sure of wait for you under Review.
        </p>
      )}

      <section>
        <h2 className="mb-3 text-xs uppercase tracking-wide text-neutral-400">Named ({shownPeople.length})</h2>
        {people.length === 0 && <p className="text-sm text-neutral-500">Name a group below to start.</p>}
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
          {shownPeople.map((p) => (
            <button key={p.id} className={`group text-center ${p.hidden ? "opacity-50" : ""}`} onClick={() => setSel({ person: p })}>
              <Chip faceId={p.cover_face_id} className="mx-auto h-24 w-24 rounded-full ring-2 ring-transparent group-hover:ring-sky-500" />
              <div className="mt-1 truncate text-sm">{p.name}</div>
              <div className="text-xs text-neutral-500">{p.photos.toLocaleString()} photos</div>
            </button>
          ))}
        </div>
      </section>

      {pets.length > 0 && !f && (
        <section>
          <h2 className="mb-3 flex items-center gap-1.5 text-xs uppercase tracking-wide text-neutral-400"><Icon name="paw" className="h-4 w-4" />Pets</h2>
          <div className="flex gap-4 overflow-x-auto pb-1">
            {pets.map((p) => (
              <button key={p.id} className="w-24 shrink-0 text-center" onClick={() => openLibrary({ pet: String(p.id), label: p.name ?? "Pet" })}>
                <img src={thumbUrl(p.cover)} alt="" className="mx-auto h-20 w-20 rounded-full object-cover" />
                <div className="mt-1 truncate text-sm">{p.name}</div>
                <div className="text-xs text-neutral-500">{p.photos.toLocaleString()} photos</div>
              </button>
            ))}
          </div>
        </section>
      )}

      {!f && (
        <section>
          <h2 className="mb-3 text-xs uppercase tracking-wide text-neutral-400">Unnamed groups ({shownGroups.length})</h2>
          {shownGroups.length === 0 && <p className="text-sm text-neutral-500">No unnamed groups.</p>}
          <div className="space-y-3">
            {shownGroups.map((c) => (
              <ClusterRow key={c.cluster_id} c={c} people={people} busy={busy}
                onOpen={() => setSel({ cluster: c })} onHide={() => hideGroup(c.cluster_id)}
                onName={(name) => act(() => post(`/api/clusters/${c.cluster_id}/name`, { name }))} />
            ))}
          </div>
        </section>
      )}

      {status && status.ungrouped > 0 && !f && (
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

// ---------------------------------------------------------------- "Is this X?"

// A few questions at a time at the top of People: one person, up to three faces.
function SamePerson({ onDone, onAll }: { onDone: () => void; onAll: () => void }) {
  const [list, setList] = useState<Suggestion[]>([]);
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(0);
  const [skipped, setSkipped] = useState<number[]>([]);
  useEffect(() => {
    api<Suggestion[]>("/api/faces/suggestions?limit=60").then((r) => { setList(r); setTotal(r.length); }).catch(() => {});
  }, []);
  const open = list.filter((s) => !skipped.includes(s.face_id));
  if (!open.length) return null;
  const who = open[0].person_id;
  const three = open.filter((s) => s.person_id === who).slice(0, 3);
  const answer = async (yes: boolean | null) => {
    const ids = three.map((s) => s.face_id);
    if (yes === null) setSkipped((s) => [...s, ...ids]);
    else {
      await post(yes ? "/api/faces/assign" : "/api/faces/reject", { face_ids: ids, person_id: who }).catch(() => {});
      setList((l) => l.filter((s) => !ids.includes(s.face_id)));
      onDone();
    }
    setDone((d) => d + ids.length);
  };
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-2xl border border-amber-700/60 bg-amber-950/30 p-4">
      <div className="flex -space-x-2">
        {three.map((s) => <img key={s.face_id} src={chipUrl(s.face_id)} alt="" className="h-16 w-16 rounded-full border-2 border-neutral-950 object-cover" />)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-lg font-semibold">Is this {three[0].person_name}?</div>
        <div className="text-xs text-neutral-400">{Math.min(done + three.length, Math.max(total, done + three.length))} of {Math.max(total, done + open.length)} · the app wasn't sure about {three.length > 1 ? "these faces" : "this face"}</div>
      </div>
      <div className="flex gap-2">
        <button className="rounded-xl bg-emerald-600 px-4 py-1.5 font-medium text-white hover:bg-emerald-500" onClick={() => answer(true)}>Yes</button>
        <button className="rounded-xl bg-neutral-800 px-4 py-1.5 hover:bg-neutral-700" onClick={() => answer(false)}>No</button>
        <button className="rounded-xl px-3 py-1.5 text-neutral-400 hover:bg-neutral-800" onClick={() => answer(null)}>Not sure</button>
        <button className="rounded-xl px-3 py-1.5 text-sky-400 hover:bg-neutral-800" onClick={onAll}>See all</button>
      </div>
    </div>
  );
}

// The review list, a page at a time, surest first. Also used as a strip on a person's page.
function ReviewPage({ onBack, personId, onChanged }: { onBack?: () => void; personId?: number; onChanged?: () => void }) {
  const [list, setList] = useState<Suggestion[]>([]);
  const [after, setAfter] = useState<number[]>([]);   // stack of page starts
  const [photo, setPhoto] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const PAGE = personId != null ? 8 : 24;
  const fetchPage = useCallback((start?: number) => {
    const q = new URLSearchParams({ limit: String(PAGE) });
    if (personId != null) q.set("person_id", String(personId));
    if (start != null) q.set("after_score", String(start));
    api<Suggestion[]>(`/api/faces/suggestions?${q}`).then(setList).catch(() => setList([]));
  }, [personId, PAGE]);
  useEffect(() => { fetchPage(after[after.length - 1]); }, [fetchPage, after]);
  const answer = async (yes: boolean, items: Suggestion[]) => {
    setBusy(true);
    const by = new Map<number, number[]>();
    items.forEach((s) => by.set(s.person_id, [...(by.get(s.person_id) ?? []), s.face_id]));
    for (const [pid, ids] of by) await post(yes ? "/api/faces/assign" : "/api/faces/reject", { face_ids: ids, person_id: pid }).catch(() => {});
    setBusy(false);
    fetchPage(after[after.length - 1]);
    onChanged?.();
  };
  const card = (s: Suggestion) => (
    <div key={s.face_id} className="rounded-xl bg-neutral-900 p-2 text-center ring-1 ring-neutral-800">
      <button onClick={() => setPhoto(s.media_id)} title="See the whole photo"><img src={chipUrl(s.face_id)} alt="" className="mx-auto h-24 w-24 rounded-full object-cover" /></button>
      <div className="mt-1 text-sm">Is this <b>{s.person_name}</b>?</div>
      <div className="mt-2 flex justify-center gap-1.5 text-sm">
        <button disabled={busy} className="rounded-lg bg-emerald-700 px-3 py-0.5 text-white hover:bg-emerald-600" onClick={() => answer(true, [s])}>Yes</button>
        <button disabled={busy} className="rounded-lg bg-neutral-800 px-3 py-0.5 hover:bg-neutral-700" onClick={() => answer(false, [s])}>No</button>
      </div>
    </div>
  );
  const viewer = photo != null && (
    <div data-theme="dark" className="fixed inset-0 z-50 grid place-items-center bg-black/90 p-6" onClick={() => setPhoto(null)}>
      <img src={thumbUrl(photo)} alt="" className="max-h-full max-w-full object-contain" />
    </div>
  );
  if (personId != null) {   // the strip on a person's page
    if (!list.length) return null;
    return (
      <div className="border-b border-neutral-800 bg-amber-950/20 px-4 py-3">
        <div className="mb-2 flex items-center gap-3 text-sm"><b>Review</b><span className="text-neutral-400">Faces that may be {list[0].person_name}</span>
          <button disabled={busy} className="ml-auto rounded-lg bg-emerald-700 px-3 py-0.5 text-white hover:bg-emerald-600" onClick={() => answer(true, list)}>Yes to all these</button></div>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>{list.map(card)}</div>
        {viewer}
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <TopBar>
        <button className="flex items-center gap-1 text-neutral-400 hover:text-white" onClick={onBack}><Icon name="chevronLeft" />People</button>
        <span className="font-semibold">Review faces</span>
        <span className="text-xs text-neutral-500">Surest first. A "No" is never asked again.</span>
        <span className="flex-1" />
        {list.length > 0 && <button disabled={busy} className="rounded-xl bg-emerald-600 px-3 py-1.5 font-medium text-white hover:bg-emerald-500" onClick={() => answer(true, list)}>Yes to all on this page</button>}
      </TopBar>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        {list.length === 0 && <p className="text-neutral-400">Nothing to review. New faces the app isn't sure about will wait here.</p>}
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))" }}>{list.map(card)}</div>
        <div className="flex items-center gap-2">
          {after.length > 0 && <button className="rounded-lg bg-neutral-800 px-3 py-1 text-sm hover:bg-neutral-700" onClick={() => setAfter((a) => a.slice(0, -1))}>‹ Previous</button>}
          {list.length === PAGE && <button className="rounded-lg bg-neutral-800 px-3 py-1 text-sm hover:bg-neutral-700" onClick={() => setAfter((a) => [...a, list[list.length - 1].score])}>Skip this page ›</button>}
        </div>
        {viewer}
      </div>
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

function ClusterRow({ c, people, busy, onOpen, onName, onHide }: {
  c: Cluster; people: Person[]; busy: boolean; onOpen: () => void; onName: (n: string) => void; onHide: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-neutral-900 p-2">
      <button className="flex gap-1" onClick={onOpen} title="Show all faces in this group">
        {c.sample_face_ids.slice(0, 8).map((id) => <Chip key={id} faceId={id} className="h-14 w-14 rounded" />)}
      </button>
      <div className="text-xs text-neutral-400 w-20">{c.faces} faces</div>
      <NameInput people={people} onSubmit={onName} disabled={busy} />
      <button className="ml-auto rounded px-2 py-1 text-xs text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200" title="Hide this group (strangers, crowds)" onClick={onHide}>Hide</button>
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

// One header row in the top bar: ‹ People · face · name (count, age) · Photos | Faces · Create ▾ · ⋯ · Look.
function PersonPage({ person, people, onBack, act }: {
  person: Person; people: Person[]; onBack: () => void; act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [tab, setTab] = useState<"photos" | "faces">("photos");
  const [name, setName] = useState(person.name);
  const [dialog, setDialog] = useState<null | "birthday" | "merge">(null);
  const [birth, setBirth] = useState(person.birth_date ?? "");
  const [mergeTo, setMergeTo] = useState("");
  const { faces, more, loadMore, reload } = useFaces(`person_id=${person.id}`);
  const { sel, toggle, clear } = useSelection();
  const others = people.filter((p) => p.id !== person.id);
  const now = new Date();
  const age = person.birth_date ? ageAt(person.birth_date, `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`) : null;

  const doAct = async (fn: () => Promise<unknown>) => { await act(fn); clear(); reload(); };
  const menu = (
    <MoreMenu up={false} items={[
      { label: "Set birthday…", hint: "Shows ages on photos", fn: () => setDialog("birthday") },
      { label: "Merge into…", hint: "Same person under two names", fn: () => setDialog("merge") },
      { label: "Show in Library", hint: "With people and date filters", fn: () => openLibrary({ persons: [person.id] }) },
      { label: person.hidden ? "Show this person again" : "Hide this person", fn: () => act(() => patch(`/api/people/${person.id}`, { hidden: !person.hidden })) },
      { label: "Remove name", hint: "Faces go back to unnamed groups", danger: true, fn: () => {
        if (confirm(`Remove the name "${person.name}"? Faces go back to unnamed groups; photos are not touched.`))
          act(() => del(`/api/people/${person.id}`)).then(onBack);
      } },
    ]} />
  );
  const head = (
    <>
      <button className="flex items-center gap-0.5 text-neutral-400 hover:text-white" onClick={onBack}><Icon name="chevronLeft" />People</button>
      <Chip faceId={person.cover_face_id} className="h-9 w-9 rounded-full" />
      <div className="min-w-0 leading-tight">
        <input value={name} onChange={(e) => setName(e.target.value)} title="Rename"
          onBlur={() => name.trim() && name !== person.name && act(() => patch(`/api/people/${person.id}`, { name }))}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          className="w-44 rounded bg-transparent px-1 font-semibold outline-none hover:bg-neutral-900 focus:bg-neutral-900" />
        <div className="px-1 text-xs text-neutral-500">{person.photos.toLocaleString()} photos · {age
          ? `age ${age}` : <button className="text-sky-400 hover:underline" onClick={() => setDialog("birthday")}>add birthday</button>}</div>
      </div>
      <span className="ml-2 flex overflow-hidden rounded-full border border-neutral-700 text-[13px]">
        {(["photos", "faces"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-1 capitalize ${tab === t ? "bg-sky-950 text-sky-300" : "text-neutral-400 hover:text-neutral-200"}`}>{t}</button>
        ))}
      </span>
    </>
  );

  return (
    <div className="flex h-full flex-col">
      {tab === "faces" && <TopBar>{head}<span className="flex-1" />{menu}</TopBar>}
      <ReviewPage personId={person.id} onChanged={reload} />
      {tab === "photos" ? (
        <div className="min-h-0 flex-1"><PhotoGrid filters={{ persons: [person.id] }} head={head} tail={menu}
          sectionNote={person.birth_date ? (m) => `age ${ageAt(person.birth_date!, m)}` : undefined} /></div>
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
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
      {dialog && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60" onClick={(e) => e.target === e.currentTarget && setDialog(null)}>
          <div className="w-80 space-y-3 rounded-xl bg-neutral-900 p-4 text-sm shadow-2xl">
            {dialog === "birthday" ? <>
              <div className="font-semibold">{person.name}'s birthday</div>
              <input type="date" value={birth} onChange={(e) => setBirth(e.target.value)} className="w-full rounded bg-neutral-800 px-2 py-1" />
              <div className="flex justify-end gap-2">
                {person.birth_date && <button className="mr-auto text-xs text-neutral-400 hover:text-red-400" onClick={() => { act(() => patch(`/api/people/${person.id}`, { birth_date: "" })); setDialog(null); }}>Remove</button>}
                <button className="rounded px-3 py-1 hover:bg-neutral-800" onClick={() => setDialog(null)}>Cancel</button>
                <button className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-500" disabled={!birth}
                  onClick={() => { act(() => patch(`/api/people/${person.id}`, { birth_date: birth })); setDialog(null); }}>Save</button>
              </div>
            </> : <>
              <div className="font-semibold">Merge {person.name} into…</div>
              <select value={mergeTo} onChange={(e) => setMergeTo(e.target.value)} className="w-full rounded bg-neutral-800 px-2 py-1">
                <option value="">Choose a person</option>
                {others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <div className="flex justify-end gap-2">
                <button className="rounded px-3 py-1 hover:bg-neutral-800" onClick={() => setDialog(null)}>Cancel</button>
                <button className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-500 disabled:opacity-40" disabled={!mergeTo}
                  onClick={() => { const t = others.find((p) => p.id === Number(mergeTo)); if (t) act(() => post(`/api/people/${t.id}/merge`, { source_ids: [person.id] })).then(onBack); }}>Merge</button>
              </div>
            </>}
          </div>
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
          {m.runtime_ok === false && <p className="rounded bg-amber-950/50 p-2 text-xs text-amber-300">This copy of the app lacks a part (onnxruntime) that the better models need. Update the app to use them.</p>}
          {(["sface", "arcface_s", "arcface_l"] as const).filter((k) => k === "sface" || m.runtime_ok !== false).map((k) => {
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
