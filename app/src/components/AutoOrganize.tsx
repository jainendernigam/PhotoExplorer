// Auto organize: saved Organize settings for chosen people ("rules"), and the
// "Run auto organize" button that files their new photos. Rules are made in
// Organize… with "Save as auto rule".
import { useCallback, useEffect, useState } from "react";
import { api, del, OrganizeMode, OrganizeRule, patch, Person, pickFolder, post, Presets } from "../api";

const MODE = { copy: "Copy", hardlink: "Hard link", symlink: "Link", move: "Move" } as const;

export default function AutoOrganize({ onRan }: { onRan: () => void }) {
  const [rules, setRules] = useState<OrganizeRule[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const load = useCallback(() => api<OrganizeRule[]>("/api/organize/rules").then(setRules).catch((e) => setErr(String(e))), []);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr(String(e)); }
    load();
  };
  const run = async (ids?: number[]) => {
    setBusy(true); setMsg(null); setErr(null);
    try {
      const r = await post<{ results: { name: string; message: string; new: number; error?: string }[] }>(
        "/api/organize/rules/run", { rule_ids: ids ?? null });
      const started = r.results.reduce((a, b) => a + b.new, 0);
      setMsg(!r.results.length ? "No rule is switched on."
        : started ? `Filing ${started} new photo${started === 1 ? "" : "s"}. Progress is under Folder exports below.`
        : "Nothing new to file.");
      if (r.results.some((x) => x.error)) setErr(r.results.filter((x) => x.error).map((x) => `${x.name}: ${x.error}`).join(" · "));
      onRan();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
    load();
  };

  return (
    <section className="space-y-2 rounded-lg bg-neutral-900 p-4 text-sm">
      <div className="flex items-center gap-3">
        <h2 className="flex-1 text-xs uppercase tracking-wide text-neutral-400">Auto organize</h2>
        <button className="rounded bg-sky-600 px-4 py-1.5 font-medium hover:bg-sky-500 disabled:opacity-40"
          disabled={busy || !rules.some((r) => r.enabled)} onClick={() => run()}>
          {busy ? "Running…" : "Run auto organize"}</button>
      </div>
      {rules.length === 0 ? (
        <p className="text-neutral-500">No rules yet. In any photo grid, open <b>Organize…</b>, tick people, choose a destination,
          then <b>Save as auto rule</b>. This button then files their new photos in one go.</p>
      ) : (
        <ul className="divide-y divide-neutral-800">
          {rules.map((r) => editing === r.id ? (
            <li key={r.id} className="py-2"><RuleEditor rule={r} onDone={() => { setEditing(null); load(); }} /></li>
          ) : (
            <li key={r.id} className="flex items-center gap-3 py-2">
              <input type="checkbox" checked={!!r.enabled} title="Switched on"
                onChange={(e) => act(() => patch(`/api/organize/rules/${r.id}`, { enabled: e.target.checked }))} />
              <div className="min-w-0 flex-1">
                <div className={r.enabled ? "" : "text-neutral-500"}>{r.name}
                  <span className={`ml-2 text-xs ${r.mode === "move" ? "text-amber-300" : "text-neutral-400"}`}>{MODE[r.mode]}</span></div>
                <div className="truncate text-xs text-neutral-400" title={r.dest_root}>
                  {r.people_names.join(r.people_mode === "all" ? " + " : ", ")} → {r.dest_root}</div>
                {r.last_result && <div className="text-xs text-neutral-500">
                  Last run {r.last_run_at ? new Date(r.last_run_at * 1000).toLocaleString() : ""}: {r.last_result.message}</div>}
              </div>
              <button className="rounded bg-neutral-800 px-2 py-1 text-xs hover:bg-neutral-700 disabled:opacity-40" disabled={busy}
                onClick={() => run([r.id])}>Run</button>
              {r.last_job && r.last_job.done > 0 && !r.last_job.undone_at && !["running", "queued"].includes(r.last_job.status) && (
                <button className="text-xs text-sky-400 hover:underline disabled:opacity-40" disabled={busy}
                  title="Puts back what the last run filed and removes the folders it made"
                  onClick={() => confirm(`Undo the last run of "${r.name}"? ${r.mode === "move"
                    ? "Moved photos go back to their old folders" : "The copies it made are removed"}, and the folders it made are deleted once empty.`)
                    && act(() => post(`/api/organize/${r.last_job_id}/undo`).then(onRan))}>Undo last run</button>)}
              <button className="text-xs text-neutral-300 hover:text-white" disabled={busy} onClick={() => setEditing(r.id)}>Edit</button>
              <button className="text-xs text-neutral-500 hover:text-red-400"
                onClick={() => confirm(`Delete the rule "${r.name}"? Files it already organized stay where they are.`)
                  && act(() => del(`/api/organize/rules/${r.id}`))}>Delete</button>
            </li>
          ))}
        </ul>
      )}
      {msg && <p className="text-emerald-400">{msg}</p>}
      {err && <p className="text-red-400">{err}</p>}
    </section>
  );
}

