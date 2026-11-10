// A helpful empty page: an icon, what is missing, how to get it, and one button.
import Icon, { IconName } from "../lib/icons";

export default function Empty({ icon, title, hint, action, compact }: {
  icon: IconName; title: string; hint?: string; compact?: boolean;
  action?: { label: string; fn: () => void };
}) {
  // "No favourites yet: open a photo and press ♥." → title and hint
  if (!hint && title.includes(": ")) {
    const [t, ...rest] = title.split(": ");
    const h = rest.join(": ");
    title = t;
    hint = h.charAt(0).toUpperCase() + h.slice(1);
  }
  return (
    <div className={`pe-empty flex flex-col items-center justify-center gap-2 text-center ${compact ? "py-8" : "h-full min-h-[16rem] p-8"}`}>
      <span className="grid h-14 w-14 place-items-center rounded-full bg-neutral-900 text-sky-400 ring-1 ring-neutral-800">
        <Icon name={icon} className="h-6 w-6" />
      </span>
      <div className="text-base font-medium text-neutral-200">{title}</div>
      {hint && <div className="max-w-sm text-sm text-neutral-500">{hint}</div>}
      {action && (
        <button className="mt-2 rounded-xl bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-500" onClick={action.fn}>
          {action.label}</button>
      )}
    </div>
  );
}
