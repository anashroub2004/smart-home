"use client";

// Device detail sheet: bottom sheet on phones, centred panel on desktop.
// Open it from anywhere with `useDeviceSheet().open(deviceId)`.

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Bot, Clock, Cpu, Loader2, Pause, Play, Power, X, Zap } from "lucide-react";
import type { DeviceConfig } from "@/lib/contract";
import {
  sendCommand,
  setAiPause,
  useAiPause,
  useAiSchedule,
  useConfig,
  useDeviceCommand,
  useEnergyDaily,
  useEvents,
  useHomeState,
  useNodes,
} from "@/lib/home";
import { clock, dayKey, round, timeAgo } from "@/lib/format";
import { DEVICE_COLOR, DEVICE_ICON } from "./icons";
import { EventRow } from "./EventRow";

const Ctx = createContext<{ open: (id: string) => void } | null>(null);

export function useDeviceSheet() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useDeviceSheet must be used inside <DeviceSheetProvider>");
  return c;
}

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

const SOURCE_LABEL: Record<string, string> = {
  web: "the app", button: "the wall button", ai: "AI", rule: "a rule",
  keypad: "the keypad", fingerprint: "fingerprint", exit_button: "the exit button", system: "the system",
};

const PAUSE_OPTIONS = [
  { label: "1 h", min: 60 },
  { label: "2 h", min: 120 },
  { label: "Until tomorrow", min: 0 }, // computed below
];

function minutesUntilTomorrow(): number {
  const t = new Date();
  t.setHours(24, 0, 0, 0);
  return Math.ceil((t.getTime() - Date.now()) / 60_000);
}

function DeviceSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { data: daily } = useEnergyDaily();
  const { data: schedule } = useAiSchedule();
  const { data: pauseUntil } = useAiPause(id);
  const { list: events } = useEvents(200);
  const cmd = useDeviceCommand(id);
  const [, tick] = useState(0);

  // close on Escape, lock page scroll, refresh countdowns every 30 s
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      clearInterval(t);
    };
  }, [onClose]);

  const found = useMemo(() => {
    for (const [roomId, room] of Object.entries(config?.rooms ?? {})) {
      const cfg = room.devices?.[id];
      if (cfg) return { roomId, roomName: room.name, node: room.node, cfg };
    }
    return null;
  }, [config, id]);

  if (!config) return null;
  if (!found) {
    return (
      <Shell onClose={onClose}>
        <p className="p-6 text-muted">Device “{id}” is not in the configuration.</p>
      </Shell>
    );
  }

  const { roomName, node, cfg } = found;
  const st = state?.devices?.[id];
  const on = st?.v === 1;
  const offline = nodes?.[node]?.online === false;
  const Icon = DEVICE_ICON[cfg.type];
  const color = DEVICE_COLOR[cfg.type];
  const isLock = cfg.type === "lock";
  const paused = !!pauseUntil && pauseUntil > Date.now();
  const ai = schedule?.[id];
  const todayWh = daily?.[dayKey()]?.[id] ?? 0;
  const deviceEvents = events.filter((e) => e.device === id).slice(0, 8);
  const busy = cmd.ui === "pending";

  const statusText = offline
    ? "Node offline"
    : isLock
      ? on ? "Unlocked" : "Locked"
      : on ? (cfg.type === "fan" && st?.speed ? `On · ${st.speed}%` : "On") : "Off";

  return (
    <Shell onClose={onClose}>
      {/* header */}
      <div className="flex items-start gap-3 p-5 pb-3">
        <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full" style={{ background: on ? color : "var(--color-tile)", color: on ? "#fff" : color }}>
          <Icon size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="device-sheet-title" className="text-xl font-bold leading-tight">{cfg.name}</h2>
          <p className="text-sm text-muted">
            {roomName} · <span style={{ color: offline ? "var(--color-bad)" : undefined }}>{statusText}</span>
          </p>
        </div>
        <button onClick={onClose} aria-label="Close" className="grid h-9 w-9 place-items-center rounded-full bg-tile text-muted hover:text-text">
          <X size={18} />
        </button>
      </div>

      <div className="space-y-4 overflow-y-auto px-5 pb-6">
        {/* main control */}
        <button
          disabled={offline || busy || (isLock && on)}
          onClick={() => (isLock ? cmd.send(1) : cmd.send(on ? 0 : 1, cfg.type === "fan" && !on ? st?.speed || 60 : undefined))}
          className="flex w-full items-center justify-center gap-2 rounded-2xl py-3.5 font-semibold transition disabled:opacity-40"
          style={{ background: on && !isLock ? "var(--color-tile)" : "var(--color-text)", color: on && !isLock ? "var(--color-text)" : "var(--color-bg)" }}
        >
          {busy ? <Loader2 size={18} className="animate-spin" /> : <Power size={18} />}
          {busy ? "Sending…" : isLock ? (on ? "Unlocked — relocks automatically" : "Unlock door") : on ? "Turn off" : "Turn on"}
        </button>
        {(cmd.ui === "failed" || cmd.ui === "timeout") && (
          <p className="-mt-2 text-center text-sm text-bad">
            {cmd.ui === "timeout" ? "The device did not respond within 10 s." : `Failed${cmd.error ? `: ${cmd.error}` : ""}.`}
          </p>
        )}

        {cfg.type === "fan" && <SpeedControl id={id} speed={st?.speed ?? 60} on={on} disabled={offline} />}

        {/* numbers */}
        {!isLock && (
          <div className="grid grid-cols-3 gap-2">
            <Stat icon={<Zap size={14} />} label="Now" value={`${round(st?.watts, 1)} W`} />
            <Stat icon={<Zap size={14} />} label="Today" value={`${round(todayWh, 1)} Wh`} />
            <Stat icon={<Clock size={14} />} label="Changed" value={timeAgo(st?.at)} />
          </div>
        )}
        {st?.src && (
          <p className="text-[13px] text-muted">
            Last changed by <span className="text-text">{SOURCE_LABEL[st.src] ?? st.src}</span> at {clock(st.at)}
          </p>
        )}

        {/* automation */}
        {!isLock && (
          <section className="rounded-2xl bg-tile p-4">
            <div className="flex items-center gap-2">
              <Bot size={16} className="text-accent" />
              <p className="flex-1 font-semibold">Automation</p>
              {ai && <span className="text-xs text-muted">{Math.round(ai.p_on * 100)}% likely on in 1 h</span>}
            </div>
            {paused ? (
              <div className="mt-3 flex items-center justify-between gap-3">
                <p className="text-sm text-warn">Paused until {clock(pauseUntil!)} — AI and rules won’t touch this device.</p>
                <button onClick={() => setAiPause(id, null)} className="chip shrink-0"><Play size={14} /> Resume</button>
              </div>
            ) : (
              <>
                <p className="mt-2 text-sm text-muted">
                  {ai?.action === "schedule_on"
                    ? `AI plans to turn it on around ${ai.execute_at?.slice(11, 16)}.`
                    : ai?.action === "paused_by_override"
                      ? "AI is waiting — you controlled this device manually in the last 2 h."
                      : "AI and rules can control this device."}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="flex items-center gap-1 text-xs text-muted"><Pause size={13} /> Pause for</span>
                  {PAUSE_OPTIONS.map((o) => (
                    <button key={o.label} className="chip" onClick={() => setAiPause(id, o.min || minutesUntilTomorrow())}>
                      {o.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </section>
        )}

        {/* recent activity */}
        <section>
          <p className="mb-1 font-semibold">Recent activity</p>
          {deviceEvents.length ? (
            <ul className="rounded-2xl bg-tile px-3">{deviceEvents.map((e) => <EventRow key={e.id} e={e} />)}</ul>
          ) : (
            <p className="text-sm text-muted">Nothing yet.</p>
          )}
        </section>

        <HardwareInfo cfg={cfg} node={config.nodes[node]?.name ?? node} nodeOnline={!offline} />
      </div>
    </Shell>
  );
}

function Shell({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center" role="dialog" aria-modal="true" aria-labelledby="device-sheet-title">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative flex max-h-[88dvh] w-full flex-col rounded-t-3xl border border-border bg-surface shadow-2xl md:max-w-md md:rounded-3xl">
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-border md:hidden" />
        {children}
      </div>
    </div>
  );
}

function SpeedControl({ id, speed, on, disabled }: { id: string; speed: number; on: boolean; disabled: boolean }) {
  const [val, setVal] = useState(speed);
  useEffect(() => setVal(speed), [speed]);
  const commit = () => val !== speed || !on ? sendCommand(id, 1, val) : undefined;
  return (
    <div className="rounded-2xl bg-tile p-4">
      <div className="flex justify-between text-sm">
        <span>Speed</span>
        <span className="text-muted">{val}%</span>
      </div>
      <input
        type="range" min={20} max={100} step={10} value={val} disabled={disabled}
        onChange={(e) => setVal(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        className="mt-3 w-full accent-[var(--color-fan)]"
        aria-label="Fan speed"
      />
      <div className="mt-1 flex justify-between text-[11px] text-muted"><span>Low</span><span>High</span></div>
    </div>
  );
}

function Stat({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-tile p-3">
      <p className="flex items-center gap-1 text-[11px] text-muted">{icon}{label}</p>
      <p className="mt-1 truncate text-[15px] font-semibold">{value}</p>
    </div>
  );
}

function HardwareInfo({ cfg, node, nodeOnline }: { cfg: DeviceConfig; node: string; nodeOnline: boolean }) {
  const rows: [string, string | number | undefined][] = [
    ["Node", `${node} · ${nodeOnline ? "online" : "offline"}`],
    ["Output pin", `GPIO${cfg.pin}`],
    ["Wall button", cfg.button !== undefined ? `GPIO${cfg.button}` : undefined],
    ["Power sensor", cfg.ina ? `INA226 @ ${cfg.ina}` : undefined],
    ["Rated power", cfg.watts ? `${cfg.watts} W` : undefined],
  ];
  return (
    <details className="rounded-2xl bg-tile p-4 text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 font-semibold">
        <Cpu size={16} className="text-muted" /> Hardware
      </summary>
      <dl className="mt-3 grid grid-cols-[120px_1fr] gap-y-1.5">
        {rows.filter(([, v]) => v !== undefined).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
