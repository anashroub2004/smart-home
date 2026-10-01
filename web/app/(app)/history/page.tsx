"use client";

import { useMemo, useState } from "react";
import { EventRow, sourceGroup } from "@/components/EventRow";
import { PageHeader } from "@/components/PageHeader";
import { useEvents } from "@/lib/home";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "manual", label: "Manual" },
  { id: "ai", label: "AI" },
  { id: "rule", label: "Rules" },
  { id: "door", label: "Door" },
  { id: "failed", label: "Failed" },
] as const;

type Filter = (typeof FILTERS)[number]["id"];

export default function HistoryPage() {
  const [limit, setLimit] = useState(100);
  const [filter, setFilter] = useState<Filter>("all");
  const { list, loading } = useEvents(limit);

  const shown = useMemo(
    () =>
      list.filter((e) =>
        filter === "all" ? true : filter === "failed" ? e.result !== "ok" : sourceGroup(e) === filter,
      ),
    [list, filter],
  );

  // group by day
  const days = useMemo(() => {
    const m = new Map<string, typeof shown>();
    shown.forEach((e) => {
      const k = new Date(e.at).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
      m.set(k, [...(m.get(k) ?? []), e]);
    });
    return [...m.entries()];
  }, [shown]);

  return (
    <>
      <PageHeader title="History" sub="Every action in the home — who, what, why and the result. Tap a row for details." />

      <div className="mt-6 flex gap-2 overflow-x-auto pb-1">
        {FILTERS.map((f) => (
          <button key={f.id} className="chip" data-active={filter === f.id} onClick={() => setFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>

      {loading && <p className="mt-6 text-muted">Loading…</p>}
      {!loading && !shown.length && <p className="mt-6 text-sm text-muted">No events.</p>}

      {days.map(([day, events]) => (
        <section key={day} className="mt-6">
          <h2 className="mb-1 text-sm font-semibold text-muted">{day}</h2>
          <ul className="card px-4">{events.map((e) => <EventRow key={e.id} e={e} />)}</ul>
        </section>
      ))}

      {list.length >= limit && (
        <button onClick={() => setLimit(limit + 100)} className="chip mx-auto mt-6 flex">Load more</button>
      )}
    </>
  );
}
