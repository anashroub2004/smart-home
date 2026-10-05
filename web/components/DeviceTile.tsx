"use client";

import { useRef } from "react";
import type { DeviceConfig, DeviceState } from "@/lib/contract";
import { useDeviceCommand } from "@/lib/home";
import { appControllable, defaultLevel, isLock, isMonitorOnly, tileState } from "@/lib/view";
import { useDeviceSheet } from "./DeviceSheet";
import { DeviceIcon } from "./DeviceIcon";
import { IMore } from "./Icons";
import { useToast } from "./Toast";

/**
 * Tap = on/off (only if the device can be switched from the app).
 * Monitor-only devices show their state but can't be tapped. ⋯ / long-press / right-click = device sheet.
 * The tile is drawn from the device's capabilities, so any new device kind works without code changes.
 */
export function DeviceTile({
  id,
  cfg,
  label,
  state,
  offline,
}: {
  id: string;
  cfg: DeviceConfig;
  label: string;
  state?: DeviceState;
  offline: boolean;
}) {
  const toast = useToast();
  const { ui, send } = useDeviceCommand(id, () => toast(`${label} didn't respond. Nothing changed.`));
  const sheet = useDeviceSheet();
  const press = useRef<{ timer?: ReturnType<typeof setTimeout>; long: boolean }>({ long: false });

  const tappable = appControllable(cfg);
  const monitor = isMonitorOnly(cfg);
  const pending = ui === "pending";
  const on = state?.v === 1 && !offline;
  const cls =
    "tile" + (on ? " on" : "") + (pending ? " pending" : "") + (offline ? " off-line" : "") +
    (ui === "failed" || ui === "timeout" ? " failed" : "") + (!tappable ? " monitor" : "");

  function tap() {
    if (press.current.long || offline || pending) return;
    if (!tappable || cfg.control.confirm) return sheet.open(id); // confirm-first devices open the sheet
    if (isLock(cfg)) send(1);
    else send(state?.v ? 0 : 1, !state?.v && cfg.caps.level ? { level: state?.level ?? defaultLevel(cfg) } : {});
  }
  function startPress() {
    press.current.long = false;
    press.current.timer = setTimeout(() => {
      press.current.long = true;
      sheet.open(id);
    }, 500);
  }
  const endPress = () => press.current.timer && clearTimeout(press.current.timer);

  return (
    <div className="tile-wrap">
      <button
        type="button"
        className={cls}
        aria-pressed={tappable ? state?.v === 1 : undefined}
        aria-label={tappable ? `Turn ${label.toLowerCase()} ${state?.v ? "off" : "on"}` : `${label}, ${tileState(cfg, state, offline, false)}`}
        disabled={offline}
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
      >
        <span className={`t-ic ic-${cfg.icon}`}>
          <DeviceIcon type={cfg.icon} />
        </span>
        <span>
          <span className="t-name" style={{ display: "block" }}>
            {cfg.name}
            {monitor && <span className="ro-badge">VIEW</span>}
          </span>
          <span className="t-state num" style={{ display: "block" }}>
            {pending && <span className="spin" />}
            {tileState(cfg, state, offline, pending)}
          </span>
        </span>
      </button>
      <button type="button" className={on ? "more dark" : "more"} aria-label={`${label} details`} onClick={() => sheet.open(id)}>
        <IMore size={18} />
      </button>
    </div>
  );
}
