// Photo grid for libraries of any size.
//
// 1. /api/timeline returns photo counts per month for the current filters.
//    From that alone the full layout is known (a header row per month, then
//    rows of tiles), so the scrollbar and the year scrubber are exact before
//    any photo is loaded.
// 2. Only the rows on screen are rendered (react-virtual), and a month's
//    photos are fetched when one of its rows first comes into view.
// 3. Gallery layout: once a month is loaded its photos are grouped by day and
//    laid out in justified rows (each photo keeps its shape); until then the
//    month has placeholder rows of about the right height.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { elementScroll, observeElementOffset, useVirtualizer } from "@tanstack/react-virtual";
import { Album, api, Bucket, filterQuery, Filters, Media, post, thumbUrl } from "../api";
import { fmtDuration, monthLabel, monthRange } from "../lib/format";
import CreateDialog, { CreateSource } from "./CreateDialog";
import OrganizeDialog from "./OrganizeDialog";
import Viewer from "./Viewer";
import EditDialog from "./EditDialog";
import PrintDialog from "./PrintDialog";
import { useAppearance } from "../lib/appearance";
import Icon, { IconName } from "../lib/icons";
import { markMedia } from "../api";
import TopBar from "../lib/topbar";
import CreateMenu from "./CreateMenu";

type Row =
  | { kind: "header"; s: number }
  | { kind: "row"; s: number; start: number }
  | { kind: "day"; s: number; label: string; ids: number[] }
  | { kind: "jrow"; s: number; start: number; widths: number[]; h: number }
  | { kind: "ph"; s: number };

const HEADER = 44;
const PAD = 8;
const DAY = 34;      // gallery: day heading
const GAP = 4;       // gallery: space between photos
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (iso: string | null) => {
  if (!iso) return "Date unknown";
  const d = new Date(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)));
  return `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`;
};
const aspectOf = (m: Media) => Math.min(3, Math.max(0.4, m.width && m.height ? m.width / m.height : 4 / 3));
// Browsers cap element height (~33.5M px in Chromium/WebView2, less elsewhere).
// Taller layouts get a shorter scroll area whose position is mapped onto the
// full layout; window.__PE_MAX_SCROLL_PX lowers the cap for testing.
const maxScrollPx = (): number => (window as unknown as { __PE_MAX_SCROLL_PX?: number }).__PE_MAX_SCROLL_PX ?? 12_000_000;

