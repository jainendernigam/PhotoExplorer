// Talks to the Python backend. Inside Tauri the port/token come from the
// Rust shell (which started the backend); in a plain browser (vite dev) they
// come from VITE_PE_URL / VITE_PE_TOKEN, e.g. with
//   python -m photoexplorer serve --port 8765 --token dev

export type Backend = { base: string; token: string };

const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

let backend: Backend | null = null;

export async function connect(): Promise<Backend> {
  if (backend) return backend;
  if (inTauri) {
    const { invoke } = await import("@tauri-apps/api/core");
    for (let i = 0; i < 600; i++) {
      try {
        const info = await invoke<{ port: number; token: string }>("backend_info");
        return (backend = { base: `http://127.0.0.1:${info.port}`, token: info.token });
      } catch (e) {
        if (String(e) !== "starting") throw new Error(String(e));
        await sleep(100);
      }
    }
    throw new Error("Backend did not start within 60 s");
  }
  return (backend = {
    base: import.meta.env.VITE_PE_URL ?? "http://127.0.0.1:8765",
    token: import.meta.env.VITE_PE_TOKEN ?? "dev",
  });
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const b = await connect();
  const res = await fetch(b.base + path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-PE-Token": b.token, ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

const authed = (path: string) =>
  backend ? `${backend.base}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(backend.token)}` : "";
/** `v` (e.g. the file size) makes the browser fetch it again after a photo is rewritten. */
export const thumbUrl = (id: number, v?: number) => authed(`/api/thumb/${id}${v ? `?v=${v}` : ""}`);
export const previewUrl = (id: number, v?: number) => authed(`/api/preview/${id}${v ? `?v=${v}` : ""}`);
/** A larger preview for zooming in (HEIC and RAW included). */
export const bigPreviewUrl = (id: number, size: number, v?: number) =>
  authed(`/api/preview/${id}?size=${size}${v ? `&v=${v}` : ""}`);
export const fileUrl = (id: number) => authed(`/api/file/${id}`);
export const chipUrl = (faceId: number) => authed(`/api/face/${faceId}/chip`);
export const exportFileUrl = (id: number) => authed(`/api/exports/${id}/file`);
export const exportThumbUrl = (id: number) => authed(`/api/exports/${id}/thumb`);
export const musicUrl = (id: string) => authed(`/api/music/file?id=${encodeURIComponent(id)}`);

export const frameUrl = (id: number, n: number) => authed(`/api/media/${id}/frame/${n}`);
export const loosePreviewUrl = (path: string) => authed(`/api/loose/preview?path=${encodeURIComponent(path)}`);
export const isTauri = inTauri;

export type PhotoDetails = {
  settings: Record<string, string>; tags: Record<string, string>; profile: string | null;
  format: string | null; mode: string | null; frames: number; error?: string;
};
export type FramesInfo = { count: number; durations: number[] };

/** A picture opened from Explorer ("Open with Photo Explorer") when the app started. */
export async function startupFile(): Promise<string | null> {
  if (!inTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return (await invoke<string | null>("startup_file")) ?? null;
}

/** Brings the main window to the front (a picture was opened from Explorer). */
export async function focusWindow(): Promise<void> {
  if (!inTauri) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  const w = getCurrentWindow();
  try { await w.unminimize(); await w.setFocus(); } catch { /* not allowed: fine */ }
}

/** Closes the desktop app (after "Restart to update" has started the updater). */
export async function quitApp(): Promise<boolean> {
  if (!inTauri) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("quit_app");
  return true;
}

export async function pickFolders(): Promise<string[]> {
  if (inTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ directory: true, multiple: true, title: "Add photo folders" });
    return r == null ? [] : Array.isArray(r) ? r : [r];
  }
  const p = window.prompt("Folder path to add:");
  return p ? [p] : [];
}

/** One folder (Tauri dialog; a text prompt in a plain browser). */
export async function pickFolder(title = "Choose a folder"): Promise<string | null> {
  if (inTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ directory: true, multiple: false, title });
    return typeof r === "string" ? r : null;
  }
  return window.prompt(`${title} (full path):`) || null;
}

