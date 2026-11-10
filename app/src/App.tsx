// App shell: navigation, the scan panel, and the views.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, connect, focusWindow, isTauri, post, ScanStatus, setShownSources, Source, startupFile, Stats } from "./api";
import LooseViewer from "./components/LooseViewer";
import logo from "./assets/logo.png";
import Splash from "./components/Splash";
import PlacesView from "./views/PlacesView";
import ThingsView from "./views/ThingsView";
import ScanPanel, { isBusy, loadPanel } from "./components/ScanPanel";
import AlbumsView from "./views/AlbumsView";
import DuplicatesView from "./views/DuplicatesView";
import FoldersView from "./views/FoldersView";
import GoogleView from "./views/GoogleView";
import LibraryView from "./views/LibraryView";
import MemoriesView from "./views/MemoriesView";
import PeopleView from "./views/PeopleView";
import { setTopBarSlot } from "./lib/topbar";
import { LibraryPreset, onOpenLibrary, onOpenPerson } from "./lib/nav";
import { restoreWindow } from "./lib/windowState";
import SetupView from "./views/SetupView";
import UpdatesView from "./views/UpdatesView";
import { setAppearance, useAppearance } from "./lib/appearance";
import LookMenu, { OpenWith } from "./components/LookMenu";

type View = "library" | "people" | "places" | "things" | "albums" | "duplicates" | "memories" | "folders" | "google" | "updates";
// Sources the user switched off in the sidebar (kept between runs; new sources start switched on).
const HIDDEN_KEY = "pe.hiddenSources";
function loadHidden(): number[] {
  try { return JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]"); } catch { return []; }
}

// Small line icons for the Gallery layout's sidebar (24x24, stroke).
const ICONS: Record<View, string> = {
  library: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  people: "M16 19c0-2.2-1.8-4-4-4s-4 1.8-4 4M12 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  places: "M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  things: "M4 4h7v7H4zM17.5 4a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7zM7.5 13l4 7h-8zM14 14h6v6h-6z",
  albums: "M5 4h14v16l-7-4-7 4z",
  duplicates: "M8 8h11v11H8zM5 16V5h11",
  memories: "M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6-5.3-3-5.3 3 1.2-6L3.4 9.3l6-.7z",
  folders: "M3 6h6l2 2h10v10H3z",
  google: "M4 15l4-4 3 3 5-5 4 4M4 5h16v14H4z",
  updates: "M12 4v10m0 0l-4-4m4 4l4-4M5 20h14",
};

const NAV: { id: View; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "people", label: "People" },
  { id: "places", label: "Places" },
  { id: "things", label: "Things" },
  { id: "albums", label: "Albums" },
  { id: "duplicates", label: "Duplicates" },
  { id: "memories", label: "Memories" },
  { id: "folders", label: "Folders" },
  { id: "google", label: "Google Photos" },
  { id: "updates", label: "Update software" },
];

