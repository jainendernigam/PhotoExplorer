// Sections stay open in the background once visited (so coming back keeps the
// scroll position and filters). A hidden section must not draw in the top bar
// or answer keys: it reads this flag.
import { createContext, useContext } from "react";

export const ActiveContext = createContext(true);
export const useActive = (): boolean => useContext(ActiveContext);

// Back (mouse back button, Alt+←): an open photo or a selection closes first.
// Views listen and call preventDefault() when they handled it.
export const BACK_EVENT = "pe-back";
export function onBack(fn: (e: Event) => void): () => void {
  window.addEventListener(BACK_EVENT, fn);
  return () => window.removeEventListener(BACK_EVENT, fn);
}