export async function pickAudioFile(): Promise<string | null> {
  if (inTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const r = await open({ multiple: false, title: "Choose music",
      filters: [{ name: "Audio", extensions: ["mp3", "m4a", "aac", "wav", "flac", "ogg"] }] });
    return typeof r === "string" ? r : null;
  }
  return window.prompt("Music file (full path):") || null;
}

// ---- which source folders the photo views show (null = all of them) ----
let shownSources: number[] | null = null;
export function setShownSources(ids: number[] | null) { shownSources = ids; }
/** Value for the engine's `sources` parameter, or undefined when all are shown. */
export const shownSourcesParam = () => shownSources?.join(",");

/** Filters in the shape /api/exports expects. */
export function filtersForExport(f: Filters): Record<string, unknown> {
  return {
    q: f.q || undefined, kind: f.kind, persons: f.persons?.length ? f.persons : undefined,
    persons_mode: f.personsMode, date_from: f.dateFrom, date_to: f.dateTo,
    folder_id: f.folderId, source_id: f.sourceId, album_id: f.albumId,
    sources: f.sourceId === undefined ? shownSources ?? undefined : undefined,
    fav: f.fav || undefined, show: f.show, tag: f.tag, pet: f.pet, place: f.place, bbox: f.bbox,
  };
}

// ---- filters shared by the photo grid, timeline and people pages ----
export type Filters = {
  q?: string;
  kind?: "image" | "raw" | "video";
  persons?: number[];
  personsMode?: "all" | "any";
  dateFrom?: string; // YYYY-MM-DD
  dateTo?: string;
  folderId?: number;
  sourceId?: number;
  albumId?: number;
  order?: "asc" | "desc";
  fav?: boolean;                       // favourites only
  show?: "main" | "archive" | "all";   // archived photos are left out unless asked for
  tag?: string;                        // a category: screenshot, selfie, document, receipt, dog, cat
  pet?: string;                        // a pet id, or "c<group>"
  place?: number;                      // a town from /api/places
  bbox?: string;                       // lat_min,lon_min,lat_max,lon_max
};

export function filterQuery(f: Filters, extra: Record<string, string | number | undefined> = {}): string {
  const p = new URLSearchParams();
  const set = (k: string, v: string | number | undefined) => { if (v !== undefined && v !== "") p.set(k, String(v)); };
  set("q", f.q?.trim());
  set("kind", f.kind);
  if (f.persons?.length) { set("persons", f.persons.join(",")); set("persons_mode", f.personsMode ?? "all"); }
  set("date_from", f.dateFrom);
  set("date_to", f.dateTo);
  set("folder_id", f.folderId);
  set("source_id", f.sourceId);
  if (f.sourceId === undefined) set("sources", shownSourcesParam());
  set("album_id", f.albumId);
  set("order", f.order ?? "desc");
  if (f.fav) set("fav", "true");
  set("show", f.show);
  set("tag", f.tag);
  set("pet", f.pet);
  set("place", f.place);
  set("bbox", f.bbox);
  for (const [k, v] of Object.entries(extra)) set(k, v);
  return p.toString();
}

