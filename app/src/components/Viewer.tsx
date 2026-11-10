// Full-screen photo/video viewer. Arrow keys move, Esc closes, I toggles info,
// F toggles face boxes, P the colour picker, S presents on another screen,
// , and . step through the frames of an animated picture. Clicking a face box
// lets you name or correct it. Zoom: mouse wheel or pinch (around the pointer),
// double-click, + / - / 0 keys; drag to move around a zoomed photo. L starts a slideshow,
// H adds to (or removes from) favourites.
import { useEffect, useRef, useState } from "react";
import { api, bigPreviewUrl, chipUrl, markMedia, EditInfo, Face, fileUrl, FramesInfo, frameUrl, Media, MediaDetail, Person, post, previewUrl } from "../api";
import EditDialog from "./EditDialog";
import Slideshow from "./Slideshow";
import PrintDialog from "./PrintDialog";
import { ColorPicker, DetailsBlock, FramesBar, useDetails } from "./ViewerTools";
import { closePresenter, listen, openPresenter, send } from "../lib/present";
import { fmtBytes, fmtTaken } from "../lib/format";
import Icon from "../lib/icons";
import { openPerson } from "../lib/nav";

export default function Viewer({ index, total, getItem, onIndex, onClose, onChanged, slideshow = false }: {
  index: number;
  total: number;
  getItem: (i: number) => Media | undefined;
  onIndex: (i: number) => void;
  onClose: () => void;
  onChanged?: () => void;          // an edit added or rewrote a photo
  slideshow?: boolean;             // start playing straight away
}) {
  const item = getItem(index);
  const [detail, setDetail] = useState<MediaDetail | null>(null);
  const [showInfo, setShowInfo] = useState(true);
  const [showFaces, setShowFaces] = useState(true);
  const [editing, setEditing] = useState<Face | null>(null);
  const [reload, setReload] = useState(0);
  const [edit, setEdit] = useState(false);
  const [edits, setEdits] = useState<EditInfo | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [bust, setBust] = useState(0);     // reload the picture after "Replace original"
  const [picking, setPicking] = useState(false);
  const [printing, setPrinting] = useState(false);
  const [presenting, setPresenting] = useState(false);
  const [frames, setFrames] = useState<FramesInfo | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(true);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imgEl, setImgEl] = useState<HTMLImageElement | null>(null);
  const details = useDetails(item?.kind === "video" ? undefined : item?.id, showInfo);
  const zoom = useZoom(item?.id);
  const [show, setShow] = useState(slideshow);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName) || edit || show) return;
      if (e.key.toLowerCase() === "l") { zoom.reset(); setShow(true); return; }
      if (e.key.toLowerCase() === "h") { toggleFav(); return; }
      if (e.key === "+" || e.key === "=") { zoom.step(1.5); return; }
      if (e.key === "-" || e.key === "_") { zoom.step(1 / 1.5); return; }
      if (e.key === "0") { zoom.reset(); return; }
      if (e.key === "Escape" && zoom.z > 1) { zoom.reset(); return; }
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" && index + 1 < total) onIndex(index + 1);
      else if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
      else if (e.key.toLowerCase() === "i") setShowInfo((v) => !v);
      else if (e.key.toLowerCase() === "f") setShowFaces((v) => !v);
      else if (e.key.toLowerCase() === "p") setPicking((v) => !v);
      else if (e.key.toLowerCase() === "s") togglePresent();
      else if ((e.key === "," || e.key === ".") && frames && frames.count > 1) {
        setPlaying(false);
        setFrame((f) => Math.max(0, Math.min(frames.count - 1, f + (e.key === "," ? -1 : 1))));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, total, onIndex, onClose, edit, frames, presenting, zoom.z, show]);

  useEffect(() => {
    setDetail(null); setEditing(null);
    if (!item) return;
    let alive = true;
    api<MediaDetail>(`/api/media/${item.id}`).then((d) => alive && setDetail(d)).catch(() => {});
    setEdits(null);
    setFrames(null); setFrame(0); setPlaying(true);
    if (item.kind !== "video" && /\.(gif|webp|png|apng|tiff?)$/i.test(item.filename))
      api<FramesInfo>(`/api/media/${item.id}/frames`).then((f) => alive && f.count > 1 && setFrames(f)).catch(() => {});
    if (item.kind !== "video") api<EditInfo>(`/api/edit/${item.id}/info`).then((d) => alive && setEdits(d)).catch(() => {});
    getItem(index + 1); getItem(index - 1); // warm neighbouring months
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id, reload]);

  const [favOverride, setFavOverride] = useState<Record<number, boolean>>({});
  const fav = item ? (favOverride[item.id] ?? !!(detail?.favorite ?? item.favorite)) : false;
  const toggleFav = async () => {
    if (!item) return;
    const v = !fav;
    setFavOverride((o) => ({ ...o, [item.id]: v }));
    item.favorite = v ? 1 : 0;
    try { await markMedia([item.id], { favorite: v }); } catch (e) { setNotice(String(e)); }
  };
  const archive = async () => {
    if (!item) return;
    const v = !(detail?.archived);
    try {
      await markMedia([item.id], { archived: v });
      setNotice(v ? "Moved to Archive (Library › Archive). It stays on disk." : "Back in the library.");
      setReload((r) => r + 1); onChanged?.();
    } catch (e) { setNotice(String(e)); }
  };

  const togglePresent = async () => {
    if (presenting) { await closePresenter(); setPresenting(false); return; }
    try { setNotice(await openPresenter()); setPresenting(true); } catch (e) { setNotice(`Could not present: ${e}`); }
  };
  const animated = frames && frames.count > 1;
  const shownSrc = !item ? "" : item.kind === "video" ? fileUrl(item.id)
    : animated ? (playing && !/\.tiff?$/i.test(item.filename) ? fileUrl(item.id) : frameUrl(item.id, frame))
    : previewUrl(item.id, bust || undefined);
  // Zoomed in on a still photo: swap in a sharper picture once it has loaded.
  const [sharp, setSharp] = useState<string | null>(null);
  useEffect(() => { setSharp(null); }, [item?.id, bust]);
  useEffect(() => {
    if (!item || item.kind === "video" || animated || zoom.z < 1.4 || sharp) return;
    const url = bigPreviewUrl(item.id, 4096, bust || undefined);
    const im = new Image();
    im.onload = () => setSharp(url);
    im.src = url;
    return () => { im.onload = null; };
  }, [item?.id, animated, zoom.z >= 1.4, sharp, bust]);
  useEffect(() => {
    if (!presenting || !item) return;
    const msg = { type: "show" as const, src: item.kind === "video" ? fileUrl(item.id) : animated ? fileUrl(item.id) : previewUrl(item.id, bust || undefined),
      kind: item.kind === "video" ? "video" as const : "image" as const };
    send(msg);
    const stop = listen((m) => {
      if (m.type === "hello") send(msg);
      if (m.type === "bye") setPresenting(false);
    });
    return stop;
  }, [presenting, item?.id, bust, animated]);
  useEffect(() => () => { if (presenting) closePresenter(); }, [presenting]);
  // TIFF pages (and paused animations) are shown frame by frame; playing a TIFF steps on a timer.
  useEffect(() => {
    if (!animated || !playing || !item || !/\.tiff?$/i.test(item.filename)) return;
    const t = setTimeout(() => setFrame((f) => (f + 1) % frames!.count), Math.max(300, frames!.durations[frame] || 800));
    return () => clearTimeout(t);
  }, [animated, playing, frame, frames, item]);

  // Load the next and previous pictures ahead, so moving between photos is instant.
  const preloaded = useRef<HTMLImageElement[]>([]);
  useEffect(() => {
    const t = setTimeout(() => {
      preloaded.current = [1, -1, 2].flatMap((d) => {
        const m = getItem(index + d);
        if (!m || m.kind === "video") return [];
        const im = new Image();
        im.decoding = "async";
        im.src = previewUrl(m.id);
        return [im];
      });
    }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, item?.id]);

  return (
    <div data-theme="dark" className="pe-open fixed inset-0 z-50 flex bg-black/95">
      <div className="relative flex flex-1 items-center justify-center min-w-0" onClick={(e) => e.target === e.currentTarget && onClose()}>
        {!item ? (
          <span className="text-neutral-500">Loading…</span>
        ) : item.kind === "video" ? (
          <video key={item.id} src={fileUrl(item.id)} controls autoPlay className="max-h-full max-w-full" />
        ) : (
          <div className="relative inline-block will-change-transform" {...zoom.bind}
            style={{ transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.z})`, transition: zoom.dragging ? "none" : "transform 120ms ease-out",
              cursor: picking ? undefined : zoom.z > 1 ? (zoom.dragging ? "grabbing" : "grab") : "zoom-in" }}>
            <img key={`${item.id}-${bust}`} ref={(el) => { imgRef.current = el; if (el !== imgEl) setImgEl(el); }} src={sharp ?? shownSrc}
              className={`block max-h-[100vh] max-w-full object-contain ${animated && item.width ? "min-h-[40vh]" : ""}`} draggable={false} />
            {showFaces && detail?.faces.map((f) => (
              <button key={f.id} onClick={() => !zoom.moved() && setEditing(f)}
                className={`absolute border-2 ${f.person ? "border-sky-400" : "border-white/60"} hover:border-amber-400`}
                style={{ left: `${f.x * 100}%`, top: `${f.y * 100}%`, width: `${f.w * 100}%`, height: `${f.h * 100}%` }}>
                <span className="absolute left-0 top-full mt-0.5 whitespace-nowrap rounded bg-black/70 px-1 text-xs">
                  {f.person ?? "?"}{f.assigned_by === "auto" ? " (suggested)" : ""}
                </span>
              </button>
            ))}
          </div>
        )}
        <button className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-xl hover:bg-white/20 disabled:opacity-20"
          disabled={index === 0} onClick={() => onIndex(index - 1)}>‹</button>
        <button className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-white/10 px-3 py-2 text-xl hover:bg-white/20 disabled:opacity-20"
          disabled={index + 1 >= total} onClick={() => onIndex(index + 1)}>›</button>
        <div className="absolute left-3 top-3 flex gap-2 text-xs">
          <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={onClose}>✕ Close</button>
          <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={() => setShowInfo((v) => !v)}>Info (I)</button>
          <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={() => setShowFaces((v) => !v)}>Faces (F)</button>
          <button className={`flex items-center gap-1 rounded px-2 py-1 ${fav ? "bg-rose-600/80 text-white" : "bg-white/10 hover:bg-white/20"}`}
            title="Favourite (H)" onClick={toggleFav}><Icon name="heart" filled={fav} className="h-3.5 w-3.5" />{fav ? "Favourite" : "Favourite (H)"}</button>
          {item && <button className="flex items-center gap-1 rounded bg-white/10 px-2 py-1 hover:bg-white/20"
            title={detail?.archived ? "Bring back into the library" : "Hide from the library without deleting"} onClick={archive}>
            <Icon name={detail?.archived ? "unarchive" : "archive"} className="h-3.5 w-3.5" />{detail?.archived ? "Unarchive" : "Archive"}</button>}
          <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" title="Play the photos full screen, one after another"
            onClick={() => { zoom.reset(); setShow(true); }}>▶ Slideshow (L)</button>
          {item && item.kind !== "video" && (
            <button className="rounded bg-sky-700 px-2 py-1 hover:bg-sky-600" onClick={() => setEdit(true)}>✎ Edit</button>
          )}
          {item && item.kind !== "video" && (
            <button className={`rounded px-2 py-1 ${picking ? "bg-amber-600" : "bg-white/10 hover:bg-white/20"}`}
              title="Read the colour of any point" onClick={() => setPicking((v) => !v)}>Colour (P)</button>
          )}
          {item && item.kind !== "video" && (
            <button className="rounded bg-white/10 px-2 py-1 hover:bg-white/20" onClick={() => setPrinting(true)}>Print…</button>
          )}
          <button className={`rounded px-2 py-1 ${presenting ? "bg-amber-600" : "bg-white/10 hover:bg-white/20"}`}
            title="Show the photos full screen on another display (TV, projector)" onClick={togglePresent}>
            {presenting ? "Stop presenting (S)" : "Present (S)"}</button>
          {notice && <span className="rounded bg-emerald-900/80 px-2 py-1 text-emerald-200">{notice}
            <button className="ml-2" onClick={() => setNotice(null)}>✕</button></span>}
          <span className="px-2 py-1 text-neutral-500">{(index + 1).toLocaleString()} / {total.toLocaleString()}</span>
          {item && item.kind !== "video" && (
            <span className="flex items-center overflow-hidden rounded bg-white/10">
              <button className="px-2 py-1 hover:bg-white/20" title="Zoom out (-)" onClick={() => zoom.step(1 / 1.5)}>−</button>
              <button className="min-w-[3.5rem] px-1 py-1 tabular-nums hover:bg-white/20" title="Fit to screen (0)" onClick={zoom.reset}>
                {zoom.z > 1 ? `${Math.round(zoom.z * 100)}%` : "Fit"}</button>
              <button className="px-2 py-1 hover:bg-white/20" title="Zoom in (+)" onClick={() => zoom.step(1.5)}>+</button>
            </span>
          )}
        </div>
        {picking && item && item.kind !== "video" && (
          <ColorPicker img={imgEl} src={animated ? frameUrl(item.id, frame) : previewUrl(item.id, bust || undefined)} onClose={() => setPicking(false)} />
        )}
        {!picking && animated && item && (
          <FramesBar id={item.id} info={frames!} frame={frame} playing={playing} onFrame={setFrame} onPlaying={setPlaying} onNotice={setNotice} />
        )}
        {editing && (
          <FaceEditor face={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); setReload((r) => r + 1); }} />
        )}
      </div>
      {showInfo && detail && <InfoPanel d={detail} edits={edits}
        onFace={(f) => (f.person_id != null ? (onClose(), openPerson(f.person_id)) : setEditing(f))}><DetailsBlock d={details} /></InfoPanel>}
      {show && <Slideshow index={index} total={total} getItem={getItem} onIndex={onIndex} onStop={() => (slideshow ? onClose() : setShow(false))} />}
      {printing && item && <PrintDialog mediaIds={[item.id]} onClose={() => setPrinting(false)} />}
      {edit && item && <EditDialog mediaIds={[item.id]} onClose={() => setEdit(false)}
        onSaved={(msg) => { setNotice(msg); setReload((r) => r + 1); setBust(Date.now()); onChanged?.(); }} />}
    </div>
  );
}

// Zoom and pan for the picture. The wrapper is centred, so offsets are measured from its centre.
const MAX_Z = 8;
function useZoom(resetKey: unknown) {
  const [st, setSt] = useState({ z: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ sx: number; sy: number; x: number; y: number; moved: boolean } | null>(null);
  const movedRef = useRef(false);
  useEffect(() => { setSt({ z: 1, x: 0, y: 0 }); }, [resetKey]);

  // zoom by factor f (or to an exact level) keeping the point p (offset from the picture's resting centre) still
  const zoomAt = (f: number, px: number, py: number, exact = false) => setSt((s) => {
    const nz = Math.min(MAX_Z, Math.max(1, exact ? f : s.z * f));
    if (nz === 1) return { z: 1, x: 0, y: 0 };
    const k = nz / s.z;
    return { z: nz, x: px - (px - s.x) * k, y: py - (py - s.y) * k };
  });
  const centreOf = (el: HTMLElement, s: { x: number; y: number }) => {
    const r = el.getBoundingClientRect();
    return { cx: r.left + r.width / 2 - s.x, cy: r.top + r.height / 2 - s.y };
  };

  const bind = {
    onWheel: (e: React.WheelEvent<HTMLDivElement>) => {
      const { cx, cy } = centreOf(e.currentTarget, st);
      const f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0025));   // pinch sends ctrl + small deltas
      zoomAt(f, e.clientX - cx, e.clientY - cy);
    },
    onDoubleClick: (e: React.MouseEvent<HTMLDivElement>) => {
      const { cx, cy } = centreOf(e.currentTarget, st);
      if (st.z > 1) setSt({ z: 1, x: 0, y: 0 }); else zoomAt(2.5, e.clientX - cx, e.clientY - cy, true);
    },
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      movedRef.current = false;
      if (st.z <= 1 || e.button !== 0) return;
      drag.current = { sx: e.clientX, sy: e.clientY, x: st.x, y: st.y, moved: false };
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = drag.current;
      if (!d) return;
      const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
      if (!d.moved && Math.hypot(dx, dy) < 4) return;
      if (!d.moved) { d.moved = true; movedRef.current = true; setDragging(true); e.currentTarget.setPointerCapture(e.pointerId); }
      setSt((s) => ({ ...s, x: d.x + dx, y: d.y + dy }));
    },
    onPointerUp: () => { drag.current = null; setDragging(false); },
    onPointerCancel: () => { drag.current = null; setDragging(false); },
  };
  return {
    ...st, dragging, bind,
    moved: () => movedRef.current,
    step: (f: number) => zoomAt(f, 0, 0),
    reset: () => setSt({ z: 1, x: 0, y: 0 }),
  };
}

function InfoPanel({ d, edits, children, onFace }: { d: MediaDetail; edits: EditInfo | null; children?: React.ReactNode; onFace: (f: Face) => void }) {
  const [place, setPlace] = useState<{ name: string; state: string; country: string; km: number } | null>(null);
  const [note, setNote] = useState(d.note ?? "");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setNote(d.note ?? ""); setPlace(null); setSaved(false);
    if (d.gps_lat != null) api<typeof place>(`/api/media/${d.id}/place`).then(setPlace).catch(() => {});
  }, [d.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const saveNote = () => {
    if (note === (d.note ?? "")) return;
    post(`/api/media/${d.id}/note`, { note }).then(() => { d.note = note; setSaved(true); }).catch(() => {});
  };
  const row = (k: string, v: string | number | null | undefined) =>
    v ? <div><div className="text-[11px] uppercase text-neutral-500">{k}</div><div className="break-words">{v}</div></div> : null;
  return (
    <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-l border-neutral-800 bg-neutral-950 p-4 text-sm">
      {row("Taken", `${fmtTaken(d.taken)}${d.date_source && d.date_source !== "exif" ? `  (from ${d.date_source})` : ""}`)}
      {row("File", d.filename)}
      {row("Folder", d.path.slice(0, d.path.length - d.filename.length - 1))}
      {row("Size", `${d.width ?? "?"} × ${d.height ?? "?"} · ${fmtBytes(d.size)}`)}
      {row("Camera", [d.camera_make, d.camera_model].filter(Boolean).join(" "))}
      {row("Lens", d.lens)}
      {place && row("Place", [place.name, place.state, place.country].filter(Boolean).join(", ") + (place.km > 25 ? ` (${Math.round(place.km)} km away)` : ""))}
      {d.gps_lat != null && d.gps_lon != null && <MiniMap lat={d.gps_lat} lon={d.gps_lon} />}
      {row("Location", d.gps_lat != null && d.gps_lon != null ? `${d.gps_lat.toFixed(5)}, ${d.gps_lon.toFixed(5)}` : null)}
      {d.faces.length > 0 && (
        <div>
          <div className="text-[11px] uppercase text-neutral-500">People</div>
          <div className="mt-1 flex flex-wrap gap-2">
            {d.faces.map((f) => (
              <button key={f.id} onClick={() => onFace(f)} title={f.person ? `Open ${f.person}` : "Who is this?"}
                className="flex w-14 flex-col items-center gap-0.5 text-[11px] text-neutral-300 hover:text-white">
                <img src={chipUrl(f.id)} alt="" className="h-11 w-11 rounded-full object-cover ring-1 ring-neutral-700" />
                <span className="w-full truncate text-center">{f.person ?? "?"}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div>
        <div className="flex text-[11px] uppercase text-neutral-500"><span className="flex-1">Description</span>{saved && <span className="normal-case text-emerald-400">saved</span>}</div>
        <textarea value={note} onChange={(e) => { setNote(e.target.value); setSaved(false); }} onBlur={saveNote} rows={3}
          placeholder="Add a note… (searchable)" className="mt-1 w-full resize-y rounded bg-neutral-900 px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-sky-500" />
      </div>
      {edits?.edit_of && row("Edited copy of", edits.edit_of.filename)}
      {edits && edits.edited_copies.length > 0 && row("Edited copies", edits.edited_copies.map((c) => c.filename).join(", "))}
      {edits?.can_restore && row("Edited", "Saved over the original. Edit > Restore original brings it back.")}
      {d.same_content.length > 0 && row("Identical copies", `${d.same_content.length} other file${d.same_content.length > 1 ? "s" : ""}`)}
      {children}
    </aside>
  );
}

function FaceEditor({ face, onClose, onDone }: { face: Face; onClose: () => void; onDone: () => void }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [name, setName] = useState(face.person ?? "");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Person[]>("/api/people?include_hidden=true").then(setPeople).catch(() => {}); }, []);

  const run = async (fn: () => Promise<unknown>) => {
    try { await fn(); onDone(); } catch (e) { setErr(String(e)); }
  };
  const save = () => {
    const n = name.trim();
    if (!n) return;
    const existing = people.find((p) => p.name.toLowerCase() === n.toLowerCase());
    run(() => post("/api/faces/assign", existing ? { face_ids: [face.id], person_id: existing.id } : { face_ids: [face.id], name: n }));
  };

  return (
    <div className="absolute bottom-6 left-1/2 w-80 -translate-x-1/2 space-y-2 rounded-lg bg-neutral-900 p-3 text-sm shadow-xl">
      <div className="text-neutral-400">{face.person ? `Marked as ${face.person}${face.assigned_by === "auto" ? " (suggested)" : ""}` : "Who is this?"}</div>
      <div className="flex gap-2">
        <input autoFocus list="pe-people" value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") onClose(); }}
          className="flex-1 rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500" placeholder="Name" />
        <button className="rounded bg-sky-600 px-3 hover:bg-sky-500" onClick={save}>Save</button>
      </div>
      <datalist id="pe-people">{people.map((p) => <option key={p.id} value={p.name} />)}</datalist>
      {face.person_id != null && (
        <button className="text-xs text-amber-400 hover:underline"
          onClick={() => run(() => post("/api/faces/reject", { face_ids: [face.id], person_id: face.person_id }))}>
          This is not {face.person}
        </button>
      )}
      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}

// A small offline map (Natural Earth outlines) around where a photo was taken.
let worldOnce: Promise<{ countries: { r: number[][] }[] }> | null = null;
function MiniMap({ lat, lon }: { lat: number; lon: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    worldOnce ??= api<{ countries: { r: number[][] }[] }>("/api/map/world");
    let alive = true;
    worldOnce.then((w) => {
      const c = ref.current;
      if (!alive || !c) return;
      const W = 248, H = 130, dpr = window.devicePixelRatio || 1, z = 18;   // pixels per degree
      c.width = W * dpr; c.height = H * dpr;
      const x = c.getContext("2d")!;
      x.setTransform(dpr, 0, 0, dpr, 0, 0);
      x.fillStyle = "#0d1424"; x.fillRect(0, 0, W, H);
      x.fillStyle = "#1f2a40"; x.strokeStyle = "#3a4a6b"; x.lineWidth = 0.8;
      const px = (lo: number) => W / 2 + (lo - lon) * z, py = (la: number) => H / 2 - (la - lat) * z;
      for (const ct of w.countries) {
        x.beginPath();
        for (const ring of ct.r) {
          for (let i = 0; i < ring.length; i += 2) (i ? x.lineTo : x.moveTo).call(x, px(ring[i]), py(ring[i + 1]));
          x.closePath();
        }
        x.fill(); x.stroke();
      }
      x.fillStyle = "#f43f5e"; x.strokeStyle = "#fff"; x.lineWidth = 2;
      x.beginPath(); x.arc(W / 2, H / 2, 6, 0, Math.PI * 2); x.fill(); x.stroke();
    }).catch(() => {});
    return () => { alive = false; };
  }, [lat, lon]);
  return <canvas ref={ref} style={{ width: 248, height: 130 }} className="rounded-lg" />;
}
