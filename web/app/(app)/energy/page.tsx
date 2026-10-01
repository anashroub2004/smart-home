"use client";

import { useMemo } from "react";
import { ICheck } from "@/components/Icons";
import { useConfig, useEnergyDaily, useEvents, useHomeState } from "@/lib/home";
import { clock, dayKey } from "@/lib/format";
import { allDevices, C, isToday } from "@/lib/view";

const total = (d?: Record<string, number>) => Object.values(d ?? {}).reduce((a, b) => a + b, 0);

export default function EnergyPage() {
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: daily } = useEnergyDaily();
  const { list: events } = useEvents(400);

  const today = daily?.[dayKey()];
  const todayWh = total(today);
  const yesterdayWh = total(daily?.[dayKey(new Date(Date.now() - 86_400_000))]);
  const now = new Date();
  const dayFrac = (now.getHours() * 60 + now.getMinutes()) / 1440;
  const yesterdaySoFar = yesterdayWh * dayFrac;
  const diffPct = yesterdaySoFar > 0 ? Math.round(((todayWh - yesterdaySoFar) / yesterdaySoFar) * 100) : null;

  const waste = events.filter((e) => e.saved_wh && isToday(e.at));
  const wasteWh = waste.reduce((a, e) => a + (e.saved_wh ?? 0), 0);

  const week = useMemo(() => {
    const days = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86_400_000);
      days.push({ label: i === 0 ? "Today" : d.toLocaleDateString("en-GB", { weekday: "short" }), wh: total(daily?.[dayKey(d)]), today: i === 0 });
    }
    const max = Math.max(1, ...days.map((d) => d.wh));
    return days.map((d) => ({ ...d, h: `${Math.max(4, (d.wh / max) * 100)}%` }));
  }, [daily]);

  const byDevice = useMemo(() => {
    const rows = [{ name: "Hub, nodes & sensors", wh: today?._base ?? 0 }];
    for (const d of allDevices(config)) if (d.cfg.watts) rows.push({ name: d.label, wh: today?.[d.id] ?? 0 });
    const max = Math.max(0.001, ...rows.map((r) => r.wh));
    return rows.sort((a, b) => b.wh - a.wh).map((r) => ({ ...r, pct: `${(r.wh / max) * 100}%` }));
  }, [config, today]);

  return (
    <>
      <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700 }}>Energy</h1>
      <section>
        <div className="num" style={{ fontSize: 52, fontWeight: 700, letterSpacing: "-0.03em", lineHeight: 1 }}>
          {Math.round(todayWh)}
          <span style={{ fontSize: 20, color: "#9BA1AA", fontWeight: 600 }}> Wh today</span>
        </div>
        <div style={{ marginTop: 8, fontSize: 14, color: "#9BA1AA" }}>
          {diffPct !== null && (
            <>
              <span style={{ color: diffPct <= 0 ? C.good : C.warn, fontWeight: 700 }}>
                {Math.abs(diffPct)}% {diffPct <= 0 ? "less" : "more"}
              </span>{" "}
              than yesterday at this time ·{" "}
            </>
          )}
          using <span className="num" style={{ color: "#F2F3F5", fontWeight: 700 }}>{(state?.power_w ?? 0).toFixed(1)} W</span> now
        </div>
      </section>

      <section className="card" style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div style={{ width: 40, height: 40, borderRadius: "50%", background: "#13261B", color: "#34C759", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
          <ICheck size={20} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            Waste stopped today · <span className="num">{wasteWh.toFixed(1)} Wh</span>
          </div>
          {waste.slice(0, 5).map((e, i) => (
            <div key={e.id} className="list-row" style={i === 0 ? { paddingTop: 10 } : undefined}>
              <span style={{ flex: 1 }}>{e.title.replace(" turned off", " auto-off")} · {e.short?.split(" · ")[0].toLowerCase()}</span>
              <span className="muted num">{clock(e.at)}</span>
            </div>
          ))}
          {!waste.length && <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>Nothing left running in an empty room so far today.</div>}
        </div>
      </section>

      <section className="card">
        <h2 className="h-label">This week</h2>
        <div className="wbars" role="img" aria-label="Daily energy for the last 7 days">
          {week.map((w) => (
            <div key={w.label} className="wbar">
              <span className="num muted" style={{ fontSize: 11 }}>{Math.round(w.wh)}</span>
              <span className={w.today ? "bar today" : "bar"} style={{ height: w.h }} />
              <span className="muted" style={{ fontSize: 12 }}>{w.label}</span>
            </div>
          ))}
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>Wh per day · today is still counting</div>
      </section>

      <section className="card">
        <h2 className="h-label">Where it went today</h2>
        {byDevice.map((b) => (
          <div key={b.name} className="list-row">
            <span style={{ flex: 1 }}>{b.name}</span>
            <div className="conf" style={{ maxWidth: 120 }}><span style={{ width: b.pct, background: "#ECEEF1" }} /></div>
            <span className="num" style={{ width: 64, textAlign: "right" }}>{b.wh.toFixed(1)} Wh</span>
          </div>
        ))}
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Measured per device by INA226 sensors. Base load is the hub, nodes and sensors.
        </div>
      </section>
    </>
  );
}
