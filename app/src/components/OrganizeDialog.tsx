// "Organize into folders": pick a destination, how (copy / hard link / move),
// and a folder pattern; preview exactly what will happen; then run it.
import { useEffect, useState } from "react";
import { api, Filters, filtersForExport, OrganizeMode, OrganizePreview, Person, pickFolder, post, Presets } from "../api";
import { fmtBytes } from "../lib/format";
import type { CreateSource } from "./CreateDialog";

const MODES: { id: OrganizeMode; label: string; help: string }[] = [
  { id: "copy", label: "Copy", help: "Originals stay where they are. Uses extra disk space." },
  { id: "hardlink", label: "Hard link", help: "Same files under a second name: no extra space. Same drive only." },
  { id: "move", label: "Move originals", help: "Actually tidies your folders. The library follows the files. Can be undone." },
];

export default function OrganizeDialog({ source, onClose, onStarted }: {
  source: CreateSource; onClose: () => void; onStarted: (msg: string) => void;
}) {
  const [presets, setPresets] = useState<Presets | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [dest, setDest] = useState<string | null>(null);
  const [mode, setMode] = useState<OrganizeMode>("copy");
  const [folderKey, setFolderKey] = useState("year_month");
  const [custom, setCustom] = useState("{year}/{year}-{month}");
  const [nameKey, setNameKey] = useState("original");
  const filterPeople = source.filters?.persons ?? [];
  const [persons, setPersons] = useState<number[]>(filterPeople);
  // With people ticked: only their photos (any / all), or every photo with folders just for them.
  const [peopleMode, setPeopleMode] = useState<"any" | "all" | "folders">("any");
  const [preview, setPreview] = useState<OrganizePreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmMove, setConfirmMove] = useState(false);
  const [ruleName, setRuleName] = useState<string | null>(null);   // null = not saving a rule

  useEffect(() => {
    api<Presets>("/api/organize/presets").then(setPresets).catch((e) => setErr(String(e)));
    api<Person[]>("/api/people").then(setPeople).catch(() => {});
    if (filterPeople.length) setFolderKey("person_year");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const folderTemplate = folderKey === "custom" ? custom : presets?.folders[folderKey]?.template ?? "";
  const byPerson = folderTemplate.includes("{person}");
  const onlyPeople = persons.length > 0 && (peopleMode !== "folders" || !byPerson);
  const body = () => ({
    dest_root: dest, mode,
    options: {
      folder_template: folderTemplate,
      name_template: presets?.names[nameKey]?.template ?? "{name}",
      persons: byPerson && persons.length ? persons : null,
    },
    ...(onlyPeople ? { people: persons, people_mode: peopleMode === "all" ? "all" : "any" } : {}),
    ...(source.mediaIds ? { media_ids: source.mediaIds } : { filters: filtersForExport(source.filters ?? ({} as Filters)) }),
  });

  // Any change invalidates the preview.
  useEffect(() => { setPreview(null); setConfirmMove(false); }, [dest, mode, folderTemplate, nameKey, persons, peopleMode]);

  const doPreview = async () => {
    setBusy(true); setErr(null);
    try { setPreview(await post<OrganizePreview>("/api/organize/preview", body())); }
    catch (e) { setErr(String(e)); }
    setBusy(false);
  };
  const start = async () => {
    setBusy(true); setErr(null);
    try {
      await post("/api/organize", body());
      onStarted(`${mode === "move" ? "Moving" : mode === "copy" ? "Copying" : "Linking"} ${preview?.new ?? ""} files. Follow it under Memories.`);
      onClose();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const saveRule = async () => {
    setBusy(true); setErr(null);
    try {
      const b = body();
      await post("/api/organize/rules", { ...b, filters: {}, media_ids: null, people: persons,
        people_mode: peopleMode === "all" ? "all" : "any", name: ruleName });
      onStarted(`Saved the auto rule "${ruleName || "Auto organize"}". Run it any time from Memories > Run auto organize.`);
      onClose();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const row = "flex items-center justify-between gap-3 text-sm";
  const input = "rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500";
  const canStart = preview && !preview.errors.length && preview.new > 0 && (mode !== "move" || confirmMove);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="max-h-[92vh] w-[36rem] max-w-[95vw] space-y-4 overflow-y-auto rounded-xl bg-neutral-900 p-5 shadow-2xl">
        <div className="flex items-center">
          <h2 className="flex-1 text-lg font-semibold">Organize {source.count.toLocaleString()} photo{source.count === 1 ? "" : "s"} into folders</h2>
          <button className="text-neutral-400 hover:text-white" onClick={onClose}>✕</button>
        </div>

        <div className={row}>Destination
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-xs text-neutral-300" title={dest ?? ""}>{dest ?? "not chosen"}</span>
            <button className="rounded bg-neutral-800 px-2 py-1 hover:bg-neutral-700" onClick={async () => setDest(await pickFolder("Organize into"))}>Choose…</button>
          </span></div>

        <div className="space-y-1">
          <div className="text-sm">How</div>
          <div className="grid grid-cols-3 gap-2">
            {MODES.map((m) => (
              <button key={m.id} onClick={() => setMode(m.id)} title={m.help}
                className={`rounded-lg border p-2 text-left text-xs ${mode === m.id ? "border-sky-500 bg-sky-950" : "border-neutral-700 hover:border-neutral-500"}`}>
                <div className={`text-sm font-medium ${m.id === "move" ? "text-amber-300" : ""}`}>{m.label}</div>
                <div className="text-neutral-400">{m.help}</div>
              </button>
            ))}
          </div>
        </div>

        <label className={row}>Folders
          <select value={folderKey} onChange={(e) => setFolderKey(e.target.value)} className={input}>
            {presets && Object.entries(presets.folders).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            <option value="custom">Custom pattern…</option>
          </select></label>
        {folderKey === "custom" && (
          <div className="space-y-1">
            <input value={custom} onChange={(e) => setCustom(e.target.value)} className={`${input} w-full font-mono text-xs`} />
            <div className="text-[11px] text-neutral-500">
              {presets && Object.keys(presets.tokens).map((t) => <code key={t} className="mr-2">{`{${t}}`}</code>)}
              · use / for sub-folders
            </div>
          </div>
        )}
        <label className={row}>File names
          <select value={nameKey} onChange={(e) => setNameKey(e.target.value)} className={input}>
            {presets && Object.entries(presets.names).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select></label>
        <div className="space-y-1 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span>People <span className="text-xs text-neutral-500">
              {persons.length ? "" : byPerson ? "(none ticked = a folder for everyone named)" : "(none ticked = all photos)"}</span></span>
            {persons.length > 0 && (
              <span className="flex items-center gap-2">
                <select value={peopleMode} onChange={(e) => setPeopleMode(e.target.value as typeof peopleMode)} className={`${input} text-xs`}>
                  <option value="any">Only photos with any of them</option>
                  <option value="all">Only photos with all of them</option>
                  {byPerson && <option value="folders">All photos; folders for them, rest in "Other photos"</option>}
                </select>
                <button className="text-xs text-neutral-400 hover:text-white" onClick={() => setPersons([])}>Clear</button>
              </span>
            )}
          </div>
          {people.length ? (
            <div className="flex max-h-28 flex-wrap gap-1 overflow-y-auto">
              {people.map((p) => (
                <button key={p.id} onClick={() => setPersons((x) => (x.includes(p.id) ? x.filter((i) => i !== p.id) : [...x, p.id]))}
                  className={`rounded-full px-2 py-0.5 text-xs ${persons.includes(p.id) ? "bg-sky-700" : "bg-neutral-800 hover:bg-neutral-700"}`}>{p.name}</button>
              ))}
            </div>
          ) : <div className="text-xs text-neutral-500">No named people yet (name face groups under People).</div>}
        </div>

        <button className="w-full rounded bg-neutral-700 py-1.5 text-sm hover:bg-neutral-600 disabled:opacity-40"
          disabled={!dest || busy || !presets} onClick={doPreview}>{busy && !preview ? "Checking…" : "Preview"}</button>

        {preview && (
          <div className="space-y-2 rounded-lg bg-neutral-950 p-3 text-sm">
            <div>
              <b>{preview.new.toLocaleString()}</b> files ({fmtBytes(preview.bytes)}) into <b>{preview.folders.toLocaleString()}</b> folders
              {preview.already_there > 0 && <span className="text-neutral-400"> · {preview.already_there} already there (skipped)</span>}
              {preview.same_in_selection > 0 && <span className="text-neutral-400"> · {preview.same_in_selection} identical copies merged</span>}
            </div>
            {preview.errors.map((e, i) => <div key={i} className="text-red-400">✕ {e}</div>)}
            {preview.warnings.map((w, i) => <div key={i} className="text-amber-300">! {w}</div>)}
            <div className="max-h-40 overflow-y-auto font-mono text-[11px] text-neutral-400">
              {preview.sample.map((s, i) => (
                <div key={i} className="truncate" title={s.src}>
                  {s.status === "new" ? "" : `[${s.status === "exists" ? "already there" : "same file"}] `}{s.dest}
                </div>
              ))}
              {preview.files > preview.sample.length && <div>… and {(preview.files - preview.sample.length).toLocaleString()} more</div>}
            </div>
            {mode === "move" && !preview.errors.length && (
              <label className="flex items-start gap-2 text-amber-300">
                <input type="checkbox" className="mt-1" checked={confirmMove} onChange={(e) => setConfirmMove(e.target.checked)} />
                I understand the original files will be moved out of their current folders (Undo under Memories moves them back).
              </label>
            )}
          </div>
        )}
        {err && <p className="text-sm text-red-400">{err}</p>}
        {ruleName !== null && (
          <div className="space-y-2 rounded-lg bg-neutral-950 p-3 text-sm">
            <div>Save these settings as an auto rule. <b>Run auto organize</b> (in Memories) then files every photo of
              {" "}{people.filter((p) => persons.includes(p.id)).map((p) => p.name).join(peopleMode === "all" ? " + " : ", ")}
              {" "}that isn't in the destination yet, each time you run it.</div>
            <input autoFocus value={ruleName} onChange={(e) => setRuleName(e.target.value)} placeholder="Rule name, e.g. Family album"
              className={`${input} w-full`} />
            <div className="flex justify-end gap-2">
              <button className="rounded px-3 py-1 hover:bg-neutral-800" onClick={() => setRuleName(null)}>Back</button>
              <button className="rounded bg-sky-600 px-3 py-1 font-medium hover:bg-sky-500 disabled:opacity-40" disabled={busy} onClick={saveRule}>Save rule</button>
            </div>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button className="mr-auto rounded px-3 py-1.5 text-sm text-sky-300 hover:bg-neutral-800 disabled:opacity-40"
            disabled={!dest || !persons.length || peopleMode === "folders" || busy || !presets}
            title={!persons.length ? "Tick at least one person first" : "Repeat this for their new photos with one click"}
            onClick={() => setRuleName("")}>Save as auto rule…</button>
          <button className="rounded px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-800" onClick={onClose}>Cancel</button>
          <button className={`rounded px-4 py-1.5 text-sm font-medium disabled:opacity-40 ${mode === "move" ? "bg-amber-700 hover:bg-amber-600" : "bg-sky-600 hover:bg-sky-500"}`}
            disabled={!canStart || busy} onClick={start}>
            {mode === "move" ? "Move files" : mode === "copy" ? "Copy files" : "Create links"}
          </button>
        </div>
      </div>
    </div>
  );
}
