"use client";

import { useRef } from "react";
import type { DeviceConfig, DeviceState } from "@/lib/contract";
import { useDeviceCommand } from "@/lib/home";
import { tileState } from "@/lib/view";
import { useDeviceSheet } from "./DeviceSheet";
import { DeviceIcon } from "./DeviceIcon";
import { IMore } from "./Icons";
import { useToast } from "./Toast";

/** Tap = on/off. ⋯ (or long-press / right-click) = device sheet. Same markup as the prototype. */
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

  const pending = ui === "pending";
  const on = state?.v === 1 && !offline;
  const cls = "tile" + (on ? " on" : "") + (pending ? " pending" : "") + (offline ? " off-line" : "") +
    (ui === "failed" || ui === "timeout" ? " failed" : "");

  function toggle() {
    if (press.current.long || offline || pending) return;
    if (cfg.type === "lock") send(1);
    else send(state?.v ? 0 : 1, cfg.type === "fan" && !state?.v ? state?.speed || 70 : undefined);
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
        aria-pressed={state?.v === 1}
        aria-label={`Turn ${label.toLowerCase()} ${state?.v ? "off" : "on"}`}
        disabled={offline}
        onClick={toggle}
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
        <span className={`t-ic ic-${cfg.type}`}>
          <DeviceIcon type={cfg.type} />
        </span>
        <span>
          <span className="t-name" style={{ display: "block" }}>{cfg.name}</span>
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
