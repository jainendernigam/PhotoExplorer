// Duplicates: exact copies, near-identical copies and similar shots. Each group
// shows a suggestion (green = keep, red = move away) that can be changed;
// files are moved to an archive folder (or the Recycle Bin), never deleted,
// and every action can be undone from the Removed tab.
import { useCallback, useEffect, useState } from "react";
import { api, DupGroup, DupKind, DupMember, DupSummary, post, previewUrl, Removal, thumbUrl } from "../api";
import { fmtBytes, fmtTaken } from "../lib/format";

type Tab = DupKind | "removed";
type Method = "archive" | "recycle" | "app";
const TABS: { id: Tab; label: string; help: string }[] = [
  { id: "exact", label: "Identical copies", help: "Exactly the same file in more than one place." },
  { id: "near", label: "Near-identical", help: "The same picture saved again: resized, recompressed or re-exported." },
  { id: "burst", label: "Similar shots", help: "Photos taken seconds apart that look alike. The sharpest, largest one is suggested." },
  { id: "removed", label: "Removed", help: "Files moved away by this app. Undo puts them back." },
];
const PAGE = 30;
type Bulk = { state: string; done: number; total: number; removed: number; bytes: number; failed_count: number; batch: string | null };

export default function DuplicatesView({ busy, onChanged }: { busy: boolean; onChanged: () => void }) {
  const [tab, setTab] = useState<Tab>("exact");
  const [summary, setSummary] = useState<DupSummary | null>(null);
  const [groups, setGroups] = useState<DupGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [sort, setSort] = useState<"size" | "date">("size");
  const [method, setMethod] = useState<Method>("archive");
  const [msg, setMsg] = useState<{ text: string; batch?: string; error?: boolean } | null>(null);
  const [working, setWorking] = useState(false);
  const [bulk, setBulk] = useState<Bulk | null>(null);

  const loadSummary = useCallback(() => api<DupSummary>("/api/dedup/summary").then(setSummary).catch(() => {}), []);
  const loadGroups = useCallback(async (offset = 0) => {
    if (tab === "removed") return;
    const r = await api<{ groups: DupGroup[]; total: number }>(`/api/dedup/groups?kind=${tab}&sort=${sort}&limit=${PAGE}&offset=${offset}`);
    setGroups((g) => (offset ? [...g, ...r.groups] : r.groups));
    setTotal(r.total);
  }, [tab, sort]);

  useEffect(() => { loadSummary(); }, [loadSummary, busy]);
  useEffect(() => { setGroups([]); loadGroups(0).catch((e) => setMsg({ text: String(e), error: true })); }, [loadGroups, busy]);

  // Restored files rejoin their groups: rerun the (quick) grouping.
  const regroup = async () => {
    try { await post("/api/dedup/run"); } catch { /* a scan is running; it regroups at the end */ }
  };

  const afterAction = (text: string, batch?: string) => {
    setMsg({ text, batch });
    loadSummary(); onChanged();
  };
  const run = async (fn: () => Promise<void>) => {
    setWorking(true);
    try { await fn(); } catch (e) { setMsg({ text: String(e), error: true }); }
    setWorking(false);
  };

  const applyAll = (kind: DupKind) => run(async () => {
    const k = summary?.kinds[kind];
    if (!k) return;
    const n = (k.files - k.groups).toLocaleString();
    const what = method === "app"
      ? `${n} files will be removed from the app only. They stay on disk where they are.`
      : `${n} files (${fmtBytes(k.reclaim_bytes)}) will be moved to ${method === "archive" ? "the archive folder inside each source folder" : "the Recycle Bin"}.`;
    if (!confirm(`Apply the suggestion to all ${k.groups.toLocaleString()} groups?\n\n` +
      `${what} One copy of every photo is kept. You can undo this from the Removed tab.`)) return;
    let st = await post<Bulk>("/api/dedup/apply", { kind, method });
    setBulk(st);
    while (st.state === "running") {             // runs in the background; follow it
      await new Promise((r) => setTimeout(r, 500));
      st = await api<Bulk>("/api/dedup/apply/status");
      setBulk(st);
    }
    setBulk(null);
    afterAction(`${st.state === "cancelled" ? "Stopped. " : ""}${method === "app" ? `Removed ${st.removed.toLocaleString()} files from the app` : `Moved ${st.removed.toLocaleString()} files (${fmtBytes(st.bytes)})`}.` +
      (st.failed_count ? ` ${st.failed_count} skipped (changed since the scan, or already gone).` : ""), st.batch ?? undefined);
    loadGroups(0);
  });

  const undoBatch = (batch: string) => run(async () => {
    const r = await post<{ restored: number[]; failed: unknown[] }>("/api/removals/undo", { batch });
    const n = r.restored.length;
    setMsg({ text: `Put back ${n} file${n === 1 ? "" : "s"}.` + (r.failed.length ? ` ${r.failed.length} could not be restored.` : "") });
    await regroup();
    loadSummary(); onChanged();
  });

  const current = TABS.find((t) => t.id === tab)!;
  const kindInfo = tab !== "removed" ? summary?.kinds[tab] : undefined;

  return (
    <div className="flex h-full flex-col">
      <header className="space-y-3 border-b border-neutral-800 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Duplicates</h1>
          <span className="text-sm text-neutral-400">
            {summary && `Up to ${fmtBytes((["exact", "near", "burst"] as DupKind[]).reduce((a, k) => a + summary.kinds[k].reclaim_bytes, 0))} can be freed`}
          </span>
          <span className="flex-1" />
          <label className="flex items-center gap-1 text-sm text-neutral-400">Removed files go to
            <select value={method} onChange={(e) => setMethod(e.target.value as Method)} className="rounded bg-neutral-900 px-2 py-1 text-neutral-200">
              <option value="archive">Archive folder (undo here)</option>
              {summary?.recycle_available && <option value="recycle">Recycle Bin (local drives only)</option>}
              <option value="app">Remove from app only (file stays on disk)</option>
            </select>
          </label>
          <button className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700 disabled:opacity-40" disabled={busy}
            onClick={() => run(async () => { await post("/api/dedup/run"); onChanged(); })}>Find duplicates</button>
        </div>
        <nav className="flex flex-wrap gap-1">
          {TABS.map((t) => {
            const n = t.id === "removed" ? summary?.removed_files : summary?.kinds[t.id].groups;
            return (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`rounded px-3 py-1.5 text-sm ${tab === t.id ? "bg-neutral-800 text-white" : "text-neutral-400 hover:bg-neutral-900"}`}>
                {t.label}{n != null && <span className="ml-1.5 text-xs text-neutral-500">{n.toLocaleString()}</span>}
              </button>
            );
          })}
        </nav>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-neutral-500">{current.help}</span>
          <span className="flex-1" />
          {tab !== "removed" && (
            <>
              <select value={sort} onChange={(e) => setSort(e.target.value as "size" | "date")} className="rounded bg-neutral-900 px-2 py-1">
                <option value="size">Most space first</option>
                <option value="date">Newest first</option>
              </select>
              {kindInfo && kindInfo.groups > 0 && (
                <button className="rounded bg-sky-700 px-3 py-1 hover:bg-sky-600 disabled:opacity-40" disabled={busy || working}
                  onClick={() => applyAll(tab as DupKind)}>
                  Apply suggestions to all {kindInfo.groups.toLocaleString()} ({fmtBytes(kindInfo.reclaim_bytes)})
                </button>
              )}
            </>
          )}
        </div>
        {bulk && (
          <div className="flex items-center gap-3 rounded bg-neutral-900 px-3 py-2 text-sm">
            <span className="whitespace-nowrap">{method === "app" ? "Removing from app…" : "Moving files…"} {bulk.done.toLocaleString()} / {bulk.total.toLocaleString()} groups</span>
            <div className="h-1.5 flex-1 rounded bg-neutral-800">
              <div className="h-full rounded bg-sky-500 transition-all" style={{ width: `${bulk.total ? Math.round((100 * bulk.done) / bulk.total) : 0}%` }} />
            </div>
            <span className="text-neutral-400">{fmtBytes(bulk.bytes)}</span>
            <button className="rounded bg-neutral-800 px-2 py-0.5 hover:bg-neutral-700" onClick={() => post("/api/dedup/apply/cancel")}>Stop</button>
          </div>
        )}
        {msg && (
          <div className={`flex items-center gap-3 rounded px-3 py-2 text-sm ${msg.error ? "bg-red-950 text-red-300" : "bg-emerald-950 text-emerald-300"}`}>
            <span className="flex-1">{msg.text}</span>
            {msg.batch && <button className="underline" onClick={() => undoBatch(msg.batch!)}>Undo</button>}
            <button onClick={() => setMsg(null)}>✕</button>
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === "removed" ? (
          <RemovedList busy={busy} onUndo={undoBatch} onChanged={() => { regroup(); loadSummary(); onChanged(); }} />
        ) : (
          <div className="space-y-4">
            {groups.length === 0 && <p className="text-neutral-500">{busy ? "Scanning…" : "Nothing here. Press “Find duplicates” after a scan."}</p>}
            {groups.map((g) => (
              <GroupCard key={g.id} g={g} disabled={busy || working} method={method}
                onDone={(text, batch) => { setGroups((gs) => gs.filter((x) => x.id !== g.id)); setTotal((t) => t - 1); afterAction(text, batch); }}
                onError={(text) => setMsg({ text, error: true })} />
            ))}
            {groups.length < total && (
              <button className="text-sm text-sky-400" onClick={() => loadGroups(groups.length)}>Show more ({(total - groups.length).toLocaleString()} left)</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- one group

function GroupCard({ g, disabled, method, onDone, onError }: {
  g: DupGroup; disabled: boolean; method: Method;
  onDone: (text: string, batch?: string) => void; onError: (text: string) => void;
}) {
  const [keep, setKeep] = useState<Set<number>>(() => new Set(g.members.filter((m) => m.keep).map((m) => m.id)));
  const [compare, setCompare] = useState<number | null>(null);
  const toggle = (id: number) => setKeep((k) => { const n = new Set(k); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const removeIds = g.members.filter((m) => !keep.has(m.id)).map((m) => m.id);
  const sharpest = Math.max(...g.members.map((m) => m.sharpness ?? 0));
  const freed = g.members.filter((m) => !keep.has(m.id)).reduce((a, m) => a + m.size, 0);

  const apply = async () => {
    try {
      const r = await post<{ batch: string; removed: number[]; failed: { error: string }[] }>(
        `/api/dedup/groups/${g.id}/remove`, { remove: removeIds, method });
      if (r.failed.length) onError(r.failed.map((f) => f.error).join("; "));
      const files = `${r.removed.length} file${r.removed.length > 1 ? "s" : ""}`;
      if (r.removed.length) onDone(method === "app" ? `Removed ${files} from the app; they are still on disk.` : `Moved ${files} (${fmtBytes(freed)}).`, r.batch);
    } catch (e) { onError(String(e)); }
  };
  const ignore = async () => {
    try { await post(`/api/dedup/groups/${g.id}/ignore`); onDone("Marked as not duplicates; they will not be shown again."); }
    catch (e) { onError(String(e)); }
  };

  return (
    <div className="rounded-lg bg-neutral-900 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">{g.size} {g.kind === "exact" ? "identical files" : "photos"}</span>
        {g.taken && <span className="text-neutral-500">{fmtTaken(g.taken)}</span>}
        <span className="flex-1" />
        <button className="text-neutral-400 hover:text-white disabled:opacity-40" disabled={disabled} onClick={ignore}>Not duplicates</button>
        <button className="rounded bg-sky-700 px-3 py-1 hover:bg-sky-600 disabled:opacity-40"
          disabled={disabled || removeIds.length === 0 || keep.size === 0} onClick={apply}
          title={keep.size === 0 ? "Keep at least one" : undefined}>
          {!removeIds.length ? "Nothing to move" : method === "app" ? `Remove ${removeIds.length} from app` : `Move ${removeIds.length} away (${fmtBytes(freed)})`}
        </button>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {g.members.map((m, i) => (
          <MemberTile key={m.id} m={m} keep={keep.has(m.id)} sharpest={g.kind !== "exact" && !!m.sharpness && m.sharpness === sharpest}
            showCopies={g.kind !== "exact"}
            onToggle={() => toggle(m.id)} onOpen={() => setCompare(i)} />
        ))}
      </div>
      {compare !== null && (
        <Compare members={g.members} start={compare} keep={keep} toggle={toggle} onClose={() => setCompare(null)} />
      )}
    </div>
  );
}

function MemberTile({ m, keep, sharpest, showCopies, onToggle, onOpen }: {
  m: DupMember; keep: boolean; sharpest: boolean; showCopies: boolean; onToggle: () => void; onOpen: () => void;
}) {
  return (
    <div className={`w-52 shrink-0 rounded-md border-2 p-1 ${keep ? "border-emerald-600" : "border-red-700"}`}>
      <button className="relative block aspect-square w-full overflow-hidden rounded bg-neutral-800" onClick={onOpen} title="Compare side by side">
        {m.has_thumb ? <img src={thumbUrl(m.id)} loading="lazy" className="h-full w-full object-contain" /> : null}
        {sharpest && <span className="absolute left-1 top-1 rounded bg-black/70 px-1 text-[10px] text-sky-300">sharpest</span>}
      </button>
      <button onClick={onToggle}
        className={`mt-1 w-full rounded py-0.5 text-xs font-medium ${keep ? "bg-emerald-700 hover:bg-emerald-600" : "bg-red-800 hover:bg-red-700"}`}>
        {keep ? "✓ Keep" : "✕ Move away"}
      </button>
      <div className="mt-1 space-y-0.5 text-[11px] leading-tight text-neutral-400">
        <div className="truncate text-neutral-200" title={m.filename}>{m.filename}</div>
        <div>{m.width}×{m.height} · {fmtBytes(m.size)}</div>
        {m.taken && <div>{fmtTaken(m.taken)}{m.date_source && m.date_source !== "exif" ? ` (${m.date_source})` : ""}</div>}
        <div className="truncate" title={m.folder}>{m.folder}</div>
        {showCopies && m.copies > 0 && <div className="text-amber-400">+{m.copies} identical cop{m.copies > 1 ? "ies" : "y"} (see Identical tab)</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- side by side

function Compare({ members, start, keep, toggle, onClose }: {
  members: DupMember[]; start: number; keep: Set<number>; toggle: (id: number) => void; onClose: () => void;
}) {
  // Left = the suggested keeper; right = the photo clicked (or the next one).
  const [left, setLeft] = useState(0);
  const [right, setRight] = useState(start === 0 ? Math.min(1, members.length - 1) : start);
  const [zoom, setZoom] = useState(1);
  const [origin, setOrigin] = useState({ x: 50, y: 50 });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "z") setZoom((z) => (z === 1 ? 3 : 1));
      if (e.key === "ArrowRight") setRight((r) => (r + 1) % members.length);
      if (e.key === "ArrowLeft") setRight((r) => (r - 1 + members.length) % members.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [members.length, onClose]);

  const pane = (idx: number, setIdx: (i: number) => void) => {
    const m = members[idx];
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="relative min-h-0 flex-1 cursor-zoom-in overflow-hidden rounded bg-neutral-900"
          onClick={() => setZoom((z) => (z === 1 ? 3 : 1))}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            setOrigin({ x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 });
          }}>
          <img src={previewUrl(m.id)} draggable={false} className="h-full w-full object-contain transition-transform duration-75"
            style={{ transform: `scale(${zoom})`, transformOrigin: `${origin.x}% ${origin.y}%` }} />
        </div>
        <div className="flex items-center gap-2 text-xs">
          <select value={idx} onChange={(e) => setIdx(Number(e.target.value))} className="max-w-[45%] rounded bg-neutral-900 px-1 py-1">
            {members.map((x, i) => <option key={x.id} value={i}>{i + 1}. {x.filename}</option>)}
          </select>
          <span className="truncate text-neutral-400">{m.width}×{m.height} · {fmtBytes(m.size)} · {m.folder}</span>
          <span className="flex-1" />
          <button onClick={() => toggle(m.id)}
            className={`rounded px-2 py-1 font-medium ${keep.has(m.id) ? "bg-emerald-700" : "bg-red-800"}`}>
            {keep.has(m.id) ? "✓ Keep" : "✕ Move away"}
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col gap-3 bg-black p-4">
      <div className="flex items-center gap-3 text-sm">
        <span className="font-medium">Compare</span>
        <span className="text-neutral-500">Click or press Z to zoom both · move the mouse to pan · ←/→ change the right photo · Esc closes</span>
        <span className="flex-1" />
        <button className="rounded bg-white/10 px-3 py-1 hover:bg-white/20" onClick={onClose}>Done</button>
      </div>
      <div className="flex min-h-0 flex-1 gap-3">
        {pane(left, setLeft)}
        {pane(right, setRight)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- removed

function RemovedList({ busy, onUndo, onChanged }: { busy: boolean; onUndo: (batch: string) => void; onChanged: () => void }) {
  const [rows, setRows] = useState<Removal[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<Removal[]>("/api/removals?limit=500").then(setRows).catch((e) => setErr(String(e))), []);
  useEffect(() => { load(); }, [load, busy]);

  const undoOne = async (id: number) => {
    try {
      const r = await post<{ restored: number[]; failed: { error: string }[] }>("/api/removals/undo", { ids: [id] });
      setErr(r.failed[0]?.error ?? null);
    } catch (e) { setErr(String(e)); }
    load(); onChanged();
  };

  const batches = [...new Set(rows.filter((r) => !r.restored_at).map((r) => r.batch))];
  return (
    <div className="space-y-2">
      {err && <p className="text-sm text-red-400">{err}</p>}
      {rows.length === 0 && <p className="text-neutral-500">Nothing removed yet.</p>}
      {batches.length > 0 && (
        <p className="text-sm text-neutral-400">Archived files are in a hidden <code>.photoexplorer-archive</code> folder inside each source folder.</p>
      )}
      <table className="w-full text-sm">
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={`border-b border-neutral-900 ${r.restored_at ? "opacity-40" : ""}`}>
              <td className="w-14 py-1">{r.media_id && r.has_thumb ? <img src={thumbUrl(r.media_id)} className="h-12 w-12 rounded object-cover" /> : null}</td>
              <td className="max-w-0 truncate px-2" title={r.original_path}>{r.original_path}</td>
              <td className="whitespace-nowrap px-2 text-neutral-400">{fmtBytes(r.size ?? 0)}</td>
              <td className="whitespace-nowrap px-2 text-neutral-400">{r.method === "archive" ? "Archive" : r.method === "app" ? "Removed from app" : "Recycle Bin"}</td>
              <td className="whitespace-nowrap px-2 text-neutral-500">{new Date(r.removed_at * 1000).toLocaleString()}</td>
              <td className="whitespace-nowrap px-2 text-right">
                {r.restored_at ? "Restored" : r.method !== "recycle" ? (
                  <>
                    <button className="text-sky-400 hover:underline" onClick={() => undoOne(r.id)}>Undo</button>
                    <button className="ml-3 text-xs text-neutral-500 hover:underline" onClick={() => onUndo(r.batch)}>undo batch</button>
                  </>
                ) : <span className="text-xs text-neutral-500">restore from Recycle Bin</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
