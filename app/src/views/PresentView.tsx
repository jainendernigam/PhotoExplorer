// The presenter window (second screen): shows what the main window's viewer
// shows, on black, with a soft cross-fade. Esc or double-click closes it.
import { useEffect, useState } from "react";
import { listen, PresentMsg, send } from "../lib/present";
import { isTauri } from "../api";

type Shown = Extract<PresentMsg, { type: "show" }>;

async function closeSelf() {
  send({ type: "bye" });
  if (isTauri) {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    getCurrentWindow().close();
  } else window.close();
}

export default function PresentView() {
  const [shown, setShown] = useState<Shown | null>(null);

  useEffect(() => {
    const stop = listen((m) => {
      if (m.type === "show") setShown(m);
      else if (m.type === "close") closeSelf();
    });
    send({ type: "hello" });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeSelf();
    window.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", () => send({ type: "bye" }));
    document.title = "Photo Explorer – Presenting";
    return () => { stop(); window.removeEventListener("keydown", onKey); };
  }, []);

  const layer = (s: Shown, cls: string) => s.kind === "video"
    ? <video key={s.src} src={s.src} autoPlay controls={false} className={`absolute inset-0 m-auto max-h-full max-w-full ${cls}`} />
    : <img key={s.src} src={s.src} draggable={false} className={`absolute inset-0 m-auto max-h-full max-w-full object-contain ${cls}`} />;

  return (
    <div data-theme="dark" className="fixed inset-0 cursor-none select-none bg-black" onDoubleClick={closeSelf}>
      {!shown && <div className="grid h-full place-items-center text-neutral-500">Waiting for a photo… (Esc to close)</div>}
      {shown && layer(shown, "animate-[pefade_.5s_ease]")}
      {shown?.caption && <div className="absolute bottom-6 left-8 text-lg text-white/80 drop-shadow">{shown.caption}</div>}
      <style>{"@keyframes pefade{from{opacity:0}to{opacity:1}}"}</style>
    </div>
  );
}
