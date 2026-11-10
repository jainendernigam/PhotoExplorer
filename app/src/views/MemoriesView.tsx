// Memories: slideshows and collages being made or already made.
import TopBar from "../lib/topbar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Bucket, ExportJob, exportFileUrl, exportThumbUrl, Filters, filterQuery, OrganizeJob, Person, post } from "../api";
import AutoOrganize from "../components/AutoOrganize";
import CreateDialog, { CreateSource } from "../components/CreateDialog";
import { fmtBytes } from "../lib/format";
import { Chip } from "./PeopleView";

export default function MemoriesView() {
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [orgJobs, setOrgJobs] = useState<OrganizeJob[]>([]);
  const [preview, setPreview] = useState<ExportJob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [create, setCreate] = useState<CreateSource | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // What to make it from: people and/or dates, within the ticked source folders.
  const [people, setPeople] = useState<Person[]>([]);
  const [chosen, setChosen] = useState<number[]>([]);
  const [mode, setMode] = useState<"all" | "any">("any");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => { api<Person[]>("/api/people").then(setPeople).catch(() => {}); }, []);
  const filters: Filters = useMemo(() => ({
    persons: chosen, personsMode: mode, dateFrom: dateFrom || undefined, dateTo: dateTo || undefined,
  }), [chosen, mode, dateFrom, dateTo]);
  useEffect(() => {
    let alive = true;
    setCount(null);
    api<Bucket[]>(`/api/timeline?${filterQuery(filters)}`)
      .then((m) => alive && setCount(m.reduce((a, b) => a + b.count, 0))).catch(() => {});
    return () => { alive = false; };
  }, [filters]);
  const toggle = (id: number) => setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  const load = useCallback(() => Promise.all([api<ExportJob[]>("/api/exports"), api<OrganizeJob[]>("/api/organize")])
    .then(([e, o]) => { setJobs(e); setOrgJobs(o); }).catch((e) => setErr(String(e))), []);
  const active = [...jobs, ...orgJobs].some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, active ? 1000 : 5000);
    return () => clearInterval(t);
  }, [load, active]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr(String(e)); }
    load();
  };

  return (
    <div className="h-full overflow-y-auto p-5 space-y-4">
      <header>
        <TopBar><span className="font-semibold">Memories</span></TopBar>
        <p className="text-sm text-neutral-400">Make a slideshow or collage: choose people and/or dates below, then <b>Create…</b>.
          To hand-pick photos, select them in any photo grid and use <b>Create…</b> there.</p>
      </header>
      <section className="space-y-3 rounded-lg bg-neutral-900 p-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-neutral-400">People</span>
          {people.length === 0 && <span className="text-neutral-500">None named yet (name faces on the People page).</span>}
          {people.map((p) => (
            <button key={p.id} onClick={() => toggle(p.id)}
              className={`flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-3 ${chosen.includes(p.id) ? "bg-sky-700" : "bg-neutral-800 hover:bg-neutral-700"}`}>
              <Chip faceId={p.cover_face_id} className="h-6 w-6 rounded-full" />{p.name}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-4">
          {chosen.length > 1 && (
            <select value={mode} onChange={(e) => setMode(e.target.value as "all" | "any")} className="rounded bg-neutral-800 px-2 py-1">
              <option value="any">Photos with any of them</option>
              <option value="all">Photos with all of them together</option>
            </select>
          )}
          <label className="flex items-center gap-1 text-neutral-300">From
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded bg-neutral-800 px-1 py-0.5" />
            to
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded bg-neutral-800 px-1 py-0.5" />
          </label>
          {(chosen.length > 0 || dateFrom || dateTo) && (
            <button className="text-xs text-neutral-400 hover:text-white" onClick={() => { setChosen([]); setDateFrom(""); setDateTo(""); }}>Clear</button>
          )}
          <span className="ml-auto text-neutral-400">
            {count === null ? "Counting…" : `${count.toLocaleString()} photo${count === 1 ? "" : "s"}${chosen.length || dateFrom || dateTo ? (count === 1 ? " matches" : " match") : " in the library"}`}
          </span>
          <button className="rounded bg-sky-600 px-4 py-1.5 font-medium hover:bg-sky-500 disabled:opacity-40" disabled={!count}
            onClick={() => count && setCreate({ filters, count })}>Create…</button>
        </div>
      </section>
      {err && <p className="text-sm text-red-400">{err}</p>}
      {notice && <p className="text-sm text-emerald-400">{notice}</p>}
      {create && <CreateDialog source={create} onClose={() => setCreate(null)}
        onStarted={(msg) => { setNotice(msg.replace(" Follow it under Memories.", "")); setCreate(null); load(); }} />}
      <AutoOrganize onRan={load} />
      {orgJobs.length > 0 && <OrganizeList jobs={orgJobs} act={act} />}
      {orgJobs.length > 0 && <h2 className="pt-2 text-xs uppercase tracking-wide text-neutral-400">Slideshows and collages</h2>}
      {jobs.length === 0 && <p className="text-neutral-500">Nothing made yet.</p>}
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>
        {jobs.map((j) => {
          const running = j.status === "running" || j.status === "queued";
          const ready = j.status === "done" && j.exists;
          const name = j.title || (j.kind === "slideshow" ? "Slideshow" : "Collage");
          return (
            <div key={j.id} className="group min-w-0">
              <button disabled={!ready} onClick={() => setPreview(j)} title={ready ? `${j.kind === "slideshow" ? "Play" : "Open"} ${name}` : undefined}
                className="pe-tile relative grid aspect-video w-full place-items-center overflow-hidden rounded-lg bg-neutral-900 text-3xl ring-sky-500 enabled:hover:ring-2">
                {ready ? <ThumbImg id={j.id} kind={j.kind} /> : <span className="opacity-60">{j.kind === "slideshow" ? "🎞" : "🖼"}</span>}
                {ready && j.kind === "slideshow" && (
                  <span className="absolute grid h-11 w-11 place-items-center rounded-full bg-black/55 text-lg text-white">▶</span>
                )}
                {running && (
                  <span className="absolute inset-x-3 bottom-3 h-1.5 rounded bg-neutral-800">
                    <span className="block h-full rounded bg-sky-500 transition-all" style={{ width: `${Math.round(j.progress * 100)}%` }} />
                  </span>
                )}
              </button>
              <div className="mt-1 flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate" title={name}>{name}</span>
                {running && <button className="text-xs text-neutral-400 hover:text-white" onClick={() => act(() => post(`/api/exports/${j.id}/cancel`))}>Cancel</button>}
              </div>
              <div className={`truncate text-xs ${j.status === "error" ? "text-red-400" : "text-neutral-500"}`} title={j.message ?? ""}>
                {j.status === "queued" ? "Waiting…" : running ? `${Math.round(j.progress * 100)}%`
                  : j.status === "done" ? (j.exists ? new Date(j.created_at * 1000).toLocaleDateString() : "File was moved or deleted")
                  : j.status === "cancelled" ? "Cancelled" : j.message}
              </div>
            </div>
          );
        })}
      </div>
      {preview && (
        <div data-theme="dark" className="fixed inset-0 z-50 !mt-0 flex flex-col bg-black" onClick={(e) => e.target === e.currentTarget && setPreview(null)}>
          <div className="flex items-center gap-3 p-3 text-sm">
            <span className="flex-1 truncate" title={preview.output_path ?? ""}>{preview.title || preview.output_path}</span>
            <button className="rounded bg-white/10 px-3 py-1 hover:bg-white/20" onClick={() => act(() => post(`/api/exports/${preview.id}/reveal`))}>Show in folder</button>
            <button className="rounded bg-white/10 px-3 py-1 hover:bg-white/20" onClick={() => setPreview(null)}>Close</button>
          </div>
          <div className="grid min-h-0 flex-1 place-items-center p-3">
            {preview.kind === "slideshow"
              ? <video src={exportFileUrl(preview.id)} controls autoPlay className="max-h-full max-w-full" />
              : <img src={exportFileUrl(preview.id)} className="max-h-full max-w-full object-contain" />}
          </div>
        </div>
      )}
    </div>
  );
}

// The thumbnail, or an icon when no picture can be made (e.g. ffmpeg missing).
function ThumbImg({ id, kind }: { id: number; kind: string }) {
  const [bad, setBad] = useState(false);
  return bad ? <span className="opacity-60">{kind === "slideshow" ? "🎞" : "🖼"}</span>
    : <img src={exportThumbUrl(id)} loading="lazy" onError={() => setBad(true)} className="h-full w-full object-cover" draggable={false} />;
}

const VERB = { copy: "Copy", hardlink: "Hard links", symlink: "Links", move: "Move" } as const;

function OrganizeList({ jobs, act }: { jobs: OrganizeJob[]; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  return (
    <section className="space-y-2">
      <h2 className="text-xs uppercase tracking-wide text-neutral-400">Folder exports</h2>
      <ul className="space-y-2">
        {jobs.map((j) => {
          const running = j.status === "queued" || j.status === "running";
          return (
            <li key={j.id} className={`flex items-center gap-4 rounded-lg bg-neutral-900 p-3 ${j.undone_at ? "opacity-50" : ""}`}>
              <div className={`w-24 shrink-0 text-center text-sm font-medium ${j.mode === "move" ? "text-amber-300" : "text-sky-300"}`}>{VERB[j.mode]}</div>
              <div className="min-w-0 flex-1 space-y-1">
                <div className="truncate font-medium" title={j.dest_root}>{j.dest_root}</div>
                {running && (
                  <div className="h-1.5 rounded bg-neutral-800"><div className="h-full rounded bg-sky-500 transition-all" style={{ width: `${Math.round(j.progress * 100)}%` }} /></div>
                )}
                <div className={`truncate text-xs ${j.status === "error" ? "text-red-400" : "text-neutral-400"}`}>
                  {j.status === "queued" ? "Waiting…" : j.message}{j.bytes ? ` · ${fmtBytes(j.bytes)}` : ""}
                  {j.undone_at ? " · undone" : ""}
                </div>
              </div>
              <div className="flex shrink-0 gap-2 text-sm">
                {running && <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700" onClick={() => act(() => post(`/api/organize/${j.id}/cancel`))}>Cancel</button>}
                {!running && j.status !== "error" && (
                  <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700" onClick={() => act(() => post(`/api/organize/${j.id}/reveal`))}>Open folder</button>
                )}
                {!running && !j.undone_at && j.done > 0 && (
                  <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700"
                    onClick={() => confirm(j.mode === "move"
                      ? `Move the ${j.done} files back to where they were?`
                      : `Remove the ${j.done} ${j.mode === "copy" ? "copies" : "links"} this export made? Originals are not touched${j.mode === "copy" ? "; copies go to the Recycle Bin where possible" : ""}.`)
                      && act(() => post(`/api/organize/${j.id}/undo`))}>Undo</button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
