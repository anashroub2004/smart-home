"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { DeviceTile } from "@/components/DeviceTile";
import { occupancy } from "@/components/RoomBlock";
import { ScreenHeader } from "@/components/ScreenHeader";
import { useConfig, useHomeState, useNodes, useSummaries } from "@/lib/home";
import { dayKey, timeAgo } from "@/lib/format";
import { C, deviceLabel, hasClimate, minutesText } from "@/lib/view";
import type { Summary } from "@/lib/contract";

// Static export can't pre-render /room/[id] for rooms added later, so the room id is a query param: /room?id=living
export default function RoomPage() {
  return (
    <Suspense fallback={<div className="skeleton" />}>
      <Room />
    </Suspense>
  );
}

/** Last 24 h of temperature → SVG path in a 300×80 box (same scale idea as the prototype). */
function tempPath(today: Record<string, Summary> | null, yesterday: Record<string, Summary> | null) {
  const now = Date.now();
  const pts: [number, number][] = [];
  const add = (src: Record<string, Summary> | null, date: Date) => {
    for (const [hhmm, s] of Object.entries(src ?? {})) {
      if (s.temp === undefined) continue;
      const [h, m] = hhmm.split(":").map(Number);
      const t = new Date(date.getFullYear(), date.getMonth(), date.getDate(), h, m).getTime();
      if (now - t <= 86_400_000 && t <= now) pts.push([t, s.temp]);
    }
  };
  add(yesterday, new Date(now - 86_400_000));
  add(today, new Date(now));
  if (pts.length < 2) return null;
  pts.sort((a, b) => a[0] - b[0]);
  const temps = pts.map((p) => p[1]);
  const lo = Math.min(...temps) - 0.5;
  const hi = Math.max(...temps) + 0.5;
  return pts
    .map(([t, v], i) => {
      const x = (300 * (t - (now - 86_400_000))) / 86_400_000;
      const y = 75 - ((v - lo) / (hi - lo)) * 70;
      return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join("");
}

function Room() {
  const id = useSearchParams().get("id") ?? "";
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { data: sumToday } = useSummaries(id || null, dayKey());
  const { data: sumYesterday } = useSummaries(id || null, dayKey(new Date(Date.now() - 86_400_000)));
  const path = useMemo(() => tempPath(sumToday, sumYesterday), [sumToday, sumYesterday]);

  const room = config?.rooms?.[id];
  if (!config) return <div className="skeleton" />;
  if (!room) return <ScreenHeader title="Room not found" />;

  const node = nodes?.[room.node];
  const offline = node?.online === false;
  const rs = state?.rooms?.[id];
  const occ = occupancy(rs, offline);
  const mmwave = (room.hardware ?? []).includes("C1001 mmWave");
  const since = rs?.occ_since ? minutesText(Date.now() - rs.occ_since) : null;
  const occSource = offline
    ? `The room node stopped reporting ${timeAgo(node?.last_seen)}.`
    : mmwave
      ? rs?.occ
        ? "Detected by mmWave (C1001) · works even when you sit still"
        : `No movement or breathing detected${since ? ` for ${since}` : ""}`
      : rs?.occ
        ? "Detected by motion sensor (PIR)"
        : `No motion${since ? ` for ${since}` : ""} (PIR)`;
  const devices = Object.entries(room.devices ?? {}).filter(([, c]) => !c.caps?.lock);
  const ticks = [24, 18, 12, 6].map((h) => new Date(Date.now() - h * 3_600_000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));

  return (
    <>
      <ScreenHeader title={room.name} />

      <div className="card" style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span className="dot" style={{ width: 10, height: 10, background: occ.color }} />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700 }}>{occ.text}</div>
          <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{occSource}</div>
        </div>
      </div>

      {hasClimate(room) && (
        <div className="big-metrics">
          <div className="bm"><div className="bm-v num">{rs?.temp !== undefined ? `${rs.temp.toFixed(1)}°` : "—"}</div><div className="bm-l">Temperature</div></div>
          <div className="bm"><div className="bm-v num">{rs?.hum !== undefined ? `${Math.round(rs.hum)}%` : "—"}</div><div className="bm-l">Humidity</div></div>
          <div className="bm"><div className="bm-v num">{rs?.lux !== undefined ? `${Math.round(rs.lux)} lx` : "—"}</div><div className="bm-l">Light level</div></div>
        </div>
      )}

      {devices.length > 0 && (
        <div className="tiles">
          {devices.map(([dev, cfg]) => (
            <DeviceTile key={dev} id={dev} cfg={cfg} label={deviceLabel(cfg, room)} state={state?.devices?.[dev]} offline={offline} />
          ))}
        </div>
      )}

      {hasClimate(room) && (
        <section className="card">
          <h2 className="h-label">Temperature · last 24 h</h2>
          {path ? (
            <svg viewBox="0 0 300 80" preserveAspectRatio="none" style={{ width: "100%", height: 90, display: "block" }} role="img" aria-label="Temperature over the last 24 hours">
              <path d={path} fill="none" stroke="#ECEEF1" strokeWidth={2} vectorEffect="non-scaling-stroke" />
            </svg>
          ) : (
            <div className="empty" style={{ height: 90 }}>Collecting data — the line appears after a few minutes.</div>
          )}
          <div className="muted num" style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, marginTop: 6 }}>
            {ticks.map((t) => <span key={t}>{t}</span>)}
            <span>Now</span>
          </div>
        </section>
      )}

      <section className="card">
        <h2 className="h-label">Sensors in this room</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {(room.hardware ?? []).map((s) => <span key={s} className="chip">{s}</span>)}
        </div>
        <div className="muted" style={{ fontSize: 13, marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
          <span className="dot" style={{ background: offline ? C.warn : C.good }} />
          {offline ? `Node "${room.node}" · offline` : `Node "${room.node}" · online · updated ${timeAgo(node?.last_seen)}`}
        </div>
      </section>
    </>
  );
}
