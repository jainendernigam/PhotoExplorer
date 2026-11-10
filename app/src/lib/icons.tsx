// Small line icons (24 x 24, drawn with the current text colour).
const P: Record<string, string> = {
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM20 20l-4.8-4.8",
  folders: "M3 7h6l2 2h10v9H3zM6 4h5l2 2",
  play: "M8 5l11 7-11 7z",
  sparkles: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
  minus: "M5 12h14",
  plus: "M12 5v14M5 12h14",
  heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
  archive: "M4 5h16v4H4zM5 9v10h14V9M10 13h4",
  unarchive: "M4 5h16v4H4zM5 9v10h14V9M12 17v-5m-2.5 2.5L12 12l2.5 2.5",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  printer: "M7 9V4h10v5M7 17H5v-6h14v6h-2M7 14h10v6H7z",
  album: "M5 4h14v16l-7-4-7 4z",
  x: "M6 6l12 12M18 6L6 18",
  map: "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14",
  shapes: "M4 4h7v7H4zM17.5 4a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7zM7.5 13l4 7h-8zM14 14h6v6h-6z",
  note: "M5 4h14v16H5zM8 9h8M8 13h8M8 17h5",
  pin: "M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
  paw: "M12 13c-3 0-5 3-5 5 0 1.5 1.5 2 2.5 2 1 0 1.5-.5 2.5-.5s1.5.5 2.5.5c1 0 2.5-.5 2.5-2 0-2-2-5-5-5zM6 9.5a1.6 2 0 1 0 0 .1M18 9.5a1.6 2 0 1 0 0 .1M9.5 6a1.6 2 0 1 0 0 .1M14.5 6a1.6 2 0 1 0 0 .1",
  chevronLeft: "M15 6l-6 6 6 6",
  chevronRight: "M9 6l6 6-6 6",
  pause: "M8 5h3v14H8zM13 5h3v14h-3z",
  film: "M4 4h16v16H4zM8 4v16M16 4v16M4 8h4M4 12h4M4 16h4M16 8h4M16 12h4M16 16h4",
  collage: "M4 4h9v7H4zM15 4h5v7h-5zM4 13h5v7H4zM11 13h9v7h-9z",
  eyeOff: "M3 3l18 18M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 9 6 9 6a15 15 0 0 1-2.7 3.3M6.6 6.6C4.4 8.1 3 12 3 12s4 6 9 6a8.7 8.7 0 0 0 4.4-1.2",
  download: "M12 4v11m0 0l-4-4m4 4l4-4M5 20h14",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
};

export type IconName = keyof typeof P;

export default function Icon({ name, className = "h-4 w-4", filled = false }: { name: IconName; className?: string; filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill={filled ? "currentColor" : "none"} stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name]} />
    </svg>
  );
}
