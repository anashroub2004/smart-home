"use client";

import { useRef } from "react";
import { Loader2, MoreHorizontal } from "lucide-react";
import type { DeviceConfig, DeviceState } from "@/lib/contract";
import { useDeviceCommand } from "@/lib/home";
import { useDeviceSheet } from "./DeviceSheet";
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
  const sheet = useDeviceSheet();
  const press = useRef<{ timer?: ReturnType<typeof setTimeout>; long: boolean }>({ long: false });
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

  // Tap = toggle. Long-press (phone) or right-click (desktop) = open the device sheet.
  function startPress() {
    press.current.long = false;
    press.current.timer = setTimeout(() => {
      press.current.long = true;
      sheet.open(id);
    }, 450);
  }
  function endPress() {
    if (press.current.timer) clearTimeout(press.current.timer);
  }

  function tap() {
    if (press.current.long) return; // the long-press already opened the sheet
    if (offline || ui === "pending") return;
    if (isLock) send(1); // lock: "1" = open for a few seconds; the door relocks itself
    else send(on ? 0 : 1, cfg.type === "fan" && !on ? state?.speed || 60 : undefined);
  }

  const bad = ui === "failed" || ui === "timeout";

  return (
    <div className="relative">
      <button
        onClick={tap}
        onPointerDown={startPress}
        onPointerUp={endPress}
        onPointerLeave={endPress}
        onPointerCancel={endPress}
        onContextMenu={(e) => {
          e.preventDefault();
          endPress();
          sheet.open(id);
        }}
        disabled={offline}
        aria-pressed={on}
        className="relative flex w-full select-none [-webkit-touch-callout:none] aspect-[1.15] md:aspect-[1.35] flex-col justify-between rounded-[var(--radius-tile)] border p-4 text-left transition active:scale-[0.98] disabled:opacity-45"
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
      <button
        onClick={() => sheet.open(id)}
        aria-label={`${cfg.name} details`}
        className="absolute right-2.5 top-2.5 grid h-8 w-8 place-items-center rounded-full transition hover:bg-black/10"
        style={{ color: on ? "#555" : "var(--color-muted)" }}
      >
        <MoreHorizontal size={18} />
      </button>
    </div>
  );
}
