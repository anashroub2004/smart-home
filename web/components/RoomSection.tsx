"use client";

import Link from "next/link";
import { ChevronRight, Droplets, Sun, Thermometer, User } from "lucide-react";
import type { DeviceState, RoomConfig, RoomState } from "@/lib/contract";
import { round } from "@/lib/format";
import { DeviceTile } from "./DeviceTile";

export function RoomSection({
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
  offline?: boolean;
}) {
  const entries = Object.entries(room.devices ?? {});
  return (
    <section className="mt-8">
      <Link href={`/room?id=${id}`} className="group mb-3 flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-bold">
            {room.name}
            {offline && <span className="rounded-full bg-bad/15 px-2 py-0.5 text-[11px] font-medium text-bad">node offline</span>}
          </h2>
          <RoomStats state={state} />
        </div>
        <ChevronRight size={18} className="text-muted transition group-hover:translate-x-0.5" />
      </Link>
      {entries.length ? (
        <div className="tiles">
          {entries.map(([dev, cfg]) => (
            <DeviceTile key={dev} id={dev} cfg={cfg} state={devices?.[dev]} offline={offline} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted">Sensors only — no controllable devices.</p>
      )}
    </section>
  );
}

export function RoomStats({ state }: { state?: RoomState }) {
  if (!state) return <p className="text-[13px] text-muted">No data yet</p>;
  return (
    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted">
      {state.temp !== undefined && (
        <span className="flex items-center gap-1"><Thermometer size={13} />{round(state.temp, 1)}°</span>
      )}
      {state.hum !== undefined && (
        <span className="flex items-center gap-1"><Droplets size={13} />{round(state.hum)}%</span>
      )}
      {state.lux !== undefined && (
        <span className="flex items-center gap-1"><Sun size={13} />{round(state.lux)} lx</span>
      )}
      <span className="flex items-center gap-1" style={{ color: state.occ ? "var(--color-good)" : undefined }}>
        <User size={13} />{state.occ ? "Occupied" : "Empty"}
      </span>
    </p>
  );
}
