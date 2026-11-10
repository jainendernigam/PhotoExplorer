// "Present on another screen": a second window, full screen on another
// monitor when there is one, that shows whatever the viewer shows. The two
// windows talk over a BroadcastChannel (same app, same origin).
import { isTauri } from "../api";

export type PresentMsg =
  | { type: "show"; src: string; kind: "image" | "video"; caption?: string }
  | { type: "close" }
  | { type: "hello" }          // the presenter window is ready
  | { type: "bye" };           // the presenter window was closed

const CHANNEL = "pe-present";
let channel: BroadcastChannel | null = null;
const chan = () => (channel ??= new BroadcastChannel(CHANNEL));

export function send(msg: PresentMsg): void {
  chan().postMessage(msg);
}

export function listen(fn: (m: PresentMsg) => void): () => void {
  const c = new BroadcastChannel(CHANNEL);
  c.onmessage = (e) => fn(e.data as PresentMsg);
  return () => c.close();
}

let browserWin: Window | null = null;

/** Opens the presenter; returns a note about where it went. */
export async function openPresenter(): Promise<string> {
  const url = `${location.pathname}?present=1`;
  if (!isTauri) {
    browserWin = window.open(url, "pe-present", "popup,width=960,height=540");
    return "Opened in a new window. Drag it to the other screen.";
  }
  const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
  const { availableMonitors, currentMonitor } = await import("@tauri-apps/api/window");
  const existing = await WebviewWindow.getByLabel("present");
  if (existing) {
    await existing.setFocus();
    return "Already presenting.";
  }
  const [all, here] = await Promise.all([availableMonitors(), currentMonitor()]);
  const other = all.find((m) => !here || m.position.x !== here.position.x || m.position.y !== here.position.y);
  const target = other ?? here ?? all[0];
  const sf = target?.scaleFactor ?? 1;
  const w = new WebviewWindow("present", {
    url, title: "Photo Explorer – Presenting",
    x: target ? target.position.x / sf + 40 : undefined, y: target ? target.position.y / sf + 40 : undefined,
    width: 960, height: 540, decorations: !other, focus: false,
  });
  await new Promise<void>((res, rej) => { w.once("tauri://created", () => res()); w.once("tauri://error", (e) => rej(e)); });
  if (other) await w.setFullscreen(true);
  return other ? "Presenting full screen on the other display." : "Only one display found: opened in a window.";
}

export async function closePresenter(): Promise<void> {
  send({ type: "close" });
  if (!isTauri) { browserWin?.close(); browserWin = null; return; }
  const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
  (await WebviewWindow.getByLabel("present"))?.close();
}
