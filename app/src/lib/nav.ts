// App-wide jumps between sections: "show these people in Library", "open this
// person". Views call these; the App shell listens and switches section.
import type { Filters } from "../api";

export type LibraryPreset = Pick<Filters, "persons" | "personsMode" | "dateFrom" | "dateTo" | "place" | "folderId" | "tag" | "pet" | "q"> & {
  label?: string;        // shown on a chip for a place, folder or category
};

const EVT_LIB = "pe-open-library";
const EVT_PERSON = "pe-open-person";

export function openLibrary(preset: LibraryPreset): void {
  window.dispatchEvent(new CustomEvent(EVT_LIB, { detail: preset }));
}
export function openPerson(id: number): void {
  window.dispatchEvent(new CustomEvent(EVT_PERSON, { detail: id }));
}
export function onOpenLibrary(fn: (p: LibraryPreset) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<LibraryPreset>).detail);
  window.addEventListener(EVT_LIB, h);
  return () => window.removeEventListener(EVT_LIB, h);
}
export function onOpenPerson(fn: (id: number) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<number>).detail);
  window.addEventListener(EVT_PERSON, h);
  return () => window.removeEventListener(EVT_PERSON, h);
}
