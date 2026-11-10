// App look: colour theme, layout and side pane, remembered on this PC.
import { useSyncExternalStore } from "react";

export type AppTheme = "dark" | "light" | "sand" | "sunrise" | "ocean" | "rainbow" | "aurora" | "confetti";
export type AppLayout = "classic" | "gallery";
export type Appearance = { theme: AppTheme; layout: AppLayout; sideClosed: boolean; intro: boolean; tune: boolean; strip: boolean };

export const THEMES: [AppTheme, string][] = [
  ["dark", "Dark"], ["light", "Light"], ["aurora", "Aurora (multicolour, dark)"],
  ["confetti", "Confetti (multicolour, light)"], ["sunrise", "Sunrise"], ["ocean", "Ocean"], ["rainbow", "Rainbow"],
  ["sand", "Warm sand"],
];
const COLORFUL: AppTheme[] = ["sunrise", "ocean", "rainbow", "aurora", "confetti"];
const MULTI: AppTheme[] = ["aurora", "confetti"];
const DARK: AppTheme[] = ["dark", "aurora"];
export const LAYOUTS: [AppLayout, string][] = [["classic", "Classic"], ["gallery", "Gallery"]];
// Colour dots shown on the Look button for each theme.
export const SWATCH: Record<AppTheme, string[]> = {
  dark: ["#171717", "#38bdf8"], light: ["#ffffff", "#2563eb"], sand: ["#f4ede1", "#b45f2a"],
  sunrise: ["#ff6b4a", "#ffd2c2"], ocean: ["#0ea5a5", "#c6eff0"], rainbow: ["#7c4dff", "#ff7a59"],
  aurora: ["#ff5f6d", "#ffd23f", "#1eb7ff", "#b45fff"], confetti: ["#ff5f6d", "#2ed573", "#1eb7ff", "#b45fff"],
};

const KEY = "pe.appearance";
let current: Appearance = load();
const listeners = new Set<() => void>();

function load(): Appearance {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      theme: THEMES.some(([t]) => t === v.theme) ? v.theme : "dark",
      layout: LAYOUTS.some(([l]) => l === v.layout) ? v.layout : "classic",
      sideClosed: v.sideClosed === true,
      intro: v.intro !== false,
      tune: v.tune !== false,
      strip: v.strip !== false,
    };
  } catch {
    return { theme: "dark", layout: "classic", sideClosed: false, intro: true, tune: true, strip: true };
  }
}

function apply(a: Appearance): void {
  const el = document.documentElement;
  el.dataset.theme = a.theme;
  el.dataset.tone = DARK.includes(a.theme) ? "dark" : "light";
  if (COLORFUL.includes(a.theme)) el.dataset.colorful = ""; else delete el.dataset.colorful;
  if (MULTI.includes(a.theme)) el.dataset.multi = ""; else delete el.dataset.multi;
}
apply(current);

export function setAppearance(change: Partial<Appearance>): void {
  current = { ...current, ...change };
  apply(current);
  try { localStorage.setItem(KEY, JSON.stringify(current)); } catch { /* not saved; fine */ }
  listeners.forEach((f) => f());
}

export function useAppearance(): Appearance {
  return useSyncExternalStore(
    (f) => { listeners.add(f); return () => listeners.delete(f); },
    () => current,
  );
}
