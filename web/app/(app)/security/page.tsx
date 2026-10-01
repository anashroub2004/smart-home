"use client";

import { useEffect, useState } from "react";
import { IExitDoor, IFinger, ILockClosed, ILockOpen, IPhone, IPin, IPlay } from "@/components/Icons";
import { useToast } from "@/components/Toast";
import type { AccessEntry } from "@/lib/contract";
import { useAccessLog, useConfig, useDeviceCommand, useEvents, useHomeState, useNodes } from "@/lib/home";
import { clock } from "@/lib/format";
import { isToday } from "@/lib/view";

const METHOD_WORD = { fingerprint: "fingerprint", keypad: "keypad", web: "this app", exit_button: "exit button" };

function accessView(a: AccessEntry) {
  if (a.method === "web") return { Icon: IPhone, title: "Remote unlock · this app", sub: "Unlocked for 5 s", color: "#8FB8FF" };
  if (a.method === "exit_button") return { Icon: IExitDoor, title: "Exit button", sub: "Opened from inside", color: "#C5CAD1" };
  if (a.method === "fingerprint")
    return a.ok
      ? { Icon: IFinger, title: `${a.who ?? "Someone"} · fingerprint`, sub: "Unlocked", color: "#34C759" }
      : { Icon: IFinger, title: "Unknown fingerprint", sub: "Door stayed locked", color: "#FF8A84" };
  return a.ok
    ? { Icon: IPin, title: "Keypad · correct PIN", sub: "Unlocked", color: "#34C759" }
    : { Icon: IPin, title: "Wrong PIN", sub: "Door stayed locked · clip saved", color: "#FF8A84" };
}

export default function SecurityPage() {
  const toast = useToast();
  const { data: config } = useConfig();
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { list: access } = useAccessLog(50);
  const { list: events } = useEvents(300);
  const door = useDeviceCommand("door_lock", () => toast("The door didn't respond. It stayed locked."));
  const [confirm, setConfirm] = useState(false);
  const [, tick] = useState(0);

  const lockoutUntil = state?.door?.lockout_until ?? 0;
  const lockout = lockoutUntil > Date.now();
  useEffect(() => {
    if (!lockout && state?.devices?.door_lock?.v !== 1) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [lockout, state?.devices?.door_lock?.v]);

  const open = state?.devices?.door_lock?.v === 1;
  const opening = door.ui === "pending";
  const offline = nodes?.door?.online === false;
  const last = state?.door?.last_open;
  const left = Math.max(0, Math.ceil((lockoutUntil - Date.now()) / 1000));
  const attempts = config?.thresholds.door_lockout_attempts ?? 5;

  const title = open ? "Unlocked" : opening ? "Unlocking…" : "Locked";
  const sub = offline
    ? "Door node offline — fingerprint and keypad still work at the door"
    : open
      ? "Locks again in 5 seconds"
      : opening
        ? "Waiting for the door to confirm"
        : lockout
          ? `Keypad blocked for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} after ${attempts} wrong PINs`
          : last
            ? `Last opened ${clock(last.at)} · ${METHOD_WORD[last.method] ?? last.method}`
            : "No one has opened it yet";
  const bg = open || opening ? "#1E2A3F" : lockout ? "#2A1716" : "#13261B";
  const fg = open || opening ? "#8FB8FF" : lockout ? "#FF8A84" : "#34C759";

  const clips = events.filter((e) => e.kind === "motion").slice(0, 4);
  const clipLabel = (at: number) => (isToday(at) ? clock(at) : `${new Date(at).toLocaleDateString("en-GB", { weekday: "short" })} ${clock(at)}`);
  const todayAccess = access.filter((a) => isToday(a.at));

  return (
    <>
      <h1 style={{ margin: 0, fontSize: 28, fontWeight: 700 }}>Security</h1>

      <section className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 10, padding: "26px 16px" }}>
        <div style={{ width: 96, height: 96, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", background: bg, color: fg }}>
          {open ? <ILockOpen size={40} /> : <ILockClosed size={40} />}
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{title}</div>
        <div className="muted num" style={{ fontSize: 14 }}>{sub}</div>
        {!open && !opening && !offline && (
          <button type="button" className="btn btn-light" style={{ marginTop: 6, minHeight: 46, padding: "0 22px" }} onClick={() => setConfirm(true)}>
            Unlock for 5 seconds
          </button>
        )}
      </section>

      <section>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h2 className="h-label">Front door camera</h2>
          <span className="chip">Home network only</span>
        </div>
        <div className="cam">
          <IPlay size={34} />
          <span className="num">{clips[0] ? `Motion · ${clipLabel(clips[0].at)} · 15 s` : "No motion clips yet"}</span>
          <span style={{ position: "absolute", top: 12, left: 12 }} className="chip">Recorded on motion</span>
        </div>
        {clips.length > 1 && (
          <div style={{ display: "flex", gap: 8, marginTop: 10, overflowX: "auto" }}>
            {clips.slice(1).map((c) => (
              <span key={c.id} className="chip num">{isToday(c.at) ? `${clock(c.at)} · 15 s` : clipLabel(c.at)}</span>
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <h2 className="h-label">Today at the door</h2>
        {todayAccess.map((a) => {
          const v = accessView(a);
          return (
            <div key={a.id} className="list-row">
              <span className="ev-ic" style={{ color: v.color }}><v.Icon size={18} /></span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{v.title}</div>
                <div className="muted" style={{ fontSize: 12.5 }}>{v.sub}</div>
              </div>
              <span className="muted num" style={{ fontSize: 13 }}>{clock(a.at)}</span>
            </div>
          );
        })}
        {!todayAccess.length && <div className="empty">No one has used the door today.</div>}
      </section>

      {confirm && (
        <div className="scrim" onClick={() => setConfirm(false)}>
          <div className="sheet" role="dialog" aria-label="Unlock front door" onClick={(e) => e.stopPropagation()}>
            <div className="grab" />
            <div style={{ fontSize: 20, fontWeight: 700 }}>Unlock the front door?</div>
            <div className="muted" style={{ fontSize: 14, marginTop: -8 }}>It opens for 5 seconds, then locks again. This is recorded in the door log.</div>
            <button type="button" className="btn btn-light btn-block" onClick={() => { setConfirm(false); door.send(1); }}>Unlock</button>
            <button type="button" className="btn btn-block" onClick={() => setConfirm(false)}>Cancel</button>
          </div>
        </div>
      )}
    </>
  );
}
