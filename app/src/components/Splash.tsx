// The opening animation "Bloom unfold" (owner, 2026-11-06): the six petals of the
// Bloom logo open one by one with a slight turn, the centre pops, the name types
// in, then this library's own counts rise. It plays while the engine starts; at
// the end the logo flies to the side pane's logo. A click skips it. A short
// chime (made here in code, no sound file) plays along unless switched off in Look.
import { useEffect, useRef, useState } from "react";
import { api, Person, Stats } from "../api";
import logo from "../assets/logo.png";

const LENGTH = 3.6;                     // seconds of animation (then it waits for the engine)
const FLY = 0.65;                       // seconds for the logo to fly to the side pane
const W = 720, H = 450;                 // drawing size (scaled to the window)
const NAVY = "#141a2e";
const PETALS = ["#E24B4A", "#EF9F27", "#97C459", "#1D9E75", "#378ADD", "#7F77DD"];
const NAME = "Photo Explorer";
const CX = W / 2, CY = 160, R = 105;    // where the flower sits

type Count = { value: number; label: string; color: string };

const ease = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : 1 - Math.pow(1 - t, 3));
const back = (t: number) => {           // overshoots a little, then settles
  t = Math.max(0, Math.min(1, t));
  const c = 1.7;
  return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
};

export default function Splash({ ready, tune, onDone }: { ready: boolean; tune: boolean; onDone: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const start = useRef(performance.now());
  const counts = useRef<Count[] | null>(null);
  const [fly, setFly] = useState<null | { from: DOMRect; to: DOMRect | null }>(null);
  const [flying, setFlying] = useState(false);
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const done = useRef(false);
  const started = useRef(false);        // the flight has begun

  // The library's own figures, once the engine is up.
  useEffect(() => {
    if (!ready) return;
    Promise.all([api<Stats>("/api/stats"), api<Person[]>("/api/people")]).then(([st, people]) => {
      const n = (k: string[]) => st.breakdown.filter((b) => b.status === 1 && k.includes(b.kind)).reduce((a, b) => a + b.n, 0);
      counts.current = [
        { value: n(["image", "raw"]), label: "photos", color: PETALS[1] },
        { value: n(["video"]), label: "videos", color: PETALS[4] },
        { value: people.length, label: "people", color: PETALS[3] },
      ];
    }).catch(() => { counts.current = []; });
  }, [ready]);

  useEffect(() => {
    if (!tune) return;
    const t = setTimeout(() => chime(), 350);
    return () => clearTimeout(t);
  }, [tune]);

  // Start the flight: the big logo in the middle goes to the side pane's logo.
  const finish = () => {
    if (started.current || done.current) return;
    started.current = true;
    const c = canvas.current;
    if (!c) { done.current = true; onDone(); return; }
    const r = c.getBoundingClientRect(), k = r.width / W, s = 2 * R * 0.95 * k;
    const from = new DOMRect(r.left + CX * k - s / 2, r.top + CY * k - s / 2, s, s);
    const target = document.querySelector<HTMLElement>("[data-pe-logo]");
    const to = target && target.offsetParent ? target.getBoundingClientRect() : null;
    setFly({ from, to });
    requestAnimationFrame(() => requestAnimationFrame(() => setFlying(true)));
    setTimeout(() => { done.current = true; onDone(); }, FLY * 1000 + 60);
  };

  useEffect(() => {
    let raf = 0;
    const draw = () => {
      const c = canvas.current;
      if (!c) return;
      const t = (performance.now() - start.current) / 1000;
      const x = c.getContext("2d")!;
      const dpr = window.devicePixelRatio || 1;
      if (c.width !== W * dpr) { c.width = W * dpr; c.height = H * dpr; }
      x.setTransform(dpr, 0, 0, dpr, 0, 0);
      x.clearRect(0, 0, W, H);
      paint(x, t, counts.current);
      if (started.current) return;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    // A timer (not the frame loop, which stops while the window is minimised) ends it.
    const tick = setInterval(() => {
      const t = (performance.now() - start.current) / 1000;
      if (t >= LENGTH && readyRef.current && counts.current) finish();
      else if (t >= 20) finish();      // never keep anyone waiting behind it
    }, 100);
    return () => { cancelAnimationFrame(raf); clearInterval(tick); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const skip = () => (readyRef.current ? finish() : (done.current = true, onDone()));

  return (
    <div onClick={skip} title="Click to skip"
      className="fixed inset-0 z-[100] flex cursor-pointer items-center justify-center"
      style={{ background: NAVY, opacity: flying ? 0 : 1, transition: `opacity ${FLY}s ease-in` }}>
      <canvas ref={canvas} style={{ width: "min(92vw, 160vh)", aspectRatio: `${W} / ${H}`, maxWidth: 1100,
        opacity: fly ? 0 : 1, transition: "opacity 120ms" }} />
      {fly && (
        <img src={logo} alt="" draggable={false} className="fixed rounded-[22%]"
          style={{
            left: 0, top: 0, width: fly.from.width, height: fly.from.height, transformOrigin: "0 0",
            transform: flying && fly.to
              ? `translate(${fly.to.left}px, ${fly.to.top}px) scale(${fly.to.width / fly.from.width})`
              : `translate(${fly.from.left}px, ${fly.from.top}px)`,
            opacity: flying && !fly.to ? 0 : 1,
            transition: `transform ${FLY}s cubic-bezier(.6,.05,.3,1), opacity ${FLY}s`,
          }} />
      )}
    </div>
  );
}

// ------------------------------------------------------------ drawing

let logoImg: HTMLImageElement | null = null;

function paint(x: CanvasRenderingContext2D, t: number, counts: Count[] | null) {
  const spin = ease(t / 1.8) * 0.6;
  // the tile of the real logo fades in under the petals at the end
  const tile = ease((t - 2.9) / 0.6);
  if (tile > 0) {
    logoImg ??= Object.assign(new Image(), { src: logo });
    if (logoImg.complete) {
      const s = 2 * R * 0.95;
      x.globalAlpha = tile;
      x.drawImage(logoImg, CX - s / 2, CY - s / 2, s, s);
      x.globalAlpha = 1;
    }
  }
  for (let i = 0; i < 6; i++) {
    const p = (t - 0.15 - i * 0.16) / 0.5;
    if (p <= 0) continue;
    const s = back(p);
    x.save();
    x.translate(CX, CY);
    x.rotate((i * Math.PI) / 3 + spin);
    x.globalAlpha = 0.88 * Math.min(1, p * 2) * (1 - tile);
    x.fillStyle = PETALS[i];
    x.beginPath();
    x.ellipse(0, -R * 0.55 * s, R * 0.28 * s, R * 0.55 * s, 0, 0, Math.PI * 2);
    x.fill();
    x.restore();
  }
  const c = (t - 0.95) / 0.45;
  if (c > 0) {
    x.globalAlpha = 1 - tile;
    x.fillStyle = "#fff";
    x.beginPath();
    x.arc(CX, CY, R * 0.16 * back(c), 0, Math.PI * 2);
    x.fill();
    x.globalAlpha = 1;
  }
  x.globalAlpha = 1;

  // the name types in
  const nt = (t - 1.4) / 0.8;
  if (nt > 0) {
    const n = Math.floor(Math.min(1, nt) * NAME.length);
    x.fillStyle = "#fff";
    x.font = "600 38px 'Segoe UI', system-ui, sans-serif";
    x.textAlign = "center";
    x.fillText(NAME.slice(0, n) + (nt < 1 && Math.floor(t * 6) % 2 ? "|" : ""), CX, 330);
  }

  // this library's counts rise
  const ct = (t - 2.2) / 1.0;
  if (ct > 0 && counts && counts.length) {
    const shown = counts.filter((k) => k.value > 0 || k.label === "photos");
    const e = ease(ct);
    shown.forEach((k, i) => {
      const cx = CX + (i - (shown.length - 1) / 2) * 170;
      x.globalAlpha = Math.min(1, ct * 2);
      x.fillStyle = k.color;
      x.font = "600 30px 'Segoe UI', system-ui, sans-serif";
      x.textAlign = "center";
      x.fillText(Math.round(k.value * e).toLocaleString(), cx, 392);
      x.fillStyle = "#aab3cc";
      x.font = "16px 'Segoe UI', system-ui, sans-serif";
      x.fillText(k.label, cx, 416);
    });
    x.globalAlpha = 1;
  }
}

// ------------------------------------------------------------ sound

// A soft rising chime (C major arpeggio), synthesised so no sound file ships.
function chime() {
  try {
    const ac = new AudioContext();
    const out = ac.createGain();
    out.gain.value = 0.18;
    out.connect(ac.destination);
    [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((f, i) => {
      const at = ac.currentTime + i * 0.16;
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0, at);
      g.gain.linearRampToValueAtTime(i === 4 ? 0.9 : 0.6, at + 0.02);
      g.gain.exponentialRampToValueAtTime(0.001, at + (i === 4 ? 1.8 : 0.9));
      o.connect(g); g.connect(out);
      o.start(at); o.stop(at + 2);
    });
    setTimeout(() => ac.close().catch(() => {}), 3500);
  } catch { /* no sound; fine */ }
}