export default function App() {
  const [ready, setReady] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("library");
  const [sources, setSources] = useState<Source[]>([]);
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [hidden, setHidden] = useState<number[]>(loadHidden);
  const wasBusy = useRef(false);
  const look = useAppearance();
  const [loose, setLoose] = useState<string | null>(null);   // a picture opened from Explorer
  const [openWith, setOpenWith] = useState<OpenWith | null>(null);
  const gallery = look.layout === "gallery";
  const closed = look.sideClosed;
  const [preset, setPreset] = useState<LibraryPreset | undefined>(undefined);   // filters handed to Library
  const [personId, setPersonId] = useState<number | undefined>(undefined);
  const [suggest, setSuggest] = useState(0);                                     // faces waiting for "Is this X?"
  useEffect(() => { restoreWindow(); }, []);
  useEffect(() => {
    if (!ready) return;
    const get = () => api<{ suggestions?: number }>("/api/faces/status").then((r) => setSuggest(r.suggestions ?? 0)).catch(() => {});
    get();
    const t = setInterval(get, 30000);
    return () => clearInterval(t);
  }, [ready, refreshKey, view]);
  useEffect(() => onOpenLibrary((p) => { setPreset({ ...p }); setView("library"); }), []);
  useEffect(() => onOpenPerson((id) => { setPersonId(id); setView("people"); }), []);
  useEffect(() => { if ((view as string) === "timeline") setView("library"); }, [view]);
  const [intro, setIntro] = useState(() => look.intro);   // the opening animation, once per start

  const refresh = useCallback(async () => {
    try {
      const [s, st, sc] = await loadPanel();
      setSources(s); setStats(st); setScan(sc); setError(null);
      // When a scan finishes, reload the photo views once.
      if (wasBusy.current && !isBusy(sc)) setRefreshKey((k) => k + 1);
      wasBusy.current = isBusy(sc);
    } catch (e) { setError(String(e)); }
  }, []);

  useEffect(() => {
    connect().then(() => { setReady(true); refresh(); })
      .catch((e) => (String(e).endsWith("setup") ? setNeedsSetup(true) : setError(String(e))));
  }, [refresh]);

  const busy = isBusy(scan);

  // "Open with Photo Explorer": the picture this copy was started with, and any
  // opened later (a second copy hands them to this one and exits).
  useEffect(() => {
    if (!ready) return;
    startupFile().then((p) => p && setLoose(p)).catch(() => {});
    if (isTauri) api<typeof openWith>("/api/system/open-with").then(setOpenWith).catch(() => {});
    const t = setInterval(() => {
      api<string[]>("/api/opened").then((ps) => {
        if (ps.length) { setLoose(ps[ps.length - 1]); focusWindow(); }
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(t);
  }, [ready]);

  // Only ids of sources that still exist count; hiding none (or all) means "show all".
  const hiddenNow = hidden.filter((id) => sources.some((s) => s.id === id));
  const shown = hiddenNow.length && hiddenNow.length < sources.length
    ? sources.filter((s) => !hiddenNow.includes(s.id)).map((s) => s.id) : null;
  setShownSources(shown); // before the views below render and query
  const scopeKey = shown ? shown.join(",") : "all";
  const changeHidden = (ids: number[]) => {
    setHidden(ids);
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(ids)); } catch { /* not saved; fine */ }
  };
  useEffect(() => {
    if (!ready) return;
    const t = setInterval(refresh, busy ? 1000 : 5000);
    return () => clearInterval(t);
  }, [ready, busy, refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { setError(String(e)); }
    refresh();
  };

  if (needsSetup) return <SetupView onReady={() => { setNeedsSetup(false); setReady(true); refresh(); }} />;

  return (
    <div className="flex h-full">
      {closed ? (
        <aside className={`pe-side flex w-16 shrink-0 flex-col items-center gap-2 overflow-y-auto py-3 ${gallery ? "" : "border-r border-neutral-800"}`}>
          <img src={logo} alt="" data-pe-logo className="h-8 w-8" draggable={false} />
          <button className="rounded-md px-2 py-0.5 text-lg text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100"
            title="Show the side pane" onClick={() => setAppearance({ sideClosed: false })}>»</button>
          <nav className="flex flex-col items-center gap-1">
            {NAV.map((n) => (
              <button key={n.id} onClick={() => setView(n.id)} title={n.label}
                className={`rounded-full p-2.5 ${view === n.id ? "bg-sky-950 text-sky-300" : "text-neutral-400 hover:bg-neutral-900"}`}>
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.8}
                  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICONS[n.id]} /></svg>
              </button>
            ))}
          </nav>
          {busy && <span className="mt-2 h-2 w-2 animate-pulse rounded-full bg-sky-400" title="Scanning…" />}
        </aside>
      ) : (
      <aside className={`pe-side flex w-72 shrink-0 flex-col gap-5 overflow-y-auto p-4 ${gallery ? "" : "border-r border-neutral-800"}`}>
        <h1 className={`flex items-center gap-2.5 ${gallery ? "px-2 text-xl font-semibold" : "text-lg font-semibold"}`}>
          <img src={logo} alt="" data-pe-logo className={gallery ? "h-8 w-8" : "h-7 w-7"} draggable={false} />
          <span className="flex-1">Photo Explorer</span>
          <button className="rounded-md px-2 py-0.5 text-lg font-normal text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100"
            title="Hide the side pane" onClick={() => setAppearance({ sideClosed: true })}>«</button></h1>
        <nav className={gallery ? "space-y-1" : "space-y-0.5"}>
          {NAV.map((n) => gallery ? (
            <button key={n.id} onClick={() => setView(n.id)}
              className={`flex w-full items-center gap-4 rounded-full px-4 py-2 text-left text-sm ${view === n.id
                ? "bg-sky-950 font-semibold text-sky-300" : "text-neutral-300 hover:bg-neutral-900"}`}>
              <svg viewBox="0 0 24 24" className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.8}
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICONS[n.id]} /></svg>
              {n.label}{n.id === "people" && suggest > 0 && <span className="ml-auto rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold text-black" title="Faces waiting for your answer: Is this …?">{suggest > 99 ? "99+" : suggest}</span>}
            </button>
          ) : (
            <button key={n.id} onClick={() => setView(n.id)}
              className={`flex w-full rounded px-3 py-1.5 text-left text-sm ${view === n.id ? "bg-neutral-800 text-white" : "text-neutral-400 hover:bg-neutral-900"}`}>
              {n.label}{n.id === "people" && suggest > 0 && <span className="ml-auto rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold text-black" title="Faces waiting for your answer: Is this …?">{suggest > 99 ? "99+" : suggest}</span>}
            </button>
          ))}
        </nav>
        <ScanPanel sources={sources} scan={scan} stats={stats} ready={ready} act={act}
          hidden={hiddenNow} onHiddenChange={changeHidden} />
        {error && <div className="break-words text-xs text-red-400">{error}</div>}
      </aside>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
      {/* One top bar for every section: its search, filters and actions, then Look (always last). */}
      <div className="pe-topbar flex min-h-[3.25rem] shrink-0 flex-wrap items-center gap-2 border-b border-neutral-900 px-3 py-2 text-sm">
        <div ref={setTopBarSlot} className="flex min-w-0 flex-1 flex-wrap items-center gap-2" />
        {closed && error && <span className="truncate text-xs text-red-400">{error}</span>}
        <LookMenu openWith={openWith} onOpenWith={(enable) => post<OpenWith>("/api/system/open-with", { enable })
          .then(setOpenWith).catch((er) => setError(String(er)))} />
      </div>
      <main key={scopeKey} className="min-h-0 min-w-0 flex-1">
        {!ready ? (
          <p className="p-6 text-neutral-500">{error ? "Could not reach the engine." : "Starting the engine…"}</p>
        ) : view === "library" ? <LibraryView refreshKey={refreshKey} preset={preset} />
          : view === "places" ? <PlacesView refreshKey={refreshKey} />
          : view === "things" ? <ThingsView busy={busy} refreshKey={refreshKey} />
          : view === "people" ? <PeopleView busy={busy} scanState={scan?.state} onChanged={refresh} openId={personId} />
          : view === "duplicates" ? <DuplicatesView busy={busy && scan?.state !== "faces" && scan?.state !== "clustering"} onChanged={refresh} />
          : view === "albums" ? <AlbumsView key={refreshKey} />
          : view === "memories" ? <MemoriesView />
          : view === "google" ? <GoogleView onChanged={refresh} />
          : view === "updates" ? <UpdatesView />
          : <FoldersView refreshKey={refreshKey} />}
      </main>
      </div>
      {loose && <LooseViewer path={loose} onClose={() => setLoose(null)}
        onAddFolder={(folder) => act(async () => { await post("/api/sources", { path: folder }); await post("/api/scan", {}); setLoose(null); setView("library"); })} />}
      {intro && <Splash ready={ready || !!error} tune={look.tune} onDone={() => setIntro(false)} />}
    </div>
  );
}