const MODES: OrganizeMode[] = ["copy", "hardlink", "move"];

function RuleEditor({ rule, onDone }: { rule: OrganizeRule; onDone: () => void }) {
  const [presets, setPresets] = useState<Presets | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [name, setName] = useState(rule.name);
  const [dest, setDest] = useState(rule.dest_root);
  const [mode, setMode] = useState<OrganizeMode>(rule.mode);
  const [folder, setFolder] = useState(String(rule.options.folder_template ?? "{year}"));
  const [fname, setFname] = useState(String(rule.options.name_template ?? "{name}"));
  const [persons, setPersons] = useState<number[]>(rule.people);
  const [pmode, setPmode] = useState(rule.people_mode);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<Presets>("/api/organize/presets").then(setPresets).catch((e) => setErr(String(e)));
    api<Person[]>("/api/people").then(setPeople).catch(() => {});
  }, []);

  const save = async () => {
    try {
      await patch(`/api/organize/rules/${rule.id}`, {
        name, dest_root: dest, mode, people: persons, people_mode: pmode,
        options: { ...rule.options, folder_template: folder, name_template: fname,
          persons: folder.includes("{person}") ? persons : null },
      });
      onDone();
    } catch (e) { setErr(String(e)); }
  };
  const input = "rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500";
  const row = "flex items-center justify-between gap-3";
  const folderOpts = presets ? Object.values(presets.folders) : [];
  const nameOpts = presets ? Object.values(presets.names) : [];

  return (
    <div className="space-y-2 rounded-lg bg-neutral-950 p-3">
      <label className={row}>Name <input value={name} onChange={(e) => setName(e.target.value)} className={`${input} w-64`} /></label>
      <div className={row}>Destination
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs text-neutral-300" title={dest}>{dest}</span>
          <button className="rounded bg-neutral-800 px-2 py-1 hover:bg-neutral-700"
            onClick={async () => { const d = await pickFolder("Organize into"); if (d) setDest(d); }}>Choose…</button>
        </span></div>
      <label className={row}>How
        <select value={mode} onChange={(e) => setMode(e.target.value as OrganizeMode)} className={input}>
          {MODES.map((m) => <option key={m} value={m}>{m === "move" ? "Move originals" : MODE[m]}</option>)}
        </select></label>
      <label className={row}>Folders
        <select value={folder} onChange={(e) => setFolder(e.target.value)} className={input}>
          {!folderOpts.some((f) => f.template === folder) && <option value={folder}>{folder}</option>}
          {folderOpts.map((f) => <option key={f.template} value={f.template}>{f.label}</option>)}
        </select></label>
      <label className={row}>File names
        <select value={fname} onChange={(e) => setFname(e.target.value)} className={input}>
          {!nameOpts.some((f) => f.template === fname) && <option value={fname}>{fname}</option>}
          {nameOpts.map((f) => <option key={f.template} value={f.template}>{f.label}</option>)}
        </select></label>
      <label className={row}>People
        <select value={pmode} onChange={(e) => setPmode(e.target.value as "any" | "all")} className={`${input} text-xs`}>
          <option value="any">Photos with any of them</option>
          <option value="all">Photos with all of them</option>
        </select></label>
      <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
        {people.map((p) => (
          <button key={p.id} onClick={() => setPersons((x) => (x.includes(p.id) ? x.filter((i) => i !== p.id) : [...x, p.id]))}
            className={`rounded-full px-2 py-0.5 text-xs ${persons.includes(p.id) ? "bg-sky-700" : "bg-neutral-800 hover:bg-neutral-700"}`}>{p.name}</button>
        ))}
      </div>
      <p className="text-xs text-neutral-500">Photos already filed stay where they are; the next run uses these settings.
        To take back what a run filed, use Undo last run or Undo under Folder exports.</p>
      {mode === "move" && rule.mode !== "move" && <p className="text-xs text-amber-300">Move takes the original files out of their folders when the rule runs.</p>}
      {err && <p className="text-red-400">{err}</p>}
      <div className="flex justify-end gap-2">
        <button className="rounded px-3 py-1 hover:bg-neutral-800" onClick={onDone}>Cancel</button>
        <button className="rounded bg-sky-600 px-3 py-1 font-medium hover:bg-sky-500 disabled:opacity-40"
          disabled={!persons.length || !dest} onClick={save}>Save changes</button>
      </div>
    </div>
  );
}
