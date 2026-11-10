// Sidebar block: source folders, scan button and live progress for all
// phases (walk, index, faces, grouping).
import { api, del, pickFolders, post, ScanStatus, Source, Stats } from "../api";
import { fmtBytes, fmtEta } from "../lib/format";

const BUSY = ["walking", "indexing", "faces", "clustering", "dedup", "smart"];
export const isBusy = (s: ScanStatus | null) => !!s && BUSY.includes(s.state);

const STATE_LABEL: Record<string, string> = {
  walking: "Looking for files", indexing: "Reading photos", faces: "Finding faces",
  clustering: "Grouping faces", dedup: "Finding duplicates", smart: "Learning what is in photos", done: "Done", cancelled: "Paused", error: "Stopped with an error",
};

export default function ScanPanel({ sources, scan, stats, ready, act, hidden, onHiddenChange }: {
  sources: Source[]; scan: ScanStatus | null; stats: Stats | null; ready: boolean;
  act: (fn: () => Promise<unknown>) => void;
  hidden: number[]; onHiddenChange: (ids: number[]) => void;
}) {
  const busy = isBusy(scan);
  const addFolders = () => act(async () => {
    for (const path of await pickFolders()) await post("/api/sources", { path });
  });
  const done = stats?.breakdown.filter((b) => b.status === 1) ?? [];
  const total = done.reduce((a, b) => a + b.n, 0);
  const bytes = done.reduce((a, b) => a + b.bytes, 0);
  const pct = (a: number, b: number) => (b ? Math.round((100 * a) / b) : 0);
  const shownIds = sources.filter((s) => !hidden.includes(s.id)).map((s) => s.id);
  const filtered = hidden.length > 0;
  // At least one source stays switched on.
  const toggle = (id: number) => hidden.includes(id)
    ? onHiddenChange(hidden.filter((x) => x !== id))
    : shownIds.length > 1 && onHiddenChange([...hidden, id]);
  const only = (id: number) => onHiddenChange(sources.filter((s) => s.id !== id).map((s) => s.id));

  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-xs uppercase tracking-wide text-neutral-400">Source folders</h2>
        {filtered && <button className="ml-auto mr-3 text-xs text-sky-400 hover:text-sky-300" onClick={() => onHiddenChange([])}>Show all</button>}
        <button className="text-sky-400 hover:text-sky-300 disabled:opacity-40" disabled={!ready || busy} onClick={addFolders}>+ Add</button>
      </div>
      {sources.length === 0 && <p className="text-xs text-neutral-500">Add the folders that hold your photos. Originals are only read, never changed.</p>}
      <ul className="space-y-1">
        {sources.map((s) => (
          <li key={s.id} className={`group rounded bg-neutral-900 px-2 py-1.5 ${hidden.includes(s.id) ? "opacity-50" : ""}`}>
            <div className="flex items-center gap-2">
              {sources.length > 1 && (
                <input type="checkbox" className="shrink-0 accent-sky-500" checked={!hidden.includes(s.id)}
                  disabled={!hidden.includes(s.id) && shownIds.length === 1}
                  title="Show this folder's photos in the views" onChange={() => toggle(s.id)} />
              )}
              <span className={`h-2 w-2 shrink-0 rounded-full ${s.online ? "bg-emerald-500" : "bg-neutral-600"}`} title={s.online ? "Online" : "Not reachable"} />
              <span className="flex-1 truncate" title={s.path}>{s.path}</span>
              {sources.length > 1 && (
                <button className="text-xs text-neutral-500 opacity-0 hover:text-sky-400 group-hover:opacity-100"
                  title="Show only this folder" onClick={() => only(s.id)}>only</button>
              )}
              <button className="text-neutral-500 opacity-0 hover:text-red-400 group-hover:opacity-100 disabled:hidden" disabled={busy}
                title="Forget this folder (files are not touched)"
                onClick={() => confirm(`Remove ${s.path} from the library?\nFiles on disk are not touched.`) && act(() => del(`/api/sources/${s.id}`))}>✕</button>
            </div>
            <div className={`text-xs text-neutral-500 ${sources.length > 1 ? "pl-9" : "pl-4"}`}>{s.files.toLocaleString()} files{hidden.includes(s.id) && " · hidden"}</div>
          </li>
        ))}
      </ul>

      {busy ? (
        <button className="w-full rounded bg-neutral-800 py-2 hover:bg-neutral-700" onClick={() => act(() => post("/api/scan/cancel"))}>Pause</button>
      ) : (
        <button className="w-full rounded bg-sky-600 py-2 font-medium hover:bg-sky-500 disabled:opacity-40" disabled={!sources.length}
          onClick={() => act(() => post("/api/scan", filtered ? { source_ids: shownIds } : {}))}>
          {filtered ? `Scan ${shownIds.length} shown folder${shownIds.length === 1 ? "" : "s"}` : "Scan now"}</button>
      )}

      {scan && scan.state !== "idle" && (
        <div className="space-y-1 rounded bg-neutral-900 p-3 text-xs">
          <div className="font-medium">{STATE_LABEL[scan.state] ?? scan.state}</div>
          {scan.state === "indexing" && <Bar p={pct(scan.indexed, scan.to_index)} />}
          {scan.state === "faces" && <Bar p={pct(scan.faces_done, scan.faces_todo)} />}
          {scan.state === "smart" && <Bar p={pct(scan.smart_done ?? 0, scan.smart_todo ?? 0)} />}
          {scan.mode === "full" && (
            <div className="text-neutral-400">{scan.discovered.toLocaleString()} files · {scan.new.toLocaleString()} new · {scan.changed} changed · {scan.missing} missing</div>
          )}
          {scan.to_index > 0 && (
            <div className="text-neutral-400">read {scan.indexed.toLocaleString()} / {scan.to_index.toLocaleString()}
              {scan.state === "indexing" && ` · ${scan.rate}/s ${fmtEta(scan.eta_s)}`} · {scan.errors} errors</div>
          )}
          {scan.faces_todo > 0 && (
            <div className="text-neutral-400">faces: {scan.faces_done.toLocaleString()} / {scan.faces_todo.toLocaleString()} photos · {scan.faces_found.toLocaleString()} found</div>
          )}
          {scan.clusters != null && <div className="text-neutral-400">{scan.clusters.toLocaleString()} face groups</div>}
          {scan.dedup_exact != null && (
            <div className="text-neutral-400">{scan.dedup_exact.toLocaleString()} identical-copy groups · {(scan.dedup_similar ?? 0).toLocaleString()} look-alike groups</div>
          )}
          {scan.current && <div className="truncate text-neutral-500" title={scan.current}>{scan.current}</div>}
          {scan.error && <div className="text-red-400">{scan.error}</div>}
          {scan.warnings.slice(-3).map((w, i) => <div key={i} className="truncate text-amber-400" title={w}>{w}</div>)}
        </div>
      )}

      {stats && total > 0 && (
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Items" value={total.toLocaleString()} />
          <Stat label="Size" value={fmtBytes(bytes)} />
          <Stat label="Identical-copy groups" value={stats.exact_duplicate_groups.toLocaleString()} />
          <Stat label="Extra copies" value={stats.exact_duplicate_extra_files.toLocaleString()} />
        </div>
      )}
    </div>
  );
}

function Bar({ p }: { p: number }) {
  return <div className="h-1.5 rounded bg-neutral-800"><div className="h-full rounded bg-sky-500 transition-all" style={{ width: `${p}%` }} /></div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-neutral-900 p-2">
      <div className="text-[11px] text-neutral-500">{label}</div>
      <div className="font-medium">{value}</div>
    </div>
  );
}

export async function loadPanel() {
  return Promise.all([api<Source[]>("/api/sources"), api<Stats>("/api/stats"), api<ScanStatus>("/api/scan/status")]);
}
