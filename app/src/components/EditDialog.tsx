// Photo editor: one photo (all tools, with a live preview) or a batch of
// selected photos (the tools that make sense for many at once). The original
// is never changed unless "Replace original" is chosen when saving, and even
// then the file as it was is kept so it can be restored.
import { useEffect, useRef, useState } from "react";
import { api, EDIT_DEFAULTS, EditBatch, EditInfo, EditOps, editPreview, pickFolder, post } from "../api";

const FILTERS = ["natural", "warm", "cool", "vivid", "faded", "sepia", "mono", "cinematic"];
const FONTS = ["sans", "serif", "script", "bold", "hand"];
const ASPECTS: { label: string; r: number | null }[] = [
  { label: "Free", r: null }, { label: "1:1", r: 1 }, { label: "4:3", r: 4 / 3 }, { label: "3:4", r: 3 / 4 },
  { label: "16:9", r: 16 / 9 }, { label: "4:5", r: 4 / 5 },
];
const SHARE = [{ label: "Keep size", v: 0 }, { label: "WhatsApp / email (1600 px)", v: 1600 },
  { label: "Small (1080 px)", v: 1080 }, { label: "Large (2048 px)", v: 2048 }, { label: "4K (3840 px)", v: 3840 }];

type Props = { mediaIds: number[]; onClose: () => void; onSaved: (msg: string, newId?: number) => void };

