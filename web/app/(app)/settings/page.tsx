"use client";

import Link from "next/link";
import { ScreenHeader } from "@/components/ScreenHeader";
import { useAuth } from "@/lib/auth";
import type { Thresholds } from "@/lib/contract";
import { setPref, setThreshold, useConfig, useConnected, useHomeState, useNodes, usePrefs } from "@/lib/home";
import { timeAgo } from "@/lib/format";
import { allDevices, C } from "@/lib/view";

const RULES: { key: keyof Thresholds; label: string; unit: string; step: number; min: number; max: number }[] = [
  { key: "fan_on_temp", label: "Fan on above", unit: "°C", step: 0.5, min: 22, max: 35 },
  { key: "fan_off_temp", label: "Fan off below", unit: "°C", step: 0.5, min: 20, max: 34 },
  { key: "light_on_lux", label: "Lights on below", unit: " lx", step: 10, min: 20, max: 400 },
  { key: "empty_room_off_min", label: "Turn off after empty for", unit: " min", step: 1, min: 2, max: 60 },
];

const LEARNING_MS = 21 * 86_400_000;

export default function SettingsPage() {
  const { user, signOut } = useAuth();
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { data: prefs } = usePrefs();
  const connected = useConnected();

  const hubOnline = !!state?.updated_at && Date.now() - state.updated_at < 30_000;
  const health = [
    { name: "Raspberry Pi hub", state: hubOnline ? "Online" : `Not reporting · ${timeAgo(state?.updated_at)}`, ok: hubOnline },
    ...Object.entries(config?.nodes ?? {}).map(([id, n]) => {
      const s = nodes?.[id];
      return { name: n.name, state: s?.online ? `Online · ${timeAgo(s.last_seen)}` : `Offline · ${timeAgo(s?.last_seen)}`, ok: !!s?.online };
    }),
    { name: "Cloud sync (Firebase)", state: connected ? "Synced" : "Reconnecting…", ok: connected },
  ];
  const devices = allDevices(config).filter((d) => d.cfg.type !== "lock");
  const waste = prefs?.notify?.waste ?? true;
  const ai = prefs?.notify?.ai ?? true;

  return (
    <>
      <ScreenHeader title="Settings" />

      <section className="card">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h2 className="h-label" style={{ margin: 0 }}>System health</h2>
          <Link href="/history" className="btn" style={{ minHeight: 34 }}>View history</Link>
        </div>
        <div style={{ height: 8 }} />
        {health.map((h) => (
          <div key={h.name} className="list-row">
            <span className="dot" style={{ background: h.ok ? C.good : C.warn }} />
            <span style={{ flex: 1 }}>{h.name}</span>
            <span className="muted num" style={{ fontSize: 13 }}>{h.state}</span>
          </div>
        ))}
      </section>

      <section className="card">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <h2 className="h-label" style={{ margin: 0 }}>Devices</h2>
          <Link href="/settings/add-device" className="btn btn-light" style={{ minHeight: 36 }}>+ Add device</Link>
        </div>
        <div style={{ marginTop: 8 }}>
          {devices.map((d) => {
            const learning = !!d.cfg.added_at && Date.now() - d.cfg.added_at < LEARNING_MS;
            return (
              <div key={d.id} className="list-row">
                <span style={{ flex: 1 }}>
                  {d.cfg.name}
                  <div className="muted" style={{ fontSize: 12.5 }}>{d.room.name}{learning ? " · learning" : ""}</div>
                </span>
                <span className="muted num" style={{ fontSize: 13, textAlign: "right" }}>
                  {d.node} · GPIO {d.cfg.pin}{d.cfg.ina ? ` · ${d.cfg.ina}` : ""}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <section className="card">
        <h2 className="h-label">Automation rules</h2>
        {RULES.map((r) => {
          const v = config?.thresholds[r.key] as number | undefined;
          const fmt = (n: number) => (r.step < 1 ? n.toFixed(1) : String(n)) + r.unit;
          const set = (n: number) => {
            const nv = Math.round(Math.min(r.max, Math.max(r.min, n)) * 10) / 10;
            if (v !== undefined && nv !== v) setThreshold(r.key, nv);
          };
          return (
            <div key={r.key} className="list-row">
              <span style={{ flex: 1 }}>{r.label}</span>
              <div className="stepper">
                <button type="button" aria-label={`Decrease ${r.label}`} onClick={() => v !== undefined && set(v - r.step)}>−</button>
                <span className="num" style={{ minWidth: 64, textAlign: "center", fontWeight: 700 }}>{v !== undefined ? fmt(v) : "—"}</span>
                <button type="button" aria-label={`Increase ${r.label}`} onClick={() => v !== undefined && set(v + r.step)}>+</button>
              </div>
            </div>
          );
        })}
      </section>

      <section className="card">
        <h2 className="h-label">Notifications</h2>
        <div className="list-row">
          <span style={{ flex: 1 }}>
            Door and camera alerts
            <div className="muted" style={{ fontSize: 12.5 }}>Always on for safety</div>
          </span>
          <button type="button" className="sw on" aria-pressed="true" aria-label="Door and camera alerts" disabled>
            <span className="k" />
          </button>
        </div>
        <div className="list-row">
          <span style={{ flex: 1 }}>Energy waste stopped</span>
          <button type="button" className={waste ? "sw on" : "sw"} aria-pressed={waste} aria-label="Energy waste notifications" onClick={() => setPref("notify/waste", !waste)}>
            <span className="k" />
          </button>
        </div>
        <div className="list-row">
          <span style={{ flex: 1 }}>AI suggestions</span>
          <button type="button" className={ai ? "sw on" : "sw"} aria-pressed={ai} aria-label="AI suggestion notifications" onClick={() => setPref("notify/ai", !ai)}>
            <span className="k" />
          </button>
        </div>
      </section>

      <section className="card">
        <div className="list-row">
          <span style={{ flex: 1 }}>
            {user?.email}
            <div className="muted" style={{ fontSize: 12.5 }}>Owner</div>
          </span>
          <button type="button" className="btn" onClick={signOut}>Sign out</button>
        </div>
      </section>
    </>
  );
}
