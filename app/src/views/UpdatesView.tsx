// Update software: new app versions plus theme and music packs, downloaded
// only when the user asks. "Restart to update" swaps in a downloaded version.
import { useCallback, useEffect, useState } from "react";
import { api, post, quitApp, UpdateStatus } from "../api";
import { fmtBytes } from "../lib/format";

export default function UpdatesView() {
  const [st, setSt] = useState<UpdateStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [url, setUrl] = useState("");
  const [showUrl, setShowUrl] = useState(false);

  const load = useCallback(() => api<UpdateStatus>("/api/updates").then((s) => { setSt(s); return s; }), []);
  useEffect(() => { load().then((s) => setUrl(s.manifest_url)).catch((e) => setErr(String(e))); }, [load]);
  const running = st?.job.state === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => load().catch(() => {}), 700);
    return () => clearInterval(t);
  }, [running, load]);

  const act = async (fn: () => Promise<UpdateStatus | unknown>) => {
    setErr(null);
    try { const r = await fn(); if (r && typeof r === "object" && "job" in r) setSt(r as UpdateStatus); else await load(); }
    catch (e) { setErr(String(e).replace(/^Error: /, "")); }
  };
  const check = async () => { setChecking(true); await act(() => post<UpdateStatus>("/api/updates/check")); setChecking(false); };
  const restart = () => act(async () => {
    await post("/api/updates/apply");
    if (!(await quitApp())) setErr("The update starts when you close Photo Explorer.");
  });

  if (!st) return <div className="p-6 text-neutral-500">{err ?? "Loading…"}</div>;
  const todo = st.packs.filter((p) => p.state !== "installed");
  const appNew = st.app?.newer && !st.app.ready && st.can_apply;   // the app download is skipped where it can't be installed
  const job = st.job;
  const pct = job.bytes_total ? Math.round((100 * job.bytes) / job.bytes_total) : 0;

  return (
    <div className="h-full space-y-5 overflow-y-auto p-5">
      <header>
        <h1 className="text-xl font-semibold">Update software</h1>
        <p className="text-sm text-neutral-400">
          Photo Explorer {st.current_version}. Checking and downloading happen only when you click; your photos and library are never sent anywhere.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <button className="rounded bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-40"
          disabled={checking || running || !st.manifest_url} onClick={check}>{checking ? "Checking…" : "Check for updates"}</button>
        {st.checked_at && !running && (todo.length > 0 || appNew) && (
          <button className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500"
            onClick={() => act(() => post("/api/updates/install", { packs: null, app: true }))}>
            Download {[appNew && "the new version", todo.length && `${todo.length} pack${todo.length === 1 ? "" : "s"}`].filter(Boolean).join(" and ")}
          </button>
        )}
        {st.checked_at && <span className="text-xs text-neutral-500">Checked {new Date(st.checked_at * 1000).toLocaleTimeString()}</span>}
        <button className="ml-auto text-xs text-neutral-500 hover:text-neutral-300" onClick={() => setShowUrl((v) => !v)}>Update address…</button>
      </div>

      {(showUrl || !st.manifest_url) && (
        <div className="space-y-2 rounded-lg bg-neutral-900 p-3 text-sm">
          <p className="text-neutral-400">{st.manifest_url ? "Where updates come from:" : "No update address is set yet. Paste the address of the manifest file:"}</p>
          <div className="flex gap-2">
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/<owner>/<repo>/releases/latest/download/manifest.json"
              className="flex-1 rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500" />
            <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700"
              onClick={() => act(() => post<UpdateStatus>("/api/updates/url", { url }))}>Save</button>
          </div>
        </div>
      )}

      {st.last_update && (
        <p className={`rounded-lg p-3 text-sm ${st.last_update.ok ? "bg-emerald-950/50 text-emerald-300" : "bg-red-950/50 text-red-300"}`}>
          {st.last_update.message}
        </p>
      )}
      {err && <p className="text-sm text-red-400">{err}</p>}

      {(running || job.state === "done" || job.state === "cancelled" || job.state === "error") && job.total > 0 && (
        <div className="space-y-2 rounded-lg bg-neutral-900 p-4 text-sm">
          <div className="flex items-center gap-3">
            <span className="flex-1">{running ? `${job.message} (${job.done + 1} of ${job.total})` : job.state === "done" ? "Downloads finished." : job.state === "cancelled" ? "Stopped." : job.message}</span>
            {running && <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700" onClick={() => act(() => post("/api/updates/cancel"))}>Stop</button>}
          </div>
          {running && (
            <>
              <div className="h-1.5 rounded bg-neutral-800"><div className="h-full rounded bg-sky-500 transition-all" style={{ width: `${pct}%` }} /></div>
              <div className="text-xs text-neutral-500">{fmtBytes(job.bytes)}{job.bytes_total ? ` of ${fmtBytes(job.bytes_total)}` : ""}</div>
            </>
          )}
          {job.failed.map((f, i) => <div key={i} className="text-xs text-red-400">{f.item}: {f.error}</div>)}
        </div>
      )}

      {st.app && (
        <section className="rounded-lg bg-neutral-900 p-4 text-sm">
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <div className="font-medium">Photo Explorer {st.app.version}</div>
              <div className="text-xs text-neutral-400">
                {!st.app.newer ? "You have the latest version." : st.app.ready ? "Downloaded. Restart to finish updating; your library stays as it is."
                  : `New version${st.app.size ? ` · ${fmtBytes(st.app.size)}` : ""}`}
              </div>
              {st.app.newer && st.app.notes && <p className="mt-1 whitespace-pre-line text-xs text-neutral-300">{st.app.notes}</p>}
              {st.app.newer && st.app_problem && (
                <p className="mt-2 rounded bg-amber-950/60 p-2 text-xs text-amber-300">
                  {st.app_problem} The new version is also on the{" "}
                  <span className="underline" title={st.manifest_url.replace(/\/latest\/download\/.*$/, "/latest")}>releases page</span>{" "}
                  ({st.manifest_url.replace(/\/latest\/download\/.*$/, "/latest")}).
                </p>
              )}
            </div>
            {st.app.ready && st.can_apply && (
              <button className="rounded bg-emerald-600 px-4 py-2 font-medium hover:bg-emerald-500" onClick={restart}>Restart to update</button>
            )}
          </div>
        </section>
      )}

      {st.packs.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-xs uppercase tracking-wide text-neutral-400">Themes and music</h2>
          <ul className="space-y-2">
            {st.packs.map((p) => (
              <li key={p.id} className="flex items-center gap-3 rounded-lg bg-neutral-900 p-3 text-sm">
                <span className="text-xl">{p.kind === "music" ? "🎵" : "🎨"}</span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{p.title}</div>
                  <div className="text-xs text-neutral-400">
                    {p.description}{p.size ? ` · ${fmtBytes(p.size)}` : ""}
                  </div>
                </div>
                {p.state === "installed" ? <span className="text-xs text-emerald-400">Installed</span> : (
                  <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700 disabled:opacity-40" disabled={running}
                    onClick={() => act(() => post("/api/updates/install", { packs: [p.id], app: false }))}>
                    {p.state === "update" ? "Update" : "Download"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-2 text-sm">
        <h2 className="text-xs uppercase tracking-wide text-neutral-400">On this computer</h2>
        {st.installed.length === 0 ? <p className="text-neutral-500">No packs yet: the 10 built-in themes are always there.</p> : (
          <ul className="space-y-1 text-neutral-300">
            {st.installed.map((p) => <li key={p.id}>{p.kind === "music" ? "🎵" : "🎨"} {p.title} <span className="text-xs text-neutral-500">v{p.version}</span></li>)}
          </ul>
        )}
        <button className="rounded bg-neutral-800 px-3 py-1 hover:bg-neutral-700" onClick={() => act(() => post("/api/music/open-folder"))}>
          Open my music folder</button>
        <p className="text-xs text-neutral-500">Songs you put in this folder (MP3, M4A, WAV, FLAC, OGG) appear in the music list of every slideshow.</p>
      </section>
    </div>
  );
}
