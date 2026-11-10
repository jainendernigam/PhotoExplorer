// First run of the small starter download: the desktop shell fetches the
// engine from GitHub (see src-tauri/src/setup.rs), then the theme and music
// packs are installed through Update software.
import { useEffect, useRef, useState } from "react";
import { connect, post, api, UpdateStatus } from "../api";
import { fmtBytes } from "../lib/format";

type SetupStatus = { state: string; message: string; bytes: number; total: number; restart: boolean };

export default function SetupView({ onReady }: { onReady: () => void }) {
  const [st, setSt] = useState<SetupStatus>({ state: "idle", message: "Getting ready", bytes: 0, total: 0, restart: false });
  const [step, setStep] = useState<"app" | "packs">("app");
  const [packs, setPacks] = useState<UpdateStatus["job"] | null>(null);
  const started = useRef(false);

  const invoke = async <T,>(cmd: string): Promise<T> => (await import("@tauri-apps/api/core")).invoke<T>(cmd);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    invoke("setup_start");
    const t = setInterval(async () => {
      const s = await invoke<SetupStatus>("setup_status");
      setSt(s);
      if (s.state === "done" || s.state === "error") clearInterval(t);
      if (s.state === "done" && !s.restart) installPacks();
    }, 500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The engine is running now: add the theme and music packs (best effort).
  const installPacks = async () => {
    setStep("packs");
    try {
      await connect();
      await post("/api/updates/check");
      await post("/api/updates/install", { packs: null, app: false });
      for (;;) {
        const u = await api<UpdateStatus>("/api/updates");
        setPacks(u.job);
        if (u.job.state !== "running") break;
        await new Promise((r) => setTimeout(r, 700));
      }
    } catch { /* no packs or no connection: the app works without them */ }
    onReady();
  };

  const pct = st.total ? Math.round((100 * st.bytes) / st.total) : 0;
  const packPct = packs?.bytes_total ? Math.round((100 * packs.bytes) / packs.bytes_total) : 0;
  return (
    <div className="grid h-full place-items-center p-6">
      <div className="w-[28rem] max-w-full space-y-4 rounded-xl bg-neutral-900 p-6">
        <h1 className="text-xl font-semibold">Setting up Photo Explorer</h1>
        <p className="text-sm text-neutral-400">
          This first start downloads the rest of the app (about {st.total ? fmtBytes(st.total) : "150 MB"}) from the
          project's GitHub page, then themes and music. It happens once; your photos are never uploaded.
        </p>
        {step === "app" && st.state !== "error" && !st.restart && (
          <div className="space-y-1.5 text-sm">
            <div>{st.message}…</div>
            <div className="h-2 rounded bg-neutral-800"><div className="h-full rounded bg-sky-500 transition-all" style={{ width: `${st.state === "unpacking" ? 100 : pct}%` }} /></div>
            {st.total > 0 && <div className="text-xs text-neutral-500">{fmtBytes(st.bytes)} of {fmtBytes(st.total)}</div>}
          </div>
        )}
        {step === "packs" && (
          <div className="space-y-1.5 text-sm">
            <div>{packs?.message || "Adding themes and music"}…</div>
            <div className="h-2 rounded bg-neutral-800"><div className="h-full rounded bg-emerald-500 transition-all" style={{ width: `${packPct}%` }} /></div>
          </div>
        )}
        {st.restart && (
          <div className="space-y-3 text-sm">
            <p>Downloaded. Photo Explorer needs to restart once to finish.</p>
            <button className="rounded bg-sky-600 px-4 py-2 font-medium hover:bg-sky-500" onClick={() => invoke("restart_app")}>Restart</button>
          </div>
        )}
        {st.state === "error" && (
          <div className="space-y-3 text-sm">
            <p className="text-red-400">{st.message}</p>
            <button className="rounded bg-neutral-800 px-4 py-2 hover:bg-neutral-700"
              onClick={() => { started.current = false; setSt({ ...st, state: "idle", message: "Trying again" }); location.reload(); }}>Try again</button>
          </div>
        )}
      </div>
    </div>
  );
}
