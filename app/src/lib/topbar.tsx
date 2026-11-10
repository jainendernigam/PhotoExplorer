// One top bar for every section, in the window's top row beside "Look".
// The App shell owns the row; a section puts its search, filters, count and
// actions there with <TopBar>…</TopBar> (a React portal), so photos start
// right below it.
import { ReactNode, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useActive } from "./active";

let slot: HTMLElement | null = null;
const subs = new Set<() => void>();

export function setTopBarSlot(el: HTMLElement | null): void {
  if (el === slot) return;
  slot = el;
  subs.forEach((f) => f());
}

function useSlot(): HTMLElement | null {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => slot);
}

export default function TopBar({ children }: { children: ReactNode }) {
  const el = useSlot();
  const active = useActive();
  return el && active ? createPortal(children, el) : null;
}
