"use client";

import { DoorClosed, DoorOpen, Fingerprint, KeyRound, LogOut, Smartphone } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ackAlert, useAccessLog, useAlerts, useDeviceCommand, useHomeState, useNodes } from "@/lib/home";
import { timeAgo } from "@/lib/format";

const METHOD_ICON = { fingerprint: Fingerprint, keypad: KeyRound, web: Smartphone, exit_button: LogOut };

export default function SecurityPage() {
  const { data: state } = useHomeState();
  const { data: nodes } = useNodes();
  const { list: access } = useAccessLog(30);
  const { list: alerts } = useAlerts(10);
  const door = useDeviceCommand("door_lock");
  const unlocked = state?.devices?.door_lock?.v === 1;
  const doorOffline = nodes?.door?.online === false;
  const openAlerts = alerts.filter((a) => !a.ack);

  return (
    <>
      <PageHeader title="Security" />

      {openAlerts.map((a) => (
        <div key={a.id} className="mt-4 flex items-start justify-between gap-3 rounded-2xl border border-bad/40 bg-bad/10 p-4">
          <div>
            <p className="font-semibold text-bad">{a.text}</p>
            <p className="text-xs text-muted">{timeAgo(a.at)}</p>
          </div>
          <button onClick={() => ackAlert(a.id)} className="chip">Dismiss</button>
        </div>
      ))}

      <div className="card mt-6 flex items-center gap-4 p-5">
        <span className="grid h-14 w-14 place-items-center rounded-full bg-tile" style={{ color: unlocked ? "var(--color-warn)" : "var(--color-good)" }}>
          {unlocked ? <DoorOpen size={26} /> : <DoorClosed size={26} />}
        </span>
        <div className="flex-1">
          <p className="text-lg font-bold">Front door</p>
          <p className="text-sm text-muted">
            {doorOffline ? "Door node offline — fingerprint & keypad still work locally" : unlocked ? "Unlocked" : "Locked"}
          </p>
        </div>
        <button
          disabled={doorOffline || door.ui === "pending" || unlocked}
          onClick={() => door.send(1)}
          className="rounded-2xl bg-text px-4 py-2.5 text-sm font-semibold text-bg disabled:opacity-40"
        >
          {door.ui === "pending" ? "Opening…" : door.ui === "timeout" ? "Retry" : "Unlock"}
        </button>
      </div>

      <div className="card mt-4 p-5">
        <p className="font-semibold">Entrance camera</p>
        <p className="mt-1 text-sm text-muted">Recordings are stored on the Raspberry Pi. Live view comes with the Pi camera service (step 39 in the roadmap).</p>
      </div>

      <h2 className="mb-2 mt-8 text-lg font-bold">Access log</h2>
      {access.length ? (
        <ul className="card divide-y divide-border px-4">
          {access.map((a) => {
            const Icon = METHOD_ICON[a.method] ?? KeyRound;
            return (
              <li key={a.id} className="flex items-center gap-3 py-3 text-sm">
                <Icon size={16} className={a.ok ? "text-good" : "text-bad"} />
                <span className="flex-1">{a.ok ? "Opened" : "Denied"} · {a.method.replace("_", " ")}{a.who ? ` · ${a.who}` : ""}</span>
                <span className="text-muted">{timeAgo(a.at)}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-muted">No access attempts yet.</p>
      )}
    </>
  );
}
