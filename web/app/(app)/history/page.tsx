"use client";

import { useMemo, useState } from "react";
import { EventItem } from "@/components/EventItem";
import { ScreenHeader } from "@/components/ScreenHeader";
import type { EventGroup } from "@/lib/contract";
import { useConfig, useEvents } from "@/lib/home";
import { isToday } from "@/lib/view";

const FILTERS: [EventGroup | "all", string][] = [
  ["all", "All"],
  ["manual", "By you"],
  ["ai", "AI"],
  ["rule", "Rules"],
  ["door", "Door"],
  ["system", "System"],
];

export default function HistoryPage() {
  const { data: config } = useConfig();
  const [limit, setLimit] = useState(300);
  const [filter, setFilter] = useState<EventGroup | "all">("all");
  const [openId, setOpenId] = useState<string | null>(null);
  const [earlier, setEarlier] = useState(false);
  const { list, loading } = useEvents(limit);

  const scope = useMemo(() => (earlier ? list : list.filter((e) => isToday(e.at))), [list, earlier]);
  const shown = scope.filter((e) => filter === "all" || e.group === filter || e.tags?.includes(filter));
  const count = (g: EventGroup) => list.filter((e) => isToday(e.at) && e.group === g).length;

  return (
    <>
      <ScreenHeader title="History" />
      <p className="muted" style={{ margin: "-10px 0 0", fontSize: 14 }}>
        Every action in your home{earlier ? "" : " today"}: what changed, who or what did it, and whether it worked. Tap an entry for details.
      </p>

      <div className="big-metrics">
        <div className="bm"><div className="bm-v num">{count("manual")}</div><div className="bm-l">By you</div></div>
        <div className="bm"><div className="bm-v num">{count("ai")}</div><div className="bm-l">By the AI</div></div>
        <div className="bm"><div className="bm-v num">{count("rule")}</div><div className="bm-l">By rules</div></div>
      </div>

      <div className="scenes" role="group" aria-label="Filter history">
        {FILTERS.map(([id, label]) => (
          <button key={id} type="button" className={filter === id ? "scene on" : "scene"} aria-pressed={filter === id} onClick={() => { setFilter(id); setOpenId(null); }}>
            {label}
          </button>
        ))}
      </div>

      <section className="card" style={{ padding: "4px 16px" }}>
        {shown.map((e) => (
          <EventItem key={e.id} e={e} config={config} open={openId === e.id} onToggle={() => setOpenId(openId === e.id ? null : e.id)} />
        ))}
        {!loading && !shown.length && (
          <div className="muted" style={{ padding: "20px 0", textAlign: "center" }}>Nothing in this filter{earlier ? "" : " today"}.</div>
        )}
      </section>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {!earlier && <button type="button" className="btn" onClick={() => setEarlier(true)}>Show earlier days</button>}
        {earlier && list.length >= limit && <button type="button" className="btn" onClick={() => setLimit(limit + 300)}>Load more</button>}
      </div>
      <div className="muted" style={{ fontSize: 12.5 }}>Kept on the hub for 90 days and in the cloud for 30 days.</div>
    </>
  );
}
