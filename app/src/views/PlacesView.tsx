// Places: an offline world map (Natural Earth, no internet) with the photos'
// locations, and a list of towns and cities with photos. Drag to move, wheel to
// zoom, click a bubble to zoom in or to see its photos.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Place, thumbUrl } from "../api";
import PhotoGrid from "../components/PhotoGrid";
import Icon from "../lib/icons";

type World = { countries: { n: string; r: number[][] }[]; states: number[][]; places: [string, number, number, number, string, string][] };
type View = { lon: number; lat: number; z: number };   // z: pixels per degree
type Pick = { title: string; place?: number; bbox?: string };

let worldCache: World | null = null;

export default function PlacesView({ refreshKey }: { refreshKey: number }) {
  const [world, setWorld] = useState<World | null>(worldCache);
  const [points, setPoints] = useState<number[][]>([]);
  const [placeList, setPlaceList] = useState<Place[]>([]);
  const [pick, setPick] = useState<Pick | null>(null);
  useEffect(() => {
    if (!worldCache) api<World>("/api/map/world").then((w) => { worldCache = w; setWorld(w); }).catch(() => {});
    api<number[][]>("/api/places/points").then(setPoints).catch(() => {});
    api<Place[]>("/api/places?limit=300").then(setPlaceList).catch(() => {});
  }, [refreshKey]);

  if (pick) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-3 border-b border-neutral-800 px-4 py-2">
          <button className="flex items-center gap-1 text-sm text-neutral-400 hover:text-white" onClick={() => setPick(null)}>
            <Icon name="chevronLeft" /> Places</button>
          <span className="flex items-center gap-1.5 font-semibold"><Icon name="pin" className="h-4 w-4 text-rose-400" />{pick.title}</span>
        </div>
        <div className="min-h-0 flex-1"><PhotoGrid filters={pick.place != null ? { place: pick.place } : { bbox: pick.bbox }} /></div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <header className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Places</h1>
        <span className="text-sm text-neutral-400">{points.length.toLocaleString()} photos with a location · {placeList.length} places</span>
      </header>
      <div className="min-h-0 flex-[3] overflow-hidden rounded-2xl border border-neutral-800">
        {world ? <WorldMap world={world} points={points} places={placeList} onPick={setPick} />
          : <div className="grid h-full place-items-center text-sm text-neutral-500">Loading the map…</div>}
      </div>
      <div className="min-h-0 flex-[2] overflow-y-auto">
        {placeList.length === 0 && <p className="text-sm text-neutral-500">No photos with a location yet. Phones usually save where a photo was taken.</p>}
        <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
          {placeList.map((p) => (
            <button key={p.id} onClick={() => setPick({ title: `${p.name}${p.country ? `, ${p.country}` : ""}`, place: p.id })}
              className="group overflow-hidden rounded-xl bg-neutral-900 text-left ring-1 ring-neutral-800 hover:ring-sky-500">
              <img src={thumbUrl(p.cover)} alt="" loading="lazy" className="aspect-[4/3] w-full object-cover transition-transform group-hover:scale-105" />
              <div className="px-2 py-1.5">
                <div className="truncate text-sm font-medium">{p.name}</div>
                <div className="truncate text-xs text-neutral-500">{[p.state, p.country].filter(Boolean).join(", ")} · {p.count.toLocaleString()}</div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ the map

function WorldMap({ world, points, places, onPick }: { world: World; points: number[][]; places: Place[]; onPick: (p: Pick) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 400 });
  const [view, setView] = useState<View | null>(null);
  const drag = useRef<{ x: number; y: number; v: View; moved: boolean } | null>(null);
  const bubbles = useRef<{ x: number; y: number; r: number; n: number; lat: [number, number]; lon: [number, number] }[]>([]);

  useEffect(() => {
    const el = box.current!;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Start zoomed to where the photos are.
  useEffect(() => {
    if (view || !size.w) return;
    if (!points.length) { setView({ lon: 20, lat: 20, z: size.w / 360 }); return; }
    const lats = points.map((p) => p[1]), lons = points.map((p) => p[2]);
    const [a, b, c, d] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
    const z = Math.min(size.w / Math.max(2, (d - c) * 1.3), size.h / Math.max(2, (b - a) * 1.3), 400);
    setView({ lon: (c + d) / 2, lat: (a + b) / 2, z: Math.max(size.w / 360, z) });
  }, [points, size, view]);

  const dark = document.documentElement.dataset.tone !== "light";
  const colors = useMemo(() => dark
    ? { sea: "#0d1424", land: "#1f2a40", border: "#3a4a6b", state: "#2c3a58", text: "#c9d3ea", dot: "#f43f5e" }
    : { sea: "#dbe9f6", land: "#f7f2e8", border: "#b9ad97", state: "#ddd2bf", text: "#3b2f22", dot: "#e11d48" }, [dark]);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !view) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const x = c.getContext("2d")!;
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    const px = (lon: number) => size.w / 2 + (lon - view.lon) * view.z;
    const py = (lat: number) => size.h / 2 - (lat - view.lat) * view.z;
    x.fillStyle = colors.sea; x.fillRect(0, 0, size.w, size.h);
    // countries
    x.fillStyle = colors.land; x.strokeStyle = colors.border; x.lineWidth = 0.8;
    for (const ct of world.countries) {
      x.beginPath();
      for (const ring of ct.r) {
        for (let i = 0; i < ring.length; i += 2) (i ? x.lineTo : x.moveTo).call(x, px(ring[i]), py(ring[i + 1]));
        x.closePath();
      }
      x.fill(); x.stroke();
    }
    if (view.z > 12) {   // state lines once zoomed in
      x.strokeStyle = colors.state; x.lineWidth = 0.6;
      for (const l of world.states) {
        x.beginPath();
        for (let i = 0; i < l.length; i += 2) (i ? x.lineTo : x.moveTo).call(x, px(l[i]), py(l[i + 1]));
        x.stroke();
      }
    }
    // photo bubbles: photos close together on screen join one bubble
    const cell = 44, grid = new Map<string, { n: number; sx: number; sy: number; la: [number, number]; lo: [number, number] }>();
    for (const [, lat, lon] of points) {
      const sx = px(lon), sy = py(lat);
      if (sx < -cell || sy < -cell || sx > size.w + cell || sy > size.h + cell) continue;
      const k = `${Math.floor(sx / cell)},${Math.floor(sy / cell)}`;
      const g = grid.get(k);
      if (g) { g.n++; g.sx += sx; g.sy += sy; g.la = [Math.min(g.la[0], lat), Math.max(g.la[1], lat)]; g.lo = [Math.min(g.lo[0], lon), Math.max(g.lo[1], lon)]; }
      else grid.set(k, { n: 1, sx, sy, la: [lat, lat], lo: [lon, lon] });
    }
    bubbles.current = [];
    x.textAlign = "center"; x.textBaseline = "middle";
    for (const g of grid.values()) {
      const bx = g.sx / g.n, by = g.sy / g.n, r = Math.min(26, 7 + Math.sqrt(g.n) * 1.6);
      x.fillStyle = colors.dot; x.globalAlpha = 0.9;
      x.beginPath(); x.arc(bx, by, r, 0, Math.PI * 2); x.fill();
      x.globalAlpha = 1; x.strokeStyle = "#fff"; x.lineWidth = 2; x.stroke();
      x.fillStyle = "#fff"; x.font = "600 11px 'Segoe UI', sans-serif";
      x.fillText(g.n > 999 ? `${Math.round(g.n / 100) / 10}k` : String(g.n), bx, by + 0.5);
      bubbles.current.push({ x: bx, y: by, r, n: g.n, lat: g.la, lon: g.lo });
    }
    // names of places with photos
    x.font = "500 12px 'Segoe UI', sans-serif"; x.fillStyle = colors.text;
    const shown = places.slice(0, view.z > 20 ? 60 : view.z > 6 ? 25 : 10);
    for (const p of shown) {
      const sx = px(p.lon), sy = py(p.lat);
      if (sx < 0 || sy < 0 || sx > size.w || sy > size.h) continue;
      x.fillText(p.name, sx, sy - 26);
    }
  }, [world, points, places, view, size, colors]);

  const toLonLat = (cx: number, cy: number, v: View) => ({ lon: v.lon + (cx - size.w / 2) / v.z, lat: v.lat - (cy - size.h / 2) / v.z });
  const zoomAt = (f: number, cx: number, cy: number) => setView((v) => {
    if (!v) return v;
    const z = Math.min(3000, Math.max(size.w / 400, v.z * f));
    const p = toLonLat(cx, cy, v);
    return { z, lon: p.lon - (cx - size.w / 2) / z, lat: p.lat + (cy - size.h / 2) / z };
  });

  return (
    <div ref={box} className="relative h-full w-full select-none">
      <canvas ref={canvas} style={{ width: size.w, height: size.h, cursor: drag.current ? "grabbing" : "grab" }}
        onWheel={(e) => { const r = e.currentTarget.getBoundingClientRect(); zoomAt(Math.exp(-e.deltaY * 0.0018), e.clientX - r.left, e.clientY - r.top); }}
        onPointerDown={(e) => { if (view) drag.current = { x: e.clientX, y: e.clientY, v: view, moved: false }; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const dx = e.clientX - d.x, dy = e.clientY - d.y;
          if (Math.hypot(dx, dy) > 3) d.moved = true;
          if (d.moved) setView({ ...d.v, lon: d.v.lon - dx / d.v.z, lat: d.v.lat + dy / d.v.z });
        }}
        onPointerUp={(e) => {
          const d = drag.current; drag.current = null;
          if (!d || d.moved || !view) return;
          const r = e.currentTarget.getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
          const b = bubbles.current.find((b) => Math.hypot(b.x - cx, b.y - cy) <= b.r + 2);
          if (!b) return;
          const spread = Math.max(b.lat[1] - b.lat[0], b.lon[1] - b.lon[0]) * view.z;
          if (b.n > 1 && spread > 8 && view.z < 2500) { zoomAt(3, b.x, b.y); return; }
          const pad = 0.0005;
          onPick({ title: `${b.n.toLocaleString()} photos here`, bbox: `${b.lat[0] - pad},${b.lon[0] - pad},${b.lat[1] + pad},${b.lon[1] + pad}` });
        }} />
      <div className="absolute right-3 top-3 flex flex-col overflow-hidden rounded-xl border border-neutral-700 bg-neutral-900 shadow">
        <button className="p-2 hover:bg-neutral-800" title="Zoom in" onClick={() => zoomAt(1.6, size.w / 2, size.h / 2)}><Icon name="plus" /></button>
        <button className="border-t border-neutral-700 p-2 hover:bg-neutral-800" title="Zoom out" onClick={() => zoomAt(1 / 1.6, size.w / 2, size.h / 2)}><Icon name="minus" /></button>
      </div>
      <div className="absolute bottom-1.5 left-2 text-[10px] text-neutral-500">Map: Natural Earth (offline)</div>
    </div>
  );
}
