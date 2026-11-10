// Timeline: photos oldest-first, month by month, for a date range and/or one
// or more people; for one person with a birth date, by age instead.
// Example: "Daughter, age 0 to 8" = a growth timeline.
import { useEffect, useMemo, useState } from "react";
import { api, Filters, Person } from "../api";
import PhotoGrid from "../components/PhotoGrid";
import { Chip } from "./PeopleView";
import { addYears, ageAt } from "../lib/format";

export default function TimelineView() {
  const [people, setPeople] = useState<Person[]>([]);
  const [chosen, setChosen] = useState<number[]>([]);
  const [mode, setMode] = useState<"all" | "any">("all");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [ageFrom, setAgeFrom] = useState("");
  const [ageTo, setAgeTo] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [byAge, setByAge] = useState(true);

  useEffect(() => { api<Person[]>("/api/people").then(setPeople).catch(() => {}); }, []);

  const single = chosen.length === 1 ? people.find((p) => p.id === chosen[0]) : undefined;
  const birth = single?.birth_date ?? null;
  const useAge = !!birth && byAge;

  const filters: Filters = useMemo(() => {
    const f: Filters = { persons: chosen, personsMode: mode, order };
    if (useAge && birth && (ageFrom !== "" || ageTo !== "")) {
      if (ageFrom !== "") f.dateFrom = addYears(birth, Number(ageFrom));
      if (ageTo !== "") {
        // "age 0 to 8" includes the whole year of age 8: up to the day before the 9th birthday
        const end = new Date(addYears(birth, Number(ageTo) + 1) + "T00:00:00Z");
        end.setUTCDate(end.getUTCDate() - 1);
        f.dateTo = end.toISOString().slice(0, 10);
      }
    } else {
      if (dateFrom) f.dateFrom = dateFrom;
      if (dateTo) f.dateTo = dateTo;
    }
    return f;
  }, [chosen, mode, order, useAge, birth, ageFrom, ageTo, dateFrom, dateTo]);

  const toggle = (id: number) => setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-3 border-b border-neutral-800 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="mr-2 text-xl font-semibold">Timeline</h1>
          {people.length === 0 && <span className="text-sm text-neutral-500">Name people on the People page to make a timeline of one person.</span>}
          {people.map((p) => (
            <button key={p.id} onClick={() => toggle(p.id)}
              className={`flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-3 text-sm ${chosen.includes(p.id) ? "bg-sky-700" : "bg-neutral-800 hover:bg-neutral-700"}`}>
              <Chip faceId={p.cover_face_id} className="h-6 w-6 rounded-full" />{p.name}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          {chosen.length > 1 && (
            <select value={mode} onChange={(e) => setMode(e.target.value as "all" | "any")} className="rounded bg-neutral-900 px-2 py-1">
              <option value="all">Photos with all of them together</option>
              <option value="any">Photos with any of them</option>
            </select>
          )}
          {birth && (
            <select value={byAge ? "age" : "date"} onChange={(e) => setByAge(e.target.value === "age")} className="rounded bg-neutral-900 px-2 py-1">
              <option value="age">By age</option>
              <option value="date">By date</option>
            </select>
          )}
          {useAge ? (
            <label className="flex items-center gap-1 text-neutral-300">Age
              <input type="number" min={0} max={120} value={ageFrom} onChange={(e) => setAgeFrom(e.target.value)} placeholder="from"
                className="w-16 rounded bg-neutral-900 px-1 py-0.5" />
              to
              <input type="number" min={0} max={120} value={ageTo} onChange={(e) => setAgeTo(e.target.value)} placeholder="to"
                className="w-16 rounded bg-neutral-900 px-1 py-0.5" />
              years
            </label>
          ) : (
            <label className="flex items-center gap-1 text-neutral-300">From
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded bg-neutral-900 px-1 py-0.5" />
              to
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded bg-neutral-900 px-1 py-0.5" />
            </label>
          )}
          {single && !birth && <span className="text-xs text-neutral-500">Add {single.name}'s birth date on the People page to filter by age.</span>}
          <select value={order} onChange={(e) => setOrder(e.target.value as "asc" | "desc")} className="rounded bg-neutral-900 px-2 py-1">
            <option value="asc">Oldest first</option>
            <option value="desc">Newest first</option>
          </select>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {chosen.length === 0 && !dateFrom && !dateTo ? (
          <p className="p-6 text-neutral-500">Choose people above, or a date range ("From … to"), or both.</p>
        ) : (
          <PhotoGrid filters={filters} emptyText="No photos match."
            sectionNote={birth ? (m) => `${single!.name}: ${ageAt(birth, m)}` : undefined} />
        )}
      </div>
    </div>
  );
}
