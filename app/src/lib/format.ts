const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

export const fmtBytes = (n: number) => {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};

export const fmtEta = (s: number | null) =>
  s == null ? "" : s > 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m ${s % 60}s`;

/** '2019-06' -> 'June 2019' */
export const monthLabel = (ym: string) => `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;

/** '2019-06-12T10:20:30' -> '12 June 2019, 10:20' */
export const fmtTaken = (iso: string | null) => {
  if (!iso) return "";
  const [d, t] = iso.split("T");
  return `${Number(d.slice(8, 10))} ${MONTHS[Number(d.slice(5, 7)) - 1]} ${d.slice(0, 4)}, ${t.slice(0, 5)}`;
};

export const fmtDuration = (ms: number | null) => {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** First and last day of a 'YYYY-MM' month as YYYY-MM-DD. */
export const monthRange = (ym: string): [string, string] => {
  const y = Number(ym.slice(0, 4)), m = Number(ym.slice(5, 7));
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${ym}-01`, `${ym}-${String(last).padStart(2, "0")}`];
};

/** Age at the middle of month `ym` for someone born on `birth` (YYYY-MM-DD). */
export const ageAt = (birth: string, ym: string): string => {
  const months = (Number(ym.slice(0, 4)) - Number(birth.slice(0, 4))) * 12
    + (Number(ym.slice(5, 7)) - Number(birth.slice(5, 7)))
    - (Number(birth.slice(8, 10)) > 15 ? 1 : 0);
  if (months < 0) return "before birth";
  if (months < 1) return "newborn";
  const y = Math.floor(months / 12), mo = months % 12;
  if (y === 0) return `${mo} month${mo > 1 ? "s" : ""}`;
  return mo ? `${y} y ${mo} m` : `${y} year${y > 1 ? "s" : ""}`;
};

/** Birth date + N years as YYYY-MM-DD (for "age 0 to 8" filters). */
export const addYears = (birth: string, years: number) => {
  const md = birth.slice(4) === "-02-29" ? "-02-28" : birth.slice(4);  // leap-day birthdays
  return `${Number(birth.slice(0, 4)) + years}${md}`;
};
