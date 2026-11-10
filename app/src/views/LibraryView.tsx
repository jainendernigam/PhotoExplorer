// Library: every photo, with search and (since Timeline joined Library) people
// and date filters at the top. The top bar beside Look holds the search box,
// "Photos and videos" and the count; the row below picks people (Together /
// Any of them), dates, favourites and the archive. With one person picked who
// has a birth date, month headers show their age.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Category, chipUrl, Filters, Person, Place } from "../api";
import PhotoGrid from "../components/PhotoGrid";
import MemoryStrip from "../components/MemoryStrip";
import Icon from "../lib/icons";
import { useAppearance } from "../lib/appearance";
import { ageAt } from "../lib/format";
import { LibraryPreset } from "../lib/nav";

type Kind = "" | "image" | "raw" | "video";
type Dates = { from?: string; to?: string; key: "all" | "year" | "five" | "custom" };
type Extra = { place?: number; folderId?: number; tag?: string; pet?: string; label: string };
const thisYear = new Date().getFullYear();

export default function LibraryView({ refreshKey, preset }: { refreshKey: number; preset?: LibraryPreset }) {
  const [q, setQ] = useState(preset?.q ?? "");
  const [query, setQuery] = useState(preset?.q ?? "");
  const [kind, setKind] = useState<Kind>("");
  const [show, setShow] = useState<"" | "fav" | "archive">("");
  const [persons, setPersons] = useState<number[]>(preset?.persons ?? []);
  const [mode, setMode] = useState<"all" | "any">(preset?.personsMode ?? "all");
  const [dates, setDates] = useState<Dates>(preset?.dateFrom || preset?.dateTo ? { from: preset.dateFrom, to: preset.dateTo, key: "custom" } : { key: "all" });
  const [extra, setExtra] = useState<Extra | null>(preset && (preset.place != null || preset.folderId != null || preset.tag || preset.pet)
    ? { place: preset.place, folderId: preset.folderId, tag: preset.tag, pet: preset.pet, label: preset.label ?? "" } : null);
  const [people, setPeople] = useState<Person[]>([]);
  const [total, setTotal] = useState(0);
  const look = useAppearance();

  // A jump from elsewhere (a person's page, a search suggestion) replaces the filters.
  useEffect(() => {
    if (!preset) return;
    setQ(preset.q ?? ""); setQuery(preset.q ?? "");
    setPersons(preset.persons ?? []); setMode(preset.personsMode ?? "all");
    setDates(preset.dateFrom || preset.dateTo ? { from: preset.dateFrom, to: preset.dateTo, key: "custom" } : { key: "all" });
    setExtra(preset.place != null || preset.folderId != null || preset.tag || preset.pet
      ? { place: preset.place, folderId: preset.folderId, tag: preset.tag, pet: preset.pet, label: preset.label ?? "" } : null);
  }, [preset]);
  useEffect(() => { api<Person[]>("/api/people").then(setPeople).catch(() => {}); }, [refreshKey]);
  useEffect(() => {  // search as you type, without a request per key press
    const t = setTimeout(() => setQuery(q), 350);
    return () => clearTimeout(t);
  }, [q]);

  const filters: Filters = {
    q: query, kind: kind || undefined, fav: show === "fav" || undefined, show: show === "archive" ? "archive" : undefined,
    persons: persons.length ? persons : undefined, personsMode: mode, dateFrom: dates.from, dateTo: dates.to,
    place: extra?.place, folderId: extra?.folderId, tag: extra?.tag, pet: extra?.pet,
  };
  const chosen = persons.map((id) => people.find((p) => p.id === id)).filter(Boolean) as Person[];
  const single = chosen.length === 1 ? chosen[0] : undefined;
  const filtered = !!(persons.length || dates.key !== "all" || extra || show || query);
  const clearAll = () => { setPersons([]); setDates({ key: "all" }); setExtra(null); setShow(""); setQ(""); setQuery(""); setKind(""); };
  const toggle = (id: number) => setPersons((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  const summary = [
    chosen.length ? `${joinNames(chosen.map((p) => p.name))}${chosen.length > 1 ? (mode === "all" ? ", together" : ", any of them") : ""}` : "",
    dates.key !== "all" ? dateLabel(dates) : "",
    extra?.label ?? "", show === "fav" ? "Favourites" : show === "archive" ? "Archive" : "",
    query ? `“${query}”` : "",
  ].filter(Boolean);

  const head = (
    <>
      <SearchBox q={q} setQ={setQ} people={people}
        onDates={(from, to) => { setQ(""); setQuery(""); setDates({ from, to, key: "custom" }); }}
        onPerson={(p) => { setQ(""); setQuery(""); setPersons((c) => (c.includes(p.id) ? c : [...c, p.id])); }}
        onExtra={(x) => { setQ(""); setQuery(""); setExtra(x); }} />
      <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}
        className="rounded-xl border border-neutral-700 bg-neutral-900 px-2.5 py-1.5 text-sm">
        <option value="">Photos and videos</option>
        <option value="image">Photos</option>
        <option value="raw">RAW</option>
        <option value="video">Videos</option>
      </select>
    </>
  );

  return (
    <div className="flex h-full flex-col">
      {/* People and dates (Timeline's filters), favourites and archive */}
      <div className="flex flex-wrap items-center gap-2 px-3 pb-1 pt-2 text-sm">
        <Faces people={people} chosen={persons} toggle={toggle} />
        {persons.length > 1 && (
          <span className="flex overflow-hidden rounded-full border border-neutral-700">
            <Seg on={mode === "all"} onClick={() => setMode("all")}>Together</Seg>
            <Seg on={mode === "any"} onClick={() => setMode("any")}>Any of them</Seg>
          </span>
        )}
        <span className="mx-1 h-6 w-px bg-neutral-800" />
        <Pill on={dates.key === "all"} onClick={() => setDates({ key: "all" })}>All dates</Pill>
        <Pill on={dates.key === "year"} onClick={() => setDates({ key: "year", from: `${thisYear}-01-01` })}>This year</Pill>
        <Pill on={dates.key === "five"} onClick={() => setDates({ key: "five", from: `${thisYear - 4}-01-01` })}>Last 5 years</Pill>
        <CustomDates dates={dates} onChange={setDates} />
        <span className="mx-1 h-6 w-px bg-neutral-800" />
        <Pill on={show === "fav"} onClick={() => setShow((s) => (s === "fav" ? "" : "fav"))}><Icon name="heart" className="h-3.5 w-3.5" filled={show === "fav"} />Favourites</Pill>
        <Pill on={show === "archive"} onClick={() => setShow((s) => (s === "archive" ? "" : "archive"))}><Icon name="archive" className="h-3.5 w-3.5" />Archive</Pill>
        {extra && <Pill on onClick={() => setExtra(null)} title="Remove this filter">{extra.label}<Icon name="x" className="h-3 w-3" /></Pill>}
      </div>
      {filtered && (
        <div className="px-4 pb-1 text-xs text-neutral-400">
          {summary.join(" · ")} · {total.toLocaleString()} photo{total === 1 ? "" : "s"}
          <button className="ml-2 text-sky-400 hover:underline" onClick={clearAll}>Clear</button>
        </div>
      )}
      <div className="min-h-0 flex-1">
        {/* refreshKey remounts the grid after a scan finishes */}
        <PhotoGrid key={`${refreshKey}-${show}`} filters={filters} head={head} onTotal={setTotal}
          above={look.strip && !filtered && !kind ? <MemoryStrip key={refreshKey} /> : undefined}
          sectionNote={single?.birth_date ? (m) => `${single.name} ${ageAt(single.birth_date!, m)}` : undefined}
          emptyText={persons.length || dates.key !== "all" ? "No photos of these people in these dates."
            : query ? "Nothing matches." : show === "fav" ? "No favourites yet: open a photo and press ♥."
            : show === "archive" ? "The archive is empty." : "No photos yet: add a folder and scan."}
          emptyAction={filtered ? { label: "Clear filters", fn: clearAll } : undefined} />
      </div>
    </div>
  );
}

const joinNames = (n: string[]) => (n.length <= 1 ? n.join("") : `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`);
function dateLabel(d: Dates): string {
  if (d.key === "year") return "This year";
  if (d.key === "five") return "Last 5 years";
  const y = (s?: string) => s?.slice(0, 4);
  if (d.from && d.to) return y(d.from) === y(d.to) ? `${y(d.from)}` : `${y(d.from)} – ${y(d.to)}`;
  return d.from ? `From ${d.from}` : d.to ? `Until ${d.to}` : "All dates";
}

function Pill({ on, onClick, children, title }: { on?: boolean; onClick: () => void; children: React.ReactNode; title?: string }) {
  return (
    <button title={title} onClick={onClick}
      className={`flex items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-[13px] ${on
        ? "border-sky-500 bg-sky-950 text-sky-300" : "border-neutral-700 text-neutral-400 hover:border-neutral-500 hover:text-neutral-200"}`}>{children}</button>
  );
}
function Seg({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className={`px-3 py-1 text-[13px] ${on ? "bg-sky-950 text-sky-300" : "text-neutral-400 hover:text-neutral-200"}`}>{children}</button>;
}

// Round face buttons of the people with most photos, then "+N" for everyone.
function Faces({ people, chosen, toggle }: { people: Person[]; chosen: number[]; toggle: (id: number) => void }) {
  const [all, setAll] = useState(false);
  const [find, setFind] = useState("");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!all) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setAll(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [all]);
  const visible = people.filter((p) => !p.hidden && p.photos > 0);
  const top = visible.slice(0, 8);
  const extraChosen = visible.filter((p) => chosen.includes(p.id) && !top.includes(p));
  if (!visible.length) return <span className="text-xs text-neutral-500">Name people on the People page to filter by them.</span>;
  const face = (p: Person) => (
    <button key={p.id} onClick={() => toggle(p.id)} title={p.name}
      className={`h-8 w-8 shrink-0 overflow-hidden rounded-full ${chosen.includes(p.id) ? "ring-2 ring-sky-400 ring-offset-1 ring-offset-neutral-950" : "opacity-80 hover:opacity-100"}`}>
      {p.cover_face_id ? <img src={chipUrl(p.cover_face_id)} alt={p.name} className="h-full w-full object-cover" draggable={false} />
        : <span className="grid h-full place-items-center bg-neutral-800 text-[11px]">{p.name.slice(0, 2)}</span>}
    </button>
  );
  return (
    <div ref={box} className="relative flex items-center gap-1.5">
      {[...top, ...extraChosen].map(face)}
      {visible.length > top.length && (
        <button onClick={() => setAll((v) => !v)} title="All people"
          className="grid h-8 min-w-8 place-items-center rounded-full bg-neutral-800 px-2 text-[11px] text-neutral-300 hover:bg-neutral-700">+{visible.length - top.length}</button>
      )}
      {all && (
        <div className="absolute left-0 top-full z-40 mt-2 w-72 rounded-xl border border-neutral-700 bg-neutral-900 p-2 shadow-xl">
          <input autoFocus value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a person"
            className="mb-2 w-full rounded-lg bg-neutral-800 px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-sky-500" />
          <div className="max-h-72 overflow-y-auto">
            {visible.filter((p) => p.name.toLowerCase().includes(find.toLowerCase())).map((p) => (
              <button key={p.id} onClick={() => toggle(p.id)}
                className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-sm hover:bg-neutral-800 ${chosen.includes(p.id) ? "text-sky-300" : ""}`}>
                {p.cover_face_id ? <img src={chipUrl(p.cover_face_id)} alt="" className="h-7 w-7 rounded-full object-cover" /> : <span className="h-7 w-7 rounded-full bg-neutral-800" />}
                <span className="flex-1 truncate">{p.name}</span><span className="text-xs text-neutral-500">{p.photos.toLocaleString()}</span>
                {chosen.includes(p.id) && "✓"}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CustomDates({ dates, onChange }: { dates: Dates; onChange: (d: Dates) => void }) {
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(dates.key === "custom" ? dates.from ?? "" : "");
  const [to, setTo] = useState(dates.key === "custom" ? dates.to ?? "" : "");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div ref={box} className="relative">
      <Pill on={dates.key === "custom"} onClick={() => setOpen((v) => !v)}>
        <Icon name="clock" className="h-3.5 w-3.5" />{dates.key === "custom" ? dateLabel(dates) : "Choose dates…"}</Pill>
      {open && (
        <div className="absolute left-0 top-full z-40 mt-2 space-y-2 rounded-xl border border-neutral-700 bg-neutral-900 p-3 text-sm shadow-xl">
          <label className="flex items-center justify-between gap-3">From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded bg-neutral-800 px-1 py-0.5" /></label>
          <label className="flex items-center justify-between gap-3">To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded bg-neutral-800 px-1 py-0.5" /></label>
          <div className="flex justify-end gap-2">
            <button className="rounded px-2 py-1 text-neutral-400 hover:bg-neutral-800" onClick={() => setOpen(false)}>Cancel</button>
            <button className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-500 disabled:opacity-40" disabled={!from && !to}
              onClick={() => { onChange({ key: "custom", from: from || undefined, to: to || undefined }); setOpen(false); }}>Apply</button>
          </div>
        </div>
      )}
    </div>
  );
}

// Search box with suggestions after 2 letters: places, people, folders and things.
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const pad = (n: number) => String(n).padStart(2, "0");
// "2019" → that year; "march 2020" or "mar 20" style → that month; "march" → March of the last three years.
function dateSuggestions(t: string): { label: string; from: string; to: string }[] {
  const y = t.match(/\b(19|20)\d{2}\b/)?.[0];
  const word = t.replace(/\b(19|20)\d{2}\b/, "").trim();
  const mi = word.length >= 3 ? MONTHS.findIndex((m) => m.startsWith(word)) : -1;
  const month = (yy: number) => ({ label: `${cap(MONTHS[mi])} ${yy}`, from: `${yy}-${pad(mi + 1)}-01`,
    to: `${yy}-${pad(mi + 1)}-${pad(new Date(yy, mi + 1, 0).getDate())}` });
  if (y && mi >= 0) return [month(+y)];
  if (y && !word) return [{ label: `The year ${y}`, from: `${y}-01-01`, to: `${y}-12-31` }];
  if (!y && mi >= 0) return [thisYear, thisYear - 1, thisYear - 2].map(month);
  return [];
}

function SearchBox({ q, setQ, people, onPerson, onExtra, onDates }: {
  q: string; setQ: (s: string) => void; people: Person[];
  onPerson: (p: Person) => void; onExtra: (x: Extra) => void; onDates: (from: string, to: string) => void;
}) {
  const [hi, setHi] = useState(-1);   // the suggestion picked with the arrow keys
  const [places, setPlaces] = useState<Place[] | null>(null);
  const [folders, setFolders] = useState<{ id: number; path: string; files: number }[] | null>(null);
  const [cats, setCats] = useState<Category[]>([]);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {   // the lists are small: fetch once when typing starts, filter here
    if (q.trim().length < 2 || places) return;
    api<Place[]>("/api/places?limit=2000").then(setPlaces).catch(() => setPlaces([]));
    api<{ id: number; path: string; files: number }[]>("/api/folders").then(setFolders).catch(() => setFolders([]));
    api<{ categories: Category[] }>("/api/things").then((r) => setCats(r.categories)).catch(() => {});
  }, [q, places]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const t = q.trim().toLowerCase();
  const sugg = useMemo(() => {
    if (t.length < 2) return null;
    const name = (s: string) => s.toLowerCase().includes(t);
    return {
      places: (places ?? []).filter((p) => name(p.name) || name(p.state) || name(p.country)).slice(0, 5),
      people: people.filter((p) => !p.hidden && name(p.name)).slice(0, 5),
      folders: (folders ?? []).filter((f) => name(f.path.split(/[\\/]/).pop() ?? "")).slice(0, 4),
      cats: cats.filter((c) => name(c.label)).slice(0, 3),
    };
  }, [t, places, people, folders, cats]);
  // Every suggestion in order, so the arrow keys can walk through them.
  type Item = { key: string; group: string; icon: React.ReactNode; label: string; hint: string; fn: () => void };
  const items: Item[] = [];
  if (sugg) {
    sugg.people.forEach((p) => items.push({ key: `p${p.id}`, group: "People",
      icon: p.cover_face_id ? <img src={chipUrl(p.cover_face_id)} alt="" className="h-6 w-6 rounded-full object-cover" /> : <Icon name="pin" />,
      label: p.name, hint: `${p.photos.toLocaleString()} photos`, fn: () => onPerson(p) }));
    sugg.places.forEach((p) => items.push({ key: `l${p.id}`, group: "Places", icon: <Icon name="pin" className="h-4 w-4 text-rose-400" />,
      label: `${p.name}${p.country ? `, ${p.country}` : ""}`, hint: p.count.toLocaleString(), fn: () => onExtra({ place: p.id, label: p.name }) }));
    dateSuggestions(t).forEach((d) => items.push({ key: `d${d.from}`, group: "Dates", icon: <Icon name="clock" className="h-4 w-4 text-emerald-400" />,
      label: d.label, hint: "", fn: () => onDates(d.from, d.to) }));
    sugg.cats.forEach((c) => items.push({ key: `c${c.tag}`, group: "Folders and things", icon: <Icon name="shapes" className="h-4 w-4 text-sky-400" />,
      label: c.label, hint: c.count.toLocaleString(), fn: () => onExtra({ tag: c.tag, label: c.label }) }));
    sugg.folders.forEach((f) => items.push({ key: `f${f.id}`, group: "Folders and things", icon: <Icon name="folders" className="h-4 w-4 text-amber-400" />,
      label: f.path.split(/[\\/]/).pop() ?? f.path, hint: f.files.toLocaleString(),
      fn: () => onExtra({ folderId: f.id, label: f.path.split(/[\\/]/).pop() ?? "Folder" }) }));
  }
  const any = items.length > 0;
  useEffect(() => { setHi(-1); }, [t]);
  const pick = (it: Item) => { setOpen(false); it.fn(); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") setOpen(false);
    else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && any) {
      e.preventDefault(); setOpen(true);
      setHi((h) => (e.key === "ArrowDown" ? (h + 1) % items.length : (h <= 0 ? items.length - 1 : h - 1)));
    } else if (e.key === "Enter") { if (open && hi >= 0 && items[hi]) pick(items[hi]); else setOpen(false); }
  };
  return (
    <div ref={box} className="relative min-w-[14rem] max-w-xl flex-1">
      <label className="pe-search flex items-center gap-2 rounded-xl border border-neutral-700 bg-neutral-900 px-3 py-1.5 focus-within:ring-1 focus-within:ring-sky-500">
        <Icon name="search" className="h-4 w-4 text-neutral-500" />
        <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          placeholder="Search: beach, cake, Goa, people, file names, notes…" className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
        {q && <button className="text-neutral-500 hover:text-neutral-200" title="Clear" onClick={() => setQ("")}><Icon name="x" className="h-3.5 w-3.5" /></button>}
      </label>
      {open && any && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1.5 rounded-xl border border-neutral-700 bg-neutral-900 p-1.5 text-sm shadow-xl">
          {items.map((it, i) => (
            <div key={it.key}>
              {(i === 0 || items[i - 1].group !== it.group) && <div className="px-3 pb-1 pt-2 text-[11px] uppercase tracking-wide text-neutral-500">{it.group}</div>}
              <button onClick={() => pick(it)} onMouseEnter={() => setHi(i)}
                className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-1.5 text-left ${i === hi ? "bg-neutral-800" : ""}`}>
                {it.icon}<span className="flex-1 truncate">{it.label}</span><span className="text-xs text-neutral-500">{it.hint}</span>
              </button>
            </div>
          ))}
          <div className="px-3 pb-1 pt-2 text-xs text-neutral-500">↑ ↓ to choose, Enter to pick. Keep typing to search names, notes and what is in photos.</div>
        </div>
      )}
    </div>
  );
}