// ---- types mirrored from the backend ----
export type Theme = {
  id: string; name: string; description: string; music: string | null;
  options: Record<string, unknown>; source: string;
};
export type Track = {
  id: string; title: string; mood: string; seconds: number | null; licence: string; source: string; path: string;
};
export type UpdatePack = {
  id: string; kind: "themes" | "music"; version: number; title: string; description?: string; size?: number;
  items?: number; installed_version: number | null; state: "new" | "update" | "installed";
};
export type UpdateStatus = {
  current_version: string; manifest_url: string; checked_at: number | null; can_apply: boolean;
  app_problem: string | null; last_update: { ok: boolean; message: string } | null;
  app: { version: string; notes: string; size?: number; newer: boolean; ready: boolean } | null;
  packs: UpdatePack[];
  installed: { id: string; kind: string; version: number; title: string }[];
  job: { state: "idle" | "running" | "done" | "cancelled" | "error"; message: string; item: string | null;
         done: number; total: number; bytes: number; bytes_total: number; failed: { item: string; error: string }[] };
};
export type Source = {
  id: number; path: string; label: string | null; files: number;
  online: boolean; last_scan_at: number | null;
};
export type ScanStatus = {
  state: "idle" | "walking" | "indexing" | "faces" | "clustering" | "dedup" | "smart" | "done" | "cancelled" | "error";
  mode: "full" | "faces" | "recheck" | "cluster" | "dedup" | "smart";
  dedup_exact: number | null; dedup_similar: number | null;
  current: string | null; folders: number; discovered: number; new: number;
  changed: number; missing: number; to_index: number; indexed: number;
  errors: number; rate: number; eta_s: number | null;
  faces_todo: number; faces_done: number; faces_found: number; faces_copied: number;
  smart_todo?: number; smart_done?: number;
  clusters: number | null; warnings: string[]; error: string | null;
};
export type Media = {
  id: number; filename: string; kind: "image" | "raw" | "video"; path: string;
  width: number | null; height: number | null; taken: string | null; has_thumb: number;
  duration_ms: number | null; size: number; favorite?: number; archived?: number;
};
export type Face = {
  id: number; x: number; y: number; w: number; h: number; score: number;
  person_id: number | null; person: string | null; assigned_by: string | null; cluster_id: number | null;
};
export type MediaDetail = Media & {
  camera_make: string | null; camera_model: string | null; lens: string | null;
  gps_lat: number | null; gps_lon: number | null; date_source: string | null;
  same_content: number[]; faces: Face[]; note: string | null;
};
export type Bucket = { month: string; count: number };
export type Person = {
  id: number; name: string; birth_date: string | null; hidden: number;
  faces: number; photos: number; cover_face_id: number | null;
};
export type Cluster = { cluster_id: number; faces: number; sample_face_ids: number[] };
export type FaceItem = {
  id: number; media_id: number; score: number; size_px: number; assigned_by: string | null;
  cluster_id: number | null; person_id: number | null; taken: string | null;
};
export type FacesStatus = {
  faces: number; photos: number; unnamed_clusters: number; named_faces: number;
  photos_todo: number; persons: number; available: boolean; missing: string[]; ungrouped: number;
};
export type ExportJob = {
  id: number; kind: "slideshow" | "collage"; title: string | null;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number; message: string | null; output_path: string | null;
  created_at: number; finished_at: number | null; photos: number; exists: boolean;
};
export type GoogleStatus = {
  configured: boolean; client_id: string | null; connected: boolean;
  login_state: "idle" | "waiting" | "error"; login_error: string | null; default_folder: string;
  import: { state: "idle" | "waiting" | "downloading" | "scanning" | "done" | "error" | "cancelled";
    message: string | null; picked: number; downloaded: number; skipped: number; failed: number;
    picker_uri: string | null; folder: string | null };
};

export type Album = { id: number; name: string; photos: number; cover_media_id: number | null; created_at: number };
export type OrganizeMode = "copy" | "hardlink" | "symlink" | "move";
export type OrganizePreview = {
  files: number; new: number; already_there: number; same_in_selection: number; bytes: number; folders: number;
  sample: { src: string; dest: string; status: string }[]; errors: string[]; warnings: string[];
};
export type OrganizeJob = {
  id: number; status: "queued" | "running" | "done" | "error" | "cancelled"; mode: OrganizeMode; dest_root: string;
  planned: number; done: number; skipped: number; failed: number; bytes: number; progress: number;
  message: string | null; manifest_path: string | null; created_at: number; finished_at: number | null; undone_at: number | null;
};
export type Presets = {
  folders: Record<string, { label: string; template: string }>;
  names: Record<string, { label: string; template: string }>;
  tokens: Record<string, string>;
};