export default function PhotoGrid({ filters, sectionNote, emptyText = "No photos here yet.", onAlbumChanged, head, tail, above, noOrganize, onTotal, emptyAction }: {
  filters: Filters;
  head?: React.ReactNode;        // search box and filters: the left of the top bar beside Look
  tail?: React.ReactNode;        // extra actions just before Create ▾ (e.g. a person's ⋯ menu)
  noOrganize?: boolean;          // Organize doesn't apply here
  onTotal?: (n: number) => void; // the number of photos shown, when it changes
  emptyAction?: { label: string; fn: () => void };
  above?: React.ReactNode;       // shown above the photos; folds away once you scroll down (memories strip)
  sectionNote?: (month: string) => string;
  emptyText?: string;
  onAlbumChanged?: () => void;   // inside an album page: selection can be removed from it
}) {
  const key = filterQuery(filters);
  const [buckets, setBuckets] = useState<Bucket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const [viewH, setViewH] = useState(0);
  const scale = useRef(1);   // virtual px per real scroll px (1 = no compression)
  const [tile, setTile] = useState(170);
  const [open, setOpen] = useState<number | null>(null);
  const [play, setPlay] = useState(false);
  const [folded, setFolded] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);   // the strip above folds away while scrolled down          // the viewer opens straight into a slideshow
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [create, setCreate] = useState<CreateSource | null>(null);
  const [organize, setOrganize] = useState<CreateSource | null>(null);
  const [albumPick, setAlbumPick] = useState(false);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [editIds, setEditIds] = useState<number[] | null>(null);
  const [printIds, setPrintIds] = useState<number[] | null>(null);
  const stale = useRef(false);      // an edit in the viewer changed the library: reload once it closes
  const toggleSel = (id: number) =>
    setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  // The tick on a photo selects it straight away; while anything is ticked,
  // clicking a photo ticks it too, and unticking the last one ends selecting.
  const getItemRef = useRef<(g: number) => Media | undefined>(() => undefined);
  // Shift-click selects a range; pressing on a tick and dragging over other tiles ticks them too.
  const lastTick = useRef<number | null>(null);
  const dragTick = useRef<null | boolean>(null);   // true: dragging adds, false: removes
  const tickSel = (id: number, g: number, shift = false) => {
    if (shift && lastTick.current !== null) {
      const [a, b] = [Math.min(lastTick.current, g), Math.max(lastTick.current, g)];
      setSelected((s) => {
        const n = new Set(s);
        for (let k = a; k <= b; k++) { const m = getItemRef.current(k); if (m) n.add(m.id); }
        return n;
      });
    } else toggleSel(id);
    lastTick.current = g;
  };
  useEffect(() => {
    const up = () => { dragTick.current = null; };
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);
  const selecting = selected.size > 0;
  const sel = selecting;
  const clearSel = () => setSelected(new Set());
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (open !== null || create || organize || editIds || printIds || ["INPUT", "TEXTAREA", "SELECT"].includes(t?.tagName) || t?.isContentEditable) return;
      if (e.key === "Escape" && selected.size) clearSel();
      else if (e.key === " " && !(t?.tagName === "BUTTON") && total) { e.preventDefault(); setPlay(true); setOpen(0); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });
  const [version, setVersion] = useState(0);
  const gallery = useAppearance().layout === "gallery";
  const scrollRef = useRef<HTMLDivElement>(null);
  // Keyed by month ('2019-06'), not by position, so a reply that arrives
  // after the filters changed can never land in the wrong place.
  const cache = useRef(new Map<string, Media[]>());
  const pickedMedia = () => {
    const out: Media[] = [];
    cache.current.forEach((list) => list.forEach((m) => selected.has(m.id) && out.push(m)));
    return out;
  };
  const allFav = selecting && pickedMedia().every((m) => m.favorite);
  const mark = async (change: { favorite?: boolean; archived?: boolean }) => {
    try {
      const r = await markMedia([...selected], change);
      setNotice(change.archived !== undefined
        ? `${r.changed.toLocaleString()} ${change.archived ? "moved to Archive (find them under Library › Archive)" : "back in the library"}.`
        : `${r.changed.toLocaleString()} ${change.favorite ? "added to" : "removed from"} favourites.`);
      clearSel();
      if (change.archived !== undefined || filters.fav) setReload((x) => x + 1);
      else cache.current.forEach((list) => list.forEach((m) => { if (r.changed && change.favorite !== undefined) m.favorite = selected.has(m.id) ? (change.favorite ? 1 : 0) : m.favorite; }));
    } catch (e) { setNotice(String(e)); }
  };
  const loading = useRef(new Set<string>());
  const gen = useRef(0);

  // New query: forget loaded months, fetch the month counts.
  useEffect(() => {
    gen.current++;
    cache.current = new Map();
    loading.current = new Set();
    setBuckets(null); setError(null); setOpen(null); setSelected(new Set());
    scrollRef.current?.scrollTo({ top: 0 });
    let alive = true;
    api<Bucket[]>(`/api/timeline?${key}`)
      .then((b) => alive && setBuckets(b))
      .catch((e) => alive && setError(String(e)));
    return () => { alive = false; };
  }, [key, reload]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => { setWidth(el.clientWidth); setViewH(el.clientHeight); });
    ro.observe(el);
    setWidth(el.clientWidth);
    setViewH(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  const cols = Math.max(2, Math.floor((width - PAD * 2) / tile));
  const cell = width > 0 ? (width - PAD * 2) / cols : tile;

  const avail = Math.max(100, width - PAD * 2);
  const layout = useMemo(() => {
    const rows: Row[] = [];
    const sectionRow: number[] = [];   // row index of each month's header
    const sectionStart: number[] = []; // global photo index of each month's first photo
    let total = 0;
    (buckets ?? []).forEach((b, s) => {
      sectionRow.push(rows.length);
      sectionStart.push(total);
      rows.push({ kind: "header", s });
      const items = gallery ? cache.current.get(b.month) : undefined;
      if (!gallery) {
        for (let i = 0; i < b.count; i += cols) rows.push({ kind: "row", s, start: i });
      } else if (!items) {
        const n = Math.max(1, Math.ceil((b.count * 1.4 * tile) / avail));
        for (let i = 0; i < n; i++) rows.push({ kind: "ph", s });
      } else {
        let i = 0;
        while (i < items.length) {
          const day = items[i].taken?.slice(0, 10) ?? null;
          let j = i;
          while (j < items.length && (items[j].taken?.slice(0, 10) ?? null) === day) j++;
          rows.push({ kind: "day", s, label: dayLabel(day), ids: items.slice(i, j).map((m) => m.id) });
          let start = i, sum = 0;
          for (let k = i; k < j; k++) {
            sum += aspectOf(items[k]);
            const count = k - start + 1;
            if (sum * tile + (count - 1) * GAP >= avail || k === j - 1) {
              const full = sum * tile + (count - 1) * GAP >= avail;
              const h = full ? (avail - (count - 1) * GAP) / sum : tile;
              rows.push({ kind: "jrow", s, start, h,
                widths: items.slice(start, k + 1).map((m) => aspectOf(m) * h) });
              start = k + 1; sum = 0;
            }
          }
          i = j;
        }
      }
      total += b.count;
    });
    const sizes = rows.map((r) => r.kind === "header" ? HEADER : r.kind === "day" ? DAY
      : r.kind === "jrow" ? r.h + GAP : r.kind === "ph" ? tile + GAP : cell);
    const starts: number[] = [];
    sizes.reduce((acc, z, i) => { starts[i] = acc; return acc + z; }, 0);
    return { rows, sectionRow, sectionStart, total, sizes, starts };
    // version: a month's photos arrived (gallery rows depend on them)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buckets, cols, cell, gallery, tile, avail, gallery ? version : 0]);
  const { rows, sectionRow, sectionStart, total, sizes, starts } = layout;
  useEffect(() => { if (buckets) onTotal?.(total); }, [buckets, total]);   // eslint-disable-line react-hooks/exhaustive-deps

  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => sizes[i] ?? cell,
    overscan: 4,
    observeElementOffset: (inst, cb) => observeElementOffset(inst, (off, scrolling) => cb(off * scale.current, scrolling)),
    scrollToFn: (off, opts, inst) => elementScroll(off / scale.current, opts, inst),
  });
  useEffect(() => { virt.measure(); }, [cell, rows, virt]);

  const ensure = useCallback(async (s: number) => {
    if (!buckets || s < 0 || s >= buckets.length) return;
    const month = buckets[s].month;
    if (cache.current.has(month) || loading.current.has(month)) return;
    loading.current.add(month);
    const g = gen.current;
    let [from, to] = monthRange(month);
    if (filters.dateFrom && filters.dateFrom > from) from = filters.dateFrom;
    if (filters.dateTo && filters.dateTo < to) to = filters.dateTo;
    const items: Media[] = [];
    let cursor: string | undefined;
    try {
      do {
        const r = await api<{ items: Media[]; next: string | null }>(
          `/api/media?${filterQuery({ ...filters, dateFrom: from, dateTo: to }, { limit: 2000, cursor })}`);
        items.push(...r.items);
        cursor = r.next ?? undefined;
      } while (cursor && gen.current === g);
    } catch {
      loading.current.delete(month);
      return;
    }
    if (gen.current !== g) return;
    cache.current.set(month, items);
    loading.current.delete(month);
    setVersion((v) => v + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buckets, key]);

  const vitems = virt.getVirtualItems();
  const firstRow = vitems[0]?.index ?? 0;
  const lastRow = vitems[vitems.length - 1]?.index ?? 0;
  useEffect(() => {
    const need = new Set<number>();
    for (let i = firstRow; i <= lastRow && i < rows.length; i++) need.add(rows[i].s);
    need.forEach((s) => ensure(s));
  }, [firstRow, lastRow, rows, ensure]);

  // global index <-> (month, position)
  const locate = useCallback((g: number): [number, number] => {
    let lo = 0, hi = sectionStart.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (sectionStart[mid] <= g) lo = mid; else hi = mid - 1;
    }
    return [lo, g - sectionStart[lo]];
  }, [sectionStart]);
  const getItem = useCallback((g: number): Media | undefined => {
    if (g < 0 || g >= total) return undefined;
    const [s, i] = locate(g);
    const items = buckets && cache.current.get(buckets[s].month);
    if (!items) { ensure(s); return undefined; }
    return items[i];
  }, [locate, total, ensure, buckets]);
  getItemRef.current = getItem;

  // Year scrubber marks, from the exact layout.
  const totalSize = virt.getTotalSize();
  // Real scroll height, and the factor that maps its last position onto the layout's last position.
  const scrollH = Math.min(totalSize, maxScrollPx());
  scale.current = totalSize > scrollH && scrollH > viewH ? (totalSize - viewH) / (scrollH - viewH) : 1;
  const shift = (virt.scrollOffset ?? 0) * (1 - 1 / scale.current);   // layout px vs real px at this position
  const offsetOf = useCallback((s: number) => starts[sectionRow[s]] ?? 0, [sectionRow, starts]);
  const years = useMemo(() => {
    const out: { label: string; s: number; frac: number }[] = [];
    (buckets ?? []).forEach((b, s) => {
      const y = b.month.slice(0, 4);
      if (s === 0 || buckets![s - 1].month.slice(0, 4) !== y)
        out.push({ label: y, s, frac: totalSize ? offsetOf(s) / totalSize : 0 });
    });
    return out;
  }, [buckets, offsetOf, totalSize]);

  const scrubTo = (clientY: number, el: HTMLElement) => {
    if (!buckets?.length) return;
    const r = el.getBoundingClientRect();
    const target = Math.min(Math.max((clientY - r.top) / r.height, 0), 1) * totalSize;
    let s = 0;
    while (s + 1 < buckets.length && offsetOf(s + 1) <= target) s++;
    // Scroll directly: the virtualizer's scrollToIndex clamps to the real
    // (possibly compressed) scroll height before our mapping applies.
    if (scrollRef.current) scrollRef.current.scrollTop = offsetOf(s) / scale.current;
  };

  const top = virt.scrollOffset ?? 0;
  const firstVisible = vitems.find((v) => v.end > top)?.index ?? firstRow;  // vitems include off-screen overscan
  const currentMonth = buckets?.[rows[firstVisible]?.s ?? 0]?.month;

  return (
    <div className="relative h-full flex flex-col min-h-0">
      <TopBar>
        {head}
        <span className="whitespace-nowrap text-xs text-neutral-500">{buckets ? total.toLocaleString() : "…"}
          {currentMonth && <span className="ml-1.5 text-neutral-400">· {monthLabel(currentMonth)}</span>}</span>
        <span className="flex-1" />
        <CreateMenu count={sel ? selected.size : total} selection={sel}
          onPlay={() => { setPlay(true); setOpen(0); }}
          onVideo={() => setCreate(sel ? { mediaIds: [...selected], count: selected.size, kind: "slideshow" } : { filters, count: total, kind: "slideshow" })}
          onCollage={() => setCreate(sel ? { mediaIds: [...selected], count: selected.size, kind: "collage" } : { filters, count: total, kind: "collage" })}
          onOrganize={noOrganize ? undefined : () => setOrganize(sel ? { mediaIds: [...selected], count: selected.size } : { filters, count: total })} />
        {tail}
        <div className="pe-toolbar flex items-center overflow-hidden rounded-xl border border-neutral-700 bg-neutral-900">
          <TB icon="minus" label="" title="Smaller tiles" onClick={() => setTile((t) => Math.max(90, t - 30))} />
          <TB icon="plus" label="" title="Bigger tiles" onClick={() => setTile((t) => Math.min(360, t + 30))} />
        </div>
      </TopBar>
      {notice && (
        <div className="absolute bottom-4 left-4 z-30 flex max-w-md items-center gap-2 rounded-xl bg-neutral-900 px-3 py-2 text-xs text-emerald-400 shadow-lg ring-1 ring-neutral-700">
          <span className="flex-1">{notice}</span><button onClick={() => setNotice(null)}><Icon name="x" className="h-3.5 w-3.5" /></button></div>
      )}
      {sel && (
        // Floating bar while photos are selected
        <div className="pe-selbar absolute bottom-5 left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-2xl border border-neutral-700 bg-neutral-900/95 p-1.5 text-sm shadow-2xl backdrop-blur">
          <button className="flex items-center gap-1.5 rounded-xl px-3 py-1.5 font-medium text-sky-300 hover:bg-neutral-800" title="Clear the selection (Esc)" onClick={clearSel}>
            <Icon name="x" />{selected.size.toLocaleString()} selected</button>
          <span className="mx-1 h-6 w-px bg-neutral-700" />
          <CreateMenu up count={selected.size} selection
            onPlay={() => { setPlay(true); setOpen(0); }}
            onVideo={() => setCreate({ mediaIds: [...selected], count: selected.size, kind: "slideshow" })}
            onCollage={() => setCreate({ mediaIds: [...selected], count: selected.size, kind: "collage" })}
            onOrganize={noOrganize ? undefined : () => setOrganize({ mediaIds: [...selected], count: selected.size })} />
          <span className="relative">
            <TB2 icon="album" label="Add to album" onClick={() => setAlbumPick((v) => !v)} />
            {albumPick && <div className="absolute bottom-full mb-2"><AlbumPicker ids={[...selected]} onDone={(msg) => { setAlbumPick(false); setNotice(msg); onAlbumChanged?.(); }} /></div>}
          </span>
          <TB2 icon="heart" label="Favourite" active={allFav} onClick={() => mark({ favorite: !allFav })} />
          <TB2 icon={filters.show === "archive" ? "unarchive" : "archive"} label={filters.show === "archive" ? "Unarchive" : "Archive"}
            onClick={() => mark({ archived: filters.show !== "archive" })} />
          <MoreMenu items={[
            { label: "Edit…", hint: "Rotate, auto fix, filter or resize", fn: () => setEditIds([...selected]) },
            { label: "Print…", hint: "Print, or a contact sheet (PDF)", fn: () => setPrintIds([...selected]) },
            ...(filters.albumId != null ? [{ label: "Remove from album", hint: "The photos themselves stay", fn: async () => {
              const r = await post<{ removed: number }>(`/api/albums/${filters.albumId}/remove`, { media_ids: [...selected] });
              setNotice(`Removed ${r.removed} from the album (the photos themselves are untouched).`);
              clearSel(); setReload((x) => x + 1); onAlbumChanged?.();
            } }] : []),
          ]} />
        </div>
      )}
      {above && (
        <div className={`shrink-0 overflow-hidden transition-all duration-300 ${folded ? "max-h-0 opacity-0" : "max-h-72 opacity-100"}`}>{above}</div>
      )}
      <div className="relative flex-1 min-h-0">
        <div ref={scrollRef} className="absolute inset-y-0 left-0 right-10 overflow-y-auto"
          onScroll={above ? (e) => setFolded((e.currentTarget.scrollTop) > 40) : undefined}>
          {error && <p className="p-4 text-red-400 text-sm">{error}</p>}
          {buckets && total === 0 && <div className="p-6 text-neutral-500">{emptyText}{emptyAction && <button className="ml-3 rounded-lg bg-neutral-800 px-3 py-1 text-sm text-neutral-200 hover:bg-neutral-700" onClick={emptyAction.fn}>{emptyAction.label}</button>}</div>}
          <div style={{ height: scrollH, position: "relative" }}>
            {vitems.map((v) => {
              const row = rows[v.index];
              if (!row) return null;
              const b = buckets![row.s];
              const style = { position: "absolute" as const, top: 0, left: 0, width: "100%", height: v.size, transform: `translateY(${v.start - shift}px)` };
              if (row.kind === "header") {
                const note = sectionNote?.(b.month);
                return (
                  <div key={v.key} style={style} className="flex items-end gap-3 px-3 pb-1.5">
                    <span data-c={[...b.month].reduce((a, ch) => a + ch.charCodeAt(0), 0) % 8} className={`pe-month ${gallery ? "text-lg font-medium text-neutral-100" : "font-medium text-neutral-200"}`}>{monthLabel(b.month)}</span>
                    {note && <span className="text-xs text-sky-400">{note}</span>}
                    <span className="text-xs text-neutral-500">{b.count.toLocaleString()}</span>
                  </div>
                );
              }
              if (row.kind === "day") {
                const all = row.ids.length > 0 && row.ids.every((id) => selected.has(id));
                return (
                  <div key={v.key} style={style} className="flex items-end gap-2 pb-1.5" >
                    <button style={{ marginLeft: PAD }} title={all ? "Unselect this day" : "Select this day"}
                      className={`grid h-5 w-5 place-items-center rounded-full border-2 text-[11px] ${all
                        ? "border-sky-500 bg-sky-500" : "border-neutral-600 hover:border-neutral-400"}`}
                      onClick={() => {
                        setSelected((sel) => {
                          const n = new Set(sel);
                          row.ids.forEach((id) => (all ? n.delete(id) : n.add(id)));
                          return n;
                        });
                      }}>{all ? "✓" : ""}</button>
                    <span className="text-sm text-neutral-300">{row.label}</span>
                  </div>
                );
              }
              if (row.kind === "ph") {
                return <div key={v.key} style={{ ...style, paddingLeft: PAD, paddingRight: PAD, paddingBottom: GAP }}>
                  <div className="h-full w-full bg-neutral-900" /></div>;
              }
              const items = cache.current.get(b.month);
              if (row.kind === "jrow") {
                return (
                  <div key={v.key} style={{ ...style, paddingLeft: PAD, gap: GAP }} className="flex">
                    {row.widths.map((w, k) => {
                      const m = items?.[row.start + k];
                      const g = sectionStart[row.s] + row.start + k;
                      return (
                        <div key={k} style={{ width: w, height: row.h }} className="shrink-0">
                          {m ? <Tile m={m} selected={selected.has(m.id)} selecting={selecting} onTick={(shift) => tickSel(m.id, g, shift)}
                          onDragStart={() => { dragTick.current = !selected.has(m.id); }}
                          onDragOver={() => { if (dragTick.current !== null && selected.has(m.id) !== dragTick.current) toggleSel(m.id); }}
                            onOpen={(shift) => (selecting ? tickSel(m.id, g, shift) : setOpen(g))} />
                            : <div className="h-full w-full bg-neutral-900" />}
                        </div>
                      );
                    })}
                  </div>
                );
              }
              const n = Math.min(cols, b.count - row.start);
              return (
                <div key={v.key} style={{ ...style, paddingLeft: PAD }} className="flex">
                  {Array.from({ length: n }, (_, k) => {
                    const m = items?.[row.start + k];
                    const g = sectionStart[row.s] + row.start + k;
                    return (
                      <div key={k} style={{ width: cell, height: cell }} className="p-px">
                        {m ? <Tile m={m} selected={selected.has(m.id)} selecting={selecting} onTick={(shift) => tickSel(m.id, g, shift)}
                          onDragStart={() => { dragTick.current = !selected.has(m.id); }}
                          onDragOver={() => { if (dragTick.current !== null && selected.has(m.id) !== dragTick.current) toggleSel(m.id); }}
                          onOpen={(shift) => (selecting ? tickSel(m.id, g, shift) : setOpen(g))} />
                          : <div className="h-full w-full bg-neutral-900" />}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
        {years.length > 0 && (
          <div className="absolute right-0 top-0 bottom-0 w-10 select-none cursor-pointer border-l border-neutral-900"
            onMouseDown={(e) => { scrubTo(e.clientY, e.currentTarget); setScrubbing(true); }}
            onMouseUp={() => setScrubbing(false)} onMouseLeave={() => setScrubbing(false)}
            onMouseMove={(e) => e.buttons === 1 && scrubTo(e.clientY, e.currentTarget)}>
            {years.map((y, i) => {
              const prev = years[i - 1];
              const cur = currentMonth?.slice(0, 4) === y.label;
              if (prev && (y.frac - prev.frac) < 0.025 && !cur) return null;  // avoid overlapping labels
              return (
                <div key={y.label} className={`absolute right-1 rounded px-0.5 text-[10px] hover:text-white ${cur ? "bg-sky-950 font-semibold text-sky-300" : "text-neutral-500"}`}
                  style={{ top: `calc(${y.frac * 100}% )` }}>{y.label}</div>
              );
            })}
            {scrubbing && currentMonth && (
              <div className="pointer-events-none absolute right-11 top-2 whitespace-nowrap rounded-lg bg-sky-600 px-2 py-1 text-xs font-semibold text-white shadow-lg">
                {monthLabel(currentMonth)}</div>
            )}
          </div>
        )}
      </div>
      {open !== null && (
        <Viewer index={open} total={total} getItem={getItem} onIndex={setOpen} slideshow={play}
          onClose={() => { setOpen(null); setPlay(false); if (stale.current) { stale.current = false; setReload((x) => x + 1); } }}
          onChanged={() => { stale.current = true; }} />
      )}
      {editIds && <EditDialog mediaIds={editIds} onClose={() => setEditIds(null)}
        onSaved={(msg) => { setNotice(msg); setSelected(new Set()); setReload((x) => x + 1); }} />}
      {printIds && <PrintDialog mediaIds={printIds} onClose={() => setPrintIds(null)} />}
      {create && <CreateDialog source={create} onClose={() => setCreate(null)}
        onStarted={(msg) => { setNotice(msg); setSelected(new Set()); }} />}
      {organize && <OrganizeDialog source={organize} onClose={() => setOrganize(null)}
        onStarted={(msg) => { setNotice(msg); setSelected(new Set()); }} />}
    </div>
  );
}

function AlbumPicker({ ids, onDone }: { ids: number[]; onDone: (msg: string) => void }) {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Album[]>("/api/albums").then(setAlbums).catch(() => {}); }, []);
  const add = async (fn: () => Promise<string>) => {
    try { onDone(await fn()); } catch (e) { setErr(String(e)); }
  };
  return (
    <div className="absolute right-0 top-7 z-30 w-64 space-y-2 rounded-lg bg-neutral-800 p-2 text-sm shadow-xl">
      {albums.map((a) => (
        <button key={a.id} className="block w-full truncate rounded px-2 py-1 text-left hover:bg-neutral-700"
          onClick={() => add(async () => {
            const r = await post<{ added: number }>(`/api/albums/${a.id}/add`, { media_ids: ids });
            return `Added ${r.added} to “${a.name}”` + (r.added < ids.length ? ` (${ids.length - r.added} were already in it)` : "");
          })}>{a.name} <span className="text-xs text-neutral-500">{a.photos}</span></button>
      ))}
      <div className="flex gap-1">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New album name"
          className="min-w-0 flex-1 rounded bg-neutral-900 px-2 py-1 outline-none focus:ring-1 focus:ring-sky-500" />
        <button className="rounded bg-sky-700 px-2 disabled:opacity-40" disabled={!name.trim()}
          onClick={() => add(async () => {
            const r = await post<{ added: number }>("/api/albums", { name, media_ids: ids });
            return `Created “${name.trim()}” with ${r.added} photos`;
          })}>Add</button>
      </div>
      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}

function Tile({ m, onOpen, selected, selecting, onTick, onDragStart, onDragOver }: {
  m: Media; onOpen: (shift: boolean) => void; selected: boolean; selecting: boolean; onTick: (shift: boolean) => void;
  onDragStart: () => void; onDragOver: () => void;
}) {
  return (
    <button className="pe-tile group relative block h-full w-full overflow-hidden bg-neutral-900 focus:outline focus:outline-2 focus:outline-sky-500"
      onClick={(e) => onOpen(e.shiftKey)} onPointerEnter={(e) => e.buttons === 1 && onDragOver()} title={m.filename}>
      {m.has_thumb ? (
        <img src={thumbUrl(m.id, m.size)} loading="lazy" decoding="async" draggable={false} className="h-full w-full object-cover" />
      ) : (
        <span className="grid h-full place-items-center p-2 text-[11px] text-neutral-500 break-all">{m.filename}</span>
      )}
      {m.kind === "video" && (
        <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[10px]">▶ {fmtDuration(m.duration_ms)}</span>
      )}
      {m.kind === "raw" && <span className="absolute top-1 left-1 rounded bg-black/70 px-1 text-[9px]">RAW</span>}
      {!!m.favorite && <span className="absolute bottom-1 left-1 text-rose-400 drop-shadow"><Icon name="heart" filled className="h-4 w-4" /></span>}
      {!selecting && <span className="pointer-events-none absolute inset-x-0 top-0 h-10 bg-gradient-to-b from-black/40 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />}
      <span role="checkbox" aria-checked={selected} title={selected ? "Unselect" : "Select"}
        onClick={(e) => { e.stopPropagation(); onTick(e.shiftKey); }}
        onPointerDown={(e) => { if (!e.shiftKey) { e.stopPropagation(); onDragStart(); } }}
        className={`absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full border-2 text-[12px] transition-opacity ${selected
          ? "border-sky-400 bg-sky-500 text-white" : "border-white/90 bg-black/30 text-transparent hover:bg-black/50 hover:text-white/80"} ${selecting ? "" : "opacity-0 group-hover:opacity-100"}`}>
        ✓
      </span>
      {selected && <span className="absolute inset-0 bg-sky-500/25 ring-2 ring-inset ring-sky-400" />}
    </button>
  );
}

// One button of the grouped toolbar.
function TB({ icon, label, title, onClick, disabled, active }: {
  icon: IconName; label: string; title: string; onClick: () => void; disabled?: boolean; active?: boolean;
}) {
  return (
    <button title={title} disabled={disabled} onClick={onClick}
      className={`flex items-center gap-1.5 border-r border-neutral-700 px-3 py-1.5 last:border-r-0 hover:bg-neutral-800 disabled:opacity-40 ${active ? "text-rose-400" : "text-neutral-200"}`}>
      <Icon name={icon} filled={active && icon === "heart"} />{label && <span>{label}</span>}
    </button>
  );
}

// A button of the floating selection bar.
function TB2({ icon, label, onClick, active }: { icon: IconName; label: string; onClick: () => void; active?: boolean }) {
  return (
    <button onClick={onClick} className={`flex items-center gap-1.5 whitespace-nowrap rounded-xl px-3 py-1.5 hover:bg-neutral-800 ${active ? "text-rose-400" : "text-neutral-200"}`}>
      <Icon name={icon} filled={active && icon === "heart"} />{label}
    </button>
  );
}

export function MoreMenu({ items, up = true }: { items: { label: string; hint?: string; fn: () => void; danger?: boolean }[]; up?: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div ref={box} className="relative">
      <button className="rounded-xl px-3 py-1.5 text-neutral-200 hover:bg-neutral-800" title="More" onClick={() => setOpen((o) => !o)}>⋯</button>
      {open && (
        <div className={`absolute right-0 z-50 w-60 rounded-xl border border-neutral-700 bg-neutral-900 p-1.5 text-sm shadow-xl ${up ? "bottom-full mb-2" : "top-full mt-2"}`}>
          {items.map((it) => (
            <button key={it.label} onClick={() => { setOpen(false); it.fn(); }}
              className={`block w-full rounded-lg px-3 py-1.5 text-left hover:bg-neutral-800 ${it.danger ? "text-red-400" : ""}`}>
              {it.label}{it.hint && <span className="block text-xs text-neutral-500">{it.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