export default function EditDialog({ mediaIds, onClose, onSaved }: Props) {
  const single = mediaIds.length === 1;
  const id = mediaIds[0];
  const [ops, setOps] = useState<EditOps>(EDIT_DEFAULTS);
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<EditInfo | null>(null);
  const [cropping, setCropping] = useState(false);
  const [aspect, setAspect] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [batch, setBatch] = useState<EditBatch | null>(null);
  const timer = useRef<number>();

  const set = <K extends keyof EditOps>(k: K, v: EditOps[K]) => setOps((o) => ({ ...o, [k]: v }));
  const changed = JSON.stringify(ops) !== JSON.stringify(EDIT_DEFAULTS);

  useEffect(() => { if (single) api<EditInfo>(`/api/edit/${id}/info`).then(setInfo).catch(() => {}); }, [id, single]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !saving && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  // Live preview: the crop box is drawn over the uncropped picture while cropping.
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const shown = cropping ? { ...ops, crop: null, frame: "none" as const, text: null } : ops;
        const u = await editPreview(id, shown);
        setUrl((old) => { if (old) URL.revokeObjectURL(old); return u; });
        setErr(null);
      } catch (e) { setErr(String(e)); }
    }, 180);
    return () => window.clearTimeout(timer.current);
  }, [ops, id, cropping]);

  const save = async (mode: "copy" | "replace", folder: boolean) => {
    let dest: string | null = null;
    if (folder) { dest = await pickFolder("Save the edited copy in"); if (!dest) return; }
    setSaving(true); setErr(null);
    try {
      if (single) {
        const r = await post<{ media_id: number; path: string }>(`/api/edit/${id}/save`, { ops, mode, dest_dir: dest });
        onSaved(mode === "replace" ? "Saved over the original (Restore original brings it back)." : `Saved as ${r.path}`, r.media_id);
        onClose();
      } else {
        await post("/api/edit/batch", { media_ids: mediaIds, ops, mode, dest_dir: dest });
        for (;;) {
          const b = await api<EditBatch>("/api/edit/batch");
          setBatch(b);
          if (b.state !== "running") {
            onSaved(b.message + (b.failed.length ? ` (${b.failed[0].error})` : ""));
            if (!b.failed.length) onClose();
            break;
          }
          await new Promise((r) => setTimeout(r, 600));
        }
      }
    } catch (e) { setErr(String(e)); }
    setSaving(false);
  };

  return (
    <div className="fixed inset-0 z-[60] flex bg-black/95 text-sm">
      <div className="relative flex min-w-0 flex-1 items-center justify-center p-4">
        {url ? (
          cropping ? <CropBox url={url} crop={ops.crop} aspect={aspect} onChange={(c) => set("crop", c)} />
            : <img src={url} className="max-h-full max-w-full object-contain" draggable={false} />
        ) : <span className="text-neutral-500">Loading…</span>}
        {!single && <div className="absolute left-4 top-4 rounded bg-black/70 px-2 py-1 text-xs">
          Preview of the first photo · applies to all {mediaIds.length}</div>}
      </div>

      <aside className="flex w-80 shrink-0 flex-col border-l border-neutral-800 bg-neutral-950">
        <div className="flex items-center border-b border-neutral-800 p-3">
          <h2 className="flex-1 font-semibold">{single ? "Edit photo" : `Edit ${mediaIds.length} photos`}</h2>
          <button className="text-neutral-400 hover:text-white disabled:opacity-40" disabled={saving} onClick={onClose}>✕</button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-3">
          <Section title={single ? "Rotate and crop" : "Rotate"}>
            <div className="flex flex-wrap gap-1">
              <Btn onClick={() => set("rotate", (((ops.rotate + 270) % 360) as EditOps["rotate"]))}>⟲ Left</Btn>
              <Btn onClick={() => set("rotate", (((ops.rotate + 90) % 360) as EditOps["rotate"]))}>⟳ Right</Btn>
              <Btn on={ops.flip_h} onClick={() => set("flip_h", !ops.flip_h)}>⇋ Flip</Btn>
              <Btn on={ops.flip_v} onClick={() => set("flip_v", !ops.flip_v)}>⇵ Flip</Btn>
            </div>
            {single && <>
              <Slider label="Straighten" value={ops.straighten} min={-15} max={15} step={0.5} unit="°" onChange={(v) => set("straighten", v)} />
              <div className="flex flex-wrap items-center gap-1">
                <Btn on={cropping} onClick={() => { setCropping(!cropping); if (!ops.crop) set("crop", { x: 0.05, y: 0.05, w: 0.9, h: 0.9 }); }}>
                  {cropping ? "✓ Done cropping" : "✂ Crop"}</Btn>
                {ops.crop && <Btn onClick={() => { set("crop", null); setCropping(false); }}>Remove crop</Btn>}
              </div>
              {cropping && <div className="flex flex-wrap gap-1">
                {ASPECTS.map((a) => <Btn key={a.label} on={aspect === a.r} onClick={() => {
                  setAspect(a.r);
                  if (a.r && ops.crop) set("crop", fitAspect(ops.crop, a.r, imgRatio(url)));
                }}>{a.label}</Btn>)}
              </div>}
            </>}
          </Section>

          <Section title="Light and colour">
            <Btn on={ops.auto} onClick={() => set("auto", !ops.auto)}>✨ Auto fix</Btn>
            <Slider label="Brightness" value={ops.brightness} onChange={(v) => set("brightness", v)} />
            <Slider label="Contrast" value={ops.contrast} onChange={(v) => set("contrast", v)} />
            <Slider label="Saturation" value={ops.saturation} onChange={(v) => set("saturation", v)} />
            <Slider label="Warmth" value={ops.warmth} onChange={(v) => set("warmth", v)} />
            <Slider label="Highlights" value={ops.highlights} onChange={(v) => set("highlights", v)} />
            <Slider label="Shadows" value={ops.shadows} onChange={(v) => set("shadows", v)} />
          </Section>

          <Section title="Filter">
            <div className="flex flex-wrap gap-1">
              {FILTERS.map((f) => <Btn key={f} on={ops.filter === f} onClick={() => set("filter", f)}>{f === "natural" ? "None" : cap(f)}</Btn>)}
            </div>
          </Section>

          <Section title="Detail">
            <Slider label="Sharpen" value={ops.sharpen} min={0} onChange={(v) => set("sharpen", v)} />
            <Slider label="Reduce noise" value={ops.denoise} min={0} onChange={(v) => set("denoise", v)} />
            {single && <label className="flex items-center gap-2"><input type="checkbox" checked={ops.redeye}
              onChange={(e) => set("redeye", e.target.checked)} /> Fix red eyes <span className="text-xs text-neutral-500">(uses the faces found)</span></label>}
          </Section>

          <Section title="Frame and text">
            <div className="flex flex-wrap gap-1">
              {(["none", "white", "black", "print"] as const).map((f) => <Btn key={f} on={ops.frame === f} onClick={() => set("frame", f)}>{f === "none" ? "No frame" : cap(f)}</Btn>)}
            </div>
            {ops.frame !== "none" && <Slider label="Border" value={ops.frame_size} min={1} max={15} unit="%" onChange={(v) => set("frame_size", v)} />}
            <input value={ops.text?.text ?? ""} placeholder="Text on the photo (optional)"
              onChange={(e) => set("text", e.target.value ? { where: "bottom", size: 6, color: "#ffffff", font: "sans", ...(ops.text ?? {}), text: e.target.value } : null)}
              className="w-full rounded bg-neutral-800 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500" />
            {ops.text && <div className="flex flex-wrap items-center gap-1">
              <select value={ops.text.where} onChange={(e) => set("text", { ...ops.text!, where: e.target.value as "top" })} className="rounded bg-neutral-800 px-1 py-0.5 text-xs">
                <option value="top">Top</option><option value="center">Middle</option><option value="bottom">Bottom</option></select>
              <select value={ops.text.font} onChange={(e) => set("text", { ...ops.text!, font: e.target.value })} className="rounded bg-neutral-800 px-1 py-0.5 text-xs">
                {FONTS.map((f) => <option key={f} value={f}>{cap(f)}</option>)}</select>
              <input type="color" value={ops.text.color} onChange={(e) => set("text", { ...ops.text!, color: e.target.value })} className="h-6 w-8 bg-transparent" />
              <input type="range" min={2} max={20} value={ops.text.size} onChange={(e) => set("text", { ...ops.text!, size: +e.target.value })} className="w-20" title="Text size" />
            </div>}
          </Section>

          <Section title="Size">
            <select value={ops.resize} onChange={(e) => set("resize", +e.target.value)} className="w-full rounded bg-neutral-800 px-2 py-1">
              {SHARE.map((s) => <option key={s.v} value={s.v}>{s.label}</option>)}</select>
          </Section>
        </div>

        <div className="space-y-2 border-t border-neutral-800 p-3">
          {err && <p className="text-xs text-red-400">{err}</p>}
          {batch && <div className="text-xs text-neutral-300">{batch.message} · {batch.done}/{batch.total}
            {batch.failed.length > 0 && <span className="text-amber-300"> · {batch.failed.length} failed</span>}</div>}
          <div className="flex gap-2">
            <button className="flex-1 rounded bg-neutral-800 py-1.5 hover:bg-neutral-700 disabled:opacity-40" disabled={!changed || saving}
              onClick={() => setOps(EDIT_DEFAULTS)}>Reset</button>
            {info?.can_restore && single && <button className="flex-1 rounded bg-neutral-800 py-1.5 text-amber-300 hover:bg-neutral-700 disabled:opacity-40"
              disabled={saving} onClick={async () => {
                try { await post(`/api/edit/${id}/restore`); onSaved("Original restored."); onClose(); } catch (e) { setErr(String(e)); }
              }}>Restore original</button>}
          </div>
          <SaveChoice disabled={!changed || saving} canReplace={single ? !!info?.can_replace : true} batch={!single} onSave={save} saving={saving} />
        </div>
      </aside>
    </div>
  );
}