export type DupKind = "exact" | "near" | "burst";
export type DupMember = {
  id: number; rank: number; keep: number; path: string; filename: string; folder: string;
  size: number; width: number | null; height: number | null; taken: string | null;
  date_source: string | null; camera_model: string | null; sharpness: number | null;
  has_thumb: number; kind: string; status: number; copies: number;
};
export type DupGroup = {
  id: number; kind: DupKind; size: number; reclaim_bytes: number; taken: string | null;
  max_distance: number; members: DupMember[];
};
export type DupSummary = {
  kinds: Record<DupKind, { groups: number; files: number; reclaim_bytes: number }>;
  removed_files: number; removed_bytes: number; recycle_available: boolean;
};
export type Removal = {
  id: number; media_id: number | null; original_path: string; archived_path: string | null;
  method: "archive" | "recycle" | "app"; size: number; reason: string; batch: string;
  removed_at: number; restored_at: number | null; has_thumb: number | null;
};

export type Stats = {
  breakdown: { kind: string; status: number; n: number; bytes: number }[];
  exact_duplicate_groups: number; exact_duplicate_extra_files: number;
  date_from: string | null; date_to: string | null;
};

// ---- photo edits ----
export type EditText = { text: string; where: "top" | "center" | "bottom"; size: number; color: string; font: string };
export type EditOps = {
  rotate: 0 | 90 | 180 | 270; flip_h: boolean; flip_v: boolean; straighten: number;
  crop: { x: number; y: number; w: number; h: number } | null;
  auto: boolean; brightness: number; contrast: number; saturation: number; warmth: number;
  highlights: number; shadows: number; filter: string; sharpen: number; denoise: number;
  redeye: boolean; resize: number; frame: "none" | "white" | "black" | "print"; frame_size: number;
  text: EditText | null;
};
export const EDIT_DEFAULTS: EditOps = {
  rotate: 0, flip_h: false, flip_v: false, straighten: 0, crop: null, auto: false, brightness: 0, contrast: 0,
  saturation: 0, warmth: 0, highlights: 0, shadows: 0, filter: "natural", sharpen: 0, denoise: 0, redeye: false,
  resize: 0, frame: "none", frame_size: 4, text: null,
};
export type EditInfo = {
  editable: boolean; can_replace: boolean; edit_of: { id: number; filename: string } | null;
  edited_copies: { id: number; filename: string }[]; can_restore: boolean; edited_at: number | null;
};
export type EditBatch = { state: "idle" | "running" | "done" | "cancelled"; done: number; total: number;
  failed: { id: number; error: string }[]; created: number[]; message: string };

/** The edited photo as an object URL (revoke it when replaced). */
export async function editPreview(id: number, ops: EditOps, size = 1400): Promise<string> {
  const b = await connect();
  const res = await fetch(`${b.base}/api/edit/${id}/preview`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-PE-Token": b.token },
    body: JSON.stringify({ ops, size }),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(String(msg));
  }
  return URL.createObjectURL(await res.blob());
}

// ---- auto organize ----
export type OrganizeRule = {
  id: number; name: string; enabled: number; dest_root: string; mode: OrganizeMode;
  options: Record<string, unknown>; people: number[]; people_names: string[]; people_mode: "any" | "all";
  last_run_at: number | null; last_result: { message: string; new: number; error?: string } | null;
  last_job_id: number | null; last_job: { status: string; done: number; undone_at: number | null } | null;
};

// ---- favourites, archive, things, pets, places, memories, face models ----
export const markMedia = (ids: number[], change: { favorite?: boolean; archived?: boolean }) =>
  post<{ changed: number }>("/api/media/mark", { media_ids: ids, ...change });
export type Download = { state: "running" | "done" | "error"; done: number; total: number; error: string | null } | null;
export type SmartStatus = { available: boolean; missing: string[]; todo: number; done: number; download: Download; size_mb: number };
export type Category = { tag: string; label: string; count: number; cover: number };
export type PetGroup = { id?: number; name?: string; cluster?: number; species: string; photos: number; cover: number };
export type Place = { id: number; name: string; state: string; country: string; lat: number; lon: number; count: number; cover: number };
export type MemoryCard = { key: string; kind: string; title: string; subtitle: string; cover: number | null; media_ids: number[] };
export type FaceModels = {
  current: string; installed: string[];
  packs: Record<string, { label: string; size_mb: number }>; downloads: Record<string, Download>;
};
