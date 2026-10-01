"use client";

import Link from "next/link";
import type { DeviceState, RoomConfig, RoomState } from "@/lib/contract";
import { C, deviceLabel, hasClimate } from "@/lib/view";
import { DeviceTile } from "./DeviceTile";
import { IChevron } from "./Icons";

export function occupancy(state: RoomState | undefined, offline: boolean) {
  if (offline) return { text: "Offline", color: C.warn };
  return state?.occ ? { text: "Someone here", color: C.accent } : { text: "Empty", color: C.dim };
}

export function RoomBlock({
  id,
  room,
  state,
  devices,
  offline,
}: {
  id: string;
  room: RoomConfig;
  state?: RoomState;
  devices?: Record<string, DeviceState>;
  offline: boolean;
}) {
  const occ = occupancy(state, offline);
  const list = Object.entries(room.devices ?? {}).filter(([, c]) => c.type !== "lock");
  return (
    <section className="room-block">
      <Link href={`/room?id=${id}`} className="row-btn" aria-label={`Open ${room.name}`}>
        <span style={{ fontSize: 18, fontWeight: 700 }}>{room.name}</span>
        {hasClimate(room) && state?.temp !== undefined && (
          <span className="num muted" style={{ fontSize: 15 }}>{state.temp.toFixed(1)}°</span>
        )}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: occ.color }}>
          <span className="dot" style={{ background: occ.color }} />
          {occ.text}
        </span>
        <IChevron size={18} style={{ marginLeft: "auto", color: "#6E747D" }} />
      </Link>
      {list.length > 0 && (
        <div className="tiles">
          {list.map(([dev, cfg]) => (
            <DeviceTile key={dev} id={dev} cfg={cfg} label={deviceLabel(cfg, room)} state={devices?.[dev]} offline={offline} />
          ))}
        </div>
      )}
    </section>
  );
}
