"use client";

import { Loader2 } from "lucide-react";
import type { DeviceConfig, DeviceState } from "@/lib/contract";
import { useDeviceCommand } from "@/lib/home";
import { DEVICE_COLOR, DEVICE_ICON } from "./icons";

const SOURCE_LABEL: Record<string, string> = {
  web: "App", button: "Button", ai: "AI", rule: "Rule",
  keypad: "Keypad", fingerprint: "Fingerprint", exit_button: "Exit button", system: "System",
};

export function DeviceTile({
  id,
  cfg,
  state,
  offline,
}: {
  id: string;
  cfg: DeviceConfig;
  state?: DeviceState;
  offline?: boolean;
}) {
  const { ui, send } = useDeviceCommand(id);
  const on = state?.v === 1;
  const Icon = DEVICE_ICON[cfg.type];
  const color = DEVICE_COLOR[cfg.type];
  const isLock = cfg.type === "lock";

  const status =
    offline ? "Offline"
    : ui === "pending" ? "Sending…"
    : ui === "failed" ? "Failed"
    : ui === "timeout" ? "No response"
    : isLock ? (on ? "Unlocked" : "Locked")
    : on ? (cfg.type === "fan" && state?.speed ? `On · ${state.speed}%` : "On")
    : "Off";

  function tap() {
    if (offline || ui === "pending") return;
    if (isLock) send(1); // lock: "1" = open for a few seconds; the door relocks itself
    else send(on ? 0 : 1, cfg.type === "fan" && !on ? state?.speed || 60 : undefined);
  }

  const bad = ui === "failed" || ui === "timeout";

  return (
    <button
      onClick={tap}
      disabled={offline}
      aria-pressed={on}
      className="relative flex aspect-[1.15] flex-col justify-between rounded-[var(--radius-tile)] border p-4 text-left transition active:scale-[0.98] disabled:opacity-45"
      style={{
        background: on ? "var(--color-on-tile)" : "var(--color-tile)",
        borderColor: bad ? "var(--color-bad)" : on ? "transparent" : "var(--color-border)",
        color: on ? "#111" : "var(--color-text)",
      }}
    >
      <span
        className="grid h-10 w-10 place-items-center rounded-full"
        style={{ background: on ? color : "var(--color-surface)", color: on ? "#fff" : color }}
      >
        {ui === "pending" ? <Loader2 size={20} className="animate-spin" /> : <Icon size={20} className={on && cfg.type === "fan" ? "animate-spin [animation-duration:2.5s]" : ""} />}
      </span>
      <span>
        <span className="block font-semibold leading-tight">{cfg.name}</span>
        <span className="mt-0.5 block text-[13px]" style={{ color: bad ? "var(--color-bad)" : on ? "#555" : "var(--color-muted)" }}>
          {status}
        </span>
        {state?.src && !bad && ui !== "pending" && (
          <span className="mt-1 block text-[11px]" style={{ color: on ? "#777" : "var(--color-muted)" }}>
            by {SOURCE_LABEL[state.src] ?? state.src}
          </span>
        )}
      </span>
    </button>
  );
}
