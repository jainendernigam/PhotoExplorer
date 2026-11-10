// The main window fits above the Windows taskbar and remembers its place.
//
// Windows sizes a *maximized* window to the work area (the screen minus the
// taskbar, wherever the taskbar is and at any display scaling) and adjusts it
// when the taskbar moves or hides. So "fill the screen" means maximize(), never
// a size taken from monitor.size. A normal window is restored to its last
// size and position, shrunk to fit the current monitor's work area.
import { isTauri } from "../api";

const KEY = "pe.window";
type Saved = { maximized: boolean; x: number; y: number; w: number; h: number };   // physical pixels

export async function restoreWindow(): Promise<void> {
  if (!isTauri) return;
  try {
    const { getCurrentWindow, currentMonitor, PhysicalPosition, PhysicalSize } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    const mon = await currentMonitor();
    let saved: Saved | null = null;
    try { saved = JSON.parse(localStorage.getItem(KEY) || "null"); } catch { saved = null; }
    if (mon) {
      const wa = mon.workArea;    // excludes the taskbar
      if (saved && !saved.maximized) {
        const w = Math.min(saved.w, wa.size.width), h = Math.min(saved.h, wa.size.height);
        const x = Math.min(Math.max(saved.x, wa.position.x), wa.position.x + wa.size.width - w);
        const y = Math.min(Math.max(saved.y, wa.position.y), wa.position.y + wa.size.height - h);
        await win.setSize(new PhysicalSize(w, h));
        await win.setPosition(new PhysicalPosition(x, y));
      } else {
        const size = await win.outerSize();
        // first run on a small screen, or last time it was maximized
        if (saved?.maximized || size.width > wa.size.width || size.height > wa.size.height) await win.maximize();
      }
    }
    let t = 0;
    const save = () => {
      window.clearTimeout(t);
      t = window.setTimeout(async () => {
        try {
          const maximized = await win.isMaximized();
          const prev: Saved | null = (() => { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; } })();
          if (maximized || (await win.isMinimized())) {
            localStorage.setItem(KEY, JSON.stringify({ ...(prev ?? { x: 0, y: 0, w: 1400, h: 900 }), maximized }));
            return;
          }
          const [pos, size] = await Promise.all([win.outerPosition(), win.outerSize()]);
          localStorage.setItem(KEY, JSON.stringify({ maximized: false, x: pos.x, y: pos.y, w: size.width, h: size.height }));
        } catch { /* not saved; fine */ }
      }, 400);
    };
    await win.onResized(save);
    await win.onMoved(save);
  } catch { /* the window keeps its default size */ }
}
