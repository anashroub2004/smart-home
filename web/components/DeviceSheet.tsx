"use client";

// Device panel from the prototype: bottom sheet on phones, centred on desktop.
// Open it from anywhere with `useDeviceSheet().open(deviceId)`.

import Link from "next/link";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  setAiPause,
  useAiPause,
  useAiSchedule,
  useConfig,
  useDeviceCommand,
  useEnergyDaily,
  useEvents,
  useHomeState,
  useNodes,
  useSummaries,
} from "@/lib/home";
import { clock, dayKey } from "@/lib/format";
import { deviceWatts, findDevice, isToday, SPEEDS, speedName } from "@/lib/view";
import { SourceTag } from "./EventItem";
import { IClose } from "./Icons";
import { useToast } from "./Toast";

const Ctx = createContext<{ open: (id: string) => void }>({ open: () => {} });
export const useDeviceSheet = () => useContext(Ctx);

export function DeviceSheetProvider({ children }: { children: ReactNode }) {
  const [id, setId] = useState<string | null>(null);
  const value = useMemo(() => ({ open: (d: string) => setId(d) }), []);
  return (
    <Ctx.Provider value={value}>
      {children}
      {id && <DeviceSheet id={id} onClose={() => setId(null)} />}
    </Ctx.Provider>
  );
}

const LEARNING_MS = 21 * 86_400_000;

function hoursText(h: number) {
  const m = Math.round(h * 60);
  return m < 60 ? `${m} m` : `${Math.floor(m / 60)} h ${m % 60} m`;
}

function DeviceSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const toast = useToast();
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { data: daily } = useEnergyDaily();
  const { data: schedule } = useAiSchedule();
  const { data: pauseUntil } = useAiPause(id);
  const { list: events } = useEvents(300);
  const dev = findDevice(config, id);
  const cmd = useDeviceCommand(id, () => toast(`${dev?.label ?? "The device"} didn't respond. Nothing changed.`));

  const today = new Date();
  const yesterday = new Date(Date.now() - 86_400_000);
  const { data: sumToday } = useSummaries(dev?.roomId ?? null, dayKey(today));
  const { data: sumYesterday } = useSummaries(dev?.roomId ?? null, dayKey(yesterday));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 24 bars, one per hour, oldest first: "hot" if the device was on at any minute of that hour
  const bars = useMemo(() => {
    const out: { hot: boolean; label: string }[] = [];
    for (let i = 23; i >= 0; i--) {
      const t = new Date(Date.now() - i * 3_600_000);
      const src = t.getDate() === today.getDate() ? sumToday : sumYesterday;
      const hh = String(t.getHours()).padStart(2, "0");
      const hot = Object.entries(src ?? {}).some(([k, s]) => k.startsWith(hh + ":") && s.dev?.[id] === 1);
      out.push({ hot, label: `${hh}:00` });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sumToday, sumYesterday, id]);

  if (!config) return null;

  const content = (() => {
    if (!dev) return <p className="muted">This device is no longer in the configuration.</p>;
    const { cfg, room, node } = dev;
    const st = state?.devices?.[id];
    const offline = nodes?.[node]?.online === false;
    const pending = cmd.ui === "pending";
    const isFan = cfg.type === "fan";
    const w = deviceWatts(cfg, st);
    const todayWh = daily?.[dayKey()]?.[id] ?? 0;
    // minutes on today = summary minutes where this device was on (falls back to energy ÷ power)
    const minutesOn = Object.values(sumToday ?? {}).filter((x) => x.dev?.[id] === 1).length;
    const ratedW = isFan ? (cfg.watts * (st?.speed ?? 70)) / 100 : cfg.watts;
    const hoursOn = sumToday ? minutesOn / 60 : ratedW ? todayWh / ratedW : 0;
    const paused = !!pauseUntil && pauseUntil > Date.now();
    const learning = !!cfg.added_at && Date.now() - cfg.added_at < LEARNING_MS;
    const plan = schedule?.[id];
    const hasPlan = plan && (plan.action === "schedule_on" || plan.action === "keep_on") && plan.title;
    const pauseMin = config.thresholds.override_pause_min ?? 120;
    const recent = events.filter((e) => e.device === id && isToday(e.at)).slice(0, 3);

    const aiTitle = learning
      ? "AI is learning this device"
      : paused
        ? `AI paused until ${clock(pauseUntil!)}`
        : hasPlan
          ? `AI plan: ${plan!.title!.toLowerCase()} at ${plan!.time}`
          : "No AI plan in the next hour";
    const aiSub = learning
      ? "Predictions start after about 3 weeks of use"
      : paused
        ? "You took control, so the AI leaves this device alone."
        : hasPlan
          ? `${Math.round(plan!.p_on * 100)}% sure · pause to keep control`
          : "Pause the AI to keep full control";

    const stateText = offline ? "Not responding" : pending ? "Updating…" : st?.v ? "On" : "Off";

    return (
      <>
        <div className="grab" />
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div id="sheet-title" style={{ fontSize: 22, fontWeight: 700 }}>{cfg.name}</div>
            <div className="muted" style={{ fontSize: 14 }}>
              {room.name} · <span className="num">{stateText}</span>
            </div>
          </div>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <IClose size={18} />
          </button>
        </div>

        <button
          type="button"
          className={st?.v ? "btn btn-block" : "btn btn-light btn-block"}
          disabled={offline || pending}
          onClick={() => cmd.send(st?.v ? 0 : 1, isFan && !st?.v ? st?.speed || 70 : undefined)}
        >
          {pending ? "Updating…" : st?.v ? "Turn off" : "Turn on"}
        </button>

        {isFan && (
          <div>
            <div className="h-label">Speed</div>
            <div className="seg" role="group" aria-label="Fan speed">
              {(Object.keys(SPEEDS) as (keyof typeof SPEEDS)[]).map((name) => (
                <button
                  key={name}
                  type="button"
                  className={st?.v && speedName(st.speed) === name ? "on" : ""}
                  disabled={offline || pending}
                  onClick={() => cmd.send(1, SPEEDS[name])}
                >
                  {name}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="big-metrics">
          <div className="bm"><div className="bm-v num" style={{ fontSize: 22 }}>{st?.v ? `${w.toFixed(1)} W` : "0 W"}</div><div className="bm-l">Using now</div></div>
          <div className="bm"><div className="bm-v num" style={{ fontSize: 22 }}>{todayWh.toFixed(1)} Wh</div><div className="bm-l">Today</div></div>
          <div className="bm"><div className="bm-v num" style={{ fontSize: 22 }}>{hoursText(hoursOn)}</div><div className="bm-l">On today</div></div>
        </div>

        <div>
          <div className="h-label">Last 24 hours</div>
          <div className="bars" role="img" aria-label="Hours the device was on in the last 24 hours">
            {bars.map((b, i) => (
              <span key={i} className={b.hot ? "hot" : ""} style={{ height: b.hot ? "100%" : "6%" }} title={b.label} />
            ))}
          </div>
          <div className="muted num" style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, marginTop: 6 }}>
            <span>{bars[0]?.label}</span>
            <span>{bars[12]?.label}</span>
            <span>Now</span>
          </div>
        </div>

        <div className="card" style={{ background: "#1C1F24", display: "flex", gap: 12, alignItems: "center" }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: 14.5 }}>{aiTitle}</div>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{aiSub}</div>
          </div>
          <button
            type="button"
            className={paused ? "sw on" : "sw"}
            aria-pressed={paused}
            aria-label={`Pause AI for this device for ${pauseMin / 60} hours`}
            onClick={() => setAiPause(id, paused ? null : pauseMin)}
          >
            <span className="k" />
          </button>
        </div>

        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div className="h-label" style={{ margin: 0 }}>Recent activity</div>
            <Link href="/history" className="btn link-btn" onClick={onClose}>All</Link>
          </div>
          {recent.map((e) => (
            <div key={e.id} className="list-row">
              <SourceTag e={e} />
              <span style={{ flex: 1, minWidth: 0 }}>{e.short ?? e.title}</span>
              <span className="muted num" style={{ fontSize: 13 }}>{clock(e.at)}</span>
            </div>
          ))}
          {!recent.length && <div className="muted" style={{ fontSize: 13 }}>No activity yet today.</div>}
        </div>

        <div className="muted" style={{ fontSize: 12 }}>
          {cfg.ina ? `Power measured by INA226 at ${cfg.ina}` : "No power sensor · usage estimated from the device rating"} ·{" "}
          {node} node, GPIO {cfg.pin}
        </div>
      </>
    );
  })();

  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title" onClick={(e) => e.stopPropagation()}>
        {content}
      </div>
    </div>
  );
}