/** "Save as a copy" or "Replace original" (asked every time). */
function SaveChoice({ disabled, canReplace, batch, saving, onSave }: {
  disabled: boolean; canReplace: boolean; batch: boolean; saving: boolean;
  onSave: (mode: "copy" | "replace", folder: boolean) => void;
}) {
  const [ask, setAsk] = useState<null | "replace">(null);
  if (ask === "replace") return (
    <div className="space-y-2 rounded bg-amber-950/60 p-2 text-xs">
      <p className="text-amber-200">Replace the original file{batch ? "s" : ""} with the edit? The date, camera and location stay the same.
        The file as it was is kept, so <b>Restore original</b> can bring it back.{batch ? " RAW and other formats that can't be rewritten get a JPEG copy instead." : ""}</p>
      <div className="flex gap-2">
        <button className="flex-1 rounded bg-neutral-800 py-1.5 hover:bg-neutral-700" onClick={() => setAsk(null)}>Back</button>
        <button className="flex-1 rounded bg-amber-700 py-1.5 font-medium hover:bg-amber-600" onClick={() => { setAsk(null); onSave("replace", false); }}>Replace</button>
      </div>
    </div>
  );
  return (
    <div className="space-y-1.5">
      <button className="w-full rounded bg-sky-600 py-1.5 font-medium hover:bg-sky-500 disabled:opacity-40" disabled={disabled}
        onClick={() => onSave("copy", false)}>{saving ? "Saving…" : "Save as a copy (next to the original)"}</button>
      <div className="flex gap-2">
        <button className="flex-1 rounded bg-neutral-800 py-1.5 text-xs hover:bg-neutral-700 disabled:opacity-40" disabled={disabled}
          onClick={() => onSave("copy", true)}>Copy to a folder…</button>
        <button className="flex-1 rounded bg-neutral-800 py-1.5 text-xs text-amber-300 hover:bg-neutral-700 disabled:opacity-40"
          disabled={disabled || !canReplace} title={canReplace ? "" : "This file type can't be rewritten; save a copy"}
          onClick={() => setAsk("replace")}>Replace original…</button>
      </div>
    </div>
  );
}

