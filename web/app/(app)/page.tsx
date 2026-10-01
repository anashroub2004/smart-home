"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { RoomBlock } from "@/components/RoomBlock";
import { IBell, IGear, IHistory, IHouse, ILeave, IMoon, ISparkle } from "@/components/Icons";
import { answerSuggestion, applyScene, useAiSchedule, useAlerts, useConfig, useConnected, useHomeState, useNodes, useSuggestions } from "@/lib/home";
import { useActivitySeen } from "@/lib/seen";
import { C, dateLine, greeting, sortedRooms } from "@/lib/view";

const SCENE_ICON: Record<string, typeof IHouse> = { home: IHouse, away: ILeave, sleep: IMoon };

export default function HomePage() {
  const { data: config, loading } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { data: schedule } = useAiSchedule();
  const { list: suggestions } = useSuggestions();
  const { list: alerts } = useAlerts(30);
  const connected = useConnected();
  const seen = useActivitySeen();
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);

  if (loading) return <div className="skeleton" />;
  if (!config)
    return (
      <div className="card">
        <div style={{ fontWeight: 700 }}>No configuration yet</div>
        <div className="muted" style={{ fontSize: 13.5, marginTop: 4 }}>Start the simulator: python pi/sim_house.py --fast</div>
      </div>
    );

  const rooms = sortedRooms(config);
  const offlineNodes = Object.entries(nodes ?? {}).filter(([, n]) => !n.online).map(([id]) => id);
  const lockout = (state?.door?.lockout_until ?? 0) > Date.now();
  const occCount = rooms.filter(([id, r]) => state?.rooms?.[id]?.occ && !offlineNodes.includes(r.node)).length;
  const power = state?.power_w ?? 0;

  let statusText = `All quiet · ${occCount} ${occCount === 1 ? "room" : "rooms"} occupied · ${power.toFixed(1)} W`;
  let statusDot = C.good;
  if (!connected) [statusText, statusDot] = ["Reconnecting to your home…", C.warn];
  else if (offlineNodes.length) [statusText, statusDot] = [`${config.nodes[offlineNodes[0]]?.name ?? offlineNodes[0]} is offline`, C.warn];
  else if (lockout) [statusText, statusDot] = ["Front door keypad is locked", C.warn];

  const unread = alerts.filter((a) => a.at > seen).length;
  const topSug = suggestions.find((s) => !s.response);
  const plan = Object.values(schedule ?? {})
    .filter((d) => (d.action === "schedule_on" || d.action === "keep_on") && d.title)
    .sort((a, b) => (a.time ?? "").localeCompare(b.time ?? ""));
  const scenes = Object.entries(config.scenes ?? {}).sort(([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0));

  return (
    <>
      <header style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontSize: 13, color: "#9BA1AA" }}>{dateLine(now)}</div>
          <h1 style={{ margin: "2px 0 0", fontSize: 30, fontWeight: 700, letterSpacing: "-0.02em" }}>{greeting(now)}</h1>
          <div className="status">
            <span className="dot" style={{ background: statusDot }} />
            <span>{statusText}</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Link href="/history" className="icon-btn" aria-label="History"><IHistory size={20} /></Link>
          <Link href="/activity" className="icon-btn" aria-label="Activity">
            <IBell size={20} />
            {unread > 0 && <span className="badge">{unread > 9 ? "9+" : unread}</span>}
          </Link>
          <Link href="/settings" className="icon-btn" aria-label="Settings"><IGear size={20} /></Link>
        </div>
      </header>

      {scenes.length > 0 && (
        <div className="scenes" role="group" aria-label="Scenes">
          {scenes.map(([id, s]) => {
            const Icon = SCENE_ICON[id] ?? IHouse;
            const on = state?.scene === id;
            return (
              <button key={id} type="button" className={on ? "scene on" : "scene"} aria-pressed={on} onClick={() => applyScene(id, s.set)}>
                <Icon size={18} sw={1.9} />
                {s.name}
              </button>
            );
          })}
        </div>
      )}

      {topSug && (
        <div className="card suggest">
          <div style={{ width: 36, height: 36, borderRadius: "50%", background: "#1E2A3F", color: "#8FB8FF", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
            <ISparkle size={18} sw={1.9} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>{topSug.title}</div>
            <div className="muted" style={{ fontSize: 13.5, marginTop: 3 }}>{topSug.why}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button type="button" className="btn btn-light" onClick={() => answerSuggestion(topSug.id, topSug, true)}>Yes</button>
              <button type="button" className="btn" onClick={() => answerSuggestion(topSug.id, topSug, false)}>Not now</button>
            </div>
          </div>
        </div>
      )}

      <section>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h2 className="h-label">Next hour</h2>
          <Link href="/insights" className="btn link-btn">All insights</Link>
        </div>
        <div className="timeline">
          {plan.map((p) => (
            <div key={p.device} className="tl-item">
              <div className="num" style={{ fontSize: 13, fontWeight: 700, color: "#8FB8FF" }}>{p.time}</div>
              <div style={{ fontSize: 14, fontWeight: 600, marginTop: 4 }}>{p.title}</div>
              <div className="muted num" style={{ fontSize: 12.5, marginTop: 2 }}>{Math.round(p.p_on * 100)}% sure</div>
            </div>
          ))}
          {!plan.length && (
            <div className="tl-item">
              <div style={{ fontSize: 14, fontWeight: 600 }}>Nothing planned</div>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>The AI only acts when it is at least 80% sure.</div>
            </div>
          )}
        </div>
      </section>

      <div className="rooms-grid">
        {rooms.map(([id, room]) => (
          <RoomBlock key={id} id={id} room={room} state={state?.rooms?.[id]} devices={state?.devices} offline={nodes?.[room.node]?.online === false} />
        ))}
      </div>
    </>
  );
}
