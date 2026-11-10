// Google Photos: one-time setup, sign-in, then "pick photos" imports.
import { useCallback, useEffect, useState } from "react";
import { api, GoogleStatus, pickFolder, post } from "../api";

export default function GoogleView({ onChanged }: { onChanged: () => void }) {
  const [st, setSt] = useState<GoogleStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [folder, setFolder] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => api<GoogleStatus>("/api/gphotos/status").then(setSt).catch((e) => setErr(String(e))), []);
  const busy = st && (st.login_state === "waiting" || ["waiting", "downloading", "scanning"].includes(st.import.state));
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, busy ? 1000 : 4000);
    return () => clearInterval(t);
  }, [load, busy]);
  useEffect(() => { if (st?.import.state === "done") onChanged(); }, [st?.import.state, onChanged]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); setErr(null); } catch (e) { setErr(String(e)); }
    load();
  };
  if (!st) return <p className="p-6 text-neutral-500">Loading…</p>;
  const imp = st.import;
  const input = "w-full rounded bg-neutral-800 px-2 py-1.5 text-sm outline-none focus:ring-1 focus:ring-sky-500";

  return (
    <div className="h-full max-w-3xl overflow-y-auto p-5 space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">Google Photos</h1>
        <p className="text-sm text-neutral-400">
          Bring photos from Google Photos into this library. Google only lets apps import photos <b>you pick</b> in
          Google's own picker (since March 2025, apps cannot read a whole Google Photos library). Picked photos are
          downloaded at full quality into a folder on this PC and then browse, group and export like any other photo.
        </p>
      </header>
      {err && <p className="rounded bg-red-950 p-2 text-sm text-red-300">{err}</p>}

      <section className="space-y-3 rounded-lg bg-neutral-900 p-4">
        <h2 className="font-medium">1. Your Google client ID {st.configured && <span className="text-sm text-emerald-400">✓ saved</span>}</h2>
        {!st.configured || !st.connected ? (
          <>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-neutral-400">
              <li>Open console.cloud.google.com and create a project (free).</li>
              <li>Enable the <b>Google Photos Picker API</b>.</li>
              <li>Set up the OAuth consent screen (External; add your own Google account as a test user).</li>
              <li>Credentials → Create credentials → OAuth client ID → Application type <b>Desktop app</b>.</li>
              <li>Copy the client ID and client secret here.</li>
            </ol>
            {st.configured && <p className="text-xs text-neutral-500">Saved: {st.client_id}. Enter new values to replace it.</p>}
            <input className={input} placeholder="Client ID (…apps.googleusercontent.com)" value={clientId} onChange={(e) => setClientId(e.target.value)} />
            <input className={input} placeholder="Client secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} />
            <button className="rounded bg-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-600 disabled:opacity-40" disabled={!clientId || !secret}
              onClick={() => act(async () => { await post("/api/gphotos/client", { client_id: clientId, client_secret: secret }); setSecret(""); })}>Save</button>
            <p className="text-xs text-neutral-500">Stored encrypted for your Windows user on this PC only.</p>
          </>
        ) : <p className="text-sm text-neutral-400">{st.client_id}</p>}
      </section>

      <section className="space-y-3 rounded-lg bg-neutral-900 p-4">
        <h2 className="font-medium">2. Sign in {st.connected && <span className="text-sm text-emerald-400">✓ connected</span>}</h2>
        {st.connected ? (
          <button className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700" onClick={() => act(() => post("/api/gphotos/disconnect"))}>Disconnect</button>
        ) : (
          <>
            <p className="text-sm text-neutral-400">Opens Google's sign-in page in your web browser. This app never sees your password; it only asks for read access to photos you pick.</p>
            <button className="rounded bg-sky-700 px-3 py-1.5 text-sm hover:bg-sky-600 disabled:opacity-40" disabled={!st.configured || st.login_state === "waiting"}
              onClick={() => act(() => post("/api/gphotos/connect"))}>{st.login_state === "waiting" ? "Waiting for the browser…" : "Sign in with Google"}</button>
            {st.login_state === "error" && <p className="text-sm text-red-400">{st.login_error}</p>}
          </>
        )}
      </section>

      <section className="space-y-3 rounded-lg bg-neutral-900 p-4">
        <h2 className="font-medium">3. Pick photos to import</h2>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-neutral-400">Save into</span>
          <span className="min-w-0 flex-1 truncate" title={folder ?? st.default_folder}>{folder ?? st.default_folder}</span>
          <button className="rounded bg-neutral-800 px-2 py-1 hover:bg-neutral-700" onClick={async () => setFolder(await pickFolder("Import Google Photos into"))}>Change…</button>
        </div>
        <div className="flex gap-2">
          <button className="rounded bg-sky-700 px-3 py-1.5 text-sm hover:bg-sky-600 disabled:opacity-40" disabled={!st.connected || !!busy}
            onClick={() => act(() => post("/api/gphotos/import", { folder }))}>Open Google Photos picker</button>
          {busy && imp.state !== "idle" && (
            <button className="rounded bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700" onClick={() => act(() => post("/api/gphotos/import/cancel"))}>Cancel</button>
          )}
        </div>
        {imp.state !== "idle" && (
          <div className="space-y-1 text-sm">
            <div className={imp.state === "error" ? "text-red-400" : "text-neutral-300"}>{imp.message}</div>
            {imp.picked > 0 && (
              <div className="text-xs text-neutral-500">{imp.picked} picked · {imp.downloaded} downloaded · {imp.skipped} already here{imp.failed ? ` · ${imp.failed} failed` : ""}</div>
            )}
            {imp.state === "waiting" && imp.picker_uri && (
              <p className="text-xs text-neutral-500">The picker opened in your browser. Didn't see it? Open <span className="select-all text-neutral-300">{imp.picker_uri}</span></p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
