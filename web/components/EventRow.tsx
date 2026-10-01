"use client";

import { useState } from "react";
import { Bot, Cpu, DoorOpen, Hand, Radar, Server, Sparkles, TriangleAlert, type LucideIcon } from "lucide-react";
import type { HomeEvent } from "@/lib/contract";
import { clock } from "@/lib/format";

const KIND_ICON: Record<string, LucideIcon> = {
  device: Hand, door: DoorOpen, motion: Radar, node: Server, ai: Bot,
  rule: Cpu, scene: Sparkles, alert: TriangleAlert, config: Cpu,
};

export function sourceGroup(e: HomeEvent): "manual" | "ai" | "rule" | "door" | "system" {
  if (e.kind === "door") return "door";
  if (e.source === "ai") return "ai";
  if (e.source === "rule") return "rule";
  if (e.source === "web" || e.source === "button") return "manual";
  return "system";
}

const GROUP_COLOR = {
  manual: "var(--color-text)", ai: "var(--color-accent)", rule: "var(--color-fan)",
  door: "var(--color-light)", system: "var(--color-muted)",
};

export function EventRow({ e }: { e: HomeEvent }) {
  const [open, setOpen] = useState(false);
  const Icon = KIND_ICON[e.kind] ?? Cpu;
  const g = sourceGroup(e);
  const failed = e.result !== "ok";

  return (
    <li className="border-b border-border last:border-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 py-3 text-left">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-tile" style={{ color: GROUP_COLOR[g] }}>
          <Icon size={16} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[14px]" style={{ color: failed ? "var(--color-bad)" : undefined }}>{e.text}</span>
          <span className="mt-0.5 block text-[12px] text-muted">
            {clock(e.at)} · {e.source}
            {e.trigger && e.trigger !== "manual" ? ` · ${e.trigger}` : ""}
            {e.confidence != null ? ` · ${Math.round(e.confidence * 100)}%` : ""}
            {failed ? ` · ${e.result}` : ""}
          </span>
        </span>
      </button>
      {open && (
        <dl className="mb-3 ml-11 grid grid-cols-[110px_1fr] gap-y-1 rounded-xl bg-tile p-3 text-[12px]">
          {Object.entries({
            Time: new Date(e.at).toLocaleString(),
            Kind: e.kind, Device: e.device, Room: e.room, Source: e.source, By: e.by,
            Trigger: e.trigger, Confidence: e.confidence != null ? `${Math.round(e.confidence * 100)}%` : undefined,
            From: e.from ? JSON.stringify(e.from) : undefined, To: e.to ? JSON.stringify(e.to) : undefined,
            Result: e.result, Latency: e.latency_ms != null ? `${e.latency_ms} ms` : undefined,
            Tags: e.tags?.join(", "),
          })
            .filter(([, v]) => v !== undefined && v !== "")
            .map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className="break-all">{v}</dd>
              </div>
            ))}
        </dl>
      )}
    </li>
  );
}
