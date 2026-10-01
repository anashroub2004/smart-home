"use client";

import type { HomeConfig, HomeEvent } from "@/lib/contract";
import { clock } from "@/lib/format";
import { findDevice, GROUP_BG, GROUP_COLOR, isToday } from "@/lib/view";
import { IHand, ILockClosed, IRule, IServer, ISparkles } from "./Icons";

const GROUP_ICON = { manual: IHand, ai: ISparkles, rule: IRule, door: ILockClosed, system: IServer };

export function eventDetails(e: HomeEvent, config: HomeConfig | null) {
  const dev = e.device ? findDevice(config, e.device) : null;
  const when = isToday(e.at) ? `Today ${clock(e.at)}` : new Date(e.at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
  const latency = e.latency_ms == null ? "" : e.latency_ms >= 10_000 ? "timeout (10 s)" : `${(e.latency_ms / 1000).toFixed(1)} s`;
  const result =
    e.result === "failed" ? "Failed" : e.result === "denied" ? "Denied" : e.kind === "device" || e.kind === "scene" ? "Confirmed by the device" : "Done";
  return [
    { k: "Done by", v: e.by_label },
    { k: "Source", v: e.src_label },
    { k: "Why", v: e.why },
    { k: "Change", v: e.change },
    { k: "Device", v: dev ? `${dev.room.name} · ${dev.cfg.name}` : "" },
    { k: "Node", v: e.node ? (e.node === "hub" ? "Raspberry Pi hub" : `${e.node} node`) : "" },
    { k: "Result", v: result },
    { k: "Response time", v: latency },
    { k: "Saved", v: e.saved_wh ? `${e.saved_wh} Wh` : "" },
    { k: "Time", v: when },
  ].filter((d) => d.v);
}

export function SourceTag({ e }: { e: HomeEvent }) {
  const g = e.group ?? "system";
  return (
    <span className="tag" style={{ background: GROUP_BG[g], color: GROUP_COLOR[g] }}>
      {e.src_label}
    </span>
  );
}

/** One row of the History list (prototype `.ev`). */
export function EventItem({
  e,
  config,
  open,
  onToggle,
}: {
  e: HomeEvent;
  config: HomeConfig | null;
  open: boolean;
  onToggle: () => void;
}) {
  const g = e.group ?? "system";
  const Icon = GROUP_ICON[g] ?? IServer;
  const failed = e.result !== "ok";
  return (
    <div className="ev">
      <button type="button" className="ev-head" aria-expanded={open} onClick={onToggle}>
        <span className="ev-ic" style={{ color: GROUP_COLOR[g] }}>
          <Icon size={18} />
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "block", fontWeight: 600, fontSize: 14.5 }}>{e.title}</span>
          <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            <SourceTag e={e} />
            {failed && (
              <span className="tag" style={{ background: "#2A1716", color: "#FF8A84" }}>
                {e.result === "denied" ? "Denied" : "Failed"}
              </span>
            )}
            {e.short && <span className="muted" style={{ fontSize: 12.5 }}>{e.short}</span>}
          </span>
        </span>
        <span className="muted num" style={{ fontSize: 13 }}>{clock(e.at)}</span>
      </button>
      {open && (
        <dl className="kv">
          {eventDetails(e, config).map((d) => (
            <div key={d.k} style={{ display: "contents" }}>
              <dt>{d.k}</dt>
              <dd className="num">{d.v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