function CropBox({ url, crop, aspect, onChange }: {
  url: string; crop: EditOps["crop"]; aspect: number | null; onChange: (c: NonNullable<EditOps["crop"]>) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const c = crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const drag = (e: React.PointerEvent, kind: "move" | "nw" | "ne" | "sw" | "se") => {
    e.preventDefault(); e.stopPropagation();
    const r = box.current!.getBoundingClientRect();
    const start = { px: e.clientX, py: e.clientY, ...c };
    const ratio = aspect ? aspect * (r.height / r.width) : null;   // aspect in box fractions (w/h)
    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - start.px) / r.width, dy = (ev.clientY - start.py) / r.height;
      let { x, y, w, h } = start;
      if (kind === "move") {
        x = clamp(x + dx, 0, 1 - w); y = clamp(y + dy, 0, 1 - h);
      } else {
        const left = kind === "nw" || kind === "sw", top = kind === "nw" || kind === "ne";
        let x2 = x + w, y2 = y + h;
        if (left) x = clamp(x + dx, 0, x2 - 0.03); else x2 = clamp(x2 + dx, x + 0.03, 1);
        if (top) y = clamp(y + dy, 0, y2 - 0.03); else y2 = clamp(y2 + dy, y + 0.03, 1);
        w = x2 - x; h = y2 - y;
        if (ratio) {
          h = Math.min(w / ratio, top ? y2 : 1 - y);
          w = h * ratio;
          if (left) x = x2 - w;
          if (top) y = y2 - h;
        }
      }
      onChange({ x, y, w, h });
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const pct = (v: number) => `${v * 100}%`;
  return (
    <div ref={box} className="relative inline-block select-none">
      <img src={url} className="block max-h-[calc(100vh-2rem)] max-w-full" draggable={false} />
      <div className="absolute inset-0 bg-black/55" style={{ clipPath: `polygon(0 0,100% 0,100% 100%,0 100%,0 0,${pct(c.x)} ${pct(c.y)},${pct(c.x)} ${pct(c.y + c.h)},${pct(c.x + c.w)} ${pct(c.y + c.h)},${pct(c.x + c.w)} ${pct(c.y)},${pct(c.x)} ${pct(c.y)})` }} />
      <div className="absolute cursor-move border-2 border-white" onPointerDown={(e) => drag(e, "move")}
        style={{ left: pct(c.x), top: pct(c.y), width: pct(c.w), height: pct(c.h) }}>
        {(["nw", "ne", "sw", "se"] as const).map((k) => (
          <span key={k} onPointerDown={(e) => drag(e, k)}
            className={`absolute h-4 w-4 rounded-sm bg-white ${k[0] === "n" ? "-top-2" : "-bottom-2"} ${k[1] === "w" ? "-left-2" : "-right-2"}`}
            style={{ cursor: k === "nw" || k === "se" ? "nwse-resize" : "nesw-resize" }} />
        ))}
      </div>
    </div>
  );
}

let lastRatio = 1;
/** Width / height of the shown picture (remembered from the last loaded preview). */
function imgRatio(url: string | null): number {
  const img = url ? Array.from(document.images).find((i) => i.src === url) : undefined;
  if (img?.naturalWidth) lastRatio = img.naturalWidth / img.naturalHeight;
  return lastRatio;
}

function fitAspect(c: NonNullable<EditOps["crop"]>, aspect: number, picture: number) {
  const r = aspect / picture;              // wanted w/h in fractions of the picture
  let w = c.w, h = w / r;
  if (h > 1) { h = 1; w = h * r; }
  if (w > 1) { w = 1; h = w / r; }
  const cx = c.x + c.w / 2, cy = c.y + c.h / 2;
  return { x: clamp(cx - w / 2, 0, 1 - w), y: clamp(cy - h / 2, 0, 1 - h), w, h };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="space-y-2"><div className="text-[11px] uppercase tracking-wide text-neutral-500">{title}</div>{children}</div>;
}
function Btn({ on, onClick, children }: { on?: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className={`rounded px-2 py-1 text-xs ${on ? "bg-sky-700" : "bg-neutral-800 hover:bg-neutral-700"}`}>{children}</button>;
}
function Slider({ label, value, onChange, min = -100, max = 100, step = 1, unit = "" }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; unit?: string;
}) {
  return (
    <label className="block">
      <div className="flex justify-between text-xs"><span>{label}</span>
        <button className="text-neutral-400 hover:text-white" onClick={(e) => { e.preventDefault(); onChange(min < 0 ? 0 : min); }}
          title="Reset">{value > 0 && min < 0 ? "+" : ""}{value}{unit}</button></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} className="w-full" />
    </label>
  );
}
